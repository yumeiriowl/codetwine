import os
import re
import fnmatch
import logging
from collections import deque
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.imports import extract_imports
from codetwine.import_to_path import (
    resolve_module_to_project_path,
    get_import_params,
)
from codetwine.utils.file_utils import rel_to_copy_path
from codetwine.config.settings import (
    DEFINITION_DICTS,
    EXCLUDE_PATTERNS,
    SAME_PACKAGE_VISIBLE,
)

logger = logging.getLogger(__name__)


def _is_inside_import(node) -> bool:
    """Determine whether an AST node is inside an import/include statement.

    Traverse ancestors from the node to the root using tree-sitter's Node.parent,
    and check for import-related node types (import_statement, import_from_statement,
    import_declaration, preproc_include, etc.).

    Args:
        node: The tree-sitter AST node to check.

    Returns:
        True if the node is inside an import/include statement.
    """
    current = node.parent
    while current is not None:
        node_type = current.type
        if "import" in node_type or node_type == "preproc_include":
            return True
        current = current.parent
    return False


_DEFINITION_NAME_NODE_TYPES = {"identifier", "type_identifier", "namespace_identifier"}


def _find_definition_node(root_node, definition_name: str):
    """Search the AST by breadth-first search (BFS) and return the definition node with the specified name.

    Target node types for the search:
        identifier           - Function names, variable names, class names (Python/Java/Kotlin/JS)
        type_identifier      - Type names (C/C++ struct/class/enum, TS interface/type alias)
        namespace_identifier - Namespace names (C++ namespace)

    Nodes inside import statements are skipped (they are references, not definitions).

    Args:
        root_node: The AST root node covering the entire file.
        definition_name: The definition name to search for (e.g. "parse_file", "Point", "geometry").

    Returns:
        The parent node containing the definition. None if not found.
    """
    queue = deque((child, root_node) for child in root_node.children)
    while queue:
        node, parent = queue.popleft()
        if node.type in _DEFINITION_NAME_NODE_TYPES and node.text.decode("utf-8") == definition_name:
            if not _is_inside_import(node):
                return parent
        for child in node.children:
            queue.append((child, node))
    return None


