import os
import logging
import tomllib
from dataclasses import dataclass, field
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    extract_definitions,
    select_top_level_definitions,
)
from codetwine.extractors.rust_path import mod_declaration, rust_import_list
from codetwine.config.settings import EXT_TO_DEFINITION_DICT

logger = logging.getLogger(__name__)

# File names whose child modules live in the file's own directory
_OWN_DIR_FILE_NAME_TUPLE = ("mod.rs", "lib.rs", "main.rs")

# Maximum number of use declarations followed when resolving one path
_MAX_USE_HOP = 8

# Cache of module trees: project_dir -> (project file set the tree was built from, tree)
module_tree_cache: dict[str, tuple[set[str], "RustModuleTree"]] = {}


def _join_path(*part_tuple: str) -> str:
    """Join relative path parts with "/" and normalize "." and "..".

    Examples:
        ("src", "util", "mod.rs")   -> "src/util/mod.rs"
        ("", "lib.rs")              -> "lib.rs"
        ("src/a", "../b.rs")        -> "src/b.rs"
    """
    return os.path.normpath("/".join(p for p in part_tuple if p)).replace("\\", "/")


@dataclass
class _ModuleFile:
    """Top-level contents of one .rs file."""

    # Names of top-level definitions
    definition_name_set: set[str] = field(default_factory=set)
    # Bound name -> segments of its use path
    use_dict: dict[str, list[str]] = field(default_factory=dict)
    # Segments of glob imports (use a::*)
    glob_list: list[list[str]] = field(default_factory=list)
    # (module name, #[path] value) of each mod declaration without a body
    mod_list: list[tuple[str, str | None]] = field(default_factory=list)


def _read_module_file(file_abs: str) -> _ModuleFile:
    """Parse a .rs file and collect its top-level definitions, use declarations and mod declarations.

    Args:
        file_abs: Absolute path of the .rs file.

    Returns:
        A _ModuleFile.
    """
    root_node = parse_file(file_abs)[0]
    module_file = _ModuleFile()

    definition_list = extract_definitions(root_node, EXT_TO_DEFINITION_DICT["rs"])
    module_file.definition_name_set = {
        d.name for d in select_top_level_definitions(definition_list)
        if d.type not in ATTACHED_DEFINITION_TYPE_SET
    }

    for child in root_node.children:
        if child.type == "mod_item":
            declaration = mod_declaration(child)
            if declaration is not None:
                module_file.mod_list.append(declaration)
        elif child.type in ("use_declaration", "extern_crate_declaration"):
            for module, name, _ in rust_import_list(child):
                if name == "*":
                    module_file.glob_list.append(module.split("::"))
                elif name:
                    module_file.use_dict[name] = module.split("::")
    return module_file


def _ancestor_dir_set(file_rel_set: set[str]) -> set[str]:
    """Return the directories that hold the files and all their parent directories.

    Examples:
        {"a/b/x.rs", "c.rs"} -> {"a/b", "a", ""}

    Args:
        file_rel_set: Relative file paths.

    Returns:
        Relative directory paths; "" is the project root.
    """
    dir_set: set[str] = set()
    for file_rel in file_rel_set:
        directory = os.path.dirname(file_rel)
        while directory not in dir_set:
            dir_set.add(directory)
            if not directory:
                break
            directory = os.path.dirname(directory)
    return dir_set


def _read_cargo_package_dict(project_dir: str, rs_file_set: set[str]) -> dict[str, dict]:
    """Read the Cargo.toml files of the project that have a [package] table.

    A Cargo.toml is looked for in every directory that holds a .rs file and in their parents.

    Args:
        project_dir: Absolute path to the project root.
        rs_file_set: Relative paths of the .rs files of the project.

    Returns:
        A {package directory relative path: parsed Cargo.toml} dict.
    """
    cargo_package_dict: dict[str, dict] = {}
    for directory in sorted(_ancestor_dir_set(rs_file_set)):
        cargo_path = os.path.join(project_dir, directory, "Cargo.toml")
        if not os.path.isfile(cargo_path):
            continue
        try:
            with open(cargo_path, "rb") as f:
                cargo_dict = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            logger.warning(f"Failed to read {cargo_path}: {e}")
            continue
        if isinstance(cargo_dict.get("package"), dict):
            cargo_package_dict[directory] = cargo_dict
    return cargo_package_dict


