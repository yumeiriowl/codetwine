# Design Document: codetwine/extractors/usages.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts usage locations of imported symbols from a parsed AST and returns structured location data, along with detecting typed variable aliases that expand the set of names to track.

## 2. When to Use This Module

- **When locating all usages of imported symbols in a file**: Call `extract_usages(root_node, imported_names, usage_node_types)` to receive a deduplicated list of `UsageInfo` objects, each recording the symbol name and its line number. Used by `usage_analysis.py` to determine which imported symbols are actually referenced in a file.
- **When resolving typed variable aliases that reference imported types**: Call `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)` to receive a `dict[str, str]` mapping variable names to the imported type names they were declared with (e.g., `{"genre": "Genre"}`). Used by `usage_analysis.py` to expand the tracking set so that local variables carrying an imported type are also detected as usage points.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass | Holds a single symbol usage: the symbol name and its 1-based line number. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | DFS-traverses the AST to find all locations where any name in `imported_names` is used, covering calls, attribute access, identifiers, type references, and namespace references; returns a deduplicated, line-sorted list. Returns `[]` when `usage_node_types` is `None`. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose declared type is in `imported_names`, and returns a mapping of each variable name to its type name. Returns `{}` when `typed_alias_parent_types` is empty. |

## 4. Design Decisions

- **Language-agnostic via configuration dict**: The behavior of `extract_usages` is driven entirely by the `usage_node_types` dict (providing `call_types`, `attribute_types`, `skip_parent_types`, and optional keys). This allows the same traversal logic to serve multiple languages without subclassing or branching on language name.
- **Separate skip rules for type references**: `skip_parent_types_for_type_ref` is kept distinct from `skip_parent_types` so that `type_identifier` and `namespace_identifier` nodes in parameter lists and method declarations are captured as dependencies, while plain identifiers in those positions are still ignored.
- **Deduplication over accumulation**: Rather than preventing duplicate insertions during traversal, the implementation freely appends and removes redundancy in a post-processing step (`_deduplicate`). When both `module` and `module.attr` appear on the same line, only the more specific `module.attr` form is kept.
- **`qualified_identifier` handled separately**: C++ scope-resolution expressions are given their own branch to extract only the leftmost scope name, avoiding double-counting from the `namespace_identifier` children that would otherwise be visited independently.

## Definition Design Specifications

# Definition Design Specifications

---

## `UsageInfo`

**Type:** Dataclass

**Responsibility:** Represents a single detected usage of an imported symbol, pairing the symbol name with its source location.

**When to use:** Instantiated internally by `extract_usages` and its helper functions whenever a qualifying identifier is found in the AST.

