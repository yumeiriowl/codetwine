# Design Document: codetwine/extractors/definitions.py

# Overview & Purpose

## 1. Module Summary
Extract structural definitions (functions, classes, variables, types, etc.) from a tree-sitter AST by traversing it in breadth-first order and return them as line-ordered `DefinitionInfo` records.

## 2. When to Use This Module
- **Building a code-navigation index of a file's top-level symbols**: call `extract_definitions(root_node, definition_dict)` to obtain the list of `DefinitionInfo` for all definitions found in a parsed source file, given a language-specific `definition_dict` mapping AST node types to name-extraction strategies.
- **Resolving where a symbol (function/class/variable) is defined for import mapping**: call `extract_definitions` to get definitions per file, then filter/consume them (e.g. picking top-level ones) to register symbol-to-file associations.
- **Attaching source context/snippets to a definition**: use the `start_line`/`end_line` fields of each returned `DefinitionInfo` to slice the corresponding lines from file content.
- **Collecting candidate identifier names from another file for usage analysis**: call `extract_definitions` against a target file's root node and target-language `definition_dict` to gather the `name` field of each definition as a name to search for.
- **Representing a single extracted definition in a language-agnostic way**: use the `DefinitionInfo` dataclass wherever a typed record of (name, type, start_line, end_line) is needed, instead of a raw dict.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `DefinitionInfo` | dataclass fields: `name (str)`, `type (str)`, `start_line (int)`, `end_line (int)` | — | Holds name, AST node type, and 1-based start/end line numbers of a single extracted definition. |
| `extract_definitions` | `root_node (Node)`, `definition_dict (dict[str, str])` | `list[DefinitionInfo]` | BFS-traverses the AST rooted at `root_node`, matches nodes against `definition_dict`, extracts names (including decorated definitions, container definitions, destructured assignments, and include-guard filtering), and returns all found definitions sorted by ascending start line. |

## 4. Design Decisions
- **BFS with a work queue instead of recursion**: traversal uses a `deque` so that name-extraction failures (e.g. forward declarations) can push child nodes back into the queue and continue searching deeper without recursive call overhead or stack-depth concerns.
- **Sentinel-value dispatch for name extraction**: `definition_dict` values are either a literal child node type (standard pattern, resolved by direct child scan) or a `"__xxx__"` sentinel string that routes to a dedicated per-language extraction function (`_extract_name` dispatcher), allowing complex/nested name locations (e.g. C/C++ `function_declarator`, JS destructuring) to be handled without complicating the main traversal loop.
- **Container-type definitions continue traversal after being recorded**: node types like `namespace_definition`, `class_specifier`, etc. are both recorded as definitions and expanded into the queue, since they can nest further definitions (methods, nested classes) that must also be extracted.
- **Explicit exclusion of `#include` guard macros**: `preproc_def` nodes matching `_INCLUDE_GUARD_RE` are skipped as definitions (but their children are still queued), preventing include-guard macros from polluting the definition list.
- **Destructuring fallback**: when standard/sentinel name extraction returns no single name, `_extract_destructured_names` is tried to handle multi-name binding patterns (Python tuple unpacking, JS object/array destructuring) before falling back to descending into children.

# Definition Design Specifications

## Module-level constant: `_INCLUDE_GUARD_RE`

**Signature:** `_INCLUDE_GUARD_RE: re.Pattern` — compiled regex pattern.

**Responsibility:** Identifies C/C++ preprocessor macro names that follow the conventional `#include` guard naming pattern (e.g. `FOO_H_`, `_BAR_HPP_INCLUDED_`), so that such macros can be excluded from the list of extracted definitions.

**When to use:** Consulted internally by `extract_definitions` whenever a `preproc_def` node is encountered, to decide whether the macro should be filtered out.

**Design decisions:** The pattern anchors on a trailing `_H`, `_HPP`, or `_HXX` (optionally followed by `INCLUDED` and/or trailing underscores) combined with a leading optional underscore and uppercase-starting identifier — a heuristic rather than a semantic guarantee, since arbitrary macros could coincidentally match or fail to match this naming convention.

