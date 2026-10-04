# Design Document: codetwine/extractors/imports.py

# Design Specification

**Overview**

Extract import and export statements from source code syntax trees across multiple languages, representing them as structured metadata that tracks module sources, imported names, aliases, scoping, and re-exports.

This file is used to:
- Call `extract_imports()` to parse import statements from a file's AST and obtain a list of `ImportInfo` objects containing the module, names, line numbers, aliases, and scope information needed for dependency resolution.
- Call `local_export_dict()` to retrieve the names a JavaScript/TypeScript file exports under different names than they are defined with, enabling re-export tracking.
- Call `module_export_list()` to identify which modules a JavaScript/TypeScript file passes through as its own default export, for transitive dependency analysis.
- Call `python_all_name_list()` to read the `__all__` variable of a Python file, which defines the public API for `from module import *` statements.
- Call `cobol_module()` and `cobol_module_part()` to convert between structured COBOL COPY and CALL statement data and their canonical string representation.

The file supports the import binding system by providing structured metadata about imports that `import_binding.py` uses to resolve module references and bind imported names to their definitions. It relies on `cobol_source.py` to parse COBOL files into structured import lists, `definitions.py` to identify CommonJS export patterns and require() calls, and `rust_path.py` to expand Rust use declarations into individual imports. The file is consumed by `import_binding.py` (which binds imported names), `dependency_graph.py` (which resolves module paths), and `cobol_file_index.py` (which resolves COBOL module references).

Query results are cached per language and query string to avoid recompiling tree-sitter queries on repeated calls; import statements with multiple captured names from the same source are consolidated into a single `ImportInfo` entry; aliases and original names are tracked separately to distinguish between how a name is used in code and what it refers to in its source module.

**Definitions**

## `ImportInfo`

Dataclass storing the complete metadata of a single import statement: the module being imported, the names brought into scope, the line number, and optional fields for module aliases (import X as Y), name aliases (from X import a as b), scope boundaries where names are bound, export status for re-exports, and usage status for member accesses. The `scope_line_tuple` field stores the line range of the innermost containing function or block, enabling scope-aware name binding; `is_export` marks JavaScript/TypeScript export statements with a source as re-exports whose names are passed to dependent files; `is_use` marks member accesses like `require("./m").run()` that both bind and use a name on the same lines.

## `cobol_module`

Construct the standard string representation of a COBOL COPY or CALL statement from its kind, name, and optional library, formatting it as "kind name" or "kind name OF library" as appropriate; inverse of `cobol_module_part()` and used by `cobol_file_index.py` to create module identifiers for COBOL import resolution.

## `cobol_module_part`

Split a COBOL module string (created by `cobol_module()`) into its components (kind, name, library) to extract the statement type and operands; used by `cobol_file_index.py` when resolving COPY and CALL statements to file paths, with special handling for the COPY kind to separate library names by the "OF" keyword.

## `_cobol_import_list`

Convert COBOL COPY and CALL statements stored in a `CobolSource` into `ImportInfo` objects, de-duplicating statements on the same line and setting names to ["*"] for COPY statements (indicating all copied names) and [name] for CALL statements; called from `extract_imports()` when processing COBOL files.

## `_line_tuple`

Extract the first and last line numbers (1-based) from a syntax tree node's position metadata; used throughout the file to record scope and statement boundaries in `ImportInfo.scope_line_tuple`.

## `_scope_line_tuple`

Determine the line range of the scope an import statement is bound for by traversing parent nodes to find the innermost function, lambda, or block; returns the statement's own lines for export statements or None for module-level imports, supporting scope-aware name binding in `import_binding.py`.

## `_member_use_import`

Construct an `ImportInfo` for a member access pattern like `require("./m").run()` where a module is loaded and a member is immediately accessed, marking it as a usage (`is_use=True`) bound to the lines of the access; returns None if the access is part of a variable declaration that binds a different name.

## `_callback_import`

Extract an `ImportInfo` from the first parameter of a dynamic import callback (e.g., `import("./m").then((m) => ...)`) which may be a simple identifier binding the whole module or an object pattern destructuring specific names; returns None if the callback has no parameter or an unsupported parameter type.

## `extract_imports`

Main entry point extracting all import statements from a file's AST, supporting multiple languages through tree-sitter queries that capture module sources, imported names, and aliases, with special handling for Rust use declarations (expanded per-name), JavaScript/TypeScript dynamic imports and member accesses, and COBOL COPY/CALL statements; consolidates multiple names from the same import statement into a single `ImportInfo` entry and respects optional scope type boundaries to record where names are bound.

## `_detect_module_alias`

