# Design Document: codetwine/extractors/definitions.py

## Overview & Purpose

## Overview & Purpose

This file implements a language-agnostic AST-based definition extractor for the CodeTwine project. Its sole responsibility is to walk a parsed syntax tree (provided by `tree-sitter`) and identify named definitions—functions, classes, variables, types, and similar constructs—returning them as structured records sorted by line number.

The file exists as a separate module because definition extraction is a discrete, reusable concern shared by at least three distinct consumers (`import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py`). Isolating it here avoids duplication and decouples the extraction logic from how results are consumed (symbol mapping, context building, or usage analysis).

---

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass instance | Data container holding a single extracted definition's identity and source location. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | BFS-traverses the AST and returns all named definitions sorted by ascending start line, using `definition_dict` to drive per-language extraction rules. |

All other functions in the file (`_parse_decorated_definition`, `_parse_definition_node`, `_extract_name`, `_extract_assignment_name`, `_extract_variable_declarator_name`, `_extract_function_declarator_name`, `_extract_init_declarator_name`, `_extract_destructured_names`, `_collect_identifiers_from_pattern`) are private helpers (underscore-prefixed) and are not part of the public API.

---

### Design Decisions

**Data-driven dispatch via `definition_dict`.**  
Rather than hard-coding language-specific logic, `extract_definitions` accepts a caller-supplied `definition_dict` that maps AST node types to name-extraction strategies. This keeps the traversal algorithm language-neutral; language specifics live in the dictionary provided at the call site.

**Sentinel values for deep-nested name extraction.**  
When a definition's name is not a direct child of the definition node (e.g., a C/C++ `function_definition` where the name is buried inside `function_declarator`), the dictionary value is a `__sentinel__` string (e.g., `"__function_declarator__"`). `_extract_name` acts as a dispatcher that routes these sentinel values to dedicated extraction functions, avoiding a proliferation of special-cased branches in the main traversal loop.

**BFS with graceful fallback.**  
The traversal uses a `deque`-based BFS. When name extraction fails for a matched node type (e.g., a C/C++ forward declaration has no `init_declarator`), the node's children are enqueued rather than discarding the subtree. This allows deeper definitions (such as a `function_declarator` nested inside a `declaration`) to be discovered without requiring explicit rules for every intermediate node type.

**Container-type pass-through.**  
Certain node types (currently `namespace_definition`) are both recorded as definitions and have their children enqueued, because they legitimately contain further named definitions. This is controlled via the internal `_CONTAINER_DEFINITION_TYPES` set.

**Destructuring as a first-class case.**  
When standard single-name extraction fails, `_extract_destructured_names` is tried before falling back to child traversal. This covers patterns such as Python tuple unpacking (`X, Y = 1, 2`) and JS/TS object/array destructuring (`const { a, b } = obj`), producing one `DefinitionInfo` per extracted name over the same source range.

## Definition Design Specifications

# Definition Design Specifications

---

## `DefinitionInfo`

A plain data class (frozen via `@dataclass`) that holds the extracted metadata for a single definition found in a source file.

| Field | Type | Meaning |
|---|---|---|
| `name` | `str` | The identifier name of the definition (function, class, variable, etc.) |
| `type` | `str` | The AST node type string as returned by tree-sitter (e.g. `"function_definition"`) |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

Line numbers are stored as 1-based to match conventional editor and tool conventions, requiring a `+1` adjustment from tree-sitter's 0-based `start_point`/`end_point`.

---

## `extract_definitions`

**Signature:** `extract_definitions(root_node: Node, definition_dict: dict[str, str]) -> list[DefinitionInfo]`

The primary public entry point. Traverses the entire AST rooted at `root_node` via BFS and collects all definitions recognized by `definition_dict`, returning them sorted by ascending start line.

**Why it exists:** Callers (import mapping, file analysis, usage analysis) need a flat, ordered list of all named definitions in a file regardless of nesting depth. BFS rather than recursive DFS is used so that traversal order and depth can be controlled explicitly via the queue.

**Design decisions:**

- `definition_dict` is language-agnostic: the caller supplies the mapping of node types to name-extraction strategies, making this function reusable across all supported languages without modification.
- `decorated_definition` is handled as a special case before the general branch because the decorator wraps an inner definition node; the outer node's line range must cover the decorator lines, not just the inner node.
- Container types (currently `namespace_definition`) explicitly re-enqueue their children even after being recorded, because a namespace can itself contain further definitions.
- `preproc_def` nodes whose names match `_INCLUDE_GUARD_RE` are silently discarded to avoid false-positive variable registrations from C/C++ header guards.
- When name extraction for a known definition node fails, children are enqueued rather than the node being dropped. This handles C/C++ forward declarations where the actual declarator is nested one level deeper.
- Destructuring assignments are tried before the BFS fallback: if multiple names are found, each is recorded as a separate `DefinitionInfo` sharing the same `type`, `start_line`, and `end_line`.

