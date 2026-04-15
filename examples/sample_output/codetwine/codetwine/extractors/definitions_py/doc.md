# Design Document: codetwine/extractors/definitions.py

# Overview & Purpose

## 1. Module Summary

Extracts named definitions (functions, classes, variables, types, etc.) from a Tree-sitter AST and returns them as structured `DefinitionInfo` objects sorted by line number.

## 2. When to Use This Module

- **Symbol-to-file mapping** (`import_to_path.py`): Call `extract_definitions(root_node, definition_dict)` to enumerate all definition names in a file so each name can be registered in a symbol lookup map.
- **File analysis / metadata generation** (`file_analyzer.py`): Call `extract_definitions(root_node, definition_dict)` to obtain each definition's name, type, and line range for building structured file metadata.
- **Usage analysis** (`extractors/usage_analysis.py`): Call `extract_definitions(root_node, definition_dict)` on a target file to collect all exported definition names for cross-file reference checking.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass | Holds the name, AST node type, and 1-based start/end line numbers for a single extracted definition. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | BFS-traverses the AST from the given root, identifies definition nodes according to `definition_dict`, extracts their names, and returns all found definitions sorted by start line. |

## 4. Design Decisions

- **Language-agnostic via `definition_dict`**: The extraction logic is fully driven by a caller-supplied mapping of AST node types to name-extraction strategies, keeping the module free of hard-coded language rules. Languages are differentiated entirely by the dictionary passed in at call time.
- **Sentinel-based dispatch for deep name extraction**: When a definition's name node is not a direct child (e.g., C/C++ `function_declarator`, JS/TS `variable_declarator`), the caller encodes this with a `__sentinel__` string value in `definition_dict`. `extract_definitions` delegates to a dedicated private extractor rather than using a generic deep search, making each special case explicit and independently testable.
- **BFS with fallback descent**: If name extraction fails for a matched definition node, the node's children are re-queued for BFS rather than being discarded. This allows nested definitions (e.g., a `function_declarator` inside a `declaration`) to be discovered without requiring every intermediate node type to be listed in `definition_dict`.
- **Container-type pass-through**: Certain definition node types (currently `namespace_definition`) are both recorded as definitions *and* have their children re-queued, so definitions nested inside them are not missed.
- **Include-guard filtering**: `#define` directives whose names match the `_INCLUDE_GUARD_RE` pattern are silently skipped to avoid polluting the definition list with C/C++ header guards.

# Definition Design Specifications

---

## Module-Level Constant

### `_INCLUDE_GUARD_RE`

| Attribute | Value |
|-----------|-------|
| Type | `re.Pattern` |
| Scope | Module-private |

A compiled regular expression used to identify C/C++ include-guard `#define` directives. Matches uppercase-dominant macro names following the conventional `FILENAME_H`, `FILENAME_HPP`, `FILENAME_HXX` patterns, with optional leading/trailing underscores and an optional `INCLUDED` suffix. Definitions matching this pattern are silently skipped rather than recorded.

---

## Data Classes

### `DefinitionInfo`

