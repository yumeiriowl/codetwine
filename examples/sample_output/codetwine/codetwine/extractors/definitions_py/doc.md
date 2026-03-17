# Design Document: codetwine/extractors/definitions.py

## Overview & Purpose

# Overview & Purpose

## Role Within the Project

This file implements the **AST-based definition extraction engine** used across the project to identify named definitions (functions, classes, variables, types, etc.) within source files parsed by `tree-sitter`. It exists as a separate module because definition extraction is a non-trivial, multi-language concern that must be reused by at least three distinct consumers: `import_to_path.py` (symbol-to-file mapping), `file_analyzer.py` (definition metadata for analysis output), and `usage_analysis.py` (collecting definition names from target files). Centralising this logic avoids duplication and provides a single, testable extraction surface.

The file performs a **BFS traversal of a tree-sitter AST**, matching nodes against a caller-supplied `definition_dict` that encodes per-language extraction rules. Name extraction is dispatched through a two-path strategy: direct child lookup for standard node types, and dedicated extractor functions for nodes where the name is nested deeper in the AST. It also handles special cases such as decorated definitions, destructuring assignments, C/C++ `#include` guard `#define` filtering, and container definitions (e.g. `namespace_definition`) that may hold nested definitions.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass instance | Data container holding metadata for a single extracted definition. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | BFS-traverses the AST and returns all discovered definitions sorted by start line. |

All other functions (`_parse_decorated_definition`, `_parse_definition_node`, `_extract_name`, `_extract_assignment_name`, `_extract_variable_declarator_name`, `_extract_function_declarator_name`, `_extract_init_declarator_name`, `_extract_destructured_names`, `_collect_identifiers_from_pattern`) are module-private helpers.

---

## Design Decisions

- **Data-driven dispatch via `definition_dict`**: The extraction logic is parameterised by a caller-supplied dictionary mapping AST node types to name-extraction hints. This separates language-specific knowledge (which lives in per-language settings) from the traversal and extraction mechanics (which live here), making the extractor reusable across languages without modification.

- **Sentinel value convention (`__name__`)**: When the definition name is not a direct child of the definition node, the `definition_dict` value is a sentinel string surrounded by double underscores (e.g. `__assignment__`, `__function_declarator__`). `_extract_name` uses this convention to dispatch to a dedicated extractor, avoiding the need for language-specific subclasses or conditional chains outside the dispatcher.

- **BFS with fallback descent**: When name extraction fails for a matched definition node, child nodes are enqueued rather than discarding the branch. This allows detection of definitions nested inside other matched node types (e.g. a `function_declarator` nested inside a C/C++ `declaration` that lacks an `init_declarator`).

- **Container definition pass-through**: A hard-coded set (`_CONTAINER_DEFINITION_TYPES`, currently `{"namespace_definition"}`) causes children of successfully recorded definitions to be enqueued, enabling detection of definitions nested inside namespaces without treating the namespace itself as a leaf.

- **`#include` guard filtering**: After a `preproc_def` node is successfully parsed, its name is checked against `_INCLUDE_GUARD_RE`. Matches are silently dropped and their children enqueued, preventing include-guard macros from appearing as definitions.

## Definition Design Specifications

# Definition Design Specifications

---

## `DefinitionInfo`

A plain data container representing a single extracted definition. Holds the definition name, its AST node type string, and the 1-based inclusive start and end line numbers within the source file. Used as the unit of output throughout the extraction pipeline.

---

## `extract_definitions(root_node, definition_dict) -> list[DefinitionInfo]`

**Arguments:**
- `root_node: Node` — The tree-sitter AST root node for an entire source file.
- `definition_dict: dict[str, str]` — Maps AST node type strings to name-extraction strategies (either a direct child node type or a `__sentinel__` string).

**Returns:** A list of `DefinitionInfo` objects sorted by `start_line` in ascending order.

