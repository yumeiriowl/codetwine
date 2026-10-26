from collections.abc import Iterator
from dataclasses import dataclass
from tree_sitter import Language, Query, QueryCursor, Node
from codetwine.parsers.ts_node import node_text
from codetwine.extractors.cobol_source import COPY_KIND, NAME_KIND_TUPLE, CobolSource
from codetwine.extractors.definitions import (
    DEFAULT_EXPORT_NAME,
    commonjs_export_assignment,
    export_value_node,
    is_module_string,
    require_module,
)
from codetwine.extractors.rust_path import UseCacheDict, node_line_tuple, rust_import_list

# Compiled import queries: (id of the Language, query string) -> Query
_query_cache_dict: dict[tuple[int, str], Query] = {}

# Child node types of an import statement that stand for "every name" (Java: asterisk,
# Kotlin / JS / TS: *, Python: wildcard_import)
_WILDCARD_NODE_TYPE_SET = {"asterisk", "*", "wildcard_import"}

# Node type of an export statement (JS / TS)
_EXPORT_STATEMENT_TYPE = "export_statement"

# Node type of a template string (JS / TS)
_TEMPLATE_STRING_TYPE = "template_string"

# Node type of the path of an #include <x.h> (C / C++)
_SYSTEM_INCLUDE_TYPE = "system_lib_string"

# The name a Python file lists the names "from module import *" takes in, the methods
# that add to it, and the node types of the constants it is written with
_PYTHON_ALL_NAME = b"__all__"
_PYTHON_ALL_METHOD_SET = {b"extend", b"append"}
_PYTHON_STRING_TYPE = "string"
_PYTHON_SEQUENCE_TYPE_SET = {"list", "tuple", "parenthesized_expression"}


@dataclass
class ImportInfo:
    """Data class holding information about a single import statement."""

    module: str         # Import source module name/path
    names: list[str]    # List of names specified in from ... import (empty list for languages without this)
    line: int           # Line number of the import statement (1-based)
    # "Y" in import X as Y; the name bound to the module as a whole in JS/TS
    # (import * as Y, const Y = require(...))
    module_alias: str | None = None
    # {alias name -> original name} (for from X import a as b: {"b": "a"}); the original
    # name of a JS/TS default import is DEFAULT_EXPORT_NAME
    alias_map: dict[str, str] | None = None
    # (first line, last line) the names are bound for: the function an import statement
    # is written in, the block or inline module of a Rust use declaration, the statement
    # itself for a JS/TS export statement with a source; None for the whole file
    scope_line_tuple: tuple[int, int] | None = None
    # True for a JS/TS export statement with a source (export { a } from "./m"): its
    # names are passed on to the files that import the file
    is_export: bool = False
    # True for a member read where its module is required (JS/TS: require("./m").run()):
    # the statement is a usage of the names on its line
    is_use: bool = False
    # True for a C/C++ #include <x.h>: the directory of the file is not searched for it
    is_system_include: bool = False


def cobol_module(kind: str, name: str, library: str = "") -> str:
    """Return the module string of a statement of a COBOL, JCL or CSD file that names a file.

    Inverse of cobol_module_part.

    Examples:
        ("COPY", "CUSTREC")             -> "COPY CUSTREC"
        ("COPY", "CUSTREC", "COPYLIB")  -> "COPY CUSTREC OF COPYLIB"
        ("CALL", "TAXCALC")             -> "CALL TAXCALC"
        ("TRANSID", "TX01")             -> "TRANSID TX01"
        ("PROC", "REPROC")              -> "PROC REPROC"
    """
    return f"{kind} {name} OF {library}" if library else f"{kind} {name}"


