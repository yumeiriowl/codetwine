# Design Document: codetwine/cobol_file_index.py

# Design Specification

**Overview**

Build and maintain indexes of COBOL files and BMS sources in a project, resolving COPY and CALL statements to their target files, and tracing data name and procedure name references to their definitions across files and copybooks.

- Call `resolve_cobol_module_path()` to convert a COPY or CALL statement name into the relative path of its target file, applying path precedence rules that prefer files matching the full path, files in named libraries, COBOL extensions, and path ordering.
- Call `cobol_reference_target_list()` to resolve all references (data names, procedure names, program calls) in a COBOL file to their definitions, with support for qualified names (OF/IN chains), COPY statement REPLACING operands, and automatic copybook inclusion.
- Call `register_copy_target()` to discover and index COBOL copybooks among files without an assigned language by following COPY statements from known COBOL files, recording them with the copybook extension and validating their content as COBOL text.
- Call `replace_name()` to apply COPY ... REPLACING operands to data names, handling prefix, suffix, and substring replacements with colon-delimited markers.

This file provides the core resolution mechanism that import_to_path.py, usage_analysis.py, and reference_target.py depend on to bind COBOL references to their definitions. It relies on parse_file() from ts_parser.py and split_cobol_source() from cobol_format.py to read and tokenize COBOL sources, on language_ext() and has_language() from settings.py to identify file types, and on project_cache_value() from project_cache.py to cache indexes and resolved references by project file set. Two global caches (file_index_cache and reference_target_cache) are cleared by dependency_graph.py and pipeline.py to release memory between analyses.

File indexes are built once per project file set and cached indefinitely; reference targets are cached per file and cleared when the project file set changes. The copybook discovery process (register_copy_target()) follows COPY statements recursively, checking each unindexed file against the text content criteria (_is_cobol_text) or whole-file-name matching (_is_named_whole) before recording it as a copybook; detected copybooks update project settings via set_copy_target_ext() to persist them across subsequent indexing.

**Definitions**

## `file_index_cache`

Module-level dictionary caching CobolFileIndex objects by project directory, keyed with the project file set they were built for; cleared by dependency_graph.py and pipeline.py between analyses to release memory.

## `reference_target_cache`

Module-level dictionary caching resolved reference lists by absolute file path, keyed with the project file set they were resolved with; cleared by dependency_graph.py and pipeline.py between analyses to release memory.

## `CobolFileIndex`

Dataclass holding three dictionaries that index COBOL files and BMS sources by file name (with and without extension, and by copy names or mapset names), by program or ENTRY name, and a set of files that define programs; created by _build_file_index() and retrieved from cache by _get_file_index() for use in path resolution functions.

## `_cobol_source`

Parse a COBOL or BMS file at the given relative path using parse_file(), returning its CobolSource or None if reading or parsing fails; logs the exception and is used internally by index-building and reference-resolution functions.

## `_add_file_name`

Add a file to the file_name_dict of a CobolFileIndex under its base file name (uppercase, with and without extension) and under any additional names in the provided list, maintaining path order; called by _add_source() and register_copy_target().

## `_is_cobol_file`

Return True if a project file is identified as COBOL by language_ext(), used to decide which files' COPY statements should be followed during copybook registration.

## `_add_source`

Index a parsed COBOL or BMS source by adding its file name and copy_name_list entries to file_name_dict, and by adding each definition matching CALL_TARGET_TYPE_TUPLE to program_dict; called during index building and copybook registration.

## `_sort_file_index`

Sort the file lists within a CobolFileIndex's file_name_dict and program_dict into path order, called after adding files to ensure consistent results when multiple files match a name.

## `_source_file_list`

Return a sorted list of COBOL and BMS files from a project file set, filtering by language_ext() membership in COBOL_EXT_SET and BMS_EXT_SET; used by _build_file_index() and register_copy_target() to identify which files to parse.

## `_build_file_index`

Parse all COBOL and BMS sources in a project file set, build their CobolFileIndex by calling _add_source() for each, sort the index, and return it; called by _get_file_index() on cache miss.

## `_get_file_index`

Retrieve or build the CobolFileIndex for a project, checking file_index_cache first and building via _build_file_index() if the cached entry matches the current project file set; called by resolve_cobol_module_path() and register_copy_target().

## `_copybook_path`

Resolve a COPY statement name and library to a project file path by searching the file_name_dict of a CobolFileIndex, applying a multi-criterion sort order that prioritizes full path matches, files in the named library directory, COBOL extensions, BMS sources, current directory proximity, copybook extension, files without programs, and path order; skip_file_set excludes already-tried candidates during retry loops.

## `_program_path`

Resolve a CALL statement name to a project file path by searching program_dict for a matching program or ENTRY name, falling back to file_name_dict if needed, and selecting by proximity to the current file and path order; used by resolve_cobol_module_path() and _program_target_dict().

## `resolve_cobol_module_path`

Convert a COPY or CALL statement module string (as produced by ImportInfo.name and .library) to a project file path using the appropriate resolution function (_copybook_path or _program_path), returning None if no match exists or if the match is the current file itself; called by import_to_path.py for import resolution and by register_copy_target() and _add_copybook_candidate() for copybook following.

## `replace_name`

Apply one or more COPY ... REPLACING operands to a data name, implementing LEADING prefix replacement, TRAILING suffix replacement, exact string matching, and colon-delimited substring replacement; returns the replaced name or the original if no operand matches, used by _Candidate.local_name() and _Candidate.find_definition_list().

