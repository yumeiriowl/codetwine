import re
from collections import deque
from dataclasses import dataclass
from tree_sitter import Node
from codetwine.extractors.cobol_source import CobolSource
from codetwine.extractors.r_source import r_definition_list
from codetwine.config.settings import (
    C_DEFINITION_DICT,
    COBOL_DEFINITION_DICT,
    CPP_DEFINITION_DICT,
    KOTLIN_DEFINITION_DICT,
    PATTERN_FIELD_DICT,
    PATTERN_TYPE_SET,
    R_DEFINITION_DICT,
)

# Regex pattern for filtering out #include guard #define directives
_INCLUDE_GUARD_RE = re.compile(r"^_*[A-Z][A-Z0-9_]*_H(?:PP|XX)?_*(?:INCLUDED)?_*$")

# AST node types for which child node traversal continues even after being recorded as a definition.
# e.g. namespace_definition contains class and function definitions inside,
# and class bodies contain methods, constructors and fields.
CONTAINER_DEFINITION_TYPE_SET = {
    "namespace_definition",        # C++
    "class_definition",            # Python
    "class_declaration",           # Java / Kotlin / JS / TS / C#
    "abstract_class_declaration",  # TS
    "class_specifier",             # C++
    "struct_specifier",            # C / C++
    "union_specifier",             # C / C++
    "enum_specifier",              # C / C++
    "interface_declaration",       # Java / TS / C#
    "enum_declaration",            # Java / TS / C#
    "record_declaration",          # Java / C#
    "annotation_type_declaration", # Java
    "struct_declaration",          # C#
    "object_declaration",          # Kotlin
    "companion_object",            # Kotlin
    "internal_module",             # TS
    "export_statement",            # JS / TS
    "class",                       # JS / TS (a class written as a value)
    "impl_item",                   # Rust
    "trait_item",                  # Rust
    "mod_item",                    # Rust
}

# Definition node types of a type an object is made of
CLASS_DEFINITION_TYPE_SET = {
    "class_definition",            # Python
    "class_declaration",           # Java / Kotlin / JS / TS
    "abstract_class_declaration",  # TS
    "class",                       # JS / TS (a class written as a value)
    "class_specifier",             # C++
    "struct_specifier",            # C / C++
    "union_specifier",             # C / C++
    "interface_declaration",       # Java / TS
    "enum_declaration",            # Java / TS
    "record_declaration",          # Java
    "object_declaration",          # Kotlin
    "struct_item",                 # Rust
    "enum_item",                   # Rust
    "union_item",                  # Rust
    "trait_item",                  # Rust
}

# Definition node types that name a type under another name (C / C++: typedef struct
# node { ... } node_t;); a variable declared with such a name is one of that type
TYPE_NAME_DEFINITION_TYPE_SET = {"type_definition"}

# Container definition types whose inner definitions are not members of the container:
# they are defined at the level the container itself is written at
# (C / C++: the enumerators of an enum, the names of a namespace)
OPEN_DEFINITION_TYPE_SET = {"namespace_definition", "enum_specifier"}

# Definition node types that only group other definitions; their own name is not a name
# of the file (C++: a namespace is written in every file that adds to it)
TRANSPARENT_DEFINITION_TYPE_SET = {"namespace_definition"}

# Definition node types of which only the type is searched for further definitions
# (C / C++: typedef struct node { ... } node_t; struct opt { ... } opts[] = { ... };)
_TYPE_FIELD_DEFINITION_TYPE_SET = {"declaration", "type_definition", "field_declaration"}


# Definition types referenced by their own name from anywhere in the file, also when
# they are nested inside another definition (COBOL)
BARE_NAME_DEFINITION_TYPE_SET = set(COBOL_DEFINITION_DICT)

# Definition node types named after a definition written elsewhere (Rust: impl Type, impl Trait for Type).
# They are listed as definitions but do not define the name they carry.
ATTACHED_DEFINITION_TYPE_SET = {"impl_item"}

# Container definition types whose inner definitions are members: they are written after
# the type or an object of it, not seen by their name alone outside the type
MEMBER_OWNER_TYPE_SET = CLASS_DEFINITION_TYPE_SET | ATTACHED_DEFINITION_TYPE_SET

# Name the companion object of a class is written with when it has no name of its own (Kotlin)
COMPANION_NAME = "Companion"

# Name of the definition an anonymous default export is listed under (JS / TS)
DEFAULT_EXPORT_NAME = "default"

# Node types of a function written as a value, and of a block that is run where it is
# written; the declarations inside one are local to it and are no definitions of the file
# (JS / TS: a callback, a class static block; Kotlin: a lambda, an init block, a
# secondary constructor; Rust: a closure)
_LOCAL_BODY_TYPE_SET = {
    "function_expression", "arrow_function", "generator_function", "class_static_block",
    "lambda_literal", "anonymous_function", "anonymous_initializer", "closure_expression",
    "secondary_constructor",
}

# Keyword of a class declaration -> definition type the declaration is listed with when
# the grammar reads it as an ERROR node (Kotlin)
_ERROR_CLASS_KEYWORD_DICT = {
    "class": "class_declaration",
    "interface": "class_declaration",
    "object": "object_declaration",
}

# Keyword of a function declaration, the definition type the declaration is listed with
# when the grammar reads it as part of an ERROR node, and the node type of its parameters
# (Kotlin)
_ERROR_FUNCTION_KEYWORD = "fun"
_ERROR_FUNCTION_TYPE = "function_declaration"
_FUNCTION_PARAMETER_TYPE = "function_value_parameters"

# Node type of the body of an enum class (Kotlin)
_ENUM_BODY_TYPE = "enum_class_body"

# Definition node types that can be declared for values of another type (Kotlin)
_RECEIVER_DEFINITION_TYPE_SET = {"function_declaration", "property_declaration"}

# Node types of the body of a class declaration (Kotlin)
_CLASS_BODY_TYPE_SET = {"class_body", "enum_class_body"}

# Definition node types of a function whose body is in the field "body" (C / C++: a
# function definition; JS / TS: a method)
_FUNCTION_BODY_TYPE_SET = {"function_definition", "method_definition"}

# Node types of a struct, union, enum or class written with its keyword (C / C++)
_TAG_SPECIFIER_TYPE_SET = {"struct_specifier", "union_specifier", "enum_specifier", "class_specifier"}

# Node types of a declaration that holds declarators (C / C++)
_DECLARATION_TYPE_SET = {"declaration", "field_declaration"}

# Node types of a statement that is written only inside a function; the declarations
# inside one are local (C / C++)
_CONTROL_STATEMENT_TYPE_SET = {
    "if_statement", "while_statement", "do_statement", "for_statement", "for_range_loop",
    "switch_statement", "try_statement",
}

# Node type of a block of statements, and the node type of which it is the body (C / C++)
_BLOCK_TYPE = "compound_statement"
_BLOCK_OWNER_TYPE = "function_definition"

# Start of the node types of a preprocessor line (C / C++)
_PREPROCESSOR_TYPE_PREFIX = "preproc"

# The last character of the text in front of a block that holds declarations of the
# file: the end of a name, of template arguments or of a string (C / C++: namespace
# geo {, struct Box<int> {, extern "C" {)
_DECLARATION_BLOCK_LEAD_RE = re.compile(rb'[\w>"]\Z')

# Declarator node types that wrap the declarator holding a name (C / C++: int *p, a[3], &r, (*f))
_WRAP_DECLARATOR_TYPE_SET = {
    "pointer_declarator", "array_declarator", "reference_declarator", "parenthesized_declarator",
}

