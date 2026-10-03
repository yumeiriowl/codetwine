import os
import re
import json
import logging
import posixpath
from dataclasses import dataclass, field
from codetwine.utils.file_utils import read_source

logger = logging.getLogger(__name__)

# Cache of the config files of a project: project_dir -> _ProjectCache
path_config_cache: dict[str, "_ProjectCache"] = {}

# The one wildcard a "paths" pattern and its targets can hold
_WILDCARD = "*"

# The keys of a config file that say which files it covers
_FILE_KEY = "files"
_INCLUDE_KEY = "include"
_EXCLUDE_KEY = "exclude"

# What "include" is when a config file sets neither it nor "files", and what "exclude"
# is when the file does not set it
_DEFAULT_INCLUDE_LIST = ["**/*"]
_DEFAULT_EXCLUDE_LIST = ["node_modules", "bower_components", "jspm_packages"]

# File name a "references" path that names a directory stands for
_REFERENCE_FILE_NAME = "tsconfig.json"


@dataclass
class PathConfig:
    """The module path settings of a tsconfig.json / jsconfig.json file."""

    # Directory a module that is not relative is looked up from ("baseUrl"), relative
    # to the project root ("" for the root); None when the file sets none
    base_dir: str | None = None
    # (pattern, targets relative to the project root) of "paths", in the order written
    path_list: list[tuple[str, list[str]]] = field(default_factory=list)

    def module_path_list(self, module: str) -> list[str]:
        """Return the paths a module that is not relative can stand for.

        Examples ("paths": {"app": ["./src/index.ts"], "app/*": ["./src/*.ts"]},
        "baseUrl": "./lib", both in the config file of the project root):
            "app"          -> ["src/index.ts", "lib/app"]
            "app/store"    -> ["src/store.ts", "lib/app/store"]
            "react"        -> ["lib/react"]

        Args:
            module: The module string of an import statement.

        Returns:
            Paths relative to the project root, without duplicates: the targets of
            the pattern that matches the module (a pattern equal to it, else the one
            with the longest part before its "*"), with the part the "*" stands for
            put in; then the module under base_dir.
        """
        module_path_list: list[str] = []
        match_tuple: tuple[int, str, list[str]] | None = None
        for pattern, target_list in self.path_list:
            prefix, wildcard, suffix = pattern.partition(_WILDCARD)
            if not wildcard:
                if pattern == module:
                    match_tuple = (len(module) + 1, "", target_list)
                continue
            is_match = (
                len(module) >= len(prefix) + len(suffix)
                and module.startswith(prefix) and module.endswith(suffix)
            )
            if is_match and (match_tuple is None or len(prefix) > match_tuple[0]):
                match_tuple = (len(prefix), module[len(prefix):len(module) - len(suffix)], target_list)
        if match_tuple is not None:
            module_path_list.extend(
                target.replace(_WILDCARD, match_tuple[1]) for target in match_tuple[2]
            )
        if self.base_dir is not None:
            module_path_list.append(posixpath.normpath(posixpath.join(self.base_dir, module)))
        return list(dict.fromkeys(module_path_list))


@dataclass
class _CoverSetting:
    """The files a config file covers, as it writes them."""

    # ("files" / "include" / "exclude" patterns, directory they are relative to); None
    # for a key neither the file nor a file it extends sets
    file_tuple: tuple[list[str], str] | None = None
    include_tuple: tuple[list[str], str] | None = None
    exclude_tuple: tuple[list[str], str] | None = None
    # Config files of the project the file names in "references"
    reference_list: list[str] = field(default_factory=list)


@dataclass
class _ProjectCache:
    """What was read of the config files of one project."""

    # Directory relative path -> config file of it or of the nearest directory above
    # it that has one; None without one
    dir_file_dict: dict[str, str | None] = field(default_factory=dict)
    # Config file -> (its path settings, the files it covers); None for a file that
    # cannot be read
    config_dict: dict[str, tuple[PathConfig, _CoverSetting] | None] = field(default_factory=dict)
    # File -> the path settings that count for it
    file_config_dict: dict[str, PathConfig | None] = field(default_factory=dict)


def read_json_file(file_rel: str, project_dir: str) -> dict | None:
    """Read a JSON file of the project that may hold comments and trailing commas.

    Args:
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.

    Returns:
        The object the file holds. None when the file cannot be read or holds no
        object; the exception is logged.
    """
    try:
        text = read_source(os.path.join(project_dir, file_rel))[0]
        option_dict = json.loads(_strip_json_comment(text))
    except (OSError, ValueError) as e:
        logger.warning(f"{file_rel} cannot be read: {type(e).__name__}: {e}")
        return None
    return option_dict if isinstance(option_dict, dict) else None