def cobol_module_part(module: str) -> tuple[str, str, str]:
    """Split the module string of a COBOL, JCL or CSD statement into (kind, name, library).

    Inverse of cobol_module.

    Examples:
        "COPY CUSTREC OF COPYLIB"  -> ("COPY", "CUSTREC", "COPYLIB")
        "CALL TAXCALC"             -> ("CALL", "TAXCALC", "")
        "CALL LIST OF ITEMS"       -> ("CALL", "LIST OF ITEMS", "")
        "INCLUDE DBSTEPS"          -> ("INCLUDE", "DBSTEPS", "")
    """
    kind, _, operand = module.partition(" ")
    if kind != COPY_KIND:
        return kind, operand, ""
    name, _, library = operand.partition(" OF ")
    return kind, name, library


def _cobol_import_list(cobol_source: CobolSource) -> list[ImportInfo]:
    """Return the statements of a COBOL, JCL or CSD file that name another file, as ImportInfo.

    COPY name        -> module "COPY name", names ["*"]
    CALL name        -> module "CALL name", names [name]
    TRANSID name     -> module "TRANSID name", names [name] (EXEC CICS TRANSID)
    PROC name        -> module "PROC name", names ["*"] (JCL)
    INCLUDE name     -> module "INCLUDE name", names ["*"] (JCL)
    MAPSET name      -> module "MAPSET name", names ["*"] (CSD, and CSD commands of JCL)
    The same statement on the same line is returned once.

    Args:
        cobol_source: The CobolSource of the file.

    Returns:
        A list of ImportInfo in line order.
    """
    import_by_key_dict: dict[tuple[str, int], ImportInfo] = {}
    for cobol_import in cobol_source.import_list:
        module = cobol_module(cobol_import.kind, cobol_import.name, cobol_import.library)
        import_by_key_dict.setdefault((module, cobol_import.line), ImportInfo(
            module=module,
            names=[cobol_import.name] if cobol_import.kind in NAME_KIND_TUPLE else ["*"],
            line=cobol_import.line,
        ))
    return list(import_by_key_dict.values())


def _scope_line_tuple(import_node: Node, scope_type_set: set[str]) -> tuple[int, int] | None:
    """Return the lines an import statement binds its names for, None for the whole file.

    Args:
        import_node: The node of the import statement.
        scope_type_set: AST node types that open a scope (functions, lambdas).

    Returns:
        The lines of the statement itself for an export statement with a source, else
        the lines of the innermost scope_type_set node around the statement, else None.
    """
    if import_node.type == _EXPORT_STATEMENT_TYPE:
        return node_line_tuple(import_node)
    scope_node = import_node.parent
    while scope_node is not None and scope_node.type not in scope_type_set:
        scope_node = scope_node.parent
    return node_line_tuple(scope_node) if scope_node is not None else None


def _member_use_import(module: str, member_node: Node, import_node: Node) -> ImportInfo | None:
    """Return the import a member read where its module is required gives (JS / TS).

    require("./m").run()  -> the name run of ./m, bound and used on the lines of the access

    Args:
        module: The module string.
        member_node: The property node (@member_use).
        import_node: The member access node (@import_node).

    Returns:
        The ImportInfo, None when the access is the value a declaration binds to a name
        (const run = require("./m").run), which binds that name instead.
    """
    parent = import_node.parent
    if parent is not None and parent.type == "variable_declarator":
        name_node = parent.child_by_field_name("name")
        if name_node is not None and name_node.type == "identifier":
            return None
    return ImportInfo(
        module=module, names=[node_text(member_node)],
        line=member_node.start_point[0] + 1, scope_line_tuple=node_line_tuple(import_node),
        is_use=True,
    )