def extract_callee_source(
    callee_file_path: str,
    callee_name: str,
    project_dir: str,
) -> str | None:
    """Retrieve the definition source code for a specified name from the dependency target file.

    Search the AST by breadth-first search (BFS) to find an identifier matching callee_name,
    then return the entire source code of its parent node (function_definition / class_definition /
    assignment, etc.).

    Parse results are reused via the module-level cache in ts_parser.py.

    Args:
        callee_file_path: Path of the dependency target file (relative to project root, e.g. "src/foo.py").
        callee_name: Name of the definition to retrieve (e.g. "parse_file", "helper.process").
        project_dir: Absolute path to the project root.

    Returns:
        The source code string. None if the definition is not found.
    """

    absolute_path = os.path.join(project_dir, callee_file_path)

    callee_root = parse_file(absolute_path)[0]

    # For attribute access like "helper.process", the trailing "process" is the actual definition name.
    # For cases like "TEMPLATE.format" where the trailing part is a built-in method,
    # the leading "TEMPLATE" is the definition name.
    # If not found by the trailing part, re-search by the leading part.
    parts = callee_name.split(".")
    search_names = [parts[-1]]
    if len(parts) > 1:
        search_names.append(parts[0])

    for definition_name in search_names:
        parent_node = _find_definition_node(callee_root, definition_name)
        if parent_node is not None:
            return parent_node.text.decode("utf-8")

    return None


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
    Import analysis is performed on all supported language files to build the dependency graph.

    Args:
        project_dir: Root directory of the project to analyze.

    Returns:
        A list of file dependency information dicts.
    """
    supported_ext_set = set(DEFINITION_DICTS.keys())

    # == Step 1: Collect all files with supported extensions ======================
    all_file_list: list[str] = []
    for dir_path, dir_names, file_name_list in os.walk(project_dir):
        # Remove directories matching exclude patterns from the traversal targets
        # Modifying dir_names in-place causes os.walk to skip those subtrees
        dir_names[:] = [d for d in dir_names if not any(fnmatch.fnmatch(d, p) for p in EXCLUDE_PATTERNS)]
        for file_name in file_name_list:
            if any(fnmatch.fnmatch(file_name, p) for p in EXCLUDE_PATTERNS):
                continue
            if os.path.splitext(file_name)[1].lstrip(".") in supported_ext_set:
                all_file_list.append(os.path.join(dir_path, file_name))

    # == Step 2: Build the set of relative paths for project files ============
    # A lookup set used to determine whether a module is within the project during import resolution
    project_file_set: set[str] = set()
    for file_path in all_file_list:
        project_file_set.add(os.path.relpath(file_path, project_dir).replace("\\", "/"))

    # == Step 3: Collect files imported by each file (callees) ======
    file_callee_map: dict[str, set[str]] = {}
    for file_path in all_file_list:
        callee_set: set[str] = set()
        file_ext = os.path.splitext(file_path)[1].lstrip(".")
        language, import_query_str = get_import_params(file_ext)
        file_rel = os.path.relpath(file_path, project_dir).replace("\\", "/")

        # Parse import statements and add those resolvable to project files as callees
        if language and import_query_str:
            root_node = parse_file(file_path)[0]
            for import_info in extract_imports(root_node, language, import_query_str):
                resolved = resolve_module_to_project_path(
                    import_info.module,
                    file_rel,
                    project_file_set,
                )
                if resolved:
                    abs_resolved = os.path.abspath(os.path.join(project_dir, resolved))
                    callee_set.add(abs_resolved)

        file_callee_map[os.path.abspath(file_path)] = callee_set

    # == Step 3.5: Add same-package files as implicit callees (Java/Kotlin) ==
    # In Java/Kotlin, classes in the same package (same directory) can be referenced without imports.
    # Add as a unidirectional dependency only when the class name (= filename without extension)
    # appears in the source code.
    dir_ext_groups: dict[tuple[str, str], list[str]] = {}
    for file_path in all_file_list:
        file_ext = os.path.splitext(file_path)[1].lstrip(".")
        if not SAME_PACKAGE_VISIBLE.get(file_ext):
            continue
        abs_path = os.path.abspath(file_path)
        key = (os.path.dirname(abs_path), file_ext)
        dir_ext_groups.setdefault(key, []).append(abs_path)

    # Check within the same group whether source code references class names from other files
    for group in dir_ext_groups.values():
        # Pre-build class names and regex patterns for each file in the group
        class_names: dict[str, str] = {}
        class_patterns: dict[str, re.Pattern[str]] = {}
        for abs_path in group:
            name = os.path.splitext(os.path.basename(abs_path))[0]
            class_names[abs_path] = name
            class_patterns[abs_path] = re.compile(r"\b" + re.escape(name) + r"\b")

        for abs_path in group:
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    source = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            for other_path in group:
                if other_path == abs_path:
                    continue
                if class_patterns[other_path].search(source):
                    file_callee_map[abs_path].add(other_path)

    # == Step 4: Build the callers (reverse lookup) index ==================
    file_caller_map: dict[str, list[str]] = {os.path.abspath(f): [] for f in all_file_list}
    for caller_path, callee_set in file_callee_map.items():
        for callee_path in callee_set:
            if callee_path in file_caller_map:
                file_caller_map[callee_path].append(caller_path)

    # == Step 5: Convert to relative paths and write to JSON ====================
    # Paths use the "project_name/copy_path" format.
    # copy_path = {parent_dir}/{file_stem}/{filename} structure.
    # This matches the actual file paths within the output folder,
    # keeping all paths valid even when the folder is moved to another environment.
    project_name = os.path.basename(project_dir)
    file_info_list = []
    for file_path in all_file_list:
        abs_path = os.path.abspath(file_path)
        rel = os.path.relpath(abs_path, project_dir).replace("\\", "/")
        caller_rels = [os.path.relpath(p, project_dir).replace("\\", "/") for p in file_caller_map[abs_path]]
        callee_rels = [os.path.relpath(p, project_dir).replace("\\", "/") for p in file_callee_map[abs_path]]
        file_info_list.append({
            "file":    f"{project_name}/{rel_to_copy_path(rel)}",
            "callers": [f"{project_name}/{rel_to_copy_path(r)}" for r in caller_rels],
            "callees": [f"{project_name}/{rel_to_copy_path(r)}" for r in callee_rels],
        })

    return file_info_list