**Responsibility:** Entry point for definition extraction. Performs a BFS traversal of the AST, classifying each visited node as a definition, a container, or a node requiring further descent, and assembles the complete list of definitions in a file.

**Design decisions:**
- BFS (rather than recursive DFS) is used so that traversal depth and queue management are explicit and non-recursive, avoiding stack overflow on deeply nested ASTs.
- `namespace_definition` is treated as a container type: it is recorded as a definition *and* its children are enqueued for further traversal, since namespaces may contain nested function and class definitions.
- When `_parse_definition_node` returns `None`, the fallback path first attempts destructuring extraction (`_extract_destructured_names`) before falling back to enqueueing children. This ordering ensures patterns like `X, Y = 1, 2` or `const { a, b } = obj` are captured without unnecessary child traversal.
- `preproc_def` nodes whose names match `_INCLUDE_GUARD_RE` are silently skipped; their children are still enqueued to avoid missing definitions inside guarded blocks.
- `decorated_definition` is handled by a dedicated branch that delegates to `_parse_decorated_definition`, which adjusts the line range to cover the decorator lines.

**Edge cases / constraints:**
- `definition_dict` must be non-empty and consistent with the grammar of the language being parsed; incorrect entries cause silent extraction failures (returns `None`), not errors.
- A node type may appear in `definition_dict` but still fail name extraction (e.g. forward declarations in C/C++); in that case BFS descent continues into children.
- The returned list may be empty if no matching nodes are found.

---

## `_parse_decorated_definition(node, definition_dict) -> DefinitionInfo | None`

**Arguments:**
- `node: Node` — A `decorated_definition` AST node.
- `definition_dict: dict[str, str]` — Per-language definition node settings.

**Returns:** A `DefinitionInfo` whose line range spans from the decorator to the end of the inner definition, or `None` if no recognized inner definition is found.

**Responsibility:** Handles the Python-specific `decorated_definition` wrapper node by locating the inner function or class node and delegating name extraction to `_parse_definition_node`, then correcting the line range to include the decorator.

**Edge cases / constraints:**
- If the `decorated_definition` contains no child whose type is in `definition_dict`, returns `None`.
- The inner node must not itself be a `decorated_definition` (stacked decorators produce nested `decorated_definition` nodes, which are excluded from the inner-node search; the outer BFS handles them correctly through re-enqueueing).
- Line range correction is applied only when `_parse_definition_node` succeeds.

---

## `_parse_definition_node(node, name_node_type) -> DefinitionInfo | None`

**Arguments:**
- `node: Node` — Any AST node whose type is a key in `definition_dict`.
- `name_node_type: str` — A child node type name or a `__sentinel__` string indicating how to extract the definition name.

**Returns:** A `DefinitionInfo` with 1-based line numbers, or `None` if the name cannot be extracted.

**Responsibility:** Thin wrapper that combines name extraction (via `_extract_name`) with `DefinitionInfo` construction, converting tree-sitter's 0-based line indices to 1-based.

---

## `_extract_name(node, name_type) -> str | None`

**Arguments:**
- `node: Node` — The definition node from which to extract the name.
- `name_type: str` — Either a standard child node type (e.g. `"identifier"`) or a `__sentinel__` value (e.g. `"__assignment__"`).

**Returns:** The definition name string, or `None` if extraction fails.

**Responsibility:** Central dispatcher that routes name extraction to the appropriate dedicated function for sentinel values, or performs a direct child scan for standard node type names.

**Design decisions:** The sentinel convention (`__name__` delimiters) allows `definition_dict` to remain a flat string-valued mapping while still expressing arbitrarily deep extraction paths without adding a separate configuration structure.

**Edge cases / constraints:**
- For the standard (non-sentinel) path, only direct children are scanned; grandchildren are not searched.
- If multiple direct children have the same type, the first match is returned.

---

## `_extract_assignment_name(node) -> str | None`

**Arguments:**
- `node: Node` — An `expression_statement` node.

**Returns:** The left-hand-side variable name string, or `None` if the statement is not a simple assignment to an identifier.

