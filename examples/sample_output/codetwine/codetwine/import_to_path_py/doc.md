# Design Document: codetwine/import_to_path.py

# Design Specification

**Overview**

Resolve import statement module names to project file paths by parsing relative/absolute imports, generating file candidates based on language-specific rules, and matching them against the project's file set.

This file is used by a developer or module to:
- Call `resolve_module_to_project_path()` with a module string from an import statement to determine if it references a project-internal file, receiving the relative path or None.
- Call `detect_source_roots()` with a project file set to identify source root prefixes (like "src/main/java/") that exist in the project, enabling fallback matching for languages with deep directory structures.
- Call `top_level_definition_names()` with a file path to extract the outermost definition names in that file, used for validating symbol bindings across the codebase.
- Call `get_import_params()` with a file extension to retrieve the tree-sitter Language and import query string needed to parse imports from that file type.
- Call `nearest_first()` to sort candidate paths by proximity to a current file, prioritizing locally-defined symbols during name resolution.
- Call `clear_import_path_cache()` when the project file set changes to invalidate cached file name indexes.

The file serves as the central import resolution engine: it depends on language-specific configuration dictionaries (EXT_TO_IMPORT_RESOLVE_DICT, EXT_TO_DEFINITION_DICT, EXT_TO_IMPORT_QUERY_DICT) from codetwine/config/settings.py to retrieve per-extension import rules and definition extraction settings; it delegates Rust and COBOL module resolution to specialized resolvers (resolve_rust_module_path, resolve_cobol_module_path) when those language flags are present; and it coordinates path resolution across TypeScript/JavaScript configuration files (via path_config), package.json import/export fields (via package_import_path_list, package_name_path_list), bundler aliases (via alias_path_list), and source root fallbacks. Callers in codetwine/extractors/dependency_graph.py and codetwine/import_binding.py invoke these functions to populate dependency graphs and symbol tables.

A global file name index cache (_file_name_index_cache) is maintained keyed by project file set identity to avoid re-indexing the same project; the cache is cleared before reprocessing. The resolution algorithm prioritizes path resolution strategies in order: TypeScript/JavaScript configuration paths first for non-relative imports, then standard candidate generation with fallback to source roots, then path-end matching for C/C++ includes; this multi-stage approach handles both modern package managers and legacy include systems without conflating them.

**Definitions**

## `_file_name_index_cache`

Global dictionary caching the file name index (mapping base file names to full paths) for each project file set, keyed by the identity of the set to detect when the project changes; entries store both the original file set reference and the computed index to validate cache hits.

## `clear_import_path_cache`

Forget all cached file name indexes so that a subsequent `resolve_module_to_project_path()` call with a different project file set will recompute its index instead of returning stale results.

## `detect_source_roots`

Identify all source root prefixes (e.g., "src/main/java/", "src/") that actually appear in the project file set by checking each known pattern from SOURCE_ROOT_PATTERN_LIST both at the start of file paths and after intermediate directories, enabling fallback matching for languages like Java that place source files under deep conventional roots.

## `_common_dir_count`

Count how many leading path components two relative paths share, used by `nearest_first()` to measure proximity between files for tiebreaking during candidate selection.

## `nearest_first`

Sort a list of candidate paths so that paths sharing the most leading directory components with a current file come first, then by length, then lexicographically; this ordering prioritizes locally-defined symbols when multiple candidates exist, matching how scoping rules work in most languages.

## `_file_name_index`

Build and cache a reverse index mapping base file names to their full project paths, computed once per unique project file set using identity-based caching to detect when the project contents change.

## `_find_by_path_end`

Match a candidate path against project files by checking which files end with the candidate, using the file name index for efficiency and `nearest_first()` to select the most locally relevant match; this handles C/C++ #include directives where "geo/shape.hpp" should resolve to "include/geo/shape.hpp" without requiring the full path in the import.

## `resolve_relative_import`

Convert a module name from an import statement into a list of directory path components by detecting Python-style relative imports (leading dots), JavaScript/TypeScript relative imports ("./", "../"), and absolute imports, handling path normalization via os.path.normpath for JavaScript/TypeScript to resolve ".." segments correctly; returns an empty list when a relative path traverses to the project root.

## `generate_candidate_path_list`

Generate a list of file path candidates in priority order from a base path and language-specific EXT_TO_IMPORT_RESOLVE_DICT settings, avoiding language-specific branching by using declarative configuration fields: appending the current file's extension unless the base path already has a known extension, trying index files for directories, appending alternative extensions, checking for package __init__ files, trying the bare path, and optionally including candidates relative to the current directory; duplicates are removed while preserving order.

## `resolve_module_to_project_path`

Convert an import statement's module name to a project file path by orchestrating a multi-stage resolution pipeline: parsing the module into path components via `resolve_relative_import()`, generating candidates via `generate_candidate_path_list()`, matching against project_file_set, falling back to source roots for languages like Java, and finally attempting path-end matching for C/C++ includes; also handles TypeScript/JavaScript configuration-based resolution (tsconfig.json baseUrl/paths, package.json imports/exports, bundler aliases) for non-relative imports when a config file exists or applies. Returns None if no project-internal file matches and the module is external; never returns the current file itself.

## `top_level_definition_names`

Extract the names of top-level (outermost) definitions in a file by parsing it with EXT_TO_DEFINITION_DICT settings, filtering out nested definitions and special types (Rust impl blocks, C++ namespaces whose members are returned instead), used to validate symbol visibility and bindings across the project.

## `get_import_params`

Retrieve the tree-sitter Language object and import query string for a given file extension from EXT_TO_LANGUAGE_DICT and EXT_TO_IMPORT_QUERY_DICT, returning (None, None) for unsupported extensions so that callers can skip import analysis gracefully; for languages without import statements (SQL), the query string is None.

# Summary

# Summary: codetwine/import_to_path.py

**Single Responsibility:** Resolve import statements to project file paths using language-specific rules, tree-sitter parsing, and multi-stage matching strategies.

**Main Public Functions:**
- `resolve_module_to_project_path()` – converts import module names to project file paths
- `detect_source_roots()` – identifies language-specific source root patterns in the project
- `top_level_definition_names()` – extracts outermost definitions from files
- `get_import_params()` – retrieves tree-sitter configuration per file extension
- `nearest_first()` – sorts candidates by directory proximity
- `clear_import_path_cache()` – invalidates cached file indexes

**Key Responsibilities:** Parses relative and absolute imports; generates file candidates using language-specific rules; handles TypeScript/JavaScript paths, package.json fields, and bundler aliases; delegates Rust and COBOL to specialized resolvers; maintains a global file name index cache; performs path-end matching for C/C++ includes; prioritizes resolution strategies (config paths → standard candidates → source roots → path-end matching).
