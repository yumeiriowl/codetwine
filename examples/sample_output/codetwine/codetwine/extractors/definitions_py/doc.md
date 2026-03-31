# Design Document: codetwine/extractors/definitions.py

## Overview & Purpose

## 1. Module Summary

Extracts named definitions (functions, classes, variables, types, etc.) from a parsed AST and returns them as a sorted list of `DefinitionInfo` objects, enabling downstream modules to map symbol names to their source locations.

## 2. When to Use This Module

- **Symbol-to-file mapping** (`import_to_path.py`): Call `extract_definitions(root_node, definition_dict)` to enumerate all definition names in a file and register each name in a symbol lookup map.
- **File analysis / definition inventory** (`file_analyzer.py`): Call `extract_definitions(root_node, definition_dict)` to obtain the name, type, start line, and end line of every definition in a file, used to build a structured definition listing with inline source context.
- **Usage analysis** (`extractors/usage_analysis.py`): Call `extract_definitions(root_node, definition_dict)` on a target file to collect all exported definition names, which are then checked against import/usage references.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass | Holds the name, AST node type, and 1-based start/end line numbers for a single definition. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | Traverses the AST via BFS, matches nodes against `definition_dict`, extracts names using language-specific strategies, and returns all found definitions sorted by start line. |

## 4. Design Decisions

- **BFS traversal with selective descent**: The traversal uses a `deque`-based BFS rather than recursive DFS. Child nodes are only enqueued when the current node is not a recognized definition, when name extraction fails (fallback to children), or when the node is a designated container type (e.g. `namespace_definition`). This avoids redundant deep traversal inside opaque definition bodies.
- **Sentinel-value dispatch for deep name extraction**: `definition_dict` values that follow the `__sentinel__` format (e.g. `__assignment__`, `__variable_declarator__`) signal that the name is nested too deeply for a direct-child search, triggering dispatch to a dedicated extraction function. This keeps language-specific AST quirks isolated from the main traversal loop.
- **Destructuring fallback**: When standard name extraction returns `None` and a destructuring pattern is detected (e.g. Python tuple unpacking, JS object/array patterns), multiple `DefinitionInfo` entries are emitted for a single AST node—one per bound name.
- **Include-guard suppression**: `preproc_def` nodes whose names match `_INCLUDE_GUARD_RE` are silently skipped and their children are enqueued instead, preventing C/C++ header guards from polluting the definition list.

## Definition Design Specifications

---

## Module-Level Constant

### `_INCLUDE_GUARD_RE`

A compiled regular expression used to identify C/C++ include-guard `#define` directives. Matches names that follow common include-guard naming conventions (all-uppercase, optional leading/trailing underscores, ending in `_H`, `_HPP`, `_HXX`, optionally suffixed with `_INCLUDED`). Definitions matching this pattern are suppressed from output.

---

## Data Classes

### `DefinitionInfo`

