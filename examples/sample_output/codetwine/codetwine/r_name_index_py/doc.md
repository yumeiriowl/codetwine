# Design Document: codetwine/r_name_index.py

# Design Specification

**Overview**

Build and query a name index of R source files in a project to resolve references to their definitions, accounting for R's scoping rules including package structure, source file inclusion, Shiny app organization, testthat test helpers, and box module imports.

The file is used to:
- Call `r_reference_target_list()` to resolve each reference in an R file to the definitions it refers to, returning target metadata for usage analysis.
- Call `r_import_file_list()` to obtain the scripts an R file reads via `source()` or box modules, for building dependency graphs.
- Clear `r_name_index_cache` and `r_target_cache` when the project file set changes or caches need invalidation.

The file depends on `codetwine/extractors/r_source.py` to parse R files into definitions, imports, and references; `codetwine/parsers/ts_parser.py` to obtain ASTs; and `codetwine/config/settings.py` to identify R file extensions and language settings. The file is consumed by `codetwine/extractors/dependency_graph.py` for building call graphs and clearing caches, `codetwine/extractors/usage_analysis.py` to resolve targets into source text ranges, and `codetwine/reference_target.py` as the entry point for R reference resolution.

The module maintains two global caches: `r_name_index_cache` stores parsed project indexes by project directory, and `r_target_cache` stores resolved reference targets by file path, both keyed by project file set to ensure cache validity across incremental updates. References unresolvable in the project scope (e.g., from external packages) silently return no targets.

**Definitions**

## `RReferenceTarget`

Holds metadata for one resolved reference of an R file: the name used at the reference site, the line number, the relative path of the file containing the definition, the lookup key for that definition, and the start and end lines of the definition in the target file. A single reference may resolve to multiple targets when the same name is defined in multiple files of the lookup table.

## `_BoxModule`

Represents one argument of a box::use import: either a module script within the project (identified by relative file path) or a package of the project (identified by package name). Distinguishes between local module paths and package references for name resolution.

## `_BoxScope`

Tracks the names attached by box::use calls within a single file: names bound individually (`attach_table`), names from attach-all imports (`attach_all_table`), and the box modules bound to names (`module_dict`). Used to resolve names visible to a file through its box imports without recursing through transitive box dependencies.

## `RNameIndex`

Central index of an R project's structure, built once per project file set and cached. Contains source files with their extracted definitions and imports; top-level name tables per file and per package; script file metadata grouped by directory and package; source() and box::use() dependency edges; and box scopes for name attachment. Enables O(1) name lookups and dependency traversal for reference resolution.

## `_file_table`

Extract the top-level names a file defines, excluding class members and method attachments (is_member, is_attach) to preserve only names visible to other files. Used during index construction to populate per-file and per-package name tables.

## `_merge_table`

Combine multiple name tables into one by extending each name's entry list. Preserves order and allows multiple definitions of the same name across different files or modules.

## `_ancestor_dir_list`

Return a file's directory and each ancestor directory up to the project root, nearest first. Used to search for script imports and DESCRIPTION files up the directory tree.

## `_package_name`

Read a DESCRIPTION file and extract the package name from its "Package:" field. Returns None if the file does not exist or lacks that field. Called during package indexing to identify package boundaries.

## `_add_package`

Index the packages of the project: assign each file to its nearest enclosing package directory (via DESCRIPTION), collect package names and their directories, and build name tables for each package's R/ directory. Updates `package_dir_dict`, `package_name_dict`, `package_file_dict`, and `package_table_dict` in the index.

## `_source_file`

Resolve a source() call path to a project script by searching from the file's directory upward, then checking case-insensitive matches. Returns None for root paths or unresolvable imports. Accounts for R's behavior of relative path resolution and case-insensitive file systems.

## `_box_module_file`

Resolve a box module path (with "/") to a project script, trying both `.R` and `.r` extensions and `__init__` files. Distinguishes relative paths ("./" or "../") from project-root-relative paths. Returns None if no matching script exists.

## `_import_file`

Dispatch source() and box::use() import paths to their resolver functions. Returns the relative path of an imported script, or None for package imports (library, box package references) and unresolvable paths.

## `_add_source_edge`

Index source() call dependencies: populate `source_file_dict` (files each script reads) and `reader_file_dict` (scripts that read each file). Used by reference resolution to follow transitive source() chains.

## `_reach_list`

Traverse an edge dictionary starting from a list of files, returning all reachable files in breadth-first order without duplicates. Used to follow source() chains and reader chains during name lookup scope construction.

## `_package_dir_list`

Return the project package directories a file names with a given package name (library(), box::use()), preferring the file's own package if it has that name. Used to resolve library() imports and namespace-qualified names.

## `_package_table`

Get the name table of packages matching a package name, merging tables from multiple packages if the name is ambiguous. Used to resolve library() and namespace-qualified references like pkg::name.

## `_box_module`

Resolve one box::use argument to a module (script or package). Distinguishes paths with "/" (module scripts via `_box_module_file`) from single names (package references). Returns None if the path or package does not exist in the project.

