# Design Document: codetwine/extractors/definitions.py

# Overview & Purpose

## 1. Module Summary

Extracts named definition information (functions, classes, variables, types, etc.) from a Tree-sitter AST and returns them as a sorted list of `DefinitionInfo` objects, enabling callers to enumerate all top-level symbols defined in a source file.

## 2. When to Use This Module

- **Symbol-to-file mapping** (`codetwine/import_to_path.py`): Call `extract_definitions(root_node, definition_dict)` to iterate over all named definitions in a parsed file and register each `defn.name` into a symbol lookup table.
- **File analysis / definition inventory** (`codetwine/file_analyzer.py`): Call `extract_definitions(root_node, definition_dict)` to produce a structured list of definitions including their `start_line`, `end_line`, and source context for reporting or indexing.
- **Usage analysis in target files** (`codetwine/extractors/usage_analysis.py`): Call `extract_definitions(root_node, target_def_dict)` on a target file's AST to collect all exported symbol names against which usage can be checked.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass | Holds the extracted metadata for a single named definition: its name, AST node type, and 1-based start/end line numbers. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | Traverses the AST via BFS using the provided language-specific `definition_dict`, extracts all named definitions (including decorated, destructured, and nested cases), and returns them sorted by start line. |

## 4. Design Decisions

- **BFS with selective descent**: The traversal uses a `deque`-based BFS rather than recursive DFS. When a node matches a definition type but name extraction fails (e.g., a C/C++ forward declaration), the node's children are enqueued to allow detection of nested declarator nodes such as `function_declarator`. Container types like `namespace_definition` explicitly continue descent even after being recorded as a definition.
- **Sentinel-based dispatch for deep name extraction**: `definition_dict` values that follow the `__name__` double-underscore convention signal that the name node is more than one level deep and require a dedicated extraction function, keeping the language-specific configuration declarative while isolating complex traversal logic in private helpers.
- **Language-agnostic configuration via `definition_dict`**: The public function accepts an externally supplied mapping of AST node types to name extraction strategies, making the core BFS logic reusable across all supported languages without modification.

# Definition Design Specifications

---

## Module-Level Constants

### `_INCLUDE_GUARD_RE`

| Attribute | Value |
|-----------|-------|
| Type | `re.Pattern[str]` |
| Purpose | Compiled regex that identifies C/C++ include-guard `#define` names |

Matches uppercase identifiers following the conventional include-guard naming pattern (e.g., `MY_HEADER_H`, `_MY_HEADER_HPP_`, `UTILS_H_INCLUDED`). Used to suppress include-guard definitions from appearing in extraction results.

---

## Data Classes

### `DefinitionInfo`

