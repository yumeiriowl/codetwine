import os
import json
import logging
import posixpath
from dataclasses import dataclass, field
from codetwine.utils.file_utils import read_source

logger = logging.getLogger(__name__)

# Cache of the path settings that count for the directories of a project:
# project_dir -> {directory relative path: PathConfig, or None without a config file}
path_config_cache: dict[str, dict[str, "PathConfig | None"]] = {}

# The one wildcard a "paths" pattern and its targets can hold
_WILDCARD = "*"


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


def _inside_project(path: str) -> str | None:
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
        extend_rel = _inside_project(posixpath.join(config_dir, value))
        if extend_rel is None:
            continue
        for candidate_rel in (extend_rel, extend_rel + ".json"):
            if os.path.isfile(os.path.join(project_dir, candidate_rel)):
                extend_file_list.append(candidate_rel)
                break
    return extend_file_list


def _read_path_config(
    config_rel: str, project_dir: str, visit_set: set[str],
) -> PathConfig | None:
    """Read the module path settings of a config file and of the files it extends.

    "baseUrl" is read relative to the file that sets it. "paths" is read relative to
    the "baseUrl" in effect, else to the file that sets "paths". A setting of the
    file itself replaces the one of the files it extends; of several extended files,
    the one written last.

    Args:
        config_rel: Relative path of the config file.
        project_dir: Absolute path to the project root.
        visit_set: Config files being read; a file that extends itself is read once.

    Returns:
        The PathConfig. None when the file cannot be read as JSON with comments; the
        exception is logged.
    """
    if config_rel in visit_set:
        return PathConfig()
    visit_set.add(config_rel)
    try:
        text = read_source(os.path.join(project_dir, config_rel))[0]
        option_dict = json.loads(_strip_json_comment(text))
    except (OSError, ValueError) as e:
        logger.warning(
            f"The path settings of {config_rel} cannot be read: {type(e).__name__}: {e}"
        )
        return None
    if not isinstance(option_dict, dict):
        return None

    config_dir = posixpath.dirname(config_rel)
    config = PathConfig()
    for extend_rel in _extend_file_list(option_dict, config_dir, project_dir):
        extend_config = _read_path_config(extend_rel, project_dir, visit_set)
        if extend_config is not None:
            if extend_config.base_dir is not None:
                config.base_dir = extend_config.base_dir
            if extend_config.path_list:
                config.path_list = extend_config.path_list

    compiler_dict = option_dict.get("compilerOptions")
    if not isinstance(compiler_dict, dict):
        return config
    base_url = compiler_dict.get("baseUrl")
    if isinstance(base_url, str):
        config.base_dir = _inside_project(posixpath.join(config_dir, base_url))
    path_dict = compiler_dict.get("paths")
    if isinstance(path_dict, dict):
        path_dir = config.base_dir if config.base_dir is not None else config_dir
        config.path_list = []
        for pattern, target_value in path_dict.items():
            target_list = [
                target_rel
                for target in (target_value if isinstance(target_value, list) else [])
                if isinstance(target, str)
                and (target_rel := _inside_project(posixpath.join(path_dir, target))) is not None
            ]
            config.path_list.append((pattern, target_list))
    return config


def path_config(
    file_rel: str, project_dir: str, config_name_list: list[str],
) -> PathConfig | None:
    """Return the module path settings that count for a file.

    These are the settings of the config file in the directory of the file, else in
    the nearest directory above it up to the project root. In one directory the
    first name of config_name_list that names a readable file is taken. The result
    is kept in path_config_cache per directory until the cache is cleared.

    Args:
        file_rel: Relative path of the file an import statement is written in.
        project_dir: Absolute path to the project root.
        config_name_list: File names of the config files ("tsconfig.json", "jsconfig.json").

    Returns:
        The PathConfig, None when no directory from the file up to the project root
        has a config file.
    """
    dir_config_dict = path_config_cache.setdefault(project_dir, {})
    dir_rel = posixpath.dirname(file_rel.replace("\\", "/"))
    walk_dir_list: list[str] = []
    config: PathConfig | None = None
    while True:
        if dir_rel in dir_config_dict:
            config = dir_config_dict[dir_rel]
            break
        walk_dir_list.append(dir_rel)
        for config_name in config_name_list:
            config_rel = posixpath.join(dir_rel, config_name)
            if os.path.isfile(os.path.join(project_dir, config_rel)):
                config = _read_path_config(config_rel, project_dir, set())
                if config is not None:
                    break
        if config is not None or not dir_rel:
            break
        dir_rel = posixpath.dirname(dir_rel)

    for walk_dir in walk_dir_list:
        dir_config_dict[walk_dir] = config
    return config
