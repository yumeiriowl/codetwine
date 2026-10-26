import os
import logging
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.parsers.ts_node import iter_node, node_text
from codetwine.parsers.ts_parser import parse_file
from codetwine.extractors.definitions import (
    ATTACHED_DEFINITION_TYPE_SET,
    CLASS_DEFINITION_TYPE_SET,
    DEFAULT_EXPORT_NAME,
    TRANSPARENT_DEFINITION_TYPE_SET,
    DefinitionInfo,
    member_owner_dict,
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
    get_import_param_tuple,
    nearest_first,
    resolve_module_to_project_path,
    top_level_definition_name_list,
)
from codetwine.extractors.rust_path import inline_module_scope_list
from codetwine.rust_module_tree import rust_import_name_dict
from codetwine.utils.file_utils import add_unique
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

# Node types of the package statement of a file, and of the package name in it (Java, Kotlin)
_PACKAGE_STATEMENT_TYPE_SET = {"package_declaration", "package_header"}
_PACKAGE_NAME_TYPE_SET = {"scoped_identifier", "qualified_identifier", "identifier"}

# Node type of an annotation of a whole file, and the name of the class of the file it
# gives (Kotlin: @file:JvmName("NameUtils"))
_FILE_ANNOTATION_TYPE = "file_annotation"
_FILE_CLASS_NAME_RE = re.compile(r'\bJvmName\s*\(\s*"([^"]+)"')

# Digits a class name of a file keeps, and the character every other character but a
# letter becomes (Kotlin: string-utils.kt -> String_utilsKt)
_FILE_CLASS_DIGIT_SET = set("0123456789")
_FILE_CLASS_FILL_CHAR = "_"

# Definition types of a function declared without a body, and of one with a body (C / C++)
_FUNCTION_DECLARE_TYPE_SET = {"function_declarator"}
_FUNCTION_BODY_TYPE_SET = {"function_definition"}

# Node types of a class with base classes, of a base written with its scopes or template
# arguments, and of the name of a base (C++)
_CLASS_NODE_TYPE_SET = {"class_specifier", "struct_specifier"}
_BASE_CLAUSE_TYPE = "base_class_clause"
_BASE_WRAPPER_TYPE_SET = {"qualified_identifier", "template_type"}
_BASE_NAME_TYPE = "type_identifier"

# Values of "bind" whose files pass on, to a file that takes every name of them, only
# the names star_name_set() gives
_STAR_NAME_BIND_SET = {"module"}

# Values of "bind" whose files pass on only the names they define themselves
_OWN_NAME_EXPORT_BIND_SET = {"package"}

# Cache of binders: project_dir -> (project file set the binder was built for, binder)
_binder_cache_dict: dict[str, tuple[set[str], "ImportBinder"]] = {}


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
    # Packages whose every name the file takes over (Java, Kotlin: import a.b.*)
    wildcard_package_list: list[str] = field(default_factory=list)
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
    # (class name, the definition) of each function defined as a member of a class
    # outside the class (C++: int Shape::count() { ... }) and of each class; of each
    # impl block (Rust: impl Counter { ... })
    member_scope_list: list[tuple[str, DefinitionInfo]] = field(default_factory=list)
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
    file_class_name: str | None = None           # _file_class_name (Kotlin)
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


class _SymbolView(Mapping[str, SymbolBinding]):
    """The names of ImportBinder.symbol_dict, read through the dicts they are taken from.

    A name of own_dict comes first; else the first dict of layer_list that has the name
    and does not lead it to the file itself gives it, unless the name is in skip_name_set.
    """

    def __init__(
        self, file_rel: str, own_dict: dict[str, SymbolBinding],
        layer_list: list[Mapping[str, SymbolBinding]], skip_name_set: set[str],
    ) -> None:
        """Keep the dicts; none of them is copied.

        Args:
            file_rel: Relative path of the file the names are of.
            own_dict: Names bound for the file by its own import statements.
            layer_list: Dicts of names shared with other files, in the order they give names.
            skip_name_set: Names layer_list does not give (the file defines them itself).
        """
        self._file_rel = file_rel
        self._own_dict = own_dict
        self._layer_list = layer_list
        self._skip_name_set = skip_name_set

    def __getitem__(self, name: str) -> SymbolBinding:
        """Return what a name stands for; KeyError when the file cannot write it."""
        binding = self._own_dict.get(name)
        if binding is not None:
            return binding
        if name not in self._skip_name_set:
            for layer in self._layer_list:
                binding = layer.get(name)
                if binding is not None and binding.file_rel != self._file_rel:
                    return binding
        raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        """Yield each name once, in the order the dicts give them."""
        yield from self._own_dict
        yield_name_set = set(self._own_dict) | self._skip_name_set
        for layer in self._layer_list:
            for name, binding in layer.items():
                if name not in yield_name_set and binding.file_rel != self._file_rel:
                    yield_name_set.add(name)
                    yield name

    def __len__(self) -> int:
        """Return the number of names."""
        return sum(1 for _ in self)


def _log_read_error(subject: str, file_rel: str, error: Exception) -> None:
    """Log that something of a file cannot be read and the names of the file are not linked.

    Args:
        subject: What cannot be read, as the start of the message ("The definitions").
        file_rel: Relative path of the file.
        error: The exception raised while reading it.
    """
    logger.warning(
        f"{subject} of {file_rel} cannot be read; its names are not linked: "
        f"{type(error).__name__}: {error}"
    )


def clear_import_binder_cache() -> None:
    """Forget the binders of every project."""
    _binder_cache_dict.clear()


