# Design Document: codetwine/extractors/definitions.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts named definition information (functions, classes, variables, types, etc.) from a Tree-sitter AST and returns them as a sorted list of `DefinitionInfo` objects, serving as the central definition-discovery engine for language-aware code analysis across the codetwine toolchain.

## 2. When to Use This Module

- **Symbol-to-file mapping** (`codetwine/import_to_path.py`): Call `extract_definitions(root_node, definition_dict)` to enumerate every named symbol defined in a file and register each name in a lookup map.
- **File-level definition inventory** (`codetwine/file_analyzer.py`): Call `extract_definitions(root_node, definition_dict)` to produce a structured list of definitions with their name, type, and line ranges for inclusion in file analysis reports.
- **Target-file symbol enumeration** (`codetwine/extractors/usage_analysis.py`): Call `extract_definitions(root_node, definition_dict)` on a target file to collect all defined names and use them for usage/reference analysis.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass | Holds structured metadata about a single extracted definition, including its name, AST node type, and 1-based start/end line numbers. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | Traverses the AST via BFS using the provided language-specific node-type mapping, extracts all named definitions, and returns them sorted by start line. |

## 4. Design Decisions

- **Caller-supplied `definition_dict`**: The extraction logic is language-agnostic; the caller provides a mapping from AST node types to name-extraction strategies, making this module reusable across all supported languages without modification.
- **Sentinel values for deep name extraction**: When a definition's name is not a direct child of the definition node (e.g., C/C++ `function_declarator`, JS/TS `variable_declarator`, Python `assignment`), `definition_dict` values use a `__sentinel__` convention (e.g., `"__assignment__"`, `"__function_declarator__"`) to dispatch to dedicated extraction functions rather than the generic child-search path.
- **BFS with fallback descent**: When name extraction fails for a matched node type (e.g., a C/C++ forward declaration lacks an `init_declarator`), the node's children are enqueued for further BFS traversal, enabling discovery of nested definitions such as `function_declarator` inside a `declaration`.
- **Container definition traversal**: Certain node types (currently `namespace_definition`) are recorded as definitions *and* have their children enqueued, allowing definitions nested inside namespaces to be discovered in the same BFS pass.
- **Destructuring as a fallback path**: If both the primary name extraction and BFS fallback are inapplicable, a dedicated destructuring extractor handles multi-name patterns (Python `pattern_list`, JS/TS `object_pattern`/`array_pattern`), emitting one `DefinitionInfo` per extracted name with the same node's line range.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constant

### `_INCLUDE_GUARD_RE`

A compiled regular expression used to identify C/C++ include-guard `#define` directives. Matches names following the conventional patterns such as `MYHEADER_H`, `_MYHEADER_HPP_`, `MYFILE_H_INCLUDED`, etc. Definitions matching this pattern are silently excluded from extraction results.

---

## Data Classes

### `DefinitionInfo`

