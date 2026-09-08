# Design Document: codetwine/extractors/usages.py

# Design Specification

**Overview**

Traverses a tree-sitter AST to extract usage locations and typed-alias mappings of previously imported symbol names.

- Call `extract_usages` when you need every location (function calls, attribute accesses, identifiers, type/namespace references) in a file where a set of imported names is referenced, and you want a deduplicated `UsageInfo` list sorted by line.
- Call `extract_typed_aliases` when a language declares local variables/parameters with an imported type name (Java, Kotlin, C/C++ style declarations) and you need a variable-name → type-name mapping so those variable names can also be tracked as usages of the imported type.
- Use the `UsageInfo` dataclass as the common return unit whenever you need a symbol name paired with its 1-based source line.
- Use the private helpers (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`, `_is_function_part_of_call`, `_extract_type_and_var`, `_deduplicate`) only if extending the traversal logic itself; external callers should not need them directly.

This file relies only on `tree_sitter.Node` for AST traversal; it has no internal project dependencies. It is used by `codetwine/extractors/usage_analysis.py`, which calls `extract_typed_aliases` to build a variable→type map from a root AST node against a set of tracked names, then merges those variable names into the tracked-name set before calling `extract_usages` to obtain the final usage list for a file (both for the file itself and for caller-side analysis using `caller_root`).

Design decisions: traversal is iterative (stack-based DFS) rather than recursive to avoid recursion-depth issues on large ASTs; language-specific behavior is entirely externalized into the `usage_node_types` / `typed_alias_parent_types` config dictionaries rather than hardcoded per-language branches, so adding a new language requires only new config entries, not new traversal code; deduplication favors the most specific dotted name (e.g. `module.attr` over `module`) per line rather than keeping both.

**Definitions**

## `UsageInfo`
A dataclass holding a single usage record: the symbol `name` and the 1-based `line` where it appears. It is the uniform return element produced by `extract_usages` and consumed by callers in `usage_analysis.py` to report where imported/tracked symbols are used.

## `extract_usages`
Performs an iterative DFS over the AST rooted at `root_node`, detecting usages of names in `imported_names` by matching node types configured in `usage_node_types` (`call_types`, `attribute_types`, `skip_parent_types`, and optional `skip_parent_types_for_type_ref` / `skip_name_field_types`), plus hardcoded handling for `identifier`, `type_identifier`/`namespace_identifier`, and C++ `qualified_identifier` nodes. It returns an empty list immediately when `usage_node_types` is falsy, which is how languages without usage-tracking configuration are handled; otherwise it delegates node-specific parsing to `_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`, and inline scope-resolution logic, then deduplicates results via `_deduplicate` before returning. Callers use it to get the final, per-file or per-caller list of `UsageInfo` for a given set of tracked names.

## `_deduplicate`
Groups collected `UsageInfo` entries by line number and removes a shorter name when a more detailed dotted name (e.g. keeps `module.attr`, drops `module`) exists on the same line, then removes exact `(name, line)` duplicates. It exists to prevent redundant reporting when both a call/attribute node and its constituent identifier nodes independently register a usage on the same line. Returns results sorted ascending by line.

## `_is_function_part_of_call`
Checks whether an attribute node is actually the callee expression of a parent call node (e.g. the `module.func` part of `module.func()`), based on `call_types` and whether the node is the parent's first identifier/attribute child. It exists so that `extract_usages` does not double-count an attribute access that is already handled by the enclosing call node's `_parse_call_node`.

## `_parse_call_node`
Extracts a `UsageInfo` from a call node by inspecting only its first child: a simple `identifier` (e.g. `func()`), an attribute node from `attribute_types` (e.g. `module.func()`, checked via the leading segment before the first dot), or a `qualified_identifier` (C++ `geometry::doSomething()`), matching the resolved name against `imported_names`. Returns `None` when the leading name/segment is not tracked; used exclusively by `extract_usages` for nodes in `call_types`.

## `_parse_attribute_node`
Extracts a `UsageInfo` from a standalone attribute-access node (`module.attr`) by checking whether the leading segment before the first dot is in `imported_names`, recording the full dotted text as the usage name. Used by `extract_usages` only for attribute nodes not already classified as the function part of a call via `_is_function_part_of_call`.

## `_parse_identifier_node`
Extracts a `UsageInfo` from a plain identifier node while filtering out identifiers that are structurally part of declarations/imports rather than genuine usages, using `skip_parent_types` to skip entirely and `skip_name_field_types` to skip only when the identifier is the parent's "name"-field child (allowing the "value" side, e.g. a default-parameter's assigned expression, to still be detected). It is the fallback usage detector for generic `identifier` nodes not covered by call/attribute/type-reference handling.

## `extract_typed_aliases`
Performs an iterative DFS over `root_node` to find variable/parameter declarations whose node type is in `typed_alias_parent_types` (e.g. Java `field_declaration`/`local_variable_declaration`/`formal_parameter`, Kotlin `property_declaration`/`parameter`, C/C++ `declaration`/`parameter_declaration`), delegating name/type extraction to `_extract_type_and_var`, and returns a `{variable_name: type_name}` dict limited to declarations whose type is in `imported_names`. Returns an empty dict immediately if `typed_alias_parent_types` is empty; `usage_analysis.py` uses this to discover locally-declared variable names (e.g. `genre` typed as imported `Genre`) so those variable names can be added to the tracked-name set before calling `extract_usages`.

## `_extract_type_and_var`
Inspects the direct children of a typed-declaration node to pull out the declared type name and one or more variable names, handling per-language AST shape differences: direct `type_identifier`, Kotlin's `user_type` wrapping a nested `type_identifier`, direct `identifier`/`simple_identifier` children, and Java/C++ `variable_declarator`/`init_declarator` wrapping a nested `identifier`. Returns `(None, [])` when no recognizable type/variable pattern is found, signaling to `extract_typed_aliases` that the node should be skipped.

# Summary

Extracts imported-symbol usage locations and typed-alias variable-to-type mappings from a tree-sitter AST via iterative stack-based DFS, using language-agnostic config dictionaries. Public: UsageInfo dataclass, extract_usages, extract_typed_aliases. Handles calls, attributes, identifiers, type/namespace references, deduplication favoring specific dotted names, and typed variable/parameter declarations across Java/Kotlin/C/C++. Depends only on tree_sitter.Node; consumed by usage_analysis.py to build tracked-name sets and report per-file/caller symbol usage locations.
