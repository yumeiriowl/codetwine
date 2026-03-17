# Design Document: codetwine/extractors/usages.py

## Overview & Purpose

# Overview & Purpose

This module is responsible for **extracting symbol usage locations from parsed ASTs (Abstract Syntax Trees)**. It forms a dedicated extraction layer within the `codetwine` project, isolating the logic of "where in source code are imported symbols actually used?" from higher-level analysis. By keeping this logic in a standalone file, the project separates AST traversal mechanics from dependency resolution, making each concern independently testable and maintainable.

The module is consumed exclusively by `codetwine/extractors/usage_analysis.py`, which drives the overall dependency analysis pipeline and passes in the root AST node, the set of names to track, and per-language node-type configuration.

---

## Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass instance | Data container holding the name and 1-based line number of a single symbol usage location. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | Traverses the AST via DFS and collects all usage locations of the given imported names, then returns a deduplicated, line-sorted list. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations (e.g. `Genre genre`) and returns a mapping of variable name → type name for variables whose declared type is in `imported_names`. |

---

## Design Decisions

- **DFS via explicit stack**: Rather than relying on recursion, `extract_usages` and `extract_typed_aliases` both use an explicit `node_stack` list for depth-first traversal. This avoids Python's call stack limits on deeply nested ASTs.

- **Language-agnostic via configuration**: The module does not hard-code language-specific node types. Instead, it accepts a `usage_node_types` dict (keyed by `"call_types"`, `"attribute_types"`, `"skip_parent_types"`, etc.) sourced from a separate `config.py`. This allows the same traversal logic to serve multiple languages (Python, Java, C/C++, Kotlin, etc.) by swapping configuration only.

- **Early exit on `None` config**: When `usage_node_types` is `None` or empty, `extract_usages` immediately returns `[]`, signalling that no usage tracking is defined for that language without raising errors.

- **Deduplication as a post-processing step**: `_deduplicate` is applied after the full traversal. It removes both exact `(name, line)` duplicates and "shadowed" shorter names — if both `"module"` and `"module.attr"` appear on the same line, the shorter `"module"` is dropped in favour of the more specific form. This keeps entries maximally informative without redundancy.

