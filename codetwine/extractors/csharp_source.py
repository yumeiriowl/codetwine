from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.extractors.definitions import definition_name
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

# AST node types of a namespace declaration
_NAMESPACE_TYPE_SET = {"namespace_declaration", "file_scoped_namespace_declaration"}

# AST node types whose children are declarations of the place the node is written in
_PASS_NODE_TYPE_SET = {
    "preproc_if", "preproc_elif", "preproc_else", "declaration_list", "ERROR",
}

# AST node types of a name chain
_CHAIN_NODE_TYPE_SET = {
    "identifier", "generic_name", "qualified_name", "alias_qualified_name",
    "member_access_expression",
}

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

# AST node types of a declaration of fields
_FIELD_DECLARATION_TYPE_SET = {"field_declaration", "event_field_declaration"}

# Prefix of the AST node types of preprocessor directives
_PREPROC_PREFIX = "preproc_"

# AST node types of the preprocessor directives that hold code
_PREPROC_CODE_TYPE_SET = {"preproc_if", "preproc_elif", "preproc_else"}


@dataclass
class CsharpUsing:
    """One using directive."""

    kind: str                     # NAMESPACE_USING / STATIC_USING / ALIAS_USING
    part_tuple: tuple[str, ...]   # Parts of the namespace or type name (A.B.Type -> A, B, Type)
    alias: str = ""               # Name of an ALIAS_USING; "" for the other kinds
    is_global: bool = False       # Whether the directive is written with "global"


@dataclass
class CsharpScope:
    """A place using directives are written in: the file, or one namespace declaration."""

    namespace: str      # Full name of the namespace; "" for the file
    start_line: int     # First line (1-based)
    end_line: int       # Last line
    depth: int          # Number of scopes around it
    using_list: list[CsharpUsing] = field(default_factory=list)


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
    # Name of a method whose first parameter is written with "this" -> (least, most)
    # number of arguments a call of each declaration of it takes, without the one
    # "this" stands for; most is None for a declaration with "params"
    extension_dict: dict[str, list[tuple[int, int | None]]] = field(default_factory=dict)


@dataclass
class CsharpDeclaration:
    """The namespaces, using directives and types of one C# file."""

    # The file itself, then each namespace declaration in line order
    scope_list: list[CsharpScope] = field(default_factory=list)
    # Type declarations in line order
    type_list: list[CsharpType] = field(default_factory=list)


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


def _node_text(node: Node) -> str:
    """Return the text of a name node without the verbatim prefix (@class -> class)."""
    return node.text.decode("utf-8").removeprefix("@")


def _root_node(node: Node) -> Node:
    """Return the root node of the tree a node is in."""
    while node.parent is not None:
        node = node.parent
    return node


def _has_modifier(node: Node, modifier: str) -> bool:
    """Return whether a declaration or parameter node is written with a modifier."""
    return any(
        child.type == "modifier" and child.text.decode("utf-8") == modifier
        for child in node.children
    )


def _type_parameter_name_list(node: Node) -> list[str]:
    """Return the names of the type parameters a declaration node declares."""
    type_parameter_list = next(
        (child for child in node.children if child.type == "type_parameter_list"), None,
    )
    if type_parameter_list is None:
        return []
    name_list: list[str] = []
    for child in type_parameter_list.children:
        if child.type == "type_parameter":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                name_list.append(_node_text(name_node))
    return name_list


def _type_argument_count(node: Node) -> int:
    """Return the number of type arguments of a type_argument_list node.

    Examples:
        <Order>      -> 1
        <string, T>  -> 2
        <,>          -> 2
    """
    return sum(1 for child in node.children if child.type == ",") + 1


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
    if node.type == "identifier":
        chain.part_list.append(_node_text(node))
        chain.arity_list.append(0)
    elif node.type == "generic_name":
        for child in node.children:
            if child.type == "identifier":
                chain.part_list.append(_node_text(child))
            elif child.type == "type_argument_list":
                chain.arity_list.append(_type_argument_count(child))
                chain.inner_node_list.append(child)
        if len(chain.arity_list) < len(chain.part_list):
            chain.arity_list.append(0)
    elif node.type == "alias_qualified_name":
        alias_node = node.child_by_field_name("alias")
        name_node = node.child_by_field_name("name")
        if alias_node is not None and _node_text(alias_node) == "global":
            chain.head = "global"
        elif alias_node is not None:
            chain.part_list.append(_node_text(alias_node))
            chain.arity_list.append(0)
        if name_node is not None:
            _append_chain(chain, _read_chain(name_node))
    elif node.type == "qualified_name":
        for field_name in ("qualifier", "name"):
            part_node = node.child_by_field_name(field_name)
            if part_node is not None and part_node.type in _CHAIN_NODE_TYPE_SET:
                _append_chain(chain, _read_chain(part_node))
    elif node.type == "member_access_expression":
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
        alias=_node_text(alias_node) if alias_node is not None else "",
        is_global="global" in child_type_set,
    )


