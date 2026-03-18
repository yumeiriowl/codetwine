# Design Document: codetwine/extractors/imports.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts import statement information from a parsed AST and returns structured `ImportInfo` objects that downstream modules use to resolve symbol dependencies across project files.

## 2. When to Use This Module

- **Resolving imported symbols in a target file**: Call `extract_imports(root_node, language, import_query_str)` to obtain the full list of imports declared in a file, then feed the result into `build_symbol_to_file_map` to map imported names to their source files (used in `file_analyzer.py`).
- **Analyzing which names a caller file imports**: Call `extract_imports(caller_root, language, import_query_str)` to determine what a calling file imports before performing usage analysis (used in `usage_analysis.py`).
- **Building a file-level dependency graph**: Call `extract_imports(root_node, language, import_query_str)` on each project file and resolve each returned `ImportInfo.module` to a project path to discover inter-file dependencies (used in `dependency_graph.py`).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | — | Data class holding all parsed details of a single import statement, including the source module, individually imported names, aliases, and line number. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST, groups captures by `(module, line)` key, and returns one `ImportInfo` per import statement with names and aliases consolidated. Returns an empty list when `import_query_str` is `None`. |

## 4. Design Decisions

- **Query capture name contract**: The function depends on a fixed set of capture names (`@module`, `@name`, `@import_node`, `@_require_func`) defined externally in query strings. This decouples language-specific syntax from the extraction logic, allowing the same function to handle Python, JavaScript, Java, Kotlin, and C/C++ by supplying a different query string.
- **Grouping by `(module, line)`**: Multiple `@name` captures from the same `from X import A, B` statement are merged into a single `ImportInfo` rather than producing one entry per name. This keeps the output one-to-one with source import statements.
- **CommonJS filtering via `@_require_func`**: Matches that include a `@_require_func` capture but whose function name is not `"require"` are skipped, allowing the query to broadly match call expressions while still excluding non-require calls.
- **Alias normalization**: `_resolve_imported_name` returns the alias name (the name actually used in code), while `_get_original_name` returns the pre-alias name. When both differ, `alias_map` records the `{alias → original}` mapping so callers can trace aliased references back to their definitions.

## Definition Design Specifications

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

**Signature:** `@dataclass class ImportInfo`

A data container representing a single parsed import statement.

### Fields

| Field | Type | Purpose |
|-------|------|---------|
| `module` | `str` | The resolved module name or path (quotes already stripped) |
| `names` | `list[str]` | Individually imported names (e.g., `Y` in `from X import Y`); empty list for bare module imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias assigned to the entire module (e.g., `Y` in `import X as Y`); `None` when no alias |
| `alias_map` | `dict[str, str] \| None` | Maps alias-as-used-in-code → original definition name for per-name aliases (e.g., `from X import a as b` → `{"b": "a"}`); `None` when no per-name aliases exist |

**Responsibility:** Acts as the canonical output unit for the import extraction pipeline, carrying all alias and naming information needed by callers to resolve symbols to their source modules.

**When to use:** Instantiated exclusively by `extract_imports`; consumed by `build_symbol_to_file_map`, dependency graph builders, and usage analysis to map imported names to project files.

**Design decisions:**
- `names` is always a list (never `None`), even for bare `import X` statements, so callers can iterate unconditionally.
- `alias_map` and `module_alias` are `None` rather than empty containers to signal absence, avoiding false positives in alias-resolution logic.
- Wildcard imports are represented as the string `"*"` in `names`.

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

- `root_node`: The root `Node` of a tree-sitter parse tree for an entire source file.
- `language`: A tree-sitter `Language` object for the file's language; required to compile the query.
- `import_query_str`: A tree-sitter S-expression query string targeting import syntax, or `None` to opt out.
- Returns: A flat list of `ImportInfo` objects, one per unique `(module, line)` pair found in the file.

**Responsibility:** The primary public entry point—scans an AST using a language-specific tree-sitter query and produces structured import records for the entire file.

**When to use:** Called after parsing a source file whenever callers need to enumerate its import dependencies (file analysis, dependency graph construction, usage analysis).

**Design decisions:**

