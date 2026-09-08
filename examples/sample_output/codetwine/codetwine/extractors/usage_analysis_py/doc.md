# Design Document: codetwine/extractors/usage_analysis.py

# Design Specification

**Overview**

Produces cross-file usage reports by linking imported/used symbol names in a file to their definition source code and by finding where a file's own definitions are used elsewhere in the project.

- Use `build_usage_info_list` when analyzing a single file's AST to generate the "callee_usages" data: given a map of imported symbol names to their defining files, it returns merged usage records (name, source file, line numbers, definition source snippet) for the JSON knowledge output.
- Use `build_caller_usages` when generating the "caller_usages" data for a file: given the list of files that depend on it, it returns per-caller usage records (name, file, line numbers, surrounding code context) showing where that file's definitions are consumed.
- Use `_collect_names_from_target` (internal) when determining, per caller file and per language convention, which imported/visible names actually originate from a specific target file (handles Python/JS/TS named imports, Java/Kotlin qualified and wildcard imports, C/C++ `#include`, and same-package visibility).
- Use `_load_target_definitions` (internal) whenever a full list of a file's top-level definition names is needed (wildcard imports, C/C++ full-file inclusion, same-package fallback).

This file depends on `ts_parser.parse_file` to obtain ASTs of caller/target files, `extractors.imports.extract_imports` and `import_to_path.get_import_params`/`resolve_module_to_project_path` to parse and resolve import statements, `extractors.usages.extract_usages`/`extract_typed_aliases` to locate symbol usages and typed-variable aliases, `extractors.definitions.extract_definitions` to enumerate definition names for wildcard/include/same-package cases, `extractors.dependency_graph.extract_callee_source` to fetch the actual definition source text, and `config.settings` (`USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `DEFINITION_DICTS`, `SAME_PACKAGE_VISIBLE`) for all per-language behavior configuration. It is used by `file_analyzer.py`, which calls `build_usage_info_list` to attach callee usage/context data and `build_caller_usages` to attach caller usage/context data when assembling per-file knowledge JSON.

Design notes: usages are grouped/deduplicated by (definition file, resolved name) with line numbers merged and sorted into a unique set to avoid redundant entries; typed-variable aliases (e.g., a variable declared with an imported type) are resolved back to the original type name before grouping and before definition-source lookup, and import aliases are similarly resolved via `alias_to_original`/`import_info.alias_map` before searching for source code; for caller usage context, only the first two usage locations per name are used to build a bounded-radius (3-line) code snippet, and file reads for context are best-effort (I/O and decode errors are silently ignored, resulting in no context rather than a failure); target definition names for C/C++ (and other whole-file-inclusion cases) are computed once and cached across the caller loop to avoid redundant parsing.

**Definitions**

## `build_usage_info_list`

Extracts and aggregates usage locations of project-imported names within a single file's AST, producing the data needed for the "callee_usages" output. It first builds typed-alias mappings (variable name to imported type name) via `extract_typed_aliases` and merges alias variables into `symbol_to_file_map` so they resolve to the same source file as their type; it then calls `extract_usages` to find raw usage occurrences, remaps attribute-style names (`helper.process`) to their root symbol, resolves import aliases via `alias_to_original` when present, and calls `extract_callee_source` to fetch the definition's source code on first occurrence of each (source file, name) pair. Results are grouped by (definition file, resolved name), with line numbers accumulated, deduplicated, and sorted; this is the primary entry point called by `file_analyzer.py` when building the callee_usages section of a file's knowledge record.

## `_collect_names_from_target`

Determines, for one caller file and its parsed import list, which symbol names in the caller actually refer to definitions in a given target file, applying language-specific import semantics driven by `IMPORT_RESOLVE_CONFIG` separators. Handles direct `from X import a, b` name lists, wildcard imports (`*`) by loading all target definitions via `_load_target_definitions`, Java/Kotlin dotted imports (`import com.foo.Bar`) by extracting the trailing segment, Java/Kotlin wildcard package imports by checking directory prefix matches, C/C++ `#include` by treating the entire target file as included, and same-package visibility (via `SAME_PACKAGE_VISIBLE`) as a fallback when no import matched but caller and target share a directory. It reuses a caller-supplied `target_definition_names` cache to avoid re-parsing the target file multiple times within one `build_caller_usages` run, and returns the collected names plus the (possibly newly populated) cache for reuse by later callers.

## `_load_target_definitions`

Parses a target file (via `parse_file`) and extracts all named definitions from it using `extract_definitions` with the language-appropriate dictionary from `DEFINITION_DICTS`, returning just the definition names. It exists to support wildcard imports, whole-file inclusion (C/C++ `#include`), Java/Kotlin wildcard package imports, and same-package visibility, all of which require knowing every name a file defines rather than only explicitly imported names; it silently returns an empty list if the target extension has no configured definition dictionary or the file does not exist.

## `build_caller_usages`

Collects, across a list of caller files, the locations where names defined in a given target file are referenced, producing the "caller_usages" JSON data. For each caller file it parses the AST, extracts imports via `extract_imports`/`get_import_params`, determines relevant target-originating names via `_collect_names_from_target` (caching target definitions across callers for efficiency), then uses `extract_typed_aliases` and `extract_usages` to locate usage occurrences of those names (including typed-alias variables) within the caller. Usages are grouped by resolved name with merged/deduplicated line numbers, and for each group a bounded source-code snippet (`usage_context`) is built from up to two usage locations with a fixed line radius around each, read directly from the caller's source file; this is the entry point invoked by `file_analyzer.py` to populate the caller_usages section of a file's knowledge record.

# Summary

This module builds cross-file usage reports linking symbols to their definitions. Main public functions: build_usage_info_list (aggregates callee usages—where imported names are used and defined, with source snippets, for a single file's AST) and build_caller_usages (finds where a target file's definitions are consumed across caller files, with line numbers and code context). Internal helpers _collect_names_from_target and _load_target_definitions resolve language-specific import semantics (Python/JS/TS, Java/Kotlin, C/C++ includes, same-package visibility) and enumerate definitions for wildcard/whole-file inclusion cases.
