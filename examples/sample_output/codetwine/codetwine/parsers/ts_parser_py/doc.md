# Design Document: codetwine/parsers/ts_parser.py

# Design Specification

**Overview**

Parse source files with tree-sitter to generate abstract syntax trees, with special handling for COBOL, BMS, R Markdown, and C/C++ files, while caching results to avoid re-parsing.

- Call `parse_file()` to obtain the AST root node and UTF-8 byte content of a file; it returns a cached result if available, otherwise reads and parses the file according to its language.
- Call `read_utf8_content()` to read a file as UTF-8 bytes with lone carriage returns normalized to line feeds, used by callers needing raw file content without parsing.
- Access `parse_cache` to retrieve cached parse results keyed by absolute file path, or to clear the cache between analyses.
- Access `class_macro_cache` to retrieve the macro names found between class keywords and class names during C/C++ parsing, used to report those names as references.

This file is the central parsing hub of the project. It depends on `read_source()` and `lone_cr_to_lf()` from file_utils for file reading and normalization, `language_ext()` and extension sets from settings for determining file languages, `read_cobol_source()` and `CobolSource` from cobol_source for COBOL parsing, `read_bms_source()` for BMS source parsing, and `r_chunk_code()` from r_markdown for extracting R code chunks. Nearly every analysis module (alias_path, cobol_file_index, csharp_namespace_index, definition_source, dependency_graph, usage_analysis, file_analyzer, import_binding, import_reference, r_name_index, rust_module_tree) calls `parse_file()` to obtain syntax trees, and dependency_graph and pipeline clear the caches between analyses.

Parse results are cached at module level in an ordered dictionary capped at `PARSE_CACHE_MAX_FILES` entries; when the limit is exceeded, the least recently used entry is discarded. Accessing a cached entry moves it to the end (most recently used). COBOL and BMS files store a `CobolSource` object instead of a root node. R Markdown and Quarto files return a synthesized tree of R code chunks with non-chunk content blanked, preserving byte positions. C/C++ files have macro names between class keywords and class names replaced with spaces and are re-parsed iteratively until no more macros are found or the maximum pass count is reached; the macro names and line numbers are cached separately in `class_macro_cache`.

**Definitions**

## `_END_NAME`

A sentinel marker appended to code during whole-code validation; used by `_is_whole_code()` to detect whether a code fragment ends cleanly as a statement without open constructs.

## `_CLASS_MACRO_QUERY_TUPLE`

A tuple of tree-sitter query patterns identifying function definitions whose return type is a class, struct, or union keyword followed by a macro name and without a body; used to find macro names between class keywords and class names in C/C++ files.

## `_class_macro_query_cache`

A module-level cache mapping Language object IDs to compiled tree-sitter Query objects for `_CLASS_MACRO_QUERY_TUPLE` patterns; reduces query compilation overhead by caching compiled patterns.

## `_CLASS_MACRO_PASS_MAX`

The maximum number of iterations to replace class macro names and re-parse a C/C++ file; stops iteration once no more macros are found or this limit is reached, preventing infinite loops.

## `parse_cache`

A module-level ordered dictionary caching parse results keyed by absolute file path, with entries containing a root node or CobolSource and the file's UTF-8 bytes; ordered from least to most recently used for LRU eviction when `PARSE_CACHE_MAX_FILES` is exceeded.

## `class_macro_cache`

A module-level dictionary mapping absolute file paths to lists of (macro name, line number) tuples found during C/C++ parsing; used to report macro names as references in import_reference.

## `read_utf8_content`

Read a file, normalize lone carriage returns to line feeds with `lone_cr_to_lf()`, and return its text encoded as UTF-8 bytes; logs warnings when encoding detection fails and debug messages when non-UTF-8 encodings are used.

## `_is_whole_code`

Determine whether a code fragment (typically an R chunk) ends where it stops without open constructs by appending a sentinel identifier, parsing with tree-sitter, and checking whether the sentinel appears as a top-level statement.

## `_class_macro_query`

Return a compiled tree-sitter Query for the `_CLASS_MACRO_QUERY_TUPLE` patterns applicable to a language, caching the result by Language ID; patterns that name node types the language grammar lacks are omitted.

## `_class_macro_range_list`

Return the byte ranges of macro names appearing between class keywords and class names in a C/C++ AST by executing the `_class_macro_query()` and extracting captured nodes, used to identify which bytes to blank during re-parsing.

## `_parse_c_family`

Parse a C/C++ file iteratively, replacing class macro names between class keywords and class names with spaces on each pass, until no more macros are found or the maximum pass count is reached; return the final AST root node and a sorted list of (macro name, line number) tuples.

## `parse_file`

Read a file with `read_utf8_content()`, parse it according to its language extension, and return a cached (root node or CobolSource, UTF-8 bytes) tuple; handles special parsing for COBOL (via `read_cobol_source()`), BMS sources (via `read_bms_source()`), R Markdown/Quarto (via `r_chunk_code()` with whole-code filtering), and C/C++ (via `_parse_c_family()` with macro blanking), while managing an LRU parse cache bounded by `PARSE_CACHE_MAX_FILES`.

# Summary

# Summary: codetwine/parsers/ts_parser.py

**Single Responsibility:** Central parsing hub that reads source files, generates abstract syntax trees using tree-sitter, and caches results to avoid re-parsing. Handles special parsing logic for COBOL, BMS, R Markdown, and C/C++ files.

**Main Public Definitions:**
- `parse_file()` — obtain cached AST root node and UTF-8 content
- `read_utf8_content()` — read file as UTF-8 bytes with normalized line endings
- `parse_cache` — module-level LRU cache of parse results
- `class_macro_cache` — cache of macro names found in C/C++ files

**Key Handling:**
- COBOL and BMS files return CobolSource objects instead of ASTs
- R Markdown and Quarto files return synthesized trees of R code chunks
- C/C++ files undergo iterative macro replacement and re-parsing
- LRU cache bounded by configurable maximum entries; least recently used entries evicted when limit exceeded
