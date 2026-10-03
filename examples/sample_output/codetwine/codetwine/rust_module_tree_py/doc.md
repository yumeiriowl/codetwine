# Design Document: codetwine/rust_module_tree.py

# Design Specification

**Overview**

Build and query the module hierarchy of Rust projects by parsing mod declarations, use statements, and Cargo.toml package metadata to resolve import paths to files and extract definition bindings.

When another file needs to resolve a Rust import path to its source file within a project, it calls `resolve_rust_module_path()` or `rust_import_name_dict()` to map module strings like "crate::config::Settings" to a file path and extract the names that import binds. The module tree is constructed once per project from all .rs files and cached until the project file set changes. Internal resolution follows Rust's scoping rules: absolute paths (leading "::"), keywords (crate, self, super), child modules declared via mod statements, use-statement bindings, glob imports, and fallback to crate root children in edition 2015 packages.

This file depends on `parse_file()` from codetwine/parsers/ts_parser.py to parse each .rs file into an AST, on `extract_definitions()` and `select_top_level_definitions()` from codetwine/extractors/definitions.py to find top-level definitions in each file, on `mod_declaration()` and `rust_import_list()` from codetwine/extractors/rust_path.py to extract module declarations and use statements, on `EXT_TO_DEFINITION_DICT` from codetwine/config/settings.py for language-specific definition extraction rules, and on `project_cache_value()` from codetwine/utils/project_cache.py to validate cached module trees. The file is used by codetwine/import_binding.py to populate import bindings and by codetwine/import_to_path.py to resolve module references during import analysis.

The module tree construction parses every .rs file once and caches the result in `module_tree_cache` keyed by project directory with validation by project file set; clearing the cache is the responsibility of callers like codetwine/pipeline.py and codetwine/extractors/dependency_graph.py. Resolution implements a hop counter (_MAX_USE_HOP = 8) to prevent infinite loops when following use declarations and glob imports, and detects cycles in parent-module traversal via a visit set.

**Definitions**

## `_join_path`

Join relative path segments with "/" and normalize "." and ".." path components; used internally to construct candidate file paths from directory, module name, and path attribute values, ensuring cross-platform path consistency by replacing backslashes.

## `_ModuleFile`

Data class holding the parsed content of one .rs file: the set of top-level definition names, a mapping of names bound by use declarations to their module paths, glob import paths (use a::*), module declarations without bodies (mod name;), and a mapping of enum names to their variant names; populated by `_read_module_file()` and used by resolution logic to determine what names are defined or re-exported.

## `_enum_variant`

Extract the name of an enum and the names of its variants from an enum_item AST node; returns None if the enum has no name or body, used to populate the variant_dict of a _ModuleFile so that use enm::Enum::* imports can expand to variant names.

## `_read_module_file`

Parse a .rs file and collect its top-level definitions (excluding impl items), mod declarations, enum variants, and use/extern_crate declarations; constructs a _ModuleFile by calling extract_definitions() on the parsed AST and walking the syntax tree for mod_item, enum_item, use_declaration, and extern_crate_declaration nodes.

## `_ancestor_dir_set`

Return all directories that contain .rs files and their parent directories, including the project root (""); used internally to locate Cargo.toml files at each level in the directory hierarchy.

## `_read_cargo_package_dict`

Parse Cargo.toml files found in directories containing .rs files and their parents, collecting those with a [package] table; returns a mapping of directory paths to parsed TOML dicts, used to determine crate names and library root files for path resolution.

## `_crate_lib_dict`

Map each crate name to its library root file by reading [lib] name (or [package] name with "-" replaced by "_") and lib path (defaulting to "src/lib.rs") from Cargo.toml files; returns a dict used by resolution to handle paths like "::crate_name::module::item".

## `RustModuleTree`

Class encapsulating the module hierarchy of a project: stores the parsed content of each .rs file in module_file_dict, links parent and child modules via child_dict and parent_dict, identifies crate roots, and implements path resolution following Rust scoping rules. Caches resolution results per (file, segment_list) pair to avoid recomputing paths.

## `RustModuleTree.__init__`

Parse every .rs file in the project, read Cargo.toml files to determine crate names and library roots, and call _link_modules() to connect parent and child modules based on mod declarations; initializes _resolve_cache for memoizing resolution results and _edition_2015_dict to track edition per directory.

## `RustModuleTree._link_modules`

Connect parent and child modules by matching mod declarations to .rs files: for each mod name, candidates are looked up as <dir>/name.rs and <dir>/name/mod.rs (where <dir> depends on the parent file type), or via #[path] attribute relative to the parent's directory, and fallback rules apply when a declaration matches no file but the parent has no parent module yet; when multiple declarations share a name (cfg alternatives), the first matched file becomes the child.

