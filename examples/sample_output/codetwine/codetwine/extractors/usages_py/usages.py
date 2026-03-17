from dataclasses import dataclass
from tree_sitter import Node



@dataclass
class UsageInfo:
    """Data class holding information about a single symbol usage location."""

    name: str    # The symbol name being used
    line: int    # Line number of the usage location (1-based)


def extract_usages(
    root_node: Node,
    imported_names: set[str],
    usage_node_types: dict | None = None,
) -> list[UsageInfo]:
    """Extract usage locations of imported names from the AST and return them.

    Traverses the AST via depth-first search (DFS) and detects the following kinds:
    - Nodes in call_types (function calls)
    - Nodes in attribute_types (attribute access)
    - identifier nodes (simple variable references)
    - type_identifier / namespace_identifier nodes (type / namespace references)
      Java: "User" in User user = new User() is a type_identifier
      C/C++: "Point" in struct Point p is a type_identifier
      C++: "geometry" in geometry::Rectangle is a namespace_identifier

    Duplicate and redundant entries are removed at the end by _deduplicate.

    Args:
        root_node: The AST root node covering the entire file.
        imported_names: Set of names whose usage is to be tracked.
        usage_node_types: Per-language node type settings dict, obtained from USAGE_NODE_TYPES in config.py.
                          Returns an empty list when None (for languages with no usage tracking defined).
                          Required keys: "call_types", "attribute_types", "skip_parent_types"
                          Optional key: "skip_parent_types_for_type_ref" (uses skip_parent_types if absent)
                          Optional key: "skip_name_field_types" (when the parent is this type,
                            only the name-field child is skipped; the value side is detected as a usage)

    Returns:
        A list of UsageInfo (deduplicated).
    """
    if not usage_node_types:
        return []

    # Retrieve per-language node types from the settings
    call_types: set[str] = usage_node_types["call_types"]
    attribute_types: set[str] = usage_node_types["attribute_types"]
    skip_parent_types: set[str] = usage_node_types["skip_parent_types"]
    skip_name_field_types: set[str] = usage_node_types.get(
        "skip_name_field_types", set()
    )
    skip_parent_types_for_type_ref: set[str] = usage_node_types.get(
        "skip_parent_types_for_type_ref", skip_parent_types
    )

    # Type reference / namespace reference node types (skip_parent_types check not needed)
    _TYPE_REFERENCE_NODE_TYPES = {"type_identifier", "namespace_identifier"}

    usage_list: list[UsageInfo] = []
    # DFS traversal of the AST using a stack
    node_stack = [root_node]

    while node_stack:
        node = node_stack.pop()

        # Process function call nodes
        if node.type in call_types:
            usage = _parse_call_node(node, imported_names, attribute_types)
            if usage:
                usage_list.append(usage)

        elif node.type in attribute_types:
            # Process only standalone attribute access (exclude function part of a call)
            if not _is_function_part_of_call(node, call_types):
                usage = _parse_attribute_node(node, imported_names)
                if usage:
                    usage_list.append(usage)

        elif node.type == "qualified_identifier":
            # C++ identifier containing the scope resolution operator.
            # Record as a Usage if the scope part (left of ::) matches an imported name.
            # Internal namespace_identifier / identifier are individually skipped by skip_parent_types.
            # By extracting only the scope part here, duplication is prevented while still detecting usages.
            # Skip qualified_identifier inside import / package declarations.
            parent = node.parent
            if parent and parent.type in skip_parent_types:
                node_stack.extend(node.children)
                continue
            for child in node.children:
                if child.type in ("namespace_identifier", "identifier", "type_identifier"):
                    name = child.text.decode("utf-8")
                    if name in imported_names:
                        usage_list.append(UsageInfo(name=name, line=child.start_point[0] + 1))
                    break

        elif node.type in _TYPE_REFERENCE_NODE_TYPES:
            # Process type reference nodes.
            # Use the type-reference skip list for checking (only import statements and scope resolution are skipped).
            # Unlike the identifier skip_parent_types, type references in parameters and method declarations
            # are detected as dependencies.
            parent = node.parent
            if parent and parent.type in skip_parent_types_for_type_ref:
                node_stack.extend(node.children)
                continue
            name = node.text.decode("utf-8")
            if name in imported_names:
                usage_list.append(UsageInfo(name=name, line=node.start_point[0] + 1))

        elif node.type == "identifier":
            # Process simple identifier nodes
            usage = _parse_identifier_node(
                node, imported_names, skip_parent_types, skip_name_field_types
            )
            if usage:
                usage_list.append(usage)

        # Add child nodes to the stack to continue traversal
        node_stack.extend(node.children)

    # Remove duplicate and redundant entries and return
    return _deduplicate(usage_list)


