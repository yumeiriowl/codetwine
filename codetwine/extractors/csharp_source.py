from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.parsers.ts_node import node_text
from codetwine.extractors.definitions import definition_name_list
from codetwine.config.settings import CSHARP_DEFINITION_DICT

# Kinds of a using directive
NAMESPACE_USING = "namespace"   # using A.B;
STATIC_USING = "static"         # using static A.B.Type;
ALIAS_USING = "alias"           # using Name = A.B.Type;

# Kinds of a reference
NAME_REFERENCE = "name"             # a name chain in an expression (Helper.Make, order)
TYPE_REFERENCE = "type"             # a name chain written where a type is (Order, Models.Customer)
ATTRIBUTE_REFERENCE = "attribute"   # the name of an attribute ([Route] -> Route)
THIS_REFERENCE = "this"             # a name chain after "this." (this.list -> list)
MEMBER_REFERENCE = "member"         # the name of a method called on a value (a.b().Run() -> Run)

# AST node types of a type declaration
_TYPE_DECLARATION_TYPE_SET = {
    "class_declaration",
    "struct_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "delegate_declaration",
}

# AST node types of the declarations that can declare type parameters (class C<T>,
# void M<T>(), local functions)
_GENERIC_DECLARATION_TYPE_SET = _TYPE_DECLARATION_TYPE_SET | {
    "method_declaration", "local_function_statement",
}

# AST node types of a namespace declaration
_NAMESPACE_TYPE_SET = {"namespace_declaration", "file_scoped_namespace_declaration"}

# AST node types of the preprocessor directives that hold code
_PREPROC_CODE_TYPE_SET = {"preproc_if", "preproc_elif", "preproc_else"}

# AST node types whose children are declarations of the place the node is written in
_PASS_NODE_TYPE_SET = _PREPROC_CODE_TYPE_SET | {"declaration_list", "ERROR"}

# Field names of a child written where a type is
_TYPE_FIELD_NAME_SET = {"type", "returns"}

# AST node types whose name chain children are written where a type is
_TYPE_PARENT_TYPE_SET = {"base_list", "type_argument_list", "explicit_interface_specifier"}

# AST node types whose "right" child is written where a type is
_TYPE_RIGHT_PARENT_TYPE_SET = {"as_expression", "is_expression"}

# AST node types whose identifier children are labels
_LABEL_PARENT_TYPE_SET = {"labeled_statement", "goto_statement"}

# AST node type -> token written right after the name of a member the node sets or
# matches (new { Name = value }, x with { Name = value }, x is { Name: pattern })
_MEMBER_NAME_TOKEN_DICT = {
    "anonymous_object_creation_expression": "=",
    "with_initializer": "=",
    "subpattern": ":",
}

# Modifiers of a member that is named through its type
_STATIC_MODIFIER_TUPLE = ("static", "const")

# Prefix of the AST node types of preprocessor directives
_PREPROC_PREFIX = "preproc_"


@dataclass
class CsharpUsing:
    """One using directive."""

    kind: str                     # NAMESPACE_USING / STATIC_USING / ALIAS_USING
    part_tuple: tuple[str, ...]   # Parts of the namespace or type name (A.B.Type -> A, B, Type)
    alias: str = ""               # Name of an ALIAS_USING; "" for the other kinds
    is_global: bool = False       # Whether the directive is written with "global"
    arity: int = 0                # Number of type arguments of the last part (A.Result<int> -> 1)


@dataclass
class CsharpScope:
    """A place using directives are written in: the file, or one namespace declaration."""

    namespace: str      # Full name of the namespace; "" for the file
    start_line: int     # First line (1-based)
    end_line: int       # Last line
    depth: int          # Number of scopes around it
    using_list: list[CsharpUsing] = field(default_factory=list)


@dataclass
class CsharpMember:
    """One declaration of a member of a type."""

    start_line: int     # First line (1-based)
    # Whether the member is named through the type: a static or const member, an enum member
    is_static: bool = False
    # (least, most) number of arguments a call of a method takes; most is None for a
    # declaration with "params"; None for a member that is not a method
    argument_range: tuple[int, int | None] | None = None
    is_extension: bool = False  # Whether the first parameter is written with "this"
    is_constructor: bool = False  # Whether the declaration is a constructor
    # Type name of each parameter of a method (_type_name); None for a type parameter and
    # for a type without a name. Empty for a member that is not a method
    parameter_type_tuple: tuple[str | None, ...] = ()


@dataclass
class CsharpType:
    """One type declaration."""

    namespace: str      # Full name of the namespace the type is in; "" for none
    path: str           # Name of the type, after the names of the types around it (Outer.Inner)
    arity: int          # Number of type parameters
    start_line: int     # First line (1-based)
    end_line: int       # Last line
    # Names of the definitions written in the body, types included
    member_name_set: set[str] = field(default_factory=set)
    # Name of a member that is not a type -> its declarations in line order
    member_list_dict: dict[str, list[CsharpMember]] = field(default_factory=dict)


@dataclass
class CsharpDeclaration:
    """The namespaces, using directives and types of one C# file."""

    # The file itself, then each namespace declaration in line order
    scope_list: list[CsharpScope] = field(default_factory=list)
    # Type declarations in line order
    type_list: list[CsharpType] = field(default_factory=list)


# Node types whose parameters and local variables are names of their own
_LOCAL_SCOPE_TYPE_SET = {
    "method_declaration", "constructor_declaration", "destructor_declaration",
    "operator_declaration", "conversion_operator_declaration", "indexer_declaration",
    "accessor_declaration", "local_function_statement", "lambda_expression",
    "anonymous_method_expression",
}

# Name of a type of System -> the keyword the type is also written with
_TYPE_KEYWORD_DICT = {
    "Boolean": "bool", "Byte": "byte", "SByte": "sbyte", "Char": "char",
    "Decimal": "decimal", "Double": "double", "Single": "float", "Int16": "short",
    "UInt16": "ushort", "Int32": "int", "UInt32": "uint", "Int64": "long",
    "UInt64": "ulong", "IntPtr": "nint", "UIntPtr": "nuint", "Object": "object",
    "String": "string",
}

