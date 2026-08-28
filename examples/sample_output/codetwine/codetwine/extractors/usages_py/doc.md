# Design Document: codetwine/extractors/usages.py

# Overview & Purpose

## 1. Module Summary
Extract, from a source file's AST, the locations where a given set of tracked symbol names are used (calls, attribute access, identifiers, type/namespace references) and the type-annotated variable aliases that refer to those symbols.

## 2. When to Use This Module
- **Finding where imported/target symbols are referenced in code**: call `extract_usages` with the AST root, a set of symbol names to track, and the language-specific `usage_node_types` config to get a deduplicated, line-sorted list of `UsageInfo` entries describing each usage site.
- **Resolving typed variable declarations back to an imported type**: call `extract_typed_aliases` with the AST root, the set of imported type names, and `typed_alias_parent_types` (language-specific declaration node types) to get a `{variable_name: type_name}` mapping (e.g., `{"genre": "Genre"}"`), so that variables of a tracked type can also be tracked as usages of that type.
- **Building a cross-file symbol usage map**: as done in `usage_analysis.py`, first call `extract_typed_aliases` to expand the tracked-name set with local variable aliases of imported types, then call `extract_usages` with the expanded name set to capture both direct references and references via aliased variables.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `UsageInfo` (dataclass) | fields: `name: str`, `line: int` | — | Represents a single usage location: the symbol name used and its 1-based line number. |
| `extract_usages` | `root_node: Node`, `imported_names: set[str]`, `usage_node_types: dict \| None` | `list[UsageInfo]` | Traverses the AST via DFS, detects usages of tracked names in calls, attribute access, identifiers, and type/namespace references, and returns a deduplicated list. |
| `extract_typed_aliases` | `root_node: Node`, `imported_names: set[str]`, `typed_alias_parent_types: set[str]` | `dict[str, str]` | Traverses the AST to find typed variable declarations whose declared type is in `imported_names`, and returns a variable-name-to-type-name mapping. |

## 4. Design Decisions
- **Configuration-driven, language-agnostic traversal**: Node-type sets (`call_types`, `attribute_types`, `skip_parent_types`, etc.) are injected via `usage_node_types`/`typed_alias_parent_types` rather than hardcoded, allowing the same traversal logic to support multiple languages (Python, Java, Kotlin, C/C++) with different AST shapes.
- **DFS via explicit stack**: Both `extract_usages` and `extract_typed_aliases` use an explicit node stack instead of recursion to walk the AST, avoiding recursion-depth concerns on large files.
- **Post-hoc deduplication over precise real-time filtering**: Rather than preventing all redundant/overlapping matches during traversal (e.g., "module" vs "module.attr"), the module collects all candidate usages first and applies `_deduplicate` afterward to keep only the most specific name per line.
- **Special-cased skip rules for syntactic positions**: `skip_parent_types` (and its type-reference variant `skip_parent_types_for_type_ref`) and `skip_name_field_types` allow fine-grained exclusion of identifiers that appear in declaration/import syntax (not actual usages), while still detecting usages on the "value" side of constructs like default parameters.

# Definition Design Specifications

## `UsageInfo` (dataclass)

**Signature:** `@dataclass class UsageInfo`

**Responsibility:** Represents a single detected usage of a tracked symbol at a specific source location, serving as the common return unit for all usage-extraction functions in this module.

**When to use:** Instantiated internally whenever the AST traversal identifies a node that references a name in the caller-supplied `imported_names` set.

**Fields:**

| Field | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name being used (may be a simple name or a dotted/qualified form such as `"module.attr"`) |
| `line` | `int` | 1-based line number of the usage location in the source file |

**Design decisions:** No `__eq__`/hashing customization beyond the dataclass default; deduplication logic (`_deduplicate`) manually builds `(name, line)` keys instead of relying on dataclass equality/hash, allowing it to also perform substring-based redundancy filtering.

**Constraints & edge cases:** `name` can contain dots (e.g., `"module.attr"`), which is significant for the deduplication logic that checks prefix relationships between entries on the same line.

---

## `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict | None = None) -> list[UsageInfo]`

**Responsibility:** Entry point that walks an entire AST and produces a deduplicated list of all locations where any of `imported_names` is referenced, covering calls, attribute access, plain identifiers, type/namespace references, and C++ qualified identifiers.

**When to use:** Called once per source file (or per caller context) after imports have been resolved to a `symbol_to_file_map`, to find where imported symbols are actually used — both for the main pass and for typed-alias-expanded name sets, as shown in `usage_analysis.py`.

