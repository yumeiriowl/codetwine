import re
from tree_sitter import Language, Node, Parser, Point, Range
from codetwine.parsers.parse_error import blank_text, error_line_set, error_size, is_smaller_error
from codetwine.parsers.ts_node import has_ancestor, iter_node

# A line that ends with the name of a class or object, with or without type parameters (Kotlin)
_KOTLIN_CLASS_NAME_END_RE = re.compile(
    rb"\b(?:class|object)[ \t]+`?[^\W\d]\w*`?(?:[ \t]*<[^<>\n]*>)?[ \t\r]*$"
)

# An annotation, with arguments that hold at most one level of parentheses (Kotlin); its
# name is matched whole
_KOTLIN_ANNOTATION_RE = rb"@[\w.:]++(?:\((?:[^()\n]|\([^()\n]*\))*\))?"

# A line that holds only annotations and modifiers, and a line that starts with them and
# the word "constructor" (Kotlin); a modifier is a whole run of lower-case letters
_KOTLIN_MODIFIER_RE = rb"(?:" + _KOTLIN_ANNOTATION_RE + rb"|[a-z]++)"
_KOTLIN_MODIFIER_LINE_RE = re.compile(rb"[ \t]*(?:" + _KOTLIN_MODIFIER_RE + rb"[ \t]*)+\r?")
_KOTLIN_CONSTRUCTOR_LINE_RE = re.compile(
    rb"[ \t]*(?:" + _KOTLIN_MODIFIER_RE + rb"[ \t]+)*constructor\b"
)

# Words that start a declaration and are never a name (Kotlin)
_KOTLIN_DECLARATION_KEYWORD_SET = {
    b"class", b"interface", b"object", b"fun", b"val", b"var", b"typealias",
}

# Node type of an annotation, and the text of the token that starts one (Kotlin)
_KOTLIN_ANNOTATION_TYPE_SET = {"annotation"}
_KOTLIN_ANNOTATION_START = b"@"

# The token in front of the name of a reference (Kotlin: Type::class, ::name)
_KOTLIN_REFERENCE_OPERATOR = b"::"

# The token that ends a statement, and the white space characters on a line (Kotlin)
_KOTLIN_STATEMENT_END = b";"
_KOTLIN_SPACE_SET = {b" ", b"\t"}

# A word that can be a modifier: a whole run of lower-case letters (Kotlin)
_KOTLIN_MODIFIER_WORD_RE = re.compile(rb"[a-z]+")

# A space in front of the "}" that closes a body written on the line it opens on, and
# what it is read as (Kotlin)
_KOTLIN_BODY_END = b" }"
_KOTLIN_BODY_END_REPAIR = b";}"

# A line that starts with a call of get with arguments, after its indentation; the group
# is the last white space character in front of the call (Kotlin)
_KOTLIN_GET_CALL_LINE_RE = re.compile(rb"[ \t]*([ \t])get\([ \t]*[^) \t\r\n]")

# Annotations followed by a getter or a setter with its parameters, with or without
# modifiers in between; the group is the annotations and the white space between them
# (Kotlin)
_KOTLIN_ACCESSOR_ANNOTATION_RE = re.compile(
    rb"(" + _KOTLIN_ANNOTATION_RE + rb"(?:\s+" + _KOTLIN_ANNOTATION_RE + rb")*)"
    rb"(?:\s+(?:public|protected|internal|private|inline|external))*\s+(?:get|set)[ \t]*\("
)


def _constructor_line_break_list(content: bytes, row_set: set[int]) -> list[int]:
    """Return the offsets of the line breaks between a Kotlin class name and its constructor.

    class Reader                    <- the line break after this line
    @Inject                         <- and after each line of annotations and modifiers
    internal constructor(

    Args:
        content: The file's text as UTF-8 bytes.
        row_set: Rows (0-based); a class name and a constructor are looked at only when
            one of the rows from the class name to the constructor is in it.

    Returns:
        The offset of the "\n" that ends each line from the one ending with a class
        name (_KOTLIN_CLASS_NAME_END_RE) to the one before the line that starts with
        "constructor" after its annotations and modifiers, in ascending order.
    """
    line_list = content.split(b"\n")
    line_end_list: list[int] = []
    offset = 0
    for line in line_list:
        offset += len(line)
        line_end_list.append(offset)
        offset += 1

    break_list: list[int] = []
    for index, line in enumerate(line_list):
        if index == 0 or not _KOTLIN_CONSTRUCTOR_LINE_RE.match(line):
            continue
        class_index = index - 1
        while (
            class_index > 0
            and _KOTLIN_MODIFIER_LINE_RE.fullmatch(line_list[class_index])
            and not _KOTLIN_CLASS_NAME_END_RE.search(line_list[class_index])
        ):
            class_index -= 1
        if (
            _KOTLIN_CLASS_NAME_END_RE.search(line_list[class_index])
            and not row_set.isdisjoint(range(class_index, index + 1))
        ):
            break_list.extend(line_end_list[class_index:index])
    return break_list


