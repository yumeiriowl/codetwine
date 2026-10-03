import os
import posixpath
from codetwine.path_config import PathConfig, inside_project, read_json_file

# Cache of the package.json files of a project:
# project_dir -> {directory relative path: content of its package.json, or None without one}
package_file_cache: dict[str, dict[str, dict | None]] = {}

# Cache of the packages of a project: project_dir -> {package name: its directory}
package_name_cache: dict[str, tuple[set[str], dict[str, str]]] = {}

# First character of a module string a package.json "imports" entry names
_IMPORT_MODULE_START = "#"

# Keys of a package.json that name the file a package is entered through, in the order
# they are tried
_ENTRY_KEY_TUPLE = ("types", "typings", "source", "module", "main")

# Directory a package keeps its sources in, and the name of the file it is entered through
_SOURCE_DIR_NAME = "src"
_INDEX_FILE_NAME = "index"


def clear_package_path_cache() -> None:
    """Forget the package.json files of every project."""
    package_file_cache.clear()
    package_name_cache.clear()


def _package_file(dir_rel: str, project_dir: str, package_file_name: str) -> dict | None:
    """Return the content of the package.json of a directory, read once per directory.

    Args:
        dir_rel: Directory relative to the project root ("" for the root).
        project_dir: Absolute path to the project root.
        package_file_name: File name of the package file ("package.json").

    Returns:
        The object the file holds, None when the directory has no readable one.
    """
    dir_dict = package_file_cache.setdefault(project_dir, {})
    if dir_rel not in dir_dict:
        package_rel = posixpath.join(dir_rel, package_file_name)
        dir_dict[dir_rel] = (
            read_json_file(package_rel, project_dir)
            if os.path.isfile(os.path.join(project_dir, package_rel)) else None
        )
    return dir_dict[dir_rel]


