# Design Document: codetwine/package_path.py

# Design Specification

**Overview**

Resolve module import paths defined in package.json "imports" and "exports" fields, and map package names to their entry points within a project.

A developer would use this file to:
- Call `package_import_path_list()` to resolve "#"-prefixed import aliases defined in a package.json "imports" field to their corresponding file paths
- Call `package_name_path_list()` to resolve bare package names (like "@acme/ui" or "react") to the entry points defined in package.json "exports", "main", or similar fields
- Call `clear_package_path_cache()` when project state changes to discard cached package.json contents

This file depends on `codetwine/path_config.py` for the `PathConfig` class to match module patterns against "imports"/"exports" entries, the `inside_project()` function to validate resolved paths stay within the project, and `read_json_file()` to parse package.json files. The file `codetwine/import_to_path.py` uses both `package_import_path_list()` and `package_name_path_list()` as part of resolving import statements to file paths; `codetwine/extractors/dependency_graph.py` calls `clear_package_path_cache()` to reset state during analysis.

Package.json files are cached per project directory and per relative directory path to avoid repeated file reads. Package names are cached per project directory keyed by the set of project files, allowing reuse across multiple lookups with the same file set. When a package name appears in multiple directories, the one in the directory that comes first in path order is retained.

**Definitions**

## `package_file_cache`

Module-level dictionary mapping project directories to caches of package.json contents, where each inner dictionary maps directory relative paths to their parsed package.json objects or None if no file exists. The cache avoids repeated reads of the same package.json file across multiple calls.

## `package_name_cache`

Module-level dictionary mapping project directories to tuples of (project file set, package directory dictionary), caching the result of `_package_dir_dict()` so the expensive traversal of package.json files is performed only once per unique project file set.

## `clear_package_path_cache()`

Clear both `package_file_cache` and `package_name_cache` to forget all cached package.json files and package names across all projects, used when the project state changes and cached information becomes stale.

## `_package_file()`

Retrieve the parsed content of a package.json file at a given directory, reading it once per directory per project and caching the result to avoid repeated file I/O; returns None if the file does not exist or cannot be read.

## `_target_list()`

Extract all file paths from a package.json "imports" or "exports" entry value, which may be a string, array of values, object with conditional exports (like "types", "import", "default"), or nested combinations thereof, flattening the result into a single list in the order encountered.

## `_pattern_path_list()`

Resolve an "imports" or "exports" pattern dictionary entry against a module name (like "#internal/a" or "./sub"), using `PathConfig` to match the name against patterns containing a single "*" wildcard and return the corresponding target paths, filtered to those that remain within the project boundary.

## `package_import_path_list()`

Resolve a "#"-prefixed module string (like "#db" or "#internal/log") to the file paths it refers to according to the "imports" field of the nearest package.json in or above the directory containing the current file, traversing upward from the file's directory to the project root until an "imports" object is found.

## `_package_dir_dict()`

Map package names to their directories within a project by reading package.json files from every directory that contains a project file and every directory above those, returning the first occurrence of each package name when duplicates exist, with results cached per project and file set.

## `_source_path_list()`

Given a path within a package, return that path followed by the same path with its first subdirectory replaced by "src" when it is a build output directory like "dist" (but not when it is already "src" or points outside the package), allowing resolution of both built and source versions of a module.

## `package_name_path_list()`

Resolve a bare package name (like "@acme/ui" or "react") to all candidate file paths it may refer to within the project, starting with paths defined in the package.json "exports" field, then entry point keys ("types", "typings", "source", "module", "main"), each followed by its source variant from `_source_path_list()`, then fallback paths constructed by appending the requested subpath or index file under the package directory and its "src" subdirectory, with duplicates removed while preserving order.

# Summary

# Summary: codetwine/package_path.py

**Single Responsibility:** Resolve module import paths from package.json "imports" and "exports" fields to file paths, and map package names to their entry points.

**Main Public Definitions:**
- `package_import_path_list()` — resolves "#"-prefixed aliases to file paths
- `package_name_path_list()` — resolves bare package names to entry points
- `clear_package_path_cache()` — clears cached package.json data

**Key Concepts:** Handles package.json parsing and caching per project directory; pattern matching against "imports"/"exports" entries with wildcard support; traversal of directory hierarchies to find package definitions; fallback to source directories ("src") for build outputs; validation that resolved paths stay within project boundaries; deduplication while preserving resolution order.