def _join_constructor_line(code: bytes, content: bytes, row_set: set[int]) -> bytes:
    """Return Kotlin code whose lines are joined between a class name and a constructor on a later line.

    Each line break _constructor_line_break_list() gives for the rows, and a "\\r" in
    front of it, is replaced by a space. Every other byte stays where it is.

    Args:
        code: The text to change: content with the changes of the steps of
            parse_kotlin before this one, which keep its line breaks where they are.
        content: The file's text as UTF-8 bytes; the rows are its lines.
        row_set: Rows (0-based) to change.

    Returns:
        The text.
    """
    repair_code = bytearray(code)
    for offset in _constructor_line_break_list(content, row_set):
        repair_code[offset:offset + 1] = b" "
        if content[offset - 1:offset] == b"\r":
            repair_code[offset - 1:offset] = b" "
    return bytes(repair_code)


def _join_range_list(code: bytes, content: bytes) -> list[Range]:
    """Return the ranges to parse a text whose lines are joined at some of the line breaks of a file.

    The text is cut into one range per piece between the "\\n" bytes of content that
    code holds as another byte; the range after such a line break starts at the row
    and column the file has there; a tree parsed with the ranges gives every node the
    position it has in the file.

    Args:
        code: The text to parse, with the bytes of content at the same offsets apart
            from the line breaks it joins and other bytes it changes.
        content: The file's text as UTF-8 bytes.

    Returns:
        The ranges for Parser.included_ranges; empty when code joins no line.
    """
    if code.count(b"\n") == content.count(b"\n"):
        return []
    range_list: list[Range] = []
    start = 0
    start_point = Point(0, 0)
    row = 0
    line_start = 0
    offset = content.find(b"\n")
    while offset != -1:
        if code[offset] != 0x0A:
            end = offset + 1
            range_list.append(Range(start_point, Point(row, offset - line_start + 1), start, end))
            start, start_point = end, Point(row + 1, 0)
        row += 1
        line_start = offset + 1
        offset = content.find(b"\n", line_start)
    if start < len(content):
        end_point = Point(row, len(content) - line_start)
        range_list.append(Range(start_point, end_point, start, len(content)))
    return range_list


def _repair_body_end(code: bytes, content: bytes, row_set: set[int]) -> bytes:
    """Return Kotlin code with ";" in place of the space before a "}" on the given rows.

    class A { val x = 1 }   ->   class A { val x = 1;}
    Only a row that also holds a "{" is changed. Every other byte stays where it is.

    Args:
        code: The text to change: content with the changes of the steps of
            parse_kotlin before this one, which keep its line breaks where they are.
        content: The file's text as UTF-8 bytes; the rows are its lines.
        row_set: Rows (0-based) to change.

    Returns:
        The text.
    """
    repair_code = bytearray(code)
    start = 0
    for row, line in enumerate(content.split(b"\n")):
        end = start + len(line)
        if row in row_set and b"{" in line:
            repair_code[start:end] = code[start:end].replace(
                _KOTLIN_BODY_END, _KOTLIN_BODY_END_REPAIR,
            )
        start = end + 1
    return bytes(repair_code)


def _repair_get_call(code: bytes, content: bytes, row_set: set[int]) -> bytes:
    """Return Kotlin code with ";" in front of a call of get that starts one of the given rows.

    val service by inject<Service>()
        get("/path") { ... }      ->         ;get("/path") { ... }
    The grammar reads such a call as the getter of the property declared on the line
    before it. Every other byte stays where it is.

    Args:
        code: The text to change: content with the changes of the steps of
            parse_kotlin before this one, which keep its line breaks where they are.
        content: The file's text as UTF-8 bytes; the rows are its lines.
        row_set: Rows (0-based) to change.

    Returns:
        The text.
    """
    repair_code = bytearray(code)
    start = 0
    for row, line in enumerate(content.split(b"\n")):
        line_match = _KOTLIN_GET_CALL_LINE_RE.match(line) if row in row_set else None
        if line_match is not None:
            offset = start + line_match.start(1)
            repair_code[offset:offset + 1] = b";"
        start += len(line) + 1
    return bytes(repair_code)


