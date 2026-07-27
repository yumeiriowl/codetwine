# Design Document: codetwine/extractors/usages.py

# Overview & Purpose

## Purpose and Role

`codetwine/extractors/usages.py` is responsible for locating *where* previously imported/declared symbols are actually used within a source file's AST (Abstract Syntax Tree). It complements symbol/import extraction stages elsewhere in the pipeline (e.g. import resolution that produces `symbol_to_file_map`) by answering the downstream question: "given a set of imported names, at which lines are they referenced, and how?"

This file exists as a separate module because usage detection is a distinct, language-agnostic traversal concern that must handle many syntactic shapes (function calls, attribute access, plain identifiers, type/namespace references, C++ qualified identifiers, and typed variable declarations) while staying decoupled from the import-resolution logic in `usage_analysis.py`. Keeping this logic isolated allows:
- Reuse across multiple call sites in `usage_analysis.py` (both for the primary file and for caller-context re-analysis).
- Independent tuning of per-language node-type behavior via the externally supplied `usage_node_types` configuration dict (sourced from `USAGE_NODE_TYPES` in `config.py`), without changing the core traversal algorithm.
- Clear separation between "finding usage locations" (`extract_usages`) and "resolving typed aliases" (`extract_typed_aliases`), which is a preprocessing step that expands the tracked-name set before usage extraction runs.

## Public Interface

| Name | Arguments | Returns | Responsibility |
|---|---|---|---|
| `UsageInfo` (dataclass) | `name: str`, `line: int` | — | Holds a single detected usage's symbol name and 1-based line number. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | DFS-traverses the AST to detect calls, attribute accesses, identifiers, and type/namespace references matching `imported_names`, then deduplicates results; returns `[]` if `usage_node_types` is None. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose declared type is an imported name, returning a `{var_name: type_name}` mapping so aliases can be tracked as usages too. |

Internal (non-public) helper functions (`_deduplicate`, `_is_function_part_of_call`, `_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`, `_extract_type_and_var`) support the two public entry points but are not intended for external use.

## Design Decisions

- **Iterative DFS via explicit stack**: Both `extract_usages` and `extract_typed_aliases` avoid recursion, using a list-based stack (`node_stack`/`stack`) to traverse the tree-sitter AST, which avoids Python recursion-depth concerns on large files.
- **Configuration-driven behavior**: Language-specific node type sets (`call_types`, `attribute_types`, `skip_parent_types`, optional `skip_parent_types_for_type_ref`, optional `skip_name_field_types`) are injected via `usage_node_types` rather than hardcoded, allowing the same traversal logic to serve multiple languages (Java, Kotlin, C/C++, etc.).
- **Skip-list pattern for syntactic noise**: `skip_parent_types` and `skip_name_field_types` implement a filtering pattern that distinguishes genuine symbol usage from purely syntactic occurrences (e.g., declaration names, import statements), while still allowing children to be traversed independently when a parent is skipped for a `qualified_identifier`/type-reference node.
- **Post-processing deduplication/redundancy resolution**: `_deduplicate` centralizes both duplicate removal and "prefer more specific dotted name" reduction (e.g., preferring `module.attr` over bare `module`) after the traversal, keeping the traversal logic itself simple and append-only.
- **Two-phase alias resolution**: `extract_typed_aliases` is designed to run before `extract_usages` (as seen in `usage_analysis.py`), expanding the tracked name set with type-inferred variable aliases so that `extract_usages` can subsequently detect usages of those aliases as if they were directly imported names.

# Definition Design Specifications

## `UsageInfo` (dataclass)

**Fields:** `name: str` — the symbol name detected at a usage site; `line: int` — 1-based line number of that usage.

**Design intent:** Acts as the uniform result unit returned by usage extraction, decoupling callers (`usage_analysis.py`) from AST node details. Keeping only name and line (no column or node reference) reflects that downstream consumers only need to map symbols to source files/lines, not precise spans.

---

## `extract_usages(root_node, imported_names, usage_node_types=None) -> list[UsageInfo]`

**Arguments:**
- `root_node: Node` — AST root for the whole file to scan.
- `imported_names: set[str]` — candidate symbol names to detect; anything not in this set is ignored.
- `usage_node_types: dict | None` — language-specific node-type configuration (from `USAGE_NODE_TYPES`), with required keys `call_types`, `attribute_types`, `skip_parent_types`, and optional keys `skip_name_field_types`, `skip_parent_types_for_type_ref`.

**Returns:** Deduplicated `list[UsageInfo]`.

