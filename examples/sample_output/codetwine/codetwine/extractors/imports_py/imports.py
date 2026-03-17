from dataclasses import dataclass
from tree_sitter import Language, Query, QueryCursor, Node


@dataclass
class ImportInfo:
    """Data class holding information about a single import statement."""

    module: str         # Import source module name/path
    names: list[str]    # List of names specified in from ... import (empty list for languages without this)
    line: int           # Line number of the import statement (1-based)
    module_alias: str | None = None  # "Y" in import X as Y (alias name)
    alias_map: dict[str, str] | None = None  # {alias name -> original name} (for from X import a as b: {"b": "a"})


def extract_imports(
    root_node: Node,
    language: Language,
    import_query_str: str | None,
) -> list[ImportInfo]:
    """Extract import statements from the AST and return them.

    Uses tree-sitter queries (S-expression pattern matching) to uniformly
    handle import syntax that differs across languages.

    Query capture names:
        @module      -> Import source (module name, path, header name, etc.)
        @name        -> Individually imported name (the Y in "from X import Y")
        @import_node -> The entire import statement node (for line number retrieval)

    When the same import statement has multiple @name captures (from X import Y, Z),
    they are consolidated into a single ImportInfo.

    Args:
        root_node: The AST root node covering the entire file.
        language: tree-sitter Language object (required for Query creation).
        import_query_str: tree-sitter query string (obtained from IMPORT_QUERIES in config.py).
                          Returns an empty list when None (for languages with no import query defined).

    Returns:
        A list of ImportInfo.
    """
    if not import_query_str:
        return []

    # Create a tree-sitter query and scan the AST with a cursor
    query = Query(language, import_query_str)
    cursor = QueryCursor(query)

    # Key: (module string, line number) -> ImportInfo
    # Groups multiple @name captures from the same import statement into one entry
    grouped: dict[tuple[str, int], ImportInfo] = {}

    # Retrieve query match results
    for _, captures in cursor.matches(root_node):
        # CommonJS require() pattern filtering:
        # If a @_require_func capture exists and the function name is not "require", skip it
        require_func_nodes = captures.get("_require_func", [])
        if require_func_nodes:
            if require_func_nodes[0].text.decode("utf-8") != "require":
                continue

        # Retrieve @module, @name, and @import_node captures
        module_nodes = captures.get("module", [])
        name_nodes = captures.get("name", [])
        import_nodes = captures.get("import_node", [])

        if not module_nodes:
            continue

        # Get the module name from the @module capture and strip quotes
        raw_module = module_nodes[0].text.decode("utf-8")
        module = _strip_quotes(raw_module)

        # Get line number from the entire import statement node (fallback to module node)
        if import_nodes:
            line = import_nodes[0].start_point[0] + 1
        else:
            line = module_nodes[0].start_point[0] + 1

        # Create the grouping key
        group_key = (module, line)

        # Create a new entry if the group does not exist yet
        if group_key not in grouped:
            grouped[group_key] = ImportInfo(module=module, names=[], line=line)

        # Detect import X as Y alias
        module_alias = _detect_module_alias(module_nodes[0], import_nodes)
        if module_alias:
            grouped[group_key].module_alias = module_alias

        # If @name captures exist, add them to the names list (excluding duplicates)
        # When an alias is present, register the alias name and record the mapping to the original name in alias_map
        for name_node in name_nodes:
            alias_name = _resolve_imported_name(name_node)
            original_name = _get_original_name(name_node)
            if alias_name and alias_name not in grouped[group_key].names:
                grouped[group_key].names.append(alias_name)
                if original_name and original_name != alias_name:
                    if grouped[group_key].alias_map is None:
                        grouped[group_key].alias_map = {}
                    grouped[group_key].alias_map[alias_name] = original_name

        # Java/Kotlin wildcard import detection:
        # If an import_node's child contains asterisk (Java) or * (Kotlin), add "*" to names
        if import_nodes and "*" not in grouped[group_key].names:
            for child in import_nodes[0].children:
                if child.type in ("asterisk", "*"):
                    grouped[group_key].names.append("*")
                    break

    return list(grouped.values())


def _detect_module_alias(
    module_node: Node, import_nodes: list[Node],
) -> str | None:
    """Detect the alias name (Y) from import X as Y.

    Python: aliased_import node has an alias field.
    Kotlin: import node has an import_alias child node directly beneath it.

    Args:
        module_node: The node captured by @module.
        import_nodes: List of nodes captured by @import_node.

    Returns:
        The alias name, or None if no alias exists.
    """
    # Python: if module_node's parent is aliased_import, get the alias
    parent = module_node.parent
    if parent and parent.type == "aliased_import":
        alias = parent.child_by_field_name("alias")
        if alias:
            return alias.text.decode("utf-8")

    # Kotlin: get the alias from import_alias directly under the import node
    if import_nodes:
        alias_child = import_nodes[0].child_by_field_name("alias")
        if alias_child:
            for child in alias_child.children:
                if child.type in ("simple_identifier", "identifier"):
                    return child.text.decode("utf-8")

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

    return None


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
