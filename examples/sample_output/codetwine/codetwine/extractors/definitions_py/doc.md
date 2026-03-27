# Design Document: codetwine/extractors/definitions.py

## Overview & Purpose

## Overview & Purpose

### 1. Module Summary

Extracts named definition information (functions, classes, variables, types, etc.) from a tree-sitter AST and returns them as a sorted list of `DefinitionInfo` objects, enabling callers to map symbol names to their source file locations and line ranges.

### 2. When to Use This Module

- **Building a symbol-to-file index** (`import_to_path.py`): Call `extract_definitions(root_node, definition_dict)` to iterate over all top-level definitions in a parsed file and register each `defn.name` in a symbol map.
- **Generating per-file definition metadata** (`file_analyzer.py`): Call `extract_definitions(root_node, definition_dict)` to produce a list of definitions with `start_line`, `end_line`, and source context for structured file analysis output.
- **Resolving symbol names exported by a target file** (`extractors/usage_analysis.py`): Call `extract_definitions(target_root, target_def_dict)` to collect all definition names from a target file in order to determine which symbols it exposes.

### 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | `name: str`, `type: str`, `start_line: int`, `end_line: int` | dataclass | Holds the name, AST node type, and 1-based start/end line numbers of a single definition. |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | Traverses the AST via BFS and returns all discovered definitions sorted by start line in ascending order. |

### 4. Design Decisions

- **Language-agnostic via `definition_dict`**: The extraction logic is fully driven by a caller-supplied mapping of AST node types to name-extraction strategies. This separates language-specific knowledge (what counts as a definition and where its name lives) from the traversal algorithm, allowing the same module to serve Python, JavaScript/TypeScript, and C/C++ without branching on language.

- **Sentinel values for deep name extraction**: When a definition's name is not a direct child of the definition node, `definition_dict` values use a `"__sentinel__"` format string (e.g., `"__assignment__"`, `"__function_declarator__"`) to dispatch to dedicated extraction functions rather than forcing all patterns through a single generic traversal.

- **BFS with fallback descent**: When name extraction fails for a matched node type (e.g., a C/C++ forward declaration lacking an `init_declarator`), the node's children are enqueued rather than discarded. This allows inner nodes such as `function_declarator` to be discovered and matched on a subsequent BFS iteration without requiring callers to pre-enumerate all possible nesting structures.

- **Container definition pass-through**: Nodes of type `namespace_definition` are both recorded as definitions and have their children enqueued, allowing nested definitions (classes, functions) inside a namespace to be discovered in the same BFS pass.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-level Constant

### `_INCLUDE_GUARD_RE`

| Item | Detail |
|---|---|
| Type | `re.Pattern` |
| Pattern | Matches C/C++ include guard macro names (e.g., `MY_HEADER_H`, `_FOO_HPP_`) |

**Responsibility:** Pre-compiled regex used to filter out `#define` directives that serve as include guards rather than meaningful constants.

---

## Data Classes

### `DefinitionInfo`

A dataclass holding metadata about a single extracted definition.

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The identifier name of the definition (function, class, variable, type, etc.) |
| `type` | `str` | The AST node type string (e.g., `"function_definition"`, `"expression_statement"`) |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

**Responsibility:** Acts as a plain data container for communicating definition metadata from extractors to callers such as `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py`.

**When to use:** Consumed by callers that iterate over the result of `extract_definitions` to map symbol names to file paths or build definition metadata tables.

---

## Public Functions

### `extract_definitions`

```python
def extract_definitions(
    root_node: Node,
    definition_dict: dict[str, str],
) -> list[DefinitionInfo]
```

**`definition_dict`:** A mapping from AST node type string to a name-extraction specifier. The specifier is either a direct child node type (e.g., `"identifier"`) or a sentinel string in `__sentinel__` format (e.g., `"__assignment__"`) that triggers a dedicated deep-extraction function.

**Responsibility:** Traverses the entire AST via BFS and collects all definitions matching the language-specific `definition_dict`, returning them sorted by start line.

**When to use:** Called by `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py` after parsing a source file to enumerate all top-level and nested definitions.

**Design decisions:**

