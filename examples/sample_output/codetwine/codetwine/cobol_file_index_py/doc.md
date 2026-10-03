# Design Document: codetwine/cobol_file_index.py

# Design Specification

**Overview**

Index COBOL files and BMS sources by file name and program name, and resolve COPY and CALL statements to their target files and definitions.

This file is used when:
- `resolve_cobol_module_path()` is called to convert a COPY or CALL statement module string to a project file path, returning the relative path of the target file or None if unresolved.
- `cobol_reference_target_list()` is called to resolve all references in a COBOL file to their definitions, returning a list of CobolReferenceTarget objects with definition locations and names.
- `register_copy_target()` is called to find files without a language that COPY statements name, recording them as COBOL copybooks with extension "cpy" and updating the project's language settings.

The file builds on COBOL parsing from `cobol_source.py` and format utilities from `cobol_format.py` to create searchable indexes of program names and file names, then uses those indexes to resolve module references and qualify data names. It provides the primary interface between import resolution (via `imports.py`) and reference targeting for COBOL files. Downstream files `dependency_graph.py`, `usage_analysis.py`, `reference_target.py`, and `import_to_path.py` use its public functions to look up COBOL module paths and resolve references to definitions. The file maintains two module-level caches: `file_index_cache` holds per-project file indexes keyed by project directory, and `reference_target_cache` holds resolved reference lists keyed by absolute file path; both caches are invalidated when the project file set changes or are manually cleared.

**Definitions**

## `file_index_cache`

Module-level cache mapping project directory to a tuple of (project file set the index was built from, CobolFileIndex), used by `_get_file_index()` to avoid rebuilding the index on repeated calls with the same project file set.

## `reference_target_cache`

Module-level cache mapping absolute file path to a tuple of (project file set the targets were resolved with, list of CobolReferenceTarget), used by `cobol_reference_target_list()` to avoid re-resolving references when the project file set has not changed.

## `CobolFileIndex`

Dataclass holding three dictionaries that index COBOL files and BMS sources: `file_name_dict` maps upper-case file names (with and without extension, plus copy_name_list entries) to lists of file paths in path order; `program_dict` maps upper-case program and ENTRY names to lists of files that define them in path order; `program_file_set` tracks which files define any program or ENTRY.

## `_cobol_source()`

Parse a COBOL file or BMS source at the given relative path and project directory, returning its CobolSource object; logs and returns None if reading or parsing raises an exception.

## `_add_file_name()`

Index a file in `file_name_dict` by its basename (upper-case, with and without extension) and by each name in the provided name_list, appending the file path to the list for each name if not already present.

## `_is_cobol_file()`

Return whether a project file is analyzed as COBOL by checking if its language extension (from `language_ext()`) is in COBOL_EXT_SET.

## `_add_source()`

Index a COBOL file or BMS source by calling `_add_file_name()` with its copy_name_list, then indexing each of its definitions with type in CALL_TARGET_TYPE_TUPLE in `program_dict` and `program_file_set`.

## `_sort_file_index()`

Sort all file lists in a CobolFileIndex (both `file_name_dict` and `program_dict` values) by path order.

## `_source_file_list()`

Return a sorted list of relative paths for COBOL files and BMS sources in a project file set by filtering based on language extension.

## `_build_file_index()`

Parse all COBOL files and BMS sources in a project file set, build a CobolFileIndex from those that parse successfully, and sort it; files that fail to parse are logged and excluded.

## `_get_file_index()`

Return a CobolFileIndex for a project, retrieving it from `file_index_cache` if it was built for the same project file set, or building and caching it via `_build_file_index()` if not.

## `_copybook_path()`

Resolve a COPY statement name to a project file by finding candidates matching the copybook name in `file_index_dict`, filtering by skip_file_set, and selecting the best match according to a priority order: exact path match, library directory match, COBOL extension, BMS source, current directory location, copybook extension, files defining no program, path order.

## `_program_path()`

Resolve a CALL statement name to a project file by finding candidates in `program_dict` for files defining that program or ENTRY name, or falling back to files in `file_name_dict` that define programs and match the name without extension; among candidates, prefer the current file, then files in the current directory, then path order.

## `resolve_cobol_module_path()`

Convert a COBOL COPY or CALL module string (via `cobol_module_part()`) to a project file path by consulting a cached CobolFileIndex, calling `_copybook_path()` for COPY or `_program_path()` for CALL, and returning None if the resolved file is the current file itself.

## `replace_name()`

Apply COPY ... REPLACING operands to a name by matching leading, trailing, or full-text replacements with case-insensitivity and support for delimiter-based partial replacement using ":" syntax; return the replaced name or the original if no operand matches.

## `_copy_name_list()`