**Design decisions:**
- Uses an explicit stack for DFS instead of recursion, avoiding Python recursion-depth limits on large files.
- Dispatches on `node.type` via an `if/elif` chain instead of a dict lookup, so each node type is handled by exactly one branch even though multiple type categories could theoretically overlap (e.g., `call_types` vs `attribute_types` vs `_TYPE_REFERENCE_NODE_TYPES` are treated as mutually exclusive per node).
- `qualified_identifier` (C++ `a::b`) is special-cased separately from the generic type-reference handling: only the first matching child (`namespace_identifier`/`identifier`/`type_identifier`) is checked and recorded, then the loop `break`s, preventing duplicate reporting of the same scope reference from nested children.
- For `qualified_identifier` and type-reference nodes (`type_identifier`/`namespace_identifier`), when the parent is in the relevant skip set, children are still pushed onto the stack (`continue`) rather than fully skipped, so nested valid usages are not lost even though the parent node itself is excluded.
- `skip_parent_types_for_type_ref` defaults to `skip_parent_types` when absent, allowing languages to reuse one skip configuration unless a distinct one is needed for type references.
- Final results always pass through `_deduplicate`, so line-level redundancy (e.g. `module` vs `module.attr`) is resolved centrally rather than at each detection site.

**Constraints & edge cases:**
- Returns `[]` immediately if `usage_node_types` is falsy (`None` or empty dict) — used for languages without usage-tracking configuration.
- Requires `usage_node_types` to contain `"call_types"`, `"attribute_types"`, and `"skip_parent_types"` keys; missing required keys raise `KeyError`.
- `"skip_name_field_types"` and `"skip_parent_types_for_type_ref"` are optional with documented fallbacks.
- Node types not covered by any branch (call/attribute/qualified_identifier/type-reference/identifier) are traversed but otherwise ignored.

---

## `_deduplicate(usage_list: list[UsageInfo]) -> list[UsageInfo]`

**Responsibility:** Collapses redundant and duplicate usage entries so that, on a given line, a shorter name subsumed by a more detailed dotted name is dropped, and repeated `(name, line)` pairs are collapsed to one.

**When to use:** Invoked once at the end of `extract_usages` on the full accumulated usage list; not intended for external/standalone use.

**Design decisions:**
- Groups usages by line first, then within each line checks, for every usage, whether another usage's name starts with `usage.name + "."` — this handles the case where both a bare module usage and a `module.attr` usage were recorded for the same line, keeping only the more specific one.
- Iterates lines in ascending sorted order to guarantee a deterministic, line-ascending output ordering.
- Uses a `seen_keys` set of `(name, line)` tuples for duplicate suppression rather than relying on dataclass equality, since `UsageInfo` does not define custom hashing behavior needed for use in a set directly (dataclass default equality/hash could work, but the tuple-key approach is used regardless).

**Constraints & edge cases:** If two distinct names on the same line have a prefix relationship not intended as module/attribute (unlikely in practice given how names are constructed), the shorter one would incorrectly be dropped; this is an inherent heuristic limitation tied to the `"module"` vs `"module.attr"` convention produced by `_parse_call_node`/`_parse_attribute_node`.

---

## `_is_function_part_of_call(node: Node, call_types: set[str]) -> bool`

**Responsibility:** Determines whether a given attribute-access node is merely the callee expression of a call node (e.g., the `module.func` part of `module.func()`), to avoid double-reporting it as a standalone attribute usage.

**When to use:** Called from `extract_usages` whenever a node matches `attribute_types`, before deciding whether to independently parse it as an attribute usage.

**Design decisions:** Checks the parent's children for the first one whose type is `"identifier"` or matches the current node's own type, and compares node identity (`.id`) rather than value equality, to correctly identify positional "first child" matching even when multiple children share a type.

**Constraints & edge cases:** Only inspects the immediate parent; if the parent's type is not in `call_types`, returns `False` regardless of grandparent structure. Assumes the callee is always among the parent's children with type `"identifier"` or the same type as `node`.

---

## `_parse_call_node(node: Node, imported_names: set[str], attribute_types: set[str]) -> UsageInfo | None`

**Responsibility:** Extracts a `UsageInfo` from a function-call AST node by inspecting only its first child (the callee expression), supporting simple calls, attribute-based calls, and C++ scope-resolved calls.

**When to use:** Called by `extract_usages` for every node whose type is in `call_types`.

