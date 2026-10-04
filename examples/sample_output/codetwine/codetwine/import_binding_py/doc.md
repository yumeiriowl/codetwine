# Design Document: codetwine/import_binding.py

# Design Specification

**Overview**

Map the names bound by import statements in a project's files to their definitions by following imports through files that re-export them until reaching files that define them.

When another file needs to resolve a symbol usage to its definition across project boundaries, it calls `import_binder()` to obtain a binder for the project, then calls methods like `symbol_dict()` to get bindings for a file's import names, `scope_binding_list()` for names bound in specific line ranges, or `implement_file_list()` to find additional files that implement type members in other languages. For C++ member access without a class prefix, `member_scope_list()` returns the members visible inside functions and classes. To determine what a JavaScript default export value refers to, code calls `default_binding()`.

This file depends on `extract_imports()`, `local_export_dict()`, `module_export_list()`, and `python_all_name_list()` from the imports extractor to read import statements and export metadata from parsed files; on `file_definition_list()` to extract top-level definitions and members from files; on `resolve_module_to_project_path()` and `detect_source_roots()` from import resolution to map module strings to file paths; on `parse_file()` to parse files into syntax trees; and on configuration modules for language-specific settings. The file is used by `import_reference.py` to resolve usage references through import bindings to their target definitions, and by `dependency_graph.py` to determine which files an import statement reads from.

The module caches binding results per file and per project to avoid recomputing expensive transitive follows; a binder instance is kept per project file set via `project_cache_value()` and cleared when the file set changes. The module logs warnings when files cannot be parsed or their import statements read, but continues with partial information rather than failing.

**Definitions**

## `SymbolBinding`

Represents what a name refers to after being bound by an import statement or export: a file path (`file_rel`) and optionally a definition name (`name`) within that file. When `name` is `None`, the binding refers to the file as a module or namespace. Used throughout the module to track where a name originated and followed to its definition.

## `ScopeBinding`

Represents a name bound for a specific line range of a file, with a start and end line, the bound name, and a `SymbolBinding` stating what it refers to or `None` when it refers to nothing in the project. Used to track imports and re-exports written inside functions, blocks, or inline modules that apply only to those line ranges.

## `_FileBinding`

Internal dataclass aggregating all the bindings that the import and export statements of a single file create: names bound for the whole file, names passed through by export statements, wildcard imports, inline module boundaries, and the lines of each import statement. Populated once per file by language-specific bind functions and cached.

## `_TreeFact`

Internal dataclass holding the facts extracted from a file's syntax tree that are needed to compute its bindings: import statements, line count, package declaration (Java/Kotlin), Python `__all__`, local and module exports (JavaScript/TypeScript), inline module scopes (Rust), and base class names (C++). Read once per file and cached.

## `clear_import_binder_cache()`

Forget all cached binders for all projects; called when clearing analysis caches after the project file set changes or analysis configuration is updated.

## `import_binder()`

Return the cached `ImportBinder` for a project and file set, building it once if not yet built for that file set. Used whenever import bindings for a project are needed.

## `_join_module()`

Join a Python module name (e.g., `"pkg"`) with a submodule name (e.g., `"core"`) into a qualified module string (e.g., `"pkg.core"`), handling relative imports with leading dots.

## `_package_name()`

Extract the package name declared in a Java or Kotlin file's package statement by traversing the AST, returning `None` if no package is declared. Used to group Java/Kotlin files by package for import resolution.

## `_base_class_name_dict()`

Extract the base class names of each class in a C++ file by traversing class definitions with bodies and collecting base class names from base clause nodes, returning a dictionary mapping class names to lists of base class names without template arguments. Used to resolve member lookups when a member is not found in a class itself but in a base class.

## `ImportBinder`

Main class managing the resolution of import bindings for a project; initialized once per project and file set, it reads import statements from files and follows names through re-exports to their definitions. Stores cached binding information per file and per project, including which files import which others, which names are visible at each location, and where type members are defined in other files.

## `_file_ext()`

Return the language extension determined for a file by `language_ext()`, used to look up language-specific configuration for parsing and binding.

## `_bind_kind()`

Return the "bind" configuration value for a file's language (e.g., `"module"` for Python, `"export"` for JavaScript), or `None` if the language has no import resolution configured.

## `top_level_name_list()`

Return the top-level definition names of a file by calling `top_level_definition_names()`, cached per file; returns an empty list and logs a warning if the file cannot be read.

## `_tree_fact()`

Parse a file once and extract the facts needed for binding: import statements, line count, and language-specific metadata (package names, `__all__`, exports, etc.). Results are cached and reused across all binding operations for the file.

## `_definition_list()`

Return the definitions of a file by calling `file_definition_list()` with the file's language definition settings, used to look up member names and base classes.

## `_resolve()`

