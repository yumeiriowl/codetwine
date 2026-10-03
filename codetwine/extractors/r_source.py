from dataclasses import dataclass, field
from tree_sitter import Node
from codetwine.utils.file_utils import line_list_of

# Kinds of an RImport
SOURCE_IMPORT = "source"      # source("path") / sys.source("path")
BOX_IMPORT = "box"            # one argument of box::use(...)
LIBRARY_IMPORT = "library"    # library(pkg) / require(pkg)

# Kinds of an RReference
NAME_REFERENCE = "name"              # a name written by itself: f(x), lapply(xs, f), new("A")
NAMESPACE_REFERENCE = "namespace"    # pkg::name / pkg:::name
MEMBER_REFERENCE = "member"          # owner$name / owner@name

# Assignment operators whose target is on the left / on the right
_LEFT_ASSIGN_SET = {"<-", "=", "<<-"}
_RIGHT_ASSIGN_SET = {"->", "->>"}

# Assignment operators that bind the name in the function they are written in
_LOCAL_ASSIGN_SET = {"<-", "=", "->"}

# Assignment operators that assign to a name outside the function they are written in
_OUTER_ASSIGN_SET = {"<<-", "->>"}

# Suffix of the name of the function f(x) <- value calls (f<-)
_REPLACE_SUFFIX = "<-"

# Operator whose left side is a name that is neither a definition nor a reference (x := value)
_COLUMN_ASSIGN = ":="

# Node types whose children are top-level statements when the node is one itself
_TOP_LEVEL_BLOCK_TYPE_SET = {"braced_expression", "parenthesized_expression", "if_statement"}

# Node type of a loop -> the fields whose nodes are top-level statements when the loop is one
_LOOP_FIELD_DICT = {
    "for_statement": ("sequence", "body"),
    "while_statement": ("condition", "body"),
    "repeat_statement": ("body",),
}

# Call -> names of its leading parameters, in order. An argument written without a name
# is the first of these that no argument of the call names
_FORMAL_DICT = {
    "setClass": ("Class", "representation", "prototype", "contains"),
    "setRefClass": ("Class", "fields", "contains", "methods"),
    "setClassUnion": ("name", "members"),
    "setGeneric": ("name", "def"),
    "setMethod": ("f", "signature", "definition"),
    "setReplaceMethod": ("f", "signature", "definition"),
    "setValidity": ("Class", "method"),
    "R6Class": ("classname", "public", "private", "active"),
    "new": ("Class",),
    "do.call": ("what", "args"),
    "match.fun": ("FUN",),
    "source": ("file",),
    "sys.source": ("file",),
    "library": ("package",),
    "require": ("package",),
}

# Call that defines a name -> name of the argument that holds it
_NAME_CALL_DICT = {
    "setClass": "Class",
    "setRefClass": "Class",
    "setClassUnion": "name",
    "setGeneric": "name",
}

# Call written for a name defined elsewhere -> name of the argument that holds it
_ATTACH_CALL_DICT = {
    "setMethod": "f",
    "setReplaceMethod": "f",
    "setValidity": "Class",
}

# Call whose string names a function -> suffix of the name of that function
# (setReplaceMethod("f", ...) is written for f<-)
_NAME_SUFFIX_CALL_DICT = {"setReplaceMethod": _REPLACE_SUFFIX}

# Call that makes a class -> names of the arguments whose list(...) holds its members
_MEMBER_ARGUMENT_DICT = {
    "R6Class": ("public", "private", "active"),
    "setRefClass": ("methods", "fields"),
}

# Call -> name of the argument whose string names a definition
_STRING_NAME_CALL_DICT = {
    "new": "Class",
    "setValidity": "Class",
    "setMethod": "f",
    "setReplaceMethod": "f",
    "do.call": "what",
    "match.fun": "FUN",
}

# Call -> names of the arguments whose strings name classes: one string, or the strings
# given to c(...) / list(...)
_CLASS_ARGUMENT_DICT = {
    "setMethod": ("signature",),
    "setReplaceMethod": ("signature",),
    "setClass": ("contains",),
    "setRefClass": ("contains",),
    "setClassUnion": ("members",),
}

# Call -> names of the arguments that give each slot or field its class: the strings
# written with a name in c(...) / list(...) (c(x = "A"); a string without a name is a
# slot name)
_SLOT_ARGUMENT_DICT = {
    "setClass": ("slots",),
    "setRefClass": ("fields",),
}

# Calls that hold the strings of an argument that names classes
_STRING_HOLDER_CALL_SET = {"c", "list"}

# Calls whose every string names a class
_CLASS_STRING_CALL_SET = {"signature", "representation"}

# Calls that read a file: the path is the first argument, or the argument named "file"
_SOURCE_CALL_SET = {"source", "sys.source"}

# Calls that attach a package: the package is the first argument, or the one named "package"
_LIBRARY_CALL_SET = {"library", "require"}

# Calls whose string arguments, all of them, make up a path
_PATH_CALL_SET = {"file.path", "here"}

# Call inside the body of a function that makes the function an S3 generic
_GENERIC_CALL = "UseMethod"

# Calls whose arguments are code that is not run where it is written
_QUOTE_CALL_SET = {"quote", "bquote", "substitute", "expression"}


