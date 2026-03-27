# Design Document: codetwine/extractors/usages.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts symbol usage locations and typed variable alias mappings from a Tree-sitter AST, enabling callers to determine which lines in a source file reference a given set of imported symbol names.

## 2. When to Use This Module

- **Tracking where imported symbols are used**: Call `extract_usages(root_node, imported_names, usage_node_types)` to receive a deduplicated list of `UsageInfo` entries, each identifying a symbol name and the line number where it appears. Used by `usage_analysis.py` to find all references to known symbols within a file's AST.
- **Discovering typed variable aliases**: Call `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)` to obtain a `dict[str, str]` mapping variable names to their declared type names (e.g., `{"genre": "Genre"}`). Used by `usage_analysis.py` to expand the tracked symbol set with local variable aliases before performing usage extraction.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass | Holds a single symbol usage: the symbol name and its 1-based line number. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | Traverses the AST via DFS and collects all locations where any name in `imported_names` is used (function calls, attribute access, identifiers, type/namespace references). Returns an empty list when `usage_node_types` is `None`. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose type is in `imported_names`, returning a variable-name-to-type-name mapping. Returns an empty dict when `typed_alias_parent_types` is empty. |

## 4. Design Decisions

- **Language-agnostic via `usage_node_types` config**: Rather than hard-coding node type names, the module accepts a per-language configuration dict (`call_types`, `attribute_types`, `skip_parent_types`, and optional keys). This makes the traversal logic reusable across languages (Python, Java, Kotlin, C/C++) without branching on language identity inside the module.
- **Separate skip lists for type references**: `skip_parent_types_for_type_ref` defaults to `skip_parent_types` when absent, but can be overridden so that type references in parameter and method declarations are detected as usages even when the same parent type would suppress plain identifier detection.
- **Post-traversal deduplication via `_deduplicate`**: Rather than guarding against duplicates during traversal, all candidate entries are collected and then deduplicated in a single pass. When both `"module"` and `"module.attr"` appear on the same line, the shorter name is dropped in favor of the more specific qualified form.
- **`skip_name_field_types` for partial skipping**: For node types such as default parameters (`def func(x=some_var)`), only the `name`-field child is suppressed; the value-side identifier is still reported as a usage, requiring only field-name introspection rather than a full parent-type skip.

## Definition Design Specifications

# Definition Design Specifications

---

## `UsageInfo`

**Signature:** `@dataclass class UsageInfo`

A plain data container for a single detected symbol usage site.

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The symbol name as it appears at the usage site (may include dotted path, e.g. `"module.attr"`) |
| `line` | `int` | 1-based line number of the usage within the source file |

- **Responsibility:** Provides a typed, immutable-by-convention record that pairs a symbol name with its location, used throughout the extraction pipeline as the unit of result.
- **When to use:** Instantiated internally by extraction helpers whenever a qualifying usage is found; consumed by callers of `extract_usages` and `extract_typed_aliases`-driven logic in `usage_analysis.py`.

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

- `root_node` – Tree-sitter `Node` representing the root of a parsed file's AST.
- `imported_names` – Set of symbol name strings whose usages are to be found.
- `usage_node_types` – Language-specific node type configuration dict, or `None`.
- Returns a list of `UsageInfo` objects (one entry per detected usage, after deduplication).

**Responsibility:** Entry point for usage extraction; traverses the entire file AST via depth-first search and collects every location where any name in `imported_names` is referenced.

**When to use:** Called by `usage_analysis.py` after the set of tracked symbol names (including typed aliases) has been assembled, once per file being analyzed.

