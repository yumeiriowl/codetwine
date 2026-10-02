import sys
from bisect import bisect_right
from dataclasses import dataclass, field
from tree_sitter import Language, Node, Parser
from codetwine.utils.file_utils import line_list_of
from codetwine.parsers.cobol_format import (
    FILE_UNIT,
    GRAMMAR_NAME_RE,
    ITEM_UNIT,
    PROCEDURE_UNIT,
    CobolText,
    CobolUnit,
    item_unit,
    literal_value,
    qualifier_tuple,
    split_cobol_source,
)

# == Definition types (the "type" of a definition in the outputs) ============
PROGRAM_TYPE = "program_definition"
ENTRY_TYPE = "entry_statement"
SECTION_TYPE = "section_header"
PARAGRAPH_TYPE = "paragraph_header"
DATA_ITEM_TYPE = "data_description"
FILE_TYPE = "file_description_entry"

# Definition types of the names a CALL statement can name
CALL_TARGET_TYPE_TUPLE = (PROGRAM_TYPE, ENTRY_TYPE)

# Statements that name another file
COPY_KIND = "COPY"
CALL_KIND = "CALL"

# Node types of the headers of the procedure division
_HEADER_NODE_TYPE_SET = {SECTION_TYPE, PARAGRAPH_TYPE}

# Level numbers of the data items that belong to no group
_NO_GROUP_LEVEL_SET = {1, 77, 78}
# Level of a RENAMES item and of a condition name
_RENAME_LEVEL = 66
_CONDITION_LEVEL = 88
# Rank of a data item that belongs to no group; an item belongs to the item before it
# with a lower rank
_TOP_RANK = 1
# Rank of a RENAMES item (it belongs to the record only) and of a condition name
# (it belongs to the item before it)
_RENAME_RANK = _TOP_RANK + 1
_CONDITION_RANK = 100
# A position after every token of a file
_NO_POSITION = sys.maxsize
# Level numbers of the items that can be a group item
_GROUP_LEVEL_RANGE = range(1, 50)
# Words of a data item's statement that make it an elementary item when no item belongs
# to it: the PICTURE clause, the VALUE clause and the usages that take no PICTURE clause
_ELEMENTARY_WORD_SET = {
    "PIC", "PICTURE", "VALUE", "VALUES", "INDEX", "POINTER", "POINTER-32",
    "PROGRAM-POINTER", "PROCEDURE-POINTER", "FUNCTION-POINTER", "COMP-1", "COMP-2",
    "COMPUTATIONAL-1", "COMPUTATIONAL-2", "FLOAT-SHORT", "FLOAT-LONG", "FLOAT-EXTENDED",
    "FLOAT-DECIMAL-16", "FLOAT-DECIMAL-34", "FLOAT-BINARY-32", "FLOAT-BINARY-64",
    "FLOAT-BINARY-128", "BINARY-CHAR", "BINARY-SHORT", "BINARY-LONG", "BINARY-INT",
    "BINARY-DOUBLE", "BINARY-C-LONG", "OBJECT", "CONSTANT", "ANY",
}


@dataclass
class CobolDefinition:
    """One definition of a COBOL file."""

    name: str        # Name as written in the source
    type: str        # One of the definition types of this module
    start_line: int  # First line of the definition (1-based)
    end_line: int    # Last line of the definition
    name_line: int   # Line the name is written on (1-based)
    # Level number of a data item; None for the other types
    level: int | None = None
    # Whether a data item is a group item (items belong to it, or a COPY statement
    # inside it brings them in); None for the other types
    is_group: bool | None = None


@dataclass
class CobolReference:
    """One place a COBOL file refers to a name."""

    name: str    # Upper-case name
    line: int    # Line of the reference (1-based)
    # Upper-case names that qualify it (X OF Y IN Z -> ("Y", "Z")), innermost first
    qualifier_tuple: tuple[str, ...] = ()
    # Whether it is the program name a CALL statement names
    is_call: bool = False