# Node type of a literal -> the type name of its value
_LITERAL_TYPE_DICT = {
    "string_literal": "string", "verbatim_string_literal": "string",
    "raw_string_literal": "string", "interpolated_string_expression": "string",
    "character_literal": "char", "boolean_literal": "bool",
}

# Suffix of a number literal (lower case) -> the type name of its value
_NUMBER_SUFFIX_TYPE_DICT = {
    "l": "long", "u": "uint", "ul": "ulong", "lu": "ulong",
    "f": "float", "d": "double", "m": "decimal",
}

# Prefix of an integer literal (lower case) -> the digits written after it
_INTEGER_PREFIX_DIGIT_DICT = {"0x": "0123456789abcdef_", "0b": "01_"}

# Digits of a decimal integer literal
_DECIMAL_DIGIT = "0123456789_"

# Node types that declare a local name in their field "name" (a parameter list: the
# parameter written with "params")
_LOCAL_DECLARE_TYPE_SET = {
    "parameter", "variable_declarator", "catch_declaration", "declaration_pattern",
    "declaration_expression", "local_function_statement", "from_clause", "join_clause",
    "tuple_pattern", "parameter_list", "bracketed_parameter_list",
}


@dataclass(frozen=True)
class CsharpReference:
    """One name chain of a C# file that is not the name of a declaration."""

    kind: str                       # NAME_REFERENCE / TYPE_REFERENCE / ...
    part_tuple: tuple[str, ...]     # Names of the chain (Helper.Make<T> -> Helper, Make)
    arity_tuple: tuple[int, ...]    # Number of type arguments of each name
    line: int                       # Line the chain starts on (1-based)
    is_call: bool = False           # Whether the chain is what an invocation calls
    is_absolute: bool = False       # Whether the chain starts with "global::"
    argument_count: int = 0         # Number of arguments of the invocation; 0 without is_call
    # Type name of each argument of the invocation where the file tells it
    # (_ReferenceWalker._argument_type_tuple), None where it does not
    argument_type_tuple: tuple[str | None, ...] = ()


@dataclass
class _Chain:
    """A name chain read from the tree."""

    part_list: list[str] = field(default_factory=list)
    arity_list: list[int] = field(default_factory=list)
    # What the chain is written after: "" (nothing), "this", "base", "global",
    # "builtin" (a type written with a keyword: string, int), or "value" (any other
    # expression that is not a name chain)
    head: str = ""
    # Nodes inside the chain that hold references of their own
    inner_node_list: list[Node] = field(default_factory=list)


def _name_text(node: Node) -> str:
    """Return the text of a name node without the verbatim prefix (@class -> class)."""
    return node_text(node).removeprefix("@")


def _root_node(node: Node) -> Node:
    """Return the root node of the tree a node is in."""
    while node.parent is not None:
        node = node.parent
    return node


def _has_modifier(node: Node, modifier: str) -> bool:
    """Return whether a declaration or parameter node is written with a modifier."""
    return any(
        child.type == "modifier" and node_text(child) == modifier
        for child in node.children
    )


def _type_parameter_name_list(node: Node) -> list[str]:
    """Return the names of the type parameters a declaration node declares.

    A node whose type is not in _GENERIC_DECLARATION_TYPE_SET gives [].
    """
    if node.type not in _GENERIC_DECLARATION_TYPE_SET:
        return []
    type_parameter_list_node = next(
        (child for child in node.children if child.type == "type_parameter_list"), None,
    )
    if type_parameter_list_node is None:
        return []
    name_list: list[str] = []
    for child in type_parameter_list_node.children:
        if child.type == "type_parameter":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                name_list.append(_name_text(name_node))
    return name_list


def _name_field_node_list(node: Node) -> list[Node]:
    """Return the identifiers in the field "name" of a node."""
    return [
        name_node for name_node in node.children_by_field_name("name")
        if name_node.type == "identifier"
    ]


def _left_identifier_node_list(node: Node) -> list[Node]:
    """Return the loop variable of a foreach statement when it is one identifier."""
    left_node = node.child_by_field_name("left")
    return [left_node] if left_node is not None and left_node.type == "identifier" else []


def _node_itself_list(node: Node) -> list[Node]:
    """Return a node that is itself the name it declares (the parameter of x => ...)."""
    return [node]


def _first_identifier_node_list(node: Node) -> list[Node]:
    """Return the first identifier child of a node (the range variable of a let clause)."""
    return [child for child in node.children if child.type == "identifier"][:1]


# AST node type -> the function that returns the name nodes of the local names a node
# of the type declares
_LOCAL_NAME_READER_DICT = {
    **{node_type: _name_field_node_list for node_type in _LOCAL_DECLARE_TYPE_SET},
    "implicit_parameter": _node_itself_list,
    "foreach_statement": _left_identifier_node_list,
    "let_clause": _first_identifier_node_list,
}


def _local_name_list(node: Node) -> list[str]:
    """Return the names declared inside a method, accessor, local function or lambda.

    These are its parameters, local variables, loop and catch variables, pattern and
    out variables, local functions, and the range variables of a query.

    Args:
        node: A node whose type is in _LOCAL_SCOPE_TYPE_SET.

    Returns:
        The names, those of the lambdas and local functions inside it included.
    """
    name_list: list[str] = []
    node_stack = [node]
    while node_stack:
        current = node_stack.pop()
        reader = _LOCAL_NAME_READER_DICT.get(current.type)
        if reader is not None and current is not node:
            name_list.extend(_name_text(name_node) for name_node in reader(current))
        node_stack.extend(current.children)
    return name_list


def _type_argument_count(node: Node) -> int:
    """Return the number of type arguments of a type_argument_list node.

    Examples:
        <Order>      -> 1
        <string, T>  -> 2
        <,>          -> 2
    """
    return sum(1 for child in node.children if child.type == ",") + 1


def _read_identifier_chain(node: Node, chain: _Chain) -> None:
    """Add the name of an identifier node to a chain."""
    chain.part_list.append(_name_text(node))
    chain.arity_list.append(0)