# Node types of a value that is a function or a class (JS / TS)
_FUNCTION_VALUE_TYPE_SET = {
    "function_expression", "arrow_function", "generator_function", "class",
}

# Node type of a class written as a value, and the declaration node types that give a
# name such a value (JS / TS)
_CLASS_VALUE_TYPE = "class"
_VALUE_DECLARATION_TYPE_SET = {"lexical_declaration", "variable_declaration"}

# Node types of the name of a class member (JS / TS)
_PROPERTY_NAME_TYPE_SET = {"property_identifier", "private_property_identifier"}

# Node types of a name inside a binding pattern
PATTERN_NAME_TYPE_SET = {
    "identifier", "shorthand_property_identifier_pattern", "shorthand_field_identifier",
}


@dataclass
class DefinitionInfo:
    """Data class holding information about a single definition."""

    name: str                                     # Definition name (function / class / variable / type, etc.)
    type: str                                     # AST node type ("function_definition" / "expression_statement", etc.)
    start_line: int                               # Start line number of the definition (1-based)
    end_line: int                                 # End line number of the definition
    name_line: int | None = None                  # Line the name is written on (COBOL, BMS)
    level: int | None = None                      # Level number of a data item (COBOL, BMS)
    is_group: bool | None = None                  # Whether a data item is a group item (COBOL, BMS)
    # Byte range of the definition in the UTF-8 text parse_file() returns; None for a
    # COBOL, BMS or R definition
    start_byte: int | None = None
    end_byte: int | None = None
    # Whether the definition is declared for values of another type (Kotlin: an
    # extension function or property, fun Shape.describe())
    has_receiver: bool = False


def extract_definitions(
    root_node: Node | CobolSource,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]:
    """Extract definitions (functions, classes, variables, types, etc.) from the AST and return them in line-number order.

    For a CobolSource its definitions are returned: programs, ENTRY names, sections,
    paragraphs, data items, index names and descriptions of files, with the line of the
    name, and the level number and whether it is a group item for a data item.

    For an R file (definition_dict is R_DEFINITION_DICT) the definitions
    r_definition_list() reads are returned: top-level assignments, the names setClass
    and the like are given, and the members of R6Class and setRefClass.

    definition_dict structure:
        key = AST node type (e.g. "function_definition")
        value = one of the following:
          - Child node type name (standard pattern): obtains the name from a direct child.
            e.g. "identifier" -> searches node.children for a child with type=="identifier"
          - "__sentinel__" format sentinel value (special pattern): the names are read by
            a dedicated extraction function.
            e.g. "__assignment__" -> expression_statement > assignment > identifier

    A node that defines several names (int a, b; / X, Y = 1, 2 / const { a, b } = obj)
    gives one definition per name, all with the line range of the node.

    If no name is obtained (e.g. a C/C++ declaration is a function prototype),
    child nodes are added to the queue and BFS continues. This allows detection of
    definitions like function_declarator nested inside.

    For a C / C++ file, a statement written only inside a function and a block that
    is the body of no function and follows no name are not searched: the grammar read
    them outside the function they belong to (_is_local_statement).

    For a Kotlin file, a class declaration the grammar reads as part of an ERROR node
    is a definition as well (_error_class_definition_list), and so is a function
    declaration with a body (_error_function_definition_list), whose body is not
    searched; a class declaration whose body it reads as an ERROR node after it covers
    that node (_error_body_node).

    Args:
        root_node: The AST root node covering the entire file, or the CobolSource of a COBOL file.
        definition_dict: Per-language definition node settings.

    Returns:
        A list of DefinitionInfo sorted by line number in ascending order.
    """
    if isinstance(root_node, CobolSource):
        return [
            DefinitionInfo(
                definition.name, definition.type, definition.start_line, definition.end_line,
                definition.name_line, definition.level, definition.is_group,
            )
            for definition in root_node.definition_list
        ]
    if definition_dict is R_DEFINITION_DICT:
        return [
            DefinitionInfo(definition.name, definition.type, definition.start_line, definition.end_line)
            for definition in r_definition_list(root_node)
        ]

    definition_list: list[DefinitionInfo] = []
    is_error_class_read = definition_dict is KOTLIN_DEFINITION_DICT
    is_local_statement_skip = definition_dict is C_DEFINITION_DICT or definition_dict is CPP_DEFINITION_DICT

    # BFS traversal of the AST using a deque
    node_queue = deque([root_node])

    while node_queue:
        node = node_queue.popleft()
        if node.type == "ERROR" and is_error_class_read:
            definition_list.extend(_error_class_definition_list(node))
            function_definition_list, local_id_set = _error_function_definition_list(node)
            definition_list.extend(function_definition_list)
            node_queue.extend(child for child in node.children if child.id not in local_id_set)
            continue
        if node.type not in definition_dict:
            # If not a definition node, add children to the queue to dig deeper, except
            # into the body of a function written as a value and into a statement of a
            # function, whose declarations are local
            if is_local_statement_skip and _is_local_statement(node):
                continue
            if node.type not in _LOCAL_BODY_TYPE_SET or _is_call_in_place(node):
                node_queue.extend(node.children)
            continue

        # A decorated definition (e.g. @property) is named and typed after the
        # function / class inside it and keeps the line range including the decorator
        inner_node = _decorated_inner_node(node, definition_dict) if node.type == "decorated_definition" else node
        name_list = definition_name_list(inner_node, definition_dict) if inner_node else []
        # A name the grammar put in as missing has no text
        name_list = [name for name in name_list if name]
        if node.type == "preproc_def":
            # Exclude #include guard #define directives
            name_list = [name for name in name_list if not _INCLUDE_GUARD_RE.match(name)]
        if not name_list:
            # When no name is obtained, descend into child nodes to continue search,
            # except into the body of a function, whose declarations are local
            body_node = _local_body_node(node)
            node_queue.extend(child for child in node.children if child != body_node)
            continue

        # The definition covers the declaration its node is the declarator of, and a
        # body the grammar reads as an ERROR node after it
        extent_node = _extent_node(node, definition_dict)
        start_line, end_line = _line_range(extent_node)
        end_byte = extent_node.end_byte
        body_node = _error_body_node(node) if is_error_class_read else None
        if body_node is not None:
            end_line, end_byte = _line_range(body_node)[1], body_node.end_byte
        has_receiver = _has_receiver(inner_node)
        # A class written as the value of the one name a statement gives is the
        # definition of that name
        class_node = _class_value_node(inner_node) if len(name_list) == 1 else None
        definition_type = class_node.type if class_node is not None else inner_node.type
        for name in name_list:
            definition_list.append(DefinitionInfo(
                name=name, type=definition_type, start_line=start_line, end_line=end_line,
                start_byte=extent_node.start_byte, end_byte=end_byte,
                has_receiver=has_receiver,
            ))

        # Continue traversal inside container-type definitions (e.g. namespace),
        # and inside the type a declaration is written with
        if class_node is not None:
            node_queue.extend(class_node.children)
        elif inner_node.type in CONTAINER_DEFINITION_TYPE_SET:
            node_queue.extend(inner_node.children)
        elif inner_node.type in _TYPE_FIELD_DEFINITION_TYPE_SET:
            type_node = inner_node.child_by_field_name("type")
            if type_node is not None:
                node_queue.append(type_node)
        elif inner_node.type == "expression_statement":
            # The entries of an object a CommonJS module exports
            export_assignment = commonjs_export_assignment(inner_node)
            if export_assignment is not None and export_assignment[1].type == "object":
                node_queue.extend(export_assignment[1].children)

    return sorted(definition_list, key=lambda definition: definition.start_line)