A plain data container representing a single extracted definition.

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The identifier name of the definition (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type string (e.g., `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition starts in the source file |
| `end_line` | `int` | 1-based line number where the definition ends in the source file |

**Responsibility:** Provides a language-agnostic, uniform record of a definition extracted from any supported language's AST.

**When to use:** Consumed by callers such as `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py` to register symbol names, build context snippets, or resolve file ownership of symbols.

---

## Public Functions

### `extract_definitions`

```python
def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]
```

- **`root_node`**: The root `Node` of a tree-sitter parse tree for an entire source file.
- **`definition_dict`**: A mapping from AST node type strings to name-extraction directives. Values are either a direct child node type (e.g., `"identifier"`) or a sentinel string (e.g., `"__assignment__"`) indicating a deeper extraction strategy.
- **Returns**: A list of `DefinitionInfo` objects sorted in ascending order by `start_line`.

**Responsibility:** Entry point for definition extraction; traverses the AST via BFS and collects all named definitions matching the provided configuration.

**When to use:** Called whenever a file's AST needs to be scanned for its top-level and nested definitions, such as during symbol map construction or file analysis.

**Design decisions:**

- **BFS over DFS**: Uses a `deque`-based breadth-first search so that container definitions (e.g., `namespace_definition`) can be processed in document order before their children are enqueued.
- **Fallback to child traversal**: When name extraction returns `None`, the node's children are enqueued for continued search rather than abandoning the subtree. This supports constructs like C/C++ forward declarations where the relevant name is nested inside a `function_declarator`.
- **Container node continuation**: Nodes listed in `_CONTAINER_DEFINITION_TYPES` (currently `namespace_definition`) have their children enqueued even after a successful definition record, allowing definitions nested inside them to be collected.
- **Destructuring fallback**: When single-name extraction fails, `_extract_destructured_names` is tried before falling back to child traversal, capturing multi-variable assignments like `X, Y = 1, 2`.
- **`decorated_definition` special-casing**: Handled separately before the general branch so that the reported line range covers the full decorator span rather than only the inner definition.
- **Include-guard filtering**: `preproc_def` nodes whose names match `_INCLUDE_GUARD_RE` are skipped and their children enqueued instead.

**Constraints & edge cases:**

- `definition_dict` must accurately reflect the target language's AST structure; an incorrect mapping silently produces no results for that node type.
- `decorated_definition` is intentionally excluded from the general `elif` branch to avoid double-processing.
- The `_CONTAINER_DEFINITION_TYPES` set is module-local and currently hard-coded to `{"namespace_definition"}`.

---

## Private Functions

### `_parse_decorated_definition`

```python
def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None
```

- **`node`**: A `decorated_definition` AST node.
- **`definition_dict`**: Per-language definition node settings, same object passed to `extract_definitions`.
- **Returns**: A `DefinitionInfo` with line numbers adjusted to include the decorator, or `None` if no valid inner definition is found.

**Responsibility:** Extracts the name from the inner function or class definition wrapped by a decorator, while attributing the full source range (including decorator lines) to the resulting record.

**When to use:** Called by `extract_definitions` exclusively when encountering a `decorated_definition` node.

**Constraints & edge cases:** Only the last matching inner definition child is used if multiple definition-type children exist. Returns `None` if no child matches any key in `definition_dict`.

---

### `_parse_definition_node`

```python
def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None
```

- **`node`**: Any definition AST node (e.g., `function_definition`, `class_definition`).
- **`name_node_type`**: Either a direct child node type string or a sentinel string indicating a specialized extractor.
- **Returns**: A `DefinitionInfo` with 1-based line numbers, or `None` if name extraction fails.

**Responsibility:** Converts a raw AST definition node into a typed `DefinitionInfo` record by delegating name extraction to `_extract_name`.

**When to use:** Called by both `extract_definitions` and `_parse_decorated_definition` whenever a definition node needs to be materialized into a `DefinitionInfo`.

---

### `_extract_name`

```python
def _extract_name(node: Node, name_type: str) -> str | None
```

- **`node`**: The definition node from which to extract the name.
- **`name_type`**: A directive string from `definition_dict`—either a standard child type or a sentinel of the form `__<strategy>__`.
- **Returns**: The definition name as a UTF-8 string, or `None`.

**Responsibility:** Central dispatcher that routes name extraction to the appropriate specialized function based on the sentinel value, or performs a direct child search for standard cases.

**Design decisions:** The sentinel convention (`__<name>__`) makes the dispatch table explicit and extensible without requiring subclassing or a registry.

| Sentinel Value | Delegates To |
|---|---|
| `__assignment__` | `_extract_assignment_name` |
| `__variable_declarator__` | `_extract_variable_declarator_name` |
| `__init_declarator__` | `_extract_init_declarator_name` |
| `__function_declarator__` | `_extract_function_declarator_name` |
| *(any other value)* | Direct child type search |

**Constraints & edge cases:** Returns `None` for both unrecognized sentinel values that do not match any branch and for standard child searches where no child with the given type exists.

---

### `_extract_assignment_name`

```python
def _extract_assignment_name(node: Node) -> str | None
```

- **`node`**: An `expression_statement` node.
- **Returns**: The identifier name from the left-hand side, or `None`.

**Responsibility:** Extracts the variable name from a Python top-level simple assignment (`X = ...`).

**Constraints & edge cases:**
- Returns `None` if the inner node is not an `assignment` (e.g., a bare function call).
- Returns `None` if the left-hand side is not a plain `identifier` (e.g., attribute assignment `obj.x = 1`).
- Does not handle destructuring (tuple unpacking); that case is handled by `_extract_destructured_names`.

---

### `_extract_variable_declarator_name`

```python
def _extract_variable_declarator_name(node: Node) -> str | None
```

- **`node`**: A `lexical_declaration` or `variable_declaration` node.
- **Returns**: The identifier name from the first `variable_declarator` child's `name` field, or `None`.

**Responsibility:** Extracts the variable name from a JS/TS `const`, `let`, or `var` declaration.

**Constraints & edge cases:** Only the first `variable_declarator` child is examined. Destructured patterns (`object_pattern`, `array_pattern`) in the name position cause this function to return `None`, which triggers the destructuring path in `_extract_destructured_names`.

---

### `_extract_function_declarator_name`

```python
def _extract_function_declarator_name(node: Node) -> str | None
```

- **`node`**: A C/C++ `function_definition` node.
- **Returns**: The function name string, or `None` if `function_declarator` is absent.

**Responsibility:** Extracts the function name from C/C++ function definitions where the name is nested inside a `function_declarator` rather than being a direct child.

**Design decisions:** Handles C++ qualified identifiers (e.g., `Shape::get_name`) by scanning the `qualified_identifier`'s children and returning the last `identifier` found, yielding only the method name portion.

**Constraints & edge cases:**
- Returns `None` if the `declarator` field is missing or is not a `function_declarator`.
- Returns `None` for AST structures where the `function_declarator`'s `declarator` field is neither an `identifier` nor a `qualified_identifier`.

---

### `_extract_init_declarator_name`

```python
def _extract_init_declarator_name(node: Node) -> str | None
```

- **`node`**: A C/C++ `declaration` node.
- **Returns**: The variable name string, or `None` if no `init_declarator` is found.

**Responsibility:** Extracts the variable name from a C/C++ variable or constant declaration that includes an initializer.

**Design decisions:** Forward declarations (e.g., `void freeFunction();`) lack an `init_declarator` and thus return `None`, intentionally triggering the BFS child-descent fallback in `extract_definitions` to find the `function_declarator` instead.

**Constraints & edge cases:** Returns `None` for any `declaration` whose `declarator` field is not an `init_declarator`, including forward declarations and plain declarations without initialization.

---

### `_extract_destructured_names`

```python
def _extract_destructured_names(node: Node, name_type: str) -> list[str]
```

- **`node`**: A definition node that failed single-name extraction.
- **`name_type`**: The same sentinel value that was passed to `_extract_name`.
- **Returns**: A list of extracted variable name strings; empty list if the node is not a recognized destructuring pattern.

**Responsibility:** Handles multi-variable destructuring assignments in Python and JS/TS by collecting all bound names from a single declaration node.

| `name_type` | Language | Pattern handled |
|---|---|---|
| `__assignment__` | Python | `X, Y = 1, 2` via `pattern_list` |
| `__variable_declarator__` | JS/TS | `const { a, b } = obj` / `const [a, b] = arr` |

**Constraints & edge cases:** Returns an empty list for any `name_type` not listed above. For JS/TS, delegates to `_collect_identifiers_from_pattern` for the actual name collection.

---

### `_collect_identifiers_from_pattern`

```python
def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]
```

- **`pattern_node`**: An `object_pattern` or `array_pattern` AST node.
- **Returns**: A flat list of all variable name strings bound by the pattern.

**Responsibility:** Recursively traverses a JS/TS destructuring pattern node to collect every locally-bound identifier name, including those in nested sub-patterns.

**Design decisions:**
- Handles `shorthand_property_identifier_pattern` (e.g., `{ a, b }`) directly as a name-bearing leaf.
- For `pair_pattern` (e.g., `{ key: localName }`), only the `value` side is examined, since that is the locally bound name.
- Recursion is used rather than BFS to naturally mirror the nesting structure of patterns.

**Constraints & edge cases:**
- Does not handle renamed default patterns (e.g., `{ a = 1 }`); only `identifier` and recognized pattern types are collected.
- Arbitrarily deep nesting is supported via recursion, but very deep patterns may hit Python's recursion limit in pathological cases.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports are from the standard library (`re`, `collections`, `dataclasses`) or third-party packages (`tree_sitter`).

## Dependents (modules that import this file)

Three project-internal modules import `extract_definitions` from this file:

- **`codetwine/import_to_path.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to parse a file's AST and register each definition's name into a symbol-to-file mapping, enabling symbol resolution across the project.