- **Separation of node-type handlers**: The four private helpers (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`, `_is_function_part_of_call`) encapsulate the detection logic for each structural category (calls, attribute access, plain identifiers), keeping the main traversal loop in `extract_usages` readable and each case independently understandable.

- **`skip_name_field_types` for partial skipping**: For node types like `default_parameter`, only the `name`-field child identifier is skipped (it is a declaration, not a usage), while the `value`-field child is still detected as a usage. This finer-grained control is handled entirely within `_parse_identifier_node`.

## Definition Design Specifications

# Definition Design Specifications

---

## `UsageInfo`

**Type:** Dataclass

**Fields:**
- `name: str` — The symbol name being used (may be a dotted name such as `module.attr` for attribute access).
- `line: int` — The 1-based line number where the usage appears.

Represents a single detected usage location of an imported symbol within a source file. Serves as the unit of output for the extraction pipeline.

---

## `extract_usages`

**Signature:** `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None) -> list[UsageInfo]`

**Arguments:**
- `root_node` — The tree-sitter AST root node for an entire source file.
- `imported_names` — The set of symbol names whose usages are to be located.
- `usage_node_types` — A language-specific configuration dict (from `USAGE_NODE_TYPES` in `config.py`). Required keys: `call_types`, `attribute_types`, `skip_parent_types`. Optional keys: `skip_parent_types_for_type_ref`, `skip_name_field_types`. When `None` or empty, an empty list is returned immediately.

**Returns:** A deduplicated `list[UsageInfo]`, sorted by ascending line number.

**Responsibility:** Entry point for AST-based usage extraction. Performs a DFS traversal and dispatches each node to the appropriate handler based on its node type.

**Design decisions:**
- Returns `[]` immediately when `usage_node_types` is falsy, making the function safe to call for languages with no tracking configuration without requiring the caller to guard.
- `qualified_identifier` (C++ scope resolution) is handled inline rather than delegated to a helper, because only the leftmost scope segment is recorded, preventing duplicate entries for each sub-part of the expression.
- `type_identifier` and `namespace_identifier` use a separate skip list (`skip_parent_types_for_type_ref`) so that type references in method signatures and parameter declarations are detected as dependencies, while still excluding import/package declarations.
- All child nodes are unconditionally pushed onto the stack after processing each node, so the handlers do not need to recurse.
- Final deduplication is deferred to `_deduplicate` rather than performed during traversal, avoiding per-node set lookups inside the hot loop.

**Edge cases:**
- If `imported_names` is empty, no `UsageInfo` entries will be produced (all name checks fail), but traversal still runs to completion.
- A node whose parent type is in `skip_parent_types` is not added to the result, but its children are still traversed (the skip applies only to the matched node itself).

---

## `_deduplicate`

**Signature:** `_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]`

**Arguments:**
- `usage_list` — A `UsageInfo` list that may contain duplicates or redundant shorter-name entries.

**Returns:** A `list[UsageInfo]` with duplicates and redundant entries removed, sorted by line number in ascending order.

**Responsibility:** Post-processing step that removes noise created by the multi-pass AST traversal, where the same symbol can be detected both as a bare name and as the prefix of a dotted name on the same line.

**Design decisions:**
- When both `module` and `module.attr` appear on the same line, only `module.attr` is retained. This keeps the most specific form and avoids double-counting a single logical usage.
- Exact duplicates sharing both `name` and `line` are also removed.
- Grouping by line before sorting limits the prefix-check comparison to entries on the same line, avoiding cross-line false suppression.

---

## `_is_function_part_of_call`

**Signature:** `_is_function_part_of_call(node: Node, call_types: set[str]) -> bool`

**Arguments:**
- `node` — An attribute-access node to test.
- `call_types` — Set of node type strings representing function call nodes.

**Returns:** `True` if `node` is the function-name child of a call node; `False` otherwise.

**Responsibility:** Prevents double-counting when an attribute access is the callee of a call expression. The call-node handler is responsible for such cases, so the attribute-node handler must skip them.

**Design decisions:** The check inspects the immediate parent rather than walking up the tree, because a call node's function part is always a direct child.

---

## `_parse_call_node`

**Signature:** `_parse_call_node(node: Node, imported_names: set[str], attribute_types: set[str]) -> UsageInfo | None`

**Arguments:**
- `node` — A call-expression AST node.
- `imported_names` — Set of symbol names to track.
- `attribute_types` — Set of node type strings for attribute access nodes.

**Returns:** A `UsageInfo` for the call if its leading name is in `imported_names`; `None` otherwise.

**Responsibility:** Extracts a usage from a function call by examining only the callee portion (first child), handling simple calls, attribute calls, and C++ scope-resolution calls uniformly.

**Design decisions:**
- Only the first child is inspected; the rest of the call node (arguments, etc.) is intentionally ignored here and handled by separate traversal.
- For attribute-style calls (`module.func()`), the full dotted name is stored as `UsageInfo.name` so that `_deduplicate` can later suppress the bare `module` entry if one exists.
- For C++ `qualified_identifier`, only the leftmost segment is recorded, consistent with how `qualified_identifier` is handled in `extract_usages`.

**Edge cases:** Returns `None` if the first child is not an identifier, attribute node, or qualified identifier (e.g., a lambda or parenthesised expression as callee).

---

## `_parse_attribute_node`

**Signature:** `_parse_attribute_node(node: Node, imported_names: set[str]) -> UsageInfo | None`

**Arguments:**
- `node` — An attribute-access AST node.
- `imported_names` — Set of symbol names to track.

**Returns:** A `UsageInfo` using the full dotted text of the node if its leading name is imported; `None` otherwise.

**Responsibility:** Handles standalone attribute accesses (not calls) by recording the full `module.attr` string, enabling `_deduplicate` to discard redundant bare-name entries.

**Edge cases:** Only the segment before the first `.` is checked against `imported_names`; the attribute name itself is never checked.

---

## `_parse_identifier_node`

**Signature:** `_parse_identifier_node(node: Node, imported_names: set[str], skip_parent_types: set[str], skip_name_field_types: set[str]) -> UsageInfo | None`

**Arguments:**
- `node` — A simple `identifier` AST node.
- `imported_names` — Set of symbol names to track.
- `skip_parent_types` — Node types whose identifier children are entirely ignored (import declarations, definitions, etc.).
- `skip_name_field_types` — Node types where only the `name`-field child is skipped; the `value`-field child is still detected as a usage (e.g., default parameter: `def f(x=some_var)` — `x` is skipped, `some_var` is detected).

**Returns:** A `UsageInfo` if the identifier is a genuine usage of an imported name; `None` if it should be skipped.

**Responsibility:** Filters out identifiers that are part of language syntax (definitions, declarations, imports) rather than actual usages, while still detecting identifiers on the value side of constructs such as default parameters.

**Design decisions:** `skip_name_field_types` takes precedence over `skip_parent_types` when both could apply, because the name-field-only skip is the more precise rule.

**Edge cases:** If `node.parent` is `None` (root is an identifier, which is pathological), neither skip applies and the name is checked directly against `imported_names`.

---

## `extract_typed_aliases`

**Signature:** `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]`

**Arguments:**
- `root_node` — The tree-sitter AST root node for an entire source file.
- `imported_names` — Set of imported type names to track.
- `typed_alias_parent_types` — Set of AST node types representing typed variable declarations (e.g., `field_declaration`, `parameter_declaration`).

**Returns:** A `dict[str, str]` mapping variable name → type name, restricted to declarations whose type is in `imported_names`.

**Responsibility:** Enables the usage analysis layer to treat a locally declared variable of an imported type as an alias for that type, so that method calls on the variable are attributed to the imported type's file.

**Design decisions:**
- Returns `{}` immediately when `typed_alias_parent_types` is empty, mirroring the guard in `extract_usages`.
- A variable name that equals the type name is explicitly excluded from the result to avoid trivial self-mappings.
- Only types present in `imported_names` are recorded; declarations of local or primitive types are silently ignored.

**Edge cases:** If multiple variables of the same name are declared (e.g., in different scopes), the last one encountered during DFS wins in the returned dict.

---

## `_extract_type_and_var`

**Signature:** `_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]`

**Arguments:**
- `node` — An AST node representing a typed variable declaration.

**Returns:** A `(type_name, [var_names])` tuple. `type_name` is `None` if no type could be identified; `var_names` may be empty.

**Responsibility:** Abstracts over the structural differences between Java, Kotlin, and C/C++ typed declaration AST shapes so that `extract_typed_aliases` can operate language-agnostically.

**Design decisions:** Handles Kotlin's `user_type > type_identifier` nesting by descending one additional level, whereas Java and C/C++ expose `type_identifier` as a direct child. This keeps all language-specific structural knowledge in one place.

**Edge cases:** Returns `(None, [])` when the node's children do not match any of the recognised patterns, which `extract_typed_aliases` handles safely by checking `type_name` before recording.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. It relies solely on `tree_sitter.Node` (from the tree-sitter library) as its primary external type, and uses Python standard library constructs (`dataclasses`). No other project-internal modules are imported or used.

---

### Dependents (what uses this file)

**`codetwine/extractors/usage_analysis.py`** depends on this file for two core functions:

- **`extract_usages`**: Used by `usage_analysis.py` to traverse the AST of a source file and identify where imported symbols are actually referenced. The results drive the dependency analysis logic — determining which files use which symbols.

- **`extract_typed_aliases`**: Used by `usage_analysis.py` to detect typed variable declarations (e.g., `Genre genre`) and build a mapping from variable names to their declared type names. This allows `usage_analysis.py` to expand the set of tracked names, so that alias variables pointing to imported types are also recognized as usages.

**Dependency direction**: Unidirectional — `usage_analysis.py` depends on this file; this file does not depend on `usage_analysis.py`.

## Data Flow

# Data Flow

## Input Data

| Parameter | Type | Description |
|-----------|------|-------------|
| `root_node` | `Node` | AST root node for the entire source file (from tree-sitter) |
| `imported_names` | `set[str]` | Symbol names whose usages are to be tracked |
| `usage_node_types` | `dict \| None` | Per-language node type configuration (from `USAGE_NODE_TYPES` in config.py) |
| `typed_alias_parent_types` | `set[str]` | AST node types for typed variable declarations (used by `extract_typed_aliases`) |

### `usage_node_types` dict structure

| Key | Required | Purpose |
|-----|----------|---------|
| `call_types` | Yes | Node types representing function calls |
| `attribute_types` | Yes | Node types representing attribute access |
| `skip_parent_types` | Yes | Parent node types whose identifier children should be skipped |
| `skip_parent_types_for_type_ref` | No | Skip list specific to type/namespace references (falls back to `skip_parent_types`) |
| `skip_name_field_types` | No | Parent types where only the `name`-field child is skipped (value side is detected as usage) |
| `typed_alias_parent_types` | No | Node types for typed variable declarations |

---

## Transformation Flow

### `extract_usages`

```
root_node (AST)
      │
      ▼
