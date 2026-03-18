# Design Document: codetwine/extractors/definitions.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts named definition information (functions, classes, variables, types, etc.) from a Tree-sitter AST and returns them as a sorted list of `DefinitionInfo` objects, enabling callers to map symbol names to their source file locations and line ranges.

## 2. When to Use This Module

- **Symbol-to-file mapping** (`import_to_path.py`): Call `extract_definitions(root_node, definition_dict)` to enumerate all definition names in a parsed file and register each name in a symbol lookup map.
- **File structure analysis** (`file_analyzer.py`): Call `extract_definitions(root_node, definition_dict)` to retrieve each definition's name, type, and line range for building a structured summary of a file's contents.
- **Usage/dependency analysis** (`extractors/usage_analysis.py`): Call `extract_definitions(root_node, target_def_dict)` against a target file to collect all definition names that other files may reference.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass | Holds the name, AST node type, and 1-based start/end line numbers for a single extracted definition. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | Performs a BFS traversal of the AST, extracts all matching definitions using the language-specific `definition_dict`, and returns them sorted by start line. |

## 4. Design Decisions

- **BFS with fallback descent**: When name extraction for a matched node fails (e.g., a C/C++ forward declaration lacks an `init_declarator`), the node's children are added back to the queue rather than discarding the subtree. This allows nested definition nodes such as `function_declarator` to be discovered in a subsequent iteration.
- **Sentinel-based dispatch**: `definition_dict` values that follow the `__name__` pattern (e.g., `__assignment__`, `__variable_declarator__`) signal that the name is nested two or more levels deep and require a dedicated extraction path, keeping the dispatch logic centralized in `_extract_name` rather than spread across callers.
- **Container type passthrough**: Nodes of type `namespace_definition` are recorded as definitions *and* have their children enqueued, allowing definitions nested inside namespaces to be discovered without special-casing at the call site.
- **Decorated definition handling**: `decorated_definition` nodes are treated separately so that the reported line range spans the decorator(s) and the inner definition together, while the name is extracted from the inner node.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constant

### `_INCLUDE_GUARD_RE`

A compiled regular expression used to identify C/C++ include guard `#define` directives. Matches macro names that follow conventional include guard naming patterns (e.g., `MY_HEADER_H`, `_UTILS_HPP_`). Used exclusively to filter out such definitions during extraction.

---

## Data Classes

### `DefinitionInfo`

A dataclass representing a single extracted definition from a source file.

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The definition's identifier (function name, class name, variable name, etc.) |
| `type` | `str` | The AST node type string that produced this definition (e.g., `"function_definition"`) |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

**Responsibility:** Serves as a plain data carrier for information about a single named definition extracted from an AST. Callers consume this to build symbol maps and file analysis metadata.

**When to use:** Instantiated internally by extraction functions; consumers access its fields to register symbols, build context strings, or report definition ranges.

---

## Public Functions

### `extract_definitions`

```python
def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]
```

**Responsibility:** Traverses the entire AST via BFS starting from `root_node` and collects all named definitions whose node types are listed in `definition_dict`, returning them sorted by start line.

**When to use:** Called by file analyzers and symbol mappers whenever a parsed file's AST needs to be scanned for all top-level and nested definitions.

**Design decisions:**

- **BFS with a deque** rather than DFS: allows controlled expansion—child nodes are only queued when the current node is not a recognized definition node, or when it is a container type (like `namespace_definition`) that may contain further definitions.
- **`definition_dict` sentinel values vs. type strings:** The same dict drives both standard (direct-child lookup) and deep (multi-level traversal) name extraction, with sentinel strings (e.g., `"__assignment__"`) acting as dispatch keys. This avoids hardcoding language-specific logic in the BFS loop.
- **Container definitions** (`_CONTAINER_DEFINITION_TYPES`): Certain node types (currently `namespace_definition`) are recorded as definitions *and* their children are still queued, enabling detection of nested definitions inside them.
- **Include guard exclusion:** `preproc_def` nodes whose names match `_INCLUDE_GUARD_RE` are silently skipped with child traversal continued, preventing false positives from C/C++ header guards.
- **Destructuring fallback:** When `_parse_definition_node` returns `None` and `_extract_destructured_names` yields results, multiple `DefinitionInfo` entries sharing the same line range are appended—one per extracted name.
- **BFS fallback on failed name extraction:** When both name extraction and destructuring detection fail, children are queued so that nested recognizable nodes (e.g., a `function_declarator` inside a forward declaration) can still be found.