def _read_generic_name_chain(node: Node, chain: _Chain) -> None:
    """Add the name of a generic_name node and its number of type arguments to a chain."""
    for child in node.children:
        if child.type == "identifier":
            chain.part_list.append(_name_text(child))
        elif child.type == "type_argument_list":
            chain.arity_list.append(_type_argument_count(child))
            chain.inner_node_list.append(child)
    if len(chain.arity_list) < len(chain.part_list):
        chain.arity_list.append(0)


def _read_alias_qualified_name_chain(node: Node, chain: _Chain) -> None:
    """Add the alias and the name of an alias_qualified_name node to a chain.

    The alias "global" is the head "global" instead of a name.
    """
    alias_node = node.child_by_field_name("alias")
    name_node = node.child_by_field_name("name")
    if alias_node is not None and _name_text(alias_node) == "global":
        chain.head = "global"
    elif alias_node is not None:
        chain.part_list.append(_name_text(alias_node))
        chain.arity_list.append(0)
    if name_node is not None:
        _append_chain(chain, _read_chain(name_node))


def _read_qualified_name_chain(node: Node, chain: _Chain) -> None:
    """Add the names of the qualifier and the name of a qualified_name node to a chain."""
    for field_name in ("qualifier", "name"):
        part_node = node.child_by_field_name(field_name)
        if part_node is not None and part_node.type in _CHAIN_NODE_TYPE_SET:
            _append_chain(chain, _read_chain(part_node))


def _read_member_access_chain(node: Node, chain: _Chain) -> None:
    """Add the expression and the name of a member_access_expression node to a chain.

    An expression that is not a name chain gives the head of the chain: "this",
    "base", "builtin" (predefined_type) or "value" (kept in inner_node_list).
    """
    expression_node = node.child_by_field_name("expression")
    name_node = node.child_by_field_name("name")
    if expression_node is None:
        chain.head = "value"
    elif expression_node.type in ("this", "base"):
        chain.head = expression_node.type
    elif expression_node.type == "predefined_type":
        chain.head = "builtin"
    elif expression_node.type in _CHAIN_NODE_TYPE_SET:
        _append_chain(chain, _read_chain(expression_node))
    else:
        chain.head = "value"
        chain.inner_node_list.append(expression_node)
    if name_node is not None and name_node.type in _CHAIN_NODE_TYPE_SET:
        _append_chain(chain, _read_chain(name_node))


# AST node type of a name chain -> the function that adds its names to a chain
_CHAIN_READER_DICT = {
    "identifier": _read_identifier_chain,
    "generic_name": _read_generic_name_chain,
    "alias_qualified_name": _read_alias_qualified_name_chain,
    "qualified_name": _read_qualified_name_chain,
    "member_access_expression": _read_member_access_chain,
}

# AST node types of a name chain
_CHAIN_NODE_TYPE_SET = set(_CHAIN_READER_DICT)


def _read_chain(node: Node) -> _Chain:
    """Read a name chain node into its names.

    Examples:
        Helper.Make<Order>          -> parts Helper, Make; arities 0, 1
        global::App.Util.Run        -> head "global"; parts App, Util, Run
        this.list.Count             -> head "this"; parts list, Count
        repo.Find(id).Total         -> head "value"; parts Total

    Args:
        node: A node whose type is in _CHAIN_NODE_TYPE_SET.

    Returns:
        The _Chain. The type argument lists of the chain, and the expression a chain
        with the head "value" is written after, are in inner_node_list.
    """
    chain = _Chain()
    reader = _CHAIN_READER_DICT.get(node.type)
    if reader is not None:
        reader(node, chain)
    return chain


def _append_chain(chain: _Chain, part_chain: _Chain) -> None:
    """Add the names of a part of a chain to the chain; the head of the first part is kept."""
    if not chain.part_list and not chain.head:
        chain.head = part_chain.head
    chain.part_list.extend(part_chain.part_list)
    chain.arity_list.extend(part_chain.arity_list)
    chain.inner_node_list.extend(part_chain.inner_node_list)


def _using_directive(node: Node) -> CsharpUsing | None:
    """Read a using_directive node.

    Examples:
        using System.Text;                  -> NAMESPACE_USING, (System, Text)
        global using static App.MathEx;     -> STATIC_USING, (App, MathEx), is_global
        using Json = App.Text.Serializer;   -> ALIAS_USING, (App, Text, Serializer), alias Json
        using Ints = App.Result<int>;       -> ALIAS_USING, (App, Result), alias Ints, arity 1

    Args:
        node: A using_directive node.

    Returns:
        The CsharpUsing. None when the directive names no namespace or type by a name
        chain (a tuple type, an array type, etc.).
    """
    alias_node = node.child_by_field_name("name")
    target_node = next(
        (
            child for child in node.children
            if child.type in _CHAIN_NODE_TYPE_SET
            and (alias_node is None or child.id != alias_node.id)
        ),
        None,
    )
    if target_node is None:
        return None
    chain = _read_chain(target_node)
    if not chain.part_list or chain.head not in ("", "global"):
        return None

    child_type_set = {child.type for child in node.children}
    if alias_node is not None:
        kind = ALIAS_USING
    elif "static" in child_type_set:
        kind = STATIC_USING
    else:
        kind = NAMESPACE_USING
    return CsharpUsing(
        kind=kind,
        part_tuple=tuple(chain.part_list),
        alias=_name_text(alias_node) if alias_node is not None else "",
        is_global="global" in child_type_set,
        arity=chain.arity_list[-1],
    )


def join_name(*part_tuple: str) -> str:
    """Join the parts of a dotted name, leaving out the empty ones.

    Examples:
        ("App.Models", "Customer")  -> "App.Models.Customer"
        ("", "Customer")            -> "Customer"
    """
    return ".".join(part for part in part_tuple if part)


def _nullable_inner_node(node: Node) -> Node | None:
    """Return the type a nullable type is written with (int? -> int)."""
    return node.child_by_field_name("type")


def _generic_inner_node(node: Node) -> Node | None:
    """Return the name of a generic type without its type arguments (List<T> -> List)."""
    return next(iter(node.named_children), None)


def _qualified_inner_node(node: Node) -> Node | None:
    """Return the last name of a qualified type name (App.Models.Order -> Order)."""
    return node.child_by_field_name("name")