- **`codetwine/file_analyzer.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to enumerate all definitions in a file, extracting their names, start/end line numbers, and source context for file-level analysis output.

- **`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to collect the definition names exported by a target file, supporting usage and dependency analysis between files.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → this module (consumer only)
- `codetwine/file_analyzer.py` → this module (consumer only)
- `codetwine/extractors/usage_analysis.py` → this module (consumer only)

This module does not import from any of its dependents; it acts purely as a provider of definition-extraction functionality.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|-------|------|-------------|
| `root_node` | `tree_sitter.Node` | The root node of a parsed AST covering an entire source file |
| `definition_dict` | `dict[str, str]` | A mapping from AST node type strings to name-extraction specifiers (either a direct child node type name or a `__sentinel__` string) |

The module receives no file handles, environment variables, or configuration files directly. All inputs are passed as arguments to `extract_definitions`.

---

## 2. Transformation Overview

```
root_node + definition_dict
        │
        ▼
┌─────────────────────────────┐
│  BFS traversal of AST nodes │  (deque-based; root_node is the initial element)
└─────────────────────────────┘
        │
        ▼ for each node
┌──────────────────────────────────────────────────────┐
│  Node classification                                 │
│   • decorated_definition  → _parse_decorated_definition │
│   • node.type in dict     → _parse_definition_node   │
│   • otherwise             → enqueue children         │
└──────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────┐
│  Name extraction via _extract_name                   │
│   • "__assignment__"          → _extract_assignment_name            │
│   • "__variable_declarator__" → _extract_variable_declarator_name  │
│   • "__init_declarator__"     → _extract_init_declarator_name      │
│   • "__function_declarator__" → _extract_function_declarator_name  │
│   • standard string           → direct child lookup                │
└──────────────────────────────────────────────────────┘
        │
        ├── name found → build DefinitionInfo; apply include-guard filter
        │               for container types: also enqueue children
        │
        └── name not found → attempt _extract_destructured_names
                │
                ├── names found → build one DefinitionInfo per name
                └── names not found → enqueue children (continue BFS)
        │
        ▼
┌──────────────────────────┐
│  Sort by start_line asc  │
└──────────────────────────┘
        │
        ▼
list[DefinitionInfo]
```

