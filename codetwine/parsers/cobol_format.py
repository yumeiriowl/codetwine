import re
import unicodedata
from dataclasses import dataclass, field

# == Columns of a fixed-format line (0-based) ================================
# Column of the indicator ("*" / "/" comment, "-" continuation, "D" debug line)
_INDICATOR_COLUMN = 6
# First column of the code area
_CODE_START_COLUMN = 7
# Column after the last one of the code area
_CODE_END_COLUMN = 72
# Column after the last one of the identification area
_LINE_END_COLUMN = 80
# Number of columns a tab character moves to a multiple of
_TAB_SIZE = 8

# Characters the indicator column of a fixed-format line can hold
_INDICATOR_CHAR_SET = set(" */-dD$")
# Indicator characters of a line that holds no code
_NO_CODE_INDICATOR_CHAR_SET = set("*/dD$")

# Left margin of every line of the text of a unit
_LEFT_MARGIN = " " * _CODE_START_COLUMN
# Number of characters the code area of one line holds
_CODE_WIDTH = _CODE_END_COLUMN - _CODE_START_COLUMN

# Starts of the code of a directive line
_DIRECTIVE_START_TUPLE = (">>", "$", "@")
# Starts of a free-format line that holds no code
_NO_CODE_START_TUPLE = ("*", *_DIRECTIVE_START_TUPLE)

# Compiler option lines, written from column 1 or in front of the first line with code
_OPTION_LINE_RE = re.compile(r"^(?:CBL|PROCESS)\s", re.IGNORECASE)
# Directive that names the source format of the lines after it
_FORMAT_DIRECTIVE_RE = re.compile(
    r"^(?:>>\s*SOURCE\s+(?:FORMAT\s+)?(?:IS\s+)?|\$\s*SET\s+SOURCEFORMAT\W*)(FREE|FIXED|VARIABLE)",
    re.IGNORECASE,
)

# == Token patterns ===========================================================
_SPACE_RE = re.compile(r"\s+")
_LITERAL_START_RE = re.compile(r"[A-Za-z]{0,2}[\"']")
_PSEUDO_TEXT_RE = re.compile(r"==.*?==")
_NAME_TAG_PATTERN = r":[A-Za-z0-9\-]+:"
_WORD_RE = re.compile(
    rf"(?:{_NAME_TAG_PATTERN}|[A-Za-z0-9_]|[^\x00-\x7f\s])"
    rf"(?:[A-Za-z0-9_\-]|[^\x00-\x7f\s]|{_NAME_TAG_PATTERN})*"
)
_PICTURE_RE = re.compile(r"\S+")
# A word the grammar reads as a name
GRAMMAR_NAME_RE = re.compile(r"[A-Za-z0-9\-]+")

# Words that start the picture string after them
_PICTURE_WORD_SET = {"PIC", "PICTURE"}
# Words of a statement that holds no code
_NO_CODE_WORD_SET = {"EJECT", "SKIP1", "SKIP2", "SKIP3"}
# Words before DIVISION in the header of the first division of a program
_IDENTIFICATION_WORD_SET = {"IDENTIFICATION", "ID"}
# Words before SECTION in the header of a section of the data division
_DATA_SECTION_WORD_SET = {
    "FILE", "WORKING-STORAGE", "LOCAL-STORAGE", "LINKAGE", "SCREEN", "REPORT",
}
# Those of the sections whose statements are in no unit
_NO_UNIT_SECTION_WORD_SET = {"SCREEN", "REPORT"}
# Words that start the description of a file
_FILE_ENTRY_WORD_SET = {"FD", "SD"}
# Characters of an operator that a word before or after it is separated from
_OPERATOR_CHAR_SET = set("=<>")

# == Divisions ================================================================
IDENTIFICATION_DIVISION = "IDENTIFICATION"
ENVIRONMENT_DIVISION = "ENVIRONMENT"
DATA_DIVISION = "DATA"
PROCEDURE_DIVISION = "PROCEDURE"
_DIVISION_WORD_SET = {ENVIRONMENT_DIVISION, DATA_DIVISION, PROCEDURE_DIVISION}

# == Unit kinds ===============================================================
# A data item
ITEM_UNIT = "item"
# The description of a file (FD / SD)
FILE_UNIT = "file"
# A SELECT entry
SELECT_UNIT = "select"
# A sentence of the procedure division
PROCEDURE_UNIT = "procedure"

# Lines written in front of the statement of a unit, by unit kind
_IDENTIFICATION_LINE = "IDENTIFICATION DIVISION."
_START_LINE_DICT = {
    ITEM_UNIT: [_IDENTIFICATION_LINE, "DATA DIVISION.", "WORKING-STORAGE SECTION."],
    FILE_UNIT: [_IDENTIFICATION_LINE, "DATA DIVISION.", "FILE SECTION."],
    SELECT_UNIT: [
        _IDENTIFICATION_LINE, "ENVIRONMENT DIVISION.", "INPUT-OUTPUT SECTION.", "FILE-CONTROL.",
    ],
    PROCEDURE_UNIT: [_IDENTIFICATION_LINE, "PROCEDURE DIVISION."],
}
# Line written after the description of a file
_FILLER_ITEM_LINE = "01 FILLER PIC X."
# Line written after a sentence of the procedure division that is one name
_CONTINUE_LINE = "CONTINUE."

# Start of the names that stand in for the names the grammar does not read
_STAND_IN_PREFIX = "QX"
# Character appended to the start of the stand-in names while a word of the source starts with it
_STAND_IN_PREFIX_CHAR = "Q"


