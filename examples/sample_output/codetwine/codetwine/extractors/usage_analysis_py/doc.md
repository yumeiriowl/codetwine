# Design Document: codetwine/extractors/usage_analysis.py

# Design Specification

**Overview**

Analyze usage patterns of definitions across source files by grouping resolved references into structured summaries of where names are used, including context around those usages and information about the definitions they reference.

A developer or tool would use this file to:
- Call `build_callee_usages()` to collect references from a file that point to definitions in other files, grouped by target file and definition with surrounding source context.
- Call `build_same_file_usages()` to collect references within a file that point to its own definitions, grouped by name and excluding self-references within definition boundaries.
- Call `build_caller_usages()` to find all usages of a specific file's definitions across dependent files, including the surrounding code context for each usage.

This file depends on language-specific reference resolution (CobolReferenceTarget, CsharpReferenceTarget, RReferenceTarget, ImportReferenceTarget from codetwine/cobol_file_index.py, codetwine/csharp_namespace_index.py, codetwine/r_name_index.py, and codetwine/import_reference.py) to obtain resolved references, and on definition extraction (DefinitionInfo, extract_definitions) and syntax tree parsing (parse_file) to determine definition locations. Files in codetwine/file_analyzer.py use these functions to build complete usage information for each file in a project.

The module implements memoization of definition lookups by reference target to avoid redundant file I/O when multiple references resolve to the same definition, and context extraction reads source files on demand with bounds checking on line ranges. References written inside their own definition boundaries (recursive calls, self-references) are filtered from same-file usage groups to exclude definitions naming themselves.

**Definitions**

## `_TargetDefinition`

Dataclass holding the context, name, and start line of a definition that a reference resolves to; used throughout the module to carry definition metadata in a unified structure that handles None values when a reference leads to no definition of its file.

## `_NO_DEFINITION`

Constant sentinel instance of `_TargetDefinition` with all fields set to None, representing a reference that resolves to no definition or to a definition that cannot be found.

## `_memo_definition_function`

Returns a memoizing wrapper around a definition lookup function that caches results by reference target identity (all fields except line number), avoiding redundant lookups when multiple references on different lines target the same definition.

## `_group_other_file_target_list`

Groups resolved references pointing to definitions in other files by (target file, name, definition name, definition start line), producing one output dict per distinct (file, name, definition) combination with sorted line numbers and the definition's context and name; memoizes definition lookups to avoid redundant file I/O.

## `_definition_range_dict`

Builds a mapping from definition names to lists of (start_line, end_line) tuples for all definitions with that name, used to efficiently check whether a reference falls within its own definition's line range.

## `_cobol_own_range_function`

Returns a function that extracts the line range of the definition a COBOL reference resolves to, handling the None case when a COBOL reference has no definition (program file not read).

## `_r_own_range_function`

Returns a function that extracts line ranges of top-level R definitions matching a reference's root name, filtering out class members and setMethod definitions that are not top-level.

## `_name_own_range_function`

Returns a function that extracts line ranges of the definition a C# or import reference names, handling multi-part names (qualified identifiers like Cfg.Max) and resolving them to member definitions or root name matches in the definition list.

## `_OWN_RANGE_FUNCTION_DICT`

Mapping from reference kind ("cobol", "r", "csharp", "import") to functions that return line ranges of definitions named by same-file references, enabling language-specific filtering of self-references.

## `TREE_RANGE_KIND_SET`

Set of reference kinds ("r") whose own_range_function requires parsing the syntax tree root node; used to determine whether parse_file must be called to extract definition ranges.

## `_group_same_file_target_list`

Groups resolved references within a file that point to the file's own definitions by (name, definition name, definition start line), excluding references written inside their own definition's line range and producing sorted line numbers per group; memoizes definition lookups.

## `_cobol_definition_function`

Returns a function that looks up a COBOL reference's definition by parsing its target file and extracting the definition's source text via CobolSource.definition_text(), returning _NO_DEFINITION if the reference has no definition.

## `_r_definition_function`

Returns a function that looks up an R reference's definition by reading the target file once and caching its line list, extracting the source text between the definition's start_line and end_line, using the reference's definition_name as the lookup key.

## `_csharp_definition_function`

Returns a function that looks up a C# reference's definition by calling source_definition with the reference's definition_name and definition_line, returning the definition and its source text.

## `_import_definition_function`

Returns a function that looks up an import reference's definition by calling source_definition with the reference's definition_name, returning the definition and its source text.

## `_source_target_definition`

Calls source_definition to find a definition by name and line, wrapping the result in a _TargetDefinition with context text, name, and start line, or returning _NO_DEFINITION when source_definition returns None.

## `_TARGET_DEFINITION_FUNCTION_DICT`

Mapping from reference kind ("cobol", "r", "csharp", "import") to factory functions that create definition lookup functions for each language, enabling language-specific definition retrieval strategies.

## `_group_caller_usage_list`

Groups references of a caller file by (name, definition name, definition start line), producing a dict keyed by that tuple with sorted line numbers, without filtering any references or extracting context.

## `_attach_usage_context`

Adds a "usage_context" field to each group dict containing up to _MAX_CONTEXT_LOCATION lines of surrounding source code (_CONTEXT_RADIUS lines before and after each usage), joining multiple context blocks with "\\n...\\n"; reads the caller file once and handles read errors by adding no context.

## `build_callee_usages`

Builds usage information for references from a file to definitions in other files, grouped by target file, name, and definition, including the definition's source context and location; returns an empty list for files without a language.

## `build_same_file_usages`

Builds usage information for references within a file to the file's own definitions, grouped by name and definition, excluding references written inside their own definition boundaries; requires the file's definitions and (for reference kinds in TREE_RANGE_KIND_SET) its parsed AST root node to determine definition line ranges.

## `build_caller_usages`

Collects all usages of a file's definitions across dependent files by resolving each caller's references to this file, grouping by name and definition, and attaching surrounding source code context from the caller file; returns a list of usage groups with "lines", "name", "file", and "usage_context" fields.

# Summary

# Summary: usage_analysis.py

**Single Responsibility**
Analyzes usage patterns of code definitions across source files by grouping resolved references into structured summaries that show where names are used, the definitions they reference, and surrounding code context.

**Main Public Functions**
- `build_callee_usages()` – references from a file pointing to definitions in other files
- `build_same_file_usages()` – references within a file pointing to its own definitions
- `build_caller_usages()` – usages of a file's definitions across dependent files

**Key Concepts**
Groups references by target definition and file, extracts definition metadata (name, location, source context), filters self-references and recursive calls within definition boundaries, memoizes definition lookups to avoid redundant I/O, and handles language-specific reference resolution (COBOL, C#, R, imports) through pluggable definition lookup and line-range extraction functions.