def select_top_level_definitions(
    definition_list: list[DefinitionInfo],
) -> list[DefinitionInfo]:
    """Keep only definitions that are not members of another definition.

    Members of a class, an impl block or a trait (methods, constructors, fields) are left out.
    The definitions inside a container in OPEN_DEFINITION_TYPE_SET (a C++ namespace, a
    C / C++ enum) are kept, and a definition of a type in BARE_NAME_DEFINITION_TYPE_SET
    is kept wherever it is.

    Args:
        definition_list: Definitions of a single file, sorted by start_line.

    Returns:
        The outermost definitions, sorted by start_line.
    """
    outer_list: list[DefinitionInfo] = []
    member_end = 0
    for definition in definition_list:
        if definition.type in BARE_NAME_DEFINITION_TYPE_SET:
            outer_list.append(definition)
            continue
        # Skip definitions that start within the range of a container with members
        if definition.start_line <= member_end:
            continue
        outer_list.append(definition)
        if (
            definition.type in CONTAINER_DEFINITION_TYPE_SET
            and definition.type not in OPEN_DEFINITION_TYPE_SET
        ):
            member_end = definition.end_line
    return outer_list


def _is_within(definition: DefinitionInfo, container: DefinitionInfo) -> bool:
    """Return whether a definition is written inside another one (by bytes when both have them)."""
    if definition.start_byte is not None and container.start_byte is not None:
        return (
            container.start_byte <= definition.start_byte
            and definition.end_byte <= container.end_byte
        )
    return (
        container.start_line <= definition.start_line
        and definition.end_line <= container.end_line
    )


def member_owner_dict(definition_list: list[DefinitionInfo]) -> dict[int, DefinitionInfo]:
    """Return the class each member of a class is written in.

    Args:
        definition_list: Definitions of a single file, sorted by start_line.

    Returns:
        {id of a definition: the definition of MEMBER_OWNER_TYPE_SET it is a direct
        member of}. A definition whose innermost container is of another type (a
        namespace, a module), or that has none, is left out.
    """
    owner_dict: dict[int, DefinitionInfo] = {}
    container_stack: list[DefinitionInfo] = []
    for definition in definition_list:
        while container_stack and not _is_within(definition, container_stack[-1]):
            container_stack.pop()
        if container_stack and container_stack[-1].type in MEMBER_OWNER_TYPE_SET:
            owner_dict[id(definition)] = container_stack[-1]
        if (
            definition.type in CONTAINER_DEFINITION_TYPE_SET
            and definition.type not in OPEN_DEFINITION_TYPE_SET
        ):
            container_stack.append(definition)
    return owner_dict


def definition_name_list(node: Node, definition_dict: dict[str, str]) -> list[str]:
    """Return the names a definition node defines.

    When the value definition_dict gives for the node type is a sentinel value (a string
    starting and ending with "__"), a dedicated function reads the names. For a
    standard value, the name is the first direct child of that node type.

    Args:
        node: An AST node.
        definition_dict: Per-language definition node settings.

    Returns:
        The names, in the order they are written. Empty when the node type is not a
        definition or no name can be obtained.
    """
    if node.type == "decorated_definition" and node.type in definition_dict:
        inner_node = _decorated_inner_node(node, definition_dict)
        return definition_name_list(inner_node, definition_dict) if inner_node else []
    name_type = definition_dict.get(node.type)
    if name_type is None:
        return []

    # Sentinel value: a dedicated extraction function
    extract_function = _SENTINEL_EXTRACTOR_DICT.get(name_type)
    if extract_function is not None:
        return extract_function(node)

    # Standard pattern: search direct children for one matching name_type
    for child in node.children:
        if child.type == name_type:
            return [_node_text(child)]
    return []


def _node_text(node: Node) -> str:
    """Return the source text of a node."""
    return node.text.decode("utf-8")


def _extent_node(node: Node, definition_dict: dict[str, str]) -> Node:
    """Return the node whose text and lines a definition node is listed with.

    A declarator that is a definition node is listed with the declaration it is
    written in, when that declaration defines no name itself (C/C++: the
    function_declarator of int *add(int a, int b); with the whole declaration).

    Args:
        node: A definition node.
        definition_dict: Per-language definition node settings.

    Returns:
        The declaration or field declaration around the node, reached through
        declarators only, when it gives no name; else the node itself.
    """
    ancestor = node.parent
    while ancestor is not None and (
        ancestor.type in _WRAP_DECLARATOR_TYPE_SET
        or ancestor.type in ("init_declarator", "function_declarator")
    ):
        ancestor = ancestor.parent
    if (
        ancestor is not None
        and ancestor.type in _DECLARATION_TYPE_SET
        and ancestor.type in definition_dict
        and not definition_name_list(ancestor, definition_dict)
    ):
        return ancestor
    return node


def _line_range(node: Node) -> tuple[int, int]:
    """Return the first and last line of a node (1-based).

    A node that ends at the start of a line (a #define with its line break) ends on
    the line before.
    """
    start_line = node.start_point[0] + 1
    end_row, end_column = node.end_point
    end_line = end_row + 1 if end_column > 0 else end_row
    return start_line, max(start_line, end_line)


def _is_local_statement(node: Node) -> bool:
    """Return whether a node the grammar read outside a function is a statement of one (C / C++).

    Args:
        node: A node that is no definition node.

    Returns:
        True for a node in _CONTROL_STATEMENT_TYPE_SET, and for a block that is the
        body of no function unless the node in front of it is no preprocessor line and
        its text ends like the head of a namespace, a type or a linkage specification
        (_DECLARATION_BLOCK_LEAD_RE).
    """
    if node.type in _CONTROL_STATEMENT_TYPE_SET:
        return True
    if node.type != _BLOCK_TYPE or (
        node.parent is not None and node.parent.type == _BLOCK_OWNER_TYPE
    ):
        return False
    lead_node = node.prev_sibling
    while lead_node is not None and lead_node.type == "comment":
        lead_node = lead_node.prev_sibling
    if (
        lead_node is None or lead_node.text is None
        or lead_node.type.startswith(_PREPROCESSOR_TYPE_PREFIX)
    ):
        return True
    return _DECLARATION_BLOCK_LEAD_RE.search(lead_node.text.rstrip()) is None


def _local_body_node(node: Node) -> Node | None:
    """Return the body of a function definition that gives no name, None for any other node.

    The declarations inside such a body are local to the function (JS / TS: a method
    named by a string or a computed key). A C / C++ function_definition without a function_declarator is no
    function the grammar read as one, and its body is searched like any other node.

    Args:
        node: A definition node that gives no name.

    Returns:
        The node in the field "body" of a node in _FUNCTION_BODY_TYPE_SET; of a node
        with a field "declarator", only when that holds a function_declarator.
    """
    if node.type not in _FUNCTION_BODY_TYPE_SET:
        return None
    declarator_node = node.child_by_field_name("declarator")
    if declarator_node is not None:
        function_node = declarator_name_node(declarator_node)
        if function_node is None or function_node.type != "function_declarator":
            return None
    return node.child_by_field_name("body")


def _has_receiver(node: Node) -> bool:
    """Return whether a declaration is written for values of another type.

    Kotlin: fun Shape.describe() and val Shape.size hold a "." between the type and the
    name as a direct child.

    Args:
        node: A definition node.

    Returns:
        True for a node in _RECEIVER_DEFINITION_TYPE_SET with such a child.
    """
    return node.type in _RECEIVER_DEFINITION_TYPE_SET and any(
        child.type == "." for child in node.children
    )


