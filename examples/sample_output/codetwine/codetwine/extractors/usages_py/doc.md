# Design Document: codetwine/extractors/usages.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts usage locations of imported symbols from a parsed AST and returns structured location data, enabling callers to determine which lines in a source file reference specific imported names.

## 2. When to Use This Module

- **Call `extract_usages`** when you have an AST root node and a set of imported symbol names and need to find every line in the file where those symbols are referenced (function calls, attribute access, identifiers, type references, and namespace references). Returns a deduplicated list of `UsageInfo` objects.
- **Call `extract_typed_aliases`** when you need to discover variables declared with an imported type (e.g., `Genre genre`) so that the variable name can be added to the tracking set as an alias for the type. Returns a `dict` mapping variable names to their declared type names.

Both functions are consumed by `codetwine/extractors/usage_analysis.py` to build cross-file dependency graphs.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | — | Data class holding the symbol name and 1-based line number of a single usage location. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | Traverses the AST via DFS to find all usage locations of the given imported names, covering calls, attribute access, identifiers, type references, and namespace references; returns a deduplicated, line-sorted list. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose declared type is in `imported_names`, returning a mapping of variable name → type name. |

## 4. Design Decisions

- **Per-language node type configuration via `usage_node_types`**: Rather than hard-coding AST node type names, the module accepts a configuration dict (`call_types`, `attribute_types`, `skip_parent_types`, and optional keys) so the same traversal logic supports multiple languages (Python, Java, C/C++, Kotlin, etc.) without branching in the core logic.
- **Early return on missing config**: `extract_usages` returns an empty list immediately when `usage_node_types` is `None` or empty, making it safe to call for languages that have no usage tracking defined.
- **Deduplication of redundant entries**: When both `module` and `module.attr` are detected on the same line, the shorter name is discarded in favor of the more specific form, preventing redundant dependency edges in the caller.
- **`qualified_identifier` handled at the parent level**: C++ scope-resolution expressions (`geometry::Rectangle`) are processed as a unit at the `qualified_identifier` node rather than letting each child `namespace_identifier` or `identifier` be processed independently, preventing duplicate entries while still capturing the usage.

## Definition Design Specifications

# Definition Design Specifications

---

## `UsageInfo`

**Signature:** `@dataclass class UsageInfo`

**Responsibility:** Holds the identity and source location of a single detected symbol usage, serving as the atomic unit of output from the extraction pipeline.

**When to use:** Instantiated internally by extraction helpers whenever a node in the AST is confirmed to reference an imported name; also consumed by callers in `usage_analysis.py`.

**Fields:**

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The symbol name as it appears at the usage site (may include dotted path, e.g. `module.attr`) |
| `line` | `int` | 1-based line number of the usage in the source file |

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
- `root_node`: AST root for the entire file.
- `imported_names`: Set of symbol name strings to track.
- `usage_node_types`: Language-specific node type configuration dict, or `None`.
- Returns: A list of `UsageInfo` objects (deduplicated, sorted by line number).

**Responsibility:** Entry point for AST-based usage detection; traverses the entire file's AST and collects every location where any name in `imported_names` is referenced.

**When to use:** Called by `usage_analysis.py` once per file to obtain all usage sites of a set of imported names.

**Design decisions:**
- Returns an empty list immediately when `usage_node_types` is `None` or falsy, making unsupported languages a no-op.
- Uses an explicit stack-based DFS rather than recursion to avoid stack-overflow on deep ASTs.
- Dispatches to specialized helpers (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) based on node type, keeping each case isolated.
- `qualified_identifier` (C++ scope resolution) is handled inline: only the leftmost (scope) part is recorded to prevent duplication with inner `namespace_identifier` / `identifier` nodes that would otherwise be visited separately.
- `type_identifier` / `namespace_identifier` nodes use a separate skip-list (`skip_parent_types_for_type_ref`) so that type references in parameter and method declaration positions are captured, unlike plain identifiers.
- Results are passed through `_deduplicate` before returning.