A plain data container describing a single extracted definition.

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The symbol name (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type string that produced this definition |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

**Responsibility:** Provides a language-agnostic, uniform record of a definition extracted from an AST, consumed by callers such as `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py`.

**When to use:** Instantiated internally by extraction functions; callers receive a list of these from `extract_definitions`.

---

## Public Functions

### `extract_definitions`

```python
def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]
```

**Responsibility:** Entry point for all definition extraction; performs a BFS traversal of the AST and returns every discovered definition as a list sorted by start line.

**When to use:** Called by any module that needs to enumerate all top-level or nested definitions in a parsed source file.

**`definition_dict` contract:**

| Value format | Meaning |
|---|---|
| A plain node-type string (e.g., `"identifier"`) | The name lives in a direct child of that type |
| A sentinel string (e.g., `"__assignment__"`) | The name is nested deeper; a dedicated extractor is dispatched |

**Design decisions:**
- BFS (not DFS) is used so that definitions are encountered in roughly document order and the queue can be selectively extended for container nodes.
- `namespace_definition` (and other `_CONTAINER_DEFINITION_TYPES`) triggers child traversal even after a definition is recorded, enabling discovery of nested definitions.
- `decorated_definition` is handled by a dedicated path (`_parse_decorated_definition`) rather than the generic path, to preserve the full decorated range.
- When name extraction yields `None`, child nodes are enqueued as a BFS fallback, enabling detection of definitions nested inside wrapper nodes (e.g., `function_declarator` inside `declaration`).
- When name extraction yields `None` but `_extract_destructured_names` succeeds, multiple `DefinitionInfo` entries sharing the same node range are emitted, one per destructured name.
- C/C++ include-guard `preproc_def` nodes are silently discarded even if a name is found.

**Constraints & edge cases:**
- `root_node` must be a valid tree-sitter `Node` representing the entire file.
- `definition_dict` must follow the documented key/value contract; unrecognized sentinel strings fall through to the direct-child search and silently return `None`.
- Nodes whose type is neither in `definition_dict` nor a container are traversed but never recorded.
- The returned list is always sorted ascending by `start_line`; caller ordering assumptions are safe.

---

## Private Functions

### `_parse_decorated_definition`

```python
def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None
```

**Responsibility:** Extracts the definition buried inside a `decorated_definition` node and adjusts the reported line range to span the entire decorated construct including decorator lines.

**When to use:** Invoked exclusively by `extract_definitions` when it encounters a `decorated_definition` node that is also present in `definition_dict`.

**Design decisions:** The start/end lines of the returned `DefinitionInfo` are overwritten to reflect the outer `decorated_definition` boundaries rather than the inner node boundaries, so callers see the complete decorated range.

**Constraints & edge cases:**
- Returns `None` if no recognizable inner definition node is found among direct children, or if `_parse_definition_node` itself returns `None`.
- Only the last matching inner node among children is used if multiple definition-type children exist.

---

### `_parse_definition_node`

```python
def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None
```

**Responsibility:** Converts a single AST definition node into a `DefinitionInfo` by delegating name extraction to `_extract_name`.

**When to use:** Called by both `extract_definitions` and `_parse_decorated_definition` whenever a candidate definition node needs to be turned into a `DefinitionInfo`.

**Constraints & edge cases:**
- Returns `None` when `_extract_name` cannot resolve a name; the caller is responsible for the fallback behaviour.
- Line numbers are converted from 0-based (tree-sitter) to 1-based in this function.

---

### `_extract_name`

```python
def _extract_name(node: Node, name_type: str) -> str | None
```

**Responsibility:** Central dispatcher that routes name extraction to the correct strategy based on whether `name_type` is a sentinel value or a standard node-type string.

**When to use:** Called only by `_parse_definition_node`; not intended for direct use by callers outside this module.

**Dispatch table:**

| `name_type` value | Delegated to |
|---|---|
| `"__assignment__"` | `_extract_assignment_name` |
| `"__variable_declarator__"` | `_extract_variable_declarator_name` |
| `"__init_declarator__"` | `_extract_init_declarator_name` |
| `"__function_declarator__"` | `_extract_function_declarator_name` |
| Any other string | Direct child search by node type |

**Constraints & edge cases:**
- An unrecognised sentinel-like string (e.g., `"__unknown__"`) will fall through to the direct-child search, which will find no child of that type and return `None`.
- Returns the decoded UTF-8 text of the matched child node.

---

### `_extract_assignment_name`

```python
def _extract_assignment_name(node: Node) -> str | None
```

**Responsibility:** Extracts the left-hand-side identifier from a Python top-level simple variable assignment inside an `expression_statement` node.

**When to use:** Dispatched by `_extract_name` for nodes associated with `"__assignment__"`.

**Constraints & edge cases:**
- Returns `None` if the `expression_statement` content is not an `assignment` (e.g., a bare function call).
- Returns `None` if the left-hand side is not a plain `identifier` (e.g., attribute assignment `obj.x = 1` or subscript assignment).
- Does not handle tuple/list unpacking on the left-hand side; that case is handled by `_extract_destructured_names`.

---

### `_extract_variable_declarator_name`

```python
def _extract_variable_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the declared variable name from a JS/TS `lexical_declaration` or `variable_declaration` node.

**When to use:** Dispatched by `_extract_name` for nodes associated with `"__variable_declarator__"`.

**Constraints & edge cases:**
- Returns `None` if no `variable_declarator` child exists.
- Returns `None` if the `name` field is not present (e.g., destructuring patterns); those cases are handled by `_extract_destructured_names`.
- Only the first `variable_declarator` child's name is returned; multi-declarator statements (e.g., `let a = 1, b = 2`) yield only `a`.

---

### `_extract_function_declarator_name`

```python
def _extract_function_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the function name from a C/C++ `function_definition` where the name is nested inside a `function_declarator` child rather than being a direct child of the definition.

**When to use:** Dispatched by `_extract_name` for nodes associated with `"__function_declarator__"`.

**Design decisions:** For C++ class method implementations (e.g., `Shape::get_name`), the declarator field is a `qualified_identifier`; in that case, the last `identifier` child of the `qualified_identifier` is returned as the method name, discarding the class-qualifier prefix.

**Constraints & edge cases:**
- Returns `None` if the `declarator` field is absent or is not a `function_declarator`.
- Returns `None` if the inner declarator name node type is neither `identifier` nor `qualified_identifier`.
- For `qualified_identifier`, if no `identifier` child exists (unusual), `None` is returned.

---

### `_extract_init_declarator_name`

```python
def _extract_init_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the variable name from a C/C++ `declaration` that has an `init_declarator` (i.e., a declaration with an initializer, not a forward declaration).

**When to use:** Dispatched by `_extract_name` for nodes associated with `"__init_declarator__"`.

**Design decisions:** Intentionally returns `None` for forward declarations (those without `init_declarator`), triggering the BFS fallback in `extract_definitions` which subsequently picks up the inner `function_declarator`.

**Constraints & edge cases:**
- Returns `None` when the `declarator` field is absent or is not an `init_declarator`.
- Returns `None` if the `init_declarator`'s own `declarator` field is not an `identifier`.

---

### `_extract_destructured_names`

```python
def _extract_destructured_names(node: Node, name_type: str) -> list[str]
```

**Responsibility:** Collects all variable names from a destructuring assignment or declaration, returning an empty list when the node does not represent a destructuring pattern.

**When to use:** Called by `extract_definitions` only after `_parse_definition_node` has already returned `None` for the same node, as a secondary attempt before falling back to child BFS.

**Supported patterns:**

| Language | Pattern | Mechanism |
|---|---|---|
| Python | `X, Y = 1, 2` | Collects `identifier` children from `pattern_list` on the left-hand side |
| JS/TS | `const { a, b } = obj` | Delegates to `_collect_identifiers_from_pattern` on `object_pattern` |
| JS/TS | `const [a, b] = arr` | Delegates to `_collect_identifiers_from_pattern` on `array_pattern` |

**Constraints & edge cases:**
- Returns an empty list for `name_type` values other than `"__assignment__"` and `"__variable_declarator__"`.
- Returns an empty list if the structure does not match a destructuring pattern.

---

### `_collect_identifiers_from_pattern`

```python
def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]
```

**Responsibility:** Recursively traverses an `object_pattern` or `array_pattern` node and collects every bound variable name, including those in nested patterns.

**When to use:** Called exclusively by `_extract_destructured_names` when a JS/TS destructuring pattern is detected.

**Handled child node types:**

| Child type | Action |
|---|---|
| `identifier` | Name appended directly |
| `shorthand_property_identifier_pattern` | Name appended directly (handles `{ a, b }` shorthand) |
| `object_pattern` / `array_pattern` | Recursed into |
| `pair_pattern` | The `value` field is inspected; if `identifier`, appended; if nested pattern, recursed into |

**Constraints & edge cases:**
- Does not handle rest elements (e.g., `...rest`) as these are not `identifier` or `shorthand_property_identifier_pattern` nodes.
- `pair_pattern` keys are ignored; only the local binding name (value) is collected.
- Unrecognised child types are silently skipped.

# Dependency Description

### Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports are from the standard library (`re`, `collections`, `dataclasses`) and the third-party package `tree_sitter`. No project-internal modules are imported.

---

### Dependents (modules that import this file)

Three project-internal modules depend on `codetwine/extractors/definitions.py`, each importing the `extract_definitions` function:

- **`codetwine/import_to_path.py`** → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to parse a file's AST root node and enumerate all top-level definitions, mapping each definition name to its source file path in a symbol-to-file registry.

- **`codetwine/file_analyzer.py`** → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to retrieve all definitions from a file's AST, then constructs structured records containing each definition's name, start/end line numbers, and source context text.

- **`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to extract all definition names from a target file's AST, collecting those names for subsequent usage analysis.

