import os
import logging
import posixpath
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.path_config import inside_project

logger = logging.getLogger(__name__)

# Cache of the aliases of the bundler config files of a project:
# project_dir -> {directory relative path: aliases that count for it}
alias_cache: dict[str, dict[str, list[tuple[str, str]]]] = {}

# Key of the aliases in a bundler config, and the keys of an alias written as an object
_ALIAS_KEY = "alias"
_FIND_KEY = "find"
_REPLACEMENT_KEY = "replacement"

# Functions that join their arguments into a path, and the names that stand for the
# directory of the config file as their first argument
_JOIN_FUNCTION_NAME_SET = {"resolve", "join"}
_CONFIG_DIR_NAME_SET = {"__dirname", "import.meta.dirname"}

# Function that turns a URL into a path, the type it is given, and the name that stands
# for the URL of the config file
_URL_PATH_FUNCTION_NAME = "fileURLToPath"
_URL_TYPE_NAME = "URL"
_CONFIG_URL_NAME = "import.meta.url"


def clear_alias_path_cache() -> None:
    """Forget the aliases of every project."""
    alias_cache.clear()


def _text(node: Node) -> str:
    """Return the source text of a node."""
    return node.text.decode("utf-8")


def _string_value(node: Node | None) -> str | None:
    """Return the text of a string constant without its quotes, None for any other node."""
    if node is None or node.type != "string":
        return None
    return "".join(_text(child) for child in node.named_children if child.type == "string_fragment")


def _argument_list(node: Node) -> list[Node]:
    """Return the argument nodes of a call or of a new expression."""
    argument_node = node.child_by_field_name("arguments")
    return list(argument_node.named_children) if argument_node is not None else []


def _alias_target(node: Node, config_dir: str) -> str | None:
    """Return the directory or file an alias stands for, relative to the project root.

    Examples (config file in "web"):
        "./src"                                          -> "web/src"
        path.resolve(__dirname, "src", "app")            -> "web/src/app"
        join(__dirname, "./src")                         -> "web/src"
        fileURLToPath(new URL("./src", import.meta.url)) -> "web/src"

    Args:
        node: The value node of an alias.
        config_dir: Directory of the config file, relative to the project root.

    Returns:
        The path. None for a value written any other way, and for a path that leads
        outside the project.
    """
    text = _string_value(node)
    if text is not None:
        return inside_project(posixpath.join(config_dir, text)) if text.startswith(".") else None
    if node.type != "call_expression":
        return None
    function_node = node.child_by_field_name("function")
    argument_list = _argument_list(node)
    if function_node is None or not argument_list:
        return None
    function_name = _text(function_node).rsplit(".", 1)[-1]

    if function_name in _JOIN_FUNCTION_NAME_SET:
        part_list = [_string_value(argument) for argument in argument_list[1:]]
        if _text(argument_list[0]) not in _CONFIG_DIR_NAME_SET or None in part_list:
            return None
        return inside_project(posixpath.join(config_dir, *part_list))

    if function_name == _URL_PATH_FUNCTION_NAME and argument_list[0].type == "new_expression":
        url_node = argument_list[0]
        constructor_node = url_node.child_by_field_name("constructor")
        url_argument_list = _argument_list(url_node)
        if (
            constructor_node is None or _text(constructor_node) != _URL_TYPE_NAME
            or len(url_argument_list) != 2 or _text(url_argument_list[1]) != _CONFIG_URL_NAME
        ):
            return None
        text = _string_value(url_argument_list[0])
        return inside_project(posixpath.join(config_dir, text)) if text is not None else None
    return None


def _pair_dict(object_node: Node) -> dict[str, Node]:
    """Return the values of an object by their keys, for the keys written as a name or a string."""
    pair_dict: dict[str, Node] = {}
    for pair_node in object_node.named_children:
        key_node = pair_node.child_by_field_name("key")
        value_node = pair_node.child_by_field_name("value")
        if pair_node.type != "pair" or key_node is None or value_node is None:
            continue
        key = _string_value(key_node) if key_node.type == "string" else _text(key_node)
        if key is not None:
            pair_dict[key] = value_node
    return pair_dict