**Constraints & edge cases:**

- `root_node` must be a valid tree-sitter `Node` for the file's language.
- `definition_dict` must be non-empty; an empty dict causes the function to return an empty list.
- Forward declarations in C/C++ intentionally return `None` from name extraction, triggering child traversal rather than registration.
- `decorated_definition` is handled by a separate branch and will not fall through to the standard node processing branch even if it is present in `definition_dict`.

---

## Private Functions

### `_parse_decorated_definition`

```python
def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None
```

**Responsibility:** Extracts a `DefinitionInfo` from a `decorated_definition` node by locating its inner function or class definition and then adjusting the line range to encompass the entire decorated block (including decorators).

**When to use:** Called from the BFS loop when a `decorated_definition` node is encountered and `"decorated_definition"` is a key in `definition_dict`.

**Design decisions:** The `start_line` and `end_line` of the returned `DefinitionInfo` are overwritten to reflect the outer `decorated_definition` node's span, not the inner definition's span, so callers see the full decorated construct.

**Constraints & edge cases:**

- Returns `None` if no recognizable definition child (other than another `decorated_definition`) is found.
- Returns `None` if `_parse_definition_node` on the inner node fails.

---

### `_parse_definition_node`

```python
def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None
```

**Responsibility:** Constructs a `DefinitionInfo` for a single definition node by delegating name extraction to `_extract_name` and recording the node's line span.

**When to use:** Called for any recognized non-decorated definition node during BFS traversal, and also by `_parse_decorated_definition` for the inner node.

**Constraints & edge cases:**

- Returns `None` when `_extract_name` cannot locate a name, signaling the caller to attempt destructuring extraction or BFS fallback.
- Line numbers are converted from 0-based (tree-sitter) to 1-based.

---

### `_extract_name`

```python
def _extract_name(node: Node, name_type: str) -> str | None
```

**Responsibility:** Dispatches name extraction to a dedicated function when `name_type` is a sentinel value, or performs a direct child scan when `name_type` is a plain AST node type string.

**When to use:** Invoked by `_parse_definition_node` to obtain the textual name of a definition.

**Design decisions:** The sentinel convention (strings like `"__assignment__"`) allows the `definition_dict` in calling code to remain a simple `dict[str, str]` while still routing to language-specific deep extraction logic without a separate configuration layer.

**Constraints & edge cases:**

- Recognized sentinel values: `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`.
- For the standard pattern, only *direct* children are searched; grandchildren are not considered.
- Returns `None` if no matching child is found.

---

### `_extract_assignment_name`

```python
def _extract_assignment_name(node: Node) -> str | None
```

**Responsibility:** Extracts a variable name from a Python `expression_statement` that wraps a simple assignment (e.g., `X = 42`).

**When to use:** Called by `_extract_name` when `name_type == "__assignment__"`.

**Constraints & edge cases:**

- Returns `None` if the expression statement does not contain an `assignment` node (e.g., bare function calls).
- Returns `None` if the left-hand side is not a plain `identifier` (e.g., attribute assignment `obj.attr = 1`, subscript assignment, or tuple/list unpacking—the last case is handled separately by `_extract_destructured_names`).

---

### `_extract_variable_declarator_name`

```python
def _extract_variable_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the variable name from a JavaScript/TypeScript `lexical_declaration` or `variable_declaration` node by traversing into its `variable_declarator` child.

**When to use:** Called by `_extract_name` when `name_type == "__variable_declarator__"`.

**Constraints & edge cases:**

- Returns `None` if no `variable_declarator` child exists or if its `name` field is absent.
- When the `name` field is an `object_pattern` or `array_pattern` (destructuring), returns `None`; the caller must invoke `_extract_destructured_names` instead.

---

### `_extract_function_declarator_name`

```python
def _extract_function_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the function name from a C/C++ `function_definition` node, including support for class method implementations using qualified identifiers (e.g., `Shape::get_name`).

**When to use:** Called by `_extract_name` when `name_type == "__function_declarator__"`.

**Design decisions:** For `qualified_identifier` declarators (C++ out-of-line method definitions), only the *last* `identifier` child is returned, yielding the method name without the class prefix.