@dataclass
class CobolImport:
    """One statement of a COBOL file that names another file."""

    kind: str               # COPY_KIND / CALL_KIND
    name: str               # Name of the copybook or of the program
    line: int               # Line of the statement (1-based)
    library: str = ""       # Library name of COPY ... OF library
    # REPLACING operands of a COPY statement as (position, replaced text, replacement text)
    replacing_list: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class CobolSource:
    """What one COBOL file defines, names and refers to."""

    # Lines of the source text
    line_list: list[str]
    # Definitions in line order
    definition_list: list[CobolDefinition] = field(default_factory=list)
    # COPY and CALL statements in line order
    import_list: list[CobolImport] = field(default_factory=list)
    # Each place a name is referred to
    reference_list: list[CobolReference] = field(default_factory=list)
    # Upper-case names a COPY statement can name the file by besides its file name
    # (BMS: the mapset names)
    copy_name_list: list[str] = field(default_factory=list)

    def usage_line_list(self, name_set: set[str]) -> list[tuple[str, int]]:
        """Return the places the file refers to one of the given names.

        Names are compared without regard to upper and lower case.

        Args:
            name_set: The names to look for.

        Returns:
            (name as given in name_set, line) tuples in line order, without duplicates.
        """
        name_dict = {name.upper(): name for name in name_set}
        usage_set = {
            (name_dict[reference.name], reference.line)
            for reference in self.reference_list if reference.name in name_dict
        }
        return sorted(usage_set, key=lambda usage: (usage[1], usage[0]))

    def definition_text(self, definition: CobolDefinition) -> str:
        """Return the source text of a definition: its lines joined by "\\n"."""
        return "\n".join(self.line_list[definition.start_line - 1:definition.end_line])

    def definition_source(self, name: str) -> str | None:
        """Return the source text of the first definition with a name.

        Names are compared without regard to upper and lower case.

        Args:
            name: The definition name.

        Returns:
            The lines of the definition joined by "\\n". None when no definition has the name.
        """
        for definition in self.definition_list:
            if definition.name.upper() == name.upper():
                return self.definition_text(definition)
        return None


def _item_rank(level: int) -> int:
    """Return the rank of a data item: an item belongs to the item before it with a lower rank.

    Examples:
        1, 77, 78 -> 1
        5         -> 5
        66        -> 2
        88        -> 100
    """
    if level in _NO_GROUP_LEVEL_SET:
        return _TOP_RANK
    if level == _RENAME_LEVEL:
        return _RENAME_RANK
    if level == _CONDITION_LEVEL:
        return _CONDITION_RANK
    return level


@dataclass
class _Item:
    """One data item of a COBOL file, as the units are read."""

    name: str | None   # Name as written in the source; None for FILLER or no name
    level: int         # Level number
    start_line: int    # First line of its statement
    end_line: int      # Last line of its statement
    name_line: int     # Line of its name (its first line without one)
    unit_index: int    # Index of its unit in CobolText.unit_list
    # Whether its statement has a PICTURE clause or a USAGE that makes it an elementary item
    is_elementary: bool


def _is_elementary_word_list(word_list: list[tuple[str, int]]) -> bool:
    """Return whether the words of a data item's statement make it an elementary item.

    The statement has a PICTURE clause, a VALUE clause, or a USAGE that takes no PICTURE
    clause (INDEX, POINTER, COMP-1, ...); it is looked at for an item no item belongs to.

    Args:
        word_list: (word, source line) of the words of the statement, level number first.
    """
    return any(word.upper() in _ELEMENTARY_WORD_SET for word, _ in word_list[1:])


def _node_list(root_node: Node) -> list[Node]:
    """Return every node of a tree in source order."""
    node_list: list[Node] = []
    node_stack = [root_node]
    while node_stack:
        node = node_stack.pop()
        node_list.append(node)
        node_stack.extend(reversed(node.children))
    return node_list


