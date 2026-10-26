import os
import logging
from collections.abc import Callable, Iterable
from functools import partial
from tree_sitter import Language
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    TRANSPARENT_DEFINITION_TYPE_SET,
    select_top_level_definitions,
)
from codetwine.extractors.definition_source import file_definition_list
from codetwine.cobol_file_index import resolve_cobol_module_path
from codetwine.path_config import inside_project, path_config
from codetwine.package_path import (
    directory_entry_path_list,
    is_dependency_module,
    package_import_path_list,
    package_name_path_list,
)
from codetwine.alias_path import alias_path_list
from codetwine.rust_module_tree import resolve_rust_module_path
from codetwine.utils.project_cache import identity_cache_tuple
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT,
    EXT_TO_IMPORT_RESOLVE_DICT,
    EXT_TO_IMPORT_QUERY_DICT,
    SOURCE_ROOT_PATTERN_LIST,
    EXT_TO_LANGUAGE_DICT,
    language_ext,
)

logger = logging.getLogger(__name__)

# Cache of file name indexes: id of a project file set -> (that set, {file name: paths
# with that name})
_file_name_index_cache_dict: dict[int, tuple[set[str], dict[str, list[str]]]] = {}

# Cache of source root indexes: (id of a project file set, id of a source root set) ->
# (those two sets, {path under a source root: the source roots it is under})
_source_root_index_cache_dict: dict[
    tuple[int, int], tuple[set[str], set[str], dict[str, list[str]]]
] = {}

# JS/TS relative module strings that name a directory without a trailing "/"
_JS_DIR_MODULE_TUPLE = (".", "..")


def clear_import_path_cache() -> None:
    """Forget the file name indexes and source root indexes of every project."""
    _file_name_index_cache_dict.clear()
    _source_root_index_cache_dict.clear()


def detect_source_roots(project_file_set: set[str]) -> set[str]:
    """Detect source root prefixes that actually exist in the project file set.

    Looks for each known source root pattern (e.g. "src/main/java/") at the start of a
    file path and after any directory of it (the roots of the modules of a
    multi-module project).

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
    cache_tuple = identity_cache_tuple(_file_name_index_cache_dict, project_file_set)
    if cache_tuple is not None:
        return cache_tuple[1]
    name_index_dict: dict[str, list[str]] = {}
    for file_rel in project_file_set:
        name_index_dict.setdefault(file_rel.rsplit("/", 1)[-1], []).append(file_rel)
    _file_name_index_cache_dict[id(project_file_set)] = (project_file_set, name_index_dict)
    return name_index_dict


def _source_root_index(
    project_file_set: set[str], source_root_set: set[str],
) -> dict[str, list[str]]:
    """Return {path under a source root: the source roots it is under}, built once per set pair.

    Examples (source roots {"src/", "src/main/java/"}):
        "src/main/java/com/a/B.java" -> "com/a/B.java": ["src/main/java/"],
                                        "main/java/com/a/B.java": ["src/"]

    Args:
        project_file_set: Set of relative file paths within the project.
        source_root_set: Source root prefixes detected in the project.

    Returns:
        For each project file and each source root its path starts with, the rest of
        the path mapped to the roots.
    """
    cache_key_tuple = (id(project_file_set), id(source_root_set))
    cache_tuple = _source_root_index_cache_dict.get(cache_key_tuple)
    if (
        cache_tuple is not None
        and cache_tuple[0] is project_file_set and cache_tuple[1] is source_root_set
    ):
        return cache_tuple[2]
    root_index_dict: dict[str, list[str]] = {}
    for file_rel in project_file_set:
        slash_index = file_rel.find("/")
        while slash_index != -1:
            if file_rel[:slash_index + 1] in source_root_set:
                root_index_dict.setdefault(file_rel[slash_index + 1:], []).append(
                    file_rel[:slash_index + 1]
                )
            slash_index = file_rel.find("/", slash_index + 1)
    _source_root_index_cache_dict[cache_key_tuple] = (
        project_file_set, source_root_set, root_index_dict,
    )
    return root_index_dict


def _find_by_path_end(
    candidate_path: str,
    current_file_rel: str,
    project_file_set: set[str],
    root_check: Callable[[str], bool] | None = None,
) -> str | None:
    """Return the project file whose path ends with a candidate path.

    Examples (candidate -> file):
        "geo/shape.hpp" -> "include/geo/shape.hpp"
        "app/models.py" -> "backend/app/models.py"

    Args:
        candidate_path: A relative path ("dir/name.ext").
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.
        root_check: Whether a directory ("backend/") may hold the candidate path; None
            for every directory.

    Returns:
        The file under some directory of the project whose path ends with
        "/" + candidate_path; of several, the one nearest_first() puts first. None when
        there is none.
    """
    file_name = candidate_path.rsplit("/", 1)[-1]
    match_list = [
        file_rel for file_rel in _file_name_index(project_file_set).get(file_name, [])
        if file_rel.endswith("/" + candidate_path)
        and (root_check is None or root_check(file_rel[:len(file_rel) - len(candidate_path)]))
    ]
    if not match_list:
        return None
    return nearest_first(match_list, current_file_rel)[0]


def _resolve_relative_import(
    module: str,
    separator: str,
    current_dir_part_list: list[str],
) -> list[str]:
    """Convert a relative import's module name into a list of directory path components.

    Detect relative imports (Python's "..", JS/TS's "./", "../", "." or "..")
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
        # Python-style relative import: one dot is the current directory, each further
        # dot one directory up; the rest of the module is split by "."
        dot_count = len(module) - len(module.lstrip("."))
        clean_module = module[dot_count:]
        path_part_list = list(current_dir_part_list)
        for _ in range(dot_count - 1):
            if path_part_list:
                path_part_list.pop()
        if clean_module:
            path_part_list.extend(clean_module.split("."))

        return path_part_list

    if separator == "/" and (module in _JS_DIR_MODULE_TUPLE or module.startswith(("./", "../"))):
        # JS/TS-style relative import, normalized ("src/utils/../lib" -> "src/lib")
        if current_dir_part_list:
            joined_path = "/".join(current_dir_part_list) + "/" + module
        else:
            joined_path = module
        clean_path = os.path.normpath(joined_path).replace("\\", "/")
        return [] if clean_path == "." else clean_path.split("/")

    # Absolute import
    return module.split(separator)