# AST node type of a type -> the function that returns the node its name is read from
_TYPE_INNER_NODE_DICT = {
    "nullable_type": _nullable_inner_node,
    "generic_name": _generic_inner_node,
    "qualified_name": _qualified_inner_node,
}


def _type_name(type_node: Node | None) -> str | None:
    """Return the name a type is compared by: its last name, without type arguments.

    A type of System written by its name is named with its keyword (_TYPE_KEYWORD_DICT).

    Examples:
        int               -> "int"
        int?              -> "int"
        List<Customer>    -> "List"
        App.Models.Order  -> "Order"
        System.String     -> "string"
        string[]          -> None

    Args:
        type_node: A type node, or None.

    Returns:
        The name; None for an array, a tuple, a pointer, "var" and no node.
    """
    current = type_node
    while current is not None:
        if current.type in ("predefined_type", "identifier"):
            name = _name_text(current)
            return _TYPE_KEYWORD_DICT.get(name, name)
        inner_reader = _TYPE_INNER_NODE_DICT.get(current.type)
        if inner_reader is None:
            return None
        current = inner_reader(current)
    return None


def _type_parameter_name_set(node: Node) -> set[str]:
    """Return the names of the type parameters a declaration and the types around it declare."""
    name_set: set[str] = set()
    current: Node | None = node
    while current is not None:
        name_set.update(_type_parameter_name_list(current))
        current = current.parent
    return name_set


def _parameter_type_tuple(node: Node) -> tuple[str | None, ...]:
    """Return the type name of each parameter of a method_declaration node (_type_name).

    A parameter whose type is a type parameter of the method or of a type around it
    gives None.
    """
    type_parameter_name_set = _type_parameter_name_set(node)
    type_name_list: list[str | None] = []
    for parameter in _parameter_node_list(node):
        name = _type_name(parameter.child_by_field_name("type"))
        type_name_list.append(None if name in type_parameter_name_set else name)
    return tuple(type_name_list)


def _variable_type_dict(node: Node) -> dict[str, str | None]:
    """Return the variables a variable_declaration node declares, with their types.

    Examples:
        Box a, b = null;      -> {"a": "Box", "b": "Box"}
        var c = new Box(1);   -> {"c": "Box"}
        var d = Make();       -> {"d": None}

    Args:
        node: A variable_declaration node.

    Returns:
        A {name: type name (_type_name)} dict. A variable written without a type name
        has the type of the object its declaration makes, None when it makes none.
    """
    declare_type_name = _type_name(node.child_by_field_name("type"))
    variable_type_dict: dict[str, str | None] = {}
    for declarator in node.named_children:
        name_node = declarator.child_by_field_name("name")
        if declarator.type != "variable_declarator" or name_node is None:
            continue
        type_name = declare_type_name
        if type_name is None:
            type_name = next(
                (
                    _type_name(child.child_by_field_name("type"))
                    for child in declarator.named_children
                    if child.type == "object_creation_expression"
                ),
                None,
            )
        variable_type_dict.setdefault(_name_text(name_node), type_name)
    return variable_type_dict


def _parameter_type_dict(node: Node) -> dict[str, str | None]:
    """Return the parameters of a node in _LOCAL_SCOPE_TYPE_SET, with their types.

    Args:
        node: A method, accessor, indexer, local function, lambda or anonymous method,
            or a type declaration, whose primary constructor (record Rec(Box B)) is read.

    Returns:
        A {name: type name (_type_name)} dict, the parameter written with "params"
        and the parameter of a lambda written without parentheses (x => ...) included.
    """
    parameter_list_node = node.child_by_field_name("parameters")
    if parameter_list_node is None and node.type in _TYPE_DECLARATION_TYPE_SET:
        parameter_list_node = next(
            (child for child in node.children if child.type == "parameter_list"), None,
        )
    if parameter_list_node is None:
        return {}
    if parameter_list_node.type == "implicit_parameter":
        return {_name_text(parameter_list_node): None}
    parameter_node_list = [
        child for child in parameter_list_node.children if child.type == "parameter"
    ]
    # The parameter written with "params" is in the fields of the list itself
    parameter_node_list.append(parameter_list_node)
    parameter_type_dict: dict[str, str | None] = {}
    for parameter in parameter_node_list:
        name_node = parameter.child_by_field_name("name")
        if name_node is not None:
            parameter_type_dict.setdefault(
                _name_text(name_node), _type_name(parameter.child_by_field_name("type")),
            )
    return parameter_type_dict


def _local_declare_dict(node: Node) -> dict[str, str | None]:
    """Return the local names a node declares for the code inside it, with their types.

    These are the parameters of a node in _LOCAL_SCOPE_TYPE_SET, the variables of the
    declarations among the children of a block or statement (Box b = ...;,
    for (Box i = ...;;), using (Box r = ...)), the typed loop variable of a foreach
    statement and the variable of a catch clause.

    Args:
        node: A node inside a member of a type.

    Returns:
        A {name: type name (_type_name)} dict; empty for a node that declares none.
    """
    declare_dict: dict[str, str | None] = {}
    if node.type in _LOCAL_SCOPE_TYPE_SET:
        declare_dict.update(_parameter_type_dict(node))
    if node.type == "foreach_statement":
        for name_node in _left_identifier_node_list(node):
            declare_dict.setdefault(
                _name_text(name_node), _type_name(node.child_by_field_name("type")),
            )
    for child in node.children:
        if child.type == "local_declaration_statement":
            child = next(
                (inner for inner in child.children if inner.type == "variable_declaration"),
                child,
            )
        if child.type == "variable_declaration":
            for name, type_name in _variable_type_dict(child).items():
                declare_dict.setdefault(name, type_name)
        elif child.type == "catch_declaration":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                declare_dict.setdefault(
                    _name_text(name_node), _type_name(child.child_by_field_name("type")),
                )
    return declare_dict