**Special cases during the pipeline:**

- `decorated_definition` nodes delegate name extraction to their inner function or class child node, then adopt the outer node's full line range.
- `preproc_def` nodes whose extracted name matches `_INCLUDE_GUARD_RE` are discarded (not added to the output list), and their children are still enqueued.
- Container nodes (currently `namespace_definition`) have their children enqueued even after a successful `DefinitionInfo` is recorded, allowing nested definitions to surface.
- Destructured patterns (Python tuple unpacking, JS/TS object/array patterns) produce multiple `DefinitionInfo` entries sharing the same `start_line`/`end_line` but each with a distinct `name`.

---

## 3. Outputs

| Output | Type | Description |
|--------|------|-------------|
| Return value of `extract_definitions` | `list[DefinitionInfo]` | All recognized definitions, sorted by `start_line` in ascending order |

There are no file writes or side effects. The list is consumed by callers in `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py` to map symbol names to file paths or to build structured metadata about definitions.

---

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The extracted symbol name (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type from which the definition was extracted (e.g. `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

---

### `definition_dict` (input parameter)

| Key | Value | Purpose |
|-----|-------|---------|
| AST node type string (e.g. `"function_definition"`) | Direct child node type (e.g. `"identifier"`) **or** a sentinel string (e.g. `"__assignment__"`) | Tells `extract_definitions` which nodes to treat as definitions and how to extract the name from each |

Sentinel values recognized:

| Sentinel | Target Language Pattern |
|----------|------------------------|
| `"__assignment__"` | Python `expression_statement > assignment > identifier` |
| `"__variable_declarator__"` | JS/TS `lexical_declaration / variable_declaration > variable_declarator > name` |
| `"__init_declarator__"` | C/C++ `declaration > init_declarator > declarator` |
| `"__function_declarator__"` | C/C++ `function_definition > function_declarator > declarator` |

---

### `node_queue` (internal BFS state)

| Aspect | Type | Purpose |
|--------|------|---------|
| Elements | `tree_sitter.Node` | AST nodes pending inspection during BFS traversal |
| Structure | `collections.deque` | Enables O(1) enqueue (`extend`) and dequeue (`popleft`) during traversal |

---

### `definition_list` (internal accumulator)

| Aspect | Type | Purpose |
|--------|------|---------|
| Elements | `DefinitionInfo` | Accumulates all successfully extracted definitions before final sorting |
| Structure | `list` | Ordered by insertion; sorted by `start_line` before being returned |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation / silent-skip** strategy. No exceptions are raised at any point; instead, every extraction function signals failure by returning `None` or an empty list. The caller then decides whether to skip the node entirely or fall back to an alternative extraction path. The overall BFS traversal never terminates early — unrecognized or unextractable nodes are simply bypassed, and processing continues with the remaining queue.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Name extraction failure (standard) | A definition node has no direct child matching the expected `name_node_type` | `_extract_name` returns `None`; the node's children are added to the BFS queue for deeper search | Yes | The definition is not recorded at that level; BFS descends further to find nested definitions |
| Name extraction failure (sentinel) | A sentinel-dispatched extractor cannot locate the expected nested node (e.g., no `assignment`, no `function_declarator`, no `init_declarator`) | The dedicated extractor returns `None`; caller falls through to `_extract_destructured_names` | Yes | Falls back to destructuring extraction; if that also yields nothing, children are queued for BFS |
| Destructuring extraction yields nothing | Node is neither a destructuring pattern nor a recognized sentinel type | `_extract_destructured_names` returns `[]` | Yes | BFS descends into child nodes; definition at this level is silently skipped |
| `decorated_definition` with no inner definition | `decorated_definition` node has no child whose type appears in `definition_dict` | `_parse_decorated_definition` returns `None`; result is discarded by the caller | Yes | The decorated definition is silently omitted from the result list |
| `#include` guard `#define` filtered out | A `preproc_def` node's extracted name matches `_INCLUDE_GUARD_RE` | Definition is discarded; node's children are still queued for BFS | Yes | The guard macro is excluded from results; traversal continues normally |
| Empty or structurally unexpected node | A node has no children (e.g., `node.children` is empty) | Guard checks (`if not node.children`) return `None` or `[]` immediately | Yes | Extraction is skipped for that node; no side effects |
| Non-`identifier` left-hand side in assignment | LHS of a Python assignment is not an `identifier` (e.g., `obj.attr = 1`) | `_extract_assignment_name` returns `None` | Yes | That assignment is not recorded as a definition; BFS may continue into children |
| Unrecognized node type | A node type is not present in `definition_dict` | The `else` branch queues the node's children for BFS | Yes | No definition recorded; traversal continues transparently |

---

## 3. Design Notes

- **No exceptions are used.** The entire module relies on sentinel return values (`None`, `[]`) to communicate extraction failure, which keeps the BFS loop free of exception-handling overhead and ensures the traversal always completes.
- **BFS fallback as the recovery mechanism.** When name extraction fails, the fallback is not a retry of the same logic but a structural descent — children are enqueued, allowing definitions nested inside unexpected wrapper nodes (e.g., C/C++ forward declarations containing a `function_declarator`) to still be discovered.
- **Destructuring as a secondary fallback.** The two-stage fallback (sentinel extractor → destructuring extractor → BFS descent) reflects a deliberate ordering from most specific to most general, minimizing missed definitions without requiring explicit error states.
- **Impact on dependents is bounded.** Because the function always returns a (possibly empty) sorted list and never raises, all three callers (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`) are insulated from any extraction anomaly — they simply iterate over whatever results are returned.

## Summary

**definitions.py**: Extracts named definitions from a Tree-sitter AST, returning a sorted list of `DefinitionInfo` objects for use in symbol mapping, file analysis, and usage analysis.

**Public interface:**
- `DefinitionInfo(name: str, type: str, start_line: int, end_line: int)` — dataclass holding definition metadata
- `extract_definitions(root_node: Node, definition_dict: dict[str, str]) -> list[DefinitionInfo]` — BFS traversal producing all definitions sorted by start line

**Key data:** `definition_dict` maps AST node type strings to child type names or `__sentinel__` strings (e.g., `__assignment__`, `__function_declarator__`) directing specialized name extraction.
