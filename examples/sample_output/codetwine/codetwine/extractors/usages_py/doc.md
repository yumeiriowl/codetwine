# Design Document: codetwine/extractors/usages.py

# Overview & Purpose

## Role in the Project

`codetwine/extractors/usages.py` is responsible for locating where previously-detected symbols (e.g., imported names, classes, functions) are actually **used** within a source file's AST. It complements import/definition extraction modules by answering "where and how is this symbol referenced in the code?" This information is consumed by `codetwine/extractors/usage_analysis.py`, which builds a symbol-to-file mapping and uses this module to determine cross-file usage relationships and typed-variable aliasing.

The module is kept separate because usage detection requires language-agnostic AST traversal logic that must handle many syntactic variations (function calls, attribute access, identifiers, type references, qualified/namespaced identifiers, typed declarations) across multiple languages (Python, Java, Kotlin, C/C++) driven by configurable node-type sets (`USAGE_NODE_TYPES` in `config.py`), isolating this complexity from higher-level orchestration logic.

## Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `UsageInfo` (dataclass) | `name: str`, `line: int` | — | Represents a single usage occurrence (symbol name + 1-based line number). |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None = None` | `list[UsageInfo]` | DFS-traverses the AST to find calls, attribute accesses, identifiers, and type/namespace references matching `imported_names`; deduplicates and returns results. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Finds typed variable declarations (e.g., `Genre genre`) whose type is in `imported_names`, returning a variable-name → type-name mapping. |

### Internal Helper Functions (not part of the public API, but supporting logic)

| Name | Responsibility |
|---|---|
| `_deduplicate` | Removes redundant same-line entries (keeping more detailed names like `module.attr` over `module`) and duplicate `(name, line)` pairs. |
| `_is_function_part_of_call` | Determines if an attribute node is the callee part of a call node (to avoid double-counting). |
| `_parse_call_node` | Extracts usage info from the first child of a call node (identifier, attribute, or qualified identifier). |
| `_parse_attribute_node` | Extracts usage info from standalone `module.attr`-style attribute access. |
| `_parse_identifier_node` | Extracts usage info from plain identifiers, applying skip rules for declaration/import contexts. |
| `_extract_type_and_var` | Extracts type name and associated variable name(s) from a typed declaration node, handling Java/Kotlin/C/C++ AST shape differences. |

## Design Decisions

- **Iterative DFS with an explicit stack** (rather than recursion) is used in both `extract_usages` and `extract_typed_aliases` to traverse the AST, avoiding recursion depth issues on large files.
- **Configuration-driven behavior**: node type sets (`call_types`, `attribute_types`, `skip_parent_types`, `skip_parent_types_for_type_ref`, `skip_name_field_types`) are injected via `usage_node_types`, allowing the same traversal logic to support multiple languages without hardcoding syntax per language.
- **Separation of concerns via dedicated parser helpers** (`_parse_call_node`, `_parse_attribute_node`, `_parse_identifier_node`): each node-type category has its own extraction function, keeping the main traversal loop in `extract_usages` readable and focused on dispatch.
- **Post-processing deduplication (`_deduplicate`)** is applied as a final pass rather than during traversal, simplifying the traversal logic and centralizing the "prefer more specific match" rule (e.g., `module.attr` over `module`) in one place.
- **Graceful degradation**: `extract_usages` returns an empty list when `usage_node_types` is falsy, and `extract_typed_aliases` returns an empty dict when `typed_alias_parent_types` is empty, allowing languages without usage-tracking configuration to be safely skipped by callers.

# Definition Design Specifications

## `UsageInfo`

A dataclass representing a single detected usage of a tracked symbol, storing the symbol `name` (str) and the 1-based `line` (int) number where it appears.

Exists to provide a uniform, minimal record type shared by all extraction and deduplication logic in this module, decoupling downstream consumers from raw tree-sitter node details.