def _callback_import(module: str, callback_node: Node) -> ImportInfo | None:
    """Return the import the first parameter of a callback given a module binds (JS / TS).

    import("./m").then((m) => ...)              -> "m": the module, on the lines of the callback
    import("./m").then(({ run, a: b }) => ...)  -> "run" and "b" (the name a of the module)

    Args:
        module: The module string.
        callback_node: The function given the module (@callback).

    Returns:
        The ImportInfo, None when the callback has no parameter or one that is neither
        a name nor an object pattern.
    """
    parameter_node = callback_node.child_by_field_name("parameter")
    if parameter_node is None:
        parameter_list_node = callback_node.child_by_field_name("parameters")
        parameter_node = next(iter(parameter_list_node.named_children), None) if parameter_list_node else None
    # TS: a parameter holds its pattern in a field
    if parameter_node is not None and parameter_node.type != "identifier":
        parameter_node = parameter_node.child_by_field_name("pattern") or parameter_node
    if parameter_node is None:
        return None
    import_info = ImportInfo(
        module=module, names=[], line=parameter_node.start_point[0] + 1,
        scope_line_tuple=node_line_tuple(callback_node),
    )
    if parameter_node.type == "identifier":
        import_info.module_alias = node_text(parameter_node)
        return import_info
    if parameter_node.type != "object_pattern":
        return None
    for child in parameter_node.named_children:
        if child.type == "shorthand_property_identifier_pattern":
            import_info.names.append(node_text(child))
        elif child.type == "pair_pattern":
            key_node = child.child_by_field_name("key")
            value_node = child.child_by_field_name("value")
            if key_node is not None and value_node is not None and value_node.type == "identifier":
                name = node_text(value_node)
                import_info.names.append(name)
                _set_alias(import_info, name, node_text(key_node))
    return import_info


def extract_imports(
    root_node: Node | CobolSource,
    language: Language,
    import_query_str: str | None,
    scope_types: set[str] | None = None,
) -> list[ImportInfo]:
    """Extract import statements from the AST and return them.

    Uses tree-sitter queries (S-expression pattern matching) to read the import
    syntax of each language.

    Processing flow:
    1. A CobolSource is read without a query (_cobol_import_list).
    2. The query is compiled once per language and query string (_import_query).
    3. Each match is read into the ImportInfo of its statement: a Rust path
       (_add_rust_import), a member read where its module is required or a callback
       given a module (_add_special_import), or any other import statement
       (_add_statement_import).

    Query capture names:
        @module      -> Import source (module name, path, header name, etc.)
        @name        -> Individually imported name (the Y in "from X import Y")
        @default_name   -> Name bound to the default export of the module (JS/TS)
        @namespace_name -> Name bound to the module as a whole (JS/TS)
        @member_name -> Member of the module the @name of the statement is bound to
                        (JS: const X = require("module").Y)
        @member_use  -> Member read where its module is required (JS: require("module").Y)
        @callback    -> Function given the module (JS: import("module").then((X) => ...))
        @_require_func -> Function called with the module; a match whose function is not
                        "require" is skipped (JS)
        @_then_func  -> Method called on import("module"); a callback is read only for "then"
        @import_node -> The entire import statement node (for line number retrieval)
        @path_item   -> A Rust node naming a module path; expanded by rust_import_list
                        into one import per name

    When the same import statement has multiple @name captures (from X import Y, Z),
    they are consolidated into a single ImportInfo.

    For a CobolSource its COPY and CALL statements and EXEC CICS TRANSID options are
    returned, without a query (for a JCL file: its programs, procedures and INCLUDE
    members; for a CSD file: its programs and mapsets).

    Args:
        root_node: The AST root node covering the entire file, or the CobolSource of a
            COBOL file, a BMS source, a JCL file or a CSD file.
        language: tree-sitter Language object (required for Query creation).
        import_query_str: tree-sitter query string (EXT_TO_IMPORT_QUERY_DICT of
                          config/settings.py). Returns an empty list when None (for
                          languages with no import query defined).
        scope_types: AST node types that open a scope (functions, lambdas). An import
                     statement written inside one binds its names for the lines of the
                     innermost one (ImportInfo.scope_line_tuple). None reads every
                     statement as binding for the whole file.

    Returns:
        A list of ImportInfo. A JS/TS export statement with a source has is_export set
        and binds its names on its own lines.
    """
    # == Step 1: COBOL, JCL and CSD sources =====================================
    if isinstance(root_node, CobolSource):
        return _cobol_import_list(root_node)
    if not import_query_str:
        return []

    # == Step 2: Compile the query ==============================================
    cursor = QueryCursor(_import_query(language, import_query_str))

    # == Step 3: Read each match into the ImportInfo of its statement ============
    # Key: (module string, line number), for Rust with the lines the names are bound
    # for -> ImportInfo; the @name captures of one import statement share one entry
    import_by_key_dict: dict[tuple, ImportInfo] = {}
    # Rust: the use declarations of the blocks and inline modules of the file
    use_cache_dict: UseCacheDict = {}
    for _, capture_dict in cursor.matches(root_node):
        # CommonJS: a @_require_func capture whose function is not "require" is skipped
        require_func_node_list = capture_dict.get("_require_func", [])
        if require_func_node_list:
            if node_text(require_func_node_list[0]) != "require":
                continue

        # Rust: one node can bring several paths into use (use a::{b, c as d})
        path_item_node_list = capture_dict.get("path_item", [])
        if path_item_node_list:
            _add_rust_import(path_item_node_list[0], use_cache_dict, import_by_key_dict)
            continue

        module_node_list = capture_dict.get("module", [])
        import_node_list = capture_dict.get("import_node", [])
        # A template string with a ${} part is built at run time
        if not module_node_list or (
            module_node_list[0].type == _TEMPLATE_STRING_TYPE
            and not is_module_string(module_node_list[0])
        ):
            continue
        module = _strip_quote(node_text(module_node_list[0]))

        # A member read where its module is required, and the callback given a module:
        # each binds its names for its own lines
        member_use_node_list = capture_dict.get("member_use", [])
        callback_node_list = capture_dict.get("callback", [])
        if member_use_node_list or callback_node_list:
            _add_special_import(
                module, member_use_node_list, callback_node_list, import_node_list,
                capture_dict, import_by_key_dict,
            )
            continue

        _add_statement_import(
            module, module_node_list[0], import_node_list, capture_dict, scope_types,
            import_by_key_dict,
        )

    return list(import_by_key_dict.values())