A plain data container representing a single extracted definition. No special methods are defined; all fields are set at construction time.

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The identifier name of the definition (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type string (e.g. `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition begins in the source file |
| `end_line` | `int` | 1-based line number where the definition ends in the source file |

**When to use:** Instantiated internally by extraction functions; consumed by callers such as `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py` to map definition names to file locations and line ranges.

---

## Public Functions

### `extract_definitions`

```python
def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]
```

**Responsibility:** The primary entry point of this module. Traverses the entire AST via BFS and collects all named definitions according to the language-specific `definition_dict` mapping, returning them sorted by start line.

**When to use:** Called once per source file after parsing, passing the root AST node and the language-appropriate definition dictionary.

**Design decisions:**

- **BFS rather than DFS:** A `deque`-based BFS queue is used so that traversal order and child-expansion logic remain explicit and easy to control.
- **Fallback to child traversal:** When name extraction for a matched node type fails, the node's children are enqueued rather than silently discarding the region. This enables discovery of definitions nested inside C/C++ declarator chains (e.g., `function_declarator` inside a `declaration`).
- **Container definitions:** Nodes whose type appears in `_CONTAINER_DEFINITION_TYPES` (currently `namespace_definition`) are recorded *and* their children are enqueued, allowing definitions nested inside them to also be captured.
- **Destructuring support:** When standard name extraction fails and `_extract_destructured_names` returns multiple names, each name is recorded as a separate `DefinitionInfo` sharing the same node's line range.
- **Include-guard filtering:** `preproc_def` nodes whose names match `_INCLUDE_GUARD_RE` are skipped and their children are enqueued instead.
- **Decorated definitions:** `decorated_definition` nodes are dispatched to a dedicated parser that adjusts the line range to include the decorator lines.

**Constraints & edge cases:**

- `definition_dict` values must be either a valid AST child node type string or a recognized `__sentinel__` string; unrecognized sentinel values fall through to direct-child search, which will silently return `None`.
- The `_CONTAINER_DEFINITION_TYPES` set is defined locally inside the function and is not configurable by callers.
- Line numbers in the returned list are 1-based; the AST provides 0-based values which are converted internally.
- The returned list is always sorted ascending by `start_line` regardless of traversal order.

---

## Private Functions

### `_parse_decorated_definition`

```python
def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None
```

**Responsibility:** Handles the special case where a function or class definition is wrapped by one or more decorators. Finds the inner definition node, extracts its name, then expands the recorded line range to cover the full decorated span.

**When to use:** Called by `extract_definitions` when a `decorated_definition` node is encountered and `"decorated_definition"` is present in `definition_dict`.

**Constraints & edge cases:**
- Returns `None` if no recognizable inner definition node is found among the direct children.
- Only the last matching child in the children list is used as `inner_node`; earlier siblings are silently overwritten.
- The `start_line` and `end_line` of the returned `DefinitionInfo` reflect the outer `decorated_definition` node, not the inner definition node.

---

### `_parse_definition_node`

```python
def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None
```

**Responsibility:** Converts a single matched AST definition node into a `DefinitionInfo` by delegating name extraction to `_extract_name` and wrapping the result with line-number metadata.

**When to use:** Called by both `extract_definitions` and `_parse_decorated_definition` whenever a confirmed definition node needs to be converted into structured data.

**Constraints & edge cases:**
- Returns `None` when `_extract_name` cannot resolve a name, signalling the caller to attempt fallback strategies.

---

### `_extract_name`

```python
def _extract_name(node: Node, name_type: str) -> str | None
```

**Responsibility:** Central dispatcher that routes name extraction to the appropriate specialized function based on whether `name_type` is a sentinel value or a plain AST node type string.

**When to use:** Called exclusively by `_parse_definition_node` to obtain the textual name from a definition node.

**Design decisions:**

| `name_type` value | Dispatched to |
|---|---|
| `"__assignment__"` | `_extract_assignment_name` |
| `"__variable_declarator__"` | `_extract_variable_declarator_name` |
| `"__init_declarator__"` | `_extract_init_declarator_name` |
| `"__function_declarator__"` | `_extract_function_declarator_name` |
| Any other string | Direct child search by node type |

**Constraints & edge cases:**
- Sentinel detection relies on the caller passing the correct string from `definition_dict`; there is no validation of unrecognized `__xxx__` patterns—these fall through to direct-child search.
- Direct-child search only inspects one level deep; nodes with names further down the tree will not be found through this path.

---

### `_extract_assignment_name`

```python
def _extract_assignment_name(node: Node) -> str | None
```

**Responsibility:** Extracts the left-hand side identifier from a Python top-level simple assignment wrapped in an `expression_statement` node.

**When to use:** Called via `_extract_name` when `name_type` is `"__assignment__"`.

**Constraints & edge cases:**
- Returns `None` if the `expression_statement` content is not an `assignment` (e.g., a bare function call).
- Returns `None` if the left-hand side is not a plain `identifier` (e.g., attribute assignment `obj.attr = 1`, subscript assignment, or a tuple/list destructuring—the latter is handled separately by `_extract_destructured_names`).

---

### `_extract_variable_declarator_name`

```python
def _extract_variable_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the declared variable name from a JavaScript/TypeScript `lexical_declaration` or `variable_declaration` node by locating the `variable_declarator` child and reading its `name` field.

**When to use:** Called via `_extract_name` when `name_type` is `"__variable_declarator__"`.

**Constraints & edge cases:**
- Returns `None` if no `variable_declarator` child exists or if its `name` field is absent.
- Destructuring patterns (`object_pattern`, `array_pattern`) as the `name` field will cause this function to return `None`; those cases are handled by `_extract_destructured_names`.

---

### `_extract_function_declarator_name`

```python
def _extract_function_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the function name from a C/C++ `function_definition` node, where the name is nested inside a `function_declarator` rather than being a direct child.

**When to use:** Called via `_extract_name` when `name_type` is `"__function_declarator__"`.

**Design decisions:**
- For C++ class method implementations, the declarator inside `function_declarator` is a `qualified_identifier` (e.g., `Shape::get_name`). In this case, the function iterates the `qualified_identifier`'s children and returns the last `identifier` found, yielding the method name without the class qualifier.

**Constraints & edge cases:**
- Returns `None` if the `declarator` field of the `function_definition` is absent or is not a `function_declarator`.
- Returns `None` if the `declarator` field of the `function_declarator` itself is absent.
- Only `identifier` and `qualified_identifier` declarator types are handled; other types (e.g., pointer declarators) return `None`.

---

### `_extract_init_declarator_name`

```python
def _extract_init_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the variable name from a C/C++ `declaration` node that uses an `init_declarator` (i.e., a declaration with an initializer such as `int x = 3`).

**When to use:** Called via `_extract_name` when `name_type` is `"__init_declarator__"`.

**Design decisions:**
- Returning `None` for forward declarations (which lack an `init_declarator`) is intentional: the BFS fallback in `extract_definitions` then descends into child nodes to discover the nested `function_declarator`.

**Constraints & edge cases:**
- Returns `None` if the `declarator` field is absent or is not an `init_declarator`.
- Returns `None` if the identifier inside the `init_declarator` is not a plain `identifier` (e.g., pointer declarators).

---

### `_extract_destructured_names`

```python
def _extract_destructured_names(node: Node, name_type: str) -> list[str]
```

**Responsibility:** Handles destructuring assignments by extracting multiple variable names from a single declaration node, covering Python tuple unpacking and JavaScript/TypeScript object/array destructuring.

**When to use:** Called by `extract_definitions` as a fallback when `_parse_definition_node` returns `None` for a matched node, allowing multiple names to be registered from one AST node.

**Design decisions:**

| Language | Pattern | AST structure used |
|---|---|---|
| Python | `X, Y = 1, 2` | `assignment` → `left: pattern_list` → `identifier` children |
| JS/TS | `const { a, b } = obj` | `variable_declarator` → `name: object_pattern` |
| JS/TS | `const [a, b] = arr` | `variable_declarator` → `name: array_pattern` |

**Constraints & edge cases:**
- Only responds to `"__assignment__"` and `"__variable_declarator__"` sentinel values; all other values return an empty list.
- Python extraction only collects direct `identifier` children of the `pattern_list`; nested patterns are not recursed.
- JS/TS patterns delegate to `_collect_identifiers_from_pattern` for recursive collection.

---

### `_collect_identifiers_from_pattern`

```python
def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]
```

**Responsibility:** Recursively traverses an `object_pattern` or `array_pattern` AST node to collect all locally bound variable names, including those in nested destructuring patterns.

**When to use:** Called by `_extract_destructured_names` when a JS/TS destructuring pattern node is found.

**Design decisions:**

| Child node type | Action |
|---|---|
| `identifier` | Name appended directly |
| `shorthand_property_identifier_pattern` | Text appended directly (shorthand `{ a, b }`) |
| `object_pattern` / `array_pattern` | Recursed into |
| `pair_pattern` | `value` field inspected; if `identifier`, appended; if nested pattern, recursed |

**Constraints & edge cases:**
- Pair pattern keys are ignored; only the value (the locally bound name) is recorded.
- Deeply nested patterns are handled through recursion with no explicit depth limit.
- Node types not listed in the dispatch table are silently skipped.

# Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/extractors/definitions.py`) has **no project-internal module dependencies**. All imports are from the standard library (`re`, `collections.deque`, `dataclasses.dataclass`) or third-party packages (`tree_sitter.Node`). No project-internal modules are imported.

---

## Dependents (modules that import this file)

Three project-internal modules depend on this file, all consuming the `extract_definitions` function:

- **`codetwine/import_to_path.py`** → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to parse an AST root node and enumerate definition names from a source file, registering each definition name into a symbol-to-file mapping.

- **`codetwine/file_analyzer.py`** → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to collect definition metadata (name, start line, end line, and source context) from a file's AST, producing a structured list of definition records for file analysis output.

- **`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to enumerate all definition names from a target file's AST, building a list of names for usage analysis purposes.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` (one-way)
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` (one-way)

`codetwine/extractors/definitions.py` itself imports no project-internal modules, so it sits at the leaf of the internal dependency graph — consumed by others but depending on none of them.

# Data Flow

## 1. Inputs

| Input | Type | Description |
|-------|------|-------------|
| `root_node` | `tree_sitter.Node` | The root node of a parsed AST covering an entire source file |
| `definition_dict` | `dict[str, str]` | A mapping from AST node type strings to name-extraction strategy strings, supplied by the caller (e.g., per-language settings) |

The module does not read files directly; all source content is already encoded within the `tree_sitter.Node` tree (text is decoded from bytes via `.text.decode("utf-8")`). The `definition_dict` acts as a configuration input that controls which AST node types are treated as definitions and how names are extracted from them.

---

## 2. Transformation Overview

```
root_node (AST)
    │
    ▼
[Stage 1: BFS traversal]
  Deque-based breadth-first walk of the AST.
  At each node, the node's type is checked against definition_dict.
    │
    ├─ decorated_definition  ──► [Stage 2a: Decorated definition parsing]
    │                               Inner definition node located among children.
    │                               Name extracted from inner node, line range
    │                               adjusted to include decorator.
    │
    ├─ node.type in definition_dict ──► [Stage 2b: Standard definition parsing]
    │                               name_node_type retrieved from definition_dict.
    │                               Dispatched to _extract_name.
    │                                 │
    │                                 ├─ sentinel value ──► dedicated extractor
    │                                 │    (__assignment__, __variable_declarator__,
    │                                 │     __init_declarator__, __function_declarator__)
    │                                 │
    │                                 └─ standard type name ──► direct child search
    │
    │   On extraction success ──► DefinitionInfo appended to definition_list
    │                             (with #include guard filtering for preproc_def)
    │                             Container types (namespace_definition) also
    │                             have their children enqueued for continued traversal.
    │
    │   On extraction failure ──► [Stage 2c: Destructured name extraction]
    │                               Attempts to collect multiple names from patterns
    │                               (Python tuple unpacking, JS/TS object/array patterns).
    │                               Each name produces a separate DefinitionInfo.
    │                               If this also fails, children are enqueued (BFS fallback).
    │
    └─ non-definition node ──► children enqueued, traversal continues
    │
    ▼
[Stage 3: Sorting]
  definition_list sorted ascending by start_line.
    │
    ▼
list[DefinitionInfo]
```

---

## 3. Outputs

| Output | Type | Description |
|--------|------|-------------|
| Return value of `extract_definitions` | `list[DefinitionInfo]` | All discovered definitions in the file, sorted by ascending start line |

There are no file writes or external side effects. All output is returned to the caller. Dependents (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`) consume the returned list, accessing `.name`, `.start_line`, and `.end_line` fields.

---

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The extracted identifier name of the definition (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type string that produced this definition (e.g., `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition begins (includes decorator line when applicable) |
| `end_line` | `int` | 1-based line number where the definition ends |

### `definition_dict` (plain dict, caller-supplied)

| Key | Value | Purpose |
|-----|-------|---------|
| AST node type string (e.g., `"function_definition"`) | Name-extraction strategy string | Controls which nodes are treated as definitions and how to locate the name within them |

Value strings fall into two categories:
- **Standard pattern**: a child node type string (e.g., `"identifier"`, `"type_identifier"`) — the name is found by searching direct children.
- **Sentinel pattern**: a double-underscore-wrapped string (e.g., `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`) — the name is located deep in the AST via a dedicated extraction function.

### BFS queue (`node_queue`)

| Structure | Type | Purpose |
|-----------|------|---------|
| `node_queue` | `deque[Node]` | Holds AST nodes pending inspection; populated initially with `root_node` and extended with child nodes during traversal |

### `definition_list` (intermediate accumulator)

| Structure | Type | Purpose |
|-----------|------|---------|
| `definition_list` | `list[DefinitionInfo]` | Accumulates all successfully extracted `DefinitionInfo` instances before final sorting |

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation with BFS fallback** strategy. No exceptions are raised or caught anywhere in the module. Instead, all extraction functions return `None` or an empty list to signal failure, and the caller silently skips or retries via BFS descent into child nodes. The overall extraction process never terminates early due to a single node's extraction failure; it continues processing the remaining AST nodes.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Name extraction failure (standard) | A definition node has no direct child matching the expected name node type | Returns `None`; BFS fallback descends into child nodes to search deeper | Yes | Node is skipped at current level; children are enqueued for re-evaluation |
| Name extraction failure (sentinel) | A sentinel extractor (e.g., `__assignment__`, `__init_declarator__`) cannot locate the expected nested structure | Returns `None`; caller falls through to destructuring check, then BFS fallback | Yes | Node is skipped; children enqueued |
| Destructuring extraction failure | The node is neither a recognized destructuring pattern nor a standard definition | `_extract_destructured_names` returns an empty list; BFS fallback activates | Yes | Node is skipped; children enqueued |
| `#include` guard `#define` filtered out | A `preproc_def` node's name matches `_INCLUDE_GUARD_RE` | Definition is discarded even after successful name extraction; children enqueued | Yes (intentional) | Definition excluded from results; no impact on other nodes |
| Decorated definition with no valid inner node | `decorated_definition` contains no child that is a known definition type | `_parse_decorated_definition` returns `None`; definition is not appended | Yes | Decorated node silently dropped from results |
| Empty or unexpected inner node structure | `node.children` is empty, or `inner.type` does not match expected types in assignment/declarator extractors | Returns `None` or `[]` immediately | Yes | Single node skipped; BFS continues |
| Non-identifier left-hand side in assignment | LHS of a Python assignment is not an `identifier` (e.g., attribute access `obj.attr = 1`) | `_extract_assignment_name` returns `None`; destructuring path checked next | Yes | Node skipped; children enqueued if destructuring also fails |
| Qualified identifier in C++ method | `function_declarator`'s declarator is a `qualified_identifier` instead of plain `identifier` | Special-cased: iterates children to find the last `identifier` | Yes | Name extracted successfully from qualified form |
| Unknown/unregistered node type | Node type not present in `definition_dict` | BFS continues into children without recording any definition | Yes | Node itself ignored; subtree still traversed |

---

## 3. Design Notes

- **No exception-based control flow.** All error conditions are expressed as `None`/empty-list return values, keeping the module free of `try/except` blocks. This is consistent with the read-only, analytical nature of AST traversal where no I/O or external state is modified.

- **BFS fallback as a recovery mechanism.** Rather than discarding an entire subtree when name extraction fails, the BFS queue is extended with the failing node's children. This is a deliberate design to handle languages (primarily C/C++) where the defining identifier is nested multiple levels deep (e.g., `declaration > init_declarator > identifier`), without requiring exhaustive upfront knowledge of every possible nesting depth.

- **Intentional silent filtering.** The `#include` guard exclusion is a policy decision, not an error condition, but it is handled through the same silent-continue pattern: matching nodes are discarded and their children enqueued, maintaining consistency with the overall graceful degradation approach.

- **Caller resilience.** Dependents (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`) all iterate over the returned list without expecting error signals, relying on the guarantee that `extract_definitions` always returns a valid (possibly empty) list rather than raising.

# Summary

**`codetwine/extractors/definitions.py`** extracts named definitions from a Tree-sitter AST and returns them as structured objects sorted by line number.

**Public interface:**
- `DefinitionInfo` (dataclass): fields `name: str`, `type: str`, `start_line: int`, `end_line: int`
- `extract_definitions(root_node: Node, definition_dict: dict[str, str]) -> list[DefinitionInfo]`

**Key data structures:**
- `definition_dict`: maps AST node type strings to name-extraction strategy strings (plain child type or `__sentinel__`)
- `list[DefinitionInfo]`: all discovered definitions, sorted by `start_line`
