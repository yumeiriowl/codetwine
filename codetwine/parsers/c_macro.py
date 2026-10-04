import re
from tree_sitter import Language, Node, Parser, Query, QueryCursor
from codetwine.parsers.parse_error import error_size, first_error_node, is_smaller_error

# A function definition whose return type is a class, struct or union written with its
# keyword and without a body, and whose declarator is a plain name (C / C++); the C
# grammar has no class_specifier
_CLASS_MACRO_QUERY_TUPLE = (
    "(function_definition type: (struct_specifier name: (_) @macro !body) declarator: (identifier))",
    "(function_definition type: (union_specifier name: (_) @macro !body) declarator: (identifier))",
    "(function_definition type: (class_specifier name: (_) @macro !body) declarator: (identifier))",
)

# Compiled queries of _CLASS_MACRO_QUERY_TUPLE: id of the Language -> Query
_class_macro_query_cache: dict[int, Query] = {}

# How many times a C / C++ file is parsed again with macro names blanked, for each of the
# two kinds of macro names (parse_c_family)
_MACRO_PASS_MAX = 8

# A line that holds one upper-case name and nothing else (C / C++: a macro written as a
# line of its own)
_MACRO_LINE_RE = re.compile(rb"^[ \t]*([A-Z_][A-Z0-9_]+)[ \t]*\r?$", re.MULTILINE)

# An upper-case name (C / C++: a macro written next to the type of a declaration)
_MACRO_NAME_RE = re.compile(rb"[A-Z_][A-Z0-9_]+")

# A name written in the arguments of a macro
_ARGUMENT_NAME_RE = re.compile(rb"[A-Za-z_]\w*")

# The text between the type of a declaration and the name it declares
_DECLARATION_GAP_RE = re.compile(rb"[\w\s*&]*")

# Node types of the names of an ERROR node that holds the type of a declaration (C / C++)
_TYPE_NAME_NODE_TYPE_SET = {"identifier", "type_identifier", "primitive_type"}

# A backslash in front of a line break written as "\r\n", and what it is read as: the
# grammar reads a line as continued only when the backslash is directly in front of "\n"
_CR_CONTINUATION = b"\\\r\n"
_CR_CONTINUATION_REPAIR = b" \\\n"

# Node types of a declaration that has a declarator (C / C++)
_DECLARATION_NODE_TYPE_SET = {"function_definition", "declaration", "field_declaration"}

# Node types of a declarator that is a plain name (C / C++)
_NAME_NODE_TYPE_SET = {"identifier", "field_identifier"}


def _class_macro_query(language: Language) -> Query:
    """Return the query of _CLASS_MACRO_QUERY_TUPLE for a language, compiled once.

    A pattern that names a node type the grammar of the language lacks is left out.
    """
    query = _class_macro_query_cache.get(id(language))
    if query is None:
        pattern_list = []
        for pattern in _CLASS_MACRO_QUERY_TUPLE:
            try:
                Query(language, pattern)
            except Exception:
                continue
            pattern_list.append(pattern)
        query = Query(language, "\n".join(pattern_list))
        _class_macro_query_cache[id(language)] = query
    return query


def _class_macro_range_list(root_node: Node, language: Language) -> list[tuple[int, int]]:
    """Return the byte ranges of the macro names written between a class keyword and a class name.

    class EXPORT Shape : public Base { ... }; is read by the grammar as a function
    definition whose return type is "class EXPORT" and whose declarator is a plain
    name. No function is written that way, so the name of such a type is a macro.

    Args:
        root_node: The AST root node of a C / C++ file.
        language: The tree-sitter Language of the file.

    Returns:
        (start byte, end byte) of each such macro name.
    """
    capture_dict = QueryCursor(_class_macro_query(language)).captures(root_node)
    return [(node.start_byte, node.end_byte) for node in capture_dict.get("macro", [])]


def _blank_code(
    code: bytes, range_list: list[tuple[int, int]], macro_list: list[tuple[str, int]],
) -> bytes:
    """Return code with the given byte ranges replaced by spaces of the same length.

    Args:
        code: The text of a file as UTF-8 bytes.
        range_list: (start byte, end byte) of each name to replace, with the arguments
            it is written with if any.
        macro_list: (name, line) of each name replaced is added to it, and of each name
            written in its arguments.

    Returns:
        The text; the line breaks of a range and every other byte stay where they are.
    """
    blank_code = bytearray(code)
    line = 1
    line_byte = 0
    for start_byte, end_byte in sorted(range_list):
        line += code.count(b"\n", line_byte, start_byte)
        line_byte = start_byte
        name_match = _MACRO_NAME_RE.match(code, start_byte, end_byte)
        name_end_byte = name_match.end() if name_match is not None else end_byte
        macro_list.append((code[start_byte:name_end_byte].decode("utf-8"), line))
        for argument_match in _ARGUMENT_NAME_RE.finditer(code, name_end_byte, end_byte):
            macro_list.append((
                argument_match.group().decode("utf-8"),
                line + code.count(b"\n", start_byte, argument_match.start()),
            ))
        blank_code[start_byte:end_byte] = bytes(
            byte if byte in b"\r\n" else 0x20 for byte in code[start_byte:end_byte]
        )
    return bytes(blank_code)


