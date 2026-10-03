# Design Document: codetwine/extractors/cobol_source.py

# Design Specification

**Overview**

Parse COBOL source files into definitions, references, and imports by splitting source text into grammatically-readable units, parsing each with tree-sitter, and extracting names, statements, and data hierarchies.

- Call `read_cobol_source()` to parse a COBOL file and receive a `CobolSource` containing definitions (programs, sections, paragraphs, data items, files, index names), references to those names, and COPY/CALL import statements.
- Call `has_statement()` to determine whether a procedure division unit contains a parsed statement, used to distinguish executable copybooks from non-executable ones.
- Instantiate `_UnitReader` internally when `read_cobol_source()` processes units to accumulate definitions, references, and imports into a `CobolSource` object.
- Access `CobolSource.usage_line_list()` to find all places a file refers to a set of names, or `CobolSource.definition_source()` to retrieve the full source text of a definition by name.
- Access `CobolSource.definition_text()` to extract the source lines of a specific definition, useful for displaying or analyzing definition bodies.

This file relies on `codetwine/parsers/cobol_format.py` to split raw COBOL text into units (data items, file descriptions, procedure division sentences) with word lists and metadata; it uses the tree-sitter parser and `Language` object to parse each unit's grammar and extract structured information. It depends on `codetwine/utils/file_utils.py` for `line_list_of()` to normalize source line breaks. The file is used by `codetwine/cobol_file_index.py` to index programs and definitions, by `codetwine/extractors/` modules to extract definitions, imports, and usages from COBOL and BMS files, and by `codetwine/parsers/ts_parser.py` as the primary parse entry point for COBOL-family files.

The file processes units sequentially without caching, parses errors are handled by reverting to word-level reference extraction, and data item hierarchy is determined by level numbers and rank comparisons; items with no name or FILLER items are excluded from definitions but included in hierarchy calculations.

**Definitions**

## `PROGRAM_TYPE`

Constant string identifier for program definition entries in the `type` field of `CobolDefinition`, used to distinguish PROGRAM-ID definitions from other definition types in definition lists and filtering operations.

## `ENTRY_TYPE`

Constant string identifier for ENTRY statement definitions, used to mark callable entry points distinct from main program definitions.

## `SECTION_TYPE`

Constant string identifier for procedure division section header definitions, used to record section boundaries in the procedure division.

## `PARAGRAPH_TYPE`

Constant string identifier for procedure division paragraph header definitions, used to record paragraph boundaries within sections or the procedure division.

## `DATA_ITEM_TYPE`

Constant string identifier for data description item definitions, used to mark data items in the working-storage, linkage, local-storage, and file sections.

## `FILE_TYPE`

Constant string identifier for file description (FD/SD) definitions, used to distinguish file-level declarations from data items within them.

## `INDEX_TYPE`

Constant string identifier for INDEXED BY index name definitions, used to mark automatically-generated index variables created by OCCURS...INDEXED BY clauses.

## `CALL_TARGET_TYPE_TUPLE`

Tuple constant containing `PROGRAM_TYPE` and `ENTRY_TYPE`, used to filter definitions that can be targets of CALL statements when building indexes or analyzing call graphs.

## `COPY_KIND`

Constant string "COPY" marking the kind of import statement for COPY and EXEC SQL INCLUDE directives.

## `CALL_KIND`

Constant string "CALL" marking the kind of import statement for CALL statements and EXEC CICS PROGRAM() directives.

## `CobolDefinition`

Dataclass representing one definition (program, entry point, section, paragraph, data item, file, or index name) found in a COBOL file, storing its name as written in source, definition type, line range, and optional level number and group-item status; used throughout the extraction pipeline to identify what a COBOL file defines.

## `CobolDefinition.name`

The name as written in the source text, used to match definitions against references and to provide the original casing for output.

## `CobolDefinition.type`

One of the definition type constants (PROGRAM_TYPE, etc.), used to categorize definitions and control filtering and analysis operations.

