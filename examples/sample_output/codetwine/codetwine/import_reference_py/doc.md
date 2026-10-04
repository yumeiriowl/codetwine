# Design Document: codetwine/import_reference.py

# Design Specification

**Overview**

Resolve each symbol reference in a source file to the definition it names by tracing import statements and following bindings through project files.

- Call `import_reference_target_list()` to get a list of `ImportReferenceTarget` objects, each mapping a usage to the file and name where its definition resides.
- Call `clear_import_reference_cache()` when the project file set changes to invalidate cached resolution results.
- Use `ImportReferenceTarget` to access the usage name, line number, target file path, and definition name of each resolved reference.

This file depends on the import binding infrastructure (`ImportBinder` from `codetwine/import_binding.py`) to map import-statement-bound names to their definitions, the definition extraction machinery (`DefinitionInfo`, `extract_definitions()` from `codetwine/extractors/definitions.py`) to identify and classify definitions by type, the usage extraction system (`UsageInfo`, `extract_usages()` from `codetwine/extractors/usages.py`) to find symbol references in code, and the syntax tree parser (`parse_file()` from `codetwine/parsers/ts_parser.py`) to read and parse source files. The file is used by `codetwine/extractors/usage_analysis.py` to populate reference metadata and by `codetwine/reference_target.py` as the import-language implementation of the polymorphic reference resolution interface.

Resolution results are cached in `import_target_cache` keyed by absolute file path and associated with the project file set they were built for; the cache is invalidated when the project file set changes or when `clear_import_reference_cache()` is called. A name an import statement binds on specific lines (scope binding) takes precedence over whole-file bindings and visibility from outside the project; names defined inside closed Rust inline modules without `use super::*` are hidden from outer scopes; C++ members accessed by name alone inside a function defined outside its class are resolved to class members before import-bound names.

**Definitions**

## `ImportReferenceTarget`

A dataclass holding the resolution of a single symbol usage: its name as written in the file, the line it appears on, the relative path of the file containing the definition, and the fully-qualified name used to look up that definition in the target file. Used by callers to understand what definition a usage refers to and to locate the source text of that definition.

## `_own_name_set`

Computes the set of names a file defines at its top level apart from import bindings, excluding names in `ATTACHED_DEFINITION_TYPE_SET` and `TRANSPARENT_DEFINITION_TYPE_SET`, names shadowed by file-wide import bindings, and names that appear only inside other definitions. Returns the union of top-level and nested definitions that pass the `is_own` filter. Called during reference resolution to distinguish definitions the file owns from those imported from other files.

## `_import_target_list`

Builds the resolution targets for a symbol usage whose root name is bound by an import statement, following module members through `ImportBinder.member_binding()`, resolving to the default export when a binding names a file, and including implementation files from `ImportBinder.implement_file_list()`. Returns one primary target and zero or more secondary targets for function implementations (C/C++) or impl-block definitions (Rust). Called when a usage starts with an imported name to determine all files that define the symbol.

## `_scope_binding`

Finds the innermost scope-binding (a name bound for a range of lines) that applies to a usage by matching its line number and checking that the bound name is a leading part of the usage name. Returns the binding with the smallest line range, then the longest name, breaking ties by appearance order; returns None if no scope binding matches. Used to give import statements written inside functions precedence over file-wide import bindings.

## `_member_scope_dict`

Reorganizes the member scope list from `ImportBinder.member_scope_list()` into a dictionary keyed by member name, mapping each name to a list of (start line, end line, binding) tuples. Called once during reference resolution to enable efficient lookup of which members are accessible by name alone on a given line in a C++ function.

## `_member_scope_binding`

Finds the member accessible by its name alone on a given line inside a C++ function by selecting the member from the smallest enclosing line range. Returns the binding or None if no member with that name is accessible on that line. Used to resolve member access written without the class name inside a function defined outside its class.

## `_pattern_reference_function`

Builds a memoizing function that identifies whether a name written in a Rust pattern refers to a constant, static, struct, or variant (returning True) rather than binding a new name (returning False). Checks both the file's own definitions and bindings to project files, reading definition types from the language settings' `pattern_reference_types` and `pattern_variant_types`. Returns None for languages without pattern reference support. Called once per file to support tracking usages in match expressions and other patterns.

## `_closed_module_scope`

Finds the innermost Rust inline module around a line that does not have `use super::*`, meaning it closes the view of names from outside. Returns the (start line, end line) tuple of that module or None if all modules around the line take over outer names. Used to determine whether a top-level name is hidden from inside an inline module.

## `_module_visibility`

Determines whether a Rust inline module without `use super::*` hides the definition of a usage's root name from the usage's location. Returns `_INSIDE` if the name is defined inside the module, `_HIDDEN` if it is a top-level or imported name that the module closes off, or None if no module hides the name. Skips checks for module-path prefixes (crate::, self::, super::) and multi-part names. Called during resolution to enforce Rust's inline-module scope rules.

## `clear_import_reference_cache`

Clears all entries from `import_target_cache`, discarding all cached reference resolutions across all files and projects. Called when the set of project files changes or when the cache must be invalidated.

## `_usage_list`

Extracts and deduplicates all symbol usages from a file's AST, filtering by tracked names, resolving typed aliases (variables declared with a tracked type and variables assigned tracked type instances), and excluding names bound locally inside scopes. Replaces each usage name with its type name when the name is aliased to a tracked type. Returns usages in source order without duplicates. Called to collect all the symbol references in a file that need to be resolved to definitions.

## `import_reference_target_list`

Resolves every symbol usage in a file to the definition it refers to by consulting cached results or by reconstructing the resolution from import statements, scope bindings, module visibility rules, member scope lookups, and the file's own definitions. Caches results keyed by absolute file path along with the project file set they were built for. Returns one `ImportReferenceTarget` per usage in source order, with the target's file and definition name indicating where the definition resides. The primary entry point for external callers and the core of the reference resolution system.

# Summary

# Summary: import_reference.py

**Single Responsibility:** Resolve symbol usages in source files to their definitions by tracing import statements and following bindings through the project.

**Main Public Definitions:**
- `ImportReferenceTarget`: Dataclass mapping a usage to its definition's file and name
- `import_reference_target_list()`: Core entry point returning resolved targets for all usages in a file
- `clear_import_reference_cache()`: Invalidates cached resolution results

**Key Terms:** Import binding resolution, scope bindings, member access in C++, Rust inline-module visibility, pattern references, definition extraction, usage extraction, caching by file path and project file set.