def _strip_json_comment(text: str) -> str:
    """Remove the comments and the trailing commas of a JSON text with comments.

    Args:
        text: The text of a tsconfig.json / jsconfig.json file.

    Returns:
        The text without // and /* */ comments written outside strings, and without
        a comma that is followed only by white space before "}" or "]".
    """
    part_list: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            # A string is copied as it is, up to its closing quote
            end = index + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            part_list.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
        else:
            part_list.append(char)
            index += 1

    # Drop a comma that only white space separates from the closing bracket after it
    clean_list: list[str] = []
    for part in part_list:
        if part in ("}", "]"):
            tail_index = len(clean_list) - 1
            while tail_index >= 0 and clean_list[tail_index].isspace():
                tail_index -= 1
            if tail_index >= 0 and clean_list[tail_index] == ",":
                del clean_list[tail_index]
        clean_list.append(part)
    return "".join(clean_list)


def inside_project(path: str) -> str | None:
    """Return a normalized relative path ("" for the project root), None when it leads outside the project."""
    clean_path = posixpath.normpath(path)
    if clean_path == ".":
        return ""
    if clean_path.startswith("../") or clean_path == ".." or clean_path.startswith("/"):
        return None
    return clean_path


def _extend_file_list(option_dict: dict, config_dir: str, project_dir: str) -> list[str]:
    """Return the config files of the project a config file extends.

    Args:
        option_dict: The content of the config file.
        config_dir: Directory of the config file, relative to the project root.
        project_dir: Absolute path to the project root.

    Returns:
        Relative paths in the order written. An "extends" value that is not a relative
        path (a package) or names no file of the project is left out; a value
        without ".json" names the file with it when no file has the name as written.
    """
    extend_value = option_dict.get("extends")
    value_list = extend_value if isinstance(extend_value, list) else [extend_value]
    extend_file_list: list[str] = []
    for value in value_list:
        if not isinstance(value, str) or not value.startswith("."):
            continue
        extend_rel = inside_project(posixpath.join(config_dir, value))
        if extend_rel is None:
            continue
        for candidate_rel in (extend_rel, extend_rel + ".json"):
            if os.path.isfile(os.path.join(project_dir, candidate_rel)):
                extend_file_list.append(candidate_rel)
                break
    return extend_file_list


def _glob_regex(pattern: str) -> re.Pattern:
    """Return the regular expression of an "include" / "exclude" pattern.

    "*" stands for any characters but "/", "?" for one of them and "**/" for any
    number of directories. A pattern also matches every path under the path it
    matches (a pattern that names a directory covers the directory).

    Examples:
        "src"            matches "src/a.ts", "src/lib/b.ts"
        "src/**/*.ts"    matches "src/a.ts", "src/lib/b.ts"
        "**/*.spec.ts"   matches "a.spec.ts", "lib/b.spec.ts"

    Args:
        pattern: The pattern, relative to the directory it is written for.
    """
    clean_pattern = posixpath.normpath(pattern)
    part_list: list[str] = []
    index = 0
    while index < len(clean_pattern):
        if clean_pattern.startswith("**/", index):
            part_list.append("(?:.*/)?")
            index += 3
        elif clean_pattern.startswith("**", index):
            part_list.append(".*")
            index += 2
        elif clean_pattern[index] == "*":
            part_list.append("[^/]*")
            index += 1
        elif clean_pattern[index] == "?":
            part_list.append("[^/]")
            index += 1
        else:
            part_list.append(re.escape(clean_pattern[index]))
            index += 1
    return re.compile("".join(part_list) + "(?:/.*)?$")


def _is_match(pattern_tuple: tuple[list[str], str], file_rel: str) -> bool:
    """Return whether a file matches one of the patterns of a config file.

    Args:
        pattern_tuple: (patterns, directory they are relative to).
        file_rel: Relative path of the file from the project root.
    """
    pattern_list, pattern_dir = pattern_tuple
    file_path = posixpath.relpath(file_rel, pattern_dir or ".")
    return any(_glob_regex(pattern).match(file_path) for pattern in pattern_list)


