# Design Document: codetwine/extractors/usages.py

## Overview & Purpose

# Overview & Purpose

## Role

This module provides AST-based symbol usage extraction for the CodeTwine project. It exists as a dedicated file to isolate the responsibility of identifying *where* imported symbols are actually referenced within source files, separating this concern from import discovery and dependency graph construction. The public functions are consumed by `codetwine/extractors/usage_analysis.py`, which drives the higher-level analysis pipeline.

The module traverses a Tree-sitter AST via depth-first search and detects usage locations for a given set of symbol names across multiple syntactic patterns: function calls, attribute access, simple identifiers, type references, namespace references, and C++ scope-resolution expressions. It also discovers typed variable declarations so that alias variable names (e.g., `genre` declared as type `Genre`) can be added to the tracking set.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass instance | Data container holding a single symbol usage: the symbol name and its 1-based line number. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | DFS-traverses the AST and returns deduplicated usage locations for all names in `imported_names`, using per-language node type configuration from `usage_node_types`. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose type is an imported name and returns a `variable_name → type_name` mapping. |

---

## Design Decisions

- **Language-agnostic via configuration**: Rather than hardcoding language-specific node type names, the module accepts a `usage_node_types` dict (keyed by `"call_types"`, `"attribute_types"`, `"skip_parent_types"`, etc.) supplied by the caller. This allows the same traversal logic to serve Python, Java, Kotlin, C, and C++ by varying only the configuration.

- **Opt-out via `None` config**: When `usage_node_types` is `None` or empty, `extract_usages` immediately returns `[]`. This is an explicit design decision to safely handle languages for which no usage tracking is configured without raising errors.

- **Deduplication as a post-processing step**: `_deduplicate` removes both exact `(name, line)` duplicates and redundant shorter names when a more-qualified form (e.g., `module` vs. `module.attr`) appears on the same line, ensuring callers receive a clean, minimal result set.

- **Separation of node-type handlers into private helpers**: Each syntactic pattern (call, attribute, identifier, type reference) is handled by a dedicated private function (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) and one inline block for `qualified_identifier`. This limits the complexity of the main DFS loop and makes each pattern independently testable.

- **`skip_name_field_types` for partial skipping**: For node types like `default_parameter`, only the `name`-field child identifier is skipped while the `value`-field identifier is still detected as a usage, using Tree-sitter's `child_by_field_name` API.

## Definition Design Specifications

# Definition Design Specifications

---

## `UsageInfo`

**Type:** Dataclass

**Fields:**
- `name: str` — The symbol name being used (may be a dotted name such as `module.attr` for attribute access).
- `line: int` — 1-based line number of the usage location.

**Responsibility:** A plain data container representing a single detected usage of an imported symbol within a source file. Serves as the unit of result returned by the extraction functions.

---

## `extract_usages`

**Signature:** `(root_node: Node, imported_names: set[str], usage_node_types: dict | None) -> list[UsageInfo]`

**Arguments:**
- `root_node` — The AST root node for the entire file being analysed.
- `imported_names` — The set of symbol names whose usages are to be located.
- `usage_node_types` — A language-specific configuration dict. Required keys: `call_types`, `attribute_types`, `skip_parent_types`. Optional keys: `skip_parent_types_for_type_ref`, `skip_name_field_types`. When `None` or empty, an empty list is returned immediately.

**Return:** A deduplicated, line-number-ascending list of `UsageInfo` objects representing every detected usage of a name in `imported_names`.

**Responsibility:** The primary entry point for usage extraction. Performs a DFS over the entire AST and dispatches each node to the appropriate handler depending on its type, then deduplicates the collected results before returning.

**Important design decisions:**
- Dispatching is done in a priority order: call nodes first, then attribute nodes, then `qualified_identifier` (C++ scope resolution), then type/namespace identifiers, then plain identifiers. This ordering prevents double-counting when a call node and its function-name child would otherwise both be processed.
- `qualified_identifier` is handled inline rather than delegated to a helper because only the leftmost scope/namespace part is recorded, and the child nodes must be explicitly prevented from being re-processed by a separate identifier pass.
- `type_identifier` and `namespace_identifier` use a separate skip list (`skip_parent_types_for_type_ref`) so that type references in parameter and method declarations are captured even when plain identifiers in those positions would be suppressed.

**Edge cases and constraints:**
- If `usage_node_types` is falsy (`None` or empty dict), the function returns `[]` without traversing the AST.
- `imported_names` must be a `set` for O(1) membership testing; passing other iterables is not supported by the type signature.

