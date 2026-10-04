# Design Document: codetwine/extractors/cobol_source.py

# Design Specification

**Overview**

Parse COBOL source files into definitions (programs, sections, paragraphs, data items, files, index names, entry statements), references (names used in code), and imports (COPY and CALL statements), extracting metadata about their locations, nesting, and attributes.

This file is used to:
- Call `read_cobol_source()` to parse a COBOL source text and obtain a `CobolSource` containing all definitions, references, and imports in the file.
- Call `has_statement()` to determine whether a COBOL unit contains a valid statement in the procedure division, used when filtering copybooks.
- Access `CobolSource.definition_list` to iterate over all definitions (programs, sections, paragraphs, data items, files, index names) in line order.
- Access `CobolSource.reference_list` to find all name references in the file, with qualifiers (OF/IN chains) and call indicators.
- Access `CobolSource.import_list` to extract COPY and CALL statements, including library names and REPLACING operands.
- Call `CobolSource.usage_line_list()` to retrieve all references to a given set of names, accounting for case-insensitive matching.
- Call `CobolSource.definition_source()` and `CobolSource.definition_text()` to retrieve the source text of a definition.

The file depends on `codetwine/parsers/cobol_format.py` to split COBOL source into units (data items, file descriptions, procedure division sentences) and extract programs, COPY/CALL statements, and word references without parsing; it uses `codetwine/utils/file_utils.py` only to split source text into lines. Five files consume this module: `codetwine/cobol_file_index.py` uses definitions and references to build a cross-file index of programs and data names; `codetwine/extractors/bms_source.py` creates `CobolSource` objects for BMS symbolic maps; `codetwine/extractors/definitions.py`, `codetwine/extractors/imports.py`, and `codetwine/extractors/usages.py` extract normalized metadata from `CobolSource` for analysis.

Definitions are built in a multi-pass process: unit parsing collects headers and data items with their spans; subordinate extent calculation determines which items belong to group items; data item definitions are expanded to include their subordinates and COPY statements within their extent; program, section, and paragraph boundaries are determined by tracking program-level control flow and procedure division structure. Index names (INDEXED BY) are recorded as separate definitions. Data items with no name (FILLER or syntax errors) are excluded from the definition list but still participate in hierarchy calculations.

**Definitions**

## `PROGRAM_TYPE`

Constant string "program_definition" identifying a definition created by a PROGRAM-ID statement.

## `ENTRY_TYPE`

Constant string "entry_statement" identifying a definition created by an ENTRY statement.

## `SECTION_TYPE`

Constant string "section_header" identifying a definition created by a section header in the procedure division.

## `PARAGRAPH_TYPE`

Constant string "paragraph_header" identifying a definition created by a paragraph header in the procedure division.

## `DATA_ITEM_TYPE`

Constant string "data_description" identifying a definition created by a data description entry (01–49, 66, 77, 78 level numbers).

## `FILE_TYPE`

Constant string "file_description_entry" identifying a definition created by a file description entry (FD or SD).

## `INDEX_TYPE`

Constant string "occurs_indexed" identifying a definition created by an INDEXED BY clause on a data item with OCCURS.

## `CALL_TARGET_TYPE_TUPLE`

Tuple of definition types (`PROGRAM_TYPE`, `ENTRY_TYPE`) that represent targets of CALL statements.

## `COPY_KIND`

Constant string "COPY" identifying a COPY statement as an import kind.

## `CALL_KIND`

Constant string "CALL" identifying a CALL statement as an import kind.

## `CobolDefinition`

A dataclass holding one definition found in a COBOL file: its name as written in source, type (program, entry, section, paragraph, data item, file, or index), start and end line numbers, the line where the name appears, and optional metadata (level number for data items; is_group flag for data items). Used by all consumers to identify and locate definitions, particularly to match references to their definitions and to determine nesting relationships.

## `CobolDefinition.name`

The name as written in the COBOL source (preserving case and any non-ASCII characters replaced by stand-ins).

## `CobolDefinition.type`

