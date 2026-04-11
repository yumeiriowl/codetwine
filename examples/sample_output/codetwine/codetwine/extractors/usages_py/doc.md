# Design Document: codetwine/extractors/usages.py

# Overview & Purpose

## 1. Module Summary

Extracts usage locations of imported symbols from a parsed AST, returning structured data that identifies where and how each symbol is referenced within a source file.

## 2. When to Use This Module

- **Tracking symbol references across a file**: Call `extract_usages(root_node, imported_names, usage_node_types)` to get a list of `UsageInfo` entries describing every location where any of the given imported names appears in the AST (function calls, attribute accesses, type references, identifiers, etc.).
- **Discovering typed variable aliases**: Call `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)` to obtain a mapping from local variable names to the imported type names used to declare them (e.g., `{"genre": "Genre"}`), so that alias variables can be added to the symbol tracking set alongside the original type name.

Both functions are consumed by `codetwine/extractors/usage_analysis.py` to build dependency relationships between files.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass | Holds a single symbol usage: the symbol name and the 1-based line number where it appears. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | Traverses the AST via DFS and collects all usage locations of the given imported names, covering calls, attribute accesses, type/namespace references, and plain identifiers. Returns a deduplicated, line-sorted list. Returns `[]` when `usage_node_types` is `None`. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose type name is in `imported_names`, and returns a `variable_name -> type_name` mapping. Returns `{}` when `typed_alias_parent_types` is empty. |

## 4. Design Decisions

- **Language-agnostic via `usage_node_types` configuration**: Rather than hard-coding node type names, the module accepts a per-language configuration dict (`call_types`, `attribute_types`, `skip_parent_types`, and optional keys). This keeps the traversal logic language-neutral while allowing per-language customization through `config.py`.
- **Separate skip lists for type references**: `skip_parent_types_for_type_ref` is kept distinct from `skip_parent_types` so that type references in parameter lists and method declarations are detected as dependencies, while plain identifiers in those same positions may still be suppressed.
- **Deduplication favors more specific names**: When both `module` and `module.attr` appear on the same line, `_deduplicate` discards the shorter entry, preserving the more informative attribute-access form and avoiding redundant reporting of the same dependency.
- **`qualified_identifier` handled at the parent level**: C++ scope-resolution expressions (`geometry::Rectangle`) are extracted at the `qualified_identifier` node rather than from the individual `namespace_identifier` / `identifier` children, preventing double-counting while still detecting the usage.

# Definition Design Specifications

---

## `UsageInfo`

**Signature:** `@dataclass class UsageInfo`

**Responsibility:** Represents a single detected usage of an imported symbol, pairing the symbol name with its source location.

**When to use:** Instantiated internally by extraction helpers whenever a node in the AST matches a tracked imported name.

**Fields:**

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The symbol name as it appears at the usage site (may include attribute path, e.g. `module.func`) |
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
- `root_node`: AST root covering the entire file.
- `imported_names`: Set of symbol names whose usages are to be detected.
- `usage_node_types`: Language-specific node type configuration dict, or `None`.
- Returns: A list of `UsageInfo` objects (deduplicated, sorted by line number).

**Responsibility:** Performs a full DFS traversal of the AST to locate all usage sites of the given imported names, covering function calls, attribute accesses, type references, namespace references, scope-resolution identifiers, and simple identifiers.

**When to use:** Called by `usage_analysis.py` after import extraction to determine which lines in a file reference each imported symbol.

**Design decisions:**

- Returns an empty list immediately when `usage_node_types` is `None` or empty, supporting languages with no configured usage tracking.
- Five distinct node categories are handled with separate logic: call nodes, attribute nodes, `qualified_identifier` (C++ scope resolution), type/namespace reference nodes, and plain `identifier` nodes. Each category has different parent-skip rules.
- `skip_parent_types_for_type_ref` defaults to `skip_parent_types` when absent, but is intended to be narrower (only import/package declarations), so that type references in parameters and method declarations are still captured.
- `skip_name_field_types` is optional and defaults to an empty set; when present, only the name-field child of a matching parent node is suppressed, allowing the value side to still be detected.
- Child nodes are always pushed onto the stack for continued traversal regardless of whether the current node was processed (except for `qualified_identifier` nodes inside skipped parent types, where children are still traversed).
- Deduplication is deferred to `_deduplicate` after the full traversal.