def _error_body_node(node: Node) -> Node | None:
    """Return the ERROR node that holds the body of a class declaration the grammar read without one.

    Args:
        node: A definition node.

    Returns:
        The next sibling of the node when the node has no child in _CLASS_BODY_TYPE_SET
        and that sibling is an ERROR node starting with "{"; else None.
    """
    sibling = node.next_sibling
    if (
        sibling is None or sibling.type != "ERROR" or not sibling.children
        or sibling.children[0].type != "{"
        or any(child.type in _CLASS_BODY_TYPE_SET for child in node.children)
    ):
        return None
    return sibling


def _error_class_definition_list(node: Node) -> list[DefinitionInfo]:
    """Return the class declarations the grammar read as part of an ERROR node.

    A keyword of _ERROR_CLASS_KEYWORD_DICT directly followed by a name, both direct
    children of the node, with a "{" among the children after them, gives one
    definition. It covers the node from the keyword, or from the modifiers in front of
    it, to the end of the node.

    Args:
        node: An ERROR node.

    Returns:
        The definitions, in the order they are written.
    """
    definition_list: list[DefinitionInfo] = []
    child_list = node.children
    end_line = _line_range(node)[1]
    for index, child in enumerate(child_list[:-1]):
        definition_type = _ERROR_CLASS_KEYWORD_DICT.get(child.type)
        name_node = child_list[index + 1]
        if (
            definition_type is None or child.is_named or name_node.type != "identifier"
            or not any(later.type == "{" for later in child_list[index + 2:])
        ):
            continue
        start_node = child
        if index > 0 and child_list[index - 1].type == "modifiers":
            start_node = child_list[index - 1]
        definition_list.append(DefinitionInfo(
            name=_node_text(name_node), type=definition_type,
            start_line=start_node.start_point[0] + 1, end_line=end_line,
            start_byte=start_node.start_byte, end_byte=node.end_byte,
        ))
    return definition_list


def _error_function_definition_list(node: Node) -> tuple[list[DefinitionInfo], set[int]]:
    """Return the function declarations the grammar read as part of an ERROR node.

    The keyword _ERROR_FUNCTION_KEYWORD, a name directly in front of the parameters and
    a "{" after them, all direct children of the node, give one definition. It covers
    the node from the keyword, or from the modifiers in front of it, to the "}" among
    the direct children that closes the "{", or to the end of the node when there is
    none. A function whose "=" comes before a "{" has no such body and gives none.

    Args:
        node: An ERROR node.

    Returns:
        (the definitions in the order they are written, the ids of the direct children
        of the node written inside the body of one of them).
    """
    definition_list: list[DefinitionInfo] = []
    local_id_set: set[int] = set()
    child_list = node.children
    index = 0
    while index < len(child_list):
        child = child_list[index]
        index += 1
        if child.type != _ERROR_FUNCTION_KEYWORD or child.is_named:
            continue
        keyword_index = index - 1
        # The parameters, then the "{" that opens the body
        parameter_index = next(
            (
                later_index for later_index in range(index, len(child_list))
                if child_list[later_index].type == _FUNCTION_PARAMETER_TYPE
            ),
            None,
        )
        if parameter_index is None or child_list[parameter_index - 1].type != "identifier":
            continue
        if any(
            later.type == _ERROR_FUNCTION_KEYWORD and not later.is_named
            for later in child_list[index:parameter_index]
        ):
            continue
        open_index = None
        for later_index in range(parameter_index + 1, len(child_list)):
            if child_list[later_index].type == "{":
                open_index = later_index
                break
            if child_list[later_index].type in ("=", "}", _ERROR_FUNCTION_KEYWORD):
                break
        if open_index is None:
            continue
        # The "}" that closes the body
        depth = 1
        close_index = len(child_list)
        for later_index in range(open_index + 1, len(child_list)):
            if child_list[later_index].type == "{":
                depth += 1
            elif child_list[later_index].type == "}":
                depth -= 1
                if depth == 0:
                    close_index = later_index
                    break
        local_id_set.update(body_child.id for body_child in child_list[open_index + 1:close_index])
        start_node = child
        if keyword_index > 0 and child_list[keyword_index - 1].type == "modifiers":
            start_node = child_list[keyword_index - 1]
        end_node = child_list[close_index] if close_index < len(child_list) else node
        definition_list.append(DefinitionInfo(
            name=_node_text(child_list[parameter_index - 1]), type=_ERROR_FUNCTION_TYPE,
            start_line=start_node.start_point[0] + 1, end_line=_line_range(end_node)[1],
            start_byte=start_node.start_byte, end_byte=end_node.end_byte,
            has_receiver=any(
                later.type == "." for later in child_list[index:parameter_index]
            ),
        ))
        index = close_index + 1
    return definition_list, local_id_set


def _class_value_node(node: Node) -> Node | None:
    """Return the class a statement gives its name as a value (JS / TS).

    const Shape = class { ... };            -> the class
    module.exports = class Shape { ... };   -> the class
    exports.Shape = class { ... };          -> the class

    Args:
        node: A definition node.

    Returns:
        The value of the only declarator of a node in _VALUE_DECLARATION_TYPE_SET, or
        the value an expression_statement assigns, when it is a node of
        _CLASS_VALUE_TYPE; else None.
    """
    value_node: Node | None = None
    if node.type in _VALUE_DECLARATION_TYPE_SET:
        declarator_list = [child for child in node.children if child.type == "variable_declarator"]
        if len(declarator_list) == 1:
            value_node = declarator_list[0].child_by_field_name("value")
    elif node.type == "expression_statement":
        assignment_node = node.children[0] if node.children else None
        while assignment_node is not None and assignment_node.type == "assignment_expression":
            value_node = assignment_node.child_by_field_name("right")
            assignment_node = value_node
    if value_node is None or value_node.type != _CLASS_VALUE_TYPE:
        return None
    return value_node


def _is_call_in_place(node: Node) -> bool:
    """Return whether a function written as a value is called where it is written.

    (function () { ... })() and (() => { ... })() wrap the code of a whole file; the
    declarations inside such a function are read as the definitions of the file.

    Args:
        node: A node whose type is in _LOCAL_BODY_TYPE_SET.

    Returns:
        True when the node, with the parentheses around it, is what a call calls.
    """
    callee_node = node
    while callee_node.parent is not None and callee_node.parent.type == "parenthesized_expression":
        callee_node = callee_node.parent
    call_node = callee_node.parent
    return (
        call_node is not None
        and call_node.type == "call_expression"
        and call_node.child_by_field_name("function") == callee_node
    )


def _decorated_inner_node(node: Node, definition_dict: dict[str, str]) -> Node | None:
    """Return the function / class definition inside a decorated definition (Python).

    Args:
        node: A decorated_definition node.
        definition_dict: Per-language definition node settings.

    Returns:
        The inner definition node, or None if there is none.
    """
    inner_node: Node | None = None
    for child in node.children:
        if child.type in definition_dict and child.type != "decorated_definition":
            inner_node = child
    return inner_node


