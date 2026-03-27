# Design Document: codetwine/extractors/imports.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts structured import statement information from a parsed AST by running tree-sitter queries and returning a list of `ImportInfo` objects that represent each import's module, imported names, aliases, and line number.

## 2. When to Use This Module

- **Analyzing imports in a target file**: Call `extract_imports(root_node, language, import_query_str)` to obtain a list of `ImportInfo` objects, which can then be passed to `build_symbol_to_file_map` to resolve imported names to their source files (used in `file_analyzer.py`).
- **Resolving imports in caller files during usage analysis**: Call `extract_imports(caller_root, language, import_query_str)` to retrieve the import list for a caller file, enabling cross-file symbol resolution (used in `usage_analysis.py`).
- **Building a dependency graph**: Call `extract_imports(root_node, language, import_query_str)` to enumerate all imports in a file, then resolve each `ImportInfo.module` to a project path to identify file-level dependencies (used in `dependency_graph.py`).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | dataclass | Holds all structured data for a single import statement, including the source module, individually imported names, line number, and any alias mappings. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST, groups multiple name captures from the same import statement into a single `ImportInfo`, and returns the complete list of imports found in the file. |

## 4. Design Decisions

- **Query-driven, language-agnostic extraction**: The extraction logic is driven entirely by the caller-supplied `import_query_str`, making the module reusable across languages without any language-specific branching in `extract_imports` itself. Language-specific behavior is encoded in the query string, not in this module.
- **Grouping by `(module, line)` key**: Multiple `@name` captures that belong to the same import statement (e.g., `from X import A, B`) are consolidated into a single `ImportInfo` entry using a `(module, line)` tuple as the dictionary key, avoiding duplicate entries.
- **Returns empty list for undefined queries**: When `import_query_str` is `None` or empty, `extract_imports` immediately returns `[]`, allowing callers to pass `None` safely for languages that have no defined import query without requiring a guard at the call site.

## Definition Design Specifications

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

**Signature:** `@dataclass class ImportInfo`

**Responsibility:** Represents a single parsed import statement as a structured record, normalizing the variety of import syntaxes across languages into a uniform shape for downstream consumers.

**When to use:** Instantiated internally by `extract_imports` for each unique (module, line) combination found in the AST; consumed by callers such as `build_symbol_to_file_map` and `resolve_module_to_project_path`.

### Fields

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source path or module name, with surrounding quotes/brackets already stripped. |
| `names` | `list[str]` | The individual names brought into scope (e.g. the `Y` in `from X import Y`). Empty list for bare module imports. |
| `line` | `int` | 1-based line number of the import statement in the source file. |
| `module_alias` | `str \| None` | The local alias assigned to the entire module (the `Y` in `import X as Y`). `None` if no alias. |
| `alias_map` | `dict[str, str] \| None` | Maps each aliased name to its original name (e.g. `{"path_join": "join"}` for `from X import join as path_join`). `None` when no per-name aliases exist. |

**Design decisions:**
- `names` uses a plain list rather than a set to preserve insertion order, with deduplication enforced at write time.
- `alias_map` is `None` (not an empty dict) when unused, allowing callers to cheaply test for alias presence.

---

## `extract_imports`

**Signature:**
```
extract_imports(
    root_node: Node,
    language: Language,
    import_query_str: str | None,
) -> list[ImportInfo]
```

**Responsibility:** Drives the full import-extraction pipeline by running a tree-sitter query against the AST and assembling the results into deduplicated `ImportInfo` records.

