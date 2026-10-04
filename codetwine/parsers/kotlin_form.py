import re
from tree_sitter import Language, Node, Parser, Point, Range
from codetwine.parsers.parse_error import error_line_set, error_size, is_smaller_error

# A line that ends with the name of a class or object, with or without type parameters (Kotlin)
_KOTLIN_CLASS_NAME_END_RE = re.compile(
    rb"\b(?:class|object)[ \t]+`?[^\W\d]\w*`?(?:[ \t]*<[^<>\n]*>)?[ \t\r]*$"
)

# A line that holds only annotations and modifiers, and a line that starts with them and
# the word "constructor" (Kotlin)
_KOTLIN_MODIFIER_RE = rb"(?:@[\w.:]+(?:\([^()\n]*\))?|[a-z]+)"
_KOTLIN_MODIFIER_LINE_RE = re.compile(rb"[ \t]*(?:" + _KOTLIN_MODIFIER_RE + rb"[ \t]*)+\r?")
_KOTLIN_CONSTRUCTOR_LINE_RE = re.compile(
    rb"[ \t]*(?:" + _KOTLIN_MODIFIER_RE + rb"[ \t]+)*constructor\b"
)

# A space in front of the "}" that closes a body written on the line it opens on, and
# what it is read as (Kotlin)
_KOTLIN_BODY_END = b" }"
_KOTLIN_BODY_END_REPAIR = b";}"

# A line that starts with a call of get with arguments, after its indentation; the group
# is the last white space character in front of the call (Kotlin)
_KOTLIN_GET_CALL_LINE_RE = re.compile(rb"[ \t]*([ \t])get\([ \t]*[^) \t\r\n]")


def _constructor_line_break_list(content: bytes) -> list[int]:
    """Return the offsets of the line breaks between a Kotlin class name and its constructor.

    class Reader                    <- the line break after this line
    @Inject                         <- and after each line of annotations and modifiers
    internal constructor(

    Args:
        content: The file's text as UTF-8 bytes.

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
        if _KOTLIN_CLASS_NAME_END_RE.search(line_list[class_index]):
            break_list.extend(line_end_list[class_index:index])
    return break_list


def _join_line(content: bytes, break_list: list[int]) -> tuple[bytes, list[Range]]:
    """Return a text whose lines are joined at the given line breaks, and the ranges to parse it with.

    Each of the line breaks is replaced by a space, and the text is cut into one range
    per piece between them; the range after a line break starts at the row and column
    the original text has there; a tree parsed with the ranges gives every node the
    position it has in the file.

    Args:
        content: The text as UTF-8 bytes.
        break_list: Offsets of the "\n" bytes to replace, in ascending order.

    Returns:
        (the text, the ranges for Parser.included_ranges).
    """
    def point_of(offset: int) -> Point:
        """Return the row and column of an offset of the original text."""
        return Point(
            content.count(b"\n", 0, offset), offset - (content.rfind(b"\n", 0, offset) + 1),
        )

    code = bytearray(content)
    range_list: list[Range] = []
    start = 0
    for offset in break_list:
        end = offset + 1
        code[offset:end] = b" "
        break_point = point_of(offset)
        range_list.append(Range(
            point_of(start), Point(break_point.row, break_point.column + 1), start, end,
        ))
        start = end
    if start < len(content):
        range_list.append(Range(point_of(start), point_of(len(content)), start, len(content)))
    return bytes(code), range_list


def _repair_body_end(code: bytes, content: bytes, row_set: set[int]) -> bytes:
    """Return Kotlin code with ";" in place of the space before a "}" on the given rows.

    class A { val x = 1 }   ->   class A { val x = 1;}
    Only a row that also holds a "{" is changed. Every other byte stays where it is.

    Args:
        code: The text to change, with the bytes of content at the same offsets apart
            from line breaks read as spaces.
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
        code: The text to change, with the bytes of content at the same offsets apart
            from line breaks read as spaces.
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


def parse_kotlin(content: bytes, language: Language) -> Node:
    """Parse a Kotlin file, reading three forms the grammar does not read in another way.

    A file the grammar reads without an error is returned as it is parsed. Else, one
    after the other, each kept only when it makes the part of the tree the grammar did
    not read smaller (error_size):
    1. The line breaks between a class name and a constructor written on a later line
       are read as spaces (_constructor_line_break_list, _join_line); every node keeps
       the position it has in the file.
    2. On the rows of the ERROR nodes, the space before a "}" that closes a body on the
       row it opens on is read as ";" (_repair_body_end).
    3. On the rows of the ERROR nodes, the white space in front of a call of get that
       starts a row is read as ";" (_repair_get_call).

    Args:
        content: The file's text as UTF-8 bytes.
        language: The tree-sitter Language of the file.

    Returns:
        The AST root node of the parse kept.
    """
    root_node = Parser(language).parse(content).root_node
    if not root_node.has_error:
        return root_node
    best_error_size = error_size(root_node)

    # == Step 1: Constructors written on the line after the class name =========
    code = content
    range_list: list[Range] | None = None
    break_list = _constructor_line_break_list(content)
    if break_list:
        join_code, join_range_list = _join_line(content, break_list)
        join_root_node = Parser(language, included_ranges=join_range_list).parse(join_code).root_node
        join_error_size = error_size(join_root_node)
        if is_smaller_error(join_error_size, best_error_size):
            root_node, best_error_size = join_root_node, join_error_size
            code, range_list = join_code, join_range_list

    # == Step 2, 3: Bodies written on one line, calls of get at the start of a row ==
    for repair in (_repair_body_end, _repair_get_call):
        if not root_node.has_error:
            break
        repair_code = repair(code, content, error_line_set(root_node))
        if repair_code == code:
            continue
        parser = Parser(language, included_ranges=range_list) if range_list else Parser(language)
        repair_root_node = parser.parse(repair_code).root_node
        repair_error_size = error_size(repair_root_node)
        if is_smaller_error(repair_error_size, best_error_size):
            root_node, best_error_size, code = repair_root_node, repair_error_size, repair_code
    return root_node
