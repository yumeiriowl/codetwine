# Design Document: codetwine/path_config.py

# Design Specification

**Overview**

Manage tsconfig.json / jsconfig.json configuration files to resolve TypeScript/JavaScript module paths using baseUrl and paths patterns, with caching of parsed configurations and file coverage information.

- Call `path_config()` to retrieve the module path settings (baseUrl and paths patterns) that apply to a specific source file, enabling import path resolution.
- Call `read_json_file()` to read and parse JSON configuration files that may contain comments and trailing commas, used by external modules like `codetwine/package_path.py` to load package.json files.
- Call `inside_project()` to validate and normalize relative paths, ensuring they stay within project boundaries when processing extends, references, and baseUrl/paths directives.
- Call `PathConfig.module_path_list()` to expand a module name into concrete file paths using the matched pattern from the paths dictionary and the baseUrl fallback.

The file depends on `codetwine/utils/file_utils.py` for encoding-aware file reading via `read_source()`. It is used by `codetwine/import_to_path.py` to resolve non-relative imports, by `codetwine/package_path.py` to read package.json files and expand package export paths, and by `codetwine/alias_path.py` to validate relative path joins within project scope. The `path_config_cache` dictionary is cleared by `codetwine/extractors/dependency_graph.py` when resetting analysis state.

The module uses aggressive caching via `path_config_cache` (global per-project) and per-file `file_config_dict` (inside `_ProjectCache`) to avoid re-reading and re-parsing configuration files and computing directory hierarchies. Configuration inheritance through "extends" is resolved recursively with cycle detection via visit sets. When a file does not match any config file's coverage rules ("files", "include", "exclude"), it uses the settings from the nearest config file found when walking up the directory tree.

**Definitions**

## `PathConfig`

Dataclass holding the module path resolution settings from a tsconfig.json / jsconfig.json file: the baseUrl directory and the ordered list of (pattern, targets) tuples from the "paths" compiler option. Called by `path_config()` to store and pass resolved settings, and by external modules to expand module names into file paths.

## `PathConfig.base_dir`

The base directory (relative to project root, or empty string for root, or None) from which non-relative imports are resolved when no "paths" pattern matches. Set from the "baseUrl" compiler option.

## `PathConfig.path_list`

Ordered list of (pattern string, list of target paths) tuples from the "paths" compiler option, where patterns may contain a single "*" wildcard and targets are relative to baseUrl (or the config file directory if baseUrl is absent).

## `PathConfig.module_path_list`

Given a module name (e.g., "app", "app/store", "react"), return the list of candidate file paths (relative to project root) by matching against the longest-prefix pattern in path_list (substituting the wildcard), then appending the module under base_dir. Returns deduplicated paths; used by external modules to resolve import statements.

## `_CoverSetting`

Dataclass storing the file coverage rules of a config file: the "files" pattern, "include" pattern, "exclude" pattern (each as a tuple of pattern list and the directory they are relative to), and the list of referenced config file paths from "references". Passed to `_is_cover()` to check whether a file is covered by the config.

## `_ProjectCache`

Dataclass caching parsed configuration state for one project: `dir_file_dict` maps directory paths to their nearest config file (or None), `config_dict` caches parsed (PathConfig, _CoverSetting) tuples per config file, and `file_config_dict` caches the final resolved PathConfig per source file. Modified in place by `_nearest_config_file()`, `_cover_config()`, and `path_config()`.

## `read_json_file`

Read a JSON file (tsconfig.json, jsconfig.json, or package.json) from the project, stripping C-style comments and trailing commas before parsing, and return the parsed dictionary or None if the file cannot be read or is not a JSON object. Called by `package_path.py` to load package.json and by `_read_config()` to load config files.

## `_strip_json_comment`

Remove // and /* */ comments and trailing commas from JSON text while preserving string contents, returning the cleaned text ready for json.loads(). Used by `read_json_file()` to handle tsconfig.json / jsconfig.json files that include comments.

## `inside_project`

Validate and normalize a relative path (using posixpath), returning the normalized path or None if it leads outside the project (contains "..", starts with "/", or similar). Called by `_extend_file_list()`, `_reference_file_list()`, `_read_config()` to validate "extends" and "references" directives, and by external modules (`alias_path.py`, `package_path.py`) to ensure paths stay within bounds.

## `_extend_file_list`