@dataclass
class _Token:
    """One token of the code of a COBOL source file."""

    # "word" / "literal" / "picture" / "pseudo" / "period" / "other"
    kind: str
    text: str
    # Line of the source the token starts on (1-based)
    line: int
    # Column of the code text of the line the token starts at
    column: int = 0
    # Whether whitespace or a line start comes before the token
    has_gap: bool = True
    # Whether the token continues the last token of the line before it
    is_glue: bool = False


@dataclass
class CobolUnit:
    """One statement of a COBOL file as fixed-format text the grammar reads."""

    # ITEM_UNIT / FILE_UNIT / SELECT_UNIT / PROCEDURE_UNIT
    kind: str
    # The header lines of a program, then the statement: comments removed, every
    # name written in ASCII, no EXEC block, no COPY statement
    text: str
    # Source line (1-based) of each line of text; 0 for a line that is added
    origin_line_list: list[int]
    # (word as written in the source, source line) of the words of the statement
    word_list: list[tuple[str, int]]


@dataclass
class CobolCopy:
    """One COPY statement or EXEC SQL INCLUDE."""

    name: str      # Name of the copybook
    line: int      # Source line of the statement (1-based)
    library: str = ""  # Library name of COPY ... OF library
    # REPLACING operands as (position, replaced text, replacement text);
    # position is "" / "LEADING" / "TRAILING"
    replacing_list: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class CobolText:
    """A COBOL source file split into the units the grammar reads and what is read without it."""

    # Data items, descriptions of files, SELECT entries and sentences of the
    # procedure division, in source order
    unit_list: list[CobolUnit] = field(default_factory=list)
    # Stand-in name written in the units (upper case) -> the name written in the source
    name_dict: dict[str, str] = field(default_factory=dict)
    # (name, line of the start of the program, line of the name) of each PROGRAM-ID
    program_list: list[tuple[str, int, int]] = field(default_factory=list)
    # (name, source line) of each END PROGRAM
    program_end_list: list[tuple[str, int]] = field(default_factory=list)
    # (name, source line) of each ENTRY statement
    entry_list: list[tuple[str, int]] = field(default_factory=list)
    # COPY statements and EXEC SQL INCLUDE
    copy_list: list[CobolCopy] = field(default_factory=list)
    # (name, whether it is a literal, source line) of each CALL statement and of each
    # EXEC CICS command with PROGRAM(name); a name that is not a literal is a data item
    call_list: list[tuple[str, bool, int]] = field(default_factory=list)
    # (word, source line) of the words of the statements that are in no unit
    word_list: list[tuple[str, int]] = field(default_factory=list)
    # Source lines that hold code, in ascending order
    code_line_list: list[int] = field(default_factory=list)


def _char_width(char: str) -> int:
    """Return the number of columns a character takes (2 for a full-width character)."""
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _line_width(line: str) -> int:
    """Return the number of columns a line takes."""
    if line.isascii():
        return len(line)
    return sum(_char_width(char) for char in line)


def _column_slice(line: str, start: int, end: int | None) -> str:
    """Return the characters of a line that start in the columns from start up to end.

    Examples:
        ("000100 MOVE A TO B.", 7, 72)  -> "MOVE A TO B."
        ("       01 顧客 PIC X.  0001", 7, 22) -> "01 顧客 PIC X.  "

    Args:
        line: One line without tab characters.
        start: First column (0-based).
        end: Column after the last one. None takes the line to its end.

    Returns:
        The characters in that column range.
    """
    if line.isascii():
        return line[start:end]
    char_list: list[str] = []
    column = 0
    for char in line:
        if end is not None and column >= end:
            break
        if column >= start:
            char_list.append(char)
        column += _char_width(char)
    return "".join(char_list)


def _indicator(line: str) -> str:
    """Return the character in the indicator column of a line, or "" when no character starts there."""
    return _column_slice(line, _INDICATOR_COLUMN, _CODE_START_COLUMN)


def _is_directive_line(line: str) -> bool:
    """Return whether a line is a compiler directive (">>", "$" or "@" first in the line or from the indicator column)."""
    return any(
        text.lstrip().startswith(_DIRECTIVE_START_TUPLE)
        for text in (line, _column_slice(line, _INDICATOR_COLUMN, None))
    )


def _format_directive(line: str, is_free: bool = False) -> str | None:
    """Return the source format a directive line names ("FREE" / "FIXED" / "VARIABLE").

    Examples:
        "       >>SOURCE FORMAT IS FREE"   -> "FREE"
        "$SET SOURCEFORMAT\"FIXED\""       -> "FIXED"
        "       MOVE A TO B."              -> None

    Args:
        line: One line. The directive starts the line; in a fixed-format line it can
            start the code area as well.
        is_free: Whether the line is a free-format line.
    """
    text = line.strip()
    if not is_free and not text.startswith(_DIRECTIVE_START_TUPLE) and _indicator(line) == " ":
        text = _column_slice(line, _CODE_START_COLUMN, None).strip()
    directive_match = _FORMAT_DIRECTIVE_RE.match(text)
    return directive_match.group(1).upper() if directive_match else None


def _is_fixed_line(line: str) -> bool:
    """Return whether a line can be a line of fixed-format source.

    A line is one when it is blank, ends before the indicator column, is a compiler
    option line or a directive line, or holds one of the indicator characters in the
    indicator column. Columns are counted with a full-width character as two.
    """
    if not line.strip() or _line_width(line) <= _INDICATOR_COLUMN:
        return True
    if _OPTION_LINE_RE.match(line) or _is_directive_line(line):
        return True
    return _indicator(line) in _INDICATOR_CHAR_SET