A frozen-like data container representing a single extracted definition from source code.

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The definition's identifier (function name, class name, variable name, etc.) |
| `type` | `str` | The AST node type string (e.g. `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

**Responsibility:** Acts as a structured, language-agnostic record of one code definition extracted from an AST, normalizing line numbers to 1-based across all languages.

**When to use:** Instantiated internally during extraction; consumed by callers in `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py` to map definition names to files and line ranges.

**Constraints:** `start_line` and `end_line` are always 1-based. `name` may be an empty string in pathological cases (callers guard against this with `if defn.name`).

---

## Public Functions

### `extract_definitions`

```python
def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]
```

- **`root_node`**: The tree-sitter `Node` representing the root of a parsed file's AST.
- **`definition_dict`**: A mapping from AST node type strings to name-extraction strategies. Values are either a child node type string (e.g. `"identifier"`) or a sentinel string of the form `"__name__"` (e.g. `"__assignment__"`).
- **Returns**: A list of `DefinitionInfo` objects sorted ascending by `start_line`.

**Responsibility:** The primary entry point for definition extraction; performs a breadth-first traversal of the AST, identifies definition nodes according to `definition_dict`, extracts names, and handles special cases like decorated definitions, destructuring, include guards, and container namespaces.

**When to use:** Called once per source file after parsing, passing the file's AST root and the language-appropriate `definition_dict`.

**Design decisions:**
- **BFS over DFS**: Uses a `deque`-based BFS so that the traversal order is consistent and namespace/container nodes can selectively enqueue their children for continued descent without recursion depth limits.
- **Selective child descent**: When a definition node is a container type (currently only `namespace_definition`), children are enqueued even after the container itself is recorded—enabling nested definitions inside namespaces to be captured.
- **Fallback on name extraction failure**: If `_parse_definition_node` returns `None`, `_extract_destructured_names` is attempted before falling back to enqueueing children. This supports C/C++ forward declarations (which lack an `init_declarator`) and allows the BFS to descend into a nested `function_declarator`.
- **Include-guard filtering**: `preproc_def` nodes whose extracted names match `_INCLUDE_GUARD_RE` are silently dropped; their children are still enqueued.

**Constraints & edge cases:**
- `decorated_definition` is handled separately from all other node types and must be present in `definition_dict` to be processed.
- The set of container definition types is hard-coded to `{"namespace_definition"}` within the function.
- Definitions are returned sorted; insertion order during BFS does not determine final order.

---

## Private Functions

### `_parse_decorated_definition`

```python
def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None
```

**Responsibility:** Extracts a `DefinitionInfo` from a `decorated_definition` node by locating the inner non-decorator definition child and delegating name extraction to `_parse_definition_node`, then widening the line range to encompass the decorator.

**When to use:** Called by `extract_definitions` exclusively when the current BFS node is of type `"decorated_definition"`.

**Design decisions:** The returned `DefinitionInfo` reports the start/end lines of the *outer* `decorated_definition` node, not the inner definition—ensuring that the decorator lines are included in the definition's range.

**Constraints:** Returns `None` if no recognizable inner definition node exists among direct children. Only the last matching inner definition child is used if multiple exist (though this is not an expected real-world case).

---

### `_parse_definition_node`

```python
def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None
```

**Responsibility:** Converts a single AST definition node into a `DefinitionInfo` by delegating name extraction to `_extract_name` and packaging the result with 1-based line numbers.

**When to use:** Called whenever a node matching a `definition_dict` key is found during BFS traversal, or from `_parse_decorated_definition` for the inner node.

**Constraints:** Returns `None` if `_extract_name` cannot determine a name, signaling the caller to attempt fallback strategies.

---

### `_extract_name`

```python
def _extract_name(node: Node, name_type: str) -> str | None
```

**Responsibility:** Dispatches name extraction to the appropriate dedicated function when `name_type` is a sentinel value, or performs a direct child search for standard node type strings.

**When to use:** Called by `_parse_definition_node` (and transitively by `_parse_decorated_definition`) as the single routing point for all name-extraction strategies.

**Design decisions:** Sentinel values follow the `"__xxx__"` convention, making dispatch purely string-based without requiring a registry or class hierarchy. The standard (non-sentinel) path iterates direct children only, intentionally avoiding deep traversal to keep the common case simple.

**Constraints:**
- Supported sentinels: `__assignment__`, `__variable_declarator__`, `__init_declarator__`, `__function_declarator__`.
- For the standard path, only the *first* matching child by node type is returned.
- Returns `None` for any unrecognized sentinel or absent child.

---

### `_extract_assignment_name`

```python
def _extract_assignment_name(node: Node) -> str | None
```

- **`node`**: An `expression_statement` AST node.

**Responsibility:** Extracts a simple variable name from a Python top-level assignment where the left-hand side is a bare identifier.

**When to use:** Invoked by `_extract_name` when `name_type == "__assignment__"`.

**Constraints:** Returns `None` for non-assignment expression statements (e.g. function calls), attribute assignments (`obj.attr = 1`), and any left-hand side that is not a plain `identifier` (handled separately by `_extract_destructured_names`).

---

### `_extract_variable_declarator_name`

```python
def _extract_variable_declarator_name(node: Node) -> str | None
```

- **`node`**: A `lexical_declaration` or `variable_declaration` AST node (JS/TS).

**Responsibility:** Extracts the declared variable name from a JS/TS `const`/`let`/`var` declaration where the name is a simple identifier.

**When to use:** Invoked by `_extract_name` when `name_type == "__variable_declarator__"`.

**Constraints:** Returns `None` if no `variable_declarator` child exists, or if the `name` field of the declarator is absent. Destructured patterns (`object_pattern`, `array_pattern`) cause `None` to be returned here; they are handled by `_extract_destructured_names`.

---

### `_extract_function_declarator_name`

```python
def _extract_function_declarator_name(node: Node) -> str | None
```

- **`node`**: A `function_definition` AST node (C/C++).

**Responsibility:** Navigates the C/C++ two-level `function_definition → function_declarator → identifier` structure to retrieve a function or method name, including qualified names for C++ class method implementations.

**When to use:** Invoked by `_extract_name` when `name_type == "__function_declarator__"`.

**Design decisions:** For `qualified_identifier` declarators (C++ `ClassName::method`), only the last `identifier` child is returned, giving the unqualified method name rather than the fully qualified form.

**Constraints:** Returns `None` if the `declarator` field is absent or not a `function_declarator`. Returns `None` for pointer-to-function or other exotic declarator types not covered by `identifier` or `qualified_identifier`.

---

### `_extract_init_declarator_name`

```python
def _extract_init_declarator_name(node: Node) -> str | None
```

- **`node`**: A `declaration` AST node (C/C++).

**Responsibility:** Extracts a variable name from a C/C++ initialized variable declaration, deliberately returning `None` for forward declarations that lack an `init_declarator`.

**When to use:** Invoked by `_extract_name` when `name_type == "__init_declarator__"`.

**Design decisions:** Returning `None` for forward declarations is intentional—it triggers the BFS fallback in `extract_definitions`, which descends into child nodes and eventually locates a `function_declarator` to extract the function name.

**Constraints:** Returns `None` if the `declarator` field is absent, not an `init_declarator`, or if the inner `declarator` field is not a plain `identifier`.

---

### `_extract_destructured_names`

```python
def _extract_destructured_names(node: Node, name_type: str) -> list[str]
```

**Responsibility:** Handles destructuring patterns (multi-variable assignments or destructured declarations) that produce multiple names from a single statement, returning all names as a list.

**When to use:** Called by `extract_definitions` as a fallback when `_parse_definition_node` returns `None`, before the final fallback of descending into child nodes.

**Design decisions:** Only two sentinel types are handled (`__assignment__` for Python tuple unpacking, `__variable_declarator__` for JS/TS object/array destructuring). All other sentinels and standard name types return an empty list, making this function a no-op for unsupported patterns.

**Constraints:**
- For `__assignment__`: only `pattern_list` left-hand sides are handled; nested patterns within `pattern_list` are not recursed into—only direct `identifier` children are collected.
- For `__variable_declarator__`: delegates to `_collect_identifiers_from_pattern` for actual name collection.
- Returns `[]` (not `None`) to allow direct truthiness testing by the caller.

---

### `_collect_identifiers_from_pattern`

```python
def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]
```

- **`pattern_node`**: An `object_pattern` or `array_pattern` AST node (JS/TS).

**Responsibility:** Recursively collects all bound variable names from a JS/TS destructuring pattern, including nested patterns and `key: localName` pair patterns.

**When to use:** Called by `_extract_destructured_names` when a `variable_declarator`'s name is an `object_pattern` or `array_pattern`.

**Design decisions:**
- Handles `shorthand_property_identifier_pattern` (the `{ a, b }` shorthand form) as a direct name source.
- For `pair_pattern` nodes (`{ key: localName }`), only the `value` side is collected because `value` is the locally bound name; the `key` is not a new binding.
- Recursion handles arbitrarily nested patterns (e.g. `{ a, inner: { b } }`).

**Constraints:** Does not handle rest elements (`...rest`) or patterns with default values beyond their identifier component; those node types are simply skipped if not covered by the listed cases.

## Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies are present. This file imports only from the standard library (`re`, `collections`, `dataclasses`) and the third-party package `tree_sitter`. There are no project-internal modules that this file imports.

---

## Dependents (modules that import this file)

The following project-internal modules depend on `codetwine/extractors/definitions_py/definitions.py` by importing `extract_definitions`:

- **`codetwine/import_to_path.py`** → this module : Uses `extract_definitions` to parse an AST root node and iterate over all definitions in a file, registering each definition's name into a symbol-to-file mapping.

- **`codetwine/file_analyzer.py`** → this module : Uses `extract_definitions` to obtain the full list of definitions from an AST root node, consuming each `DefinitionInfo`'s `start_line`, `end_line`, and associated source content to build a structured definition summary.

- **`codetwine/extractors/usage_analysis.py`** → this module : Uses `extract_definitions` to enumerate definition names from a target file's AST, collecting those names for use in usage analysis.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions_py/definitions.py`
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions_py/definitions.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions_py/definitions.py`

This file does not import from any of these dependents, and none of the dependents are imported by this file. The data flow is strictly one-way: the three dependent modules consume `extract_definitions` (and the `DefinitionInfo` dataclass it returns) from this module.

## Data Flow

## 1. Inputs

| Input | Format | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | The root AST node produced by parsing an entire source file |
| `definition_dict` | `dict[str, str]` | A mapping from AST node type strings to name-extraction strategies; keys are node types (e.g. `"function_definition"`), values are either a child node type name (e.g. `"identifier"`) or a sentinel string (e.g. `"__assignment__"`) |

No file I/O or configuration reads occur within this module. All inputs arrive as function arguments.

---

## 2. Transformation Overview

```
root_node
    │
    ▼