- **BFS instead of DFS:** Ensures definitions are discovered in document order before descending; also allows selective child expansion (container nodes like `namespace_definition` continue traversal after being recorded, while non-container definitions do not).
- **Fallback behavior on name-extraction failure:** Rather than silently dropping a node, child nodes are enqueued for continued search. This handles cases like C/C++ forward declarations where the actual function name lives inside a nested `function_declarator`.
- **Include guard filtering:** `preproc_def` nodes whose extracted name matches `_INCLUDE_GUARD_RE` are discarded and their children are re-enqueued instead of being recorded.
- **Destructured assignment handling:** When standard name extraction returns `None` and the node matches a destructuring pattern, multiple `DefinitionInfo` entries are created—one per extracted name—all sharing the same node's line range.
- **`decorated_definition` separation:** Decorated definitions are routed to a dedicated parser rather than the generic path to preserve the full decorator-inclusive line range.
- **`_CONTAINER_DEFINITION_TYPES`:** A locally defined set (`{"namespace_definition"}`) of node types whose children are always enqueued after the node itself is recorded.

**Constraints & edge cases:**

- `root_node` must be the root of a valid tree-sitter parse tree for the target language.
- `definition_dict` must use sentinel strings that are recognized by `_extract_name`; unrecognized sentinels fall through to direct-child search with no match.
- A `decorated_definition` that contains no recognized inner definition node produces no output.
- Destructuring extraction is only supported for `__assignment__` (Python) and `__variable_declarator__` (JS/TS) sentinel types.

---

## Private Functions

### `_parse_decorated_definition`

```python
def _parse_decorated_definition(
    node: Node,
    definition_dict: dict[str, str],
) -> DefinitionInfo | None
```

**Responsibility:** Extracts a `DefinitionInfo` from a `decorated_definition` node by locating the inner definition node and delegating name extraction, then overrides the line range to span the full decorator extent.

**When to use:** Called exclusively by `extract_definitions` when the BFS encounters a `decorated_definition` node type.

**Design decisions:** The `start_line` and `end_line` on the returned `DefinitionInfo` are overwritten to reflect the outer decorated node's range, so callers see the complete decorated block rather than just the inner function/class.

**Constraints & edge cases:** Returns `None` if no recognized definition node exists among the direct children of the `decorated_definition`. Only the last matching child is used as `inner_node`; multiple definitions within a single decorator block would result in only the last one being recorded.

---

### `_parse_definition_node`

```python
def _parse_definition_node(
    node: Node,
    name_node_type: str,
) -> DefinitionInfo | None
```

**Responsibility:** Converts a single definition AST node into a `DefinitionInfo` by delegating name extraction to `_extract_name` and building the result with 1-based line numbers.

**When to use:** Called by both `extract_definitions` (for standard definition nodes) and `_parse_decorated_definition` (for the inner node of a decorated definition).

**Constraints & edge cases:** Returns `None` when `_extract_name` cannot locate the name, signaling the caller to attempt alternative extraction strategies.

---

### `_extract_name`

```python
def _extract_name(node: Node, name_type: str) -> str | None
```

**Responsibility:** Dispatches to the appropriate dedicated extraction function based on the sentinel value of `name_type`, or performs a direct child search for standard node type names.

**When to use:** Called by `_parse_definition_node` as the single entry point for all name-extraction logic.

**Design decisions:** Sentinel values are identified by their `__sentinel__` naming convention. The dispatch table covers four dedicated extractors:

| Sentinel | Target Language | Dedicated Function |
|---|---|---|
| `__assignment__` | Python | `_extract_assignment_name` |
| `__variable_declarator__` | JS/TS | `_extract_variable_declarator_name` |
| `__init_declarator__` | C/C++ | `_extract_init_declarator_name` |
| `__function_declarator__` | C/C++ | `_extract_function_declarator_name` |

**Constraints & edge cases:** For standard patterns, only direct children are searched; deeper nesting requires a sentinel. Returns `None` if no matching child is found.

---

### `_extract_assignment_name`

```python
def _extract_assignment_name(node: Node) -> str | None
```

**Responsibility:** Extracts the variable name from a Python top-level simple assignment (`expression_statement > assignment > identifier`).

**When to use:** Invoked by `_extract_name` when `name_type == "__assignment__"` and the node is an `expression_statement`.

**Constraints & edge cases:**
- Returns `None` if the `expression_statement` does not contain an `assignment` (e.g., a standalone function call).
- Returns `None` if the left-hand side is not a plain `identifier` (e.g., attribute assignment `obj.attr = 1` or subscript assignment).
- Tuple/list destructuring (`X, Y = 1, 2`) is handled separately by `_extract_destructured_names`.

---

### `_extract_variable_declarator_name`