def _is_free_start(line_list: list[str]) -> bool:
    """Return whether the lines before the first format directive are free-format source.

    Args:
        line_list: The lines of the file without tab characters.

    Returns:
        True when one of those lines cannot be a line of fixed-format source.
    """
    for line in line_list:
        if _format_directive(line) is not None:
            return False
        if not _is_fixed_line(line):
            return True
    return False


def _has_right_margin(line_list: list[str]) -> bool:
    """Return whether the code area of the fixed-format lines ends at the right margin.

    Args:
        line_list: The lines of the file without tab characters.

    Returns:
        False when a line that is not a comment is wider than the identification area allows.
    """
    for line in line_list:
        if _indicator(line) in _NO_CODE_INDICATOR_CHAR_SET:
            continue
        if _line_width(line.rstrip()) > _LINE_END_COLUMN:
            return False
    return True


def _code_text_list(line_list: list[str]) -> list[tuple[str, bool]]:
    """Return the code of each line and whether the line continues the one before it.

    Comment lines, debug lines, directive lines and compiler option lines hold no code.
    A compiler option line starts in column 1, or comes before the first line with code.
    Columns are counted with a full-width character as two.
    The source format follows the format directives of the file; before the first
    directive it is free format when a line cannot be fixed format.

    Args:
        line_list: The lines of the file without tab characters.

    Returns:
        One (code text, is continuation) tuple per line; the code text is "" for a
        line without code.
    """
    is_free = _is_free_start(line_list)
    code_end = _CODE_END_COLUMN if _has_right_margin(line_list) else None

    code_text_list: list[tuple[str, bool]] = []
    has_code = False
    for line in line_list:
        code_text, is_continuation = "", False
        directive = _format_directive(line, is_free)
        if directive is not None:
            is_free = directive == "FREE"
            if directive == "VARIABLE":
                code_end = None
        elif is_free:
            if not line.strip().startswith(_NO_CODE_START_TUPLE):
                code_text = line
        elif not _OPTION_LINE_RE.match(line) and not _is_directive_line(line):
            indicator = _indicator(line)
            if indicator not in _NO_CODE_INDICATOR_CHAR_SET:
                code_text = _column_slice(line, _CODE_START_COLUMN, code_end)
                is_continuation = indicator == "-"

        if not has_code and _OPTION_LINE_RE.match(code_text.strip() + " "):
            code_text = ""
        has_code = has_code or bool(code_text.strip())
        code_text_list.append((code_text, is_continuation))
    return code_text_list


def _literal_end(code_text: str, start: int, quote: str) -> int | None:
    """Return the position after the quote that ends a literal, or None when the line ends first.

    A quote written twice is a quote character of the literal.

    Args:
        code_text: The code of one line.
        start: Position of the first character of the literal content.
        quote: The quote character the literal starts with.

    Returns:
        Position after the closing quote. None when the literal is not closed on the line.
    """
    position = start
    while True:
        position = code_text.find(quote, position)
        if position < 0:
            return None
        if code_text.startswith(quote, position + 1):
            position += 2
            continue
        return position + 1


def _is_picture_position(token_list: list[_Token]) -> bool:
    """Return whether the next token is a picture string (after PIC / PICTURE [IS])."""
    if not token_list or token_list[-1].kind != "word":
        return False
    last_word = token_list[-1].text.upper()
    if last_word in _PICTURE_WORD_SET:
        return True
    return (
        last_word == "IS"
        and len(token_list) > 1
        and token_list[-2].text.upper() in _PICTURE_WORD_SET
    )


def _picture_token(code_text: str, position: int, line: int) -> tuple[_Token, int]:
    """Read a picture string and return it with the position after it.

    A period that ends the picture string and the statement is left for the next token.
    Characters outside ASCII are written as "9".

    Args:
        code_text: The code of one line.
        position: Position of the first character of the picture string.
        line: Source line of code_text (1-based).

    Returns:
        A (token, position after the token) tuple.
    """
    text = _PICTURE_RE.match(code_text, position).group(0)
    if len(text) > 1 and text.endswith((".", ",", ";")):
        text = text[:-1]
    end = position + len(text)
    text = "".join(char if char.isascii() else "9" for char in text)
    return _Token("picture", text, line, position), end


def _line_token_list(
    code_text: str, line: int, is_continuation: bool, open_literal: _Token | None,
    last_token_list: list[_Token],
) -> tuple[list[_Token], _Token | None]:
    """Split the code of one line into tokens.

    Args:
        code_text: The code of the line.
        line: Source line of code_text (1-based).
        is_continuation: Whether the line continues the line before it.
        open_literal: The literal the line before left without a closing quote, or None.
        last_token_list: The last two tokens before this line (read to find a picture string).

    Returns:
        A (tokens of the line, literal left without a closing quote or None) tuple.
        The text of open_literal is extended in place when this line continues it.
    """
    token_list: list[_Token] = []
    position = 0
    has_gap = True

    # The rest of a literal the line before did not close
    if open_literal is not None:
        quote = open_literal.text[-1]
        start = len(code_text) - len(code_text.lstrip())
        if code_text.startswith(quote, start):
            start += 1
        end = _literal_end(code_text, start, quote)
        if end is None:
            open_literal.text = open_literal.text[:-1] + code_text[start:].rstrip() + quote
            return token_list, open_literal
        open_literal.text = open_literal.text[:-1] + code_text[start:end]
        position = end
        has_gap = False

    is_glue = is_continuation and open_literal is None
    while position < len(code_text):
        space_match = _SPACE_RE.match(code_text, position)
        if space_match:
            position = space_match.end()
            has_gap = True
            continue
        if code_text.startswith("*>", position):
            break

        token, position, open_literal = _next_token(
            code_text, position, line, (last_token_list + token_list)[-2:],
        )
        token.has_gap = has_gap
        token.is_glue = is_glue
        token_list.append(token)
        has_gap, is_glue = False, False
        if open_literal is not None:
            return token_list, open_literal
    return token_list, None