DFS traversal (node_stack)
      │
      ├─ call_types node      → _parse_call_node()      → UsageInfo (name = full call target)
      ├─ attribute_types node → _parse_attribute_node() → UsageInfo (name = "module.attr")
      ├─ qualified_identifier → extract scope part      → UsageInfo (name = left of ::)
      ├─ type_identifier /
      │  namespace_identifier → direct text match       → UsageInfo (name = type/ns name)
      └─ identifier           → _parse_identifier_node()→ UsageInfo (name = symbol name)
             │
             ▼ (all filtered against imported_names)
        usage_list: []UsageInfo
             │
             ▼
        _deduplicate()
             │
             ▼
        []UsageInfo (sorted by line, redundant entries removed)
```

**Key filtering rules during traversal:**
- `skip_parent_types`: identifier children inside these parent nodes are dropped entirely
- `skip_name_field_types`: only the `name`-field child is dropped; the `value`-field child is kept
- `skip_parent_types_for_type_ref`: same skip logic applied to `type_identifier` / `namespace_identifier`
- Attribute nodes that are the function part of a call node are skipped (handled by the call node instead)

**Deduplication logic (`_deduplicate`):**
- Groups `UsageInfo` entries by line number
- On the same line, if both `"module"` and `"module.attr"` exist, the shorter `"module"` entry is dropped
- Exact `(name, line)` duplicates are removed

### `extract_typed_aliases`

```
root_node (AST)
      │
      ▼