## `CobolDefinition.start_line`

First line of the definition (1-based), used to determine definition boundaries and to sort definitions in source order.

## `CobolDefinition.end_line`

Last line of the definition (1-based), computed by analyzing subordinate items and COPY statements for data items, or by finding END PROGRAM markers for programs; used to determine definition spans and to resolve whether a reference falls within a definition's scope.

## `CobolDefinition.name_line`

Line where the name appears in the source (1-based), used for precise location reporting when a definition spans multiple lines.

## `CobolDefinition.level`

Level number (1–99) of a data item, None for other types; used to determine data item hierarchy, group membership, and whether an item can contain subordinate items.

## `CobolDefinition.is_group`

Whether a data item is a group item (has subordinate items or receives items from a COPY statement), None for non-data-item types; used to distinguish group items from elementary items for semantic analysis.

## `CobolReference`

Dataclass representing one place a COBOL file refers to a name (data item, procedure name, program name, etc.), storing the name, line, optional qualification chain, and call flag; used to track dependencies and to resolve references to definitions.

## `CobolReference.name`

Upper-case name being referred to, used as the key to match against definition names and imported names.

## `CobolReference.line`

Line where the reference appears (1-based), used for precise location reporting and to map references to the source display.

## `CobolReference.qualifier_tuple`

Tuple of upper-case qualifying names (e.g., `("RECORDA", "FILEA")` for `FIELD OF RECORDA IN FILEA`), innermost first; used to resolve qualified references to definitions within specific record structures or file hierarchies.

## `CobolReference.is_call`

Boolean flag set to True for program names in CALL statements, used to distinguish call references from data name references during analysis.

## `CobolImport`

Dataclass representing one COPY, EXEC SQL INCLUDE, or CALL statement found in a COBOL file, storing the statement kind, name, line, library name, and optional REPLACING operands; used to track dependencies on external files and programs.

## `CobolImport.kind`

Either COPY_KIND or CALL_KIND, used to determine whether the import refers to a copybook file or a callable program.

## `CobolImport.name`

Name of the copybook or program, used to resolve the import to a file path or program definition.

## `CobolImport.line`

Line of the import statement (1-based), used for precise location reporting.

## `CobolImport.library`

Library name specified in COPY...OF library syntax, empty string if absent; used to search for the copybook in library-specific directories.

## `CobolImport.replacing_list`

List of (position, replaced text, replacement text) tuples from COPY...REPLACING clauses, used to apply text substitutions when processing copied code.

## `CobolSource`

Dataclass representing the complete analysis result for one COBOL source file, storing line list, definitions, imports, references, and optional additional copy names (for BMS mapsets); serves as the output of `read_cobol_source()` and the input for downstream extraction and indexing operations.

## `CobolSource.line_list`

The source lines of the file, indexed from 0 (matching Python list indexing), used to retrieve definition source text and to validate line number references.

## `CobolSource.definition_list`

Definitions in order of start line (then reverse end line for nesting), used to extract all definitions a file provides and to answer queries about what a file defines.

## `CobolSource.import_list`

COPY and CALL statements in source order, used to traverse file dependencies and to build call graphs.

## `CobolSource.reference_list`

Each place a name is referred to, used to track usage and to resolve references to definitions for call graph and data flow analysis.

## `CobolSource.copy_name_list`

Additional names a COPY statement can reference the file by (e.g., BMS mapset names), used to index files by alternate names in addition to their file path.

## `CobolSource.usage_line_list()`

Return places the file refers to any name in a given set as (name, line) tuples in line order without duplicates; name matching is case-insensitive to handle COBOL's case-insensitivity while preserving the case given in the query set.

## `CobolSource.definition_text()`

Return the source text of a definition by joining its lines with newlines, used to display or export definition bodies.

## `CobolSource.definition_source()`

Return the source text of the first definition matching a name (case-insensitive), or None if not found; used to retrieve a specific definition's source by name without iterating the definition list.

