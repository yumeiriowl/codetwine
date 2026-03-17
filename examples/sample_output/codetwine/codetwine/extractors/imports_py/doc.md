# Design Document: codetwine/extractors/imports.py

## Overview & Purpose

# Overview & Purpose

## Role Within the Project

This file implements the **import statement extraction layer** for the CodeTwine static analysis pipeline. It exists as a dedicated module to centralize the logic for parsing and normalizing import declarations across multiple programming languages using tree-sitter AST queries. By isolating this responsibility, it provides a uniform `ImportInfo` data structure to three distinct consumers — `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` — each of which relies on resolved import data to build symbol maps, trace call relationships, and construct dependency graphs respectively.

The file bridges the gap between raw tree-sitter query results (heterogeneous across languages) and a normalized, language-agnostic representation of import statements.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | dataclass instance | Data container holding all extracted metadata for a single import statement |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs tree-sitter queries against an AST root node and returns a deduplicated, grouped list of import records |

---

## Design Decisions

- **Tree-sitter query abstraction**: The extraction logic is driven entirely by an externally supplied query string (`import_query_str`), making the core function language-agnostic. Language-specific handling is delegated to query definitions elsewhere (`config.py`), with this file only interpreting the standardized capture names `@module`, `@name`, and `@import_node`.

- **Grouping by `(module, line)` key**: Multiple `@name` captures from a single `from X import A, B` statement are consolidated into one `ImportInfo` entry via a `dict` keyed on `(module, line)`, avoiding redundant entries for multi-name imports.

- **Private helper decomposition**: Language-specific edge cases (alias detection, quote stripping, original-name resolution) are extracted into private functions (`_detect_module_alias`, `_resolve_imported_name`, `_get_original_name`, `_strip_quotes`), keeping `extract_imports` readable while encapsulating per-language quirks.

- **Graceful no-op on missing query**: Passing `None` as `import_query_str` returns an empty list immediately, allowing callers to safely invoke the function for languages that have no import query defined without additional guard logic.

- **`require()` filtering**: A special `@_require_func` capture name is reserved to filter out non-`require` function calls when handling CommonJS-style patterns, preventing false positives from similarly structured call expressions.

## Definition Design Specifications

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

A data container representing a single parsed import statement. Designed to be language-agnostic so that the same structure can represent Python `import`/`from ... import`, JavaScript `import`, Java/Kotlin `import`, C/C++ `#include`, and other forms.

| Field | Type | Meaning |
|---|---|---|
| `module` | `str` | The resolved module name/path, with surrounding quotes or angle brackets stripped |
| `names` | `list[str]` | Individually imported symbols (e.g. `Y` in `from X import Y`); empty list for bare module imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The local alias name when the module itself is aliased (e.g. `Y` in `import X as Y`) |
| `alias_map` | `dict[str, str] \| None` | Maps each alias name to its original name for per-symbol aliases (e.g. `{"path_join": "join"}` for `from X import join as path_join`); `None` when no such aliases exist |

**Design decision:** `names` being an empty list (rather than `None`) for non-`from`-style imports allows callers to check the presence of named imports with a simple truthiness test, without guarding against `None`.

---

## `extract_imports`

```
extract_imports(root_node: Node, language: Language, import_query_str: str | None) -> list[ImportInfo]
```

