# Design Document: codetwine/import_binding.py

# Design Specification

**Overview**

Map the names that import statements bind in source files across a multi-language project to their definitions by following re-exports, package boundaries, and language-specific scoping rules.

When a developer needs to resolve where a name comes from:
- Call `import_binder()` with project metadata to obtain an `ImportBinder` instance (cached per project file set), then call methods like `symbol_dict()` to retrieve the definitions accessible to a file through its imports.
- Call `scope_binding_list()` to get names bound for specific line ranges (for imports inside functions or blocks) and their definitions, used when a usage appears on a particular line.
- Call `default_binding()` to resolve JavaScript/TypeScript default exports and `member_binding()` to follow dotted or scope-qualified accesses after an import binds a module.
- Call `import_file_set()` to discover which project files an import statement resolves to, and `import_line_dict()` to map imported names back to their import statement line numbers.
- Call `implement_file_list()` to find where a C++/Rust member is defined when a header only declares it or when an impl block extends a type from another file.

The file is the central dependency resolution engine: it relies on `codetwine/extractors/` modules (imports, definitions, usages) to parse individual files, on `codetwine/import_to_path.py` to resolve module strings to file paths, and on `codetwine/rust_module_tree.py` and language-specific extractors (Rust paths, COBOL) for specialized resolution. The `codetwine/import_reference.py` module consumes its `SymbolBinding` data class and binder methods to build usage-to-definition targets, and `codetwine/extractors/dependency_graph.py` uses it to compute import relationships.

The binder caches results per file and project to avoid re-reading syntax trees; all caching is keyed by project file set, invalidated by `clear_import_binder_cache()` when the project changes. Exceptions during import or definition parsing are logged as warnings, and the affected file is treated as having no bindings rather than failing the entire analysis, allowing partial results for projects with syntax errors or missing dependencies.

**Definitions**

## `SymbolBinding`

A pairing of a project file and an optional definition name that represents what an import statement or export binds; used throughout the binder to track where a name leads and whether it names a specific definition or an entire file/module. When `name` is `None`, the binding stands for the file as a whole (a module namespace); when `name` is present, it identifies a specific definition within that file.

## `ScopeBinding`

Associates a bound name with a line range (function body, block, or statement) and its definition, used for import statements that do not bind for the entire file (Python imports inside functions, JavaScript dynamic imports, Rust use declarations in blocks). The `binding` field is `None` when the name is bound but does not resolve to a project definition.

## `_FileBinding`

Internal data structure capturing what import and export statements of a single file write as bindings: names for the whole file, re-exports without binding, wildcard imports from other files, and scope-limited bindings. It also tracks which files the import statements resolve to and which definitions are members of classes for C++ member scoping.

## `_TreeFact`

Internal data structure holding language-specific facts extracted from a file's syntax tree in a single parse: import statements, package declarations, Python `__all__` lists, JavaScript export aliases, C++ base class names, and Rust inline module boundaries. Built once per file and reused to avoid re-parsing.

## `clear_import_binder_cache`

Discard all cached binders across all projects; called when project configuration or file sets change to force rebuilding on next use.

## `import_binder`

Return the `ImportBinder` for a project, creating it once per unique project file set and caching it until the file set changes or the cache is cleared. This is the entry point for obtaining a binder to query symbol bindings.

## `_join_module`

Join a module path string and a name into a single module path following Python semantics (e.g., `"pkg"` + `"core"` becomes `"pkg.core"`); used when resolving `from module import name` to check if `name` is itself a module.

## `_package_name`

Extract the package name declared by a Java or Kotlin file from its syntax tree (the `package` statement), returning `None` if no package is declared.

## `_base_class_name_dict`

Extract the base class names of each class in a C++ file by walking the syntax tree and matching class definitions with their base class clauses, returning a dictionary mapping class names to lists of their base classes without namespaces or template arguments.

## `ImportBinder`

The main binder class that reads import and export statements from project files, resolves them to target files, and follows re-exports to find definitions. It manages caches for file bindings, symbol visibility, exports, packages (Java/Kotlin), function implementations (C/C++), and class members, supporting multi-language resolution with language-specific bind strategies.

## `ImportBinder.__init__`

Initialize a binder for a project by storing the project directory and file set, detecting source roots for the language-specific resolution strategies, and setting up internal caches. Called once per project file set by `import_binder()`.

## `_file_ext`

Return the file extension (language identifier) of a project file as determined by `language_ext()`, used throughout to look up language-specific settings.

## `_bind_kind`

Look up the `"bind"` value from the import resolve configuration of a file's language (e.g., `"module"` for Python, `"export"` for JavaScript, `"include"` for C/C++), determining which binding strategy applies; returns `None` if the file's language has no import resolve configuration.

## `top_level_name_list`

Return the outermost definition names of a file using `top_level_definition_names()`, cached once per file; logs a warning and returns an empty list if the file cannot be parsed.

