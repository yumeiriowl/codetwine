# Design Document: codetwine/parsers/ts_parser.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Parses source files into tree-sitter ASTs, caching results at module level to avoid redundant parsing across the pipeline.

## 2. When to Use This Module

- **Extracting an AST root node**: Call `parse_file(file_path)` and take the first element of the returned tuple (`root_node`) to run tree-sitter queries or definition extraction against a source file (used by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, and `import_to_path.py`).
- **Accessing raw file bytes alongside the AST**: Call `parse_file(file_path)` and unpack both elements `(root_node, content)` when the byte content is also needed (e.g., to decode into text lines for source extraction in `file_analyzer.py`).
- **Releasing cached parse results**: Access `parse_cache.clear()` directly to free memory after a pipeline run completes (used by `pipeline.py`).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file in binary mode, selects the tree-sitter `Language` by file extension, parses the content into an AST, caches the result, and returns the root node together with the raw byte content. |
| `parse_cache` | — | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously computed `(root_node, content)` tuples; can be cleared externally to reclaim memory. |

## 4. Design Decisions

- **Module-level cache (`parse_cache`)**: Parse results are stored in a plain module-level dictionary rather than inside `parse_file` itself, making the cache directly accessible to callers (e.g., `pipeline.py`) for explicit invalidation via `parse_cache.clear()` without requiring a separate cache-management API.
- **Language selection via extension mapping**: The parser is configured at call time by looking up the file extension in `TREE_SITTER_LANGUAGES` (imported from `settings.py`), delegating all language-to-extension binding to the centralized registry rather than embedding it here.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

### `_language_map`

| Property | Detail |
|---|---|
| Type | `dict[str, Language]` |
| Source | Alias for `TREE_SITTER_LANGUAGES` from `codetwine/config/settings.py` |

**Responsibility:** Provides a module-local reference to the file-extension-to-`Language`-object mapping, used during parser initialization without importing the settings symbol directly at each call site.

---

### `parse_cache`

| Property | Detail |
|---|---|
| Type | `dict[str, tuple[Node, bytes]]` |
| Keys | Absolute file path strings |
| Values | A 2-tuple of the tree-sitter AST root `Node` and the raw binary file content |

**Responsibility:** Module-level memoization store that prevents repeated disk reads and tree-sitter parses for the same file within a single process lifetime.

**Constraints & edge cases:**
- Callers outside this module may clear this cache directly (as done in `pipeline.py`) to reclaim memory; no internal eviction mechanism exists.
- Does not account for file modifications after the first parse — a cached result will be stale if the file changes on disk between calls.

---

## Functions

### `parse_file`

**Signature:**
```
parse_file(file_path: str) -> tuple[Node, bytes]
```

- `file_path`: Absolute path to the source file to parse.
- Return type: A 2-tuple where the first element is the tree-sitter `Node` representing the root of the AST, and the second element is the raw binary content of the file.

**Responsibility:** Reads a source file from disk, selects the appropriate tree-sitter `Language` by file extension, parses the content into an AST, and returns both the root node and the raw bytes. Caches the result in `parse_cache` so subsequent calls for the same path skip I/O and parsing.

**When to use:** Call this whenever any part of the codebase needs the parsed AST or raw content of a source file identified by its absolute path.

**Design decisions:**
- The language is resolved purely from the file extension (the portion after the last `.`), delegating all extension-to-language mappings to `settings.py`.
- File content is read in binary mode; callers that need text are responsible for decoding (e.g., `content.decode("utf-8")`).
- A single `Parser` instance is constructed per call (not reused across calls); no shared mutable parser state is retained between invocations.
- Cache lookup occurs before any file I/O or parser construction, so repeated calls for already-parsed files incur no disk or CPU cost.

**Constraints & edge cases:**
- `file_path` must be an absolute path; relative paths are not normalized internally.
- The file extension (after stripping the leading `.`) must exist as a key in `_language_map`; an unrecognized extension raises a `KeyError`.
- The file must be readable; missing or permission-denied files raise the standard `OSError` family of exceptions.
- No thread-safety guarantees are provided for concurrent writes to `parse_cache`.
- Stale cache entries are not invalidated automatically when the underlying file changes.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/parsers/ts_parser_py/ts_parser.py` → `codetwine/config/settings.py` : imports `TREE_SITTER_LANGUAGES` (a `dict[str, Language]`) to obtain the mapping from file extensions to tree-sitter `Language` objects, which is used to initialize the `Parser` for each file's language.

## Dependents (modules that import this file)

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain the AST root node of a source file, then passes the root node to `extract_definitions` to register symbol-to-file mappings.

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain both the AST root node and the raw byte content of the target file, then uses the byte content to extract definition source code by line range.

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : accesses `parse_cache` directly and calls `parse_cache.clear()` to release cached parse results and free memory after pipeline analysis is complete.

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain the AST root node of both target files (for definition extraction) and caller files (for import extraction during usage analysis).

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain the AST root node of callee files (for definition lookup) and of caller files (for import statement extraction during dependency graph construction).

## Dependency Direction

All relationships are **unidirectional**:

- `ts_parser.py` → `settings.py` : `ts_parser.py` depends on `settings.py`; `settings.py` has no knowledge of `ts_parser.py`.
- Each dependent module → `ts_parser.py` : each of the five dependent modules imports from `ts_parser.py`; `ts_parser.py` has no knowledge of any of its dependents.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `file_path` | Caller argument | Absolute path string to a source file |
| File content | Disk read via `file_path` | Raw bytes read in binary mode |
| `_language_map` | `TREE_SITTER_LANGUAGES` from `settings.py` | `dict[str, Language]` mapping file extension strings to tree-sitter `Language` objects |

The file extension is derived from `file_path` by stripping the leading dot (e.g., `".py"` → `"py"`), which is then used as the lookup key into `_language_map`.

---

## 2. Transformation Overview

```
file_path
    │
    ▼
