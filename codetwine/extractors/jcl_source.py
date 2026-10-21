import re
from bisect import bisect_left
from dataclasses import dataclass, field
from codetwine.extractors.cobol_source import CALL_KIND, CobolDefinition, CobolImport, CobolSource
from codetwine.parsers.csd_command import (
    MAPSET_RESOURCE,
    PROGRAM_RESOURCE,
    CsdName,
    csd_command_list,
    csd_definition,
)
from codetwine.parsers.operand import Operand, operand_list
from codetwine.utils.file_utils import line_list_of

# == Definition types (the "type" of a definition in the outputs) ============
JOB_TYPE = "job_statement"
PROC_TYPE = "proc_statement"
STEP_TYPE = "exec_statement"
SYMBOL_TYPE = "symbolic_parameter"

# Statements that name another file besides a program (CALL_KIND): the procedure an
# EXEC statement calls, the member an INCLUDE statement includes, and the mapset a CSD
# command names
PROC_KIND = "PROC"
INCLUDE_KIND = "INCLUDE"
MAPSET_KIND = "MAPSET"

# == Kinds of the names a reference names =====================================
PROGRAM_REFERENCE = "program"      # A program a step runs
PROC_REFERENCE = "proc"            # The procedure an EXEC statement calls
STEP_REFERENCE = "step"            # A step
SYMBOL_REFERENCE = "symbol"        # A symbol written &NAME
# A symbolic parameter an EXEC statement gives the procedure it calls
PARAMETER_REFERENCE = "parameter"
INCLUDE_REFERENCE = "include"      # The member an INCLUDE statement includes
MAPSET_REFERENCE = "mapset"        # The mapset a CSD command names
# A CICS resource a CSD command names, defined in the same file
RESOURCE_REFERENCE = "resource"

# Column (0-based) after the last column of the text of a statement
TEXT_END_COLUMN = 71
# Column (0-based) the rest of a quoted string continued on the next line starts at
_QUOTE_CONTINUE_COLUMN = 15
# Column (0-based) after the last column of a line of in-stream data
_DATA_END_COLUMN = 72
# Width of a tab
_TAB_SIZE = 8

# Delimiter that ends in-stream data
_DATA_DELIMITER = "/*"
# First operands of a DD statement whose in-stream data follows it; "*" data also ends
# at the next statement
_DATA_OPERAND_SET = {"*", "DATA"}
_STAR_DATA_OPERAND = "*"

# Programs that run the TSO commands of their SYSTSIN data, and that DD name
_TSO_PROGRAM_SET = {"IKJEFT01", "IKJEFT1A", "IKJEFT1B"}
_TSO_INPUT_DD = "SYSTSIN"
# Program that reads the CSD commands of its SYSIN data, and that DD name
CSD_PROGRAM = "DFHCSDUP"
_CSD_INPUT_DD = "SYSIN"
# Resource type of a program or mapset a CSD command names -> (reference kind, import kind)
_CSD_FILE_KIND_DICT = {
    PROGRAM_RESOURCE: (PROGRAM_REFERENCE, CALL_KIND),
    MAPSET_RESOURCE: (MAPSET_REFERENCE, MAPSET_KIND),
}
# Program that runs the IMS batch program its PARM names second, after one of these
# region types
_IMS_PROGRAM = "DFSRRC00"
_IMS_REGION_SET = {"DLI", "DBB", "BMP"}
# TSO command continuation characters at the end of a line; after "+" the blanks at the
# start of the next line are dropped
_TSO_CONTINUE_CHAR_TUPLE = ("-", "+")
_TSO_STRIP_CONTINUE_CHAR = "+"

# Keywords of an EXEC statement; any other keyword on the EXEC statement of a procedure
# is a symbolic parameter of the procedure
_EXEC_KEYWORD_SET = {
    "ACCT", "ADDRSPC", "CCSID", "COND", "DPRTY", "DYNAMNBR", "MEMLIMIT", "PARM", "PARMDD",
    "PERFORM", "PGM", "PROC", "RD", "REGION", "REGIONX", "RLSTMOUT", "TIME", "TVSAMCOM",
}
_PGM_KEYWORD = "PGM"
_PROC_KEYWORD = "PROC"
_PARM_KEYWORD = "PARM"
_COND_KEYWORD = "COND"
_MEMBER_KEYWORD = "MEMBER"
_DLM_KEYWORD = "DLM"

# Operations
_JOB_OPERATION = "JOB"
_PROC_OPERATION = "PROC"
_PEND_OPERATION = "PEND"
_EXEC_OPERATION = "EXEC"
_DD_OPERATION = "DD"
_SET_OPERATION = "SET"
_IF_OPERATION = "IF"
_ELSE_OPERATION = "ELSE"
_ENDIF_OPERATION = "ENDIF"
_INCLUDE_OPERATION = "INCLUDE"
# Operation of the null statement "//", which ends a job
_NULL_OPERATION = ""
# Operation of a statement with a name and no operation
_NO_OPERATION = "?"
# Operations that take no operands
_NO_OPERAND_OPERATION_SET = {_PEND_OPERATION, _ELSE_OPERATION, _ENDIF_OPERATION}
# Operations that end the step before them
_STEP_END_OPERATION_SET = {
    _JOB_OPERATION, _NULL_OPERATION, _PROC_OPERATION, _PEND_OPERATION, _EXEC_OPERATION,
    _IF_OPERATION, _ELSE_OPERATION, _ENDIF_OPERATION,
}