## `_item_rank()`

Return the rank of a data item for hierarchy calculation: level 1/77/78 → 1, level 66 → 2, level 88 → 100, other levels → their level number; an item belongs to the item before it with a lower rank, allowing subordinate items to be grouped under parents with appropriate level numbers.

## `_Item`

Internal dataclass recording a data item as units are read, storing name, level, line range, unit index, and whether it is elementary; used to compute data item hierarchies and to build `CobolDefinition` objects for items.

## `_File`

Internal dataclass recording a file description (FD/SD) as units are read, storing name, line range, and unit index; used to build `CobolDefinition` objects for file descriptions.

## `_is_elementary_word_list()`

Return whether a data item's statement contains a PICTURE clause, VALUE clause, or non-picture USAGE (INDEX, POINTER, etc.), indicating the item must be elementary; used to determine whether an item with no subordinate items is group or elementary.

## `_node_list()`

Return every node of a tree-sitter parse tree in source order by depth-first traversal; used to iterate all nodes for reference and definition extraction.

## `_UnitReader`

Internal class that reads units from a `CobolText` object into a `CobolSource` by parsing each unit, extracting definitions and references, computing data item hierarchies, and building import statements; instantiated once per file during `read_cobol_source()`.

## `_UnitReader.__init__()`

Initialize the reader with a `CobolText` object, source line list, and tree-sitter `Language`, setting up internal state for parsing units and accumulating results.

## `_UnitReader.name()`

Return the source name for a stand-in name, looking up the mapping in `CobolText.name_dict` to restore names containing non-ASCII characters that were replaced during tokenization.

## `_UnitReader.node_name()`

Return the name or literal content a tree-sitter node holds by decoding its text, applying `literal_value()` to extract literal contents, and restoring source case via `name()`.

## `_UnitReader.read()`

Parse all units, add references from outside units, build definitions with line ranges, and compile import statements; return the completed `CobolSource` following a four-step processing flow (units, words outside units, definitions, imports).

## `_UnitReader._read_unit()`

Parse one unit's text with tree-sitter, record its definition (name, literals, index names), and add references; when the tree has errors, fall back to word-level reference extraction.

## `_UnitReader._read_definition()`

Record what a unit defines (item name, file name, or paragraph/section header), extract VALUE clause and MOVE statement literals for data items, and record INDEXED BY index names; return the name the unit defines or None.

## `_UnitReader._add_word_reference()`

Add every word of a unit as a reference except the first occurrence of the defined name, applying OF/IN qualification to construct qualifier tuples; used when parse errors prevent tree-based reference extraction.

## `_UnitReader._add_node_reference()`

Add each WORD node of the parse tree that is not a definition name as a reference, computing qualifiers from qualified_word parent nodes; used for error-free units to extract precise reference information from the grammar.

## `_UnitReader._node_qualifier_tuple()`

Return upper-case names qualifying a WORD node by extracting subsequent WORD nodes from its qualified_word parent, innermost first; implements qualification extraction for references like `FIELD OF RECORD IN FILE`.

## `_UnitReader._is_definition_name()`

Return True if a WORD node is a definition name (index name, constant entry name, or file description name), used to exclude definition names from reference lists.

## `_UnitReader._item_name()`

Return the name of a data item and its line; the name is extracted from entry_name nodes or from the word after the level number if the grammar recognizes it as an item name; returns (None, 0) for FILLER items or items with no name.

## `_UnitReader._read_index()`

Record each index name (INDEXED BY clause) as a definition with the same line range as its data item, allowing index names to be treated as definitions distinct from their parent items.

## `_UnitReader._is_item_name()`

Return whether the grammar reads a word as a data item name by creating a synthetic item_unit and parsing it, caching results to avoid re-parsing; used to distinguish actual item names from filler or other contexts.

## `_UnitReader._file_name()`