@dataclass
class RDefinition:
    """One definition of an R file."""

    name: str                   # Name of the definition
    type: str                   # function_definition / binary_operator / call / argument
    start_line: int             # First line of the statement (1-based)
    end_line: int               # Last line of the statement
    is_function: bool = False   # Whether the value is a function
    is_generic: bool = False    # Whether the function calls UseMethod
    is_member: bool = False     # Whether it is a member of a class (R6Class, setRefClass)
    is_attach: bool = False     # Whether it is written for a name defined elsewhere (setMethod)


@dataclass
class RImport:
    """One file or package an R file reads."""

    kind: str     # SOURCE_IMPORT / BOX_IMPORT / LIBRARY_IMPORT
    path: str     # Path as written (source), module path "a/b" or package name (box), package name (library)
    line: int     # Line of the call (1-based)
    alias: str = ""                                            # box: name the module is bound to
    name_dict: dict[str, str] = field(default_factory=dict)    # box: {name in the file: name in the module}
    is_attach_all: bool = False                                # box: whether every name is attached ([...])


@dataclass(frozen=True)
class RReference:
    """One place an R file refers to a name no function around it binds."""

    name: str        # The name referred to
    line: int        # Line of the reference (1-based)
    kind: str        # NAME_REFERENCE / NAMESPACE_REFERENCE / MEMBER_REFERENCE
    owner: str = ""  # Package of pkg::name, or the name before the first "$" / "@"
    member_tuple: tuple[str, ...] = ()  # owner$name$a$b: the names after name ("a", "b")


@dataclass
class RSource:
    """The definitions, imports and references of one R file."""

    definition_list: list[RDefinition]
    import_list: list[RImport]
    reference_list: list[RReference]


def _node_text(node: Node) -> str:
    """Return the source text of a node."""
    return node.text.decode("utf-8")


def _line(node: Node) -> int:
    """Return the line a node starts on (1-based)."""
    return node.start_point[0] + 1


def _string_text(node: Node | None) -> str | None:
    """Return the text between the quotes of a string node.

    Returns:
        The text, "" for an empty string. None when the node is not a string, or is a
        raw string (r"(...)").
    """
    if node is None or node.type != "string":
        return None
    content = node.child_by_field_name("content")
    if content is not None:
        return _node_text(content)
    return "" if node.child_by_field_name("open") is not None else None


def _name_text(node: Node | None) -> str | None:
    """Return the name a node writes: an identifier without its backticks, or a string.

    Examples:
        add        -> "add"
        `my var`   -> "my var"
        "add"      -> "add"
        x$y        -> None
    """
    if node is None:
        return None
    if node.type == "identifier":
        return _node_text(node).strip("`")
    return _string_text(node)


def _operator(node: Node) -> str:
    """Return the operator of a binary_operator, extract_operator or namespace_operator node."""
    operator_node = node.child_by_field_name("operator")
    return _node_text(operator_node) if operator_node is not None else ""


def _assign_part(node: Node) -> tuple[Node | None, Node | None] | None:
    """Split an assignment into its target and its value.

    Examples:
        x <- 1     -> (x, 1)
        1 -> x     -> (x, 1)
        x + 1      -> None

    Returns:
        (target node, value node), or None when the node is not an assignment.
    """
    if node.type != "binary_operator":
        return None
    operator = _operator(node)
    lhs = node.child_by_field_name("lhs")
    rhs = node.child_by_field_name("rhs")
    if operator in _LEFT_ASSIGN_SET:
        return lhs, rhs
    if operator in _RIGHT_ASSIGN_SET:
        return rhs, lhs
    return None


def _call_name_part(node: Node) -> tuple[str, str] | None:
    """Return the package and the name of the function a call node calls.

    Examples:
        source("a.R")       -> ("", "source")
        box::use(./a)       -> ("box", "use")
        obj$run()           -> None
    """
    function_node = node.child_by_field_name("function")
    if function_node is None:
        return None
    if function_node.type == "identifier":
        return "", _node_text(function_node).strip("`")
    if function_node.type == "namespace_operator":
        owner = _name_text(function_node.child_by_field_name("lhs"))
        name = _name_text(function_node.child_by_field_name("rhs"))
        if owner is not None and name is not None:
            return owner, name
    return None


def _call_name(node: Node | None) -> str | None:
    """Return the name of the function a call node calls, without its package."""
    if node is None or node.type != "call":
        return None
    name_part = _call_name_part(node)
    return name_part[1] if name_part else None


def _argument_list(node: Node) -> list[tuple[str | None, Node | None, Node]]:
    """Return the arguments of a call or subset node.

    Returns:
        One (name or None, value node or None, argument node) per argument, in order.
    """
    argument_holder = node.child_by_field_name("arguments")
    if argument_holder is None:
        return []
    return [
        (
            _name_text(child.child_by_field_name("name")),
            child.child_by_field_name("value"),
            child,
        )
        for child in argument_holder.children if child.type == "argument"
    ]