# A name of the name field, a step or a symbol
_NAME_PATTERN = r"[A-Za-z@#$][A-Za-z0-9@#$]*"
_NAME_RE = re.compile(_NAME_PATTERN)
# The operation after the name field
_OPERATION_RE = re.compile(r" +(\S+)")
# A symbol written in an operand (&&NAME is the name of a temporary data set)
_SYMBOL_RE = re.compile(rf"(?<!&)&({_NAME_PATTERN})")
# A value that is one symbol: &NAME or &NAME.
_WHOLE_SYMBOL_RE = re.compile(rf"&({_NAME_PATTERN})\.?")
# A referback: *.STEP.DD or *.STEP.PROCSTEP.DD
_REFERBACK_RE = re.compile(
    rf"\*\.({_NAME_PATTERN})\.({_NAME_PATTERN})(?:\.({_NAME_PATTERN}))?",
)
# A step an IF statement tests: STEP.RC, STEP.PROCSTEP.ABEND, STEP.¬RUN (¬ or ^), ...
_IF_STEP_RE = re.compile(
    rf"(?<![A-Za-z0-9@#$.])({_NAME_PATTERN})(?:\.({_NAME_PATTERN}))?"
    r"\.[¬^]?(?:RC|ABENDCC|ABEND|RUN)\b",
    re.IGNORECASE,
)
# A test of a COND parameter that names a step: (code,operator,STEP[.PROCSTEP])
_COND_STEP_RE = re.compile(
    rf"\(\s*\d+\s*,\s*(?:GT|GE|EQ|LT|LE|NE)\s*,\s*({_NAME_PATTERN})(?:\.({_NAME_PATTERN}))?\s*\)",
    re.IGNORECASE,
)
# The word that ends the condition of an IF statement
_THEN_RE = re.compile(r"\bTHEN\b", re.IGNORECASE)
# The TSO command that runs a program: RUN PROGRAM(name)
_TSO_RUN_RE = re.compile(
    rf"\bRUN\s+PROG(?:RAM)?\s*\(\s*'?({_NAME_PATTERN})'?\s*\)", re.IGNORECASE,
)


@dataclass
class JclReference:
    """One place a JCL or CSD file names a program, procedure, member, step, symbol or resource."""

    name: str    # Upper-case name
    line: int    # Line of the reference (1-based)
    kind: str    # One of the reference kinds of this module
    # Line of an EXEC statement that calls a procedure: the statement itself for a
    # PROC_REFERENCE, the statement whose procedure has the step or the symbolic
    # parameter otherwise; 0 for a name of the file itself
    exec_line: int = 0
    # First line of the definition of the file the name names; 0 when the name is not
    # resolved in the file
    definition_line: int = 0


@dataclass
class JclSource(CobolSource):
    """What one JCL or CSD file defines, runs, calls and refers to."""

    # Each place a name is referred to
    reference_list: list[JclReference] = field(default_factory=list)
    # The resources the CSD commands of the file define (DEFINE / USERDEFINE), in line
    # order; for a JCL file, those of the SYSIN data of its PGM=DFHCSDUP steps
    csd_resource_list: list[CsdName] = field(default_factory=list)


@dataclass
class _Proc:
    """One procedure: an in-stream one (PROC ... PEND), or the one a file holds."""

    name: str                             # Upper-case name; "" without one
    start_line: int                       # Line of the PROC statement
    end_line: int                         # Last line of the procedure
    job_index: int                        # Number of JOB statements before it
    definition: CobolDefinition | None    # Definition of its name; None without one
    # Upper-case symbolic parameter -> (default value as written, its definition)
    parameter_dict: dict[str, tuple[str, CobolDefinition]] = field(default_factory=dict)
    is_in_stream: bool = False            # Whether a PEND statement ends it


@dataclass
class _Step:
    """One step: an EXEC statement and the statements after it."""

    name: str                             # Upper-case name; "" without one
    start_line: int                       # Line of the EXEC statement
    job_index: int                        # Number of JOB statements before it
    proc: _Proc | None                    # The procedure it is written in
    definition: CobolDefinition | None    # Definition of its name; None without one
    # Procedure name as written (positional operand or PROC=); "" for a program step
    proc_text: str = ""
    # Upper-case names of the procedures it calls: proc_text with its symbol replaced
    proc_name_list: list[str] = field(default_factory=list)
    # Upper-case symbolic parameter -> value as written, of the procedure it calls
    override_dict: dict[str, str] = field(default_factory=dict)
    # Upper-case names of the programs it runs
    program_list: list[str] = field(default_factory=list)
    # Upper-case DD name of its last DD statement with a name (without a procedure step)
    dd_name: str = ""


@dataclass
class _SetSymbol:
    """One symbol a SET statement gives a value."""

    value: str                    # Value as written
    definition: CobolDefinition   # Definition of the symbol
    statement_line: int           # First line of the SET statement
    proc: _Proc | None            # The procedure the SET statement is written in
    job_index: int                # Number of JOB statements before it


@dataclass
class _ValueSource:
    """A value whose symbol is replaced, and where it is written."""

    value: str            # Value as written
    line: int             # First line of the statement it is written in
    proc: _Proc | None    # The procedure the statement is in
    job_index: int        # Number of JOB statements up to the statement
    # The SET statement or symbolic parameter the value is given to; None for the value
    # a lookup starts from, for a symbol passed on to an EXEC statement, and for a value
    # an EXEC statement gives a name the procedure has no parameter of
    definition: CobolDefinition | None = None

    def key(self) -> tuple[str, int, int, int]:
        """Return (value, line, line of its procedure or 0, job_index) of the value."""
        return self.value, self.line, self.proc.start_line if self.proc else 0, self.job_index