---

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` (one-way)
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` (one-way)

`codetwine/extractors/definitions.py` itself imports no project-internal modules, so it sits at the leaf of the internal dependency graph. It is a pure provider of definition-extraction functionality and does not call back into any of its dependents.

# Data Flow

## 1. Inputs

This module receives two inputs via the `extract_definitions` function:

- **`root_node: Node`** — A tree-sitter AST `Node` representing the root of a parsed source file. This node forms the entry point for BFS traversal across the entire file tree.
- **`definition_dict: dict[str, str]`** — A caller-supplied configuration mapping that controls which AST node types are treated as definitions and how their names are extracted. Keys are AST node type strings (e.g. `"function_definition"`); values are either a direct child node type name (e.g. `"identifier"`) or a sentinel string (e.g. `"__assignment__"`).

No file I/O or global config values are read within this module. All inputs arrive as function arguments.

---

## 2. Transformation Overview

### Stage 1 — BFS Traversal of the AST

Starting from `root_node`, nodes are consumed from a `deque` queue. Each node is classified into one of three categories:

- **Decorated definition** (`decorated_definition` in `definition_dict`): routed to `_parse_decorated_definition`.
- **Known definition type** (key present in `definition_dict`): routed to `_parse_definition_node`.
- **Unknown node**: its children are enqueued and traversal continues deeper.