**Constraints & edge cases:**
- `usage_node_types` must contain keys `"call_types"`, `"attribute_types"`, and `"skip_parent_types"`; missing these raises a `KeyError`.
- `"skip_name_field_types"` and `"skip_parent_types_for_type_ref"` are optional; absent keys fall back to an empty set and `skip_parent_types`, respectively.
- Child nodes of a `qualified_identifier` are not pushed onto the stack when the parent is in `skip_parent_types`; children are pushed instead.

---

## `_deduplicate`

**Signature:**
```
_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]
```
- `usage_list`: Potentially redundant list of `UsageInfo` objects.
- Returns: Deduplicated `UsageInfo` list sorted by ascending line number.

**Responsibility:** Removes two categories of redundancy from the raw usage list: shorter names that are strict prefixes of a longer dotted name on the same line (e.g., `module` when `module.attr` also appears), and exact `(name, line)` duplicate pairs.

**When to use:** Called at the end of `extract_usages` before returning results to the caller.

**Design decisions:**
- Groups entries by line number first, then applies prefix-suppression within each group. This limits the comparison scope to entries sharing a line, avoiding cross-line false suppressions.
- A `seen_keys` set ensures exact duplicates are removed in a single pass after prefix filtering.

**Constraints & edge cases:**
- The prefix check is exact string prefix with a trailing `.` separator, so `module2` is not suppressed by `module.attr`.
- Output order is determined by sorted line number, not by the original list order.

---

## `_is_function_part_of_call`

**Signature:**
```
_is_function_part_of_call(node: Node, call_types: set[str]) -> bool
```
- `node`: An attribute-type AST node.
- `call_types`: Set of node type strings representing function call nodes.
- Returns: `True` if this attribute node is the callee position of a call node; `False` otherwise.

**Responsibility:** Prevents double-counting when an attribute access is the function position of a call expression, because the call node itself is already processed separately.

**When to use:** Called inside `extract_usages` before deciding whether to process a standalone attribute node.

**Design decisions:**
- Only the first child of the parent call node that is an `identifier` or has the same type as `node` is compared by node ID; subsequent children are ignored. This reflects the AST convention that the callee is always the first positional child.

**Constraints & edge cases:**
- Returns `False` if `node` has no parent or the parent is not in `call_types`.

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
- `node`: A call-type AST node.
- `imported_names`: Set of names to track.
- `attribute_types`: Set of node type strings for attribute access nodes.
- Returns: A `UsageInfo` if the leading name is imported; `None` otherwise.

**Responsibility:** Extracts the usage from a function call node by inspecting only the callee (first child), covering simple calls, attribute-access calls, and C++ scope-resolution calls.

**When to use:** Called by `extract_usages` whenever a node whose type is in `call_types` is encountered.

**Design decisions:**
- Explicitly breaks after examining the first child, since only the callee position is relevant. Argument identifiers are handled elsewhere in the traversal.
- For attribute-access callees, the full dotted text is stored as the name while only the leading segment is checked against `imported_names`.
- For `qualified_identifier` callees (C++ `::` syntax), only the leftmost part is checked and stored.

**Constraints & edge cases:**
- Returns `None` if the first child does not match any recognized callee pattern or if its name is not in `imported_names`.

---

## `_parse_attribute_node`

**Signature:**
```
_parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None
```
- `node`: An attribute-access AST node.
- `imported_names`: Set of names to track.
- Returns: A `UsageInfo` using the full dotted text as the name, or `None`.

**Responsibility:** Handles standalone attribute accesses (i.e., not the callee of a call) by capturing the full `module.attr` text and verifying the leading segment is imported.

**When to use:** Called by `extract_usages` for attribute nodes that are not the function position of a call.

**Constraints & edge cases:**
- The name stored in `UsageInfo` is the full attribute text, not just the leading segment; deduplication in `_deduplicate` relies on this to suppress plain `module` entries on the same line.
- Returns `None` if the leading segment is not in `imported_names`.

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
- `node`: An `identifier`-type AST node.
- `imported_names`: Set of names to track.
- `skip_parent_types`: Set of parent node types whose children are entirely skipped.
- `skip_name_field_types`: Set of parent node types where only the `name`-field child is skipped, while the `value`-side child is treated as a usage.
- Returns: A `UsageInfo` if the identifier is a usage of an imported name; `None` otherwise.