**Constraints:** `root_node` must be a valid tree-sitter `Node` covering the full file. `definition_dict` values must be either a direct child node type string or a recognized `__sentinel__` string; unrecognized values will silently fall through to the direct-child search.

---

## `_parse_decorated_definition`

**Signature:** `_parse_decorated_definition(node: Node, definition_dict: dict[str, str]) -> DefinitionInfo | None`

Handles `decorated_definition` nodes by locating the first child that is itself a recognized, non-decorated definition node, extracting its name, and then expanding the line range to cover the entire decorated block (decorator lines included).

**Why it exists:** Tree-sitter represents `@decorator` + `def f():` as a single `decorated_definition` wrapping an inner `function_definition`. Without this handler the decorator lines would be excluded from the reported range.

Returns `None` if no recognizable inner definition is found among the children.

---

## `_parse_definition_node`

**Signature:** `_parse_definition_node(node: Node, name_node_type: str) -> DefinitionInfo | None`

Converts a single definition AST node into a `DefinitionInfo` by delegating name extraction to `_extract_name`. Returns `None` when the name cannot be determined.

**Why it exists:** Centralises the construction of `DefinitionInfo` so that line-number conversion (`start_point[0] + 1`) and field assignment are not duplicated across multiple call sites.

---

## `_extract_name`

**Signature:** `_extract_name(node: Node, name_type: str) -> str | None`

Dispatcher that routes name extraction to the appropriate strategy based on the value of `name_type`. Sentinel strings of the form `__<keyword>__` invoke dedicated extraction functions; any other string triggers a direct search through `node.children` for a child whose `.type` equals `name_type`.

**Why it exists:** Different languages and node types require structurally different traversal paths to reach the identifier. A single dispatcher keeps the call sites (`_parse_definition_node`, `_parse_decorated_definition`) uniform.

**Recognized sentinels:**

| Sentinel | Delegated to |
|---|---|
| `__assignment__` | `_extract_assignment_name` |
| `__variable_declarator__` | `_extract_variable_declarator_name` |
| `__init_declarator__` | `_extract_init_declarator_name` |
| `__function_declarator__` | `_extract_function_declarator_name` |

Returns `None` when neither a sentinel matches nor a direct child of the expected type is found.

---

## `_extract_assignment_name`

**Signature:** `_extract_assignment_name(node: Node) -> str | None`

Extracts the variable name from a Python top-level `expression_statement` that contains a simple assignment (`identifier = value`).

**Why it exists:** In Python's AST, a module-level variable assignment is wrapped inside an `expression_statement` rather than a dedicated declaration node, so the identifier is two levels deep and cannot be found by direct child search.

**Edge cases:** Returns `None` if the `expression_statement` does not contain an `assignment` (e.g. a bare function call), or if the left-hand side is not a plain `identifier` (e.g. attribute assignment `obj.attr = 1`, subscript assignment). Tuple/pattern unpacking on the left is not handled here; that falls to `_extract_destructured_names`.

---

## `_extract_variable_declarator_name`

**Signature:** `_extract_variable_declarator_name(node: Node) -> str | None`

Extracts the variable name from a JavaScript/TypeScript `lexical_declaration` or `variable_declaration` by locating a `variable_declarator` child and reading its `name` field.

**Why it exists:** JS/TS variable declarations nest the identifier inside a `variable_declarator` child, making it inaccessible via direct child type matching.

**Edge cases:** Returns `None` if no `variable_declarator` child is present or if the `name` field is absent. Destructuring patterns on the `name` field (`object_pattern`, `array_pattern`) are not handled here; `_extract_destructured_names` covers those cases.

---

## `_extract_function_declarator_name`

**Signature:** `_extract_function_declarator_name(node: Node) -> str | None`

Extracts the function name from a C/C++ `function_definition` node by navigating through the `declarator` field chain: `function_definition → function_declarator → identifier`.

**Why it exists:** In C/C++ ASTs, a function's identifier is never a direct child of `function_definition`; it is always nested inside a `function_declarator`, so standard direct-child lookup fails.

**Design decisions:** For C++ out-of-class method implementations, the inner declarator is a `qualified_identifier` (e.g. `Shape::get_name`). In this case the last `identifier` child of the `qualified_identifier` is returned as the name, discarding the class qualifier. If the outer `declarator` field is missing or is not a `function_declarator`, `None` is returned.

---

## `_extract_init_declarator_name`

**Signature:** `_extract_init_declarator_name(node: Node) -> str | None`

Extracts the variable name from a C/C++ `declaration` node that contains an `init_declarator` (i.e. a declaration with an initialiser, such as `int X = 3`).