```python
def _extract_variable_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the variable name from a JS/TS `lexical_declaration` or `variable_declaration` by locating the `variable_declarator` child and reading its `name` field.

**When to use:** Invoked by `_extract_name` when `name_type == "__variable_declarator__"`.

**Constraints & edge cases:**
- Returns `None` if no `variable_declarator` child exists.
- Returns `None` if the declarator's `name` field is absent.
- Destructured patterns (`object_pattern`, `array_pattern`) on the `name` field cause this function to return `None`; `_extract_destructured_names` handles those cases.

---

### `_extract_function_declarator_name`

```python
def _extract_function_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts the function name from a C/C++ `function_definition` node where the name is nested inside a `function_declarator` rather than being a direct child.

**When to use:** Invoked by `_extract_name` when `name_type == "__function_declarator__"`.

**Design decisions:** Handles two sub-cases for the `function_declarator`'s declarator field:
- Plain `identifier`: returns the text directly.
- `qualified_identifier` (C++ class method, e.g., `Shape::get_name`): iterates children and returns the last `identifier` found, yielding only the method name portion.

**Constraints & edge cases:**
- Returns `None` if the `declarator` field of `function_definition` is absent or is not a `function_declarator`.
- Returns `None` if neither `identifier` nor `qualified_identifier` is found as the inner declarator type.

---

### `_extract_init_declarator_name`

```python
def _extract_init_declarator_name(node: Node) -> str | None
```

**Responsibility:** Extracts a variable name from a C/C++ `declaration` node that uses an `init_declarator` (i.e., a declaration with an initializer).

**When to use:** Invoked by `_extract_name` when `name_type == "__init_declarator__"`.

**Constraints & edge cases:**
- Returns `None` for forward declarations (e.g., `void freeFunction();`) that do not have an `init_declarator`; this triggers the BFS fallback in `extract_definitions` to discover the nested `function_declarator` instead.
- Returns `None` if the `declarator` field of `init_declarator` is not an `identifier`.

---

### `_extract_destructured_names`

```python
def _extract_destructured_names(node: Node, name_type: str) -> list[str]
```

**Responsibility:** Detects and extracts multiple variable names from destructuring assignments when standard single-name extraction fails.

**When to use:** Called by `extract_definitions` after `_parse_definition_node` returns `None`, to check whether the node represents a destructuring pattern before falling back to child-node BFS.

**Design decisions:** Covers two sentinel cases with different AST structures:

| Sentinel | Language | Pattern |
|---|---|---|
| `__assignment__` | Python | `expression_statement > assignment > pattern_list > identifier*` |
| `__variable_declarator__` | JS/TS | `lexical_declaration > variable_declarator > object_pattern / array_pattern` |

**Constraints & edge cases:**
- Returns an empty list for all sentinel values other than the two above.
- For JS/TS, only the first `variable_declarator` child with a recognized pattern name field is processed.
- Nested JS/TS patterns are handled by delegating to `_collect_identifiers_from_pattern`.

---

### `_collect_identifiers_from_pattern`

```python
def _collect_identifiers_from_pattern(pattern_node: Node) -> list[str]
```

**Responsibility:** Recursively collects all variable names from an `object_pattern` or `array_pattern` node, including nested destructuring.

**When to use:** Called by `_extract_destructured_names` when a JS/TS destructuring pattern is identified.

**Design decisions:** Handles four child node types within a pattern:

| Child Type | Handling |
|---|---|
| `identifier` | Appended directly |
| `shorthand_property_identifier_pattern` | Appended directly (e.g., `{ a, b }`) |
| `object_pattern` / `array_pattern` | Recursed into |
| `pair_pattern` | Extracts `value` field; recurses if value is itself a pattern |

**Constraints & edge cases:**
- Only the `value` field of `pair_pattern` is recorded (the local binding name), not the key.
- Nodes of types not listed above are silently ignored.
- Recursion depth is bounded by the nesting depth of the destructuring pattern in the source.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports (`re`, `collections.deque`, `dataclasses.dataclass`, `tree_sitter.Node`) are either standard library or third-party packages, which are excluded from this description.

---

## Dependents (modules that import this file)

Three project-internal modules depend on this file, each consuming `extract_definitions` and the `DefinitionInfo` data class it returns.

- **`codetwine/import_to_path.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to iterate over all top-level definitions in a parsed source file and register each definition's name into a symbol-to-file mapping, enabling symbol resolution across the project.

- **`codetwine/file_analyzer.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to enumerate definitions in a parsed source file and build structured records containing each definition's name, type, start/end line numbers, and source context text.