def _import_query(language: Language, import_query_str: str) -> Query:
    """Return the compiled query, compiled once per language and query string."""
    query_key_tuple = (id(language), import_query_str)
    query = _query_cache_dict.get(query_key_tuple)
    if query is None:
        query = Query(language, import_query_str)
        _query_cache_dict[query_key_tuple] = query
    return query


def _set_alias(import_info: ImportInfo, alias_name: str, original_name: str) -> None:
    """Record in the alias_map of an import that alias_name stands for original_name."""
    if import_info.alias_map is None:
        import_info.alias_map = {}
    import_info.alias_map[alias_name] = original_name


def _add_rust_import(
    path_item_node: Node, use_cache_dict: UseCacheDict, import_by_key_dict: dict[tuple, ImportInfo],
) -> None:
    """Add the imports a Rust node naming a module path (@path_item) gives, one per module.

    Args:
        path_item_node: The node captured by @path_item.
        use_cache_dict: The use declarations of the blocks and inline modules of the
            file (rust_import_list); modified in place.
        import_by_key_dict: {(module, line, scope lines): ImportInfo}; modified in place.
    """
    line = path_item_node.start_point[0] + 1
    for module, name, original, scope_line_tuple in rust_import_list(
        path_item_node, use_cache_dict,
    ):
        import_info = import_by_key_dict.setdefault(
            (module, line, scope_line_tuple),
            ImportInfo(
                module=module, names=[], line=line, scope_line_tuple=scope_line_tuple,
            ),
        )
        if name and name not in import_info.names:
            import_info.names.append(name)
            if original:
                _set_alias(import_info, name, original)