Return the name of a file description (FD/SD) and its line by finding the first WORD node marked as a definition name, or (None, 0) if not found.

## `_UnitReader._header_name()`

Record a section or paragraph header by finding a header node matching the first word of a sentence, record it in the header list, and return its name or None if not a header; used to track procedure division structure.

## `_UnitReader._read_value()`

Record literals from VALUE clauses of data items by finding string nodes within value_item parents; literals are stored in `item_literal_dict` keyed by upper-case item name for later use in CALL resolution.

## `_UnitReader._read_move()`

Record literals from MOVE statements in procedure division units by extracting string children and their target item names, adding them to `item_literal_dict` to support dynamic CALL resolution.

## `_UnitReader._add_import()`

Build import statements from `CobolText.copy_list` and `CobolText.call_list`, expanding literal and non-literal CALL names using `item_literal_dict`, adding call references, and sorting by line; used to finalize the import list.

## `_UnitReader._last_code_line()`

Return the last source line with code at or before a given line using binary search on `cobol_text.code_line_list`; used to find the end of a program or header when no explicit end marker exists.

## `_UnitReader._program_definition_list()`

Build definitions for each program by finding its end line via END PROGRAM markers or the next program's start line, defaulting to the last code line of the file; returns programs in source order.

## `_UnitReader._header_definition_list()`

Build definitions for each section and paragraph by computing end lines based on the next header, program boundary, or last code line; paragraphs end before the next paragraph or section, sections end before the next section; returns headers in source order.

## `_UnitReader._item_definition_list()`

Build definitions for each named data item following a four-step process: compute subordinate extent (items belonging to each item), identify items receiving subordinates from COPY statements, compute final line ranges including subordinates, and build `CobolDefinition` objects; returns items in item_list order.

## `_UnitReader._subordinate_extent_list()`

Return the last line and group-item flag for each data item by iterating items in order, matching each to open items with lower rank, updating their end lines and group status; implements the data item hierarchy model where rank determines membership.

## `_UnitReader._copy_end_line()`

Return the last line of the first COPY statement after a data item that belongs to it, searching within the item's extent; used to extend group item definitions when subordinates come from COPY statements.

## `_UnitReader._extent_end_position()`

Return the token position of the next statement that does not belong to a unit (section/paragraph header, next non-item unit, or next item with equal or lower rank); used to bound searches for subordinate items and COPY statements belonging to a parent item.

## `_UnitReader._file_definition_list()`

Build definitions for each file description by computing the end line from subordinate items and COPY statements within the file's extent; returns file definitions in file_list order.

## `has_statement()`

Return True if a procedure division unit contains a parsed statement (a node ending with "_statement" suffix on a source line), False if the parse tree has errors or no statement is found; used to distinguish executable copybooks from non-executable ones.

## `read_cobol_source()`

Parse a COBOL source file text and return a `CobolSource` containing all definitions, references, and imports; splits the source into units via `split_cobol_source()` and processes them with `_UnitReader.read()`; the primary entry point for COBOL file analysis.

# Summary

# Summary: codetwine/extractors/cobol_source.py

**Responsibility:** Parse COBOL source files into structured definitions (programs, sections, paragraphs, data items, files, index names), references to those names, and import statements (COPY and CALL), enabling downstream indexing and dependency analysis.

**Main Public Definitions:** `read_cobol_source()` parses a file and returns a `CobolSource` object; `has_statement()` detects executable code; `CobolSource` stores definitions, references, imports, and line lists with methods to query usages and retrieve definition text; `CobolDefinition`, `CobolReference`, and `CobolImport` represent individual definitions, references, and imports.

**Key Terms:** Data item hierarchy by level number, tree-sitter parsing with fallback to word-level extraction on errors, qualification chains for qualified references, literal extraction from VALUE clauses and MOVE statements for dynamic CALL resolution, COPY statement text replacement, program/section/paragraph boundaries, file descriptions, INDEXED BY index names.