def join_name(*part_tuple: str) -> str:
    """Join the parts of a dotted name, leaving out the empty ones.

    Examples:
        ("App.Models", "Customer")  -> "App.Models.Customer"
        ("", "Customer")            -> "Customer"
    """
    return ".".join(part for part in part_tuple if part)


def _extension_argument_range(node: Node) -> tuple[int, int | None] | None:
    """Return the number of arguments a call of an extension method takes.

    Examples:
        Twice(this int v)                              -> (0, 0)
        Pad(this string s, int width, char fill = ' ') -> (1, 2)
        Join(this string s, params object[] rest)      -> (0, None)
        Parse(string s)                                -> None

    Args:
        node: A method_declaration node.

    Returns:
        (least, most) number of arguments, without the one the first parameter stands
        for; most is None for a declaration with "params". None when the first
        parameter is not written with "this".
    """
    parameter_list = node.child_by_field_name("parameters")
    if parameter_list is None:
        return None
    parameter_node_list = [
        child for child in parameter_list.children if child.type == "parameter"
    ]
    if not parameter_node_list or not _has_modifier(parameter_node_list[0], "this"):
        return None
    rest_list = parameter_node_list[1:]
    least = sum(
        1 for parameter in rest_list
        if not any(child.type == "=" for child in parameter.children)
    )
    has_params = any(child.type == "params" for child in parameter_list.children)
    return least, None if has_params else len(rest_list)


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
    name = _node_text(name_node)
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
    if node.type in _FIELD_DECLARATION_TYPE_SET:
        return [
            _node_text(name_node)
            for variable_declaration in node.children
            if variable_declaration.type == "variable_declaration"
            for declarator in variable_declaration.children
            if declarator.type == "variable_declarator"
            and (name_node := declarator.child_by_field_name("name")) is not None
        ]
    name = definition_name(node, CSHARP_DEFINITION_DICT)
    return [name.removeprefix("@")] if name else []