**Constraints & edge cases:** Only applies to nodes of type `preproc_def`; does not detect include guards implemented via `#pragma once` or non-conforming naming schemes.

---

## `DefinitionInfo` (dataclass)

**Signature:** `@dataclass class DefinitionInfo`

**Responsibility:** Represents a single extracted code definition (function, class, variable, type, etc.) in a language-agnostic, serializable form.

**When to use:** Instantiated by the extraction functions in this module every time a definition is successfully parsed from the AST; consumed by downstream modules (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`) to build symbol maps and contextual code snippets.

**Fields:**

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The identifier name of the definition (function/class/variable/type name). |
| `type` | `str` | The tree-sitter AST node type of the definition (e.g. `"function_definition"`, `"expression_statement"`). |
| `start_line` | `int` | 1-based line number where the definition starts. |
| `end_line` | `int` | 1-based line number where the definition ends. |

**Constraints & edge cases:** No validation is performed on field values (e.g. `start_line <= end_line` is not enforced); the object is a plain data holder mutated directly in `_parse_decorated_definition` to widen the line range to include a decorator.

---

## `extract_definitions(root_node: Node, definition_dict: dict[str, str]) -> list[DefinitionInfo]`

**Responsibility:** Traverses an entire AST and produces the ordered list of all definitions matching language-specific rules supplied via `definition_dict`.

**When to use:** Called once per parsed source file whenever a caller needs the set of top-level and nested definitions contained in that file (used by file analysis, import resolution, and usage analysis modules).

**Design decisions:**
- Uses breadth-first traversal (`deque`) rather than recursion, avoiding Python recursion-depth issues on deeply nested ASTs.
- `definition_dict` maps AST node type → either a direct child node type name ("standard pattern") or a `"__sentinel__"`-style string that triggers a dedicated extraction routine ("special pattern") for names nested more than one level deep.
- A fixed set `_CONTAINER_DEFINITION_TYPES` (namespaces, classes, structs, interfaces, enums, objects) is treated specially: even after being recorded as a definition, their children are still enqueued so nested members (methods, fields, nested classes) are also discovered.
- If a node matches `definition_dict` but name extraction fails, its children are still enqueued — this enables detecting nested constructs like a bare `function_declarator` inside an otherwise unnamed forward declaration.
- If name extraction fails but the node is a destructuring assignment, `_extract_destructured_names` is tried as a fallback before giving up and descending into children.
- `preproc_def` nodes whose name matches `_INCLUDE_GUARD_RE` are excluded from results but their children are still traversed.
- `decorated_definition` nodes are special-cased and always routed to `_parse_decorated_definition`, bypassing the standard/sentinel dispatch.
- Final result is sorted ascending by `start_line`, even though BFS order does not guarantee line order.

**Constraints & edge cases:**
- `definition_dict` must map exact tree-sitter node type strings to either a valid child node type or one of the recognized sentinel strings; unrecognized sentinels silently fall through to `None` in `_extract_name`.
- Assumes `node.children` and `child_by_field_name` behave per the `tree_sitter.Node` API; malformed or partial ASTs are not explicitly handled beyond returning `None`/empty results.
- If a `decorated_definition` type is absent from `definition_dict`, decorated definitions are not extracted at all (they fall into the generic "not a definition node" branch and only their children are traversed).

---

## `_parse_decorated_definition(node: Node, definition_dict: dict[str, str]) -> DefinitionInfo | None`

**Responsibility:** Extracts the inner function/class definition wrapped by a decorator (e.g. Python `@property`) and reports it with the line range extended to cover the decorator itself.

**When to use:** Invoked by `extract_definitions` whenever a `decorated_definition` node is encountered and `definition_dict` declares handling for it.

**Design decisions:** Iterates all children of the decorated definition (not just the last one) to find the first child whose type is a key in `definition_dict` (excluding `decorated_definition` itself), then delegates name extraction to `_parse_definition_node`; afterward it overwrites `start_line`/`end_line` on the resulting `DefinitionInfo` to span the full decorated block instead of just the inner definition.

**Constraints & edge cases:** Returns `None` if no matching inner definition node is found among the direct children, or if the inner definition's name cannot be extracted. Does not recursively handle stacked/multiple decorators beyond what tree-sitter represents as direct children.

---

## `_parse_definition_node(node: Node, name_node_type: str) -> DefinitionInfo | None`

**Responsibility:** Converts a matched definition node into a `DefinitionInfo` by extracting its name and converting 0-based tree-sitter line numbers to 1-based line numbers.

**When to use:** Called for every AST node whose type is a key in `definition_dict` (both directly from `extract_definitions` and indirectly via `_parse_decorated_definition` for the inner node).

**Constraints & edge cases:** Returns `None` when `_extract_name` cannot resolve a name (e.g. forward declarations, unsupported destructuring), signaling the caller to attempt other fallbacks.

---

## `_extract_name(node: Node, name_type: str) -> str | None`

**Responsibility:** Central dispatcher that routes name extraction either to one of several dedicated sentinel-handling functions or to a generic "search direct children for matching type" strategy.

**When to use:** Called exclusively by `_parse_definition_node` for every definition node needing name resolution.

**Design decisions:** Sentinel values are plain string constants (`"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`, `"__declarator_name__"`, `"__kotlin_property__"`) checked via equality rather than a data-driven dictionary dispatch table, keeping the mapping explicit and easy to trace per language construct.

**Constraints & edge cases:** For the standard (non-sentinel) pattern, only direct children are inspected — no recursion — so `name_type` values must correspond to an immediate child node type of `node`. Unknown non-sentinel `name_type` values that don't match any child simply return `None`.

---

## `_extract_assignment_name(node: Node) -> str | None`

**Responsibility:** Extracts a Python top-level variable name from an `expression_statement` wrapping a simple assignment (`X = value`).

**When to use:** Used for Python `expression_statement` nodes when `definition_dict` specifies the `"__assignment__"` sentinel.

**Constraints & edge cases:** Returns `None` if the statement's first child is not an `assignment` node, if the assignment has no `left` field, or if the left-hand side is not a plain `identifier` (e.g. attribute assignment `obj.attr = 1` or destructuring, which is instead handled by `_extract_destructured_names`). Only the first child of `expression_statement` is inspected.

---

## `_extract_variable_declarator_name(node: Node) -> str | None`

**Responsibility:** Extracts the declared variable/field name from JS/TS `lexical_declaration`/`variable_declaration` or Java `field_declaration` nodes.

**When to use:** Used when `definition_dict` maps a node type to the `"__variable_declarator__"` sentinel.

**Design decisions:** When multiple variables are declared in one statement (e.g. `let a, b;`), only the first `variable_declarator` encountered is used — subsequent declarators are ignored for naming purposes (though not for further processing elsewhere).

**Constraints & edge cases:** Returns `None` if no `variable_declarator` child exists or if it lacks a `name` field (e.g. destructuring patterns, handled separately via `_extract_destructured_names`).

---

## `_extract_function_declarator_name(node: Node) -> str | None`

**Responsibility:** Extracts a C/C++ function name from a `function_definition` node, including plain functions, class methods defined inline, and out-of-line qualified method implementations (`Shape::get_name`).

**When to use:** Used when `definition_dict` maps `function_definition` (C/C++) to the `"__function_declarator__"` sentinel.

**Design decisions:** Handles three distinct declarator name shapes: `identifier` (free function), `field_identifier` (inline class method), and `qualified_identifier` (out-of-line method definition) — for the latter, the *last* `identifier` child among the qualifier segments is taken as the actual method name (discarding namespace/class qualifiers).

**Constraints & edge cases:** Returns `None` if the `declarator` field is missing or not a `function_declarator`, if no nested `declarator` field exists, or if a `qualified_identifier` contains no `identifier` children.

---

## `_extract_declarator_name(node: Node) -> str | None`

**Responsibility:** Extracts a function/method name directly from a standalone `function_declarator` node (used for forward declarations discovered via BFS fallback).

**When to use:** Invoked when `definition_dict` maps `function_declarator` to `"__declarator_name__"`, typically reached after `extract_definitions`' BFS falls through from a parent node (e.g. an unnamed `declaration`) into its children.

**Constraints & edge cases:** Only recognizes `identifier` and `field_identifier` as valid name node types; returns `None` for other declarator shapes such as pointer declarators or operator overload declarators.

---

## `_extract_kotlin_property_name(node: Node) -> str | None`

**Responsibility:** Extracts the property name from a Kotlin `property_declaration` (`val`/`var`), for both top-level constants and class-body properties.

**When to use:** Used when `definition_dict` maps `property_declaration` to `"__kotlin_property__"`.

**Constraints & edge cases:** Returns `None` if no nested `variable_declaration` with an `identifier` child is found, notably for destructuring declarations such as `val (a, b) = pair` — no fallback destructuring handling exists for Kotlin in this module.

---

## `_extract_init_declarator_name(node: Node) -> str | None`

**Responsibility:** Extracts a variable/constant name from a C/C++ `declaration` node that includes an initializer (`int x = 3;`).

**When to use:** Used when `definition_dict` maps `declaration` to `"__init_declarator__"`.

**Design decisions:** Deliberately returns `None` (rather than attempting further extraction) for forward declarations lacking an `init_declarator`, relying on the caller's BFS fallback in `extract_definitions` to instead discover a nested `function_declarator` for prototype declarations.

**Constraints & edge cases:** Only recognizes `identifier` as the declarator's inner name type; does not handle pointer/array declarators nested inside `init_declarator`.

---

## `_extract_destructured_names(node: Node, name_type: str) -> list[str]`

**Responsibility:** Extracts multiple variable names from destructuring/multi-target assignment patterns that standard single-name extraction cannot handle.

**When to use:** Called as a fallback by `extract_definitions` only after `_parse_definition_node` (i.e. `_extract_name`) has failed to produce a single name for a node.

**Design decisions:** Supports exactly two sentinel cases:
- `"__assignment__"`: Python tuple-unpacking assignment (`X, Y = 1, 2`), collecting `identifier` children from a `pattern_list` on the left-hand side.
- `"__variable_declarator__"`: JS/TS destructuring (`const { a, b } = obj` / `const [a, b] = arr`), delegating to `_collect_identifiers_from_pattern` for the `object_pattern`/`array_pattern` found under the `variable_declarator`'s `name` field.

**Constraints & edge cases:** Returns an empty list (not `None`) for any other `name_type`, for non-assignment expression statements, or when the pattern isn't a recognized destructuring shape. Only inspects the first child of `expression_statement` for the Python case, mirroring `_extract_assignment_name`.

---

## `_collect_identifiers_from_pattern(pattern_node: Node) -> list[str]`

**Responsibility:** Recursively walks a JS/TS `object_pattern` or `array_pattern` to collect all bound variable names, including nested and renamed destructuring targets.

**When to use:** Called by `_extract_destructured_names` for JS/TS destructuring declarations, and recursively by itself for nested patterns (e.g. `const { a, inner: { b } } = obj`).

**Design decisions:**
- Handles four child shapes distinctly: plain `identifier` (array pattern elements), `shorthand_property_identifier_pattern` (object pattern shorthand keys like `{ a, b }`), nested `object_pattern`/`array_pattern` (recursion), and `pair_pattern` (renamed destructuring `{ key: localName }`), where only the `value` field (the local binding name) is captured, not the `key`.
- For `pair_pattern`, recurses further if the `value` is itself an `object_pattern`/`array_pattern`, supporting arbitrarily nested renamed destructuring.

**Constraints & edge cases:** Does not handle default value patterns or rest patterns (`...rest`) explicitly — such nodes are silently skipped since they don't match any of the four handled `child.type` cases (unless their internal structure happens to contain one of the recognized types as a direct child).

# Dependency Description

### Dependencies (modules this file imports)

This file has no dependencies on other project-internal modules. It only relies on standard library modules (`re`, `collections.deque`, `dataclasses.dataclass`) and the third-party `tree_sitter.Node` type, all of which are excluded from this description per the exclusion rules.

### Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to obtain a list of `DefinitionInfo` objects from a parsed AST root node, in order to build per-definition context blocks (extracting `start_line`, `end_line`, and surrounding source content) for file analysis output.

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to parse a file's AST and obtain its list of definitions, then uses the `DefinitionInfo` type (via `_select_top_level_definitions`) to filter for top-level definitions and register their names in a symbol-to-file mapping.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to retrieve the definitions present in a target file's AST, collecting their names for usage/import analysis purposes.

### Dependency Direction

All relationships are **unidirectional**: `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py` each depend on `definitions.py` by importing and invoking `extract_definitions` (and, in the case of `import_to_path.py`, also the `DefinitionInfo` data class). `definitions.py` itself does not import or reference any of these dependent modules, so there is no reverse dependency.

# Data Flow

## 1. Inputs

This module receives no file paths or raw text directly; it operates entirely on pre-parsed data supplied by callers:

- **`root_node` (`tree_sitter.Node`)** — The root of an already-parsed AST for a source file. Callers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`) obtain this from `parse_file(...)[0]` before invoking `extract_definitions`.
- **`definition_dict` (`dict[str, str]`)** — A per-language configuration mapping AST node type names (e.g. `"function_definition"`) to either:
  - a standard child node type name (e.g. `"identifier"`), or
  - a `"__sentinel__"`-style string (e.g. `"__assignment__"`, `"__variable_declarator__"`, `"__init_declarator__"`, `"__function_declarator__"`, `"__declarator_name__"`, `"__kotlin_property__"`) indicating a dedicated extraction function must be used.

Internally, individual extraction helper functions also consume `Node` objects (and their `.children`, `.child_by_field_name(...)`, `.type`, `.text` properties) as inputs — these are sub-trees of the original `root_node`.

## 2. Transformation Overview

1. **Queue initialization** — `root_node` is placed into a `deque` to drive a breadth-first traversal (BFS) of the AST.
2. **Node dispatch loop** — Each dequeued node is classified into one of three paths:
   - **Decorated definitions**: if `node.type == "decorated_definition"` and it's configured, delegate to `_parse_decorated_definition`, which locates the inner function/class node, extracts its name via `_parse_definition_node`, and rewrites the line range to cover the decorator plus the inner definition.
   - **Configured definition nodes**: if `node.type` is a key in `definition_dict`, `_parse_definition_node` is called to attempt name extraction via `_extract_name`.
     - **Success** → a `DefinitionInfo` is appended to the result list.
       - Special case: `preproc_def` nodes matching the include-guard regex are filtered out (not appended), but their children are still enqueued for continued traversal.
       - If the node type is a "container" type (namespace/class/struct/interface/enum/object declarations), its children are also enqueued so nested definitions (methods, nested classes) are discovered.
     - **Failure (name is `None`)** → fallback to `_extract_destructured_names`, which checks for Python `pattern_list` assignments or JS/TS object/array destructuring patterns and emits one `DefinitionInfo` per extracted name.
       - If no destructured names are found either, the node's children are enqueued so the search descends further (e.g. to find a nested `function_declarator` inside a forward declaration).
   - **Non-definition nodes**: children are simply enqueued to continue the search deeper into the tree.
3. **Name resolution dispatch** — `_extract_name` routes each definition node to either a direct-child scan (standard pattern) or one of several dedicated helpers (`_extract_assignment_name`, `_extract_variable_declarator_name`, `_extract_init_declarator_name`, `_extract_function_declarator_name`, `_extract_declarator_name`, `_extract_kotlin_property_name`), each of which walks a fixed, language-specific sub-structure (e.g. `assignment > left`, `function_declarator > declarator`, `qualified_identifier`'s last `identifier`) to pull out a name string.
4. **Destructuring collection** — For patterns that fail standard extraction, `_extract_destructured_names` inspects `pattern_list` (Python) or `variable_declarator > object_pattern/array_pattern` (JS/TS), and `_collect_identifiers_from_pattern` recursively walks nested object/array patterns and `pair_pattern`/`shorthand_property_identifier_pattern` nodes to build a flat list of names.
5. **Aggregation and sort** — All accumulated `DefinitionInfo` records (from direct definitions and destructured names) are collected into `definition_list`, then sorted ascending by `start_line` before being returned.

There is no async/parallel processing; the traversal is a single-threaded BFS with a work queue (`deque`), and results merge naturally into one flat list.

## 3. Outputs

- **Return value**: `list[DefinitionInfo]` — a flat, line-ordered list of all discovered definitions in the AST (top-level and nested/container-scoped), including expanded entries for destructured assignments.
- **No side effects**: the module does not write files, mutate global state, or perform I/O. All output is via the function's return value.

Downstream consumers use this output as follows:
- `file_analyzer.py` maps each `DefinitionInfo` into a dict with `end_line`, `context` (source lines slice), etc.
- `import_to_path.py` filters the list via `_select_top_level_definitions` (external function) and registers `defn.name` into a `symbol_to_file_map`.
- `usage_analysis.py` collects `defn.name` values into a plain list of symbol names for usage comparison.

## 4. Key Data Structures

### `DefinitionInfo` (dataclass)

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | The extracted identifier/name of the definition (function, class, variable, type, etc.). |
| `type` | `str` | The AST node type of the definition (e.g. `"function_definition"`, `"expression_statement"`). |
| `start_line` | `int` | 1-based line number where the definition (or its decorator, if any) starts. |
| `end_line` | `int` | 1-based line number where the definition ends. |

### `definition_dict` (input configuration dict)

| Field / Key | Type | Purpose |
|---|---|---|
| key: AST node type name | `str` | Identifies which AST node types represent definitions (e.g. `"function_definition"`, `"class_declaration"`). |
| value: child type or sentinel | `str` | Either a direct child node type to search for (standard pattern) or a `"__..__"` sentinel string selecting a dedicated extraction function for deeply nested names. |

### `_CONTAINER_DEFINITION_TYPES` (module-level constant set)

| Field / Key | Type | Purpose |
|---|---|---|
| set members | `str` (AST node type names) | Node types (e.g. `namespace_definition`, `class_specifier`, `struct_specifier`, `interface_declaration`, `enum_declaration`, `object_declaration`) whose children must still be traversed after being recorded as a definition, since they may contain nested definitions. |

### `node_queue` (internal BFS work queue)

| Field / Key | Type | Purpose |
|---|---|---|
| queue elements | `tree_sitter.Node` | AST nodes pending inspection during the BFS traversal; seeded with `root_node` and extended with children when descent is required. |

### `definition_list` (internal accumulator, becomes the return value)

| Field / Key | Type | Purpose |
|---|---|---|
| list elements | `DefinitionInfo` | Accumulates all definitions found (including expanded destructured names) before final sorting by `start_line`. |

# Error Handling

## 1. Overall Strategy

This module contains no `try`/`except` blocks and raises no explicit exceptions. Its error handling policy is entirely based on **graceful degradation via `None`/empty-result sentinels combined with BFS fallback traversal**. When a name cannot be extracted from an expected AST shape (e.g., a forward declaration, an unsupported destructuring form, or a missing child node), the responsible function returns `None` (or an empty list), and the caller responds not by failing but by descending into child nodes to keep searching for nested definitions. There is no logging, no retries in the sense of re-invoking a failed operation, and no process termination triggered from within this file — malformed or unexpected AST shapes are simply treated as "no definition found here, keep looking deeper."

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Name extraction failure (standard pattern) | `node.children` has no child whose `type` matches `name_node_type` (e.g., missing `identifier`) | `_extract_name` returns `None`; `_parse_definition_node` returns `None`; BFS enqueues the node's children to continue searching | Yes | Definition is not recorded at this node, but nested definitions may still be found via child traversal |
| Name extraction failure (sentinel pattern) | Sentinel-specific structure not matched, e.g. assignment LHS is not `identifier` (`obj.attr = 1`), no `init_declarator`/`function_declarator` present (forward declarations), no `variable_declaration` found (Kotlin destructuring) | Dedicated extractor (`_extract_assignment_name`, `_extract_init_declarator_name`, etc.) returns `None`; falls through to destructuring check, then to BFS child enqueue | Yes | No definition recorded for that node; nested declarators (e.g. `function_declarator`) can still be discovered on a later queue iteration |
| Destructuring pattern not recognized | `name_type` extraction failed and LHS/name node is not `pattern_list`, `object_pattern`, or `array_pattern` | `_extract_destructured_names` returns an empty list; caller falls back to enqueueing children | Yes | No names extracted from this statement; no crash |
| Decorated definition with no inner definition | `decorated_definition` node has no child whose `type` is present in `definition_dict` | `_parse_decorated_definition` returns `None`; node is simply skipped (no children enqueued in this branch) | Yes | Decorated construct produces no `DefinitionInfo`; any nested definitions inside it are not further explored via BFS in that branch |
| Missing `function_declarator`/`declarator` field | `child_by_field_name("declarator")` returns `None` or wrong type (e.g., non-function declaration) | Dedicated extractor returns `None` immediately | Yes | Treated as extraction failure; handled by the same fallback path as above |
| Qualified identifier with no plain identifier child | `qualified_identifier` (e.g., `Shape::get_name`) contains no `identifier` child | `last_id` remains `None` and is returned as-is | Yes | Function name resolves to `None`, is treated as extraction failure downstream |
| Include guard macro filtering | `preproc_def` node's name matches `_INCLUDE_GUARD_RE` (e.g., `FOO_H_INCLUDED`) | Definition is intentionally discarded; children are still enqueued via `continue` | Yes (by design, not an actual error) | Guard macro is excluded from results, but nested content is still scanned |
| Malformed / unexpected `root_node` (e.g., `None`, non-`Node` input) | Caller passes an invalid AST root | Not handled in this file; deque/attribute access would raise an unhandled exception (`AttributeError`/`TypeError`) that propagates to the caller | No | Function call fails; caller (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`) must handle or let it propagate |