Detect an alias assigned to a module (import X as Y) from the AST by checking for an alias field in Python's `aliased_import` node or an "as" keyword followed by an identifier in Kotlin's import node; used to populate `ImportInfo.module_alias` when a module is renamed on import.

## `_resolve_imported_name`

Extract the name actually used in code from a `@name` capture, returning the alias if one exists (e.g., "path_join" from "from X import join as path_join"); handles Python `aliased_import` nodes and JavaScript/TypeScript `import_specifier` / `export_specifier` nodes that hold alias fields.

## `_get_original_name`

Retrieve the definition name before aliasing from a `@name` capture, returning None if no alias exists; used to populate `ImportInfo.alias_map` with the mapping from alias names (as used in code) to original names (as defined in the module), supporting languages with renaming syntax like `import { useState as useMyState }`.

## `module_export_list`

Scan a JavaScript/TypeScript file's expression statements for CommonJS export assignments (module.exports = require(...)) and return the list of module strings being re-exported as the file's default export; used by `import_binding.py` for transitive dependency tracking.

## `_string_list`

Extract string constants from a Python list, tuple, or parenthesized expression recursively, returning them in order or None if the node contains anything other than plain string constants; used to read the values of `__all__` assignments and method calls.

## `python_all_name_list`

Parse all top-level assignments and method calls to Python's `__all__` variable (including `__all__ = [...]`, `__all__ += [...]`, `__all__.extend(...)`, `__all__.append(...)`) and return the names listed in order without duplicates, or None if `__all__` is not defined or contains non-constant expressions; used by `import_binding.py` to determine the public API of Python modules for `from module import *`.

## `local_export_dict`

Build a mapping from exported names to the names they are defined as in a JavaScript/TypeScript file, covering ES6 export statements (export { p as q }), CommonJS assignments (module.exports = { q: p }), and default exports (export default p); used by `import_binding.py` to track re-exports and aliased exports.

## `_strip_quotes`

Remove surrounding quotes or angle brackets from a module name string, handling JavaScript/TypeScript quotes (single and double), C/C++ angle brackets, and returning the text unchanged if not quoted; called after capturing the module name from a query to normalize it for use as a module identifier.

## `_WILDCARD_NODE_TYPE_SET`

Set of AST node types that represent a "import all" declaration (asterisk in Java, * in Kotlin/JavaScript/TypeScript, wildcard_import in Python); used to detect and mark imports with the name "*" to indicate all names from a module are imported.

## `_EXPORT_STATEMENT_TYPE`

String constant "export_statement" naming the JavaScript/TypeScript AST node type for export statements; used to identify re-export statements and set their scope to their own lines rather than an enclosing function.

## `_PYTHON_ALL_NAME`

Bytes literal `b"__all__"` representing the Python magic variable name that defines a module's public API; used when comparing node text in `python_all_name_list()` to identify assignments to `__all__`.

## `_PYTHON_ALL_METHOD_SET`

Set of bytes constants {b"extend", b"append"} naming the methods that add names to Python's `__all__`; used to recognize method calls on `__all__` as part of the public API definition.

## `_PYTHON_STRING_TYPE`

String constant "string" naming the AST node type for Python string literals; used to identify and extract string content when reading `__all__` values.

## `_PYTHON_SEQUENCE_TYPE_SET`

Set of string constants {"list", "tuple", "parenthesized_expression"} naming Python AST node types that can hold sequences of values; used to recursively parse containers when reading `__all__` definitions.

## `_query_cache`

Module-level dictionary caching compiled tree-sitter `Query` objects by (language id, query string) tuple to avoid recompiling queries when `extract_imports()` is called multiple times with the same language and query; populated lazily on first use and shared across all calls.

# Summary

# Summary: codetwine/extractors/imports.py

**Single Responsibility:** Extract and structure import/export statements from source code ASTs across multiple languages, providing metadata for dependency resolution and name binding.

**Main Public Functions:**
- `extract_imports()` — parse import statements from file ASTs into ImportInfo objects
- `local_export_dict()` — map exported names to their definitions in JavaScript/TypeScript
- `module_export_list()` — identify modules passed through as default exports
- `python_all_name_list()` — read Python `__all__` public API definitions
- `cobol_module()` / `cobol_module_part()` — convert COBOL COPY/CALL statements to/from canonical strings

**Key Concepts:** import statements, module sources, imported names, aliases, re-exports, scope binding, CommonJS and ES6 patterns, COBOL COPY/CALL, dynamic imports, member accesses, `__all__` declarations, tree-sitter queries, AST node processing, name resolution across Python, JavaScript, TypeScript, Rust, COBOL, Java, and Kotlin.
