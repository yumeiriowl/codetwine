import re
from dataclasses import dataclass, field
from codetwine.extractors.cobol_source import DATA_ITEM_TYPE, CobolDefinition, CobolSource
from codetwine.utils.file_utils import line_list_of

# Column (0-based) whose character marks an assembler statement as continued on the next line
_CONTINUE_COLUMN = 71
# Column (0-based) the text of a continuation line starts at
_CONTINUE_START_COLUMN = 15

# The extended attributes of a field and the letter that ends the name of their field in
# the symbolic map, in the order the map holds them
_ATTRIBUTE_TUPLE = (
    ("COLOR", "C"), ("PS", "P"), ("HILIGHT", "H"), ("VALIDN", "V"),
    ("OUTLINE", "U"), ("SOSI", "M"), ("TRANSP", "T"),
)
# The extended attributes EXTATT=YES gives every field when DSATTS names none
_EXTATT_ATTRIBUTE_TUPLE = ("COLOR", "PS", "HILIGHT", "VALIDN")

# Letters that end the names of the length, flag and attribute fields of a field in the
# input record, and the letters of its data field in the input and output records
_LENGTH_LETTER = "L"
_FLAG_LETTER = "F"
_ATTRIBUTE_LETTER = "A"
_INPUT_LETTER = "I"
_OUTPUT_LETTER = "O"

# Level number of the records of a map, and of the fields directly in them
_RECORD_LEVEL = 1
_FIELD_LEVEL = 2

# A label BMS makes COBOL names from
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
# Label and operation at the start of a statement
_STATEMENT_HEAD_RE = re.compile(r"(\S*)\s+(\S+)\s*")


@dataclass
class _Statement:
    """One assembler statement of a BMS source."""

    label: str         # Label in column 1; "" without one
    operation: str     # Upper-case operation (DFHMSD / DFHMDI / DFHMDF / ...)
    operand_text: str  # Operands without the remark after them
    start_line: int    # First line (1-based)
    end_line: int      # Last line, the last continuation line


@dataclass
class _Field:
    """One named field of a map, or a group of fields (GRPNAME)."""

    name: str          # Upper-case label, or the group name
    occurs: int        # OCCURS count; 0 without OCCURS
    start_line: int    # First line of its statement (of the first member for a group)
    end_line: int      # Last line of its statement (of the last member for a group)
    member_list: list["_Field"] = field(default_factory=list)


@dataclass
class _Map:
    """One map (DFHMDI) of a mapset."""

    name: str                     # Upper-case label
    attribute_list: list[str]     # Letters of the extended attributes of its fields
    start_line: int               # First line of its DFHMDI statement
    end_line: int                 # Last line of its last statement
    field_list: list[_Field] = field(default_factory=list)


def _statement_list(line_list: list[str]) -> list[_Statement]:
    """Read the assembler statements of a BMS source.

    A line with "*" in column 1 is a comment. A statement is continued while column 72
    holds a character, on the next line from column 16. The operands end at the first
    blank outside quotes; what follows is a remark.

    Args:
        line_list: The lines of the source.

    Returns:
        The statements in source order.
    """
    statement_list: list[_Statement] = []
    statement: _Statement | None = None
    is_in_quote = is_operand_end = False
    for line_index, raw_line in enumerate(line_list):
        line = raw_line.expandtabs(8)
        if statement is None:
            if not line.strip() or line.startswith("*"):
                continue
            head_match = _STATEMENT_HEAD_RE.match(line[:_CONTINUE_COLUMN])
            if head_match is None:
                continue
            statement = _Statement(
                label=head_match.group(1),
                operation=head_match.group(2).upper(),
                operand_text="",
                start_line=line_index + 1,
                end_line=line_index + 1,
            )
            body = line[head_match.end():_CONTINUE_COLUMN]
            is_in_quote = is_operand_end = False
        else:
            statement.end_line = line_index + 1
            body = line[_CONTINUE_START_COLUMN:_CONTINUE_COLUMN]
            if not is_in_quote:
                body = body.lstrip()
        for char in body:
            if is_operand_end:
                break
            if char == "'":
                is_in_quote = not is_in_quote
            elif char == " " and not is_in_quote:
                is_operand_end = True
                break
            statement.operand_text += char
        if is_in_quote:
            is_operand_end = False
        if len(line) > _CONTINUE_COLUMN and line[_CONTINUE_COLUMN] != " ":
            is_operand_end = False
        else:
            statement_list.append(statement)
            statement = None
    if statement is not None:
        statement_list.append(statement)
    return statement_list