def pattern_name_list(
    node: Node, pattern_type_set: set[str], pattern_field_dict: dict[str, str],
) -> list[str]:
    """Return the names a binding pattern binds.

    A node in PATTERN_NAME_TYPE_SET is a name. Of a node in pattern_field_dict only the
    child in that field is read (b = 1 -> b, key: local -> local), its first named
    child when the field is "", and of a node in pattern_type_set every named child.
    Any other node (an attribute, a subscript, a type) binds nothing.

    Examples:
        { a, inner: { b }, hp: local = hp, ...rest }  -> ["a", "b", "local", "rest"]
        (x, [y, z], *tail)                            -> ["x", "y", "z", "tail"]

    Args:
        node: A pattern node, or a node holding patterns.
        pattern_type_set: The pattern_types of the language settings.
        pattern_field_dict: The pattern_field_dict of the language settings.

    Returns:
        The bound names, in the order they are written.
    """
    if node.type in PATTERN_NAME_TYPE_SET:
        return [_node_text(node)]
    field_name = pattern_field_dict.get(node.type)
    if field_name is not None:
        child_list = (
            node.children_by_field_name(field_name) if field_name else node.named_children[:1]
        )
    elif node.type in pattern_type_set:
        child_list = node.named_children
    else:
        return []
    name_list: list[str] = []
    for child in child_list:
        name_list.extend(pattern_name_list(child, pattern_type_set, pattern_field_dict))
    return name_list


def _extract_assignment_name_list(node: Node) -> list[str]:
    """Extract the variable names from a Python variable assignment.

    Target AST structure:
        expression_statement       <- this node is passed as the argument
          +-- assignment
               +-- left: identifier "X"  <- extract this
               +-- =
               +-- right: (value)

    The targets of a chain (a = b = 1) and of a destructuring (X, Y = 1, 2) are all
    returned. A target that is not a name (obj.attr = 1) gives none.

    Args:
        node: An expression_statement node.

    Returns:
        The variable names, empty when the statement is not an assignment.
    """
    name_list: list[str] = []
    inner_node = node.children[0] if node.children else None
    while inner_node is not None and inner_node.type == "assignment":
        left_node = inner_node.child_by_field_name("left")
        if left_node is not None:
            name_list.extend(pattern_name_list(left_node, PATTERN_TYPE_SET, PATTERN_FIELD_DICT))
        inner_node = inner_node.child_by_field_name("right")
    return name_list


def _extract_type_alias_name_list(node: Node) -> list[str]:
    """Extract the name from a Python type alias statement (type Alias = int, type Pair[T] = ...)."""
    name_node = node.child_by_field_name("left")
    while name_node is not None and name_node.type != "identifier":
        name_node = next(iter(name_node.named_children), None)
    return [_node_text(name_node)] if name_node is not None else []


def _extract_variable_declarator_name_list(node: Node) -> list[str]:
    """Extract the variable names from a JS/TS variable declaration or a Java field declaration.

    Target AST structure:
        lexical_declaration              <- this node is passed as the argument
          +-- const / let
          +-- variable_declarator
               +-- name: identifier "X"  <- extract this
               +-- (: type annotation)   <- TS only
               +-- =
               +-- (value)

    The same structure applies to variable_declaration (var declarations) and to
    Java field_declaration (modifiers + type + declarator: variable_declarator).
    Every declarator gives its name (int a, b;), and a destructuring pattern every
    name it binds (const { a, b } = obj). A declarator whose value is a module an
    import brings in (const m = require("./m"), _is_module_value) gives none: the
    name is one of the import, not a definition of the file.

    Args:
        node: A lexical_declaration, variable_declaration or field_declaration node.

    Returns:
        The variable names.
    """
    name_list: list[str] = []
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            if name_node is not None and not _is_module_value(child.child_by_field_name("value")):
                name_list.extend(pattern_name_list(name_node, PATTERN_TYPE_SET, PATTERN_FIELD_DICT))
    return name_list


def commonjs_export_assignment(node: Node) -> tuple[str, Node] | None:
    """Read a CommonJS export assignment (JS / TS).

    Target AST structure:
        expression_statement             <- this node is passed as the argument
          +-- assignment_expression
               +-- left: member_expression "exports.name" / "module.exports.name" / "module.exports"
               +-- right: (value)

    In a chain (exports = module.exports = value) the first target that is one of
    these counts.

    Args:
        node: An expression_statement node.

    Returns:
        (exported name, value node): the name is the property of exports.name and
        module.exports.name, and DEFAULT_EXPORT_NAME for module.exports itself. None
        when the statement is no such assignment.
    """
    inner_node = node.children[0] if node.children else None
    while inner_node is not None and inner_node.type == "assignment_expression":
        left_node = inner_node.child_by_field_name("left")
        right_node = inner_node.child_by_field_name("right")
        if left_node is None or right_node is None:
            return None
        value_node = right_node
        while value_node.type == "assignment_expression":
            value_node = value_node.child_by_field_name("right") or value_node.children[-1]
        if left_node.type == "member_expression":
            if _node_text(left_node) == "module.exports":
                return DEFAULT_EXPORT_NAME, value_node
            object_node = left_node.child_by_field_name("object")
            property_node = left_node.child_by_field_name("property")
            if (
                object_node is not None and property_node is not None
                and _node_text(object_node) in ("exports", "module.exports")
            ):
                return _node_text(property_node), value_node
        inner_node = right_node
    return None


def require_module(node: Node) -> str | None:
    """Return the module string of a require("module") call, None for any other node (JS / TS)."""
    if node.type != "call_expression":
        return None
    function_node = node.child_by_field_name("function")
    argument_node = node.child_by_field_name("arguments")
    if function_node is None or argument_node is None or _node_text(function_node) != "require":
        return None
    string_node_list = [child for child in argument_node.named_children if child.type == "string"]
    if len(string_node_list) != 1:
        return None
    return _node_text(string_node_list[0])[1:-1]


def _is_module_value(node: Node | None) -> bool:
    """Return whether a value is a module, or a member of one, an import brings in (JS / TS).

    require("./m")           -> True
    require("./m").name      -> True
    await import("./m")      -> True
    require("./m")(options)  -> False

    Args:
        node: The value node of a variable_declarator, or None.

    Returns:
        True for the three forms above.
    """
    if node is None:
        return False
    if node.type == "await_expression":
        call_node = next(iter(node.named_children), None)
        function_node = call_node.child_by_field_name("function") if call_node is not None else None
        argument_node = call_node.child_by_field_name("arguments") if call_node is not None else None
        return (
            call_node is not None and call_node.type == "call_expression"
            and function_node is not None and function_node.type == "import"
            and argument_node is not None
            and [child.type for child in argument_node.named_children] == ["string"]
        )
    if node.type == "member_expression":
        node = node.child_by_field_name("object")
    return node is not None and require_module(node) is not None


def _member_function_name(node: Node) -> str | None:
    """Return the name a top-level statement gives a function it assigns to a member (JS / TS).

    app.init = function () {};         -> "init"
    Foo.prototype.run = () => {};      -> "run"

    Args:
        node: An expression_statement node.

    Returns:
        The member name, or None when the statement is not written at the top level of
        the file or does not assign a function or class to a member.
    """
    inner_node = node.children[0] if node.children else None
    if (
        inner_node is None or inner_node.type != "assignment_expression"
        or node.parent is None or node.parent.type != "program"
    ):
        return None
    left_node = inner_node.child_by_field_name("left")
    right_node = inner_node.child_by_field_name("right")
    if (
        left_node is None or right_node is None
        or left_node.type != "member_expression"
        or right_node.type not in _FUNCTION_VALUE_TYPE_SET
    ):
        return None
    property_node = left_node.child_by_field_name("property")
    if property_node is None or property_node.type != "property_identifier":
        return None
    return _node_text(property_node)