class _UnitReader:
    """Reads the units of one COBOL file into a CobolSource."""

    def __init__(self, cobol_text: CobolText, line_list: list[str], language: Language) -> None:
        """
        Args:
            cobol_text: The units of the file and what is read without the grammar.
            line_list: Lines of the source text.
            language: The tree-sitter Language of the COBOL grammar.
        """
        self.cobol_text = cobol_text
        self.parser = Parser(language)
        self.source = CobolSource(line_list=line_list)
        # (name, type, line) of each header of the procedure division
        self.header_list: list[tuple[str, str, int]] = []
        # Each data item
        self.item_list: list[_Item] = []
        # Index in CobolText.unit_list of the unit being read
        self.unit_index = -1
        # Upper-case data item name -> literals the item is given (VALUE clause, MOVE)
        self.item_literal_dict: dict[str, list[str]] = {}
        # Upper-case word -> whether the grammar reads it as the name of a data item
        self.is_item_name_dict: dict[str, bool] = {}

    def name(self, text: str) -> str:
        """Return the name written in the source for a name written in a unit."""
        return self.cobol_text.name_dict.get(text.upper(), text)

    def node_name(self, node: Node) -> str:
        """Return the name or the literal content a node holds."""
        text = node.text.decode("utf-8")
        value = literal_value(text)
        return value if value != text else self.name(text)

    def read(self) -> CobolSource:
        """Read the file and return the CobolSource.

        Processing flow:
        1. Parse each unit and collect the headers, data items, descriptions of
           files and references
        2. Add the references of the statements that are in no unit
        3. Build the definitions with their line ranges
        4. Build the COPY and CALL statements
        """
        # == Step 1: Units ====================================================
        for unit in self.cobol_text.unit_list:
            self._read_unit(unit)

        # == Step 2: Words outside the units ==================================
        for word, line, qualifier_list in self.cobol_text.word_list:
            self.source.reference_list.append(CobolReference(
                word.upper(), line, tuple(qualifier.upper() for qualifier in qualifier_list),
            ))

        # == Step 3: Definitions ==============================================
        definition_list = [
            *self._program_definition_list(),
            *self._header_definition_list(),
            *self._item_definition_list(),
            *self.source.definition_list,
        ]
        for name, line in self.cobol_text.entry_list:
            definition_list.append(CobolDefinition(name, ENTRY_TYPE, line, line, line))
        self.source.definition_list = sorted(
            definition_list, key=lambda definition: (definition.start_line, -definition.end_line),
        )

        # == Step 4: COPY and CALL statements =================================
        self._add_import()
        return self.source

    def _read_unit(self, unit: CobolUnit) -> None:
        """Parse one unit and record what it defines and refers to.

        The name a unit defines is read from the tree. References are the WORD nodes
        of the tree; for a unit the grammar does not read without error they are
        every word of the statement except the name it defines.
        """
        root_node = self.parser.parse(unit.text.encode("utf-8")).root_node
        source_line_list = [line for line in unit.origin_line_list if line]
        self.unit_index += 1
        if not source_line_list:
            return
        node_list = [
            node for node in _node_list(root_node)
            if node.start_point[0] < len(unit.origin_line_list)
            and unit.origin_line_list[node.start_point[0]]
        ]

        define_name = self._read_definition(
            unit, node_list, source_line_list[0], source_line_list[-1],
        )
        if root_node.has_error:
            self._add_word_reference(unit, define_name)
        else:
            self._add_node_reference(unit, node_list)

    def _read_definition(
        self, unit: CobolUnit, node_list: list[Node], start_line: int, end_line: int,
    ) -> str | None:
        """Record what a unit defines and the literals it gives to data items.

        Args:
            unit: The unit.
            node_list: The nodes of the tree of the unit that start on a source line.
            start_line: First source line of the unit.
            end_line: Last source line of the unit.

        Returns:
            The name the unit defines. None when it defines none.
        """
        define_name = None
        if unit.kind == ITEM_UNIT:
            define_name, name_line = self._item_name(unit, node_list)
            self.item_list.append(_Item(
                name=define_name,
                level=int(unit.word_list[0][0]),
                start_line=start_line,
                end_line=end_line,
                name_line=name_line or start_line,
                unit_index=self.unit_index,
                is_elementary=_is_elementary_word_list(unit.word_list),
            ))
            if define_name is not None:
                self._read_value(node_list, define_name)
        elif unit.kind == FILE_UNIT:
            define_name, name_line = self._file_name(unit, node_list)
            if define_name is not None:
                self.source.definition_list.append(
                    CobolDefinition(define_name, FILE_TYPE, start_line, end_line, name_line)
                )
        elif unit.kind == PROCEDURE_UNIT:
            define_name = self._header_name(unit, node_list, start_line)
            self._read_move(node_list)
        return define_name

    def _add_word_reference(self, unit: CobolUnit, define_name: str | None) -> None:
        """Add every word of a unit as a reference, except the first one that is the defined name.

        A word followed by OF / IN is qualified by the words after them.
        """
        text_list = [word for word, _ in unit.word_list]
        is_define_due = define_name is not None
        for index, (word, line) in enumerate(unit.word_list):
            if is_define_due and word.upper() == define_name.upper():
                is_define_due = False
                continue
            qualifier_list = qualifier_tuple(text_list, index)
            self.source.reference_list.append(CobolReference(
                word.upper(), line, tuple(qualifier.upper() for qualifier in qualifier_list),
            ))

    def _add_node_reference(self, unit: CobolUnit, node_list: list[Node]) -> None:
        """Add each WORD node of a unit that is not the name of a definition as a reference.

        A WORD of a qualified_word node is qualified by the WORD nodes after it.
        """
        for node in node_list:
            if node.type == "WORD" and not self._is_definition_name(node):
                line = unit.origin_line_list[node.start_point[0]]
                self.source.reference_list.append(CobolReference(
                    self.node_name(node).upper(), line, self._node_qualifier_tuple(node),
                ))

    def _node_qualifier_tuple(self, node: Node) -> tuple[str, ...]:
        """Return the upper-case names that qualify a WORD node.

        They are the WORD nodes after it in its qualified_word node.
        """
        parent = node.parent
        if parent is None or parent.type != "qualified_word":
            return ()
        word_node_list = [child for child in parent.children if child.type == "WORD"]
        index = next(
            (position for position, child in enumerate(word_node_list) if child.id == node.id),
            None,
        )
        if index is None:
            return ()
        return tuple(self.node_name(child).upper() for child in word_node_list[index + 1:])

    @staticmethod
    def _is_definition_name(node: Node) -> bool:
        """Return whether a WORD node is the name of a constant or of the description of a file."""
        parent = node.parent
        if parent is None or parent.type not in ("constant_entry", FILE_TYPE):
            return False
        first_word = next(child for child in parent.children if child.type == "WORD")
        return first_word.id == node.id

    def _item_name(self, unit: CobolUnit, node_list: list[Node]) -> tuple[str | None, int]:
        """Return the name of the data item of a unit and the line it is written on.

        The name is the entry name of the tree. When the tree holds none, it is the
        word after the level number, provided the grammar reads that word as the name
        of a data item.

        Returns:
            A (name, source line) tuple. (None, 0) for an item without a name or FILLER.
        """
        for node in node_list:
            if node.type == "entry_name" or (
                node.type == "WORD" and self._is_definition_name(node)
            ):
                text = node.text.decode("utf-8")
                if text.upper() == "FILLER":
                    return None, 0
                return self.name(text), unit.origin_line_list[node.start_point[0]]

        if len(unit.word_list) > 1 and unit.word_list[1][1] == unit.word_list[0][1]:
            word, line = unit.word_list[1]
            if not GRAMMAR_NAME_RE.fullmatch(word) or self._is_item_name(word):
                return word, line
        return None, 0

    def _is_item_name(self, word: str) -> bool:
        """Return whether the grammar reads a word as the name of a data item."""
        if word.upper() not in self.is_item_name_dict:
            unit = item_unit(word)
            root_node = self.parser.parse(unit.text.encode("utf-8")).root_node
            self.is_item_name_dict[word.upper()] = not root_node.has_error and any(
                node.type == "entry_name" and node.text.decode("utf-8") == word
                for node in _node_list(root_node)
            )
        return self.is_item_name_dict[word.upper()]

    def _file_name(self, unit: CobolUnit, node_list: list[Node]) -> tuple[str | None, int]:
        """Return the name of the file a unit describes and the line it is written on.

        Returns:
            A (name, source line) tuple. (None, 0) when the name is not read.
        """
        for node in node_list:
            if node.type == "WORD" and self._is_definition_name(node):
                return self.node_name(node), unit.origin_line_list[node.start_point[0]]
        return None, 0

    def _header_name(self, unit: CobolUnit, node_list: list[Node], line: int) -> str | None:
        """Record the section or paragraph header a sentence is and return its name.

        A sentence is a header when the tree holds a header node named by the first
        word of the sentence.

        Returns:
            The name of the header. None when the sentence is not one.
        """
        if not unit.word_list:
            return None
        first_word = unit.word_list[0][0]
        for node in node_list:
            if node.type not in _HEADER_NODE_TYPE_SET:
                continue
            text = node.text.decode("utf-8").split()[0].rstrip(".")
            if self.name(text).upper() == first_word.upper():
                self.header_list.append((first_word, node.type, line))
                return first_word
        return None

    def _read_value(self, node_list: list[Node], item_name: str) -> None:
        """Record the literals of the VALUE clause of a data item."""
        for node in node_list:
            if node.type == "string" and node.parent is not None and node.parent.type == "value_item":
                self.item_literal_dict.setdefault(item_name.upper(), []).append(
                    self.node_name(node)
                )

    def _read_move(self, node_list: list[Node]) -> None:
        """Record the literal each MOVE statement gives to the data items after TO."""
        for node in node_list:
            child_list = node.named_children if node.type == "move_statement" else []
            if len(child_list) < 2 or child_list[0].type != "string":
                continue
            literal = self.node_name(child_list[0])
            for target_node in child_list[1:]:
                word_node = next(
                    (item for item in _node_list(target_node) if item.type == "WORD"), None,
                )
                if word_node is not None:
                    item_name = self.node_name(word_node).upper()
                    self.item_literal_dict.setdefault(item_name, []).append(literal)

    def _add_import(self) -> None:
        """Build the COPY and CALL statements of the file, in line order.

        A CALL of a data item becomes one CALL for each literal the item is given.
        Each CALL adds a reference to the program name on its line.
        """
        import_list = [
            CobolImport(COPY_KIND, copy.name, copy.line, copy.library, copy.replacing_list)
            for copy in self.cobol_text.copy_list
        ]
        for name, is_literal, line in self.cobol_text.call_list:
            if is_literal:
                name_list = [name]
            else:
                name_list = list(dict.fromkeys(self.item_literal_dict.get(name.upper(), [])))
            for program_name in name_list:
                if program_name:
                    import_list.append(CobolImport(CALL_KIND, program_name, line))
                    self.source.reference_list.append(
                        CobolReference(program_name.upper(), line, is_call=True)
                    )
        self.source.import_list = sorted(import_list, key=lambda cobol_import: cobol_import.line)

    def _last_code_line(self, line: int) -> int:
        """Return the last line with code at or before a line (the line itself without one)."""
        code_line_list = self.cobol_text.code_line_list
        index = bisect_right(code_line_list, line)
        return code_line_list[index - 1] if index else line

    def _program_definition_list(self) -> list[CobolDefinition]:
        """Build the definition of each program.

        A program ends on its END PROGRAM line. Without one it ends on the last line
        with code before the next program, or on the last line with code of the file.
        """
        program_list = self.cobol_text.program_list
        definition_list: list[CobolDefinition] = []
        last_line = self._last_code_line(len(self.source.line_list))
        for index, (name, start_line, name_line) in enumerate(program_list):
            end_line = next(
                (
                    line for end_name, line in self.cobol_text.program_end_list
                    if end_name.upper() == name.upper() and line > name_line
                ),
                0,
            )
            if not end_line and index + 1 < len(program_list):
                end_line = self._last_code_line(program_list[index + 1][1] - 1)
            definition_list.append(CobolDefinition(
                name, PROGRAM_TYPE, start_line, max(end_line or last_line, name_line), name_line,
            ))
        return definition_list

    def _header_definition_list(self) -> list[CobolDefinition]:
        """Build the definition of each section and paragraph.

        A paragraph ends on the last line with code before the next header, a section
        before the next section header; both end before the line that starts or ends
        a program.
        """
        limit_line_list = sorted(
            [start_line for _, start_line, _ in self.cobol_text.program_list]
            + [line for _, line in self.cobol_text.program_end_list]
            + [len(self.source.line_list) + 1]
        )
        definition_list: list[CobolDefinition] = []
        for index, (name, header_type, line) in enumerate(self.header_list):
            next_line = limit_line_list[bisect_right(limit_line_list, line)]
            for _, next_type, next_header_line in self.header_list[index + 1:]:
                if next_header_line >= next_line:
                    break
                if header_type == PARAGRAPH_TYPE or next_type == SECTION_TYPE:
                    next_line = next_header_line
                    break
            end_line = max(self._last_code_line(next_line - 1), line)
            definition_list.append(CobolDefinition(name, header_type, line, end_line, line))
        return definition_list

    def _item_definition_list(self) -> list[CobolDefinition]:
        """Build the definition of each data item with a name.

        Processing flow:
        1. Find the items that items belong to (_subordinate_extent_list)
        2. Make an item that is not an elementary item and that no item belongs to a
           group item when a COPY statement comes after it before the next statement
           that does not belong to it; its own lines end on the last line of the first
           such COPY statement (_copy_end_line)
        3. End each item on the last line of the items that belong to it
        4. Build the definitions
        """
        # == Step 1: Items that items belong to ===============================
        own_end_line_list = [item.end_line for item in self.item_list]
        _, is_group_list = self._subordinate_extent_list(own_end_line_list)

        # == Step 2: Items whose items come from a COPY statement ===============
        item_by_unit_dict = {item.unit_index: item for item in self.item_list}
        for index, item in enumerate(self.item_list):
            if is_group_list[index] or item.is_elementary or item.level not in _GROUP_LEVEL_RANGE:
                continue
            copy_end_line = self._copy_end_line(item, item_by_unit_dict)
            if copy_end_line is not None:
                is_group_list[index] = True
                own_end_line_list[index] = max(own_end_line_list[index], copy_end_line)

        # == Step 3: Last line of each item ===================================
        end_line_list, _ = self._subordinate_extent_list(own_end_line_list)

        # == Step 4: Definitions ==============================================
        return [
            CobolDefinition(
                name=item.name,
                type=DATA_ITEM_TYPE,
                start_line=item.start_line,
                end_line=end_line_list[index],
                name_line=item.name_line,
                level=item.level,
                is_group=is_group_list[index],
            )
            for index, item in enumerate(self.item_list)
            if item.name is not None
        ]

    def _subordinate_extent_list(
        self, own_end_line_list: list[int],
    ) -> tuple[list[int], list[bool]]:
        """Return the last line of each data item and whether items belong to it.

        An item belongs to the item before it with a lower rank (_item_rank). An item
        ends on the last of its own last line and those of the items that belong to it.

        Args:
            own_end_line_list: The last line of each item by itself, in the order of item_list.

        Returns:
            (last line of each item, whether an item other than a condition name or a
            RENAMES item belongs to it), both in the order of item_list.
        """
        end_line_list = list(own_end_line_list)
        is_group_list = [False] * len(self.item_list)
        # (index, rank) of the items the next item can belong to
        open_list: list[tuple[int, int]] = []
        for index, item in enumerate(self.item_list):
            rank = _item_rank(item.level)
            while open_list and open_list[-1][1] >= rank:
                open_list.pop()
            for open_index, _ in open_list:
                end_line_list[open_index] = max(end_line_list[open_index], end_line_list[index])
                if item.level not in (_RENAME_LEVEL, _CONDITION_LEVEL):
                    is_group_list[open_index] = True
            open_list.append((index, rank))
        return end_line_list, is_group_list

    def _copy_end_line(self, item: _Item, item_by_unit_dict: dict[int, _Item]) -> int | None:
        """Return the last line of the first COPY statement after a data item that belongs to it.

        Args:
            item: The data item.
            item_by_unit_dict: {unit index: data item of that unit} of every data item.

        Returns:
            The line. None when no COPY statement comes after the item's statement before
            the next statement that does not belong to it (_extent_end_position).
        """
        start_position = self.cobol_text.unit_list[item.unit_index].end_position
        end_position = self._extent_end_position(item, item_by_unit_dict)
        return next(
            (
                cobol_copy.end_line for cobol_copy in self.cobol_text.copy_list
                if start_position < cobol_copy.position < end_position
            ),
            None,
        )

    def _extent_end_position(self, item: _Item, item_by_unit_dict: dict[int, _Item]) -> int:
        """Return the position of the next statement that does not belong to a data item.

        The statement is the next one in no unit (a section header, ...), the next unit
        that is not a data item, or the next data item whose rank is not higher than
        that of the item.

        Args:
            item: The data item.
            item_by_unit_dict: {unit index: data item of that unit} of every data item.

        Returns:
            Index of the first token of that statement among the tokens of the file; one
            more than the last index of any token when no such statement follows.
        """
        unit_list = self.cobol_text.unit_list
        start_position = unit_list[item.unit_index].end_position
        other_position_list = self.cobol_text.other_statement_position_list
        other_index = bisect_right(other_position_list, start_position)
        end_position = (
            other_position_list[other_index] if other_index < len(other_position_list)
            else _NO_POSITION
        )
        rank = _item_rank(item.level)
        for unit_index in range(item.unit_index + 1, len(unit_list)):
            other = item_by_unit_dict.get(unit_index)
            if other is None or _item_rank(other.level) <= rank:
                return min(end_position, unit_list[unit_index].start_position)
        return end_position


def read_cobol_source(source: str, language: Language) -> CobolSource:
    """Parse the text of a COBOL file and return what it defines, names and refers to.

    The text is split by split_cobol_source() and each unit is parsed by itself.
    Names and line numbers of the result are those of the source.

    Args:
        source: The text of the file.
        language: The tree-sitter Language of the COBOL grammar.

    Returns:
        A CobolSource.
    """
    cobol_text = split_cobol_source(source)
    return _UnitReader(cobol_text, line_list_of(source), language).read()