@dataclass
class _Statement:
    """One JCL statement with its continuation lines."""

    label: str                 # Name field; "" without one
    # Upper-case operation; "" for the null statement, "?" for a name without one
    operation: str
    operand_text: str          # Operands without the comment after them (IF: the condition)
    char_line_list: list[int]  # Line of each character of operand_text (1-based)
    start_line: int            # First line (1-based)
    end_line: int              # Last line, the last continuation line
    # (line, text) of each line of the in-stream data after a DD statement
    data_line_list: list[tuple[int, str]] = field(default_factory=list)
    # Characters of operand_text while the statement is read (append, finish)
    char_list: list[str] = field(default_factory=list)
    # The procedure and the step it is written in, and the number of JOB statements up
    # to it (_JclReader._read_structure)
    proc: _Proc | None = None
    step: _Step | None = None
    job_index: int = 0

    def append(self, char: str, line: int) -> None:
        """Add a character of the operands written on a line."""
        self.char_list.append(char)
        self.char_line_list.append(line)

    def finish(self) -> None:
        """Set operand_text to the characters added."""
        self.operand_text = "".join(self.char_list)
        self.char_list = []

    def line_of(self, offset: int) -> int:
        """Return the line of the character of operand_text at an offset."""
        if not self.char_line_list:
            return self.start_line
        return self.char_line_list[min(offset, len(self.char_line_list) - 1)]


def _code_text(line: str) -> str:
    """Return the text of a line a statement is read from: columns 1 to 71, right-stripped."""
    return line.expandtabs(_TAB_SIZE)[:TEXT_END_COLUMN].rstrip()


def _is_statement_text(text: str) -> bool:
    """Return whether a line is a JCL statement other than a comment statement."""
    return text.startswith("//") and not text.startswith("//*")


def _is_continuation_text(text: str) -> bool:
    """Return whether a line can continue a statement: "//" followed by a blank."""
    return _is_statement_text(text) and text[2:3] == " "


def _next_statement_index(line_list: list[str], index: int) -> int | None:
    """Return the index of the first line at or after an index that is no comment statement."""
    while index < len(line_list):
        if not _code_text(line_list[index]).startswith("//*"):
            return index
        index += 1
    return None


def _first_column(text: str, column: int) -> int:
    """Return the column of the first character at or after a column that is not a blank."""
    while column < len(text) and text[column] == " ":
        column += 1
    return column


def _read_operand(line_list: list[str], index: int, column: int, statement: _Statement) -> int:
    """Read the operands of a statement from a column of its first line.

    The operands end at the first blank outside quotes. They are continued on the next
    statement line while they end with a comma, from its first character after "//",
    and while a quoted string is open, from column 16. Comment statements between them
    are skipped.

    Args:
        line_list: The lines of the source.
        index: Index of the first line of the statement.
        column: Column (0-based) the operands start at.
        statement: The statement; its operands and last line are filled in.

    Returns:
        Index of the line after the last line of the statement.
    """
    is_in_quote = False
    text = _code_text(line_list[index])
    while True:
        for char in text[column:]:
            if char == " " and not is_in_quote:
                break
            if char == "'":
                is_in_quote = not is_in_quote
            statement.append(char, index + 1)
        statement.end_line = index + 1
        if not is_in_quote and statement.char_list[-1:] != [","]:
            return index + 1
        next_index = _next_statement_index(line_list, index + 1)
        if next_index is None or not _is_continuation_text(_code_text(line_list[next_index])):
            return index + 1
        index = next_index
        text = _code_text(line_list[index])
        column = _QUOTE_CONTINUE_COLUMN if is_in_quote else _first_column(text, 2)


def _read_condition(line_list: list[str], index: int, column: int, statement: _Statement) -> int:
    """Read the condition of an IF statement: the text up to THEN, over continuation lines.

    Args:
        line_list: The lines of the source.
        index: Index of the first line of the statement.
        column: Column (0-based) after the word IF.
        statement: The statement; the condition is filled in as its operands, the lines
            joined by a blank.

    Returns:
        Index of the line after the last line of the statement.
    """
    text = _code_text(line_list[index])
    while True:
        chunk = text[column:]
        then_match = _THEN_RE.search(chunk)
        for char in chunk[:then_match.start() if then_match else len(chunk)]:
            statement.append(char, index + 1)
        statement.append(" ", index + 1)
        statement.end_line = index + 1
        if then_match:
            return index + 1
        next_index = _next_statement_index(line_list, index + 1)
        if next_index is None or not _is_continuation_text(_code_text(line_list[next_index])):
            return index + 1
        index = next_index
        text = _code_text(line_list[index])
        column = 2


def _read_statement(line_list: list[str], index: int) -> tuple[_Statement, int]:
    """Read the statement that starts on a line ("//" not followed by "*").

    PEND, ELSE and ENDIF take no operands: the text after them is a comment.

    Returns:
        (statement, index of the line after its last line).
    """
    text = _code_text(line_list[index])
    label = "" if text[2:3] in ("", " ") else text[2:].split(" ", 1)[0]
    operation_match = _OPERATION_RE.match(text, 2 + len(label))
    if operation_match is not None:
        operation = operation_match.group(1).upper()
    else:
        operation = _NO_OPERATION if label else _NULL_OPERATION
    statement = _Statement(
        label=label,
        operation=operation,
        operand_text="",
        char_line_list=[],
        start_line=index + 1,
        end_line=index + 1,
    )
    if operation_match is None or statement.operation in _NO_OPERAND_OPERATION_SET:
        return statement, index + 1
    if statement.operation == _IF_OPERATION:
        next_index = _read_condition(line_list, index, operation_match.end(), statement)
    else:
        next_index = _read_operand(
            line_list, index, _first_column(text, operation_match.end()), statement,
        )
    statement.finish()
    return statement, next_index


def _keyword_value(statement_operand_list: list[Operand], key: str) -> str | None:
    """Return the value of the first operand with an upper-case key, or None without one."""
    return next(
        (operand.value for operand in statement_operand_list if operand.key == key), None,
    )


def _symbol_name(value: str) -> str:
    """Return the upper-case name of the symbol a value is (&NAME, &NAME., '&NAME'), else ""."""
    symbol_match = _WHOLE_SYMBOL_RE.fullmatch(_unquote(value.strip()))
    return symbol_match.group(1).upper() if symbol_match else ""


