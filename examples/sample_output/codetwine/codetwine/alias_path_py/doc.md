# Design Document: codetwine/alias_path.py

# Design Specification

**Overview**

Resolve module import strings that use bundler aliases by parsing configuration files and matching alias patterns to construct normalized paths relative to the project root.

- Call `alias_path_list()` to resolve an import module string using bundler aliases defined in configuration files, receiving a list of relative paths the alias maps to.
- Call `clear_alias_path_cache()` before re-analyzing a project to forget cached aliases and ensure fresh reads of configuration files.

This file supports import resolution in the dependency analysis pipeline by interpreting TypeScript/JavaScript bundler configuration aliases (such as Vite, Webpack, or TypeScript path mappings written in object or array form). It depends on `parse_file()` from `codetwine/parsers/ts_parser.py` to parse configuration files as syntax trees and on `inside_project()` from `codetwine/path_config.py` to validate that alias targets remain within project boundaries. The file `codetwine/import_to_path.py` calls `alias_path_list()` as part of its module-to-path resolution strategy, and `codetwine/extractors/dependency_graph.py` calls `clear_alias_path_cache()` to reset state during project re-analysis.

Aliases are cached per project directory to avoid re-parsing configuration files for every import lookup; when a configuration file is not found in a directory, the search walks up the directory tree and caches the result for all intermediate directories. Parsing errors in configuration files are logged as warnings and treated as if no aliases are defined; paths leading outside the project are silently rejected.

**Definitions**

## `clear_alias_path_cache`

Forgets all cached aliases for every project, typically called before re-analyzing a project to ensure configuration files are re-read.

## `alias_cache`

Module-level dictionary mapping project directories to cached aliases, where each project maps relative directory paths to their corresponding alias lists; cleared by `clear_alias_path_cache()`.

## `_text`

Decodes a tree-sitter Node's byte content as UTF-8 and returns it as a string.

## `_string_value`

Extracts the text content of a string constant node (a "string" type) by concatenating its string_fragment children, returning None for non-string nodes; used to extract literal values from configuration files.

## `_argument_list`

Returns the named child nodes representing arguments of a function call or new expression by reading the "arguments" field, or an empty list if none exist.

## `_alias_target`

Interprets an alias value node and returns the directory or file path it points to, relative to the project root, or None if the value is not recognized or the path leads outside the project. Recognizes literal relative paths starting with ".", calls to `resolve()` or `join()` with `__dirname` or `import.meta.dirname`, and `fileURLToPath()` wrapping a URL constructor with `import.meta.url`.

## `_pair_dict`

Extracts key-value pairs from an object literal node, returning a dictionary mapping string or identifier keys to their value nodes; used to navigate bundler configuration object structures.

## `_read_alias_list`

Parses a bundler configuration file by walking its syntax tree to find all "alias" keys with object or array values, extracting alias name-to-path mappings using `_alias_target()`, and returning a sorted list with longest names first. Returns an empty list if the file cannot be parsed, logging the error as a warning.

## `_alias_list`

Returns the aliases applicable to a directory by searching for the first bundler configuration file (by name from config_name_list) in that directory or its ancestors, caching the result for all directories traversed during the search.

## `alias_path_list`

Resolves a module import string using the applicable bundler aliases, matching the longest alias that equals the module or prefixes it up to a "/" boundary, and returning a list containing the alias target path with the remaining module path appended, or an empty list if no alias matches.

# Summary

# Summary: codetwine/alias_path.py

**Responsibility:** Resolves module import strings using bundler aliases (Vite, Webpack, TypeScript path mappings) by parsing configuration files and matching alias patterns to construct normalized paths relative to the project root.

**Public API:**
- `alias_path_list()` – resolves an import string to a list of relative paths using applicable aliases
- `clear_alias_path_cache()` – forgets cached aliases to force fresh configuration file reads

**Key Concepts:** Bundler alias configuration files, import path resolution, syntax tree parsing via tree-sitter, relative path normalization, project boundary validation, directory-based caching with ancestor traversal, configuration file discovery.

**Dependencies:** `codetwine/parsers/ts_parser.py` (parse_file), `codetwine/path_config.py` (inside_project).