def _formal_name(argument_name: str, formal_tuple: tuple[str, ...]) -> str:
    """Return the parameter an argument name stands for.

    Examples (parameters f, signature, definition):
        "signature" -> "signature"
        "sig"       -> "signature"
        "other"     -> "other"

    Args:
        argument_name: The name an argument is written with.
        formal_tuple: The parameter names of the call.

    Returns:
        The parameter of that name, else the one parameter the name is the start of,
        else the name itself.
    """
    if argument_name in formal_tuple:
        return argument_name
    match_list = [formal for formal in formal_tuple if formal.startswith(argument_name)]
    return match_list[0] if len(match_list) == 1 else argument_name


def _argument_value(node: Node, name: str) -> Node | None:
    """Return the value a call gives the parameter of a name.

    An argument written with the name, or with the start of it that fits no other
    parameter of _FORMAL_DICT, is taken. Else the arguments written without a name
    fill, in order, the parameters of _FORMAL_DICT that no argument names.

    Examples (setMethod: f, signature, definition):
        setMethod("area", "Shape", fn)       signature -> "Shape"
        setMethod(f = "area", "Shape", fn)   signature -> "Shape"
        setMethod("area", sig = "Shape")     signature -> "Shape"
        setMethod("area", definition = fn)   signature -> None

    Args:
        node: A call node.
        name: Name of the parameter.

    Returns:
        The value node, or None when the call gives the parameter no value.
    """
    formal_tuple = _FORMAL_DICT.get(_call_name(node) or "", ())
    argument_list = [
        (_formal_name(argument_name, formal_tuple) if argument_name is not None else None, value)
        for argument_name, value, _ in _argument_list(node)
    ]
    name_set = {argument_name for argument_name, _ in argument_list if argument_name is not None}
    free_formal_list = [formal for formal in formal_tuple if formal not in name_set]
    position = 0
    for argument_name, value in argument_list:
        if argument_name == name:
            return value
        if argument_name is None:
            if position < len(free_formal_list) and free_formal_list[position] == name:
                return value
            position += 1
    return None


def _assign_chain(node: Node) -> tuple[list[Node], Node | None]:
    """Return the targets of a chain of assignments and the value they are given.

    Examples:
        a <- b <- 1     -> ([a, b], 1)
        1 -> a -> b     -> ([b, a], 1)
        f(x)            -> ([], f(x))

    Args:
        node: A statement.

    Returns:
        (target nodes from the outermost assignment inwards, the value node). A node
        that is not an assignment is its own value.
    """
    target_list: list[Node] = []
    value: Node | None = node
    while value is not None:
        assign_part = _assign_part(value)
        if assign_part is None:
            break
        if assign_part[0] is not None:
            target_list.append(assign_part[0])
        value = assign_part[1]
    return target_list, value


def _read_top_level(root_node: Node) -> tuple[list[Node], set[str]]:
    """Return the top-level statements of a file and the variables of its top-level for loops.

    The statements inside a top-level "{ }", "( )" and "if" (condition, both branches)
    are top-level statements themselves, and so are the sequence, the condition and the
    statements of the body of a top-level for, while and repeat loop.

    Args:
        root_node: The AST root node of the file.

    Returns:
        (statement nodes in source order, without comments; names of the for variables).
    """
    statement_list: list[Node] = []
    loop_name_set: set[str] = set()
    node_stack = [child for child in reversed(root_node.children) if child.is_named]
    while node_stack:
        node = node_stack.pop()
        if node.type == "comment":
            continue
        if node.type in _TOP_LEVEL_BLOCK_TYPE_SET:
            node_stack.extend(child for child in reversed(node.children) if child.is_named)
            continue
        loop_field_tuple = _LOOP_FIELD_DICT.get(node.type)
        if loop_field_tuple is None:
            statement_list.append(node)
            continue
        loop_name = _name_text(node.child_by_field_name("variable"))
        if loop_name:
            loop_name_set.add(loop_name)
        for field_name in reversed(loop_field_tuple):
            child = node.child_by_field_name(field_name)
            if child is not None:
                node_stack.append(child)
    return statement_list, loop_name_set


def _has_call(function_node: Node, name: str) -> bool:
    """Return whether a function calls the named function, outside the functions written in it."""
    node_stack = list(function_node.children)
    while node_stack:
        current = node_stack.pop()
        if current.type == "function_definition":
            continue
        if _call_name(current) == name:
            return True
        node_stack.extend(current.children)
    return False


def _member_definition_list(call_node: Node) -> list[RDefinition]:
    """Return the members of the class a call makes.

    Target AST structure:
        call                               <- this node is passed as the argument
          +-- function: identifier "R6Class"
          +-- arguments
               +-- argument  name: "public"
                    +-- value: call "list"
                         +-- argument  name: "deposit"  value: function_definition   <- a member

    The same structure applies to private and active of R6Class and to methods and
    fields of setRefClass.

    Args:
        call_node: A call node.

    Returns:
        One RDefinition of type "argument" per named argument of each list, with
        is_member set. Empty when the call makes no class.
    """
    member_list: list[RDefinition] = []
    for argument_name in _MEMBER_ARGUMENT_DICT.get(_call_name(call_node) or "", ()):
        list_node = _argument_value(call_node, argument_name)
        if _call_name(list_node) != "list":
            continue
        for member_name, value, argument_node in _argument_list(list_node):
            if member_name is None:
                continue
            member_list.append(RDefinition(
                name=member_name,
                type="argument",
                start_line=_line(argument_node),
                end_line=argument_node.end_point[0] + 1,
                is_function=value is not None and value.type == "function_definition",
                is_member=True,
            ))
    return member_list


