import re
from collections import deque
from dataclasses import dataclass
from tree_sitter import Node

# Regex pattern for filtering out #include guard #define directives
_INCLUDE_GUARD_RE = re.compile(r"^_*[A-Z][A-Z0-9_]*_H(?:PP|XX)?_*(?:INCLUDED)?_*$")


@dataclass
class DefinitionInfo:
    """Data class holding information about a single definition."""

    name: str                                     # Definition name (function / class / variable / type, etc.)
    type: str                                     # AST node type ("function_definition" / "expression_statement", etc.)
    start_line: int                               # Start line number of the definition (1-based)
    end_line: int                                 # End line number of the definition


def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]:
    """Extract definitions (functions, classes, variables, types, etc.) from the AST and return them in line-number order.

    definition_dict structure:
        key = AST node type (e.g. "function_definition")
        value = one of the following:
          - Child node type name (standard pattern): obtains the name from a direct child.
            e.g. "identifier" -> searches node.children for a child with type=="identifier"
          - "__sentinel__" format sentinel value (special pattern): the name node is
            nested two or more levels deep, and a dedicated extraction function is used.
            e.g. "__assignment__" -> expression_statement > assignment > identifier

    If name extraction fails (e.g. a C/C++ declaration is a forward declaration),
    child nodes are added to the queue and BFS continues. This allows detection of
    definitions like function_declarator nested inside.

    Args:
        root_node: The AST root node covering the entire file.
        definition_dict: Per-language definition node settings.

    Returns:
        A list of DefinitionInfo sorted by line number in ascending order.
    """

    definition_list: list[DefinitionInfo] = []

    # AST node types for which child node traversal continues even after being recorded as a definition.
    # e.g. namespace_definition contains class and function definitions inside.
    _CONTAINER_DEFINITION_TYPES = {"namespace_definition"}

    # BFS traversal of the AST using a deque
    node_queue = deque([root_node])

    while node_queue:
        node = node_queue.popleft()

        if node.type == "decorated_definition" and "decorated_definition" in definition_dict:
            # Process decorated definitions (e.g. @property) with a dedicated parser
            definition = _parse_decorated_definition(node, definition_dict)
            if definition:
                definition_list.append(definition)

        elif node.type in definition_dict and node.type != "decorated_definition":
            # Process a standard definition node
            name_node_type = definition_dict[node.type]
            definition = _parse_definition_node(node, name_node_type)
            if definition:
                # Exclude #include guard #define directives
                if node.type == "preproc_def" and _INCLUDE_GUARD_RE.match(definition.name):
                    node_queue.extend(node.children)
                    continue

                definition_list.append(definition)

                # Continue traversal inside container-type definitions (e.g. namespace)
                if node.type in _CONTAINER_DEFINITION_TYPES:
                    node_queue.extend(node.children)
            else:
                # For destructuring (destructured assignment), extract multiple names
                names = _extract_destructured_names(node, name_node_type)
                if names:
                    for name in names:
                        definition_list.append(DefinitionInfo(
                            name=name,
                            type=node.type,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        ))
                else:
                    # When name extraction fails, descend into child nodes to continue search
                    node_queue.extend(node.children)

        else:
            # If not a definition node, add children to the queue to dig deeper
            node_queue.extend(node.children)

    return sorted(definition_list, key=lambda d: d.start_line)


def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None:
    """Parse a decorated definition node (decorated_definition).

    Extracts the inner function/class definition and delegates
    to _parse_definition_node.

    Args:
        node: A decorated_definition node.
        definition_dict: Per-language definition node settings.

    Returns:
        DefinitionInfo, or None if no valid definition is found inside.
    """
    # Find the inner function/class definition
    inner_node: Node | None = None

    # Search children of decorated_definition for a definition node
    for child in node.children:
        if child.type in definition_dict and child.type != "decorated_definition":
            inner_node = child

    if inner_node is None:
        return None

    # Extract the name from the inner definition node
    name_node_type = definition_dict[inner_node.type]
    definition = _parse_definition_node(inner_node, name_node_type)

    if definition:
        # Adjust to the full line range including the decorator
        definition.start_line = node.start_point[0] + 1
        definition.end_line = node.end_point[0] + 1

    return definition


