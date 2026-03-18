# Design Document: codetwine/extractors/imports.py

## Overview & Purpose

# Overview & Purpose

This file implements the import statement extraction layer for the CodeTwine project. It provides a language-agnostic mechanism to parse AST nodes produced by tree-sitter and convert raw import syntax into structured `ImportInfo` objects. It exists as a separate module because import extraction is a reusable, cross-language concern consumed by at least three distinct analysis pipelines—`file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`—each of which needs to resolve what a file imports without duplicating parsing logic.

## Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module`, `names`, `line`, `module_alias`, `alias_map` (dataclass fields) | — | Data container holding all structured information about a single import statement |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST root and returns deduplicated, grouped `ImportInfo` objects for every import statement found in the file |

The four private helpers (`_detect_module_alias`, `_resolve_imported_name`, `_get_original_name`, `_strip_quotes`) are internal utilities supporting `extract_imports` and are not part of the public interface.

## Design Decisions

- **Query-driven, language-agnostic dispatch**: `extract_imports` accepts the query string as a parameter (`import_query_str`) rather than embedding language-specific logic. Language differences are encoded in the query strings defined elsewhere (`config.py`), keeping this file free of per-language branching beyond unavoidable AST structural variations (Python `aliased_import`, JS/TS `import_specifier`, Kotlin `import_alias`, Java/Kotlin `asterisk`).

- **Grouping by `(module, line)` key**: Because a single `from X import A, B` statement produces multiple `@name` captures in separate query matches, results are accumulated into a `dict[tuple[str, int], ImportInfo]` before being returned as a list. This prevents one import statement from generating multiple `ImportInfo` entries.

- **Alias tracking via dual fields**: Aliasing is represented at two levels—`module_alias` for `import X as Y` (whole-module rename) and `alias_map` (a `{alias → original}` dict) for name-level aliases such as `from X import a as b`—allowing callers to reconstruct both the in-code name and the canonical definition name.

- **Early exit on absent query**: Passing `None` as `import_query_str` immediately returns an empty list, providing a safe no-op path for languages that have no import query defined.

## Definition Design Specifications

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

A value object representing a single resolved import statement. Designed to be language-agnostic: fields that are not applicable to a given language are represented as empty collections or `None` rather than being omitted.

| Field | Type | Meaning |
|---|---|---|
| `module` | `str` | The import source path/name after quote stripping |
| `names` | `list[str]` | Names explicitly imported from the module (empty list if the language has no such syntax, or for bare module imports) |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | The alias name `Y` in `import X as Y`; `None` if no alias |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for named imports (e.g., `from X import a as b` → `{"b": "a"}`); `None` when no aliased named imports exist |

---

## `extract_imports`

**Signature:** `(root_node: Node, language: Language, import_query_str: str | None) -> list[ImportInfo]`

Entry point for import extraction. Runs a tree-sitter query against the file's AST and produces a flat, deduplicated list of `ImportInfo` objects. This function is responsible for abstracting over syntactic differences between languages so that callers can handle import information uniformly.

**Arguments:**
- `root_node`: The root AST node of a fully parsed file.
- `language`: The tree-sitter `Language` object required to compile the query.
- `import_query_str`: A tree-sitter S-expression query string. When `None`, the function returns an empty list without error, allowing callers to skip import analysis for unsupported languages gracefully.

**Returns:** A list of `ImportInfo`, one per unique `(module, line)` pair. Multiple `@name` captures from the same statement are merged into a single entry.

**Design decisions:**
- The grouping key `(module, line)` is chosen over the AST node identity to consolidate multi-name imports (e.g., `from os import path, getcwd`) into one `ImportInfo`.
- The `_require_func` capture name is a reserved convention for filtering CommonJS-style patterns; any match where the captured function name is not literally `"require"` is discarded.
- When `@import_node` is present it takes priority over `@module` for line number resolution, since the import statement node's start position is more accurate for multi-line imports.
- Wildcard imports (`*` in Java/Kotlin) are detected via child node type inspection rather than via a dedicated query capture, because AST node types differ between languages.

**Constraints:** Expects `root_node` to be the root of a complete, successfully parsed file. Behavior with partial or error-recovery AST nodes is undefined.

---

## `_detect_module_alias`

**Signature:** `(module_node: Node, import_nodes: list[Node]) -> str | None`

Extracts the alias name from a module-level alias (`import X as Y`). Exists because the alias is structurally attached to different parent nodes depending on the language (Python vs. Kotlin), requiring branching resolution logic to be isolated from the main loop.

