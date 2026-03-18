# Design Document: codetwine/parsers/ts_parser.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Parses source files into tree-sitter ASTs by resolving file extensions to language configurations, providing callers with a root `Node` and raw byte content for subsequent AST analysis.

## 2. When to Use This Module

- **Extracting symbol definitions from a file**: Call `parse_file(abs_path)` and use the returned root `Node` as input to definition extractors (e.g., `extract_definitions`).
- **Analyzing file content alongside its AST**: Call `parse_file(target_file)` to receive both the root `Node` and the raw `bytes` content, enabling line-based source extraction from `content.decode("utf-8").splitlines()`.
- **Resolving imports and usage references**: Call `parse_file(abs_path)` to obtain the root `Node` for import extraction and usage analysis queries.
- **Freeing memory after a pipeline run**: Access `parse_cache` directly and call `parse_cache.clear()` to release all cached parse results.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file, parses it with tree-sitter using the language resolved from the file extension, and returns the AST root node and raw byte content. Results are stored in `parse_cache` to avoid redundant parsing. |
| `parse_cache` | — | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously computed `(root_node, content)` tuples. Exposed for external cache management (e.g., clearing after a pipeline run). |

## 4. Design Decisions

- **Module-level cache**: `parse_cache` is a plain module-level `dict` rather than a function-scoped or instance-bound structure. This makes it accessible to any caller that imports the module, enabling explicit lifecycle control (e.g., `parse_cache.clear()` in `pipeline.py`) without requiring a dedicated cache manager object.
- **Language resolution via `_language_map`**: The extension-to-`Language` mapping is sourced entirely from `TREE_SITTER_LANGUAGES` in `settings.py`, keeping language configuration centralized. This module performs no language configuration of its own; an unsupported extension will raise a `KeyError` at the map lookup step, making misconfiguration immediately visible.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

### `_language_map`

| Property | Detail |
|---|---|
| Type | `dict[str, Language]` |
| Source | Alias to `TREE_SITTER_LANGUAGES` from `codetwine/config/settings.py` |

**Responsibility:** Provides a module-local reference to the extension-to-`Language` object mapping, used when selecting the correct tree-sitter grammar for a given file extension.

---

### `parse_cache`

| Property | Detail |
|---|---|
| Type | `dict[str, tuple[Node, bytes]]` |
| Key | Absolute file path (`str`) |
| Value | A `(root_node, content)` pair — the tree-sitter AST root node and the raw binary file content |

**Responsibility:** Stores previously computed parse results so that the same file is never parsed more than once within a single process run. External callers (e.g., `pipeline.py`) can call `parse_cache.clear()` to release memory after a pipeline stage completes.

---

## Functions

### `parse_file`

**Signature:**
```python
def parse_file(file_path: str) -> tuple[Node, bytes]
```

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Absolute path to the source file to parse |

**Return type:** `tuple[Node, bytes]` — a pair of the tree-sitter AST root node (`Node`) and the raw binary content of the file (`bytes`).

**Responsibility:** Reads a source file from disk, selects the appropriate tree-sitter grammar by file extension, parses the content into an AST, and returns both the root node and the raw bytes. Acts as the single entry point for all tree-sitter parsing in the codebase.

**When to use:** Any time a caller needs either the AST root node or the raw byte content of a source file; used by file analysis, definition extraction, usage analysis, and dependency graph construction modules.

**Design decisions:**

- **Cache-first lookup:** Before performing any I/O or parsing, the function checks `parse_cache`. If the path is already present, the cached result is returned immediately, making repeated calls for the same file O(1) after the first call.
- **Extension-based language dispatch:** The tree-sitter `Language` object is looked up from `_language_map` using only the file extension (without the leading dot). This delegates all extension-to-language knowledge to `settings.py`.
- **Binary read mode:** The file is read in binary mode (`"rb"`), which is required by tree-sitter's `parser.parse()` interface and also ensures the returned `bytes` value can be decoded by callers using any encoding they choose.
- **Result stored before return:** The computed `(root_node, content)` tuple is written to `parse_cache` before returning, so the cache is populated even on the first call.

**Constraints & edge cases:**

