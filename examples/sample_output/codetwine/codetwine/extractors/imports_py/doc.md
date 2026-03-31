# Design Document: codetwine/extractors/imports.py

## Overview & Purpose

## 1. Module Summary

Extracts and normalizes import statement information from a tree-sitter AST, returning structured `ImportInfo` objects that map module paths to the names imported from them.

## 2. When to Use This Module

- **Resolving per-file dependencies**: Call `extract_imports(root_node, language, import_query_str)` to obtain all import statements in a file. The result is passed to `build_symbol_to_file_map` in `file_analyzer.py` to build a mapping from imported symbol names to their source files.
- **Analyzing cross-file symbol usage**: Call `extract_imports(root_node, language, import_query_str)` in `usage_analysis.py` to enumerate the imports of a caller file before checking which imported names are actually referenced in its source.
- **Building a project dependency graph**: Call `extract_imports(root_node, language, import_query_str)` in `dependency_graph.py` to enumerate each file's import statements and resolve them to concrete project file paths.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | — | Data container holding all parsed information for a single import statement, including the module path, individually imported names, line number, and any alias mappings. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST, consolidates multiple name captures from the same import statement into one `ImportInfo`, and returns the full list of imports found in the file. Returns an empty list when `import_query_str` is `None`. |

## 4. Design Decisions

- **Query-driven, language-agnostic extraction**: Import syntax varies significantly across languages. Rather than encoding per-language logic in Python, the module delegates pattern matching entirely to an externally supplied tree-sitter query string (`import_query_str`). The same `extract_imports` function therefore works for every language whose query is defined in the configuration.
- **Grouping by `(module, line)` key**: A single `from X import A, B` statement produces multiple `@name` captures from the query. These are collapsed into a single `ImportInfo` using a `(module, line)` tuple as a dictionary key, so callers always receive one object per import statement regardless of how many names it imports.
- **Alias normalization via `alias_map`**: When a name is imported under an alias (e.g., `from X import join as path_join`), the alias is stored in `names` (the name visible in code) and the original name is recorded in `alias_map` as `{"path_join": "join"}`. This lets callers resolve both the in-code reference and the original definition name without re-parsing the AST.
- **`_require_func` guard for CommonJS**: Matches containing a `_require_func` capture are validated to ensure the function name is literally `"require"`, filtering out false positives from other call expressions that structurally resemble a `require()` call.

## Definition Design Specifications

---

## `ImportInfo` (dataclass)

**Responsibility:** Holds all extracted metadata for a single import statement as a structured value object, serving as the data contract between the extraction layer and consumers such as dependency graph builders and usage analyzers.

**When to use:** Instantiated exclusively by `extract_imports`; consumed by callers that need to resolve module dependencies or map imported names to their source files.

### Fields

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source path or module name, with quotes/angle brackets already stripped |
| `names` | `list[str]` | Names brought into scope by the import (e.g., items from `from X import ...`); empty list for whole-module imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The local alias assigned to the entire module (the `Y` in `import X as Y`); `None` when no alias is present |
| `alias_map` | `dict[str, str] \| None` | Maps each aliased name to its original name for per-name aliases (e.g., `{"path_join": "join"}`); `None` when no per-name aliases exist |

**Design decisions:**
- `names` uses an empty list (not `None`) as the default so callers can always iterate without a null check.
- `module_alias` and `alias_map` default to `None` to avoid allocating unnecessary objects for the common case of no aliasing.
- `alias_map` key is the alias (the name used in code) and value is the original name, matching the direction needed by consumers performing reverse-lookup.

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

- `root_node`: The root `Node` of a parsed AST covering an entire source file.
- `language`: A tree-sitter `Language` object for the file's programming language.
- `import_query_str`: A tree-sitter S-expression query string, or `None` if the language has no defined import query.
- Returns: A flat list of `ImportInfo` objects, one per distinct import statement (not one per imported name).

**Responsibility:** Traverses the AST using a tree-sitter query to locate all import statements, then consolidates captures from the same statement into a single `ImportInfo`, handling aliases, wildcard imports, and language-specific patterns uniformly.

**When to use:** Called by any component that needs structured import information from an already-parsed AST, including file dependency resolution, dependency graph construction, and cross-file usage analysis.

### Query capture name contract

| Capture name | Meaning |
|---|---|
| `@module` | The import source (module name, file path, header name, etc.) |
| `@name` | A single imported name (the `Y` in `from X import Y`) |
| `@import_node` | The entire import statement node, used for accurate line number retrieval |
| `@_require_func` | (Internal filter) The called function name in CommonJS-style patterns; match is skipped if value is not `"require"` |