**Constraints & edge cases:**

- `usage_node_types` must contain keys `"call_types"`, `"attribute_types"`, and `"skip_parent_types"` when not `None`.
- `"skip_parent_types_for_type_ref"` and `"skip_name_field_types"` are optional keys.
- Only the leading (leftmost) component of a qualified or attribute name is checked against `imported_names`.
- For `qualified_identifier`, only the first matching scope/namespace/type child is examined.

---

## `_deduplicate`

**Signature:**
```
_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]
```
- Returns: A list of `UsageInfo` with redundant and duplicate entries removed, sorted by ascending line number.

**Responsibility:** Cleans the raw usage list by removing entries that are superseded by a more-qualified name on the same line, and eliminating exact `(name, line)` duplicates.

**When to use:** Called once at the end of `extract_usages` before returning results to the caller.

**Design decisions:**

- Entries are grouped by line number; within each line, a shorter name (e.g. `module`) is suppressed if any other entry on the same line starts with that name followed by a dot (e.g. `module.func`). This prevents redundant entries when both the module reference and an attribute call on it are detected on the same line.
- A `seen_keys` set guards against exact `(name, line)` duplicates after the subsumption check.

**Constraints & edge cases:**

- Only subsumes names where the more-detailed form begins with `name + "."`. Names that merely start with the same string but are not dot-qualified are not affected.

---

## `_is_function_part_of_call`

**Signature:**
```
_is_function_part_of_call(node: Node, call_types: set[str]) -> bool
```
- Returns: `True` if `node` is the function-name child of a call node; `False` otherwise.

**Responsibility:** Prevents double-counting when an attribute access node is itself the callee of a call node (which is handled separately by `_parse_call_node`).

**When to use:** Called from `extract_usages` before processing a standalone attribute node to determine whether that node should be skipped.

**Design decisions:**

- Checks identity by comparing node `.id` values, not position, to avoid false positives from structural ambiguity.
- Only the first child of the parent call node that matches `"identifier"` or the attribute node's own type is considered the function-name position.

**Constraints & edge cases:**

- Returns `False` if the node has no parent or the parent is not a call type.

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
- Returns: A `UsageInfo` for the call if the leading name is imported, otherwise `None`.

**Responsibility:** Extracts the usage of an imported symbol from a function call node by inspecting only the callee (function-name) part.

**When to use:** Called from `extract_usages` when the current AST node is a call-type node.

**Design decisions:**

- Only the first child of the call node is examined; subsequent children (arguments, punctuation) are ignored.
- Three callee shapes are handled: plain `identifier`, attribute access (module-qualified call), and `qualified_identifier` (C++ scope-resolution call). For attribute access, only the leading component before the first dot is checked against `imported_names`, but the full attribute string is stored as the usage name.
- For `qualified_identifier` callees, only the first namespace/identifier/type child is examined.

**Constraints & edge cases:**

- Returns `None` if the first child does not match any of the three expected shapes or if the extracted name is not in `imported_names`.

---

## `_parse_attribute_node`

**Signature:**
```
_parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None
```
- Returns: A `UsageInfo` using the full attribute text if the leading name is imported, otherwise `None`.

**Responsibility:** Extracts a usage from a standalone attribute-access node (i.e., not the callee of a call).

**When to use:** Called from `extract_usages` when a non-call attribute node is encountered.

**Design decisions:**

- The full attribute expression text (e.g. `module.attr`) is stored as the usage name, while only the leading component is matched against `imported_names`. This allows `_deduplicate` to later subsume a plain `module` entry on the same line.

**Constraints & edge cases:**

- Assumes the node's text is a dot-separated name; the split uses `"."` as the delimiter.

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
- Returns: A `UsageInfo` if the identifier is an imported name used in a non-skipped context, otherwise `None`.

**Responsibility:** Determines whether a plain identifier node represents a genuine usage of an imported symbol, filtering out occurrences that are part of definitions, import statements, or syntax (e.g., parameter names).

