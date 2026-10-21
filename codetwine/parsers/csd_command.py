import re
import string
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field

# Column (0-based) after the last column of a line of CSD commands
TEXT_END_COLUMN = 72
# Width of a tab
_TAB_SIZE = 8
# Start of a comment line
COMMENT_PREFIX = "*"
# Last character of a line whose open value goes on with the next line
_VALUE_CONTINUE_CHAR = "*"

# Commands of DFHCSDUP, as an upper-case word that starts a command may shorten them
# to its first letters (at least _MIN_VERB_LENGTH)
_VERB_TUPLE = (
    "ADD", "ALTER", "APPEND", "CHECK", "COPY", "DEFINE", "DELETE", "EXTRACT",
    "INITIALIZE", "LIST", "MIGRATE", "PROCESS", "REMOVE", "SCAN", "SERVICE", "UPGRADE",
    "USERDEFINE", "VERIFY",
)
_MIN_VERB_LENGTH = 3
# Commands that define a resource
_DEFINE_VERB_TUPLE = ("DEFINE", "USERDEFINE")

# == Resource types ===========================================================
PROGRAM_RESOURCE = "PROGRAM"
TRANSACTION_RESOURCE = "TRANSACTION"
MAPSET_RESOURCE = "MAPSET"
# CICS resource types of the CSD -> the shortest abbreviation of each; a keyword that
# starts a type with at least the letters of its abbreviation stands for it (TRANS, TD)
_RESOURCE_TYPE_DICT = {
    "ATOMSERVICE": "A", "BUNDLE": "B", "CONNECTION": "CON", "CORBASERVER": "COR",
    "DB2CONN": "DB2C", "DB2ENTRY": "DB2E", "DB2TRAN": "DB2T", "DJAR": "DJ",
    "DOCTEMPLATE": "DO", "ENQMODEL": "E", "FILE": "F", "IPCONN": "I",
    "JOURNALMODEL": "JO", "JVMSERVER": "JV", "LIBRARY": "LI", "LSRPOOL": "LSR",
    MAPSET_RESOURCE: "MA", "MQCONN": "MQ", "MQMONITOR": "MQMON", "PARTITIONSET": "PARTI",
    "PARTNER": "PARTN", "PIPELINE": "PI", "PROCESSTYPE": "PROC", "PROFILE": "PROF",
    PROGRAM_RESOURCE: "PROG", "REQUESTMODEL": "R", "SESSIONS": "S", "TCPIPSERVICE": "TC",
    "TDQUEUE": "TD", "TERMINAL": "TE", "TRANCLASS": "TRANC", TRANSACTION_RESOURCE: "TRANS",
    "TSMODEL": "TS", "TYPETERM": "TY", "URIMAP": "U", "WEBSERVICE": "W",
}
# (resource type of the command, attribute) -> resource types the value of the attribute
# names, in the order they are looked up, besides an attribute named like a resource
# type or its abbreviation; "" for a command of any type
_ATTRIBUTE_RESOURCE_DICT = {
    ("", "TRANSID"): (TRANSACTION_RESOURCE,),
    ("", "REMOTESYSTEM"): ("CONNECTION", "IPCONN"),
    ("", "TRPROF"): ("PROFILE",),
    ("", "PRINTER"): ("TERMINAL",),
    ("", "ALTPRINTER"): ("TERMINAL",),
    ("", "CONVERTER"): (PROGRAM_RESOURCE,),
    ("", "URM"): (PROGRAM_RESOURCE,),
    ("", "EXITPGM"): (PROGRAM_RESOURCE,),
    ("DB2TRAN", "ENTRY"): ("DB2ENTRY",),
    ("TDQUEUE", "INDIRECTNAME"): ("TDQUEUE",),
}
# Attributes that start a resource type and name no resource
_PLAIN_ATTRIBUTE_SET = {"TYPE", "JOURNAL", "PARTITIONS", "JVM"}
_GROUP_KEYWORD = "GROUP"