def _string_name(call_node: Node, argument_name: str) -> str | None:
    """Return the name a call is given as a string.

    The name of setReplaceMethod("f", ...) is "f<-" (_NAME_SUFFIX_CALL_DICT).

    Args:
        call_node: A call node.
        argument_name: Name of the parameter that holds the string.

    Returns:
        The name, or None when the parameter is given no string or an empty one.
    """
    name = _string_text(_argument_value(call_node, argument_name))
    if not name:
        return None
    suffix = _NAME_SUFFIX_CALL_DICT.get(_call_name(call_node) or "", "")
    return name if name.endswith(suffix) else name + suffix


def _define_call_list(node: Node | None) -> list[Node]:
    """Return the calls of a statement that define a name by a string, or a class.

    The node itself when it is such a call (_NAME_CALL_DICT, _ATTACH_CALL_DICT,
    _MEMBER_ARGUMENT_DICT), else the ones among the arguments of the calls it is made
    of: invisible(setClass("A")), suppressMessages(setGeneric("g", ...)). The calls
    inside a function, and inside quote(), bquote(), substitute() and expression(),
    are not returned.

    Args:
        node: A call node, or any other node (no calls are returned for it).

    Returns:
        The call nodes, in source order.
    """
    call_list: list[Node] = []
    node_stack = [node] if node is not None else []
    while node_stack:
        current = node_stack.pop()
        if current.type != "call":
            continue
        call_name = _call_name(current) or ""
        if any(
            call_name in call_dict
            for call_dict in (_NAME_CALL_DICT, _ATTACH_CALL_DICT, _MEMBER_ARGUMENT_DICT)
        ):
            call_list.append(current)
            continue
        if call_name in _QUOTE_CALL_SET:
            continue
        node_stack.extend(
            value for _, value, _ in reversed(_argument_list(current)) if value is not None
        )
    return call_list


def _call_definition_list(call_node: Node, statement: Node, skip_name_set: set[str]) -> list[RDefinition]:
    """Return the definitions a call writes by the string it is given.

    setClass("A"), setRefClass("A"), setClassUnion("A") and setGeneric("g") define the
    name; setMethod("g", ...), setReplaceMethod("g", ...) (named g<-) and
    setValidity("A", ...) are written for a name defined elsewhere (is_attach).

    Args:
        call_node: A call node.
        statement: The top-level statement the call is written in.
        skip_name_set: Names not returned (the names the statement assigns the call to).

    Returns:
        At most one RDefinition of type "call", with the lines of the statement.
    """
    call_name = _call_name(call_node) or ""
    is_attach = call_name in _ATTACH_CALL_DICT
    argument_name = _ATTACH_CALL_DICT.get(call_name) or _NAME_CALL_DICT.get(call_name)
    if argument_name is None:
        return []
    name = _string_name(call_node, argument_name)
    if not name or name in skip_name_set:
        return []
    return [RDefinition(
        name=name,
        type="call",
        start_line=_line(statement),
        end_line=statement.end_point[0] + 1,
        is_attach=is_attach,
    )]


def r_definition_list(root_node: Node) -> list[RDefinition]:
    """Return the definitions of an R file in line order.

    A top-level assignment to a name (name <- value, name = value, name <<- value,
    value -> name, value ->> name; the name an identifier, a backtick name or a string)
    is a definition: of type "function_definition" when the value is a function, else
    "binary_operator". Every name of a chain (a <- b <- value) is one. setClass,
    setRefClass, setClassUnion and setGeneric define the name of their string, and
    setMethod, setReplaceMethod and setValidity are listed under the name they are
    written for (type "call"), also as an argument of another top-level call
    (invisible(setClass("A"))). The named entries of public, private and active of
    R6Class and of methods and fields of setRefClass are members (type "argument").
    The statements of a top-level if, "{ }", "( )", for, while and repeat are top-level
    statements (_read_top_level). An assignment inside a function is not a definition.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The RDefinition list, sorted by start_line.
    """
    definition_list: list[RDefinition] = []
    for statement in _read_top_level(root_node)[0]:
        start_line = _line(statement)
        end_line = statement.end_point[0] + 1
        target_list, value = _assign_chain(statement)
        name_list = [name for name in map(_name_text, target_list) if name]

        # The names the statement assigns: a function, or any other value
        if value is not None:
            is_function = value.type == "function_definition"
            is_generic = is_function and _has_call(value, _GENERIC_CALL)
            definition_list.extend(
                RDefinition(
                    name=name,
                    type="function_definition" if is_function else "binary_operator",
                    start_line=start_line,
                    end_line=end_line,
                    is_function=is_function,
                    is_generic=is_generic,
                )
                for name in name_list
            )

        # The calls that define a name by a string, or make a class
        for call_node in _define_call_list(value):
            definition_list.extend(_call_definition_list(call_node, statement, set(name_list)))
            definition_list.extend(_member_definition_list(call_node))

    return sorted(definition_list, key=lambda definition: definition.start_line)