def generate_candidate_path_list(
    base_path: str,
    src_ext_with_dot: str,
    resolve_config: dict,
    current_dir_part_list: list[str],
    entry_candidate_list: list[str] | None = None,
) -> list[str]:
    """Generate a list of file path candidates from base_path based on EXT_TO_IMPORT_RESOLVE_DICT settings.

    The candidate rules of a language (index files, alternative extensions,
    current-directory relative paths, etc.) are read from the fields of its config.
    With try_current_dir, every candidate is tried from the directory of the current
    file (current_dir_part_list) first, then from the project root. With
    last_ext_tuple, the candidates that end with one of its extensions come last.

    When base_path already has one of the extensions in alt_ext_list (e.g. C/C++
    #include "stdio.h", JS/TS import "./helpers.js"), base_path is used as the candidate
    itself and no extension is appended (no "stdio.h.h" candidate). The same path with
    each extension source_ext_dict gives for its extension follows it (JS/TS:
    "./helpers.ts" and "./helpers.tsx" for "./helpers.js").

    Args:
        base_path: The base path converted from the module name (e.g. "src/utils", "stdio.h").
        src_ext_with_dot: Extension of the current file (with leading ".", e.g. ".py", ".c").
        resolve_config: An EXT_TO_IMPORT_RESOLVE_DICT entry (per-language settings dict).
        current_dir_part_list: Path components of the directory containing the current file.
        entry_candidate_list: Candidates of the files the package file of the base_path
            directory names as its entry (JS/TS); tried before its index files.

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
    has_alt_ext = base_ext in alt_ext_list

    # Generate candidates from the project root
    root_candidate_list: list[str] = []

    # base_path already carries an extension of alt_ext_list: it is the file path itself,
    # then the source files that extension is written for (JS/TS: "./a.js" for a.ts).
    # Otherwise try a file with the same extension as the current file
    if has_alt_ext:
        root_candidate_list.append(base_path)
        for source_ext in source_ext_dict.get(base_ext, []):
            root_candidate_list.append(base_stem + source_ext)
    else:
        root_candidate_list.append(base_path + src_ext_with_dot)

    # Python package: try __init__.py (the directory may be a package)
    if try_init:
        root_candidate_list.append(base_path + "/__init__.py")

    # Try the entry files of the directory, then its index files (for JS/TS:
    # import './components' -> './components/index.ts')
    root_candidate_list.extend(entry_candidate_list or [])
    index_prefix = base_path + "/" if base_path else ""
    for index_ext in index_ext_list:
        root_candidate_list.append(index_prefix + "index" + index_ext)

    # Try the alternative extensions but the one of the current file, when base_path has
    # none of them
    if not has_alt_ext:
        for alt_ext in alt_ext_list:
            if alt_ext != src_ext_with_dot:
                root_candidate_list.append(base_path + alt_ext)

    # Use base_path as-is without extension (for C/C++: #include "stdio.h" cases)
    if try_bare_path:
        root_candidate_list.append(base_path)

    # Put the candidates from the current directory before those from the project root
    if try_current_dir:
        current_dir = "/".join(current_dir_part_list)
        if current_dir:
            root_candidate_list = [
                current_dir + "/" + candidate for candidate in root_candidate_list
            ] + root_candidate_list

    # Remove duplicates while preserving order
    return _last_ext_to_end(list(dict.fromkeys(root_candidate_list)), resolve_config)


def _last_ext_to_end(candidate_list: list[str], resolve_config: dict) -> list[str]:
    """Return candidates with the ones ending with an extension of last_ext_tuple moved to the end.

    Examples (last_ext_tuple (".d.ts",)):
        ["a/index.d.ts", "a/index.ts", "a/index.js"] -> ["a/index.ts", "a/index.js", "a/index.d.ts"]

    Args:
        candidate_list: Candidate relative paths, in priority order.
        resolve_config: An EXT_TO_IMPORT_RESOLVE_DICT entry.

    Returns:
        The candidates, each group in its order; the list itself without last_ext_tuple.
    """
    last_ext_tuple = resolve_config.get("last_ext_tuple")
    if not last_ext_tuple:
        return candidate_list
    return [
        candidate for candidate in candidate_list if not candidate.endswith(last_ext_tuple)
    ] + [candidate for candidate in candidate_list if candidate.endswith(last_ext_tuple)]


def resolve_module_to_project_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    source_root_set: set[str] | None = None,
    project_dir: str | None = None,
    is_system_include: bool = False,
) -> str | None:
    """Resolve an import statement's module name to a file path within the project.

    The module names passed to this function include not only project-internal modules
    but also standard library modules (os, json, etc.) and external packages (requests, etc.).
    This function generates file path candidates from the module name and checks them
    against project_file_set to determine whether the module is a project-internal file.
    Returns None if no matching file exists within the project.

    Processing flow:
    1. Take the resolve config of the extension of the current file. A language whose
       resolve config has module_tree (Rust) or name_index (COBOL, JCL, CSD) is
       resolved by its own function (see below).
    2. Parse relative/absolute imports into path_part_list (_resolve_relative_import).
    3. A language whose resolve config has path_config_name_list (JS/TS) resolves a
       module that is not relative through its config and package files
       (_config_path_match); when that gives no file and the current file has such a
       config file, the module is one of "imports", or a package.json around the file
       lists the package of the module as a dependency (is_dependency_module), nothing
       else is tried.
    4. Generate candidate file paths from path_part_list (generate_candidate_path_list).
       For a relative module of such a language, the entry files the package.json of
       the directory it names gives (directory_entry_path_list) come before the index
       files of the directory. The candidates from the directory of the current file
       are made only for a module that is not relative and is no C/C++ #include <x.h>,
       in a directory that is no package (package_file).
    5. Match them against project_file_set and return the first matching candidate.
       If no exact match is found, the module is not relative and source_root_set is
       provided, retry with each source root prefix prepended to the candidate path
       (_source_root_match).
       (e.g. "com/example/Foo.java" -> "src/main/java/com/example/Foo.java")
    6. If still unmatched and the resolve config has min_path_end_part, a module that
       is not relative and has at least that many parts is matched against the end of
       the project file paths (_find_by_path_end).
       (e.g. #include "geo/shape.hpp" -> "include/geo/shape.hpp")
    A module that is not relative and whose first part is in the standard_module_set
    of the resolve config (Python) is looked up in step 5 and step 6 only under the
    source roots and the directories that are above the current file and are no
    package (from profile.models in src/accounts/views.py -> src/profile/models.py).

    The current file itself is never returned.

    A language whose resolve config has module_tree (Rust) is resolved by
    resolve_rust_module_path instead, which reads the project files under project_dir.
    A language whose resolve config has name_index (COBOL, JCL, CSD) is resolved by
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
        is_system_include: True for a C/C++ #include <x.h>.

    Returns:
        A project-internal file path ("path/to/file.ext" format).
        None if no matching file exists within the project.
    """
    # == Step 1: The resolve config of the current file ========================
    src_ext_with_dot = os.path.splitext(current_file_rel)[1]
    src_ext = language_ext(
        os.path.join(project_dir, current_file_rel) if project_dir else current_file_rel
    )
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

    # == Step 2: Convert the module name to path components ======================
    separator = resolve_config["separator"]
    current_dir_part_list = current_file_rel.replace("\\", "/").split("/")[:-1]
    path_part_list = _resolve_relative_import(
        module, separator, current_dir_part_list
    )
    base_path = "/".join(path_part_list)
    is_relative = module.startswith(".")

    # == Step 3: The config and package files of a JS/TS file ====================
    config_name_list = resolve_config.get("path_config_name_list")
    if config_name_list and project_dir and not is_relative:
        match_path, is_final = _config_path_match(
            module, current_file_rel, project_file_set, project_dir, resolve_config,
            config_name_list, src_ext_with_dot,
        )
        if is_final:
            return match_path
        if is_dependency_module(
            module, current_file_rel, project_dir, resolve_config["package_file_name"],
        ):
            return None

    # == Step 4: Generate file candidates ========================================
    # A relative module already starts at the current directory, an #include <x.h> is
    # not looked up there, and neither is a module from a directory that is a package
    package_file = resolve_config.get("package_file")
    is_from_current_dir = not (
        is_relative or is_system_include
        or _is_package_dir("/".join(current_dir_part_list), project_dir, package_file)
    )
    candidate_path_list = generate_candidate_path_list(
        base_path, src_ext_with_dot, resolve_config,
        current_dir_part_list if is_from_current_dir else [],
        _entry_candidate_list(base_path, src_ext_with_dot, resolve_config, project_dir)
        if is_relative else None,
    )

    # == Step 5: Match against project_file_set, then with the source roots ======
    match_path = _first_project_path(candidate_path_list, current_file_rel, project_file_set)
    if match_path is not None or is_relative:
        return match_path
    # A module of the standard library is looked up further only in the directories
    # above the current file that are no package
    is_standard_module = bool(path_part_list) and (
        path_part_list[0] in resolve_config.get("standard_module_set", ())
    )
    root_check = (
        partial(_is_import_root, current_file_rel, project_dir, package_file)
        if is_standard_module else None
    )
    if source_root_set:
        match_path = _source_root_match(
            candidate_path_list, current_file_rel, project_file_set, source_root_set,
            root_check,
        )
        if match_path is not None:
            return match_path

    # == Step 6: A module written with enough parts, by the end of the path =======
    min_path_end_part = resolve_config.get("min_path_end_part")
    if min_path_end_part and len(path_part_list) >= min_path_end_part:
        for candidate_path in candidate_path_list:
            path_match = _find_by_path_end(
                candidate_path, current_file_rel, project_file_set, root_check,
            )
            if path_match is not None and path_match != current_file_rel:
                return path_match

    return None


def _is_package_dir(dir_rel: str, project_dir: str | None, package_file: str | None) -> bool:
    """Return whether a directory holds the file that makes it a package (Python __init__.py).

    Args:
        dir_rel: Directory relative to the project root ("" for the root; a trailing "/"
            is allowed).
        project_dir: Absolute path to the project root; None gives False.
        package_file: The package_file of the resolve config; None gives False.
    """
    return bool(
        package_file and project_dir
        and os.path.isfile(os.path.join(project_dir, dir_rel, package_file))
    )


def _is_import_root(
    current_file_rel: str, project_dir: str | None, package_file: str | None, dir_rel: str,
) -> bool:
    """Return whether a directory is above a file and is no package (_is_package_dir).

    Examples (current_file_rel "src/accounts/views.py", no src/__init__.py):
        "src/"     -> True
        "backend/" -> False

    Args:
        current_file_rel: Relative path of the file an import is written in.
        project_dir: Absolute path to the project root.
        package_file: The package_file of the resolve config.
        dir_rel: Directory relative to the project root, ending with "/".
    """
    return current_file_rel.startswith(dir_rel) and not _is_package_dir(
        dir_rel, project_dir, package_file,
    )


def _entry_candidate_list(
    base_path: str, src_ext_with_dot: str, resolve_config: dict, project_dir: str | None,
) -> list[str] | None:
    """Return the candidates of the entry files the package file of a directory names (JS/TS).

    Args:
        base_path: The path a relative module stands for, relative to the project root.
        src_ext_with_dot: Extension of the current file (with leading ".").
        resolve_config: The EXT_TO_IMPORT_RESOLVE_DICT entry of the current file.
        project_dir: Absolute path to the project root.

    Returns:
        The candidates (generate_candidate_path_list) of each path
        directory_entry_path_list gives for base_path, in that order. None for a
        language without package_file_name, without project_dir and for a path that
        leads outside the project.
    """
    package_file_name = resolve_config.get("package_file_name")
    dir_rel = inside_project(base_path) if package_file_name and project_dir else None
    if dir_rel is None:
        return None
    return [
        candidate_path
        for entry_path in directory_entry_path_list(dir_rel, project_dir, package_file_name)
        for candidate_path in generate_candidate_path_list(
            entry_path, src_ext_with_dot, resolve_config, [],
        )
    ]


def _first_project_path(
    candidate_iter: Iterable[str], current_file_rel: str, project_file_set: set[str],
) -> str | None:
    """Return the first candidate that is a project file other than the current file.

    Args:
        candidate_iter: Candidate relative paths, in priority order.
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.

    Returns:
        The candidate, None when no candidate is such a file.
    """
    for candidate_path in candidate_iter:
        if candidate_path in project_file_set and candidate_path != current_file_rel:
            return candidate_path
    return None


def _config_path_match(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
    resolve_config: dict,
    config_name_list: list[str],
    src_ext_with_dot: str,
) -> tuple[str | None, bool]:
    """Resolve a JS/TS module that is not relative through the config and package files.

    The paths are taken in order from the "imports" of the package.json around the
    current file (package_import_path_list), the "paths" and "baseUrl" of the config
    file that counts for the current file (path_config), the aliases of the bundler
    config around it (alias_path_list) and the packages of the project by their names
    (package_name_path_list); the candidates of each (generate_candidate_path_list)
    are matched against project_file_set, those of every path that end with an
    extension of last_ext_tuple after all the others.

    Args:
        module: The module name from the import statement.
        current_file_rel: Relative path of the current file from the project root.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.
        resolve_config: The EXT_TO_IMPORT_RESOLVE_DICT entry of the current file.
        config_name_list: File names of the config files (path_config_name_list).
        src_ext_with_dot: Extension of the current file (with leading ".").

    Returns:
        (the project file, whether no other way is tried). The second is True when a
        file is found, when the current file has a config file and when the module is
        one of "imports".
    """
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
    candidate_path_list = _last_ext_to_end(
        [
            candidate_path for module_path in module_path_list
            for candidate_path in generate_candidate_path_list(
                module_path, src_ext_with_dot, resolve_config, [],
            )
        ],
        resolve_config,
    )
    match_path = _first_project_path(candidate_path_list, current_file_rel, project_file_set)
    if match_path is not None:
        return match_path, True
    return None, config is not None or bool(import_path_list)


def _source_root_match(
    candidate_path_list: list[str],
    current_file_rel: str,
    project_file_set: set[str],
    source_root_set: set[str],
    root_check: Callable[[str], bool] | None = None,
) -> str | None:
    """Return the first candidate under a source root that is a project file.

    Java import "com.example.Foo" generates candidate "com/example/Foo.java"; the file
    may be at "src/main/java/com/example/Foo.java".

    Args:
        candidate_path_list: Candidate relative paths, in priority order.
        current_file_rel: Relative path of the current file from the project root.
        project_file_set: Set of file paths within the project.
        source_root_set: Source root prefixes detected in the project.
        root_check: Whether a source root may hold the candidates; None for every root.

    Returns:
        The source root + candidate path of the first candidate, with the roots nearest
        to the current file first, that is a project file other than the current file.
        None when there is none.
    """
    root_index_dict = _source_root_index(project_file_set, source_root_set)
    for candidate_path in candidate_path_list:
        source_root_list = [
            source_root for source_root in root_index_dict.get(candidate_path, [])
            if source_root + candidate_path != current_file_rel
            and (root_check is None or root_check(source_root))
        ]
        if source_root_list:
            return nearest_first(source_root_list, current_file_rel)[0] + candidate_path
    return None


def top_level_definition_name_list(file_rel: str, project_dir: str) -> list[str]:
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
        definition.name for definition in select_top_level_definitions(definition_list)
        if definition.name
        and definition.type not in ATTACHED_DEFINITION_TYPE_SET
        and definition.type not in TRANSPARENT_DEFINITION_TYPE_SET
    ))


def get_import_param_tuple(file_ext: str) -> tuple[Language, str | None] | tuple[None, None]:
    """Retrieve the Language object and query string needed for import analysis from a file extension.

    For an extension without a tree-sitter language, returns (None, None). For a
    language without import statements (SQL), the query string is None and
    extract_imports returns no imports.

    Args:
        file_ext: File extension (without ".", e.g. "py", "java").

    Returns:
        A (Language, import_query_str) tuple. (None, None) if unsupported.
    """
    language = EXT_TO_LANGUAGE_DICT.get(file_ext)
    if language is None:
        return None, None
    return language, EXT_TO_IMPORT_QUERY_DICT.get(file_ext)