def _unquote(value: str) -> str:
    """Return a value without the quotes around it ('A B' -> A B)."""
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _read_data(line_list: list[str], index: int, statement: _Statement) -> int:
    """Read the in-stream data after a DD statement whose first operand is * or DATA.

    The data ends at the delimiter "/*" or the one of DLM, which is not data; data of
    "*" without DLM also ends at the next statement. An empty DLM counts as none.

    Args:
        line_list: The lines of the source.
        index: Index of the line after the statement.
        statement: The DD statement; its data lines are filled in.

    Returns:
        Index of the line after the data and its delimiter.
    """
    statement_operand_list = operand_list(statement.operand_text)
    first_operand = statement_operand_list[0]
    if first_operand.name or first_operand.value.upper() not in _DATA_OPERAND_SET:
        return index
    dlm_text = _unquote(_keyword_value(statement_operand_list, _DLM_KEYWORD) or "")
    delimiter = dlm_text or _DATA_DELIMITER
    is_star_data = first_operand.value == _STAR_DATA_OPERAND and not dlm_text
    while index < len(line_list):
        line = line_list[index]
        if line.startswith(delimiter):
            return index + 1
        if is_star_data and line.startswith("//"):
            return index
        statement.data_line_list.append((index + 1, line))
        index += 1
    return index


def _statement_list(line_list: list[str]) -> list[_Statement]:
    """Read the statements of a JCL source in source order.

    Comment statements, JES statements and lines of data are not statements.
    """
    statement_list: list[_Statement] = []
    index = 0
    while index < len(line_list):
        if not _is_statement_text(_code_text(line_list[index])):
            index += 1
            continue
        statement, index = _read_statement(line_list, index)
        if statement.operation == _DD_OPERATION:
            index = _read_data(line_list, index, statement)
        statement_list.append(statement)
    return statement_list


def _symbol_assignment_list(statement: _Statement) -> list[tuple[str, CobolDefinition]]:
    """Return (value as written, definition) of each symbol a PROC or SET statement sets."""
    symbol_list: list[tuple[str, CobolDefinition]] = []
    for operand in operand_list(statement.operand_text):
        if not _NAME_RE.fullmatch(operand.name):
            continue
        start_line = statement.line_of(operand.offset)
        symbol_list.append((operand.value, CobolDefinition(
            name=operand.name,
            type=SYMBOL_TYPE,
            start_line=start_line,
            end_line=statement.line_of(operand.offset + len(operand.text) - 1),
            name_line=start_line,
        )))
    return symbol_list


def _tso_program_list(data_line_list: list[tuple[int, str]]) -> list[tuple[str, int]]:
    """Return (program name, line) of each RUN PROGRAM(name) command of TSO commands.

    A command continued with "-" or "+" at the end of a line goes on with its next line
    in place of that character; after "+" without the blanks at the start of that line.
    """
    program_list: list[tuple[str, int]] = []
    part_list: list[str] = []
    command_line = 0
    continue_char = ""

    def add_command() -> None:
        """Add the programs of the command of part_list and start the next command."""
        command = "".join(part_list)
        program_list.extend(
            (match.group(1), command_line) for match in _TSO_RUN_RE.finditer(command)
        )
        part_list.clear()

    for line, text in data_line_list:
        code = text.expandtabs(_TAB_SIZE)[:_DATA_END_COLUMN].rstrip()
        if not part_list:
            command_line = line
        if continue_char == _TSO_STRIP_CONTINUE_CHAR:
            code = code.lstrip()
        continue_char = code[-1:] if code.endswith(_TSO_CONTINUE_CHAR_TUPLE) else ""
        if continue_char:
            part_list.append(code[:-1])
            continue
        part_list.append(code)
        add_command()
    add_command()
    return program_list


def add_csd_file_name(source: JclSource, name: CsdName) -> None:
    """Add a program or mapset a CSD command names: a reference and an import.

    Args:
        source: The JclSource the reference and the import are added to.
        name: A PROGRAM_RESOURCE or MAPSET_RESOURCE name of CsdDefinition.file_name_list.
    """
    reference_kind, import_kind = _CSD_FILE_KIND_DICT[name.type]
    source.reference_list.append(JclReference(name.name.upper(), name.line, reference_kind))
    source.import_list.append(CobolImport(import_kind, name.name, name.line))