def r_definition_text(root_node: Node, content: bytes, name: str) -> str | None:
    """Return the source lines of the definition of a name in an R file.

    A top-level definition of the name comes before a member and before a call written
    for a name defined elsewhere.

    Args:
        root_node: The AST root node of the file.
        content: The file's text as UTF-8 bytes (parse_file).
        name: Name of the definition.

    Returns:
        The lines of the first such definition joined with "\\n". None when the file
        has no definition of the name.
    """
    candidate_list = [d for d in r_definition_list(root_node) if d.name == name]
    if not candidate_list:
        return None
    definition = next(
        (d for d in candidate_list if not d.is_member and not d.is_attach), candidate_list[0],
    )
    line_list = line_list_of(content.decode("utf-8"))
    return "\n".join(line_list[definition.start_line - 1:definition.end_line])


def _literal_path(node: Node | None) -> str | None:
    """Return the path a node writes with string literals only.

    Examples:
        "R/helper.R"                    -> "R/helper.R"
        file.path("R", "helper.R")      -> "R/helper.R"
        here::here("R", "helper.R")     -> "R/helper.R"
        file.path(base_dir, "x.R")      -> None
    """
    path = _string_text(node)
    if path is not None:
        return path
    if _call_name(node) not in _PATH_CALL_SET:
        return None
    part_list = [
        _string_text(value) if argument_name is None else None
        for argument_name, value, _ in _argument_list(node)
    ]
    if not part_list or any(part is None for part in part_list):
        return None
    return "/".join(part_list)


def _box_path_part_list(node: Node | None) -> list[str] | None:
    """Return the parts of a module path written in box::use.

    Examples:
        ./mod/calc          -> [".", "mod", "calc"]
        app/logic/calc[add] -> ["app", "logic", "calc"]
        dplyr               -> ["dplyr"]

    Returns:
        The parts, or None when the node is not a path.
    """
    if node is None:
        return None
    if node.type == "identifier":
        return [_node_text(node).strip("`")]
    if node.type == "subset":
        return _box_path_part_list(node.child_by_field_name("function"))
    if node.type == "binary_operator" and _operator(node) == "/":
        lhs_part_list = _box_path_part_list(node.child_by_field_name("lhs"))
        rhs_part_list = _box_path_part_list(node.child_by_field_name("rhs"))
        if lhs_part_list is None or rhs_part_list is None:
            return None
        return lhs_part_list + rhs_part_list
    return None


def _box_import(argument_name: str | None, value: Node | None) -> RImport | None:
    """Read one argument of box::use.

    Target AST structure of alias = app/logic/calc[add, minus = sub]:
        argument
          +-- name: identifier "alias"
          +-- value: binary_operator "/"
               +-- lhs: binary_operator "/"  (app / logic)
               +-- rhs: subset
                    +-- function: identifier "calc"
                    +-- arguments: add, minus = sub

    The module is bound to the name of the argument, or to the last part of its path
    when the argument has neither a name nor an attach list. Each name of the attach
    list is bound to the name of the module it is given ("[...]" attaches every name).

    Args:
        argument_name: Name of the argument, or None.
        value: Value node of the argument.

    Returns:
        The RImport, or None when the value is not a module path.
    """
    part_list = _box_path_part_list(value)
    if not part_list or value is None:
        return None

    # The attach list is written after the last part of the path
    last_node = value
    while last_node.type == "binary_operator":
        rhs = last_node.child_by_field_name("rhs")
        if rhs is None:
            break
        last_node = rhs

    box_import = RImport(kind=BOX_IMPORT, path="/".join(part_list), line=_line(value))
    if last_node.type != "subset":
        box_import.alias = argument_name or part_list[-1]
        return box_import

    box_import.alias = argument_name or ""
    for attach_name, attach_value, _ in _argument_list(last_node):
        if attach_value is None:
            continue
        if attach_value.type == "dots":
            box_import.is_attach_all = True
            continue
        original_name = _name_text(attach_value)
        if original_name is not None:
            box_import.name_dict[attach_name or original_name] = original_name
    return box_import


def _import_list(root_node: Node) -> list[RImport]:
    """Return the files and packages an R file reads, in line order.

    source("path") and sys.source("path") with a path written with string literals
    (_literal_path), library(pkg) and require(pkg) with the package written as a name
    or a string, and each argument of box::use(...). The calls are read wherever they
    are written in the file.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The RImport list.
    """
    import_list: list[RImport] = []
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        node_stack.extend(node.children)
        if node.type != "call":
            continue
        name_part = _call_name_part(node)
        if name_part is None:
            continue
        owner, name = name_part

        if (owner, name) == ("box", "use"):
            for argument_name, value, _ in _argument_list(node):
                box_import = _box_import(argument_name, value)
                if box_import is not None:
                    import_list.append(box_import)
        elif name in _SOURCE_CALL_SET and owner in ("", "base"):
            path = _literal_path(_argument_value(node, "file"))
            if path:
                import_list.append(RImport(kind=SOURCE_IMPORT, path=path, line=_line(node)))
        elif name in _LIBRARY_CALL_SET and owner in ("", "base"):
            package_node = _argument_value(node, "package")
            is_string_only = _argument_value(node, "character.only") is not None
            package = _string_text(package_node) if is_string_only else _name_text(package_node)
            if package:
                import_list.append(RImport(kind=LIBRARY_IMPORT, path=package, line=_line(node)))

    return sorted(import_list, key=lambda r_import: r_import.line)