**Design intent:** Serves as the single entry point that language-agnostically walks a tree-sitter AST to find every place an imported name is referenced (calls, attribute access, plain identifiers, type/namespace references), so that call sites in `usage_analysis.py` don't need per-language logic.

**Design decisions:**
- Returns an empty list immediately when `usage_node_types` is falsy, allowing languages without usage-tracking configuration to be safely skipped rather than raising errors.
- Uses an explicit stack-based DFS (not recursion) to traverse arbitrarily deep/large ASTs without risking Python recursion limits.
- Dispatches per node type into dedicated helper parsers (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) to isolate the differing extraction rules for calls, attributes, and simple identifiers.
- Handles `qualified_identifier` (C++ scope resolution) and `type_identifier`/`namespace_identifier` as separate cases from plain `identifier`, since these represent type/namespace references that should bypass the general identifier skip rules (`skip_parent_types_for_type_ref` is intentionally distinct from `skip_parent_types`, defaulting to it only when unset) — this allows type references in parameters/declarations to be tracked as dependencies even when the same name as a plain identifier would be skipped.
- When a parent node is in the relevant skip set, only that node's children are pushed back onto the stack (`continue`), preventing the node itself from being misinterpreted as a usage while still allowing nested usages within it to be found.
- Attribute nodes that are merely the function-name part of a call (`_is_function_part_of_call`) are excluded from standalone attribute processing to avoid double-counting the same usage as both a call and an attribute access.
- Final results always pass through `_deduplicate` to collapse redundant/overlapping entries before returning.

**Constraints/edge cases:** Node types not covered by any branch are traversed but produce no usage; `imported_names` empty means no usages will ever match.

---

## `_deduplicate(usage_list) -> list[UsageInfo]`

**Arguments:** `usage_list: list[UsageInfo]` — possibly containing duplicates or redundant coarse/fine-grained entries.

**Returns:** A new `list[UsageInfo]`, sorted by ascending line number, with duplicates and redundant shorter names removed.

**Design intent:** Consolidates raw AST-traversal output into a clean, presentation-ready result, since a single logical usage (e.g., `module.attr`) can otherwise appear multiple times or in multiple granularities.

**Design decisions:**
- Groups entries by line number first, since redundancy/duplication is only checked within the same line — usages on different lines are always independent.
- Removes an entry if another entry on the same line starts with `name + "."`, implementing "prefer the more specific/detailed reference" (e.g., drop `module` if `module.attr` is also present on that line).
- Uses a `(name, line)` key set to filter exact duplicates, handling cases where the same usage might be detected twice by different traversal branches.
- Sorting by line number gives callers a deterministic, human-readable ordering rather than AST-visit (stack pop) order, which would be effectively reversed/unordered.

---

## `_is_function_part_of_call(node, call_types) -> bool`

**Arguments:** `node: Node` — an attribute-type node being checked; `call_types: set[str]` — node types representing calls.

**Returns:** `bool` — `True` if `node` is the callee expression of its parent call node.

**Design intent:** Prevents double-reporting a usage: when an attribute node is the function-name part of a call expression (e.g., `module.func()`), the call node's own parser already extracts the usage, so the attribute node must not be independently treated as a standalone attribute access.

**Design decision:** Checks that the parent is a call type and that the first matching child (identifier or same-type node) by identity (`child.id == node.id`) is this exact node — this identity check (rather than value comparison) avoids false positives from structurally identical but distinct nodes.

---

## `_parse_call_node(node, imported_names, attribute_types) -> UsageInfo | None`

**Arguments:** `node: Node` — a call expression node; `imported_names: set[str]`; `attribute_types: set[str]` — node types representing attribute access, needed to recognize `module.func()`-style callees.

**Returns:** `UsageInfo` if the callee's leading name is imported, otherwise `None`.

**Design intent:** Extracts the tracked symbol from a function/method call by inspecting only its callee position, since only the leading identifier/module of a call expression is relevant for dependency tracking (arguments are handled separately as their own nodes during traversal).

**Design decisions:**
- Examines only the first child of the call node (`break` after the first iteration) because the callee is always positioned first in the supported grammars, and other children (arguments) are not this function's concern.
- Handles three callee shapes distinctly: bare `identifier` (simple call), `attribute_types` (dotted call, checking only the leading segment before the first `.`), and `qualified_identifier` (C++ `namespace::func()`), reflecting the different grammars across supported languages.
- Returns the full dotted/qualified name as `name` (not just the leading segment) when an attribute call matches, preserving specificity for later deduplication logic in `_deduplicate`.