**Design decisions:**
- Only the first child of the call node is examined (enforced via an unconditional `break` at the end of the first loop iteration), reflecting the assumption that the callee is always the first child in the supported grammars.
- For attribute-style callees (`module.func`), the full dotted text is kept as the reported `name`, but matching against `imported_names` is done on the leading segment (`name.split(".")[0]`) so partial import matching works while preserving specificity for deduplication.
- For `qualified_identifier` callees, only the first matching sub-child among `namespace_identifier`/`identifier`/`type_identifier` is checked, then breaks — mirroring the same "single scope segment" strategy used elsewhere.

**Constraints & edge cases:** Returns `None` if the call has no children, if the first child's type doesn't match any handled case, or if the extracted name is not in `imported_names`. Does not examine any child beyond the first.

---

## `_parse_attribute_node(node: Node, imported_names: set[str]) -> UsageInfo | None`

**Responsibility:** Extracts a `UsageInfo` from a standalone attribute-access node (e.g., `module.attr`) when its leading component matches a tracked import.

**When to use:** Called by `extract_usages` for nodes matching `attribute_types` that are not the callee part of a call (per `_is_function_part_of_call`).

**Design decisions:** Uses the full node text (potentially multi-level, e.g. `a.b.c`) as the reported name but only validates the first `.`-separated segment against `imported_names`.

**Constraints & edge cases:** Returns `None` if the leading segment isn't tracked, even if a later segment happens to match an imported name.

---

## `_parse_identifier_node(node: Node, imported_names: set[str], skip_parent_types: set[str], skip_name_field_types: set[str]) -> UsageInfo | None`

**Responsibility:** Determines whether a plain identifier node represents a genuine usage of a tracked symbol, filtering out identifiers that are purely syntactic (declaration names, import statements, parameter names).

**When to use:** Called by `extract_usages` for every node of type `"identifier"` that wasn't already handled as part of a call or attribute node.

**Design decisions:**
- Implements a two-tier skip strategy: for parent types in `skip_name_field_types`, only the specific child bound to the parent's `"name"` field is skipped (via `child_by_field_name("name")` identity check), allowing the "value" side of constructs like default parameters (`x=some_var`) to still be detected as usage.
- For parent types in the coarser `skip_parent_types`, the identifier is unconditionally skipped regardless of field role.
- The `skip_name_field_types` check takes precedence (`elif`) over the general `skip_parent_types` check, so a parent type should logically belong to at most one of the two categories for correct behavior.

**Constraints & edge cases:** If a parent node has no `"name"` field (unexpected grammar shape), `name_child` is `None` and the identifier is not skipped by that branch, falling through to the plain name-match check. Assumes `skip_name_field_types` and `skip_parent_types` are configured without overlapping semantics per parent type.

---

## `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]`

**Responsibility:** Scans the AST for typed variable/parameter/field declarations whose declared type is one of the tracked imported names, producing a mapping from variable name to the imported type name (e.g., `{"genre": "Genre"}`) so that usages of the variable can later be attributed to the imported type's originating file.

**When to use:** Called before (or in place of) `extract_usages` to expand the tracked-name set with local aliases of imported types, as done in `usage_analysis.py` where resulting variable names are merged into `symbol_to_file_map`/`names_from_target` prior to calling `extract_usages`.

**Design decisions:**
- Uses the same stack-based DFS pattern as `extract_usages` for consistency, but with a simpler single-condition check (`node.type in typed_alias_parent_types`) since no skip-list logic is needed at this stage.
- Delegates the actual type/variable-name extraction to `_extract_type_and_var`, keeping language-structure handling isolated from the traversal/aggregation logic.
- Excludes self-referential mappings where `var_name == type_name` (e.g., avoids mapping a type name to itself in edge cases where the extraction logic might otherwise conflate them).

**Constraints & edge cases:** Returns `{}` immediately if `typed_alias_parent_types` is empty/falsy. If multiple declarations across the file declare different types for the same variable name, later occurrences (processed later in the DFS/stack order, which is not strictly source order due to `stack.extend`) overwrite earlier ones in the returned dict, since it's a plain `dict` assignment with no conflict handling.

---

## `_extract_type_and_var(node: Node) -> tuple[str | None, list[str]]`

**Responsibility:** Extracts the declared type name and the list of variable names from a single typed-declaration node, normalizing differences between Java/C/C++ (`type_identifier` + `variable_declarator`/`init_declarator`) and Kotlin (`user_type` wrapping `type_identifier`, plus `simple_identifier`) grammars.