def _add_special_import(
    module: str,
    member_use_node_list: list[Node],
    callback_node_list: list[Node],
    import_node_list: list[Node],
    capture_dict: dict[str, list[Node]],
    import_by_key_dict: dict[tuple, ImportInfo],
) -> None:
    """Add the import of a member read on a require() or of a callback given a module (JS / TS).

    The names of an import with the same module, line, scope lines and is_use are
    added to the one already there.

    Args:
        module: The module string.
        member_use_node_list: The nodes captured by @member_use.
        callback_node_list: The nodes captured by @callback.
        import_node_list: The nodes captured by @import_node.
        capture_dict: The captures of the match.
        import_by_key_dict: {key: ImportInfo}; modified in place.
    """
    special_import: ImportInfo | None = None
    if member_use_node_list and import_node_list:
        special_import = _member_use_import(module, member_use_node_list[0], import_node_list[0])
    elif callback_node_list:
        then_node_list = capture_dict.get("_then_func", [])
        if then_node_list and node_text(then_node_list[0]) == "then":
            special_import = _callback_import(module, callback_node_list[0])
    if special_import is None:
        return
    special_key_tuple = (
        module, special_import.line, special_import.scope_line_tuple, special_import.is_use,
    )
    group_import = import_by_key_dict.setdefault(special_key_tuple, special_import)
    if group_import is not special_import:
        group_import.names.extend(
            name for name in special_import.names if name not in group_import.names
        )


def _add_statement_import(
    module: str,
    module_node: Node,
    import_node_list: list[Node],
    capture_dict: dict[str, list[Node]],
    scope_types: set[str] | None,
    import_by_key_dict: dict[tuple, ImportInfo],
) -> None:
    """Add the module alias and the names one match of an import statement captures.

    The ImportInfo of the statement is keyed by (module, line) and created on the first
    match of the statement.

    Args:
        module: The module string, without quotes.
        module_node: The node captured by @module.
        import_node_list: The nodes captured by @import_node.
        capture_dict: The captures of the match.
        scope_types: AST node types that open a scope (extract_imports).
        import_by_key_dict: {key: ImportInfo}; modified in place.
    """
    # The line of the whole import statement node, else of the module node
    if import_node_list:
        line = import_node_list[0].start_point[0] + 1
    else:
        line = module_node.start_point[0] + 1
    group_key_tuple = (module, line)
    if group_key_tuple not in import_by_key_dict:
        import_node = import_node_list[0] if import_node_list else module_node
        import_by_key_dict[group_key_tuple] = ImportInfo(
            module=module, names=[], line=line,
            scope_line_tuple=_scope_line_tuple(import_node, scope_types or set()),
            is_export=import_node.type == _EXPORT_STATEMENT_TYPE,
            is_system_include=module_node.type == _SYSTEM_INCLUDE_TYPE,
        )
    import_info = import_by_key_dict[group_key_tuple]

    # Detect import X as Y alias, and the name bound to the whole module
    module_alias = _detect_module_alias(module_node, import_node_list)
    for namespace_node in capture_dict.get("namespace_name", []):
        module_alias = node_text(namespace_node)
    if module_alias:
        import_info.module_alias = module_alias

    # A default import binds its name to the default export of the module
    for default_node in capture_dict.get("default_name", []):
        default_name = node_text(default_node)
        if default_name not in import_info.names:
            import_info.names.append(default_name)
            _set_alias(import_info, default_name, DEFAULT_EXPORT_NAME)

    # The @name captures are added to names once each; an aliased name is added by its
    # alias and alias_map maps it to the original name
    member_node_list = capture_dict.get("member_name", [])
    for name_node in capture_dict.get("name", []):
        alias_name = _resolve_import_name(name_node)
        original_name = (
            node_text(member_node_list[0]) if member_node_list
            else _get_original_name(name_node)
        )
        if alias_name and alias_name not in import_info.names:
            import_info.names.append(alias_name)
            if original_name and original_name != alias_name:
                _set_alias(import_info, alias_name, original_name)

    # Wildcard import detection: a child of the statement in _WILDCARD_NODE_TYPE_SET
    # adds "*" to names (import a.b.*, from m import *, export * from "./m")
    if import_node_list and "*" not in import_info.names:
        for child in import_node_list[0].children:
            if child.type in _WILDCARD_NODE_TYPE_SET:
                import_info.names.append("*")
                break