Parse the "extends" field from a config file and return the list of extended config file paths (relative to project root) in order written, validating that they are relative paths, exist as files, and stay within the project. Called by `_read_config()` to chain configuration inheritance.

## `_glob_regex`

Convert a glob pattern (with "*" for any chars except "/", "?", and "**/") into a compiled regex that matches file paths and any paths under them (e.g., a pattern "src" matches "src/a.ts" and "src/lib/b.ts"). Called by `_is_match()` to test files against "include" and "exclude" patterns.

## `_is_match`

Test whether a file path matches any pattern in a (pattern_list, directory) tuple, using glob semantics. Called by `_is_cover()` to check "files", "include", and "exclude" rules.

## `_is_cover`

Determine whether a config file covers a file based on its "files", "include", and "exclude" patterns: a file is covered if "files" names it, or if it matches "include" and does not match "exclude" (with defaults: "include" = ["**/*"] when neither "files" nor "include" is set, and "exclude" = _DEFAULT_EXCLUDE_LIST when not set). Called by `_cover_config()` to find which config file's settings apply to a source file.

## `_reference_file_list`

Parse the "references" field from a config file and return the list of referenced config file paths in order, resolving directory references to tsconfig.json and adding ".json" when needed. Called by `_read_config()` to populate the reference_list in _CoverSetting.

## `_read_config`

Read a config file (tsconfig.json or jsconfig.json), parse its "extends" chain to inherit baseUrl, paths, files, include, exclude settings, and read "compilerOptions.baseUrl" and "compilerOptions.paths" directly. Return (PathConfig, _CoverSetting) or None if the file cannot be read. Called by `_nearest_config_file()` and `_cover_config()` to load and cache config state.

## `_nearest_config_file`

Walk up the directory tree from a given directory, returning the path to the first readable config file (matching one of the names in config_name_list) found in that directory or any ancestor up to the project root. Cache the result per directory in project_cache.dir_file_dict. Called by `path_config()` to locate the config file nearest to a source file.

## `_cover_config`

Starting from a given config file, check if it covers a file; if not, recursively check files it references (from "references") until one is found that covers the file. Return the PathConfig of the covering config file or None. Called by `path_config()` to find which config's settings apply to a source file.

## `path_config`

Retrieve the module path settings (PathConfig) that apply to a source file by walking up the directory tree from the file's directory, checking each config file and its "references" until one covers the file, falling back to the nearest config file if none covers it. Result is cached per file in project_cache.file_config_dict. Called by `import_to_path.py` to resolve non-relative imports.

## `path_config_cache`

Module-level dictionary caching parsed configuration state per project directory (project_dir → _ProjectCache), preventing redundant file reads and config parsing across multiple calls. Cleared by `dependency_graph.py` when resetting analysis state.

## `_WILDCARD`

The wildcard character "*" used in "paths" patterns to match and capture parts of module names (e.g., "app/*" captures "app/store" → "store").

## `_FILE_KEY`, `_INCLUDE_KEY`, `_EXCLUDE_KEY`

String constants for the "files", "include", and "exclude" keys in tsconfig.json / jsconfig.json, used to parse file coverage rules.

## `_DEFAULT_INCLUDE_LIST`, `_DEFAULT_EXCLUDE_LIST`

Default patterns for "include" (["**/*"] = all files) and "exclude" (["node_modules", "bower_components", "jspm_packages"]) when a config file does not set them.

## `_REFERENCE_FILE_NAME`

The default file name "tsconfig.json" used when a "references" entry names a directory instead of a file.

# Summary

# Summary of codetwine/path_config.py

**Single Responsibility**: Manage TypeScript/JavaScript configuration files (tsconfig.json, jsconfig.json) to resolve module import paths using baseUrl and paths patterns, with aggressive caching of parsed configurations and file coverage information.

**Main Public Definitions**: 
- `PathConfig`: Dataclass holding baseUrl and paths patterns for module resolution
- `path_config()`: Retrieve settings applying to a source file
- `read_json_file()`: Parse JSON files with comments and trailing commas
- `inside_project()`: Validate relative paths stay within project boundaries

**Key Concepts**: Configuration inheritance through "extends" chains, file coverage rules (files/include/exclude patterns), project references, glob pattern matching, directory tree traversal, per-project and per-file caching, cycle detection in inheritance chains, fallback to nearest config file.
