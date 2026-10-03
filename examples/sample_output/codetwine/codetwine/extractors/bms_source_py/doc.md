# Design Document: codetwine/extractors/bms_source.py

# Design Specification

**Overview**

Parse BMS (Basic Mapping Support) source files containing DFHMSD/DFHMDI/DFHMDF macro definitions and extract their symbolic map structures as COBOL data item definitions.

This file is used to:
- Call `read_bms_source()` from a file parser to convert BMS macro text into a `CobolSource` object containing symbolic map definitions and mapset names for downstream analysis
- Enable COBOL programs to reference BMS-generated copybook structures by extracting the data items (records, fields, and their variants) that a COPY statement would provide

The file depends on `codetwine/extractors/cobol_source.py` for `CobolDefinition`, `CobolSource`, and `DATA_ITEM_TYPE` to represent extracted map structures in the standard COBOL definition format. It uses `codetwine/utils/file_utils.py` only to split source text into lines. The file `codetwine/parsers/ts_parser.py` calls `read_bms_source()` when processing files with BMS extensions to parse their content.

The parsing strategy skips statements and fields whose labels are not valid COBOL words (via the `_NAME_RE` pattern), silently omitting them from the result without error reporting. Line continuation in assembler format (column 72) and operand parsing within quotes and parentheses are handled to reconstruct complete statements and key-value operand pairs.

**Definitions**

## `_CONTINUE_COLUMN`

The zero-based column index (71, meaning column 72 in one-based notation) where the continuation marker appears in assembler format BMS source lines; a non-space character here indicates the statement continues on the next line.

## `_CONTINUE_START_COLUMN`

The zero-based column index (15, meaning column 16) where text resumes on a continuation line in assembler format BMS source.

## `_ATTRIBUTE_TUPLE`

A tuple of (attribute name, letter suffix) pairs defining the extended field attributes (COLOR, PS, HILIGHT, VALIDN, OUTLINE, SOSI, TRANSP) that BMS symbolic maps can include in their output record definitions, in the order the symbolic map holds them; used to generate suffixed field names in output records.

## `_EXTATT_ATTRIBUTE_TUPLE`

A tuple of the default extended attributes (COLOR, PS, HILIGHT, VALIDN) that are included in symbolic map fields when the BMS mapset specifies `EXTATT=YES` without listing specific attributes via DSATTS.

## `_LENGTH_LETTER`

The letter "L" appended to field names in the input record to create length data items in symbolic maps.

## `_FLAG_LETTER`

The letter "F" appended to field names in the input record to create flag data items in symbolic maps.

## `_ATTRIBUTE_LETTER`

The letter "A" appended to field names in the input record to create attribute data items in symbolic maps; these are defined as children of a FILLER that redefines the flag field.

## `_INPUT_LETTER`

The letter "I" appended to map and field names to create input record variants in symbolic maps.

## `_OUTPUT_LETTER`

The letter "O" appended to map and field names to create output record variants in symbolic maps.

## `_RECORD_LEVEL`

The COBOL level number 1 assigned to the input and output records of each map in the symbolic map structure.

## `_FIELD_LEVEL`

The COBOL level number 2 assigned to fields directly within map records, or level 3 when a field has OCCURS.

## `_NAME_RE`

A compiled regex pattern matching valid COBOL words (starting with a letter, followed by letters, digits, or hyphens) used to validate BMS statement labels and group names; labels not matching this pattern are excluded from the extracted definitions.

## `_STATEMENT_HEAD_RE`

A compiled regex pattern extracting the label and operation (upper-cased) from the beginning of an assembler source line, up to column 72.

## `_Statement`

A dataclass representing one assembler statement parsed from BMS source, holding its label (empty if absent), upper-case operation name (DFHMSD, DFHMDI, DFHMDF, etc.), the operand text excluding remarks, and the range of source lines it spans including continuations.

## `_Field`

A dataclass representing a named field (or a group via GRPNAME) within a BMS map, storing its name, optional OCCURS count, source line range, and a list of member fields for groups.

## `_Map`

A dataclass representing one map (DFHMDI statement) within a BMS mapset, holding its name, the letters of extended attributes its fields receive, source line range, and the list of fields and groups it contains.

## `_statement_list`

Parses assembler format BMS source lines into complete statements by handling line continuation (checking column 72), extracting labels and operations, and building operand text while respecting quotes to avoid splitting at spaces or commas inside quoted strings; comments (lines starting with "*") and blank lines are skipped.

## `_operand_dict`

Splits BMS operand text of the form "KEY=value,..." into a dictionary by splitting at commas outside quotes and parentheses, returning uppercase keys mapped to values as written; keys without "=" have empty string values.

## `_value_list`

Extracts and returns a list of upper-case items from operand values such as "(COLOR,HILIGHT)" or "YES" by stripping parentheses and splitting at commas.

## `_attribute_list`

Determines the extended attribute letters (COLOR, PS, HILIGHT, etc.) that fields in a map will have in its symbolic map by checking the mapset and map DFHMSD/DFHMDI operands, preferring explicit DSATTS over the EXTATT=YES default, returning the letters in `_ATTRIBUTE_TUPLE` order.

## `_read_map_list`

Traverses a list of parsed assembler statements to extract all maps (DFHMDI) and their fields (DFHMDF) from mapsets (DFHMSD), building a list of `_Map` objects and a list of mapset names; statements and fields with non-COBOL-word labels are silently skipped, and group membership (via GRPNAME) is tracked.

## `_item_definition`

Constructs a single `CobolDefinition` representing a symbolic map data item with the given name, source line range, COBOL level number, and group indicator; called to create definitions for records, fields, and their suffixed variants (L, F, A, I, O, and attribute letters).

## `_field_definition_list`

Generates all the `CobolDefinition` entries that a BMS field produces in the input and output records of its map: the field's length (L), flag (F), and attribute (A) items in the input record, the field itself and attribute suffix variants (COLOR, PS, etc.) in the output record, and for groups, the input (I) and output (O) members; each definition spans the field's source lines.

## `read_bms_source`

Parses a complete BMS source file containing DFHMSD/DFHMDI/DFHMDF macro definitions and returns a `CobolSource` with the symbolic map data item definitions extracted and sorted by source line, and the mapset names as copybook alternatives; this is the public entry point called by the file parser to convert BMS files into COBOL-compatible definition structures.

# Summary

# Summary

**Responsibility:** Parse BMS (Basic Mapping Support) source files containing DFHMSD/DFHMDI/DFHMDF macro definitions and extract their symbolic map structures as COBOL data item definitions compatible with copybook references.

**Main Public Definition:** `read_bms_source()` converts BMS macro text into a `CobolSource` object containing symbolic map definitions and mapset names for downstream analysis.

**Key Handling:** Processes assembler-format BMS source with line continuation (column 72), reconstructs complete statements, parses operand key-value pairs, validates labels as COBOL words, extracts maps and fields, generates input/output record variants with suffixed items (length, flag, attribute), and handles extended attributes (COLOR, PS, HILIGHT, VALIDN, OUTLINE, SOSI, TRANSP) based on mapset configuration.