**Responsibility:** The primary entry point of this module. Runs a tree-sitter query over the AST and converts all matched import statements in a source file into a flat list of `ImportInfo` objects. It is the single place that bridges raw AST query results and the structured `ImportInfo` representation consumed by dependents (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`).

**Arguments:**
- `root_node`: The root `Node` of a fully parsed AST for a source file.
- `language`: The tree-sitter `Language` required to compile the query.
- `import_query_str`: A tree-sitter S-expression query string. Passing `None` or an empty string short-circuits and returns `[]`, allowing callers to safely pass the value from a language config that may not define an import query.

**Returns:** A list of `ImportInfo`, one entry per unique `(module, line)` pair. Multiple `@name` captures from the same import statement (e.g. `from X import A, B`) are consolidated into a single `ImportInfo` rather than producing separate entries.

**Design decisions:**
- The `(module, line)` grouping key ensures that `from X import A, B` on one line yields one entry, while two separate imports of the same module on different lines yield two entries.
- The `@_require_func` capture is a private convention used to filter CommonJS `require()` patterns: any match where the captured function name is not literally `"require"` is discarded, preventing false positives from similarly-shaped call expressions.
- Wildcard imports (Java `*`, Kotlin `*`) are detected by inspecting the children of `@import_node` for nodes of type `"asterisk"` or `"*"` rather than text-matching, making the detection structural rather than lexical.

**Edge cases / constraints:**
- When `import_query_str` is falsy, returns `[]` immediately without touching the AST.
- If a match contains no `@module` capture, it is silently skipped.
- The `@import_node` capture is used for line number and wildcard detection; when absent, the `@module` node's start position is used as a fallback for the line number.
- Duplicate names within the same `names` list are suppressed; the first occurrence wins.

---

## `_detect_module_alias`

```
_detect_module_alias(module_node: Node, import_nodes: list[Node]) -> str | None
```

**Responsibility:** Extracts the alias name in module-level aliasing constructs (`import X as Y`), isolating the language-specific AST structure differences from the main extraction loop.

**Arguments:**
- `module_node`: The node captured by `@module`, used to walk up to a possible `aliased_import` parent (Python).
- `import_nodes`: The list of nodes captured by `@import_node`, used to look for an `import_alias` child (Kotlin).

**Returns:** The alias name string, or `None` if no alias is present.

**Design decision:** Two independent detection paths are tried in sequence—Python's `aliased_import` parent pattern, then Kotlin's `import_alias` field child—so the function handles both languages without branching on a language identifier.

---

## `_resolve_imported_name`

```
_resolve_imported_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the name by which an imported symbol is referenced in the consuming code, accounting for per-symbol aliases. This is the "effective" name (the alias if one exists, the original otherwise).

**Arguments:**
- `name_node`: A node captured by `@name`.

**Returns:** The string name as it appears in the file's local scope; never `None` in practice (falls back to the node's raw text).

**Design decision:** Handles two structurally distinct aliasing patterns—Python's `aliased_import` node (where `@name` captures the whole aliased construct) and JavaScript/TypeScript's `import_specifier`/`export_specifier` parent with an `alias` field—within a single function to keep the caller's loop uniform.

---

## `_get_original_name`

```
_get_original_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the pre-alias original name for an imported symbol, intended to be paired with `_resolve_imported_name` to populate `ImportInfo.alias_map`. Returns `None` when no alias exists, signalling to the caller that no map entry is needed.

**Arguments:**
- `name_node`: A node captured by `@name`.

**Returns:** The original (pre-alias) name string when an alias is present; `None` otherwise.

**Design decision:** Returning `None` for non-aliased names (rather than returning the name itself) lets the caller use the `None` result as the condition for deciding whether to write to `alias_map`, avoiding spurious identity-mapping entries.

---

## `_strip_quotes`

```
_strip_quotes(text: str) -> str
```

**Responsibility:** Normalises raw module path strings captured from AST nodes by removing surrounding punctuation (double quotes, single quotes, or angle brackets), producing a bare module name suitable for resolution by callers.

**Arguments:**
- `text`: The raw text of a `@module` capture node.

**Returns:** The unquoted string, or the original string unchanged if it has no recognised surrounding delimiters or is shorter than two characters.

**Edge cases / constraints:** Only the outermost pair of matching delimiters is removed; no recursive stripping is performed. Strings of length less than 2 are returned as-is.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports are from external libraries (`dataclasses`, `tree_sitter`) and the standard library. There are no imports of other files within this project.

---

### Dependents (what uses this file)

Three files within the project depend on this file, all consuming the `extract_imports` function and `ImportInfo` dataclass.

- **`codetwine/file_analyzer.py`**
  Uses `extract_imports` to parse import statements from a file's AST, then feeds the resulting `ImportInfo` list into `build_symbol_to_file_map` to construct a mapping from imported symbol names to their source files. This is part of the per-file analysis pipeline.

- **`codetwine/extractors/usage_analysis.py`**
  Uses `extract_imports` to retrieve the import list of a caller file, which is then used during usage/call analysis to understand what symbols a given file has imported.

- **`codetwine/extractors/dependency_graph.py`**
  Uses `extract_imports` to enumerate all import statements in a file and resolve each imported module to a project-local file path, thereby building the edges of the project's dependency graph.

**Direction of dependency:** All dependencies are unidirectional — `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` each depend on this file, but this file does not depend on any of them.

## Data Flow

# Data Flow

## Input

| Parameter | Type | Source |
|---|---|---|
| `root_node` | `Node` | AST root node of a parsed source file |
| `language` | `Language` | tree-sitter Language object |
| `import_query_str` | `str \| None` | S-expression query string from config |

Callers: `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`

---

## Main Transformation Flow

```
import_query_str + language
        │
        ▼
  Query + QueryCursor
        │
        ▼
  cursor.matches(root_node)
        │  yields (_, captures) per match
        ▼
  ┌─────────────────────────────────────────┐
  │  Per match:                             │
  │  @_require_func → guard filter          │
  │  @module  → raw text → _strip_quotes()  │
  │  @import_node → line number             │
  │  @name    → _resolve_imported_name()    │
  │             _get_original_name()        │
  │  @import_node children → wildcard check │
  └──────────────────┬──────────────────────┘
                     │
                     ▼
        grouped: dict[(module, line) → ImportInfo]
        (multiple @name captures per statement
         collapsed into a single ImportInfo.names list)
                     │
                     ▼
        list(grouped.values())  →  list[ImportInfo]
