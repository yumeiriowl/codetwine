# Design Document: codetwine/extractors/usages.py

# Overview & Purpose

## 1. Module Summary

Extracts usage locations of imported symbols from a parsed AST and identifies typed variable aliases that may reference those symbols, providing the raw data needed to determine which files depend on which imported names.

## 2. When to Use This Module

- **Call `extract_usages(root_node, imported_names, usage_node_types)`** when you need to find every line in a file where a set of imported symbol names is referenced. Returns a deduplicated list of `UsageInfo` objects, each carrying the symbol name and its 1-based line number. Called by `usage_analysis.py` to locate usages of both direct imports and alias variable names.

- **Call `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)`** when you need to discover local variable names that are declared with an imported type (e.g., a variable `genre` declared as type `Genre`). Returns a `dict[str, str]` mapping variable name → type name, which `usage_analysis.py` uses to expand the tracked name set before calling `extract_usages`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass | Holds a single symbol usage: the symbol name and its 1-based line number |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | DFS-traverses the AST to find all usage locations of the given imported names, covering function calls, attribute access, type references, namespace references, and simple identifiers; returns deduplicated results |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose declared type is in `imported_names`, returning a variable-name → type-name mapping |

## 4. Design Decisions

- **Language-agnostic via `usage_node_types` configuration:** Rather than hard-coding language-specific node type names, `extract_usages` accepts a configuration dict (`usage_node_types`) that supplies the sets of call, attribute, and skip-parent node types. Passing `None` or an empty dict short-circuits the function and returns `[]`, making the module safe to call for languages that have no usage tracking defined.

- **`skip_parent_types_for_type_ref` as a separate skip list:** Type reference nodes (`type_identifier`, `namespace_identifier`) use a distinct skip set from plain identifiers. This allows type references in method parameters and declarations to be detected as dependencies while still excluding import/package declarations—a distinction that a shared skip list would not support.

- **Redundancy removal via `_deduplicate`:** When both `module` and `module.attr` appear on the same line, only the more specific `module.attr` entry is retained. This prevents double-counting when a call node and its inner attribute node are both traversed.

- **`qualified_identifier` handled separately:** C++ scope-resolution expressions (e.g., `geometry::Rectangle`) require dedicated handling because only the left-hand (scope/namespace) part is checked against `imported_names`, preventing duplicate entries that would otherwise arise from also processing the individual `namespace_identifier` child.

# Definition Design Specifications

---

## `UsageInfo`

**Dataclass**

A plain data container representing a single detected usage of a symbol within a source file.

| Field  | Type  | Purpose                                              |
|--------|-------|------------------------------------------------------|
| `name` | `str` | The symbol name as it appears at the usage site (may include dotted form such as `module.attr`) |
| `line` | `int` | 1-based line number of the usage location            |

- **Responsibility:** Provides a structured, immutable-by-convention record of where an imported name was found in the AST so callers can work with typed objects rather than raw tuples.
- **When to use:** Instantiated internally by the extraction functions and returned in lists to callers such as `extract_usages`.

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

- **`root_node`**: The root `Node` of a tree-sitter AST for an entire source file.
- **`imported_names`**: Set of symbol name strings whose occurrences should be tracked.
- **`usage_node_types`**: A language-specific configuration dict (from `USAGE_NODE_TYPES` in `config.py`). `None` causes early return of an empty list.
- **Return**: A deduplicated, line-sorted list of `UsageInfo` objects.

**Responsibility:** Primary public entry point for scanning an AST and collecting every location where any of the given imported names is referenced, covering function calls, attribute accesses, type references, namespace references, C++ qualified identifiers, and simple variable references.

**When to use:** Called by `usage_analysis.py` after the set of names to track has been assembled, to obtain the list of usage locations across a source file.

**Design decisions:**
- Uses an explicit stack-based depth-first search rather than recursion to avoid Python stack-depth limits on large files.
- Each AST node type family (calls, attributes, qualified identifiers, type references, plain identifiers) is dispatched to a dedicated private helper, keeping the main loop a routing layer rather than a processing layer.
- `_TYPE_REFERENCE_NODE_TYPES` (`type_identifier`, `namespace_identifier`) uses a separate skip-list (`skip_parent_types_for_type_ref`) that is less aggressive than the general `skip_parent_types`, deliberately allowing type usages in parameter and method-declaration positions to be reported.
- For `qualified_identifier` (C++ scope resolution), only the leftmost (scope/namespace) part is extracted here; the inner child nodes are not pushed onto the stack for re-processing, preventing duplicate entries.
- Final deduplication is delegated to `_deduplicate`.