- **`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/definitions_py/definitions.py` : Uses `extract_definitions` to collect all definition names exported by a target file, supporting usage analysis that determines which symbols a target module exposes.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions_py/definitions.py`
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions_py/definitions.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions_py/definitions.py`

`definitions.py` itself imports no project-internal modules, so it sits at the leaf of the internal dependency graph. All data flow goes inward toward this module; it does not call back into any of its dependents.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|-------|------|-------------|
| `root_node` | `tree_sitter.Node` | The root AST node of a parsed source file, produced by an external parser (e.g. `parse_file`) |
| `definition_dict` | `dict[str, str]` | A per-language configuration map from AST node type strings to name-extraction strategy strings |

The `definition_dict` keys are AST node type strings (e.g. `"function_definition"`, `"expression_statement"`). Values are either a direct child node type string (e.g. `"identifier"`) or a sentinel string (e.g. `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`) that signals a deeper, language-specific extraction path.

---

## 2. Transformation Overview

```
root_node  ──►  BFS traversal  ──►  node classification  ──►  name extraction  ──►  DefinitionInfo list  ──►  sorted output
```

**Stage 1 — BFS Traversal**  
Starting from `root_node`, nodes are visited breadth-first via a `deque`. At each step, the current node's type is tested against `definition_dict`.

**Stage 2 — Node Classification**  
Each visited node falls into one of four categories:
- `decorated_definition`: delegated to `_parse_decorated_definition`, which locates the inner definition node and then follows the standard path.
- A type present in `definition_dict` (non-decorated): dispatched to `_parse_definition_node`.
- A type not present in `definition_dict`: its children are enqueued for further traversal.
- `preproc_def` nodes whose extracted name matches `_INCLUDE_GUARD_RE` are discarded; their children are still enqueued.

**Stage 3 — Name Extraction**  
`_parse_definition_node` calls `_extract_name`, which dispatches based on the sentinel value:

| Sentinel | Extraction path |
|----------|----------------|
| `__assignment__` | `expression_statement → assignment → left identifier` |
| `__variable_declarator__` | `lexical_declaration / variable_declaration → variable_declarator → name identifier` |
| `__init_declarator__` | `declaration → init_declarator → declarator identifier` |
| `__function_declarator__` | `function_definition → function_declarator → declarator identifier` (or last identifier in `qualified_identifier`) |
| (standard string) | Direct child of the node whose type matches the string |

**Stage 4 — Fallback Paths**  
If name extraction returns `None`, `_extract_destructured_names` is tried, which collects multiple identifiers from destructuring patterns (`pattern_list`, `object_pattern`, `array_pattern`). If that also yields nothing, the node's children are enqueued so BFS can descend into nested declarations (e.g. forward declarations in C/C++).

Container nodes (currently `namespace_definition`) always enqueue their children even after a successful extraction, allowing nested definitions inside them to be found.

**Stage 5 — Sorting**  
The accumulated `definition_list` is sorted by `start_line` in ascending order before being returned.

---

## 3. Outputs

| Output | Type | Description |
|--------|------|-------------|
| Return value | `list[DefinitionInfo]` | All discovered definitions sorted by start line (1-based), with no side effects or file writes |