**Arguments:**
- `module_node`: The node captured by `@module`; its parent is inspected for the Python `aliased_import` pattern.
- `import_nodes`: Nodes captured by `@import_node`; the first element is inspected for the Kotlin `import_alias` field.

**Returns:** The alias string as it appears in source, or `None` if no alias is present.

**Design decisions:** Python and Kotlin paths are distinguished by node type (`aliased_import` vs. field name `alias` on the import node), not by language parameter, keeping the function stateless with respect to the active language.

---

## `_resolve_imported_name`

**Signature:** `(name_node: Node) -> str | None`

Returns the name as it will be referenced in code after import. When an alias exists, the alias is returned; otherwise the declared name is returned. Centralises the alias-or-original resolution so that the caller only needs to store one effective name per named import.

**Arguments:**
- `name_node`: A node captured by `@name`.

**Returns:** The effective name string. Never returns `None` in practice (falls back to the raw node text), but the return type is `str | None` for consistency with its sibling `_get_original_name`.

**Design decisions:** Three structural cases are handled: Python `aliased_import` nodes, JS/TS `import_specifier`/`export_specifier` parent nodes, and all other nodes (raw text fallback). The JS/TS alias is retrieved from the parent node because the `@name` capture points to the identifier child, not the specifier.

---

## `_get_original_name`

**Signature:** `(name_node: Node) -> str | None`

Returns the pre-alias original name only when an alias actually exists. Returning `None` when there is no alias prevents redundant `alias_map` entries for non-aliased names, keeping `alias_map` meaningful as a signal that renaming occurred.

**Arguments:**
- `name_node`: A node captured by `@name`.

**Returns:** The original name string if an alias is present, otherwise `None`.

**Design decisions:** The `None` sentinel has semantic meaning: callers use it to decide whether to populate `alias_map`, so it must not be conflated with the case where the original and effective names happen to be identical.

---

## `_strip_quotes`

**Signature:** `(text: str) -> str`

Normalises a raw module string captured from the AST by removing surrounding quote characters or angle brackets. Exists because different languages encode import paths with different delimiters in their AST text representation, and downstream consumers expect a bare path string.

**Arguments:**
- `text`: The raw text from an `@module` capture node, potentially wrapped in `"..."`, `'...'`, or `<...>`.

**Returns:** The inner string with delimiters removed, or the original string unchanged if no recognised delimiter pair is found.

**Constraints:** Only single-character symmetric delimiters are handled; inputs shorter than two characters are returned as-is.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file relies on the following external modules:

- **`dataclasses` (standard library)**: Used to define the `ImportInfo` data class, which serves as the structured container for holding extracted import information (module name, imported names, line number, alias data).
- **`tree_sitter` (`Language`, `Query`, `QueryCursor`, `Node`)**: The core dependency for AST-based import extraction. `Query` and `QueryCursor` are used to execute S-expression pattern matching against the parsed AST, and `Node` is used as the type for traversing and inspecting AST nodes throughout all helper functions.

No project-internal file dependencies exist in this file. It is a self-contained module that depends only on external libraries.

---

### Dependents (what uses this file)

Three project-internal files consume `extract_imports` from this module, all in a unidirectional dependency relationship (they depend on this file; this file does not depend on them).

- **`codetwine/file_analyzer.py`**: Uses `extract_imports` to obtain the list of import statements for a target file, which is then passed to `build_symbol_to_file_map` to construct a mapping from imported symbol names and aliases to their corresponding dependency files. This is the primary consumer driving per-file import resolution.

- **`codetwine/extractors/usage_analysis.py`**: Uses `extract_imports` to retrieve the import list of a caller file during usage analysis. The resulting `ImportInfo` list is used to understand which external symbols a caller file brings in, enabling cross-file usage tracing.

- **`codetwine/extractors/dependency_graph.py`**: Uses `extract_imports` to enumerate all import statements of a given project file, then resolves each import's module path to a project-internal file path in order to build callee relationships in a dependency graph.

All three relationships are strictly unidirectional: `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` each import from this file, while this file has no knowledge of its dependents.

## Data Flow

# Data Flow

## Input

| Input | Type | Source |
|-------|------|--------|
| `root_node` | `Node` | Tree-sitter AST root of a parsed source file |
| `language` | `Language` | Tree-sitter Language object for the target language |
| `import_query_str` | `str \| None` | S-expression query string from `IMPORT_QUERIES` config |

## Main Transformation Flow