def import_binder(project_dir: str, project_file_set: set[str]) -> "ImportBinder":
    """Return the binder of a project, built once per project file set.

    Args:
        project_dir: Absolute path to the project root.
        project_file_set: Relative paths of the project files that have a language.

    Returns:
        The ImportBinder, kept until the project file set changes or
        clear_import_binder_cache() is called.
    """
    binder = project_cache_value(_binder_cache_dict, project_dir, project_file_set)
    if binder is not None:
        return binder
    binder = ImportBinder(project_dir, project_file_set)
    _binder_cache_dict[project_dir] = (project_file_set, binder)
    return binder


def _join_module(module: str, name: str) -> str:
    """Return the module string of a name inside a Python module.

    Examples:
        ("pkg", "core")   -> "pkg.core"
        (".", "core")     -> ".core"
        ("..sub", "core") -> "..sub.core"
    """
    return module + name if module.endswith(".") else f"{module}.{name}"


def _symbol_binding(file_rel: str | None, name: str | None = None) -> SymbolBinding | None:
    """Return the binding of a name of a project file, None when file_rel is None.

    Args:
        file_rel: Relative path of the project file, None when the import leads to none.
        name: Name of the definition in file_rel; None for the file as a whole.
    """
    return SymbolBinding(file_rel, name) if file_rel is not None else None


def _package_name(root_node: Node) -> str | None:
    """Return the package a Java or Kotlin file declares, None when it declares none."""
    for child in root_node.children:
        if child.type in _PACKAGE_STATEMENT_TYPE_SET:
            for name_node in child.named_children:
                if name_node.type in _PACKAGE_NAME_TYPE_SET:
                    return "".join(node_text(name_node).split())
    return None


def _file_class_name(root_node: Node) -> str | None:
    """Return the name an annotation of the whole file gives the class of a Kotlin file.

    @file:JvmName("NameUtils")  -> "NameUtils"

    Returns:
        The name, None when no annotation of the file gives one.
    """
    for child in root_node.children:
        if child.type == _FILE_ANNOTATION_TYPE:
            name_match = _FILE_CLASS_NAME_RE.search(node_text(child))
            if name_match is not None:
                return name_match.group(1)
    return None


def _default_file_class_name(file_rel: str, suffix: str) -> str:
    """Return the name of the class of a Kotlin file that no annotation of the file names.

    Each character of the file name without its extension that is neither a letter nor
    a digit 0-9 becomes "_"; the first character is then made upper-case, and "_" is
    put before a first digit; suffix follows.

    Examples:
        ("acme/strings.kt", "Kt")       -> "StringsKt"
        ("acme/string-utils.kt", "Kt")  -> "String_utilsKt"
        ("acme/1abc.kt", "Kt")          -> "_1abcKt"

    Args:
        file_rel: Relative path of the file.
        suffix: The file_class_suffix of the language of the file.
    """
    stem = os.path.splitext(os.path.basename(file_rel))[0]
    name = "".join(
        char if char.isalpha() or char in _FILE_CLASS_DIGIT_SET else _FILE_CLASS_FILL_CHAR
        for char in stem
    )
    if name[:1] in _FILE_CLASS_DIGIT_SET:
        return _FILE_CLASS_FILL_CHAR + name + suffix
    return name[:1].upper() + name[1:] + suffix