def _next_token(
    code_text: str, position: int, line: int, last_token_list: list[_Token],
) -> tuple[_Token, int, _Token | None]:
    """Read the token that starts at a position of the code of one line.

    Args:
        code_text: The code of the line.
        position: Position of the first character of the token.
        line: Source line of code_text (1-based).
        last_token_list: The last two tokens before the position.

    Returns:
        A (token, position after the token, the token when it is a literal without a
        closing quote or None) tuple. A literal without a closing quote is closed in
        the token text, the letters before the quote of a literal are written in upper
        case, and "<>" is written as "NOT =".
    """
    if _is_picture_position(last_token_list):
        token, end = _picture_token(code_text, position, line)
        return token, end, None

    literal_match = _LITERAL_START_RE.match(code_text, position)
    if literal_match:
        quote = literal_match.group(0)[-1]
        prefix = literal_match.group(0)[:-1].upper()
        end = _literal_end(code_text, literal_match.end(), quote)
        if end is None:
            text = prefix + code_text[literal_match.end() - 1:].rstrip() + quote
            token = _Token("literal", text, line, position)
            return token, len(code_text), token
        text = prefix + code_text[literal_match.end() - 1:end]
        return _Token("literal", text, line, position), end, None

    if code_text.startswith("<>", position):
        return _Token("other", "NOT =", line, position), position + 2, None

    pseudo_match = _PSEUDO_TEXT_RE.match(code_text, position)
    if pseudo_match:
        return _Token("pseudo", pseudo_match.group(0), line, position), pseudo_match.end(), None

    word_match = _WORD_RE.match(code_text, position)
    if word_match:
        return _Token("word", word_match.group(0), line, position), word_match.end(), None

    char = code_text[position]
    next_char = code_text[position + 1:position + 2]
    kind = "period" if char == "." and (not next_char or next_char.isspace()) else "other"
    return _Token(kind, char, line, position), position + 1, None


def _source_token_list(line_list: list[str]) -> list[_Token]:
    """Split the code of a COBOL source file into tokens.

    A literal continued on the next line is one token on the line it starts on. A word
    continued on the next line is one token as well.

    Args:
        line_list: The lines of the file without tab characters.

    Returns:
        The tokens in source order.
    """
    token_list: list[_Token] = []
    open_literal: _Token | None = None
    for index, (code_text, is_continuation) in enumerate(_code_text_list(line_list)):
        if not code_text.strip():
            continue
        if not is_continuation:
            open_literal = None
        line_token_list, open_literal = _line_token_list(
            code_text, index + 1, is_continuation, open_literal, token_list[-2:],
        )
        for token in line_token_list:
            if token.is_glue and token_list and token.kind == token_list[-1].kind == "word":
                token_list[-1].text += token.text
            else:
                token_list.append(token)
    return token_list


def _word(token_list: list[_Token], index: int) -> str:
    """Return the upper-case text of the word at an index, or "" when it is not a word."""
    if 0 <= index < len(token_list) and token_list[index].kind == "word":
        return token_list[index].text.upper()
    return ""


def _statement_end(token_list: list[_Token], start: int) -> int:
    """Return the index of the period that ends the statement, or the token count without one."""
    for index in range(start, len(token_list)):
        if token_list[index].kind == "period":
            return index
    return len(token_list)


def _exec_end(token_list: list[_Token], index: int) -> int | None:
    """Return the index of the END-EXEC of the EXEC block at an index, or None without one.

    Args:
        token_list: The tokens of the file.
        index: Index of the word EXEC; the word after it names the kind of the block.
    """
    for end in range(index + 2, len(token_list)):
        if _word(token_list, end) == "END-EXEC":
            return end
    return None


def _replacing_list(token_list: list[_Token]) -> list[tuple[str, str, str]]:
    """Read the operands of a REPLACING phrase whose texts are one word each.

    Examples:
        ==:PFX:== BY ==WS==             -> [("", ":PFX:", "WS")]
        OLD-NAME BY NEW-NAME            -> [("", "OLD-NAME", "NEW-NAME")]
        LEADING ==IN== BY ==OUT==       -> [("LEADING", "IN", "OUT")]

    Args:
        token_list: The tokens after the word REPLACING up to the end of the statement.

    Returns:
        (position, replaced text, replacement text) tuples; position is "" / "LEADING" /
        "TRAILING". An operand whose text is not one word is left out.
    """
    def operand_text(token: _Token) -> str:
        """Return the text of one operand without its pseudo-text delimiters."""
        return token.text[2:-2].strip() if token.kind == "pseudo" else token.text

    replacing_list: list[tuple[str, str, str]] = []
    for index in range(1, len(token_list) - 1):
        if _word(token_list, index) != "BY":
            continue
        position = _word(token_list, index - 2)
        if position not in ("LEADING", "TRAILING"):
            position = ""
        old_text = operand_text(token_list[index - 1])
        new_text = operand_text(token_list[index + 1])
        if old_text and new_text and not _SPACE_RE.search(old_text + new_text):
            replacing_list.append((position, old_text, new_text))
    return replacing_list


def literal_value(text: str) -> str:
    """Return the content of a literal, or the text itself when it is not one.

    Examples:
        '"TAXCALC"'  -> "TAXCALC"
        "X'0A'"      -> "0A"
        "TAXCALC"    -> "TAXCALC"
    """
    if len(text) >= 2 and text[-1] in "\"'" and text[-1] in text[:-1]:
        return text[text.index(text[-1]) + 1:-1].strip()
    return text