**Constraints & edge cases:**
- Returns `[]` immediately when `usage_node_types` is falsy (e.g., `None` or empty dict).
- Required keys in `usage_node_types`: `call_types`, `attribute_types`, `skip_parent_types`. Missing required keys raise `KeyError`.
- Optional keys (`skip_parent_types_for_type_ref`, `skip_name_field_types`) fall back to `skip_parent_types` and `set()` respectively when absent.

---

## `_deduplicate`

**Signature:**
```
_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]
```

- **Return**: A line-sorted, deduplicated list of `UsageInfo`.

**Responsibility:** Removes both exact duplicates (same name and line) and *redundant shorter names* when a more qualified form of the same name (e.g., `module.attr`) is already recorded on the same line.

**When to use:** Called once at the end of `extract_usages` before returning results.

**Design decisions:**
- Groups entries by line number and, within each line, suppresses any name that is a strict prefix of another name on the same line (connected by `.`), ensuring the more-specific dotted form is preserved and the bare module name is not double-counted.
- Processes lines in ascending order so the returned list is inherently sorted.

**Constraints & edge cases:**
- The prefix check is strict: `module` is suppressed only if `module.something` exists on the same line; `module` alone is retained if no dotted extension is present.

---

## `_is_function_part_of_call`

**Signature:**
```
_is_function_part_of_call(node: Node, call_types: set[str]) -> bool
```

- **Return**: `True` if `node` is the callee (function expression) child of a call node; `False` otherwise.

**Responsibility:** Prevents an attribute-access node from being processed twice—once as an attribute and again as the function expression of an enclosing call node.

**When to use:** Called from `extract_usages` before dispatching a node in `attribute_types` to `_parse_attribute_node`.

**Design decisions:**
- Only examines the parent node and checks whether the first matching child (by `id`) is the node in question, matching tree-sitter's convention that the callee is the first significant child of a call node.

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

**Responsibility:** Extracts a `UsageInfo` from a function-call AST node by inspecting only its first child (the callee), covering plain calls, attribute-style calls (`module.func()`), and C++ scope-resolution calls (`ns::func()`).

**When to use:** Called from `extract_usages` when a node whose type is in `call_types` is encountered.

**Design decisions:**
- Deliberately inspects only the first child and then breaks, because subsequent children are arguments that are traversed separately by the main DFS loop.
- For attribute-style callees, the full dotted text is stored as the name but the leading segment is checked against `imported_names`.
- For C++ qualified identifiers, only the first namespace/type/identifier segment is checked and recorded.

**Constraints & edge cases:**
- Returns `None` if the leading name is not in `imported_names`.

---

## `_parse_attribute_node`

**Signature:**
```
_parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None
```

**Responsibility:** Extracts a `UsageInfo` from a standalone (non-call) attribute-access node by checking whether the leftmost name component is an imported name.

**When to use:** Called from `extract_usages` for attribute-type nodes that are confirmed to not be the function part of a call.

**Constraints & edge cases:**
- The full dotted string (e.g., `module.attr`) is stored as `name`; only the first segment determines whether a `UsageInfo` is created.
- Returns `None` if the leading name is not in `imported_names`.

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

**Responsibility:** Determines whether a plain `identifier` node represents an actual usage of an imported name, filtering out positions that are definitional or syntactic (imports, declarations, parameter names).

**When to use:** Called from `extract_usages` for every `identifier`-type node encountered during DFS.

**Design decisions:**
- Distinguishes two parent-based skip categories: `skip_parent_types` causes the whole node to be skipped, while `skip_name_field_types` causes only the `name`-field child to be skipped, allowing the `value`-field sibling (e.g., a default parameter value) to be detected as a usage.

**Constraints & edge cases:**
- If `node.parent` is `None` (root-level identifier), no skip logic applies and the name is checked directly.

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

- **Return**: A mapping of `{variable_name: type_name}` for variables declared with a type that is in `imported_names`.

**Responsibility:** Detects typed variable declarations (e.g., `Genre genre`) where the declared type is an imported name, so that the variable name can itself be tracked as a proxy for the imported type.

**When to use:** Called by `usage_analysis.py` before `extract_usages` to expand the set of trackable names with alias variable names.

**Design decisions:**
- Uses stack-based DFS over the full AST, delegating node-level extraction to `_extract_type_and_var` which abstracts over Java, Kotlin, and C/C++ AST structural differences.
- A variable name is excluded from the alias map when it equals the type name, to avoid trivial self-mappings.

**Constraints & edge cases:**
- Returns `{}` immediately when `typed_alias_parent_types` is falsy.
- Supported node patterns are limited to what `_extract_type_and_var` can parse; declaration patterns not covered by that function are silently ignored.

---

## `_extract_type_and_var`

**Signature:**
```
_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]
```

- **Return**: A 2-tuple of `(type_name_or_None, list_of_variable_name_strings)`.