---

## `_parse_attribute_node(node, imported_names) -> UsageInfo | None`

**Arguments:** `node: Node` — an attribute-access node; `imported_names: set[str]`.

**Returns:** `UsageInfo` with the full attribute-access text as `name` if the leading component is imported, otherwise `None`.

**Design intent:** Captures standalone (non-call) attribute references such as `module.CONST` so dependencies expressed as data access, not just calls, are tracked.

**Design decision:** Checks only the leading segment (`name.split(".")[0]`) against `imported_names`, but stores the full text as the usage name — this mirrors the call-node handling and supports the "more specific name wins" deduplication rule.

---

## `_parse_identifier_node(node, imported_names, skip_parent_types, skip_name_field_types) -> UsageInfo | None`

**Arguments:** `node: Node` — a plain identifier node; `imported_names: set[str]`; `skip_parent_types: set[str]` — parent node types whose identifier children should never count as usages (e.g., import statements, declarations); `skip_name_field_types: set[str]` — parent node types where only the `name`-field child should be skipped, while other fields (e.g., `value`) are still checked.

**Returns:** `UsageInfo` if the identifier's name is imported and not filtered out by the skip rules; otherwise `None`.

**Design intent:** Filters plain identifier occurrences down to genuine usages, excluding cases where an identifier is merely being declared/named (e.g., a parameter name) rather than referencing an imported symbol.

**Design decisions:**
- `skip_name_field_types` is checked before the generic `skip_parent_types` fallback, and uses `child_by_field_name("name")` with identity comparison (`.id ==`) to distinguish the declared name from a default/assigned value in constructs like `default_parameter` (`x=some_var)`), ensuring the value side (`some_var`) is still detected as a usage even though the parent node type would otherwise be fully skipped.
- Falls back to a simple parent-type membership check (`skip_parent_types`) for all other skip cases, keeping the common case simple.

---

## `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types) -> dict[str, str]`