def _is_cover(cover: _CoverSetting, file_rel: str) -> bool:
    """Return whether a config file covers a file.

    A file "files" names is covered. Any other file is covered when it matches
    "include" and does not match "exclude"; "include" is every file when the config
    file sets neither it nor "files", and "exclude" is _DEFAULT_EXCLUDE_LIST when the
    config file does not set it.

    Args:
        cover: The files the config file covers.
        file_rel: Relative path of the file from the project root.
    """
    if cover.file_tuple is not None and _is_match(cover.file_tuple, file_rel):
        return True
    include_tuple = cover.include_tuple
    if include_tuple is None:
        if cover.file_tuple is not None:
            return False
        include_tuple = (_DEFAULT_INCLUDE_LIST, "")
    if not _is_match(include_tuple, file_rel):
        return False
    exclude_tuple = cover.exclude_tuple or (_DEFAULT_EXCLUDE_LIST, include_tuple[1])
    return not _is_match(exclude_tuple, file_rel)


def _reference_file_list(option_dict: dict, config_dir: str, project_dir: str) -> list[str]:
    """Return the config files of the project a config file names in "references".

    Args:
        option_dict: The content of the config file.
        config_dir: Directory of the config file, relative to the project root.
        project_dir: Absolute path to the project root.

    Returns:
        Relative paths in the order written. A path that names a directory stands for
        the tsconfig.json in it; a path that names no file of the project is left out.
    """
    reference_value = option_dict.get("references")
    reference_file_list: list[str] = []
    for reference in reference_value if isinstance(reference_value, list) else []:
        path = reference.get("path") if isinstance(reference, dict) else None
        reference_rel = inside_project(posixpath.join(config_dir, path)) if isinstance(path, str) else None
        if reference_rel is None:
            continue
        for candidate_rel in (
            reference_rel, posixpath.join(reference_rel, _REFERENCE_FILE_NAME), reference_rel + ".json",
        ):
            if os.path.isfile(os.path.join(project_dir, candidate_rel)):
                reference_file_list.append(candidate_rel)
                break
    return reference_file_list


def _read_config(
    config_rel: str, project_dir: str, visit_set: set[str],
) -> tuple[PathConfig, _CoverSetting] | None:
    """Read the module path settings of a config file, and the files it covers.

    "baseUrl" is read relative to the file that sets it. "paths" is read relative to
    the "baseUrl" in effect, else to the file that sets "paths". "files", "include"
    and "exclude" are read relative to the file that sets them. A setting of the file
    itself replaces the one of the files it extends; of several extended files, the
    one written last. "references" is read from the file itself only.

    Args:
        config_rel: Relative path of the config file.
        project_dir: Absolute path to the project root.
        visit_set: Config files being read; a file that extends itself is read once.

    Returns:
        (path settings, files the file covers). None when the file cannot be read as
        JSON with comments; the exception is logged.
    """
    if config_rel in visit_set:
        return PathConfig(), _CoverSetting()
    visit_set.add(config_rel)
    option_dict = read_json_file(config_rel, project_dir)
    if option_dict is None:
        return None

    config_dir = posixpath.dirname(config_rel)
    config, cover = PathConfig(), _CoverSetting()
    for extend_rel in _extend_file_list(option_dict, config_dir, project_dir):
        extend_tuple = _read_config(extend_rel, project_dir, visit_set)
        if extend_tuple is None:
            continue
        extend_config, extend_cover = extend_tuple
        if extend_config.base_dir is not None:
            config.base_dir = extend_config.base_dir
        if extend_config.path_list:
            config.path_list = extend_config.path_list
        cover.file_tuple = extend_cover.file_tuple or cover.file_tuple
        cover.include_tuple = extend_cover.include_tuple or cover.include_tuple
        cover.exclude_tuple = extend_cover.exclude_tuple or cover.exclude_tuple

    for key, attribute in (
        (_FILE_KEY, "file_tuple"), (_INCLUDE_KEY, "include_tuple"), (_EXCLUDE_KEY, "exclude_tuple"),
    ):
        pattern_value = option_dict.get(key)
        if isinstance(pattern_value, list):
            setattr(cover, attribute, (
                [pattern for pattern in pattern_value if isinstance(pattern, str)], config_dir,
            ))
    cover.reference_list = _reference_file_list(option_dict, config_dir, project_dir)

    compiler_dict = option_dict.get("compilerOptions")
    if not isinstance(compiler_dict, dict):
        return config, cover
    base_url = compiler_dict.get("baseUrl")
    if isinstance(base_url, str):
        config.base_dir = inside_project(posixpath.join(config_dir, base_url))
    path_dict = compiler_dict.get("paths")
    if isinstance(path_dict, dict):
        path_dir = config.base_dir if config.base_dir is not None else config_dir
        config.path_list = []
        for pattern, target_value in path_dict.items():
            target_list = [
                target_rel
                for target in (target_value if isinstance(target_value, list) else [])
                if isinstance(target, str)
                and (target_rel := inside_project(posixpath.join(path_dir, target))) is not None
            ]
            config.path_list.append((pattern, target_list))
    return config, cover


