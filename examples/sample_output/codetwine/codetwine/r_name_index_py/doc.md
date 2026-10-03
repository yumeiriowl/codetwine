# Design Document: codetwine/r_name_index.py

# Design Specification

**Overview**

Index the definitions, imports, and references of R files in a project to resolve each reference to the definition it refers to, supporting R's scoping rules, package structure, source file inclusion, box modules, Shiny apps, and testthat tests.

- Call `r_reference_target_list()` to resolve each reference of an R file to the definition it refers to, receiving a list of targets with definition location and metadata.
- Call `r_import_file_list()` to find the scripts an R file reads with `source()` or imports with `box::use`, receiving relative paths of imported scripts.
- Call `RNameIndex` through internal functions to access the project's parsed R sources, indexed names, package structure, and source() edges.
- Call `RReferenceTarget` to examine resolved references, receiving name, line, file location, and definition boundaries.

This file relies on `r_source.py` to extract definitions, imports, and references from parsed R files; `ts_parser.py` to parse R source files into abstract syntax trees; `settings.py` to identify R file extensions and handle R Markdown files; and `project_cache.py` to validate cached indexes against project file set changes. Files `dependency_graph.py`, `usage_analysis.py`, and `pipeline.py` import `r_reference_target_list()`, `r_import_file_list()`, and the cache objects to build dependency graphs and analyze usage.

The module caches both name indexes (project-level structures mapping files to definitions) and resolved reference targets (per-file lists of references and their targets) to avoid recomputation when the project file set has not changed. Caching is keyed by project directory for indexes and by absolute file path for targets, with cache validation via `project_cache_value()`. Files that cannot be read or parsed are logged as warnings and excluded from the index rather than raising exceptions.

**Definitions**

## `RReferenceTarget`

Records where a reference of an R file resolves to: the name used, the line it appears on, the file and definition containing it, the name used to look it up, and the definition's line range. Used by downstream analysis to link references to their targets and extract definition text.

## `_BoxModule`

Represents the result of one argument to `box::use`: either a module script file of the project or a package of the project by name. Populated by `_box_module()` and used to resolve member access (owner$name) through module bindings.

## `_BoxScope`

Collects the names that `box::use` calls of one file bind: names attached one by one (`attach_table`), names from modules that attach all (`attach_all_table`), and modules bound to aliases (`module_dict`). Built by `_box_scope()` and used by `_ReferenceResolver` to locate names visible through box imports.

## `RNameIndex`

Central data structure holding the indexed state of R files in a project: parsed sources by file, top-level names defined by each file, script file set with case-insensitive lookup, directory and package structure, source() edges, and box module bindings. Populated by `_build_name_index()` and queried by `_ReferenceResolver` to resolve references.

## `_file_table`

Extracts the top-level names an R file defines, excluding class members and S4/S3 method definitions (entries where `is_member` or `is_attach` is true). Used during indexing to populate `RNameIndex.table_dict` and later to look up names visible to references.

## `_merge_table`

