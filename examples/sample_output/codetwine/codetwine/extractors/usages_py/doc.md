# Design Document: codetwine/extractors/usages.py

# Design Specification

**Overview**

Extract and deduplicate symbol usage locations from source code ASTs, tracking where imported names are referenced in function calls, attribute access, type references, and qualified paths across multiple programming languages.

The file supports the following use cases:

- Call `extract_usages()` to retrieve all UsageInfo locations where imported names appear in code, filtering out local variable bindings and import statements, with support for language-specific node type configurations
- Call `extract_typed_aliases()` to find variables declared with tracked types and their scope boundaries, enabling type inference for usage tracking
- Call `extract_value_aliases()` to identify variables assigned objects of tracked types from their instantiation through their scope
- Call `deduplicate_usage_list()` to remove redundant entries when both a name and its qualified version appear on the same line
- Call helper functions like `symbol_part_list()`, `usage_root_name()`, and `typed_alias_type()` to analyze and resolve usage names in import binding and reference resolution

The file depends on `definitions.py` for pattern name extraction and declarator name resolution in typed declarations, `cobol_source.py` to handle COBOL file references, and `rust_path.py` to extract and normalize Rust paths and macro arguments. Multiple files use this module: `import_reference.py` calls `extract_usages()`, `extract_typed_aliases()`, `extract_value_aliases()`, and deduplication functions to build usage lists for dependency tracking; `definition_source.py` and `usage_analysis.py` use `symbol_part_list()` and `usage_root_name()` to resolve definition lookups; and `import_binding.py` uses `symbol_part_list()` to construct class member binding names.

The module caches computed local name sets per scope node to avoid reprocessing the same scope during traversal, and handles scope visibility through scope_body_dict to correctly interpret names in function parameter defaults and annotations that exist outside function scope. Duplicate (name, line) pairs are eliminated after traversal, and redundant shorter names are removed when a qualified version exists on the same line.

**Definitions**

## `UsageInfo`

Dataclass holding a single symbol usage location, with the name being used (potentially qualified with dots or double-colons) and its 1-based line number in the source file.

## `symbol_part_list`

Split a usage name into its parts at separator boundaries (either "." or "::"), used by callers to analyze multipart names and construct scope-qualified references.

## `_track_root`

Return the longest leading part of a usage name (up to a separator) that exists in a tracked name set, or None if no prefix is tracked; internal helper for determining the root name a usage refers to.

## `usage_root_name`

Return the tracked name that a usage starts from by finding the longest matching prefix or defaulting to the first part, used by import reference resolution to map usages back to their definition imports.

## `_UsageSetting`

Dataclass bundling language-specific node type configurations and per-file tracking state: names to track, member names for self/this references, typed variable aliases, import statement line ranges, computed local name sets, and a callback to identify pattern names that refer to definitions rather than binding names.

## `_pattern_name_list`

Wrapper around `pattern_name_list()` that applies the per-file settings' pattern types and field dictionary to extract names bound by a pattern node.

## `_binding_name_list`

Extract the names bound as local variables by a binding node (parameter or local declaration), filtering out names that are part of import statements on those lines and names identified as definition references by the language callback.

## `_local_name_set`

Compute and cache the set of names bound within a scope (parameters, local variables, inner functions) by walking the scope's children while skipping nested scopes and opaque nodes, excluding names released by unbind statements like Python's global/nonlocal.

## `_is_local_name`

Determine whether a name at a given node is bound by an enclosing scope, checking scope_body_dict to handle language-specific cases where scope bindings apply only to specific fields (Python function defaults read outside the function).

## `_chain_part`

Decompose an attribute access chain into its base node and the sequence of member names following it, handling templates by extracting names without template arguments; returns None if a chain part lacks a name component.

## `_chain_usage`

Construct a UsageInfo from a chain base node and member names, handling self/this references that produce short names and identifier bases that must match tracked names, skipping local names unless the base is a called function.

## `_qualified_segment_list`

Split a qualified_identifier node (C++ or Kotlin scope resolution) into its path segments, dropping template arguments; used to normalize fully-qualified names for comparison against tracked imports.

## `_template_name`

Extract the name from a template node (Box from Box<int>) by reading the name field or using the whole node text for non-template types.

## `_qualified_usage`