**When to use:** Called once per matching node inside `extract_typed_aliases`'s traversal loop; not intended for standalone use outside that context.

**Design decisions:**
- Iterates only the direct children of the declaration node (no deeper recursion), relying on the grammars' shallow structure for these declaration types; nested `variable_declarator`/`init_declarator` children are inspected one level deeper specifically to reach the `identifier` inside them.
- For Kotlin's `user_type`, only the first `type_identifier` sub-child found is used (`break` after assignment), assuming a single type identifier per `user_type` node.
- Supports multiple variable names per declaration by appending to `var_names` in a list rather than assuming a single variable (relevant for cases like multi-declarator statements), though `type_name` is a single value.

**Constraints & edge cases:** Returns `(None, [])` if no recognized child types are found. If a declaration node contains multiple `type_identifier` children (unexpected in the documented grammars), the last one encountered wins since `type_name` is repeatedly overwritten. Does not handle recursive/nested type expressions beyond the one-level `variable_declarator`/`init_declarator` unwrapping.

# Dependency Description

### Dependencies (modules this file imports)

This file has no project-internal module dependencies. It only relies on external packages (`dataclasses`, `tree_sitter`), which are excluded per instructions. All logic in `codetwine/extractors/usages.py` is self-contained, operating directly on the `Node` objects passed in from callers and the `UsageInfo` dataclass defined internally.

### Dependents (modules that import this file)

- `codetwine/extractors/usage_analysis.py → codetwine/extractors/usages.py` : uses `extract_typed_aliases` to build a variable-name-to-type-name mapping from typed variable declarations in the AST, based on a set of imported type names (`symbol_to_file_map.keys()` or `names_from_target`) and `typed_alias_parent_types` derived from `usage_node_types`.
- `codetwine/extractors/usage_analysis.py → codetwine/extractors/usages.py` : uses `extract_usages` to extract a list of `UsageInfo` symbol usage locations from an AST (`root_node` / `caller_root`), given a set of tracked names (which include both directly imported symbols and typed-alias variable names) and the `usage_node_types` configuration.

### Dependency Direction

The relationship is unidirectional: `codetwine/extractors/usage_analysis.py` depends on `codetwine/extractors/usages.py` by calling its exported functions (`extract_usages`, `extract_typed_aliases`). This file (`usages.py`) does not import or depend on `usage_analysis.py` or any other project-internal module.

# Data Flow

## 1. Inputs

This module receives no file or config reads directly; all data enters as function arguments from callers in `usage_analysis.py`:

- **`root_node`** (`tree_sitter.Node`): The root of an AST for a source file, produced by tree-sitter parsing upstream.
- **`imported_names`** (`set[str]`): The set of symbol names (typically imported/aliased names) whose usages should be detected. Built by callers from `symbol_to_file_map.keys()` or similar name lists.
- **`usage_node_types`** (`dict | None`): Per-language configuration dict (from `USAGE_NODE_TYPES` in `config.py`), containing:
  - `call_types` (`set[str]`, required)
  - `attribute_types` (`set[str]`, required)
  - `skip_parent_types` (`set[str]`, required)
  - `skip_name_field_types` (`set[str]`, optional)
  - `skip_parent_types_for_type_ref` (`set[str]`, optional)
  - `typed_alias_parent_types` (`set[str]`, optional, used only by `extract_typed_aliases`)
- **`typed_alias_parent_types`** (`set[str]`): Node type names representing typed variable declarations (e.g. `field_declaration`, `property_declaration`), passed separately into `extract_typed_aliases`.

## 2. Transformation Overview

The module implements two independent but related pipelines that both traverse the same AST via an explicit stack-based DFS (no recursion).

**Pipeline A: `extract_usages`**
1. **Config unpacking** – Extract node-type sets (`call_types`, `attribute_types`, `skip_parent_types`, etc.) from `usage_node_types`; short-circuit to `[]` if config is missing.
2. **DFS traversal** – Pop nodes off a stack, classify each by `node.type` into one of: call node, attribute node, `qualified_identifier`, type/namespace reference (`type_identifier` / `namespace_identifier`), or plain `identifier`. All children are always pushed for continued traversal regardless of branch taken.
3. **Per-branch extraction**:
   - Call nodes → `_parse_call_node` inspects only the first child (identifier, attribute, or qualified_identifier) to detect calls like `func()`, `module.func()`, `ns::func()`.
   - Attribute nodes → skipped if they are the callee part of a call (`_is_function_part_of_call`), otherwise parsed by `_parse_attribute_node` for `module.attr` patterns.
   - `qualified_identifier` → scope-resolution parsing (`ns::Type`), skipping import/package contexts via `skip_parent_types`.
   - Type/namespace reference nodes → checked against `imported_names` unless their parent is in `skip_parent_types_for_type_ref`.
   - Plain identifiers → `_parse_identifier_node` filters out syntactic positions (declarations, name fields) using `skip_parent_types` / `skip_name_field_types`.
