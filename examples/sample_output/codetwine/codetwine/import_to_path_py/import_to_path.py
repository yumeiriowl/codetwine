import os
import logging
from tree_sitter import Language
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    TRANSPARENT_DEFINITION_TYPE_SET,
    select_top_level_definitions,
)
from codetwine.extractors.definition_source import file_definition_list
from codetwine.cobol_file_index import resolve_cobol_module_path
from codetwine.path_config import path_config
from codetwine.package_path import package_import_path_list, package_name_path_list
from codetwine.alias_path import alias_path_list
from codetwine.rust_module_tree import resolve_rust_module_path
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT,
    EXT_TO_IMPORT_RESOLVE_DICT,
    EXT_TO_IMPORT_QUERY_DICT,
    SOURCE_ROOT_PATTERN_LIST,
    EXT_TO_LANGUAGE_DICT,
    language_ext,
)

logger = logging.getLogger(__name__)

# Cache of file name indexes: project file set (by identity) -> {file name: paths with that name}
_file_name_index_cache: dict[int, tuple[set[str], dict[str, list[str]]]] = {}


def clear_import_path_cache() -> None:
    """Forget the file name indexes of every project."""
    _file_name_index_cache.clear()


def detect_source_roots(project_file_set: set[str]) -> set[str]:
    """Detect source root prefixes that actually exist in the project file set.

    Looks for each known source root pattern (e.g. "src/main/java/") at the start of a
    file path and after any directory of it, so the roots of the modules of a
    multi-module project are found as well.

    Examples:
        {"src/main/java/com/a/B.java"}        -> {"src/main/java/", "src/"}
        {"core/src/main/java/com/a/B.java"}   -> {"core/src/main/java/", "core/src/"}
        {"app/models.py"}                     -> set()

    Args:
        project_file_set: Set of relative file paths within the project.

    Returns:
        A set of source root prefix strings that exist in the project.
        Empty set if no known source root patterns are found.
    """
    source_root_set: set[str] = set()
    for file_path in project_file_set:
        for pattern in SOURCE_ROOT_PATTERN_LIST:
            if file_path.startswith(pattern):
                source_root_set.add(pattern)
            pattern_start = file_path.find("/" + pattern)
            while pattern_start != -1:
                source_root_set.add(file_path[:pattern_start + 1] + pattern)
                pattern_start = file_path.find("/" + pattern, pattern_start + 1)
    return source_root_set


def _common_dir_count(file_rel: str, other_rel: str) -> int:
    """Return how many leading directories two relative paths share."""
    count = 0
    for part, other_part in zip(file_rel.split("/")[:-1], other_rel.split("/")[:-1]):
        if part != other_part:
            break
        count += 1
    return count


def nearest_first(path_list: list[str], current_file_rel: str) -> list[str]:
    """Sort paths so that the ones sharing the most leading directories with a file come first.

    Args:
        path_list: Relative paths (files, or directory prefixes ending with "/").
        current_file_rel: Relative path of the file the paths are looked up for.

    Returns:
        The paths ordered by shared leading directories (most first), then by length,
        then by name.
    """
    return sorted(
        path_list,
        key=lambda path: (-_common_dir_count(current_file_rel, path), len(path), path),
    )


def _file_name_index(project_file_set: set[str]) -> dict[str, list[str]]:
    """Return {file name: paths of the project files with that name}, built once per file set."""
    cache_entry = _file_name_index_cache.get(id(project_file_set))
    if cache_entry is not None and cache_entry[0] is project_file_set:
        return cache_entry[1]
    name_index: dict[str, list[str]] = {}
    for file_rel in project_file_set:
        name_index.setdefault(file_rel.rsplit("/", 1)[-1], []).append(file_rel)
    _file_name_index_cache[id(project_file_set)] = (project_file_set, name_index)
    return name_index


def _find_by_path_end(
    candidate_path: str, current_file_rel: str, project_file_set: set[str],
) -> str | None:
    """Return the project file whose path ends with a candidate path.

    Examples (candidate -> file):
        "geo/shape.hpp" -> "include/geo/shape.hpp"
        "app/models.py" -> "backend/app/models.py"

    Args:
        candidate_path: A relative path ("dir/name.ext").
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.

    Returns:
        The file under some directory of the project whose path ends with
        "/" + candidate_path; of several, the one nearest_first() puts first. None when
        there is none.
    """
    file_name = candidate_path.rsplit("/", 1)[-1]
    match_list = [
        file_rel for file_rel in _file_name_index(project_file_set).get(file_name, [])
        if file_rel.endswith("/" + candidate_path)
    ]
    if not match_list:
        return None
    return nearest_first(match_list, current_file_rel)[0]