def _detect_module_alias(
    module_node: Node, import_node_list: list[Node],
) -> str | None:
    """Detect the alias name (Y) from import X as Y.

    Python: aliased_import node has an alias field.
    Kotlin: the import node holds "as" followed by the alias identifier.

    Args:
        module_node: The node captured by @module.
        import_node_list: List of nodes captured by @import_node.

    Returns:
        The alias name, or None if no alias exists.
    """
    # Python: if module_node's parent is aliased_import, get the alias
    parent = module_node.parent
    if parent and parent.type == "aliased_import":
        alias = parent.child_by_field_name("alias")
        if alias:
            return node_text(alias)

    # Kotlin: the identifier that follows "as" directly under the import node
    if import_node_list and import_node_list[0].type == "import":
        child_list = import_node_list[0].children
        for index, child in enumerate(child_list[:-1]):
            if child.type == "as" and child_list[index + 1].type == "identifier":
                return node_text(child_list[index + 1])

    return None


def _resolve_import_name(name_node: Node) -> str | None:
    """Get the name actually used in code from a @name capture.

    Returns the alias name if one exists.
    e.g. from X import join as path_join -> returns "path_join"
    e.g. import { useState as useMyState } -> returns "useMyState"

    Args:
        name_node: The node captured by @name.

    Returns:
        The name string as used in code.
    """
    # Python: when @name is an aliased_import node (from X import a as b)
    if name_node.type == "aliased_import":
        alias = name_node.child_by_field_name("alias")
        if alias:
            return node_text(alias)
        name = name_node.child_by_field_name("name")
        if name:
            return node_text(name)
        return node_text(name_node)

    # JS/TS: when @name is an identifier inside import_specifier / export_specifier
    parent = name_node.parent
    if parent and parent.type in ("import_specifier", "export_specifier"):
        alias = parent.child_by_field_name("alias")
        if alias:
            return node_text(alias)

    return node_text(name_node)


def _get_original_name(name_node: Node) -> str | None:
    """Get the original definition name (before aliasing) from a @name capture.

    Returns None if there is no alias.
    e.g. from X import join as path_join -> returns "join"
    e.g. import { useState as useMyState } -> returns "useState"
    e.g. from X import join -> returns None (no alias)

    Args:
        name_node: The node captured by @name.

    Returns:
        The original name string, or None if no alias exists.
    """
    # Python: return the original name only when aliased_import has an alias field
    if name_node.type == "aliased_import":
        alias = name_node.child_by_field_name("alias")
        if alias:
            name = name_node.child_by_field_name("name")
            return node_text(name) if name else None
        return None

    # JS/TS: when import_specifier / export_specifier has an alias field
    parent = name_node.parent
    if parent and parent.type in ("import_specifier", "export_specifier"):
        alias = parent.child_by_field_name("alias")
        if alias:
            return node_text(name_node)

    # JS/TS: const { key: name } = require(...) and const { key: name = 1 } = require(...)
    # bind name to key
    if parent and parent.type == "assignment_pattern":
        parent = parent.parent
    if parent and parent.type == "pair_pattern":
        key = parent.child_by_field_name("key")
        if key:
            return node_text(key)

    return None


def _commonjs_export_iter(root_node: Node) -> Iterator[tuple[str, Node]]:
    """Yield (exported name, value node) of each top-level CommonJS export assignment (JS / TS).

    The assignments (commonjs_export_assignment) are yielded in line order; the name
    of an assignment to module.exports itself is DEFAULT_EXPORT_NAME.
    """
    for statement in root_node.children:
        if statement.type != "expression_statement":
            continue
        export_assignment_tuple = commonjs_export_assignment(statement)
        if export_assignment_tuple is not None:
            yield export_assignment_tuple