4. **Accumulation** – Matches are collected into a flat `list[UsageInfo]`.
5. **Deduplication (`_deduplicate`)** – Group by line, drop shorter names when a more qualified name (`name.attr`) exists on the same line, then drop exact `(name, line)` duplicates. Result sorted by ascending line number.

**Pipeline B: `extract_typed_aliases`**
1. **Guard** – Return `{}` if `typed_alias_parent_types` is empty.
2. **DFS traversal** – Same stack-based approach; nodes matching `typed_alias_parent_types` are inspected.
3. **Per-node extraction (`_extract_type_and_var`)** – Language-specific child pattern matching extracts a `type_name` and list of `var_names` (handling Java `variable_declarator`, Kotlin `user_type`, C/C++ `init_declarator`, etc.).
4. **Filtering & mapping** – Only declarations whose `type_name` is in `imported_names` are kept; each `var_name` (excluding self-referential matches) is mapped to its `type_name` in the output dict.

**Cross-pipeline data flow (in `usage_analysis.py`, the consumer)**: `extract_typed_aliases` output is used to extend `imported_names`/`symbol_to_file_map` with alias variable names before calling `extract_usages`, so type-alias resolution feeds into usage detection as an enrichment stage, though this merging happens outside this file.

## 3. Outputs

- **`extract_usages`** → `list[UsageInfo]`: deduplicated, line-sorted list of detected symbol usages.
- **`extract_typed_aliases`** → `dict[str, str]`: mapping from variable name to the imported type name it was declared with.
- No file writes or external side effects occur within this module; all output is via return values.

## 4. Key Data Structures

### `UsageInfo` (dataclass)
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | The symbol name found in use (may be a simple name or dotted `module.attr` form) |
| `line` | `int` | 1-based line number of the usage occurrence |

### `usage_node_types` (input dict, schema)
| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types representing function/method calls |
| `attribute_types` | `set[str]` | AST node types representing attribute/member access |
| `skip_parent_types` | `set[str]` | Parent node types under which identifier usages should be ignored (e.g. import statements) |
| `skip_name_field_types` | `set[str]` (optional) | Parent node types where only the "name" field child is skipped, "value" side still tracked |
| `skip_parent_types_for_type_ref` | `set[str]` (optional) | Parent node types to skip specifically for type/namespace reference nodes; defaults to `skip_parent_types` |
| `typed_alias_parent_types` | `set[str]` (optional, used by caller) | Node types representing typed variable declarations |

### Typed alias mapping (output of `extract_typed_aliases`)
| Key | Type | Purpose |
|---|---|---|
| variable name | `str` (dict key) | Local variable/field name declared with an imported type |
| type name | `str` (dict value) | The imported type name used in the declaration |

### Intermediate: `(type_name, var_names)` tuple from `_extract_type_and_var`
| Field | Type | Purpose |
|---|---|---|
| `type_name` | `str \| None` | Extracted declared type (from `type_identifier` or `user_type`) |
| `var_names` | `list[str]` | Extracted variable/field names declared with that type |

### `by_line` (internal dict in `_deduplicate`)
| Key | Type | Purpose |
|---|---|---|
| line number | `int` (dict key) | Groups usages occurring on the same source line |
| usages | `list[UsageInfo]` (dict value) | All `UsageInfo` entries found on that line, used for redundancy filtering |

# Error Handling

## 1. Overall Strategy

This module employs a **graceful degradation** strategy with no explicit exception handling (no `try/except` blocks anywhere in the file). Instead, error avoidance is achieved through:

- **Defensive guards**: Functions check for `None`/empty inputs upfront (`if not usage_node_types: return []`, `if not typed_alias_parent_types: return {}`) and short-circuit to safe default return values.
- **Silent skipping via return `None`**: Helper functions such as `_parse_call_node`, `_parse_attribute_node`, and `_parse_identifier_node` return `None` when a node does not match expected conditions, and callers simply skip appending to the result list rather than raising errors.
- **Optional key handling with `.get()`**: Dictionary lookups for optional configuration keys (`skip_name_field_types`, `skip_parent_types_for_type_ref`) use `.get()` with sensible defaults instead of raising `KeyError`.
- **No propagation to callers**: There is no raising or re-raising of exceptions; the module assumes malformed or unexpected AST structures simply yield no usage/alias data rather than causing failures.