---

## `_deduplicate`

**Signature:** `(usage_list: list[UsageInfo]) -> list[UsageInfo]`

**Arguments:**
- `usage_list` — A list of `UsageInfo` objects that may contain duplicates or redundant prefix entries.

**Return:** A deduplicated list of `UsageInfo` sorted by ascending line number.

**Responsibility:** Removes two categories of redundancy from the raw usage list: (1) entries where a shorter name is a prefix of a longer dotted name on the same line (e.g. `module` is suppressed when `module.attr` is also present on that line), and (2) exact `(name, line)` duplicate pairs.

**Important design decisions:** The prefix-suppression rule ensures that when an attribute access is recorded as `module.func`, the incidental recording of the bare `module` identifier on the same line does not produce a spurious second entry. Sorting by line number is a guaranteed postcondition of the output.

---

## `_is_function_part_of_call`

**Signature:** `(node: Node, call_types: set[str]) -> bool`

**Arguments:**
- `node` — An attribute node to test.
- `call_types` — Set of node type strings representing function call AST nodes.

**Return:** `True` if the node is the function-name child of an enclosing call node; `False` otherwise.

**Responsibility:** Guards against double-counting by detecting when an attribute node will already be handled by its parent call node. Only the first child of a call node is checked, which is the position occupied by the function expression.

---

## `_parse_call_node`

**Signature:** `(node: Node, imported_names: set[str], attribute_types: set[str]) -> UsageInfo | None`

**Arguments:**
- `node` — A call AST node.
- `imported_names` — Set of names to track.
- `attribute_types` — Set of node type strings representing attribute access.

**Return:** A `UsageInfo` for the call if the leading name is imported, otherwise `None`.

**Responsibility:** Extracts the usage from a function call node by examining only its first child, which holds the function expression. Handles three shapes: a bare identifier call, an attribute-access call (`module.func()`), and a C++ scope-resolution call (`ns::func()`).

**Edge cases and constraints:** Only the first child is inspected; all remaining children (arguments, etc.) are ignored. For dotted calls the full dotted text is stored as `name`, while for scope-resolution calls only the leftmost namespace/identifier segment is stored.

---

## `_parse_attribute_node`

**Signature:** `(node: Node, imported_names: set[str]) -> UsageInfo | None`

**Arguments:**
- `node` — An attribute access AST node.
- `imported_names` — Set of names to track.

**Return:** A `UsageInfo` whose `name` is the full dotted text of the attribute expression if its leading segment is imported, otherwise `None`.

**Responsibility:** Records standalone attribute access (not part of a call) where the root object is an imported name. The full dotted name is preserved so `_deduplicate` can suppress bare-name duplicates on the same line.

---

## `_parse_identifier_node`

**Signature:** `(node: Node, imported_names: set[str], skip_parent_types: set[str], skip_name_field_types: set[str]) -> UsageInfo | None`

**Arguments:**
- `node` — A plain `identifier` AST node.
- `imported_names` — Set of names to track.
- `skip_parent_types` — Parent node types whose identifier children must be ignored entirely (import declarations, definitions, etc.).
- `skip_name_field_types` — Parent node types for which only the child in the `name` field is ignored; identifiers in other field positions (e.g. default values) are still detected.

**Return:** A `UsageInfo` if the identifier is an imported name in a usage context, otherwise `None`.

**Responsibility:** Handles simple variable references while suppressing syntactic identifiers that are part of definitions or import statements. The `skip_name_field_types` mechanism allows partial suppression within a parent node, so that, for example, a default-argument value is detected even when the parameter name itself is not.

**Edge cases and constraints:** When both `skip_parent_types` and `skip_name_field_types` could match the same parent, `skip_name_field_types` takes priority (it is checked first).

---

## `extract_typed_aliases`

**Signature:** `(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]`

**Arguments:**
- `root_node` — The AST root node for the entire file.
- `imported_names` — Set of imported type names to recognise as declaration types.
- `typed_alias_parent_types` — Set of AST node types that represent typed variable declarations (e.g. `field_declaration`, `local_variable_declaration`, `parameter_declaration`).

**Return:** A mapping from variable name to type name for every declaration whose type is in `imported_names`. Only declarations where the variable name differs from the type name are included.

**Responsibility:** Enables downstream code to track variables that hold values of an imported type, so that usages of those variables can be attributed to the same dependency as the type itself (e.g. a variable `genre` of type `Genre` is treated as a usage of the `Genre` dependency).