One of the definition type constants (`PROGRAM_TYPE`, `ENTRY_TYPE`, `SECTION_TYPE`, `PARAGRAPH_TYPE`, `DATA_ITEM_TYPE`, `FILE_TYPE`, `INDEX_TYPE`) indicating what kind of definition this is.

## `CobolDefinition.start_line`

The line number (1-based) where the definition begins.

## `CobolDefinition.end_line`

The line number (1-based) where the definition ends, inclusive. For data items and files, this includes subordinate items and COPY statements within their extent.

## `CobolDefinition.name_line`

The line number (1-based) where the name is written in the source.

## `CobolDefinition.level`

For data item definitions, the level number (01–88); None for other definition types.

## `CobolDefinition.is_group`

For data item definitions, True if the item is a group item (subordinate items belong to it or COPY statements within its extent bring in subordinates), False if it is elementary, None for other definition types.

## `CobolReference`

A dataclass holding one place a COBOL file refers to a name: the upper-case name, line number, optional qualifiers (names after OF/IN keywords in source order, innermost first), and a flag indicating whether it is a program name in a CALL statement. Used to find all usages of names and to resolve them to definitions.

## `CobolReference.name`

The name in upper case.

## `CobolReference.line`

The line number (1-based) where the reference appears.

## `CobolReference.qualifier_tuple`

A tuple of upper-case names that qualify the reference via OF/IN keywords (e.g., X OF Y IN Z gives ("Y", "Z") for X), or empty tuple if unqualified.

## `CobolReference.is_call`

True if this reference is the program name in a CALL statement, False otherwise.

## `CobolImport`

A dataclass holding one COPY or CALL statement: the kind (COPY_KIND or CALL_KIND), name of the copybook or program, line number, optional library name (for COPY OF library), and optional list of REPLACING operands as (position, replaced text, replacement text) tuples. Used to identify external dependencies and CALL targets.

## `CobolImport.kind`

Either COPY_KIND or CALL_KIND.

## `CobolImport.name`

The copybook name or program name.

## `CobolImport.line`

The line number (1-based) of the statement.

## `CobolImport.library`

The library name from COPY ... OF library, empty string if not present.

## `CobolImport.replacing_list`

List of REPLACING operands from a COPY statement, each as (position, replaced text, replacement text). Empty list for CALL statements or COPY without REPLACING.

## `CobolSource`

A dataclass holding everything extracted from a COBOL source file: the source lines, all definitions, all imports (COPY and CALL statements), all references, and alternate copy names (e.g., mapset names in BMS). This is the main output of the module and is consumed by all downstream analysis.

## `CobolSource.line_list`

List of source lines as strings, preserving original content including comments.

## `CobolSource.definition_list`

List of all definitions in the file, sorted by start line (ascending) then by end line (descending), including programs, entries, sections, paragraphs, data items (with names), files, and index names.

## `CobolSource.import_list`

List of all COPY and CALL statements in line order.

## `CobolSource.reference_list`

List of all name references in the file.

## `CobolSource.copy_name_list`

List of alternate names that COPY statements can use to reference the file (used for BMS symbolic maps).

## `CobolSource.usage_line_list`

Method that takes a set of names and returns a sorted list of (name, line) tuples for each place any of those names is referenced, matching case-insensitively and deduplicating by (name, line) pair.

## `CobolSource.definition_text`

Method that returns the source text of a given definition as its lines joined by newline, extracted from line_list using the definition's start_line and end_line.

## `CobolSource.definition_source`

Method that returns the source text of the first definition with a given name (case-insensitive), or None if no such definition exists.

## `_item_rank`

Static function that computes the rank of a data item for hierarchy purposes: level 1, 77, 78 → rank 1; level 66 → rank 2; level 88 → rank 100; other levels → the level number itself. Lower rank means the item can contain items with higher rank.

## `_Item`

Internal dataclass holding a data item as read from units: its name (or None for FILLER), level number, line span, line where name appears, unit index, and whether its statement contains elementary-making clauses (PIC, VALUE, special USAGE).