### Design decisions

- **Grouping key `(module, line)`:** Multiple `@name` captures from a single `from X import Y, Z` statement share the same key, so they are merged into one `ImportInfo` rather than producing duplicate entries.
- **`@import_node` for line numbers:** Line number is taken from the full import statement node when available, falling back to the `@module` node, ensuring the reported line corresponds to the start of the statement rather than the module token.
- **Duplicate name guard:** Names are only appended if not already present in `names`, preventing duplicates when the query can match the same name more than once.
- **Wildcard detection via child node type:** Java/Kotlin wildcard imports are detected by inspecting child node types (`"asterisk"` or `"*"`) on the import node rather than by a dedicated query capture, adding `"*"` to `names`.
- **Early exit on `None` query string:** Returns an empty list immediately without constructing any tree-sitter objects, supporting languages that have no defined import query.

### Constraints & edge cases

- If `import_query_str` is falsy (empty string or `None`), the return value is always `[]`.
- The function does not validate that `root_node` corresponds to the language of `language`.
- Module strings are passed through `_strip_quotes` but otherwise used verbatim; no path normalization or resolution is performed here.
- CommonJS filtering operates on the text content of `@_require_func`; if the node text cannot be decoded as UTF-8 the decode will raise, as no error handling is present.

---

## `_detect_module_alias`

**Signature:**
```python
def _detect_module_alias(
    module_node: Node,
    import_nodes: list[Node],
) -> str | None
```

- `module_node`: The node captured by `@module`.
- `import_nodes`: The list of nodes captured by `@import_node` (may be empty).
- Returns: The alias string if one exists, otherwise `None`.

**Responsibility:** Encapsulates the language-specific logic for detecting a whole-module alias (`import X as Y`) across Python and Kotlin AST structures.

**When to use:** Called internally by `extract_imports` once per query match to populate `ImportInfo.module_alias`.

### Design decisions

- **Two separate structural checks:** Python aliases are detected via the parent node relationship of `module_node` (parent type `"aliased_import"`), while Kotlin aliases are detected via a named `"alias"` child field on the import statement node. These are independent paths with no shared logic.
- For Kotlin, the alias value is extracted from the first child of the alias node whose type is `"simple_identifier"` or `"identifier"`, rather than using the alias node's text directly.

### Constraints & edge cases

- Returns `None` silently if neither structural pattern matches; no exception is raised for unrecognized AST shapes.
- If `import_nodes` is an empty list, the Kotlin path is skipped entirely.

---

## `_resolve_imported_name`

**Signature:**
```python
def _resolve_imported_name(name_node: Node) -> str | None
```

- `name_node`: A node captured by `@name`.
- Returns: The name as it is used in code (alias name if aliased, original name otherwise). Never returns `None` in practice given a valid node.

**Responsibility:** Normalizes a `@name` capture to the locally usable name, abstracting over Python `aliased_import` nodes and JavaScript/TypeScript `import_specifier`/`export_specifier` parent patterns.

**When to use:** Called internally by `extract_imports` for every `@name` capture node to determine what name to record in `ImportInfo.names`.

### Design decisions

- For Python, the function handles the case where `@name` is itself the `aliased_import` node (rather than a leaf identifier), inspecting `"alias"` and `"name"` named fields in priority order.
- For JS/TS, the alias is resolved by inspecting the *parent* node's `"alias"` field rather than the captured node itself, because the query captures the original identifier, not the alias.
- Falls back to the raw text of `name_node` when no recognized alias structure is found.

### Constraints & edge cases

- Assumes `name_node.text` is always decodable as UTF-8.
- The JS/TS path only activates for parent types `"import_specifier"` and `"export_specifier"`; other parent types fall through to the raw text fallback.

---

## `_get_original_name`

**Signature:**
```python
def _get_original_name(name_node: Node) -> str | None
```

- `name_node`: A node captured by `@name`.
- Returns: The original (pre-alias) name if an alias is present, or `None` if there is no alias.

**Responsibility:** Provides the counterpart to `_resolve_imported_name` by returning the original definition name when a rename alias is in effect, enabling construction of `ImportInfo.alias_map`.

**When to use:** Called internally by `extract_imports` alongside `_resolve_imported_name` to build the `alias_map` entry when the two return different values.

### Design decisions

- Intentionally returns `None` (not the name itself) when no alias exists, so callers can use a simple truthiness check and the identity condition `original != alias` to decide whether to record a mapping.
- Mirrors the two-language structural approach of `_detect_module_alias` and `_resolve_imported_name` for consistency.

### Constraints & edge cases

