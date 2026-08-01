# Design Document: codetwine/extractors/definitions.py

# Overview & Purpose

## Purpose and Responsibilities

This module provides language-agnostic AST traversal logic for extracting **definitions** (functions, classes, variables, types, etc.) from source code parsed by `tree-sitter`. It acts as the core extraction engine used across the codebase's static analysis tooling (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`) to identify named entities and their locations within a file.

The file exists as a separate module because it isolates the language-agnostic BFS traversal algorithm from language-specific configuration (which lives in `definition_dict` settings passed in from callers). This separation allows the same extraction logic to support multiple languages (Python, JS/TS, Java, Kotlin, C/C++) by mapping AST node types to name-extraction strategies, rather than duplicating traversal code per language.

Key responsibilities:
- Perform BFS traversal over a `tree_sitter.Node` AST, matching node types against a `definition_dict` configuration.
- Extract the definition name using either a "standard pattern" (direct child lookup) or a "sentinel pattern" (dedicated helper functions for names nested deeper in the tree).
- Handle special cases: decorated definitions (Python `@decorator`), C/C++ forward declarations (via BFS fallback into children), `#include` guard filtering, and destructuring assignments (Python tuple unpacking, JS/TS object/array patterns).
- Continue traversing into "container" definitions (namespaces, classes, structs, interfaces, enums) so that nested definitions (methods, fields) are also captured.
- Return results sorted by ascending start line number.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `DefinitionInfo` (dataclass) | `name: str`, `type: str`, `start_line: int`, `end_line: int` | — | Holds extracted metadata for a single definition (name, AST node type, line range). |
| `extract_definitions` | `root_node: Node`, `definition_dict: dict[str, str]` | `list[DefinitionInfo]` | Main entry point: BFS-traverses the AST, extracts all definitions per `definition_dict`, and returns them sorted by line number. |

## Internal Helper Functions (module-private, non-public but structurally important)

| Name | Responsibility |
|---|---|
| `_parse_decorated_definition` | Unwraps a `decorated_definition` node to find and parse the inner function/class definition, adjusting line range to include the decorator. |
| `_parse_definition_node` | Converts a matched definition node into a `DefinitionInfo`, delegating name extraction to `_extract_name`. |
| `_extract_name` | Dispatches to sentinel-specific extractors (e.g. `__assignment__`, `__function_declarator__`) or falls back to direct-child lookup by node type. |
| `_extract_assignment_name` | Extracts variable name from Python `expression_statement > assignment` (identifier LHS only). |
| `_extract_variable_declarator_name` | Extracts name from JS/TS/Java `variable_declarator` (first declared name if multiple). |
| `_extract_function_declarator_name` | Extracts function name from C/C++ `function_definition`, handling qualified identifiers (`Class::method`). |
| `_extract_declarator_name` | Extracts function name from a standalone `function_declarator` (identifier or field_identifier). |
| `_extract_kotlin_property_name` | Extracts property name from Kotlin `property_declaration > variable_declaration`. |
| `_extract_init_declarator_name` | Extracts variable name from C/C++ `declaration > init_declarator`; returns `None` for forward declarations. |
| `_extract_destructured_names` | Extracts multiple names from Python tuple unpacking or JS/TS destructuring patterns when standard extraction yields none. |
| `_collect_identifiers_from_pattern` | Recursively collects identifiers from nested JS/TS `object_pattern`/`array_pattern` structures. |

## Design Decisions

- **Sentinel-value dispatch pattern**: `definition_dict` values are either plain node-type strings (standard pattern, resolved via direct child search) or `__sentinel__`-formatted strings (special pattern, resolved via dedicated functions). This allows a single configuration schema to express both simple and deeply-nested name locations without complicating the dictionary structure.
- **BFS with deque**: Traversal uses `collections.deque` for FIFO breadth-first traversal, ensuring definitions are naturally discovered in a top-down, level-order manner and enabling straightforward fallback (re-queueing children) when name extraction fails.
- **Fallback-on-failure traversal**: If a node matches a definition type but its name cannot be extracted (e.g., C/C++ forward declarations), its children are still enqueued for further BFS exploration rather than discarding the subtree, so nested constructs (e.g. `function_declarator`) can still be found.
- **Container-type continuation**: A dedicated `_CONTAINER_DEFINITION_TYPES` set explicitly whitelists node types (namespaces, classes, structs, interfaces, enums) whose children must still be traversed after being recorded, supporting nested member extraction.
- **Include-guard filtering**: A dedicated regex (`_INCLUDE_GUARD_RE`) excludes C/C++ `#define` include guards from being treated as definitions, while still allowing their children to be traversed.