def _crate_lib_dict(cargo_package_dict: dict[str, dict], rs_file_set: set[str]) -> dict[str, str]:
    """Map each crate name to its library root file.

    Args:
        cargo_package_dict: Return value of _read_cargo_package_dict.
        rs_file_set: Relative paths of the .rs files of the project.

    Returns:
        A {crate name: library root file relative path} dict. The crate name is
        [lib] name, or [package] name with "-" replaced by "_".
    """
    crate_lib_dict: dict[str, str] = {}
    for directory, cargo_dict in cargo_package_dict.items():
        package = cargo_dict["package"]
        lib = cargo_dict.get("lib") if isinstance(cargo_dict.get("lib"), dict) else {}
        crate_name = str(lib.get("name") or package.get("name", "")).replace("-", "_")
        lib_rel = _join_path(directory, str(lib.get("path", "src/lib.rs")))
        if crate_name and lib_rel in rs_file_set:
            crate_lib_dict[crate_name] = lib_rel
    return crate_lib_dict


class RustModuleTree:
    """The module tree of the .rs files of a project, built from their mod declarations.

    A file with no parent module is a crate root (lib.rs, main.rs, a file under
    tests/, examples/, benches/, build.rs, etc.).
    """

    def __init__(self, project_dir: str, rs_file_set: set[str]) -> None:
        """Parse every .rs file and link each mod declaration to its file.

        Args:
            project_dir: Absolute path to the project root.
            rs_file_set: Relative paths of the .rs files of the project.
        """
        self.module_file_dict: dict[str, _ModuleFile] = {
            file_rel: _read_module_file(os.path.join(project_dir, file_rel))
            for file_rel in sorted(rs_file_set)
        }
        self.child_dict: dict[str, dict[str, str]] = {f: {} for f in self.module_file_dict}
        self.parent_dict: dict[str, str] = {}
        cargo_package_dict = _read_cargo_package_dict(project_dir, rs_file_set)
        self.crate_lib_dict = _crate_lib_dict(cargo_package_dict, rs_file_set)
        # Package directory -> whether the package is edition 2015 (the edition when none is written)
        self._edition_2015_dict = {
            directory: cargo_dict["package"].get("edition", "2015") == "2015"
            for directory, cargo_dict in cargo_package_dict.items()
        }
        # (file, segments) -> result of resolve, for calls from outside the tree
        self._resolve_cache: dict[tuple[str, tuple[str, ...]], tuple[str | None, tuple[str, ...]]] = {}
        self._link_modules()

    def _link_modules(self) -> None:
        """Fill child_dict and parent_dict from the mod declarations.

        mod name; is looked up as <dir>/name.rs and <dir>/name/mod.rs, where <dir> is
        the file's directory for mod.rs / lib.rs / main.rs and <dir>/<file stem> otherwise;
        #[path = "..."] is taken relative to the file's directory. A declaration
        that none of these match is then looked up in the file's directory, when
        that file has no parent yet.
        When two declarations share a name (#[cfg] alternatives), the first one is the
        child, and every matched file gets the declaring file as its parent.
        """
        fallback_list: list[tuple[str, str, list[str]]] = []
        for file_rel, module_file in self.module_file_dict.items():
            directory, file_name = os.path.split(file_rel)
            if file_name in _OWN_DIR_FILE_NAME_TUPLE:
                own_dir = directory
            else:
                own_dir = _join_path(directory, file_name[:-3])
            for name, path_value in module_file.mod_list:
                if path_value:
                    candidate_list = [_join_path(directory, path_value)]
                else:
                    candidate_list = [_join_path(own_dir, name + ".rs"), _join_path(own_dir, name, "mod.rs")]
                target = next(
                    (c for c in candidate_list if c in self.module_file_dict and c != file_rel), None,
                )
                if target is not None:
                    self._link(file_rel, name, target)
                elif not path_value and own_dir != directory:
                    fallback_list.append((file_rel, name, [
                        _join_path(directory, name + ".rs"), _join_path(directory, name, "mod.rs"),
                    ]))

        for file_rel, name, candidate_list in fallback_list:
            target = next(
                (c for c in candidate_list
                 if c in self.module_file_dict and c != file_rel and c not in self.parent_dict),
                None,
            )
            if target is not None:
                self._link(file_rel, name, target)

    def _link(self, parent_rel: str, name: str, child_rel: str) -> None:
        """Register child_rel as the module name of parent_rel."""
        self.child_dict[parent_rel].setdefault(name, child_rel)
        self.parent_dict.setdefault(child_rel, parent_rel)

    def crate_root(self, file_rel: str) -> str:
        """Return the crate root file of a file by following its parent modules.

        Args:
            file_rel: Relative path of a .rs file of the project.

        Returns:
            Relative path of the file with no parent module reached from file_rel.
        """
        visit_set = {file_rel}
        while file_rel in self.parent_dict:
            file_rel = self.parent_dict[file_rel]
            if file_rel in visit_set:
                break
            visit_set.add(file_rel)
        return file_rel

    def _is_edition_2015(self, file_rel: str) -> bool:
        """Return whether the nearest package above the file is edition 2015.

        Args:
            file_rel: Relative path of a .rs file of the project.

        Returns:
            False when no Cargo.toml with a [package] table is above the file.
        """
        directory = os.path.dirname(file_rel)
        while True:
            if directory in self._edition_2015_dict:
                return self._edition_2015_dict[directory]
            if not directory:
                return False
            directory = os.path.dirname(directory)

    def _is_found(
        self, target_rel: str | None, rest_list: list[str], segment_list: list[str],
    ) -> bool:
        """Return whether resolving through a glob import reached what segment_list names.

        Args:
            target_rel: File returned by resolve / _walk.
            rest_list: Segments returned with it.
            segment_list: Segments that were looked up behind the glob path.

        Returns:
            True when the glob path was consumed and then either the first segment was
            consumed as a module, or target_rel defines the first remaining segment
            (the segments after it being the same as in segment_list).
        """
        if target_rel is None:
            return False
        if len(rest_list) < len(segment_list):
            return rest_list == segment_list[len(segment_list) - len(rest_list):]
        return (
            len(rest_list) == len(segment_list)
            and rest_list[1:] == segment_list[1:]
            and rest_list[0] in self.module_file_dict[target_rel].definition_name_set
        )

    def resolve(
        self,
        file_rel: str,
        segment_list: list[str],
        hop: int = 0,
        visit_set: set[tuple[str, tuple[str, ...]]] | None = None,
    ) -> tuple[str | None, list[str]]:
        """Resolve a path written in a file to the file that defines what it names.

        The first segment is looked up in this order:
            crate / self / super    -> the crate root / this module / the parent module
            a child module          -> that module's file
            a name bound by use     -> the path of that use declaration
            a definition of the file -> the file itself
            a crate of the project  -> the crate's library root (Cargo.toml)
            a name of a glob import (use super::*) -> the module the glob path leads to
            a child module of the crate root, in an edition 2015 package

        A call without visit_set is cached per (file, segments).

        Args:
            file_rel: Relative path of the file the path is written in.
            segment_list: Segments of the path (e.g. ["crate", "util", "log"]).
            hop: Number of use declarations followed so far.
            visit_set: (file, segments) pairs already looked up in this resolution;
                a pair looked up again resolves to None.

        Returns:
            (file relative path, segments not consumed by modules). The file is None
            when the path does not lead into the project (std, an external crate, etc.).
        """
        if not segment_list or file_rel not in self.module_file_dict:
            return None, segment_list
        state = (file_rel, tuple(segment_list))
        if visit_set is None:
            if state not in self._resolve_cache:
                target_rel, rest_list = self._resolve_head(file_rel, segment_list, hop, {state})
                self._resolve_cache[state] = (target_rel, tuple(rest_list))
            target_rel, rest_tuple = self._resolve_cache[state]
            return target_rel, list(rest_tuple)
        if state in visit_set:
            return None, segment_list
        visit_set.add(state)
        return self._resolve_head(file_rel, segment_list, hop, visit_set)

    def _resolve_head(
        self,
        file_rel: str,
        segment_list: list[str],
        hop: int,
        visit_set: set[tuple[str, tuple[str, ...]]],
    ) -> tuple[str | None, list[str]]:
        """Look up the first segment of a path in the order described in resolve."""
        head = segment_list[0]
        module_file = self.module_file_dict[file_rel]
        if head == "crate":
            return self._walk(self.crate_root(file_rel), segment_list[1:], hop, visit_set)
        if head in ("self", "super") or head in self.child_dict[file_rel]:
            return self._walk(file_rel, segment_list, hop, visit_set)
        use_list = module_file.use_dict.get(head)
        if use_list is not None and use_list != [head] and hop < _MAX_USE_HOP:
            return self.resolve(file_rel, use_list + segment_list[1:], hop + 1, visit_set)
        if head in module_file.definition_name_set:
            return file_rel, segment_list
        if head in self.crate_lib_dict:
            return self._walk(self.crate_lib_dict[head], segment_list[1:], hop, visit_set)
        if hop < _MAX_USE_HOP:
            for glob in module_file.glob_list:
                target_rel, rest_list = self.resolve(file_rel, glob + segment_list, hop + 1, visit_set)
                if self._is_found(target_rel, rest_list, segment_list):
                    return target_rel, rest_list
        root_rel = self.crate_root(file_rel)
        if root_rel != file_rel and head in self.child_dict[root_rel] and self._is_edition_2015(file_rel):
            return self._walk(root_rel, segment_list, hop, visit_set)
        return None, segment_list

    def _walk(
        self,
        file_rel: str,
        segment_list: list[str],
        hop: int,
        visit_set: set[tuple[str, tuple[str, ...]]],
    ) -> tuple[str | None, list[str]]:
        """Follow the segments through child and parent modules starting from a file.

        When the first remaining segment is not defined in the reached file, it is
        followed through that file's use declarations (re-exports) and glob imports.

        Args:
            file_rel: Relative path of the module file to start from.
            segment_list: Segments to follow (self / super / module names / item names).
            hop: Number of use declarations followed so far.
            visit_set: (file, segments) pairs already looked up in this resolution.

        Returns:
            (file relative path, segments not consumed by modules). The file is None
            when super goes above a crate root.
        """
        current_rel = file_rel
        rest_list = list(segment_list)
        while rest_list:
            segment = rest_list[0]
            if segment == "super":
                if current_rel not in self.parent_dict:
                    return None, rest_list
                current_rel = self.parent_dict[current_rel]
            elif segment in self.child_dict[current_rel]:
                current_rel = self.child_dict[current_rel][segment]
            elif segment != "self":
                break
            rest_list.pop(0)

        if not rest_list or rest_list[0] == "*" or hop >= _MAX_USE_HOP:
            return current_rel, rest_list
        module_file = self.module_file_dict[current_rel]
        name = rest_list[0]
        if name in module_file.definition_name_set:
            return current_rel, rest_list

        # The name is brought into the module by a use declaration (pub use a::Name)
        use_list = module_file.use_dict.get(name)
        if use_list is not None:
            target_rel, target_rest_list = self.resolve(
                current_rel, use_list + rest_list[1:], hop + 1, visit_set,
            )
            if target_rel is not None:
                return target_rel, target_rest_list
            return current_rel, rest_list

        # The name comes from a glob import (pub use a::*) of a module that has it
        for glob in module_file.glob_list:
            target_rel, target_rest_list = self.resolve(
                current_rel, glob + rest_list, hop + 1, visit_set,
            )
            if self._is_found(target_rel, target_rest_list, rest_list):
                return target_rel, target_rest_list
        return current_rel, rest_list