[Cache lookup] ──── hit ────────────────────────────────► return (root_node, content)
    │ miss
    ▼
[Extension extraction]
  os.path.splitext(file_path)[1].lstrip(".")  →  ext (str)
    │
    ▼
[Language resolution]
  _language_map[ext]  →  Language object
    │
    ▼
[Parser initialization]
  Parser(Language)  →  parser instance
    │
    ▼
[File read]
  open(file_path, "rb")  →  content (bytes)
    │
    ▼
[Tree-sitter parsing]
  parser.parse(content)  →  tree  →  tree.root_node (Node)
    │
    ▼
[Result assembly]
  (root_node, content)  →  tuple[Node, bytes]
    │
    ▼
[Cache write]
  parse_cache[file_path] = result
    │
    ▼
return (root_node, content)
```

On a cache hit, the transformation pipeline is bypassed entirely and the previously stored result is returned directly.

---

## 3. Outputs

| Output | Type | Description |
|---|---|---|
| Return value | `tuple[Node, bytes]` | `(root_node, content)` where `root_node` is the AST root produced by tree-sitter and `content` is the raw byte content of the parsed file |
| `parse_cache` side effect | `dict[str, tuple[Node, bytes]]` | Module-level cache populated with the result keyed by `file_path`; persists across calls until explicitly cleared (e.g., `parse_cache.clear()` called from `pipeline.py`) |

---

## 4. Key Data Structures

### `parse_cache`

The module-level cache that stores parse results indexed by file path.

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | Absolute file path used as the cache lookup key |
| Value | `tuple[Node, bytes]` | Pair of the AST root node and the raw file bytes for that path |

### Return value tuple

| Position | Type | Purpose |
|---|---|---|
| `[0]` — `root_node` | `Node` (tree-sitter) | Root of the AST produced by parsing the file; used by callers to traverse and query syntax structure |
| `[1]` — `content` | `bytes` | Raw binary file content; used by callers (e.g., `file_analyzer.py`) to decode into text lines for source extraction |

### `_language_map`

Sourced from `TREE_SITTER_LANGUAGES` in `settings.py`.

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | File extension without leading dot (e.g., `"py"`, `"ts"`) |
| Value | `Language` (tree-sitter) | Language object passed to the `Parser` constructor to configure grammar-aware parsing |

## Error Handling

# Error Handling

## 1. Overall Strategy

`ts_parser.py` adopts a **fail-fast** strategy. The module contains no explicit error handling constructs (no try-except blocks, no fallback logic, no logging). All errors propagate immediately to the caller as unhandled exceptions. The module relies entirely on Python's default exception propagation mechanism, delegating all error management responsibility to upstream callers.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | The file extension extracted from `file_path` is not present as a key in `_language_map` (i.e., `TREE_SITTER_LANGUAGES`) | None — exception propagates to caller | No | Entire `parse_file` call aborts |
| `FileNotFoundError` | The file at `file_path` does not exist when opened in binary mode | None — exception propagates to caller | No | Entire `parse_file` call aborts |
| `OSError` / `IOError` | The file at `file_path` exists but cannot be read due to permission or I/O error | None — exception propagates to caller | No | Entire `parse_file` call aborts |
| `Exception` (tree-sitter internal) | `parser.parse(content)` encounters an internal failure | None — exception propagates to caller | No | Entire `parse_file` call aborts |

---

## 3. Design Notes

- **No defensive guarding at module level.** The module performs no pre-validation of `file_path` (e.g., existence checks, extension whitelist checks) before executing the parse pipeline. This places the burden of input validation on callers such as `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.
- **Cache does not mask errors.** The module-level `parse_cache` only stores results for successfully completed parses. A failed parse attempt does not write to the cache, meaning a subsequent call with the same path would re-attempt and fail again identically.
- **Fail-fast is consistent with a batch pipeline context.** As seen in `pipeline.py`, `parse_cache.clear()` is invoked at pipeline completion, suggesting `parse_file` is used within a controlled pipeline where unrecoverable errors are expected to surface immediately and halt the relevant processing unit rather than be silently absorbed.

## Summary

**ts_parser.py** parses source files into tree-sitter ASTs, caching results to avoid redundant I/O across the pipeline.

**Public interface:**
- `parse_file(file_path: str) -> tuple[Node, bytes]`: reads file, selects language via extension lookup, parses into AST, caches and returns result.
- `parse_cache: dict[str, tuple[Node, bytes]]`: module-level cache keyed by absolute file path, clearable externally.

**Key data structures:** `_language_map` (`dict[str, Language]`) from `settings.py`; returned `(root_node, content)` tuple consumed by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, and `import_to_path.py`.