- **Grouping key `(module, line)`:** Multiple `@name` captures from one `from X import Y, Z` statement are merged into a single `ImportInfo` rather than producing separate records, avoiding duplicate module entries.
- **`@_require_func` guard:** Matches for CommonJS `require()` patterns are filtered based on whether the captured function identifier is literally `"require"`, preventing false matches on similarly-shaped call expressions.
- **Fallback line source:** When no `@import_node` capture is present, the line number is derived from the `@module` node itself.
- **Required captures:** A match with no `@module` capture is silently skipped.
- **Wildcard detection:** Performed by inspecting `import_node` children for node types `"asterisk"` or `"*"` rather than via a dedicated query capture, accommodating Java and Kotlin AST differences.

**Constraints & edge cases:**
- Returns an empty list immediately when `import_query_str` is falsy (covers both `None` and empty string).
- Duplicate names within the same group are suppressed; the first occurrence wins.
- `alias_map` is only created on the `ImportInfo` when at least one aliased name is encountered.

---

## `_detect_module_alias`

**Signature:**
```
_detect_module_alias(
    module_node: Node,
    import_nodes: list[Node],
) -> str | None
```

- `module_node`: The tree-sitter node captured by `@module`.
- `import_nodes`: List of nodes captured by `@import_node` (may be empty).
- Returns: The alias string if a whole-module alias is detected; `None` otherwise.

**Responsibility:** Isolates language-specific AST structure differences for detecting `import X as Y` patterns so that `extract_imports` remains language-agnostic.

**When to use:** Called once per query match inside `extract_imports` to populate the `module_alias` field.

**Design decisions:**
- Two separate detection paths exist: one based on the `module_node`'s parent type (Python), and one based on a named child field of the `import_node` (Kotlin). These are checked independently, not as a fallback chain—the Python path runs first unconditionally.
- Kotlin's `import_alias` child may wrap the identifier in another node, so child types `"simple_identifier"` and `"identifier"` are both accepted.

**Constraints & edge cases:**
- Returns `None` when `import_nodes` is empty and the Python path does not match.

---

## `_resolve_imported_name`

**Signature:**
```
_resolve_imported_name(name_node: Node) -> str | None
```

- `name_node`: A tree-sitter node captured by `@name`.
- Returns: The name as it will appear in calling code (the alias if one is present, otherwise the original name). Returns `None` only if the raw text cannot be decoded, which is not expected in practice.

**Responsibility:** Determines the identifier that code in the importing file will actually reference, abstracting over Python aliased imports and JS/TS import/export specifier alias fields.

**When to use:** Called for every `@name` capture inside `extract_imports` to populate `ImportInfo.names`.

**Design decisions:**
- Handles Python `aliased_import` nodes by prioritizing the `alias` field, then falling back to `name`, then to raw node text—so malformed AST nodes degrade gracefully.
- For JS/TS specifiers, alias resolution is delegated upward to the parent node (`import_specifier` / `export_specifier`) rather than the captured node itself.

---

## `_get_original_name`

**Signature:**
```
_get_original_name(name_node: Node) -> str | None
```

- `name_node`: A tree-sitter node captured by `@name`.
- Returns: The original (pre-alias) name string when an alias is detected; `None` when no alias is present.

**Responsibility:** Provides the source-side name needed to populate `ImportInfo.alias_map`, separating the "what is used locally" concern (`_resolve_imported_name`) from the "what was defined remotely" concern.

**When to use:** Called alongside `_resolve_imported_name` inside `extract_imports`; its result is only recorded when it differs from the resolved name.

**Design decisions:**
- Deliberately returns `None` (not the name itself) when no alias exists, so callers can use the return value as a presence check rather than comparing strings.
- For JS/TS specifiers, the original name is the captured `name_node`'s own text, while the alias comes from the parent—the inverse of the `_resolve_imported_name` logic.

---

## `_strip_quotes`

**Signature:**
```
_strip_quotes(text: str) -> str
```

- `text`: A raw module string as captured from the AST.
- Returns: The module string with surrounding `"..."`, `'...'`, or `<...>` delimiters removed; returned unchanged if none of those patterns match.