def _member_declare_dict(node: Node) -> dict[str, str | None]:
    """Return the fields and properties written in the body of a type, with their types.

    The members inside #if / #elif / #else are read; the members of the types nested in
    the body are not. For the body of a type declaration, the parameters of its primary
    constructor (record Rec(Box B), class Prim(Box pc)) are added after the members.

    Args:
        node: The declaration_list node of a type declaration, or a node of
            _PASS_NODE_TYPE_SET inside it.

    Returns:
        A {name: type name (_type_name)} dict; the first declaration of a name is kept.
    """
    declare_dict: dict[str, str | None] = {}
    for child in node.children:
        if child.type in _PASS_NODE_TYPE_SET:
            child_declare_dict = _member_declare_dict(child)
        elif child.type == "field_declaration":
            child_declare_dict = {}
            for inner in child.children:
                if inner.type == "variable_declaration":
                    child_declare_dict = _variable_type_dict(inner)
        elif child.type == "property_declaration":
            name_node = child.child_by_field_name("name")
            child_declare_dict = {} if name_node is None else {
                _name_text(name_node): _type_name(child.child_by_field_name("type")),
            }
        else:
            continue
        for name, type_name in child_declare_dict.items():
            declare_dict.setdefault(name, type_name)
    if _is_type_body(node):
        for name, type_name in _parameter_type_dict(node.parent).items():
            declare_dict.setdefault(name, type_name)
    return declare_dict


def _is_type_body(node: Node) -> bool:
    """Return whether a node is the body of a type declaration."""
    return (
        node.type == "declaration_list"
        and node.parent is not None
        and node.parent.type in _TYPE_DECLARATION_TYPE_SET
    )


def _argument_value_node(node: Node) -> Node | None:
    """Return the expression an argument node passes (the value after the name of a named argument)."""
    if node.child_by_field_name("name") is not None and node.named_child_count >= 2:
        return node.named_children[-1]
    return next(iter(node.named_children), None)


def _integer_literal_type(text: str) -> str:
    """Return the type name of the value of an integer literal by its suffix.

    Examples:
        "10" / "10L" / "0x10UL" / "0b1u"  -> "int" / "long" / "ulong" / "uint"

    Args:
        text: Text of an integer_literal node.

    Returns:
        The type name; "int" without a suffix.
    """
    body = text.lower()
    digit = _DECIMAL_DIGIT
    for prefix, prefix_digit in _INTEGER_PREFIX_DIGIT_DICT.items():
        if body.startswith(prefix):
            body, digit = body[len(prefix):], prefix_digit
            break
    return _NUMBER_SUFFIX_TYPE_DICT.get(body.lstrip(digit), "int")


def _expression_type(node: Node | None) -> str | None:
    """Return the type name of the value of a literal, an object creation or a cast.

    Examples:
        1 / 2L / 0xFFu / 2.5 / 2.5f / 1m  -> "int" / "long" / "uint" / "double" / "float" / "decimal"
        "a" / 'a' / true                  -> "string" / "char" / "bool"
        new Box(1)                        -> "Box"
        (long)value                       -> "long"

    Args:
        node: An expression node, or None.

    Returns:
        The type name; None for any other expression, for null and for no node.
    """
    if node is None:
        return None
    literal_type = _LITERAL_TYPE_DICT.get(node.type)
    if literal_type is not None:
        return literal_type
    if node.type == "integer_literal":
        return _integer_literal_type(node_text(node))
    if node.type == "real_literal":
        return _NUMBER_SUFFIX_TYPE_DICT.get(node_text(node).lower()[-1], "double")
    if node.type in ("object_creation_expression", "cast_expression"):
        return _type_name(node.child_by_field_name("type"))
    return None


def _parameter_node_list(node: Node) -> list[Node]:
    """Return the parameter nodes of a method_declaration node, without a "params" one."""
    parameter_list_node = node.child_by_field_name("parameters")
    if parameter_list_node is None:
        return []
    return [child for child in parameter_list_node.children if child.type == "parameter"]


def _argument_range(node: Node) -> tuple[int, int | None] | None:
    """Return the number of arguments a call of a method takes.

    Examples:
        Twice(int v)                              -> (1, 1)
        Pad(string s, int width, char fill = ' ') -> (2, 3)
        Join(string s, params object[] rest)      -> (1, None)

    Args:
        node: A method_declaration node.

    Returns:
        (least, most) number of arguments; most is None for a declaration with
        "params". None when the declaration has no parameter list.
    """
    parameter_list_node = node.child_by_field_name("parameters")
    if parameter_list_node is None:
        return None
    parameter_node_list = _parameter_node_list(node)
    least = sum(
        1 for parameter in parameter_node_list
        if not any(child.type == "=" for child in parameter.children)
    )
    has_params = any(child.type == "params" for child in parameter_list_node.children)
    return least, None if has_params else len(parameter_node_list)


def _is_extension(node: Node) -> bool:
    """Return whether the first parameter of a method_declaration node is written with "this"."""
    parameter_node_list = _parameter_node_list(node)
    return bool(parameter_node_list) and _has_modifier(parameter_node_list[0], "this")


def _read_type(
    node: Node, scope: CsharpScope, owner: CsharpType | None, declaration: CsharpDeclaration,
) -> None:
    """Add a type declaration and the types written in its body to a CsharpDeclaration.

    Args:
        node: A node whose type is in _TYPE_DECLARATION_TYPE_SET.
        scope: The scope the declaration is written in.
        owner: The type the declaration is written in the body of; None for none.
        declaration: The CsharpDeclaration of the file; modified in place.
    """
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _name_text(name_node)
    csharp_type = CsharpType(
        namespace=scope.namespace,
        path=join_name(owner.path if owner else "", name),
        arity=len(_type_parameter_name_list(node)),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )
    if owner is not None:
        owner.member_name_set.add(name)
    declaration.type_list.append(csharp_type)
    body_node = node.child_by_field_name("body")
    if body_node is not None:
        _read_declaration_list(body_node, scope, csharp_type, declaration)


