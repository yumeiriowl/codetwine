import os
import fnmatch
import logging
from collections import deque
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import CONTAINER_DEFINITION_TYPE_SET
from codetwine.extractors.imports import extract_imports
from codetwine.extractors.usages import extract_usages
from codetwine.import_to_path import (
    detect_source_roots,
    resolve_module_to_project_path,
    get_import_params,
    top_level_definition_names,
)
from codetwine.utils.file_utils import is_text_file, rel_to_copy_path
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT,
    EXCLUDE_PATTERNS,
    EXT_TO_USAGE_NODE_TYPE_DICT,
    has_language,
    implicit_scope_key,
)

logger = logging.getLogger(__name__)


_DEFINITION_NAME_NODE_TYPE_SET = {"identifier", "type_identifier", "namespace_identifier"}


def _enclosing_definition(node: Node, definition_dict: dict[str, str]) -> Node | None:
    """Return the definition node that a name node belongs to.

    Walk up from the name node to the first ancestor whose type is in definition_dict,
    then keep walking up while the parent is also a definition node that is not a
    container (C/C++: function_declarator -> function_definition,
    Python: function_definition -> decorated_definition).

    Args:
        node: A name node (identifier, etc.).
        definition_dict: Per-language definition node settings.

    Returns:
        The definition node. None when no ancestor is a definition node.
    """
    current = node.parent
    while current is not None and current.type not in definition_dict:
        current = current.parent
    if current is None:
        return None
    while (
        current.parent is not None
        and current.parent.type in definition_dict
        and current.parent.type not in CONTAINER_DEFINITION_TYPE_SET
    ):
        current = current.parent
    return current


def _find_definition_node(
    root_node: Node, definition_name: str, definition_dict: dict[str, str],
) -> Node | None:
    """Search the AST by breadth-first search (BFS) and return the definition node with the specified name.

    Target node types for the search:
        identifier           - Function names, variable names, class names (Python/Java/Kotlin/JS/SQL)
        type_identifier      - Type names (C/C++ struct/class/enum, TS interface/type alias)
        namespace_identifier - Namespace names (C++ namespace)

    A name node with no definition node among its ancestors (inside an import statement,
    a SQL DROP statement, etc.) is skipped and the search continues.

    Args:
        root_node: The AST root node covering the entire file.
        definition_name: The definition name to search for (e.g. "parse_file", "Point", "geometry").
        definition_dict: Per-language definition node settings.

    Returns:
        The definition node. None if not found.
    """
    queue = deque(root_node.children)
    while queue:
        node = queue.popleft()
        if node.type in _DEFINITION_NAME_NODE_TYPE_SET and node.text.decode("utf-8") == definition_name:
            definition_node = _enclosing_definition(node, definition_dict)
            if definition_node is not None:
                return definition_node
        queue.extend(node.children)
    return None


def extract_callee_source(
    callee_file_path: str,
    callee_name: str,
    project_dir: str,
) -> str | None:
    """Retrieve the definition source code for a specified name from the dependency target file.

    Search the AST by breadth-first search (BFS) to find an identifier matching callee_name,
    then return the entire source code of the definition node it belongs to
    (function_definition / class_definition / expression_statement / create_table, etc.).

    Parse results are reused via the module-level cache in ts_parser.py.

    Args:
        callee_file_path: Path of the dependency target file (relative to project root, e.g. "src/foo.py").
        callee_name: Name of the definition to retrieve (e.g. "parse_file", "helper.process").
        project_dir: Absolute path to the project root.

    Returns:
        The source code string. None if the definition is not found.
    """
    definition_dict = EXT_TO_DEFINITION_DICT.get(os.path.splitext(callee_file_path)[1].lstrip("."))
    if not definition_dict:
        return None

    absolute_path = os.path.join(project_dir, callee_file_path)

    callee_root = parse_file(absolute_path)[0]

    # For attribute access like "helper.process", the trailing "process" is the actual definition name.
    # For cases like "TEMPLATE.format" where the trailing part is a built-in method,
    # the leading "TEMPLATE" is the definition name.
    # If not found by the trailing part, re-search by the leading part.
    part_list = callee_name.split(".")
    search_name_list = [part_list[-1]]
    if len(part_list) > 1:
        search_name_list.append(part_list[0])

    for definition_name in search_name_list:
        definition_node = _find_definition_node(callee_root, definition_name, definition_dict)
        if definition_node is not None:
            return definition_node.text.decode("utf-8")

    return None


def _collect_text_file_list(project_dir: str) -> list[str]:
    """Walk the project and return the absolute paths of its non-empty text files.

    Directories and files matching EXCLUDE_PATTERNS are left out, and so are empty,
    binary and unreadable files (is_text_file). The number of skipped files is logged.

    Args:
        project_dir: Root directory of the project to analyze.

    Returns:
        Absolute file paths in os.walk order.
    """
    text_file_list: list[str] = []
    skip_count = 0
    for dir_path, dir_name_list, file_name_list in os.walk(project_dir):
        # Remove directories matching exclude patterns from the traversal targets
        # Modifying dir_names in-place causes os.walk to skip those subtrees
        dir_name_list[:] = [d for d in dir_name_list if not any(fnmatch.fnmatch(d, p) for p in EXCLUDE_PATTERNS)]
        for file_name in file_name_list:
            if any(fnmatch.fnmatch(file_name, p) for p in EXCLUDE_PATTERNS):
                continue
            file_path = os.path.join(dir_path, file_name)
            if is_text_file(file_path):
                text_file_list.append(file_path)
            else:
                skip_count += 1
    if skip_count:
        logger.info(f"Skipped {skip_count} empty, binary or unreadable files")
    return text_file_list