## `_tree_fact`

Parse a file once and extract language-specific facts (imports, package names, exports, base classes, inline modules) into a `_TreeFact`, reading the syntax tree a single time but extracting multiple pieces of information to avoid re-parsing.

## `_definition_list`

Return the definitions of a file by calling `file_definition_list()` with the file's language-specific definition dictionary.

## `_resolve`

Resolve a module string written in a file to a project file path using `resolve_module_to_project_path()` with the binder's source roots and project context.

## `_file_binding`

Read and cache the import and export bindings of a single file once by selecting the appropriate bind function based on the file's language and calling it with the file's import statements and language facts. Registered in the cache before being filled to handle circular references (a file that imports itself).

## `_bind_name`

Helper for language-specific bind functions to bind a single imported or exported name, either for the whole file or for a line range (scope binding), with logic to skip `None` bindings and pass through export statements.

## `_bind_module`

Python-specific binding strategy: process `import`, `from import`, and `from import *` statements, resolving modules to files, binding names to definitions or modules, and tracking the file's `__all__` list.

## `_bind_export`

JavaScript/TypeScript-specific binding strategy: process `import`, `require()`, `export`, and `export *` statements, binding names to definitions or modules, tracking local re-exports and module exports, and recording where names are used (require member access, dynamic import callbacks).

## `_bind_include`

C/C++-specific binding strategy: process `#include` directives to mark every included file as passing on all names, and extract member scopes (functions and classes) to enable member lookup in C++ files that define members of classes outside the class body.

## `_bind_path`

Rust-specific binding strategy: process `use` declarations and paths, resolving them through the Rust module tree, binding names to definitions with scope awareness (whole file or block/inline module), and handling enum variants and wildcard imports.

## `_bind_path_import`

Helper for `_bind_path` processing a single Rust `use` declaration or path, resolving the module, binding imported names, and returning variants of enums that the import takes over for a line range (to be added to scope bindings).

## `_bind_package`

Java/Kotlin-specific binding strategy: process `import` statements to resolve qualified names to types and members, handling wildcard imports, `import static` for members, and building a symbol dictionary for the file.

## `_package_index`

Build once a dictionary mapping package names (and `/` + directory for files without a package statement) to the files that declare them, reading package statements from each file's syntax tree.

## `_package_file_list`

Return the files of a Java or Kotlin package in path order by looking them up in `_package_index()`.

## `_resolve_qualified_name`

Resolve a qualified name (e.g., `["com", "acme", "model", "User"]`) written in a Java/Kotlin import to a file and definition path by finding the longest leading part that is a package, then looking up the next part among the top-level names of that package's files, with preference for files named like the part.

## `_member_name_list`

Return the names of definitions nested inside a container type (class, interface) of a file, used for Java/Kotlin `import static Type.*` to enumerate members.

## `_implicit_file_list`

Return the files whose names a file can write without an import statement based on implicit visibility rules: same package (Java/Kotlin) or same language (SQL).

## `_module_part_file`

Return the file of a Python module inside a package by resolving `"." + name` relative to the package's `__init__.py`, used when an import names a module that has no script of its own.

## `_take_over_file_list`

Return a file and all files it recursively takes over as a whole (wildcard imports, includes, re-exports), visited in breadth-first order, used to compute transitive visibility. Files whose `"bind"` is in `_OWN_NAME_EXPORT_BIND_SET` take over no files.

## `_direct_name_dict`

Return the names another file can import directly from a file as they are written (not followed further): re-exports, top-level definitions, export aliases, and whole-file imports, in priority order. For `"bind"` values in `_OWN_NAME_EXPORT_BIND_SET`, only top-level definitions are returned.

## `_visible_name_dict`

Return every name visible from a file, including names from files it takes over as a whole, each followed only one level (not to the definition). Built once per file by merging `_direct_name_dict()` over `_take_over_file_list()`, with the first file defining a name winning.

## `star_name_set`

Return the names `from file import *` takes from a Python file: the `__all__` list if present, else the file's definitions and imported names (excluding those starting with `_`), with transitive closure over files that take each other over. Used to compute what wildcard imports provide in Python and languages with similar semantics.

## `_export_name_dict`

Return every name visible from a file, each followed to the definition it ultimately binds to, used when another file imports from this one. Built once per file by following each binding in `_visible_name_dict()` to its definition via `_follow()`.

## `_follow`

Follow a binding through re-export chains to the file that defines it: each step looks up the name in the file the binding names, stopping when a definition is found, when the name is not found (and is not a Python submodule), when a cycle is detected, or when the binding refers to the file itself. Bindings that stand for modules (name is `None`) are returned unchanged.

## `member_binding`

Follow the parts written after a name that stands for a module (e.g., `package.module.Name.method`) by recursively looking up each part in the module's exports, stopping when a part is not found or when a definition is reached. Returns the binding and any parts that could not be followed.