def _member_assignment_name_list(node: Node) -> list[str]:
    """Extract the name from an assignment to a member written as a statement (JS / TS).

    exports.name = ... and module.exports.name = ... give the name;
    module.exports = <unnamed value> gives DEFAULT_EXPORT_NAME. An export whose value
    is a name (module.exports = name, exports.run = run) and
    module.exports = require("./m") give none (the named definition, or the module,
    stands for the export). A function assigned to a member
    of another object at the top level of the file gives the member name
    (_member_function_name).

    Args:
        node: An expression_statement node.

    Returns:
        [name], or empty.
    """
    export_assignment = commonjs_export_assignment(node)
    if export_assignment is None:
        member_name = _member_function_name(node)
        return [member_name] if member_name else []
    export_name, value_node = export_assignment
    if value_node.type == "identifier" or (
        export_name == DEFAULT_EXPORT_NAME and require_module(value_node) is not None
    ):
        return []
    return [export_name]


def _extract_export_member_name_list(node: Node) -> list[str]:
    """Extract the key of an entry of an object a module exports (JS / TS).

    module.exports = { run: function () {} };   -> "run"
    export default { run: () => 1 };            -> "run"
    An entry whose value is a name (key: name) gives none (the named definition
    stands for it), and so does an entry of any other object.

    Args:
        node: A pair node.

    Returns:
        [key], or empty.
    """
    key_node = node.child_by_field_name("key")
    value_node = node.child_by_field_name("value")
    object_node = node.parent
    if (
        key_node is None or value_node is None or object_node is None
        or key_node.type != "property_identifier" or value_node.type == "identifier"
    ):
        return []
    holder_node = object_node.parent
    is_export_value = holder_node is not None and (
        (holder_node.type == "export_statement" and holder_node.child_by_field_name("value") == object_node)
        or (
            holder_node.type == "assignment_expression" and holder_node.parent is not None
            and holder_node.parent.type == "expression_statement"
            and (export_assignment := commonjs_export_assignment(holder_node.parent)) is not None
            and export_assignment[1] == object_node
        )
    )
    return [_node_text(key_node)] if is_export_value else []


def _extract_property_name_list(node: Node) -> list[str]:
    """Extract the name of a class member written with a plain or a private name (JS / TS).

    run() {}        -> "run"
    #count = 0;     -> "#count"
    A member named by a string, a number or a computed key gives none.

    Args:
        node: A method_definition, field_definition or public_field_definition node.

    Returns:
        [name], or empty.
    """
    for child in node.children:
        if child.type in _PROPERTY_NAME_TYPE_SET:
            return [_node_text(child)]
    return []


def _extract_enum_entry_name_list(node: Node) -> list[str]:
    """Extract the name of a Kotlin enum entry.

    Target AST structure:
        class_declaration
          +-- modifiers > class_modifier > "enum"
          +-- enum_class_body
               +-- enum_entry            <- this node is passed as the argument
                    +-- identifier

    Args:
        node: An enum_entry node.

    Returns:
        [name], or empty for an entry the grammar reads in the body of a class
        declared without the modifier "enum".
    """
    body_node = node.parent
    class_node = body_node.parent if body_node is not None else None
    if body_node is None or body_node.type != _ENUM_BODY_TYPE or class_node is None:
        return []
    is_enum = any(
        keyword.type == "enum"
        for child in class_node.children if child.type == "modifiers"
        for modifier in child.children
        for keyword in modifier.children
    )
    if not is_enum:
        return []
    return [_node_text(child) for child in node.children if child.type == "identifier"][:1]


def _extract_enum_member_name_list(node: Node) -> list[str]:
    """Extract the name of a TypeScript enum member written without a value (enum E { A, B })."""
    if node.parent is None or node.parent.type != "enum_body":
        return []
    return [_node_text(node)]


def _default_export_name_list(node: Node) -> list[str]:
    """Return [DEFAULT_EXPORT_NAME] for an export default statement of an unnamed value (JS / TS).

    export default function () {} / class {} / { ... } / call() gives the name;
    export default name and export default function name() {} give none (the named
    definition stands for the export).

    Args:
        node: An export_statement node.

    Returns:
        [DEFAULT_EXPORT_NAME] or empty.
    """
    if not any(child.type == "default" for child in node.children):
        return []
    value_node = node.child_by_field_name("value")
    if value_node is None or value_node.type == "identifier":
        return []
    if value_node.child_by_field_name("name") is not None:
        return []
    return [DEFAULT_EXPORT_NAME]


def declarator_name_node(declarator_node: Node | None) -> Node | None:
    """Return the node holding the name of a C/C++ declarator.

    The declarators that wrap another one (pointer, array, reference, parentheses and
    an initializer) are removed: *names[] = { ... } -> names.

    Args:
        declarator_node: A declarator node, or None.

    Returns:
        The innermost node (an identifier, a function_declarator, etc.), or None.
    """
    while declarator_node is not None and (
        declarator_node.type in _WRAP_DECLARATOR_TYPE_SET
        or declarator_node.type == "init_declarator"
    ):
        inner_node = declarator_node.child_by_field_name("declarator")
        if inner_node is None:
            # A parenthesized declarator holds its declarator as a plain child
            inner_node = next(iter(declarator_node.named_children), None)
        declarator_node = inner_node
    return declarator_node


def _declarator_name_list(node: Node, name_type_tuple: tuple[str, ...]) -> list[str]:
    """Return the names of the declarators of a C/C++ declaration that are of the given node types.

    Args:
        node: A declaration, field_declaration or type_definition node.
        name_type_tuple: Node types of a name ("identifier", "field_identifier", ...).

    Returns:
        One name per declarator whose innermost node is of one of the types.
    """
    name_list: list[str] = []
    for declarator_node in node.children_by_field_name("declarator"):
        name_node = declarator_name_node(declarator_node)
        if name_node is not None and name_node.type in name_type_tuple:
            name_list.append(_node_text(name_node))
    return name_list


def _function_name(function_declarator_node: Node) -> str | None:
    """Return the name a C/C++ function_declarator declares.

    identifier (free function, constructor), field_identifier (method in a class body),
    operator_name (operator==) and destructor_name (~Shape) are taken as written. A
    qualified_identifier (a member defined outside its class) is taken with its
    scopes, without template arguments: Shape::get_name, Box<T>::get -> "Box::get".

    Args:
        function_declarator_node: A function_declarator node.

    Returns:
        The name, or None for another declarator (a function pointer).
    """
    name_node = function_declarator_node.child_by_field_name("declarator")
    scope_list: list[str] = []
    while name_node is not None and name_node.type == "qualified_identifier":
        scope_node = name_node.child_by_field_name("scope")
        if scope_node is not None:
            scope_list.append(_node_text(scope_node.child_by_field_name("name") or scope_node))
        name_node = name_node.child_by_field_name("name")
    if name_node is not None and name_node.type in (
        "identifier", "field_identifier", "operator_name", "destructor_name",
    ):
        return "::".join([*scope_list, _node_text(name_node)])
    return None


def _operator_cast_name(declarator_node: Node | None) -> str | None:
    """Return the name of a C++ conversion operator, None for any other declarator.

    operator bool() const { ... }         -> "operator bool"
    Flag::operator int() const { ... }    -> "Flag::operator int"

    Args:
        declarator_node: The declarator of a function_definition, without the
            declarators that wrap it.

    Returns:
        "operator " and the text of the type, after the scopes the operator is
        written with.
    """
    scope_list: list[str] = []
    while declarator_node is not None and declarator_node.type == "qualified_identifier":
        scope_node = declarator_node.child_by_field_name("scope")
        if scope_node is not None:
            scope_list.append(_node_text(scope_node.child_by_field_name("name") or scope_node))
        declarator_node = declarator_node.child_by_field_name("name")
    if declarator_node is None or declarator_node.type != "operator_cast":
        return None
    type_node = declarator_node.child_by_field_name("type")
    if type_node is None:
        return None
    return "::".join([*scope_list, "operator " + " ".join(_node_text(type_node).split())])


