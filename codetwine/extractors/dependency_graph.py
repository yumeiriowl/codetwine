import os
import fnmatch
import logging
from collections import deque
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    CONTAINER_DEFINITION_TYPE_SET,
    definition_name,
)
from codetwine.extractors.imports import extract_imports
from codetwine.extractors.usages import extract_usages, symbol_part_list
from codetwine.cobol_file_index import reference_target_cache, register_copy_target
from codetwine.csharp_namespace_index import csharp_reference_target_list, csharp_target_cache
from codetwine.import_to_path import (
    detect_source_roots,
    resolve_module_to_project_path,
    get_import_params,
    top_level_definition_names,
)
from codetwine.utils.file_utils import is_text_file, rel_to_copy_path
from codetwine.config.settings import (
    CSHARP_EXT_SET,
    EXT_TO_DEFINITION_DICT,
    EXCLUDE_PATTERNS,
    EXT_TO_USAGE_NODE_TYPE_DICT,
    has_language,
    implicit_scope_key,
    language_ext,
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
    root_node: Node, search_name: str, definition_dict: dict[str, str], is_own_name: bool = False,
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
        search_name: The definition name to search for (e.g. "parse_file", "Point", "geometry").
        definition_dict: Per-language definition node settings.
        is_own_name: When True, only a definition whose own name is search_name is returned;
                     a definition that merely uses the name, and one in
                     ATTACHED_DEFINITION_TYPE_SET (Rust impl blocks), are skipped.

    Returns:
        The definition node. None if not found.
    """
    queue = deque(root_node.children)
    while queue:
        node = queue.popleft()
        if node.type in _DEFINITION_NAME_NODE_TYPE_SET and node.text.decode("utf-8") == search_name:
            definition_node = _enclosing_definition(node, definition_dict)
            if definition_node is not None and (
                not is_own_name
                or (
                    definition_node.type not in ATTACHED_DEFINITION_TYPE_SET
                    and definition_name(definition_node, definition_dict) == search_name
                )
            ):
                return definition_node
        queue.extend(node.children)
    return None


def _find_member_definition_node(
    root_node: Node, owner_name: str, member_name: str, definition_dict: dict[str, str],
) -> Node | None:
    """Return the definition named member_name inside a container definition named owner_name.

    Every container definition with that name is searched (Rust: a struct and each of
    its impl blocks; Python: a class).

    Args:
        root_node: The AST root node covering the entire file.
        owner_name: Name of the container definition (e.g. "Settings").
        member_name: Name of the member definition (e.g. "new").
        definition_dict: Per-language definition node settings.

    Returns:
        The member definition node. None if not found.
    """
    queue = deque(root_node.children)
    while queue:
        node = queue.popleft()
        if (
            node.type in CONTAINER_DEFINITION_TYPE_SET
            and definition_name(node, definition_dict) == owner_name
        ):
            member_node = _find_definition_node(node, member_name, definition_dict, is_own_name=True)
            if member_node is not None and member_node.id != node.id:
                return member_node
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
    The candidates are tried in this order:
        1. For a name with two or more parts ("Settings::new", "Config.load"), the definition
           of the last part inside the container definitions named by the part before it
        2. The definition whose own name is the part before the last (the owner of an
           enum variant or of an associated item), the last part, then the first part
        3. The definition that contains a name node equal to the last part, the part
           before it, then the first part

    For a COBOL file the lines of the first definition with the name are returned;
    names are compared without regard to upper and lower case.

    Parse results are reused via the module-level cache in ts_parser.py.

    Args:
        callee_file_path: Path of the dependency target file (relative to project root, e.g. "src/foo.py").
        callee_name: Name of the definition to retrieve (e.g. "parse_file", "helper.process",
                     "config::Settings::new").
        project_dir: Absolute path to the project root.

    Returns:
        The source code string. None if the definition is not found.
    """
    absolute_path = os.path.join(project_dir, callee_file_path)
    definition_dict = EXT_TO_DEFINITION_DICT.get(language_ext(absolute_path))
    if not definition_dict:
        return None

    callee_root = parse_file(absolute_path)[0]
    if isinstance(callee_root, CobolSource):
        return callee_root.definition_source(callee_name)

    # For attribute access like "helper.process", the trailing "process" is the actual definition name.
    # For cases like "TEMPLATE.format" where the trailing part is a built-in method,
    # the leading "TEMPLATE" is the definition name.
    # If not found by the trailing part, re-search by the preceding and the leading parts.
    part_list = symbol_part_list(callee_name)
    if len(part_list) > 1:
        definition_node = _find_member_definition_node(
            callee_root, part_list[-2], part_list[-1], definition_dict,
        )
        if definition_node is not None:
            return definition_node.text.decode("utf-8")

    own_name_list = list(dict.fromkeys([*part_list[-2:-1], part_list[-1], part_list[0]]))
    contain_name_list = list(dict.fromkeys([part_list[-1], *part_list[-2:-1], part_list[0]]))

    for is_own_name, search_name_list in ((True, own_name_list), (False, contain_name_list)):
        for search_name in search_name_list:
            definition_node = _find_definition_node(
                callee_root, search_name, definition_dict, is_own_name,
            )
            if definition_node is not None:
                return definition_node.text.decode("utf-8")

    return None


def _is_own_file(project_dir: str, file_path: str) -> bool:
    """Return whether a path is a file of the project itself rather than a link to one.

    Args:
        project_dir: Root directory of the project to analyze.
        file_path: Absolute path of a file under project_dir.

    Returns:
        False for a symbolic link and for a path under a linked directory of the
        project, True otherwise.
    """
    rel_path = os.path.relpath(os.path.abspath(file_path), os.path.abspath(project_dir))
    own_path = os.path.join(os.path.realpath(project_dir), rel_path)
    return os.path.normcase(os.path.realpath(file_path)) == os.path.normcase(own_path)


def _collect_text_file_list(project_dir: str) -> list[str]:
    """Walk the project and return the absolute paths of its non-empty text files.

    Directories and files matching EXCLUDE_PATTERNS are left out, and so are symbolic
    links (_is_own_file) and empty, binary and unreadable files (is_text_file). Linked
    directories are not entered. The number of skipped files is logged.

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
            if not _is_own_file(project_dir, file_path):
                continue
            if is_text_file(file_path):
                text_file_list.append(file_path)
            else:
                skip_count += 1
    if skip_count:
        logger.info(f"Skipped {skip_count} empty, binary or unreadable files")
    return text_file_list


def _filter_text_file_list(project_dir: str, file_list: list[str]) -> list[str]:
    """Return the absolute paths of the non-empty text files among the given files.

    The same files are left out as in _collect_text_file_list: a path with a directory
    or file name matching EXCLUDE_PATTERNS, symbolic links and paths under a linked
    directory (_is_own_file), and empty, binary and unreadable files (is_text_file). A
    path outside project_dir is left out too. The number of skipped files is logged.

    Args:
        project_dir: Root directory of the project to analyze.
        file_list: File paths relative to project_dir.

    Returns:
        Absolute file paths in the order given, without duplicates.
    """
    text_file_list: list[str] = []
    known_path_set: set[str] = set()
    skip_count = 0
    for file_rel in file_list:
        file_path = os.path.normpath(os.path.join(project_dir, file_rel))
        part_list = os.path.relpath(file_path, project_dir).replace("\\", "/").split("/")
        if part_list[0] == ".." or file_path in known_path_set:
            continue
        known_path_set.add(file_path)
        if any(fnmatch.fnmatch(part, p) for part in part_list for p in EXCLUDE_PATTERNS):
            continue
        if not _is_own_file(project_dir, file_path):
            continue
        if is_text_file(file_path):
            text_file_list.append(file_path)
        else:
            skip_count += 1
    if skip_count:
        logger.info(f"Skipped {skip_count} empty, binary or unreadable files")
    return text_file_list


def _to_rel(path: str, project_dir: str) -> str:
    """Return a path relative to project_dir with "/" separators."""
    return os.path.relpath(path, project_dir).replace("\\", "/")


def _collect_import_callee_dict(
    language_file_list: list[str],
    project_dir: str,
    project_file_set: set[str],
    source_root_set: set[str],
) -> dict[str, set[str]]:
    """Map each file to the project files its import statements resolve to.

    Args:
        language_file_list: Absolute paths of the files that have a language.
        project_dir: Root directory of the project to analyze.
        project_file_set: Relative paths of the files that have a language.
        source_root_set: Source root prefixes present in the project.

    Returns:
        A {file absolute path: set of callee absolute paths} dict, one entry per file.
        A file whose analysis raises an exception has no callees; the exception is logged.
    """
    file_callee_dict: dict[str, set[str]] = {}
    for file_path in language_file_list:
        callee_set: set[str] = set()
        file_ext = language_ext(file_path)
        language, import_query_str = get_import_params(file_ext)
        file_rel = _to_rel(file_path, project_dir)

        # Parse import statements and add those resolvable to project files as callees
        if language:
            try:
                root_node = parse_file(file_path)[0]
                for import_info in extract_imports(root_node, language, import_query_str):
                    resolved = resolve_module_to_project_path(
                        import_info.module,
                        file_rel,
                        project_file_set,
                        source_root_set,
                        project_dir,
                    )
                    if resolved:
                        callee_set.add(os.path.abspath(os.path.join(project_dir, resolved)))
            except Exception as e:
                _log_graph_failure(file_rel, e)
                callee_set = set()

        file_callee_dict[os.path.abspath(file_path)] = callee_set
    return file_callee_dict


def _log_graph_failure(file_rel: str, error: Exception) -> None:
    """Log that a file is analyzed without its dependencies in the dependency graph."""
    logger.warning(
        f"{file_rel} has no dependencies in the dependency graph: "
        f"{type(error).__name__}: {error}"
    )


def _add_implicit_callee(
    file_callee_dict: dict[str, set[str]],
    language_file_list: list[str],
    project_dir: str,
) -> None:
    """Add the files whose definitions a file uses without an import statement as its callees.

    Java/Kotlin: classes in the same package (same directory).
    SQL: tables, views and functions created in any .sql file of the project.
    An edge is added in one direction, only when a top-level definition name of the other
    file is used in the source code. A file whose analysis raises an exception adds no
    names and no edges; the exception is logged.

    Args:
        file_callee_dict: Return value of _collect_import_callee_dict; modified in place.
        language_file_list: Absolute paths of the files that have a language.
        project_dir: Root directory of the project to analyze.
    """
    scope_group_dict: dict[tuple[str, str], list[str]] = {}
    for file_path in language_file_list:
        file_rel = _to_rel(file_path, project_dir)
        scope_key = implicit_scope_key(file_rel)
        if scope_key is not None:
            scope_group_dict.setdefault(scope_key, []).append(file_rel)

    for group in scope_group_dict.values():
        # Definition name -> file that defines it, for every file in the group
        name_file_dict: dict[str, str] = {}
        fail_file_set: set[str] = set()
        for file_rel in group:
            try:
                name_list = top_level_definition_names(file_rel, project_dir)
            except Exception as e:
                _log_graph_failure(file_rel, e)
                fail_file_set.add(file_rel)
                continue
            for name in name_list:
                name_file_dict[name] = file_rel

        for file_rel in group:
            other_name_set = {name for name, other_rel in name_file_dict.items() if other_rel != file_rel}
            if not other_name_set or file_rel in fail_file_set:
                continue
            abs_path = os.path.abspath(os.path.join(project_dir, file_rel))
            root_node = parse_file(abs_path)[0]
            usage_node_types = EXT_TO_USAGE_NODE_TYPE_DICT.get(language_ext(abs_path))
            for usage in extract_usages(root_node, other_name_set, usage_node_types):
                other_rel = name_file_dict[usage.name.split(".")[0]]
                file_callee_dict[abs_path].add(os.path.abspath(os.path.join(project_dir, other_rel)))


def _add_reference_callee(
    file_callee_dict: dict[str, set[str]],
    language_file_list: list[str],
    project_dir: str,
    project_file_set: set[str],
) -> None:
    """Add the files the references of a C# file resolve to as its callees.

    A file whose analysis raises an exception adds no edges; the exception is logged.

    Args:
        file_callee_dict: Return value of _collect_import_callee_dict; modified in place.
        language_file_list: Absolute paths of the files that have a language.
        project_dir: Root directory of the project to analyze.
        project_file_set: Relative paths of the files that have a language.
    """
    for file_path in language_file_list:
        if language_ext(file_path) not in CSHARP_EXT_SET:
            continue
        file_rel = _to_rel(file_path, project_dir)
        try:
            root_node = parse_file(file_path)[0]
            target_list = csharp_reference_target_list(
                root_node, file_rel, project_file_set, project_dir,
            )
        except Exception as e:
            _log_graph_failure(file_rel, e)
            continue
        for target in target_list:
            if target.file_rel != file_rel:
                file_callee_dict[os.path.abspath(file_path)].add(
                    os.path.abspath(os.path.join(project_dir, target.file_rel))
                )


def _to_output_entry_list(
    all_file_list: list[str],
    project_dir: str,
    file_caller_dict: dict[str, list[str]],
    file_callee_dict: dict[str, set[str]],
) -> list[dict]:
    """Build one {"file", "callers", "callees"} entry per file with "project_name/copy_path" paths.

    copy_path is the {parent_dir}/{stem}_{ext}/{filename} path of the file in the output
    directory. A file without a language has no entry in the two dicts and gets empty lists.

    Args:
        all_file_list: Absolute paths of every analyzed file, in output order.
        project_dir: Root directory of the project to analyze.
        file_caller_dict: {file absolute path: caller absolute paths}.
        file_callee_dict: {file absolute path: callee absolute paths}.

    Returns:
        The entries in the order of all_file_list.
    """
    project_name = os.path.basename(project_dir)

    def to_output(path: str) -> str:
        """Convert an absolute path to "project_name/copy_path"."""
        return f"{project_name}/{rel_to_copy_path(_to_rel(path, project_dir))}"

    file_info_list = []
    for file_path in all_file_list:
        abs_path = os.path.abspath(file_path)
        file_info_list.append({
            "file":    to_output(abs_path),
            "callers": [to_output(caller) for caller in file_caller_dict.get(abs_path, [])],
            "callees": [to_output(callee) for callee in file_callee_dict.get(abs_path, ())],
        })
    return file_info_list


def build_project_dependencies(
    project_dir: str,
    file_list: list[str] | None = None,
) -> list[dict]:
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
    runs on the files with a language (has_language); every other file is listed with
    empty callers and callees. A file without a language that a COBOL COPY statement
    names is analyzed as a COBOL copybook, and keeps that language until the next call
    for the project (register_copy_target). The resolved references of COBOL files
    (reference_target_cache) and of C# files (csharp_target_cache) are cleared.

    Args:
        project_dir: Root directory of the project to analyze.
        file_list: File paths relative to project_dir. When given, only these files
            are analyzed instead of walking project_dir.

    Returns:
        A list of file dependency information dicts.
    """
    # == Step 1: Collect every non-empty text file ==============================
    if file_list is None:
        all_file_list = _collect_text_file_list(project_dir)
    else:
        all_file_list = _filter_text_file_list(project_dir, file_list)

    # Files without a language that COBOL COPY statements name are analyzed as COBOL
    reference_target_cache.clear()
    csharp_target_cache.clear()
    register_copy_target(project_dir, [_to_rel(f, project_dir) for f in all_file_list])

    # Only the files with a language take part in import resolution and dependency edges
    language_file_list = [f for f in all_file_list if has_language(f)]

    # == Step 2: Build the set of relative paths for project files ============
    # A lookup set used to determine whether a module is within the project during import resolution
    project_file_set = {_to_rel(file_path, project_dir) for file_path in language_file_list}

    # Detect source root prefixes (e.g. "src/main/java/") present in the project
    source_root_set = detect_source_roots(project_file_set)

    # == Step 3: Collect files imported by each file (callees) ======
    file_callee_dict = _collect_import_callee_dict(
        language_file_list, project_dir, project_file_set, source_root_set,
    )

    # == Step 3.5: Add files visible without an import statement as implicit callees ==
    _add_implicit_callee(file_callee_dict, language_file_list, project_dir)

    # == Step 3.6: Add the files the references of a C# file resolve to ========
    _add_reference_callee(file_callee_dict, language_file_list, project_dir, project_file_set)

    # == Step 4: Build the callers (reverse lookup) index ==================
    file_caller_dict: dict[str, list[str]] = {os.path.abspath(f): [] for f in language_file_list}
    for caller_path, callee_set in file_callee_dict.items():
        for callee_path in callee_set:
            if callee_path in file_caller_dict:
                file_caller_dict[callee_path].append(caller_path)

    # == Step 5: Convert to "project_name/copy_path" paths ====================
    return _to_output_entry_list(all_file_list, project_dir, file_caller_dict, file_callee_dict)
