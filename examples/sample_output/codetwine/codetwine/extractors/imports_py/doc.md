# Design Document: codetwine/extractors/imports.py

# Overview & Purpose

## Role and Responsibilities

This file provides a language-agnostic mechanism for extracting import/dependency statements from source code ASTs. It exists as a separate module to decouple the *generic* logic of import extraction (grouping, alias resolution, quote stripping) from the *language-specific* details, which are externalized as tree-sitter query strings (defined elsewhere, e.g. `IMPORT_QUERIES` in `config.py`).

By relying on tree-sitter's S-expression query syntax with standardized capture names (`@module`, `@name`, `@import_node`), this module allows callers such as `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to obtain a uniform `ImportInfo` representation regardless of the target language (Python, JavaScript/TypeScript, Java, Kotlin, C/C++, etc.), without needing to know each language's grammar quirks. This centralization avoids duplicating import-parsing logic across the multiple consumers that need to resolve module dependencies and imported symbol names.

## Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `ImportInfo` (dataclass) | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None = None`, `alias_map: dict[str, str] \| None = None` | — | Data container representing a single (possibly consolidated) import statement, including source module, imported names, line number, module-level alias, and name-level alias mapping. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs the given tree-sitter import query against the AST, groups matches by `(module, line)`, resolves aliases and wildcard imports, and returns a consolidated list of `ImportInfo` objects; returns `[]` if no query is provided. |
| `_detect_module_alias` | `module_node: Node`, `import_nodes: list[Node]` | `str \| None` | Detects a module-level alias (`import X as Y`) for Python (`aliased_import` parent) or Kotlin (`import_alias` child). |
| `_resolve_imported_name` | `name_node: Node` | `str \| None` | Returns the name actually used in code (post-alias) for a `@name` capture, handling Python `aliased_import` and JS/TS `import_specifier`/`export_specifier` alias fields. |
| `_get_original_name` | `name_node: Node` | `str \| None` | Returns the pre-alias original name for a `@name` capture, or `None` if no alias exists. |
| `_strip_quotes` | `text: str` | `str` | Removes surrounding quotes (`"`/`'`) or angle brackets (`<>`) from a raw module string, leaving unquoted names (e.g., Python, Java) unchanged. |

## Design Decisions

- **Query-driven abstraction**: Instead of writing per-language parsing branches for import statement structure, the module delegates syntax matching to tree-sitter queries supplied by the caller, and standardizes on three capture names (`@module`, `@name`, `@import_node`) as the contract between query authors and this extraction logic.
- **Grouping/consolidation strategy**: Uses a `(module, line)` key to merge multiple `@name` captures belonging to the same import statement (e.g., `from X import Y, Z`) into a single `ImportInfo`, avoiding duplicate entries per imported name.
- **Special-case filtering**: Includes a targeted filter for CommonJS `require()` calls (via `@_require_func` capture) to exclude non-`require` function calls that might otherwise match a generic call pattern.
- **Alias handling separation**: Alias resolution is split into two distinct concerns — the name currently used in code (`_resolve_imported_name`) versus its original pre-alias name (`_get_original_name`) — allowing `alias_map` to record only genuine renames.
- **Wildcard import support**: Explicitly checks for `asterisk`/`*` child node types under the import node to represent Java/Kotlin wildcard imports (`import pkg.*`) as a `"*"` entry in `names`.

# Definition Design Specifications

## `ImportInfo`

A dataclass representing a single normalized import statement, independent of source language syntax.

- `module: str` — the import source (module name, file path, or header name).
- `names: list[str]` — names explicitly imported from the module (empty when the language has no such concept, e.g. plain `import X`).
- `line: int` — 1-based line number of the import statement, used for downstream reporting/tracing.
- `module_alias: str | None` — the alias bound to the whole module (the `Y` in `import X as Y`).
- `alias_map: dict[str, str] | None` — maps an aliased imported name back to its original name (e.g. `{"path_join": "join"}`), allowing consumers to resolve renamed symbols back to their real definitions.

This structure exists to give a single uniform shape for import data across multiple languages (Python, JS/TS, Java, Kotlin, C/C++, CommonJS), so downstream consumers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) don't need language-specific handling.