### Fields

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The symbol name as it appears at the usage site (may include dotted attribute path, e.g. `module.attr`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

---

## `extract_usages`

**Signature:**
```
extract_usages(
    root_node: Node,
    imported_names: set[str],
    usage_node_types: dict | None = None,
) -> list[UsageInfo]
```

**`imported_names`:** A set of symbol name strings whose usages are to be detected.  
**`usage_node_types`:** A configuration dictionary keyed by node-type category names; `None` or empty causes immediate return of `[]`.  
**Returns:** A list of `UsageInfo` objects, deduplicated and sorted by line number.

**Responsibility:** Performs a full DFS traversal of the AST to collect every location where any symbol from `imported_names` is used, covering calls, attribute accesses, type references, namespace references, scope-resolution identifiers, and plain identifiers.

**When to use:** Called by `usage_analysis.py` after building a `symbol_to_file_map` to locate all in-file usages of tracked symbols.

### Design Decisions

- **DFS via explicit stack** rather than recursion, avoiding Python recursion depth limits on large ASTs.
- **Node-type dispatch** separates five distinct AST patterns (call, attribute, `qualified_identifier`, type/namespace reference, plain identifier) so each can apply its own parent-context filtering rules.
- **`skip_parent_types` vs. `skip_parent_types_for_type_ref`:** Type-reference nodes use a narrower skip set so that type usages in parameter and method declarations are detected as dependencies, unlike plain identifiers which respect the broader skip set.
- **`skip_name_field_types`:** For node types in this set, only the syntactic "name" field child is suppressed; the "value" field child is still detected as a usage.
- **`qualified_identifier` handling:** Only the leftmost scope part is recorded here; child `namespace_identifier`/`identifier` nodes are individually suppressed by `skip_parent_types` to prevent double-counting.
- Final deduplication is delegated to `_deduplicate`.

### Constraints & Edge Cases

- Returns `[]` immediately when `usage_node_types` is falsy.
- Required keys in `usage_node_types`: `"call_types"`, `"attribute_types"`, `"skip_parent_types"`. Missing optional keys (`"skip_name_field_types"`, `"skip_parent_types_for_type_ref"`) fall back to safe defaults.
- Does not track usages of names not present in `imported_names`.

---

## `_deduplicate`

**Signature:**
```
_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]
```

**Returns:** A `list[UsageInfo]` sorted ascending by line number, with redundant and duplicate entries removed.

**Responsibility:** Eliminates two categories of redundancy: entries whose name is a strict prefix of another entry on the same line (e.g. `module` is dropped when `module.attr` also exists), and exact `(name, line)` duplicates.

**When to use:** Called once at the end of `extract_usages` before returning results.

### Design Decisions

- **Prefix suppression is line-scoped:** A shorter name is only dropped when a longer dotted form exists *on the same line*, preserving legitimate multi-line usages.
- **Deterministic output order:** Groups are processed in sorted line-number order, and a `seen_keys` set enforces uniqueness.

### Constraints & Edge Cases

- Prefix check uses `startswith(name + ".")` so a name that is a substring of an unrelated name is not incorrectly suppressed.

---

## `_is_function_part_of_call`

**Signature:**
```
_is_function_part_of_call(node: Node, call_types: set[str]) -> bool
```

**Returns:** `True` if `node` is the function-name child of a parent call node; `False` otherwise.

**Responsibility:** Prevents an attribute-access node from being double-counted when it is the callee of a call node that will itself be processed.

**When to use:** Called within `extract_usages` before processing any attribute-type node.

### Design Decisions

- Parent-type membership in `call_types` is checked first; only then is child identity compared, keeping the check cheap for non-call parents.

### Constraints & Edge Cases

- Returns `False` when `node` has no parent.

---

## `_parse_call_node`

**Signature:**
```
_parse_call_node(
    node: Node,
    imported_names: set[str],
    attribute_types: set[str],
) -> UsageInfo | None
```

**Returns:** A `UsageInfo` if the leading name of the call is in `imported_names`; `None` otherwise.

**Responsibility:** Extracts the symbol usage from a function-call AST node by inspecting only its first child (the callee), covering simple calls, attribute-access calls, and C++ scope-resolution calls.

**When to use:** Called by `extract_usages` when a call-type node is encountered.

### Design Decisions

- Only the first child is examined; remaining children (arguments, punctuation) are ignored.
- For attribute-access callees, `name.split(".")[0]` extracts the root module name for the membership check, while the full dotted string is stored as the usage name.
- For `qualified_identifier` callees, only the leftmost scope part is checked and stored.

### Constraints & Edge Cases

- Returns `None` if the first child does not match any of the three recognised child patterns.

---

## `_parse_attribute_node`

**Signature:**
```
_parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None
```

**Returns:** A `UsageInfo` for the full dotted name if its root is imported; `None` otherwise.

**Responsibility:** Detects standalone attribute-access usages (i.e. not the function part of a call) where the root object is an imported symbol.

**When to use:** Called by `extract_usages` for attribute-type nodes that are not call callees.

### Constraints & Edge Cases

- Membership is checked against only the first component of the dotted name.

---

## `_parse_identifier_node`

**Signature:**
```
_parse_identifier_node(
    node: Node,
    imported_names: set[str],
    skip_parent_types: set[str],
    skip_name_field_types: set[str],
) -> UsageInfo | None
```

**Returns:** A `UsageInfo` if the identifier is an imported name in a usage context; `None` otherwise.

**Responsibility:** Handles plain identifier nodes, applying parent-context rules to exclude syntactic positions (definitions, imports, argument declarations) while still detecting default-value and right-hand-side usages.

**When to use:** Called by `extract_usages` for every `"identifier"` node.

### Design Decisions

- **Two-tier parent filtering:** When the parent type is in `skip_name_field_types`, only the child bound to the `"name"` field is suppressed; all other children (e.g. the `"value"` field) pass through. When the parent type is in `skip_parent_types`, the node is always suppressed.

### Constraints & Edge Cases

- Returns `None` if the identifier's name is not in `imported_names` after passing parent-context checks.
- Returns `None` if `parent.child_by_field_name("name")` returns `None` for a `skip_name_field_types` parent, but the identifier still passes the name-membership check.

---

## `extract_typed_aliases`

**Signature:**
```
extract_typed_aliases(
    root_node: Node,
    imported_names: set[str],
    typed_alias_parent_types: set[str],
) -> dict[str, str]
```

**`typed_alias_parent_types`:** A set of AST node type strings representing typed variable declarations (e.g. `field_declaration`, `parameter`).  
**Returns:** A `dict[str, str]` mapping variable name → type name, restricted to declarations whose type is in `imported_names`.

**Responsibility:** Discovers variables whose declared type is an imported symbol so that usages of those variables can later be attributed to the same dependency as the type itself.

**When to use:** Called by `usage_analysis.py` before `extract_usages` to expand the set of tracked names with alias variables.

### Design Decisions

- DFS traversal visits all nodes; type extraction is delegated to `_extract_type_and_var` to centralise cross-language AST structure differences.
- A variable whose name equals its type name is excluded from the result to avoid self-referential entries.

### Constraints & Edge Cases

- Returns `{}` immediately when `typed_alias_parent_types` is falsy.
- Only declarations where the type name is present in `imported_names` contribute entries.

---

## `_extract_type_and_var`

**Signature:**
```
_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]
```

**Returns:** A two-element tuple: the type name string (or `None` if not found) and a list of variable name strings (possibly empty).

**Responsibility:** Abstracts away per-language AST structural differences in typed variable declarations to yield a uniform (type, [variables]) result.

### Supported AST Patterns

| Language | Type node | Variable node |
|----------|-----------|---------------|
| Java | `type_identifier` | `variable_declarator` → `identifier` |
| Kotlin | `user_type` → `type_identifier` | `simple_identifier` |
| C/C++ | `type_identifier` | `init_declarator` → `identifier` / `identifier` |

**When to use:** Called exclusively by `extract_typed_aliases` for each node matching `typed_alias_parent_types`.

### Constraints & Edge Cases

- Returns `(None, [])` when neither a type node nor a variable node is found among immediate children (or their one-level-deep wrappers).
- Only the first `type_identifier` found under a `user_type` child is used.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the standard library (`dataclasses`) and the third-party package `tree_sitter`. No internal project modules are imported.

## Dependents (modules that import this file)

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages_py/usages.py` : imports and calls `extract_usages` to traverse an AST and collect `UsageInfo` records for imported symbol names found within a file, and imports and calls `extract_typed_aliases` to build a variable-name-to-type-name mapping from typed variable declarations, enabling alias tracking alongside direct symbol usages.

## Dependency Direction

- The relationship between `codetwine/extractors/usage_analysis.py` and this module is **unidirectional**: `usage_analysis.py` depends on this module, and this module has no knowledge of or dependency on `usage_analysis.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | Root AST node of an entire source file, produced by tree-sitter parsing |
| `imported_names` | `set[str]` | Set of symbol names whose usages are to be tracked (e.g. `{"User", "Genre"}`) |
| `usage_node_types` | `dict \| None` | Per-language node type configuration dict obtained from `USAGE_NODE_TYPES` in config; controls which AST node types trigger detection |
| `typed_alias_parent_types` | `set[str]` | Set of AST node type strings that represent typed variable declarations (used only by `extract_typed_aliases`) |

The `usage_node_types` dict has the following expected keys:

| Key | Type | Required | Purpose |
|---|---|---|---|
| `call_types` | `set[str]` | Required | AST node types representing function calls |
| `attribute_types` | `set[str]` | Required | AST node types representing attribute access |
| `skip_parent_types` | `set[str]` | Required | Parent node types whose children are excluded from identifier detection |
| `skip_name_field_types` | `set[str]` | Optional | Parent types where only the name-field child is skipped; value-side children are still detected |
| `skip_parent_types_for_type_ref` | `set[str]` | Optional | Overrides `skip_parent_types` specifically for type/namespace reference nodes |

---

## 2. Transformation Overview

### `extract_usages` pipeline

```
root_node
    │
    ▼
[DFS traversal via node_stack]
    │
    ├─ node.type ∈ call_types        → _parse_call_node()      → UsageInfo or None
    ├─ node.type ∈ attribute_types   → _parse_attribute_node() → UsageInfo or None
    │   (only if not function part of a call)
    ├─ node.type == "qualified_identifier"
    │   └─ extract leftmost namespace/identifier child        → UsageInfo or None
    ├─ node.type ∈ {type_identifier, namespace_identifier}    → UsageInfo or None
    │   (skip if parent ∈ skip_parent_types_for_type_ref)
    └─ node.type == "identifier"     → _parse_identifier_node() → UsageInfo or None
    │
    ▼
usage_list: list[UsageInfo]  (raw, may contain duplicates/redundancies)
    │
    ▼
_deduplicate()
    ├─ Group entries by line number
    ├─ Drop shorter names when a more specific "name.attr" form exists on the same line
    └─ Drop exact (name, line) duplicate pairs
    │
    ▼
list[UsageInfo]  (deduplicated, sorted by line number ascending)
```

Each parse helper applies the same fundamental check: extract the leading name from the node text, test it against `imported_names`, and return a `UsageInfo` if matched.

### `extract_typed_aliases` pipeline

```
root_node
    │
    ▼
[DFS traversal via stack]
    │
    └─ node.type ∈ typed_alias_parent_types
           │
           ▼
       _extract_type_and_var(node)
           ├─ Traverse direct children for type_identifier / user_type → type_name
           └─ Traverse direct children for identifier / variable_declarator / init_declarator → var_names
           │
           ▼
       type_name ∈ imported_names?
           └─ Yes → add {var_name: type_name} for each var_name ≠ type_name
    │
    ▼
dict[str, str]  (variable name → type name)
```

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | Deduplicated list of usage locations, sorted ascending by line number; empty list when `usage_node_types` is `None` |
| `extract_typed_aliases` | `dict[str, str]` | Mapping from variable name to its declared type name, restricted to types in `imported_names`; empty dict when `typed_alias_parent_types` is empty |

No file writes or side effects occur; all outputs are pure return values.

---

## 4. Key Data Structures

### `UsageInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name detected as being used; may be a simple name (`"os"`) or a dotted attribute path (`"os.path"`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

### `usage_list` / `list[UsageInfo]` (intermediate)

Accumulated during DFS traversal before deduplication. May contain multiple entries for the same symbol on the same line (e.g., both `"module"` and `"module.attr"`).

### `by_line` (inside `_deduplicate`)

| Key | Value Type | Purpose |
|---|---|---|
| `line` (int) | `list[UsageInfo]` | Groups all raw usage entries that share the same line number for redundancy elimination |

### `aliases` / `dict[str, str]` (output of `extract_typed_aliases`)

| Key | Value Type | Purpose |
|---|---|---|
| variable name (`str`) | type name (`str`) | Maps a locally declared variable (e.g. `"genre"`) to the imported type it was declared with (e.g. `"Genre"`) |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions on invalid or unexpected input, functions return empty collections (`[]` or `{}`) as safe defaults. Processing continues silently when nodes do not match expected patterns, and no logging or exception propagation is performed. The design prioritizes stability of the AST traversal pipeline over strict error reporting.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing or falsy `usage_node_types` | `extract_usages` is called with `None` or an empty dict | Returns an empty list immediately via early-return guard | Yes | No usages extracted for the file; caller receives `[]` |
| Missing or falsy `typed_alias_parent_types` | `extract_typed_aliases` is called with an empty set | Returns an empty dict immediately via early-return guard | Yes | No typed aliases extracted; caller receives `{}` |
| Node text not matching any imported name | An AST node is visited but its decoded text is not in `imported_names` | Node is silently skipped; no entry is added to the result list | Yes | That node contributes no usage entry; traversal continues normally |
| Node with no matching parent type | An identifier/type node's parent type is not in any skip or special-case set | Node is processed as a normal usage candidate without special filtering | Yes | Possibly over-inclusive detection, but no error is raised |
| Optional key absent from `usage_node_types` | `skip_name_field_types` or `skip_parent_types_for_type_ref` not present in the dict | Resolved via `.get()` with a safe default (`set()` or fallback to `skip_parent_types`) | Yes | Behavior falls back to default; no exception |
| Qualified identifier with no matching child | A `qualified_identifier` node has no `namespace_identifier`, `identifier`, or `type_identifier` child | The inner loop finds nothing; no usage is appended and traversal continues | Yes | No usage recorded for that node |
| `_extract_type_and_var` finds no type or variable | A typed declaration node lacks expected child types | Returns `(None, [])` and the caller's `if type_name` guard prevents any alias from being recorded | Yes | That declaration is silently ignored in the alias map |
| Duplicate or redundant usage entries | Multiple traversal paths produce overlapping `UsageInfo` entries for the same symbol and line | `_deduplicate` post-processes the list, retaining only the most specific entry per line | Yes | No data loss; only the most informative entry is kept |

---

## 3. Design Notes

- **No exception raising.** The file contains no `try/except` blocks and raises no exceptions. All unexpected or non-matching conditions are resolved by returning `None`, `[]`, or `{}`, delegating the responsibility of interpreting empty results to the caller (`usage_analysis.py`).
- **Guard clauses as the primary defense.** Both public entry points (`extract_usages`, `extract_typed_aliases`) use falsy-value guards at the top to handle missing configuration without any downstream impact.
- **Silent skip convention in helpers.** Private helpers (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`, `_extract_type_and_var`) consistently return `None` or empty values when a node does not satisfy the detection criteria, allowing the main traversal loop to ignore them without any special branching.
- **Deduplication as a correctness safeguard.** Because multiple node types can represent the same symbol on the same line, `_deduplicate` is applied unconditionally at the end of `extract_usages` to ensure the output is clean regardless of traversal order or overlapping matches.

## Summary

**usages.py** — Extracts usage locations of imported symbols from a tree-sitter AST and detects typed variable aliases.

**Public API:**
- `UsageInfo` (dataclass): `name: str`, `line: int`
- `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None) -> list[UsageInfo]`
- `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]`

**Key structures:** `UsageInfo` list (deduplicated, line-sorted); alias dict mapping variable name → imported type name; `usage_node_types` config dict with keys `call_types`, `attribute_types`, `skip_parent_types`.