**Constraints & edge cases:**

- Returns `None` if the `declarator` field is absent or is not a `function_declarator`.
- Returns `None` if the inner declarator is neither `identifier` nor `qualified_identifier`.

---

### `_extract_init_declarator_name`

```python
def _extract_init_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the variable name from a C/C++ `declaration` that includes an `init_declarator` (i.e., a variable declaration with an initializer).

**When to use:** Called by `_extract_name` when `name_type == "__init_declarator__"`.

**Design decisions:** Intentionally returns `None` for forward declarations and function prototypes (which lack an `init_declarator`), allowing the BFS fallback to pick up the nested `function_declarator` instead.

**Constraints & edge cases:**

- Returns `None` if the `declarator` field is not an `init_declarator`.
- Returns `None` if the inner `declarator` field of the `init_declarator` is not an `identifier`.

---

### `_extract_destructured_names`

```python
def _extract_destructured_names(node: Node, name_type: str) -> list[str]
```

**Responsibility:** Collects multiple variable names from a destructuring pattern node, covering Python tuple unpacking and JavaScript/TypeScript object/array destructuring.

**When to use:** Called by `extract_definitions` when `_parse_definition_node` returns `None`, to detect whether the node represents a multi-name binding.

**Design decisions:** Only `"__assignment__"` and `"__variable_declarator__"` sentinel values are handled; all other inputs return an empty list.

**Constraints & edge cases:**

- For `"__assignment__"`: only `pattern_list` left-hand sides are processed; `identifier` elements within it are collected.
- For `"__variable_declarator__"`: delegates to `_collect_identifiers_from_pattern` for `object_pattern` and `array_pattern` names.
- Returns an empty list (not `None`) when the node is not a destructuring pattern.

---

### `_collect_identifiers_from_pattern`

```python
def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]
```

**Responsibility:** Recursively collects all variable names bound by an `object_pattern` or `array_pattern` node, including nested patterns and `pair_pattern` value bindings.

**When to use:** Called by `_extract_destructured_names` when a JS/TS destructuring pattern node has been identified.

**Design decisions:** Recursion handles arbitrary nesting depth. For `pair_pattern` nodes (e.g., `{ key: localName }`), only the *value* side is collected because only the value introduces a new local binding. Nested `object_pattern` or `array_pattern` values within a `pair_pattern` are also recursed into.

**Constraints & edge cases:**

- `shorthand_property_identifier_pattern` nodes (e.g., `{ a, b }` shorthand) are treated as direct name sources.
- Only `identifier` and `shorthand_property_identifier_pattern` leaf nodes contribute names; other node types within the pattern are ignored unless they are themselves patterns.
- Deeply nested aliasing (e.g., `{ key: localName }`) yields only `localName`, not `key`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies are present in this file. All imports (`re`, `collections.deque`, `dataclasses.dataclass`, `tree_sitter.Node`) are either standard library or third-party packages, which are excluded from this description.

---

## Dependents (modules that import this file)

The following project-internal modules depend on this file by importing `extract_definitions`:

- **`codetwine/import_to_path.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to parse a file's AST root node and enumerate all defined symbols, registering each definition name into a symbol-to-file mapping.

- **`codetwine/file_analyzer.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to obtain all definitions from a file's AST, building structured records that include each definition's name, start line, end line, and source context text.

- **`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to enumerate all definition names exported by a target file, collecting those names for downstream usage analysis.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions_py/definitions.py`
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions_py/definitions.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions_py/definitions.py`

This file (`definitions.py`) does not import from any of its dependents, and none of its dependents are imported back by this file. The data flow is strictly one-way: the three consumer modules call into this module to obtain `DefinitionInfo` results.

## Data Flow

# Data Flow

## 1. Inputs

This module receives two inputs passed as function arguments:

- **`root_node: Node`** — The root node of a tree-sitter AST representing an entire parsed source file. This node provides access to the full syntactic tree via its `children` property and field accessors (`child_by_field_name`), along with positional data (`start_point`, `end_point`) and node type strings (`type`), and raw text bytes (`text`).
- **`definition_dict: dict[str, str]`** — A caller-supplied mapping that configures which AST node types count as definitions and how to extract their names. Keys are AST node type strings (e.g., `"function_definition"`, `"expression_statement"`). Values are either a direct child node type string (e.g., `"identifier"`) or a sentinel string (e.g., `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`) that signals a deep extraction strategy.