## `extract_imports`

- Arguments: `root_node` (AST root of the parsed file), `language` (tree-sitter `Language` used to compile the query), `import_query_str` (a tree-sitter S-expression query string, or `None`).
- Returns: `list[ImportInfo]`, one entry per distinct import statement (grouped by module + line), consolidating multiple imported names from the same statement.

This function is the single entry point for turning raw AST nodes into structured import data, decoupling all downstream logic (dependency resolution, usage analysis, symbol mapping) from tree-sitter query mechanics and language-specific grammar differences.

Design decisions:
- Grouping key is `(module, line)` rather than just `module`, since a file may import from the same module multiple times on different statements, and each occurrence should be tracked with its own line number.
- Query captures are standardized to `@module`, `@name`, and `@import_node`, allowing one function to handle many languages by only varying the query string (defined externally in `config.py`), not the extraction logic.
- A special `@_require_func` capture is supported to filter CommonJS `require(...)` calls, ensuring only actual `require` invocations are treated as imports (avoiding false positives from other function calls with a similar call pattern).
- Wildcard imports (Java `import pkg.*`, Kotlin `import pkg.*`) are normalized into a synthetic `"*"` entry in `names`, so callers can detect wildcard imports without needing language-specific AST knowledge.
- Returns an empty list immediately when `import_query_str` is `None`, supporting languages for which no import query has been defined, without raising errors.

Edge cases/constraints:
- Requires `module_nodes` to be non-empty for a match to be processed; matches without a `@module` capture are skipped.
- Duplicate `@name` values within the same group are not re-added to `names`.
- Line number falls back to the `@module` node's line if no `@import_node` capture is present.

## `_detect_module_alias`

- Arguments: `module_node` (the node captured by `@module`), `import_nodes` (list of nodes captured by `@import_node`).
- Returns: `str | None`, the alias name bound to the whole imported module, or `None` if the import has no module-level alias.

Exists to isolate the two distinct language-specific alias representations (Python's `aliased_import` parent node vs. Kotlin's `import_alias` child) behind one interface, since alias location in the AST differs structurally between them.