def _line_macro_range_list(root_node: Node, code: bytes) -> list[tuple[int, int]]:
    """Return the byte ranges of the macro names written as a line of their own that the grammar misreads.

    struct node { NODE_FIELDS int own; }; with NODE_FIELDS on a line of its own is
    read as a member of the type NODE_FIELDS, and BEGIN_DECLS on a line of its own as
    the type of the declaration after it. Such a name is one that _MACRO_LINE_RE
    matches and that is, in a part of the tree the grammar did not read without an
    error, the type of a declaration, a statement or a direct child of an ERROR node.

    Args:
        root_node: The AST root node of a C / C++ file.
        code: The text the tree was parsed from.

    Returns:
        (start byte, end byte) of each such name.
    """
    range_list: list[tuple[int, int]] = []
    for line_match in _MACRO_LINE_RE.finditer(code):
        start_byte, end_byte = line_match.span(1)
        name_node = root_node.descendant_for_byte_range(start_byte, end_byte)
        if (
            name_node is None or name_node.start_byte != start_byte
            or name_node.end_byte != end_byte or name_node.child_count
        ):
            continue
        parent = name_node.parent
        if parent is None or not parent.has_error:
            continue
        if (
            parent.type == "ERROR"
            or parent.child_by_field_name("type") == name_node
            or parent.type == "expression_statement"
        ):
            range_list.append((start_byte, end_byte))
    return range_list


def _declaration_macro_range_list(root_node: Node, code: bytes) -> list[tuple[int, int]]:
    """Return the byte ranges of the macro names written next to the type of a declaration.

    EXPORT int f(void);      the type is read as EXPORT and "int" as an ERROR node
    int STDCALL f(int a);        STDCALL is read as an ERROR node after the type
    The node read as the type and the names of the ERROR node after it on the same row
    are looked at: the upper-case names of the ERROR node are the macros, or, when it
    has none, the type when it is an upper-case name. A type that is an upper-case
    name is a macro as well when the first thing the grammar did not read in the
    declaration is a missing token after one more name on the same row.

    Args:
        root_node: The AST root node of a C / C++ file.
        code: The text the tree was parsed from.

    Returns:
        (start byte, end byte) of each such name.
    """
    range_list: list[tuple[int, int]] = []
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        if not node.has_error:
            continue
        node_stack.extend(node.children)
        type_node = node.child_by_field_name("type")
        if type_node is None or node.type == "ERROR":
            continue
        row = type_node.end_point[0]
        error_node = next(
            (
                child for child in node.children
                if child.start_byte >= type_node.end_byte and child.has_error
            ),
            None,
        )
        if error_node is None or error_node.start_point[0] != row:
            continue
        is_macro_type = (
            type_node.type == "type_identifier" and _MACRO_NAME_RE.fullmatch(type_node.text)
        )
        if error_node.type == "ERROR":
            name_node_list = error_node.children
            if not all(child.type in _TYPE_NAME_NODE_TYPE_SET for child in name_node_list):
                continue
            macro_node_list = [
                child for child in name_node_list if _MACRO_NAME_RE.fullmatch(child.text)
            ]
            if not macro_node_list and is_macro_type:
                macro_node_list = [type_node]
            range_list.extend((child.start_byte, child.end_byte) for child in macro_node_list)
        elif is_macro_type:
            first_node = first_error_node(error_node)
            if (
                first_node is not None and first_node.is_missing
                and first_node.start_point[0] == row
                and _DECLARATION_GAP_RE.fullmatch(code[type_node.end_byte:first_node.start_byte])
            ):
                range_list.append((type_node.start_byte, type_node.end_byte))
    return range_list


def _macro_declarator_node(node: Node) -> Node | None:
    """Return the declarator of a declaration when it is written like a macro.

    Args:
        node: A node with a field "declarator".

    Returns:
        The node in the field "declarator" when it is an upper-case name
        (_MACRO_NAME_RE), by itself or called with arguments (a function_declarator
        whose name it is); else None.
    """
    declarator_node = node.child_by_field_name("declarator")
    if declarator_node is None:
        return None
    name_node = declarator_node
    if declarator_node.type == "function_declarator":
        name_node = declarator_node.child_by_field_name("declarator")
    if (
        name_node is None or name_node.type not in _NAME_NODE_TYPE_SET
        or not _MACRO_NAME_RE.fullmatch(name_node.text)
    ):
        return None
    return declarator_node