## `_File`

Internal dataclass holding a file description (FD/SD) as read from units: its name, line span, name line, and unit index.

## `_is_elementary_word_list`

Internal function that checks whether a data item's statement word list contains any of the elementary-making keywords (PIC, PICTURE, VALUE, VALUES, INDEX, POINTER, various COMP and FLOAT variants, etc.), used to determine if an item with no subordinates is elementary or potentially a group.

## `_node_list`

Internal function that returns a depth-first traversal of all nodes in a tree-sitter parse tree in source order, used to extract information from parsed units.

## `_UnitReader`

Internal class that orchestrates parsing and analysis of a COBOL source file: it maintains state for headers, data items, files, index definitions, literals assigned to items, and item-name parsing cache; reads each unit by parsing with tree-sitter and extracting definitions and references; builds hierarchies of data items and determines their spans; and finally constructs definitions and imports in line order. It is instantiated once per source file and discarded after reading.

## `_UnitReader.__init__`

Constructor that initializes internal state given the CobolText (units and metadata from splitting), source line list, and tree-sitter Language.

## `_UnitReader.name`

Method that returns the original name as written in source for a stand-in name (used to recover case and non-ASCII characters from cobol_text.name_dict).

## `_UnitReader.node_name`

Method that extracts the content of a tree-sitter node: if it is a literal (quoted string, hex/octal), returns the literal content; otherwise returns the original source name via name().

## `_UnitReader.read`

Main method that orchestrates the four-step parsing process: parse units and collect headers, items, files, and references; add references from statements outside units; build definitions by combining program spans, header spans, item hierarchies, index definitions, and file spans; and build imports from COPY and CALL statements. Returns the completed CobolSource.

## `_UnitReader._read_unit`

Internal method that parses one unit with tree-sitter, extracts what it defines and what it refers to, records the definition and literals, and handles both parse-success (extract node references) and parse-error (extract word references) cases.

## `_UnitReader._read_definition`

Internal method that processes a single unit to extract its definition (if any) and side effects (literals assigned to items, index names, file entries, procedure division headers); dispatches based on unit kind (ITEM_UNIT, FILE_UNIT, PROCEDURE_UNIT).

## `_UnitReader._add_word_reference`

Internal method that adds every word of a unit as a reference except the first one (the defined name), handling OF/IN qualifiers using qualifier_tuple().

## `_UnitReader._add_node_reference`

Internal method that adds each WORD node in a unit's parse tree as a reference, skipping definition names (entry names, file names, index names in occurs_indexed nodes), and computing qualifiers from qualified_word parent nodes.

## `_UnitReader._node_qualifier_tuple`

Internal method that extracts the upper-case qualifier names from a WORD node by finding it in its qualified_word parent and returning subsequent WORD nodes in that parent.

## `_UnitReader._is_definition_name`

Internal static method that checks whether a WORD node is a definition name by testing for parent types constant_entry, file_description_entry, or occurs_indexed.

## `_UnitReader._item_name`

Internal method that extracts the name of a data item from a unit: searches for entry_name nodes or WORD nodes in constant_entry that are the first word; returns None for FILLER or items with name suffixes; handles cases where the word after the level number is a grammar-recognized item name. Returns (name, line) or (None, 0).

## `_UnitReader._read_index`

Internal method that records each INDEXED BY index name of a data item as a separate definition with the same span as the item.

## `_UnitReader._is_item_name`

Internal method that caches whether the grammar parses a given word as an item name by constructing a minimal item_unit, parsing it, and checking for entry_name nodes.

## `_UnitReader._file_name`

Internal method that extracts the name of a file description entry by finding a WORD node that is a definition name (first word of file_description_entry parent). Returns (name, line) or (None, 0).

## `_UnitReader._header_name`

Internal method that identifies whether a procedure division unit is a section or paragraph header by matching its first word against header nodes in the parse tree; records the header in header_list with its type and line. Returns the header name or None.

## `_UnitReader._read_value`

Internal method that records literals from VALUE clauses in data items by finding string nodes whose parent is a value_item.