def _chain_node_id_set(statement: Node) -> set[int]:
    """Return the ids of the assignment nodes of the chain a statement starts with.

    Examples:
        a <- b <- f(x)   -> the ids of "a <- b <- f(x)" and "b <- f(x)"
        f(x)             -> empty
    """
    node_id_set: set[int] = set()
    current: Node | None = statement
    while current is not None:
        assign_part = _assign_part(current)
        if assign_part is None:
            break
        node_id_set.add(current.id)
        current = assign_part[1]
    return node_id_set


def _local_name_set(node: Node, skip_node_id_set: set[int] | None = None) -> set[str]:
    """Return the names assigned inside a node, outside the functions written in it.

    The targets of "<-", "=" and "->" and the variables of for loops are returned.

    Args:
        node: The node to read: the body of a function, or a top-level statement.
        skip_node_id_set: Ids of the assignments whose own target is not returned (the
            assignments a top-level statement defines names with).

    Returns:
        The set of names.
    """
    name_set: set[str] = set()
    node_stack = [node]
    while node_stack:
        current = node_stack.pop()
        if current.type == "function_definition":
            continue
        if current.type == "binary_operator" and (
            skip_node_id_set is None or current.id not in skip_node_id_set
        ):
            operator = _operator(current)
            if operator in _LOCAL_ASSIGN_SET:
                target = current.child_by_field_name("rhs" if operator == "->" else "lhs")
                name = _name_text(target)
                if name:
                    name_set.add(name)
        elif current.type == "for_statement":
            name = _name_text(current.child_by_field_name("variable"))
            if name:
                name_set.add(name)
        node_stack.extend(current.children)
    return name_set


def _function_local_name_set(function_node: Node) -> set[str]:
    """Return the names a function binds: its parameters and the names assigned in its body."""
    name_set: set[str] = set()
    parameter_holder = function_node.child_by_field_name("parameters")
    if parameter_holder is not None:
        for parameter in parameter_holder.children:
            if parameter.type == "parameter":
                name = _name_text(parameter.child_by_field_name("name"))
                if name:
                    name_set.add(name)
    body = function_node.child_by_field_name("body")
    if body is not None:
        name_set.update(_local_name_set(body))
    return name_set


def _string_node_list(node: Node | None, is_name_only: bool = False) -> list[Node]:
    """Return the string nodes an argument names classes with.

    One string, or the strings given to c(...) / list(...): "A", c("A", "B"),
    list(x = "A").

    Args:
        node: The value node of the argument.
        is_name_only: Whether only the strings written with a name are returned
            (c(x = "A") gives "A", c("x") and "A" give nothing).
    """
    if node is None:
        return []
    if node.type == "string":
        return [] if is_name_only else [node]
    if _call_name(node) in _STRING_HOLDER_CALL_SET:
        return [
            value for argument_name, value, _ in _argument_list(node)
            if value is not None and value.type == "string"
            and (argument_name is not None or not is_name_only)
        ]
    return []


def _string_reference_list(call_node: Node) -> list[RReference]:
    """Return the references a call makes with strings that name definitions.

    A function or class named by a string (_STRING_NAME_CALL_DICT):
        new("A"), setValidity("A", ...), setMethod("g", ...), setReplaceMethod("g", ...)
        (a reference to g<-), do.call("f", ...), match.fun("f")
    Classes named by the strings of an argument (_CLASS_ARGUMENT_DICT):
        setMethod(..., "A"), setClass(contains = "A"), setRefClass(contains = "A"),
        setClassUnion(members = c("A", "B"))
    The class of each slot or field (_SLOT_ARGUMENT_DICT):
        setClass(slots = c(x = "B")), setRefClass(fields = list(x = "B"))
    Classes named by every string of a call (_CLASS_STRING_CALL_SET):
        signature("A", "B"), representation(x = "A")

    Args:
        call_node: A call node.

    Returns:
        One NAME_REFERENCE per string.
    """
    call_name = _call_name(call_node) or ""
    reference_list: list[RReference] = []

    name_argument = _STRING_NAME_CALL_DICT.get(call_name)
    name_node = _argument_value(call_node, name_argument) if name_argument else None
    name = _string_name(call_node, name_argument) if name_argument else None
    if name and name_node is not None:
        reference_list.append(RReference(name=name, line=_line(name_node), kind=NAME_REFERENCE))

    string_node_list: list[Node] = []
    for class_argument in _CLASS_ARGUMENT_DICT.get(call_name, ()):
        string_node_list.extend(_string_node_list(_argument_value(call_node, class_argument)))
    for slot_argument in _SLOT_ARGUMENT_DICT.get(call_name, ()):
        string_node_list.extend(
            _string_node_list(_argument_value(call_node, slot_argument), is_name_only=True)
        )
    if call_name in _CLASS_STRING_CALL_SET:
        string_node_list.extend(
            value for _, value, _ in _argument_list(call_node)
            if value is not None and value.type == "string"
        )
    for string_node in string_node_list:
        class_name = _string_text(string_node)
        if class_name:
            reference_list.append(
                RReference(name=class_name, line=_line(string_node), kind=NAME_REFERENCE)
            )
    return reference_list