## `RustModuleTree._link`

Register a module as a child of its parent file by adding it to child_dict with the module name as key and updating parent_dict; ensures each file has at most one parent and prevents re-linking if already registered.

## `RustModuleTree.crate_root`

Return the crate root file (the file with no parent module) by following parent_dict up the hierarchy, detecting cycles via a visit set to prevent infinite loops.

## `RustModuleTree._is_edition_2015`

Return True if the nearest Cargo.toml package above a file specifies edition 2015 (or no edition, the default); used to enable fallback resolution rules that only apply to edition 2015 packages.

## `RustModuleTree._is_found`

Check whether resolving through a glob import (use a::*) successfully consumed the glob path and then either consumed the first segment as a module, or found the first remaining segment as a top-level definition of the target file; used to validate whether a glob import matches the segments being resolved.

## `RustModuleTree.resolve`

Resolve a path written in a file (segments joined from "crate::config::Settings") to the file that defines what it names and the segments not consumed by modules; implements caching per (file, segment_list) pair when visit_set is None, and delegates to _resolve_head() to handle the first segment and then _walk() to follow child/parent modules and apply use/glob imports; returns (file relative path, remaining segments).

## `RustModuleTree._resolve_head`

Look up the first segment of a path in order: empty string (leading ::) to access crate or crate name, "crate" to the crate root, "self"/"super" or child module to follow, a use-declared binding or definition of the current file, a crate name from Cargo.toml, glob imports, and finally (for edition 2015) child modules of the crate root; enforces _MAX_USE_HOP to prevent infinite loops through use chains.

## `RustModuleTree._walk`

Follow segments through child modules (mod name), parent modules (super), and the current module (self) in sequence; when a segment does not match a child or parent, apply use-declaration re-exports and glob imports to find the segment as a definition of the module, with hop counting to prevent infinite loops; returns (file, remaining segments).

## `_get_module_tree`

Retrieve or construct the RustModuleTree for a project, caching it by project directory with validation that the project file set has not changed; returns the cached tree if valid, otherwise constructs a new tree from .rs files and stores it in module_tree_cache.

## `_is_path_attribute`

Return True if a module string contains "." or "/" (indicating it is a file path like "unix.rs" from a #[path] attribute) rather than a :: -delimited module path; used to route path resolution through different logic.

## `_resolve_import`

Resolve a module path (segments joined with "::") written in a file to its file within the project by calling resolve() on the module tree; filters out resolutions that lead to the current file itself and returns (file relative path, remaining segments).

## `resolve_rust_module_path`

Resolve a Rust import module string to a project-internal file, handling both :: -delimited paths like "crate::config::Settings" and #[path] file paths like "unix.rs"; returns the file path if it resolves within the project and is not the current file, otherwise None. This is the primary entry point for external callers.

## `rust_import_name_dict`

Return a mapping of names an import binds to their paths within the resolved file, expanding glob imports (use a::*) to enum variant names when they target an enum; applies rules to filter out module references, re-exports from other files, and imports that resolve to the current file; returns an empty dict when the import does not resolve or when it leads outside the project.

## `module_tree_cache`

Module-level cache storing RustModuleTree instances by project directory, with each entry holding both the project file set it was built from and the tree itself; cached trees remain valid only if the project file set has not changed.

## `_OWN_DIR_FILE_NAME_TUPLE`

Constant tuple containing file names (mod.rs, lib.rs, main.rs) whose child modules live in the file's own directory rather than a subdirectory named after the file; used to determine the candidate directory when looking up mod declarations.

## `_MAX_USE_HOP`

Constant maximum number of use declarations followed when resolving a single path (set to 8); enforced via hop counter to prevent infinite loops when following chains of use bindings and glob imports.

# Summary

# Summary: codetwine/rust_module_tree.py

**Responsibility:** Build and cache the module hierarchy of Rust projects by parsing .rs files, Cargo.toml metadata, and mod/use declarations; resolve import paths to source files and extract binding names following Rust scoping rules.

**Main Public Definitions:**
- `resolve_rust_module_path()` — resolve import strings to project files
- `rust_import_name_dict()` — map imported names to their paths
- `RustModuleTree` — module hierarchy with path resolution

**Key Terms:** module tree caching, path resolution (absolute/relative, crate/super/self), use declarations, glob imports, mod declarations, enum variants, Cargo.toml crate names, edition 2015 fallback, hop counting for cycle prevention.