This is effectively a "best-effort extraction" design: if the AST doesn't match expected shapes, the code produces an empty or partial result rather than crashing.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing configuration | `usage_node_types` is `None` or empty (language has no usage tracking defined) | Immediately return empty list `[]` | Yes | No usages extracted for that file/language; no crash |
| Missing typed-alias configuration | `typed_alias_parent_types` is empty | Immediately return empty dict `{}` | Yes | No aliases extracted; no crash |
| Required config keys absent | `call_types`, `attribute_types`, or `skip_parent_types` missing from `usage_node_types` | Direct dict indexing (`usage_node_types["..."]`) — would raise `KeyError` | No | Function call fails; caller (`usage_analysis.py`) not shown to catch this, so exception would propagate |
| Optional config keys absent | `skip_name_field_types` / `skip_parent_types_for_type_ref` not present | `.get()` with fallback default (empty set or `skip_parent_types`) | Yes | Behaves as if feature is disabled or falls back to base skip logic |
| Unmatched/unexpected node shape | Call/attribute/identifier node doesn't match any recognized pattern (e.g., first child isn't `identifier`/attribute/qualified_identifier) | Helper returns `None`; no `UsageInfo` appended | Yes | That particular node is simply not recorded as a usage |
| Name not in tracked set | Extracted `name` (identifier, attribute, type reference, qualified identifier) not in `imported_names` | Conditional check fails; nothing appended | Yes | Node is effectively ignored, treated as irrelevant usage |
| Type/variable not found in declaration | `_extract_type_and_var` finds no `type_identifier`/`user_type` or no identifier children | Returns `(None, [])`; caller's `if type_name and type_name in imported_names` guard skips it | Yes | Declaration contributes no alias entries |
| Self-referential alias | `var_name == type_name` in `extract_typed_aliases` | Explicit skip via `if var_name != type_name` | Yes | Prevents nonsensical alias mapping (e.g., `Genre -> Genre`) |
| Duplicate/redundant usage entries | Same name appears multiple times per line, or a shorter name is subsumed by a longer dotted name on the same line | Deduplication logic in `_deduplicate` filters using string-prefix and set-based key checks | Yes | Result list is cleaned rather than causing errors |

## 3. Design Notes

- The module treats the AST as a best-effort, heterogeneous input source (multiple languages, multiple grammar shapes), so the design favors "return nothing meaningful found" over raising exceptions for structural mismatches — this keeps traversal robust across differing tree-sitter grammars (Java, Kotlin, C/C++, etc.).
- Required configuration keys (`call_types`, `attribute_types`, `skip_parent_types`) are treated as **mandatory contract** values from `USAGE_NODE_TYPES` in `config.py`; the code does not defensively guard against their absence, implying the responsibility for supplying valid configuration lies with the caller (`usage_analysis.py`), consistent with a fail-fast expectation at the configuration boundary while runtime AST traversal remains fault-tolerant.
- Optional keys are handled leniently via `.get()` fallback, reflecting a deliberate distinction between "core" settings (must exist) and "extension" settings (degrade gracefully when absent).
- The deduplication step acts as a final normalization/cleanup pass rather than an error-correction mechanism, ensuring consistent output even when the traversal logic produces overlapping or redundant entries by design (e.g., both `call_types` and `attribute_types` potentially matching related nodes).
- No logging is performed anywhere in this file; there is no logging-and-continue behavior — omissions and skips happen silently by design, consistent with the module's role as a pure data-extraction utility rather than a diagnostic component.

# Summary

Extracts AST-based usage locations of tracked symbol names (calls, attributes, identifiers, type refs). Public API: `UsageInfo(name: str, line: int)` dataclass; `extract_usages(root_node: Node, imported_names: set[str], usage_node_types: dict|None) -> list[UsageInfo]`; `extract_typed_aliases(root_node: Node, imported_names: set[str], typed_alias_parent_types: set[str]) -> dict[str, str]`. Uses config dict `usage_node_types` with sets like `call_types`, `attribute_types`, `skip_parent_types`. No internal deps; consumed by `usage_analysis.py`.