# A node still to read, with the name sets in scope around it (outermost first)
_ScopeNode = tuple[Node, list[set[str]]]


class _ReferenceWalker:
    """Collects the references of an R file while it reads the tree with the names in scope."""

    def __init__(self) -> None:
        """Start with no references."""
        self.reference_list: list[RReference] = []

    def walk(self, node: Node, scope_list: list[set[str]]) -> None:
        """Collect the references written in a node and below it.

        Each node is handed to the method _READER_DICT names for its type, which adds
        the references of the node and returns the nodes below it still to read; a node
        of any other type has every child read.

        Args:
            node: The node to read.
            scope_list: The name sets of the top-level statement and of the functions
                around the node, outermost first. A name in one of them is no reference.
        """
        node_stack: list[_ScopeNode] = [(node, scope_list)]
        while node_stack:
            current, current_scope_list = node_stack.pop()
            reader = self._READER_DICT.get(current.type)
            if reader is None:
                node_stack.extend((child, current_scope_list) for child in current.children)
            else:
                node_stack.extend(reader(self, current, current_scope_list))

    @staticmethod
    def _is_bound(name: str, scope_list: list[set[str]]) -> bool:
        """Return whether a name is in one of the scopes."""
        return any(name in scope for scope in scope_list)

    def _add_name(self, name: str, line: int, scope_list: list[set[str]]) -> None:
        """Add a NAME_REFERENCE unless a scope binds the name."""
        if not self._is_bound(name, scope_list):
            self.reference_list.append(RReference(name=name, line=line, kind=NAME_REFERENCE))

    def _read_comment(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read nothing of a comment."""
        return []

    def _read_identifier(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read a name written by itself."""
        self._add_name(_node_text(node).strip("`"), _line(node), scope_list)
        return []

    def _read_function(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read a function: its parameter defaults and its body, with its names in scope."""
        function_scope_list = scope_list + [_function_local_name_set(node)]
        child_list: list[_ScopeNode] = []
        parameter_holder = node.child_by_field_name("parameters")
        if parameter_holder is not None:
            for parameter in parameter_holder.children:
                default = parameter.child_by_field_name("default")
                if default is not None:
                    child_list.append((default, function_scope_list))
        body = node.child_by_field_name("body")
        if body is not None:
            child_list.append((body, function_scope_list))
        return child_list

    def _read_namespace(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read pkg::name / pkg:::name as a NAMESPACE_REFERENCE."""
        owner = _name_text(node.child_by_field_name("lhs"))
        name = _name_text(node.child_by_field_name("rhs"))
        if owner and name:
            self.reference_list.append(
                RReference(name=name, line=_line(node), kind=NAMESPACE_REFERENCE, owner=owner)
            )
        return []

    def _read_extract(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read owner$name / owner@name: the names after the operators are not read by themselves.

        owner$name$a with owner a name out of every scope is one MEMBER_REFERENCE
        (name "name", member_tuple ("a",)). With any other left side (f(x)$name) the
        left side is read as an expression.
        """
        name_list: list[str] = []
        current = node
        while current.type == "extract_operator":
            name = _name_text(current.child_by_field_name("rhs"))
            lhs = current.child_by_field_name("lhs")
            if lhs is None:
                return []
            if name:
                name_list.append(name)
            current = lhs
        if current.type != "identifier" or not name_list:
            return [(current, scope_list)]

        owner = _node_text(current).strip("`")
        if not self._is_bound(owner, scope_list):
            name_list.reverse()
            self.reference_list.append(RReference(
                name=name_list[0], line=_line(node), kind=MEMBER_REFERENCE,
                owner=owner, member_tuple=tuple(name_list[1:]),
            ))
        return []

    def _read_argument(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read the value of an argument; its name is not read."""
        value = node.child_by_field_name("value")
        return [(value, scope_list)] if value is not None else []

    def _read_for(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read the sequence and the body of a for loop; its variable is not read."""
        child_list = [node.child_by_field_name("sequence"), node.child_by_field_name("body")]
        return [(child, scope_list) for child in child_list if child is not None]

    def _read_binary(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read a binary operator: an assignment, or an expression with two sides.

        The left side of x := v is not read when it is a name. An operator written
        between "%" is a reference to the function of that name (a %+% b is a reference
        to %+%).
        """
        operator = _operator(node)
        lhs = node.child_by_field_name("lhs")
        rhs = node.child_by_field_name("rhs")
        if operator in _LEFT_ASSIGN_SET:
            return self._read_assign(lhs, rhs, operator, scope_list)
        if operator in _RIGHT_ASSIGN_SET:
            return self._read_assign(rhs, lhs, operator, scope_list)
        if operator == _COLUMN_ASSIGN and _name_text(lhs) is not None:
            return [(rhs, scope_list)] if rhs is not None else []
        if len(operator) > 2 and operator[0] == operator[-1] == "%":
            self._add_name(operator, _line(node.child_by_field_name("operator")), scope_list)
        return [(child, scope_list) for child in (lhs, rhs) if child is not None]

    def _read_assign(
        self, target: Node | None, value: Node | None, operator: str, scope_list: list[set[str]],
    ) -> list[_ScopeNode]:
        """Read an assignment.

        target a name:
            name <- v, name = v, v -> name              the name is not read
            name <<- v, v ->> name inside a function    a reference to the name, unless
                                                        a scope outside that function binds it
        any other target (names(x) <- v, x$y <- v): _read_replace_target
        The value is read in every case.
        """
        child_list: list[_ScopeNode] = [(value, scope_list)] if value is not None else []
        if target is None:
            return child_list
        name = _name_text(target)
        if name is None:
            return child_list + self._read_replace_target(target, scope_list)
        if operator in _OUTER_ASSIGN_SET and len(scope_list) > 1:
            self._add_name(name, _line(target), scope_list[:-1])
        return child_list

    def _read_replace_target(self, target: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read the target of an assignment that is not a name.

        The target is followed inwards through the first argument of each call, the
        value of each "[ ]" / "[[ ]]" and the left side of each "$" / "@":
            names(x) <- v              a reference to names<-
            names(attr(x, "a")) <- v   references to names<-, attr<- and attr
            levels(x)[2] <- v          references to levels<- and levels
            pkg::unit(x) <- v          a reference to unit<- of pkg
        The function of the outermost call is not read by itself. The other arguments
        of the calls, the indexes and what is left at the innermost place (x) are read
        as expressions. A call of a function that is not a name (x$f(y) <- v) is read
        as an expression as a whole.

        Returns:
            The nodes still to read.
        """
        child_list: list[_ScopeNode] = []
        node: Node | None = target
        is_outer = True
        while node is not None:
            name_part = _call_name_part(node) if node.type == "call" else None
            if name_part is not None:
                owner, call_name = name_part
                name_list = [call_name + _REPLACE_SUFFIX]
                if not is_outer:
                    name_list.append(call_name)
                for name in name_list:
                    if owner:
                        self.reference_list.append(RReference(
                            name=name, line=_line(node), kind=NAMESPACE_REFERENCE, owner=owner,
                        ))
                    else:
                        self._add_name(name, _line(node), scope_list)
                value_list = [value for _, value, _ in _argument_list(node) if value is not None]
                child_list.extend((value, scope_list) for value in value_list[1:])
                node = value_list[0] if value_list else None
            elif node.type in ("subset", "subset2"):
                argument_holder = node.child_by_field_name("arguments")
                if argument_holder is not None:
                    child_list.append((argument_holder, scope_list))
                node = node.child_by_field_name("function")
            elif node.type == "extract_operator":
                node = node.child_by_field_name("lhs")
            else:
                break
            is_outer = False
        if node is not None:
            child_list.append((node, scope_list))
        return child_list

    def _read_call(self, node: Node, scope_list: list[set[str]]) -> list[_ScopeNode]:
        """Read a call: the strings that name definitions, then its function and arguments.

        The arguments of box::use, library and require are not read.
        """
        name_part = _call_name_part(node)
        if name_part == ("box", "use") or (
            name_part is not None and name_part[1] in _LIBRARY_CALL_SET
        ):
            return []
        self.reference_list.extend(_string_reference_list(node))
        return [(child, scope_list) for child in node.children]

    # Node type -> method that reads a node of that type
    _READER_DICT = {
        "comment": _read_comment,
        "identifier": _read_identifier,
        "function_definition": _read_function,
        "namespace_operator": _read_namespace,
        "extract_operator": _read_extract,
        "argument": _read_argument,
        "for_statement": _read_for,
        "binary_operator": _read_binary,
        "call": _read_call,
    }


def _reference_list(root_node: Node) -> list[RReference]:
    """Return the references of an R file.

    Read as a reference:
        - an identifier (the function of a call, a value)
        - an operator written between "%" (a %+% b is a reference to %+%)
        - pkg::name and pkg:::name
        - owner$name and owner@name with owner an identifier
        - a string that names a definition (_string_reference_list)
        - f(x) <- value, a reference to f<- (_read_replace_target)
        - name <<- value and value ->> name inside a function
    Not read: the name of an argument (f(x = 1)), the name of a parameter, the target
    of any other assignment, the names after "$" / "@", the arguments of box::use,
    library and require.
    A name is no reference where it is bound: by the parameters of a function around
    it, by an assignment ("<-", "=", "->") or a for loop in the body of such a function,
    or by an assignment or a for loop inside the top-level statement it is written in
    (outside the functions of that statement; the names the statement defines are not
    bound), or by a top-level for loop of the file as its variable.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The RReference list, in the order the statements are read.
    """
    statement_list, loop_name_set = _read_top_level(root_node)
    walker = _ReferenceWalker()
    for statement in statement_list:
        local_name_set = _local_name_set(statement, _chain_node_id_set(statement))
        walker.walk(statement, [local_name_set | loop_name_set])
    return walker.reference_list


def read_r_source(root_node: Node) -> RSource:
    """Read the definitions, imports and references of an R file.

    Args:
        root_node: The AST root node of the file.

    Returns:
        The RSource of the file (r_definition_list, _import_list, _reference_list).
    """
    return RSource(
        definition_list=r_definition_list(root_node),
        import_list=_import_list(root_node),
        reference_list=_reference_list(root_node),
    )