**Design decisions:**
- Returns an empty list immediately when `usage_node_types` is `None` or falsy, making the function safe to call for languages with no tracking configured without requiring the caller to guard.
- Uses an explicit stack-based DFS rather than recursion to avoid Python call-stack limits on deep ASTs.
- Dispatches to specialized helpers (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) per node type category, keeping the main loop as a routing layer.
- `qualified_identifier` (C++ scope resolution) is handled inline rather than delegated, because only the leftmost scope component is recorded to prevent duplicate entries that would otherwise arise from child `namespace_identifier`/`identifier` nodes.
- `type_identifier` and `namespace_identifier` use a separate skip-list (`skip_parent_types_for_type_ref`) so that type references in parameter lists and method declarations are captured, unlike plain identifiers.
- Results are passed through `_deduplicate` before returning.

**Constraints & edge cases:**
- `usage_node_types` must contain keys `"call_types"`, `"attribute_types"`, and `"skip_parent_types"` when not `None`; `"skip_name_field_types"` and `"skip_parent_types_for_type_ref"` are optional.
- When `"skip_parent_types_for_type_ref"` is absent, `skip_parent_types` is used as its fallback.
- When `"skip_name_field_types"` is absent, an empty set is used.
- The function does not validate the structure of `usage_node_types`; missing required keys will raise a `KeyError` at runtime.

---

## `_deduplicate`

**Signature:**
```
_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]
```

- Returns a `list[UsageInfo]` sorted by ascending line number, with redundant and duplicate entries removed.

**Responsibility:** Cleans the raw usage list by (a) preferring a more-qualified name over its prefix on the same line, and (b) removing exact `(name, line)` duplicate pairs.

**When to use:** Called once at the end of `extract_usages` before returning results; not intended to be called directly by external code.

**Design decisions:**
- "Prefix suppression" rule: if both `"module"` and `"module.attr"` appear on the same line, `"module"` is dropped because the dotted form is more informative and already implies the module reference.
- Processes entries grouped by line number, allowing the prefix check to be scoped to a single line without global comparisons.

**Constraints & edge cases:**
- The prefix suppression is purely textual (string `startswith`); it does not validate that the longer name is semantically related.
- Input order within a line is not guaranteed to affect the output beyond the prefix rule and the `seen_keys` deduplication.

---

## `_is_function_part_of_call`

**Signature:**
```
_is_function_part_of_call(node: Node, call_types: set[str]) -> bool
```

- `node` – An attribute-type AST node.
- `call_types` – Set of node type strings representing call expressions.
- Returns `True` if this node is the callee child of a call node; `False` otherwise.

**Responsibility:** Prevents double-counting when an attribute access is both the function position of a call and a standalone attribute reference—the call handler takes precedence.

**When to use:** Called inside `extract_usages` before processing an attribute node, to decide whether to skip it in favor of the enclosing call node's processing.

**Design decisions:**
- Checks whether the parent is a call type and whether this node is the first matching child of that parent, matching on either `"identifier"` or the node's own type.

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

- Returns a `UsageInfo` if the callee name (or its leading component) is in `imported_names`; otherwise `None`.

**Responsibility:** Extracts usage information specifically from function/method call AST nodes by inspecting only the callee position.

**When to use:** Called by `extract_usages` for every node whose type is in `call_types`.

**Design decisions:**
- Only the first child of the call node is inspected; the rest (arguments, punctuation) are ignored entirely.
- Handles three callee forms: plain `identifier`, attribute access (dotted), and C++ `qualified_identifier` (scope resolution). Only the leading name component is matched against `imported_names` for the dotted and scope-resolution forms.

**Constraints & edge cases:**
- Returns `None` when the leading component is not in `imported_names`, even if a deeper component matches.

---

## `_parse_attribute_node`

**Signature:**
```
_parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None
```

- Returns a `UsageInfo` for the full dotted name if its leading component is in `imported_names`; otherwise `None`.

**Responsibility:** Handles standalone attribute accesses (i.e., not in the callee position of a call), recording the full dotted path as the usage name.

**When to use:** Called by `extract_usages` for attribute-type nodes that are not the function part of a call expression.

**Design decisions:**
- The full text of the attribute node (e.g., `"module.attr"`) is stored as `name`, enabling `_deduplicate` to suppress the shorter prefix form.

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