def _extract_function_declarator_name_list(node: Node) -> list[str]:
    """Extract the function name from a C/C++ function definition.

    Target AST structure:
        function_definition              <- this node is passed as the argument
          +-- primitive_type "double"
          +-- declarator: function_declarator
          |    +-- declarator: identifier "distance"  <- extract this
          |    +-- parameters: parameter_list
          +-- body: compound_statement { ... }

    The function_declarator may be wrapped in pointer or reference declarators
    (node_t *list_push(...) { ... }). See _function_name for the forms of the name.
    A function written with a macro that takes its name (TEST_IMPL(ping) { ... }) has
    a name in parentheses in place of a function_declarator and gives that name. A
    conversion operator gives the name _operator_cast_name() returns.

    Args:
        node: A function_definition node.

    Returns:
        [name], or empty if neither is found.
    """
    declarator_node = node.child_by_field_name("declarator")
    function_declarator_node = declarator_name_node(declarator_node)
    if (
        declarator_node is not None and declarator_node.type == "parenthesized_declarator"
        and function_declarator_node is not None
        and function_declarator_node.type == "identifier"
    ):
        return [_node_text(function_declarator_node)]
    cast_name = _operator_cast_name(function_declarator_node)
    if cast_name is not None:
        return [cast_name]
    if function_declarator_node is None or function_declarator_node.type != "function_declarator":
        return []
    name = _function_name(function_declarator_node)
    return [name] if name else []


def is_macro_call_declarator(node: Node) -> bool:
    """Return whether a C/C++ function_declarator is a macro call written as a statement.

    MODULE_INIT(setup);  and  DISALLOW_COPY(Shape);  are read as the declaration of a
    function without a type. A declaration without a type is the one of a constructor
    when it is written in the body of a class and named like the class (Shape(int);),
    and a macro call otherwise.

    Args:
        node: A function_declarator node.

    Returns:
        True when the node is the declarator of a declaration or field_declaration
        without a type, its name is a plain name, and it is not written in the body
        of a class of that name.
    """
    holder_node = node.parent
    while holder_node is not None and (
        holder_node.type in _WRAP_DECLARATOR_TYPE_SET or holder_node.type == "init_declarator"
    ):
        holder_node = holder_node.parent
    name_node = node.child_by_field_name("declarator")
    if (
        holder_node is None or holder_node.type not in _DECLARATION_TYPE_SET
        or holder_node.child_by_field_name("type") is not None
        or name_node is None or name_node.type not in ("identifier", "field_identifier")
    ):
        return False
    body_node = holder_node.parent
    while body_node is not None and body_node.type == "template_declaration":
        body_node = body_node.parent
    class_node = body_node.parent if body_node is not None else None
    if (
        body_node is None or body_node.type != "field_declaration_list"
        or class_node is None or class_node.type not in _TAG_SPECIFIER_TYPE_SET
    ):
        return True
    return _node_text(name_node) not in _extract_body_name_list(class_node)


def _extract_declarator_name_list(node: Node) -> list[str]:
    """Extract the function name from a C/C++ function_declarator (a prototype).

    Target AST structure:
        function_declarator              <- this node is passed as the argument
          +-- declarator: identifier "freeFunction"        <- free function / constructor
          |   or field_identifier "m"                      <- method declared in a class body
          +-- parameters: parameter_list

    Args:
        node: A function_declarator node.

    Returns:
        [name], or empty if the name node is of another type (e.g. a function pointer)
        or the node is a macro call (is_macro_call_declarator).
    """
    if is_macro_call_declarator(node):
        return []
    name = _function_name(node)
    return [name] if name else []


def _extract_init_declarator_name_list(node: Node) -> list[str]:
    """Extract the variable names from a C/C++ variable declaration.

    Target AST structure:
        declaration                      <- this node is passed as the argument
          +-- const (optional)
          +-- primitive_type "int"
          +-- declarator: init_declarator
          |    +-- declarator: identifier "X"  <- extract this
          |    +-- =
          |    +-- number_literal 3
          +-- declarator: pointer_declarator
               +-- declarator: identifier "p"  <- extract this

    A function prototype (void freeFunction();) gives no name here; the caller's BFS
    reaches its function_declarator.

    Args:
        node: A declaration node.

    Returns:
        The variable names.
    """
    return _declarator_name_list(node, ("identifier",))


def _extract_field_declarator_name_list(node: Node) -> list[str]:
    """Extract the member names from a C/C++ field declaration (int *p, q;)."""
    return _declarator_name_list(node, ("field_identifier",))


def _extract_type_declarator_name_list(node: Node) -> list[str]:
    """Extract the type names from a C/C++ typedef.

    typedef struct node { ... } node_t, *node_p;  -> ["node_t", "node_p"]
    typedef int (*cmp_fn)(const void *);          -> ["cmp_fn"]
    typedef struct node { ... } node;             -> [] (the struct is the definition node)

    Args:
        node: A type_definition node.

    Returns:
        The names the typedef declares, without the name of the struct, union or enum
        it is written with when that has a body.
    """
    type_node = node.child_by_field_name("type")
    tag_name_list = (
        _extract_body_name_list(type_node)
        if type_node is not None and type_node.type in _TAG_SPECIFIER_TYPE_SET else []
    )
    name_list: list[str] = []
    for declarator_node in node.children_by_field_name("declarator"):
        name_node = declarator_name_node(declarator_node)
        if name_node is not None and name_node.type == "function_declarator":
            name_node = declarator_name_node(name_node.child_by_field_name("declarator"))
        if (
            name_node is not None and name_node.type in ("type_identifier", "identifier")
            and _node_text(name_node) not in tag_name_list
        ):
            name_list.append(_node_text(name_node))
    return name_list


def _extract_body_name_list(node: Node) -> list[str]:
    """Extract the name from a C/C++ struct, union, enum or class specifier that has a body.

    struct node { ... } gives the name; struct node written as a type (struct node *p)
    and a forward declaration (struct node;) give none.

    Args:
        node: A struct_specifier, union_specifier, enum_specifier or class_specifier node.

    Returns:
        [name], or empty without a body or a name.
    """
    name_node = node.child_by_field_name("name")
    if name_node is None or node.child_by_field_name("body") is None:
        return []
    return [_node_text(name_node)]


def _extract_kotlin_property_name_list(node: Node) -> list[str]:
    """Extract the property names from a Kotlin val / var declaration.

    Target AST structure:
        property_declaration             <- this node is passed as the argument
          +-- val / var
          +-- variable_declaration
          |    +-- identifier "TOP"      <- extract this
          |    +-- (: type)
          +-- =
          +-- (value)

    The same structure is used for top-level constants and for properties
    declared in a class body. A destructuring declaration (val (a, b) = pair) holds
    its variable_declaration nodes in a multi_variable_declaration.

    Args:
        node: A property_declaration node.

    Returns:
        The property names.
    """
    name_list: list[str] = []
    for child in node.children:
        declaration_list = (
            child.children if child.type == "multi_variable_declaration" else [child]
        )
        for declaration_node in declaration_list:
            if declaration_node.type != "variable_declaration":
                continue
            for name_node in declaration_node.children:
                if name_node.type == "identifier":
                    name_list.append(_node_text(name_node))
                    break
    return name_list