**Responsibility:** Normalizes module path strings across languages that embed quotes or angle brackets in their AST text representations before any further processing.

**When to use:** Applied to every `@module` capture inside `extract_imports` immediately after decoding the node text.

**Constraints & edge cases:**
- Only outer delimiters are removed; nested quotes or brackets are preserved.
- Strings shorter than two characters are returned as-is without modification.
- Only exact matching pairs are stripped; mixed delimiters (e.g., `"foo'`) are left unchanged.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. All imports in the source code are from the standard library (`dataclasses`) and the third-party package `tree_sitter` (`Language`, `Query`, `QueryCursor`, `Node`), which are excluded from this description.

## Dependents (modules that import this file)

Three project-internal modules depend on this file, each consuming `extract_imports`:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to parse import statements from an AST root node as part of building a symbol-to-file map (`build_symbol_to_file_map`) that resolves imported names to their dependency files.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to obtain the import list (`caller_import_list`) from a caller file's AST, enabling cross-file usage analysis.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to enumerate import statements from a file's AST and resolve each `ImportInfo.module` to a project-internal path via `resolve_module_to_project_path`, thereby constructing the dependency graph's callee edges.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports_py/imports.py` (one-way)
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports_py/imports.py` (one-way)
- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports_py/imports.py` (one-way)

This file acts as a pure leaf-level utility module: it receives AST nodes and language configuration from its callers and returns structured `ImportInfo` data, without importing from any other project-internal module itself.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `Node` | The root node of a tree-sitter AST covering an entire source file |
| `language` | `Language` | A tree-sitter `Language` object used to compile the query |
| `import_query_str` | `str \| None` | An S-expression query string that encodes language-specific import syntax patterns; sourced from an external config |

The module itself performs no file I/O. All data enters through function arguments. The query string is language-specific and is expected to be supplied by the caller (e.g., from `IMPORT_QUERIES` in a config module).

---

## 2. Transformation Overview

```
import_query_str + language
        │
        ▼
  [1] Query compilation
        Query(language, import_query_str)
        │
        ▼
  [2] AST traversal via QueryCursor
        cursor.matches(root_node)
        → stream of (pattern_index, captures) pairs
        │
        ▼
  [3] Per-match extraction & filtering
        - Filter out non-require() CommonJS patterns
        - Extract @module, @name, @import_node capture nodes
        - Decode raw module text → strip quotes/angle brackets
        - Determine line number from @import_node or @module node
        │
        ▼
  [4] Grouping by (module, line)
        Matches sharing the same (module name, line number) are
        merged into a single ImportInfo, accumulating @name captures
        │
        ▼
  [5] Enrichment per group entry
        - Detect and attach module alias (import X as Y)
        - Resolve each @name node to the in-code name (alias if present)
        - Record original→alias mappings in alias_map
        - Detect wildcard imports (*) via import_node children
        │
        ▼
  [6] Output collection
        dict values → list[ImportInfo]
```

**Grouping stage detail:** The intermediate structure `grouped: dict[tuple[str, int], ImportInfo]` accumulates all `@name` captures across matches that share the same `(module, line)` key, so a single `from X import A, B, C` statement produces exactly one `ImportInfo` rather than three.

---

## 3. Outputs

`extract_imports` returns `list[ImportInfo]`. Each element represents one logical import statement found in the file.

This list is consumed by three callers:
- `codetwine/file_analyzer.py` — feeds into `build_symbol_to_file_map` to resolve imported names to project files.
- `codetwine/extractors/usage_analysis.py` — used to match call-site symbols against imports.
- `codetwine/extractors/dependency_graph.py` — each `import_info.module` is resolved to a project path to build dependency edges.

There are no file writes or other side effects.

---

## 4. Key Data Structures

### `ImportInfo` (dataclass — primary output element)

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source after quote/bracket stripping (e.g., `react`, `os.path`) |
| `names` | `list[str]` | Names imported from the module (the `Y` in `from X import Y`); empty list for bare module imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias assigned to the entire module (`Y` in `import X as Y`); `None` when absent |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for aliased name imports (e.g., `{"path_join": "join"}` for `from X import join as path_join`); `None` when no aliased names exist |