def _operand_dict(operand_text: str) -> dict[str, str]:
    """Split operands "KEY=value,..." at the commas outside quotes and parentheses.

    Examples:
        "TYPE=MAP,DSATTS=(COLOR,HILIGHT)" -> {"TYPE": "MAP", "DSATTS": "(COLOR,HILIGHT)"}

    Returns:
        {upper-case key: value as written}; a key without "=" has the value "".
    """
    part_list: list[str] = []
    part = ""
    depth = 0
    is_in_quote = False
    for char in operand_text:
        if char == "'":
            is_in_quote = not is_in_quote
        elif not is_in_quote and char == "(":
            depth += 1
        elif not is_in_quote and char == ")":
            depth -= 1
        elif not is_in_quote and depth == 0 and char == ",":
            part_list.append(part)
            part = ""
            continue
        part += char
    part_list.append(part)

    operand_dict: dict[str, str] = {}
    for part in part_list:
        key, _, value = part.partition("=")
        if key.strip():
            operand_dict[key.strip().upper()] = value.strip()
    return operand_dict


def _value_list(value: str) -> list[str]:
    """Return the upper-case items of an operand value such as "(COLOR,HILIGHT)" or "YES"."""
    return [item.strip().upper() for item in value.strip("()").split(",") if item.strip()]


def _attribute_list(option_dict: dict[str, str]) -> list[str]:
    """Return the letters of the extended attributes a map's fields get in the symbolic map.

    Args:
        option_dict: The operands of the mapset (DFHMSD), updated with those of the map (DFHMDI).
    """
    if "DSATTS" in option_dict:
        attribute_set = set(_value_list(option_dict["DSATTS"]))
    elif option_dict.get("EXTATT", "NO").upper() == "YES":
        attribute_set = set(_EXTATT_ATTRIBUTE_TUPLE)
    else:
        attribute_set = set()
    return [letter for attribute, letter in _ATTRIBUTE_TUPLE if attribute in attribute_set]


def _read_map_list(statement_list: list[_Statement]) -> tuple[list[_Map], list[str]]:
    """Read the maps of the mapsets of a BMS source.

    A map or a field whose label is not a COBOL word is left out.

    Args:
        statement_list: The statements of the source.

    Returns:
        A (maps in source order, upper-case mapset names) tuple.
    """
    map_list: list[_Map] = []
    mapset_name_list: list[str] = []
    mapset_option_dict: dict[str, str] = {}
    current_map: _Map | None = None
    group_dict: dict[str, _Field] = {}
    for statement in statement_list:
        operand_dict = _operand_dict(statement.operand_text)
        if statement.operation == "DFHMSD":
            current_map = None
            if operand_dict.get("TYPE", "").upper() != "FINAL":
                mapset_option_dict = operand_dict
                if _NAME_RE.fullmatch(statement.label):
                    mapset_name_list.append(statement.label.upper())
            continue
        if statement.operation == "DFHMDI":
            current_map = None
            if _NAME_RE.fullmatch(statement.label):
                current_map = _Map(
                    statement.label.upper(),
                    _attribute_list({**mapset_option_dict, **operand_dict}),
                    statement.start_line,
                    statement.end_line,
                )
                map_list.append(current_map)
            group_dict = {}
            continue
        if current_map is None:
            continue
        current_map.end_line = statement.end_line
        if statement.operation != "DFHMDF" or not _NAME_RE.fullmatch(statement.label):
            continue

        occurs = operand_dict.get("OCCURS", "")
        bms_field = _Field(
            statement.label.upper(), int(occurs) if occurs.isdigit() else 0,
            statement.start_line, statement.end_line,
        )
        group_name = operand_dict.get("GRPNAME", "").upper()
        if not group_name:
            current_map.field_list.append(bms_field)
            continue
        if group_name not in group_dict:
            group_dict[group_name] = _Field(group_name, 0, statement.start_line, statement.end_line)
            current_map.field_list.append(group_dict[group_name])
        group_dict[group_name].member_list.append(bms_field)
        group_dict[group_name].end_line = statement.end_line
    return map_list, mapset_name_list


