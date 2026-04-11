# Design Document: codetwine/extractors/imports.py

# Overview & Purpose

## 1. Module Summary

Extracts and normalizes import statement information from a parsed AST, returning structured `ImportInfo` objects that downstream modules use to resolve inter-file dependencies.

## 2. When to Use This Module

- **Resolving imports in a single file during analysis** (`file_analyzer.py`): Call `extract_imports(root_node, language, import_query_str)` to obtain a list of `ImportInfo` objects, which are then passed to `build_symbol_to_file_map` to map imported names to their source files.
- **Resolving imports across caller files during usage analysis** (`usage_analysis.py`): Call `extract_imports(caller_root, language, import_query_str)` to retrieve the import list for any caller file being inspected, enabling cross-file symbol tracking.
- **Building a project-wide dependency graph** (`dependency_graph.py`): Call `extract_imports(root_node, language, import_query_str)` for each project file to enumerate its dependencies, which are then resolved to concrete project file paths.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | — | Data container holding all structured information extracted from a single import statement, including aliasing details. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST, groups multiple `@name` captures from the same statement into one `ImportInfo`, and returns the full list of imports found in the file. Returns an empty list when `import_query_str` is `None`. |

## 4. Design Decisions

- **Query-driven, language-agnostic extraction**: Rather than encoding language-specific parsing logic directly, `extract_imports` delegates language differences to the caller-supplied `import_query_str`. The function only requires that captures follow a fixed naming convention (`@module`, `@name`, `@import_node`), making it reusable across all supported languages without modification.
- **Grouping by `(module, line)` key**: When a single import statement produces multiple `@name` captures (e.g., `from X import A, B`), they are consolidated into one `ImportInfo` entry using a `(module string, line number)` tuple as the key, preventing duplicate entries.
- **`_require_func` escape hatch for CommonJS**: Matches that include a `@_require_func` capture are validated to ensure the function name is literally `"require"`, allowing the query to match `require()` call patterns while filtering out lookalike function names without needing a separate query.
- **Alias normalization at extraction time**: Both module-level aliases (`import X as Y`) and name-level aliases (`from X import a as b`) are resolved during extraction. The code-facing name is stored in `names` and the original-to-alias mapping is stored in `alias_map`, so consumers never need to inspect raw AST nodes to perform alias resolution.

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

**Responsibility:** Holds all extracted metadata for a single import statement in a normalized, language-agnostic representation.

**When to use:** Instantiated by `extract_imports` for each unique import statement found in a source file; consumed by callers such as `build_symbol_to_file_map` and dependency graph builders.

### Fields

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source module name or path, with surrounding quotes/brackets already stripped |
| `names` | `list[str]` | Names brought into scope (e.g., the `Y` in `from X import Y`); empty list for import styles that do not name individual symbols |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The local alias for the entire module (the `Y` in `import X as Y`); `None` when no alias exists |
| `alias_map` | `dict[str, str] \| None` | Maps each aliased name to its original name for named imports (e.g., `{"path_join": "join"}`); `None` when no per-name aliases exist |

**Design decisions:**
- `names` uses an empty list rather than `None` to allow uniform iteration regardless of language.
- `alias_map` is initialized to `None` rather than an empty dict to distinguish "no aliases present" from "aliases container exists but is empty," avoiding unnecessary allocations.

---

## `extract_imports`

**Signature:**
```python
def extract_imports(
    root_node: Node,
    language: Language,
    import_query_str: str | None,
) -> list[ImportInfo]
```
- `root_node`: The root `Node` of the parsed AST for an entire source file.
- `language`: A tree-sitter `Language` instance used to compile the query.
- `import_query_str`: A tree-sitter S-expression query string, or `None` for languages without import queries.
- Returns: A flat list of `ImportInfo` objects, one per unique `(module, line)` pair.

**Responsibility:** The primary public entry point; traverses the AST using a tree-sitter query and aggregates all import statements into `ImportInfo` records.

**When to use:** Called by file analyzers, usage analysis, and dependency graph builders whenever import metadata must be extracted from a parsed source file.

