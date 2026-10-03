# Design Document: codetwine/extractors/definition_source.py

# Design Specification

**Overview**

Extract and cache the definitions and source text of code files to support name resolution and definition lookup across a multi-language codebase.

This file is used to:
- Call `file_definition()` or `source_definition()` to retrieve a definition by name and optional start line from a source file, receiving the DefinitionInfo and optionally the source text.
- Call `extract_callee_source()` or `extract_definition_source()` to retrieve the source code text of a definition by name, for languages with syntax tree support (Python, Java, C/C++, JavaScript, TypeScript, Rust, C#, SQL) and special handling for COBOL, BMS, and R files.
- Call `file_definition_list()` to get all definitions of a file in line order without the syntax tree, for efficient bulk access to definition metadata.
- Call `file_content()` to retrieve the UTF-8 text of a file for extracting definition source ranges by byte offset.
- Call `clear_definition_source_cache()` to reset cached definitions and file content when the analysis scope changes.

The file depends on `codetwine/parsers/ts_parser.py` to parse files and access the parse cache, and on `codetwine/extractors/definitions.py` to extract definitions from ASTs; it also depends on `codetwine/config/settings.py` to map file extensions to language-specific definition dictionaries and detect file languages, and on `codetwine/extractors/r_source.py` and `codetwine/extractors/usages.py` for R-specific and name-splitting utilities. It is used by `codetwine/file_analyzer.py` to retrieve definitions and source text for report generation, by `codetwine/import_binding.py` and `codetwine/import_reference.py` to resolve imported and local names to their definitions, by `codetwine/import_to_path.py` to list exported names, and by `codetwine/extractors/usage_analysis.py` and `codetwine/extractors/dependency_graph.py` for definition resolution and cache management.

Definitions are cached in two structures: `_definition_cache` holds definitions with file content as an LRU cache capped by `PARSE_CACHE_MAX_FILES`, used when both metadata and source text are needed; `_definition_list_cache` holds definitions alone, used for metadata-only access and not capped. Files without syntax tree support (COBOL, BMS, R) return `None` from `file_definition()` and `source_definition()` but are handled by language-specific extraction functions in `extract_callee_source()`. The byte offsets in DefinitionInfo are used to extract source text by slicing the file content; for COBOL, BMS, and R files these are `None` and source text is obtained via language-specific methods.

**Definitions**

## `_definition_cache`

Module-level ordered dictionary caching definitions and file content together by absolute file path, with entries ordered from least to most recently used and capped at `PARSE_CACHE_MAX_FILES` entries. When an entry is accessed via `_file_definition()`, it is moved to the end to mark it as most recently used; oldest entries are dropped when the cache exceeds the size limit.

## `_definition_list_cache`

Module-level dictionary caching definitions alone by absolute file path, separate from and not capped by `_definition_cache`. Used by `file_definition_list()` to cache metadata-only access without holding file content in memory.

## `clear_definition_source_cache()`

Forget all cached definitions and file content from both `_definition_cache` and `_definition_list_cache`. Called when the analysis scope or project configuration changes to ensure stale definitions are not reused.

## `file_definition_list()`

Return the definitions of a file extracted from its syntax tree, using `_definition_list_cache` to avoid re-parsing and re-extracting per file. The definitions are kept without the syntax tree until `clear_definition_source_cache()` is called, reducing memory usage for metadata-only access.

## `file_content()`

Return the UTF-8 text of a file without parsing, preferring the cached copy from `parse_cache` if available, otherwise reading it via `read_utf8_content()`. Used to obtain file content for extracting definition source text by byte offset when the file is not in the parse cache.

## `_file_definition()`

Return definitions and UTF-8 content of a file together from `_definition_cache`, managing the LRU order and respecting the `PARSE_CACHE_MAX_FILES` limit. Definitions are obtained via `file_definition_list()` and content via `file_content()`, then cached as a tuple for cohesive access when both are needed.

## `_is_inside()`

Test whether a definition is written inside the line range of one of the owners in a list, used to determine if a definition is a member of a containing definition (class, impl block, trait). Returns true only if the definition is not the owner itself and its line range is fully contained.

## `_find_by_path()`

Walk through parts of a dotted or scope-qualified name by following the definitions of a file, starting with top-level definitions for the first part and then looking inside definitions named by prior parts for nested members. Returns the definition the last part reached names, preferring main definitions over attached types like impl blocks; returns `None` if any part names nothing.

## `find_definition()`

Return the definition a fully-qualified name (with "." or "::") refers to in a file, first trying to match the entire name as a single definition, then trying each suffix of the name via `_find_by_path()` to skip module or namespace prefixes that are not definitions. Used to resolve qualified identifiers like "Settings::new" or "Config.load" to their definitions.

## `file_definition()`

Return the definition a name refers to in a file by calling `source_definition()` and extracting the definition metadata, ignoring the source text. Returns `None` for files without syntax tree support (COBOL, BMS, R) or when the name does not resolve.

## `source_definition()`

Return the definition a name refers to in a file along with its source text, using start_line to disambiguate when the same name appears multiple times. When start_line is provided, parts of the name are tried from last to first, matching the first definition with that name and start line; otherwise `find_definition()` is used. Returns `None` for files without syntax tree support or when the name does not resolve.

## `extract_callee_source()`

Retrieve and return the source code text of a definition by name from a dependency target file, handling COBOL, BMS, and R files with language-specific methods and other languages via `source_definition()`. Used to extract definition text for callees in dependency analysis.

## `extract_definition_source()`

Return the source text of a definition a name refers to at a start line via `source_definition()`, falling back to `extract_callee_source()` if no matching line is found. Used to retrieve definition source for definitions recorded with their start line.

# Summary

# Summary: definition_source.py

This module extracts and caches definitions and source text from code files to support name resolution across multi-language codebases. It maintains two LRU caches: `_definition_cache` for definitions with file content, and `_definition_list_cache` for metadata-only access.

Main public functions: `file_definition()` and `source_definition()` retrieve definitions by name; `extract_callee_source()` and `extract_definition_source()` get source text; `file_definition_list()` lists all file definitions; `file_content()` reads file text; `find_definition()` resolves qualified names; `clear_definition_source_cache()` resets caches.

It handles syntax-tree-supported languages (Python, Java, C/C++, JavaScript, TypeScript, Rust, C#, SQL) via AST parsing, with special support for COBOL, BMS, and R files using language-specific extraction methods. Byte offsets in DefinitionInfo extract source text by slicing file content.