**When to use:** Called once per source file whenever a caller needs the full list of imports that file declares; used by `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.

**Design decisions:**

- **Grouping key `(module, line)`:** Multiple query matches that refer to the same import statement (e.g. `from X import A, B` producing two `@name` captures) are merged into one `ImportInfo` rather than producing duplicates.
- **Early return on absent query:** Returns `[]` immediately when `import_query_str` is `None`, making the function safe to call for languages that have no defined query without requiring the caller to guard against `None`.
- **CommonJS filtering:** Matches that expose a `_require_func` capture whose text is not `"require"` are silently skipped, preventing false positives from similarly shaped call expressions.
- **Wildcard detection:** Checks direct children of the `import_node` for nodes typed `"asterisk"` or `"*"` and appends `"*"` to `names`, covering both Java and Kotlin wildcard import syntax without a language-specific branch.
- **Line number source priority:** The `@import_node` capture is preferred over the `@module` capture for line attribution, with the module node used only as a fallback.

**Constraints & edge cases:**
- Returns `[]` (not an error) when `import_query_str` is falsy.
- Requires `root_node` to be the AST root of the entire file; partial subtrees may produce incorrect line numbers.
- Deduplication of names within a group is done by membership check, so the first occurrence wins.

---

## `_detect_module_alias`

**Signature:**
```
_detect_module_alias(
    module_node: Node,
    import_nodes: list[Node],
) -> str | None
```

**Responsibility:** Extracts the local alias assigned to an entire module (the `Y` in `import X as Y`) from the AST, covering at least Python and Kotlin syntax.

**When to use:** Called once per query match inside `extract_imports` to populate `ImportInfo.module_alias`.

**Design decisions:**
- Handles two distinct AST shapes under a single function: Python exposes the alias through the parent node's `alias` field, while Kotlin exposes it through a dedicated `import_alias` child of the import node containing a `simple_identifier` or `identifier` child.
- Returns `None` rather than raising when neither pattern matches, keeping the caller's logic uniform.

**Constraints & edge cases:**
- Returns `None` if `import_nodes` is empty and the Python parent-node pattern does not match.
- Assumes at most one alias per import statement.

---

## `_resolve_imported_name`

**Signature:**
```
_resolve_imported_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the name as it is actually referenced in code after the import, preferring the alias over the original when one is present.

**When to use:** Called for every `@name` capture node to determine what string should be stored in `ImportInfo.names`.

**Design decisions:**
- Covers three distinct AST patterns under one function: Python `aliased_import` nodes (checked by node type), JS/TS `import_specifier`/`export_specifier` nodes (checked via the parent node), and all other nodes (raw text fallback).
- Always returns a non-`None` string in practice (the raw node text serves as the unconditional fallback), though the return type is declared `str | None` for consistency with the module.

**Constraints & edge cases:**
- For JS/TS specifiers, relies on the parent node being typed `import_specifier` or `export_specifier`; names captured outside that parent shape fall through to the raw-text path.

---

## `_get_original_name`

**Signature:**
```
_get_original_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the name as defined in the exporting module (before any `as` alias), or `None` when no alias is present—distinguishing the "aliased" case from the "plain import" case for `ImportInfo.alias_map` construction.

**When to use:** Called alongside `_resolve_imported_name` for every `@name` capture to determine whether an `alias_map` entry is needed.

**Design decisions:**
- Intentionally returns `None` for unaliased imports (rather than the name itself) so the caller can use the return value as a boolean sentinel to decide whether to write an `alias_map` entry.
- Mirrors `_resolve_imported_name` in structure but inverts the priority: original name is returned only when an alias is confirmed to exist.

**Constraints & edge cases:**
- For Python `aliased_import` nodes without an `alias` field, returns `None` even though the node type is `aliased_import`.
- For JS/TS specifiers, confirms alias presence via the parent's `alias` field, then returns `name_node.text` as the original; the alias text itself is obtained separately by `_resolve_imported_name`.

---

## `_strip_quotes`

**Signature:**
```
_strip_quotes(text: str) -> str
```

**Responsibility:** Normalizes a raw module string captured from the AST by removing surrounding quotation marks or angle brackets, producing a bare module path usable for resolution.

**When to use:** Applied to every `@module` capture immediately after decoding, before the module string is stored in `ImportInfo.module`.

**Design decisions:**
- Handles three delimiters: double quotes, single quotes, and `<>`/angle brackets, covering JS/TS string literals and C/C++ `#include` paths in a single function.
- Returns the input unchanged for languages (Python, Java, Kotlin, etc.) that represent module paths as bare identifiers with no surrounding delimiters.

**Constraints & edge cases:**
- Only strips when the string is at least two characters long; single-character strings are returned as-is.
- Does not handle mismatched delimiters (e.g. `"foo'`); such inputs are returned unchanged.
- Strips exactly one layer of delimiters; nested or escaped quotes are not processed.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports in the source code are from the standard library (`dataclasses`) and the third-party package `tree_sitter` (`Language`, `Query`, `QueryCursor`, `Node`), which are excluded from this description.