Line numbers are stored as 1-based (converted from tree-sitter's 0-based `start_point`) to match conventional editor/file line numbering used by callers.

## `extract_usages`

Takes `root_node` (tree-sitter `Node`, the AST root), `imported_names` (`set[str]`, symbols to track), and `usage_node_types` (`dict | None`, per-language node type configuration). Returns `list[UsageInfo]`, deduplicated usage records.

Serves as the main entry point for locating where imported/tracked symbols are referenced in source code, abstracting over language-specific AST shapes via the `usage_node_types` configuration so the same traversal logic works across languages.

Uses an explicit stack-based DFS rather than recursion, likely to avoid recursion-depth issues on large ASTs and to allow selectively skipping subtrees (e.g., pushing only `node.children` and `continue`-ing) when a parent type indicates the subtree should not be treated as ordinary identifiers.

Key design decisions:
- Node types are checked in a specific priority order (call types, then attribute types, then `qualified_identifier`, then type/namespace identifiers, then plain identifiers) so that composite constructs (calls, attribute chains, qualified names) are captured as a single higher-level usage instead of being fragmented into multiple lower-level identifier usages.
- `qualified_identifier` and type-reference nodes each have their own skip-parent logic (`skip_parent_types` vs. `skip_parent_types_for_type_ref`) because type references (e.g., in parameter/method declarations) should be tracked as usages even in contexts where plain identifiers should not be.
- Returns an empty list immediately when `usage_node_types` is falsy, allowing languages without usage-tracking configuration to be safely skipped by callers.
- Required keys (`call_types`, `attribute_types`, `skip_parent_types`) must be present in `usage_node_types`; optional keys default to empty sets or to `skip_parent_types`.

Edge case: when a `qualified_identifier` or type-reference node's parent is in the relevant skip set, its children are still pushed onto the stack for further traversal rather than being discarded entirely, so nested usages are not lost even when the outer node is skipped.

## `_deduplicate`

Takes `usage_list` (`list[UsageInfo]`, possibly containing duplicates or redundant entries). Returns a `list[UsageInfo]` sorted by ascending line number with duplicates and redundant shorter names removed.

Exists to reconcile multiple detections of the same logical usage (e.g., a plain identifier usage and a more specific attribute-access usage on the same line) into a single, most-informative entry per line.

Key design decision: grouping by line number and preferring the longest dotted name (`"module.attr"` over `"module"`) reflects the assumption that a more qualified name is a strict refinement of a shorter one appearing on the same line, so the shorter one is redundant rather than a distinct usage.

Constraint: deduplication logic (prefix matching) is applied per line only; identical `(name, line)` pairs across the whole list are removed via a separate seen-key check.

## `_is_function_part_of_call`

Takes `node` (`Node`, an attribute-type node) and `call_types` (`set[str]`). Returns `bool` indicating whether this attribute node is the callee expression of a call node (and thus should not be separately recorded as a standalone attribute usage).

Exists to prevent double-counting: when an attribute access like `module.func` is the function part of `module.func()`, the call-handling logic already records the usage, so the attribute node itself must be excluded.

Design decision: identifies the callee by comparing node identity (`child.id == node.id`) against the call node's first matching child (`identifier` or same attribute type), rather than relying on field names, to remain robust across languages whose grammars may not expose a named "function" field uniformly.

## `_parse_call_node`

Takes `node` (`Node`, a call-type node), `imported_names` (`set[str]`), and `attribute_types` (`set[str]`). Returns `UsageInfo | None` for the call's target symbol if it matches a tracked name.

Exists to extract the invoked symbol from call expressions across several call-target shapes: simple identifier calls, attribute-based calls (`module.func()`), and C++ scope-resolved calls (`ns::func()`).

Design decision: only the first child of the call node is inspected (loop breaks after the first iteration) since, in the supported grammars, the first child of a call node is always the callee expression; this avoids misinterpreting call arguments as usages.

Edge case: for attribute-type callees, only the leading segment before the first `.` is checked against `imported_names`, but the full dotted name is stored, preserving specificity for later deduplication.

## `_parse_attribute_node`

Takes `node` (`Node`, an attribute-access node) and `imported_names` (`set[str]`). Returns `UsageInfo | None` based on whether the leading component of the dotted attribute path is a tracked name.

Exists to detect standalone attribute-access usages (as opposed to attribute expressions that are merely the callee of a call, which are filtered out beforehand by `_is_function_part_of_call`).

Design decision: matches only on the leading segment (`name.split(".")[0]`) against `imported_names` while storing the full attribute text as `name`, allowing precise reporting of exactly which attribute was accessed while still keying the match on the imported root symbol.

## `_parse_identifier_node`

Takes `node` (`Node`, an identifier node), `imported_names` (`set[str]`), `skip_parent_types` (`set[str]`), and `skip_name_field_types` (`set[str]`). Returns `UsageInfo | None`.

Exists to detect simple, non-compound symbol references while filtering out identifiers that are part of syntactic declarations (parameter names, import statement components, etc.) rather than actual usages.

Key design decision: `skip_name_field_types` enables a finer-grained skip than `skip_parent_types` — for node types like default/keyword parameters where only the declared name (identified via the parent's `"name"` field) should be ignored, while a same-node "value" child (e.g., a default value referencing an imported symbol) is still treated as a genuine usage. This distinguishes declaration sites from reference sites within the same parent construct.

## `extract_typed_aliases`

Takes `root_node` (`Node`), `imported_names` (`set[str]`, tracked type names), and `typed_alias_parent_types` (`set[str]`, node types representing typed declarations). Returns `dict[str, str]` mapping variable name to its declared imported type name.

Exists to support tracking of usages that occur indirectly through a locally-declared variable typed with an imported class/type (e.g., `Genre genre`), so that later uses of `genre` alone can be attributed back to the `Genre` symbol by callers (as seen in `usage_analysis.py`, which merges these aliases into the tracked symbol set before calling `extract_usages`).

Design decision: uses the same stack-based DFS pattern as `extract_usages` for consistency, but only needs to find declaration nodes matching `typed_alias_parent_types` rather than performing full symbol classification.

Edge case/constraint: returns an empty dict immediately if `typed_alias_parent_types` is empty. A variable is excluded from the result if its name equals the type name (`var_name != type_name`), avoiding self-referential or nonsensical aliasing entries. Only declarations whose extracted type is present in `imported_names` are included.

## `_extract_type_and_var`

Takes `node` (`Node`, a typed-declaration node). Returns `tuple[str | None, list[str]]` — the declared type name (or `None` if not found) and the list of variable names declared under that type.

Exists to normalize differing AST shapes across Java, Kotlin, and C/C++ typed-declaration grammars into a single (type, variables) representation, so `extract_typed_aliases` can remain language-agnostic.

Design decision: handles nested wrapper nodes explicitly — `user_type` (Kotlin) for locating the inner `type_identifier`, and `variable_declarator`/`init_declarator` (Java/C/C++) for locating the inner `identifier` — since in these grammars the variable name is not a direct child of the declaration node but nested one level deeper.

Edge case: a declaration node may contain multiple identifier-like children (e.g., multiple declarators), all of which are appended to `var_names`, supporting multi-variable declarations of a single type such as `Genre a, b;`.

# Dependency Description

### Dependencies (what this file uses)

- **tree_sitter (`Node`)**: Used purely as a type annotation for AST node parameters throughout the module. This file relies on the `tree_sitter` library's AST node structure (`type`, `children`, `parent`, `text`, `start_point`, `child_by_field_name`) to traverse and inspect syntax trees, but this is an external library dependency rather than a project-internal one.

This file has no project-internal dependencies; it operates solely on generic AST `Node` objects and configuration dictionaries (`usage_node_types`, `typed_alias_parent_types`) passed in by its callers, without importing any other project module.

### Dependents (what uses this file)

- **`codetwine/extractors/usage_analysis.py`**: This module depends on `usages.py` for two core capabilities:
  - It calls `extract_typed_aliases` to detect typed variable declarations (e.g., a variable declared with an imported type) and build a mapping from variable names to their declared type names, which it then merges into its own symbol-to-file mapping so that aliased variables are tracked alongside their original type names.
  - It calls `extract_usages` to scan an AST and produce a deduplicated list of `UsageInfo` records for a given set of tracked symbol names (including the aliases resolved via `extract_typed_aliases`), using `usage_node_types` configuration to drive language-specific detection rules.

The dependency direction is **unidirectional**: `usage_analysis.py` depends on `usages.py` for AST-based usage and alias extraction, while `usages.py` has no knowledge of or dependency on `usage_analysis.py` or any other project file.

# Data Flow

## Input

| Source | Format | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | AST root of a source file, provided by callers in `usage_analysis.py` |
| `imported_names` | `set[str]` | Symbol names to track (typically keys of `symbol_to_file_map`, possibly extended with alias variable names) |
| `usage_node_types` / `typed_alias_parent_types` | `dict` / `set[str]` | Per-language node-type configuration derived from `USAGE_NODE_TYPES` in config.py |

## Main Transformation Flow

### `extract_usages`
```
root_node ──► DFS stack traversal ──► per-node-type dispatch ──► UsageInfo (raw) ──► _deduplicate ──► list[UsageInfo]
```
1. **Config unpack**: `usage_node_types` is destructured into `call_types`, `attribute_types`, `skip_parent_types`, `skip_name_field_types`, `skip_parent_types_for_type_ref`.
2. **DFS traversal**: nodes are popped from a stack; each node's children are pushed back regardless of branch taken (full-tree scan).
3. **Node classification & extraction** (mutually exclusive per node):
   - `call_types` → `_parse_call_node` inspects only the first child (identifier / attribute / qualified_identifier) to detect calls like `func()`, `module.func()`, `ns::func()`.
   - `attribute_types` (not the function part of a call, checked via `_is_function_part_of_call`) → `_parse_attribute_node` detects `module.attr` style access.
   - `qualified_identifier` → scope part (`namespace_identifier`/`identifier`/`type_identifier`) is checked directly against `imported_names`; skipped if parent is in `skip_parent_types`.
   - `type_identifier` / `namespace_identifier` → checked against `imported_names` using `skip_parent_types_for_type_ref` for skip logic.
   - `identifier` → `_parse_identifier_node` applies `skip_parent_types` / `skip_name_field_types` rules to filter out declaration/import syntax noise, keeping only real references.
4. **Deduplication**: `_deduplicate` groups raw `UsageInfo` by `line`, removes shorter names when a more qualified name (`name.` prefix) exists on the same line, and removes exact `(name, line)` duplicates. Result sorted by ascending line number.

### `extract_typed_aliases`
```
root_node ──► DFS stack traversal ──► nodes matching typed_alias_parent_types ──► _extract_type_and_var ──► {var_name: type_name}
```
1. DFS traversal similar to above, but simpler (no classification branches besides membership in `typed_alias_parent_types`).
2. For each matching node, `_extract_type_and_var` parses children to find a type name (`type_identifier`, or nested inside `user_type`) and variable names (`identifier`, `simple_identifier`, or nested inside `variable_declarator`/`init_declarator`).
3. Only pairs where `type_name` is in `imported_names` are kept; self-referential (`var_name == type_name`) entries are excluded.

## Output

| Function | Output Type | Structure | Destination |
|---|---|---|---|
| `extract_usages` | `list[UsageInfo]` | `{name: str, line: int}` per entry | Consumed by `usage_analysis.py` to map usages to source files via `symbol_to_file_map` |
| `extract_typed_aliases` | `dict[str, str]` | `{variable_name: type_name}` | Used by `usage_analysis.py` to extend `symbol_to_file_map` (alias variable → same file as its type) and to extend the tracked `imported_names` set before calling `extract_usages` |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `UsageInfo` (dataclass) | `name: str`, `line: int` | Represents one detected usage occurrence (symbol name + 1-based line number) |
| `usage_node_types` (input dict) | `call_types`, `attribute_types`, `skip_parent_types`, `skip_name_field_types` (optional), `skip_parent_types_for_type_ref` (optional) | Language-specific rules controlling which AST node types represent calls/attributes and which parent contexts to skip |
| `by_line` (internal, in `_deduplicate`) | `{line: list[UsageInfo]}` | Groups usages per line to resolve redundancy between short and qualified names |
| `aliases` (output of `extract_typed_aliases`) | `{var_name: type_name}` | Maps locally declared variable names to their imported type, enabling indirect usage tracking (e.g., `genre` → `Genre`) |

# Error Handling

## Overall Strategy

This module follows a **graceful degradation** strategy with no explicit exception handling (no `try/except` blocks anywhere in the file). Instead, robustness is achieved through:

- **Defensive configuration checks**: Functions validate their configuration inputs (`usage_node_types`, `typed_alias_parent_types`) at the entry point and return empty results (`[]` or `{}`) rather than raising errors when configuration is absent.
- **Optional-key tolerance**: Missing optional keys in `usage_node_types` (e.g., `skip_name_field_types`, `skip_parent_types_for_type_ref`) are handled via `.get()` with sensible defaults, avoiding `KeyError`.
- **Null-safety checks**: Node traversal code consistently checks for `None` (e.g., `parent`, `name_child`) before dereferencing, preventing `AttributeError`.
- **Silent skipping over failure**: When a node doesn't match expected patterns (unrecognized structure, name not in `imported_names`, etc.), the function simply returns `None` or continues traversal, rather than raising an error.

Overall, the module assumes it will be fed a valid, well-formed AST (`tree_sitter.Node`) and does not attempt to validate the AST structure itself; it only guards against absent/optional configuration and incomplete node relationships (missing parent/child fields) that are expected to occur naturally across different languages/grammars.

## Main Error Patterns and Handling

| Error Type | Handling | Impact |
|---|---|---|
| `usage_node_types` is `None`/empty | Early return of an empty list before any processing | `extract_usages` yields no usages for the file/language; no exception surfaces to caller |
| `typed_alias_parent_types` is empty | Early return of an empty dict before any processing | `extract_typed_aliases` yields no aliases; no exception surfaces to caller |
| Missing optional config keys (`skip_name_field_types`, `skip_parent_types_for_type_ref`) | Retrieved via `.get()` with default (empty set or fallback to `skip_parent_types`) | Traversal continues with reduced/default skip behavior instead of crashing |
| Required config keys (`call_types`, `attribute_types`, `skip_parent_types`) missing | Direct dict indexing (`usage_node_types["..."]`) — no guard | Would raise `KeyError`; treated as a caller/config contract violation rather than a runtime condition to degrade gracefully from |
| Node has no parent (`node.parent` is `None`) | Checked explicitly before accessing `parent.type` | Skip logic is bypassed safely; node is still processed as a normal candidate |
| Node text decoding (`.text.decode("utf-8")`) | No guard around decode calls | Assumes AST node text is always valid UTF-8, consistent with tree-sitter's guarantees; failures are not anticipated |
| Name not found among `imported_names` | Function returns `None` / loop simply continues | Node is silently excluded from results; not treated as an error condition |
| Node type does not match any recognized pattern (e.g., `_extract_type_and_var` finds no `type_identifier`) | Returns `(None, [])` and caller checks truthiness before use | Declaration is skipped without alias entry; no propagation of failure |
| Duplicate/redundant usage entries on the same line | Handled by `_deduplicate` via string-prefix and key-based filtering | Ensures cleaner output list rather than an error state; purely a data-quality normalization step, not error handling |

## Design Considerations

- The module draws a clear line between **configuration errors** (required keys assumed present, accessed directly and would fail loudly via `KeyError`) and **data/traversal irregularities** (parent/child absence, unmatched node types, unregistered names), which are treated as normal, expected variation across languages and handled gracefully.
- Because the AST structure varies significantly between languages (Java, Kotlin, C/C++, etc.), the functions are written to tolerate structural absence (e.g., no `type_identifier` child, no `parent`) as a normal case rather than an anomaly, reflecting the multi-language design intent described in the docstrings.
- No logging or diagnostic reporting is performed on skipped/unmatched cases; the module relies entirely on return values (`None`, empty collections) to communicate "nothing found" to callers, keeping the traversal logic simple and side-effect-free.

# Summary

`usages.py` locates where tracked symbols (imports, classes, functions) are used in an AST. Public API: `UsageInfo` (name, line), `extract_usages` (finds calls/attributes/identifiers/type-refs matching given names, dedup'd) and `extract_typed_aliases` (maps variable names to imported types via typed declarations). Both use config-driven, iterative DFS to stay language-agnostic (Python/Java/Kotlin/C/C++). No project-internal dependencies; used by `usage_analysis.py` to build symbol-to-file mappings. Degrades gracefully when config is missing; no exceptions raised.
