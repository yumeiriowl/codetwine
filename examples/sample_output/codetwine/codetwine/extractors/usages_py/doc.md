# Design Document: codetwine/extractors/usages.py

## Overview & Purpose

## 1. Module Summary

Extracts usage locations of imported symbols from a parsed AST and identifies typed variable aliases, providing the data needed to determine which imported names are actually referenced within a source file.

## 2. When to Use This Module

- **Tracking symbol usages across a file**: Call `extract_usages(root_node, imported_names, usage_node_types)` to receive a deduplicated list of `UsageInfo` entries describing where each imported name appears in the AST (function calls, attribute access, identifiers, type references, namespace references).
- **Resolving typed variable aliases**: Call `extract_typed_aliases(root_node, imported_names, typed_alias_parent_types)` to obtain a mapping from variable names to their declared type names (e.g., `{"genre": "Genre"}`), so that variables declared with an imported type can also be tracked as usages of that type.

Both functions are consumed by `codetwine/extractors/usage_analysis.py`, which first resolves aliases to expand the set of tracked names and then calls `extract_usages` to locate all references.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` | `name: str`, `line: int` | dataclass | Holds a single symbol usage: the symbol name and its 1-based line number. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | Traverses the AST via DFS and returns a deduplicated list of locations where any imported name is used (calls, attribute access, identifiers, type/namespace references). Returns `[]` when `usage_node_types` is `None`. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose type is an imported name, returning a `{variable_name: type_name}` mapping. Returns `{}` when `typed_alias_parent_types` is empty. |

## 4. Design Decisions

- **Language-agnostic via configuration**: Both `extract_usages` and `extract_typed_aliases` accept external node-type sets (`usage_node_types`, `typed_alias_parent_types`) rather than hard-coding language-specific AST node names. This allows the same traversal logic to serve multiple languages (Python, Java, Kotlin, C/C++) by varying only the configuration.
- **Separation of call and attribute handling**: Function call nodes (`call_types`) and standalone attribute access nodes (`attribute_types`) are handled by distinct paths. An attribute node that is the function part of a call is intentionally skipped during standalone attribute processing to avoid double-counting; the call node handler is responsible for recording it instead.
- **Deduplication strategy**: After collection, `_deduplicate` removes both exact `(name, line)` duplicates and redundant shorter names when a more-qualified form (e.g., `module` vs. `module.attr`) appears on the same line, keeping only the more specific entry.
- **Qualified identifier scoping (C++)**: `qualified_identifier` nodes are handled separately to extract only the leftmost scope part, preventing duplication that would otherwise arise from individually processing the `namespace_identifier` and `identifier` children inside the same node.

## Definition Design Specifications

---

## `UsageInfo`

**Signature:** `@dataclass class UsageInfo`

**Responsibility:** Represents a single detected usage location of an imported symbol within source code. Serves as the atomic unit of result data returned by the extraction pipeline.

**When to use:** Instantiated internally by extraction helpers whenever a matching symbol name is found at a specific line; callers receive lists of these objects from `extract_usages`.

**Fields:**

| Field | Type | Purpose |
|-------|------|---------|
| `name` | `str` | The symbol name as it appears at the usage site (may be a dotted name such as `module.attr`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

**Design decisions:** Using a dataclass provides value-equality semantics and a lightweight structure with no behaviour, making deduplication comparisons straightforward.

---

## `extract_usages`

**Signature:**
```
extract_usages(
    root_node: Node,
    imported_names: set[str],
    usage_node_types: dict | None = None,
) -> list[UsageInfo]
```

- `root_node`: The root `Node` of a tree-sitter AST for an entire source file.
- `imported_names`: A set of symbol name strings whose usage sites are to be found.
- `usage_node_types`: A configuration dictionary keyed by language-specific node type category names. `None` or empty causes an immediate empty return.
- Returns: A deduplicated list of `UsageInfo` objects, sorted by ascending line number.

**Responsibility:** Performs a depth-first traversal of the AST to locate all usage sites of the given imported names, dispatching to specialised helpers based on AST node type.

**When to use:** Called by `usage_analysis.py` once per file to obtain all dependency usage locations for a set of tracked symbol names.

**Design decisions:**

- Uses an explicit stack for DFS rather than recursion, avoiding stack-overflow on deeply nested ASTs.
- Detection is split into five mutually exclusive branches per node type: call nodes, attribute nodes, `qualified_identifier` (C++ scope resolution), type/namespace reference nodes, and plain identifier nodes. This prevents double-counting the same textual occurrence.
- The `qualified_identifier` branch extracts only the leftmost (scope) part to avoid duplication with individually-visited child `namespace_identifier`/`identifier` nodes.
- `skip_parent_types_for_type_ref` defaults to `skip_parent_types` when absent, allowing type references in parameter and method declarations to be detected as dependencies while plain identifiers in the same positions are still suppressed.
- Results are passed through `_deduplicate` before being returned.

**Constraints & edge cases:**

- Returns `[]` immediately when `usage_node_types` is falsy (covers `None` and empty dict).
- Required keys in `usage_node_types`: `"call_types"`, `"attribute_types"`, `"skip_parent_types"`. Missing optional keys (`"skip_name_field_types"`, `"skip_parent_types_for_type_ref"`) are resolved to safe defaults.
- Assumes tree-sitter `Node` objects with valid `.type`, `.text`, `.parent`, `.children`, `.start_point`, and `.id` attributes.

---

## `_deduplicate`

**Signature:**
```
_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]
```

- `usage_list`: A list of `UsageInfo` objects that may contain duplicates or redundant entries.
- Returns: A deduplicated list sorted by line number in ascending order.

**Responsibility:** Removes two categories of redundancy: exact `(name, line)` duplicates, and shorter names that are subsumed by a longer dotted form on the same line (e.g. `module` when `module.attr` is also present).

**When to use:** Called exclusively at the end of `extract_usages` before its return value is handed back to the caller.

**Design decisions:** Groups entries by line number so that the "subsumed by dotted form" check only needs to compare names within the same line, keeping the operation linear in the number of entries per line rather than quadratic across the entire list.

**Constraints & edge cases:** Subsumption check uses a prefix match of the form `other.startswith(usage.name + ".")`, so a name that happens to be a prefix of an unrelated name in a different dotted path would be incorrectly suppressed if the longer form appears on the same line.

---

## `_is_function_part_of_call`

**Signature:**
```
_is_function_part_of_call(node: Node, call_types: set[str]) -> bool
```

- `node`: An attribute-type AST node being evaluated.
- `call_types`: Set of node type strings representing function call nodes.
- Returns: `True` if the node is the function (callee) part of a parent call node; `False` otherwise.

**Responsibility:** Prevents an attribute-access node from being processed standalone when it is already the function-name part of a call expression handled by the call branch.

**When to use:** Called within `extract_usages` before delegating attribute nodes to `_parse_attribute_node`, to ensure a call like `module.func()` is not counted twice.

**Design decisions:** Identity comparison is done via `.id` rather than positional index to be robust against AST variants where non-identifier children (punctuation, etc.) precede the callee.

---

## `_parse_call_node`

**Signature:**
```
_parse_call_node(
    node: Node,
    imported_names: set[str],
    attribute_types: set[str],
) -> UsageInfo | None
```

- `node`: A call-type AST node.
- `imported_names`: Set of symbol names to match against.
- `attribute_types`: Set of node type strings representing attribute access nodes.
- Returns: A `UsageInfo` for the call site, or `None` if no imported name is the callee.

**Responsibility:** Extracts the leading name from a function call node and returns a usage record when that name (or the root of a dotted callee) is in the imported set.

**When to use:** Invoked by `extract_usages` for every node whose type is in `call_types`.

**Design decisions:** Only the first child of the call node is inspected; all subsequent children are ignored. This deliberately avoids descending into arguments, which are handled by subsequent DFS iterations. Handles three callee shapes: plain identifier, attribute access, and C++ qualified identifier.

**Constraints & edge cases:** For attribute-style callees, only the leftmost segment (`name.split(".")[0]`) is compared against `imported_names`, so the full dotted form is recorded as the usage name while matching is done on the root.

---

## `_parse_attribute_node`

**Signature:**
```
_parse_attribute_node(
    node: Node,
    imported_names: set[str],
) -> UsageInfo | None
```

- `node`: An attribute-access AST node.
- `imported_names`: Set of symbol names to match against.
- Returns: A `UsageInfo` carrying the full dotted text, or `None` if the root is not imported.

**Responsibility:** Records a standalone attribute access (not part of a call) as a usage when the leading name is an imported symbol.

**When to use:** Invoked by `extract_usages` for attribute-type nodes that pass the `_is_function_part_of_call` guard.

**Constraints & edge cases:** The full text of the node (potentially multi-level, e.g. `a.b.c`) is stored as the usage name, but only the first segment is checked for membership in `imported_names`.

---

## `_parse_identifier_node`

**Signature:**
```
_parse_identifier_node(
    node: Node,
    imported_names: set[str],
    skip_parent_types: set[str],
    skip_name_field_types: set[str],
) -> UsageInfo | None
```

- `node`: A plain `identifier` AST node.
- `imported_names`: Set of symbol names to match against.
- `skip_parent_types`: Set of parent node types that indicate the identifier is part of syntax (definitions, imports, etc.) and should be suppressed entirely.
- `skip_name_field_types`: Set of parent node types where only the child occupying the `"name"` field is suppressed; the `"value"` field sibling is still eligible.
- Returns: A `UsageInfo` if the identifier is a genuine usage, or `None` if it should be suppressed.

**Responsibility:** Determines whether a plain identifier represents a real usage of an imported symbol, filtering out occurrences that are part of declaration syntax rather than references.

**When to use:** Invoked by `extract_usages` for every `identifier`-type node encountered during DFS.

**Design decisions:** The `skip_name_field_types` mechanism allows parameter default expressions (`def f(x=some_var)`) to be detected: the parameter name `x` is suppressed while `some_var` is captured. This requires a named-field lookup on the parent node rather than a simple type check.

**Constraints & edge cases:** Suppression logic depends on the parent node being present; parentless identifiers (e.g. the root of a trivial tree) bypass all checks and are matched against `imported_names` directly.

---

## `extract_typed_aliases`

**Signature:**
```
extract_typed_aliases(
    root_node: Node,
    imported_names: set[str],
    typed_alias_parent_types: set[str],
) -> dict[str, str]
```

- `root_node`: The root `Node` of a tree-sitter AST for an entire source file.
- `imported_names`: Set of imported type names to match.
- `typed_alias_parent_types`: Set of AST node types that represent typed variable declaration sites (language-specific).
- Returns: A mapping of `{variable_name: type_name}` for declarations whose declared type is in `imported_names`.

**Responsibility:** Discovers variables whose declared type is one of the tracked imported types, so that those variable names can also be tracked as indirect usages of the imported type.

**When to use:** Called by `usage_analysis.py` before `extract_usages` to expand the set of trackable names with typed alias variables (e.g. mapping `genre` → `Genre`).

**Design decisions:** Uses a DFS stack traversal, delegating type/variable extraction to `_extract_type_and_var` for each matching node type. A variable is excluded from the result when its name equals the type name to avoid trivially self-referential entries.

**Constraints & edge cases:** Returns `{}` immediately when `typed_alias_parent_types` is falsy. Only produces entries where the type name is a member of `imported_names`; declarations with untracked types are silently ignored.

---

## `_extract_type_and_var`

**Signature:**
```
_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]
```

- `node`: An AST node representing a typed variable declaration.
- Returns: A two-element tuple `(type_name, [var_name, ...])`. `type_name` is `None` and the list is empty when neither is found.

**Responsibility:** Abstracts the AST structural differences across Java, Kotlin, and C/C++ to extract the declared type name and the names of declared variables from a single declaration node.

**When to use:** Called exclusively by `extract_typed_aliases` for each node whose type is in `typed_alias_parent_types`.

**Design decisions:** Handles four distinct child patterns in one pass over the node's direct children to accommodate language-specific nesting (e.g. Kotlin's `user_type > type_identifier`, Java/C++ wrapper declarator nodes). This avoids separate per-language implementations.

**Constraints & edge cases:** Only inspects direct children and one level of nesting inside `user_type`, `variable_declarator`, and `init_declarator` nodes. More deeply nested or non-standard AST shapes are not covered.

## Dependency Description

## Dependencies (modules this file imports)

This file has no project-internal module dependencies. All imports (`dataclasses`, `tree_sitter`) are standard library or third-party packages, which are excluded from this description.

## Dependents (modules that import this file)

**`codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages_py/usages.py`**

`usage_analysis.py` imports and uses two functions from this module:

- `usage_analysis` → `usages` : Uses `extract_usages` to traverse the AST of a source file and retrieve a list of `UsageInfo` objects indicating where imported symbol names are referenced. This is called with a root AST node, a set of symbol names to track, and a per-language `usage_node_types` configuration dict.

- `usage_analysis` → `usages` : Uses `extract_typed_aliases` to discover typed variable declarations in the AST and build a mapping from variable names to their declared type names. This is called with a root AST node, a set of imported type names, and a set of AST node types representing typed variable declarations.

## Dependency Direction

- **`codetwine/extractors/usage_analysis.py` → this module**: Unidirectional. `usage_analysis.py` consumes `extract_usages` and `extract_typed_aliases` from this module; this module has no reference back to `usage_analysis.py`.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `root_node` | Caller (`usage_analysis.py`) | A `tree_sitter.Node` representing the root of a parsed AST for an entire source file |
| `imported_names` | Caller | A `set[str]` of symbol names whose usages are to be tracked |
| `usage_node_types` | Caller (derived from `config.py` `USAGE_NODE_TYPES`) | A `dict` containing per-language node type classification sets, or `None` |
| `typed_alias_parent_types` | Caller (`usage_analysis.py`) | A `set[str]` of AST node type names representing typed variable declarations |

The `usage_node_types` dict carries the following keys:

| Key | Type | Required | Purpose |
|---|---|---|---|
| `call_types` | `set[str]` | Required | AST node types representing function calls |
| `attribute_types` | `set[str]` | Required | AST node types representing attribute access |
| `skip_parent_types` | `set[str]` | Required | Parent node types whose identifier children should be skipped |
| `skip_name_field_types` | `set[str]` | Optional | Parent node types where only the `name`-field child is skipped |
| `skip_parent_types_for_type_ref` | `set[str]` | Optional | Skip list used specifically for type/namespace reference nodes |
| `typed_alias_parent_types` | `set[str]` | Optional | Node types representing typed variable declarations (used by `extract_typed_aliases`) |

---

## 2. Transformation Overview

### `extract_usages` pipeline

**Stage 1 – Guard and configuration extraction**
If `usage_node_types` is `None` or empty, an empty list is returned immediately. Otherwise, the required and optional node type sets are unpacked from the dict for use throughout traversal.

**Stage 2 – DFS AST traversal**
A stack is initialized with `root_node`. On each iteration, one node is popped and classified by its `node.type`:
- **Call nodes** (`call_types`): delegated to `_parse_call_node`, which inspects the first child to determine whether a simple call (`func()`), an attribute call (`module.func()`), or a C++ scope-resolution call (`geometry::doSomething()`) involves an imported name.
- **Attribute nodes** (`attribute_types`): if not the function part of a call (checked via `_is_function_part_of_call`), delegated to `_parse_attribute_node`, which checks whether the leading name of `module.attr` is imported.
- **`qualified_identifier` nodes**: the leftmost namespace/identifier/type child is extracted; if it matches an imported name, a `UsageInfo` is created directly—unless the parent type is in `skip_parent_types`.
- **Type/namespace reference nodes** (`type_identifier`, `namespace_identifier`): the node text is matched against `imported_names`, skipping when the parent is in `skip_parent_types_for_type_ref`.
- **`identifier` nodes**: delegated to `_parse_identifier_node`, which skips the node when its parent is in `skip_parent_types`, or skips only the `name`-field child when the parent is in `skip_name_field_types`.

All matched nodes append a `UsageInfo` to `usage_list`. After processing, all children of the current node are pushed onto the stack.

**Stage 3 – Deduplication (`_deduplicate`)**
The accumulated `usage_list` is grouped by line number. Within each line group, any entry whose `name` is a prefix of another entry's name on the same line (e.g., `module` when `module.attr` also exists) is dropped. Remaining entries with duplicate `(name, line)` pairs are also removed. The result is returned sorted by ascending line number.

---

### `extract_typed_aliases` pipeline

**Stage 1 – Guard**
If `typed_alias_parent_types` is empty, an empty dict is returned immediately.

**Stage 2 – DFS AST traversal**
A stack initialized with `root_node` traverses all nodes. When a node's type is in `typed_alias_parent_types`, it is passed to `_extract_type_and_var`.

**Stage 3 – Type and variable extraction (`_extract_type_and_var`)**
Children of the declaration node are inspected to find a `type_identifier` (or `user_type > type_identifier` for Kotlin) as the type name, and `identifier`, `simple_identifier`, `variable_declarator`, or `init_declarator` children as variable names. A `(type_name, [var_names])` tuple is returned.

**Stage 4 – Alias map construction**
If the extracted type name is in `imported_names`, each variable name (excluding the type name itself) is recorded in the `aliases` dict mapping `var_name → type_name`.

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `extract_usages` | `list[UsageInfo]` | Deduplicated list of symbol usage locations, sorted by line number |
| `extract_typed_aliases` | `dict[str, str]` | Mapping of variable name → type name for typed declarations using imported types |

Both functions return purely in-memory values; there are no file writes or side effects.

---

## 4. Key Data Structures

### `UsageInfo`

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name being used (may include dotted form such as `module.attr`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

### `by_line` (internal to `_deduplicate`)

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `int` | Line number |
| Value | `list[UsageInfo]` | All usage entries recorded on that line |

### `aliases` (output of `extract_typed_aliases`)

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | Variable name declared with an imported type (e.g., `"genre"`) |
| Value | `str` | The imported type name used in the declaration (e.g., `"Genre"`) |

### `usage_node_types` dict (input)

Documented in the Inputs section above.

## Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** strategy. Rather than raising exceptions or terminating on unexpected or missing inputs, functions return safe empty values (empty lists or empty dicts) when preconditions are not met. Node traversal is designed so that unrecognized or irrelevant nodes are silently skipped, and processing continues without interruption.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing configuration (`usage_node_types` is `None` or empty) | Caller passes `None` or falsy dict to `extract_usages` | Returns empty list immediately | Yes | No usages extracted; caller receives `[]` |
| Missing configuration (`typed_alias_parent_types` is empty) | Caller passes empty set to `extract_typed_aliases` | Returns empty dict immediately | Yes | No aliases extracted; caller receives `{}` |
| Optional key absent in `usage_node_types` dict | `"skip_name_field_types"` or `"skip_parent_types_for_type_ref"` key not present | `dict.get()` with a safe default (`set()` or fallback to `skip_parent_types`) | Yes | Functionality falls back to default behavior without error |
| Node has no parent (`node.parent` is `None`) | Root node or detached node encountered during identifier/type-ref checks | Guard conditions (`if parent and ...`) prevent attribute access on `None` | Yes | Node is processed without parent-based skip logic |
| Non-matching node types during traversal | A node's type does not match any recognized category | Node is silently skipped in the `if/elif` chain; children are still pushed onto the stack | Yes | That node contributes no usage; traversal continues normally |
| No children matching expected structure | Call node or attribute node lacks an identifier or attribute child in the expected position | Loop finds no match and falls through; function returns `None` | Yes | No `UsageInfo` recorded for that node |
| Duplicate or redundant `UsageInfo` entries | Multiple traversal paths produce the same `(name, line)` pair, or a short name and its qualified form appear on the same line | `_deduplicate` removes shadowed shorter names and exact duplicates | Yes | Final list is clean; no data loss of meaningful entries |

---

## 3. Design Notes

- **No exceptions are raised anywhere in this file.** All potential absence or mismatch conditions are handled through guard clauses and early returns, keeping the module's public contract simple: callers always receive a list or dict, never an exception from this layer.
- The early-return pattern on falsy `usage_node_types` / `typed_alias_parent_types` reflects an explicit design decision that languages without a registered configuration produce no output rather than a runtime error, allowing the system to support multi-language scenarios where some languages lack usage-tracking definitions.
- The `_deduplicate` step is a post-processing safeguard that compensates for the fact that DFS traversal may visit both a parent node (e.g., a call or attribute node) and its identifier children, which would otherwise produce overlapping records. This is handled as a structural property of the traversal rather than an exception condition.
- The use of `dict.get()` with fallback defaults for optional configuration keys means the caller is not required to supply a complete configuration object, reducing the risk of `KeyError` failures from partially defined language configurations.

## Summary

**usages.py** extracts symbol usage locations from a tree-sitter AST.

- `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict|None) → list[UsageInfo]`: DFS traversal returning deduplicated usage locations.
- `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) → dict[str, str]`: finds typed variable declarations mapping `var_name → type_name`.
- `UsageInfo` dataclass: `name: str`, `line: int`.
- Consumes `usage_node_types` dict with keys `call_types`, `attribute_types`, `skip_parent_types` (all `set[str]`).