def _is_level_number(word: str) -> bool:
    """Return whether a word is a level number (decimal digits only)."""
    return word.isdecimal()


def _is_division_header(token_list: list[_Token], index: int) -> bool:
    """Return whether the tokens at an index are the first two words of a division header."""
    if _word(token_list, index + 1) != "DIVISION":
        return False
    word = _word(token_list, index)
    return word in _IDENTIFICATION_WORD_SET or word in _DIVISION_WORD_SET


def _first_statement_start(token_list: list[_Token]) -> int:
    """Return the index of the first token that is in no EXEC block and no COPY statement.

    Args:
        token_list: The tokens of the file.

    Returns:
        The index; the number of tokens when every token is in one of them.
    """
    index = 0
    while index < len(token_list):
        word = _word(token_list, index)
        exec_end = _exec_end(token_list, index) if word == "EXEC" else None
        if word == "COPY":
            index = _statement_end(token_list, index) + 1
        elif exec_end is not None:
            index = exec_end + 1
        elif token_list[index].kind == "period":
            index += 1
        else:
            break
    return min(index, len(token_list))


def _start_division(token_list: list[_Token]) -> str:
    """Return the division the first statement of a file belongs to.

    The statement is the first one after the EXEC blocks and COPY statements the
    file starts with.

    Examples (first tokens of the file):
        IDENTIFICATION DIVISION.    -> "IDENTIFICATION"
        PROGRAM-ID. SUB1.           -> "IDENTIFICATION"
        01 CUST-RECORD.             -> "DATA"
        FD CUST-FILE.               -> "DATA"
        LINKAGE SECTION.            -> "DATA"
        SELECT CUST-FILE ASSIGN ... -> "ENVIRONMENT"
        READ-PARA.                  -> "PROCEDURE"

    Args:
        token_list: The tokens of the file.

    Returns:
        One of the division names of this module.
    """
    start = _first_statement_start(token_list)
    first_word, second_word = _word(token_list, start), _word(token_list, start + 1)
    if first_word == "PROGRAM-ID" or _is_division_header(token_list, start):
        return IDENTIFICATION_DIVISION
    if _is_level_number(first_word) or first_word in _FILE_ENTRY_WORD_SET:
        return DATA_DIVISION
    if first_word in _DATA_SECTION_WORD_SET and second_word == "SECTION":
        return DATA_DIVISION
    if first_word == "SELECT":
        return ENVIRONMENT_DIVISION
    return PROCEDURE_DIVISION


def _call_name_token(token_list: list[_Token], index: int) -> _Token | None:
    """Return the token that names the program of a CALL or ENTRY at an index.

    Examples (tokens from the index):
        CALL "SUB1" USING A        -> "SUB1"
        CALL WS-PGM USING A        -> WS-PGM
        CALL STATIC "cJSON_Parse"  -> "cJSON_Parse"
        ENTRY "ALT1" USING A       -> "ALT1"

    Args:
        token_list: The tokens of a sentence.
        index: Index of a token of the sentence.

    Returns:
        The word or literal after CALL / ENTRY; for CALL, the literal after the word
        of a call convention. None when the token at the index is not CALL or ENTRY,
        or no word or literal follows it.
    """
    word = _word(token_list, index)
    if word not in ("CALL", "ENTRY") or index + 1 >= len(token_list):
        return None
    name_token = token_list[index + 1]
    if name_token.kind not in ("word", "literal"):
        return None
    after_token = token_list[index + 2] if index + 2 < len(token_list) else None
    if word == "CALL" and name_token.kind == "word" and after_token and after_token.kind == "literal":
        return after_token
    return name_token


