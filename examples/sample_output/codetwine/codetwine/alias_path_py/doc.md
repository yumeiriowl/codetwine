# Design Document: codetwine/alias_path.py

# Design Specification

**Overview**

Resolve alias mappings defined in bundler configuration files to convert module import strings into project-relative file paths.

This file is used to:
- Call `alias_path_list()` from `codetwine/import_to_path.py` to expand a module string into zero or more paths when the module uses an alias declared in a bundler config file.
- Call `clear_alias_path_cache()` from `codetwine/extractors/dependency_graph.py` to reset cached aliases when analysis begins on a new project.

The file depends on `codetwine/path_config.py` to validate that alias target paths remain within project boundaries via `inside_project()`, and on `codetwine/parsers/ts_parser.py` to parse bundler config files and extract their AST structure. Files that use this module rely on it to map import statements with aliases to concrete file paths for dependency resolution.

The module implements an LRU cache keyed by project directory and relative path, storing alias lists per directory to avoid re-parsing config files for every import lookup. Parsing failures log warnings and return empty alias lists rather than propagating exceptions, allowing the system to continue with degraded alias support.

**Definitions**

## `alias_cache`

Module-level dictionary caching parsed alias lists by project directory and directory relative path, populated by `_alias_list()` and read by `alias_path_list()` to avoid repeated config file parsing within a single project.

## `clear_alias_path_cache()`

Forget all cached aliases across all projects; called at the start of a new analysis run to ensure fresh reads of potentially modified config files.

## `_text()`

Decode a tree-sitter node's byte content as UTF-8 and return it as a string; used internally to extract source text from the AST.

## `_string_value()`

Extract the text of a string constant without its surrounding quotes, returning None for non-string nodes; handles multi-part string fragments in the AST representation.

## `_argument_list()`

Return the named argument nodes from a function call or constructor's arguments field; used to extract parameters that define alias paths.

## `_alias_target()`

Resolve an alias value node into a normalized project-relative path by recognizing string literals, path-joining function calls with `__dirname` or `import.meta.dirname`, and `fileURLToPath()` with `import.meta.url` patterns; return None if the value uses an unsupported syntax or leads outside the project boundary.

## `_pair_dict()`

Convert an object node's key-value pairs into a dictionary, extracting keys written as strings or identifier names and associating them with their value nodes; used to parse alias definitions and bundler config structure.

## `_read_alias_list()`

Parse a bundler config file at a given relative path and extract all aliases declared under "alias" keys, returning them as (name, target-path) tuples sorted by name length descending; log warnings and return empty results if parsing fails, recognizing both object and array alias formats with "find"/"replacement" keys.

## `_alias_list()`

Return the alias list applicable to a directory by searching upward for the first bundler config file matching a name in `config_name_list`, with results cached per directory to avoid redundant parsing; walk up the directory tree until a config is found or the project root is reached, caching the result in all intermediate directories.

## `alias_path_list()`

Expand a module import string using the longest matching alias from the nearest bundler config file to the importing file, returning zero or one normalized project-relative path; match the module exactly or as a prefix before "/" in the alias name, falling back to empty results if no alias applies.

# Summary

# Summary: codetwine/alias_path.py

**Single Responsibility**
Resolve alias mappings from bundler configuration files to convert module import strings into project-relative file paths.

**Main Public Definitions**
- `alias_path_list()` – expands a module import string using the longest matching alias from the nearest bundler config, returning zero or one normalized path
- `clear_alias_path_cache()` – resets all cached aliases when starting analysis on a new project

**Key Terms**
Handles bundler config file parsing (via tree-sitter AST), alias extraction and validation, LRU caching by project directory to avoid re-parsing, path normalization and project boundary checking, and graceful degradation when parsing fails. Recognizes string literals, path-joining functions with `__dirname` or `import.meta.dirname`, and `fileURLToPath()` patterns in alias target resolution.