def resolve_relative_import(
    module: str,
    separator: str,
    current_dir_part_list: list[str],
) -> list[str]:
    """Convert a relative import's module name into a list of directory path components.

    Detect relative imports (Python's "..", JS/TS's "./" or "../")
    and build the path starting from current_dir_part_list.
    For absolute imports, simply split by the separator.

    Args:
        module: The module name from the import statement (e.g. "..utils", "./helper", "os").
        separator: The delimiter for module names ("." or "/").
        current_dir_part_list: Path components of the directory containing the current file
                               (e.g. ["src", "app"]).

    Returns:
        A list of path components (e.g. ["src", "utils"]); empty for a relative path
        that leads to the project root ("../.." from "a/b/c.js").
        Joining this return value with "/".join() produces the base_path.
    """
    if separator == "." and module.startswith("."):
        # Python-style relative import

        # Count the dots (1 dot = current, 2 dots = 1 level up, 3 dots = 2 levels up)
        dot_count = len(module) - len(module.lstrip("."))

        # Extract the remaining module name after removing the dots
        clean_module = module[dot_count:]

        # Copy the current directory path components as the starting point
        path_part_list = list(current_dir_part_list)

        # One dot refers to the current directory.
        # Remove trailing elements (dot_count - 1) times to traverse up to parent directories.
        for _ in range(dot_count - 1):
            if path_part_list:
                path_part_list.pop()

        # Split the remaining module name by "." and append as path components
        if clean_module:
            path_part_list.extend(clean_module.split("."))

        return path_part_list

    if separator == "/" and (module.startswith("./") or module.startswith("../")):
        # JS/TS-style relative import
        # Normalize with os.path.normpath: "src/utils/../lib" -> "src/lib"
        if current_dir_part_list:
            joined_path = "/".join(current_dir_part_list) + "/" + module
        else:
            joined_path = module
        clean_path = os.path.normpath(joined_path).replace("\\", "/")
        return [] if clean_path == "." else clean_path.split("/")

    # Absolute import: split by separator to convert to path
    return module.split(separator)


def generate_candidate_path_list(
    base_path: str,
    src_ext_with_dot: str,
    resolve_config: dict,
    current_dir_part_list: list[str],
) -> list[str]:
    """Generate a list of file path candidates from base_path based on EXT_TO_IMPORT_RESOLVE_DICT settings.

    Language-specific candidate generation rules (index files, alternative extensions,
    current-directory relative paths, etc.) are declaratively defined via config fields,
    so this function contains no language-specific if-branches.

    When base_path already has one of the extensions in alt_ext_list (e.g. C/C++
    #include "stdio.h", JS/TS import "./helpers.js"), base_path is used as the candidate
    itself and no extension is appended, so no "stdio.h.h" candidate is produced. The
    same path with each extension source_ext_dict gives for its extension follows it
    (JS/TS: "./helpers.ts" and "./helpers.tsx" for "./helpers.js").

    Args:
        base_path: The base path converted from the module name (e.g. "src/utils", "stdio.h").
        src_ext_with_dot: Extension of the current file (with leading ".", e.g. ".py", ".c").
        resolve_config: An EXT_TO_IMPORT_RESOLVE_DICT entry (per-language settings dict).
        current_dir_part_list: Path components of the directory containing the current file.

    Returns:
        A list of candidate paths in priority order. No duplicates.
    """
    try_init = resolve_config.get("try_init", False)
    index_ext_list = resolve_config.get("index_ext_list", [])
    alt_ext_list = resolve_config.get("alt_ext_list", [])
    try_bare_path = resolve_config.get("try_bare_path", False)
    try_current_dir = resolve_config.get("try_current_dir", False)
    source_ext_dict = resolve_config.get("source_ext_dict", {})

    # Check whether base_path already has one of the extensions in alt_ext_list
    base_stem, base_ext = os.path.splitext(base_path)
    has_known_ext = base_ext in alt_ext_list

    # Generate candidates from the project root
    root_candidate_list: list[str] = []

    # base_path already carries a known extension: it is the file path itself, then the
    # source files that extension is written for (JS/TS: "./a.js" for a.ts).
    # Otherwise try a file with the same extension as the current file
    if has_known_ext:
        root_candidate_list.append(base_path)
        for source_ext in source_ext_dict.get(base_ext, []):
            root_candidate_list.append(base_stem + source_ext)
    else:
        root_candidate_list.append(base_path + src_ext_with_dot)

    # Python package: try __init__.py (the directory may be a package)
    if try_init:
        root_candidate_list.append(base_path + "/__init__.py")

    # Try directory index files (for JS/TS: import './components' -> './components/index.ts')
    index_prefix = base_path + "/" if base_path else ""
    for idx_ext in index_ext_list:
        root_candidate_list.append(index_prefix + "index" + idx_ext)

    # Try alternative extensions (skip if base_path already has an extension)
    if not has_known_ext:
        for alt_ext in alt_ext_list:
            # Same extension as the current file already added. Skip
            if alt_ext != src_ext_with_dot:
                root_candidate_list.append(base_path + alt_ext)

    # Use base_path as-is without extension (for C/C++: #include "stdio.h" cases)
    if try_bare_path:
        root_candidate_list.append(base_path)

    # Add relative path candidates from the current directory
    if try_current_dir:
        current_dir = "/".join(current_dir_part_list)
        if current_dir:
            for candidate in list(root_candidate_list):
                root_candidate_list.append(current_dir + "/" + candidate)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(root_candidate_list))