class _Split:
    """Split of the tokens of one file into the statements of the units.

    What is read without the grammar (programs, COPY, CALL, EXEC, ENTRY) is recorded
    in cobol_text.
    """

    def __init__(self, token_list: list[_Token], cobol_text: CobolText) -> None:
        """
        Args:
            token_list: The tokens of the file in source order.
            cobol_text: Record of what is read without the grammar.
        """
        self.token_list = token_list
        self.cobol_text = cobol_text
        self.division = _start_division(token_list)
        # Word before SECTION in the last section header of the data division
        self.section = ""
        # Line of the header of an identification division that has no PROGRAM-ID yet
        self.program_start_line = 0
        # Whether no identification division header and no PROGRAM-ID has been read
        self.is_prologue = True
        # (unit kind, tokens of the statement) of each unit
        self.unit_token_list: list[tuple[str, list[_Token]]] = []

    def run(self) -> list[tuple[str, list[_Token]]]:
        """Read every statement and return the (unit kind, tokens) of each unit."""
        index = 0
        while index < len(self.token_list):
            statement_token_list, index = self._statement(index)
            if any(token.kind != "period" for token in statement_token_list):
                index = self._read_statement(statement_token_list, index)
        return self.unit_token_list

    def _record_word_list(self, token_list: list[_Token]) -> None:
        """Record the words of tokens that are in no unit."""
        for token in token_list:
            if token.kind == "word":
                self.cobol_text.word_list.append((token.text, token.line))

    def _statement(self, index: int) -> tuple[list[_Token], int]:
        """Collect the tokens of the statement that starts at an index.

        The statement ends with its period, or before PROGRAM-ID, END PROGRAM or a
        division header.
        COPY statements and EXEC blocks inside it are read and left out, except in an
        identification division; EJECT, SKIP1 to SKIP3 and TITLE at its start are left out.

        Args:
            index: Index of the first token of the statement.

        Returns:
            A (tokens of the statement, index after the statement) tuple.
        """
        token_list = self.token_list
        statement_token_list: list[_Token] = []
        while index < len(token_list):
            word, next_word = _word(token_list, index), _word(token_list, index + 1)
            is_code = self.division != IDENTIFICATION_DIVISION or self.is_prologue
            if statement_token_list and (
                _is_division_header(token_list, index)
                or word == "PROGRAM-ID"
                or (word == "END" and next_word == "PROGRAM")
            ):
                break
            exec_end = _exec_end(token_list, index) if word == "EXEC" and next_word else None
            if word == "COPY" and is_code:
                index = self._read_copy(index)
            elif exec_end is not None and is_code:
                index = self._read_exec(index, exec_end, statement_token_list)
            elif not statement_token_list and word in _NO_CODE_WORD_SET:
                index += 1
            elif not statement_token_list and word == "TITLE" and is_code:
                index += 2
            else:
                statement_token_list.append(token_list[index])
                index += 1
                if statement_token_list[-1].kind == "period":
                    break
        return statement_token_list, index

    def _read_statement(self, statement_token_list: list[_Token], index: int) -> int:
        """Read one statement into a unit or into cobol_text.

        Args:
            statement_token_list: The tokens of the statement.
            index: Index of the token after the statement.

        Returns:
            Index of the next token to read.
        """
        first_word = _word(statement_token_list, 0)
        second_word = _word(statement_token_list, 1)
        line = statement_token_list[0].line

        if _is_division_header(statement_token_list, 0):
            if first_word in _IDENTIFICATION_WORD_SET:
                self.division = IDENTIFICATION_DIVISION
                self.program_start_line = line
                self.is_prologue = False
            else:
                self.division = first_word
                self._record_word_list(statement_token_list[2:])
            self.section = ""
            return index
        if first_word == "PROGRAM-ID":
            return self._read_program(statement_token_list, index)
        if first_word == "END" and second_word == "PROGRAM":
            if len(statement_token_list) > 2:
                name = literal_value(statement_token_list[2].text)
                self.cobol_text.program_end_list.append((name, line))
            return index
        if first_word == "REPLACE":
            return index

        if self.division == ENVIRONMENT_DIVISION:
            self._read_environment(statement_token_list)
        elif self.division == DATA_DIVISION:
            self._read_data(statement_token_list)
        elif self.division == PROCEDURE_DIVISION:
            self._read_procedure(statement_token_list)
        return index

    def _read_program(self, statement_token_list: list[_Token], index: int) -> int:
        """Record the program a PROGRAM-ID paragraph names and return the index after it."""
        line = statement_token_list[0].line
        name_token_list = statement_token_list[1:]
        if all(token.kind == "period" for token in name_token_list):
            name_token_list, index = self._statement(index)
        name_token = next(
            (token for token in name_token_list if token.kind in ("word", "literal")), None,
        )
        if name_token is not None:
            self.cobol_text.program_list.append(
                (literal_value(name_token.text), self.program_start_line or line, name_token.line)
            )
        self.program_start_line = 0
        self.is_prologue = False
        self.division = IDENTIFICATION_DIVISION
        return index

    def _read_environment(self, statement_token_list: list[_Token]) -> None:
        """Read a statement of the environment division: a SELECT entry is a unit."""
        if _word(statement_token_list, 0) == "SELECT":
            self.unit_token_list.append((SELECT_UNIT, statement_token_list))
        else:
            self._record_word_list(statement_token_list)

    def _read_data(self, statement_token_list: list[_Token]) -> None:
        """Read a statement of the data division.

        A data item and the description of a file are units. The statements of the
        SCREEN and REPORT sections are in no unit.
        """
        first_word = _word(statement_token_list, 0)
        if first_word in _DATA_SECTION_WORD_SET and _word(statement_token_list, 1) == "SECTION":
            self.section = first_word
        elif self.section in _NO_UNIT_SECTION_WORD_SET:
            self._record_word_list(statement_token_list)
        elif _is_level_number(first_word):
            self.unit_token_list.append((ITEM_UNIT, statement_token_list))
        elif first_word in _FILE_ENTRY_WORD_SET:
            self.unit_token_list.append((FILE_UNIT, statement_token_list))
        else:
            self._record_word_list(statement_token_list)

    def _read_procedure(self, statement_token_list: list[_Token]) -> None:
        """Read a sentence of the procedure division.

        DECLARATIVES. / END DECLARATIVES.   -> nothing
        any other                           -> a unit
        Each CALL of the sentence is recorded in cobol_text.call_list and each ENTRY in
        cobol_text.entry_list; ENTRY is written as CALL in the unit.
        """
        if "DECLARATIVES" in (_word(statement_token_list, 0), _word(statement_token_list, 1)):
            return

        unit_token_list = list(statement_token_list)
        for index, token in enumerate(statement_token_list):
            word = _word(statement_token_list, index)
            name_token = _call_name_token(statement_token_list, index)
            if name_token is None:
                continue
            if word == "CALL":
                self.cobol_text.call_list.append(
                    (literal_value(name_token.text), name_token.kind == "literal", token.line)
                )
            elif word == "ENTRY":
                self.cobol_text.entry_list.append((literal_value(name_token.text), token.line))
                unit_token_list[index] = _Token(
                    "word", "CALL", token.line, token.column, token.has_gap,
                )
        self.unit_token_list.append((PROCEDURE_UNIT, unit_token_list))

    def _read_exec(self, index: int, end: int, statement_token_list: list[_Token]) -> int:
        """Read an EXEC ... END-EXEC block and return the index after it.

        EXEC SQL INCLUDE name         -> recorded in cobol_text.copy_list
        EXEC CICS ... PROGRAM(name)   -> recorded in cobol_text.call_list
        The words of the block are recorded in cobol_text.word_list, a word with ":"
        as the words between them. In a procedure division CONTINUE is added to the
        statement in place of the block.

        Args:
            index: Index of the word EXEC.
            end: Index of the END-EXEC of the block.
            statement_token_list: The tokens of the statement the block is in.
        """
        token_list = self.token_list
        block_token_list = token_list[index + 2:end]
        line = token_list[index].line
        for token in block_token_list:
            if token.kind == "word":
                self.cobol_text.word_list.extend(
                    (word, token.line) for word in token.text.split(":") if word
                )

        kind = _word(token_list, index + 1)
        if kind == "SQL" and _word(block_token_list, 0) == "INCLUDE" and len(block_token_list) > 1:
            name_token = block_token_list[1]
            if name_token.kind in ("word", "literal"):
                self.cobol_text.copy_list.append(CobolCopy(literal_value(name_token.text), line))
        elif kind == "CICS":
            for position in range(len(block_token_list) - 2):
                name_token = block_token_list[position + 2]
                if (
                    _word(block_token_list, position) == "PROGRAM"
                    and block_token_list[position + 1].text == "("
                    and name_token.kind in ("word", "literal")
                ):
                    self.cobol_text.call_list.append(
                        (literal_value(name_token.text), name_token.kind == "literal", line)
                    )

        if self.division == PROCEDURE_DIVISION:
            statement_token_list.append(_Token("word", "CONTINUE", line, token_list[index].column))
        return end + 1

    def _read_copy(self, index: int) -> int:
        """Record a COPY statement in cobol_text.copy_list and return the index after it."""
        token_list = self.token_list
        end = _statement_end(token_list, index)
        operand_token_list = token_list[index + 1:end]
        if not operand_token_list or operand_token_list[0].kind not in ("word", "literal"):
            return index + 1

        library = ""
        phrase_start = len(operand_token_list)
        for position in range(1, len(operand_token_list)):
            word = _word(operand_token_list, position)
            if word in ("OF", "IN") and position == 1 and len(operand_token_list) > 2:
                library = literal_value(operand_token_list[2].text)
            elif word in ("REPLACING", "SUPPRESS"):
                phrase_start = position
                break
        self.cobol_text.copy_list.append(CobolCopy(
            name=literal_value(operand_token_list[0].text),
            line=token_list[index].line,
            library=library,
            replacing_list=_replacing_list(operand_token_list[phrase_start:]),
        ))
        return min(end + 1, len(token_list))