## Dependents (modules that import this file)

Three project-internal modules import `extract_imports` from this file:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to parse import statements from a file's AST, feeding the results into `build_symbol_to_file_map` to construct an "imported name → dependency file" mapping for analysis.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to retrieve the import list of a caller file's AST, enabling resolution of which symbols a caller file imports during usage analysis.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to enumerate import statements from each file's AST, then resolves each `ImportInfo.module` to a project-internal path via `resolve_module_to_project_path` to build the dependency graph's callee edges.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → this module (one-way)
- `codetwine/extractors/usage_analysis.py` → this module (one-way)
- `codetwine/extractors/dependency_graph.py` → this module (one-way)

This module itself has no project-internal imports, making it a leaf node in the internal dependency graph.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|-------|------|-------------|
| `root_node` | `Node` | The root node of a tree-sitter AST covering an entire source file |
| `language` | `Language` | A tree-sitter `Language` object used to compile the query |
| `import_query_str` | `str \| None` | An S-expression query string defining capture patterns for import syntax; when `None`, processing is skipped entirely |

The module receives no file I/O or configuration values directly. All inputs are passed as arguments by callers in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.

---

## 2. Transformation Overview

```
import_query_str ──► compile Query + attach QueryCursor
                                │
root_node ──────────────────────┤
                                ▼
                     cursor.matches(root_node)
                     (raw pattern match results)
                                │
                                ▼
                     filter: @_require_func != "require" → skip
                                │
                                ▼
                     extract @module, @name, @import_node captures
                     strip quotes from module text
                     determine line number
                                │
                                ▼
                     group by (module, line) key
                     → create or update ImportInfo entry
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
           detect module alias       accumulate @name captures
           (import X as Y)           resolve alias vs. original name
           → set module_alias        → append to names / alias_map
                    │                       │
                    └───────────┬───────────┘
                                ▼
                     detect wildcard child nodes (asterisk / *)
                     → append "*" to names if present
                                │
                                ▼
                     grouped.values() → list[ImportInfo]
```

**Stage 1 — Query compilation and matching:** A `Query` is compiled from `import_query_str` against the provided `Language`. A `QueryCursor` runs the query over `root_node`, producing a sequence of pattern matches, each containing named captures.

**Stage 2 — CommonJS filtering:** Matches that include a `@_require_func` capture where the function name is not `"require"` are discarded, preventing non-require call expressions from being treated as imports.

**Stage 3 — Capture extraction and normalization:** From each surviving match, `@module`, `@name`, and `@import_node` captures are retrieved. The module text is stripped of surrounding quotes or angle brackets. The line number is taken from `@import_node` if present, otherwise from `@module`.

**Stage 4 — Grouping:** A `(module, line)` tuple serves as a grouping key. Multiple matches that share the same key (e.g., a single `from X import A, B` statement generating two `@name` captures) are merged into a single `ImportInfo` entry.

**Stage 5 — Alias and name resolution:** For each match, `_detect_module_alias` checks whether the module node or import node carries an `as Y` alias. For each `@name` capture, `_resolve_imported_name` returns the name as used in code (the alias side if one exists), and `_get_original_name` returns the pre-alias name only when an alias is actually present. The `alias_map` is populated only when alias and original differ.

**Stage 6 — Wildcard detection:** The children of `@import_node` are inspected for nodes typed `"asterisk"` or `"*"`, and `"*"` is appended to `names` when found.

---

## 3. Outputs

| Output | Type | Description |
|--------|------|-------------|
| Return value of `extract_imports` | `list[ImportInfo]` | One `ImportInfo` per distinct `(module, line)` pair found in the file |

The module produces no file writes and no side effects. All results are returned as values to the caller.

---

## 4. Key Data Structures

### `ImportInfo` (dataclass)

| Field | Type | Purpose |
|-------|------|---------|
| `module` | `str` | The import source after quote stripping (e.g., `"react"`, `os.path`) |
| `names` | `list[str]` | Names imported from the module (e.g., `["useState", "useEffect"]`); empty for bare module imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias `Y` from `import X as Y`; `None` when no alias is present |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for `from X import a as b` patterns; `None` when no name-level aliases exist |