def _read_alias_list(config_rel: str, project_dir: str) -> list[tuple[str, str]]:
    """Read the aliases a bundler config file writes.

    alias: { "@": path.resolve(__dirname, "src") }
    alias: [{ find: "@", replacement: path.resolve(__dirname, "src") }]
    Every "alias" key of the file whose value is an object or an array is read. An
    alias whose name is not a string (a regular expression) or whose value is not
    written as _alias_target reads it is left out.

    Args:
        config_rel: Relative path of the config file.
        project_dir: Absolute path to the project root.

    Returns:
        (alias name, the path it stands for relative to the project root), the
        longest name first. Empty when the file cannot be parsed; the exception is
        logged.
    """
    try:
        root_node = parse_file(os.path.join(project_dir, config_rel))[0]
    except Exception as e:
        logger.warning(f"The aliases of {config_rel} cannot be read: {type(e).__name__}: {e}")
        return []
    config_dir = posixpath.dirname(config_rel)
    alias_dict: dict[str, str] = {}
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        node_stack.extend(reversed(node.children))
        key_node = node.child_by_field_name("key") if node.type == "pair" else None
        value_node = node.child_by_field_name("value") if key_node is not None else None
        if key_node is None or value_node is None or _text(key_node).strip("'\"") != _ALIAS_KEY:
            continue
        if value_node.type == "object":
            for name, target_node in _pair_dict(value_node).items():
                target = _alias_target(target_node, config_dir)
                if target is not None:
                    alias_dict.setdefault(name, target)
        elif value_node.type == "array":
            for entry_node in value_node.named_children:
                entry_dict = _pair_dict(entry_node) if entry_node.type == "object" else {}
                name = _string_value(entry_dict.get(_FIND_KEY))
                target_node = entry_dict.get(_REPLACEMENT_KEY)
                target = _alias_target(target_node, config_dir) if target_node is not None else None
                if name is not None and target is not None:
                    alias_dict.setdefault(name, target)
    return sorted(alias_dict.items(), key=lambda alias: -len(alias[0]))


def _alias_list(
    dir_rel: str, project_dir: str, config_name_list: list[str],
) -> list[tuple[str, str]]:
    """Return the aliases that count for a directory, read once per directory.

    These are the aliases of the bundler config file in the directory, else in the
    nearest directory above it; in one directory the first name of config_name_list
    that names a file is taken.

    Args:
        dir_rel: Directory relative to the project root ("" for the root).
        project_dir: Absolute path to the project root.
        config_name_list: File names of the bundler config files.

    Returns:
        The result of _read_alias_list for that file; empty without one.
    """
    dir_dict = alias_cache.setdefault(project_dir, {})
    walk_dir_list: list[str] = []
    alias_list: list[tuple[str, str]] = []
    while True:
        if dir_rel in dir_dict:
            alias_list = dir_dict[dir_rel]
            break
        walk_dir_list.append(dir_rel)
        config_rel = next(
            (
                candidate_rel for config_name in config_name_list
                if os.path.isfile(os.path.join(
                    project_dir, candidate_rel := posixpath.join(dir_rel, config_name),
                ))
            ),
            None,
        )
        if config_rel is not None:
            alias_list = _read_alias_list(config_rel, project_dir)
            break
        if not dir_rel:
            break
        dir_rel = posixpath.dirname(dir_rel)
    for walk_dir in walk_dir_list:
        dir_dict[walk_dir] = alias_list
    return alias_list


def alias_path_list(
    module: str, file_rel: str, project_dir: str, config_name_list: list[str],
) -> list[str]:
    """Return the paths a module string written with an alias of a bundler config can stand for.

    Examples (alias "@" -> "web/src", "~lib/" -> "web/lib/"):
        "@/store/user"   -> ["web/src/store/user"]
        "@"              -> ["web/src"]
        "~lib/log"       -> ["web/lib/log"]
        "@scope/pkg"     -> []

    Args:
        module: The module string of an import statement.
        file_rel: Relative path of the file the import statement is written in.
        project_dir: Absolute path to the project root.
        config_name_list: File names of the bundler config files.

    Returns:
        Paths relative to the project root: for the longest alias that is the module
        itself, or that the module starts with up to a "/", the path of the alias
        with the rest of the module. Empty when no alias matches.
    """
    dir_rel = posixpath.dirname(file_rel.replace("\\", "/"))
    for name, target in _alias_list(dir_rel, project_dir, config_name_list):
        if module == name:
            return [target]
        prefix = name if name.endswith("/") else name + "/"
        if module.startswith(prefix):
            return [posixpath.normpath(posixpath.join(target, module[len(prefix):]))]
    return []