def resolve_module_to_project_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    source_root_set: set[str] | None = None,
    project_dir: str | None = None,
) -> str | None:
    """Resolve an import statement's module name to a file path within the project.

    The module names passed to this function include not only project-internal modules
    but also standard library modules (os, json, etc.) and external packages (requests, etc.).
    This function generates file path candidates from the module name and checks them
    against project_file_set to determine whether the module is a project-internal file.
    Returns None if no matching file exists within the project.

    The internal processing consists of 3 steps, each delegated to a dedicated function:
    1. resolve_relative_import: Parse relative/absolute imports to determine path_part_list.
    2. generate_candidate_path_list: Generate candidate file paths from path_part_list.
    3. Match against project_file_set and return the first matching candidate.
       If no exact match is found and source_root_set is provided, retry with
       each source root prefix prepended to the candidate path.
       (e.g. "com/example/Foo.java" -> "src/main/java/com/example/Foo.java")
    4. If still unmatched and the resolve config has min_path_end_part, a module that
       is not relative and has at least that many parts is matched against the end of
       the project file paths (_find_by_path_end).
       (e.g. #include "geo/shape.hpp" -> "include/geo/shape.hpp")

    A language whose resolve config has path_config_name_list (JS/TS) resolves a module
    that is not relative through, in order: the "imports" of the package.json around
    the current file (package_import_path_list), the "paths" and "baseUrl" of the config
    file that counts for the current file (path_config), the aliases of the bundler
    config around it (alias_path_list) and the packages of the project by their names
    (package_name_path_list). The paths they give are matched against project_file_set;
    when none matches and the file has such a config file, or the module is one of
    "imports", nothing else is tried. Otherwise the steps above apply.

    The current file itself is never returned.

    A language whose resolve config has module_tree (Rust) is resolved by
    resolve_rust_module_path instead, which reads the project files under project_dir.
    A language whose resolve config has name_index (COBOL) is resolved by
    resolve_cobol_module_path, which reads them as well.

    Args:
        module: The module name from the import statement. Both project-internal and external
                modules are passed (e.g. "..utils", "os", "requests", "com.example.Foo",
                "./helper", "stdio.h").
        current_file_rel: Relative path of the current file from the project root.
        project_file_set: Set of file paths within the project ("path/to/file.ext" format).
        source_root_set: Set of source root prefixes detected in the project
                         (e.g. {"src/main/java/", "src/test/java/"}). None or empty to skip.
        project_dir: Absolute path to the project root. Required for module_tree and
                     name_index languages; None makes them unresolvable.

    Returns:
        A project-internal file path ("path/to/file.ext" format).
        None if no matching file exists within the project.
    """
    # Get the current file's extension and resolve config
    src_ext_with_dot = os.path.splitext(current_file_rel)[1]
    src_ext = language_ext(
        os.path.join(project_dir, current_file_rel) if project_dir else current_file_rel
    )

    # Get the module resolve config for this extension
    resolve_config = EXT_TO_IMPORT_RESOLVE_DICT.get(src_ext)
    if not resolve_config:
        return None

    if resolve_config.get("module_tree"):
        if project_dir is None:
            return None
        return resolve_rust_module_path(module, current_file_rel, project_file_set, project_dir)

    if resolve_config.get("name_index"):
        if project_dir is None:
            return None
        return resolve_cobol_module_path(module, current_file_rel, project_file_set, project_dir)

    separator = resolve_config["separator"]
    # Split the current file's directory path into components
    current_dir_part_list = current_file_rel.replace("\\", "/").split("/")[:-1]

    # Step 1: Convert the module name to path components
    path_part_list = resolve_relative_import(
        module, separator, current_dir_part_list
    )
    base_path = "/".join(path_part_list)
    is_relative = module.startswith(".")

    # A module that is not relative is looked up through the path settings of the
    # config file that counts for the current file, when there is one
    config_name_list = resolve_config.get("path_config_name_list")
    if config_name_list and project_dir and not is_relative:
        config = path_config(current_file_rel, project_dir, config_name_list)
        package_file_name = resolve_config["package_file_name"]
        import_path_list = package_import_path_list(
            module, current_file_rel, project_dir, package_file_name,
        )
        module_path_list = [
            *import_path_list,
            *(config.module_path_list(module) if config is not None else []),
            *alias_path_list(
                module, current_file_rel, project_dir, resolve_config["alias_config_name_list"],
            ),
            *package_name_path_list(module, project_dir, project_file_set, package_file_name),
        ]
        for module_path in module_path_list:
            for candidate_path in generate_candidate_path_list(
                module_path, src_ext_with_dot, resolve_config, [],
            ):
                if candidate_path in project_file_set and candidate_path != current_file_rel:
                    return candidate_path
        if config is not None or import_path_list:
            return None

    # Step 2: Generate file candidates. An import that is not relative is not looked up
    # from the current directory when that directory is a package
    package_file = resolve_config.get("package_file")
    is_in_package = bool(
        package_file and project_dir and not is_relative
        and os.path.isfile(os.path.join(project_dir, *current_dir_part_list, package_file))
    )
    candidate_path_list = generate_candidate_path_list(
        base_path, src_ext_with_dot, resolve_config,
        [] if is_in_package else current_dir_part_list,
    )

    # Step 3: Match against project_file_set and return the first matching candidate
    for candidate_path in candidate_path_list:
        if candidate_path in project_file_set and candidate_path != current_file_rel:
            return candidate_path

    # Step 3 fallback: Prepend source root prefixes and retry, the roots nearest to the
    # current file first.
    # Java import "com.example.Foo" generates candidate "com/example/Foo.java",
    # but the actual file may be at "src/main/java/com/example/Foo.java".
    if source_root_set:
        source_root_list = nearest_first(list(source_root_set), current_file_rel)
        for candidate_path in candidate_path_list:
            for source_root in source_root_list:
                path_with_root = source_root + candidate_path
                if path_with_root in project_file_set and path_with_root != current_file_rel:
                    return path_with_root

    # Step 4: A module written with enough parts is looked up by the end of the path
    min_path_end_part = resolve_config.get("min_path_end_part")
    if min_path_end_part and not is_relative and len(path_part_list) >= min_path_end_part:
        for candidate_path in candidate_path_list:
            path_match = _find_by_path_end(candidate_path, current_file_rel, project_file_set)
            if path_match is not None and path_match != current_file_rel:
                return path_match

    return None