def _read_declaration_list(
    node: Node, scope: CsharpScope, owner: CsharpType | None, declaration: CsharpDeclaration,
) -> CsharpScope:
    """Read the declarations among the children of a node into a CsharpDeclaration.

    A file scoped namespace declaration (namespace A.B;) starts a scope that holds the
    declarations after it, up to the last line of the file.

    Args:
        node: The root node, the body of a namespace or type declaration, or a node
            whose type is in _PASS_NODE_TYPE_SET.
        scope: The scope the node is written in.
        owner: The type whose body the node is; None outside a type.
        declaration: The CsharpDeclaration of the file; modified in place.

    Returns:
        The scope at the end of the node.
    """
    for child in node.children:
        if child.type == "using_directive":
            using = _using_directive(child)
            if using is not None:
                scope.using_list.append(using)
        elif child.type in _NAMESPACE_TYPE_SET:
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            is_file_scope = child.type == "file_scoped_namespace_declaration"
            inner_scope = CsharpScope(
                namespace=join_name(scope.namespace, *_read_chain(name_node).part_list),
                start_line=child.start_point[0] + 1,
                end_line=(_root_node(node) if is_file_scope else child).end_point[0] + 1,
                depth=scope.depth + 1,
            )
            declaration.scope_list.append(inner_scope)
            if is_file_scope:
                scope = _read_declaration_list(child, inner_scope, owner, declaration)
            else:
                body_node = child.child_by_field_name("body")
                if body_node is not None:
                    _read_declaration_list(body_node, inner_scope, owner, declaration)
        elif child.type in _TYPE_DECLARATION_TYPE_SET:
            _read_type(child, scope, owner, declaration)
        elif child.type in _PASS_NODE_TYPE_SET:
            scope = _read_declaration_list(child, scope, owner, declaration)
        elif owner is not None and child.type in CSHARP_DEFINITION_DICT:
            _add_member(owner, child)
    return scope


def _member_name_list(node: Node) -> list[str]:
    """Return the names a member declaration gives, without the verbatim prefix.

    Examples:
        int a, b = 1;             -> ["a", "b"]
        public void @event() {}   -> ["event"]

    Args:
        node: A node whose type is in CSHARP_DEFINITION_DICT.

    Returns:
        Every variable of a declaration of fields; the name of any other declaration.
        Empty when the declaration has no name.
    """
    return definition_name_list(node, CSHARP_DEFINITION_DICT)


def _add_member(owner: CsharpType, node: Node) -> None:
    """Add the names a member declaration gives to its type, each with the declaration.

    Args:
        owner: The type whose body the declaration is written in; modified in place.
        node: A node whose type is in CSHARP_DEFINITION_DICT.
    """
    member = CsharpMember(
        start_line=node.start_point[0] + 1,
        is_static=node.type == "enum_member_declaration" or any(
            _has_modifier(node, modifier) for modifier in _STATIC_MODIFIER_TUPLE
        ),
    )
    member.is_constructor = node.type == "constructor_declaration"
    if node.type == "method_declaration":
        member.argument_range = _argument_range(node)
        member.is_extension = _is_extension(node)
        member.parameter_type_tuple = _parameter_type_tuple(node)
    for name in _member_name_list(node):
        owner.member_name_set.add(name)
        owner.member_list_dict.setdefault(name, []).append(member)


def read_csharp_declaration(root_node: Node) -> CsharpDeclaration:
    """Read the namespaces, using directives and types of a C# file.

    Declarations inside #if / #elif / #else are read as the ones outside.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The CsharpDeclaration. scope_list[0] is the file itself.
    """
    declaration = CsharpDeclaration()
    file_scope = CsharpScope(
        namespace="", start_line=1, end_line=root_node.end_point[0] + 1, depth=0,
    )
    declaration.scope_list.append(file_scope)
    _read_declaration_list(root_node, file_scope, None, declaration)
    return declaration


def _is_type_position(node: Node, field_name: str | None) -> bool:
    """Return whether a name chain node is written where a type is.

    Args:
        node: A node whose type is in _CHAIN_NODE_TYPE_SET.
        field_name: Name of the field the node is in its parent; None for none.

    Returns:
        True for a qualified name (A.B, M::B), a child in a field of
        _TYPE_FIELD_NAME_SET, a child of a node in _TYPE_PARENT_TYPE_SET, and the right
        side of "as" / "is".
    """
    if node.type in ("qualified_name", "alias_qualified_name"):
        return True
    if field_name in _TYPE_FIELD_NAME_SET:
        return True
    parent = node.parent
    if parent is None:
        return False
    if parent.type in _TYPE_PARENT_TYPE_SET:
        return True
    return parent.type in _TYPE_RIGHT_PARENT_TYPE_SET and field_name == "right"


def _chain_reference(
    chain: _Chain,
    kind: str,
    line: int,
    argument_type_tuple: tuple[str | None, ...] | None = None,
) -> CsharpReference | None:
    """Return the reference of a name chain.

    Args:
        chain: The chain.
        kind: NAME_REFERENCE, TYPE_REFERENCE or ATTRIBUTE_REFERENCE; the kind of a
            chain with the head "" or "global".
        line: Line the chain starts on (1-based).
        argument_type_tuple: Type name of each argument of the invocation that calls
            the chain, None where the file does not tell it; None when the chain is
            not called.

    Returns:
        The reference. A chain after "this." is a THIS_REFERENCE. A chain after a value
        is a MEMBER_REFERENCE of its last name when it is a call. None for a chain
        without a name, a chain after "base." or after a type written with a keyword,
        and a chain after a value that is not a call.
    """
    if not chain.part_list or chain.head in ("base", "builtin"):
        return None
    is_call = argument_type_tuple is not None
    if argument_type_tuple is None:
        argument_type_tuple = ()
    argument_count = len(argument_type_tuple)
    if chain.head == "value":
        if not is_call:
            return None
        return CsharpReference(
            MEMBER_REFERENCE, (chain.part_list[-1],), (chain.arity_list[-1],), line, True,
            argument_count=argument_count, argument_type_tuple=argument_type_tuple,
        )
    if chain.head == "this":
        kind = THIS_REFERENCE
    return CsharpReference(
        kind, tuple(chain.part_list), tuple(chain.arity_list), line, is_call,
        chain.head == "global", argument_count, argument_type_tuple,
    )


def _local_value_reference(reference: CsharpReference) -> CsharpReference | None:
    """Turn a chain that starts with a local name into the member it calls on that value.

    Examples (items is a local variable):
        items.Where(x => ...)   -> MEMBER_REFERENCE Where
        items.Count             -> None
        items                   -> None

    Args:
        reference: A reference whose first name is a local name.

    Returns:
        A MEMBER_REFERENCE of the last name when the chain is a call with two or more
        names, else None.
    """
    if not reference.is_call or len(reference.part_tuple) < 2:
        return None
    return CsharpReference(
        MEMBER_REFERENCE, (reference.part_tuple[-1],), (reference.arity_tuple[-1],),
        reference.line, True, argument_count=reference.argument_count,
        argument_type_tuple=reference.argument_type_tuple,
    )