**Responsibility:** Handles simple variable reference identifiers while filtering out syntactic positions such as import declarations, definitions, and parameter name declarations.

**When to use:** Called by `extract_usages` for every `identifier` node encountered during traversal.

**Design decisions:**
- `skip_name_field_types` enables partial skipping: for constructs like default parameter declarations (`def func(x=some_var)`), the parameter name `x` is suppressed but the default value `some_var` is detected as a usage. The distinction is made via the AST field name `"name"` on the parent node.
- `skip_parent_types` takes precedence only when `skip_name_field_types` does not match the parent, avoiding a conflict between the two skip mechanisms.

**Constraints & edge cases:**
- Returns `None` when the node has no parent, but still proceeds to the `imported_names` check in that case (since no parent means no skip applies).
- Returns `None` if the identifier text is not in `imported_names`.

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
- `root_node`: AST root for the entire file.
- `imported_names`: Set of imported type names to track.
- `typed_alias_parent_types`: Set of AST node type strings representing typed variable declarations.
- Returns: A `dict[str, str]` mapping variable name → type name (only for types in `imported_names`).

**Responsibility:** Discovers variables declared with an imported type so that their names can be added to the tracked set, enabling usage detection of aliased references (e.g., a variable `genre` of type `Genre`).

**When to use:** Called by `usage_analysis.py` before `extract_usages` to augment the `imported_names` set with alias variable names.

**Design decisions:**
- Uses a full DFS stack traversal; when a node matches `typed_alias_parent_types`, delegates to `_extract_type_and_var` for language-agnostic field extraction.
- Excludes entries where the variable name equals the type name to avoid self-referential mappings.
- Returns an empty dict immediately when `typed_alias_parent_types` is falsy.

**Constraints & edge cases:**
- Only variables whose declared type is present in `imported_names` are included.
- Multiple variables declared in the same declaration node (e.g., `int a, b`) are all captured.

---

## `_extract_type_and_var`

**Signature:**
```
_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]
```
- `node`: An AST node representing a typed variable declaration.
- Returns: A tuple of `(type_name_or_None, list_of_variable_name_strings)`.

**Responsibility:** Abstracts over AST structural differences across Java, Kotlin, and C/C++ to extract a type name and its associated variable names from a single declaration node.

**When to use:** Called internally by `extract_typed_aliases` for each node matching `typed_alias_parent_types`.

**Design decisions:**

| Language | Type node path | Variable node path |
|----------|---------------|--------------------|
| Java | `type_identifier` (direct child) | `variable_declarator` → `identifier` |
| Kotlin | `user_type` → `type_identifier` | `simple_identifier` (direct child) |
| C/C++ | `type_identifier` (direct child) | `init_declarator` → `identifier` |

- Returns `(None, [])` when neither a type nor variable names are found, allowing the caller to safely ignore the result.

**Constraints & edge cases:**
- Only the first `identifier` child inside a `variable_declarator` or `init_declarator` is collected (loop breaks after the first match).
- Does not handle nested or complex type expressions beyond `user_type` → `type_identifier`.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the standard library (`dataclasses`) and the third-party package `tree_sitter`. No internal modules from the project are imported.

---

### Dependents (modules that import this file)

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages_py/usages.py` : Uses `extract_usages` to traverse the AST and collect `UsageInfo` records for imported symbol names found in source files, and uses `extract_typed_aliases` to discover variable-to-type mappings for typed declarations so that alias variable names can be added to the symbol tracking set.

---

### Dependency Direction

- The relationship between `codetwine/extractors/usage_analysis.py` and this file is **unidirectional**: `usage_analysis.py` imports from this file, but this file does not import from `usage_analysis.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | AST root node covering the entire source file, produced by tree-sitter parsing |
| `imported_names` | `set[str]` | Set of symbol names whose usages are to be tracked (e.g. `{"Genre", "User"}`) |
| `usage_node_types` | `dict \| None` | Per-language configuration dict sourced from `USAGE_NODE_TYPES` in `config.py`; controls which AST node types are treated as calls, attribute accesses, or skipped contexts |
| `typed_alias_parent_types` | `set[str]` | Set of AST node type strings representing typed variable declarations; used only by `extract_typed_aliases` |

