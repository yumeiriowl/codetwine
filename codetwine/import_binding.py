import os
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    CONTAINER_DEFINITION_TYPE_SET,
    DEFAULT_EXPORT_NAME,
    DefinitionInfo,
    select_top_level_definitions,
)
from codetwine.extractors.definition_source import file_definition, file_definition_list
from codetwine.extractors.usages import symbol_part_list
from codetwine.extractors.imports import (
    ImportInfo,
    extract_imports,
    has_module_export,
    local_export_dict,
    member_module_export_dict,
    module_export_list,
    python_all_name_list,
)
from codetwine.import_to_path import (
    detect_source_roots,
    get_import_params,
    nearest_first,
    resolve_module_to_project_path,
    top_level_definition_names,
)
from codetwine.extractors.rust_path import inline_module_scope_list
from codetwine.rust_module_tree import rust_import_name_dict
from codetwine.utils.project_cache import project_cache_value
from codetwine.config.settings import (
    EXT_TO_DEFINITION_DICT,
    EXT_TO_IMPLICIT_VISIBILITY_DICT,
    EXT_TO_IMPORT_RESOLVE_DICT,
    EXT_TO_USAGE_NODE_TYPE_DICT,
    language_ext,
)

logger = logging.getLogger(__name__)

# File name of the script of a Python package
_PACKAGE_INIT_FILE_NAME = "__init__.py"

# Node types of the package statement of a file (Java, Kotlin)
_PACKAGE_STATEMENT_TYPE_SET = {"package_declaration", "package_header"}

# Definition types of a function declared without a body, and of one with a body (C / C++)
_FUNCTION_DECLARE_TYPE_SET = {"function_declarator"}
_FUNCTION_BODY_TYPE_SET = {"function_definition"}

# Node types of a class with base classes, and of a base written with template arguments (C++)
_CLASS_NODE_TYPE_SET = {"class_specifier", "struct_specifier"}
_BASE_CLAUSE_TYPE = "base_class_clause"

# Values of "bind" whose files pass on, to a file that takes every name of them, only
# the names star_name_set() gives
_STAR_NAME_BIND_SET = {"module"}

# Values of "bind" whose files pass on only the names they define themselves
_OWN_NAME_EXPORT_BIND_SET = {"package"}

# Cache of binders: project_dir -> (project file set the binder was built for, binder)
_binder_cache: dict[str, tuple[set[str], "ImportBinder"]] = {}


@dataclass(frozen=True)
class SymbolBinding:
    """What a name bound by an import statement stands for."""

    file_rel: str       # Relative path of the project file the name comes from
    # Name of the definition in file_rel; None when the name stands for the file as a
    # whole (a module, a namespace import)
    name: str | None


@dataclass(frozen=True)
class ScopeBinding:
    """What a name stands for on a range of lines of a file."""

    name: str           # The bound name
    start_line: int     # First line the name is bound for (1-based)
    end_line: int       # Last line the name is bound for
    # What the name stands for; None when it stands for nothing of the project
    binding: SymbolBinding | None


@dataclass
class _FileBinding:
    """What the import statements of one file bind, as they are written."""

    # Name bound for the whole file -> the file and the name the statement names (not
    # followed further)
    name_dict: dict[str, SymbolBinding] = field(default_factory=dict)
    # Name an export statement with a source passes on without binding it in the file
    # itself (JS/TS: export { a } from "./m") -> the file and the name it stands for
    export_dict: dict[str, SymbolBinding] = field(default_factory=dict)
    # Name the file exports under another name than it defines it with (JS/TS:
    # export { p as q }, export default p, module.exports = p) -> the file and the name
    # it stands for
    export_alias_dict: dict[str, SymbolBinding] = field(default_factory=dict)
    # Files whose every name the file takes over (from m import *, #include, export *)
    wildcard_list: list[str] = field(default_factory=list)
    # Name taken over from a definition as a whole (Rust: use Enum::*) -> the name it stands for
    wildcard_name_dict: dict[str, SymbolBinding] = field(default_factory=dict)
    # Names bound for a range of lines (an import statement written in a function, a
    # JS/TS export statement with a source; Rust: a use declaration in a block or an
    # inline module, a path that leads to the file itself)
    scope_binding_list: list[ScopeBinding] = field(default_factory=list)
    # (file, first line, last line) of the files whose every name the file takes over
    # for a range of lines
    scope_wildcard_list: list[tuple[str, int, int]] = field(default_factory=list)
    # The names "from file import *" takes from the file as its __all__ lists them
    # (Python); None when the file has no __all__ that is written with constants
    all_name_list: list[str] | None = None
    # (first line, last line, whether it takes over the names of the module around it)
    # of each inline module (Rust: mod name { ... }, use super::*)
    module_scope_list: list[tuple[int, int, bool]] = field(default_factory=list)
    # (name, line) of the names an import statement uses where it is written (JS/TS:
    # require("./m").run())
    use_list: list[tuple[str, int]] = field(default_factory=list)
    # (class name, first line, last line) of each function defined as a member of a
    # class outside the class (C++: int Shape::count() { ... }) and of each class
    member_scope_list: list[tuple[str, int, int]] = field(default_factory=list)
    # Whether the names of wildcard_list can be written in the file itself
    is_wildcard_local: bool = True
    # Files the import statements resolve to
    import_file_set: set[str] = field(default_factory=set)
    # Name an import statement binds -> lines of those statements
    import_line_dict: dict[str, set[int]] = field(default_factory=dict)
    # The same for the statements that bind their names for the whole file
    file_import_line_dict: dict[str, set[int]] = field(default_factory=dict)


@dataclass
class _TreeFact:
    """What the binder reads from the syntax tree of one file, kept without the tree."""

    import_info_list: list[ImportInfo] = field(default_factory=list)
    line_count: int = 1                          # Number of lines of the tree
    package_name: str | None = None              # Package the file declares (Java, Kotlin)
    all_name_list: list[str] | None = None       # python_all_name_list (Python)
    local_export_dict: dict[str, str] = field(default_factory=dict)   # local_export_dict (JS/TS)
    module_export_list: list[str] = field(default_factory=list)      # module_export_list (JS/TS)
    # member_module_export_dict (JS/TS)
    member_module_export_dict: dict[str, str] = field(default_factory=dict)
    has_module_export: bool = False              # has_module_export (JS/TS)
    # inline_module_scope_list (Rust)
    module_scope_list: list[tuple[int, int, bool]] = field(default_factory=list)
    # _base_class_name_dict (C++)
    base_name_dict: dict[str, list[str]] = field(default_factory=dict)


def clear_import_binder_cache() -> None:
    """Forget the binders of every project."""
    _binder_cache.clear()


def import_binder(project_dir: str, project_file_set: set[str]) -> "ImportBinder":
    """Return the binder of a project, built once per project file set.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The ImportBinder, kept until the project file set changes or
        clear_import_binder_cache() is called.
    """
    binder = project_cache_value(_binder_cache, project_dir, project_file_set)
    if binder is not None:
        return binder
    binder = ImportBinder(project_dir, project_file_set)
    _binder_cache[project_dir] = (project_file_set, binder)
    return binder


def _join_module(module: str, name: str) -> str:
    """Return the module string of a name inside a Python module.

    Examples:
        ("pkg", "core")   -> "pkg.core"
        (".", "core")     -> ".core"
        ("..sub", "core") -> "..sub.core"
    """
    return module + name if module.endswith(".") else f"{module}.{name}"


def _package_name(root_node: Node) -> str | None:
    """Return the package a Java or Kotlin file declares, None when it declares none."""
    for child in root_node.children:
        if child.type in _PACKAGE_STATEMENT_TYPE_SET:
            for name_node in child.named_children:
                if name_node.type in ("scoped_identifier", "qualified_identifier", "identifier"):
                    return "".join(name_node.text.decode("utf-8").split())
    return None