Callers (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`) consume the list by iterating over `DefinitionInfo` fields — primarily `name`, `start_line`, and `end_line`.

---

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The identifier name of the definition (function name, class name, variable name, etc.) |
| `type` | `str` | The AST node type string that produced this definition (e.g. `"function_definition"`) |
| `start_line` | `int` | 1-based line number where the definition begins |
| `end_line` | `int` | 1-based line number where the definition ends |

### `definition_dict` (input dict)

| Key | Value | Purpose |
|-----|-------|---------|
| AST node type string | Child node type string | Signals that the name is a direct child of the matching type |
| AST node type string | `"__assignment__"` | Signals Python assignment extraction path |
| AST node type string | `"__variable_declarator__"` | Signals JS/TS variable declarator extraction path |
| AST node type string | `"__init_declarator__"` | Signals C/C++ init declarator extraction path |
| AST node type string | `"__function_declarator__"` | Signals C/C++ function declarator extraction path |

### Internal working structures

| Structure | Type | Purpose |
|-----------|------|---------|
| `node_queue` | `deque[Node]` | BFS frontier; holds AST nodes awaiting classification |
| `definition_list` | `list[DefinitionInfo]` | Accumulates all successfully extracted `DefinitionInfo` objects before final sorting |
| `_CONTAINER_DEFINITION_TYPES` | `set[str]` | Node types (`"namespace_definition"`) whose children are always enqueued even after a successful extraction |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach throughout. No exceptions are raised or caught anywhere in the code. Instead, every extraction function returns `None` (or an empty list) when it cannot produce a valid result, and callers treat those sentinel returns as signals to either skip the node, try an alternative extraction path, or continue BFS traversal into child nodes. The overall effect is that unrecognizable or malformed AST nodes are silently bypassed while well-formed nodes continue to be collected normally.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| No name found in direct children | A definition node has no child matching the expected `name_type` | `_extract_name` returns `None`; caller falls back to destructuring extraction, then to BFS child traversal | Yes | Node is skipped at the current level; children are enqueued for further search |
| Forward declaration (C/C++) | A `declaration` node has no `init_declarator` (e.g. `void freeFunction();`) | `_extract_init_declarator_name` returns `None`; BFS descends into children to find a nested `function_declarator` | Yes | No definition recorded at declaration level; inner declarator may be captured on the next BFS pass |
| Non-assignment expression statement (Python) | `expression_statement` wraps something other than `assignment` (e.g. `print("hello")`) | `_extract_assignment_name` returns `None`; destructuring check also fails; BFS continues into children | Yes | Node produces no definition; processing continues normally |
| Non-identifier left-hand side (Python) | Assignment LHS is not an `identifier` (e.g. `obj.attr = 1`) | `_extract_assignment_name` returns `None` after LHS type check fails | Yes | Node is skipped silently |
| Include-guard `#define` directive (C/C++) | `preproc_def` name matches `_INCLUDE_GUARD_RE` | Definition is discarded after extraction; children are still enqueued | Yes | Guard macro is excluded from results; traversal continues |
| Missing `decorated_definition` inner node | A `decorated_definition` contains no recognized inner definition child | `_parse_decorated_definition` returns `None` | Yes | No definition recorded for that decorated node |
| Empty or structurally unexpected node | Node has no children or lacks expected fields (`child_by_field_name` returns `None`) | Each extractor guards with explicit `None` / emptiness checks and returns `None` or `[]` | Yes | Node is skipped; BFS continues with siblings and children |
| Destructuring pattern not recognized | Destructuring extraction finds no `pattern_list`, `object_pattern`, or `array_pattern` | `_extract_destructured_names` returns `[]`; BFS falls back to child traversal | Yes | Node produces no definitions; traversal continues |

---

## 3. Design Notes

**Return-value-based error signaling:** The entire error handling contract is expressed through return values (`None` and empty lists) rather than exceptions. This keeps the extraction pipeline uniform and avoids any need for exception handling in the callers (`import_to_path.py`, `file_analyzer.py`, `usage_analysis.py`), all of which simply iterate over the returned list without guarding against exceptions.

**BFS fallback as recovery mechanism:** The decision to re-enqueue child nodes whenever name extraction fails is deliberate. It allows the extractor to handle deeply or variably nested AST structures (e.g., C/C++ declarators) without requiring an exhaustive upfront enumeration of every possible nesting shape. Failed extraction at one level automatically promotes child nodes for another attempt.

**No logging or error reporting:** Failures are absorbed silently. There is no logging, warning output, or error accumulation. This is consistent with the role of the module as a pure AST-to-data transformer used inside a larger pipeline; error reporting policy is delegated entirely to callers.

**Scope-limited filtering (include guards):** The one case where a successfully extracted definition is actively discarded—the include-guard `#define` pattern—is handled with an explicit post-extraction filter rather than a pre-extraction guard, keeping the extraction logic uniform while still preventing noise from polluting the results.

## Summary

**`codetwine/extractors/definitions.py`** extracts named definitions from a tree-sitter AST and returns them as sorted `DefinitionInfo` objects.

**Public interface:**
- `DefinitionInfo` (dataclass): `name:str`, `type:str`, `start_line:int`, `end_line:int`
- `extract_definitions(root_node:Node, definition_dict:dict[str,str]) -> list[DefinitionInfo]`

**Key data structures:**
- `definition_dict`: maps AST node type strings to name-extraction strategies (direct child type or sentinel like `__assignment__`, `__function_declarator__`)
- Returns `list[DefinitionInfo]` sorted by `start_line`