- `file_path` must be an absolute path; relative paths are not explicitly rejected but may cause incorrect cache keying or file-not-found errors.
- The file extension (after stripping the leading dot) must exist as a key in `_language_map`; an unrecognized extension will raise a `KeyError`.
- The file must be readable; missing or permission-denied files will raise an `OSError`.
- The cache is never automatically invalidated; if a file changes on disk after first parse, the stale result will continue to be returned until `parse_cache.clear()` is called externally.
- No thread-safety mechanism is applied to `parse_cache`; concurrent writes from multiple threads could produce inconsistent state.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/parsers/ts_parser_py/ts_parser.py` → `codetwine/config/settings.py` : imports `TREE_SITTER_LANGUAGES` to obtain the mapping from file extensions to tree-sitter `Language` objects, which is used to select the correct language when initializing a `Parser` instance for a given file.

## Dependents (modules that import this file)

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : uses `parse_file` to obtain the AST root node of a source file, then passes that node to `extract_definitions` to register definition names in the symbol-to-file map.

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : uses `parse_file` to obtain both the AST root node and the raw byte content of the target file, then decodes the content into text lines for source code extraction alongside definition analysis.

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : accesses `parse_cache` directly and calls `parse_cache.clear()` after analysis is complete in order to free memory held by cached parse results.

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : uses `parse_file` to obtain AST root nodes for both target files (to extract definition names) and caller files (to extract import information for usage analysis).

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : uses `parse_file` to obtain AST root nodes for callee files (to resolve definition references) and for files being scanned for import statements when building the dependency graph.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/parsers/ts_parser_py/ts_parser.py` → `codetwine/config/settings.py` is unidirectional; `settings.py` has no dependency on this module.
- Each of the five dependent modules (`import_to_path.py`, `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, `dependency_graph.py`) → `codetwine/parsers/ts_parser_py/ts_parser.py` is unidirectional; this module does not import any of those dependents.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `file_path` | Caller argument | Absolute path string to a source file |
| `_language_map` | `TREE_SITTER_LANGUAGES` from `codetwine/config/settings.py` | `dict[str, Language]` mapping file extension strings to tree-sitter `Language` objects |
| File content | Binary file read from `file_path` | Raw bytes (`bytes`) |

The module-level `_language_map` is populated once at import time from `TREE_SITTER_LANGUAGES`, which itself is derived from `_LANG_REGISTRY` via `_expand_ext_aliases`. The `parse_cache` dict is also initialized at module level as an empty `dict[str, tuple[Node, bytes]]`.

---

## 2. Transformation Overview

```
file_path (str)
     │
     ▼
[Cache lookup] ──── hit ────► return cached (Node, bytes)
     │
   miss
     │
     ▼
[Extension extraction]
  os.path.splitext → strip leading "." → ext (str)
     │
     ▼
[Language resolution]
  _language_map[ext] → Language object
     │
     ▼
[Parser initialization]
  Parser(Language) → parser instance
     │
     ▼
[File read]
  open(file_path, "rb") → content (bytes)
     │
     ▼
[Tree-sitter parse]
  parser.parse(content) → Tree → tree.root_node (Node)
     │
     ▼
[Result assembly]
  (root_node, content) → tuple[Node, bytes]
     │
     ▼
[Cache store]
  parse_cache[file_path] = result
     │
     ▼
return (Node, bytes)
```

The pipeline has a short-circuit path: if the `file_path` key already exists in `parse_cache`, the cached result is returned immediately without any file I/O or parsing.

---

## 3. Outputs

| Output | Format | Destination |
|---|---|---|
| `(root_node, content)` return value | `tuple[Node, bytes]` | Callers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`) |
| `parse_cache` side-effect | Module-level `dict[str, tuple[Node, bytes]]` updated in-place | Read by callers; cleared externally via `parse_cache.clear()` in `pipeline.py` |

There are no file writes. The only side effect is the mutation of the module-level `parse_cache` dict.

---

## 4. Key Data Structures

### `parse_cache`

The module-level cache that stores parse results keyed by absolute file path.

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | Absolute file path used as cache identifier |
| Value | `tuple[Node, bytes]` | The AST root node and raw file content bytes for that path |

### Return value of `parse_file`

| Element | Type | Purpose |
|---|---|---|
| `[0]` — `root_node` | `tree_sitter.Node` | Root of the tree-sitter AST for the parsed file |
| `[1]` — `content` | `bytes` | Raw binary content of the file as read from disk |

### `_language_map`

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | File extension (without leading `.`, e.g., `"py"`, `"ts"`) |
| Value | `tree_sitter.Language` | The tree-sitter `Language` object used to construct a `Parser` for that extension |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **fail-fast** strategy with no explicit error handling. No try-except blocks are present. All errors propagate immediately to the caller as unhandled exceptions. The module relies entirely on the calling layer to manage failures, providing no recovery, logging, or graceful degradation within this file itself.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | File extension not present in `_language_map` (i.e., unsupported file type) | None — propagates to caller | No | Entire `parse_file` call aborts |
| `FileNotFoundError` | `file_path` does not exist on the filesystem | None — propagates to caller | No | Entire `parse_file` call aborts |
| `IOError` / `OSError` | File exists but cannot be read (permissions, I/O error) | None — propagates to caller | No | Entire `parse_file` call aborts |
| Stale cache entry | A previously cached result is returned even if the file on disk has changed | None — no cache invalidation logic | No | Caller receives outdated AST and content silently |

---

## 3. Design Notes

- The module-level `parse_cache` dict stores results indefinitely with no expiration or invalidation mechanism. Cache clearing is delegated entirely to external callers (e.g., `pipeline.py` explicitly calls `parse_cache.clear()`), meaning the module itself makes no attempt to detect or handle stale state.
- The absence of any error handling within `parse_file` reflects a design assumption that preconditions (valid path, supported extension, readable file) are guaranteed by the caller before invocation.
- Because errors propagate unhandled, any unsupported extension or missing file will surface immediately in the dependent pipeline stages (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, etc.), rather than being silently ignored.

## Summary

**ts_parser.py** parses source files into tree-sitter ASTs for use by downstream analysis modules.

- **`parse_file(file_path: str) → tuple[Node, bytes]`**: resolves file extension to a tree-sitter `Language` via `_language_map`, reads the file in binary mode, parses it, and returns the AST root node and raw bytes.
- **`parse_cache: dict[str, tuple[Node, bytes]]`**: module-level cache keyed by absolute file path; cleared externally by callers such as `pipeline.py`.
- **`_language_map: dict[str, Language]`**: alias to `TREE_SITTER_LANGUAGES` from `settings.py`.