**Responsibility:** Handles Python top-level variable assignments (`X = ...`) where the name is two levels deep (expression_statement → assignment → left identifier) rather than a direct child.

**Edge cases / constraints:**
- Returns `None` for attribute assignments (`obj.attr = 1`), augmented assignments, and any `expression_statement` whose first child is not an `assignment` node.
- Returns `None` for destructuring assignments (`X, Y = 1, 2`); those are handled by `_extract_destructured_names`.

---

## `_extract_variable_declarator_name(node) -> str | None`

**Arguments:**
- `node: Node` — A `lexical_declaration` or `variable_declaration` node.

**Returns:** The declared variable name string, or `None` if extraction fails.

**Responsibility:** Handles JS/TS variable declarations where the name is inside a `variable_declarator` child, accessible via the `name` field.

**Edge cases / constraints:**
- Returns only the first `variable_declarator`'s name. Multiple declarators in a single declaration (e.g. `const a = 1, b = 2`) yield only the first name; remaining names are not extracted by this function.
- Returns `None` when the `name` field of `variable_declarator` is a destructuring pattern (`object_pattern` / `array_pattern`); those are handled by `_extract_destructured_names`.

---

## `_extract_function_declarator_name(node) -> str | None`

**Arguments:**
- `node: Node` — A `function_definition` node (C/C++).

**Returns:** The function name string, or `None` if no `function_declarator` is found under the `declarator` field.

**Responsibility:** Handles C/C++ function definitions where the function name is not a direct child but is nested inside a `function_declarator`, and additionally handles C++ qualified method names (`Shape::get_name`) by extracting the last identifier segment.

**Edge cases / constraints:**
- Returns `None` if the `declarator` field is absent or is not a `function_declarator` (e.g. pointer-to-function declarators are not handled).
- For `qualified_identifier` declarators, returns the last `identifier` child; if no `identifier` child exists within the qualified identifier, returns `None`.

---

## `_extract_init_declarator_name(node) -> str | None`

**Arguments:**
- `node: Node` — A C/C++ `declaration` node.

**Returns:** The variable name string, or `None` if no `init_declarator` is present.

**Responsibility:** Handles C/C++ initialized variable declarations (`int X = 3`) where the name is inside an `init_declarator`. Deliberately returns `None` for forward declarations (which lack an `init_declarator`), allowing the BFS fallback to extract the function name from the nested `function_declarator`.

**Edge cases / constraints:**
- Returns `None` for any `declaration` whose `declarator` field is not an `init_declarator` (covers forward declarations, uninitialized declarations, etc.).
- The inner `declarator` field of `init_declarator` must be an `identifier`; pointer declarators and array declarators are not handled.

---

## `_extract_destructured_names(node, name_type) -> list[str]`

**Arguments:**
- `node: Node` — A definition node (e.g. `expression_statement`, `lexical_declaration`).
- `name_type: str` — The sentinel value from `definition_dict` that determined this code path.

**Returns:** A list of variable name strings extracted from the destructuring pattern, or an empty list if the node is not a recognized destructuring form.

**Responsibility:** Handles multi-variable bindings that a single-name extraction function cannot represent: Python tuple unpacking (`X, Y = 1, 2`) and JS/TS object/array destructuring (`const { a, b } = obj`).

**Edge cases / constraints:**
- Only `__assignment__` and `__variable_declarator__` sentinel values are handled; all other `name_type` values return an empty list.
- For `__assignment__`, the left-hand side must be a `pattern_list`; any other compound left-hand side returns an empty list.
- For `__variable_declarator__`, only the first `variable_declarator` child whose `name` field is a pattern is processed.

---

## `_collect_identifiers_from_pattern(pattern_node) -> list[str]`

**Arguments:**
- `pattern_node: Node` — An `object_pattern` or `array_pattern` node.

**Returns:** A flat list of all variable name strings bound by the pattern, including names from nested sub-patterns.