## `_module_table`

Extract the names a box module exposes to a file that binds it: for scripts, combines the module's own top-level names with names attached by its box::use calls; for packages, returns the package's name table. Handles circular box dependencies by marking visited files and avoiding infinite recursion.

## `_box_scope`

Build the scope of box::use attachments for a file by resolving all its box::use imports and populating `attach_table`, `attach_all_table`, and `module_dict`. Caches results in `box_scope_dict` unless the resolution encountered a circular dependency back to a caller.

## `_build_name_index`

Parse all R files in a project file set, extract their definitions and imports, index packages and source() edges, and return a complete `RNameIndex`. Logs warnings and skips files that fail to parse.

## `_get_name_index`

Return the cached `RNameIndex` for a project if it matches the current file set, or build and cache a new one. Ensures the index remains valid across incremental file set changes via `project_cache_value`.

## `_ReferenceResolver`

Resolve each reference in one R file to the definitions it refers to, applying R's visibility rules. Instantiates with a file and builds its lookup scope (files and names it can see), then answers reference queries by searching that scope in priority order.

## `_ReferenceResolver._with_source`

Filter a list of scripts to those not in a skip set, following source() edges to include transitively sourced files. Used to expand lookup scopes with source() chains.

## `_ReferenceResolver._reader_file_list`

Build the list of files that read the current file with source(), followed up through their readers, plus all scripts those files read. Included in the lookup scope so names from sourcing files are visible.

## `_ReferenceResolver._is_app_dir`

Check whether a directory contains app.R or server.R, indicating a Shiny app. Used to identify app directories for Shiny-specific name scoping.

## `_ReferenceResolver._app_file_list`

Return the Shiny app scripts visible to a file: for app.R, ui.R, server.R, or R/ scripts of an app, return global.R and the app's R/ scripts; otherwise return empty. App files see global.R and each other's names.

## `_ReferenceResolver._test_helper_file_list`

Return the helper and setup scripts in the testthat/ directory of a test file. These scripts' names are visible to the test file.

## `_ReferenceResolver._library_file_set_list`

Collect the package scripts attached by library() / require() in the file and its sourced files, returning one set per package in reverse attachment order. Excludes the file's own package. Used to add package scopes to the lookup list.

## `_ReferenceResolver._lookup_file_set_list`

Build the ordered list of file sets a name lookup searches, reflecting R's visibility rules: the file itself and box attachments, then its package (if applicable), then sourced files, then reading files, then app and test files, then the package (if not already included), then library packages. Empty sets are excluded.

## `_ReferenceResolver._lookup`

Return the definitions of a name the file sees, caching the result. Delegates to `_find_entry_list` on first call per name.

## `_ReferenceResolver._find_entry_list`

Search for a name's definitions in priority order: the file's own table, box attachments (one by one, then whole), then the file's lookup scopes in order. Returns the first non-empty list found or an empty list if not found.

## `_ReferenceResolver._package_table`

Get and cache the name table of a package named by the file, used for namespace-qualified references.

## `_ReferenceResolver._member_entry_list`

Follow a chain of names after a box module (e.g., logic$data$load), descending into nested modules if the file binds them to other modules, and resolving the final name in the innermost module's table. Returns the definitions the final name refers to.

## `_ReferenceResolver._entry_list`

Dispatch a reference to the appropriate lookup based on its kind: namespace-qualified names (`pkg::name`) are looked up in the package table; member references (`owner$name`) are looked up in box modules or as simple names; other references are looked up through the file's scope.

## `_ReferenceResolver._method_target_list`

Find the generics each S3 method of the file is written for. For each top-level function whose name contains dots (e.g., print.person), try splitting it at each dot from right to left and find the longest generic name that refers to a function calling UseMethod. Returns one target per generic-method pair.

## `_ReferenceResolver.target_list`

Resolve all references and S3 methods of the file to their definitions, returning one `RReferenceTarget` per reference-definition pair.

## `r_reference_target_list`

Main entry point: resolve all references of an R file to the definitions they refer to, applying R scoping rules including packages, source() chains, Shiny app organization, testthat helpers, and box modules. Returns a deduplicated sorted list of targets, cached by file path and project file set; empty for unparseable files.

## `r_import_file_list`

Return the relative paths of scripts imported by an R file via source() and box::use(), in line order without duplicates, excluding the file itself. Used to build R project dependency graphs.

# Summary

# Summary: codetwine/r_name_index.py

**Responsibility:** Build and query an index of R project definitions to resolve references according to R's scoping rules, including packages, source() chains, Shiny apps, testthat helpers, and box modules.

**Main Public Definitions:**
- `r_reference_target_list()` — resolve all references in an R file to their definitions
- `r_import_file_list()` — list scripts imported via source() and box::use()

**Key Concepts:** Name indexing by file and package; lookup scope construction reflecting R visibility rules; reference resolution through namespace qualification, member access, and S3 method matching; caching by project file set; silent handling of unresolvable external references.