**Design decisions:**
- Grouping key `(module, line)` merges multiple `@name` captures from the same import statement (e.g., `from X import A, B`) into a single `ImportInfo` instead of producing one record per name.
- CommonJS `require()` calls are filtered by checking a `@_require_func` capture; matches where the function name is not `"require"` are discarded, preventing false positives from similarly-shaped call expressions.
- Line number is sourced from the `@import_node` capture when available and falls back to the `@module` node, ensuring accurate line attribution across languages where the query may not capture a full statement node.
- Wildcard import detection for Java/Kotlin inspects child node types (`"asterisk"` or `"*"`) on the import statement node rather than relying on query captures, because wildcard syntax varies structurally across those languages.
- Duplicate names within the same import group are suppressed before appending.

**Constraints & edge cases:**
- Returns an empty list immediately when `import_query_str` is `None` or falsy; no AST traversal occurs.
- Entries without a `@module` capture are silently skipped.
- Expected query capture names are specifically `@module`, `@name`, `@import_node`, and `@_require_func`; queries using different capture names will produce no results.

---

## `_detect_module_alias`

**Signature:**
```python
def _detect_module_alias(
    module_node: Node,
    import_nodes: list[Node],
) -> str | None
```
- Returns the alias string if one is found, otherwise `None`.

**Responsibility:** Extracts the local alias assigned to an entire imported module (e.g., `import X as Y`), handling the structural differences between Python and Kotlin ASTs.

**When to use:** Called internally by `extract_imports` for each match to populate `ImportInfo.module_alias`.

**Design decisions:**
- Two separate code paths handle Python (alias detected via the parent node of `@module` being an `aliased_import`) and Kotlin (alias detected via a child field named `"alias"` on the import statement node), because the AST structures differ fundamentally between the two languages.

**Constraints & edge cases:**
- Only handles Python and Kotlin alias patterns; other languages are not covered by this function.
- Returns `None` if neither language-specific pattern matches.

---

## `_resolve_imported_name`

**Signature:**
```python
def _resolve_imported_name(name_node: Node) -> str | None
```
- Returns the name string as it appears in the importing file's code scope.

**Responsibility:** Resolves the effective (post-alias) name for a single `@name` capture, returning the alias when one is present and the original name otherwise.

**When to use:** Called internally by `extract_imports` to determine which name string to store in `ImportInfo.names`.