def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None:
    """Convert a definition node into a DefinitionInfo.

    When name_node_type is a standard node type name (e.g. "identifier"),
    the name is looked up among direct children.
    When it is a "__sentinel__" format sentinel value, processing is
    delegated to a dedicated extraction function via _extract_name.

    Args:
        node: A definition node (function_definition / class_definition / expression_statement, etc.).
        name_node_type: A string indicating how to obtain the name.

    Returns:
        DefinitionInfo, or None if the name cannot be obtained.
    """
    # Extract the name from the node
    name = _extract_name(node, name_node_type)
    if name is None:
        return None

    # Create and return a DefinitionInfo (convert 0-based line numbers to 1-based)
    return DefinitionInfo(
        name=name,
        type=node.type,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )


def _extract_name(node: Node, name_type: str) -> str | None:
    """Dispatcher that extracts the name according to the node type.

    When name_type is a sentinel value (a string starting and ending with "__"),
    a dedicated function is used to obtain the name node located deep in the AST.
    For standard values, the name is looked up among direct children.

    Args:
        node: The definition node from which to extract the name.
        name_type: The value specified in definition_dict of settings.py.
                   Standard pattern: "identifier", "type_identifier", etc.
                   Special pattern: "__assignment__", "__variable_declarator__",
                                    "__function_declarator__", "__init_declarator__"

    Returns:
        The definition name string, or None if extraction fails.
    """
    # Dispatch to a dedicated extraction function for each sentinel value

    # Python: expression_statement > assignment > left-hand identifier
    if name_type == "__assignment__":
        return _extract_assignment_name(node)

    # JS/TS: lexical_declaration / variable_declaration > variable_declarator > identifier
    if name_type == "__variable_declarator__":
        return _extract_variable_declarator_name(node)

    # C/C++: declaration > init_declarator > identifier
    if name_type == "__init_declarator__":
        return _extract_init_declarator_name(node)

    # C/C++: function_definition > function_declarator > identifier
    if name_type == "__function_declarator__":
        return _extract_function_declarator_name(node)

    # Standard pattern: search direct children for one matching name_type
    for child in node.children:
        if child.type == name_type:
            return child.text.decode("utf-8")
    return None


def _extract_assignment_name(node: Node) -> str | None:
    """Extract the variable name from a Python top-level variable assignment.

    Target AST structure:
        expression_statement       <- this node is passed as the argument
          +-- assignment
               +-- left: identifier "X"  <- extract this
               +-- =
               +-- right: (value)

    Returns None if the content of expression_statement is not an assignment
    (e.g. print("hello")) or if the left-hand side is not an identifier
    (e.g. obj.attr = 1).

    Args:
        node: An expression_statement node.

    Returns:
        The variable name string, or None if not applicable.
    """
    if not node.children:
        return None
    inner = node.children[0]
    if inner.type != "assignment":
        return None
    # Get the left-hand side from the assignment's left field
    lhs = inner.child_by_field_name("left")
    if lhs and lhs.type == "identifier":
        return lhs.text.decode("utf-8")
    return None


def _extract_variable_declarator_name(node: Node) -> str | None:
    """Extract the variable name from a JS/TS variable declaration.

    Target AST structure:
        lexical_declaration              <- this node is passed as the argument
          +-- const / let
          +-- variable_declarator
               +-- name: identifier "X"  <- extract this
               +-- (: type annotation)   <- TS only
               +-- =
               +-- (value)

    The same structure applies to variable_declaration (var declarations).

    Args:
        node: A lexical_declaration or variable_declaration node.

    Returns:
        The variable name string, or None if extraction fails.
    """
    # Get the identifier from the variable_declarator's name field
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            if name_node:
                return name_node.text.decode("utf-8")
    return None