class _JclReader:
    """Read the definitions, the statements naming other files and the references of JCL."""

    def __init__(self, line_list: list[str]) -> None:
        """Read the statements of the lines of a source, with no definitions yet."""
        self.statement_list = _statement_list(line_list)
        self.source = JclSource(line_list=line_list)
        self.proc_list: list[_Proc] = []
        self.step_list: list[_Step] = []
        # (upper-case name, number of JOB statements) -> the in-stream procedures of that
        # name in the job, in line order
        self.in_stream_proc_dict: dict[tuple[str, int], list[_Proc]] = {}
        # Upper-case name -> the steps of that name, in line order
        self.step_dict: dict[str, list[_Step]] = {}
        # Line of an in-stream procedure -> the steps that call it (_call_list)
        self.call_list_dict: dict[int, list[_Step]] = {}
        # Key of a symbol lookup (_lookup_key) -> the values and the definitions of the symbol
        self.lookup_value_dict: dict[tuple, list[str]] = {}
        self.lookup_definition_dict: dict[tuple, list[CobolDefinition]] = {}
        # (upper-case name, number of JOB statements) -> the SET statements of the symbol
        # in the job, in line order
        self.set_symbol_dict: dict[tuple[str, int], list[_SetSymbol]] = {}
        # (upper-case procedure name, number of JOB statements) -> the steps of the job whose
        # proc_name_list has it
        self.call_step_dict: dict[tuple[str, int], list[_Step]] = {}

    def read(self) -> JclSource:
        """Return the JclSource of the source.

        Processing flow:
        1. Read the jobs, procedures, steps and symbols
        2. Replace the symbols in the names of the procedures the steps call, from the
           last step to the first
        3. Read the programs, procedures, members, steps and symbols each statement names
        """
        # == Step 1: Jobs, procedures, steps and symbols ======================
        self._read_structure()

        # == Step 2: Names of the called procedures ===========================
        # From the last step to the first
        for step in reversed(self.step_list):
            if step.proc_text:
                value_list = self._value_list(
                    step.proc_text, step.start_line, step.proc, step.job_index,
                )
                step.proc_name_list = list(dict.fromkeys(
                    name.upper() for name in value_list if _NAME_RE.fullmatch(name)
                ))
                for name in step.proc_name_list:
                    self.call_step_dict.setdefault((name, step.job_index), []).append(step)

        # == Step 3: Names of each statement ==================================
        for statement in self.statement_list:
            self._read_statement_name(statement)

        self.source.definition_list.sort(
            key=lambda definition: (definition.start_line, -definition.end_line),
        )
        self.source.import_list.sort(key=lambda jcl_import: jcl_import.line)
        return self.source

    # ---- Structure ------------------------------------------------------------

    def _read_structure(self) -> None:
        """Read the jobs, procedures, steps and symbols, and the place of each statement.

        Each statement gets the procedure and the step it is written in and the number
        of JOB statements up to it.

        A job ends at the next JOB statement or a null statement, an in-stream procedure
        at its PEND statement, a procedure without one at the next JOB, PROC or null
        statement or the end of the file, a step at the next EXEC, PROC, PEND, JOB, IF,
        ELSE, ENDIF or null statement. Each ends with the last line of its last
        statement or in-stream data.
        """
        job_index = 0
        job_definition: CobolDefinition | None = None
        proc: _Proc | None = None
        step: _Step | None = None
        last_line = 0
        for statement in self.statement_list:
            operation = statement.operation
            if step is not None and operation in _STEP_END_OPERATION_SET:
                self._close_step(step, last_line)
                step = None
            if proc is not None and operation in (
                _JOB_OPERATION, _NULL_OPERATION, _PROC_OPERATION,
            ):
                self._close_proc(proc, last_line)
                proc = None
            if job_definition is not None and operation in (_JOB_OPERATION, _NULL_OPERATION):
                job_definition.end_line = last_line
                job_definition = None

            if operation == _JOB_OPERATION:
                job_index += 1
                job_definition = self._add_definition(statement, JOB_TYPE)
            elif operation == _PROC_OPERATION:
                proc = self._open_proc(statement, job_index)
            elif operation == _EXEC_OPERATION:
                step = self._open_step(statement, job_index, proc)
            elif operation == _SET_OPERATION:
                for value, definition in _symbol_assignment_list(statement):
                    name = definition.name.upper()
                    self.set_symbol_dict.setdefault((name, job_index), []).append(_SetSymbol(
                        value, definition, statement.start_line, proc, job_index,
                    ))
                    self.source.definition_list.append(definition)

            statement.proc, statement.step, statement.job_index = proc, step, job_index
            last_line = max([statement.end_line, *(line for line, _ in statement.data_line_list)])
            if operation == _PEND_OPERATION and proc is not None:
                proc.is_in_stream = True
                self._close_proc(proc, last_line)
                proc = None

        if step is not None:
            self._close_step(step, last_line)
        if proc is not None:
            self._close_proc(proc, last_line)
        if job_definition is not None:
            job_definition.end_line = last_line
        for proc in self.proc_list:
            if proc.is_in_stream:
                self.in_stream_proc_dict.setdefault((proc.name, proc.job_index), []).append(proc)

    def _add_definition(
        self, statement: _Statement, definition_type: str,
    ) -> CobolDefinition | None:
        """Add the definition of the name field of a statement; None without a name."""
        if not _NAME_RE.fullmatch(statement.label):
            return None
        definition = CobolDefinition(
            name=statement.label,
            type=definition_type,
            start_line=statement.start_line,
            end_line=statement.end_line,
            name_line=statement.start_line,
        )
        self.source.definition_list.append(definition)
        return definition

    def _open_proc(self, statement: _Statement, job_index: int) -> _Proc:
        """Start the procedure of a PROC statement and add its symbolic parameters."""
        definition = self._add_definition(statement, PROC_TYPE)
        proc = _Proc(
            name=statement.label.upper() if definition else "",
            start_line=statement.start_line,
            end_line=statement.end_line,
            job_index=job_index,
            definition=definition,
        )
        for value, parameter_definition in _symbol_assignment_list(statement):
            proc.parameter_dict.setdefault(
                parameter_definition.name.upper(), (value, parameter_definition),
            )
            self.source.definition_list.append(parameter_definition)
        self.proc_list.append(proc)
        return proc

    @staticmethod
    def _close_proc(proc: _Proc, last_line: int) -> None:
        """End a procedure at a line."""
        proc.end_line = last_line
        if proc.definition is not None:
            proc.definition.end_line = last_line

    def _open_step(self, statement: _Statement, job_index: int, proc: _Proc | None) -> _Step:
        """Start the step of an EXEC statement and add its definition.

        The procedure name is the first operand when it has no "=", else the value of PROC=.
        """
        statement_operand_list = operand_list(statement.operand_text)
        first_operand = statement_operand_list[0]
        proc_text = (
            first_operand.value if not first_operand.name
            else _keyword_value(statement_operand_list, _PROC_KEYWORD) or ""
        )
        step = _Step(
            name=statement.label.upper(),
            start_line=statement.start_line,
            job_index=job_index,
            proc=proc,
            definition=self._add_definition(statement, STEP_TYPE),
            proc_text=proc_text,
        )
        if proc_text:
            for operand in statement_operand_list[1:]:
                if _NAME_RE.fullmatch(operand.name) and operand.key not in _EXEC_KEYWORD_SET:
                    step.override_dict.setdefault(operand.key, operand.value)
        self.step_list.append(step)
        if step.name:
            self.step_dict.setdefault(step.name, []).append(step)
        return step

    @staticmethod
    def _close_step(step: _Step, last_line: int) -> None:
        """End the definition of a step at a line."""
        if step.definition is not None:
            step.definition.end_line = last_line

    # ---- Lookups --------------------------------------------------------------

    def _in_stream_proc(self, name: str, line: int, job_index: int) -> _Proc | None:
        """Return the last in-stream procedure of a job with a name, ending before a line.

        Args:
            name: Upper-case name of the procedure.
            line: Line the procedure ends before.
            job_index: Number of JOB statements up to the line.
        """
        proc_list = self.in_stream_proc_dict.get((name, job_index), [])
        end = bisect_left(proc_list, line, key=lambda proc: proc.end_line)
        return proc_list[end - 1] if end else None

    def _set_symbol(
        self, name: str, line: int, proc: _Proc | None, job_index: int,
    ) -> _SetSymbol | None:
        """Return the last SET statement of a scope setting an upper-case symbol before a statement.

        Args:
            name: Upper-case name of the symbol.
            line: First line of the statement.
            proc: The procedure whose SET statements count; None for those of the job
                outside a procedure.
            job_index: Number of JOB statements up to the statement.
        """
        set_symbol_list = self.set_symbol_dict.get((name, job_index), [])
        end = bisect_left(set_symbol_list, line, key=lambda set_symbol: set_symbol.statement_line)
        for index in range(end - 1, -1, -1):
            if set_symbol_list[index].proc is proc:
                return set_symbol_list[index]
        return None

    def _lookup_key(
        self, name: str, line: int, proc: _Proc | None, job_index: int,
    ) -> tuple | None:
        """Return the key of a symbol written in a statement: equal keys, equal values and definitions.

        The key is the SET statement the symbol takes its value from in its scope, else,
        in a procedure, the symbol, the procedure and whether the statement is its PROC
        statement.

        Args:
            name: Upper-case name of the symbol.
            line: First line of the statement.
            proc: The procedure the statement is in.
            job_index: Number of JOB statements up to the statement.

        Returns:
            The key. None outside a procedure without such a SET statement.
        """
        set_symbol = self._set_symbol(name, line, proc, job_index)
        if set_symbol is not None:
            return "set", id(set_symbol)
        if proc is not None:
            return "proc", name, proc.start_line, line == proc.start_line
        return None

    def _symbol_definition_list(self, statement: _Statement, name: str) -> list[CobolDefinition]:
        """Return the SET statements and symbolic parameters a symbol in a statement takes values from.

        These are the definitions of the values _symbol_source_list gives the symbol, in
        the order of the values; a symbol written in place of the same symbol elsewhere is
        followed there. A symbol written in a procedure that has a symbolic parameter of
        it, after the PROC statement and with no SET statement of it before, refers to
        that parameter as well.

        Args:
            statement: The statement the symbol is written in.
            name: Upper-case name of the symbol.

        Returns:
            The definitions in order, without duplicates; kept in lookup_definition_dict
            under the key of the symbol (_lookup_key) when it has one.
        """
        lookup_key = self._lookup_key(
            name, statement.start_line, statement.proc, statement.job_index,
        )
        if lookup_key in self.lookup_definition_dict:
            return self.lookup_definition_dict[lookup_key]
        definition_list: list[CobolDefinition] = []

        def add_definition(definition: CobolDefinition) -> None:
            """Add a definition that is not in definition_list yet."""
            if all(definition is not other for other in definition_list):
                definition_list.append(definition)

        visit_set: set[tuple[str, int, int, int]] = set()
        todo_source_list = [
            _ValueSource("&" + name, statement.start_line, statement.proc, statement.job_index),
        ]
        while todo_source_list:
            source = todo_source_list.pop()
            if source.key() in visit_set:
                continue
            visit_set.add(source.key())
            if source.definition is not None:
                add_definition(source.definition)
                continue
            proc = source.proc
            if (
                proc is not None
                and name in proc.parameter_dict
                and source.line != proc.start_line
                and self._set_symbol(name, source.line, proc, source.job_index) is None
            ):
                add_definition(proc.parameter_dict[name][1])
            todo_source_list.extend(reversed([
                value_source for value_source in self._symbol_source_list(name, source)
                if value_source.definition is not None or _symbol_name(value_source.value) == name
            ]))
        if lookup_key is not None:
            self.lookup_definition_dict[lookup_key] = definition_list
        return definition_list

    def _step(self, name: str, line: int, proc: _Proc | None, job_index: int) -> _Step | None:
        """Return the last step with an upper-case name before a line, in its procedure or job."""
        step_list = self.step_dict.get(name, [])
        end = bisect_left(step_list, line, key=lambda step: step.start_line)
        for index in range(end - 1, -1, -1):
            step = step_list[index]
            if step.proc is proc and (proc is not None or step.job_index == job_index):
                return step
        return None

    def _call_list(self, proc: _Proc) -> list[_Step]:
        """Return the steps that call an in-stream procedure (proc_name_list), in line order.

        The list is kept in call_list_dict from the first call; the steps after the
        procedure have their proc_name_list by then (read, step 2).
        """
        if proc.start_line not in self.call_list_dict:
            self.call_list_dict[proc.start_line] = sorted(
                (
                    step for step in self.call_step_dict.get((proc.name, proc.job_index), [])
                    if self._in_stream_proc(proc.name, step.start_line, step.job_index) is proc
                ),
                key=lambda step: step.start_line,
            )
        return self.call_list_dict[proc.start_line]

    def _value_list(self, value: str, line: int, proc: _Proc | None, job_index: int) -> list[str]:
        """Return the values a value written in a statement stands for, its symbols replaced.

        A value that is one symbol (&NAME, &NAME.) stands for the values the file gives
        the symbol (_symbol_source_list), each replaced in turn where it is written; a
        value with another symbol in it stands for none. A value written on the same
        place is replaced once.

        Args:
            value: The value as written.
            line: First line of the statement it is written in.
            proc: The procedure the statement is in.
            job_index: Number of JOB statements up to the statement.

        Returns:
            The values without quotes, in order, without duplicates; kept in
            lookup_value_dict under the key of a value that is a symbol (_lookup_key)
            when it has one.
        """
        start_name = _symbol_name(value)
        lookup_key = (
            self._lookup_key(start_name, line, proc, job_index) if start_name else None
        )
        if lookup_key in self.lookup_value_dict:
            return self.lookup_value_dict[lookup_key]
        value_list: list[str] = []
        visit_set: set[tuple[str, int, int, int]] = set()
        todo_source_list = [_ValueSource(value, line, proc, job_index)]
        while todo_source_list:
            source = todo_source_list.pop()
            if source.key() in visit_set:
                continue
            visit_set.add(source.key())
            symbol_name = _symbol_name(source.value)
            if not symbol_name:
                text = _unquote(source.value.strip())
                if text and "&" not in text:
                    value_list.append(text)
                continue
            todo_source_list.extend(reversed(self._symbol_source_list(symbol_name, source)))
        value_list = list(dict.fromkeys(value_list))
        if lookup_key is not None:
            self.lookup_value_dict[lookup_key] = value_list
        return value_list

    def _statement_value_list(self, statement: _Statement, value: str) -> list[str]:
        """Return the values a value written in a statement stands for (_value_list)."""
        return self._value_list(
            value, statement.start_line, statement.proc, statement.job_index,
        )

    def _symbol_source_list(self, name: str, source: _ValueSource) -> list[_ValueSource]:
        """Return the values the file gives a symbol written as a value, each with its place.

        The value of the last SET statement of the symbol before the statement, written
        in the same procedure (in the job outside a procedure). Else, in a procedure:
        for each EXEC statement that calls an in-stream procedure, the value it gives the
        symbol, else the default of the PROC statement, else the symbol as written at the
        EXEC statement; in a procedure not called in the file, the default. A default
        that is the symbol itself is none. The value of a SET statement is in the place
        of that statement.

        Args:
            name: Upper-case name of the symbol.
            source: The value that is the symbol.

        Returns:
            The values as written, in order, each with the SET statement or symbolic
            parameter it is given to (_ValueSource.definition).
        """
        proc = source.proc
        set_symbol = self._set_symbol(name, source.line, proc, source.job_index)
        if set_symbol is not None:
            return [_ValueSource(
                set_symbol.value, set_symbol.statement_line, set_symbol.proc,
                set_symbol.job_index, set_symbol.definition,
            )]
        if proc is None:
            return []
        default, parameter_definition = proc.parameter_dict.get(name, ("", None))
        default_source_list = [
            _ValueSource(default, proc.start_line, proc, proc.job_index, parameter_definition),
        ] if parameter_definition is not None and _symbol_name(default) != name else []
        call_list = self._call_list(proc) if proc.is_in_stream else []
        if not call_list:
            return default_source_list
        source_list: list[_ValueSource] = []
        has_default_source = False
        for call in call_list:
            if name in call.override_dict:
                source_list.append(_ValueSource(
                    call.override_dict[name], call.start_line, call.proc, call.job_index,
                    parameter_definition,
                ))
            elif default_source_list:
                if not has_default_source:
                    source_list += default_source_list
                    has_default_source = True
            else:
                source_list.append(
                    _ValueSource("&" + name, call.start_line, call.proc, call.job_index),
                )
        return source_list

    # ---- Names of a statement -------------------------------------------------

    def _add_program(self, name: str, line: int, step: _Step) -> None:
        """Add a program a step runs: a reference and the CALL statement of its file."""
        if not _NAME_RE.fullmatch(name):
            return
        self.source.reference_list.append(JclReference(name.upper(), line, PROGRAM_REFERENCE))
        self.source.import_list.append(CobolImport(CALL_KIND, name, line))
        if name.upper() not in step.program_list:
            step.program_list.append(name.upper())

    def _add_step(
        self, statement: _Statement, name: str, procstep_name: str | None, line: int,
    ) -> None:
        """Add a reference on a line to a step before a statement, and to a step of its procedure."""
        step = self._step(name.upper(), statement.start_line, statement.proc, statement.job_index)
        if step is None or step.definition is None:
            return
        self.source.reference_list.append(JclReference(
            name.upper(), line, STEP_REFERENCE, definition_line=step.start_line,
        ))
        if procstep_name and step.proc_text:
            self._add_proc_inner_name(procstep_name, line, step, STEP_REFERENCE)

    def _add_proc_inner_name(self, name: str, line: int, step: _Step, kind: str) -> None:
        """Add a reference to a step or symbolic parameter of the procedure a step calls."""
        self.source.reference_list.append(JclReference(
            name.upper(), line, kind, exec_line=step.start_line,
        ))

    def _read_statement_name(self, statement: _Statement) -> None:
        """Read the names a statement refers to and the files it names."""
        for match in _SYMBOL_RE.finditer(statement.operand_text):
            name = match.group(1).upper()
            for definition in self._symbol_definition_list(statement, name):
                self.source.reference_list.append(JclReference(
                    name, statement.line_of(match.start()), SYMBOL_REFERENCE,
                    definition_line=definition.start_line,
                ))
        for match in _REFERBACK_RE.finditer(statement.operand_text):
            procstep_name = match.group(2) if match.group(3) else None
            self._add_step(
                statement, match.group(1), procstep_name, statement.line_of(match.start()),
            )

        if statement.operation == _EXEC_OPERATION and statement.step is not None:
            self._read_exec(statement, statement.step)
        elif statement.operation == _DD_OPERATION and statement.step is not None:
            self._read_dd(statement, statement.step)
        elif statement.operation == _IF_OPERATION:
            for match in _IF_STEP_RE.finditer(statement.operand_text):
                self._add_step(
                    statement, match.group(1), match.group(2), statement.line_of(match.start()),
                )
        elif statement.operation == _INCLUDE_OPERATION:
            self._read_include(statement)

    def _read_exec(self, statement: _Statement, step: _Step) -> None:
        """Read the program or procedure of an EXEC statement and the names of its operands."""
        self._read_proc_call(statement, step)
        parm: Operand | None = None
        for operand in operand_list(statement.operand_text):
            if not operand.name:
                continue
            line = statement.line_of(operand.offset)
            if operand.key == _PARM_KEYWORD:
                parm = operand
            if operand.key == _PGM_KEYWORD and not operand.value.startswith("*."):
                for program in self._statement_value_list(statement, operand.value):
                    self._add_program(program, line, step)
            elif operand.key == _COND_KEYWORD or operand.key.startswith(_COND_KEYWORD + "."):
                for match in _COND_STEP_RE.finditer(
                    statement.operand_text, operand.offset, operand.offset + len(operand.text),
                ):
                    self._add_step(
                        statement, match.group(1), match.group(2),
                        statement.line_of(match.start(1)),
                    )
            procstep_name = operand.key.split(".", 1)[1] if "." in operand.key else ""
            if procstep_name and step.proc_text and _NAME_RE.fullmatch(procstep_name):
                self._add_proc_inner_name(procstep_name, line, step, STEP_REFERENCE)
            elif operand.key in step.override_dict:
                self._add_proc_inner_name(operand.key, line, step, PARAMETER_REFERENCE)

        if _IMS_PROGRAM in step.program_list and parm is not None:
            self._read_ims_parm(statement, parm, step)

    def _read_ims_parm(self, statement: _Statement, parm: Operand, step: _Step) -> None:
        """Add the program the PARM of an IMS batch step names after its region type.

        The program is on the line of the character after the first comma of the value.
        """
        field_list = _unquote(_unquote(parm.value).strip("()")).split(",")
        if len(field_list) < 2 or field_list[0].strip().upper() not in _IMS_REGION_SET:
            return
        comma_offset = statement.operand_text.find(",", parm.offset + parm.text.index("="))
        line = statement.line_of(comma_offset + 1)
        for program in self._statement_value_list(statement, field_list[1]):
            self._add_program(program, line, step)

    def _read_proc_call(self, statement: _Statement, step: _Step) -> None:
        """Add the procedures an EXEC statement calls: in-stream in its job, or in another file."""
        line = statement.start_line
        for name in step.proc_name_list:
            local_proc = self._in_stream_proc(name, line, step.job_index)
            if local_proc is not None:
                self.source.reference_list.append(JclReference(
                    name, line, PROC_REFERENCE,
                    exec_line=line, definition_line=local_proc.start_line,
                ))
                continue
            self.source.reference_list.append(
                JclReference(name, line, PROC_REFERENCE, exec_line=line),
            )
            self.source.import_list.append(CobolImport(PROC_KIND, name, line))

    def _read_dd(self, statement: _Statement, step: _Step) -> None:
        """Read the procedure step a DD statement overrides, and the names of TSO and CSD commands.

        The TSO commands are the SYSTSIN data of a step that runs a TSO program or calls
        a procedure, the CSD commands the SYSIN data of a step with PGM=DFHCSDUP; a DD
        statement without a name is concatenated to the one before it.
        """
        procstep_name, _, dd_name = statement.label.rpartition(".")
        if procstep_name and step.proc_text and _NAME_RE.fullmatch(procstep_name):
            self._add_proc_inner_name(procstep_name, statement.start_line, step, STEP_REFERENCE)
        if dd_name:
            step.dd_name = dd_name.upper()
        if step.dd_name == _TSO_INPUT_DD and (
            step.proc_text or _TSO_PROGRAM_SET & set(step.program_list)
        ):
            for program, line in _tso_program_list(statement.data_line_list):
                self._add_program(program, line, step)
        if step.dd_name == _CSD_INPUT_DD and CSD_PROGRAM in step.program_list:
            for command in csd_command_list(statement.data_line_list):
                definition = csd_definition(command)
                if definition is None:
                    continue
                self.source.csd_resource_list.append(definition.resource)
                for name in definition.file_name_list:
                    add_csd_file_name(self.source, name)

    def _read_include(self, statement: _Statement) -> None:
        """Add the members an INCLUDE statement includes: a reference and an INCLUDE_KIND import."""
        for operand in operand_list(statement.operand_text):
            if operand.key != _MEMBER_KEYWORD:
                continue
            line = statement.line_of(operand.offset)
            for member in self._statement_value_list(statement, operand.value):
                if _NAME_RE.fullmatch(member):
                    self.source.reference_list.append(
                        JclReference(member.upper(), line, INCLUDE_REFERENCE),
                    )
                    self.source.import_list.append(CobolImport(INCLUDE_KIND, member, line))