def _deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]:
    """Remove redundant entries within the same line and eliminate duplicates.

    When both "module" and "module.attr" exist on the same line,
    the more detailed "module.attr" is kept and "module" is removed.
    Entries with duplicate (name, line) pairs are also removed.

    Args:
        usage_list: A UsageInfo list that may contain duplicates.

    Returns:
        A deduplicated UsageInfo list (sorted by line number in ascending order).
    """
    # Group by line number
    by_line: dict[int, list[UsageInfo]] = {}
    for usage in usage_list:
        by_line.setdefault(usage.line, []).append(usage)

    seen_keys: set[tuple] = set()
    result: list[UsageInfo] = []

    # Process each group in ascending line-number order
    for line in sorted(by_line):
        line_usages = by_line[line]
        line_names = [u.name for u in line_usages]

        for usage in line_usages:
            # Exclude the shorter name if a more detailed name (usage.name.xxx) exists on the same line
            if any(other.startswith(usage.name + ".") for other in line_names):
                continue

            # Also remove entries with duplicate (name, line) pairs
            entry_key = (usage.name, usage.line)
            if entry_key not in seen_keys:
                seen_keys.add(entry_key)
                result.append(usage)

    return result


def _is_function_part_of_call(node: Node, call_types: set[str]) -> bool:
    """Determine whether this attribute node is the function part of a call node.

    In the AST for "func()", the first child of the call node is an identifier or attribute.
    In that case, the call node handles the processing, so the attribute node alone is not processed.

    Args:
        node: The attribute node to check.
        call_types: Set of node types representing function calls.

    Returns:
        True if it is the function part of a call, False if it is a standalone attribute access.
    """
    # Check if the parent node is a call type
    parent = node.parent
    if parent and parent.type in call_types:
        # Check if the first child of the call node is this node
        for child in parent.children:
            if child.type in ("identifier", node.type):
                return child.id == node.id
    return False


def _parse_call_node(
    node: Node,
    imported_names: set[str],
    attribute_types: set[str],
) -> UsageInfo | None:
    """Extract symbol usage information from a function call node.

    Only checks the first child (function name part) of the call node.
    Returns a UsageInfo only if the leading name is in the imported names set.

    Args:
        node: A call node.
        imported_names: Set of names to track.
        attribute_types: Set of node types representing attribute access.

    Returns:
        UsageInfo, or None if not applicable.
    """
    line = node.start_point[0] + 1

    # Check only the first child (function name part) of the call node
    for child in node.children:
        if child.type == "identifier":
            # Simple function call: func()
            name = child.text.decode("utf-8")
            if name in imported_names:
                return UsageInfo(name=name, line=line)

        elif child.type in attribute_types:
            # Call via attribute access: module.func()
            name = child.text.decode("utf-8")
            # Check if the leading "module" is imported
            if name.split(".")[0] in imported_names:
                return UsageInfo(name=name, line=line)

        elif child.type == "qualified_identifier":
            # C++ scope resolution operator call: geometry::doSomething()
            for sub in child.children:
                if sub.type in ("namespace_identifier", "identifier", "type_identifier"):
                    name = sub.text.decode("utf-8")
                    if name in imported_names:
                        return UsageInfo(name=name, line=line)
                    break

        break  # Check only the first child

    return None


def _parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None:
    """Extract symbol usage information from an attribute access node.

    Returns a UsageInfo when the leading name of "module.attr" style attribute access is imported.

    Args:
        node: An attribute node.
        imported_names: Set of names to track.

    Returns:
        UsageInfo, or None if not applicable.
    """
    # Get the full text of the attribute access
    name = node.text.decode("utf-8")
    # Check if the leading name (the "module" part) is imported
    if name.split(".")[0] in imported_names:
        return UsageInfo(name=name, line=node.start_point[0] + 1)
    return None