def _extract_object_reference_name_list(node: Node) -> list[str]:
    """Extract the object name from a SQL CREATE statement.

    Target AST structure:
        create_table                     <- this node is passed as the argument
          +-- object_reference
          |    +-- schema: identifier "app"   (optional)
          |    +-- name: identifier "items"   <- extract this
          +-- column_definitions

    The same structure applies to create_view, create_materialized_view, create_function,
    create_procedure, create_type, create_sequence and create_trigger.

    Args:
        node: A CREATE statement node.

    Returns:
        [name], or empty if no object_reference is found.
    """
    for child in node.children:
        if child.type == "object_reference":
            name_node = child.child_by_field_name("name")
            return [_node_text(name_node)] if name_node else []
    return []


def _extract_impl_type_name_list(node: Node) -> list[str]:
    """Extract the name of the type an impl block is written for.

    Target AST structure:
        impl_item                        <- this node is passed as the argument
          +-- (trait: type_identifier "Shape")   (impl Shape for Circle)
          +-- type: type_identifier "Circle"      <- extract this
          +-- body: declaration_list

    Generic arguments, a path prefix and a reference are removed:
    impl<T> Wrapper<T> -> "Wrapper", impl fmt::Display for x::Point -> "Point",
    impl<'a> Trait for &'a Point -> "Point". A type that is not a named type
    (impl dyn Shape, impl Trait for (u8, u8)) gives its text.

    Args:
        node: An impl_item node.

    Returns:
        [name], or empty when the node has no type.
    """
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return []
    inner_node: Node | None = type_node
    while inner_node is not None:
        if inner_node.type == "type_identifier":
            return [_node_text(inner_node)]
        if inner_node.type == "scoped_type_identifier":
            inner_node = inner_node.child_by_field_name("name")
        elif inner_node.type in ("generic_type", "reference_type"):
            inner_node = inner_node.child_by_field_name("type")
        else:
            break
    return [" ".join(_node_text(type_node).split())]


def _extract_inline_module_name_list(node: Node) -> list[str]:
    """Extract the module name from a Rust inline module (mod name { ... }).

    Args:
        node: A mod_item node.

    Returns:
        [name], or empty for a declaration without a body.
    """
    if node.child_by_field_name("body") is None:
        return []
    name_node = node.child_by_field_name("name")
    return [_node_text(name_node)] if name_node else []


def _extract_name_field_name_list(node: Node) -> list[str]:
    """Extract the name from the name field of a C# declaration.

    Target AST structure:
        method_declaration               <- this node is passed as the argument
          +-- modifier
          +-- returns: identifier "Order"
          +-- name: identifier "Find"    <- extract this
          +-- parameters: parameter_list

    The same structure applies to the declarations of types, constructors, properties,
    events and enum members. The prefix of a verbatim identifier is removed
    (@class -> class).

    Args:
        node: A declaration node.

    Returns:
        [name], or empty if the node has no name field.
    """
    name_node = node.child_by_field_name("name")
    return [_node_text(name_node).removeprefix("@")] if name_node else []


def _extract_variable_declaration_name_list(node: Node) -> list[str]:
    """Extract the variable names from a C# field declaration.

    Target AST structure:
        field_declaration                <- this node is passed as the argument
          +-- modifier
          +-- variable_declaration
               +-- type: identifier "Order"
               +-- variable_declarator
                    +-- name: identifier "X"  <- extract this

    The same structure applies to event_field_declaration. Every declarator gives its
    name (int a, b;), without the prefix of a verbatim identifier.

    Args:
        node: A field_declaration or event_field_declaration node.

    Returns:
        The variable names.
    """
    for child in node.children:
        if child.type == "variable_declaration":
            return [
                name.removeprefix("@") for name in _extract_variable_declarator_name_list(child)
            ]
    return []


def _extract_unnamed_member_name_list(node: Node) -> list[str]:
    """Name a C# member declaration that has no name of its own.

    public T this[int i] => ...;                  -> "this[]"
    public static Vec operator +(Vec a, Vec b)    -> "operator +"
    public static implicit operator int(Vec v)    -> "operator int"
    ~Vec() {}                                     -> "~Vec"

    Args:
        node: An indexer_declaration, operator_declaration,
            conversion_operator_declaration or destructor_declaration node.

    Returns:
        [name], or empty when the declaration cannot be read.
    """
    if node.type == "indexer_declaration":
        return ["this[]"]
    if node.type == "destructor_declaration":
        name_node = node.child_by_field_name("name")
        return ["~" + _node_text(name_node)] if name_node else []
    if node.type == "conversion_operator_declaration":
        type_node = node.child_by_field_name("type")
        return ["operator " + _node_text(type_node)] if type_node else []
    # operator_declaration: the token written after the operator keyword
    child_list = node.children
    for index, child in enumerate(child_list[:-1]):
        if child.type == "operator":
            return ["operator " + _node_text(child_list[index + 1])]
    return []


# Sentinel value in definition_dict -> function extracting the names from the definition node
_SENTINEL_EXTRACTOR_DICT = {
    # Python: expression_statement > assignment > left-hand identifiers
    "__assignment__": _extract_assignment_name_list,
    # Python: type_alias_statement > left: type > identifier
    "__type_alias__": _extract_type_alias_name_list,
    # JS/TS/Java: lexical_declaration / variable_declaration / field_declaration
    #             > variable_declarator > identifier / pattern
    "__variable_declarator__": _extract_variable_declarator_name_list,
    # JS/TS: expression_statement > assignment_expression > exports.name / module.exports.name /
    #        a member given a function
    "__member_assignment__": _member_assignment_name_list,
    # JS/TS: pair of an object a module exports > key
    "__export_member__": _extract_export_member_name_list,
    # JS/TS: method_definition / field_definition > property_identifier / private_property_identifier
    "__property_name__": _extract_property_name_list,
    # TS: property_identifier directly in an enum_body
    "__enum_member__": _extract_enum_member_name_list,
    # JS/TS: export_statement with default and an unnamed value
    "__default_export__": _default_export_name_list,
    # C/C++: declaration > (init / pointer / array) declarator > identifier
    "__init_declarator__": _extract_init_declarator_name_list,
    # C/C++: field_declaration > (pointer / array) declarator > field_identifier
    "__field_declarator__": _extract_field_declarator_name_list,
    # C/C++: type_definition > declarator > type_identifier
    "__type_declarator__": _extract_type_declarator_name_list,
    # C/C++: struct / union / enum / class specifier with a body > name
    "__body_name__": _extract_body_name_list,
    # C/C++: function_definition > function_declarator > name
    "__function_declarator__": _extract_function_declarator_name_list,
    # C/C++: function_declarator > name (free function) / field_identifier (class member)
    "__declarator_name__": _extract_declarator_name_list,
    # Kotlin: property_declaration > variable_declaration > identifier
    "__kotlin_property__": _extract_kotlin_property_name_list,
    # Kotlin: enum_entry directly in an enum_class_body > identifier
    "__enum_entry__": _extract_enum_entry_name_list,
    # SQL: create_table / create_view / create_function, etc. > object_reference > name: identifier
    "__object_reference__": _extract_object_reference_name_list,
    # Rust: impl_item > type: type_identifier (generic_type / scoped_type_identifier / reference_type)
    "__impl_type__": _extract_impl_type_name_list,
    # Rust: mod_item with a body > name: identifier
    "__inline_module__": _extract_inline_module_name_list,
    # C#: declaration > name: identifier
    "__name_field__": _extract_name_field_name_list,
    # C#: field_declaration / event_field_declaration > variable_declaration
    #     > variable_declarator > identifier
    "__variable_declaration__": _extract_variable_declaration_name_list,
    # C#: indexer / operator / conversion operator / destructor declaration
    "__unnamed_member__": _extract_unnamed_member_name_list,
}