- Returns a `UsageInfo` if the identifier is a qualifying usage; `None` if it should be skipped.

**Responsibility:** Handles plain identifier nodes, filtering out those that appear as part of declarations, imports, or parameter definitions rather than as actual usages.

**When to use:** Called by `extract_usages` for every `"identifier"` node encountered during traversal.

**Design decisions:**
- Two-tier parent-type check: nodes whose parent is in `skip_parent_types` are skipped entirely; nodes whose parent is in `skip_name_field_types` are skipped only when they occupy the `"name"` field of the parent, while identifiers on the `"value"` side (e.g., default parameter values) are still reported as usages.
- This asymmetric handling specifically supports cases like `def func(x=some_var)` where `x` is syntax but `some_var` is a real reference.

**Constraints & edge cases:**
- Relies on tree-sitter's `child_by_field_name("name")` API; behavior is undefined if the grammar does not define a `"name"` field for a type listed in `skip_name_field_types`.

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

- `typed_alias_parent_types` – Set of AST node type strings for typed variable declarations (e.g., field declarations, parameter declarations).
- Returns a `dict[str, str]` mapping variable name → type name for every declaration whose type is in `imported_names`.

**Responsibility:** Discovers variables whose declared type is an imported symbol, so that the variable name can be added to the tracking set and its usages subsequently detected.

**When to use:** Called by `usage_analysis.py` before `extract_usages`, to expand the set of tracked names with alias variables.

**Design decisions:**
- Returns an empty dict immediately when `typed_alias_parent_types` is empty, making it safe to call for languages that have no typed declaration tracking configured.
- Delegates the structural parsing of each qualifying node to `_extract_type_and_var`, which absorbs language-specific AST shape differences.
- Excludes entries where the variable name equals the type name to avoid trivially circular mappings.

**Constraints & edge cases:**
- Only direct type names (appearing as `type_identifier` or inside `user_type`) are matched; generic/parameterized types are not handled beyond their outer type identifier.

---

## `_extract_type_and_var`

**Signature:**
```
_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]
```

- Returns a tuple of `(type_name_or_None, list_of_variable_name_strings)`.

**Responsibility:** Abstracts over language-specific AST layouts for typed variable declarations to produce a uniform `(type, [vars])` pair.

**When to use:** Called exclusively by `extract_typed_aliases` for each node whose type is in `typed_alias_parent_types`.

**Design decisions:**
- Handles four structural patterns within a single function to avoid per-language branching in the caller:
  - Direct `type_identifier` child (Java, C/C++)
  - `user_type > type_identifier` (Kotlin)
  - Direct `identifier` / `simple_identifier` child (variable name, Java/Kotlin)
  - `variable_declarator` / `init_declarator > identifier` (Java/C++ initializer forms)
- Only the first qualifying identifier inside a declarator child is extracted.

**Constraints & edge cases:**
- Returns `(None, [])` when no type identifier is found in the immediate child list.
- Does not recurse beyond one level of nesting (declarator child); more deeply nested patterns are not supported.
- Multiple variable declarators on the same declaration line (e.g., `Type a, b;`) are supported via the list return value.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports (`dataclasses`, `tree_sitter`) are from the standard library or third-party packages, which are excluded from this description.

---

### Dependents (modules that import this file)

**`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/usages_py/usages.py` : imports and uses two public functions from this module.

- `usage_analysis.py` → this module via `extract_usages` : to traverse an AST and detect usage locations of imported/tracked symbol names within a source file, returning a list of `UsageInfo` objects for further analysis.
- `usage_analysis.py` → this module via `extract_typed_aliases` : to traverse an AST and identify typed variable declarations (e.g., `Genre genre`) that associate a variable name with an imported type name, returning a mapping used to expand the set of tracked symbol names.

---

### Dependency Direction