**Edge cases and constraints:** Returns an empty dict immediately when `typed_alias_parent_types` is falsy. A variable is excluded from the result if its name equals the type name.

---

## `_extract_type_and_var`

**Signature:** `(node: Node) -> tuple[str | None, list[str]]`

**Arguments:**
- `node` — An AST node representing a typed variable declaration.

**Return:** A `(type_name, variable_names)` tuple. `type_name` is `None` if no type identifier was found; `variable_names` may be empty.

**Responsibility:** Abstracts over the different AST shapes used by Java, Kotlin, and C/C++ to express a typed variable declaration, so that `extract_typed_aliases` can remain language-agnostic. Handles direct `type_identifier` children, Kotlin's `user_type` wrapper, and declarator sub-nodes (`variable_declarator`, `init_declarator`) that nest the variable name one level deeper.

**Edge cases and constraints:** Returns `(None, [])` when neither a type identifier nor any variable identifier can be found in the node's children.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. It relies solely on `tree_sitter.Node` (from the tree-sitter library) as its primary external type, which is used as the AST node type throughout all traversal and extraction logic. No other project-internal modules are imported or used.

---

### Dependents (what uses this file)

**`codetwine/extractors/usage_analysis.py`** depends on this file for two distinct extraction functions:

- **`extract_usages`**: Used by `usage_analysis.py` to perform AST-based detection of where imported symbol names are actually referenced within a file. It is called with the AST root node and a set of symbol names to track, and returns the located usage positions for further analysis.

- **`extract_typed_aliases`**: Used by `usage_analysis.py` to discover typed variable declarations whose declared type matches an imported symbol name. The resulting variable-to-type mapping is used to expand the set of names being tracked (e.g., so that a variable `genre` of type `Genre` is also monitored for usages).

**Direction of dependency**: Unidirectional. `usage_analysis.py` depends on this file; this file does not import or reference `usage_analysis.py`.

## Data Flow

# Data Flow

## Input Data

| Parameter | Type | Description |
|---|---|---|
| `root_node` | `Node` | AST root node covering the entire source file |
| `imported_names` | `set[str]` | Set of symbol names whose usages are to be tracked |
| `usage_node_types` | `dict \| None` | Per-language node type configuration (from `config.py`) |

### `usage_node_types` Dictionary Structure

| Key | Required | Type | Purpose |
|---|---|---|---|
| `call_types` | Required | `set[str]` | AST node types representing function calls |
| `attribute_types` | Required | `set[str]` | AST node types representing attribute access |
| `skip_parent_types` | Required | `set[str]` | Parent node types whose identifier children are skipped |
| `skip_parent_types_for_type_ref` | Optional | `set[str]` | Skip list specific to type/namespace references; falls back to `skip_parent_types` |
| `skip_name_field_types` | Optional | `set[str]` | Parent types where only the `name`-field child is skipped; value-side identifiers are still detected |
| `typed_alias_parent_types` | Optional | `set[str]` | AST node types representing typed variable declarations (used by `extract_typed_aliases`) |

---

## Main Data Structures

### `UsageInfo`
```
UsageInfo
├── name: str   # Symbol name (may include dotted form, e.g. "module.attr")
└── line: int   # 1-based line number of the usage location
```

---

## Transformation Flow

### `extract_usages`

```
root_node + imported_names + usage_node_types
        │
        ▼
  DFS traversal (node_stack)
        │
        ├─ call_types node          → _parse_call_node()      → UsageInfo(name, line)
        ├─ attribute_types node     → _parse_attribute_node() → UsageInfo(name, line)
        ├─ qualified_identifier     → extract scope part only → UsageInfo(name, line)
        ├─ type_identifier /
        │  namespace_identifier     → check skip list         → UsageInfo(name, line)
        └─ identifier               → _parse_identifier_node()→ UsageInfo(name, line)
        │
        ▼
  usage_list: list[UsageInfo]  (may contain duplicates)
        │
        ▼
  _deduplicate()
        │
        ▼
  list[UsageInfo]  (sorted by line, deduplicated)
```

**Key filtering rules applied during traversal:**

| Node type | Filter applied |
|---|---|
| `call_types` | Only the first child (function name part) is examined |
| `attribute_types` | Skipped if it is the function part of a call node (`_is_function_part_of_call`) |
| `qualified_identifier` | Skipped if parent is in `skip_parent_types`; only the leftmost scope part is recorded |
| `type_identifier` / `namespace_identifier` | Skipped if parent is in `skip_parent_types_for_type_ref` |
| `identifier` | Skipped if parent is in `skip_parent_types`; if parent is in `skip_name_field_types`, only the `name`-field child is skipped |