- For Python, returns `None` if the `aliased_import` node lacks a `"name"` field, which would indicate a malformed AST.
- For JS/TS, returns the text of `name_node` (not the alias node's text) as the original name, relying on the fact that the query captures the original identifier.
- Returns `None` for all node types that do not match the Python or JS/TS patterns.

---

## `_strip_quotes`

**Signature:**
```python
def _strip_quotes(text: str) -> str
```

- `text`: A raw module string as captured from the AST.
- Returns: The string with surrounding `"..."`, `'...'`, or `<...>` delimiters removed; unchanged if none of these patterns match.

**Responsibility:** Normalizes import path strings from languages that embed delimiters into the AST node text (JavaScript/TypeScript strings, C/C++ header paths) so that `ImportInfo.module` is always the bare path.

**When to use:** Called internally by `extract_imports` on every `@module` capture before storing it in `ImportInfo`.

### Constraints & edge cases

- Only strips a single layer of matching delimiters; nested or mismatched delimiters are not handled.
- Strings shorter than two characters are returned unchanged.
- Languages where the module name has no surrounding quotes (Python, Java, Kotlin) are unaffected.

## Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal dependencies**. All imports in the source code are from external packages (`dataclasses` from the standard library, and `tree_sitter` as a third-party package). No project-internal modules are imported.

## Dependents (modules that import this file)

Three project-internal modules depend on this file, all consuming the `extract_imports` function:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to parse import statements from a file's AST, feeding the results into `build_symbol_to_file_map` to construct a mapping from imported symbol names to their source dependency files.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to retrieve the import list of a caller file's AST, which is then used to support usage analysis across files.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to enumerate import statements in a file's AST, then resolves each `ImportInfo.module` to a project file path in order to build the dependency graph's callee edges.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → this module (one-way)
- `codetwine/extractors/usage_analysis.py` → this module (one-way)
- `codetwine/extractors/dependency_graph.py` → this module (one-way)

This module does not import from any of its dependents, so no bidirectional relationships exist.

## Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `Node` | The root AST node of a fully parsed source file, produced by tree-sitter |
| `language` | `Language` | A tree-sitter `Language` object used to compile the query |
| `import_query_str` | `str \| None` | A tree-sitter S-expression query string that defines capture patterns for import statements; `None` signals that the language has no import query defined |

The module receives no file I/O or configuration values directly. All inputs are passed as arguments by callers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`).

---

## 2. Transformation Overview

```
import_query_str ──► [1. Query compilation]
                          │
                          ▼
root_node ──────► [2. AST cursor scan]  → raw match stream (pattern_index, captures dict)
                          │
                          ▼
                    [3. Per-match filtering & extraction]
                       • Skip non-require() calls (_require_func guard)
                       • Decode @module text, strip quotes
                       • Resolve line number from @import_node or @module
                          │
                          ▼
                    [4. Grouping by (module, line)]
                       grouped: dict[(str, int) → ImportInfo]
                       • Merge multiple @name captures for the same statement
                       • Detect module alias (import X as Y)
                       • Detect per-name aliases (from X import a as b)
                       • Detect wildcard imports (asterisk / * child nodes)
                          │
                          ▼
                    [5. Output collection]
                       list(grouped.values()) → list[ImportInfo]
```

**Stage 1 – Query compilation:** `import_query_str` is compiled into a `Query` object against the given `Language`. If `import_query_str` is `None` or empty, the function returns an empty list immediately.

**Stage 2 – AST cursor scan:** A `QueryCursor` walks `root_node` and produces an iterable of `(pattern_index, captures)` pairs, where `captures` maps capture names to lists of `Node` objects.

**Stage 3 – Per-match filtering and extraction:** Each match is inspected for the `_require_func` guard (CommonJS filtering), then the `@module` node is decoded and unquoted via `_strip_quotes`. The import statement's line number is taken from `@import_node` if present, otherwise from `@module`.

**Stage 4 – Grouping:** A `(module, line)` tuple acts as the grouping key in `grouped`. All `@name` captures from the same statement are merged into one `ImportInfo.names` list. Three helper functions run during this stage: `_detect_module_alias` (returns the alias string for `import X as Y`), `_resolve_imported_name` (returns the name as used in code, preferring the alias), and `_get_original_name` (returns the pre-alias name only when an alias exists). Wildcard imports are detected by inspecting the children of `@import_node` for nodes typed `asterisk` or `*`.

**Stage 5 – Output collection:** The values of `grouped` are converted to a flat list and returned.

---

## 3. Outputs

| Output | Type | Description |
|---|---|---|
| Return value of `extract_imports` | `list[ImportInfo]` | One `ImportInfo` per distinct `(module, line)` pair found in the file, with all per-name and alias details populated |

There are no file writes or global side effects. The return value is consumed by callers to build symbol-to-file maps, resolve dependency graphs, and perform usage analysis.

---

## 4. Key Data Structures

### `ImportInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source path or module name, with surrounding quotes or angle brackets removed |
| `names` | `list[str]` | Names brought into scope by the import; empty for bare `import X` statements; contains `"*"` for wildcard imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The local alias assigned to the entire module (`Y` in `import X as Y`); `None` when no alias is present |
| `alias_map` | `dict[str, str] \| None` | Maps each alias name to its original name for per-name aliases (`{"path_join": "join"}` for `from X import join as path_join`); `None` when no per-name aliases exist |

### `grouped` (internal accumulation dict)

| Key | Type | Purpose |
|---|---|---|
| `(module, line)` | `tuple[str, int]` | Composite key that uniquely identifies one import statement; ensures multiple `@name` captures from the same statement are merged into a single `ImportInfo` |
| value | `ImportInfo` | The accumulating `ImportInfo` record for that statement |

### `captures` (per-match dict, produced by tree-sitter)

| Key | Type | Purpose |
|---|---|---|
| `"module"` | `list[Node]` | Nodes matching `@module`; first element is the import source |
| `"name"` | `list[Node]` | Nodes matching `@name`; one per individually imported identifier |
| `"import_node"` | `list[Node]` | Nodes matching `@import_node`; the full import statement, used for line number and wildcard detection |
| `"_require_func"` | `list[Node]` | Nodes matching `@_require_func`; present only in CommonJS patterns, used to filter non-`require` calls |

## Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions on invalid or unexpected input, the functions apply defensive checks and return safe neutral values (empty lists, `None`) when data is absent or unrecognizable. Processing continues uninterrupted regardless of whether individual captures, nodes, or fields are missing from the AST.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| No import query defined | `import_query_str` is `None` or empty | Returns an empty list immediately | Yes | No imports extracted; callers receive an empty list |
| Missing `@module` capture in a match | A query match yields no `module` nodes | The entire match is skipped via `continue` | Yes | That specific import statement is silently ignored |
| Non-`require` call in CommonJS pattern | `@_require_func` capture exists but the function name is not `"require"` | The match is skipped via `continue` | Yes | Non-require call expressions are excluded from results |
| Missing `@import_node` capture | A match has no `import_node` nodes | Line number falls back to the `module` node's start position | Yes | Line number may be slightly different but extraction continues |
| Missing alias field on a node | `child_by_field_name("alias")` or `child_by_field_name("name")` returns `None` | Returns `None` or falls back to raw node text | Yes | Alias or original name is omitted; base name is used |
| Missing parent node | `name_node.parent` is `None` | Parent-dependent alias resolution is skipped | Yes | Alias detection for JS/TS specifiers is skipped |
| Unquoted or non-bracketed module string | `_strip_quotes` receives text shorter than 2 chars or without recognized delimiters | Returns the text unchanged | Yes | Module name is used as-is without modification |
| Duplicate `@name` capture in one import | The same name appears more than once for a single `(module, line)` group | Duplicate is skipped; existing entry is preserved | Yes | No duplicate names in the `names` list |

---

## 3. Design Notes

The absence of any `try-except` blocks is intentional. All error conditions are handled through **explicit pre-condition checks** (guard clauses, `dict.get` with defaults, `None` checks on child fields) rather than exception catching. This reflects a design assumption that tree-sitter provides a well-formed AST, so failures are treated as missing or unexpected structure rather than runtime exceptions.

The `(module, line)` grouping key is central to correctness: it consolidates multiple `@name` captures from a single import statement into one `ImportInfo` entry. Because this grouping is driven purely by presence of data, a partially malformed import match degrades locally — only that match is skipped or produces a reduced result — without affecting other matches or callers that consume the returned list.

## Summary

**codetwine/extractors/imports.py** extracts and normalizes import statements from tree-sitter ASTs into structured objects.

**Responsibility:** Runs a tree-sitter query against a parsed AST to locate all import statements and return one structured record per statement.

**Public API:**
- `ImportInfo(module: str, names: list[str], line: int, module_alias: str|None, alias_map: dict[str,str]|None)` — dataclass holding one import statement's metadata
- `extract_imports(root_node: Node, language: Language, import_query_str: str|None) → list[ImportInfo]`

**Key data:** `ImportInfo` (module path, imported names, line, aliases); grouped by `(module, line)` tuple internally.