def _suffix_macro_range_list(root_node: Node) -> list[tuple[int, int]]:
    """Return the byte ranges of the macros written after the declarator of a declaration.

    void Lock() ACQUIRE() { ... }     the declarator is read as ACQUIRE() and "Lock()"
    int size_ GUARDED_BY(mu_);        as an ERROR node in front of it
    void Stop() NOEXCEPT_MACRO;
    Status Read(int* out)             the declaration ends in a missing ";" and the
        REQUIRES(mu_);                macro is read as a declaration without a type
    int count GUARDED_BY(mu);         read as the function count::GUARDED_BY with a
                                      missing "::"
    The macro is the declarator _macro_declarator_node() gives for a node that has an
    ERROR node with a name in front of that declarator, or for a declaration that has
    no type and directly follows a declaration ending in a missing ";"; and the name
    and the parameters of a function_declarator whose name is an upper-case name
    written after a missing "::".

    Args:
        root_node: The AST root node of a C / C++ file.

    Returns:
        (start byte, end byte) of each such macro, with its arguments.
    """
    range_list: list[tuple[int, int]] = []
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        if not node.has_error:
            continue
        child_list = node.children
        node_stack.extend(child_list)
        macro_node = _macro_declarator_node(node)
        if macro_node is not None and any(
            child.type == "ERROR" and child.end_byte <= macro_node.start_byte
            and child.named_child_count
            for child in child_list
        ):
            range_list.append((macro_node.start_byte, macro_node.end_byte))
        if node.type == "function_declarator":
            path_node = node.child_by_field_name("declarator")
            name_node = (
                path_node.child_by_field_name("name")
                if path_node is not None and path_node.type == "qualified_identifier"
                else None
            )
            if (
                name_node is not None and name_node.type == "identifier"
                and _MACRO_NAME_RE.fullmatch(name_node.text)
                and any(child.is_missing and child.type == "::" for child in path_node.children)
            ):
                range_list.append((name_node.start_byte, node.end_byte))
        for before_node, after_node in zip(child_list, child_list[1:]):
            if (
                before_node.type not in _DECLARATION_NODE_TYPE_SET
                or after_node.type not in _DECLARATION_NODE_TYPE_SET
                or not before_node.child_count or not before_node.children[-1].is_missing
                or after_node.child_by_field_name("type") is not None
            ):
                continue
            macro_node = _macro_declarator_node(after_node)
            if macro_node is not None:
                range_list.append((macro_node.start_byte, macro_node.end_byte))
    return range_list


def parse_c_family(content: bytes, language: Language) -> tuple[Node, list[tuple[str, int]]]:
    """Parse a C / C++ file, reading macro names the grammar misreads as spaces.

    A backslash in front of a line break written as "\\r\\n" is read as a space and a
    backslash in front of "\\n", so the line counts as continued (a string or a macro
    written over several lines in a file with such line breaks).

    The macro names _class_macro_range_list() finds are replaced by spaces of the same
    length and the text is parsed again, until none is left or _MACRO_PASS_MAX passes
    are done. Then the names _line_macro_range_list() and
    _declaration_macro_range_list() find, and the macros _suffix_macro_range_list()
    finds, are replaced the same way, as long as a pass makes the part of the tree the
    grammar did not read smaller (error_size). Every other byte stays where it is.

    Args:
        content: The file's text as UTF-8 bytes.
        language: The tree-sitter Language of the file.

    Returns:
        (AST root node of the last parse, (macro name, line) of each name replaced, in
        line order; a macro replaced with its arguments is listed by its name, followed
        by the names written in the arguments).
    """
    parser = Parser(language)
    code = content.replace(_CR_CONTINUATION, _CR_CONTINUATION_REPAIR)
    root_node = parser.parse(code).root_node
    macro_list: list[tuple[str, int]] = []
    for _ in range(_MACRO_PASS_MAX):
        range_list = _class_macro_range_list(root_node, language)
        if not range_list:
            break
        code = _blank_code(code, range_list, macro_list)
        root_node = parser.parse(code).root_node

    for _ in range(_MACRO_PASS_MAX):
        range_list = sorted({
            *_line_macro_range_list(root_node, code),
            *_declaration_macro_range_list(root_node, code),
            *_suffix_macro_range_list(root_node),
        }) if root_node.has_error else []
        if not range_list:
            break
        pass_macro_list: list[tuple[str, int]] = []
        blank_code = _blank_code(code, range_list, pass_macro_list)
        blank_root_node = parser.parse(blank_code).root_node
        if not is_smaller_error(error_size(blank_root_node), error_size(root_node)):
            break
        code, root_node = blank_code, blank_root_node
        macro_list.extend(pass_macro_list)
    return root_node, sorted(macro_list, key=lambda macro: macro[1])