**When to use:** Called from `extract_usages` when the current AST node is a plain `"identifier"` node.

**Design decisions:**

- For parents in `skip_name_field_types`, only the child designated as the `"name"` field is suppressed; other children of the same parent (e.g., the default-value side of a default parameter) are still eligible for detection. This is a finer-grained skip than the blanket `skip_parent_types` suppression.
- When a parent is in `skip_parent_types`, the entire identifier is suppressed regardless of field position.

**Constraints & edge cases:**

- If the node has no parent, no skipping is applied and only the name membership check governs the result.
- The two skip mechanisms are mutually exclusive per parent node: `skip_name_field_types` is checked first.

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
- `typed_alias_parent_types`: Set of AST node type strings that represent typed variable declarations (e.g., `field_declaration`, `parameter`).
- Returns: A `dict[str, str]` mapping variable name → type name, for variables whose declared type is in `imported_names`.

**Responsibility:** Builds a variable-to-type alias map so that variables declared with an imported type can themselves be tracked as indirect usages of that type.

**When to use:** Called by `usage_analysis.py` before `extract_usages` to expand the set of names to track with alias variable names.

**Design decisions:**

- Returns an empty dict immediately when `typed_alias_parent_types` is empty, supporting languages where this feature is not configured.
- Variable names equal to the type name are excluded from the alias map to avoid self-mapping.
- Delegates per-node extraction to `_extract_type_and_var`, centralising the multi-language AST structure differences.

**Constraints & edge cases:**

- Only declarations whose resolved type name is present in `imported_names` produce entries.
- Does not recurse into the children of matched declaration nodes for further alias extraction.

---

## `_extract_type_and_var`

**Signature:**
```
_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]
```
- Returns: A tuple of `(type_name, [variable_names])`. Returns `(None, [])` when the expected child structure is absent.

**Responsibility:** Abstracts the AST structure differences for typed variable declarations across Java, Kotlin, and C/C++ into a single, uniform extraction result.

**When to use:** Called exclusively from `extract_typed_aliases` for each node that matches a typed declaration parent type.

**Design decisions:**

- Handles four child-node shapes within a single loop:
  - `type_identifier` (Java, C/C++) — direct type child
  - `user_type > type_identifier` (Kotlin) — type nested one level deeper
  - `identifier` / `simple_identifier` — direct variable name children
  - `variable_declarator` / `init_declarator` — wrapper nodes (Java/C++) whose first `identifier` child is the variable name
- Only the first `identifier` child inside `variable_declarator`/`init_declarator` wrappers is extracted.

**Constraints & edge cases:**

- Does not handle pointer/reference declarators or array declarators beyond `init_declarator`.
- If a node contains no `type_identifier` or `user_type`, `type_name` is returned as `None`.

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports used in `codetwine/extractors/usages.py` come exclusively from the standard library (`dataclasses`) and the third-party package `tree_sitter`, neither of which are described here per the stated scope.

## Dependents (modules that import this file)

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : imports and calls `extract_usages` to traverse the AST of a source file and retrieve a list of `UsageInfo` objects representing where tracked imported names appear; also imports and calls `extract_typed_aliases` to discover typed variable declarations (e.g., `genre: Genre`) and build a variable-name-to-type-name mapping, which is then used to expand the set of names being tracked before performing usage extraction.

## Dependency Direction

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : **unidirectional**. `usage_analysis.py` depends on `usages.py` for AST-traversal and usage-extraction functionality. `usages.py` does not import from or reference `usage_analysis.py` in any way.

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | Root node of a parsed AST covering an entire source file |
| `imported_names` | `set[str]` | Set of symbol names whose usages are to be tracked |
| `usage_node_types` | `dict \| None` | Per-language configuration dict containing node type sets; when `None` or empty, processing is skipped |
| `typed_alias_parent_types` | `set[str]` | Set of AST node type names representing typed variable declarations (used by `extract_typed_aliases`) |

The `usage_node_types` dict carries the following keys:

| Key | Type | Required | Description |
|---|---|---|---|
| `call_types` | `set[str]` | Required | Node types representing function calls |
| `attribute_types` | `set[str]` | Required | Node types representing attribute access |
| `skip_parent_types` | `set[str]` | Required | Parent node types whose identifier children are skipped |
| `skip_name_field_types` | `set[str]` | Optional | Parent types where only the `name`-field child is skipped |
| `skip_parent_types_for_type_ref` | `set[str]` | Optional | Parent types to skip for type/namespace reference nodes; falls back to `skip_parent_types` |
| `typed_alias_parent_types` | `set[str]` | Optional | Node types representing typed variable declarations |

---

## 2. Transformation Overview

### `extract_usages` pipeline

```
root_node (AST)
    │
    ▼
[Stage 1: DFS traversal of the AST]
  For each node popped from the stack, classify by node type:
    - call_types         → _parse_call_node()
    - attribute_types    → _is_function_part_of_call() guard → _parse_attribute_node()
    - qualified_identifier → scope part extracted directly; skip if parent in skip_parent_types
    - type_identifier /
      namespace_identifier → skip if parent in skip_parent_types_for_type_ref
    - identifier         → _parse_identifier_node()
  Each match produces a UsageInfo appended to usage_list.
    │
    ▼
[Stage 2: Deduplication — _deduplicate()]
  Group UsageInfo entries by line number.
  For each line group:
    - Drop shorter name if a longer "name.attr" form exists on the same line.
    - Drop entries with duplicate (name, line) keys.
    │
    ▼
list[UsageInfo]  (sorted by line number)
```

### `extract_typed_aliases` pipeline

```
root_node (AST)
    │
    ▼
[Stage 1: DFS traversal]
  For each node whose type is in typed_alias_parent_types:
    - _extract_type_and_var() extracts type name and variable name(s).
    │
    ▼
[Stage 2: Filter and map]
  Keep only declarations where type_name is in imported_names
  and var_name differs from type_name.
  Build var_name → type_name mapping.
    │
    ▼
dict[str, str]  (variable name → type name)
```

### Helper routing inside DFS

```
call node         → _parse_call_node
                      ├─ first child is identifier      → plain call: func()
                      ├─ first child in attribute_types → attribute call: module.func()
                      └─ first child is qualified_identifier → C++ scoped call: ns::func()

attribute node    → _parse_attribute_node
                      └─ leading name (split on ".") checked against imported_names

identifier node   → _parse_identifier_node
                      ├─ parent in skip_name_field_types → skip name-field child only
                      └─ parent in skip_parent_types     → skip entirely
```

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | Deduplicated list of symbol usage locations, sorted by line number ascending |
| `extract_typed_aliases` | `dict[str, str]` | Mapping of variable name → declared type name for typed declarations using imported types |
| `_deduplicate` | `list[UsageInfo]` | Cleaned UsageInfo list with redundant and duplicate entries removed |
| `_parse_call_node` | `UsageInfo \| None` | Single usage extracted from a call node, or `None` |
| `_parse_attribute_node` | `UsageInfo \| None` | Single usage extracted from an attribute node, or `None` |
| `_parse_identifier_node` | `UsageInfo \| None` | Single usage extracted from an identifier node, or `None` |
| `_extract_type_and_var` | `tuple[str \| None, list[str]]` | Type name and variable names extracted from a typed declaration node |

All outputs are return values; this module performs no file writes or side effects.

---

## 4. Key Data Structures

### `UsageInfo`

