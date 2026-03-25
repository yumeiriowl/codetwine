# Design Document: codetwine/parsers/ts_parser.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Parses source files into tree-sitter AST root nodes and raw byte content, exposing a single cached parsing function that maps file extensions to tree-sitter `Language` objects via the application's central language registry.

## 2. When to Use This Module

- **Extracting symbol definitions from a file**: Call `parse_file(abs_path)` and use the returned `root_node` with a definition extractor (e.g., `extract_definitions`), as done in `import_to_path.py` and `usage_analysis.py`.
- **Reading both AST and raw source text together**: Call `parse_file(target_file)` and unpack `(root_node, content)` to decode lines for source reconstruction, as done in `file_analyzer.py`.
- **Parsing caller or callee files for import/dependency analysis**: Call `parse_file(file_path)` to obtain the `root_node` passed to import or usage extractors, as done in `dependency_graph.py`.
- **Releasing cached parse results after a pipeline run**: Access `parse_cache.clear()` directly to free memory, as done in `pipeline.py`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file in binary mode, parses it with the tree-sitter `Language` matching its extension, caches the result, and returns the AST root node together with the raw byte content. |
| `parse_cache` | — (module-level `dict`) | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously computed `(root_node, content)` tuples; can be cleared externally to free memory. |

## 4. Design Decisions

- **Module-level result cache**: `parse_cache` is a plain `dict` at module scope so that any number of callers across different modules share a single parse result per file path within one process lifetime, avoiding redundant I/O and parsing. Callers are responsible for invalidating the cache (e.g., calling `parse_cache.clear()` at pipeline end).
- **Extension-to-language delegation**: The module does not embed any language configuration itself; it delegates entirely to `TREE_SITTER_LANGUAGES` from `settings.py`, so adding support for a new language requires no changes to this module.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants and Variables

### `_language_map`

| Property | Detail |
|---|---|
| Type | `dict[str, Language]` |
| Source | Alias for `TREE_SITTER_LANGUAGES` imported from `codetwine/config/settings.py` |

**Responsibility:** Provides a file-extension-to-`Language`-object mapping used locally within the module to avoid repeated references to the settings import. Keys are file extensions (without leading dot, e.g. `"py"`, `"ts"`); values are tree-sitter `Language` objects.

---

### `parse_cache`

| Property | Detail |
|---|---|
| Type | `dict[str, tuple[Node, bytes]]` |
| Scope | Module-level |

**Responsibility:** Stores previously computed parse results keyed by absolute file path, so that repeated calls to `parse_file` for the same path do not trigger redundant disk I/O or AST construction.

- The value type is a two-element tuple: the tree-sitter `Node` (AST root) and the raw binary file content.
- Exposed publicly so that external callers (e.g., `codetwine/pipeline.py`) can call `parse_cache.clear()` to release memory after a pipeline run.

**Constraint:** The cache is never invalidated based on file modification time. If the file changes on disk after it has been cached, the stale result will continue to be returned for the lifetime of the process or until `parse_cache.clear()` is called externally.

---

## Functions

### `parse_file`

**Signature:**
```python
def parse_file(file_path: str) -> tuple[Node, bytes]
```

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Absolute path to the source file to be parsed |

**Return type:** `tuple[Node, bytes]`
- `Node`: The root node of the tree-sitter AST for the parsed file.
- `bytes`: The raw binary content of the file as read from disk.

**Responsibility:** Produces a tree-sitter AST root node and the associated raw file bytes for a given source file, serving as the single entry point for all parsing needs across the codebase.

**When to use:** Call this function whenever any module needs either the AST or the byte content of a source file (e.g., for definition extraction, import analysis, or usage analysis).

**Design decisions:**