- The relationship between `usage_analysis.py` and this module is **unidirectional**: `usage_analysis.py` depends on this module, but this module does not import or reference `usage_analysis.py` in any way.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `root_node` | Caller (`usage_analysis.py`) | A `tree_sitter.Node` representing the root of a parsed AST for an entire source file |
| `imported_names` | Caller | `set[str]` of symbol names whose usages are to be tracked |
| `usage_node_types` | Caller (derived from config) | `dict` with keys `call_types`, `attribute_types`, `skip_parent_types` (required) and `skip_name_field_types`, `skip_parent_types_for_type_ref`, `typed_alias_parent_types` (optional); all values are `set[str]` |
| `typed_alias_parent_types` | Caller | `set[str]` of AST node type names representing typed variable declarations (passed to `extract_typed_aliases`) |

When `usage_node_types` is `None` or empty, no traversal occurs and an empty result is returned immediately.

---

## 2. Transformation Overview

### `extract_usages` pipeline

```
root_node + imported_names + usage_node_types
        │
        ▼
[Stage 1: Config Extraction]
  Unpack call_types, attribute_types, skip_parent_types,
  skip_name_field_types, skip_parent_types_for_type_ref from usage_node_types
        │
        ▼
[Stage 2: DFS AST Traversal]
  Walk every node in the AST via an explicit stack.
  For each node, dispatch to one of five handlers based on node.type:
    - call node          → _parse_call_node()
    - attribute node     → _parse_attribute_node() (only if not function part of a call)
    - qualified_identifier → inline scope-part extraction (C++ ::)
    - type_identifier / namespace_identifier → inline type-ref extraction
    - identifier         → _parse_identifier_node()
  Each handler checks whether the detected name is in imported_names
  and, if so, appends a UsageInfo to usage_list.
        │
        ▼
[Stage 3: Deduplication]
  _deduplicate() groups UsageInfo entries by line number.
  Within each line, shorter names are dropped when a longer dotted name
  (e.g. "module.attr") that starts with them is also present.
  Exact (name, line) duplicates are removed.
        │
        ▼
  list[UsageInfo]  (sorted ascending by line number)
```

### `extract_typed_aliases` pipeline

```
root_node + imported_names + typed_alias_parent_types
        │
        ▼
[Stage 1: DFS AST Traversal]
  Walk every node; when node.type is in typed_alias_parent_types,
  delegate to _extract_type_and_var().
        │
        ▼
[Stage 2: Type/Var Extraction per Node]
  _extract_type_and_var() inspects direct children of the declaration node:
    - type_identifier / user_type → type name
    - identifier / simple_identifier → variable name
    - variable_declarator / init_declarator → nested variable name
  Returns (type_name, [var_names]).
        │
        ▼
[Stage 3: Filter and Map Construction]
  Keep only entries where type_name is in imported_names
  and var_name differs from type_name.
  Accumulate into a flat dict.
        │
        ▼
  dict[str, str]  { variable_name → type_name }
```

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | Deduplicated list of symbol usage locations, sorted by line number ascending |
| `extract_typed_aliases` | `dict[str, str]` | Mapping of variable name → declared type name, for variables whose type is in `imported_names` |
| `_parse_call_node` | `UsageInfo \| None` | Single usage from a call node, or `None` |
| `_parse_attribute_node` | `UsageInfo \| None` | Single usage from an attribute access node, or `None` |
| `_parse_identifier_node` | `UsageInfo \| None` | Single usage from a simple identifier node, or `None` |
| `_deduplicate` | `list[UsageInfo]` | Cleaned, sorted usage list |
| `_extract_type_and_var` | `tuple[str \| None, list[str]]` | `(type_name, [variable_names])` extracted from one declaration node |

No file writes or side effects occur; all outputs are pure return values.

---

## 4. Key Data Structures

### `UsageInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name detected as a usage; may be a simple name (`"module"`) or dotted form (`"module.attr"`) |
| `line` | `int` | 1-based line number where the usage appears in the source file |

---

### `usage_node_types` (input dict)