def module_export_list(root_node: Node) -> list[str]:
    """Return the modules a JS/TS file passes on as its own value.

    module.exports = require("./lib/express");  -> ["./lib/express"]

    Args:
        root_node: The AST root node of the file.

    Returns:
        The module strings, in line order.
    """
    module_list: list[str] = []
    for export_name, value_node in _commonjs_export_iter(root_node):
        if export_name == DEFAULT_EXPORT_NAME:
            module = require_module(value_node)
            if module is not None:
                module_list.append(module)
    return module_list


def has_module_export(root_node: Node) -> bool:
    """Return whether a JS/TS file gives its value by an assignment to module.exports.

    Args:
        root_node: The AST root node of the file.

    Returns:
        True when a top-level statement assigns to module.exports itself.
    """
    return any(
        export_name == DEFAULT_EXPORT_NAME for export_name, _ in _commonjs_export_iter(root_node)
    )


def member_module_export_dict(root_node: Node) -> dict[str, str]:
    """Return the modules a JS/TS file passes on as members of its own value.

    exports.util = require("./util");                  -> {"util": "./util"}
    module.exports = { Engine: require("./engine") };  -> {"Engine": "./engine"}

    Args:
        root_node: The AST root node of the file.

    Returns:
        {exported name: module string}.
    """
    module_dict: dict[str, str] = {}
    for export_name, value_node in _commonjs_export_iter(root_node):
        if export_name != DEFAULT_EXPORT_NAME:
            module = require_module(value_node)
            if module is not None:
                module_dict[export_name] = module
        elif value_node.type == "object":
            for pair_node in value_node.children:
                key_node = pair_node.child_by_field_name("key")
                member_node = pair_node.child_by_field_name("value")
                module = require_module(member_node) if member_node is not None else None
                if pair_node.type == "pair" and key_node is not None and module is not None:
                    module_dict[node_text(key_node)] = module
    return module_dict


def _string_list(node: Node) -> list[str] | None:
    """Return the strings of a list, tuple or single string written as constants (Python).

    Args:
        node: A list, tuple, parenthesized expression or string node.

    Returns:
        The strings in the order they are written; None when the node is none of
        those or holds anything but plain string constants.
    """
    if node.type == _PYTHON_STRING_TYPE:
        content_list = [child for child in node.named_children if child.type != "string_content"]
        if any(child.type not in ("string_start", "string_end") for child in content_list):
            return None
        return ["".join(
            node_text(child) for child in node.named_children
            if child.type == "string_content"
        )]
    if node.type not in _PYTHON_SEQUENCE_TYPE_SET:
        return None
    name_list: list[str] = []
    for child in node.named_children:
        if child.type == "comment":
            continue
        child_name_list = _string_list(child) if child.type == _PYTHON_STRING_TYPE else None
        if child_name_list is None:
            return None
        name_list.extend(child_name_list)
    return name_list