def _base_class_name_dict(root_node: Node) -> dict[str, list[str]]:
    """Return the names of the base classes of each class of a C++ file.

    class Document : public Node, private geo::Shape<int> { ... }
        -> {"Document": ["Node", "Shape"]}

    Args:
        root_node: The AST root node of the file.

    Returns:
        {class name: its base class names without namespaces and template arguments,
        in the order they are written}, for the classes written with a body and a base
        class; of several classes of one name the first.
    """
    base_name_dict: dict[str, list[str]] = {}
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        node_stack.extend(reversed(node.children))
        if node.type not in _CLASS_NODE_TYPE_SET or node.child_by_field_name("body") is None:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        base_name_list: list[str] = []
        for clause_node in node.children:
            if clause_node.type != _BASE_CLAUSE_TYPE:
                continue
            for base_node in clause_node.named_children:
                while base_node is not None and base_node.type in ("qualified_identifier", "template_type"):
                    base_node = base_node.child_by_field_name("name")
                if base_node is not None and base_node.type == "type_identifier":
                    base_name_list.append(base_node.text.decode("utf-8"))
        if base_name_list:
            base_name_dict.setdefault(name_node.text.decode("utf-8"), base_name_list)
    return base_name_dict


class ImportBinder:
    """Maps the names the import statements of the files of a project bind to their definitions.

    A name is followed through the files that only pass it on (a Python __init__.py
    that imports it, a JS/TS file that re-exports it, a header that includes another)
    to the file that defines it.
    """

    def __init__(self, project_dir: str, project_file_set: set[str]) -> None:
        """Remember the project; the files are read when their names are first asked for.

        Args:
            project_dir: Absolute path to the project root.
            project_file_set: Relative paths of the project files that have a language.
        """
        self.project_dir = project_dir
        self.project_file_set = project_file_set
        self.source_root_set = detect_source_roots(project_file_set)
        self._file_binding_dict: dict[str, _FileBinding] = {}
        self._take_over_file_dict: dict[str, list[str]] = {}
        self._visible_dict: dict[str, dict[str, SymbolBinding]] = {}
        self._export_dict: dict[str, dict[str, SymbolBinding]] = {}
        self._symbol_dict: dict[str, dict[str, SymbolBinding]] = {}
        self._top_level_name_dict: dict[str, list[str]] = {}
        # Package key -> files of the package, built on first use (Java, Kotlin)
        self._package_file_dict: dict[str, list[str]] | None = None
        self._file_package_dict: dict[str, str] = {}
        # Function name -> files that define it, built on first use (C / C++)
        self._implement_file_dict: dict[str, list[str]] | None = None
        self._implement_list_dict: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # "Type::member" -> files with an impl block of the type that defines the
        # member, built on first use (Rust)
        self._impl_member_file_dict: dict[str, list[str]] | None = None
        self._scope_binding_dict: dict[str, list[ScopeBinding]] = {}
        # (file, path of a class in it) -> member name -> the member, with the members
        # of its base classes (C++)
        self._class_member_dict: dict[tuple[str, str], dict[str, SymbolBinding]] = {}
        # File -> what was read from its syntax tree
        self._tree_fact_dict: dict[str, _TreeFact] = {}
        # File -> names "from file import *" takes from it (Python)
        self._star_name_dict: dict[str, set[str]] = {}
        # File -> receiver_name_set (Kotlin)
        self._receiver_name_dict: dict[str, set[str]] = {}
        # package_member_name_set, built on first use (Java, Kotlin)
        self._package_member_name_set: set[str] | None = None

    # == Names of one file ====================================================

    def _file_ext(self, file_rel: str) -> str:
        """Return the extension whose language settings a file is analyzed with."""
        return language_ext(os.path.join(self.project_dir, file_rel))

    def _bind_kind(self, file_rel: str) -> str | None:
        """Return the "bind" value of the resolve config of a file, None when it has none."""
        return EXT_TO_IMPORT_RESOLVE_DICT.get(self._file_ext(file_rel), {}).get("bind")

    def top_level_name_list(self, file_rel: str) -> list[str]:
        """Return the top-level definition names of a file (top_level_definition_names).

        A file whose definitions cannot be read has none; the exception is logged.
        """
        name_list = self._top_level_name_dict.get(file_rel)
        if name_list is None:
            try:
                name_list = top_level_definition_names(file_rel, self.project_dir)
            except Exception as e:
                logger.warning(
                    f"The definitions of {file_rel} cannot be read; its names are not linked: "
                    f"{type(e).__name__}: {e}"
                )
                name_list = []
            self._top_level_name_dict[file_rel] = name_list
        return name_list

    def _tree_fact(self, file_rel: str) -> _TreeFact:
        """Return what the binder reads from the syntax tree of a file, read once per file.

        The file is parsed once: its import statements, what its "bind" value needs
        (_TREE_FACT_FUNCTION_DICT) and its definitions (file_definition_list) are all
        read from that one tree.

        Raises:
            Exception: Whatever parsing the file or reading its import statements raises.
        """
        tree_fact = self._tree_fact_dict.get(file_rel)
        if tree_fact is not None:
            return tree_fact
        file_abs = os.path.join(self.project_dir, file_rel)
        file_ext = self._file_ext(file_rel)
        root_node = parse_file(file_abs)[0]
        language, import_query_str = get_import_params(file_ext)
        tree_fact = _TreeFact(
            import_info_list=extract_imports(
                root_node, language, import_query_str,
                EXT_TO_USAGE_NODE_TYPE_DICT.get(file_ext, {}).get("scope_types"),
            ) if language is not None and import_query_str else [],
            line_count=root_node.end_point[0] + 1,
        )
        fact_function = _TREE_FACT_FUNCTION_DICT.get(self._bind_kind(file_rel))
        if fact_function is not None:
            fact_function(root_node, tree_fact)
        file_definition_list(file_abs, EXT_TO_DEFINITION_DICT[file_ext])
        self._tree_fact_dict[file_rel] = tree_fact
        return tree_fact

    def _definition_list(self, file_rel: str) -> list[DefinitionInfo]:
        """Return the definitions of a file (file_definition_list)."""
        return file_definition_list(
            os.path.join(self.project_dir, file_rel),
            EXT_TO_DEFINITION_DICT[self._file_ext(file_rel)],
        )

    def _resolve(self, module: str, file_rel: str) -> str | None:
        """Resolve a module string written in a file to a project file."""
        return resolve_module_to_project_path(
            module, file_rel, self.project_file_set, self.source_root_set, self.project_dir,
        )

    def _file_binding(self, file_rel: str) -> _FileBinding:
        """Return what the import statements of a file bind, read once per file.

        A file whose import statements cannot be read binds nothing; the exception is
        logged.
        """
        file_binding = self._file_binding_dict.get(file_rel)
        if file_binding is not None:
            return file_binding

        bind_function = _BIND_FUNCTION_DICT.get(self._bind_kind(file_rel))
        file_binding = _FileBinding()
        # Registered before it is filled: a file that leads back to itself gets it as it is
        self._file_binding_dict[file_rel] = file_binding
        if bind_function is None:
            return file_binding

        try:
            tree_fact = self._tree_fact(file_rel)
            import_info_list = tree_fact.import_info_list
            for import_info in import_info_list:
                # A member read where its module is required binds no variable
                if import_info.is_use:
                    continue
                for name in [*import_info.names, import_info.module_alias]:
                    if not name:
                        continue
                    file_binding.import_line_dict.setdefault(name, set()).add(import_info.line)
                    if import_info.scope_line_tuple is None:
                        file_binding.file_import_line_dict.setdefault(
                            name, set(),
                        ).add(import_info.line)
            bind_function(self, file_rel, import_info_list, tree_fact, file_binding)
        except Exception as e:
            logger.warning(
                f"The import statements of {file_rel} cannot be read; its names are not "
                f"linked: {type(e).__name__}: {e}"
            )
        file_binding.import_file_set.discard(file_rel)
        return file_binding

    # == Binding per language ==================================================

    @staticmethod
    def _bind_name(
        file_binding: _FileBinding, import_info: ImportInfo, name: str,
        binding: SymbolBinding | None,
    ) -> None:
        """Bind a name of an import statement of a Python or JS/TS file.

        A statement that gives lines (ImportInfo.scope_line_tuple) binds the name for
        those lines, also when it stands for nothing of the project; any other
        statement for the whole file. The name of an export statement with a source
        is passed on to the files importing the file as well.

        Args:
            file_binding: The bindings of the file; modified in place.
            import_info: The import statement.
            name: The name the statement binds.
            binding: What the name stands for; None when it stands for nothing of the
                project.
        """
        line_tuple = import_info.scope_line_tuple
        if line_tuple is not None:
            file_binding.scope_binding_list.append(ScopeBinding(name, *line_tuple, binding))
        elif binding is not None:
            file_binding.name_dict[name] = binding
        if import_info.is_export and binding is not None:
            file_binding.export_dict[name] = binding

    def _bind_module(
        self, file_rel: str, import_info_list: list[ImportInfo], tree_fact: _TreeFact,
        file_binding: _FileBinding,
    ) -> None:
        """Read the bindings of the import statements of a Python file.

        import a.b.c            -> "a.b.c", and "a" / "a.b" when they are packages of
                                   the project
        import a.b.c as m       -> "m"
        from m import n as k    -> "k": the name n of m, or the module m.n when m has
                                   no script of its own
        from m import *         -> the names of m its __all__ lists, or every name of m
                                   that does not start with "_" (star_name_set)
        A statement written inside a function binds its names for the lines of the
        function (_bind_name). The __all__ of the file itself is read as well.
        """
        file_binding.all_name_list = tree_fact.all_name_list
        for import_info in import_info_list:
            module_file = self._resolve(import_info.module, file_rel)
            if module_file is not None:
                file_binding.import_file_set.add(module_file)

            if not import_info.names:
                if import_info.module_alias:
                    self._bind_name(
                        file_binding, import_info, import_info.module_alias,
                        SymbolBinding(module_file, None) if module_file is not None else None,
                    )
                    continue
                part_list = import_info.module.split(".")
                for part_count in range(1, len(part_list) + 1):
                    package = ".".join(part_list[:part_count])
                    package_file = self._resolve(package, file_rel)
                    self._bind_name(
                        file_binding, import_info, package,
                        SymbolBinding(package_file, None) if package_file is not None else None,
                    )
                continue

            alias_dict = import_info.alias_map or {}
            for name in import_info.names:
                original_name = alias_dict.get(name, name)
                if name == "*":
                    if module_file is not None:
                        file_binding.wildcard_list.append(module_file)
                    continue
                binding: SymbolBinding | None = None
                if module_file is not None:
                    binding = SymbolBinding(module_file, original_name)
                else:
                    module_part_file = self._resolve(
                        _join_module(import_info.module, original_name), file_rel,
                    )
                    if module_part_file is not None:
                        binding = SymbolBinding(module_part_file, None)
                        file_binding.import_file_set.add(module_part_file)
                self._bind_name(file_binding, import_info, name, binding)

    def _bind_export(
        self, file_rel: str, import_info_list: list[ImportInfo], tree_fact: _TreeFact,
        file_binding: _FileBinding,
    ) -> None:
        """Read the bindings of the import and export statements of a JS/TS file.

        import d from "./m"              -> "d": the default export of m
        import { a as b } from "./m"     -> "b": the name a of m
        import * as M from "./m"         -> "M": the file m
        const u = require("./m")         -> "u": the file m
        export { a as b } from "./m"     -> "b": the name a of m, for the files importing
                                            this one and on the lines of the statement
        export * from "./m"              -> every name of m, for the files importing this one
        export { p as q } / export default p  -> "q" / "default": the name p of the file
                                            itself, for the files importing this one
        module.exports = require("./m")  -> the default export and every name of m, for
                                            the files importing this one
        exports.u = require("./m") / module.exports = { u: require("./m") }
                                         -> "u": the file m, for the files importing this one
        require("./m").run()             -> "run": the name run of m, on the lines of the
                                            access, where it is a usage of the name
        import("./m").then((u) => ...)   -> "u": the file m, on the lines of the callback
        An import or require() written inside a function binds its names for the lines
        of the function (_bind_name).
        """
        file_binding.is_wildcard_local = False
        for import_info in import_info_list:
            module_file = self._resolve(import_info.module, file_rel)
            if module_file is not None:
                file_binding.import_file_set.add(module_file)
            if import_info.module_alias:
                self._bind_name(
                    file_binding, import_info, import_info.module_alias,
                    SymbolBinding(module_file, None) if module_file is not None else None,
                )
            alias_dict = import_info.alias_map or {}
            for name in import_info.names:
                if name == "*":
                    if module_file is not None:
                        file_binding.wildcard_list.append(module_file)
                    continue
                self._bind_name(
                    file_binding, import_info, name,
                    SymbolBinding(module_file, alias_dict.get(name, name))
                    if module_file is not None else None,
                )
                if import_info.is_use:
                    file_binding.use_list.append((name, import_info.line))
        for export_name, local_name in tree_fact.local_export_dict.items():
            file_binding.export_alias_dict[export_name] = SymbolBinding(file_rel, local_name)
        for module in tree_fact.module_export_list:
            module_file = self._resolve(module, file_rel)
            if module_file is not None:
                file_binding.export_alias_dict.setdefault(
                    DEFAULT_EXPORT_NAME, SymbolBinding(module_file, DEFAULT_EXPORT_NAME),
                )
                file_binding.wildcard_list.append(module_file)
        for export_name, module in tree_fact.member_module_export_dict.items():
            module_file = self._resolve(module, file_rel)
            if module_file is not None:
                file_binding.export_dict[export_name] = SymbolBinding(module_file, None)

    def _bind_include(
        self, file_rel: str, import_info_list: list[ImportInfo], tree_fact: _TreeFact,
        file_binding: _FileBinding,
    ) -> None:
        """Read the bindings of the #include directives of a C/C++ file: every name of each file.

        The lines of each function defined as a member of a class outside the class
        (int Shape::count() { ... }) and the lines of each class are kept with the name
        of the class (member_scope_list).
        """
        for import_info in import_info_list:
            include_file = self._resolve(import_info.module, file_rel)
            if include_file is not None:
                file_binding.import_file_set.add(include_file)
                file_binding.wildcard_list.append(include_file)
        for definition in self._definition_list(file_rel):
            part_list = definition.name.split("::")
            if definition.type in _FUNCTION_BODY_TYPE_SET and len(part_list) > 1:
                file_binding.member_scope_list.append(
                    (part_list[-2], definition.start_line, definition.end_line)
                )
            elif definition.type in _CLASS_NODE_TYPE_SET:
                file_binding.member_scope_list.append(
                    (definition.name, definition.start_line, definition.end_line)
                )

    def _bind_path(
        self, file_rel: str, import_info_list: list[ImportInfo], tree_fact: _TreeFact,
        file_binding: _FileBinding,
    ) -> None:
        """Read the bindings of the use declarations and paths of a Rust file (rust_import_name_dict).

        use a::Name [as N]; / a::Name   -> "Name" ("N") / "a::Name": the definition Name of a
        use a::*;                       -> every name of the file of module a
        use a::Enum::*;                 -> each variant of Enum
        A declaration written in a block or an inline module, and a path whose first
        segment such a declaration binds, is bound for the lines ImportInfo gives
        (scope_binding_list, scope_wildcard_list); a name that leads to no definition
        of the project stands for nothing on those lines. A name that leads to a definition
        of the file itself is bound in scope_binding_list as well, for the whole file
        when ImportInfo gives no lines; a variant bound for the whole file gives way to
        a name of name_dict and to a top-level definition of the file.
        """
        file_binding.module_scope_list = tree_fact.module_scope_list
        file_line_tuple = (1, tree_fact.line_count)
        variant_binding_list: list[ScopeBinding] = []
        for import_info in import_info_list:
            variant_binding_list.extend(
                self._bind_path_import(file_rel, import_info, file_line_tuple, file_binding)
            )

        # The variants of an enum of the file itself, taken over for the whole file
        top_level_name_set = set(self.top_level_name_list(file_rel))
        variant_binding_list.extend(
            ScopeBinding(name, *file_line_tuple, binding)
            for name, binding in file_binding.wildcard_name_dict.items()
            if binding.file_rel == file_rel
            and name not in file_binding.name_dict and name not in top_level_name_set
        )
        file_binding.scope_binding_list.extend(variant_binding_list)

    def _bind_path_import(
        self, file_rel: str, import_info: ImportInfo, file_line_tuple: tuple[int, int],
        file_binding: _FileBinding,
    ) -> list[ScopeBinding]:
        """Read the bindings of one use declaration or path of a Rust file (_bind_path).

        Args:
            file_rel: Relative path of the file.
            import_info: The import.
            file_line_tuple: (first line, last line) of the file.
            file_binding: The bindings of the file; modified in place.

        Returns:
            The variants of an enum the import takes over for a range of lines.
        """
        module_file = self._resolve(import_info.module, file_rel)
        if module_file is not None:
            file_binding.import_file_set.add(module_file)
        name_dict = rust_import_name_dict(
            import_info.module, import_info.names, file_rel, self.project_file_set,
            self.project_dir,
        )
        target_file = module_file or file_rel
        line_tuple = import_info.scope_line_tuple
        variant_binding_list: list[ScopeBinding] = []
        for name, original_name in name_dict.items():
            binding = SymbolBinding(target_file, original_name)
            if name == "*":
                if line_tuple is None:
                    file_binding.wildcard_list.append(target_file)
                else:
                    file_binding.scope_wildcard_list.append((target_file, *line_tuple))
            elif name not in import_info.names:
                # A variant of an enum the import takes over as a whole
                if line_tuple is None:
                    file_binding.wildcard_name_dict.setdefault(name, binding)
                else:
                    variant_binding_list.append(ScopeBinding(name, *line_tuple, binding))
            elif line_tuple is None and target_file != file_rel:
                file_binding.name_dict[name] = binding
            else:
                file_binding.scope_binding_list.append(
                    ScopeBinding(name, *(line_tuple or file_line_tuple), binding)
                )
        if line_tuple is not None:
            file_binding.scope_binding_list.extend(
                ScopeBinding(name, *line_tuple, None)
                for name in import_info.names if name != "*" and name not in name_dict
            )
        return variant_binding_list

    def _bind_package(
        self, file_rel: str, import_info_list: list[ImportInfo], tree_fact: _TreeFact,
        file_binding: _FileBinding,
    ) -> None:
        """Read the bindings of the import statements of a Java or Kotlin file.

        import a.b.C [as D]      -> "C" ("D"): the type, function or property C of package a.b
        import a.b.C.Inner       -> "Inner": the member Inner of C
        import static a.b.C.m    -> "m": the member m of C
        import a.b.*             -> every name of the files of package a.b
        import static a.b.C.*    -> every member of C
        """
        for import_info in import_info_list:
            part_list = import_info.module.split(".")
            if "*" in import_info.names:
                package_file_list = self._package_file_list(import_info.module)
                if package_file_list:
                    file_binding.wildcard_list.extend(
                        other_rel for other_rel in package_file_list if other_rel != file_rel
                    )
                    continue
                type_tuple = self._resolve_qualified_name(part_list, file_rel)
                if type_tuple is not None:
                    type_file, type_path = type_tuple
                    for member_name in self._member_name_list(type_file, type_path):
                        file_binding.name_dict.setdefault(
                            member_name, SymbolBinding(type_file, f"{type_path}.{member_name}"),
                        )
                continue
            type_tuple = self._resolve_qualified_name(part_list, file_rel)
            if type_tuple is not None:
                type_file, type_path = type_tuple
                local_name = import_info.module_alias or part_list[-1]
                file_binding.name_dict[local_name] = SymbolBinding(type_file, type_path)
                file_binding.import_file_set.add(type_file)

    # == Packages (Java, Kotlin) ==============================================

    def _package_index(self) -> dict[str, list[str]]:
        """Return {package key: files of the package}, built from the package statements once.

        The key of a file with a package statement is the package name; of a file
        without one, "/" and its directory. The top-level names of each file are read
        while it is parsed (top_level_name_list).
        """
        if self._package_file_dict is not None:
            return self._package_file_dict
        package_file_dict: dict[str, list[str]] = {}
        for file_rel in sorted(self.project_file_set):
            if self._bind_kind(file_rel) != "package":
                continue
            try:
                package = self._tree_fact(file_rel).package_name
            except Exception as e:
                logger.warning(
                    f"The package of {file_rel} cannot be read: {type(e).__name__}: {e}"
                )
                continue
            self.top_level_name_list(file_rel)
            package_key = package if package is not None else "/" + os.path.dirname(file_rel)
            self._file_package_dict[file_rel] = package_key
            package_file_dict.setdefault(package_key, []).append(file_rel)
        self._package_file_dict = package_file_dict
        return package_file_dict

    def _package_file_list(self, package: str) -> list[str]:
        """Return the files of a package, in path order."""
        return self._package_index().get(package, [])

    def _resolve_qualified_name(
        self, part_list: list[str], file_rel: str,
    ) -> tuple[str, str] | None:
        """Resolve a qualified name written in an import statement to a definition.

        The longest leading part that is a package of the project is taken as the
        package; the next part is looked up among the top-level names of its files.

        Examples (package com.acme.model with User.java):
            ["com", "acme", "model", "User"]             -> ("…/User.java", "User")
            ["com", "acme", "model", "User", "Builder"]  -> ("…/User.java", "User.Builder")

        Args:
            part_list: The parts of the name.
            file_rel: Relative path of the file the import is written in.

        Returns:
            (file, path of the definition in the file), None when no package of the
            project defines the name. Of several files that define it, the one named
            like it comes first, then the one nearest to file_rel.
        """
        for package_part_count in range(len(part_list) - 1, 0, -1):
            package_file_list = self._package_file_list(".".join(part_list[:package_part_count]))
            rest_list = part_list[package_part_count:]
            candidate_list = [
                other_rel for other_rel in package_file_list
                if other_rel != file_rel and rest_list[0] in self.top_level_name_list(other_rel)
            ]
            if not candidate_list:
                continue
            same_name_list = [
                other_rel for other_rel in candidate_list
                if os.path.splitext(os.path.basename(other_rel))[0] == rest_list[0]
            ]
            return nearest_first(same_name_list or candidate_list, file_rel)[0], ".".join(rest_list)
        return None

    def _member_name_list(self, file_rel: str, type_path: str) -> list[str]:
        """Return the names of the definitions written inside a type of a file.

        Args:
            file_rel: Relative path of the file.
            type_path: Path of the type in the file ("User", "User.Builder").

        Returns:
            The names of the definitions inside the lines of the container definitions
            named like the last part of type_path, without duplicates.
        """
        type_name = type_path.split(".")[-1]
        definition_list = self._definition_list(file_rel)
        member_name_list: list[str] = []
        for owner in definition_list:
            if owner.name != type_name or owner.type not in CONTAINER_DEFINITION_TYPE_SET:
                continue
            for definition in definition_list:
                if definition is not owner and owner.start_line <= definition.start_line <= owner.end_line:
                    member_name_list.append(definition.name)
        return list(dict.fromkeys(member_name_list))

    def _implicit_file_list(self, file_rel: str) -> list[str]:
        """Return the files whose names a file can write without an import statement.

        "package" visibility (Java / Kotlin): the other files of its package.
        "project" visibility (SQL): every other file of its language.

        Returns:
            Relative paths in path order, without the file itself.
        """
        file_ext = self._file_ext(file_rel)
        scope = EXT_TO_IMPLICIT_VISIBILITY_DICT.get(file_ext)
        if scope == "package":
            self._package_index()
            package_key = self._file_package_dict.get(file_rel)
            return [
                other_rel for other_rel in self._package_file_list(package_key or "")
                if other_rel != file_rel
            ]
        if scope == "project":
            return [
                other_rel for other_rel in sorted(self.project_file_set)
                if other_rel != file_rel and self._file_ext(other_rel) == file_ext
            ]
        return []

    # == Following a name to its definition ===================================

    def _module_part_file(self, file_rel: str, name: str) -> str | None:
        """Return the module of a Python package that has a name, None when there is none.

        Args:
            file_rel: Relative path of the __init__.py of the package.
            name: Name of the module inside the package.
        """
        if os.path.basename(file_rel) != _PACKAGE_INIT_FILE_NAME:
            return None
        return self._resolve("." + name, file_rel)

    def _take_over_file_list(self, file_rel: str) -> list[str]:
        """Return a file and the files whose every name it passes on, read once per file.

        Returns:
            The file itself, then the files it takes over as a whole (from m import *,
            #include, export *) and the ones those take over, each once, in the order
            they are reached following the first of them first. The file alone when
            its "bind" is in _OWN_NAME_EXPORT_BIND_SET.
        """
        take_over_file_list = self._take_over_file_dict.get(file_rel)
        if take_over_file_list is not None:
            return take_over_file_list
        take_over_file_list = []
        visit_set: set[str] = set()
        file_stack = [file_rel]
        while file_stack:
            current_rel = file_stack.pop()
            if current_rel in visit_set:
                continue
            visit_set.add(current_rel)
            take_over_file_list.append(current_rel)
            if self._bind_kind(current_rel) not in _OWN_NAME_EXPORT_BIND_SET:
                file_stack.extend(reversed(self._file_binding(current_rel).wildcard_list))
        self._take_over_file_dict[file_rel] = take_over_file_list
        return take_over_file_list

    def _direct_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return the names another file can take from a file itself, as they are written.

        In order, an earlier one kept for a name given twice: the names its export
        statements with a source pass on, its top-level definitions, the names it
        exports under another name, the names its import statements bind for the
        whole file. Of a file whose "bind" is in _OWN_NAME_EXPORT_BIND_SET, its
        top-level definitions only.

        Returns:
            {name: the file and the name it stands for, not followed further}.
        """
        own_name_dict = {
            name: SymbolBinding(file_rel, name) for name in self.top_level_name_list(file_rel)
        }
        if self._bind_kind(file_rel) in _OWN_NAME_EXPORT_BIND_SET:
            return own_name_dict
        file_binding = self._file_binding(file_rel)
        return {
            **file_binding.name_dict, **file_binding.export_alias_dict,
            **own_name_dict, **file_binding.export_dict,
        }

    def _visible_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return every name another file can take from a file, as it is written, read once per file.

        Returns:
            The names of the file itself and of each file it passes on as a whole
            (_direct_name_dict over _take_over_file_list); the first file that gives a
            name is kept. The bindings are not followed further.
        """
        visible_dict = self._visible_dict.get(file_rel)
        if visible_dict is not None:
            return visible_dict
        visible_dict = {}
        for take_over_file in self._take_over_file_list(file_rel):
            for name, binding in self._direct_name_dict(take_over_file).items():
                visible_dict.setdefault(name, binding)
        self._visible_dict[file_rel] = visible_dict
        return visible_dict

    def star_name_set(self, file_rel: str) -> set[str]:
        """Return the names "from file import *" takes from a Python file.

        A file with an __all__ written with constants gives the names it lists; any
        other file gives the names it defines, the names its import statements bind
        and the names it takes with "from other import *" itself, without the names
        that start with "_". The files that take over one another are worked out
        together, so the result does not depend on which of them is asked first.

        Args:
            file_rel: Relative path of the file.

        Returns:
            The names. Looked up once per file.
        """
        star_name_set = self._star_name_dict.get(file_rel)
        if star_name_set is not None:
            return star_name_set
        file_list = self._take_over_file_list(file_rel)
        direct_dict = {other_rel: set(self._direct_name_dict(other_rel)) for other_rel in file_list}
        name_dict: dict[str, set[str]] = {other_rel: set() for other_rel in file_list}
        is_change = True
        while is_change:
            is_change = False
            for other_rel in file_list:
                file_binding = self._file_binding(other_rel)
                if file_binding.all_name_list is not None:
                    name_set = set(file_binding.all_name_list)
                else:
                    name_set = set(direct_dict[other_rel])
                    for wildcard_file in file_binding.wildcard_list:
                        name_set |= name_dict.get(wildcard_file, set())
                    name_set = {name for name in name_set if not name.startswith("_")}
                if name_set != name_dict[other_rel]:
                    name_dict[other_rel] = name_set
                    is_change = True
        self._star_name_dict.update(name_dict)
        return name_dict[file_rel]

    def _export_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return every name another file can take from a file, followed to its definition.

        These are the names of _visible_name_dict, each followed to the file that
        defines it (_follow). Read once per file.
        """
        export_dict = self._export_dict.get(file_rel)
        if export_dict is not None:
            return export_dict
        visible_dict = self._visible_name_dict(file_rel)
        export_dict = {name: self._follow(binding) for name, binding in visible_dict.items()}
        if export_dict == visible_dict:
            export_dict = visible_dict
        self._export_dict[file_rel] = export_dict
        return export_dict

    def _follow(self, binding: SymbolBinding) -> SymbolBinding:
        """Follow a binding through the files that pass its name on to the file that defines it.

        Each step looks the name up among the names of the file the binding names
        (_visible_name_dict). A name that file defines itself ends the walk; a name
        it has not ends it with the module of that name when the file is the script
        of a Python package, else with the binding reached. Bindings that lead back
        to one another end the walk at the first one reached twice.

        Args:
            binding: A binding as an import or export statement writes it.

        Returns:
            The binding of the definition, or of the file when the name stands for a
            file as a whole.
        """
        visit_set: set[SymbolBinding] = set()
        while binding.name is not None and binding not in visit_set:
            visit_set.add(binding)
            next_binding = self._visible_name_dict(binding.file_rel).get(binding.name)
            if next_binding is None:
                module_part_file = self._module_part_file(binding.file_rel, binding.name)
                if module_part_file is not None:
                    return SymbolBinding(module_part_file, None)
                return binding
            if next_binding == binding:
                return binding
            binding = next_binding
        return binding

    def member_binding(
        self, binding: SymbolBinding, part_list: list[str],
    ) -> tuple[SymbolBinding, list[str]]:
        """Follow the parts written after a name that stands for a file.

        Examples (pkg/__init__.py imports Engine from pkg/core.py):
            (pkg/__init__.py, None), ["Engine", "run"] -> ((pkg/core.py, "Engine"), ["run"])
            (pkg/__init__.py, None), ["core", "COUNT"] -> ((pkg/core.py, "COUNT"), [])

        Args:
            binding: The binding of the first part of a usage name.
            part_list: The parts after it.

        Returns:
            (binding reached, parts left over). The binding is returned as it is when
            it names a definition or the next part is no name of its file.
        """
        while binding.name is None and part_list:
            part_binding = self._export_name_dict(binding.file_rel).get(part_list[0])
            if part_binding is None:
                module_part_file = self._module_part_file(binding.file_rel, part_list[0])
                if module_part_file is None:
                    break
                part_binding = SymbolBinding(module_part_file, None)
            binding = part_binding
            part_list = part_list[1:]
        return binding, part_list

    def module_value_binding(self, file_rel: str) -> SymbolBinding | None:
        """Return the definition the value of a CommonJS module leads to.

        Args:
            file_rel: Relative path of the file.

        Returns:
            The binding of DEFAULT_EXPORT_NAME among the names of a JS/TS file that
            assigns to module.exports; None for a file that does not (its default
            export, if any, is a member of the module), and for a file of another
            language.
        """
        if self._bind_kind(file_rel) != "export":
            return None
        try:
            if not self._tree_fact(file_rel).has_module_export:
                return None
        except Exception as e:
            logger.warning(
                f"The exports of {file_rel} cannot be read: {type(e).__name__}: {e}"
            )
            return None
        return self._export_name_dict(file_rel).get(DEFAULT_EXPORT_NAME)

    def module_member_binding(
        self, binding: SymbolBinding, part_list: list[str],
    ) -> tuple[SymbolBinding, list[str]]:
        """Follow the parts written after a name bound to the value of a CommonJS module.

        import util from "./util" binds util to the default export of the file; when
        that is the value module.exports is given, util.helper names what the file
        exports as helper.

        Args:
            binding: The binding of the first part of a usage name.
            part_list: The parts after it.

        Returns:
            (the member the first part names, parts left over) when binding is the
            module_value_binding() of its file and the first part is a name of the
            file; else the arguments as they are.
        """
        if not part_list or binding != self.module_value_binding(binding.file_rel):
            return binding, part_list
        member_binding, rest_part_list = self.member_binding(
            SymbolBinding(binding.file_rel, None), part_list,
        )
        if member_binding.name is None:
            return binding, part_list
        return member_binding, rest_part_list

    # == Declarations and their definitions (C / C++) =========================

    def _implement_index(self) -> dict[str, list[str]]:
        """Return {function name: files with a function definition of that name}, built once.

        The files whose "bind" is "include" are read. A member defined outside its
        class is found under its name with each number of leading scopes
        (geo::Shape::count -> "geo::Shape::count", "Shape::count").
        """
        if self._implement_file_dict is not None:
            return self._implement_file_dict
        implement_file_dict: dict[str, list[str]] = {}
        for file_rel in sorted(self.project_file_set):
            if self._bind_kind(file_rel) != "include":
                continue
            try:
                # The import statements are read from the same tree as the definitions
                self._tree_fact(file_rel)
                definition_list = self._definition_list(file_rel)
            except Exception as e:
                logger.warning(
                    f"The definitions of {file_rel} cannot be read: {type(e).__name__}: {e}"
                )
                continue
            for definition in definition_list:
                if definition.type not in _FUNCTION_BODY_TYPE_SET:
                    continue
                part_list = definition.name.split("::")
                for start in range(max(1, len(part_list) - 1)):
                    file_list = implement_file_dict.setdefault("::".join(part_list[start:]), [])
                    if file_rel not in file_list:
                        file_list.append(file_rel)
        self._implement_file_dict = implement_file_dict
        return implement_file_dict

    def implement_file_list(self, file_rel: str, definition_name: str) -> list[tuple[str, str]]:
        """Return the other files that define what a name of a file names.

        C / C++: the files that define a function a header only declares. Rust: the
        files with an impl block that defines a member of a type of the file.

        Examples:
            ("include/list.h", "list_push")    -> [("src/list.c", "list_push")]
            ("include/shape.hpp", "Shape.area") -> [("src/shape.cpp", "Shape::area")]
            ("src/shape.rs", "Circle::extra")  -> [("src/shape_ext.rs", "Circle::extra")]

        Args:
            file_rel: Relative path of the file a usage leads to.
            definition_name: Name the definition is looked up by in file_rel.

        Returns:
            (file, name of the definition in it) of each such file, in path order
            (_find_implement_file_list, _find_impl_member_file_list). Empty for a file
            of a language without an entry in _IMPLEMENT_FUNCTION_DICT. Looked up once
            per file and name.
        """
        find_function = _IMPLEMENT_FUNCTION_DICT.get(self._bind_kind(file_rel))
        if find_function is None:
            return []
        implement_key = (file_rel, definition_name)
        if implement_key not in self._implement_list_dict:
            self._implement_list_dict[implement_key] = find_function(
                self, file_rel, definition_name,
            )
        return self._implement_list_dict[implement_key]

    def _find_implement_file_list(
        self, file_rel: str, definition_name: str,
    ) -> list[tuple[str, str]]:
        """Look up the files that define a function a C / C++ file only declares (implement_file_list)."""
        definition = file_definition(file_rel, definition_name, self.project_dir)
        if definition is None or definition.type not in _FUNCTION_DECLARE_TYPE_SET:
            return []
        # A member is defined outside its class under the name of the class and its own
        part_list = symbol_part_list(definition_name)
        implement_name = "::".join([*part_list[-2:-1], definition.name])
        return [
            (implement_file, implement_name)
            for implement_file in self._implement_index().get(implement_name, [])
            if implement_file != file_rel and file_rel in self._take_over_file_list(implement_file)
        ]

    # == Members of a class, used by name inside a member defined outside it (C++) ==

    def _base_name_list(self, file_rel: str, class_name: str) -> list[str]:
        """Return the names of the base classes of a class of a file (_base_class_name_dict)."""
        return self._tree_fact(file_rel).base_name_dict.get(class_name, [])

    def _class_member_binding_dict(
        self, class_file: str, class_path: str,
    ) -> dict[str, SymbolBinding]:
        """Return the members of a class and of its base classes, looked up once per class.

        Args:
            class_file: Relative path of the file that defines the class.
            class_path: Path of the class in the file ("Shape", "Outer.Inner").

        Returns:
            {member name: the member as "class_path.member" of the file of its class},
            without the constructors (the members named like their class).
            A member of the class itself comes before a member of the same name of a
            base class, and an earlier base class before a later one. A base class is
            looked up among the top-level names of class_file, then among the names it
            takes from other files.
        """
        class_key = (class_file, class_path)
        member_dict = self._class_member_dict.get(class_key)
        if member_dict is not None:
            return member_dict
        # Registered before it is filled: a class that leads back to itself gets it as it is
        member_dict = {}
        self._class_member_dict[class_key] = member_dict
        class_name = class_path.split(".")[-1]
        try:
            for member_name in self._member_name_list(class_file, class_path):
                if member_name != class_name:
                    member_dict[member_name] = SymbolBinding(class_file, f"{class_path}.{member_name}")
            base_name_list = self._base_name_list(class_file, class_name)
        except Exception as e:
            logger.warning(
                f"The members of {class_path} of {class_file} cannot be read: "
                f"{type(e).__name__}: {e}"
            )
            return member_dict
        own_name_set = set(self.top_level_name_list(class_file))
        for base_name in base_name_list:
            base_binding = (
                SymbolBinding(class_file, base_name) if base_name in own_name_set
                else self.symbol_dict(class_file).get(base_name)
            )
            if base_binding is None or base_binding.name is None:
                continue
            base_member_dict = self._class_member_binding_dict(
                base_binding.file_rel, base_binding.name,
            )
            for member_name, binding in base_member_dict.items():
                member_dict.setdefault(member_name, binding)
        return member_dict

    def member_scope_list(
        self, file_rel: str,
    ) -> list[tuple[int, int, dict[str, SymbolBinding]]]:
        """Return the members a file can write by name alone, for the lines they can be written on.

        C++: inside a function defined as a member of a class outside the class
        (int Shape::count() { ... }) and inside a class, a member of the class or of
        one of its base classes is written without the class. Of a class the file
        defines itself only the members of other files are given: the file refers to
        its own definitions by their names.

        Args:
            file_rel: Relative path of the file.

        Returns:
            (first line, last line, {member name: the member}) of each such function
            and class, in line order. Empty for a file without one.
        """
        scope_list: list[tuple[int, int, dict[str, SymbolBinding]]] = []
        file_binding = self._file_binding(file_rel)
        if not file_binding.member_scope_list:
            return scope_list
        symbol_dict = self.symbol_dict(file_rel)
        own_name_set = set(self.top_level_name_list(file_rel))
        for class_name, start_line, end_line in file_binding.member_scope_list:
            class_binding = symbol_dict.get(class_name)
            if (
                class_binding is None and class_name in own_name_set
                and self._base_name_list(file_rel, class_name)
            ):
                class_binding = SymbolBinding(file_rel, class_name)
            if class_binding is None or class_binding.name is None:
                continue
            member_dict = {
                name: binding
                for name, binding in self._class_member_binding_dict(
                    class_binding.file_rel, class_binding.name,
                ).items()
                if binding.file_rel != file_rel
            }
            if member_dict:
                scope_list.append((start_line, end_line, member_dict))
        return scope_list

    def inherit_binding(self, file_rel: str, definition_name: str) -> tuple[str, str]:
        """Return where a member written with a class leads when a base class defines it.

        Examples (class Comment : public Node; Node declares Value):
            ("xml.h", "Comment.Value")  -> ("xml.h", "Node.Value")
            ("xml.h", "Comment.Kind")   -> ("xml.h", "Comment.Kind")  (its own member)
            ("xml.h", "Comment")        -> ("xml.h", "Comment")

        Args:
            file_rel: Relative path of the file a usage leads to.
            definition_name: Name the definition is looked up by in file_rel.

        Returns:
            (file, name) of the member of the base class when the first part of
            definition_name names a C++ class of file_rel and the second part a member
            only a base class has; else the arguments as they are.
        """
        if self._bind_kind(file_rel) != "include":
            return file_rel, definition_name
        part_list = symbol_part_list(definition_name)
        if len(part_list) < 2:
            return file_rel, definition_name
        class_definition = file_definition(file_rel, part_list[0], self.project_dir)
        if class_definition is None or class_definition.type not in _CLASS_NODE_TYPE_SET:
            return file_rel, definition_name
        binding = self._class_member_binding_dict(file_rel, part_list[0]).get(part_list[1])
        own_binding = SymbolBinding(file_rel, f"{part_list[0]}.{part_list[1]}")
        if binding is None or binding == own_binding or binding.name is None:
            return file_rel, definition_name
        rest_part_list = [binding.name, *part_list[2:]]
        return binding.file_rel, ".".join(rest_part_list)

    # == Members defined in the impl blocks of other files (Rust) ============

    def _impl_member_index(self) -> dict[str, list[str]]:
        """Return {"Type::member": files with an impl block of Type that defines member}, built once.

        The files whose "bind" is "path" are read.
        """
        if self._impl_member_file_dict is not None:
            return self._impl_member_file_dict
        impl_member_file_dict: dict[str, list[str]] = {}
        for file_rel in sorted(self.project_file_set):
            if self._bind_kind(file_rel) != "path":
                continue
            try:
                # The import statements are read from the same tree as the definitions
                self._tree_fact(file_rel)
                definition_list = self._definition_list(file_rel)
            except Exception as e:
                logger.warning(
                    f"The definitions of {file_rel} cannot be read: {type(e).__name__}: {e}"
                )
                continue
            for owner in definition_list:
                if owner.type not in ATTACHED_DEFINITION_TYPE_SET:
                    continue
                for definition in definition_list:
                    if definition is owner or not (
                        owner.start_line <= definition.start_line <= owner.end_line
                    ):
                        continue
                    file_list = impl_member_file_dict.setdefault(
                        f"{owner.name}::{definition.name}", [],
                    )
                    if file_rel not in file_list:
                        file_list.append(file_rel)
        self._impl_member_file_dict = impl_member_file_dict
        return impl_member_file_dict

    def _find_impl_member_file_list(
        self, file_rel: str, definition_name: str,
    ) -> list[tuple[str, str]]:
        """Look up the files whose impl blocks define a member of a type of a Rust file (implement_file_list).

        Examples (src/shape_ext.rs has use crate::shape::Circle; impl Circle { fn extra() }):
            ("src/shape.rs", "Circle::extra")  -> [("src/shape_ext.rs", "Circle::extra")]
            ("src/shape.rs", "Circle")         -> []

        Args:
            file_rel: Relative path of the file that defines the type.
            definition_name: Path of the member in file_rel ("Type::member").

        Returns:
            (file, "Type::member") of each other file that has an impl block of the
            type with the member and binds a name to the type of file_rel, in path order.
        """
        part_list = symbol_part_list(definition_name)
        if len(part_list) < 2:
            return []
        member_name = "::".join(part_list[-2:])
        type_binding = SymbolBinding(file_rel, "::".join(part_list[:-1]))
        return [
            (impl_file, member_name)
            for impl_file in self._impl_member_index().get(member_name, [])
            if impl_file != file_rel and type_binding in self.symbol_dict(impl_file).values()
        ]

    # == Public: names of a file ==============================================

    def symbol_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return the names a file can write that come from other project files.

        In order: the names its import statements bind for the whole file; the names
        visible without an import statement (_implicit_file_list); the names of the
        definitions and files it takes over as a whole. A later group does not replace
        a name of an earlier one, and the last two leave out the names the file defines
        at its top level.

        Args:
            file_rel: Relative path of the file.

        Returns:
            {name: binding followed to the file that defines it}, without the names
            that lead to the file itself.
        """
        symbol_dict = self._symbol_dict.get(file_rel)
        if symbol_dict is not None:
            return symbol_dict
        symbol_dict = {}
        file_binding = self._file_binding(file_rel)
        own_name_set = set(self.top_level_name_list(file_rel))

        for name, binding in file_binding.name_dict.items():
            target_binding = self._follow(binding)
            if target_binding.file_rel != file_rel:
                symbol_dict[name] = target_binding

        for other_rel in self._implicit_file_list(file_rel):
            for name in self.top_level_name_list(other_rel):
                if name not in own_name_set:
                    symbol_dict.setdefault(name, SymbolBinding(other_rel, name))

        if file_binding.is_wildcard_local:
            for name, binding in file_binding.wildcard_name_dict.items():
                if name not in own_name_set and binding.file_rel != file_rel:
                    symbol_dict.setdefault(name, binding)
            is_star = self._bind_kind(file_rel) in _STAR_NAME_BIND_SET
            for wildcard_file in file_binding.wildcard_list:
                export_name_dict = self._export_name_dict(wildcard_file)
                if is_star:
                    star_name_set = self.star_name_set(wildcard_file)
                    export_name_dict = {
                        name: binding for name, binding in export_name_dict.items()
                        if name in star_name_set
                    }
                    # A name __all__ lists that is a module of the package
                    for name in sorted(star_name_set - set(export_name_dict)):
                        module_part_file = self._module_part_file(wildcard_file, name)
                        if module_part_file is not None:
                            export_name_dict[name] = SymbolBinding(module_part_file, None)
                for name, binding in export_name_dict.items():
                    if name not in own_name_set and binding.file_rel != file_rel:
                        symbol_dict.setdefault(name, binding)

        self._symbol_dict[file_rel] = symbol_dict
        return symbol_dict

    def scope_binding_list(self, file_rel: str) -> list[ScopeBinding]:
        """Return what the names bound for a range of lines of a file stand for.

        In order: the names its import statements bind for a range of lines; then, for
        each file it takes over as a whole for a range of lines, every name of that
        file that does not lead to the file itself.

        Args:
            file_rel: Relative path of the file.

        Returns:
            The bindings, each followed to the file that defines the name, without
            duplicates. Empty for a file whose import statements bind every name for
            the whole file.
        """
        scope_binding_list = self._scope_binding_dict.get(file_rel)
        if scope_binding_list is not None:
            return scope_binding_list
        file_binding = self._file_binding(file_rel)
        scope_binding_list = [
            ScopeBinding(
                scope_binding.name, scope_binding.start_line, scope_binding.end_line,
                scope_binding.binding and self._follow(scope_binding.binding),
            )
            for scope_binding in file_binding.scope_binding_list
        ]
        for wildcard_file, start_line, end_line in file_binding.scope_wildcard_list:
            for name, binding in self._export_name_dict(wildcard_file).items():
                if binding.file_rel != file_rel:
                    scope_binding_list.append(ScopeBinding(name, start_line, end_line, binding))
        scope_binding_list = list(dict.fromkeys(scope_binding_list))
        self._scope_binding_dict[file_rel] = scope_binding_list
        return scope_binding_list

    def package_member_name_set(self) -> set[str]:
        """Return the names of the members of the types of every Java and Kotlin file of the project.

        Returns:
            The names of the definitions that are no top-level definitions of their
            file, of the files whose "bind" is "package". Built once.
        """
        if self._package_member_name_set is None:
            member_name_set: set[str] = set()
            for file_list in self._package_index().values():
                for file_rel in file_list:
                    try:
                        definition_list = self._definition_list(file_rel)
                    except Exception as e:
                        logger.warning(
                            f"The definitions of {file_rel} cannot be read: "
                            f"{type(e).__name__}: {e}"
                        )
                        continue
                    top_level_id_set = {
                        id(definition)
                        for definition in select_top_level_definitions(definition_list)
                    }
                    member_name_set.update(
                        definition.name for definition in definition_list
                        if id(definition) not in top_level_id_set
                    )
            self._package_member_name_set = member_name_set
        return self._package_member_name_set

    def receiver_name_set(self, file_rel: str) -> set[str]:
        """Return the top-level names of a file that are declared for values of another type.

        Args:
            file_rel: Relative path of the file.

        Returns:
            The names of the definitions with has_receiver among the top-level names
            of the file (Kotlin: extension functions and properties). Read once per file.
        """
        name_set = self._receiver_name_dict.get(file_rel)
        if name_set is None:
            top_level_name_set = set(self.top_level_name_list(file_rel))
            try:
                name_set = {
                    definition.name for definition in self._definition_list(file_rel)
                    if definition.has_receiver and definition.name in top_level_name_set
                }
            except Exception as e:
                logger.warning(
                    f"The definitions of {file_rel} cannot be read: {type(e).__name__}: {e}"
                )
                name_set = set()
            self._receiver_name_dict[file_rel] = name_set
        return name_set

    def module_scope_list(self, file_rel: str) -> list[tuple[int, int, bool]]:
        """Return the inline modules of a file and whether each takes over the names around it.

        Args:
            file_rel: Relative path of the file.

        Returns:
            (first line, last line, True when the module has use super::*) of each
            inline module (Rust: mod name { ... }). Empty for a file of a language
            without inline modules.
        """
        return self._file_binding(file_rel).module_scope_list

    def use_list(self, file_rel: str) -> list[tuple[str, int]]:
        """Return the names the import statements of a file use where they are written.

        Args:
            file_rel: Relative path of the file.

        Returns:
            (name, line) of each such name (JS/TS: the member of require("./m").run()).
        """
        return self._file_binding(file_rel).use_list

    def import_file_set(self, file_rel: str) -> set[str]:
        """Return the project files the import statements of a file resolve to.

        Args:
            file_rel: Relative path of the file.

        Returns:
            Relative paths, without the file itself.
        """
        return set(self._file_binding(file_rel).import_file_set)

    def import_line_dict(self, file_rel: str, is_whole_file: bool = False) -> dict[str, set[int]]:
        """Return the lines of the import statements of a file by the names they bind.

        Args:
            file_rel: Relative path of the file.
            is_whole_file: True to leave out the statements that bind their names for
                a range of lines (ImportInfo.scope_line_tuple).

        Returns:
            {name an import statement binds: lines of those statements (1-based)},
            also for the names that lead outside the project.
        """
        file_binding = self._file_binding(file_rel)
        return file_binding.file_import_line_dict if is_whole_file else file_binding.import_line_dict


