import os
import logging
import tomllib
from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.parsers.ts_node import node_text
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    DefinitionInfo,
    extract_definitions,
    select_top_level_definitions,
)
from codetwine.extractors.rust_path import (
    UseCacheDict,
    inline_module_name,
    mod_declaration,
    rust_import_list,
)
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import EXT_TO_DEFINITION_DICT

logger = logging.getLogger(__name__)

# File names whose child modules live in the file's own directory
_OWN_DIR_FILE_NAME_TUPLE = ("mod.rs", "lib.rs", "main.rs")

# Maximum number of use declarations followed when resolving one path
_MAX_USE_HOP = 8

# Cache of module trees: project_dir -> (project file set the tree was built from, tree)
module_tree_cache_dict: dict[str, tuple[set[str], "RustModuleTree"]] = {}

# A module: (relative path of its file, names of the inline modules it is nested in,
# outermost first; () for the file itself)
_Scope = tuple[str, tuple[str, ...]]


def _join_path(*part_tuple: str) -> str:
    """Join relative path parts with "/" and normalize "." and "..".

    Examples:
        ("src", "util", "mod.rs")   -> "src/util/mod.rs"
        ("", "lib.rs")              -> "lib.rs"
        ("src/a", "../b.rs")        -> "src/b.rs"
    """
    return os.path.normpath("/".join(part for part in part_tuple if part)).replace("\\", "/")


def _mod_file_candidate_list(directory: str, name: str) -> list[str]:
    """Return the files a declaration mod name; without #[path] is looked up as in a directory.

    Examples:
        ("src/util", "log")   -> ["src/util/log.rs", "src/util/log/mod.rs"]

    Args:
        directory: Relative path of the directory to look in ("" is the project root).
        name: Name of the module.

    Returns:
        <directory>/<name>.rs and <directory>/<name>/mod.rs, in this order.
    """
    return [_join_path(directory, name + ".rs"), _join_path(directory, name, "mod.rs")]


@dataclass
class _ModuleFile:
    """Contents written directly in one .rs file, or in one inline module of it."""

    # Names of the definitions written directly in it
    definition_name_set: set[str] = field(default_factory=set)
    # Bound name -> segments of its use path, written from the file's module
    use_dict: dict[str, list[str]] = field(default_factory=dict)
    # Segments of glob imports (use a::*), written from the file's module
    glob_list: list[list[str]] = field(default_factory=list)
    # (module name, #[path] value) of each mod declaration without a body (a file only)
    mod_list: list[tuple[str, str | None]] = field(default_factory=list)
    # Name of an enum written directly in it -> names of its variants
    variant_dict: dict[str, list[str]] = field(default_factory=dict)
    # Name of an inline module written directly in it (mod name { ... }) -> its contents
    inline_dict: dict[str, "_ModuleFile"] = field(default_factory=dict)


def _enum_variant(node: Node) -> tuple[str, list[str]] | None:
    """Read the name of an enum and the names of its variants.

    Args:
        node: An enum_item node.

    Returns:
        (enum name, variant names in the order written), None when the enum has no
        name or no body.
    """
    name_node = node.child_by_field_name("name")
    body_node = node.child_by_field_name("body")
    if name_node is None or body_node is None:
        return None
    variant_name_list: list[str] = []
    for child in body_node.named_children:
        variant_name_node = child.child_by_field_name("name") if child.type == "enum_variant" else None
        if variant_name_node is not None:
            variant_name_list.append(node_text(variant_name_node))
    return node_text(name_node), variant_name_list