def member_function_class_name(definition: DefinitionInfo) -> str | None:
    """Return the class a function defined outside its class is a member of (C++).

    int Shape::count() { ... }      -> "Shape"
    int geo::Box::fit() { ... }     -> "Box"

    Returns:
        The part of the name before the name of the function, for a function definition
        written with "::"; None for any other definition.
    """
    part_list = definition.name.split("::")
    if definition.type not in _FUNCTION_BODY_TYPE_SET or len(part_list) < 2:
        return None
    return part_list[-2]


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
    for node in iter_node(root_node):
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
                while base_node is not None and base_node.type in _BASE_WRAPPER_TYPE_SET:
                    base_node = base_node.child_by_field_name("name")
                if base_node is not None and base_node.type == _BASE_NAME_TYPE:
                    base_name_list.append(node_text(base_node))
        if base_name_list:
            base_name_dict.setdefault(node_text(name_node), base_name_list)
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
        # File -> the extension its language settings are read with (_language_ext)
        self._language_ext_dict: dict[str, str] = {}
        # File -> _file_binding of it
        self._file_binding_dict: dict[str, _FileBinding] = {}
        # File -> the files of _take_over_file_list of it as a set
        self._take_over_set_dict: dict[str, frozenset[str]] = {}
        # A set of _take_over_set_dict -> the one object kept for every equal set
        self._take_over_set_group_dict: dict[frozenset[str], frozenset[str]] = {}
        # File -> _direct_name_dict of it
        self._direct_dict: dict[str, dict[str, SymbolBinding]] = {}
        # File -> _visible_name_dict of it
        self._visible_dict: dict[str, dict[str, SymbolBinding]] = {}
        # Files a _visible_name_dict is read from -> that dict, kept when no two of the
        # files give the same name
        self._visible_group_dict: dict[frozenset[str], dict[str, SymbolBinding]] = {}
        # id of a _visible_name_dict -> _export_name_dict of the files it is read from
        self._export_dict: dict[int, dict[str, SymbolBinding]] = {}
        # File -> symbol_dict of it
        self._symbol_dict: dict[str, _SymbolView] = {}
        # File the names of "from file import *" are taken from -> those names, followed
        # to their definitions (Python)
        self._star_export_dict: dict[str, dict[str, SymbolBinding]] = {}
        # (visibility, extension) -> _implicit_name_dict of the files of "project" visibility
        self._implicit_dict: dict[tuple[str, str], dict[str, SymbolBinding]] = {}
        # (package key, is_class_view) -> _package_entry_dict of it (Java, Kotlin)
        self._package_entry_cache_dict: dict[
            tuple[str, bool], dict[str, list[tuple[str, str | None]]]
        ] = {}
        # (package key, is_class_view) -> _package_binding_dict of it (Java, Kotlin)
        self._package_binding_cache_dict: dict[tuple[str, bool], dict[str, SymbolBinding]] = {}
        # is_class_view -> _qualified_name_dict of it (Java, Kotlin)
        self._qualified_name_cache_dict: dict[bool, dict[str, SymbolBinding]] = {}
        # File -> top_level_name_list of it
        self._top_level_name_dict: dict[str, list[str]] = {}
        # Package key -> files of the package, built on first use (Java, Kotlin)
        self._package_file_dict: dict[str, list[str]] | None = None
        # File -> its package key (_package_index)
        self._file_package_dict: dict[str, str] = {}
        # Function name -> (file, name of the definition in it) of the files that
        # define it, built on first use (C / C++)
        self._implement_file_dict: dict[str, list[tuple[str, str]]] | None = None
        # (file, name) -> implement_file_list of it
        self._implement_list_dict: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # Type -> member -> files with an impl block of the type that defines the
        # member, built on first use (Rust)
        self._impl_member_file_dict: dict[str, dict[str, list[str]]] | None = None
        # File -> scope_binding_list of it
        self._scope_binding_dict: dict[str, list[ScopeBinding]] = {}
        # (file, path of a class in it) -> member name -> the member, with the members
        # of its base classes (C++) and of the impl blocks of other files (Rust)
        self._class_member_dict: dict[tuple[str, str], dict[str, SymbolBinding]] = {}
        # File -> what was read from its syntax tree; None for a file whose tree cannot be read
        self._tree_fact_dict: dict[str, _TreeFact | None] = {}
        # Files whose definitions cannot be read
        self._definition_error_set: set[str] = set()
        # File -> {name of a type: the names of its direct members} (_member_name_list)
        self._member_name_dict: dict[str, dict[str, list[str]]] = {}
        # File -> names "from file import *" takes from it (Python)
        self._star_name_dict: dict[str, set[str]] = {}
        # File -> receiver_name_set (Kotlin)
        self._receiver_name_dict: dict[str, set[str]] = {}
        # package_member_name_set, built on first use (Java, Kotlin)
        self._package_member_name_set: set[str] | None = None

    # == Names of one file ====================================================

    def _language_ext(self, file_rel: str) -> str:
        """Return the extension whose language settings a file is analyzed with."""
        file_ext = self._language_ext_dict.get(file_rel)
        if file_ext is None:
            file_ext = language_ext(os.path.join(self.project_dir, file_rel))
            self._language_ext_dict[file_rel] = file_ext
        return file_ext

    def _bind_kind(self, file_rel: str) -> str | None:
        """Return the "bind" value of the resolve config of a file, None when it has none."""
        return EXT_TO_IMPORT_RESOLVE_DICT.get(self._language_ext(file_rel), {}).get("bind")

    def top_level_name_list(self, file_rel: str) -> list[str]:
        """Return the top-level definition names of a file (top_level_definition_name_list).

        A file whose definitions cannot be read has none (_definition_error).
        """
        name_list = self._top_level_name_dict.get(file_rel)
        if name_list is None:
            try:
                name_list = top_level_definition_name_list(file_rel, self.project_dir)
            except Exception as e:
                self._definition_error(file_rel, e)
                name_list = []
            self._top_level_name_dict[file_rel] = name_list
        return name_list

    def _tree_fact(self, file_rel: str) -> _TreeFact | None:
        """Return what the binder reads from the syntax tree of a file, read once per file.

        The file is parsed once: its import statements, what its "bind" value needs
        (_TREE_FACT_FUNCTION_DICT) and its definitions (file_definition_list) are all
        read from that one tree.

        Returns:
            The _TreeFact; None when parsing the file or reading any of these fails,
            which is logged once per file.
        """
        if file_rel in self._tree_fact_dict:
            return self._tree_fact_dict[file_rel]
        file_abs = os.path.join(self.project_dir, file_rel)
        file_ext = self._language_ext(file_rel)
        try:
            root_node = parse_file(file_abs)[0]
            language, import_query_str = get_import_param_tuple(file_ext)
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
        except Exception as e:
            _log_read_error("The syntax tree", file_rel, e)
            tree_fact = None
        self._tree_fact_dict[file_rel] = tree_fact
        return tree_fact

    def _definition_error(self, file_rel: str, error: Exception) -> None:
        """Remember that the definitions of a file cannot be read, logged once per file."""
        if file_rel in self._definition_error_set:
            return
        self._definition_error_set.add(file_rel)
        _log_read_error("The definitions", file_rel, error)

    def _definition_list(self, file_rel: str) -> list[DefinitionInfo]:
        """Return the definitions of a file (file_definition_list).

        A file whose definitions cannot be read has none (_definition_error).
        """
        if file_rel in self._definition_error_set:
            return []
        try:
            return file_definition_list(
                os.path.join(self.project_dir, file_rel),
                EXT_TO_DEFINITION_DICT[self._language_ext(file_rel)],
            )
        except Exception as e:
            self._definition_error(file_rel, e)
            return []

    def _resolve(
        self, module: str, file_rel: str, is_system_include: bool = False,
    ) -> str | None:
        """Resolve a module string written in a file to a project file.

        is_system_include is True for a C/C++ #include <x.h> (ImportInfo.is_system_include).
        """
        return resolve_module_to_project_path(
            module, file_rel, self.project_file_set, self.source_root_set, self.project_dir,
            is_system_include,
        )

    def _resolve_import_file(
        self, module: str, file_rel: str, file_binding: _FileBinding,
        is_system_include: bool = False,
    ) -> str | None:
        """Resolve a module string written in a file and add the file to import_file_set.

        Returns:
            Relative path of the project file the module resolves to, None when there is none.
        """
        module_file = self._resolve(module, file_rel, is_system_include)
        if module_file is not None:
            file_binding.import_file_set.add(module_file)
        return module_file

    def _file_binding(self, file_rel: str) -> _FileBinding:
        """Return what the import statements of a file bind, read once per file.

        A file whose import statements cannot be read binds nothing; the exception is
        logged (_tree_fact).
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

        tree_fact = self._tree_fact(file_rel)
        if tree_fact is None:
            return file_binding
        try:
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
            _log_read_error("The import statements", file_rel, e)
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
            module_file = self._resolve_import_file(import_info.module, file_rel, file_binding)

            if not import_info.names:
                if import_info.module_alias:
                    self._bind_name(
                        file_binding, import_info, import_info.module_alias,
                        _symbol_binding(module_file),
                    )
                    continue
                part_list = import_info.module.split(".")
                for part_count in range(1, len(part_list) + 1):
                    package = ".".join(part_list[:part_count])
                    self._bind_name(
                        file_binding, import_info, package,
                        _symbol_binding(self._resolve(package, file_rel)),
                    )
                continue

            alias_dict = import_info.alias_map or {}
            for name in import_info.names:
                original_name = alias_dict.get(name, name)
                if name == "*":
                    if module_file is not None:
                        file_binding.wildcard_list.append(module_file)
                    continue
                if module_file is not None:
                    binding = SymbolBinding(module_file, original_name)
                else:
                    binding = _symbol_binding(self._resolve_import_file(
                        _join_module(import_info.module, original_name), file_rel, file_binding,
                    ))
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
            module_file = self._resolve_import_file(import_info.module, file_rel, file_binding)
            if import_info.module_alias:
                self._bind_name(
                    file_binding, import_info, import_info.module_alias,
                    _symbol_binding(module_file),
                )
            alias_dict = import_info.alias_map or {}
            for name in import_info.names:
                if name == "*":
                    if module_file is not None:
                        file_binding.wildcard_list.append(module_file)
                    continue
                self._bind_name(
                    file_binding, import_info, name,
                    _symbol_binding(module_file, alias_dict.get(name, name)),
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

        Each function defined as a member of a class outside the class
        (int Shape::count() { ... }, member_function_class_name) and each class are kept
        with the name of the class (member_scope_list).
        """
        for import_info in import_info_list:
            include_file = self._resolve_import_file(
                import_info.module, file_rel, file_binding, import_info.is_system_include,
            )
            if include_file is not None:
                file_binding.wildcard_list.append(include_file)
        for definition in self._definition_list(file_rel):
            class_name = member_function_class_name(definition)
            if class_name is None and definition.type in _CLASS_NODE_TYPE_SET:
                class_name = definition.name
            if class_name is not None:
                file_binding.member_scope_list.append((class_name, definition))

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
        a name of name_dict and to a top-level definition of the file. Each impl block is
        kept with the name of its type (member_scope_list).
        """
        file_binding.module_scope_list = tree_fact.module_scope_list
        file_binding.member_scope_list = [
            (definition.name, definition)
            for definition in self._definition_list(file_rel)
            if definition.type in ATTACHED_DEFINITION_TYPE_SET
        ]
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
        module_file = self._resolve_import_file(import_info.module, file_rel, file_binding)
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
        import a.b.*             -> every name of the files of package a.b (_package_binding_dict)
        import static a.b.C.*    -> every member of C
        The class of a Kotlin file (a.b.StringsKt) stands for the file, and its members
        are the top-level names of the file (_package_entry_dict).
        """
        for import_info in import_info_list:
            part_list = import_info.module.split(".")
            if "*" in import_info.names:
                if self._package_file_list(import_info.module):
                    file_binding.wildcard_package_list.append(import_info.module)
                    continue
                type_tuple = self._resolve_qualified_name(part_list, file_rel)
                if type_tuple is not None:
                    type_file, type_path = type_tuple
                    member_name_list = (
                        self.top_level_name_list(type_file) if type_path is None
                        else self._member_name_list(type_file, type_path)
                    )
                    for member_name in member_name_list:
                        file_binding.name_dict.setdefault(
                            member_name, SymbolBinding(
                                type_file,
                                member_name if type_path is None else f"{type_path}.{member_name}",
                            ),
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
            tree_fact = self._tree_fact(file_rel)
            if tree_fact is None:
                continue
            package = tree_fact.package_name
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
        package; the next part is looked up among the names of its files as file_rel
        sees them (_package_entry_dict).

        Examples (package com.acme.model with User.java and Strings.kt):
            ["com", "acme", "model", "User"]             -> ("…/User.java", "User")
            ["com", "acme", "model", "User", "Builder"]  -> ("…/User.java", "User.Builder")
            ["com", "acme", "model", "StringsKt", "shout"] (from Java) -> ("…/Strings.kt", "shout")

        Args:
            part_list: The parts of the name.
            file_rel: Relative path of the file the import is written in.

        Returns:
            (file, path of the definition in the file; None for the file as a whole),
            None when no package of the project defines the name. Of several files that
            define it, the one named like it comes first, then the one nearest to
            file_rel.
        """
        is_class_view = self._is_class_view(file_rel)
        for package_part_count in range(len(part_list) - 1, 0, -1):
            package = ".".join(part_list[:package_part_count])
            if not self._package_file_list(package):
                continue
            rest_list = part_list[package_part_count:]
            entry_dict = dict(
                entry_tuple
                for entry_tuple in reversed(
                    self._package_entry_dict(package, is_class_view).get(rest_list[0], [])
                )
                if entry_tuple[0] != file_rel
            )
            if not entry_dict:
                continue
            same_name_list = [
                other_rel for other_rel in entry_dict
                if os.path.splitext(os.path.basename(other_rel))[0] == rest_list[0]
            ]
            other_rel = nearest_first(same_name_list or list(entry_dict), file_rel)[0]
            definition_name = entry_dict[other_rel]
            path_list = ([definition_name] if definition_name is not None else []) + rest_list[1:]
            return other_rel, ".".join(path_list) or None
        return None

    def _is_class_view(self, file_rel: str) -> bool:
        """Return whether a file sees the files of a language with file_class_suffix through their classes.

        A file of a language without file_class_suffix (Java) sees a file of a
        language with it (Kotlin) as its top-level types and the class of the file.
        """
        return not self._file_class_suffix(file_rel)

    def _file_class_suffix(self, file_rel: str) -> str | None:
        """Return the "file_class_suffix" of the resolve config of a file, None when it has none."""
        return EXT_TO_IMPORT_RESOLVE_DICT.get(self._language_ext(file_rel), {}).get(
            "file_class_suffix",
        )

    def _package_entry_dict(
        self, package_key: str, is_class_view: bool,
    ) -> dict[str, list[tuple[str, str | None]]]:
        """Return the names the files of a package give, built once per package and view.

        A file gives its top-level names. Seen with is_class_view, a file of a language
        with file_class_suffix gives instead its top-level types (CLASS_DEFINITION_TYPE_SET)
        and, when it has other top-level definitions, the class of the file: the name
        @file:JvmName gives, else _default_file_class_name ("strings.kt" -> "StringsKt").

        Args:
            package_key: Key of the package (_package_index).
            is_class_view: The _is_class_view of the file the names are read for.

        Returns:
            {name: [(file, name of the definition in it; None for the class of the
            file), ...] in path order}.
        """
        cache_key_tuple = (package_key, is_class_view)
        entry_dict = self._package_entry_cache_dict.get(cache_key_tuple)
        if entry_dict is not None:
            return entry_dict
        entry_dict = {}
        for other_rel in self._package_file_list(package_key):
            suffix = self._file_class_suffix(other_rel)
            if not (is_class_view and suffix):
                for name in self.top_level_name_list(other_rel):
                    entry_dict.setdefault(name, []).append((other_rel, name))
                continue
            name_set = set(self.top_level_name_list(other_rel))
            top_level_list = [
                definition for definition in select_top_level_definitions(self._definition_list(other_rel))
                if definition.name in name_set
            ]
            for definition in top_level_list:
                if definition.type in CLASS_DEFINITION_TYPE_SET:
                    entry_dict.setdefault(definition.name, []).append((other_rel, definition.name))
            if any(definition.type not in CLASS_DEFINITION_TYPE_SET for definition in top_level_list):
                tree_fact = self._tree_fact(other_rel)
                class_name = (
                    tree_fact.file_class_name if tree_fact is not None and tree_fact.file_class_name
                    else _default_file_class_name(other_rel, suffix)
                )
                entry_dict.setdefault(class_name, []).append((other_rel, None))
        self._package_entry_cache_dict[cache_key_tuple] = entry_dict
        return entry_dict

    def _package_binding_dict(self, package_key: str, is_class_view: bool) -> dict[str, SymbolBinding]:
        """Return {name: what it stands for} of the names a package gives, built once per package and view.

        Of the files of _package_entry_dict that give a name, the first in path order.
        """
        cache_key_tuple = (package_key, is_class_view)
        binding_dict = self._package_binding_cache_dict.get(cache_key_tuple)
        if binding_dict is None:
            binding_dict = {
                name: SymbolBinding(*entry_list[0])
                for name, entry_list in self._package_entry_dict(package_key, is_class_view).items()
            }
            self._package_binding_cache_dict[cache_key_tuple] = binding_dict
        return binding_dict

    def _qualified_name_dict(self, is_class_view: bool) -> dict[str, SymbolBinding]:
        """Return the names of every package written with their package, built once per view.

        Examples (package com.acme.model with User.java):
            "com.acme.model.User" -> ("…/User.java", "User")

        Returns:
            {package + "." + name: what it stands for} of the names of
            _package_binding_dict of each package declared by a package statement.
        """
        name_dict = self._qualified_name_cache_dict.get(is_class_view)
        if name_dict is None:
            name_dict = {
                f"{package_key}.{name}": binding
                for package_key in self._package_index() if not package_key.startswith("/")
                for name, binding in self._package_binding_dict(package_key, is_class_view).items()
            }
            self._qualified_name_cache_dict[is_class_view] = name_dict
        return name_dict

    def _member_name_list(self, file_rel: str, type_path: str) -> list[str]:
        """Return the names of the definitions written inside a type of a file.

        Args:
            file_rel: Relative path of the file.
            type_path: Path of the type in the file ("User", "User.Builder").

        Returns:
            The names of the direct members (member_owner_dict) of the definitions
            named like the last part of type_path, without duplicates; the members of
            a class written inside such a definition are not among them. The members
            of each file are indexed once.
        """
        name_list_dict = self._member_name_dict.get(file_rel)
        if name_list_dict is None:
            definition_list = self._definition_list(file_rel)
            owner_dict = member_owner_dict(definition_list)
            name_list_dict = {}
            for definition in definition_list:
                owner = owner_dict.get(id(definition))
                if owner is not None:
                    name_list = name_list_dict.setdefault(owner.name, [])
                    if definition.name not in name_list:
                        name_list.append(definition.name)
            self._member_name_dict[file_rel] = name_list_dict
        return name_list_dict.get(type_path.split(".")[-1], [])

    def _implicit_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return the names a file can write without an import statement, built once per group of files.

        "package" visibility (Java / Kotlin): the names of the files of its package
        (_package_binding_dict). "project" visibility (SQL): the top-level names of
        every file of its language.

        Returns:
            "package": the dict of _package_binding_dict, in which the class of a Kotlin
            file seen from Java stands for the file (name None); "project": {name: (first
            file in path order that defines it, name)}. The file itself is among those
            files. Empty for a language without implicit visibility.
        """
        file_ext = self._language_ext(file_rel)
        visibility = EXT_TO_IMPLICIT_VISIBILITY_DICT.get(file_ext)
        if visibility == "package":
            self._package_index()
            return self._package_binding_dict(
                self._file_package_dict.get(file_rel) or "", self._is_class_view(file_rel),
            )
        if visibility != "project":
            return {}
        name_dict = self._implicit_dict.get((visibility, file_ext))
        if name_dict is not None:
            return name_dict
        name_dict = {}
        for other_rel in sorted(self.project_file_set):
            if self._language_ext(other_rel) == file_ext:
                for name in self.top_level_name_list(other_rel):
                    name_dict.setdefault(name, SymbolBinding(other_rel, name))
        self._implicit_dict[(visibility, file_ext)] = name_dict
        return name_dict

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
        """Return a file and the files whose every name it passes on.

        The files are kept as a set for _take_over_file_set; the list is built on each call.

        Returns:
            The file itself, then the files it takes over as a whole (from m import *,
            #include, export *) and the ones those take over, each once, in the order
            they are reached following the first of them first. The file alone when
            its "bind" is in _OWN_NAME_EXPORT_BIND_SET.
        """
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
        if file_rel not in self._take_over_set_dict:
            file_set = frozenset(visit_set)
            self._take_over_set_dict[file_rel] = self._take_over_set_group_dict.setdefault(
                file_set, file_set,
            )
        return take_over_file_list

    def _take_over_file_set(self, file_rel: str) -> frozenset[str]:
        """Return the files of _take_over_file_list as a set, read once per file; equal sets are one object."""
        file_set = self._take_over_set_dict.get(file_rel)
        if file_set is None:
            self._take_over_file_list(file_rel)
            file_set = self._take_over_set_dict[file_rel]
        return file_set

    def _takes_over(self, file_rel: str, other_rel: str) -> bool:
        """Return whether other_rel is a file of _take_over_file_list of file_rel.

        Looked up through the sets of the files file_rel takes over as a whole, without
        building the set of file_rel itself.
        """
        if other_rel == file_rel:
            return True
        if self._bind_kind(file_rel) in _OWN_NAME_EXPORT_BIND_SET:
            return False
        return any(
            other_rel in self._take_over_file_set(wildcard_file)
            for wildcard_file in self._file_binding(file_rel).wildcard_list
        )

    def _direct_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return the names another file can take from a file itself, as they are written.

        In order, an earlier one kept for a name given twice: the names its export
        statements with a source pass on, its top-level definitions, the names it
        exports under another name, the names its import statements bind for the
        whole file. Of a file whose "bind" is in _OWN_NAME_EXPORT_BIND_SET, its
        top-level definitions only.

        Returns:
            {name: the file and the name it stands for, not followed further}. Read
            once per file.
        """
        direct_dict = self._direct_dict.get(file_rel)
        if direct_dict is not None:
            return direct_dict
        direct_dict = {
            name: SymbolBinding(file_rel, name) for name in self.top_level_name_list(file_rel)
        }
        if self._bind_kind(file_rel) not in _OWN_NAME_EXPORT_BIND_SET:
            file_binding = self._file_binding(file_rel)
            direct_dict = {
                **file_binding.name_dict, **file_binding.export_alias_dict,
                **direct_dict, **file_binding.export_dict,
            }
        self._direct_dict[file_rel] = direct_dict
        return direct_dict

    def _visible_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return every name another file can take from a file, as it is written, read once per file.

        Returns:
            The names of the file itself and of each file it passes on as a whole
            (_direct_name_dict over _take_over_file_list); the first file that gives a
            name is kept. The bindings are not followed further. Files that pass on
            the same files, no two of which give the same name, share one dict.
        """
        visible_dict = self._visible_dict.get(file_rel)
        if visible_dict is not None:
            return visible_dict
        group_key = self._take_over_file_set(file_rel)
        visible_dict = self._visible_group_dict.get(group_key)
        if visible_dict is None:
            visible_dict = {}
            name_count = 0
            for take_over_file in self._take_over_file_list(file_rel):
                direct_dict = self._direct_name_dict(take_over_file)
                name_count += len(direct_dict)
                for name, binding in direct_dict.items():
                    visible_dict.setdefault(name, binding)
            if name_count == len(visible_dict):
                self._visible_group_dict[group_key] = visible_dict
        self._visible_dict[file_rel] = visible_dict
        return visible_dict

    def star_name_set(self, file_rel: str) -> set[str]:
        """Return the names "from file import *" takes from a Python file.

        A file with an __all__ written with constants gives the names it lists; any
        other file gives the names it defines, the names its import statements bind
        and the names it takes with "from other import *" itself, without the names
        that start with "_". The files that take over one another are worked out
        together; the result does not depend on which of them is asked first.

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
        defines it (_follow). Read once per _visible_name_dict.
        """
        visible_dict = self._visible_name_dict(file_rel)
        export_dict = self._export_dict.get(id(visible_dict))
        if export_dict is not None:
            return export_dict
        export_dict = {name: self._follow(binding) for name, binding in visible_dict.items()}
        if export_dict == visible_dict:
            export_dict = visible_dict
        self._export_dict[id(visible_dict)] = export_dict
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
        tree_fact = self._tree_fact(file_rel)
        if tree_fact is None or not tree_fact.has_module_export:
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

    def _iter_file_definition_list(
        self, bind_kind: str,
    ) -> Iterator[tuple[str, list[DefinitionInfo]]]:
        """Yield the definitions of each project file whose "bind" is bind_kind, in path order.

        The import statements of each file are read from the same tree as its
        definitions (_tree_fact). A file whose tree cannot be read is left out.

        Args:
            bind_kind: Value of "bind" of the resolve config of the files to read.

        Yields:
            (relative path of the file, its definitions) of each such file.
        """
        for file_rel in sorted(self.project_file_set):
            if self._bind_kind(file_rel) != bind_kind or self._tree_fact(file_rel) is None:
                continue
            yield file_rel, self._definition_list(file_rel)

    def _implement_index(self) -> dict[str, list[tuple[str, str]]]:
        """Return {function name: (file, name of the definition) of each function definition of that name}, built once.

        The files whose "bind" is "include" are read; a member function written inside
        its class is left out. The leading scopes of a definition written with its
        scopes that are names of namespaces of those files are dropped (geo::area ->
        "area"), and a member defined outside its class is found under its name with
        each number of the leading scopes left (Table::Cmp::make -> "Table::Cmp::make",
        "Cmp::make").
        """
        if self._implement_file_dict is not None:
            return self._implement_file_dict
        file_definition_tuple_list = list(self._iter_file_definition_list("include"))
        namespace_name_set = {
            part
            for _, definition_list in file_definition_tuple_list for definition in definition_list
            if definition.type in TRANSPARENT_DEFINITION_TYPE_SET
            for part in definition.name.split("::")
        }
        implement_file_dict: dict[str, list[tuple[str, str]]] = {}
        for file_rel, definition_list in file_definition_tuple_list:
            owner_dict = member_owner_dict(definition_list)
            for definition in definition_list:
                if definition.type not in _FUNCTION_BODY_TYPE_SET or id(definition) in owner_dict:
                    continue
                part_list = definition.name.split("::")
                while len(part_list) > 1 and (not part_list[0] or part_list[0] in namespace_name_set):
                    part_list = part_list[1:]
                for start in range(max(1, len(part_list) - 1)):
                    add_unique(
                        implement_file_dict, "::".join(part_list[start:]),
                        (file_rel, definition.name),
                    )
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
        implement_key_tuple = (file_rel, definition_name)
        if implement_key_tuple not in self._implement_list_dict:
            self._implement_list_dict[implement_key_tuple] = find_function(
                self, file_rel, definition_name,
            )
        return self._implement_list_dict[implement_key_tuple]

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
            (implement_file, name)
            for implement_file, name in self._implement_index().get(implement_name, [])
            if implement_file != file_rel and self._takes_over(implement_file, file_rel)
        ]

    # == Members of a class, used inside a member defined outside it (C++, Rust) ==

    def _base_name_list(self, file_rel: str, class_name: str) -> list[str]:
        """Return the names of the base classes of a class of a file (_base_class_name_dict)."""
        tree_fact = self._tree_fact(file_rel)
        return tree_fact.base_name_dict.get(class_name, []) if tree_fact is not None else []

    def _class_member_binding_dict(
        self, class_file: str, class_path: str,
    ) -> dict[str, SymbolBinding]:
        """Return the members of a class and of its base classes, looked up once per class.

        Args:
            class_file: Relative path of the file that defines the class.
            class_path: Path of the class in the file ("Shape", "Outer.Inner").

        Returns:
            {member name: the member as "class_path.member" of the file of its class},
            without the constructors (the members named like their class). Rust: a
            member only an impl block of another file defines (_impl_member_index) is
            "class_path.member" of that file when the file binds a name to the class.
            A member of the class itself comes before a member of the same name of a
            base class, and an earlier base class before a later one. A base class is
            looked up among the top-level names of class_file, then among the names it
            takes from other files.
        """
        class_key_tuple = (class_file, class_path)
        member_dict = self._class_member_dict.get(class_key_tuple)
        if member_dict is not None:
            return member_dict
        # Registered before it is filled: a class that leads back to itself gets it as it is
        member_dict = {}
        self._class_member_dict[class_key_tuple] = member_dict
        class_name = class_path.split(".")[-1]
        for member_name in self._member_name_list(class_file, class_path):
            if member_name != class_name:
                member_dict[member_name] = SymbolBinding(class_file, f"{class_path}.{member_name}")
        if self._bind_kind(class_file) == "path":
            self._add_impl_member(class_file, class_path, member_dict)
        own_name_set = set(self.top_level_name_list(class_file))
        for base_name in self._base_name_list(class_file, class_name):
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

    def _add_impl_member(
        self, class_file: str, class_path: str, member_dict: dict[str, SymbolBinding],
    ) -> None:
        """Add the members the impl blocks of other files define for a Rust type.

        Args:
            class_file: Relative path of the file that defines the type.
            class_path: Path of the type in the file.
            member_dict: Member name -> the member; a name it has is kept, modified in place.
        """
        type_binding = SymbolBinding(class_file, class_path)
        member_file_dict = self._impl_member_index().get(class_path.split(".")[-1], {})
        for member_name, impl_file_list in member_file_dict.items():
            impl_file = next(
                (
                    impl_file for impl_file in impl_file_list
                    if impl_file != class_file and type_binding in self.symbol_dict(impl_file).values()
                ),
                None,
            )
            if impl_file is not None:
                member_dict.setdefault(member_name, SymbolBinding(impl_file, f"{class_path}.{member_name}"))

    def member_scope_list(
        self, file_rel: str,
    ) -> list[tuple[DefinitionInfo, dict[str, SymbolBinding]]]:
        """Return the members a file can write without their class, for the definitions they can be written in.

        C++: inside a function defined as a member of a class outside the class
        (int Shape::count() { ... }) and inside a class, a member of the class or of
        one of its base classes is written by its name alone. Rust: inside an impl
        block of a type, a member of the type is written after self. A class named
        like a class (CLASS_DEFINITION_TYPE_SET) the file defines itself, at any
        depth, is that class of the file, and only the members other files give it
        (its base classes, the impl blocks of other files) are given: the file refers
        to its own definitions by their names. Any other class is the one of its name
        in symbol_dict.

        Args:
            file_rel: Relative path of the file.

        Returns:
            (the function, class or impl block, {member name: the member}) of each such
            definition that has such members, in line order. Empty for a file without one.
        """
        scope_list: list[tuple[DefinitionInfo, dict[str, SymbolBinding]]] = []
        file_binding = self._file_binding(file_rel)
        if not file_binding.member_scope_list:
            return scope_list
        symbol_dict = self.symbol_dict(file_rel)
        own_class_name_set = {
            definition.name for definition in self._definition_list(file_rel)
            if definition.type in CLASS_DEFINITION_TYPE_SET
        }
        for class_name, definition in file_binding.member_scope_list:
            class_binding = (
                SymbolBinding(file_rel, class_name) if class_name in own_class_name_set
                else symbol_dict.get(class_name)
            )
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
                scope_list.append((definition, member_dict))
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

    def _impl_member_index(self) -> dict[str, dict[str, list[str]]]:
        """Return {Type: {member: files with an impl block of Type that defines member}}, built once.

        The files whose "bind" is "path" are read.
        """
        if self._impl_member_file_dict is not None:
            return self._impl_member_file_dict
        impl_member_file_dict: dict[str, dict[str, list[str]]] = {}
        for file_rel, definition_list in self._iter_file_definition_list("path"):
            owner_dict = member_owner_dict(definition_list)
            for definition in definition_list:
                owner = owner_dict.get(id(definition))
                if owner is None or owner.type not in ATTACHED_DEFINITION_TYPE_SET:
                    continue
                add_unique(
                    impl_member_file_dict.setdefault(owner.name, {}), definition.name, file_rel,
                )
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
        type_binding = SymbolBinding(file_rel, "::".join(part_list[:-1]))
        return [
            (impl_file, "::".join(part_list[-2:]))
            for impl_file in self._impl_member_index().get(part_list[-2], {}).get(part_list[-1], [])
            if impl_file != file_rel and type_binding in self.symbol_dict(impl_file).values()
        ]

    # == Public: names of a file ==============================================

    def _star_export_name_dict(self, file_rel: str) -> dict[str, SymbolBinding]:
        """Return the names "from file import *" takes from a Python file, followed to their definitions.

        Returns:
            The names of _export_name_dict that star_name_set gives, then each other
            name star_name_set gives that is a module of the package (in name order).
            Read once per file.
        """
        name_dict = self._star_export_dict.get(file_rel)
        if name_dict is not None:
            return name_dict
        star_name_set = self.star_name_set(file_rel)
        name_dict = {
            name: binding for name, binding in self._export_name_dict(file_rel).items()
            if name in star_name_set
        }
        # A name __all__ lists that is a module of the package
        for name in sorted(star_name_set - set(name_dict)):
            module_part_file = self._module_part_file(file_rel, name)
            if module_part_file is not None:
                name_dict[name] = SymbolBinding(module_part_file, None)
        self._star_export_dict[file_rel] = name_dict
        return name_dict

    def symbol_dict(self, file_rel: str) -> Mapping[str, SymbolBinding]:
        """Return the names a file can write that come from other project files.

        In order: the names its import statements bind for the whole file; the names
        visible without an import statement (_implicit_name_dict); the names of the
        definitions, files and packages it takes over as a whole; Java / Kotlin: the
        names of every package written with their package (_qualified_name_dict). A
        later group does not replace a name of an earlier one, and the groups after
        the first leave out the names the file defines at its top level.

        Args:
            file_rel: Relative path of the file.

        Returns:
            {name: binding followed to the file that defines it}, without the names
            that lead to the file itself; a read-only view of the dicts the names are
            taken from (_SymbolView), built once per file.
        """
        symbol_dict = self._symbol_dict.get(file_rel)
        if symbol_dict is not None:
            return symbol_dict
        file_binding = self._file_binding(file_rel)
        own_dict = {}
        for name, binding in file_binding.name_dict.items():
            target_binding = self._follow(binding)
            if target_binding.file_rel != file_rel:
                own_dict[name] = target_binding

        layer_list: list[Mapping[str, SymbolBinding]] = [self._implicit_name_dict(file_rel)]
        if file_binding.is_wildcard_local:
            layer_list.append(file_binding.wildcard_name_dict)
            is_star = self._bind_kind(file_rel) in _STAR_NAME_BIND_SET
            layer_list.extend(
                self._star_export_name_dict(wildcard_file) if is_star
                else self._export_name_dict(wildcard_file)
                for wildcard_file in file_binding.wildcard_list
            )
        if self._bind_kind(file_rel) == "package":
            is_class_view = self._is_class_view(file_rel)
            layer_list.extend(
                self._package_binding_dict(package, is_class_view)
                for package in file_binding.wildcard_package_list
            )
            layer_list.append(self._qualified_name_dict(is_class_view))
        symbol_dict = _SymbolView(
            file_rel, own_dict, [layer for layer in layer_list if layer],
            set(self.top_level_name_list(file_rel)),
        )
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
                    definition_list = self._definition_list(file_rel)
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
            name_set = {
                definition.name for definition in self._definition_list(file_rel)
                if definition.has_receiver and definition.name in top_level_name_set
            }
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
    tree_fact.file_class_name = _file_class_name(root_node)


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