| Key | Type | Required | Purpose |
|---|---|---|---|
| `call_types` | `set[str]` | Yes | AST node types that represent function calls |
| `attribute_types` | `set[str]` | Yes | AST node types that represent attribute/member access |
| `skip_parent_types` | `set[str]` | Yes | Parent node types whose identifier children are skipped (e.g. import declarations, definitions) |
| `skip_name_field_types` | `set[str]` | No (defaults to `set()`) | Parent node types where only the `name`-field child is skipped; the `value`-field side is detected as a usage |
| `skip_parent_types_for_type_ref` | `set[str]` | No (defaults to `skip_parent_types`) | Separate skip list applied specifically to `type_identifier` and `namespace_identifier` nodes |
| `typed_alias_parent_types` | `set[str]` | No | AST node types representing typed variable declarations; consumed by `extract_typed_aliases` via the caller |

---

### `by_line` (internal to `_deduplicate`)

| Key | Value Type | Purpose |
|---|---|---|
| `line` (int) | `list[UsageInfo]` | Groups all `UsageInfo` entries that share the same line number for redundancy filtering |

---

### Return value of `extract_typed_aliases`

| Key | Value Type | Purpose |
|---|---|---|
| variable name (`str`) | type name (`str`) | Maps a variable (e.g. `"genre"`) to the imported type it was declared with (e.g. `"Genre"`), enabling the caller to expand the set of tracked names |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions on invalid or unexpected input, functions return empty collections (`[]` or `{}`) or `None` as early-exit signals. The caller is expected to handle the absence of results rather than catching exceptions. No logging, retries, or explicit error propagation are present.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing configuration | `usage_node_types` is `None` or empty (falsy) passed to `extract_usages` | Returns an empty list immediately | Yes | No usages are extracted for this file |
| Missing configuration | `typed_alias_parent_types` is empty (falsy) passed to `extract_typed_aliases` | Returns an empty dict immediately | Yes | No typed aliases are extracted for this file |
| Missing optional config key | `skip_name_field_types` or `skip_parent_types_for_type_ref` absent from `usage_node_types` | Falls back to `set()` or `skip_parent_types` respectively via `.get()` | Yes | Analysis proceeds with the fallback default |
| No matching imported name | An AST node's text does not appear in `imported_names` | Returns `None` from the parse helper; entry is silently skipped | Yes | That node contributes no usage entry |
| No matching child in call/attribute node | The expected first child of a call or attribute node is not found | Loop exits without appending; returns `None` | Yes | That call site contributes no usage entry |
| Node with no parent | A node's `.parent` is `None` during parent-type checks | Guard `if parent` short-circuits the check; node is treated as non-skippable | Yes | Identifier or type reference is evaluated normally without parent context |
| Unrecognized node type | A node type does not match any of the handled categories | Node is silently ignored; children are still traversed | Yes | That node contributes no usage entry; subtree is still explored |

---

## 3. Design Notes

- **No exceptions are raised or caught** anywhere in this file. The design assumes that the `tree-sitter` AST is structurally well-formed and that callers supply valid configuration; no defensive validation beyond falsy checks is applied.
- The falsy-check pattern (`if not usage_node_types`) serves as the primary guard for misconfiguration, aligning with the expectation from dependents (`usage_analysis.py`) that pass `None` or an empty set when a language has no tracking defined.
- Optional configuration keys (`skip_name_field_types`, `skip_parent_types_for_type_ref`) are handled through `.get()` with safe defaults, preventing `KeyError` without explicit error handling.
- The `None`-return convention in helper functions (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) keeps error signaling implicit and uniform, avoiding branching exception logic in the main traversal loop.

## Summary

**usages.py** extracts symbol usage locations and typed variable aliases from a Tree-sitter AST.

- `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None) -> list[UsageInfo]` — finds all lines where imported symbols are referenced.
- `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]` — maps variable names to their declared imported types.
- **`UsageInfo`** dataclass: `name: str`, `line: int`.
- **`usage_node_types`** dict keys: `call_types`, `attribute_types`, `skip_parent_types` (all `set[str]`).