# A word at the start of a line, followed by a blank or the end of the line and not by "("
_FIRST_WORD_RE = re.compile(r" *([A-Za-z]+)(?= |$)(?! *\()")
# The characters of a keyword, and a keyword of a command
_KEYWORD_CHAR_SET = frozenset(string.ascii_letters + string.digits + "@#$_-")
_KEYWORD_RE = re.compile(r"[A-Za-z0-9@#$_-]+")
# A resource name: any characters but blanks, parentheses and quotes
_RESOURCE_NAME_RE = re.compile(r"[^\s()']+")

# == Places in the text of a command, as _CommandText follows it ==============
_OUTSIDE = "outside"            # Outside the values
_VALUE_START = "value_start"    # After the "(" of a value, before its first character
_UNQUOTED = "unquoted"          # In a value that does not start with a quote
_QUOTED = "quoted"              # In the quotes of a value
_QUOTE_END = "quote_end"        # After a quote in the quotes of a value
_AFTER_QUOTE = "after_quote"    # After the closing quote of a value, before its ")"


@dataclass
class CsdKeyword:
    """One keyword of a CSD command: KEYWORD(value) or KEYWORD."""

    name: str          # Upper-case keyword as written
    # Text inside the parentheses, without blanks around it, or inside the quotes of a
    # quoted value ('' as one quote); None without
    value: str | None
    line: int = 0      # Line the value starts on (1-based); 0 without a value


@dataclass
class CsdCommand:
    """One CSD command (DEFINE TRANSACTION(T) GROUP(G) ...) with its continuation lines."""

    verb: str                    # Upper-case command, written out in full (DEFINE, ADD, ...)
    start_line: int              # First line (1-based)
    end_line: int                # Last line
    keyword_list: list[CsdKeyword] = field(default_factory=list)


@dataclass
class CsdName:
    """A resource a CSD command names."""

    type: str    # Upper-case resource type, written out in full
    name: str    # Resource name as written
    line: int    # Line it is written on (1-based)


@dataclass
class CsdAttribute:
    """An attribute of a CSD command that names a resource (PROGRAM(P), TRANSID(T), ...)."""

    type_tuple: tuple[str, ...]  # Upper-case resource types it names, in lookup order
    name: str                    # Resource name as written
    line: int                    # Line it is written on (1-based)


@dataclass
class CsdDefinition:
    """The resource a DEFINE command defines, and the names its attributes hold."""

    resource: CsdName                 # The resource the command defines
    group: str                        # Upper-case GROUP of the command; "" without
    # Attributes that name a resource
    attribute_list: list[CsdAttribute] = field(default_factory=list)
    # Programs and mapsets of other files the command names (PROGRAM_RESOURCE / MAPSET_RESOURCE)
    file_name_list: list[CsdName] = field(default_factory=list)