**Why it exists:** C/C++ `declaration` nodes cover both forward declarations (no initialiser) and variable definitions (with initialiser). Returning `None` for nodes without an `init_declarator` lets the BFS fallback in `extract_definitions` descend into child nodes and pick up the `function_declarator` for forward-declared functions.

**Edge cases:** Returns `None` for forward declarations, pointer declarators, and any case where the `declarator` field of the `init_declarator` is not a plain `identifier`.

---

## `_extract_destructured_names`

**Signature:** `_extract_destructured_names(node: Node, name_type: str) -> list[str]`

Attempts to extract multiple variable names from a destructuring pattern when single-name extraction has already failed. Returns an empty list when the node does not match a recognised destructuring structure.

**Why it exists:** Destructuring assignments (`X, Y = 1, 2` in Python; `const { a, b } = obj` in JS/TS) produce multiple definitions from a single AST node. They must be recorded as separate `DefinitionInfo` entries, which cannot be expressed by the single-name extraction path.

**Supported patterns:**
- Python `__assignment__`: left-hand side is a `pattern_list`; all `identifier` children are collected.
- JS/TS `__variable_declarator__`: `variable_declarator`'s `name` field is an `object_pattern` or `array_pattern`; delegates to `_collect_identifiers_from_pattern`.

Returns an empty list for any unrecognised `name_type`.

---

## `_collect_identifiers_from_pattern`

**Signature:** `_collect_identifiers_from_pattern(pattern_node: Node) -> list[str]`

Recursively collects all locally-bound variable names from a JS/TS `object_pattern` or `array_pattern` node, handling arbitrarily nested destructuring.

**Why it exists:** JS/TS destructuring patterns can nest (`const { a, inner: { b } } = obj`), so a single-level scan is insufficient. Recursion mirrors the recursive structure of the AST.

**Design decisions:** `shorthand_property_identifier_pattern` nodes (bare names in `{ a, b }`) are collected directly. For `pair_pattern` nodes (`{ key: localName }`), only the `value` side is collected because the `key` is the property name being accessed, not a new local binding. Nested `object_pattern`/`array_pattern` values within a `pair_pattern` are also recursed into.

**Constraints:** Only the node types explicitly enumerated (`identifier`, `shorthand_property_identifier_pattern`, `object_pattern`, `array_pattern`, `pair_pattern`) are processed; other child node types (punctuation, etc.) are silently ignored.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports (`re`, `collections.deque`, `dataclasses.dataclass`, `tree_sitter.Node`) are standard library or third-party framework components, which are excluded from this description.

---

### Dependents (what uses this file)

Three files in the project depend on `extract_definitions` from this module. The dependency direction is unidirectional in all cases: the dependents call into this file, and this file has no knowledge of them.

- **`codetwine/import_to_path.py`**: Uses `extract_definitions` to build a symbol-to-file mapping. After parsing a source file into an AST, it iterates over the returned `DefinitionInfo` list and registers each definition name along with its file path into a lookup map.

- **`codetwine/file_analyzer.py`**: Uses `extract_definitions` to produce a structured summary of definitions within a file. It consumes the `start_line`, `end_line`, and `name` fields from each `DefinitionInfo` to construct per-definition records that include the corresponding source text extracted from the file's content lines.

- **`codetwine/extractors/usage_analysis.py`**: Uses `extract_definitions` to enumerate the names defined in a target file. These collected names are used as part of import/usage analysis to determine what symbols a target file exports or defines.

## Data Flow

# Data Flow

## Input

| Source | Type | Description |
|--------|------|-------------|
| `root_node` | `tree_sitter.Node` | AST root node of a parsed source file |
| `definition_dict` | `dict[str, str]` | Maps AST node type → name extraction strategy |

### `definition_dict` Value Conventions

| Value Format | Pattern | Example | Extraction Strategy |
|---|---|---|---|
| Standard node type string | `"identifier"`, `"type_identifier"` | `"identifier"` | Search direct children for matching type |
| Sentinel string (`__X__`) | `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"` | `"__assignment__"` | Delegated to a dedicated deep-traversal function |

---

## Main Transformation Flow

```
root_node (AST)
     │
     ▼
┌─────────────────────────────────────────┐
│  BFS via deque (extract_definitions)    │
│                                         │
│  For each node:                         │
│  ┌──────────────────────────────────┐   │
│  │ node.type == "decorated_def"?    │   │
│  │   → _parse_decorated_definition  │   │
│  │     (find inner def, adjust      │   │
│  │      line range to decorator)    │   │
│  │                                  │   │
│  │ node.type in definition_dict?    │   │
│  │   → _parse_definition_node       │   │
│  │     → _extract_name (dispatch)   │   │
│  │       ├─ standard: child search  │   │
│  │       └─ sentinel: dedicated fn  │   │
│  │                                  │   │
│  │   name found? → DefinitionInfo   │   │
│  │   name missing?                  │   │
│  │     → _extract_destructured_names│   │
│  │       (multiple DefinitionInfos) │   │
│  │     or → enqueue children (BFS)  │   │
│  │                                  │   │
│  │ node not in dict?                │   │
│  │   → enqueue children             │   │
│  └──────────────────────────────────┘   │
└─────────────────────────────────────────┘
     │
     ▼
definition_list  →  sorted by start_line
     │
     ▼
list[DefinitionInfo]
```