The `usage_node_types` dict has the following expected keys:

| Key | Type | Required | Purpose |
|---|---|---|---|
| `call_types` | `set[str]` | Required | AST node types representing function calls |
| `attribute_types` | `set[str]` | Required | AST node types representing attribute access |
| `skip_parent_types` | `set[str]` | Required | Parent node types whose identifier children are skipped (imports, definitions, etc.) |
| `skip_name_field_types` | `set[str]` | Optional | Parent types where only the `name`-field child is skipped; the value side is still detected |
| `skip_parent_types_for_type_ref` | `set[str]` | Optional | Skip list specific to type/namespace reference nodes; defaults to `skip_parent_types` |
| `typed_alias_parent_types` | `set[str]` | Optional | Typed variable declaration node types; consumed by callers before passing to `extract_usages` |

---

## 2. Transformation Overview

### `extract_usages` pipeline

```
root_node
    │
    ▼
[1] Guard: usage_node_types is None/empty → return []
    │
    ▼
[2] Unpack language config
    call_types, attribute_types, skip_parent_types,
    skip_name_field_types, skip_parent_types_for_type_ref
    │
    ▼
[3] DFS traversal of the AST (stack-based)
    For each node, dispatch by node.type:
    ┌─────────────────────────────────────────────────────┐
    │ call_types          → _parse_call_node()            │
    │ attribute_types     → _parse_attribute_node()       │
    │ qualified_identifier→ extract scope (::) prefix     │
    │ type_identifier /   → check skip_parent_types_for_  │
    │ namespace_identifier  type_ref, emit if imported    │
    │ identifier          → _parse_identifier_node()      │
    └─────────────────────────────────────────────────────┘
    Each match emits a UsageInfo and appends to usage_list
    │
    ▼
[4] _deduplicate(usage_list)
    - Group entries by line number
    - Drop shorter name when "name.attr" exists on the same line
    - Drop duplicate (name, line) pairs
    │
    ▼
list[UsageInfo]  (sorted by line, deduplicated)
```

### `extract_typed_aliases` pipeline

```
root_node
    │
    ▼
[1] Guard: typed_alias_parent_types is empty → return {}
    │
    ▼
[2] DFS traversal of the AST (stack-based)
    For each node whose type is in typed_alias_parent_types:
        _extract_type_and_var(node)
        → (type_name, [var_names])
    │
    ▼
[3] Filter: keep only entries where type_name is in imported_names
    Build aliases dict: var_name → type_name
    (skip entries where var_name == type_name)
    │
    ▼
dict[str, str]  e.g. {"genre": "Genre"}
```

### Helper dispatch detail

| Helper | Input | Logic | Output |
|---|---|---|---|
| `_parse_call_node` | call node | Inspects first child: `identifier` → simple call; `attribute_types` → `module.func()` style; `qualified_identifier` → C++ `::` style | `UsageInfo \| None` |
| `_parse_attribute_node` | attribute node | Reads full text, checks leading name (before `.`) against `imported_names` | `UsageInfo \| None` |
| `_parse_identifier_node` | identifier node | Skips if parent is in `skip_parent_types`; for `skip_name_field_types` parents skips only the `name`-field child | `UsageInfo \| None` |
| `_is_function_part_of_call` | attribute node | Checks whether the node is the first child of a parent call node | `bool` |
| `_extract_type_and_var` | declaration node | Walks children for `type_identifier`/`user_type` (type) and `identifier`/`variable_declarator`/`init_declarator` (variable names) | `(str \| None, list[str])` |

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | Deduplicated list of symbol usage locations, sorted by line number in ascending order |
| `extract_typed_aliases` | `dict[str, str]` | Mapping of variable name → type name for typed declarations whose type is in `imported_names` |

There are no file writes or side effects. All outputs are pure return values.

---

## 4. Key Data Structures