## `_UnitReader._read_move`

Internal method that records literals assigned to data items by MOVE statements in the procedure division by finding move_statement nodes with a string as the first child and WORD nodes in subsequent children.

## `_UnitReader._add_import`

Internal method that constructs CobolImport objects from cobol_text.copy_list and cobol_text.call_list: expands CALL statements that reference data items (non-literals) by substituting the literals assigned to those items, records each as an import, adds a reference for each program name, and sorts by line number.

## `_UnitReader._last_code_line`

Internal method that returns the last line with code at or before a given line by binary search in cobol_text.code_line_list.

## `_UnitReader._program_definition_list`

Internal method that builds definitions for each program by finding its span: uses END PROGRAM statements if present, otherwise the line before the next program (or end of file), and ensures the span is at least from start_line to name_line.

## `_UnitReader._header_definition_list`

Internal method that builds definitions for sections and paragraphs recorded in header_list by determining their end lines: paragraphs end before the next header or section; sections end before the next section; both end before program boundaries found in cobol_text.program_list and program_end_list.

## `_UnitReader._item_definition_list`

Internal method that builds definitions for all named data items by: computing which items are subordinate to which via _subordinate_extent_list; marking items as group items if they have subordinates or COPY statements within their extent (via _copy_end_line); expanding end lines to include subordinates; and filtering to include only items with names.

## `_UnitReader._subordinate_extent_list`

Internal method that computes the last line of each data item (including subordinates) and whether each item is a group item, given the items' own end lines. Uses _item_rank to determine hierarchy: an item belongs to the preceding item with lower rank. Returns (end_line_list, is_group_list) in item_list order.

## `_UnitReader._copy_end_line`

Internal method that returns the end line of the first COPY statement after a data item within its subordinate extent, or None if no such COPY exists. Uses start_position and _extent_end_position to bound the search in cobol_text.copy_list.

## `_UnitReader._extent_end_position`

Internal method that computes the token position of the next statement that does not belong to a unit (section header, PROGRAM-ID, etc.) after a given unit. Searches cobol_text.other_statement_position_list and the next unit that is not a subordinate data item, using _item_rank to determine subordinacy.

## `_UnitReader._file_definition_list`

Internal method that builds definitions for file descriptions (FD/SD) by finding all data items and COPY statements within their extent (via _extent_end_position) and expanding their end lines to include these.

## `has_statement`

Function that determines whether a COBOL procedure division unit contains a valid statement: parses the unit with tree-sitter and checks for a statement node (type ending in _statement_node_suffix) that starts on a source line. Used to filter copybooks that contain executable code.

## `read_cobol_source`

Main entry point that parses a COBOL source file and returns a CobolSource: splits the source via split_cobol_source(), instantiates _UnitReader, and calls its read() method. Takes source text and tree-sitter Language; returns CobolSource with all definitions, references, and imports.

# Summary

# Summary: codetwine/extractors/cobol_source.py

**Single Responsibility:** Parse COBOL source files into structured data capturing definitions (programs, sections, paragraphs, data items, files, index names, entry statements), references (name uses with qualifiers), and imports (COPY and CALL statements), including metadata about locations, nesting, hierarchy, and attributes.

**Main Public Definitions:**
- `CobolSource`: dataclass holding parsed file contents—lines, definitions, imports, references, and methods to query usage and retrieve source text
- `CobolDefinition`: dataclass representing one definition with name, type, line span, and optional metadata (level, is_group)
- `CobolReference`: dataclass representing one name reference with qualifiers and call indicator
- `CobolImport`: dataclass representing COPY or CALL statements with library and REPLACING info
- `read_cobol_source()`: main entry point returning CobolSource
- `has_statement()`: checks if a procedure division unit contains valid code

**Key Terms:** definition types (program, entry, section, paragraph, data item, file, index), hierarchical data item structure, subordinate extent, group/elementary classification, qualified references (OF/IN chains), copy statements, call statements, tree-sitter parsing, multi-pass processing.