Combines multiple name tables into one by concatenating entry lists for each name across tables in order. Used to merge the definitions from multiple files (e.g., all scripts in a package's R directory, or all modules in a box attachment chain).

## `_ancestor_dir_list`

Returns the directory of a file and every ancestor directory up to the project root, in nearest-first order. Used to search for package DESCRIPTION files, resolve relative `source()` paths, and determine scope chain for names visible through enclosing directories.

## `_package_name`

Reads the value of the "Package:" field from a DESCRIPTION file in a given directory, returning the package name or None if the file does not exist or lacks the field. Used during indexing to map directories to package names.

## `_add_package`

Populates `RNameIndex` with package structure: the package directory containing each file, the directories of all packages by name, the scripts in each package's R directory, and the merged name table of each package. Walks ancestor directories to find DESCRIPTION files for each indexed file.

## `_source_file`

Resolves a `source()` path relative to a file to the actual R script in the project, trying the path from the file's directory and parent directories, then case-insensitive matching if exact paths fail. Returns the relative path or None for absolute paths or paths with no matching script.

## `_box_module_file`

Resolves a box module path (with "/") to the R script it refers to: a file at `<path>.R/.r` or `<path>/__init__.R/.r`, searched from the file's directory for relative paths (./,../) or from the file's directory and ancestors for module-style paths. Returns the relative path or None if no script matches.

## `_import_file`

Returns the script a `source()` call or `box::use` with a module path leads to by dispatching to `_source_file()` or `_box_module()`. Used to build the source file dependency edges in `RNameIndex`.

## `_add_source_edge`

Builds the source file dependency graph in `RNameIndex`: for each file, resolves its `source()` calls to scripts and records which files source each script. Populates `RNameIndex.source_file_dict` and `RNameIndex.reader_file_dict`.

## `_reach_list`

Traverses a directed graph of edges (file-to-file dependencies) from a list of start files and returns all reachable files in order, with no duplicates and start files first. Used to follow chains of `source()` calls and reverse-source readers.

## `_package_dir_list`

Returns the directories of packages a file refers to by a package name: if the file itself is in a package of that name, returns that package only; otherwise returns all packages in the project with that name, in path order. Used to resolve `library()` and `box::use()` package references.

## `_package_table`

Returns the merged name table of all packages a file refers to by a package name. Used to look up names in `pkg::name` syntax and to find names that `library()` or `box::use()` make visible.

## `_box_module`

Resolves one argument of `box::use` to the module it refers to: a file path with "/" becomes a `_BoxModule` with a file if the script exists, a single-part path becomes a `_BoxModule` with a package name if a package exists, or None if neither matches. Used to bind module names and resolve member access.

## `_module_table`

Returns the top-level names a box module makes visible to the file that binds it: its own names plus the names its own `box::use` calls attach (excluding circular imports detected via `visit_set`). Used by `_box_scope()` and `_ReferenceResolver` to resolve names attached by box imports.

## `_box_scope`

Builds the `_BoxScope` of a file: the names box::use calls bind to (one by one and in bulk), and the modules bound to aliases for member access. Detects cycles in module attachments and caches the scope when no cycles back to the caller. Called once per file during reference resolution.

## `_build_name_index`

Parses all R files in a project, extracts their definitions and imports, and builds the complete `RNameIndex` with file tables, package structure, and source() edges. Logs warnings for files that cannot be read or parsed and excludes them from the index.

## `_get_name_index`

Returns the cached `RNameIndex` for a project or builds it on first call, storing it in `r_name_index_cache` keyed by project directory with the project file set as a validity check. Reused for multiple files until the project file set changes.

## `_ReferenceResolver`

Resolves each reference in one R file to the definitions it refers to by building lookup tables, following scope rules (file, box imports, source() chains, Shiny app globals, testthat helpers, package), and handling package and member access syntax. Instantiated once per file in `r_reference_target_list()`.

## `_ReferenceResolver._with_source`

Expands a list of files to include all files reached by following their `source()` calls recursively, optionally excluding a skip set. Used to include definitions from all scripts in a source chain when building scope.

## `_ReferenceResolver._reader_file_list`

Returns files that read the current file with `source()`, followed up through their readers, and all files those readers source. Used to make names visible to files that include the current file.

## `_ReferenceResolver._is_app_dir`

Tests whether a directory is a Shiny app directory by checking for app.R or server.R. Used to identify app contexts where global.R and R/ scripts are mutually visible.

## `_ReferenceResolver._app_file_list`

Returns the names visible to a file in a Shiny app context: global.R and the R directory scripts when the file is app.R, ui.R, server.R, or a script in the R directory. Used to add app-scoped names to the lookup chain.

## `_ReferenceResolver._test_helper_file_list`

Returns the helper and setup scripts in a testthat directory for a file in that directory. Used to include test helper definitions when resolving names in test files.

## `_ReferenceResolver._library_file_set_list`

Returns the scripts of project packages attached with `library()` or `require()`, in reverse order of attachment (last-attached first), excluding the file's own package. Used to add package definitions to the lookup chain.

## `_ReferenceResolver._lookup_file_set_list`

Builds the ordered list of file sets where a name is looked up: the file itself, box imports, sourced files, source readers, Shiny app globals, testthat helpers, the file's package, and attached packages. Implements the complete R scoping rules for this indexer.

## `_ReferenceResolver._lookup`

Caches and returns the definitions a name resolves to in the current file, searching the file's own names, box attachments, and then the lookup file set chain in order. Each name is looked up at most once per file.

## `_ReferenceResolver._find_entry_list`

Searches for definitions of a name in the lookup file set chain: first the file's own tables and box attachments, then the first file set that defines the name. Used by `_lookup()` to resolve bare name references.

## `_ReferenceResolver._package_table`

Returns and caches the names of packages a package name resolves to. Used to resolve `pkg::name` and `pkg:::name` syntax and to look up names attached by `library()`.

## `_ReferenceResolver._member_entry_list`

Resolves a chain of member accesses after a box module (owner$name$a$b): follows intermediate names that are also modules, then looks up the final name in the terminal module. Used to resolve `owner$member` and deeper chains.

## `_ReferenceResolver._entry_list`

Dispatches reference resolution based on syntax: `pkg::name` looks up in the package, `owner$name` looks up in the module bound to owner, and bare names use the lookup chain. Used to resolve the target of each reference.

## `_ReferenceResolver._method_target_list`

For each S3 method in the file (function named `generic.class`), finds the generic function it refers to by looking up progressively shorter prefixes until a function that calls `UseMethod` is found. Returns targets pointing from the method to the generic at line 1 of the method.

## `_ReferenceResolver.target_list`

Returns all targets for references and S3 methods in the file by resolving each reference through `_entry_list()` and calling `_method_target_list()`. Used by `r_reference_target_list()` to produce final results.

## `r_reference_target_list`

Main entry point: resolves each reference of an R file to the definition it refers to, handling all R scoping rules (packages, box modules, source chains, Shiny apps, testthat). Returns targets in line order without duplicates, cached by absolute file path with project file set validation.

## `r_import_file_list`

Returns the scripts an R file reads with `source()` or imports with `box::use`, in line order without duplicates or the file itself. Used by downstream code to build call graphs and dependency edges.

## `r_name_index_cache`

Module-level cache mapping project directory to (project file set, `RNameIndex`), used to reuse indexes across multiple reference resolutions and imports lookups. Cleared when the project file set changes or cache is explicitly cleared.

## `r_target_cache`

Module-level cache mapping absolute file path to (project file set, list of `RReferenceTarget`), used to reuse reference resolution results within a project. Cleared when the project file set changes or cache is explicitly cleared.

# Summary

# Summary: codetwine/r_name_index.py

**Responsibility:** Index R project definitions and resolve references to their targets, handling R's scoping rules including packages, box modules, source file chains, Shiny apps, and testthat tests.

**Main Public Definitions:**
- `RReferenceTarget`: Records where a reference resolves to, including name, location, and definition boundaries.
- `RNameIndex`: Central index of project R files, their definitions, package structure, and source dependencies.
- `r_reference_target_list()`: Resolves each reference in an R file to its definition.
- `r_import_file_list()`: Returns scripts imported via `source()` or `box::use()`.

**Key Capabilities:** Parses R source files, extracts definitions and imports, builds file-level and project-level name tables, resolves package and module dependencies, follows source chains and Shiny app contexts, detects box module cycles, and implements R's full scoping rules. Caches indexes and results by project to avoid recomputation when files unchanged.