```
import_query_str + language
        │
        ▼
  Query(language, import_query_str)
        │
        ▼
  QueryCursor.matches(root_node)
        │   Yields (_, captures) per match
        ▼
  ┌─────────────────────────────────────────────┐
  │  Per match:                                 │
  │  1. Filter: skip non-require() captures     │
  │  2. Extract @module  → strip quotes → str   │
  │  3. Extract @import_node → line number      │
  │  4. Group key = (module_str, line_num)      │
  │  5. Detect module alias (import X as Y)     │
  │  6. For each @name node:                    │
  │     - resolve alias name (used in code)     │
  │     - resolve original name (pre-alias)     │
  │     - append to ImportInfo.names            │
  │     - populate ImportInfo.alias_map         │
  │  7. Detect wildcard (*) in import children  │
  └─────────────────────────────────────────────┘
        │
        ▼
  grouped: dict[(module, line), ImportInfo]
        │  (multiple @name captures from the same
        │   statement are merged into one entry)
        ▼
  list(grouped.values())
```

## Output

A `list[ImportInfo]` returned to callers in:
- `codetwine/file_analyzer.py` → feeds `build_symbol_to_file_map`
- `codetwine/extractors/usage_analysis.py` → caller import resolution
- `codetwine/extractors/dependency_graph.py` → resolved as project-internal dependencies

## Key Data Structures

### `ImportInfo` (dataclass)

| Field | Type | Purpose |
|-------|------|---------|
| `module` | `str` | Import source path/name, quotes and angle brackets stripped |
| `names` | `list[str]` | Names imported from the module (from X import **Y, Z**); empty for bare imports |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | Alias assigned to the entire module (`import X as Y` → `"Y"`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias → original name for named imports (`{"path_join": "join"}`) |

### `grouped` (internal accumulator)

```
dict[
  (module: str, line: int),   # key: uniquely identifies one import statement
  ImportInfo                  # value: accumulated from all @name captures of that statement
]
```

Multiple query matches that share the same `(module, line)` key—caused by multiple `@name` captures on a single `from X import A, B` statement—are merged into a single `ImportInfo` rather than producing duplicate entries.

## Helper Transformation Chain

```
@module node  ──text──▶  raw_module  ──_strip_quotes()──▶  module (str)

@name node    ──────────────────────────────────────────────────────────┐
              _resolve_imported_name() → alias name (used in code)      │→ ImportInfo.names
              _get_original_name()    → pre-alias name or None          │→ ImportInfo.alias_map

@module node  ──_detect_module_alias()──▶  module_alias or None         → ImportInfo.module_alias
```

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions when input is missing, malformed, or unexpected, the functions return empty collections or `None` values, allowing callers to continue processing without interruption. No explicit `try/except` blocks are used; instead, defensive guard clauses and safe fallback paths handle unexpected states silently.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `import_query_str` is `None` or empty | Returns an empty list immediately via guard clause | The caller receives no import data; processing continues normally |
| `@module` capture is absent from a match | Match is skipped via guard clause | That import statement is not recorded; remaining matches continue to be processed |
| `@import_node` capture is absent | Falls back to using the `@module` node for line number extraction | Line number is still recorded; no data loss for core fields |
| `@name` capture node yields an empty or `None` name string | The name is not appended to the names list | That specific imported name is silently omitted from the result |
| Duplicate name in the same import group | Duplicate is filtered out by an existence check before appending | Exactly one entry per name is recorded; no error is raised |
| `module_alias` or `alias_map` field is absent on a node | Returns `None` from helper functions; field remains `None` on `ImportInfo` | Alias information is simply absent; the core import record is unaffected |
| `_require_func` capture resolves to a function other than `"require"` | Entire match is skipped | Non-`require` call expressions are excluded from results without error |
| Wildcard (`*`) already present in names list | Duplicate check prevents re-insertion | Idempotent; no duplicate wildcard entries |

---

## Design Considerations

The absence of exception handling is intentional: the extractors are read-only static analysis tools consumed by multiple dependents (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`). A single malformed or unrecognized AST node should not abort an entire file analysis pass. By returning `None` or empty structures at the boundary of each helper function, failures are contained to the smallest possible unit—an individual import statement or even a single captured node—while the rest of the extraction result remains valid and usable by callers.

## Summary

## codetwine/extractors/imports.py

Extracts import statements from tree-sitter ASTs into structured `ImportInfo` objects. Language-agnostic: query strings are passed in as parameters rather than hardcoded. The public `extract_imports(root_node, language, import_query_str)` function runs a tree-sitter query, groups results by `(module, line)` key to merge multi-name imports, and returns a deduplicated `list[ImportInfo]`. `ImportInfo` fields: `module` (bare path), `names` (imported identifiers), `line` (1-based), `module_alias` (whole-module rename), `alias_map` (alias→original for named imports). Consumed by `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.