def _repair_accessor_annotation(code: bytes, content: bytes, row_set: set[int]) -> bytes:
    """Return Kotlin code with the annotations of a getter or setter on the given rows read as spaces.

    val size                       val size
        @JvmName("size")      ->
        get() = count()                get() = count()
    The grammar does not read an annotation of the getter of a property declared
    without a type. Only annotations followed by "get(" or "set(" (after white space
    and modifiers) whose first line is one of the rows are changed; line breaks and
    every other byte stay where they are.

    Args:
        code: The text to change: content with the changes of the steps of
            parse_kotlin before this one, which keep its line breaks where they are.
        content: The file's text as UTF-8 bytes; the rows are its lines.
        row_set: Rows (0-based) to change.

    Returns:
        The text.
    """
    repair_code = bytearray(code)
    row = 0
    row_byte = 0
    for annotation_match in _KOTLIN_ACCESSOR_ANNOTATION_RE.finditer(code):
        start, end = annotation_match.span(1)
        row += content.count(b"\n", row_byte, start)
        row_byte = start
        if row in row_set:
            repair_code[start:end] = blank_text(code[start:end])
    return bytes(repair_code)


def _declaration_annotation_span_list(content: bytes, root_node: Node) -> list[tuple[int, int]]:
    """Return the spans of the annotations of the top-level declarations a tree reads as expressions (Kotlin).

    @Target(TYPE) annotation class Q          -> the span of "@Target(TYPE) "
    @Suppress("x") enum class E { A }         -> the span of '@Suppress("x") '
    The grammar can read annotations and the declaration after them as an
    expression, without an error, with the word that starts the declaration as a
    name.

    Args:
        content: The file's text as UTF-8 bytes.
        root_node: The tree parsed from content.

    Returns:
        (start byte, end byte) for each child of root_node that starts with "@" and
        whose first token of a text of _KOTLIN_DECLARATION_KEYWORD_SET, other than
        one after "::" (Type::class), is an identifier: from its start to the first of the identifiers of lower-case
        letters outside an annotation that come right before that token; none when no
        text comes before them.
    """
    span_list: list[tuple[int, int]] = []
    for statement in root_node.children:
        if not content.startswith(_KOTLIN_ANNOTATION_START, statement.start_byte):
            continue
        leaf_list: list[Node] = []
        keyword_node = None
        for node in iter_node(statement):
            if node.child_count:
                continue
            if node.text in _KOTLIN_DECLARATION_KEYWORD_SET and not (
                leaf_list and leaf_list[-1].text == _KOTLIN_REFERENCE_OPERATOR
            ):
                keyword_node = node if node.type == "identifier" else None
                break
            leaf_list.append(node)
        if keyword_node is None:
            continue
        start_node = keyword_node
        while (
            leaf_list and leaf_list[-1].type == "identifier"
            and _KOTLIN_MODIFIER_WORD_RE.fullmatch(leaf_list[-1].text)
            and not has_ancestor(leaf_list[-1], _KOTLIN_ANNOTATION_TYPE_SET)
        ):
            start_node = leaf_list.pop()
        if start_node.start_byte > statement.start_byte:
            span_list.append((statement.start_byte, start_node.start_byte))
    return span_list


def _separate_declaration_annotation(content: bytes, span_list: list[tuple[int, int]]) -> bytes:
    """Return Kotlin code with the annotations of the given spans as statements of their own.

    @Qualifier @Retention(RUNTIME) annotation class Named
        ->  ;Qualifier ;Retention(RUNTIME);annotation class Named
    Each "@" of a span is read as ";", and so is the space or tab that ends a span;
    the names of the annotations stay where they are.

    Args:
        content: The file's text as UTF-8 bytes.
        span_list: (start byte, end byte) of each span (_declaration_annotation_span_list).

    Returns:
        The text, with its line breaks and every other byte where they are.
    """
    repair_code = bytearray(content)
    for start, end in span_list:
        repair_code[start:end] = content[start:end].replace(
            _KOTLIN_ANNOTATION_START, _KOTLIN_STATEMENT_END,
        )
        if content[end - 1:end] in _KOTLIN_SPACE_SET:
            repair_code[end - 1:end] = _KOTLIN_STATEMENT_END
    return bytes(repair_code)