[BFS traversal of the AST via deque]
    │
    ├─ node.type == "decorated_definition"
    │       │
    │       ▼
    │   _parse_decorated_definition()
    │       │  finds inner definition child
    │       │  delegates to _parse_definition_node()
    │       │  overwrites start/end lines with decorator range
    │       ▼
    │   DefinitionInfo (or None → skip)
    │
    ├─ node.type in definition_dict (non-decorated)
    │       │
    │       ▼
    │   _parse_definition_node()
    │       │  calls _extract_name()
    │       │       │
    │       │       ├─ sentinel dispatch:
    │       │       │   __assignment__           → _extract_assignment_name()
    │       │       │   __variable_declarator__  → _extract_variable_declarator_name()
    │       │       │   __init_declarator__      → _extract_init_declarator_name()
    │       │       │   __function_declarator__  → _extract_function_declarator_name()
    │       │       └─ standard: scan direct children for child.type == name_type
    │       ▼
    │   DefinitionInfo (name found)
    │   OR None →
    │       ├─ _extract_destructured_names()
    │       │       produces multiple DefinitionInfo entries
    │       └─ None → push children back onto queue (BFS fallback)
    │
    │   [#include guard filter applied to preproc_def nodes]
    │   [container definitions (e.g. namespace) also push children onto queue]
    │
    └─ non-definition node → push children onto queue
    │
    ▼
definition_list: list[DefinitionInfo]
    │
    ▼
sorted by start_line ascending
    │
    ▼
list[DefinitionInfo]  ← returned to caller
```

**Stages:**

1. **BFS dispatch** — Each node dequeued is classified as a decorated definition, a matching definition type, or a non-definition; the last category simply extends the queue with children.
2. **Name extraction** — For each definition node, the name is obtained either through sentinel-specific deep traversal or through a direct child scan.
3. **Fallback handling** — If name extraction returns `None`, a destructuring check is attempted before the BFS fallback pushes children back for deeper search.
4. **Filtering** — `preproc_def` nodes whose names match the include-guard regex are suppressed.
5. **Sorting** — The accumulated `definition_list` is sorted by `start_line` before being returned.

---

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| Return value of `extract_definitions` | `list[DefinitionInfo]` | All discovered definitions, sorted by start line in ascending order |

There are no file writes or side effects. The result is consumed by callers in `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py`, each of which accesses `.name`, `.start_line`, `.end_line`, and `.type` fields.

---

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The extracted definition name (function name, class name, variable name, etc.) |
| `type` | `str` | The AST node type string that produced this definition (e.g. `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition begins; for decorated definitions this covers the decorator |
| `end_line` | `int` | 1-based line number where the definition ends |

---

### `definition_dict` (input parameter)

| Key | Value | Purpose |
|---|---|---|
| AST node type string (e.g. `"function_definition"`) | Child node type string (e.g. `"identifier"`) | Directs the extractor to find the name among direct children with that type |
| AST node type string | Sentinel string (e.g. `"__assignment__"`) | Directs the extractor to use a dedicated deep-traversal function instead of a direct child scan |

---

### `node_queue` (internal)

| Aspect | Type | Purpose |
|---|---|---|
| Contents | `deque[tree_sitter.Node]` | Holds AST nodes pending inspection; drives the BFS traversal of the entire syntax tree |

---

### `definition_list` (internal accumulator)

| Aspect | Type | Purpose |
|---|---|---|
| Contents | `list[DefinitionInfo]` | Collects all `DefinitionInfo` instances as they are produced during BFS; sorted before being returned |

## Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation with BFS fallback** approach. There are no exceptions raised or caught anywhere in the module. Instead, every extraction function returns `None` or an empty list to signal failure, and the BFS traversal in `extract_definitions` treats a failed extraction as a signal to descend deeper into child nodes rather than abandoning the search. The overall extraction process always completes and returns whatever definitions were successfully found, silently skipping anything that could not be resolved.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Name extraction failure (single node) | A definition node's name cannot be obtained via the primary extraction path (e.g., no matching child node type found) | `_parse_definition_node` returns `None`; BFS falls through to `_extract_destructured_names`, then to child-node re-queuing | Yes | The node itself is skipped; its children are enqueued for continued search |
| Destructuring extraction yields nothing | The node is neither a standard assignment nor a recognized destructuring pattern (`pattern_list`, `object_pattern`, `array_pattern`) | `_extract_destructured_names` returns an empty list; BFS re-queues child nodes | Yes | The node is skipped; children are enqueued |
| Decorated definition has no inner definition | No recognized definition node type is found among the children of a `decorated_definition` node | `_parse_decorated_definition` returns `None`; the result is discarded silently | Yes | The decorated definition is omitted from results entirely |
| Include-guard `#define` detected | A `preproc_def` node's extracted name matches the `_INCLUDE_GUARD_RE` pattern | The definition is discarded; child nodes are re-queued | Yes | The include-guard macro is excluded from the definition list; children are still searched |
| Forward declaration (C/C++) | A `declaration` node has no `init_declarator` (e.g., `void freeFunction();`) | `_extract_init_declarator_name` returns `None`; BFS re-queues children, allowing the nested `function_declarator` to be found | Yes | The outer declaration is skipped; the inner declarator is detected on the next BFS iteration |
| Empty or structurally unexpected node | A node has no children or the expected field name (e.g., `left`, `declarator`, `name`) is absent | The individual extraction function returns `None` or `[]`; BFS continues | Yes | The specific node contributes no definition; traversal proceeds normally |
| Unrecognized node type | A node's type is not present in `definition_dict` | BFS enqueues the node's children without recording a definition | Yes | No definition recorded; subtree is still traversed |

---

## 3. Design Notes

- **No exceptions, no logging.** The module intentionally avoids raising exceptions or emitting log messages. All failure states are expressed as `None` / empty-list return values, keeping the extraction pipeline side-effect-free from the caller's perspective. Callers in `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py` guard against empty names (`if defn.name`) but otherwise assume the returned list is always valid.
- **BFS descent as the recovery mechanism.** The decision to re-enqueue child nodes when name extraction fails is the core recovery strategy. This allows a single traversal pass to handle both shallowly-named nodes (direct child lookup) and deeply-nested naming patterns (e.g., C/C++ declarator chains) without requiring separate pre-processing passes.
- **Sentinel-based dispatch isolates failure scope.** By routing each special extraction case through a dedicated function (`_extract_assignment_name`, `_extract_variable_declarator_name`, etc.), a failure in one extractor has no effect on any other; each returns `None` independently.
- **Container types receive special treatment.** For node types listed in `_CONTAINER_DEFINITION_TYPES` (e.g., `namespace_definition`), successful name extraction does *not* stop child traversal, ensuring that nested definitions are not silently dropped as a side effect of recording the container itself.

## Summary

Extracts named definitions from a parsed AST, returning sorted `DefinitionInfo` objects for symbol-to-file mapping.

**Public API:**
- `DefinitionInfo(name:str, type:str, start_line:int, end_line:int)` — dataclass holding one definition's identity and line range
- `extract_definitions(root_node:Node, definition_dict:dict[str,str]) -> list[DefinitionInfo]` — BFS-traverses the AST, matching nodes against `definition_dict` (node-type→extraction-strategy), returning all definitions sorted by `start_line`

**Key structures:** `definition_dict` maps AST node type strings to child-type strings or sentinel strings (`__assignment__`, `__variable_declarator__`, etc.) for deep extraction dispatch.