# Definition Design Specifications

## `_INCLUDE_GUARD_RE`

Module-level compiled regex used to identify C/C++ `#include` guard macro names (e.g. `FOO_H_`, `__BAR_HPP_INCLUDED__`). It exists so that `extract_definitions` can filter out `preproc_def` nodes that are merely include guards rather than meaningful definitions, since these guards are extremely common in headers and would otherwise pollute the definition list with noise.

## `DefinitionInfo`

A dataclass representing a single extracted definition, with fields `name` (str, the identifier of the function/class/variable/type), `type` (str, the AST node type such as `"function_definition"` or `"expression_statement"`), `start_line` (int, 1-based start line), and `end_line` (int, 1-based end line). It exists as the uniform, language-agnostic output unit returned by all extraction logic in this module, and is directly consumed by dependents (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`) to build symbol tables and context snippets. Line numbers are stored as 1-based to match human-readable source line numbering, even though tree-sitter internally uses 0-based points.

## `extract_definitions`

Top-level entry point that walks a tree-sitter AST rooted at `root_node` using breadth-first search and returns a list of `DefinitionInfo` sorted by ascending start line. `definition_dict` maps AST node types to either a direct child node type name (standard extraction) or a `"__sentinel__"`-style string that routes to a dedicated extraction function for names nested deeper in the tree.

Design intent: this function centralizes multi-language definition discovery (Python, C/C++, Java, Kotlin, JS/TS) behind one generic dictionary-driven dispatch mechanism, avoiding per-language traversal code.

Key design decisions:
- BFS (via `deque`) is used instead of DFS so that top-level definitions are naturally discovered before deeply nested ones, and traversal can be selectively continued or pruned per node.
- `decorated_definition` nodes are handled before the generic `definition_dict` lookup because their name is not a direct property of the node itself but of an inner wrapped node.
- Container-type node types (namespace, class, struct, interface, enum, object declarations) are recorded as definitions *and* still have their children enqueued, since they can contain nested definitions (methods, fields, inner classes) that must also be extracted.
- If name extraction fails for a recognized definition type, the code falls back to checking for destructuring patterns (`_extract_destructured_names`); if that also yields nothing, the node's children are enqueued so that nested definitions (e.g. a forward declaration's inner `function_declarator`) are not lost.
- `preproc_def` nodes whose name matches the include-guard regex are excluded from results but their children are still enqueued to continue traversal.

Edge cases/constraints: assumes `root_node` and `definition_dict` are consistent with the language of the parsed file; a node type absent from `definition_dict` is transparently skipped (its children just get enqueued).

## `_parse_decorated_definition`

Resolves the actual definition (function or class) wrapped by a `decorated_definition` node (used for decorator syntax such as Python's `@property`) and produces a `DefinitionInfo` whose line range spans the entire decorated block, including the decorator lines.

Design intent: decorators attach to an inner node whose own line range excludes the decorator, but callers generally want the full definition span reported, so this function extracts the name via the inner node while overwriting the reported line range with the outer node's range.

Returns `None` if no recognized inner definition node is found among the decorated node's children, or if name extraction from that inner node fails.

## `_parse_definition_node`

Converts a definition AST node into a `DefinitionInfo` by delegating name extraction to `_extract_name` and converting 0-based tree-sitter line numbers to 1-based output line numbers. Returns `None` when no name can be extracted, signaling to the caller that this node should not be treated as a valid definition (allowing fallback behavior such as BFS descent or destructuring extraction).

## `_extract_name`

Dispatcher that routes name extraction either to one of several per-language dedicated extraction functions (triggered by sentinel values like `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`, `"__declarator_name__"`, `"__kotlin_property__"`) or, for standard node type strings, performs a simple direct-child type match.

Design intent: this indirection allows `definition_dict` (external configuration) to describe simple cases with a plain child type name while still supporting complex, deeply-nested AST shapes (e.g. C function declarators, JS variable declarators) through named sentinel dispatch, without hardcoding per-language logic into the BFS traversal itself.

## `_extract_assignment_name`

Extracts the variable name from a Python top-level assignment wrapped in an `expression_statement`, returning the left-hand identifier's text. Returns `None` when the statement is not an assignment (e.g. a bare function call) or when the left-hand side is not a simple `identifier` (e.g. an attribute assignment like `obj.attr = 1`), which correctly excludes non-declarative statements from being reported as definitions.

## `_extract_variable_declarator_name`

Extracts the declared variable name from a JS/TS `lexical_declaration`/`variable_declaration` or a Java `field_declaration` by locating its `variable_declarator` child and reading the `name` field. When multiple variables are declared in one statement (e.g. `int a, b;`), only the first declarator's name is returned, reflecting a deliberate simplification that treats the statement as a single definition entry.

## `_extract_function_declarator_name`

Extracts the function name from a C/C++ `function_definition` by descending into its `declarator` field (`function_declarator`) and then into that node's own `declarator` field to reach the actual name node. Handles three name-node shapes: plain `identifier` (free function), `field_identifier` (inline class/struct method), and `qualified_identifier` (out-of-class method implementation such as `Shape::get_name`), in which case only the last `identifier` segment (the method name, not the class qualifier) is returned. Returns `None` if the declarator is missing or not a `function_declarator`, which allows BFS fallback to still discover a nested declarator in unusual cases.

## `_extract_declarator_name`

Extracts a function/method name directly from a `function_declarator` node by reading its `declarator` field, accepting either `identifier` (free functions, constructors) or `field_identifier` (class member declarations). Returns `None` for other declarator shapes such as pointer declarators or operator overload declarators, which are intentionally not treated as simple named definitions here.

## `_extract_kotlin_property_name`

Extracts the property name from a Kotlin `property_declaration` by finding its `variable_declaration` child and returning the first `identifier` found within it. Returns `None` for destructuring declarations (e.g. `val (a, b) = pair`), since those do not contain a plain `identifier` child in the expected position and are not handled by this function.

## `_extract_init_declarator_name`

Extracts the variable name from a C/C++ `declaration` node by reading its `declarator` field, requiring it to be an `init_declarator`, and then reading that node's own `declarator` field for the identifier. Returns `None` for forward declarations (which lack an `init_declarator`), an intentional signal that lets the caller's BFS fallback extract a function name instead from the nested `function_declarator`.

## `_extract_destructured_names`

Handles cases where standard single-name extraction has already failed, checking whether the node represents a destructuring assignment and, if so, returning all bound variable names as a list. Supports two patterns: Python tuple-unpacking assignment (`X, Y = 1, 2`, via `pattern_list`) and JS/TS destructuring declarations (`const {a, b} = obj` / `const [a, b] = arr`, via `object_pattern`/`array_pattern`). Returns an empty list for any node/name_type combination that isn't a recognized destructuring shape, signaling the caller to fall back to BFS descent instead.

## `_collect_identifiers_from_pattern`

Recursively walks a JS/TS `object_pattern` or `array_pattern` to collect all locally-bound variable names, handling plain `identifier` children, shorthand properties (`shorthand_property_identifier_pattern`), nested patterns (recursing into `object_pattern`/`array_pattern`), and renamed destructured keys (`pair_pattern`, where only the `value` field—the local binding name—is collected, not the original key). This recursive design is necessary because destructuring patterns can nest arbitrarily deeply (e.g. `const { a, inner: { b } } = obj`), and only local binding names (not source object keys) should be reported as definitions.

# Dependency Description

### Dependencies (what this file uses)

This file has no project-internal dependencies. It relies only on standard library modules (`re`, `collections.deque`, `dataclasses.dataclass`) and the external `tree_sitter` library for AST node traversal, which are excluded per the instructions. All logic for extracting definitions (functions, classes, variables, etc.) from AST nodes is self-contained within this file.

### Dependents (what uses this file)

Several project-internal modules depend on this file, forming a unidirectional dependency where this file is consumed by others without depending back on them:

- **codetwine/file_analyzer.py** uses `extract_definitions` to obtain a list of definitions from a parsed AST root node, using the returned line ranges to slice source content for context extraction.
- **codetwine/import_to_path.py** uses both `extract_definitions` and `DefinitionInfo`. It calls `extract_definitions` to retrieve all definitions in a file and register their names in a symbol-to-file mapping, and uses the `DefinitionInfo` type to filter and select only top-level (non-nested) definitions.
- **codetwine/extractors/usage_analysis.py** uses `extract_definitions` to gather definition names from a target file's AST, supporting cross-file usage analysis.

In all cases, the dependency direction is one-way: these files call into `definitions.py` to perform AST-based definition extraction, while this file remains independent of them.

# Data Flow

## Input

| Source | Format | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | AST root of a parsed source file (provided by callers such as `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py` via `parse_file`) |
| `definition_dict` | `dict[str, str]` | Language-specific mapping: AST node type → name extraction rule (either a direct child node type like `"identifier"`, or a `"__sentinel__"`-style key referring to a dedicated extractor function) |

## Main Transformation Flow

```
root_node
   │
   ▼
BFS queue (deque) ── pops node
   │
   ├─ node.type == "decorated_definition"?
   │      → _parse_decorated_definition()
   │            → finds inner definition node
   │            → _parse_definition_node() (name + line range)
   │            → adjusts line range to include decorator
   │
   ├─ node.type in definition_dict?
   │      → _parse_definition_node()
   │            → _extract_name()
   │                  ├─ sentinel type → dedicated extractor
   │                  │     (_extract_assignment_name,
   │                  │      _extract_variable_declarator_name,
   │                  │      _extract_init_declarator_name,
   │                  │      _extract_function_declarator_name,
   │                  │      _extract_declarator_name,
   │                  │      _extract_kotlin_property_name)
   │                  └─ standard type → search direct children
   │
   │      success → DefinitionInfo appended
   │                (container types re-enqueue children for nested defs)
   │      name == include guard macro → skip, enqueue children
   │      failure → try _extract_destructured_names()
   │                (splits one node into multiple DefinitionInfo,
   │                 for Python tuple assignment / JS destructuring)
   │      still failure → enqueue children (fallback deeper search)
   │
   └─ not a definition node → enqueue children
   │
   ▼
definition_list (unsorted, grows during BFS)
   │
   ▼ sorted by start_line
   ▼
list[DefinitionInfo]  (returned)
```

Helper extractors (`_extract_*`) each know a specific nested AST shape (e.g. `expression_statement > assignment > identifier`, `function_declarator > declarator > identifier/qualified_identifier`, `object_pattern`/`array_pattern` destructuring) and reduce a `Node` subtree to a `str | None` name, or in the destructuring case, `_collect_identifiers_from_pattern` recursively walks nested patterns to build `list[str]`.

## Output

| Consumer | Usage |
|---|---|
| `file_analyzer.py` | Iterates `extract_definitions(...)` results to build definition summaries with `start_line`, `end_line`, and extracted source `context` text |
| `import_to_path.py` | Uses `extract_definitions` results, filters via `_select_top_level_definitions`, and registers `defn.name` into a symbol-to-file map |
| `usage_analysis.py` | Collects `defn.name` from `extract_definitions` results into a `names` list for cross-file usage checks |

## Data Structures

### `DefinitionInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | Extracted identifier of the definition (function/class/variable/type name) |
| `type` | `str` | AST node type of the definition (e.g. `"function_definition"`) |
| `start_line` | `int` | 1-based start line of the definition (includes decorator if present) |
| `end_line` | `int` | 1-based end line of the definition |

### `definition_dict` (input map)

| Key | Value | Meaning |
|---|---|---|
| AST node type (e.g. `"function_definition"`) | `"identifier"` / other child node type | Standard rule: name is a direct child of this type |
| AST node type | `"__sentinel__"`-style string | Special rule: name is nested deeper, resolved via a dedicated extractor function |

### `_CONTAINER_DEFINITION_TYPES` (set)

A fixed set of node types (`namespace_definition`, `class_definition`, `class_declaration`, `class_specifier`, `struct_specifier`, `interface_declaration`, `enum_declaration`, `object_declaration`) whose children continue to be traversed after being recorded, since they may contain nested definitions (methods, fields, inner classes).

### BFS queue (`deque[Node]`)

Working structure that drives traversal order; nodes are appended when a container definition is recorded, when name extraction fails, or when a node is not itself a definition type.

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** strategy rather than fail-fast. There are no explicit exception handling blocks (`try`/`except`); instead, error conditions are handled through **defensive checks and `None`/empty-collection returns**. When name extraction fails at any level, the module does not raise exceptions or abort processing—it falls back to alternative strategies (BFS descent into child nodes) or simply omits the problematic node from the result set. This design reflects the reality that AST shapes vary across languages and grammar versions, and malformed or unexpected node structures (e.g., forward declarations, destructuring patterns, decorators without inner definitions) should not halt extraction for the entire file.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Name extraction fails (`_extract_name` returns `None`) on a standard definition node | Node is not recorded as a definition; its children are pushed onto the BFS queue for further inspection | Nested definitions (e.g., a `function_declarator` inside a forward-declared `declaration`) can still be discovered later; the outer node itself is silently skipped |
| Definition node matches destructuring pattern but standard extraction fails | Falls back to `_extract_destructured_names`, which returns a list of names (possibly empty) | If destructuring names are found, multiple `DefinitionInfo` entries are created; if not, falls through to child-node BFS descent |
| Missing/absent expected child or field (e.g., `child_by_field_name` returns `None`, unexpected node type on LHS of assignment, non-identifier destructuring target) | Each dedicated extractor (`_extract_assignment_name`, `_extract_variable_declarator_name`, `_extract_init_declarator_name`, `_extract_function_declarator_name`, `_extract_declarator_name`, `_extract_kotlin_property_name`) returns `None` at the first unmet precondition | Caller treats it as "extraction failed" and proceeds with the fallback BFS logic; no partial or incorrect `DefinitionInfo` is produced |
| `decorated_definition` node with no recognizable inner definition | `_parse_decorated_definition` returns `None` | The decorated node is dropped entirely (not added to the queue for further descent), since no inner name could be identified |
| `#include` guard `#define` matched by `_INCLUDE_GUARD_RE` | Definition is discarded from the result list, but its children are still enqueued for BFS traversal | Prevents spurious top-level "definitions" from include guards while preserving traversal into any nested content |
| Unknown/irrelevant node types (not in `definition_dict`, not a container type) | Node's children are unconditionally enqueued for further BFS traversal | Traversal continues transparently through arbitrary AST structure without special-casing every node type |
| Nested pattern structures in destructuring (`_collect_identifiers_from_pattern`) with unrecognized child types | Such children are simply skipped in the loop (no explicit `else` branch) | Unsupported/unknown pattern element types are silently ignored, potentially resulting in fewer extracted names for complex or unusual destructuring, without raising an error |

## Design Considerations

- All extraction failures propagate as `None` (single value) or an empty list (multiple values) rather than exceptions, making the control flow predictable and allowing calling code (BFS loop) to uniformly react by descending further into the tree.
- The design explicitly acknowledges forward declarations and other "incomplete" definition-like nodes as an expected condition (documented in `_extract_init_declarator_name` and `extract_definitions`), not as an error state — the BFS fallback exists specifically to recover the "real" definition nested deeper in such cases.
- Dependents (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`) rely on `extract_definitions` never raising for structural/name-extraction issues; they only guard against `defn.name` being falsy before using it, consistent with this module's graceful-degradation contract.
- No error handling is provided for malformed `root_node` input (e.g., `None`) or for `definition_dict` missing expected sentinel keys — the module assumes valid AST input and correctly formed configuration, placing responsibility for those guarantees on callers.

# Summary

**Summary:** `definitions.py` provides language-agnostic BFS traversal over tree-sitter ASTs to extract code definitions (functions, classes, variables) via a configurable `definition_dict` mapping node types to name-extraction rules (direct child or sentinel-based helpers). Public API: `DefinitionInfo` dataclass (name, type, start_line, end_line) and `extract_definitions(root_node, definition_dict) -> list[DefinitionInfo]`, sorted by line. Handles decorators, forward declarations, include guards, and destructuring via graceful fallback (no exceptions). Used by `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`; no internal dependencies.