Produced by `extract_usages` and its helpers; consumed by callers in `usage_analysis.py`.

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name being used (may include dotted path, e.g. `"module.attr"`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

### `usage_list` (intermediate)

| Aspect | Detail |
|---|---|
| Type | `list[UsageInfo]` |
| Purpose | Accumulates raw (possibly duplicate) usage entries during DFS traversal before deduplication |

### `by_line` (inside `_deduplicate`)

| Key | Value Type | Purpose |
|---|---|---|
| `int` (line number) | `list[UsageInfo]` | Groups all usage entries occurring on the same line for redundancy checking |

### `aliases` (inside `extract_typed_aliases`)

| Key | Value Type | Purpose |
|---|---|---|
| `str` (variable name) | `str` (type name) | Maps a variable declared with an imported type to that type's name |

### `usage_node_types` dict (input configuration)

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types that represent function call expressions |
| `attribute_types` | `set[str]` | AST node types that represent attribute/member access expressions |
| `skip_parent_types` | `set[str]` | Parent node types that mark an identifier as non-usage (e.g. import declarations, definitions) |
| `skip_name_field_types` | `set[str]` | Parent types where only the `name`-field child is excluded; value-side children are treated as usages |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types to skip specifically for `type_identifier` and `namespace_identifier` nodes |
| `typed_alias_parent_types` | `set[str]` | Node types representing typed variable declarations used by `extract_typed_aliases` |

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** strategy. Rather than raising exceptions or terminating on unexpected input, the functions return empty collections (`[]` or `{}`) or `None` as sentinel values when preconditions are not met or when a node does not match expected patterns. Processing continues silently without logging. No `try-except` blocks are present; all defensive handling is achieved through guard clauses and conditional branching at the logic level.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing or `None` configuration | `usage_node_types` is `None` or empty (falsy) passed to `extract_usages` | Returns `[]` immediately via guard clause | Yes | No usages extracted for the file; caller receives an empty list |
| Missing or empty configuration | `typed_alias_parent_types` is empty or falsy passed to `extract_typed_aliases` | Returns `{}` immediately via guard clause | Yes | No typed aliases extracted; caller receives an empty dict |
| Node does not match expected pattern | A call, attribute, or identifier node has no children matching the expected structure | Inner loop finds no match and `None` is returned from the parse helper | Yes | That individual node is silently skipped; traversal continues |
| Identifier is part of a skipped parent | An identifier node whose parent type is in `skip_parent_types` or is the name field of a `skip_name_field_types` node | Returns `None` from `_parse_identifier_node` | Yes | Node is excluded from results; no impact on overall traversal |
| Attribute node is function part of a call | An attribute node that is the first child of a call node | `_is_function_part_of_call` returns `True`; node is skipped in the `attribute_types` branch | Yes | Node skipped; the corresponding call node handles it instead |
| No matching imported name | A node's text does not appear in `imported_names` | Condition check fails silently; no `UsageInfo` appended | Yes | Node is not recorded; traversal continues normally |
| Parent node is `None` | A node at the AST root has no parent | Parent checks short-circuit via `if parent` guards | Yes | Node is processed without parent-type filtering |
| Type or variable not found in declaration node | A typed variable declaration node lacks a `type_identifier` or variable name child | `_extract_type_and_var` returns `(None, [])` | Yes | Declaration is excluded from the alias map |

---

## 3. Design Notes

- **No exception propagation**: The file contains no `try-except` constructs. All defensive handling relies on guard clauses (`if not usage_node_types`, `if parent`, `if name in imported_names`, etc.), meaning errors in input data manifest as empty or partial results rather than raised exceptions.
- **Sentinel-based communication**: Helper functions (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) return `None` to signal "no applicable usage found," allowing the main traversal loop to skip appending without branching on exception state.
- **Optional configuration keys**: Missing optional keys in `usage_node_types` (`skip_name_field_types`, `skip_parent_types_for_type_ref`) are handled with `.get()` and explicit defaults, preventing `KeyError` without requiring explicit error handling.
- **Deduplication as a correctness safeguard**: `_deduplicate` handles cases where the same usage might be recorded multiple times due to overlapping node traversal (e.g., both a `qualified_identifier` and its child `namespace_identifier` matching). This is a structural defense against redundant results rather than an error condition per se.
- **No logging**: The strategy assumes that absent or non-matching data is a normal operational condition (e.g., a language with no usage tracking defined), not an exceptional one, so no diagnostic output is emitted.

# Summary

**usages.py** — Extracts usage locations of imported symbols from a parsed AST.

**Public API:**
- `UsageInfo` dataclass: `name: str`, `line: int`
- `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None) → list[UsageInfo]`
- `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) → dict[str, str]`

**Key data:** `usage_node_types` dict (keys: `call_types`, `attribute_types`, `skip_parent_types`); produces `list[UsageInfo]` (deduplicated, line-sorted) and `dict[str,str]` mapping variable→type.
