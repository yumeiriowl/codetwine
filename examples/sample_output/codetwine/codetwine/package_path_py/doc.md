# Design Document: codetwine/package_path.py

# Design Specification

**Overview**

Resolve package.json "imports" and "exports" entries to determine the file paths that module strings can stand for in a project, with per-project caching of package metadata.

- Call `package_import_path_list()` to resolve module strings starting with "#" against the "imports" field of the nearest package.json above the importing file.
- Call `package_name_path_list()` to resolve module strings naming a project package against its "exports" and entry point fields ("main", "module", "source", "typings", "types"), with fallback to file system paths under the package directory and its "src" subdirectory.
- Call `clear_package_path_cache()` to discard cached package.json contents when project state changes.

This file depends on `codetwine/path_config.py` to validate paths stay within project boundaries via `inside_project()` and to expand pattern-based "imports"/"exports" entries via `PathConfig.module_path_list()`. The file is used by `codetwine/import_to_path.py` to resolve non-relative module imports to candidate file paths, and by `codetwine/extractors/dependency_graph.py` to clear caches during analysis resets.

The module implements two-level caching: `package_file_cache` stores parsed package.json contents per directory to avoid redundant file reads, and `package_name_cache` stores computed package-by-name mappings per project file set to avoid recomputation when the set of project files changes. Paths in build outputs (like "dist/") are mirrored to their source equivalents (like "src/") when a package follows the pattern of having sources separate from build output.

**Definitions**

## `package_file_cache`

Module-level dictionary caching parsed package.json file contents by project directory and relative directory path, to avoid re-reading and re-parsing the same files; the value is the parsed object or None if the file does not exist or cannot be read.

## `package_name_cache`

Module-level dictionary caching computed mappings of package names to their directories by project directory, paired with the project file set used to compute them; invalidated and recomputed when the project file set changes.

## `_IMPORT_MODULE_START`

Constant character "#" identifying module strings that use the package.json "imports" field for resolution.

## `_ENTRY_KEY_TUPLE`

Tuple of ordered field names ("types", "typings", "source", "module", "main") from package.json that specify the entry point of a package, tried in order to find the primary export path.

## `_SOURCE_DIR_NAME`

Constant directory name "src" representing the conventional location of source files in a package, used to map build output paths back to their source equivalents.

## `_INDEX_FILE_NAME`

Constant file name "index" representing the conventional default entry file of a directory, used as a fallback when no explicit entry point is specified.

## `clear_package_path_cache()`

Discard all cached package.json file contents and package name mappings; called when project state changes to ensure subsequent lookups read current file system state.

## `_package_file()`

Retrieve the parsed content of a package.json file in a given directory, reading and caching it once per directory per project to minimize file I/O; returns None if the file does not exist or cannot be parsed as JSON.

## `_target_list()`

Extract file paths from a package.json "imports" or "exports" entry value, which may be a string, a list of values, or an object with condition-based values (like "types" or "default"), recursively flattening the structure into a list of paths.

## `_pattern_path_list()`

Resolve a module name or import specifier against a set of pattern-based entries (like those in "imports" or "exports" objects), using `PathConfig.module_path_list()` to handle wildcards, and return only paths that remain within the project boundary.

## `package_import_path_list()`

Return file paths that a module string starting with "#" can resolve to by consulting the "imports" field of the package.json in or above the file's directory; traverses up the directory tree to find a package.json with an "imports" field that matches the module name.

## `_package_dir_dict()`

Compute and cache the set of packages in a project by name, scanning package.json files in the directories containing project files and their parent directories; when two packages share the same name, the one in the earliest directory wins.

## `_source_path_list()`

Return a file path and, if the path appears to be in a build output directory (not "src"), also the corresponding path with the first directory under the package replaced by "src"; used to provide both compiled and source versions of a module.

## `package_name_path_list()`

Return file paths that a module string naming a project package can resolve to by checking the package's "exports" field (with pattern matching), then falling back to its entry point fields, and finally to file system lookups under the package directory and its "src" subdirectory; scoped package names (starting with "@") are matched before unscoped names.

# Summary

# Summary: codetwine/package_path.py

**Single Responsibility**

Resolve module strings to file paths by consulting package.json "imports" and "exports" fields, with project-scoped caching of package metadata.

**Main Public Functions**

- `package_import_path_list()`: Resolves "#"-prefixed module strings against "imports" field
- `package_name_path_list()`: Resolves package names against "exports" and entry point fields
- `clear_package_path_cache()`: Invalidates all cached package data

**Key Concepts**

Handles two-level caching: parsed package.json files and computed package name mappings. Supports pattern-based import/export entries via `PathConfig` integration. Falls back through entry point fields ("types", "typings", "source", "module", "main"), then file system lookups in package and "src" directories. Mirrors build output paths (e.g., "dist/") to source equivalents (e.g., "src/") when appropriate. Maintains project boundaries using `inside_project()` validation. Scoped packages ("@namespace/name") are resolved before unscoped names.