def _get_module_tree(project_dir: str, project_file_set: set[str]) -> RustModuleTree:
    """Return the module tree of the project, building it on the first call.

    The tree is kept in module_tree_cache until the project file set changes or the
    cache is cleared.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The RustModuleTree of the .rs files in project_file_set.
    """
    cache_entry = module_tree_cache.get(project_dir)
    if cache_entry is not None:
        cache_file_set, module_tree = cache_entry
        if cache_file_set is project_file_set:
            return module_tree
        if cache_file_set == project_file_set:
            module_tree_cache[project_dir] = (project_file_set, module_tree)
            return module_tree

    module_tree = RustModuleTree(project_dir, {f for f in project_file_set if f.endswith(".rs")})
    module_tree_cache[project_dir] = (project_file_set, module_tree)
    return module_tree


def _is_path_attribute(module: str) -> bool:
    """Return whether an ImportInfo module is the value of a #[path] attribute ("unix.rs")."""
    return "." in module or "/" in module


def _resolve_import(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> tuple[str | None, list[str]]:
    """Resolve a module path of a Rust import through the module tree of the project.

    Args:
        module: A module string of an ImportInfo (segments joined with "::").
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.

    Returns:
        (file relative path, segments not consumed by modules). The file is None
        when the path leads outside the project or to the current file itself.
    """
    module_tree = _get_module_tree(project_dir, project_file_set)
    target_rel, rest_list = module_tree.resolve(current_file_rel, module.split("::"))
    if target_rel == current_file_rel:
        return None, rest_list
    return target_rel, rest_list


def resolve_rust_module_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> str | None:
    """Resolve a module path of a Rust import to a file within the project.

    Examples (module -> file):
        "crate::config::Settings"   -> "src/config.rs"
        "self::util"                -> "src/util.rs" or "src/util/mod.rs"
        "unix.rs" (#[path] value)   -> "src/unix.rs" (relative to the current file's directory)
        "std::collections::HashMap" -> None

    Args:
        module: A module string of an ImportInfo (segments joined with "::",
            or the value of a #[path] attribute).
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.

    Returns:
        A project-internal file path. None when the path leads outside the project
        or to the current file itself.
    """
    if _is_path_attribute(module):
        target_rel = _join_path(os.path.dirname(current_file_rel), module)
        return target_rel if target_rel in project_file_set and target_rel != current_file_rel else None
    return _resolve_import(module, current_file_rel, project_file_set, project_dir)[0]


def rust_import_name_dict(
    module: str,
    name_list: list[str],
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> dict[str, str | None]:
    """Return the names a Rust import binds that refer to a definition, with the path the
    definition has in the resolved file.

    A name that refers to a module is left out. "*" is kept only when the path is a
    module (use a::*), not an item (use a::Enum::*).

    Examples (module, name_list -> result):
        "crate::config::Settings", ["Settings"]          -> {"Settings": "Settings"}
        "crate::shape::Circle", ["Round"]                 -> {"Round": "Circle"}
        "crate::flags::gen_bash", ["gen_bash"]
            (flags has pub use self::bash::generate as gen_bash)   -> {"gen_bash": "generate"}
        "Settings::new", ["Settings::new"]                -> {"Settings::new": "Settings::new"}
        "self::config", ["config"]                        -> {}
        "crate::util", ["*"]                              -> {"*": None}
        "crate::shape::Kind", ["*"]                       -> {}

    Args:
        module: A module string of an ImportInfo.
        name_list: ImportInfo.names of the import.
        current_file_rel: Relative path of the file the import is written in.
        project_file_set: Set of file paths within the project.
        project_dir: Absolute path to the project root.

    Returns:
        A {bound name: path of the definition in the resolved file ("::"-joined)} dict.
        The value is None for "*". Empty when the import does not resolve.
    """
    if _is_path_attribute(module):
        return {}
    target_rel, rest_list = _resolve_import(module, current_file_rel, project_file_set, project_dir)
    if target_rel is None:
        return {}

    name_dict: dict[str, str | None] = {}
    for name in name_list:
        if name == "*":
            if not rest_list:
                name_dict[name] = None
        elif rest_list:
            name_dict[name] = "::".join(rest_list)
    return name_dict