No file I/O or external configuration reads occur within this module.

---

## 2. Transformation Overview

### Stage 1 — BFS Traversal of the AST

Starting from `root_node`, all AST nodes are visited breadth-first using a `deque`. At each step, the current node's `type` is checked against `definition_dict`. Nodes whose types are not registered as definitions have their children enqueued for further traversal. Container definition types (currently `namespace_definition`) enqueue their children even after being recorded, allowing nested definitions to be discovered.

### Stage 2 — Definition Node Classification

Each node whose type appears in `definition_dict` is classified into one of three cases:
- **Decorated definition** (`decorated_definition`): Routed to `_parse_decorated_definition`, which first locates the inner definition node among the decorator node's children, then delegates to the standard name extraction path while adjusting line numbers to span the full decorated range.
- **Standard definition**: Routed to `_parse_definition_node` with the corresponding `name_node_type` value from `definition_dict`.

### Stage 3 — Name Extraction

`_parse_definition_node` calls `_extract_name`, which dispatches based on the `name_node_type` value:

| Value | Strategy |
|---|---|
| `"__assignment__"` | Traverses `expression_statement → assignment → left` to find an `identifier` |
| `"__variable_declarator__"` | Traverses to `variable_declarator → name` field |
| `"__init_declarator__"` | Traverses `declaration → init_declarator → declarator` field |
| `"__function_declarator__"` | Traverses `function_definition → function_declarator → declarator` field; handles `qualified_identifier` for C++ class methods |
| Standard string (e.g., `"identifier"`) | Searches direct children for a node matching that type |

### Stage 4 — Fallback for Failed Extraction

When name extraction returns `None` for a non-decorated definition node, two fallback paths are attempted in order:
1. **Destructuring extraction** — `_extract_destructured_names` is called. For `__assignment__`, it searches for a `pattern_list` on the left-hand side of a Python tuple assignment. For `__variable_declarator__`, it searches for `object_pattern` or `array_pattern` inside the `variable_declarator`, then recursively collects identifiers via `_collect_identifiers_from_pattern`. If names are found, one `DefinitionInfo` per name is appended.
2. **BFS descent** — If destructuring extraction also yields nothing, the node's children are enqueued, allowing the BFS to continue searching deeper (e.g., to find a `function_declarator` inside a C/C++ forward declaration).

### Stage 5 — Filtering

After a definition is recorded for a `preproc_def` node, its name is checked against `_INCLUDE_GUARD_RE`. If the name matches a C/C++ include guard pattern, the result is discarded and the node's children are enqueued instead.

### Stage 6 — Sorting

The accumulated `definition_list` is sorted in ascending order by `start_line` before being returned.

---

## 3. Outputs

The module returns a single value:

- **`list[DefinitionInfo]`** — A list of `DefinitionInfo` dataclass instances, one per detected definition, sorted by `start_line` in ascending order. Each instance carries the definition's name, its AST node type, and its 1-based start and end line numbers.

There are no file writes or other side effects.