def top_level_definition_names(file_rel: str, project_dir: str) -> list[str]:
    """Return the names of the definitions of a file that are not nested inside another definition.

    Args:
        file_rel: Relative path from the project root (e.g. "c_app/utils.h").
        project_dir: Absolute path to the project root.

    Returns:
        The outermost definition names in line order, without duplicates, without the
        ones in ATTACHED_DEFINITION_TYPE_SET (Rust impl blocks) and without the names
        of the definitions in TRANSPARENT_DEFINITION_TYPE_SET (C++ namespaces), whose
        members are returned in their place. Empty when the file does not exist or its
        extension has no definition settings.
    """
    abs_path = os.path.join(project_dir, file_rel)
    if not os.path.isfile(abs_path):
        return []

    definition_dict = EXT_TO_DEFINITION_DICT.get(language_ext(abs_path))
    if not definition_dict:
        return []

    definition_list = file_definition_list(abs_path, definition_dict)
    return list(dict.fromkeys(
        d.name for d in select_top_level_definitions(definition_list)
        if d.name
        and d.type not in ATTACHED_DEFINITION_TYPE_SET
        and d.type not in TRANSPARENT_DEFINITION_TYPE_SET
    ))


def get_import_params(file_ext: str) -> tuple[Language, str | None] | tuple[None, None]:
    """Retrieve the Language object and query string needed for import analysis from a file extension.

    For an extension without a tree-sitter language, returns (None, None) to let the
    caller skip the analysis. For a language without import statements (SQL), the query
    string is None and extract_imports returns no imports.

    Args:
        file_ext: File extension (without ".", e.g. "py", "java").

    Returns:
        A (Language, import_query_str) tuple. (None, None) if unsupported.
    """
    language = EXT_TO_LANGUAGE_DICT.get(file_ext)
    if language is None:
        return None, None
    return language, EXT_TO_IMPORT_QUERY_DICT.get(file_ext)