## `_copy_name_list`

Extract the (copybook name, library name) tuples from a parsed CobolSource's import_list by filtering for COPY_KIND entries; used by register_copy_target() to populate the copy_name_dict queue.

## `_is_named_whole`

Return True if a copybook name (with backslash-to-slash normalization) matches the base file name of a file in uppercase comparison; used by register_copy_target() to decide whether an unindexed file should be registered as a copybook without content inspection.

## `_is_cobol_text`

Return True if the text of a file contains COBOL structure (data items, file descriptions, or procedure division statements), checked by reading and splitting the source via split_cobol_source() and inspecting for ITEM_UNIT, FILE_UNIT, or PROCEDURE_UNIT with valid grammar; used by register_copy_target() to validate unindexed files before recording them as copybooks.

## `register_copy_target`

Discover unindexed files that COPY statements name, validate them as COBOL by name or content (_is_cobol_text or _is_named_whole), record them with the copybook extension via set_copy_target_ext(), follow their COPY statements recursively, and cache the resulting file index; called by dependency_graph.py during project initialization to enable copybook resolution.

## `CobolReferenceTarget`

Dataclass holding the name, line, file path, and optional definition of one resolved reference, returned by cobol_reference_target_list() and used by usage_analysis.py to build usage ranges and definition information.

## `_Candidate`

Dataclass representing a file (the referring file or a copybook it includes) whose definitions can be referenced, storing its CobolSource, the line of the COPY statement bringing it in, REPLACING operands to apply to names, and a lazily-built definition_dict for name lookup; created by _candidate_list() and _add_copybook_candidate().

## `_Candidate.local_name`

Return the name of a definition as the referring file writes it by applying the candidate's REPLACING operands via replace_name(); used by _Candidate.find_definition_list() and _name_target().

## `_Candidate.find_definition_list`

Return the definitions of a name (excluding programs and ENTRY names) whose local_name matches the given name, building the definition_dict on first use; used by _is_under(), _target_under_qualifier(), and _name_target().

## `_add_copybook_candidate`

Traverse the COPY statements of a file, resolving each to a project file via resolve_cobol_module_path(), creating _Candidate objects for each copybook with accumulated REPLACING operands, and recursively adding their copybooks to the candidate_list in depth-first order; used by _candidate_list() to build the complete set of files that can supply definitions.

## `_candidate_list`

Return the referring file plus all copybooks its COPY statements bring in (via _add_copybook_candidate()), in line order with copybooks following the COPY that includes them; used by cobol_reference_target_list() to resolve data and procedure names.

## `_is_under`

Return True if a qualifier name refers to a definition that structurally contains another definition (by nesting range), or for a copybook, if a definition in the referring file contains the COPY statement line; used by _target_under_qualifier() to validate qualified name references.

## `_target_under_qualifier`

Find the first definition matching a qualified name (with OF/IN chains) in the candidate_list by checking that each qualifier names a containing definition via _is_under(); returns (candidate, definition) or None if no match, used by _name_target() to resolve qualified references.

## `_program_target_dict`

Return a dictionary mapping (program name uppercase, statement line) to (program name as written, target file path) for each CALL statement in a CobolSource that resolves to another file; used by cobol_reference_target_list() to avoid re-resolving CALL targets.

## `_program_definition`

Return the first program or ENTRY definition of a CobolSource matching an uppercase name, or None if the source is None or no definition matches; used by _call_target() to find the definition of a called program.

## `_first_program_definition`

Return the first program or ENTRY definition of a CobolSource, or None if the source is None or contains no programs; used by _call_target() as a fallback when the specific program name is not defined in the called file.

## `_call_target`

Resolve a CALL reference to a CobolReferenceTarget by looking up the target file in program_target_dict, retrieving its CobolSource, and finding the matching or first program/ENTRY definition; returns None if the call is within the same file and no definition exists there, used by cobol_reference_target_list().

## `_name_target`

Resolve a data or procedure name reference to a CobolReferenceTarget by searching for the name under qualifiers via _target_under_qualifier() if qualified, or else finding the first definition of the name in candidate_list order; returns None if no definition exists, used by cobol_reference_target_list().

## `cobol_reference_target_list`

Resolve all references in a COBOL file to their definitions by building a candidate_list of the file and its copybooks, collecting CALL targets via _program_target_dict(), and resolving each reference via _call_target() or _name_target(); return sorted unique targets in line order, with results cached per file in reference_target_cache by project file set, called by reference_target.py to supply usage analysis.

# Summary

# Summary: codetwine/cobol_file_index.py

**Single Responsibility**

Build and maintain indexes of COBOL files and BMS sources in a project, resolving COPY and CALL statements to target files, and tracing data name and procedure name references to their definitions across files and copybooks.

**Main Public Definitions**

- `resolve_cobol_module_path()` – converts COPY or CALL statement names to file paths
- `cobol_reference_target_list()` – resolves all references in a COBOL file to their definitions
- `register_copy_target()` – discovers and indexes copybooks by following COPY statements
- `replace_name()` – applies COPY REPLACING operands to data names
- `CobolFileIndex` – indexes files by name, program name, and copy names
- `CobolReferenceTarget` – holds a resolved reference with its definition location

**Key Terms**

COPY statements, copybook resolution, CALL statements, qualified names (OF/IN chains), REPLACING operands, copybook discovery, definition lookup, data names, procedure names, program definitions, path precedence, file indexing, caching.