class _CommandText:
    """The text of one command as its lines are added, with the offset each line starts at."""

    def __init__(self, verb: str, line: int) -> None:
        """Start the text of a command on a line."""
        self.command = CsdCommand(verb, line, line)
        self.part_list: list[str] = []
        self.length = 0
        # Offset in the text where each added line starts, and that line
        self.offset_list: list[int] = []
        self.line_list: list[int] = []
        # Place in the text after its last character; parentheses open in an unquoted
        # value; whether the last character that is no blank outside the values ends a
        # keyword
        self.place = _OUTSIDE
        self.depth = 0
        self.is_after_keyword = False

    def add(self, text: str, line: int, separator: str) -> None:
        """Add the text of a line, after a separator."""
        piece = separator + text
        self.offset_list.append(self.length)
        self.line_list.append(line)
        self.part_list.append(piece)
        self.length += len(piece)
        self.command.end_line = line
        for char in piece:
            self._read_char(char)

    def is_value_open(self) -> bool:
        """Return whether the text ends inside a value."""
        return self.place != _OUTSIDE

    def _read_char(self, char: str) -> None:
        """Follow the values the way _ValueTable.read() reads them, for a value that closes.

        Outside the values, a "(" after a keyword and blanks opens a value. A value whose
        first character that is no blank is a quote ends at the ")" after its closing
        quote ('' is one quote in it); any other value ends at the ")" that closes its
        parentheses.
        """
        if self.place == _QUOTE_END:
            self.place = _QUOTED if char == "'" else _AFTER_QUOTE
            if self.place == _QUOTED:
                return
        if self.place == _OUTSIDE:
            if char == "(" and self.is_after_keyword:
                self.place = _VALUE_START
            elif char != " ":
                self.is_after_keyword = char in _KEYWORD_CHAR_SET
            return
        if self.place == _VALUE_START:
            if char == " ":
                return
            if char == "'":
                self.place = _QUOTED
                return
            self.place, self.depth = _UNQUOTED, 1
        if self.place == _UNQUOTED:
            if char == "(":
                self.depth += 1
            elif char == ")":
                self.depth -= 1
                if self.depth == 0:
                    self.place, self.is_after_keyword = _OUTSIDE, False
        elif self.place == _QUOTED:
            if char == "'":
                self.place = _QUOTE_END
        elif char == ")":
            self.place, self.is_after_keyword = _OUTSIDE, False

    def finish(self) -> CsdCommand:
        """Return the command with the keywords of its text."""
        text = "".join(self.part_list)

        def line_of(offset: int) -> int:
            """Return the line of the character of the text at an offset."""
            return self.line_list[bisect_right(self.offset_list, offset) - 1]

        self.command.keyword_list = _keyword_list(text, line_of)
        return self.command


def _verb(word: str) -> str:
    """Return the command an upper-case word at the start of a line stands for; "" for none."""
    if len(word) < _MIN_VERB_LENGTH:
        return ""
    verb_list = [verb for verb in _VERB_TUPLE if verb.startswith(word)]
    return verb_list[0] if len(verb_list) == 1 else ""


class _ValueTable:
    """Where the values of the text of a command end, found in one pass over the text."""

    def __init__(self, text: str) -> None:
        """Find the parentheses and quotes of the text."""
        self.text = text
        # Index of a "(" -> index of the ")" that closes it; indexes of every ")"
        self.close_dict: dict[int, int] = {}
        self.close_list: list[int] = []
        # Indexes of every quote; for the k-th, the quote that ends a quoted value whose
        # first quote after the opening one is the k-th ('' being one quote in it), -1
        # for none
        self.quote_list: list[int] = []
        open_list: list[int] = []
        for index, char in enumerate(text):
            if char == "(":
                open_list.append(index)
            elif char == ")":
                self.close_list.append(index)
                if open_list:
                    self.close_dict[open_list.pop()] = index
            elif char == "'":
                self.quote_list.append(index)
        self.quote_end_list = [-1] * (len(self.quote_list) + 2)
        for position in range(len(self.quote_list) - 1, -1, -1):
            is_pair = (
                position + 1 < len(self.quote_list)
                and self.quote_list[position + 1] == self.quote_list[position] + 1
            )
            self.quote_end_list[position] = (
                self.quote_end_list[position + 2] if is_pair else self.quote_list[position]
            )

    def _next_close(self, index: int) -> int:
        """Return the index of the first ")" at or after an index; -1 for none."""
        position = bisect_left(self.close_list, index)
        return self.close_list[position] if position < len(self.close_list) else -1

    def read(self, open_index: int) -> tuple[str, int, int]:
        """Read the value of a keyword from its "(".

        A value that starts with a quote ends at its closing quote ('' is one quote in
        it); any other value ends at the ")" that closes its parentheses, quotes in it
        being plain characters. A value whose closing quote or ")" is missing ends at the
        first ")".

        Returns:
            (value without blanks around it and without its quotes, index of its first
            character that is no blank (of the "(" for none), index after the value and
            its ")").
        """
        text = self.text
        start = open_index + 1
        index = start
        while index < len(text) and text[index] == " ":
            index += 1
        value_start = index if index < len(text) else open_index
        if index < len(text) and text[index] == "'":
            quote_end = self.quote_end_list[bisect_left(self.quote_list, index) + 1]
            if quote_end >= 0:
                close = self._next_close(quote_end)
                value = text[index + 1:quote_end].replace("''", "'")
                return value, value_start, close + 1 if close >= 0 else len(text)
            start = index + 1
        else:
            close = self.close_dict.get(open_index)
            if close is not None:
                return text[start:close].strip(), value_start, close + 1
        close = self._next_close(start)
        if close < 0:
            return text[start:].strip(), value_start, len(text)
        return text[start:close].strip(), value_start, close + 1


