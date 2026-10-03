from dataclasses import dataclass
from tree_sitter import Language, Query, QueryCursor, Node
from codetwine.extractors.cobol_source import CALL_KIND, COPY_KIND, CobolSource
from codetwine.extractors.definitions import (
    DEFAULT_EXPORT_NAME,
    commonjs_export_assignment,
    require_module,
)
from codetwine.extractors.rust_path import UseCacheDict, rust_import_list

# Compiled import queries: (id of the Language, query string) -> Query
_query_cache: dict[tuple[int, str], Query] = {}

# Child node types of an import statement that stand for "every name" (Java: asterisk,
# Kotlin / JS / TS: *, Python: wildcard_import)
_WILDCARD_NODE_TYPE_SET = {"asterisk", "*", "wildcard_import"}

# Node type of an export statement (JS / TS)
_EXPORT_STATEMENT_TYPE = "export_statement"


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


def cobol_module(kind: str, name: str, library: str = "") -> str:
    """Return the module string of a COBOL COPY or CALL statement.

    Inverse of cobol_module_part.

    Examples:
        ("COPY", "CUSTREC")             -> "COPY CUSTREC"
        ("COPY", "CUSTREC", "COPYLIB")  -> "COPY CUSTREC OF COPYLIB"
        ("CALL", "TAXCALC")             -> "CALL TAXCALC"
    """
    return f"{kind} {name} OF {library}" if library else f"{kind} {name}"


def cobol_module_part(module: str) -> tuple[str, str, str]:
    """Split the module string of a COBOL statement into (kind, name, library).

    Inverse of cobol_module.

    Examples:
        "COPY CUSTREC OF COPYLIB"  -> ("COPY", "CUSTREC", "COPYLIB")
        "CALL TAXCALC"             -> ("CALL", "TAXCALC", "")
        "CALL LIST OF ITEMS"       -> ("CALL", "LIST OF ITEMS", "")
    """
    kind, _, operand = module.partition(" ")
    if kind != COPY_KIND:
        return kind, operand, ""
    name, _, library = operand.partition(" OF ")
    return kind, name, library


def _cobol_import_list(cobol_source: CobolSource) -> list[ImportInfo]:
    """Return the COPY and CALL statements of a COBOL file as ImportInfo.

    COPY name        -> module "COPY name", names ["*"]
    CALL name        -> module "CALL name", names [name]
    The same statement on the same line is returned once.

    Args:
        cobol_source: The CobolSource of the file.

    Returns:
        A list of ImportInfo in line order.
    """
    import_by_key_dict: dict[tuple[str, int], ImportInfo] = {}
    for cobol_import in cobol_source.import_list:
        module = cobol_module(cobol_import.kind, cobol_import.name, cobol_import.library)
        is_call = cobol_import.kind == CALL_KIND
        import_by_key_dict.setdefault((module, cobol_import.line), ImportInfo(
            module=module,
            names=[cobol_import.name] if is_call else ["*"],
            line=cobol_import.line,
        ))
    return list(import_by_key_dict.values())


def _line_tuple(node: Node) -> tuple[int, int]:
    """Return the first and last line of a node (1-based)."""
    return node.start_point[0] + 1, node.end_point[0] + 1


def _scope_line_tuple(import_node: Node, scope_types: set[str]) -> tuple[int, int] | None:
    """Return the lines an import statement binds its names for, None for the whole file.

    Args:
        import_node: The node of the import statement.
        scope_types: AST node types that open a scope (functions, lambdas).

    Returns:
        The lines of the statement itself for an export statement with a source, else
        the lines of the innermost scope_types node around the statement, else None.
    """
    if import_node.type == _EXPORT_STATEMENT_TYPE:
        return _line_tuple(import_node)
    scope_node = import_node.parent
    while scope_node is not None and scope_node.type not in scope_types:
        scope_node = scope_node.parent
    return _line_tuple(scope_node) if scope_node is not None else None