### `UsageInfo`

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name detected as a usage; may be a simple name (`"Genre"`) or a qualified name (`"module.func"`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

### `usage_list` (intermediate)

| Aspect | Detail |
|---|---|
| Type | `list[UsageInfo]` |
| Purpose | Accumulates all candidate usages during DFS traversal before deduplication |
| Lifecycle | Built during traversal, consumed and replaced by `_deduplicate` |

### `by_line` (inside `_deduplicate`)

| Key | Value Type | Purpose |
|---|---|---|
| `int` (line number) | `list[UsageInfo]` | Groups all `UsageInfo` entries sharing the same line for redundancy elimination |

### `aliases` (output of `extract_typed_aliases`)

| Key | Value Type | Purpose |
|---|---|---|
| `str` (variable name) | `str` (type name) | Maps a declared variable (e.g. `"genre"`) to its declared type (e.g. `"Genre"`); used by callers to expand the set of tracked names |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions or terminating on unexpected or missing input, functions return safe empty values (`[]`, `{}`, `None`) when preconditions are not met. The traversal logic silently skips AST nodes that do not match expected patterns, allowing the caller to continue processing without interruption.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing configuration | `usage_node_types` is `None` or empty (falsy) in `extract_usages` | Returns an empty list immediately | Yes | No usages are extracted for that file; caller receives `[]` |
| Missing configuration | `typed_alias_parent_types` is empty or falsy in `extract_typed_aliases` | Returns an empty dict immediately | Yes | No typed aliases are extracted; caller receives `{}` |
| Missing optional config keys | `skip_name_field_types` or `skip_parent_types_for_type_ref` absent from `usage_node_types` | Falls back to `set()` or `skip_parent_types` respectively via `.get()` with defaults | Yes | Traversal continues using the fallback value; no data loss |
| Unmatched AST node type | A node type does not match any of the recognized categories during DFS traversal | Node is silently skipped; its children are still added to the stack | Yes | That node contributes no usage entry; traversal continues normally |
| Absent parent node | `node.parent` is `None` when checking skip conditions in identifier/type-reference handling | Guarded by `if parent and ...`; the absence is treated as non-skippable | Yes | The node is processed as a normal usage candidate |
| No matching child in call/attribute node | The first child of a call or attribute node does not match any expected type | `_parse_call_node` or `_parse_attribute_node` returns `None` | Yes | No usage is recorded for that node; traversal continues |
| Name not in imported set | An identifier or type reference is found but its text is not in `imported_names` | Silently ignored; no `UsageInfo` is appended | Yes | Node produces no output; no side effects |
| Duplicate or redundant entries | Multiple `UsageInfo` records with the same `(name, line)` key, or a short name co-existing with `name.attr` on the same line | Removed by `_deduplicate` using a `seen_keys` set and prefix-matching logic | Yes | Final list is deduplicated; no data is surfaced to the caller twice |

---

## 3. Design Notes

- **No exception handling is present in the file.** The entire error strategy relies on defensive conditional checks (`if not`, `if parent and`, `.get()` with defaults) rather than `try/except` blocks. This is a deliberate design choice that keeps the traversal lightweight and avoids disrupting the caller's pipeline when individual nodes or configurations are incomplete.
- **Early-return guards** at the entry points of `extract_usages` and `extract_typed_aliases` serve as the primary safety mechanism for missing configuration, ensuring the DFS loop is never entered unnecessarily.
- **Optional configuration keys** are handled with dictionary `.get()` and explicit fallback values, meaning partial configuration is tolerated without failure.
- The deduplication step in `_deduplicate` acts as a post-processing safety net rather than an error handler, but it implicitly absorbs redundancy that could arise from multiple AST node types legitimately matching the same source token.

## Summary

**usages.py** — Extracts usage locations of imported symbols from a tree-sitter AST.

**Public API:**
- `UsageInfo` dataclass: `name: str`, `line: int`
- `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None) -> list[UsageInfo]`
- `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]`

**Key data:** `list[UsageInfo]` (deduplicated, line-sorted usage sites); `dict[str, str]` mapping variable name → declared type name for typed alias discovery.