**Arguments:**
- `root_node: Node` — AST root.
- `imported_names: set[str]` — imported *type* names to track (not general symbols).
- `typed_alias_parent_types: set[str]` — AST node types representing typed variable/parameter declarations (language-specific, e.g., Java's `field_declaration`, Kotlin's `property_declaration`, C/C++'s `declaration`).

**Returns:** `dict[str, str]` mapping variable name → imported type name, e.g. `{"genre": "Genre"}`.

**Design intent:** Bridges the gap where a variable of an imported type is later referenced by its variable name rather than the type name directly (e.g., `genre.getId()`); by recording this alias mapping, callers (per `usage_analysis.py`) can expand the tracked-name set so such variable usages are still attributed to the correct imported symbol/file.

**Design decisions:**
- Returns an empty dict immediately if `typed_alias_parent_types` is empty, mirroring `extract_usages`'s behavior for unsupported/unconfigured languages.
- Uses the same stack-based DFS pattern as `extract_usages` for consistency and to avoid recursion depth issues.
- Delegates the language-specific structural parsing to `_extract_type_and_var`, keeping this function purely about traversal and filtering (only keeping mappings where the type is actually in `imported_names`).
- Explicitly excludes the case `var_name == type_name` from the resulting mapping, avoiding a no-op/self-referential alias entry (which would be redundant with directly tracking the type name itself).
- Supports multiple variable names per single type-declaration node (e.g., `Genre a, b;`), producing one dict entry per variable.

---

## `_extract_type_and_var(node) -> tuple[str | None, list[str]]`

**Arguments:** `node: Node` — a single typed-declaration node (whose type is one of `typed_alias_parent_types`).

**Returns:** A tuple `(type_name, var_names)`; `type_name` is `None` if no type could be identified, and `var_names` is a list of zero or more variable names found under this declaration node.

**Design intent:** Encapsulates the cross-language structural differences in how "type + variable name(s)" are represented in typed declarations, so `extract_typed_aliases` can remain language-agnostic.

**Design decisions:**
- Only inspects direct children of `node` (not deeper descendants), assuming the type and variable declarators are always immediate children in the supported grammars.
- Handles `type_identifier` directly (Java/C/C++) and `user_type` (Kotlin, which wraps `type_identifier`) as distinct type-name sources, since Kotlin's grammar nests the type identifier inside a `user_type` node.
- Treats `identifier`/`simple_identifier` children as direct variable names, but also drills one level into `variable_declarator`/`init_declarator` children to find the variable name, since Java and C/C++ nest the declared variable inside these wrapper nodes rather than exposing it as a direct child.
- Within `variable_declarator`/`init_declarator`, stops at the first `identifier` found (`break`), assuming the variable name is the first identifier child (e.g., ignoring subsequent initializer expressions that might also contain identifiers).

# Dependency Description

## Dependencies (what this file uses)

This file relies only on external libraries (`dataclasses`, `tree_sitter`) for its internal logic; it has no project-internal file dependencies. All symbol usage extraction, deduplication, and typed alias resolution logic is self-contained within this module.

## Dependents (what uses this file)

- **`codetwine/extractors/usage_analysis.py`**: This file depends on `usages.py` for two core capabilities:
  - It calls `extract_typed_aliases` to resolve variable-to-type mappings (e.g., mapping a variable name to an imported type name) so that variables using an imported type can also be tracked as usages of that type's originating file.
  - It calls `extract_usages` to obtain the actual list of usage locations (`UsageInfo`) for a given set of tracked symbol names, using the AST root node and the previously resolved typed aliases combined with the imported symbol names.

The dependency direction is unidirectional: `usage_analysis.py` depends on `usages.py`, while `usages.py` has no reciprocal dependency on `usage_analysis.py` or any other project file.

# Data Flow

## Input

| Source | Data | Format |
|---|---|---|
| Caller (`usage_analysis.py`) | `root_node` | Tree-sitter AST root `Node` for a source file |
| Caller | `imported_names` | `set[str]` of symbol names to track (keys of `symbol_to_file_map`, possibly extended with typed-alias variable names) |
| Caller | `usage_node_types` / `typed_alias_parent_types` | Dict/set from `USAGE_NODE_TYPES` config, defining language-specific node type categories |

## Main Transformation Flow

### `extract_usages`
1. **Setup**: Unpack `usage_node_types` into category sets (`call_types`, `attribute_types`, `skip_parent_types`, `skip_name_field_types`, `skip_parent_types_for_type_ref`). Returns `[]` immediately if config is absent.
2. **DFS traversal**: A stack-based depth-first walk over the AST classifies each node by `node.type` into one of: call, attribute, `qualified_identifier`, type/namespace reference, or plain `identifier`.
3. **Per-node extraction**: Each branch delegates to a helper (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) or inline logic to decide whether the node's leading name matches `imported_names`, producing a `UsageInfo` when matched. Parent-type checks (`skip_parent_types`, `skip_parent_types_for_type_ref`, `skip_name_field_types`) filter out declaration/import contexts vs. real usages.
4. **Aggregation**: Matches accumulate into `usage_list: list[UsageInfo]`.
5. **Deduplication**: `_deduplicate` groups entries by line, drops shorter names subsumed by a more specific `name.attr` on the same line, removes duplicate `(name, line)` pairs, and returns a line-sorted list.

### `extract_typed_aliases`
1. **Setup**: Returns `{}` if `typed_alias_parent_types` is empty.
2. **DFS traversal**: Stack-based walk finds nodes whose type is in `typed_alias_parent_types` (declaration/parameter nodes).
3. **Per-node extraction**: `_extract_type_and_var` inspects a node's children to pull out a type name (`type_identifier` or Kotlin `user_type`) and associated variable name(s) (`identifier`/`simple_identifier`, or nested inside `variable_declarator`/`init_declarator`).
4. **Filtering & aggregation**: Only kept if the extracted type name is in `imported_names`; builds `aliases: dict[var_name -> type_name]` (skips self-referential entries where var name equals type name).

## Output

| Function | Output Type | Destination |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` (deduplicated, sorted by line) | Returned to `usage_analysis.py`, consumed as `usage_info_list` / `usage_list` |
| `extract_typed_aliases` | `dict[str, str]` (`var_name -> type_name`) | Returned to `usage_analysis.py`, used to expand tracked names (`symbol_to_file_map`, `names_from_target`) |

## Key Data Structures

**`UsageInfo`** (dataclass)
| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name found in use (may be dotted, e.g. `module.attr`) |
| `line` | `int` | 1-based source line of the usage |

**Aliases dict** (`extract_typed_aliases` output)
| Key | Value | Purpose |
|---|---|---|
| variable name | imported type name | Maps a locally declared variable to the imported type it was declared with, enabling downstream usage tracking of the variable as if it were the type |

**`usage_node_types` config dict** (input, per-language)
| Key | Purpose |
|---|---|
| `call_types` | Node types representing function/method calls |
| `attribute_types` | Node types representing attribute/member access |
| `skip_parent_types` | Parent node types under which identifiers should be ignored (e.g., declarations, imports) |
| `skip_parent_types_for_type_ref` (optional) | Same purpose but specific to type/namespace reference nodes; falls back to `skip_parent_types` |
| `skip_name_field_types` (optional) | Parent types where only the "name" field child is skipped, letting "value" field be treated as usage |
| `typed_alias_parent_types` (used by `extract_typed_aliases`) | Node types representing typed variable/parameter declarations |

**Internal traversal structure**: `node_stack` / `stack` (`list[Node]`) implements DFS by popping a node, processing it, then pushing all its children — order of visitation is not guaranteed to be document order due to stack-based (LIFO) traversal, but all nodes are eventually visited.

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** strategy rather than fail-fast. No exceptions are explicitly raised; instead, functions return empty collections (`[]`, `{}`, `None`) when inputs are missing, unrecognized, or do not match expected patterns. The AST traversal (DFS via stack) is designed to continue safely even when nodes don't match any known pattern, simply skipping them and proceeding to their children. This reflects the nature of the module as a best-effort static analysis tool operating over potentially varied or incomplete AST structures across multiple languages (Java, Kotlin, C/C++).

## Main Error Patterns and Handling

| Error/Edge Case Type | Handling Policy | Impact |
|---|---|---|
| `usage_node_types` is `None` or empty | `extract_usages` returns `[]` immediately | No usages extracted for languages without configured node types; caller receives empty list, no crash |
| `typed_alias_parent_types` is empty | `extract_typed_aliases` returns `{}` immediately | No alias mapping produced; caller treats it as "no typed aliases found" |
| Missing optional keys in `usage_node_types` (`skip_name_field_types`, `skip_parent_types_for_type_ref`) | Uses `.get()` with sensible defaults (empty set, or falls back to `skip_parent_types`) | Avoids `KeyError`; behavior degrades to a safe default rather than failing |
| Required keys missing (`call_types`, `attribute_types`, `skip_parent_types`) | Direct dict indexing (`usage_node_types["..."]`) with no guard | Would raise `KeyError` if config is malformed; treated as a configuration contract that must be satisfied by callers (config.py) |
| Node with no matching type in call/attribute/identifier/type-reference branches | Silently skipped; only children are pushed onto the stack | Traversal continues unaffected; no data loss for other branches |
| `_parse_call_node` / `_parse_attribute_node` / `_parse_identifier_node` find no match (name not in `imported_names`, or parent type in skip list) | Return `None` | Caller (`extract_usages`) checks for `None` before appending, so no invalid entry is added |
| `_extract_type_and_var` cannot find a `type_identifier`/`user_type` or variable child | Returns `(None, [])` | `extract_typed_aliases` skips the node since `type_name` is falsy or not in `imported_names` |
| Absent/optional `node.parent` (e.g., root node) | Guarded with `if parent:` / `if parent and parent.type in ...` checks | Prevents `AttributeError` on `None.type`; treated as "no skip condition applies" |
| Duplicate or redundant usage entries (same name/line, or shorter name subsumed by a longer dotted name on same line) | Handled deterministically in `_deduplicate` via grouping and set-based key tracking | Ensures clean, non-redundant output list without raising errors |

## Design Considerations

- The module assumes that `usage_node_types`, when provided, contains all required keys (`call_types`, `attribute_types`, `skip_parent_types`); this is an implicit contract enforced by the caller-side configuration (`config.py`) rather than validated defensively within this module.
- Optional keys are handled with defaults to support partial/incremental configuration per language without requiring exhaustive settings.
- All node-type checks and dictionary lookups are structured to naturally result in "no match / no usage detected" rather than raising, aligning with the traversal-based, best-effort nature of static AST analysis where malformed or unexpected node shapes should not halt processing of the rest of the file.
- No logging or explicit error reporting is performed; silent skipping is the consistent behavior throughout, placing the responsibility for detecting misconfiguration (e.g., missing required keys) on the caller or on upstream configuration validation.

# Summary

`usages.py` locates where imported/declared symbols are used in an AST, independent of import resolution. Public API: `UsageInfo` (name, line) dataclass; `extract_usages(root_node, imported_names, usage_node_types)` finds calls, attribute accesses, identifiers, and type refs matching imported names via stack-based DFS, deduplicating results; `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)` maps variables to imported types for alias tracking. Config-driven, language-agnostic, degrades gracefully (empty results) on missing config. Used exclusively by `usage_analysis.py`.