def _put_stand_in_name(
    token_list: list[_Token], unit_token_list: list[tuple[str, list[_Token]]], cobol_text: CobolText,
) -> None:
    """Write a stand-in name in place of each word of the units the grammar does not read as a name.

    A word with a character outside ASCII letters, digits and "-" gets a stand-in name;
    the same word (upper and lower case alike) gets the same one. The stand-in names
    are recorded in cobol_text.name_dict.

    Args:
        token_list: Every token of the file.
        unit_token_list: (unit kind, tokens) of each unit; the text of the words is
            replaced in place.
        cobol_text: Record of the stand-in names.
    """
    prefix = _STAND_IN_PREFIX
    word_set = {token.text.upper() for token in token_list if token.kind == "word"}
    while any(word.startswith(prefix) for word in word_set):
        prefix += _STAND_IN_PREFIX_CHAR

    stand_in_dict: dict[str, str] = {}
    for _, statement_token_list in unit_token_list:
        for token in statement_token_list:
            if token.kind != "word" or GRAMMAR_NAME_RE.fullmatch(token.text):
                continue
            key = token.text.upper()
            if key not in stand_in_dict:
                stand_in_dict[key] = f"{prefix}{len(stand_in_dict) + 1}"
                cobol_text.name_dict[stand_in_dict[key]] = token.text
            token.text = stand_in_dict[key]


def _short_literal(text: str, width: int) -> str:
    """Return a literal cut to a width, with its quotes kept.

    Examples:
        ("'ABCDEFGH'", 6) -> "'ABCD'"

    Args:
        text: A literal with its opening and closing quote.
        width: Number of characters the literal may take.

    Returns:
        The literal with its content cut at the end.
    """
    quote = text[-1]
    start = text.index(quote) + 1
    content = text[start:-1][:max(0, width - start - 1)]
    # A quote written twice is not cut in half
    if (len(content) - len(content.rstrip(quote))) % 2:
        content = content[:-1]
    return text[:start] + content + quote


def _token_gap(last_token: _Token, token: _Token) -> str:
    """Return the text written between two tokens of one line (" " or "")."""
    if token.has_gap:
        return " "
    if token.kind == "word" and last_token.text[-1] in _OPERATOR_CHAR_SET:
        return " "
    if last_token.kind == "word" and token.text[0] in _OPERATOR_CHAR_SET:
        return " "
    return ""


