# Design Document: codetwine/extractors/bms_source.py

# Design Specification

**Overview**

Parse BMS (Basic Mapping Support) source files containing DFHMSD/DFHMDI/DFHMDF macros and extract their symbolic map definitions as structured COBOL data items.

This file serves these situations:

- Call `read_bms_source()` with the text content of a BMS file to obtain a `CobolSource` containing the symbolic map records and fields as COBOL data item definitions, along with mapset names as copy statement aliases.
- Use the returned `CobolSource` to access line-indexed definitions of map records (input/output variants), field groups, and extended attributes that a COBOL program would receive via a COPY statement of the mapset.
- Integrate BMS file parsing into a broader source analysis pipeline that treats BMS files similarly to COBOL files by producing the same `CobolSource` structure.

The file depends on `CobolSource`, `CobolDefinition`, and `line_list_of()` from project modules `cobol_source.py` and `file_utils.py` to represent and return parsed results in a format consistent with COBOL parsing. It is used by `ts_parser.py` to parse BMS files identified by extension and produce a `CobolSource` for inclusion in project analysis. BMS parsing produces no import or reference lists, only definitions and copy names.

The file treats all macro labels that do not match the COBOL name pattern (letters, digits, hyphens, starting with a letter) as unnamed and omits them from results. BMS statements can span multiple lines via continuation characters in column 72; operands are parsed up to the first unquoted blank, and remarks are discarded. Mapsets with TYPE=FINAL are ignored.

**Definitions**

## `_CONTINUE_COLUMN`

Column index (0-based) marking position 72 of a BMS assembler statement, where a non-blank character indicates the statement continues on the next line.

## `_CONTINUE_START_COLUMN`

Column index (0-based) marking position 16, where the text of a continuation line begins in a BMS assembler statement.

## `_ATTRIBUTE_TUPLE`

Tuple of (attribute name, field suffix letter) pairs defining the extended attributes (COLOR, PS, HILIGHT, VALIDN, OUTLINE, SOSI, TRANSP) that BMS fields can have and their corresponding letters appended to field names in the symbolic map.

## `_EXTATT_ATTRIBUTE_TUPLE`

Tuple of attribute names (COLOR, PS, HILIGHT, VALIDN) that are automatically included in every field when EXTATT=YES is specified without an explicit DSATTS list.

## `_LENGTH_LETTER`

The letter "L" that ends the name of the length field created for each BMS field in the input record of a symbolic map.

## `_FLAG_LETTER`

The letter "F" that ends the name of the flag (status) field created for each BMS field in the input record of a symbolic map.

## `_ATTRIBUTE_LETTER`

The letter "A" that ends the name of the extended attribute field created for each BMS field in the input record of a symbolic map.

## `_INPUT_LETTER`

The letter "I" that ends the name of the input record variant of each map and its fields in a symbolic map.

## `_OUTPUT_LETTER`

The letter "O" that ends the name of the output record variant of each map and its fields in a symbolic map.

## `_RECORD_LEVEL`

The COBOL data item level number (1) assigned to map record definitions (the input and output record groups).

## `_FIELD_LEVEL`

The COBOL data item level number (2) assigned to field definitions directly under a map record, or level 3 if the field has OCCURS.

## `_NAME_RE`

Compiled regular expression pattern matching valid COBOL names: a letter followed by zero or more letters, digits, or hyphens.

## `_STATEMENT_HEAD_RE`

Compiled regular expression pattern matching the label and operation at the start of a BMS assembler statement, capturing both as separate groups.

## `_Statement`

Dataclass representing one parsed assembler statement from a BMS source, holding its label, upper-case operation name (DFHMSD, DFHMDI, DFHMDF, etc.), operand text without remarks, and the 1-based line range it spans including continuations.

## `_Field`

Dataclass representing either a named field or a field group (GRPNAME) defined in a BMS map, holding its upper-case name, OCCURS count (0 if absent), line range, and a list of member fields for groups.

## `_Map`

Dataclass representing one map (DFHMDI statement) within a mapset, holding its upper-case name, the list of extended attribute letters applicable to its fields, the line range of all its statements, and its list of top-level fields and field groups.

## `_statement_list()`

Parse a BMS source into a list of assembler statements by reading lines, handling comment lines (starting with "*"), joining continuation lines (identified by non-blank character in column 72), and extracting operands up to the first unquoted blank; returns statements in source order with line numbers and operand text.

## `_operand_dict()`

Split a BMS operand string of the form "KEY=value,..." into a dictionary mapping upper-case keys to their values, respecting nested parentheses and quoted strings so commas inside them are not treated as delimiters; operands without "=" receive empty-string values.

## `_value_list()`

Extract and return a list of upper-case items from a BMS operand value such as "(COLOR,HILIGHT)" or "YES" by stripping parentheses and splitting at commas.

## `_attribute_list()`

Determine which extended attribute letters should appear in a map's symbolic map fields by examining the DSATTS operand (if present), falling back to EXTATT=YES defaults, or returning an empty list; takes a merged operand dictionary from both mapset and map levels.

## `_read_map_list()`

Process a list of BMS statements to extract map definitions and mapset names, building a tree of maps containing fields and field groups; ignores statements with non-COBOL-word labels and mapsets marked TYPE=FINAL; returns a tuple of the extracted maps in source order and the list of mapset names.

## `_item_definition()`

Create a `CobolDefinition` representing one data item of a BMS symbolic map with the given name, line range, level, and group status; used internally to generate definitions for records, fields, and field attributes.

## `_field_definition_list()`

Generate all `CobolDefinition` objects for a single BMS field, including the length field (L), flag field (F), attribute field (A), input variant (I), extended attribute variants (one per attribute letter), output variant (O), and for field groups, the input and output variants of each member; returns definitions spanning the field's statement lines.

## `read_bms_source()`

Parse a BMS source file into a `CobolSource` by extracting statements, maps, and fields; create definitions for each map's input and output records and all their component fields with extended attributes; return the result with definitions sorted by line range and mapset names as copy statement aliases; the returned `CobolSource` has empty import and reference lists.

# Summary

# Summary: codetwine/extractors/bms_source.py

**Single Responsibility**
Parse BMS (Basic Mapping Support) assembler source files to extract symbolic map definitions as structured COBOL data items, producing a CobolSource object compatible with COBOL parsing output.

**Main Public Definition**
`read_bms_source()` — parses BMS file text and returns a CobolSource containing map records, fields, and extended attributes as COBOL definitions, with mapset names as copy aliases.

**Key Terms**
Handles DFHMSD/DFHMDI/DFHMDF macros defining mapsets and maps; extracts symbolic map records (input/output variants), fields with length/flag/attribute suffixes, field groups, and extended attributes (COLOR, PS, HILIGHT, VALIDN, OUTLINE, SOSI, TRANSP); processes assembler continuation lines; validates COBOL-compliant names; ignores TYPE=FINAL mapsets and malformed labels.