Design decisions/constraints:
- Checks the Python-style pattern first (module node's parent is `aliased_import`), then falls back to checking the Kotlin-style pattern (alias child under `import_nodes[0]`), since only one of the two can realistically apply to a given match.
- Assumes at most one alias exists per import node; returns the first match found.

## `_resolve_imported_name`

- Argument: `name_node` (the node captured by `@name`).
- Returns: `str | None` (in practice always a string; used as the effective, alias-resolved name to record in `ImportInfo.names`).

Provides the "as-used-in-code" name for an imported symbol, since downstream consumers need to match usages against the name actually available in the importing file's scope, not necessarily the name as defined in the source module.

Design decisions:
- Handles Python's `aliased_import` node shape distinctly (alias field takes priority, falling back to name field, then raw text) because Python represents `import a as b` as a single node type shared between aliased and non-aliased contexts depending on grammar version/fallback needs.
- Handles JS/TS `import_specifier`/`export_specifier` parents by preferring the `alias` field if present.
- Falls back to the raw node text when no alias structure is detected, covering languages without import aliasing syntax for individual names.

## `_get_original_name`

- Argument: `name_node` (the node captured by `@name`).
- Returns: `str | None` — the pre-alias name, or `None` when there is no alias (avoiding redundant duplication of the same value already captured by `_resolve_imported_name`).

Exists to support the `alias_map` field, letting downstream code trace a locally-used alias back to the symbol's original name for definitions lookup across files.

Design decisions/constraints:
- Mirrors the same node-shape checks as `_resolve_imported_name` (Python `aliased_import`, JS/TS `import_specifier`/`export_specifier`) but returns `None` instead of the node's own text when no alias is found, explicitly signaling "no aliasing occurred" rather than returning a name equal to the resolved name.

## `_strip_quotes`

- Argument: `text` (raw string captured from the `@module` node, potentially still containing language-specific delimiters).
- Returns: `str` — the module path/name with surrounding quotes or angle brackets removed.

Normalizes module identifiers across languages that quote or bracket their import paths (JS/TS string literals, C/C++ angle-bracket or quoted includes), so that `ImportInfo.module` is a bare, comparable path string regardless of source language.

Constraints:
- Only strips matching pairs of `"`, `'`, or `<`/`>` when both the first and last character match the expected delimiter pair; otherwise returns the text unchanged (covers languages like Python/Java where module names are bare identifiers).
- Requires `len(text) >= 2` to attempt stripping, avoiding index errors on single-character or empty input.

# Dependency Description

## Dependencies (what this file uses)

This file has no project-internal file dependencies. It relies solely on `tree_sitter` (an external library, excluded per instructions) for parsing and querying ASTs via `Language`, `Query`, `QueryCursor`, and `Node`. All logic for detecting aliases, resolving names, and stripping quotes is self-contained within this module.

## Dependents (what uses this file)

The `extract_imports` function is a shared utility consumed by multiple modules within the project:

- **`codetwine/file_analyzer.py`**: Uses `extract_imports` to parse import statements from a target file's AST, then feeds the resulting `ImportInfo` list into `build_symbol_to_file_map` to construct a mapping from imported symbol names to their originating dependency files.
- **`codetwine/extractors/usage_analysis.py`**: Uses `extract_imports` to obtain the list of imports made by a "caller" file, enabling further analysis of how imported symbols are used within that caller.
- **`codetwine/extractors/dependency_graph.py`**: Uses `extract_imports` to extract import statements from a file and resolve each imported module to a project-internal file path, building edges in a dependency graph between files.

**Direction of dependency**: Unidirectional. This file (`imports.py`) has no knowledge of or dependency on `file_analyzer.py`, `usage_analysis.py`, or `dependency_graph.py`. These three modules depend on `imports.py` for import-extraction functionality, but `imports.py` does not depend on any of them.

# Data Flow

## Input
| Source | Type | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | AST root of the parsed source file (produced upstream by `parse_file`) |
| `language` | `tree_sitter.Language` | Grammar object used to compile the query |
| `import_query_str` | `str \| None` | S-expression query string from `IMPORT_QUERIES` config, per language; `None` disables extraction |

## Main Transformation Flow

```
import_query_str + language
        │
        ▼
  Query + QueryCursor  ──cursor.matches(root_node)──▶  stream of (pattern_id, captures)
        │
        ▼
 For each match's captures dict:
   1. Filter CommonJS false-positives via @_require_func (must equal "require")
   2. Extract @module → strip quotes/brackets → module name
   3. Extract @import_node (or fallback @module) → line number
   4. Build group_key = (module, line)
   5. Get-or-create ImportInfo in `grouped` dict
   6. Detect module alias (Python aliased_import / Kotlin import_alias)
   7. For each @name capture:
        - resolve alias-used name  (_resolve_imported_name)
        - resolve original pre-alias name (_get_original_name)
        - append alias name to .names (dedup)
        - if original differs, record in .alias_map
   8. Detect wildcard import (Java "asterisk" / Kotlin "*") → append "*" to .names
        │
        ▼
   grouped: dict[(module, line) -> ImportInfo]
        │
        ▼
        list(grouped.values())
```

The core transformation is **grouping**: multiple tree-sitter query matches (one per captured name) belonging to the same physical import statement (identified by `(module, line)`) are folded into a single `ImportInfo`, accumulating names and alias metadata.

## Output
- **Type:** `list[ImportInfo]`
- **Destination:** Consumed by downstream analyzers:
  - `file_analyzer.py` → feeds `build_symbol_to_file_map` to map imported symbols to project files
  - `usage_analysis.py` → builds caller-side import lists to resolve cross-file symbol usage
  - `dependency_graph.py` → resolves `import_info.module` to project file paths to build dependency edges

## Key Data Structures

### `ImportInfo` (dataclass, output unit)
| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | Import source (module/path), quotes/brackets stripped |
| `names` | `list[str]` | Imported names as used in code (aliases if present); `["*"]` for wildcard imports |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | Alias for whole-module import (`import X as Y`) |
| `alias_map` | `dict[str, str] \| None` | Maps used alias name → original name, only for aliased individual imports |

### `grouped` (internal working structure)
| Key | Value | Purpose |
|---|---|---|
| `(module: str, line: int)` | `ImportInfo` | Deduplicates/merges multiple query captures belonging to the same import statement before final list conversion |

### `captures` (per-match, from tree-sitter)
| Capture name | Meaning |
|---|---|
| `@module` | Module/source path node |
| `@name` | Individually imported name node (may repeat per match) |
| `@import_node` | Whole import statement node (for line number and alias/wildcard detection) |
| `@_require_func` | Function name node used to filter genuine CommonJS `require()` calls |

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** strategy throughout. There are no explicit `try/except` blocks anywhere in the file; instead, robustness is achieved through defensive conditional checks (guard clauses) before accessing potentially missing data. When required data (captures, fields, nodes) is absent, the code silently skips the item, returns `None`, or returns an empty list rather than raising exceptions. This design assumes that malformed or partial AST matches are a normal occurrence (due to variance across language grammars) rather than an exceptional condition, and prioritizes returning partial/best-effort results over halting execution.

## Main Error Patterns and Handling Policies

| Error Type / Condition | Handling Policy | Impact |
|---|---|---|
| `import_query_str` is `None` (language has no import query defined) | Immediately return an empty list (`[]`) before any query construction | No imports extracted for that language; caller receives empty list, no crash |
| `@module` capture missing in a query match | `continue` to skip that match entirely | That match is silently discarded; no `ImportInfo` created for it |
| `@_require_func` capture present but function name is not `"require"` | `continue` to skip that match | Filters out false-positive CommonJS-style call patterns that aren't actual `require()` imports |
| `@import_node` capture missing | Falls back to using `module_nodes[0].start_point[0]` for line number | Line number still computed; no exception raised |
| No alias found (`_detect_module_alias`, `_get_original_name`) | Return `None` | Caller treats absence of alias as "no alias"; `module_alias`/`alias_map` remain unset |
| `alias` field missing on `aliased_import` node (Python) | Falls back to `name` field, then to raw node text (`_resolve_imported_name`); returns `None` for original name (`_get_original_name`) | Ensures some name string is still produced for the imported symbol when possible |
| Duplicate `@name` captures for the same import statement | Checked via `if alias_name not in grouped[group_key].names` before appending | Prevents duplicate entries; does not raise or warn |
| `_strip_quotes` receiving text without matching quote/bracket pairs or length < 2 | Returns the text unchanged | Non-quoted module identifiers (e.g., Python, Java) pass through safely without modification |
| Query/grammar produces no matches at all | `cursor.matches()` yields nothing; `grouped` remains empty | Returns empty list; treated identically to "no imports present" |

## Design Considerations

- The consistent use of `.get(...)` with default empty lists (`captures.get("module", [])`) instead of direct dictionary indexing reflects an intentional design to tolerate queries that do not produce every expected capture group for every match.
- Grouping by `(module, line)` key inherently deduplicates and merges partial matches, which also serves as an implicit error-tolerance mechanism: multiple capture events for the same logical import statement never produce conflicting or duplicate `ImportInfo` objects.
- Since this function is invoked by three different dependents (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) as part of a larger analysis pipeline, propagating exceptions from a single malformed import statement would risk aborting analysis of an entire file; the chosen "skip and continue" policy protects the broader pipeline's ability to process the rest of the file even when specific import patterns are unrecognized or incomplete.
- No logging or diagnostic reporting of skipped/malformed captures is performed within this file; failures are handled purely through silent omission.

# Summary

`imports.py` provides language-agnostic extraction of import statements from tree-sitter ASTs, using externally-supplied query strings (standardized captures: `@module`, `@name`, `@import_node`) to avoid per-language parsing logic. Public API: `ImportInfo` dataclass (module, names, line, module_alias, alias_map) and `extract_imports(root_node, language, import_query_str) -> list[ImportInfo]`, which groups matches by `(module, line)`, resolving aliases/wildcards. Helper functions handle alias detection and quote/bracket stripping. No internal dependencies; consumed by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py` for cross-file symbol/dependency resolution.
