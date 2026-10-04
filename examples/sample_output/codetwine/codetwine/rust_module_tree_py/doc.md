# Design Document: codetwine/rust_module_tree.py

# Design Specification

**Overview**

Build and query the module tree of a Rust project by parsing mod declarations, use statements, and Cargo.toml files to resolve import paths to their defining files.

The file is used to resolve Rust import module paths to project-internal files:
- Call `resolve_rust_module_path()` to map an import's module string to the .rs file that defines what it names, or None if the import targets an external crate or the current file.
- Call `rust_import_name_dict()` to determine which names an import binds and their paths within the resolved file, handling re-exports, glob imports, and enum variants.
- Call `_get_module_tree()` internally to obtain a cached RustModuleTree for a project's .rs files.

The module builds on `parse_file()` and `extract_definitions()` to read .rs files and identify top-level definitions, uses `mod_declaration()` and `rust_import_list()` to extract module and import metadata from syntax trees, and consults `EXT_TO_DEFINITION_DICT` for language configuration. It is consumed by `import_binding.py` and `import_to_path.py` to bind imports to their targets, and by `dependency_graph.py` and `pipeline.py` which clear its `module_tree_cache` to free memory between analyses.

The module caches RustModuleTree objects per project directory, invalidating the cache when the project file set changes. It resolves paths through use declarations with a maximum hop limit (`_MAX_USE_HOP`) to prevent infinite loops, and distinguishes between edition 2015 and later Rust editions to handle implicit crate-root module visibility differently.

**Definitions**

## `_OWN_DIR_FILE_NAME_TUPLE`

Marks file names whose child modules live in the file's own directory rather than a subdirectory named after the file; used during `_link_modules()` to compute the search directory for mod declarations.

## `_MAX_USE_HOP`

Limits the number of use declarations followed when resolving a single path to prevent infinite loops in cyclic re-export chains; enforced in `resolve()` and `_walk()` during path traversal.

## `module_tree_cache`

Module-level dictionary caching RustModuleTree objects by project directory; each entry stores the project file set the tree was built from so `_get_module_tree()` can invalidate stale caches when files change.

## `_join_path`

Join relative path parts with "/" and normalize "." and ".." segments; used throughout the module to compute file paths from directory and module name components, handling cross-platform path separators.

## `_ModuleFile`

Dataclass holding the parsed top-level contents of one .rs file: definition names, use declarations (bound name to path segments), glob imports, mod declarations (with optional #[path] values), and enum variant names; constructed by `_read_module_file()` and queried during path resolution.

## `_enum_variant`

Extract the name and variant names from an enum_item AST node; returns None if the enum lacks a name or body, otherwise a tuple of enum name and ordered variant names used by `_read_module_file()`.

## `_read_module_file`

Parse a .rs file and collect its top-level definitions, use declarations, mod declarations, and enum variants into a _ModuleFile; called once per file when constructing a RustModuleTree to support later path resolution.

## `_ancestor_dir_set`

Return all ancestor directories of the given relative file paths, including the project root (""); used by `_read_cargo_package_dict()` to locate Cargo.toml files that may define crate metadata.

## `_read_cargo_package_dict`

Read Cargo.toml files in and above directories holding .rs files, extracting those with a [package] table; used to identify crates and their library root files for resolving absolute crate names in imports.

## `_crate_lib_dict`

Map each crate name to its library root file path by reading [lib] and [package] tables from Cargo.toml files; crate names use the [lib] name or [package] name with "-" replaced by "_", enabling resolution of imports like `use external_crate::item`.

## `RustModuleTree`

Class that models the module tree of a Rust project: parses all .rs files, links each mod declaration to its target file, and resolves import paths to the files and definitions they name; constructed once per project and cached.

## `RustModuleTree.__init__`

Initialize the tree by parsing every .rs file, reading Cargo.toml for crate metadata and edition information, and linking mod declarations to their target files via `_link_modules()`; sets up caches for resolved paths.

## `RustModuleTree._link_modules`

Populate child_dict and parent_dict by matching mod declarations to .rs files, following #[path] attributes and searching standard module locations; handles fallback lookup in the file's own directory and prioritizes earlier declarations when multiple candidates exist (#[cfg] alternatives).

## `RustModuleTree._link`

Register a parent-child relationship between two modules in child_dict and parent_dict, using setdefault to preserve the first declaration when multiple mod declarations share a name.

## `RustModuleTree.crate_root`

Follow parent_dict upward to find the module with no parent, which is the crate root (.rs file with no parent module); detects and breaks cycles to avoid infinite loops.

## `RustModuleTree._is_edition_2015`

Determine whether the nearest package above a file is Rust edition 2015 by consulting _edition_2015_dict; used to apply edition 2015's implicit root module visibility when resolving paths.

## `RustModuleTree._is_found`

Test whether resolving through a glob import (use a::*) reached the target path by comparing the remaining segments; returns True if the glob path was consumed and the first remaining segment either was consumed as a module or is a definition in the reached file.

## `RustModuleTree.resolve`

Resolve a path written in a file to the file defining what it names, handling module hierarchy (child, parent, crate root), use bindings, definitions, external crates, glob imports, and edition 2015 rules; caches results per (file, segments) pair and detects cycles via visit_set.

## `RustModuleTree._resolve_head`

Look up the first segment of a path in resolution order: leading "::" (crate or external crate), crate / self / super keywords, child modules, use bindings, file definitions, project crates, glob imports, and edition 2015 root visibility; delegates to `_walk()` to follow remaining segments through module hierarchy.

## `RustModuleTree._walk`

Follow segments through child and parent modules, processing self / super / module names, and resolve remaining segments through use re-exports and glob imports when they are not modules; used by `_resolve_head()` to traverse the resolved module chain.

## `_get_module_tree`

Retrieve or construct the cached RustModuleTree for a project, validating that the tree was built for the current project file set and discarding it if the file set changed; used by public resolution functions to obtain the tree.

## `_is_path_attribute`

Determine whether a module string is the value of a #[path] attribute (contains "." or "/") rather than a "::" -joined path; used to route resolution through direct file lookup instead of module tree traversal.

## `_resolve_import`

Resolve a "::" -joined module path through the module tree, returning the file it names and any unconsumed segments; returns None if the path leads to an external crate or the current file itself.

## `resolve_rust_module_path`

Public entry point to resolve an import's module string (either "::" -joined path or #[path] attribute value) to a project-internal .rs file or None; used by `import_to_path.py` to map imports to their defining files.

## `rust_import_name_dict`

Public entry point to determine which names an import binds and their definition paths within the resolved file; handles re-exports, glob imports with enum variant expansion, and returns an empty dict if the import leads outside the project or to a use re-export of the resolved file.

# Summary

# Summary: codetwine/rust_module_tree.py

**Single Responsibility**

Build and cache a Rust project's module tree by parsing .rs files and Cargo.toml metadata, then resolve import paths to their defining files and bound names.

**Main Public Definitions**

- `resolve_rust_module_path()` — map import module strings to project-internal .rs files
- `rust_import_name_dict()` — determine which names an import binds and their paths
- `module_tree_cache` — module-level cache for RustModuleTree objects by project directory

**Key Capabilities**

Parses mod declarations, use statements, and Cargo.toml to build a module tree; resolves absolute and relative import paths through module hierarchies, use bindings, and glob imports; handles re-exports and enum variants; supports Rust editions 2015 and later; caches results per (file, path) pair; prevents infinite loops with hop limits and cycle detection; invalidates cache when project file sets change.