def _extract_function_declarator_name(node: Node) -> str | None:
    """Extract the function name from a C/C++ function definition.

    Target AST structure:
        function_definition              <- this node is passed as the argument
          +-- primitive_type "double"
          +-- declarator: function_declarator
          |    +-- declarator: identifier "distance"  <- extract this
          |    +-- parameters: parameter_list
          +-- body: compound_statement { ... }

    function_definition does not have an identifier as a direct child;
    it is inside function_declarator, so the standard pattern (direct child search)
    cannot obtain the name.

    For C++ class method implementations, the function_declarator's declarator
    becomes a qualified_identifier (e.g. "Shape::get_name"). In that case,
    the method name is obtained from the last identifier within the qualified_identifier.

    Args:
        node: A function_definition node.

    Returns:
        The function name string, or None if function_declarator is not found.
    """
    # Get function_declarator from the function_definition's declarator field
    func_decl = node.child_by_field_name("declarator")
    if not func_decl or func_decl.type != "function_declarator":
        return None
    # Get the name node from function_declarator's declarator field
    name_node = func_decl.child_by_field_name("declarator")
    if not name_node:
        return None
    if name_node.type == "identifier":
        return name_node.text.decode("utf-8")
    # C++ class method implementation: qualified_identifier like Shape::get_name
    if name_node.type == "qualified_identifier":
        last_id = None
        for qc in name_node.children:
            if qc.type == "identifier":
                last_id = qc.text.decode("utf-8")
        return last_id
    return None


def _extract_init_declarator_name(node: Node) -> str | None:
    """Extract the variable name from a C/C++ variable/constant declaration.

    Target AST structure:
        declaration                      <- this node is passed as the argument
          +-- const (optional)
          +-- primitive_type "int"
          +-- declarator: init_declarator
               +-- declarator: identifier "X"  <- extract this
               +-- =
               +-- number_literal 3

    Forward declarations (e.g. void freeFunction();) do not have an init_declarator,
    so None is returned. The caller's BFS fallback will extract the function name
    from the child function_declarator.

    Args:
        node: A declaration node.

    Returns:
        The variable name string, or None if no init_declarator is found.
    """
    # Get the init_declarator from the declaration's declarator field
    declarator = node.child_by_field_name("declarator")
    if not declarator or declarator.type != "init_declarator":
        return None
    # Get the identifier from init_declarator's declarator field
    name_node = declarator.child_by_field_name("declarator")
    if name_node and name_node.type == "identifier":
        return name_node.text.decode("utf-8")
    return None


def _extract_destructured_names(node: Node, name_type: str) -> list[str]:
    """Extract multiple variable names from a destructuring (destructured assignment).

    Called when standard name extraction fails; determines whether the node
    is a destructuring pattern and collects the names.

    Target patterns:
        Python:  X, Y = 1, 2    -> collect identifiers from pattern_list
        JS/TS:   const { a, b } = obj  -> collect names from object_pattern
        JS/TS:   const [a, b] = arr    -> collect names from array_pattern

    Args:
        node: A definition node (expression_statement / lexical_declaration, etc.).
        name_type: The sentinel value specified in definition_dict of settings.py.

    Returns:
        A list of variable names. Empty list if not a destructuring pattern.
    """
    if name_type == "__assignment__":
        # Python: collect variable names from pattern_list in X, Y = 1, 2
        if not node.children:
            return []
        inner = node.children[0]
        if inner.type != "assignment":
            return []
        lhs = inner.child_by_field_name("left")
        if not lhs or lhs.type != "pattern_list":
            return []
        return [
            child.text.decode("utf-8")
            for child in lhs.children
            if child.type == "identifier"
        ]

    if name_type == "__variable_declarator__":
        # JS/TS: const { a, b } = obj / const [a, b] = arr
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node and name_node.type in ("object_pattern", "array_pattern"):
                    return _collect_identifiers_from_pattern(name_node)
        return []

    return []


def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]:
    """Collect variable names from an object_pattern / array_pattern.

    Handles nested patterns recursively
    (e.g. const { a, inner: { b } } = obj).

    Args:
        pattern_node: An object_pattern or array_pattern node.

    Returns:
        A list of variable names found in the pattern.
    """
    names: list[str] = []
    for child in pattern_node.children:
        if child.type == "identifier":
            names.append(child.text.decode("utf-8"))
        elif child.type == "shorthand_property_identifier_pattern":
            # a, b in { a, b }
            names.append(child.text.decode("utf-8"))
        elif child.type in ("object_pattern", "array_pattern"):
            names.extend(_collect_identifiers_from_pattern(child))
        elif child.type == "pair_pattern":
            # { key: localName } -> localName (local variable name) is defined
            value = child.child_by_field_name("value")
            if value and value.type == "identifier":
                names.append(value.text.decode("utf-8"))
            elif value and value.type in ("object_pattern", "array_pattern"):
                names.extend(_collect_identifiers_from_pattern(value))
    return names