### `_deduplicate`

```
list[UsageInfo]  (raw, may contain duplicates)
        │
        ▼
  Group by line number  →  by_line: dict[int, list[UsageInfo]]
        │
        ▼
  For each line (ascending order):
    - Drop shorter name if a dotted extension exists on the same line
      (e.g., drop "module" when "module.attr" is present)
    - Drop entries with duplicate (name, line) keys
        │
        ▼
  list[UsageInfo]  (deduplicated, ascending line order)
```

### `extract_typed_aliases`

```
root_node + imported_names + typed_alias_parent_types
        │
        ▼
  DFS traversal
        │
        └─ node.type in typed_alias_parent_types
                │
                ▼
          _extract_type_and_var(node)
          → (type_name: str | None, var_names: list[str])
                │
                ▼
          filter: type_name in imported_names
                  and var_name != type_name
                │
                ▼
  aliases: dict[str, str]   # { var_name → type_name }
```

**`_extract_type_and_var` child node mapping:**

| Child node type | Extracted as |
|---|---|
| `type_identifier` | `type_name` |
| `user_type` > `type_identifier` | `type_name` (Kotlin) |
| `identifier` / `simple_identifier` | `var_name` |
| `variable_declarator` / `init_declarator` > `identifier` | `var_name` (Java/C++) |

---

## Output Data and Destination

| Function | Output type | Consumer |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | `usage_analysis.py` — used to resolve which files are depended upon |
| `extract_typed_aliases` | `dict[str, str]` (var → type) | `usage_analysis.py` — alias variable names are added to the tracking set so usages of typed variables are also detected |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions when inputs are missing, incomplete, or unrecognized, functions return empty collections (`[]` or `{}`) and silently skip nodes that do not match expected patterns. This allows the caller (`usage_analysis.py`) to continue processing even when certain inputs are unavailable or when AST nodes yield no meaningful result.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `usage_node_types` is `None` or empty | `extract_usages` returns `[]` immediately; `extract_typed_aliases` returns `{}` immediately | No usages or aliases are reported for that language; caller proceeds with empty data |
| `typed_alias_parent_types` is empty or not provided | `extract_typed_aliases` returns `{}` immediately | No typed alias mapping is produced; caller proceeds without alias expansion |
| Optional keys absent from `usage_node_types` dict | `dict.get()` with a default (`set()` or fallback to `skip_parent_types`) silently supplies the default | Processing continues with conservative defaults; no crash |
| AST node has no parent (`node.parent` is `None`) | Parent checks are guarded with `if parent` before accessing `parent.type` | Node is processed without parent-context filtering; no crash |
| AST node text cannot resolve to an imported name | Name lookup against `imported_names` fails silently; no `UsageInfo` is appended | That node produces no usage entry; traversal continues |
| Call or attribute node yields no matching first child | Loop exits via `break` or falls through without returning a result; returns `None` | No `UsageInfo` is produced for that node; traversal continues |
| `_extract_type_and_var` finds no type or variable | Returns `(None, [])` | Declaration is silently skipped; no alias entry is added |
| Decoded name matches nothing in `imported_names` | Condition check fails silently; nothing is appended | Only genuinely imported names generate output |

---

## Design Considerations

The absence of explicit exception handling (`try/except`) is intentional: the code assumes that the tree-sitter `Node` objects passed in are structurally valid, and that all required keys (`call_types`, `attribute_types`, `skip_parent_types`) are present in `usage_node_types` when it is not `None`. Callers are expected to supply well-formed inputs; only the optional/missing-input cases (i.e., `None` config, absent optional keys, absent parent node) are explicitly defended against. This keeps the hot-path DFS traversal free of exception overhead while still preventing crashes from the most common legitimate absence cases.

## Summary

## codetwine/extractors/usages.py

Provides AST-based symbol usage extraction via Tree-sitter DFS traversal. Language-agnostic through a `usage_node_types` configuration dict. Returns empty collections gracefully when config is absent.

**Public interfaces:**
- `UsageInfo` — dataclass holding symbol `name` and 1-based `line`
- `extract_usages(root_node, imported_names, usage_node_types)` → deduplicated `list[UsageInfo]`; detects calls, attribute access, identifiers, type/namespace references, and C++ scope resolution
- `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)` → `dict[var_name → type_name]` for typed variable declarations

Both functions are consumed by `usage_analysis.py`.