- **Cache-first lookup:** The function checks `parse_cache` before performing any I/O or parsing. If the file path is already cached, the stored tuple is returned immediately without re-reading or re-parsing the file.
- **Language resolution via extension:** The tree-sitter `Language` object is resolved by stripping the leading dot from the file extension and looking it up in `_language_map`. No fallback or default language is attempted; a missing extension raises a `KeyError`.
- **Binary read mode:** The file is read in binary mode (`"rb"`), which is required by the tree-sitter `Parser.parse` API and preserves byte offsets that tree-sitter nodes refer to.

**Constraints & edge cases:**

| Constraint | Detail |
|---|---|
| Supported extensions only | `file_path` must have an extension present as a key in `TREE_SITTER_LANGUAGES`; otherwise a `KeyError` is raised from `_language_map`. |
| Absolute path expected | Callers are responsible for providing an absolute path; relative paths are not normalized internally. |
| No staleness detection | Once cached, a file's parse result is never refreshed based on disk changes within the same process run. |
| No thread safety guarantee | `parse_cache` is a plain `dict`; concurrent writes from multiple threads are not protected. |
| File must exist and be readable | A missing or unreadable file raises the standard `FileNotFoundError` or `PermissionError` from the `open` call. |

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/parsers/ts_parser_py/ts_parser.py` → `codetwine/config/settings.py` : imports `TREE_SITTER_LANGUAGES` to obtain the mapping from file extension strings to tree-sitter `Language` objects, which is used to select the correct language when initialising the `Parser` for a given file.

## Dependents (modules that import this file)

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain the AST root node of a source file, which is then passed to `extract_definitions` to register definition names in a symbol-to-file map.

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain both the AST root node and the raw byte content of the target file, using the root node for definition extraction and the byte content for reconstructing source text lines.

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : accesses the module-level `parse_cache` object directly and calls `parse_cache.clear()` to release cached parse results after the analysis pipeline completes.

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain AST root nodes for both the target file (to enumerate its definitions) and each caller file (to extract import statements for usage analysis).

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain AST root nodes for callee files (to resolve definition names) and for caller files (to extract import statements when building the dependency graph).

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/parsers/ts_parser_py/ts_parser.py` → `codetwine/config/settings.py` is unidirectional; `settings.py` has no dependency on `ts_parser.py`.
- Each dependent module (`import_to_path.py`, `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, `dependency_graph.py`) → `codetwine/parsers/ts_parser_py/ts_parser.py` is unidirectional; `ts_parser.py` has no dependency on any of those modules.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `file_path` | Caller argument | Absolute path string to the source file to be parsed |
| File content | Disk read (binary mode) | `bytes` read from the file at `file_path` |
| `TREE_SITTER_LANGUAGES` | `codetwine/config/settings.py` (module-level import) | `dict[str, Language]` mapping file extension strings to tree-sitter `Language` objects |

The file extension is derived from `file_path` by splitting on the final `.` and stripping the leading dot, yielding a plain string key (e.g., `"py"`, `"ts"`) used to look up the appropriate `Language` object.

---

## 2. Transformation Overview

```
file_path
    │
    ▼
[1. Cache lookup]
    │  hit → return cached (root_node, content)
    │  miss ↓
    ▼
[2. Extension extraction]
    file_path → ext (str)
    │
    ▼
[3. Language resolution]
    ext → Language object  (via _language_map / TREE_SITTER_LANGUAGES)
    │
    ▼
[4. Parser initialization]
    Language → Parser instance
    │
    ▼
[5. File read]
    file_path → content (bytes)
    │
    ▼
[6. Tree-sitter parse]
    (Parser, content) → Tree → root_node (Node)
    │
    ▼
[7. Cache store & return]
    parse_cache[file_path] = (root_node, content)
    → return (root_node, content)
```

Stages 1–7 are strictly sequential with no async or parallel processing. The cache check at Stage 1 short-circuits the entire pipeline on a cache hit, meaning Stages 2–7 are only executed the first time a given `file_path` is encountered.

---

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| Return value | `tuple[Node, bytes]` | A pair of the AST root node produced by tree-sitter and the raw binary content of the parsed file |
| `parse_cache` side effect | `dict[str, tuple[Node, bytes]]` | Module-level cache updated with the new entry; persists across calls within the same process lifetime and is externally accessible for clearing (e.g., `parse_cache.clear()` called from `pipeline.py`) |

There are no file writes. The module does not produce any output files or log entries.

---

## 4. Key Data Structures

### `parse_cache`

The module-level cache that stores parse results keyed by absolute file path.

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute file path string identifying the parsed file |
| value[0] | `Node` | Tree-sitter AST root node for the parsed file |
| value[1] | `bytes` | Raw binary content of the parsed file |

### Return value of `parse_file`

| Index | Type | Purpose |
|---|---|---|
| `[0]` | `Node` | Root node of the tree-sitter AST; used by callers to traverse the syntax tree for definition or import extraction |
| `[1]` | `bytes` | Raw file content in binary form; used by callers (e.g., `file_analyzer.py`) to decode into text lines for source reconstruction |

### `_language_map` (module-level reference to `TREE_SITTER_LANGUAGES`)

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File extension without leading dot (e.g., `"py"`, `"ts"`) |
| value | `Language` | Tree-sitter `Language` object used to initialize the `Parser` for files of that extension |

## Error Handling

# Error Handling

## 1. Overall Strategy

`ts_parser.py` adopts a **fail-fast** strategy. The module contains no explicit error-handling constructs (no try/except blocks, no fallback logic, and no logging). Any exception raised during file I/O, language lookup, or parsing propagates immediately to the caller without interception. The module trusts that inputs are valid and delegates responsibility for error recovery entirely to upstream callers.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | File extension not present in `_language_map` (i.e., not registered in `TREE_SITTER_LANGUAGES`) | None — exception propagates to caller | No | Parsing of the file is aborted; caller receives an unhandled exception |
| `FileNotFoundError` | `file_path` does not point to an existing file | None — exception propagates to caller | No | Parsing is aborted; caller receives an unhandled exception |
| `OSError` / `IOError` | File exists but cannot be opened or read (e.g., permission error) | None — exception propagates to caller | No | Parsing is aborted; caller receives an unhandled exception |
| Tree-sitter parse error | Malformed or syntactically invalid source content | None — tree-sitter itself returns a partial AST with error nodes; no exception is raised at this layer | Yes (partial) | A root node containing error nodes is returned and cached normally; callers receive an AST that may contain `ERROR` nodes |

---

## 3. Design Notes

- **No defensive wrapping**: The absence of try/except is a deliberate simplicity choice. The module is a thin parsing utility, and error policy decisions (skip, abort, log) are left to the callers such as `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.
- **Cache-before-validation**: Results are cached only after a successful parse. A failed call (one that raises) does not populate `parse_cache`, so a subsequent retry with a corrected environment would re-attempt parsing rather than returning a cached error state.
- **Tree-sitter's tolerance**: Tree-sitter parses even syntactically invalid files by inserting `ERROR` nodes into the AST. This means the only hard failures at this layer are I/O and language-lookup errors, not syntactic errors in the source file.
- **Cache invalidation is external**: The cache (`parse_cache`) is a plain module-level dict with no TTL or file-change detection. Callers (e.g., `pipeline.py`) are responsible for explicitly calling `parse_cache.clear()` to free memory and avoid stale data; the module itself imposes no automatic invalidation policy.

## Summary

**ts_parser.py**: Parses source files into tree-sitter ASTs using the language registry from `settings.py`.

- `parse_file(file_path: str) → tuple[Node, bytes]`: reads a file, resolves its tree-sitter `Language` by extension, parses it, caches and returns the AST root node plus raw bytes.
- `parse_cache: dict[str, tuple[Node, bytes]]`: module-level cache keyed by absolute file path; cleared externally via `parse_cache.clear()`.
- `_language_map: dict[str, Language]`: alias for `TREE_SITTER_LANGUAGES` mapping extensions to `Language` objects.