def _declare_name_id(node: Node) -> int | None:
    """Return the id of the child of a node that is a name the node declares or sets.

    Args:
        node: A node whose type is not in _CHAIN_NODE_TYPE_SET.

    Returns:
        The id of the loop variable of a foreach statement, or of the member name on
        the left side of an assignment inside an initializer. None when the node has
        none of them.
    """
    if node.type == "foreach_statement":
        left_node = node.child_by_field_name("left")
        return left_node.id if left_node is not None else None
    if (
        node.type == "assignment_expression"
        and node.parent is not None
        and node.parent.type == "initializer_expression"
    ):
        left_node = node.child_by_field_name("left")
        if left_node is not None and left_node.type == "identifier":
            return left_node.id
    return None


def _is_member_name(node: Node, child_index: int) -> bool:
    """Return whether a child of a node is the name of a member the node sets or matches.

    Examples:
        new { Name = value }        -> Name
        x with { Name = value }     -> Name
        x is { Customer.Name: "a" } -> Customer.Name

    Args:
        node: A node.
        child_index: Index of the child among node.children.

    Returns:
        True for the child right before the token _MEMBER_NAME_TOKEN_DICT gives for
        the type of the node.
    """
    token = _MEMBER_NAME_TOKEN_DICT.get(node.type)
    if token is None or child_index + 1 >= len(node.children):
        return False
    return node.children[child_index + 1].type == token


def _call_member_chain(function_node: Node) -> _Chain | None:
    """Read the member an invocation calls through a conditional access (a?.Run()).

    Args:
        function_node: The node an invocation calls; its type is not in
            _CHAIN_NODE_TYPE_SET.

    Returns:
        The chain of the member name, with the head "value". None when the node is
        not a conditional access that ends with a member.
    """
    member_node = function_node
    if function_node.type == "conditional_access_expression" and function_node.named_children:
        member_node = function_node.named_children[-1]
    if member_node.type != "member_binding_expression":
        return None
    name_node = member_node.child_by_field_name("name")
    if name_node is None or name_node.type not in _CHAIN_NODE_TYPE_SET:
        return None
    chain = _read_chain(name_node)
    chain.head = "value"
    return chain