def _read_module_scope(
    scope_node: Node,
    definition_list: list[DefinitionInfo],
    use_cache_dict: UseCacheDict,
    module_name_tuple: tuple[str, ...] = (),
) -> _ModuleFile:
    """Collect what a file or the body of an inline module writes directly in it.

    What is collected is the definitions, use declarations, mod declarations, enum
    variants and inline modules.

    Args:
        scope_node: The root node of the file, or the body of an inline module.
        definition_list: Definitions of the whole file (extract_definitions).
        use_cache_dict: Cache of the use declarations of the file's blocks and inline
            modules, shared by the calls for one file.
        module_name_tuple: Names of the inline modules from the file's module down to
            scope_node, outermost first; empty for the file.

    Returns:
        A _ModuleFile. The paths of the use declarations of an inline module are
        written from the file's module (rust_import_list, _rebase_scope_path). The mod
        declarations without a body are read for the file only.
    """
    module_file = _ModuleFile()
    is_file = scope_node.parent is None
    scope_definition_list = definition_list if is_file else [
        definition for definition in definition_list
        if scope_node.start_byte <= definition.start_byte
        and definition.end_byte <= scope_node.end_byte
    ]
    module_file.definition_name_set = {
        definition.name for definition in select_top_level_definitions(scope_definition_list)
        if definition.type not in ATTACHED_DEFINITION_TYPE_SET
    }

    for child in scope_node.children:
        if child.type == "mod_item":
            name = inline_module_name(child)
            declaration = mod_declaration(child) if is_file else None
            if name:
                module_file.inline_dict[name] = _read_module_scope(
                    child.child_by_field_name("body"), definition_list, use_cache_dict,
                    (*module_name_tuple, name),
                )
            elif declaration is not None:
                module_file.mod_list.append(declaration)
        elif child.type == "enum_item":
            enum_variant = _enum_variant(child)
            if enum_variant is not None:
                module_file.variant_dict[enum_variant[0]] = enum_variant[1]
        elif child.type in ("use_declaration", "extern_crate_declaration"):
            for module, name, _, _ in rust_import_list(child, use_cache_dict):
                if name == "*":
                    module_file.glob_list.append(module.split("::"))
                elif name:
                    module_file.use_dict[name] = module.split("::")
    if module_name_tuple:
        _rebase_scope_path(module_file, module_name_tuple)
    return module_file


def _rebase_scope_path(module_file: _ModuleFile, module_name_tuple: tuple[str, ...]) -> None:
    """Rewrite the use paths of an inline module that start with its own names from the file's module.

    A path whose first segment is a definition, an inline module or a name another use
    declaration binds in the module gets self and the names of the inline modules in
    front of it; other paths are kept.

    Examples (inside mod o { mod imp { ... } }):
        ["imp", "g"]          -> ["self", "o", "imp", "g"]
        ["self", "o", "g"]    -> ["self", "o", "g"]
        ["std", "fmt"]        -> ["std", "fmt"]

    Args:
        module_file: The _ModuleFile of the inline module; its use_dict and glob_list
            are rewritten in place.
        module_name_tuple: Names of the inline modules from the file's module down to
            this one, outermost first.
    """
    scope_name_set = module_file.definition_name_set | module_file.inline_dict.keys() | {
        name for name, segment_list in module_file.use_dict.items() if segment_list[0] != name
    }
    prefix_list = ["self", *module_name_tuple]

    def rebase(segment_list: list[str]) -> list[str]:
        """Put prefix_list in front of a path that starts with a name of the module."""
        return [*prefix_list, *segment_list] if segment_list[0] in scope_name_set else segment_list

    module_file.use_dict = {
        name: rebase(segment_list) for name, segment_list in module_file.use_dict.items()
    }
    module_file.glob_list = [rebase(segment_list) for segment_list in module_file.glob_list]