## `default_binding`

Return the definition the default export of a JavaScript/TypeScript file binds to by looking it up in the export names, returning `None` for non-export files or files with no default export.

## `_implement_index`

Build once a dictionary mapping function names to files that define them, read from C/C++ files by walking their definitions and handling members defined outside their class (using multiple lookup keys for different scope depths).

## `implement_file_list`

Return the files that define a member or function that a file only declares (C/C++ function declarations, Rust impl members), looked up once per file and name using language-specific find functions.

## `_find_implement_file_list`

Look up the C/C++ files that define a function declared in a header by checking the implementation index, filtering for files that include the header, and handling members defined outside their class.

## `_base_name_list`

Return the base class names of a C++ class of a file by extracting them from the file's syntax tree.

## `_class_member_binding_dict`

Return the members of a C++ class (from the class itself and its base classes) as bindings, built once per class with breadth-first traversal of the base class hierarchy. A member of the class itself shadows a member of the same name from a base class.

## `member_scope_list`

Return the line ranges and member dictionaries for C++ functions defined outside their class and class bodies in a file, enabling name lookup of class members by name alone within those scopes.

## `inherit_binding`

Resolve a member access on a C++ class to the member of a base class when appropriate (e.g., `Comment.Value` becomes `Node.Value` when `Node` is a base of `Comment` and defines `Value`), used when a definition lies in a base class.

## `_impl_member_index`

Build once a dictionary mapping `"Type::member"` to files with impl blocks that define the member, read from Rust files by walking definitions within impl items.

## `_find_impl_member_file_list`

Look up the Rust files with impl blocks that define a member of a type of a file, checking that the files bind the type and finding the member in their implementation index.

## `symbol_dict`

Return the names a file can write that come from other project files: import bindings, implicit visibility names, and transitive names from wildcard imports, each followed to its definition. Used to resolve usage names to where they ultimately come from, excluding names the file defines at its top level.

## `scope_binding_list`

Return names bound for specific line ranges (import statements in functions, Rust use declarations in blocks, JavaScript export statements) and their definitions, used to resolve usages that appear on particular lines where scope-limited imports apply.

## `module_scope_list`

Return the line ranges and scope-inheritance flags of Rust inline modules (blocks and `use super::*` declarations), used for scope-aware name lookup in Rust.

## `use_list`

Return the names where import statements are used directly (JavaScript `require("./m").method()`), recorded as (name, line) pairs for identifying usage locations that are also import accesses.

## `import_file_set`

Return the project files that the import statements of a file resolve to, used to compute import dependencies.

## `import_line_dict`

Return a mapping from names to the line numbers of the import statements that bind them, distinguishing between whole-file imports and scope-limited imports via the `is_whole_file` parameter.

## `_module_tree_fact`

Extract Python `__all__` declarations from a file's syntax tree into a `_TreeFact`.

## `_export_tree_fact`

Extract JavaScript/TypeScript local export aliases and module exports from a file's syntax tree into a `_TreeFact`.

## `_include_tree_fact`

Extract C++ base class names from a file's syntax tree into a `_TreeFact`.

## `_path_tree_fact`

Extract Rust inline module boundaries from a file's syntax tree into a `_TreeFact`.

## `_package_tree_fact`

Extract Java/Kotlin package declarations from a file's syntax tree into a `_TreeFact`.

## `_TREE_FACT_FUNCTION_DICT`

Dictionary mapping bind strategy values (`"module"`, `"export"`, `"include"`, `"path"`, `"package"`) to functions that extract language-specific facts from syntax trees.

## `_BIND_FUNCTION_DICT`

Dictionary mapping bind strategy values to the language-specific binding methods that process import statements and build file bindings.

## `_IMPLEMENT_FUNCTION_DICT`

Dictionary mapping bind strategy values (`"include"`, `"path"`) to the methods that find files with implementations or impl members of declarations.

# Summary

# Summary: codetwine/import_binding.py

**Responsibility:** Map import statement bindings to their definitions across multi-language projects by resolving module paths, following re-exports, and applying language-specific scoping rules.

**Main Public API:** `import_binder()` returns a cached `ImportBinder` instance; `SymbolBinding` pairs files with optional definition names; `ScopeBinding` associates names with line ranges and definitions.

**Key Methods:** `symbol_dict()` retrieves names accessible via imports; `scope_binding_list()` handles scope-limited bindings (functions, blocks); `member_binding()` and `default_binding()` resolve dotted accesses and default exports; `import_file_set()` discovers resolved files; `implement_file_list()` finds C++/Rust implementations.

**Key Concepts:** Language-specific bind strategies (Python modules, JavaScript exports, C++ includes, Rust paths, Java packages); transitive visibility through re-exports and wildcard imports; member scoping for C++ classes and Rust impl blocks; caching per project file set with `clear_import_binder_cache()` for invalidation.