def extract_imports(
    root_node: Node | CobolSource,
    language: Language,
    import_query_str: str | None,
    scope_types: set[str] | None = None,
) -> list[ImportInfo]:
    """Extract import statements from the AST and return them.

    Uses tree-sitter queries (S-expression pattern matching) to uniformly
    handle import syntax that differs across languages.

    Query capture names:
        @module      -> Import source (module name, path, header name, etc.)
        @name        -> Individually imported name (the Y in "from X import Y")
        @default_name   -> Name bound to the default export of the module (JS/TS)
        @namespace_name -> Name bound to the module as a whole (JS/TS)
        @member_name -> Member of the module the @name of the statement is bound to
                        (JS: const X = require("module").Y)
        @import_node -> The entire import statement node (for line number retrieval)
        @path_item   -> A Rust node naming a module path; expanded by rust_import_list
                        into one import per name

    When the same import statement has multiple @name captures (from X import Y, Z),
    they are consolidated into a single ImportInfo.

    For a CobolSource its COPY and CALL statements are returned, without a query.

    Args:
        root_node: The AST root node covering the entire file, or the CobolSource of a COBOL file.
        language: tree-sitter Language object (required for Query creation).
        import_query_str: tree-sitter query string (obtained from EXT_TO_IMPORT_QUERY_DICT in config.py).
                          Returns an empty list when None (for languages with no import query defined).
        scope_types: AST node types that open a scope (functions, lambdas). An import
                     statement written inside one binds its names for the lines of the
                     innermost one (ImportInfo.scope_line_tuple). None reads every
                     statement as binding for the whole file.

    Returns:
        A list of ImportInfo. A JS/TS export statement with a source has is_export set
        and binds its names on its own lines.
    """
    if isinstance(root_node, CobolSource):
        return _cobol_import_list(root_node)
    if not import_query_str:
        return []

    # Scan the AST with a cursor of the query, compiled once per language and query string
    query_key = (id(language), import_query_str)
    query = _query_cache.get(query_key)
    if query is None:
        query = Query(language, import_query_str)
        _query_cache[query_key] = query
    cursor = QueryCursor(query)

    # Key: (module string, line number), for Rust with the lines the names are bound
    # for -> ImportInfo
    # Groups multiple @name captures from the same import statement into one entry
    import_by_key_dict: dict[tuple, ImportInfo] = {}
    # Rust: the use declarations of the blocks and inline modules of the file
    use_cache_dict: UseCacheDict = {}

    # Retrieve query match results
    for _, captures in cursor.matches(root_node):
        # CommonJS require() pattern filtering:
        # If a @_require_func capture exists and the function name is not "require", skip it
        require_func_node_list = captures.get("_require_func", [])
        if require_func_node_list:
            if require_func_node_list[0].text.decode("utf-8") != "require":
                continue

        # Rust: one node can bring several paths into use (use a::{b, c as d})
        path_item_node_list = captures.get("path_item", [])
        if path_item_node_list:
            path_item_node = path_item_node_list[0]
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
                        if import_info.alias_map is None:
                            import_info.alias_map = {}
                        import_info.alias_map[name] = original
            continue

        # Retrieve @module, @name, and @import_node captures
        module_node_list = captures.get("module", [])
        name_node_list = captures.get("name", [])
        import_node_list = captures.get("import_node", [])

        if not module_node_list:
            continue

        # Get the module name from the @module capture and strip quotes
        raw_module = module_node_list[0].text.decode("utf-8")
        module = _strip_quotes(raw_module)

        # Get line number from the entire import statement node (fallback to module node)
        if import_node_list:
            line = import_node_list[0].start_point[0] + 1
        else:
            line = module_node_list[0].start_point[0] + 1

        # Create the grouping key
        group_key = (module, line)

        # Create a new entry if the group does not exist yet
        if group_key not in import_by_key_dict:
            import_node = import_node_list[0] if import_node_list else module_node_list[0]
            import_by_key_dict[group_key] = ImportInfo(
                module=module, names=[], line=line,
                scope_line_tuple=_scope_line_tuple(import_node, scope_types or set()),
                is_export=import_node.type == _EXPORT_STATEMENT_TYPE,
            )

        # Detect import X as Y alias, and the name bound to the whole module
        module_alias = _detect_module_alias(module_node_list[0], import_node_list)
        for namespace_node in captures.get("namespace_name", []):
            module_alias = namespace_node.text.decode("utf-8")
        if module_alias:
            import_by_key_dict[group_key].module_alias = module_alias

        # A default import binds its name to the default export of the module
        for default_node in captures.get("default_name", []):
            default_name = default_node.text.decode("utf-8")
            if default_name not in import_by_key_dict[group_key].names:
                import_by_key_dict[group_key].names.append(default_name)
                if import_by_key_dict[group_key].alias_map is None:
                    import_by_key_dict[group_key].alias_map = {}
                import_by_key_dict[group_key].alias_map[default_name] = DEFAULT_EXPORT_NAME

        # If @name captures exist, add them to the names list (excluding duplicates)
        # When an alias is present, register the alias name and record the mapping to the original name in alias_map
        member_node_list = captures.get("member_name", [])
        for name_node in name_node_list:
            alias_name = _resolve_imported_name(name_node)
            original_name = (
                member_node_list[0].text.decode("utf-8") if member_node_list
                else _get_original_name(name_node)
            )
            if alias_name and alias_name not in import_by_key_dict[group_key].names:
                import_by_key_dict[group_key].names.append(alias_name)
                if original_name and original_name != alias_name:
                    if import_by_key_dict[group_key].alias_map is None:
                        import_by_key_dict[group_key].alias_map = {}
                    import_by_key_dict[group_key].alias_map[alias_name] = original_name

        # Wildcard import detection: a child of the statement in _WILDCARD_NODE_TYPE_SET
        # adds "*" to names (import a.b.*, from m import *, export * from "./m")
        if import_node_list and "*" not in import_by_key_dict[group_key].names:
            for child in import_node_list[0].children:
                if child.type in _WILDCARD_NODE_TYPE_SET:
                    import_by_key_dict[group_key].names.append("*")
                    break

    return list(import_by_key_dict.values())


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
            return alias.text.decode("utf-8")

    # Kotlin: the identifier that follows "as" directly under the import node
    if import_node_list and import_node_list[0].type == "import":
        child_list = import_node_list[0].children
        for index, child in enumerate(child_list[:-1]):
            if child.type == "as" and child_list[index + 1].type == "identifier":
                return child_list[index + 1].text.decode("utf-8")

    return None