**Design decisions:**
- Handles two structurally distinct alias patterns: Python's `aliased_import` node (resolved via field names `alias` and `name`) and JavaScript/TypeScript's `import_specifier`/`export_specifier` nodes (resolved via the parent node's `alias` field).
- Falls back to the raw node text when neither pattern applies, making the function safe to call for any `@name` node regardless of language.

**Constraints & edge cases:**
- For Python `aliased_import` nodes, if neither the `alias` nor `name` field is found, the entire node's text is returned as a last resort.

---

## `_get_original_name`

**Signature:**
```python
def _get_original_name(name_node: Node) -> str | None
```
- Returns the original (pre-alias) name string, or `None` if no alias exists for this node.

**Responsibility:** Extracts the name as defined in the exporting module when an alias is present, enabling `alias_map` population in `ImportInfo`.

**When to use:** Called internally by `extract_imports` alongside `_resolve_imported_name` to build the `alias_map` entry only when an alias differs from the original name.

**Design decisions:**
- Returns `None` (rather than the name itself) when no alias is present, so callers can distinguish "aliased" from "not aliased" without comparing two strings. This is complementary to `_resolve_imported_name`, which always returns a usable name.
- Covers the same two structural patterns as `_resolve_imported_name` (Python `aliased_import`, JS/TS specifier nodes).

**Constraints & edge cases:**
- For JS/TS specifier nodes, the original name is taken from the `name_node` text directly rather than from a dedicated field, relying on the assumption that `@name` captures the left-hand identifier inside the specifier.
- Returns `None` for any node type not matching the two recognized patterns.

---

## `_strip_quotes`

**Signature:**
```python
def _strip_quotes(text: str) -> str
```
- Returns the input string with enclosing `"..."`, `'...'`, or `<...>` delimiters removed.

**Responsibility:** Normalizes raw module strings captured from the AST by removing language-specific quoting so that all callers work with bare module names or paths.

**When to use:** Called internally by `extract_imports` immediately after decoding the raw `@module` node text.

**Design decisions:**
- Handles three delimiter styles—double quotes, single quotes, and angle brackets—covering JavaScript/TypeScript string literals and C/C++ `#include` path forms.
- Languages that use unquoted module identifiers (Python, Java, Kotlin) pass through unchanged.

**Constraints & edge cases:**
- Requires the string to be at least 2 characters long before any stripping is attempted; single-character or empty strings are returned as-is.
- Only outer delimiters are removed; nested quotes within the module string are not modified.

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports in `codetwine/extractors/imports.py` are sourced exclusively from the standard library (`dataclasses`) and the third-party package `tree_sitter` (`Language`, `Query`, `QueryCursor`, `Node`). No internal project modules are imported.

## Dependents (modules that import this file)

Three internal modules depend on `codetwine/extractors/imports.py`, each consuming the `extract_imports` function:

- **`codetwine/file_analyzer.py`** → `codetwine/extractors/imports.py` : Uses `extract_imports` to parse import statements from a file's AST root node, feeding the results into `build_symbol_to_file_map` to construct an imported-name-to-dependency-file mapping for the target file being analyzed.

- **`codetwine/extractors/usage_analysis.py`** → `codetwine/extractors/imports.py` : Uses `extract_imports` to retrieve the import list from a caller file's AST root node, enabling downstream usage analysis to understand what external symbols the caller brings into scope.

- **`codetwine/extractors/dependency_graph.py`** → `codetwine/extractors/imports.py` : Uses `extract_imports` to enumerate import statements from each file's AST, then resolves each `ImportInfo.module` to a project-internal path via `resolve_module_to_project_path` in order to build callee edges in the dependency graph.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` (one-way)
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` (one-way)
- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports.py` (one-way)

`codetwine/extractors/imports.py` does not import from any of these modules; it acts purely as a provider of import-extraction functionality with no back-references to its dependents.

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `Node` | The root node of a tree-sitter AST representing a fully parsed source file |
| `language` | `Language` | A tree-sitter `Language` object used to compile the query string |
| `import_query_str` | `str \| None` | An S-expression query string defining capture patterns for import syntax; sourced from `IMPORT_QUERIES` in `config.py` |

The module receives no file I/O or configuration reads directly. All inputs are passed as arguments from callers in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.

---

## 2. Transformation Overview

```
import_query_str + language
        │
        ▼
  Compile Query → QueryCursor
        │
        ▼
  cursor.matches(root_node)
  (walk the AST, yield (pattern_index, captures) pairs)
        │
        ▼
  Per-match filtering and extraction
  ├── Skip if @_require_func exists and is not "require"
  ├── Skip if no @module capture
  ├── Decode and strip quotes from @module text → module name
  ├── Resolve line number from @import_node (or fallback to @module node)
  └── Form group key: (module, line)
        │
        ▼
  Grouping into `grouped` dict keyed by (module, line)
  ├── Create new ImportInfo if key is unseen
  ├── Detect module alias (import X as Y) via _detect_module_alias
  ├── For each @name node:
  │   ├── _resolve_imported_name → alias name used in code
  │   ├── _get_original_name    → original pre-alias name (if aliased)
  │   ├── Append alias name to ImportInfo.names (deduplication check)
  │   └── Populate ImportInfo.alias_map if alias differs from original
  └── Detect wildcard imports (* child node) and append "*" to names
        │
        ▼
  list(grouped.values())
  → list[ImportInfo]
```

Multiple `@name` captures from the same import statement (e.g., `from X import A, B`) are merged into a single `ImportInfo` entry via the `(module, line)` grouping key. There is no async or parallel processing; the pipeline is sequential.

---

## 3. Outputs

The sole output of `extract_imports` is:

| Output | Type | Description |
|---|---|---|
| Return value | `list[ImportInfo]` | One `ImportInfo` per distinct import statement found in the file, with all imported names consolidated |

There are no file writes or side effects. An empty list is returned when `import_query_str` is `None` or empty. Callers use the returned list to build symbol-to-file maps, resolve dependencies, and analyse cross-file usage.

---

## 4. Key Data Structures

### `ImportInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source path or module name, with surrounding quotes/angle brackets stripped |
| `names` | `list[str]` | Names imported from the module (the `Y` in `from X import Y`); empty list for whole-module imports or languages without selective import syntax |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias assigned to the entire module (`Y` in `import X as Y`); `None` when no alias is present |
| `alias_map` | `dict[str, str] \| None` | Maps each alias name to its original name for aliased name imports (e.g., `{"path_join": "join"}` for `from X import join as path_join`); `None` when no aliased names exist |

### `grouped` (internal accumulation dict)

| Key | Type | Purpose |
|---|---|---|
| `(module, line)` | `tuple[str, int]` | Composite key ensuring one `ImportInfo` per distinct import statement, using the stripped module name and its 1-based line number |
| value | `ImportInfo` | The partially-built `ImportInfo` that accumulates `@name` captures across multiple query matches for the same statement |

### `captures` (per query match)

| Key | Type | Purpose |
|---|---|---|
| `"module"` | `list[Node]` | Nodes representing the import source (module path or name) |
| `"name"` | `list[Node]` | Nodes representing individual imported names within the statement |
| `"import_node"` | `list[Node]` | Nodes representing the entire import statement, used for accurate line number extraction |
| `"_require_func"` | `list[Node]` | Nodes representing the called function name, used to filter out non-`require` calls in CommonJS patterns |

# Error Handling

## 1. Overall Strategy

The file follows a **graceful degradation / logging-and-continue** strategy. No exceptions are raised or propagated to callers. Instead, invalid or unrecognizable inputs are silently skipped, and partial results are returned. The function always returns a list (possibly empty), ensuring that all three dependents (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) receive a safe, iterable result regardless of input quality.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing import query string | `import_query_str` is `None` or empty | Returns an empty list immediately | Yes | No imports extracted for that language; callers receive `[]` |
| Missing `@module` capture | A query match contains no `module` capture | The entire match is skipped via `continue` | Yes | That import statement is omitted from results |
| Non-`require` function call (CommonJS) | `@_require_func` capture exists but its text is not `"require"` | The match is skipped via `continue` | Yes | Non-require call-expression matches are excluded from results |
| Missing alias field on aliased node | `child_by_field_name("alias")` or `child_by_field_name("name")` returns `None` | Falls back to the next resolution path or returns `None` | Yes | Alias or original name is not recorded; the base name may still be captured |
| Missing `@import_node` capture | No `import_node` capture in a match | Line number falls back to the `@module` node's start position | Yes | Line number is approximate but still recorded |
| Unquoted or unconventionally quoted module string | `_strip_quotes` receives a string not wrapped in `"`, `'`, or `<>` | String returned as-is without modification | Yes | Module name is used verbatim; no data loss |
| Duplicate `@name` captures for the same import | Same name appears more than once in `name_nodes` for a given group key | Duplicate check (`alias_name not in grouped[group_key].names`) prevents re-insertion | Yes | No duplicate entries in the `names` list |
| No wildcard child node found | `import_nodes[0].children` contains no node of type `"asterisk"` or `"*"` | Loop completes without appending `"*"` | Yes | Wildcard is simply not recorded; no error |

---

## 3. Design Notes

- **No exception raising**: The entire module is free of `try/except` blocks and explicit `raise` statements. All defensive logic is expressed through guard clauses (`if not ...`, `continue`) and `None`-returning helpers, keeping error handling implicit and non-disruptive.
- **Caller contract preserved**: Because `extract_imports` always returns `list[ImportInfo]`, all three dependents can safely iterate over the result without their own error handling for this call site.
- **Partial result preference**: When a single capture within a match is missing or malformed, only that match (or sub-element) is skipped. Already-grouped results from prior matches are unaffected, so the output reflects as much valid data as the AST provides.
- **Responsibility boundary**: No validation of the `Language` object or the query string's syntactic correctness is performed. If `Query(language, import_query_str)` raises internally, that exception propagates to the caller unchanged — the file does not attempt to catch tree-sitter-level failures.

# Summary

**`codetwine/extractors/imports.py`**: Extracts and normalizes import statements from a parsed AST into structured records.

**Public interface:**
- `ImportInfo` (dataclass): `module: str`, `names: list[str]`, `line: int`, `module_alias: str|None`, `alias_map: dict[str,str]|None`
- `extract_imports(root_node: Node, language: Language, import_query_str: str|None) → list[ImportInfo]`

**Key data:** Produces `list[ImportInfo]` grouped by `(module, line)` tuple; consumes tree-sitter `Node` and `Language` objects plus a query string with captures `@module`, `@name`, `@import_node`, `@_require_func`.