**Key transformation decisions during BFS:**

- `decorated_definition` nodes → inner definition extracted; outer decorator's line range overrides the inner node's range.
- Nodes matching `definition_dict` → name extraction attempted; on failure, either destructured names are collected (multiple records emitted) or children are enqueued to continue the search deeper (e.g., C/C++ forward declarations → nested `function_declarator`).
- `namespace_definition` (container type) → recorded **and** children are enqueued so nested definitions inside are also captured.
- `preproc_def` matching `_INCLUDE_GUARD_RE` → discarded (not added to output).

---

## Output

| Destination | Type | Description |
|-------------|------|-------------|
| Return value of `extract_definitions` | `list[DefinitionInfo]` | All definitions found, sorted ascending by `start_line` |

Consumed by `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py` to map symbol names to source files or build definition metadata.

---

## Data Structures

### `DefinitionInfo`

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Extracted symbol name (function, class, variable, type, etc.) |
| `type` | `str` | AST node type of the definition node (e.g., `"function_definition"`) |
| `start_line` | `int` | 1-based start line of the definition (includes decorator if present) |
| `end_line` | `int` | 1-based end line of the definition |

> Line numbers are converted from tree-sitter's 0-based `start_point[0]` / `end_point[0]` to 1-based by adding 1.

### Internal Processing Structures

| Structure | Type | Purpose |
|-----------|------|---------|
| `node_queue` | `deque[Node]` | BFS frontier; nodes whose children have not yet been examined |
| `definition_list` | `list[DefinitionInfo]` | Accumulates results before final sort |
| `_CONTAINER_DEFINITION_TYPES` | `set[str]` | AST node types that are both recorded as definitions and traversed into (currently `namespace_definition`) |

## Error Handling

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** approach. Extraction errors at the individual node level never propagate as exceptions to callers; instead, the failing node is silently skipped or the search is redirected deeper into the AST via BFS fallback. The overall extraction process always completes and returns whatever definitions were successfully found.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Definition node has no extractable name (e.g. forward declaration, non-assignment expression statement) | Returns `None` from the extraction function; BFS descends into child nodes to continue searching | The node itself is not recorded, but definitions nested inside it remain discoverable |
| Destructured assignment where standard single-name extraction fails | Falls back to `_extract_destructured_names`; collects multiple identifiers from the pattern | Each destructured variable is individually recorded; none are silently lost |
| `decorated_definition` contains no recognizable inner definition | Returns `None` from `_parse_decorated_definition` | The decorated node is omitted from results without affecting the rest of the extraction |
| C/C++ `#include` guard `#define` matches the guard pattern | Definition is excluded from results; BFS continues into children | The guard macro is filtered out while any nested definitions remain reachable |
| A sentinel dispatch value has no matching extraction logic | Falls through to the standard direct-child search, which may return `None` | Behaves identically to a failed standard extraction; BFS fallback applies |
| AST node has no children or expected field is absent | Extraction function returns `None` via guard checks | Processing continues; the node is treated as unresolvable at that level |

## Design Considerations

The module makes no use of exceptions for control flow. All failure conditions are expressed as `None` returns or empty list returns, and callers are expected to check these values. This keeps the extraction pipeline non-interruptible: a malformed or unexpected AST structure in one part of a file does not prevent definitions elsewhere from being captured. The BFS architecture naturally provides this resilience, since an unresolvable node simply causes its children to be enqueued rather than the entire traversal aborting.

## Summary

**codetwine/extractors/definitions.py**

Language-agnostic AST definition extractor using tree-sitter. Performs BFS traversal of a parsed syntax tree to identify named definitions (functions, classes, variables, types), returning sorted structured records.

**Public interfaces:**
- `DefinitionInfo` (frozen dataclass): holds `name`, `type`, `start_line`, `end_line` (1-based)
- `extract_definitions(root_node, definition_dict) → list[DefinitionInfo]`: accepts a caller-supplied node-type-to-strategy mapping, enabling language-neutral reuse

**Key behaviors:** handles decorated definitions, destructuring assignments, C/C++ nested declarators, namespace containers, and include-guard filtering. Failures degrade gracefully via `None` returns and BFS child fallback.