Callers (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`) iterate over this list to extract `.name`, `.start_line`, `.end_line`, and `.type` fields for symbol mapping, file analysis, and usage analysis respectively.

---

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The extracted definition name (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type string that produced this definition (e.g., `"function_definition"`) |
| `start_line` | `int` | 1-based line number where the definition begins (includes decorator if present) |
| `end_line` | `int` | 1-based line number where the definition ends (includes decorator if present) |

### `definition_dict` (plain `dict`)

| Key | Value Type | Purpose |
|---|---|---|
| AST node type string (e.g., `"function_definition"`) | `str` | Either a direct child node type to match (e.g., `"identifier"`) or a sentinel string (e.g., `"__assignment__"`) indicating a deep extraction strategy |

### `node_queue` (`deque[Node]`)

| Element | Type | Purpose |
|---|---|---|
| AST node | `Node` | Holds nodes pending visit during BFS traversal of the tree-sitter AST |

### `definition_list` (`list[DefinitionInfo]`)

| Element | Type | Purpose |
|---|---|---|
| Definition entry | `DefinitionInfo` | Accumulates discovered definitions during traversal before final sort and return |

### `_CONTAINER_DEFINITION_TYPES` (`set[str]`)

| Element | Type | Purpose |
|---|---|---|
| AST node type string | `str` | Identifies definition node types whose children should continue to be traversed after the node itself is recorded (currently only `"namespace_definition"`) |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file applies a **graceful degradation / skip-and-continue** policy throughout. No exceptions are raised or caught anywhere in the codebase. Instead, every extraction function returns `None` or an empty list to signal failure, and the caller silently skips the unresolvable node and proceeds with the remaining AST. The BFS traversal treats a failed name extraction not as a terminal error but as a signal to descend deeper into the child nodes, enabling recovery from partially structured or ambiguous AST nodes.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Name extraction failure (standard) | A definition node has no direct child matching the expected type | Returns `None`; BFS falls back to enqueuing child nodes for deeper traversal | Yes | The node itself is skipped as a definition; children are still searched |
| Name extraction failure (sentinel) | A sentinel-dispatched extractor (e.g. `__assignment__`, `__variable_declarator__`) cannot find the required intermediate node or field | Returns `None` | Yes | The node is skipped; BFS fallback enqueues children |
| Non-assignment expression statement | An `expression_statement` contains something other than an `assignment` (e.g. a bare function call) | Returns `None` from `_extract_assignment_name` | Yes | Node is silently skipped |
| Non-identifier left-hand side | The left side of an assignment is not a plain `identifier` (e.g. `obj.attr = 1`) | Returns `None`; falls through to `_extract_destructured_names` | Yes | Node is skipped unless it matches a destructuring pattern |
| Destructuring pattern (multi-name) | Standard extraction returns `None` and the LHS is a `pattern_list` (Python) or `object_pattern`/`array_pattern` (JS/TS) | `_extract_destructured_names` collects all identifier names and emits one `DefinitionInfo` per name | Yes | All extracted names are recorded; any non-identifier children within the pattern are silently ignored |
| Include-guard `#define` filtered out | A `preproc_def` node's name matches `_INCLUDE_GUARD_RE` | Definition is discarded; children are enqueued to continue traversal | Yes | The guard macro is excluded; inner content is still traversed |
| Missing `decorated_definition` inner node | A `decorated_definition` has no child whose type appears in `definition_dict` | Returns `None` from `_parse_decorated_definition`; no definition is appended | Yes | The decorated node is entirely skipped |
| C/C++ forward declaration | A `declaration` node has no `init_declarator` (e.g. `void f();`) | Returns `None` from `_extract_init_declarator_name`; BFS fallback processes child `function_declarator` | Yes | Name is recovered from the nested declarator by the BFS fallback |
| Missing or unexpected declarator type | `function_declarator` or `init_declarator` field is absent or holds an unexpected node type | Returns `None` from the respective extractor | Yes | Node is skipped; BFS fallback may recover it from children |
| Empty node children | Any node passed to an extractor has an empty `children` list | Guard checks (`if not node.children`) return `None` or `[]` immediately | Yes | Node is silently skipped |

---

## 3. Design Notes

- **No exceptions are used as a control-flow or error-signaling mechanism.** The entire policy relies on `None`/empty-list sentinel returns, keeping the BFS loop free of try-except blocks.
- **BFS fallback as the primary recovery mechanism.** When name extraction fails for a node, child nodes are re-enqueued rather than discarded. This is the deliberate mechanism for handling C/C++ nested declarator patterns and other languages where the definition name is not a direct child.
- **Silent omission over partial data.** When no valid name can be determined and no destructuring names are found, the node produces no output. Callers (dependents) apply their own `if defn.name` guards, consistent with the expectation that some definitions may be absent.
- **Filtering is a positive policy decision, not an error.** The exclusion of include-guard `#define` directives via `_INCLUDE_GUARD_RE` is a deliberate correctness filter, not an error recovery step, though it follows the same skip-and-continue pattern.

## Summary

Extracts named definitions from a tree-sitter AST and returns them sorted by line number. Public interface: `DefinitionInfo(name:str, type:str, start_line:int, end_line:int)` dataclass; `extract_definitions(root_node:Node, definition_dict:dict[str,str]) -> list[DefinitionInfo]`. Consumes a `definition_dict` mapping AST node type strings to name-extraction strategies (direct child type or sentinel like `__assignment__`). Produces a sorted `list[DefinitionInfo]` consumed by `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py`.