Container-type definitions (e.g. `namespace_definition`) also enqueue their children even after being recorded, allowing nested definitions to be discovered.

### Stage 2 — Name Extraction per Node

Each candidate node is passed to `_parse_definition_node`, which calls `_extract_name` to obtain the definition's name. `_extract_name` dispatches based on the sentinel value in `definition_dict`:

| Sentinel Value | Extraction Path |
|---|---|
| `__assignment__` | `expression_statement → assignment → left (identifier)` |
| `__variable_declarator__` | `lexical_declaration → variable_declarator → name (identifier)` |
| `__init_declarator__` | `declaration → init_declarator → declarator (identifier)` |
| `__function_declarator__` | `function_definition → function_declarator → declarator (identifier or qualified_identifier)` |
| Standard string | Direct child of node matching the given type string |

### Stage 3 — Failure Recovery

If name extraction returns `None`, two fallback paths are tried in order:

1. **Destructured name extraction** via `_extract_destructured_names`: handles Python tuple unpacking (`pattern_list`) and JS/TS object/array patterns. If identifiers are found, multiple `DefinitionInfo` entries are produced from a single AST node.
2. **BFS fallback**: if destructuring also yields nothing, the node's children are enqueued for further traversal. This enables discovery of nested declarators (e.g. a `function_declarator` inside a C/C++ `declaration`).

### Stage 4 — Include-Guard Filtering

For `preproc_def` nodes (C/C++ `#define`), the extracted name is tested against the regex `_INCLUDE_GUARD_RE`. Matching names (include guards) are discarded, and the node's children are enqueued instead.

### Stage 5 — Sorting

The accumulated `list[DefinitionInfo]` is sorted in ascending order by `start_line` before being returned.

---

## 3. Outputs

The sole output of this module is the return value of `extract_definitions`:

- **`list[DefinitionInfo]`** — A list of `DefinitionInfo` dataclass instances, sorted by `start_line` in ascending order. Each instance represents one named definition found in the source file.

There are no file writes, global state mutations, or other side effects.