def _parse_identifier_node(
    node: Node,
    imported_names: set[str],
    skip_parent_types: set[str],
    skip_name_field_types: set[str],
) -> UsageInfo | None:
    """Extract symbol usage information from a simple identifier node.

    Ignored when the identifier is part of import statements, definitions,
    or argument declarations (i.e., part of syntax).
    For node types in skip_name_field_types, only the "name"-field child is
    skipped while the "value" side is detected as a usage.

    Example: in def func(x=some_var), for default_parameter:
        identifier "x" (name field) -> skipped
        identifier "some_var" (value field) -> detected as a usage

    Args:
        node: An identifier node.
        imported_names: Set of names to track.
        skip_parent_types: Set of node types whose children should be skipped.
        skip_name_field_types: Set of node types where only the name-field child is skipped.

    Returns:
        UsageInfo, or None if not applicable.
    """
    parent = node.parent
    if parent:
        if parent.type in skip_name_field_types:
            # Skip only the "name" field child; treat the "value" side as a usage
            name_child = parent.child_by_field_name("name")
            if name_child and name_child.id == node.id:
                return None
        elif parent.type in skip_parent_types:
            return None

    # Check if the name matches an imported name
    name = node.text.decode("utf-8")
    if name in imported_names:
        return UsageInfo(name=name, line=node.start_point[0] + 1)

    return None


def extract_typed_aliases(
    root_node: Node,
    imported_names: set[str],
    typed_alias_parent_types: set[str],
) -> dict[str, str]:
    """Traverse the AST to find typed variable declarations and return a variable-name -> type-name mapping.

    Detects variables declared with an imported type (e.g. Genre) such as genre,
    and returns them in the format {"genre": "Genre"}.

    Supported AST patterns:
      Java:   field_declaration / local_variable_declaration / formal_parameter
      Kotlin: property_declaration / parameter
      C/C++:  declaration / parameter_declaration

    Args:
        root_node: The AST root node covering the entire file.
        imported_names: Set of imported type names to track.
        typed_alias_parent_types: Set of AST node types representing typed variable declarations.

    Returns:
        A variable-name -> type-name mapping dict.
        Only declarations whose type name is in imported_names are included.
    """
    if not typed_alias_parent_types:
        return {}

    aliases: dict[str, str] = {}
    stack = [root_node]

    while stack:
        node = stack.pop()

        if node.type in typed_alias_parent_types:
            type_name, var_names = _extract_type_and_var(node)
            if type_name and type_name in imported_names:
                for var_name in var_names:
                    if var_name != type_name:
                        aliases[var_name] = type_name

        stack.extend(node.children)

    return aliases


def _extract_type_and_var(node: Node) -> tuple[str | None, list[str]]:
    """Extract the type name and variable names from a typed variable declaration node.

    Absorbs AST structure differences across languages:
      Java:   type_identifier + variable_declarator > identifier / identifier
      Kotlin: user_type > type_identifier + simple_identifier
      C/C++:  type_identifier + init_declarator > identifier / identifier

    Args:
        node: An AST node representing a typed variable declaration.

    Returns:
        A (type_name, [list of variable names]) tuple. Returns (None, []) if not found.
    """
    type_name: str | None = None
    var_names: list[str] = []

    for child in node.children:
        if child.type == "type_identifier":
            type_name = child.text.decode("utf-8")
        elif child.type == "user_type":
            # Kotlin: user_type > type_identifier
            for sub in child.children:
                if sub.type == "type_identifier":
                    type_name = sub.text.decode("utf-8")
                    break
        elif child.type in ("identifier", "simple_identifier"):
            var_names.append(child.text.decode("utf-8"))
        elif child.type in ("variable_declarator", "init_declarator"):
            # Java: variable_declarator > identifier
            # C/C++: init_declarator > identifier
            for sub in child.children:
                if sub.type == "identifier":
                    var_names.append(sub.text.decode("utf-8"))
                    break

    return type_name, var_names