**Responsibility:** Recursively traverses destructuring patterns to collect all locally-bound variable names, supporting `shorthand_property_identifier_pattern`, nested `object_pattern`/`array_pattern`, and `pair_pattern` (where only the value side, not the key, is a new binding).

**Edge cases / constraints:**
- For `pair_pattern`, only the `value` field is inspected; the key is not a new binding and is ignored.
- Handles one level of nesting in `pair_pattern` values: if the value is itself an `object_pattern` or `array_pattern`, recursion continues; other complex value types are ignored.
- Returns an empty list if the pattern node has no recognized bindable children.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports (`re`, `collections.deque`, `dataclasses.dataclass`, `tree_sitter.Node`) are either standard library or third-party framework components, which are excluded from this description.

---

### Dependents (what uses this file)

Three project-internal files depend on this file, all consuming the `extract_definitions` function and the `DefinitionInfo` dataclass it returns.

- **`codetwine/import_to_path.py`**: Uses `extract_definitions` to populate a symbol-to-file mapping. It parses each source file into an AST, then iterates over the returned `DefinitionInfo` list to register each definition name alongside its containing file path.

- **`codetwine/file_analyzer.py`**: Uses `extract_definitions` to build a structured list of definition metadata per file. It reads `start_line`, `end_line`, and the surrounding source lines from each `DefinitionInfo` to produce a context-enriched definition record for further analysis.

- **`codetwine/extractors/usage_analysis.py`**: Uses `extract_definitions` to enumerate the names defined in a target file. The collected names are used during import/usage analysis to determine which symbols a target file exposes.

**Direction of dependency**: Unidirectional. All three dependents import from this file; this file does not reference any of them.

## Data Flow

# Data Flow

## Input

| Input | Type | Source |
|---|---|---|
| `root_node` | `tree_sitter.Node` | Parsed AST root node of a source file |
| `definition_dict` | `dict[str, str]` | Per-language configuration mapping AST node types to name extraction strategies |

### `definition_dict` Value Conventions

| Value Format | Meaning | Example |
|---|---|---|
| Standard node type string | Name found in a direct child of that type | `"identifier"` |
| `__sentinel__` string | Name is nested deeper; dispatched to a dedicated extractor | `"__assignment__"`, `"__variable_declarator__"`, `"__function_declarator__"`, `"__init_declarator__"` |

---

## Main Transformation Flow

```
root_node (AST)
    │
    ▼
BFS traversal (deque)
    │
    ├─ node.type == "decorated_definition"
    │       └─ _parse_decorated_definition()
    │               └─ finds inner definition child
    │                       └─ _parse_definition_node()  ──► DefinitionInfo (start_line adjusted to decorator)
    │
    ├─ node.type in definition_dict
    │       └─ _parse_definition_node()
    │               └─ _extract_name()
    │                       ├─ sentinel dispatch ──► dedicated extractor (_extract_assignment_name, etc.)
    │                       └─ standard: search direct children by type
    │                               │
    │                     success ──► DefinitionInfo appended
    │                     failure ──► _extract_destructured_names()
    │                                       ├─ success ──► multiple DefinitionInfo appended
    │                                       └─ failure ──► node.children added back to queue (BFS continues)
    │
    └─ node.type not in definition_dict
            └─ node.children added to queue (dig deeper)
    │
    ▼
sorted(definition_list, key=start_line)
    │
    ▼
list[DefinitionInfo]
```

**Special cases during traversal:**
- `namespace_definition` nodes: after recording as a definition, children are also enqueued (container traversal continues)
- `preproc_def` nodes matching `_INCLUDE_GUARD_RE`: discarded, children enqueued instead

---

## Output

| Output | Type | Consumers |
|---|---|---|
| `list[DefinitionInfo]` | Sorted ascending by `start_line` | `import_to_path.py`, `file_analyzer.py`, `usage_analysis.py` |

---

## Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Extracted definition name (function, class, variable, etc.) |
| `type` | `str` | AST node type of the definition (e.g. `"function_definition"`) |
| `start_line` | `int` | 1-based start line in the source file |
| `end_line` | `int` | 1-based end line in the source file |

> Line numbers are converted from tree-sitter's 0-based `start_point[0]` / `end_point[0]` to 1-based values during construction. For decorated definitions, `start_line` is overwritten to the decorator's start position.

### BFS Queue (`deque`)

Holds `tree_sitter.Node` objects awaiting processing. Nodes are added back when:
- A node is not a definition node (dig deeper)
- Name extraction fails and no destructured names are found
- A container-type definition node's children need further scanning

---

## Name Extraction Dispatch Summary

| Sentinel Value | Target Language | AST Path |
|---|---|---|
| `__assignment__` | Python | `expression_statement` → `assignment` → `left` (identifier) |
| `__variable_declarator__` | JS/TS | `lexical_declaration` / `variable_declaration` → `variable_declarator` → `name` |
| `__function_declarator__` | C/C++ | `function_definition` → `declarator` (function_declarator) → `declarator` (identifier or qualified_identifier) |
| `__init_declarator__` | C/C++ | `declaration` → `declarator` (init_declarator) → `declarator` (identifier) |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** policy. No exceptions are raised at any point during AST traversal or name extraction. When a definition cannot be successfully extracted, the logic silently falls back to alternative strategies (such as descending into child nodes via BFS) or returns `None` / an empty collection, allowing the overall extraction process to continue without interruption. Callers receive a best-effort result rather than a hard failure.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Definition node found but name extraction returns `None` | Falls back to destructured-name extraction; if that also yields nothing, child nodes are enqueued for continued BFS traversal | The node is silently skipped as a top-level definition; nested definitions within it may still be discovered |
| Destructuring pattern extraction returns an empty list | Child nodes are enqueued for BFS continuation | No definition is recorded for the node; traversal proceeds deeper |
| `decorated_definition` contains no recognizable inner definition node | `_parse_decorated_definition` returns `None`; no entry is appended | The decorated construct is omitted from results without error |
| A `preproc_def` node matches the include-guard regex | The node is discarded and its children are enqueued | Include-guard macros are excluded from the definition list |
| A field lookup (`child_by_field_name`) returns `None` | Each extractor function returns `None` immediately | The calling site treats the node as unresolvable and applies the BFS fallback |
| An AST node's child list is empty | Guard checks (`if not node.children`) cause the extractor to return `None` or an empty list | No crash; the node produces no definition entry |
| `name_type` is an unrecognized sentinel or standard type with no matching child | `_extract_name` returns `None` (standard loop exhausted with no match) | Name extraction silently fails; BFS fallback is triggered |

---

## Design Considerations

The entire strategy is built around the assumption that the input AST may be **incomplete or structurally unexpected** (e.g., forward declarations in C/C++, partial parses). Rather than asserting invariants or raising exceptions, every extraction function contracts to return `None` or `[]` on any unexpected structure. This makes the module safe to use across multiple languages and grammar versions without requiring callers to add defensive error handling. The BFS fallback mechanism is the structural embodiment of this policy: a failed extraction at one level automatically delegates responsibility to deeper nodes rather than abandoning the search entirely.

## Summary

**`codetwine/extractors/definitions.py`**

AST-based definition extraction engine using BFS traversal of tree-sitter parse trees. Parameterised by a caller-supplied `definition_dict` mapping node types to name-extraction strategies (direct child lookup or `__sentinel__` dispatch to dedicated extractors). Handles decorated definitions, destructuring assignments, C/C++ include-guard filtering, and namespace container traversal.

**Public interface:**
- `DefinitionInfo` — dataclass holding `name`, `type`, `start_line`, `end_line` (1-based)
- `extract_definitions(root_node, definition_dict) → list[DefinitionInfo]` — returns definitions sorted by start line

Consumed by `import_to_path.py`, `file_analyzer.py`, and `usage_analysis.py`.