def read_jcl_source(source: str) -> JclSource:
    """Read a JCL source (a job, a procedure or an INCLUDE member): what it defines and names.

    Definitions: jobs, procedures, named steps, and the symbols PROC and SET statements
    give a value. Statements naming another file: the program a step runs (PGM=, a TSO
    RUN PROGRAM command of SYSTSIN data, the PARM of an IMS batch step) as CALL_KIND,
    the procedure an EXEC statement calls that is not in-stream in its job as
    PROC_KIND, the member of an INCLUDE statement as INCLUDE_KIND, and the programs
    (CALL_KIND) and mapsets (MAPSET_KIND) the CSD commands of the SYSIN data of a
    PGM=DFHCSDUP step name. A symbol in their names stands for the values the file gives
    it. References: the programs, procedures, INCLUDE members and mapsets, the steps of
    COND parameters, IF statements and referbacks, the steps and symbolic parameters of
    a called procedure named on its EXEC and DD statements, and the symbols of the
    operands, each to the SET statements and symbolic parameters its values come from.
    In-stream procedures and steps count within their job, SET statements within the
    procedure or job they are written in.

    Args:
        source: The text of the file.

    Returns:
        A JclSource; its definitions and statements are in line order.
    """
    return _JclReader(line_list_of(source)).read()