def _resolve_imported_name(name_node: Node) -> str | None:
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
            return alias.text.decode("utf-8")
        name = name_node.child_by_field_name("name")
        if name:
            return name.text.decode("utf-8")
        return name_node.text.decode("utf-8")

    # JS/TS: when @name is an identifier inside import_specifier / export_specifier
    parent = name_node.parent
    if parent and parent.type in ("import_specifier", "export_specifier"):
        alias = parent.child_by_field_name("alias")
        if alias:
            return alias.text.decode("utf-8")

    return name_node.text.decode("utf-8")


def _get_original_name(name_node: Node) -> str | None:
    """Get the original definition name (before aliasing) from a @name capture.

    Returns None if there is no alias (would be the same result as _resolve_imported_name).
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
            return name.text.decode("utf-8") if name else None
        return None

    # JS/TS: when import_specifier / export_specifier has an alias field
    parent = name_node.parent
    if parent and parent.type in ("import_specifier", "export_specifier"):
        alias = parent.child_by_field_name("alias")
        if alias:
            return name_node.text.decode("utf-8")

    # JS/TS: const { key: name } = require(...) binds name to key
    if parent and parent.type == "pair_pattern":
        key = parent.child_by_field_name("key")
        if key:
            return key.text.decode("utf-8")

    return None


def module_export_list(root_node: Node) -> list[str]:
    """Return the modules a JS/TS file passes on as its own value.

    module.exports = require("./lib/express");  -> ["./lib/express"]

    Args:
        root_node: The AST root node of the file.

    Returns:
        The module strings, in line order.
    """
    module_list: list[str] = []
    for statement in root_node.children:
        if statement.type != "expression_statement":
            continue
        export_assignment = commonjs_export_assignment(statement)
        if export_assignment is not None and export_assignment[0] == DEFAULT_EXPORT_NAME:
            module = require_module(export_assignment[1])
            if module is not None:
                module_list.append(module)
    return module_list


def local_export_dict(root_node: Node) -> dict[str, str]:
    """Return the names a JS/TS file exports under another name than it defines them with.

    export { p as q, r };          -> {"q": "p"}
    export default p;              -> {"default": "p"}
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
            export_assignment = commonjs_export_assignment(statement)
            if export_assignment is None:
                continue
            export_name, value_node = export_assignment
            if value_node.type == "identifier":
                export_dict[export_name] = value_node.text.decode("utf-8")
            elif export_name == DEFAULT_EXPORT_NAME and value_node.type == "object":
                # An entry written key: name exports the name under the key
                for pair_node in value_node.children:
                    key_node = pair_node.child_by_field_name("key")
                    name_node = pair_node.child_by_field_name("value")
                    if (
                        pair_node.type == "pair" and key_node is not None
                        and name_node is not None and name_node.type == "identifier"
                    ):
                        export_dict[key_node.text.decode("utf-8")] = name_node.text.decode("utf-8")
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
                    export_dict[alias_node.text.decode("utf-8")] = name_node.text.decode("utf-8")
        if any(child.type == "default" for child in statement.children):
            value_node = (
                statement.child_by_field_name("value")
                or statement.child_by_field_name("declaration")
            )
            if value_node is not None and value_node.type != "identifier":
                value_node = value_node.child_by_field_name("name")
            if value_node is not None:
                export_dict[DEFAULT_EXPORT_NAME] = value_node.text.decode("utf-8")
    return export_dict


def _strip_quotes(text: str) -> str:
    """Remove quotes or angle brackets surrounding a module name.

    Import path notation varies by language:
        JavaScript/TypeScript: "react" / 'react'
        C/C++:                 <stdio.h> / "helper.h"

    Languages without quotes (Python, Java, etc.) are returned as-is.

    Args:
        text: The raw module string captured by the query.

    Returns:
        The string with quotes/angle brackets removed.
    """
    if len(text) >= 2:
        if (text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'"):
            return text[1:-1]
        if text[0] == '<' and text[-1] == '>':
            return text[1:-1]
    return text