def _keyword_list(text: str, line_of: Callable[[int], int]) -> list[CsdKeyword]:
    """Read the keywords of the text of a command after its verb.

    Args:
        text: The text of the command after its verb, its lines joined.
        line_of: Returns the line of the character of the text at an offset.

    Returns:
        The keywords in the order they are written.
    """
    keyword_list: list[CsdKeyword] = []
    value_table = _ValueTable(text)
    index = 0
    while index < len(text):
        match = _KEYWORD_RE.match(text, index)
        if match is None:
            index += 1
            continue
        keyword = CsdKeyword(match.group(0).upper(), None)
        index = match.end()
        open_index = index
        while open_index < len(text) and text[open_index] == " ":
            open_index += 1
        if open_index < len(text) and text[open_index] == "(":
            keyword.value, value_start, index = value_table.read(open_index)
            keyword.line = line_of(value_start)
        keyword_list.append(keyword)
    return keyword_list


def _command_start(text: str) -> tuple[str, int]:
    """Return the command a line starts and the index after its first word; ("", 0) for none."""
    first_match = _FIRST_WORD_RE.match(text)
    verb = _verb(first_match.group(1).upper()) if first_match else ""
    return (verb, first_match.end()) if verb else ("", 0)


def is_command_line(text: str) -> bool:
    """Return whether a line of DFHCSDUP input starts a command (columns 1 to 72 are read)."""
    return bool(_command_start(text.expandtabs(_TAB_SIZE)[:TEXT_END_COLUMN])[0])


def csd_command_list(line_list: list[tuple[int, str]]) -> list[CsdCommand]:
    """Read the CSD commands of DFHCSDUP input.

    A line with "*" in column 1 is a comment. A line whose first word is a command (or
    its first three or more letters, in any case), followed by a blank or the end of
    the line and not by "(", starts a command (is_command_line); any other line
    continues the command before it, after a blank. A line that ends with "*" inside an
    open value goes on with the next line in place of that "*", without the blanks at
    its start, and the next line never starts a command. Columns 73 to 80 are not read.

    Examples:
        [(1, "Define Transaction(TRN1) Group(GRP1)"), (2, "  Program(PRG1)")]
            -> [CsdCommand("DEFINE", 1, 2, [TRANSACTION(TRN1), GROUP(GRP1), PROGRAM(PRG1)])]

    Args:
        line_list: (line, text) of each line of the input (1-based lines).

    Returns:
        The commands in the order they are written.
    """
    command_list: list[CsdCommand] = []
    command_text: _CommandText | None = None
    is_value_continue = False
    for line, raw_line in line_list:
        text = raw_line.expandtabs(_TAB_SIZE)[:TEXT_END_COLUMN].rstrip()
        if not text.strip() or text.startswith(COMMENT_PREFIX):
            continue
        if is_value_continue and command_text is not None:
            piece, separator = text.lstrip(), ""
        else:
            verb, verb_end = _command_start(text)
            if verb:
                if command_text is not None:
                    command_list.append(command_text.finish())
                command_text = _CommandText(verb, line)
                piece, separator = text[verb_end:], " "
            elif command_text is not None:
                piece, separator = text, " "
            else:
                continue
        # The text before a last "*" is read first; the "*" is added when no value is open
        is_continue_char = piece.endswith(_VALUE_CONTINUE_CHAR)
        command_text.add(piece[:-1] if is_continue_char else piece, line, separator)
        is_value_continue = is_continue_char and command_text.is_value_open()
        if is_continue_char and not is_value_continue:
            command_text.add(_VALUE_CONTINUE_CHAR, line, "")
    if command_text is not None:
        command_list.append(command_text.finish())
    return command_list