## 3. Design Notes

- **BFS as a universal fallback**: The core design principle is that any failure to extract a name at one level of the AST is not treated as fatal — the BFS queue mechanism ensures the traversal simply continues one level deeper. This is deliberately used to handle cases like C/C++ forward declarations, where the outer `declaration` node lacks a name but an inner `function_declarator` may still contain one.
- **`None`/empty-list as the sole failure signal**: All parsing helper functions communicate failure exclusively through `None` return values (or empty lists for the destructuring helpers), never through exceptions. This keeps the control flow uniform across the many extraction functions and lets the BFS loop use simple conditional checks rather than exception handling.
- **No validation of input types**: The module assumes `root_node` is a valid `tree_sitter.Node` and that `definition_dict` is correctly structured per language. There is no defensive validation of these inputs; malformed inputs would surface as unhandled exceptions in caller code rather than being caught here.
- **Intentional exclusion is not an error**: The `#include` guard filtering (`_INCLUDE_GUARD_RE`) is a deliberate business-logic exclusion rather than an error condition, but it uses the same "skip and continue traversal" mechanism as genuine extraction failures, reflecting the uniform degrade-and-continue philosophy of the whole module.
- **No logging or diagnostics**: Because extraction failures are expected and common (e.g., non-definition statements, forward declarations, decorators without inner definitions), the module does not log these events; they are treated as normal branching rather than exceptional circumstances requiring diagnostic output.

# Summary

Extracts structural definitions (functions, classes, vars, etc.) from a tree-sitter AST via BFS, using a language-specific mapping of node types to name-extraction strategies. Public API: `DefinitionInfo` dataclass (name: str, type: str, start_line: int, end_line: int); `extract_definitions(root_node: Node, definition_dict: dict[str, str]) -> list[DefinitionInfo]`, sorted by start_line. No internal deps; used by file_analyzer.py, import_to_path.py, usage_analysis.py.