Resolve a module string written in a file to a project file path by calling `resolve_module_to_project_path()` with the project's source roots, returning `None` if the module is external or does not exist.

## `_file_binding()`

Return the bindings created by a file's import and export statements, computed once per file by reading its tree facts and applying the language-specific bind function. Results are cached; if an error occurs during reading, the file is marked as binding nothing and the error is logged.

## `_bind_name()`

Internal helper that binds a name from an import statement to what it refers to, adding it to the file's binding maps based on whether it is bound for the whole file or a specific line range, and whether it should be passed on to files that import this file.

## `_bind_module()`

Read Python import statements and populate a file's bindings: `import a.b.c` binds intermediate packages if they are project files, `from m import n` binds the name or the module if the file has no script, and `from m import *` takes names from the module's `__all__` or public names. Statements written inside functions bind their names for those function's lines.

## `_bind_export()`

Read JavaScript/TypeScript import and export statements: import statements bind their module and names to what they refer to, export statements with sources pass names on to importing files, `export *` and `module.exports = require(...)` pass all names or the default export, and `require(...).member()` marks the access as a usage. Statements in function scopes bind for those lines.

## `_bind_include()`

Read C/C++ `#include` directives and bind every name from each included file as a wildcard import. Also extract the line ranges of functions defined as class members outside the class and classes themselves, storing them for member lookup.

## `_bind_path()`

Read Rust `use` declarations and expand them to bound names and modules using `rust_import_name_dict()`, handling paths and inline modules that bind names for specific line ranges. Enum variants taken over by `use Enum::*` are tracked separately.

## `_bind_path_import()`

Process a single Rust use declaration or path, resolving its module and determining which names it binds, adding them to the file's binding maps with appropriate line ranges and distinguishing between whole-file bindings and range-specific bindings.

## `_bind_package()`

Read Java and Kotlin import statements and bind names to types and members of packages: `import a.b.C` binds the type, `import a.b.*` binds all top-level names of package files, and `import static a.b.C.m` binds the member. Members of types are looked up from top-level definitions.

## `_package_index()`

Build and cache a mapping from package names (or directory paths for files without packages) to the files in each package by reading package statements from files with "package" bind kind; used to resolve package imports in Java and Kotlin.

## `_package_file_list()`

Return the files belonging to a named package by looking them up in the package index, returning an empty list if the package does not exist.

## `_resolve_qualified_name()`

Resolve a Java/Kotlin qualified name from an import statement to a definition by finding the longest leading part that matches a package, then looking up the next part among top-level names of that package's files, preferring files named after the type and nearest to the importing file.

## `_member_name_list()`

Return the names of definitions written inside a type (such as inner classes or static members) by finding container definitions matching the type name and collecting definitions nested within them.

## `_implicit_file_list()`

Return files whose names are visible in a file without an import statement based on language-specific implicit visibility: Java/Kotlin files in the same package, or SQL files in the same language.

## `_module_part_file()`

When a Python file is a package's `__init__.py`, return the file of the submodule with a given name by resolving a relative import, used to handle `from package import submodule` when the package has no direct binding.

## `_take_over_file_list()`

Return a file and all files it takes over as a whole (via `from m import *`, `#include`, `export *`, or `use super::*`) recursively, each appearing once in traversal order; used to compute which files' public names should be visible in a file.

## `_direct_name_dict()`

Return the names a file can directly pass on to importing files: the export re-exports and local exports, its top-level definitions, and its import bindings, in that priority order; for files with "package" or "own" bind kind, only top-level definitions are included.

## `_visible_name_dict()`

Return all names visible in a file by merging the direct names of the file and all files it takes over as a whole, with the first occurrence of each name kept; used to resolve re-exports and follow names to their definitions.

## `star_name_set()`

Return the names that `from file import *` takes from a file by computing the union of the file's top-level names, import bindings, and names taken from its wildcard imports, excluding names starting with `"_"`, or the names listed in `__all__` if present. Results are cached and synchronized across files that take over one another.

## `_export_name_dict()`

Return the names a file exports to importing files, each followed to the file and definition that ultimately provides it by calling `_follow()` on each visible name; cached per file.

## `_follow()`

Follow a binding through files that re-export it to the file that defines it, stepping through `_visible_name_dict()` at each file until reaching a name the file defines itself, returning a module file when a name is not found but the file is a Python package with that submodule, or stopping at a circular reference. Used to determine the source of every exported name.

## `member_binding()`

Given a binding that refers to a file and a list of parts to traverse within that file, follow the parts through the file's exports to find nested definitions, returning the binding reached and any remaining parts, handling module files and looking up names in export dictionaries.

## `default_binding()`

Return the binding of the default export (CommonJS or ES6) of a JavaScript/TypeScript file by looking up `DEFAULT_EXPORT_NAME` in the file's export names, returning `None` for non-JavaScript files or files with no default export.