class _ReferenceWalker:
    """Walks the tree of a C# file and collects its references (csharp_reference_list)."""

    def __init__(self) -> None:
        """Start with no references and an empty stack."""
        self.reference_dict: dict[CsharpReference, None] = {}
        # (node, name of the field the node is in its parent, names of the type
        # parameters and local names of the declarations around the node)
        self.node_stack: list[tuple[Node, str | None, frozenset[str]]] = []
        # id of a node -> the names its declarations give, with their types
        # (_local_declare_dict, _member_declare_dict)
        self.scope_declare_dict: dict[int, dict[str, str | None]] = {}
        # AST node type -> the method that visits a node of the type
        self.visit_dict = {
            "using_directive": self._visit_using,
            "attribute": self._visit_attribute,
            "invocation_expression": self._visit_invocation,
        }

    def walk(self, root_node: Node) -> list[CsharpReference]:
        """Visit every node under root_node and return the references found.

        Args:
            root_node: The AST root node of the file.

        Returns:
            The references in the order they are found, without duplicates.
        """
        self.node_stack.append((root_node, None, frozenset()))
        while self.node_stack:
            node, field_name, local_name_set = self.node_stack.pop()
            if node.type in _CHAIN_NODE_TYPE_SET:
                kind = TYPE_REFERENCE if _is_type_position(node, field_name) else NAME_REFERENCE
                self._add_chain(_read_chain(node), kind, node, local_name_set)
            elif node.type.startswith(_PREPROC_PREFIX):
                self._visit_preproc(node, local_name_set)
            else:
                self.visit_dict.get(node.type, self._visit_other)(node, local_name_set)
        return list(self.reference_dict)

    def _push(self, node_list: list[Node], local_name_set: frozenset[str]) -> None:
        """Put nodes on the stack without the name of their field."""
        self.node_stack.extend((node, None, local_name_set) for node in node_list)

    def _declare_dict(self, node: Node) -> dict[str, str | None]:
        """Return the names the declarations of a node give, reading the node once.

        Returns:
            _member_declare_dict for the body of a type declaration, else
            _local_declare_dict.
        """
        declare_dict = self.scope_declare_dict.get(node.id)
        if declare_dict is None:
            reader = _member_declare_dict if _is_type_body(node) else _local_declare_dict
            declare_dict = reader(node)
            self.scope_declare_dict[node.id] = declare_dict
        return declare_dict

    def _name_type(self, name: str, node: Node, is_local: bool) -> str | None:
        """Return the type a name is declared with where a node is written.

        A local name is looked up in the declarations of the nodes around the node up
        to the body of its type (_local_declare_dict), innermost first. Any other name
        is looked up among the fields, properties and primary constructor parameters of
        the types around the node (_member_declare_dict), innermost first; the types
        nested in them, their base types and the namespace are not looked into.

        Args:
            name: The name.
            node: The node the name is written at.
            is_local: Whether the name is a local name of a declaration around the node.

        Returns:
            The type name (_type_name); None when no declaration of the name is found
            or its type has no name.
        """
        current = node.parent
        while current is not None:
            if is_local and _is_type_body(current):
                return None
            if is_local or _is_type_body(current):
                declare_dict = self._declare_dict(current)
                if name in declare_dict:
                    return declare_dict[name]
            current = current.parent
        return None

    def _argument_type_tuple(
        self, call_node: Node, local_name_set: frozenset[str],
    ) -> tuple[str | None, ...]:
        """Return the type name of each argument of an invocation where the file tells it.

        Examples:
            Show(1, "a", new Box(), (long)v)   -> ("int", "string", "Box", "long")
            Show(name) (declared Box name;)    -> ("Box",)
            Show(Make(), null)                 -> (None, None)

        Args:
            call_node: An invocation_expression node.
            local_name_set: Names of the type parameters and local names around the node.

        Returns:
            The type names (_expression_type, and _name_type for a name); None for an
            argument whose type the file does not tell.
        """
        argument_list_node = call_node.child_by_field_name("arguments")
        if argument_list_node is None:
            return ()
        type_name_list: list[str | None] = []
        for argument_node in argument_list_node.children:
            if argument_node.type != "argument":
                continue
            value_node = _argument_value_node(argument_node)
            if value_node is not None and value_node.type == "identifier":
                name = _name_text(value_node)
                type_name_list.append(
                    self._name_type(name, argument_node, name in local_name_set),
                )
            else:
                type_name_list.append(_expression_type(value_node))
        return tuple(type_name_list)

    def _add_chain(
        self,
        chain: _Chain,
        kind: str,
        node: Node,
        local_name_set: frozenset[str],
        call_node: Node | None = None,
    ) -> None:
        """Keep the reference of a chain and put the nodes inside the chain on the stack.

        A chain whose first name is a type parameter or a local name of a declaration
        around it is not a name of a type or member: only the member it calls is kept
        (_local_value_reference).

        Args:
            chain: The chain.
            kind: Kind of the reference (_chain_reference).
            node: The node the chain starts at.
            local_name_set: Names of the type parameters and local names around the node.
            call_node: The invocation_expression node that calls the chain; None when
                the chain is not called.
        """
        argument_type_tuple = None
        if call_node is not None:
            argument_type_tuple = self._argument_type_tuple(call_node, local_name_set)
        reference = _chain_reference(chain, kind, node.start_point[0] + 1, argument_type_tuple)
        if (
            reference is not None
            and reference.kind not in (MEMBER_REFERENCE, THIS_REFERENCE)
            and not reference.is_absolute
            and reference.part_tuple[0] in local_name_set
        ):
            reference = _local_value_reference(reference)
        if reference is not None:
            self.reference_dict.setdefault(reference)
        self._push(chain.inner_node_list, local_name_set)

    def _visit_using(self, node: Node, local_name_set: frozenset[str]) -> None:
        """Walk the types written inside the name a using directive gives.

        The type arguments of a name chain (using L = List<Order>;) and a type that is
        not a name chain (using P = (Order, int);) are walked; the name chain itself
        and the alias are not kept.
        """
        for child in node.children:
            if child.type in _CHAIN_NODE_TYPE_SET:
                self._push(_read_chain(child).inner_node_list, local_name_set)
            elif child.is_named:
                self._push([child], local_name_set)

    def _visit_attribute(self, node: Node, local_name_set: frozenset[str]) -> None:
        """Keep the name of an attribute and walk its arguments."""
        name_node = node.child_by_field_name("name")
        if name_node is not None and name_node.type in _CHAIN_NODE_TYPE_SET:
            self._add_chain(_read_chain(name_node), ATTRIBUTE_REFERENCE, node, local_name_set)
        self._push(
            [child for child in node.children if name_node is None or child.id != name_node.id],
            local_name_set,
        )

    def _visit_invocation(self, node: Node, local_name_set: frozenset[str]) -> None:
        """Keep what an invocation calls and walk the rest of it.

        A name chain that is called is kept with its number of arguments; a member
        called through a conditional access (a?.Run()) is kept as a MEMBER_REFERENCE.
        """
        function_node = node.child_by_field_name("function")
        skip_id = None
        if function_node is not None and function_node.type in _CHAIN_NODE_TYPE_SET:
            self._add_chain(
                _read_chain(function_node), NAME_REFERENCE, node, local_name_set, node,
            )
            skip_id = function_node.id
        elif function_node is not None:
            chain = _call_member_chain(function_node)
            if chain is not None:
                self._add_chain(chain, NAME_REFERENCE, node, local_name_set, node)
        self._push([child for child in node.children if child.id != skip_id], local_name_set)

    def _visit_preproc(self, node: Node, local_name_set: frozenset[str]) -> None:
        """Walk the code of #if / #elif / #else; the other directives are not read."""
        if node.type not in _PREPROC_CODE_TYPE_SET:
            return
        condition_node = node.child_by_field_name("condition")
        self._push(
            [
                child for child in node.children
                if condition_node is None or child.id != condition_node.id
            ],
            local_name_set,
        )

    def _visit_other(self, node: Node, local_name_set: frozenset[str]) -> None:
        """Walk the children of a node, leaving out the names it declares.

        Left out: every child in the field "name" (the names a declaration gives, the
        names of named arguments, the member of a conditional access), the child
        _declare_name_id gives, labels, and the names of members the node sets or
        matches (_is_member_name). The type parameters the node declares, and the
        local names declared inside a node in _LOCAL_SCOPE_TYPE_SET, are added to the
        ones around its children.
        """
        declare_name_list = _type_parameter_name_list(node)
        if node.type in _LOCAL_SCOPE_TYPE_SET:
            declare_name_list += _local_name_list(node)
        if declare_name_list:
            local_name_set = local_name_set | frozenset(declare_name_list)

        skip_id = _declare_name_id(node)
        for child_index, child in enumerate(node.children):
            field_name = node.field_name_for_child(child_index)
            if field_name == "name" or child.id == skip_id:
                continue
            if node.type in _LABEL_PARENT_TYPE_SET and child.type == "identifier":
                continue
            if _is_member_name(node, child_index):
                continue
            self.node_stack.append((child, field_name, local_name_set))


def csharp_reference_list(root_node: Node) -> list[CsharpReference]:
    """Read the name chains of a C# file that are not names of declarations.

    Left out: the names declarations give (types, members, parameters, variables,
    labels), the names of namespace declarations, the names of using directives
    (the types written inside them are read: using L = List<Order>; -> Order), the
    names of named arguments and of members set in an initializer, a chain that starts
    with a type parameter or a local name (parameter, variable) of a declaration
    around it, of which a call keeps only the last name as a MEMBER_REFERENCE
    (items.Where(...) -> Where), and the name of a member of a value that is not
    called. The conditions of #if / #elif and the other preprocessor
    directives are not read.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The references in the order they are found, without duplicates.
    """
    return _ReferenceWalker().walk(root_node)