def python_all_name_list(root_node: Node) -> list[str] | None:
    """Return the names the __all__ of a Python file lists.

    __all__ = ["a", "b"]         __all__ += ["c"]
    __all__.extend(["d"])        __all__.append("e")
    Only statements at the top level of the file are read.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The names in the order they are written, without duplicates. None when the
        file has no __all__, or writes it with anything but string constants.
    """
    name_list: list[str] | None = None
    for statement in root_node.children:
        if statement.type != "expression_statement" or not statement.named_children:
            continue
        node = statement.named_children[0]
        value_node: Node | None = None
        is_all = False
        if node.type in ("assignment", "augmented_assignment"):
            left_node = node.child_by_field_name("left")
            is_all = left_node is not None and left_node.text == _PYTHON_ALL_NAME
            value_node = node.child_by_field_name("right")
            is_reset = node.type == "assignment"
        elif node.type == "call":
            function_node = node.child_by_field_name("function")
            object_node = function_node.child_by_field_name("object") if function_node else None
            method_node = function_node.child_by_field_name("attribute") if function_node else None
            is_all = (
                function_node is not None and function_node.type == "attribute"
                and object_node is not None and object_node.text == _PYTHON_ALL_NAME
                and method_node is not None and method_node.text in _PYTHON_ALL_METHOD_SET
            )
            argument_node = node.child_by_field_name("arguments")
            argument_list = argument_node.named_children if argument_node else []
            value_node = argument_list[0] if len(argument_list) == 1 else None
            is_reset = False
        if not is_all:
            continue
        value_name_list = _string_list(value_node) if value_node is not None else None
        if value_name_list is None:
            return None
        name_list = value_name_list if is_reset or name_list is None else name_list + value_name_list
    return list(dict.fromkeys(name_list)) if name_list is not None else None


def local_export_dict(root_node: Node) -> dict[str, str]:
    """Return the names a JS/TS file exports under another name than it defines them with.

    export { p as q, r };          -> {"q": "p"}
    export default p;              -> {"default": "p"}
    export default (p);            -> {"default": "p"}
    export default function f() {} -> {"default": "f"}
    module.exports = p;            -> {"default": "p"}
    module.exports = { q: p };     -> {"q": "p"}
    An export statement with a source (export { a } from "./m") is an import
    (extract_imports) and is not read here.

    Args:
        root_node: The AST root node of the file.

    Returns:
        {exported name: name in the file}. Empty for a file of another language.
    """
    export_dict: dict[str, str] = {}
    for statement in root_node.children:
        if statement.type == "expression_statement":
            export_assignment_tuple = commonjs_export_assignment(statement)
            if export_assignment_tuple is None:
                continue
            export_name, value_node = export_assignment_tuple
            if value_node.type == "identifier":
                export_dict[export_name] = node_text(value_node)
            elif export_name == DEFAULT_EXPORT_NAME and value_node.type == "object":
                # An entry written key: name exports the name under the key
                for pair_node in value_node.children:
                    key_node = pair_node.child_by_field_name("key")
                    name_node = pair_node.child_by_field_name("value")
                    if (
                        pair_node.type == "pair" and key_node is not None
                        and name_node is not None and name_node.type == "identifier"
                    ):
                        export_dict[node_text(key_node)] = node_text(name_node)
            continue
        if statement.type != "export_statement" or statement.child_by_field_name("source"):
            continue
        for child in statement.children:
            if child.type != "export_clause":
                continue
            for specifier in child.children:
                name_node = specifier.child_by_field_name("name")
                alias_node = specifier.child_by_field_name("alias")
                if name_node is not None and alias_node is not None:
                    export_dict[node_text(alias_node)] = node_text(name_node)
        if any(child.type == "default" for child in statement.children):
            value_node = export_value_node(statement)
            if value_node is None or value_node.type != "identifier":
                written_node = (
                    statement.child_by_field_name("value")
                    or statement.child_by_field_name("declaration")
                )
                value_node = (
                    written_node.child_by_field_name("name") if written_node is not None else None
                )
            if value_node is not None:
                export_dict[DEFAULT_EXPORT_NAME] = node_text(value_node)
    return export_dict


def _strip_quote(text: str) -> str:
    """Remove quotes or angle brackets surrounding a module name.

    Import path notation varies by language:
        JavaScript/TypeScript: "react" / 'react' / `react`
        C/C++:                 <stdio.h> / "helper.h"

    Languages without quotes (Python, Java, etc.) are returned as-is.

    Args:
        text: The raw module string captured by the query.

    Returns:
        The string with quotes/angle brackets removed.
    """
    if len(text) >= 2:
        if text[0] == text[-1] and text[0] in "\"'`":
            return text[1:-1]
        if text[0] == '<' and text[-1] == '>':
            return text[1:-1]
    return text