def _nearest_config_file(
    dir_rel: str, project_dir: str, config_name_list: list[str], project_cache: _ProjectCache,
) -> str | None:
    """Return the config file of a directory, else of the nearest directory above it.

    In one directory the first name of config_name_list that names a readable file is
    taken. The answer is kept per directory.

    Args:
        dir_rel: Directory relative to the project root ("" for the root).
        project_dir: Absolute path to the project root.
        config_name_list: File names of the config files.
        project_cache: The cache of the project; modified in place.

    Returns:
        Relative path of the config file, None when no directory up to the project
        root has one.
    """
    walk_dir_list: list[str] = []
    config_rel: str | None = None
    while True:
        if dir_rel in project_cache.dir_file_dict:
            config_rel = project_cache.dir_file_dict[dir_rel]
            break
        walk_dir_list.append(dir_rel)
        for config_name in config_name_list:
            candidate_rel = posixpath.join(dir_rel, config_name)
            if not os.path.isfile(os.path.join(project_dir, candidate_rel)):
                continue
            if candidate_rel not in project_cache.config_dict:
                project_cache.config_dict[candidate_rel] = _read_config(candidate_rel, project_dir, set())
            if project_cache.config_dict[candidate_rel] is not None:
                config_rel = candidate_rel
                break
        if config_rel is not None or not dir_rel:
            break
        dir_rel = posixpath.dirname(dir_rel)

    for walk_dir in walk_dir_list:
        project_cache.dir_file_dict[walk_dir] = config_rel
    return config_rel


def _cover_config(
    config_rel: str, file_rel: str, project_dir: str, project_cache: _ProjectCache,
    visit_set: set[str],
) -> PathConfig | None:
    """Return the path settings of a config file, or of one it refers to, that covers a file.

    Args:
        config_rel: Relative path of the config file.
        file_rel: Relative path of the file.
        project_dir: Absolute path to the project root.
        project_cache: The cache of the project; modified in place.
        visit_set: Config files looked at; a file is looked at once.

    Returns:
        The path settings of the config file when it covers the file (_is_cover), else
        of the first file of its "references" that does, followed through the
        references of those files. None when none of them covers the file.
    """
    if config_rel in visit_set:
        return None
    visit_set.add(config_rel)
    if config_rel not in project_cache.config_dict:
        project_cache.config_dict[config_rel] = _read_config(config_rel, project_dir, set())
    config_tuple = project_cache.config_dict[config_rel]
    if config_tuple is None:
        return None
    config, cover = config_tuple
    if _is_cover(cover, file_rel):
        return config
    for reference_rel in cover.reference_list:
        reference_config = _cover_config(reference_rel, file_rel, project_dir, project_cache, visit_set)
        if reference_config is not None:
            return reference_config
    return None


def path_config(
    file_rel: str, project_dir: str, config_name_list: list[str],
) -> PathConfig | None:
    """Return the module path settings that count for a file.

    The config file in the directory of the file, else in the nearest directory above
    it, is looked at first, then the config files it names in "references"; the first
    of them that covers the file (its "files", "include" and "exclude") gives the
    settings. When none does, the next config file further up is looked at the same
    way. A file no config file covers takes the settings of the nearest one. The
    result is kept in path_config_cache until the cache is cleared.

    Args:
        file_rel: Relative path of the file an import statement is written in.
        project_dir: Absolute path to the project root.
        config_name_list: File names of the config files ("tsconfig.json", "jsconfig.json").

    Returns:
        The PathConfig, None when no directory from the file up to the project root
        has a config file.
    """
    project_cache = path_config_cache.setdefault(project_dir, _ProjectCache())
    file_rel = file_rel.replace("\\", "/")
    if file_rel in project_cache.file_config_dict:
        return project_cache.file_config_dict[file_rel]

    nearest_config: PathConfig | None = None
    config: PathConfig | None = None
    dir_rel = posixpath.dirname(file_rel)
    while True:
        config_rel = _nearest_config_file(dir_rel, project_dir, config_name_list, project_cache)
        if config_rel is None:
            break
        if nearest_config is None:
            nearest_config = project_cache.config_dict[config_rel][0]
        config = _cover_config(config_rel, file_rel, project_dir, project_cache, set())
        config_dir = posixpath.dirname(config_rel)
        if config is not None or not config_dir:
            break
        dir_rel = posixpath.dirname(config_dir)

    project_cache.file_config_dict[file_rel] = config or nearest_config
    return project_cache.file_config_dict[file_rel]