def build_project_dependencies(project_dir: str) -> list[dict]:
    """Analyze inter-file dependencies within the project and build a dependency graph in memory.

    Return value structure (array):
        [
          {
            "file":    "project_name/src/foo.py/foo.py",
            "callers": ["project_name/src/bar.py/bar.py"],
            "callees": ["project_name/src/baz.py/baz.py"],
          },
          ...
        ]

    Paths use the "project_name/copy_path" format.
    Every non-empty text file that passes EXCLUDE_PATTERNS is listed. Import analysis
    runs on the files whose extension has a tree-sitter language; every other file is
    listed with empty callers and callees.

    Args:
        project_dir: Root directory of the project to analyze.

    Returns:
        A list of file dependency information dicts.
    """
    # == Step 1: Collect every non-empty text file ==============================
    all_file_list = _collect_text_file_list(project_dir)

    # Only the files with a language take part in import resolution and dependency edges
    language_file_list = [f for f in all_file_list if has_language(f)]

    # == Step 2: Build the set of relative paths for project files ============
    # A lookup set used to determine whether a module is within the project during import resolution
    project_file_set: set[str] = set()
    for file_path in language_file_list:
        project_file_set.add(os.path.relpath(file_path, project_dir).replace("\\", "/"))

    # Detect source root prefixes (e.g. "src/main/java/") present in the project
    source_root_set = detect_source_roots(project_file_set)

    # == Step 3: Collect files imported by each file (callees) ======
    file_callee_map: dict[str, set[str]] = {}
    for file_path in language_file_list:
        callee_set: set[str] = set()
        file_ext = os.path.splitext(file_path)[1].lstrip(".")
        language, import_query_str = get_import_params(file_ext)
        file_rel = os.path.relpath(file_path, project_dir).replace("\\", "/")

        # Parse import statements and add those resolvable to project files as callees
        if language:
            root_node = parse_file(file_path)[0]
            for import_info in extract_imports(root_node, language, import_query_str):
                resolved = resolve_module_to_project_path(
                    import_info.module,
                    file_rel,
                    project_file_set,
                    source_root_set,
                )
                if resolved:
                    resolved_abs = os.path.abspath(os.path.join(project_dir, resolved))
                    callee_set.add(resolved_abs)

        file_callee_map[os.path.abspath(file_path)] = callee_set

    # == Step 3.5: Add files visible without an import statement as implicit callees ==
    # Java/Kotlin: classes in the same package (same directory) can be referenced without imports.
    # SQL: tables, views and functions created in any file can be referenced from any other file.
    # Add as a unidirectional dependency only when a top-level definition name of the other file
    # is used in the source code.
    scope_group_dict: dict[tuple[str, str], list[str]] = {}
    for file_path in language_file_list:
        file_rel = os.path.relpath(file_path, project_dir).replace("\\", "/")
        scope_key = implicit_scope_key(file_rel)
        if scope_key is not None:
            scope_group_dict.setdefault(scope_key, []).append(file_rel)

    for group in scope_group_dict.values():
        # Definition name -> file that defines it, for every file in the group
        name_to_file: dict[str, str] = {}
        for file_rel in group:
            for name in top_level_definition_names(file_rel, project_dir):
                name_to_file[name] = file_rel

        for file_rel in group:
            other_name_set = {n for n, f in name_to_file.items() if f != file_rel}
            if not other_name_set:
                continue
            abs_path = os.path.abspath(os.path.join(project_dir, file_rel))
            root_node = parse_file(abs_path)[0]
            usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(os.path.splitext(file_rel)[1].lstrip("."))
            for usage in extract_usages(root_node, other_name_set, usage_node_types):
                other_rel = name_to_file[usage.name.split(".")[0]]
                file_callee_map[abs_path].add(os.path.abspath(os.path.join(project_dir, other_rel)))

    # == Step 4: Build the callers (reverse lookup) index ==================
    file_caller_map: dict[str, list[str]] = {os.path.abspath(f): [] for f in language_file_list}
    for caller_path, callee_set in file_callee_map.items():
        for callee_path in callee_set:
            if callee_path in file_caller_map:
                file_caller_map[callee_path].append(caller_path)

    # == Step 5: Convert to relative paths and write to JSON ====================
    # Paths use the "project_name/copy_path" format.
    # copy_path = {parent_dir}/{file_stem}/{filename} structure.
    # This matches the actual file paths within the output folder,
    # keeping all paths valid even when the folder is moved to another environment.
    # A file without a language has no entry in the two maps and gets empty lists.
    project_name = os.path.basename(project_dir)
    file_info_list = []
    for file_path in all_file_list:
        abs_path = os.path.abspath(file_path)
        rel = os.path.relpath(abs_path, project_dir).replace("\\", "/")
        caller_rel_list = [os.path.relpath(p, project_dir).replace("\\", "/") for p in file_caller_map.get(abs_path, [])]
        callee_rel_list = [os.path.relpath(p, project_dir).replace("\\", "/") for p in file_callee_map.get(abs_path, ())]
        file_info_list.append({
            "file":    f"{project_name}/{rel_to_copy_path(rel)}",
            "callers": [f"{project_name}/{rel_to_copy_path(r)}" for r in caller_rel_list],
            "callees": [f"{project_name}/{rel_to_copy_path(r)}" for r in callee_rel_list],
        })

    return file_info_list