**Responsibility:** Abstracts over language-specific AST layouts to uniformly extract the declared type name and the declared variable name(s) from a single typed-declaration node.

**When to use:** Called exclusively by `extract_typed_aliases` for each node whose type is in `typed_alias_parent_types`.

**Design decisions:**
- Handles Kotlin's `user_type > type_identifier` nesting as a special case alongside the flat `type_identifier` used by Java and C/C++.
- Handles Java's `variable_declarator` and C/C++'s `init_declarator` as intermediate wrapper nodes that contain the actual variable `identifier`.

**Constraints & edge cases:**
- Returns `(None, [])` when no recognizable type or variable structure is found among the children.
- Only the first `identifier` inside `variable_declarator` / `init_declarator` is collected per wrapper node.

# Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/extractors/usages.py`) has **no project-internal module dependencies**. It imports only from the standard library (`dataclasses`) and the third-party package `tree_sitter`. No project-internal modules are imported.

## Dependents (modules that import this file)

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : imports and calls `extract_usages` to perform AST-based traversal that detects where imported symbol names are actually used within a source file, and imports and calls `extract_typed_aliases` to discover typed variable declarations (e.g., `genre: Genre`) that create local aliases for tracked symbols, so that alias variable names can be added to the usage tracking set.

## Dependency Direction

- The relationship between `codetwine/extractors/usage_analysis.py` and `codetwine/extractors/usages.py` is **unidirectional**: `usage_analysis.py` depends on `usages.py`, and `usages.py` has no knowledge of or dependency on `usage_analysis.py`.

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | AST root node of the file being analyzed |
| `imported_names` | `set[str]` | Set of symbol names whose usages are to be tracked |
| `usage_node_types` | `dict \| None` | Per-language node type configuration obtained from `USAGE_NODE_TYPES` in config |
| `typed_alias_parent_types` | `set[str]` | Set of AST node types representing typed variable declarations (for `extract_typed_aliases`) |

The `usage_node_types` dict carries the following keys:

| Key | Required | Purpose |
|---|---|---|
| `call_types` | Required | Node types representing function calls |
| `attribute_types` | Required | Node types representing attribute access |
| `skip_parent_types` | Required | Parent node types whose children should not be treated as usages |
| `skip_parent_types_for_type_ref` | Optional | Override of `skip_parent_types` used only for type/namespace reference nodes |
| `skip_name_field_types` | Optional | Parent types where only the `name`-field child is skipped; the value side is still detected |
| `typed_alias_parent_types` | Optional | Node types representing typed variable declarations |

---

## 2. Transformation Overview

### `extract_usages` pipeline

```
root_node
    │
    ▼
[DFS traversal of AST via node_stack]
    │
    ├─ call_types node      → _parse_call_node()      → UsageInfo or None
    ├─ attribute_types node → _parse_attribute_node() → UsageInfo or None
    ├─ qualified_identifier → scope part extracted    → UsageInfo or None
    ├─ type_identifier /
    │  namespace_identifier → direct name match       → UsageInfo or None
    └─ identifier           → _parse_identifier_node()→ UsageInfo or None
    │
    ▼
usage_list: list[UsageInfo]  (raw, may contain duplicates)
    │
    ▼
_deduplicate()
    ├─ Group entries by line number
    ├─ For each line: remove shorter names if a more detailed "name.attr" form exists on the same line
    └─ Remove duplicate (name, line) pairs
    │
    ▼
list[UsageInfo]  (deduplicated, sorted by line number)
```

Each node type dispatches to a specialized helper:

- **`_parse_call_node`**: Inspects the first child of a call node. Handles three sub-cases: plain `identifier`, `attribute_types` child (records full `module.func` text), and `qualified_identifier` (C++ `::` scope resolution). Returns as soon as the first child is processed.
- **`_parse_attribute_node`**: Reads the full text of an attribute access node and checks whether the leading segment (before the first `.`) is in `imported_names`.
- **`_parse_identifier_node`**: Checks the parent node type; skips the node if the parent is in `skip_parent_types`. For parents in `skip_name_field_types`, only the `name`-field child is skipped while the value-side identifier is allowed through.

### `extract_typed_aliases` pipeline

```
root_node
    │
    ▼
[DFS traversal via stack]
    │
    └─ node.type in typed_alias_parent_types
           │
           ▼
       _extract_type_and_var(node)
           ├─ Extracts type_name from type_identifier / user_type > type_identifier
           └─ Extracts var_names from identifier / simple_identifier /
              variable_declarator > identifier / init_declarator > identifier
    │
    ▼
Filter: type_name must be in imported_names, var_name must differ from type_name
    │
    ▼
aliases: dict[str, str]  (var_name → type_name)
```

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | Deduplicated list of symbol usage locations, sorted by line number. Empty list when `usage_node_types` is `None` or empty. |
| `extract_typed_aliases` | `dict[str, str]` | Mapping of variable name → type name for typed declarations whose type is in `imported_names`. Empty dict when `typed_alias_parent_types` is empty. |