Extract and return a list of (copybook name, library name) tuples from a CobolSource's import_list, selecting only those imports with kind equal to COPY_KIND.

## `_is_named_whole()`

Return whether a copybook name matches the whole file basename by comparing the last path component (upper-case, with backslashes converted to forward slashes) to the file's basename (upper-case).

## `_is_cobol_text()`

Return whether a file's text contains COBOL content by reading the file, splitting it via `split_cobol_source()`, and checking for data items, file descriptions, or procedure division statements via `has_statement()`; logs and returns False if reading or parsing fails.

## `register_copy_target()`

Find files without a language that COPY statements name and record them as COBOL copybooks by: parsing COBOL files and BMS sources once and indexing them, indexing files without a language, following COPY statements to find candidates, recording files as copybook targets when they match the whole copybook name or contain COBOL text, and updating the project's language settings via `set_copy_target_ext()`; return a dict mapping file paths to extension "cpy".

## `CobolReferenceTarget`

Dataclass representing one resolved reference in a COBOL file, holding the definition name (as written in the referring file after REPLACING), the reference line number, the file path containing the definition, and the CobolDefinition object itself (None if the program file was not read).

## `_Candidate`

Dataclass representing a file (the referring file or a copybook it includes) whose definitions may be referred to, holding the file path, its CobolSource, the line of the COPY statement (0 for the referring file itself), REPLACING operands from the COPY statement, and a lazily-built dictionary mapping upper-case local names to CobolDefinition lists for non-program definitions.

## `_Candidate.local_name()`

Return the name of a definition as the referring file writes it by applying the candidate's REPLACING operands via `replace_name()`.

## `_Candidate.find_definition_list()`

Return a list of definitions (excluding programs and ENTRY names) whose local name matches the given upper-case name, building the definition_dict on first use.

## `_add_copybook_candidate()`

Recursively add copybooks brought in by COPY statements of a holder file to a candidate_list in line order, resolving each COPY via `resolve_cobol_module_path()`, skipping circular includes via open_file_set, and combining REPLACING operands from nested COPY statements.

## `_candidate_list()`

Return a list of _Candidate objects representing the referring file plus all copybooks its COPY statements bring in (and their copybooks) in line order, by starting with the referring file and recursively calling `_add_copybook_candidate()`.

## `_is_under()`

Return whether a qualifier names a definition that holds another definition's line range, or (for a copybook) whether a definition of the referring file holds the COPY statement's line; used to evaluate qualified names like "FLD OF GROUP".

## `_target_under_qualifier()`

Resolve a qualified name (with OF / IN qualifiers) to a definition by finding the first definition whose line range is held under all qualifier definitions, returning a tuple of (candidate, definition) or None.

## `_program_target_dict()`

Return a dict mapping (upper-case program name, statement line) to (program name as written, resolved file path) for each CALL statement of a COBOL file that resolves to another file.

## `_program_definition()`

Return the first program or ENTRY definition of a CobolSource with a matching upper-case name, or None if the source is None or no such definition exists.

## `_first_program_definition()`

Return the first program or ENTRY definition of a CobolSource, or None if the source is None or no such definition exists.

## `_call_target()`

Resolve a CALL statement to its target by looking up the program file in `program_target_dict`, finding the matching program or ENTRY definition in that file (or the first program if no match), and returning a CobolReferenceTarget; if no program file is found, look for the program in the referring file itself.

## `_name_target()`

Resolve a data or procedure name to its target by checking if it is qualified and matching qualifiers via `_target_under_qualifier()`, or otherwise finding the first definition of that name in the referring file or its copybooks in COPY statement order, returning a CobolReferenceTarget or None.

## `cobol_reference_target_list()`

Resolve all references in a COBOL file to their definitions by: retrieving cached results if available for the same project file set, building a candidate list via `_candidate_list()` and a program target dict via `_program_target_dict()`, resolving each reference via `_call_target()` or `_name_target()`, deduplicating by (name, line, file, definition_line), and caching the sorted result; return a list of CobolReferenceTarget objects in line order.

# Summary

# codetwine/cobol_file_index.py Summary

**Single Responsibility:**
Index COBOL files and BMS sources by file and program name, then resolve COPY and CALL statements to target files and definitions.

**Main Public Definitions:**
- `resolve_cobol_module_path()` – converts COPY/CALL module strings to project file paths
- `cobol_reference_target_list()` – resolves all references in a COBOL file to their definitions
- `register_copy_target()` – identifies and records unnamed COBOL copybooks
- `CobolFileIndex` – indexes files by name and programs by definition
- `CobolReferenceTarget` – represents one resolved reference with location and definition

**Key Terms:**
Module references, copybooks, program definitions, data names, qualified names, REPLACING operands, file indexing, reference resolution, definition lookup, circular includes, COPY/CALL statements.