def _code_line_text_list(token_list: list[_Token]) -> list[str]:
    """Write the tokens of one source line as the code of one or more fixed-format lines.

    The tokens keep the indent of the first one when the line fits the code area.
    A line that does not fit is written without indent, then with each literal cut,
    then on several lines.

    Args:
        token_list: The tokens of one source line, in order.

    Returns:
        The code of each line to write, each no longer than the code area.
    """
    part_list = [token_list[0].text]
    for last_token, token in zip(token_list, token_list[1:]):
        part_list.append(_token_gap(last_token, token) + token.text)

    indent = " " * token_list[0].column
    if len(indent) + sum(len(part) for part in part_list) <= _CODE_WIDTH:
        return [indent + "".join(part_list)]

    # Cut the literals, the longest first, until the line fits
    literal_index_list = sorted(
        (index for index, token in enumerate(token_list) if token.kind == "literal"),
        key=lambda index: -len(part_list[index]),
    )
    for index in literal_index_list:
        extra_width = sum(len(part) for part in part_list) - _CODE_WIDTH
        if extra_width <= 0:
            break
        gap = part_list[index][:len(part_list[index]) - len(token_list[index].text)]
        width = max(len(token_list[index].text) - extra_width, 0)
        part_list[index] = gap + _short_literal(token_list[index].text, width)

    # Move the tokens that still do not fit to the next line
    line_text_list = [""]
    for part in part_list:
        if line_text_list[-1] and len(line_text_list[-1]) + len(part) > _CODE_WIDTH:
            line_text_list.append("")
        line_text_list[-1] += part if line_text_list[-1] else part.lstrip()
    return [_short_text(line_text) for line_text in line_text_list]


def _short_text(line_text: str) -> str:
    """Return the code of one line cut to the width of the code area."""
    if len(line_text) <= _CODE_WIDTH:
        return line_text
    if line_text[-1] in "\"'":
        return _short_literal(line_text, _CODE_WIDTH)
    return line_text[:_CODE_WIDTH]


def _is_name_only(token_list: list[_Token]) -> bool:
    """Return whether a sentence is one name, or one name and the word SECTION.

    Examples:
        MAIN-PARA.           -> True
        CALC SECTION.        -> True
        CALC SECTION 10.     -> True
        MOVE A TO B.         -> False
    """
    if token_list[0].kind != "word":
        return False
    if len(token_list) == 2:
        return token_list[1].kind == "period"
    return _word(token_list, 1) == "SECTION" and len(token_list) <= 4


def _unit(kind: str, token_list: list[_Token], word_list: list[tuple[str, int]]) -> CobolUnit:
    """Write one statement as the text of a unit.

    The header lines of a program come first, then each source line of the statement
    as one line; a source line that does not fit the code area is written as several
    lines. A data item is written after the description of a file, and CONTINUE after
    a sentence that is one name.

    Args:
        kind: The unit kind.
        token_list: The tokens of the statement, with the stand-in names.
        word_list: (word as written in the source, source line) of the words of the statement.

    Returns:
        The CobolUnit.
    """
    by_line_dict: dict[int, list[_Token]] = {}
    for token in token_list:
        by_line_dict.setdefault(token.line, []).append(token)

    text_line_list = list(_START_LINE_DICT[kind])
    origin_line_list = [0] * len(text_line_list)
    for line in sorted(by_line_dict):
        for line_text in _code_line_text_list(by_line_dict[line]):
            text_line_list.append(line_text)
            origin_line_list.append(line)

    if kind == FILE_UNIT:
        text_line_list.append(_FILLER_ITEM_LINE)
        origin_line_list.append(0)
    elif kind == PROCEDURE_UNIT and _is_name_only(token_list):
        text_line_list.append(_CONTINUE_LINE)
        origin_line_list.append(0)

    text = "".join(f"{_LEFT_MARGIN}{line_text}\n" for line_text in text_line_list)
    return CobolUnit(kind, text, origin_line_list, word_list)


def item_unit(name: str) -> CobolUnit:
    """Return the unit of a data item of one character with the given name.

    Args:
        name: A word of ASCII letters, digits and "-".

    Returns:
        The unit of the statement "01 name PIC X." on line 1.
    """
    token_list = [
        _Token(kind, text, 1, has_gap=kind != "period")
        for kind, text in (
            ("word", "01"), ("word", name), ("word", "PIC"), ("picture", "X"), ("period", "."),
        )
    ]
    return _unit(ITEM_UNIT, token_list, [("01", 1), (name, 1)])


def split_cobol_source(source: str) -> CobolText:
    """Split the text of a COBOL source file into the units the grammar reads.

    Processing flow:
    1. Split the code into tokens. Fixed-format and free-format lines are read by their
       own columns, comments are dropped, a literal or word continued on the next
       line becomes one token
    2. Split the tokens into statements. Data items, descriptions of files, SELECT
       entries and sentences of the procedure division become units; programs, COPY
       statements, CALL statements, EXEC blocks and ENTRY statements are recorded
    3. Write a stand-in name for each name with a character outside ASCII letters,
       digits and "-"
    4. Write each unit as fixed-format text

    Args:
        source: The text of the file.

    Returns:
        A CobolText. Names and line numbers in it are those of the source, except in
        the text of the units.
    """
    line_list = [line.expandtabs(_TAB_SIZE) for line in source.splitlines()]
    cobol_text = CobolText()

    # == Step 1: Tokens =======================================================
    token_list = _source_token_list(line_list)
    cobol_text.code_line_list = sorted({token.line for token in token_list})

    # == Step 2: Statements ===================================================
    unit_token_list = _Split(token_list, cobol_text).run()
    unit_word_list = [
        [(token.text, token.line) for token in statement_token_list if token.kind == "word"]
        for _, statement_token_list in unit_token_list
    ]

    # == Step 3: Stand-in names ===============================================
    _put_stand_in_name(token_list, unit_token_list, cobol_text)

    # == Step 4: Text of the units ============================================
    for (kind, statement_token_list), word_list in zip(unit_token_list, unit_word_list):
        cobol_text.unit_list.append(_unit(kind, statement_token_list, word_list))
    return cobol_text
