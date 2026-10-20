from dataclasses import dataclass


@dataclass(frozen=True)
class Operand:
    """One operand of a statement: KEY=value, or a positional value."""

    text: str    # Operand as written
    offset: int  # Offset of its first character in the operand text
    name: str    # Key as written, without the blanks around it; "" for a positional operand
    # Value after "=" without blanks around it; the whole operand for a positional one
    value: str

    @property
    def key(self) -> str:
        """Return the upper-case key; "" for a positional operand."""
        return self.name.upper()


def _split_operand(operand_text: str) -> list[tuple[str, int]]:
    """Split operands "KEY=value,..." at the commas outside quotes and parentheses.

    Examples:
        "TYPE=MAP,DSATTS=(COLOR,HILIGHT)" -> [("TYPE=MAP", 0), ("DSATTS=(COLOR,HILIGHT)", 9)]
        "PGM=X,PARM='A,B'"                -> [("PGM=X", 0), ("PARM='A,B'", 6)]

    Args:
        operand_text: The operands of one statement, without the remark after them.

    Returns:
        (operand as written, offset of its first character in operand_text) of each
        operand, empty ones among them.
    """
    part_list: list[tuple[str, int]] = []
    part = ""
    part_start = 0
    depth = 0
    is_in_quote = False
    for offset, char in enumerate(operand_text):
        if char == "'":
            is_in_quote = not is_in_quote
        elif not is_in_quote and char == "(":
            depth += 1
        elif not is_in_quote and char == ")":
            depth -= 1
        elif not is_in_quote and depth == 0 and char == ",":
            part_list.append((part, part_start))
            part = ""
            part_start = offset + 1
            continue
        part += char
    part_list.append((part, part_start))
    return part_list


def operand_list(operand_text: str) -> list[Operand]:
    """Return the operands of a statement (_split_operand) with their keys and values.

    Examples:
        "MYPROC,OUT='A B'" -> [Operand("MYPROC", 0, "", "MYPROC"),
                               Operand("OUT='A B'", 7, "OUT", "'A B'")]

    Args:
        operand_text: The operands of one statement, without the remark after them.

    Returns:
        The operands in the order they are written, empty ones among them.
    """
    statement_operand_list: list[Operand] = []
    for part, offset in _split_operand(operand_text):
        name, equal_sign, value = part.partition("=")
        if equal_sign:
            statement_operand_list.append(Operand(part, offset, name.strip(), value.strip()))
        else:
            statement_operand_list.append(Operand(part, offset, "", part.strip()))
    return statement_operand_list


def operand_dict(operand_text: str) -> dict[str, str]:
    """Return the operands of a statement by their keys.

    Examples:
        "TYPE=MAP,DSATTS=(COLOR,HILIGHT)" -> {"TYPE": "MAP", "DSATTS": "(COLOR,HILIGHT)"}

    Args:
        operand_text: The operands of one statement, without the remark after them.

    Returns:
        {upper-case key: value as written}; a key without "=" has the value "".
    """
    key_value_dict: dict[str, str] = {}
    for operand in operand_list(operand_text):
        if "=" in operand.text:
            if operand.name:
                key_value_dict[operand.key] = operand.value
        elif operand.value:
            key_value_dict[operand.value.upper()] = ""
    return key_value_dict