def _item_definition(
    name: str, start_line: int, end_line: int, level: int, is_group: bool,
) -> CobolDefinition:
    """Return the definition of one data item of a symbolic map.

    Args:
        name: Name of the item.
        start_line: First line of the statement or statements it comes from (1-based).
        end_line: Last line of them.
        level: Level number of the item.
        is_group: Whether items belong to it.

    Returns:
        A data item definition whose name is on start_line.
    """
    return CobolDefinition(
        name=name,
        type=DATA_ITEM_TYPE,
        start_line=start_line,
        end_line=end_line,
        name_line=start_line,
        level=level,
        is_group=is_group,
    )


def _field_definition_list(
    bms_field: _Field, attribute_list: list[str], level: int,
) -> list[CobolDefinition]:
    """Return the definitions a field gives in the input and the output record of its map.

    Input record:  <field>L, <field>F, <field>A (under a FILLER that redefines <field>F),
                   <field>I
    Output record: <field> + the letter of each extended attribute, <field>O
    A group (GRPNAME) holds <member>I / <member>O of each member in <group>I / <group>O.

    Args:
        bms_field: The field or the group.
        attribute_list: Letters of the extended attributes of the map's fields.
        level: Level number of the field's items.

    Returns:
        The definitions, each over the lines of the field's statement (of its members'
        statements for a group).
    """
    is_group = bool(bms_field.member_list)
    suffix_level_list = [
        (_LENGTH_LETTER, level, False),
        (_FLAG_LETTER, level, False),
        (_ATTRIBUTE_LETTER, level + 1, False),
        (_INPUT_LETTER, level, is_group),
        *((letter, level, False) for letter in attribute_list),
        (_OUTPUT_LETTER, level, is_group),
    ]
    definition_list = [
        _item_definition(
            bms_field.name + suffix, bms_field.start_line, bms_field.end_line,
            item_level, item_is_group,
        )
        for suffix, item_level, item_is_group in suffix_level_list
    ]
    for member in bms_field.member_list:
        for letter in (_INPUT_LETTER, _OUTPUT_LETTER):
            definition_list.append(_item_definition(
                member.name + letter, member.start_line, member.end_line, level + 1, False,
            ))
    return definition_list


def read_bms_source(source: str) -> CobolSource:
    """Read a BMS source (DFHMSD / DFHMDI / DFHMDF macros) and return its symbolic maps.

    Each map is an input record <map>I and an output record <map>O, the names a COBOL
    program gets from COPY of the mapset. A field with OCCURS has its items under a
    FILLER that repeats them.

    Args:
        source: The text of the file.

    Returns:
        A CobolSource with the data items of the symbolic maps as its definitions and the
        mapset names as its copy_name_list. It has no imports and no references.
    """
    line_list = line_list_of(source)
    map_list, mapset_name_list = _read_map_list(_statement_list(line_list))

    definition_list: list[CobolDefinition] = []
    for bms_map in map_list:
        for letter in (_INPUT_LETTER, _OUTPUT_LETTER):
            definition_list.append(_item_definition(
                bms_map.name + letter, bms_map.start_line, bms_map.end_line, _RECORD_LEVEL, True,
            ))
        for bms_field in bms_map.field_list:
            level = _FIELD_LEVEL + 1 if bms_field.occurs else _FIELD_LEVEL
            definition_list += _field_definition_list(bms_field, bms_map.attribute_list, level)

    return CobolSource(
        line_list=line_list,
        definition_list=sorted(
            definition_list, key=lambda definition: (definition.start_line, -definition.end_line),
        ),
        copy_name_list=list(dict.fromkeys(mapset_name_list)),
    )