def _keeps_statement(root_node: Node, other_root_node: Node, skip_start_set: set[int]) -> bool:
    """Return whether a parse reads every top-level statement of another parse as that one does.

    Args:
        root_node: The AST root node of a parse.
        other_root_node: The AST root node of a parse of a text with the same
            positions.
        skip_start_set: Start bytes of the statements of root_node not looked at.

    Returns:
        True when each child of root_node other than an ERROR node and those starting
        at a byte of skip_start_set is matched by a child of other_root_node of the
        same type that starts at the same byte.
    """
    other_statement_set = {(child.start_byte, child.type) for child in other_root_node.children}
    return all(
        (child.start_byte, child.type) in other_statement_set
        for child in root_node.children
        if child.type != "ERROR" and child.start_byte not in skip_start_set
    )


# The steps of parse_kotlin, in the order they change the text
_KOTLIN_REPAIR_TUPLE = (
    _repair_body_end, _repair_get_call, _repair_accessor_annotation, _join_constructor_line,
)


def _repaired_root_node(code: bytes, content: bytes, root_node: Node, language: Language) -> Node:
    """Return the parse of the smallest part not read that the steps of _KOTLIN_REPAIR_TUPLE give (Kotlin).

    The steps change the text one after the other, each on the rows of the ERROR
    nodes of the parse the steps before it leave (error_line_set); each step changes
    the text those steps leave, and its change is left for the steps after it unless
    it makes the part of the tree the grammar did not read larger (error_size).

    Args:
        code: The text parsed into root_node: content, or content with bytes other
            than its line breaks changed.
        content: The file's text as UTF-8 bytes; the rows are its lines.
        root_node: The AST root node parsed from code.
        language: The tree-sitter Language of the file.

    Returns:
        root_node when it has no error, else the first parse of the smallest part not
        read among root_node and the parses of the steps.
    """
    size_tuple = error_size(root_node)
    best_root_node, best_size_tuple = root_node, size_tuple
    for repair in _KOTLIN_REPAIR_TUPLE:
        if not root_node.has_error:
            break
        repair_code = repair(code, content, error_line_set(root_node))
        if repair_code == code:
            continue
        range_list = _join_range_list(repair_code, content)
        parser = Parser(language, included_ranges=range_list) if range_list else Parser(language)
        repair_root_node = parser.parse(repair_code).root_node
        repair_size_tuple = error_size(repair_root_node)
        if is_smaller_error(size_tuple, repair_size_tuple):
            continue
        root_node, size_tuple, code = repair_root_node, repair_size_tuple, repair_code
        if is_smaller_error(size_tuple, best_size_tuple):
            best_root_node, best_size_tuple = root_node, size_tuple
    return best_root_node


def parse_kotlin(content: bytes, language: Language) -> Node:
    """Parse a Kotlin file, reading five forms the grammar does not read in another way.

    A file the grammar reads without an error and without a top-level declaration
    read as an expression is returned as it is parsed. Else the four steps below
    (_repaired_root_node) are taken on the parse of the file, and on the parse of the
    text whose annotations in front of such declarations are statements of their own
    (_separate_declaration_annotation); the second is kept when it reads the other
    top-level statements as the first parse of the file does (_keeps_statement) and
    the part of its tree the grammar did not read is not larger (error_size). The
    steps:
    1. The space before a "}" that closes a body on the row it opens on is read as ";"
       (_repair_body_end).
    2. The white space in front of a call of get that starts a row is read as ";"
       (_repair_get_call).
    3. The annotations in front of a getter or setter are read as spaces
       (_repair_accessor_annotation).
    4. The line breaks between a class name and a constructor written on a later line
       are read as spaces (_join_constructor_line); the text is parsed in ranges cut at
       those line breaks (_join_range_list), so every node keeps the position it has in
       the file.

    Args:
        content: The file's text as UTF-8 bytes.
        language: The tree-sitter Language of the file.

    Returns:
        The AST root node of the parse kept.
    """
    root_node = Parser(language).parse(content).root_node
    span_list = _declaration_annotation_span_list(content, root_node)
    repaired_root_node = _repaired_root_node(content, content, root_node, language)
    if not span_list:
        return repaired_root_node
    annotation_code = _separate_declaration_annotation(content, span_list)
    annotation_root_node = _repaired_root_node(
        annotation_code, content, Parser(language).parse(annotation_code).root_node, language,
    )
    if not _keeps_statement(
        root_node, annotation_root_node, {start for start, _ in span_list},
    ) or is_smaller_error(error_size(repaired_root_node), error_size(annotation_root_node)):
        return repaired_root_node
    return annotation_root_node