```

---

## Intermediate Data Structure

### `grouped: dict[tuple[str, int], ImportInfo]`

| Key | Purpose |
|---|---|
| `(module, line)` | De-duplicates multiple `@name` captures from the same import statement |

---

## Output: `ImportInfo`

| Field | Type | Description |
|---|---|---|
| `module` | `str` | Module path/name with quotes or angle brackets stripped |
| `names` | `list[str]` | Imported symbol names (alias-resolved); `["*"]` for wildcard imports; empty for bare `import X` |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | Local alias for the whole module (`import X as Y` → `"Y"`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias → original name (`{"path_join": "join"}`); `None` when no per-name aliases exist |

Return value: `list[ImportInfo]` — consumed by callers to resolve imported symbols to project files.

---

## Helper Transformations

| Helper | Input | Output |
|---|---|---|
| `_strip_quotes` | Raw module text (`"react"`, `<stdio.h>`) | Bare module string (`react`, `stdio.h`) |
| `_detect_module_alias` | `@module` node + `@import_node` list | Alias string or `None` |
| `_resolve_imported_name` | `@name` node | Name as used in code (alias if present) |
| `_get_original_name` | `@name` node | Pre-alias original name, or `None` if no alias |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** strategy throughout. Rather than raising exceptions on invalid or missing data, the functions return empty collections or `None` to signal the absence of results. Processing continues for remaining items even when individual captures or nodes yield no usable information.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `import_query_str` is `None` or empty | Early return of an empty list | No imports are extracted; callers receive an empty result without exception |
| `@module` capture absent from a match | Match is silently skipped via `continue` | That particular import statement is omitted from results; other matches proceed normally |
| Non-`require` function matched by the CommonJS pattern | Match is silently skipped via `continue` | False-positive `require`-like calls are excluded; legitimate imports are unaffected |
| `@name` or `@import_node` captures absent | Fallback to empty list / module node for line number | Import is still recorded with an empty `names` list and a best-effort line number |
| Alias or parent node fields missing on a `Node` | `_detect_module_alias`, `_resolve_imported_name`, and `_get_original_name` return `None` | Alias information is omitted; the base name or module is still recorded |
| Duplicate `@name` values within one import statement | Duplicate check before appending | Each name appears at most once in `ImportInfo.names`; no exception is raised |
| Node text that contains surrounding quotes or angle brackets | `_strip_quotes` removes them defensively | Module strings shorter than two characters are returned unchanged |

## Design Considerations

The error handling policy is designed to ensure that **partial or malformed AST data never propagates as an exception to callers** (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`). Because tree-sitter query results can vary across language grammars and file conditions, every capture lookup uses `.get()` with a default of an empty list, and every optional field is guarded before access. This makes the extractor tolerant of query definitions that omit optional capture names for certain languages, without requiring per-language exception handling in the caller.

## Summary

**codetwine/extractors/imports.py**

Extracts and normalizes import statements from tree-sitter ASTs across multiple languages. Accepts a root AST node, a tree-sitter Language, and an optional query string; returns a deduplicated `list[ImportInfo]`. The `ImportInfo` dataclass holds: `module` (unquoted path), `names` (imported symbols), `line` (1-based), `module_alias` (whole-module alias), and `alias_map` (per-symbol alias→original mapping). Multiple named imports from one statement are consolidated by `(module, line)` key. Private helpers handle quote stripping, alias detection, and per-language AST quirks. Returns `[]` gracefully when no query is provided.