### `grouped` (intermediate dict)

| Key | Type | Purpose |
|-----|------|---------|
| `(module, line)` | `tuple[str, int]` | Unique identity of one import statement; used to merge multiple `@name` captures from the same statement |
| value | `ImportInfo` | The accumulating result for that import statement |

### `captures` (per-match dict from tree-sitter)

| Key | Type | Purpose |
|-----|------|---------|
| `"module"` | `list[Node]` | Nodes matching the `@module` capture (import source) |
| `"name"` | `list[Node]` | Nodes matching the `@name` capture (individually imported names) |
| `"import_node"` | `list[Node]` | Nodes matching the `@import_node` capture (entire import statement) |
| `"_require_func"` | `list[Node]` | Nodes matching the CommonJS function name capture; used only for filtering |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** policy. Rather than raising exceptions on unexpected or missing data, functions return empty results or `None` to signal absence. The caller is shielded from partial or malformed AST data by skipping invalid entries silently and continuing processing. No exceptions are explicitly raised or caught within the module; the design assumes tree-sitter provides well-formed nodes and delegates responsibility for handling a `None` query string to an early-exit guard.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| No query string provided | `import_query_str` is `None` or empty | Returns an empty list immediately | Yes | No imports extracted; caller receives `[]` |
| Missing `@module` capture | A query match contains no `module` capture group | Entry is skipped via `continue` | Yes | That match is silently dropped; other matches proceed normally |
| Non-`require` function call (CommonJS) | `_require_func` capture exists but its text is not `"require"` | Match is skipped via `continue` | Yes | Non-require call expressions are excluded from results |
| Missing alias field on aliased node | `alias` child field is absent on an `aliased_import` or `import_specifier` node | Falls back to the `name` field or the node's raw text | Yes | Name is resolved without alias information; no data loss for non-aliased imports |
| Missing `name` field on aliased node | `name` child field is absent on an `aliased_import` node | Returns the full node text as fallback | Yes | Module or name string may be slightly over-inclusive but processing continues |
| Missing `import_node` capture | `import_nodes` list is empty | Falls back to the `module` node's start point for line number | Yes | Line number is derived from the module token rather than the full statement |
| Unquoted or unrecognized quote style | Module text does not begin and end with recognized delimiters | `_strip_quotes` returns the text unchanged | Yes | Module name retains its raw form; no crash |
| Duplicate `@name` capture within a group | The same name appears more than once for the same `(module, line)` key | Duplicate is skipped via membership check before appending | Yes | Each name is recorded only once; no data corruption |
| No wildcard child on import node | Import node children contain no `asterisk` or `*` typed node | Wildcard detection loop exits without appending `"*"` | Yes | Wildcard is not added; normal imports are unaffected |

---

## 3. Design Notes

- **No exception raising**: The module contains no `raise` statements. All unexpected or absent data conditions are resolved through `None` returns, empty-list returns, or `continue` statements, keeping the extraction pipeline uninterrupted.
- **Guard-at-entry pattern**: The `None`/empty check on `import_query_str` at the top of `extract_imports` acts as the sole explicit gate, preventing unnecessary query construction for unsupported languages.
- **Defensive field access**: All tree-sitter node field accesses (`child_by_field_name`, `.parent`, `.children`) are guarded by truthiness checks before use, preventing attribute errors on nodes that lack expected children.
- **Deduplication as implicit error mitigation**: The `(module, line)` grouping key and the duplicate-name check serve both a logical grouping purpose and act as safeguards against repeated captures that tree-sitter queries may produce for a single source construct.
- **No logging**: The module performs no logging of skipped or fallback cases, consistent with a library-layer component that expects callers to interpret empty or partial results.

## Summary

**codetwine/extractors/imports.py** extracts structured import information from tree-sitter ASTs using caller-supplied query strings. Public interface: `ImportInfo` (dataclass: `module:str`, `names:list[str]`, `line:int`, `module_alias:str|None`, `alias_map:dict[str,str]|None`) and `extract_imports(root_node:Node, language:Language, import_query_str:str|None) -> list[ImportInfo]`. Groups multiple name captures sharing the same `(module, line)` key into one `ImportInfo`; returns `[]` for absent queries.