Callers (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`) consume this list to map definition names to file paths, build structured definition metadata, or collect exportable symbol names.

---

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The extracted identifier name of the definition (function, class, variable, etc.) |
| `type` | `str` | The AST node type string that produced this definition (e.g. `"function_definition"`) |
| `start_line` | `int` | 1-based line number where the definition begins in the source file |
| `end_line` | `int` | 1-based line number where the definition ends in the source file |

### `definition_dict` (plain `dict[str, str]`)

| Key | Value | Purpose |
|---|---|---|
| AST node type string (e.g. `"function_definition"`) | Direct child node type string (e.g. `"identifier"`) or sentinel (e.g. `"__assignment__"`) | Controls which node types are recognized as definitions and which extraction strategy is used for the name |

### `node_queue` (`deque[Node]`)

| Element | Type | Purpose |
|---|---|---|
| AST node | `tree_sitter.Node` | Holds pending nodes for BFS traversal; nodes are appended and consumed front-to-back |

### `definition_list` (`list[DefinitionInfo]`)

| Element | Type | Purpose |
|---|---|---|
| Definition record | `DefinitionInfo` | Accumulates all successfully extracted definitions before final sort and return |

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation** policy throughout. No exceptions are raised, and no logging occurs. Instead, every extraction function returns `None` or an empty list to signal failure, and the caller silently skips or falls back to an alternative extraction path. The BFS traversal continues regardless of whether any individual node yields a definition, ensuring that a failure on one node never halts processing of the rest of the AST.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Name extraction failure (standard) | A definition node has no direct child matching the expected `name_node_type` | `_extract_name` returns `None`; `_parse_definition_node` returns `None` | Yes | BFS descends into child nodes to continue searching for nested definitions |
| Name extraction failure (sentinel) | A sentinel-dispatched extractor (e.g. `__assignment__`, `__init_declarator__`) cannot locate the expected sub-node or field | The specific `_extract_*` function returns `None`; caller proceeds to destructuring fallback or BFS descent | Yes | Falls back to `_extract_destructured_names`; if that also returns empty, child nodes are queued for BFS |
| Destructuring extraction failure | Node is not a recognized destructuring pattern, or expected child nodes are absent | `_extract_destructured_names` returns an empty list | Yes | BFS descends into child nodes |
| Decorated definition without inner node | A `decorated_definition` node contains no child that matches a key in `definition_dict` | `_parse_decorated_definition` returns `None`; no `DefinitionInfo` is appended | Yes | The decorated node is silently skipped |
| Include guard `#define` filtered out | A `preproc_def` node's extracted name matches `_INCLUDE_GUARD_RE` | The definition is not appended; BFS continues into child nodes | Yes | The include guard macro is excluded from the definition list; no other nodes are affected |
| Empty or structurally unexpected node | A node has no children where children are required (e.g. `expression_statement` with empty `children`) | Guard checks (`if not node.children`) cause the extractor to return `None` | Yes | Node is silently skipped; BFS may descend into its children |
| Pattern node with unrecognised child type | An `object_pattern` or `array_pattern` contains child node types not handled by `_collect_identifiers_from_pattern` | Unrecognised children are silently ignored in the iteration | Yes | Only recognised pattern elements contribute names; unrecognised ones are omitted |

---

## 3. Design Notes

**Silent-skip over fail-fast.** The design deliberately avoids exceptions and logging. Every function in the extraction pipeline returns a typed sentinel (`None` / `[]`) on failure, allowing callers to branch without error propagation. This is appropriate for a static-analysis tool where partial results are more useful than an aborted run.

**BFS fallback as the primary recovery mechanism.** When name extraction fails for a node, the node's children are enqueued rather than the node being discarded entirely. This allows definitions nested multiple levels deep (e.g. `function_declarator` inside a C/C++ `declaration`) to be discovered without requiring every possible nesting to be enumerated in `definition_dict`.

**Layered fallback within a single node.** For nodes using sentinel extractors, there is a two-level fallback: first `_parse_definition_node` is tried, then `_extract_destructured_names`, and only if both yield nothing does BFS descent occur. This ordering prioritises the most specific extraction before widening the search.

**Domain-specific filtering as a special case.** The include-guard filter is the only case where a *successfully extracted* name is intentionally discarded. It is handled inline rather than through a general mechanism, reflecting its narrow, language-specific nature (C/C++ `preproc_def` only).

# Summary

**`codetwine/extractors/definitions.py`**: Extracts named definitions from a Tree-sitter AST and returns them as sorted `DefinitionInfo` objects.

**Public interface:**
- `DefinitionInfo` (dataclass): `name: str`, `type: str`, `start_line: int`, `end_line: int`
- `extract_definitions(root_node: Node, definition_dict: dict[str, str]) -> list[DefinitionInfo]`

**Key data structures:**
- `definition_dict`: maps AST node-type strings to name-extraction strategies (direct child type or sentinel like `"__assignment__"`)
- `list[DefinitionInfo]`: sorted by `start_line`, consumed by `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py`