### `grouped` (intermediate accumulator)

| Key / Field | Type | Purpose |
|---|---|---|
| Key: `(module, line)` | `tuple[str, int]` | Deduplication key combining the stripped module name and the statement's line number |
| Value | `ImportInfo` | The partially-built `ImportInfo` that accumulates `names` and `alias_map` across multiple matches for the same statement |

### `captures` (per-match data from tree-sitter)

| Key | Type | Purpose |
|---|---|---|
| `"module"` | `list[Node]` | Nodes matching the `@module` capture — the import source |
| `"name"` | `list[Node]` | Nodes matching the `@name` capture — individually imported identifiers |
| `"import_node"` | `list[Node]` | Nodes matching the `@import_node` capture — the full import statement, used for line number and wildcard detection |
| `"_require_func"` | `list[Node]` | Optional capture used to validate that a CommonJS-style call is actually `require()` |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file follows a **graceful degradation / silent-skip** policy. No exceptions are raised or caught explicitly. Instead, invalid or unexpected inputs are handled by returning early with empty results, skipping malformed captures, or falling back to alternative data sources. The module is designed to never terminate the calling process due to a bad input condition.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing query string | `import_query_str` is `None` or empty | Returns an empty list immediately | Yes | No imports extracted; callers receive `[]` |
| Missing `@module` capture | A query match contains no `module` capture | Skips the entire match via `continue` | Yes | That match is silently dropped; other matches proceed normally |
| Non-`require` function in CommonJS pattern | `_require_func` capture exists but its text is not `"require"` | Skips the entire match via `continue` | Yes | Non-require calls are excluded; processing continues |
| Missing `@import_node` capture | No `import_node` capture in a match | Falls back to `module_nodes[0].start_point` for the line number | Yes | Line number is derived from the module node instead; no data loss |
| Node with no alias field | `aliased_import`, `import_specifier`, or `export_specifier` has no alias | Returns only the base name (or `None` for original-name lookup) | Yes | Alias mapping is omitted; the base name is still recorded |
| `alias_map` not yet initialized | First aliased name encountered for a given `ImportInfo` | Lazily initializes `alias_map` to `{}` before inserting | Yes | No data lost; map is created on demand |
| Duplicate `@name` capture | The same name appears more than once for a given group key | Duplicate is skipped via membership check before appending | Yes | Name list remains deduplicated; no error raised |
| Unquoted or non-standard module text | Module text has no surrounding quotes or angle brackets | `_strip_quotes` returns the text unchanged | Yes | Raw text is used as-is; no extraction failure |

---

## 3. Design Notes

- **No exception boundary exists in this module.** All defensive logic is implemented through conditional checks and early returns, meaning errors surface as missing or incomplete data rather than as raised exceptions. Callers such as `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` receive either a partial list or an empty list without any signal that input was abnormal.

- **The `None`-query early-exit** is an explicit design contract: languages without a defined import query are supported by passing `None`, and the module treats this as a valid no-op rather than an error condition.

- **Lazy initialization of `alias_map`** (defaulting to `None` in the dataclass) reflects a deliberate choice to keep the common case (no aliases) lightweight, with the field populated only when aliased imports are actually encountered.

- **Group-key deduplication** (`(module, line)` tuple) serves as the sole mechanism for consolidating multi-name imports. No error is raised if the same key is seen multiple times; the existing entry is simply extended in place.

## Summary

**codetwine/extractors/imports.py**: Extracts import statements from tree-sitter ASTs into structured records.

**Responsibility:** Parses a file's AST using a language-specific query string and returns one `ImportInfo` per import statement.

**Public API:**
- `ImportInfo` (dataclass): `module: str`, `names: list[str]`, `line: int`, `module_alias: str|None`, `alias_map: dict[str,str]|None`
- `extract_imports(root_node: Node, language: Language, import_query_str: str|None) → list[ImportInfo]`

**Key structures:** `ImportInfo` (output); intermediate `dict[tuple[str,int], ImportInfo]` groups captures by `(module, line)`.