Parse a qualified_identifier by finding the first tracked segment and returning a UsageInfo with the qualified name from that point onward, dropping irrelevant namespace prefixes.

## `extract_usages`

Main entry point extracting all usage locations of imported names from an AST or COBOL file, traversing depth-first and detecting calls, attribute access chains, simple identifiers, type references, qualified names, and Rust paths while filtering out local bindings and import statements; returns a deduplicated UsageInfo list sorted by line.

## `_leading_part_set`

Return every prefix of tracked names that ends at a separator, used to identify which names are shorter versions of longer qualified names on the same line.

## `deduplicate_usage_list`

Remove duplicate (name, line) pairs and filter out shorter names when longer qualified versions appear on the same line, returning a deduplicated list sorted by line number.

## `_is_function_part_of_call`

Check whether an attribute node is the function being called in a call expression by testing if the parent is a call node and this node is its first identifier-like child.

## `_parse_call_node`

Extract usage information from a function call node by reading the function name part (identifier, attribute chain, or qualified identifier) and optional object+name pattern used by languages like Java.

## `_parse_attribute_node`

Extract usage information from an attribute access node by decomposing its chain and delegating to `_chain_usage()` to validate the base and member names.

## `_parse_path_node`

Extract usage information from a Rust path node (scoped_identifier / scoped_type_identifier) by normalizing its segments and checking if the whole path or first segment is imported.

## `_parse_identifier_node`

Extract usage information from a simple identifier by validating it against import tracking, parent node context (skip_parent_types, identifier_parent_types, skip_name_field_types), pattern fields, and scope bindings.

## `TypedAlias`

Dataclass representing a variable that holds an object of a tracked type, with the variable name, type name (None for untyped aliases), scope start and end lines, used to distinguish uses of variables that represent imports from plain local variables.

## `_scope_node`

Return the innermost scope (function, block) containing a node, or the root if none exists, checking for opaque nodes (class bodies) that break scope binding visibility.

## `extract_typed_aliases`

Extract variables declared with tracked type names by traversing the AST and reading typed_alias_parent_types nodes, returning one TypedAlias per variable with the scope boundaries where it counts.

## `_new_type_name`

Parse a value node (constructor call, new expression) to extract the type name being instantiated, reading typed_alias_new_dict to find the name field and handling identifiers and dotted attribute chains.

## `extract_value_aliases`

Extract variables assigned objects of tracked types by traversing the AST, matching value assignments to type instantiations, tracking scope boundaries, and ending aliases when a variable is reassigned to a non-tracked type or untracked value.

## `typed_alias_dict`

Convert a TypedAlias list into a dictionary keyed by variable name for fast line-based lookup of type information.

## `typed_alias`

Retrieve the TypedAlias entry that applies to a variable name on a specific line, returning the alias with the smallest scope range when multiple match, or None if none applies.

## `typed_alias_type`

Retrieve the type name a variable represents on a specific line by delegating to `typed_alias()` and extracting its type_name field.

## `_type_name`

Extract the type name from a declaration's type node by unwrapping wrappers (type annotations, user_type nodes), handling attribute access (Python dotted types), and reading the innermost identifier or type_identifier.

## `_field_type_and_var`

Extract type and variable name from a declaration that records both in explicit fields (Kotlin properties, Python/TypeScript annotated declarations), returning (None, []) if the structure is invalid.

## `_extract_type_and_var`

Extract type and variable names from a typed variable declaration node by handling language differences (Java declarators, Kotlin holders, C++ pointers) and returning (None, []) if extraction fails.

# Summary

# Summary: codetwine/extractors/usages.py

**Single Responsibility:** Extract and deduplicate symbol usage locations from source code ASTs, tracking where imported names are referenced across multiple programming languages while filtering out local bindings and import statements.

**Main Public Definitions:**
- `UsageInfo`: dataclass for symbol usage locations
- `extract_usages()`: main entry point to find all imported name references
- `extract_typed_aliases()`: identify variables declared with tracked types
- `extract_value_aliases()`: find variables assigned tracked type instances
- `deduplicate_usage_list()`: remove redundant entries
- `symbol_part_list()`, `usage_root_name()`: analyze and resolve usage names

**Key Terms:** AST traversal, function calls, attribute access chains, type references, qualified identifiers, scope tracking, local name resolution, deduplication, language-specific configurations, Rust paths, COBOL file references.