def _module_tree_fact(root_node: Node, tree_fact: _TreeFact) -> None:
    """Read what the bindings of a Python file need from its tree."""
    tree_fact.all_name_list = python_all_name_list(root_node)


def _export_tree_fact(root_node: Node, tree_fact: _TreeFact) -> None:
    """Read what the bindings of a JS/TS file need from its tree."""
    tree_fact.local_export_dict = local_export_dict(root_node)
    tree_fact.module_export_list = module_export_list(root_node)
    tree_fact.member_module_export_dict = member_module_export_dict(root_node)
    tree_fact.has_module_export = has_module_export(root_node)


def _include_tree_fact(root_node: Node, tree_fact: _TreeFact) -> None:
    """Read what the bindings of a C/C++ file need from its tree."""
    tree_fact.base_name_dict = _base_class_name_dict(root_node)


def _path_tree_fact(root_node: Node, tree_fact: _TreeFact) -> None:
    """Read what the bindings of a Rust file need from its tree."""
    tree_fact.module_scope_list = inline_module_scope_list(root_node)


def _package_tree_fact(root_node: Node, tree_fact: _TreeFact) -> None:
    """Read what the bindings of a Java or Kotlin file need from its tree."""
    tree_fact.package_name = _package_name(root_node)


# Value of "bind" in a resolve config -> function reading what the bindings of a file
# need from its syntax tree into a _TreeFact: (root_node, tree_fact)
_TREE_FACT_FUNCTION_DICT: dict[str, Callable[[Node, _TreeFact], None]] = {
    "module":  _module_tree_fact,
    "export":  _export_tree_fact,
    "include": _include_tree_fact,
    "path":    _path_tree_fact,
    "package": _package_tree_fact,
}

# Value of "bind" in a resolve config -> method reading the bindings of a file:
# (binder, file_rel, import_info_list, tree_fact, file_binding)
_BIND_FUNCTION_DICT: dict[str, Callable[..., None]] = {
    "module":  ImportBinder._bind_module,
    "export":  ImportBinder._bind_export,
    "include": ImportBinder._bind_include,
    "path":    ImportBinder._bind_path,
    "package": ImportBinder._bind_package,
}

# Value of "bind" in a resolve config -> method looking up the other files that define
# what a name of a file names: (binder, file_rel, definition_name)
_IMPLEMENT_FUNCTION_DICT: dict[str, Callable[..., list[tuple[str, str]]]] = {
    "include": ImportBinder._find_implement_file_list,
    "path":    ImportBinder._find_impl_member_file_list,
}