def _target_list(value: object) -> list[str]:
    """Return the paths an "imports" / "exports" value names, in the order written.

    A value is a path, a list of values, or an object whose values are values (one per
    condition: "types", "import", "require", "default").

    Examples:
        "./src/a.js"                                       -> ["./src/a.js"]
        {"types": "./a.d.ts", "default": "./a.js"}         -> ["./a.d.ts", "./a.js"]
        {"import": {"default": "./a.mjs"}, "require": null} -> ["./a.mjs"]
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [target for item in value for target in _target_list(item)]
    if isinstance(value, dict):
        return [target for item in value.values() for target in _target_list(item)]
    return []


def _pattern_path_list(pattern_dict: dict, name: str, package_dir: str) -> list[str]:
    """Return the paths the entry of an "imports" / "exports" object that matches a name names.

    Args:
        pattern_dict: {name or pattern with one "*": value}.
        name: The name looked up ("#internal/a", "./sub").
        package_dir: Directory of the package file, relative to the project root.

    Returns:
        Paths relative to the project root, with the part the "*" stands for put in.
        Empty when no entry matches or the paths lead outside the project.
    """
    config = PathConfig(path_list=[
        (pattern, _target_list(value)) for pattern, value in pattern_dict.items()
    ])
    return [
        path_rel for target in config.module_path_list(name)
        if (path_rel := inside_project(posixpath.join(package_dir, target))) is not None
    ]


def package_import_path_list(
    module: str, file_rel: str, project_dir: str, package_file_name: str,
) -> list[str]:
    """Return the paths a module string of the "imports" of a package.json can stand for.

    The package.json in the directory of the file, else in the nearest directory above
    it, is read.

    Examples ("imports": {"#db": "./src/db.js", "#internal/*": "./src/internal/*.js"}
    in the package.json of the project root):
        "#db"            -> ["src/db.js"]
        "#internal/log"  -> ["src/internal/log.js"]
        "react"          -> []

    Args:
        module: The module string of an import statement.
        file_rel: Relative path of the file the import statement is written in.
        project_dir: Absolute path to the project root.
        package_file_name: File name of the package file ("package.json").

    Returns:
        Paths relative to the project root. Empty for a module that does not start
        with "#" and when the package file has no entry for it.
    """
    if not module.startswith(_IMPORT_MODULE_START):
        return []
    dir_rel = posixpath.dirname(file_rel.replace("\\", "/"))
    while True:
        package_dict = _package_file(dir_rel, project_dir, package_file_name)
        if package_dict is not None:
            import_dict = package_dict.get("imports")
            return (
                _pattern_path_list(import_dict, module, dir_rel)
                if isinstance(import_dict, dict) else []
            )
        if not dir_rel:
            return []
        dir_rel = posixpath.dirname(dir_rel)


def _package_dir_dict(
    project_dir: str, project_file_set: set[str], package_file_name: str,
) -> dict[str, str]:
    """Return the packages of a project by their names, read once per project file set.

    The package.json of every directory that holds a project file, and of the
    directories above those, is read.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.
        package_file_name: File name of the package file ("package.json").

    Returns:
        {"name" of a package.json: its directory}; of two packages of one name, the
        one whose directory comes first in path order.
    """
    cache_entry = package_name_cache.get(project_dir)
    if cache_entry is not None and cache_entry[0] == project_file_set:
        return cache_entry[1]
    dir_set: set[str] = set()
    for file_rel in project_file_set:
        dir_rel = posixpath.dirname(file_rel.replace("\\", "/"))
        while dir_rel not in dir_set:
            dir_set.add(dir_rel)
            dir_rel = posixpath.dirname(dir_rel)
    package_dir_dict: dict[str, str] = {}
    for dir_rel in sorted(dir_set):
        package_dict = _package_file(dir_rel, project_dir, package_file_name)
        name = package_dict.get("name") if package_dict is not None else None
        if isinstance(name, str) and name:
            package_dir_dict.setdefault(name, dir_rel)
    package_name_cache[project_dir] = (project_file_set, package_dir_dict)
    return package_dir_dict


def _source_path_list(path_rel: str, package_dir: str) -> list[str]:
    """Return a path of a package and the path its source has when the path names a build output.

    Examples (package_dir "packages/ui"):
        "packages/ui/dist/index.js"  -> ["packages/ui/dist/index.js", "packages/ui/src/index.js"]
        "packages/ui/index.js"       -> ["packages/ui/index.js"]

    Args:
        path_rel: A path under package_dir, relative to the project root.
        package_dir: Directory of the package, relative to the project root.

    Returns:
        The path, then the same path with its first directory under the package
        replaced by "src" when it has one that is not "src".
    """
    inner_part_list = posixpath.relpath(path_rel, package_dir or ".").split("/")
    if len(inner_part_list) < 2 or inner_part_list[0] in (_SOURCE_DIR_NAME, ".."):
        return [path_rel]
    return [path_rel, posixpath.join(package_dir, _SOURCE_DIR_NAME, *inner_part_list[1:])]


def package_name_path_list(
    module: str, project_dir: str, project_file_set: set[str], package_file_name: str,
) -> list[str]:
    """Return the paths a module string that names a package of the project can stand for.

    Examples (packages/ui/package.json: {"name": "@acme/ui", "main": "./dist/index.js",
    "exports": {".": "./dist/index.js", "./button": "./dist/button.js"}}):
        "@acme/ui"         -> ["packages/ui/dist/index.js", "packages/ui/src/index.js",
                               "packages/ui/src/index", "packages/ui/index"]
        "@acme/ui/button"  -> ["packages/ui/dist/button.js", "packages/ui/src/button.js",
                               "packages/ui/button", "packages/ui/src/button"]
        "react"            -> []

    Args:
        module: The module string of an import statement.
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.
        package_file_name: File name of the package file ("package.json").

    Returns:
        Paths relative to the project root, without duplicates: the paths "exports"
        names for the part of the module after the package name, for the package
        itself also the paths of _ENTRY_KEY_TUPLE, each followed by the path of its
        source (_source_path_list); then the part after the package name under the
        package and under its "src", or the index file of the package.
    """
    package_dir_dict = _package_dir_dict(project_dir, project_file_set, package_file_name)
    part_list = module.split("/")
    name = "/".join(part_list[:2]) if module.startswith("@") else part_list[0]
    package_dir = package_dir_dict.get(name)
    if package_dir is None:
        return []
    package_dict = _package_file(package_dir, project_dir, package_file_name) or {}
    inner_path = module[len(name):].lstrip("/")

    target_list: list[str] = []
    export_value = package_dict.get("exports")
    export_key = "./" + inner_path if inner_path else "."
    if isinstance(export_value, dict) and any(key.startswith(".") for key in export_value):
        target_list.extend(_pattern_path_list(export_value, export_key, package_dir))
    elif not inner_path:
        target_list.extend(
            path_rel for target in _target_list(export_value)
            if (path_rel := inside_project(posixpath.join(package_dir, target))) is not None
        )
    if not inner_path:
        for entry_key in _ENTRY_KEY_TUPLE:
            entry_value = package_dict.get(entry_key)
            path_rel = (
                inside_project(posixpath.join(package_dir, entry_value))
                if isinstance(entry_value, str) else None
            )
            if path_rel is not None:
                target_list.append(path_rel)

    path_list = [
        source_rel for path_rel in target_list
        for source_rel in _source_path_list(path_rel, package_dir)
    ]
    if inner_path:
        path_list.extend([
            posixpath.join(package_dir, inner_path),
            posixpath.join(package_dir, _SOURCE_DIR_NAME, inner_path),
        ])
    else:
        path_list.extend([
            posixpath.join(package_dir, _SOURCE_DIR_NAME, _INDEX_FILE_NAME),
            posixpath.join(package_dir, _INDEX_FILE_NAME),
        ])
    return list(dict.fromkeys(path_list))