def _read_module_file(file_abs: str) -> _ModuleFile:
    """Parse a .rs file and collect what it and its inline modules write directly in them (_read_module_scope).

    Args:
        file_abs: Absolute path of the .rs file.

    Returns:
        The _ModuleFile of the file.
    """
    root_node = parse_file(file_abs)[0]
    definition_list = extract_definitions(root_node, EXT_TO_DEFINITION_DICT["rs"])
    return _read_module_scope(root_node, definition_list, {})


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
        package_dict = cargo_dict["package"]
        lib_dict = cargo_dict.get("lib") if isinstance(cargo_dict.get("lib"), dict) else {}
        crate_name = str(lib_dict.get("name") or package_dict.get("name", "")).replace("-", "_")
        lib_rel = _join_path(directory, str(lib_dict.get("path", "src/lib.rs")))
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
        self.child_dict: dict[str, dict[str, str]] = {file_rel: {} for file_rel in self.module_file_dict}
        self.parent_dict: dict[str, str] = {}
        cargo_package_dict = _read_cargo_package_dict(project_dir, rs_file_set)
        self.crate_lib_dict = _crate_lib_dict(cargo_package_dict, rs_file_set)
        # Package directory -> whether the package is edition 2015 (the edition when none is written)
        self._edition_2015_dict = {
            directory: cargo_dict["package"].get("edition", "2015") == "2015"
            for directory, cargo_dict in cargo_package_dict.items()
        }
        # (file, segments) -> result of resolve, for calls from outside the tree
        self._resolve_cache_dict: dict[tuple[str, tuple[str, ...]], tuple[str | None, tuple[str, ...]]] = {}
        # Scope -> the scopes its glob imports lead to, None while they are being read
        self._glob_target_dict: dict[_Scope, list[_Scope] | None] = {}
        # (scope, name) -> whether the scope or a scope its glob imports lead to has the name
        self._glob_reach_dict: dict[tuple[_Scope, str], bool] = {}
        self._link_module()

    def _link_module(self) -> None:
        """Fill child_dict and parent_dict from the mod declarations.

        mod name; is looked up as <dir>/name.rs and <dir>/name/mod.rs, where <dir> is
        the file's directory for mod.rs / lib.rs / main.rs and <dir>/<file stem> otherwise;
        #[path = "..."] is taken relative to the file's directory. A declaration
        that none of these match is then looked up in the file's directory: the file
        found there is taken when it has no parent yet, and after that also when the
        declaring file has no parent (a crate root; tests/a.rs and tests/b.rs can each
        declare mod common; for tests/common/mod.rs).
        When two declarations share a name (#[cfg] alternatives), the first one is the
        child. A file declared by several files gets the first of them as its parent.
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
                    candidate_list = _mod_file_candidate_list(own_dir, name)
                target = next(
                    (
                        candidate for candidate in candidate_list
                        if candidate in self.module_file_dict and candidate != file_rel
                    ),
                    None,
                )
                if target is not None:
                    self._link(file_rel, name, target)
                elif not path_value and own_dir != directory:
                    fallback_list.append((file_rel, name, _mod_file_candidate_list(directory, name)))

        for file_rel, name, candidate_list in fallback_list:
            target = next(
                (
                    candidate for candidate in candidate_list
                    if candidate in self.module_file_dict and candidate != file_rel
                    and candidate not in self.parent_dict
                ),
                None,
            )
            if target is not None:
                self._link(file_rel, name, target)

        # A file with no parent takes the file found in its own directory also when that
        # file is the module of another file already
        for file_rel, name, candidate_list in fallback_list:
            if file_rel in self.parent_dict or name in self.child_dict[file_rel]:
                continue
            target = next(
                (
                    candidate for candidate in candidate_list
                    if candidate in self.module_file_dict and candidate != file_rel
                ),
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

    def _inline_module(self, file_rel: str, name_list: list[str]) -> _ModuleFile | None:
        """Return the inline module a file reaches through the names of nested inline modules.

        Args:
            file_rel: Relative path of a .rs file of the project.
            name_list: Names of inline modules, outermost first ([] is the file itself).

        Returns:
            The _ModuleFile of the inline module (of the file for []), None when a name
            is not an inline module written directly in the one before it.
        """
        module_file = self.module_file_dict[file_rel]
        for name in name_list:
            module_file = module_file.inline_dict.get(name)
            if module_file is None:
                return None
        return module_file

    def _inline_scope(self, file_rel: str, segment_list: list[str]) -> tuple[_ModuleFile, int]:
        """Follow the leading segments that name nested inline modules of a file.

        Examples (src/lib.rs has pub mod prelude { pub mod inner { ... } }):
            ("src/lib.rs", ["prelude", "inner", "Circle"])  -> (inner, 2)
            ("src/lib.rs", ["prelude"])                     -> (src/lib.rs, 0)

        Args:
            file_rel: Relative path of a .rs file of the project.
            segment_list: Segments left over in the file.

        Returns:
            (_ModuleFile of the innermost inline module reached, number of segments
            consumed). The last segment is not consumed.
        """
        module_file = self.module_file_dict[file_rel]
        depth = 0
        while depth < len(segment_list) - 1 and segment_list[depth] in module_file.inline_dict:
            module_file = module_file.inline_dict[segment_list[depth]]
            depth += 1
        return module_file, depth

    def _is_glob_hit(
        self, target_rel: str | None, rest_list: list[str], segment_list: list[str],
    ) -> bool:
        """Return whether resolving through a glob import reached what segment_list names.

        Args:
            target_rel: File returned by resolve / _walk.
            rest_list: Segments returned with it.
            segment_list: Segments that were looked up behind the glob path.

        Returns:
            True when the glob path was consumed and then either the first segment was
            consumed as a module, or target_rel (or an inline module of it the segments
            before it name) defines the first remaining segment (the segments after it
            being the same as in segment_list).
        """
        if target_rel is None:
            return False
        if len(rest_list) < len(segment_list):
            return rest_list == segment_list[len(segment_list) - len(rest_list):]
        depth = len(rest_list) - len(segment_list)
        module_file = self._inline_module(target_rel, rest_list[:depth])
        return (
            module_file is not None
            and rest_list[depth + 1:] == segment_list[1:]
            and rest_list[depth] in module_file.definition_name_set
        )

    def _glob_target_list(self, scope: _Scope) -> list[_Scope] | None:
        """Return the scopes the glob imports of a scope lead to, read once per scope.

        Args:
            scope: (file relative path, names of the inline modules) of a module.

        Returns:
            The file or inline module each glob path resolves to, in the order written;
            a glob path that leads to no module (an enum, outside the project) is left
            out. None while the glob paths of the scope are being resolved.
        """
        if scope in self._glob_target_dict:
            return self._glob_target_dict[scope]
        self._glob_target_dict[scope] = None
        file_rel, inline_tuple = scope
        target_list: list[_Scope] = []
        for glob_segment_list in self._inline_module(file_rel, list(inline_tuple)).glob_list:
            target_rel, rest_list = self.resolve(file_rel, glob_segment_list)
            if target_rel is not None and self._inline_module(target_rel, rest_list) is not None:
                target_list.append((target_rel, tuple(rest_list)))
        self._glob_target_dict[scope] = target_list
        return target_list

    def _has_name(self, scope: _Scope, name: str) -> bool:
        """Return whether a name is a definition, a use-bound name, a child module or an inline module of a scope."""
        file_rel, inline_tuple = scope
        module_file = self._inline_module(file_rel, list(inline_tuple))
        return (
            name in module_file.definition_name_set or name in module_file.use_dict
            or (not inline_tuple and name in self.child_dict[file_rel])
        )

    def _glob_reach(self, scope: _Scope, name: str) -> bool:
        """Return whether the glob imports of a scope can lead to a name.

        Args:
            scope: (file relative path, names of the inline modules) of a module.
            name: First segment of a path the scope does not have itself.

        Returns:
            True when a scope reached through one or more glob imports has the name
            (_has_name), and when that cannot be told while glob paths are being
            resolved. False when none has it: then resolving the name through the glob
            imports leads to no definition.
        """
        target_list = self._glob_target_list(scope)
        return target_list is None or any(self._scope_reach(target, name) for target in target_list)

    def _scope_reach(self, start: _Scope, name: str) -> bool:
        """Return whether a scope, or a scope its glob imports lead to, has a name (_glob_reach).

        The answer is kept per (scope, name) unless it could not be told.
        """
        key = (start, name)
        if key in self._glob_reach_dict:
            return self._glob_reach_dict[key]
        visit_set = {start}
        scope_stack = [start]
        is_reach = False
        while scope_stack and not is_reach:
            scope = scope_stack.pop()
            prior_reach = self._glob_reach_dict.get((scope, name))
            if prior_reach is False:
                continue
            if prior_reach or self._has_name(scope, name):
                is_reach = True
                continue
            target_list = self._glob_target_list(scope)
            if target_list is None:
                return True
            for target in target_list:
                if target not in visit_set:
                    visit_set.add(target)
                    scope_stack.append(target)
        self._glob_reach_dict[key] = is_reach
        return is_reach

    def resolve(
        self,
        file_rel: str,
        segment_list: list[str],
        hop: int = 0,
        visit_set: set[tuple[str, tuple[str, ...]]] | None = None,
    ) -> tuple[str | None, list[str]]:
        """Resolve a path written in a file to the file that defines what it names.

        The first segment is looked up in this order:
            "" (a leading "::")     -> the library root of the crate the next segment
                                       names, else the crate root in an edition 2015 package
            crate / self / super    -> the crate root / this module / the parent module
            a child module          -> that module's file
            a name bound by use     -> the path of that use declaration
            an inline module of the file -> the next segment in that module (_walk)
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
        state_tuple = (file_rel, tuple(segment_list))
        if visit_set is None:
            if state_tuple not in self._resolve_cache_dict:
                target_rel, rest_list = self._resolve_head(file_rel, segment_list, hop, {state_tuple})
                self._resolve_cache_dict[state_tuple] = (target_rel, tuple(rest_list))
            target_rel, rest_tuple = self._resolve_cache_dict[state_tuple]
            return target_rel, list(rest_tuple)
        if state_tuple in visit_set:
            return None, segment_list
        visit_set.add(state_tuple)
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
        if head == "":
            if len(segment_list) > 1 and segment_list[1] in self.crate_lib_dict:
                return self._walk(
                    self.crate_lib_dict[segment_list[1]], segment_list[2:], hop, visit_set,
                )
            if self._is_edition_2015(file_rel):
                return self._walk(self.crate_root(file_rel), segment_list[1:], hop, visit_set)
            return None, segment_list
        if head == "crate":
            return self._walk(self.crate_root(file_rel), segment_list[1:], hop, visit_set)
        if head in ("self", "super") or head in self.child_dict[file_rel]:
            return self._walk(file_rel, segment_list, hop, visit_set)
        use_list = module_file.use_dict.get(head)
        if use_list is not None and use_list != [head] and hop < _MAX_USE_HOP:
            return self.resolve(file_rel, use_list + segment_list[1:], hop + 1, visit_set)
        if head in module_file.inline_dict:
            return self._walk(file_rel, segment_list, hop, visit_set)
        if head in module_file.definition_name_set:
            return file_rel, segment_list
        if head in self.crate_lib_dict:
            return self._walk(self.crate_lib_dict[head], segment_list[1:], hop, visit_set)
        if hop < _MAX_USE_HOP and self._glob_reach((file_rel, ()), head):
            for glob_segment_list in module_file.glob_list:
                target_rel, rest_list = self.resolve(
                    file_rel, glob_segment_list + segment_list, hop + 1, visit_set,
                )
                if self._is_glob_hit(target_rel, rest_list, segment_list):
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

        Segments that name inline modules of the reached file are followed into them.
        When the next segment is not defined in the reached file or inline module, it
        is followed through its use declarations (re-exports) and glob imports.

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
        module_file, depth = self._inline_scope(current_rel, rest_list)
        name = rest_list[depth]
        if name in module_file.definition_name_set:
            return current_rel, rest_list

        # The name is brought into the module by a use declaration (pub use a::Name)
        use_list = module_file.use_dict.get(name)
        if use_list is not None:
            target_rel, target_rest_list = self.resolve(
                current_rel, use_list + rest_list[depth + 1:], hop + 1, visit_set,
            )
            if target_rel is not None:
                return target_rel, target_rest_list
            return current_rel, rest_list

        # The name comes from a glob import (pub use a::*) of a module that has it
        name_list = rest_list[depth:]
        if not self._glob_reach((current_rel, tuple(rest_list[:depth])), name):
            return current_rel, rest_list
        for glob_segment_list in module_file.glob_list:
            target_rel, target_rest_list = self.resolve(
                current_rel, glob_segment_list + name_list, hop + 1, visit_set,
            )
            if self._is_glob_hit(target_rel, target_rest_list, name_list):
                return target_rel, target_rest_list
        return current_rel, rest_list


