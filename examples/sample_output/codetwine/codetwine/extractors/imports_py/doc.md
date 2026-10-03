# Design Document: codetwine/extractors/imports.py

# Design Specification

**Overview**

Extract import statements from source code ASTs and COBOL files into structured ImportInfo objects, enabling dependency analysis across multiple programming languages.

- Call `extract_imports()` with a syntax tree root node, language, and query string to retrieve all imports as a list of ImportInfo objects with module names, imported names, line numbers, and alias mappings.
- Call `module_export_list()` on a JS/TS syntax tree to get the list of required modules that a file re-exports as its default export.
- Call `local_export_dict()` on a JS/TS syntax tree to retrieve a mapping of exported names to their original names in the file, handling both ES module and CommonJS patterns.
- Call `python_all_name_list()` on a Python syntax tree to extract the list of public names declared in `__all__`, supporting assignment, augmented assignment, and method calls.
- Call `cobol_module()` and `cobol_module_part()` to convert between COBOL import statements and their string representation with kind, name, and library components.

This file relies on `cobol_source.py` to provide structured COBOL import data (COPY and CALL statements) from parsed files, `definitions.py` for identifying CommonJS export patterns and require() calls in JS/TS, and `rust_path.py` for expanding Rust use declarations into individual imported names with scope awareness. The file is used by `import_binding.py` to bind imported names to their definitions and track export chains, by `dependency_graph.py` to resolve module paths into project files, and by `cobol_file_index.py` to resolve COBOL COPY and CALL module names to file paths.

Tree-sitter queries are cached by language and query string to avoid recompilation. For Rust, a use_cache_dict is threaded through recursive calls to avoid recomputing use declarations within blocks and inline modules. The file handles language-specific syntax differences uniformly through query patterns and post-processing: wildcard imports use a node type set, export statements are detected by type, Python `__all__` is reconstructed from constant strings only, and JS/TS callbacks bind parameter names for their scope lines rather than the whole file.

**Definitions**

## `ImportInfo`

Data class holding metadata about a single import statement: the source module name, list of imported names, line number, optional alias for the entire module, mapping of alias names to original names for "import as" patterns, scope lines where names are bound, whether it is a JS/TS export statement with a source, and whether it is a member access usage like `require("./m").run()`.

## `cobol_module`

Return the COBOL import string representation by joining kind, name, and optional library with "OF" for COPY statements; used to construct module identifiers for COBOL COPY and CALL statements that can be stored and parsed.

## `cobol_module_part`

Split a COBOL import string into kind, name, and library components; the inverse of `cobol_module()` that is called by `cobol_file_index.py` to resolve COPY and CALL modules to file paths.

## `_cobol_import_list`

Convert COBOL COPY and CALL statements from a CobolSource into ImportInfo objects, deduplicating statements on the same line by using a dictionary keyed on module and line number; COPY statements bind the wildcard name "*" while CALL statements bind the call target name.

## `_line_tuple`

Return the 1-based start and end line numbers of a syntax tree node; used throughout to record where import statements and their scopes span in the source file.

## `_scope_line_tuple`

Determine the scope lines where an import statement binds its names: for export statements with a source, the statement's own lines; for regular imports inside functions or lambdas, the innermost scope_types node's lines; or None for file-level bindings. Called during import processing to populate ImportInfo.scope_line_tuple.

## `_member_use_import`

Extract an import from a member access on a required module, like `require("./m").run()`, binding the member name "run" for the lines of the access itself; returns None when the access is the value in a variable declarator that binds a different name instead.

## `_callback_import`

Extract an import from the first parameter of a callback passed to `import("./m").then(...)`, binding the parameter name or destructured properties for the callback's scope lines; handles both simple identifiers and object patterns with shorthand and renamed properties.

## `extract_imports`

Main function that parses an AST or CobolSource using tree-sitter queries to extract all import statements; groups multiple names from the same statement into a single ImportInfo, handles Rust use declarations with scope caching, detects module aliases and wildcard imports, processes JS/TS member accesses and dynamic imports, and returns a list of ImportInfo objects in document order. Caches compiled queries by language and query string to avoid recompilation.

## `_detect_module_alias`

Detect the alias name from "import X as Y" patterns in Python (aliased_import node with an alias field) and Kotlin (import node with "as" followed by an identifier); called when processing @namespace_name captures to populate ImportInfo.module_alias.

## `_resolve_imported_name`

Get the name actually used in code from a @name capture, returning the alias name if one exists from "import a as b" or "import { a as b }" patterns; handles Python aliased_import nodes and JS/TS import/export specifiers with alias fields.

## `_get_original_name`

Get the original definition name from a @name capture before any aliasing was applied; returns None if there is no alias, indicating the name was not renamed. Used to populate ImportInfo.alias_map with the mapping from alias name to original name.

## `module_export_list`

Return the list of required modules that a JS/TS file passes on as its default export via `module.exports = require("./lib/express")`; scans only top-level expression statements for CommonJS export assignments and extracts the required module string.

## `_string_list`

Extract string constants from a Python list, tuple, or parenthesized expression node, returning them in order or None if the node contains non-string elements or non-constant expressions; used by `python_all_name_list()` to read the names in `__all__`.

## `python_all_name_list`

Extract the list of public names from a Python file's `__all__` declaration by scanning top-level statements for assignment, augmented assignment, and method calls (extend, append); returns None if `__all__` is missing or written with non-string constants, and deduplicates names using insertion order preservation.

## `local_export_dict`

Return a mapping of exported names to their original names in a JS/TS file, handling ES module `export { p as q }` and `export default`, CommonJS `module.exports = p` and `module.exports = { q: p }` patterns, and skipping export statements with a source (which are imports); called by `import_binding.py` to track re-exports.

## `_strip_quotes`

Remove quotes or angle brackets from a module name string, supporting JavaScript/TypeScript double/single quotes, C/C++ angle brackets, and returning unquoted strings unchanged; applied to all @module captures before storing the module string.

# Summary

# Summary: codetwine/extractors/imports.py

**Single Responsibility:** Extract and structure import statements from source code ASTs and COBOL files into ImportInfo objects to enable cross-language dependency analysis.

**Main Public Definitions:** `extract_imports()` parses syntax trees to retrieve imports with module names, imported names, line numbers, and aliases; `module_export_list()` and `local_export_dict()` handle JS/TS export patterns; `python_all_name_list()` extracts `__all__` declarations; `cobol_module()` and `cobol_module_part()` convert COBOL import string representations.

**Key Terms:** ImportInfo metadata, wildcard imports, module aliases, JS/TS member accesses and dynamic imports, Python `__all__` constants, COBOL COPY and CALL statements, Rust use declarations with scope caching, CommonJS and ES module patterns, re-exports, query caching by language.