def _resource_type(keyword: str) -> str | None:
    """Return the resource type an upper-case keyword stands for; None for none.

    Examples:
        "TRANS" -> "TRANSACTION", "PROG" -> "PROGRAM", "TD" -> "TDQUEUE", "PRO" -> None
    """
    for resource_type, abbreviation in _RESOURCE_TYPE_DICT.items():
        if resource_type.startswith(keyword) and len(keyword) >= len(abbreviation):
            return resource_type
    return None


def _attribute_type_tuple(command_type: str, keyword: str) -> tuple[str, ...]:
    """Return the resource types an attribute of a command of a type names; () for none."""
    type_tuple = (
        _ATTRIBUTE_RESOURCE_DICT.get((command_type, keyword))
        or _ATTRIBUTE_RESOURCE_DICT.get(("", keyword))
    )
    if type_tuple:
        return type_tuple
    resource_type = None if keyword in _PLAIN_ATTRIBUTE_SET else _resource_type(keyword)
    return (resource_type,) if resource_type else ()


def csd_definition(command: CsdCommand) -> CsdDefinition | None:
    """Return the resource a DEFINE or USERDEFINE command defines and the names it holds.

    The first keyword other than GROUP is the resource type with the resource name. An
    attribute named like a resource type or its abbreviation (but those of
    _PLAIN_ATTRIBUTE_SET), or one of _ATTRIBUTE_RESOURCE_DICT, names a resource. The
    program of DEFINE PROGRAM, the PROGRAM of DEFINE TRANSACTION and the mapset of DEFINE
    MAPSET name a program or mapset of another file. Each name has the line of its value.

    Returns:
        The definition. None for another command and for one without a resource type
        and name.
    """
    if command.verb not in _DEFINE_VERB_TUPLE:
        return None
    resource_keyword = next(
        (
            keyword for keyword in command.keyword_list
            if not _GROUP_KEYWORD.startswith(keyword.name)
        ),
        None,
    )
    resource_type = _resource_type(resource_keyword.name) if resource_keyword else None
    if (
        resource_type is None
        or not resource_keyword.value
        or not _RESOURCE_NAME_RE.fullmatch(resource_keyword.value)
    ):
        return None
    resource = CsdName(resource_type, resource_keyword.value, resource_keyword.line)
    definition = CsdDefinition(resource, "")
    for keyword in command.keyword_list:
        if (
            keyword is resource_keyword
            or not keyword.value
            or not _RESOURCE_NAME_RE.fullmatch(keyword.value)
        ):
            continue
        if _GROUP_KEYWORD.startswith(keyword.name):
            definition.group = definition.group or keyword.value.upper()
            continue
        type_tuple = _attribute_type_tuple(resource.type, keyword.name)
        if type_tuple:
            definition.attribute_list.append(
                CsdAttribute(type_tuple, keyword.value, keyword.line),
            )

    if resource.type in (PROGRAM_RESOURCE, MAPSET_RESOURCE):
        definition.file_name_list.append(resource)
    elif resource.type == TRANSACTION_RESOURCE:
        definition.file_name_list.extend(
            CsdName(PROGRAM_RESOURCE, attribute.name, attribute.line)
            for attribute in definition.attribute_list
            if attribute.type_tuple == (PROGRAM_RESOURCE,)
        )
    return definition