def _add_member(owner: CsharpType, node: Node) -> None:
    """Add the names a member declaration gives to its type, and its extension method.

    Args:
        owner: The type whose body the declaration is written in; modified in place.
        node: A node whose type is in CSHARP_DEFINITION_DICT.
    """
    argument_range = None
    if node.type == "method_declaration":
        argument_range = _extension_argument_range(node)
    for name in _member_name_list(node):
        owner.member_name_set.add(name)
        if argument_range is not None:
            owner.extension_dict.setdefault(name, []).append(argument_range)


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
        True for a qualified name, a child in a field of _TYPE_FIELD_NAME_SET, a child
        of a node in _TYPE_PARENT_TYPE_SET, and the right side of "as" / "is".
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
    chain: _Chain, kind: str, line: int, call_node: Node | None = None,
) -> CsharpReference | None:
    """Return the reference of a name chain.

    Args:
        chain: The chain.
        kind: NAME_REFERENCE, TYPE_REFERENCE or ATTRIBUTE_REFERENCE; the kind of a
            chain with the head "" or "global".
        line: Line the chain starts on (1-based).
        call_node: The invocation_expression node that calls the chain; None when
            the chain is not called.

    Returns:
        The reference. A chain after "this." is a THIS_REFERENCE. A chain after a value
        is a MEMBER_REFERENCE of its last name when it is a call. None for a chain
        without a name, a chain after "base." or after a type written with a keyword,
        and a chain after a value that is not a call.
    """
    if not chain.part_list or chain.head in ("base", "builtin"):
        return None
    is_call = call_node is not None
    argument_count = 0
    if call_node is not None:
        argument_list = call_node.child_by_field_name("arguments")
        if argument_list is not None:
            argument_count = sum(
                1 for child in argument_list.children if child.type == "argument"
            )
    if chain.head == "value":
        if not is_call:
            return None
        return CsharpReference(
            MEMBER_REFERENCE, (chain.part_list[-1],), (chain.arity_list[-1],), line, True,
            argument_count=argument_count,
        )
    if chain.head == "this":
        kind = THIS_REFERENCE
    return CsharpReference(
        kind, tuple(chain.part_list), tuple(chain.arity_list), line, is_call,
        chain.head == "global", argument_count,
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
        # parameters of the declarations around the node)
        self.node_stack: list[tuple[Node, str | None, frozenset[str]]] = []

    def walk(self, root_node: Node) -> list[CsharpReference]:
        """Visit every node under root_node and return the references found.

        Args:
            root_node: The AST root node of the file.

        Returns:
            The references in the order they are found, without duplicates.
        """
        self.node_stack.append((root_node, None, frozenset()))
        while self.node_stack:
            node, field_name, type_parameter_set = self.node_stack.pop()
            if node.type == "using_directive":
                continue
            if node.type in _CHAIN_NODE_TYPE_SET:
                kind = TYPE_REFERENCE if _is_type_position(node, field_name) else NAME_REFERENCE
                self._add_chain(_read_chain(node), kind, node, type_parameter_set)
            elif node.type == "attribute":
                self._visit_attribute(node, type_parameter_set)
            elif node.type == "invocation_expression":
                self._visit_invocation(node, type_parameter_set)
            elif node.type.startswith(_PREPROC_PREFIX):
                self._visit_preproc(node, type_parameter_set)
            else:
                self._visit_other(node, type_parameter_set)
        return list(self.reference_dict)

    def _push(self, node_list: list[Node], type_parameter_set: frozenset[str]) -> None:
        """Put nodes on the stack without the name of their field."""
        self.node_stack.extend((node, None, type_parameter_set) for node in node_list)

    def _add_chain(
        self,
        chain: _Chain,
        kind: str,
        node: Node,
        type_parameter_set: frozenset[str],
        call_node: Node | None = None,
    ) -> None:
        """Keep the reference of a chain and put the nodes inside the chain on the stack.

        A reference whose first name is a type parameter of a declaration around it is
        not kept.

        Args:
            chain: The chain.
            kind: Kind of the reference (_chain_reference).
            node: The node the chain starts at.
            type_parameter_set: Names of the type parameters around the node.
            call_node: The invocation_expression node that calls the chain; None when
                the chain is not called.
        """
        reference = _chain_reference(chain, kind, node.start_point[0] + 1, call_node)
        if reference is not None and (
            reference.kind == MEMBER_REFERENCE or reference.part_tuple[0] not in type_parameter_set
        ):
            self.reference_dict.setdefault(reference)
        self._push(chain.inner_node_list, type_parameter_set)

    def _visit_attribute(self, node: Node, type_parameter_set: frozenset[str]) -> None:
        """Keep the name of an attribute and walk its arguments."""
        name_node = node.child_by_field_name("name")
        if name_node is not None and name_node.type in _CHAIN_NODE_TYPE_SET:
            self._add_chain(_read_chain(name_node), ATTRIBUTE_REFERENCE, node, type_parameter_set)
        self._push(
            [child for child in node.children if name_node is None or child.id != name_node.id],
            type_parameter_set,
        )

    def _visit_invocation(self, node: Node, type_parameter_set: frozenset[str]) -> None:
        """Keep what an invocation calls and walk the rest of it.

        A name chain that is called is kept with its number of arguments; a member
        called through a conditional access (a?.Run()) is kept as a MEMBER_REFERENCE.
        """
        function_node = node.child_by_field_name("function")
        skip_id = None
        if function_node is not None and function_node.type in _CHAIN_NODE_TYPE_SET:
            self._add_chain(
                _read_chain(function_node), NAME_REFERENCE, node, type_parameter_set, node,
            )
            skip_id = function_node.id
        elif function_node is not None:
            chain = _call_member_chain(function_node)
            if chain is not None:
                self._add_chain(chain, NAME_REFERENCE, node, type_parameter_set, node)
        self._push([child for child in node.children if child.id != skip_id], type_parameter_set)

    def _visit_preproc(self, node: Node, type_parameter_set: frozenset[str]) -> None:
        """Walk the code of #if / #elif / #else; the other directives are not read."""
        if node.type not in _PREPROC_CODE_TYPE_SET:
            return
        condition_node = node.child_by_field_name("condition")
        self._push(
            [
                child for child in node.children
                if condition_node is None or child.id != condition_node.id
            ],
            type_parameter_set,
        )

    def _visit_other(self, node: Node, type_parameter_set: frozenset[str]) -> None:
        """Walk the children of a node, leaving out the names it declares.

        Left out: every child in the field "name" (the names a declaration gives, the
        names of named arguments, the member of a conditional access), the child
        _declare_name_id gives, labels, and the names of members the node sets or
        matches (_is_member_name). The type parameters the node declares are added to
        the ones around its children.
        """
        type_parameter_name_list = _type_parameter_name_list(node)
        if type_parameter_name_list:
            type_parameter_set = type_parameter_set | frozenset(type_parameter_name_list)

        skip_id = _declare_name_id(node)
        for child_index, child in enumerate(node.children):
            field_name = node.field_name_for_child(child_index)
            if field_name == "name" or child.id == skip_id:
                continue
            if node.type in _LABEL_PARENT_TYPE_SET and child.type == "identifier":
                continue
            if _is_member_name(node, child_index):
                continue
            self.node_stack.append((child, field_name, type_parameter_set))


def csharp_reference_list(root_node: Node) -> list[CsharpReference]:
    """Read the name chains of a C# file that are not names of declarations.

    Left out: the names declarations give (types, members, parameters, variables,
    labels), the names of using directives and namespace declarations, the names of
    named arguments and of members set in an initializer, a chain that starts with a
    type parameter of a declaration around it, and the name of a member of a value
    that is not called. The conditions of #if / #elif and the other preprocessor
    directives are not read.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The references in the order they are found, without duplicates.
    """
    return _ReferenceWalker().walk(root_node)