Both functions produce no side effects and perform no file I/O.

---

## 4. Key Data Structures

### `UsageInfo`

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name being used (may be a dotted name such as `module.attr` for attribute access) |
| `line` | `int` | 1-based line number of the usage location in the source file |

### `usage_node_types` dict

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types that represent function/method calls |
| `attribute_types` | `set[str]` | AST node types that represent attribute/member access expressions |
| `skip_parent_types` | `set[str]` | Parent node types that signal the child identifier should not be recorded as a usage (e.g., import declarations, definitions) |
| `skip_parent_types_for_type_ref` | `set[str]` | Narrower skip list applied only to `type_identifier` and `namespace_identifier` nodes |
| `skip_name_field_types` | `set[str]` | Parent types where only the child in the `name` field is excluded; value-side children are still captured |
| `typed_alias_parent_types` | `set[str]` | Node types for typed variable declarations used by `extract_typed_aliases` |

### `by_line` dict (internal to `_deduplicate`)

| Key | Type | Purpose |
|---|---|---|
| line number | `int` | 1-based line number grouping key |
| value | `list[UsageInfo]` | All raw usage entries recorded on that line |

### `aliases` dict (output of `extract_typed_aliases`)

| Key | Type | Purpose |
|---|---|---|
| variable name | `str` | Name of the variable declared with a typed annotation (e.g., `genre`) |
| value | `str` | The imported type name used in the declaration (e.g., `Genre`) |

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions on invalid or missing input, functions return safe empty defaults (empty list `[]` or empty dict `{}`) when preconditions are not met. No `try-except` blocks are present; error prevention is achieved through guard clauses and defensive checks on node relationships before accessing node properties.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing or None `usage_node_types` | `extract_usages` called with `None` or empty dict | Returns `[]` immediately via guard clause | Yes | No usages extracted; caller receives empty list |
| Missing or None `typed_alias_parent_types` | `extract_typed_aliases` called with empty set | Returns `{}` immediately via guard clause | Yes | No aliases extracted; caller receives empty dict |
| Missing optional keys in `usage_node_types` | Keys `skip_name_field_types` or `skip_parent_types_for_type_ref` absent from the dict | Falls back to `.get()` with safe defaults (`set()` or the value of `skip_parent_types`) | Yes | Correct behavior is maintained using fallback values |
| `node.parent` is `None` | A node at the AST root has no parent | Parent type checks are guarded by `if parent` before access | Yes | Node is processed without parent-context filtering |
| Type or variable name not found in declaration node | `_extract_type_and_var` finds no matching child structure | Returns `(None, [])` | Yes | Declaration is silently skipped; no alias registered |
| Extracted type name not in `imported_names` | A declaration's type exists in the AST but is not a tracked import | Filtered out by `if type_name and type_name in imported_names` | Yes | Declaration silently excluded from alias map |
| Identifier name not in `imported_names` | An identifier node is found during traversal but does not match any tracked name | Filtered out by membership check against `imported_names` | Yes | Node is silently skipped; no usage recorded |
| Duplicate or redundant `UsageInfo` entries | Multiple traversal paths detect the same name/line, or both `module` and `module.attr` appear | Resolved by `_deduplicate` post-processing | Yes | Final list contains only the most specific, non-duplicate entries |

---

## 3. Design Notes

- **No exception-based control flow**: The file contains no `try-except` constructs. All error prevention is structural, relying on guard clauses at function entry points and conditional checks at the point of node property access.
- **Fail-silent on missing data**: When expected AST structures (e.g., a parent node, a named child field, a specific child type) are absent, the affected node is silently skipped. This tolerates AST variations across languages without disrupting the overall traversal.
- **Optional configuration via defaults**: The use of `.get()` with fallback values for optional keys in `usage_node_types` ensures that the function degrades gracefully when callers provide minimal configuration, rather than raising `KeyError`.
- **Post-processing as a correctness layer**: `_deduplicate` serves as a compensating control, correcting over-detection that may result from multiple traversal paths converging on the same symbol. This separates traversal concerns from output correctness concerns.

# Summary

**usages.py** — Extracts symbol usage locations and typed variable aliases from a tree-sitter AST.

**Public API:**
- `UsageInfo` dataclass: `name: str`, `line: int`
- `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None) → list[UsageInfo]`
- `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) → dict[str, str]`

**Key structures:**
- `usage_node_types` dict with keys: `call_types`, `attribute_types`, `skip_parent_types` (sets of AST node type strings)
- `aliases` dict: variable name → type name