## `_implement_index()`

Build and cache a mapping from function names to the files that define them, scanning all files with "include" bind kind and indexing function definitions with each possible prefix of their scoped names; used to find implementations of functions declared in headers.

## `implement_file_list()`

Return other files that provide implementations of a definition, used for C/C++ function implementations and Rust impl block members; results are cached per file and name and delegated to language-specific find functions.

## `_find_implement_file_list()`

For a C/C++ file, return the source files that define a function the file only declares, by looking up the declaration's name in the implement index and checking that the source files include the header.

## `_base_name_list()`

Return the names of base classes for a C++ class by looking up the class name in the file's base class mapping extracted from the syntax tree.

## `_class_member_binding_dict()`

Return the accessible members of a C++ class (including inherited members from base classes in order) as a dictionary mapping member names to their bindings, cached per class; members of the class itself take precedence over inherited members of the same name.

## `member_scope_list()`

Return the line ranges of C++ functions defined as class members outside the class and classes themselves, along with the members visible by name alone in each range, filtering to include only members from other files.

## `inherit_binding()`

When a C++ usage refers to a member that only a base class defines, return the binding of that member in the base class; otherwise return the argument bindings unchanged. Used to redirect member accesses to the correct base class definition.

## `_impl_member_index()`

Build and cache a mapping from `"Type::member"` strings to the files with impl blocks of the type that define the member, scanning all files with "path" bind kind (Rust); used to find impl blocks that extend a type.

## `_find_impl_member_file_list()`

For a Rust file, return other files whose impl blocks define members of a type, by looking up the member in the impl index and checking that those files import the type.

## `symbol_dict()`

Return the names that can be written in a file by importing or implicitly (without an import statement), each mapped to the binding of the definition that provides it. This is the primary public method for looking up what a name refers to in a file, excluding names the file defines itself.

## `scope_binding_list()`

Return the names bound for specific line ranges in a file (import statements in functions, Rust use declarations in blocks) and the definitions they refer to, deduplicated and followed to their sources. Used to determine which names are available at a given line inside a scope.

## `module_scope_list()`

Return the line ranges of inline modules (Rust `mod name { ... }`) and whether each inherits the names of the module around it via `use super::*`, returning an empty list for non-Rust files.

## `use_list()`

Return the names accessed through member operations on imports (JavaScript `require(...).method()`) that should be treated as usages, paired with their line numbers.

## `import_file_set()`

Return the project files that the import statements of a file resolve to, used to determine import dependencies.

## `import_line_dict()`

Return a mapping from each name an import binds to the line numbers of the statements that bind it, optionally filtered to only statements that bind for the whole file; used to locate import statements.

## `_module_tree_fact()`

Internal function that extracts Python `__all__` declarations from a file's syntax tree into a `_TreeFact`.

## `_export_tree_fact()`

Internal function that extracts JavaScript/TypeScript local and module exports from a file's syntax tree into a `_TreeFact`.

## `_include_tree_fact()`

Internal function that extracts C++ base class mappings from a file's syntax tree into a `_TreeFact`.

## `_path_tree_fact()`

Internal function that extracts Rust inline module scopes from a file's syntax tree into a `_TreeFact`.

## `_package_tree_fact()`

Internal function that extracts Java/Kotlin package declarations from a file's syntax tree into a `_TreeFact`.

## `_TREE_FACT_FUNCTION_DICT`

Mapping from bind kind to the function that extracts language-specific facts needed for binding from a file's syntax tree, allowing each language to contribute custom data to `_TreeFact` during parsing.

## `_BIND_FUNCTION_DICT`

Mapping from bind kind to the method that reads and processes import statements for a language, populating a file's `_FileBinding` with the names it binds and their targets.

## `_IMPLEMENT_FUNCTION_DICT`

Mapping from bind kind to the method that finds additional files implementing definitions from a file, supporting C/C++ function declarations and Rust impl blocks.

# Summary

# Summary: codetwine/import_binding.py

**Single Responsibility**: Maps names bound by import statements to their definitions by following imports and re-exports through project files until reaching the files that define them.

**Main Public Definitions**: `ImportBinder` (main class managing import resolution per project), `SymbolBinding` (file path and optional name within that file), `ScopeBinding` (name bound for specific line ranges), `import_binder()` (returns cached binder for project), `symbol_dict()` (names importable in a file), `scope_binding_list()` (names bound in line ranges), `default_binding()` (JavaScript default export), `member_scope_list()` (C++ member visibility), `implement_file_list()` (implementations in other files).

**Key Concepts**: Import and export statement processing; transitive name resolution through re-exports; language-specific binding for Python (modules, `__all__`), JavaScript (exports, CommonJS), C/C++ (includes, base classes), Rust (use paths, impl blocks), Java/Kotlin (packages, static members); per-file and per-project caching; partial recovery from parse errors.