def _get_module_tree(project_dir: str, project_file_set: set[str]) -> RustModuleTree:
    """Return the module tree of the project, building it on the first call.

    The tree is kept in module_tree_cache_dict until the project file set changes or the
    cache is cleared.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The RustModuleTree of the .rs files in project_file_set.
    """
    module_tree = project_cache_value(module_tree_cache_dict, project_dir, project_file_set)
    if module_tree is not None:
        return module_tree

    module_tree = RustModuleTree(
        project_dir, {file_rel for file_rel in project_file_set if file_rel.endswith(".rs")},
    )
    module_tree_cache_dict[project_dir] = (project_file_set, module_tree)
    return module_tree


def _is_path_attribute(module: str) -> bool:
    """Return whether an ImportInfo module is the value of a #[path] attribute ("unix.rs")."""
    return "." in module or "/" in module


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
    module_tree = _get_module_tree(project_dir, project_file_set)
    target_rel = module_tree.resolve(current_file_rel, module.split("::"))[0]
    return target_rel if target_rel != current_file_rel else None


def rust_import_name_dict(
    module: str,
    name_list: list[str],
    current_file_rel: str,
    project_file_set: set[str],
    project_dir: str,
) -> dict[str, str | None]:
    """Return the names a Rust import binds that refer to a definition, with their paths.

    The path of a name is the path its definition has in the resolved file. A name that
    refers to a module is left out. "*" is kept when the path is a module of another
    file (use a::*); when the path is an enum of the file or of an inline module of it
    (use a::Enum::*), the names of its variants are returned in its place. The segments
    left over that name inline modules (pub mod prelude { ... }) are followed into them,
    and "the segment" below is the first one after them. An import that leads to the
    current file gives its names when the segment is a definition of the file. An import
    whose segment is a name a use declaration of the resolved file or inline module
    binds (pub use std::fmt::Write) gives no names.

    Examples (module, name_list -> result):
        "crate::config::Settings", ["Settings"]          -> {"Settings": "Settings"}
        "crate::shape::Circle", ["Round"]                 -> {"Round": "Circle"}
        "crate::flags::gen_bash", ["gen_bash"]
            (flags has pub use self::bash::generate as gen_bash)   -> {"gen_bash": "generate"}
        "Settings::new", ["Settings::new"]                -> {"Settings::new": "Settings::new"}
        "self::config", ["config"]                        -> {}
        "crate::util", ["*"]                              -> {"*": None}
        "crate::shape::Kind", ["*"]    -> {"Round": "Kind::Round", "Square": "Kind::Square"}
        "self::parse", ["self::parse"] (the current file defines parse)
                                                          -> {"self::parse": "parse"}
        "crate::prelude::Write", ["Write"] (prelude has pub use std::fmt::Write)  -> {}
        "crate::prelude::Circle", ["Circle"]
            (lib.rs has pub mod prelude { pub use crate::shape::Circle; })  -> {"Circle": "Circle"}

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
    module_tree = _get_module_tree(project_dir, project_file_set)
    target_rel, rest_list = module_tree.resolve(current_file_rel, module.split("::"))
    if target_rel is None:
        return {}
    module_file, depth = module_tree._inline_scope(target_rel, rest_list)
    head = rest_list[depth] if rest_list else None
    is_definition = head in module_file.definition_name_set
    if not is_definition and (target_rel == current_file_rel or head in module_file.use_dict):
        return {}

    name_dict: dict[str, str | None] = {}
    for name in name_list:
        if name == "*":
            if not rest_list:
                name_dict[name] = None
            elif depth == len(rest_list) - 1:
                for variant_name in module_file.variant_dict.get(head, []):
                    name_dict.setdefault(variant_name, "::".join([*rest_list, variant_name]))
        elif rest_list:
            name_dict[name] = "::".join(rest_list)
    return name_dict