DFS traversal
      │
      └─ typed_alias_parent_types node
              │
              ▼
         _extract_type_and_var()
              │  ┌─ type_name  (from type_identifier / user_type > type_identifier)
              │  └─ var_names  (from identifier / simple_identifier / variable_declarator / init_declarator)
              │
              ▼  (filter: type_name in imported_names, var_name != type_name)
         aliases: dict[var_name → type_name]
```

---

## Output Data

### `UsageInfo` dataclass

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Symbol name as used (may be `"module.attr"` for attribute access, or a plain name) |
| `line` | `int` | 1-based line number of the usage location |

### Function return types

| Function | Return Type | Destination |
|----------|-------------|-------------|
| `extract_usages` | `list[UsageInfo]` | Consumed by `usage_analysis.py` to locate symbol usage positions |
| `extract_typed_aliases` | `dict[str, str]` | Consumed by `usage_analysis.py` to expand tracking set with alias variable names (e.g. `genre` → `Genre`) |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** strategy. Rather than raising exceptions on unexpected or missing input, functions return empty collections (`[]` or `{}`) and `None` as sentinel values, allowing callers to continue execution safely without interruption.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `usage_node_types` is `None` or empty | `extract_usages` returns `[]` immediately | No usages are extracted; caller receives an empty list |
| `typed_alias_parent_types` is empty | `extract_typed_aliases` returns `{}` immediately | No aliases are extracted; caller receives an empty dict |
| Call node has no matching child | `_parse_call_node` returns `None` | The call node is silently skipped; no usage is recorded |
| Attribute node has no imported leading name | `_parse_attribute_node` returns `None` | The attribute access is silently skipped |
| Identifier node has a disqualifying parent type | `_parse_identifier_node` returns `None` | The identifier is silently skipped |
| Type or variable name not found in a typed declaration node | `_extract_type_and_var` returns `(None, [])` | The declaration is silently skipped; no alias is recorded |
| `skip_name_field_types` key absent from `usage_node_types` | `dict.get()` returns an empty `set()` as default | Processing continues without that filter |
| `skip_parent_types_for_type_ref` key absent from `usage_node_types` | Falls back to `skip_parent_types` value | Type reference filtering uses the standard skip list |

## Design Considerations

- **Optional configuration keys** in `usage_node_types` are accessed via `dict.get()` with safe defaults (`set()` or a fallback value), ensuring the module remains functional even when a language's configuration is partially defined.
- **Guard clauses at function entry points** (`if not usage_node_types`, `if not typed_alias_parent_types`) act as the primary defensive layer, eliminating the need for deeper checks when configuration is absent.
- **`None` as a sentinel** from helper functions (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`) keeps the main traversal loop clean; the caller simply skips `None` returns without any exception handling overhead.
- No exceptions are explicitly raised or caught anywhere in the file, which means unexpected AST structures or unrecognized node types are silently bypassed rather than surfaced to the caller.

## Summary

**`codetwine/extractors/usages.py`** extracts symbol usage locations from tree-sitter ASTs. It exposes two public functions: `extract_usages` (DFS traversal returning a deduplicated, line-sorted `list[UsageInfo]` of where imported names appear) and `extract_typed_aliases` (returns a `dict[var_name → type_name]` for typed declarations whose type is an imported name). `UsageInfo` is a dataclass holding a symbol `name` (possibly dotted) and 1-based `line`. Behavior is language-agnostic via a `usage_node_types` config dict. Both functions return empty collections when configuration is absent. Consumed exclusively by `usage_analysis.py`.
