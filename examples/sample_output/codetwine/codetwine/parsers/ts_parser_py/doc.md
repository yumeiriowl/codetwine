# Design Document: codetwine/parsers/ts_parser.py

# Overview & Purpose

## 1. Module Summary
Parses source files into tree-sitter ASTs and caches the results with an LRU eviction policy to avoid redundant re-parsing across the codebase.

## 2. When to Use This Module
- When any module needs the AST root node and raw byte content of a source file for further analysis (e.g., extracting definitions, imports, or usages), call `parse_file(file_path)` to obtain a `(root_node, content)` tuple.
- When a long-running process (e.g., a full pipeline run) has finished analyzing files and wants to release memory held by cached syntax trees, call `parse_cache.clear()`.
- When multiple parts of the pipeline (definition extraction, import resolution, dependency graph building, usage analysis) need to parse the same file repeatedly, rely on this module's caching so the file is only read and parsed once per cache lifetime (unless evicted).

## 3. Public Interface Table
| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file, parses it with the appropriate tree-sitter language based on file extension, and returns its AST root node and raw byte content; caches results and reuses cached entries when available. |
| `parse_cache` | — (module-level `OrderedDict[str, tuple[Node, bytes]]`) | — | Holds cached parse results keyed by file path, ordered from least to most recently used; callers may invoke `parse_cache.clear()` to free memory. |

## 4. Design Decisions
- Uses an `OrderedDict` as a manual LRU cache: on cache hit, the entry is moved to the end via `move_to_end`; on insertion, the oldest entry (`popitem(last=False)`) is evicted once the cache exceeds `PARSE_CACHE_MAX_FILES`.
- `PARSE_CACHE_MAX_FILES = 0` disables the eviction limit entirely, allowing unbounded caching.
- The language used for parsing is resolved dynamically from the file's extension via the `TREE_SITTER_LANGUAGES` mapping, decoupling this module from any specific language.
- Caching is file-content-based per path only (not content-hash based); a cached tree persists until evicted or `parse_cache` is cleared, regardless of whether the underlying file has changed.

# Definition Design Specifications

## `_language_map`

- **Signature**: `_language_map: dict[str, Language]` (module-level alias)
- **Responsibility**: Provides a short, local alias to `TREE_SITTER_LANGUAGES` so that `parse_file` can look up the appropriate tree-sitter `Language` object by file extension.
- **When to use**: Referenced internally by `parse_file` when constructing a `Parser` for a given file extension; not intended for direct external use.
- **Design decisions**: Simple re-binding of the imported settings dict, avoiding repeated attribute access to the settings module.
- **Constraints & edge cases**: Any extension not present in `TREE_SITTER_LANGUAGES` will raise a `KeyError` when accessed.

## `parse_cache`

- **Signature**: `parse_cache: OrderedDict[str, tuple[Node, bytes]]`
- **Responsibility**: Module-level LRU-style cache storing previously parsed files, keyed by absolute file path, to avoid redundant parsing across the codebase.
- **When to use**: Populated and read automatically by `parse_file`; cleared externally (e.g., by `pipeline.py` calling `parse_cache.clear()`) once analysis is complete to free memory.
- **Design decisions**:
  - Uses `OrderedDict` to maintain insertion/access order, enabling least-recently-used (LRU) eviction via `move_to_end` and `popitem(last=False)`.
  - Each entry keeps a `Node` (tree-sitter AST root) alive, which implicitly keeps the whole parsed tree alive since a `Node` holds a reference to its tree.
  - Size is bounded by `PARSE_CACHE_MAX_FILES`; a value of `0` disables the size limit entirely (no eviction occurs).
- **Constraints & edge cases**:
  - Keys must be consistent (e.g., always absolute paths) for cache hits to work correctly; the module does not normalize paths itself.
  - If `PARSE_CACHE_MAX_FILES` is `0`, the cache can grow unbounded, which is a known trade-off for disabling eviction.
  - External code (`pipeline.py`) directly manipulates the cache via `.clear()`, so this module's encapsulation of the cache is not strict.

## `parse_file`

- **Signature**: `parse_file(file_path: str) -> tuple[Node, bytes]`
  - `file_path`: absolute path of the file to parse.
  - Return type: a tuple of `(root_node, content)` where `root_node` is the tree-sitter AST root (`Node`) and `content` is the raw file bytes.
- **Responsibility**: Reads a source file, parses it into an AST using the tree-sitter language inferred from its extension, and returns the root node plus raw content, using a cache to avoid redundant work.
- **When to use**: Called whenever a caller needs the AST and raw bytes of a project source file — e.g., definition extraction, import resolution, or usage analysis in other modules (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`).
- **Design decisions**:
  - Implements an LRU cache manually on top of `OrderedDict`: a cache hit moves the entry to the end (most recently used); insertion of a new entry may trigger eviction of the oldest (least recently used) entry from the front.
  - Language selection is derived purely from the file's extension (via `os.path.splitext`, stripped of the leading dot), delegating the extension-to-`Language` mapping to `TREE_SITTER_LANGUAGES`.
  - File is read in binary mode (`"rb"`) so tree-sitter can operate on raw bytes, matching tree-sitter's byte-offset-based node API.
  - Eviction logic only runs when `PARSE_CACHE_MAX_FILES > 0`, allowing the cache to be effectively unbounded when the setting is `0`.
- **Constraints & edge cases**:
  - If the file extension is not a key in `TREE_SITTER_LANGUAGES` (i.e., in `_language_map`), a `KeyError` is raised when constructing the `Parser`.
  - If the file does not exist or cannot be opened, the standard file I/O exception (`FileNotFoundError`, `PermissionError`, etc.) propagates.
  - Assumes `file_path` is used consistently as the cache key; passing different string representations of the same file (e.g., relative vs. absolute) results in separate cache entries and re-parsing.
  - Does not handle parse errors from tree-sitter itself — tree-sitter's `parser.parse` returns a tree even for syntactically invalid content (with error nodes), so no exception handling is needed there, but this file does not inspect for parse errors.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py` (`TREE_SITTER_LANGUAGES`): retrieves the extension-to-`Language` object mapping used to select the correct tree-sitter grammar for a given file's extension when constructing a `Parser`.
- `codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py` (`PARSE_CACHE_MAX_FILES`): retrieves the configured cache size limit that determines when the module-level `parse_cache` should evict least-recently-used entries (a value of `0` disables the limit).

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` (`parse_file`): parses a target file to obtain its AST root node and byte content, which are then used to extract definitions and their corresponding source code lines.
- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser.py` (`parse_file`): parses a file to obtain its AST root node, used to extract definitions and register their names in a symbol-to-file mapping.
- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` (`parse_cache`): clears the module-level parse result cache at the end of analysis to free memory.
- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` (`parse_file`): parses both target files and caller files to obtain AST root nodes, used for extracting definition names and analyzing imports/usages.
- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser.py` (`parse_file`): parses callee files and general source files to obtain AST root nodes, used for resolving definitions and extracting import statements to build the dependency graph.

## Dependency Direction

- The relationship between `codetwine/parsers/ts_parser.py` and `codetwine/config/settings.py` is **unidirectional**: `ts_parser.py` depends on configuration values exposed by `settings.py`, while `settings.py` has no dependency on `ts_parser.py`.
- The relationships between `codetwine/parsers/ts_parser.py` and each of `codetwine/file_analyzer.py`, `codetwine/import_to_path.py`, `codetwine/pipeline.py`, `codetwine/extractors/usage_analysis.py`, and `codetwine/extractors/dependency_graph.py` are **unidirectional**: each dependent module calls into `ts_parser.py`'s `parse_file` function or accesses its `parse_cache`, while `ts_parser.py` has no dependency on any of these modules.

# Data Flow

## 1. Inputs

- **`file_path: str`** — Absolute path of the file to parse, passed as an argument to `parse_file()`.
- **File system content** — Raw bytes read from disk at `file_path` via binary file read.
- **`TREE_SITTER_LANGUAGES: dict[str, Language]`** — Module-level config dict (from `settings.py`) mapping file extensions (without the leading dot) to tree-sitter `Language` objects, aliased locally as `_language_map`.
- **`PARSE_CACHE_MAX_FILES: int`** — Module-level config value (from `settings.py`) defining the maximum number of cached entries; `0` means unlimited.
- **`parse_cache: OrderedDict[str, tuple[Node, bytes]]`** — Module-level mutable cache state, consulted as an implicit input on every call.

## 2. Transformation Overview

1. **Cache lookup** — The incoming `file_path` is looked up in `parse_cache`. If a cached `(Node, bytes)` tuple exists, it is moved to the end of the `OrderedDict` (marking it most-recently-used) and returned immediately, bypassing all further stages.
2. **Extension resolution** — On a cache miss, the file extension is extracted from `file_path` (stripped of the leading dot) to serve as the lookup key into `_language_map`.
3. **Language resolution** — The extension key is used to fetch the corresponding tree-sitter `Language` object from `_language_map`, which is used to construct a `Parser` instance.
4. **File read** — The file at `file_path` is opened in binary mode and its full contents are read into a `bytes` object.
5. **Parsing** — The `Parser` parses the raw bytes into a tree-sitter syntax tree; the tree's `root_node` is extracted.
6. **Result assembly** — `(root_node, content)` is packaged into a tuple named `result`.
7. **Cache insertion & eviction** — `result` is stored in `parse_cache` under `file_path`. If `PARSE_CACHE_MAX_FILES > 0` and the cache size exceeds that limit, the least-recently-used entries (from the front of the `OrderedDict`) are evicted one at a time until the size constraint is satisfied.
8. **Return** — `result` is returned to the caller, identical in shape whether it came from cache or fresh parsing.

There is no async or parallel branching; the flow is strictly sequential with a single early-return short-circuit for cache hits.

## 3. Outputs

- **Return value**: `tuple[Node, bytes]` — the tree-sitter AST `root_node` and the raw file `content` as bytes. This is the sole output consumed by all dependents (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`) to extract definitions, imports, and source text.
- **Side effect**: mutation of module-level `parse_cache` (insertion of new entries, reordering of existing entries, eviction of stale entries). This state persists across calls within the process and is exposed to other modules (e.g., `pipeline.py` calls `parse_cache.clear()` to release memory after analysis completes).

## 4. Key Data Structures

**`parse_cache: OrderedDict[str, tuple[Node, bytes]]`**

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute file path used as cache identifier |
| value[0] | `tree_sitter.Node` | Root AST node of the parsed file (keeps the whole tree alive) |
| value[1] | `bytes` | Raw byte content of the file, used for decoding/text extraction by callers |

**`_language_map` / `TREE_SITTER_LANGUAGES: dict[str, Language]`**

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File extension without leading dot (e.g., `"py"`, `"js"`) |
| value | `tree_sitter.Language` | Grammar object used to construct a `Parser` for that extension |

**`result: tuple[Node, bytes]`** (return value / cache entry)

| Field / Key | Type | Purpose |
|---|---|---|
| root_node | `tree_sitter.Node` | Entry point into the parsed syntax tree for downstream traversal (definition/import extraction) |
| content | `bytes` | Full file content, later decoded (e.g., to UTF-8 text lines) by consumers |

# Error Handling

## 1. Overall Strategy

This file adopts a **fail-fast** strategy with no explicit exception handling. `parse_file` performs no try-except blocks of its own; any error occurring during extension lookup, file reading, or parsing propagates directly to the caller. There is no fallback language, no retry logic, and no logging of failures — errors are surfaced immediately and unmodified, relying on upstream callers (or the process itself) to decide how to react.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | File extension (from `os.path.splitext`) is not present as a key in `_language_map` (`TREE_SITTER_LANGUAGES`), i.e., an unsupported/unmapped language | Not caught; exception propagates to caller | No | `parse_file` call fails; caller must handle or the process terminates |
| `FileNotFoundError` / `OSError` | `file_path` does not exist or is inaccessible (permissions, I/O issue) during `open(file_path, "rb")` | Not caught; exception propagates to caller | No | File cannot be parsed; caller must handle or the process terminates |
| Parser/tree-sitter internal errors | Malformed input to `Parser(...)` construction or `parser.parse(content)` raising due to unexpected content/state | Not caught; exception propagates to caller | No | Parsing aborts for that file; no cache entry is stored |
| Cache staleness (not a raised error) | Same `file_path` is requested again after underlying file content on disk has changed | Cached `(root_node, content)` is returned as-is without re-reading/re-parsing | N/A (silent behavior, not an exception) | Caller may work with outdated AST/content; no error signaled |

## 3. Design Notes

- The function intentionally keeps no defensive checks (e.g., no explicit validation of `ext` or file existence) before invoking dependent operations, delegating error detection entirely to the underlying `os.path`, built-in `open`, and `tree_sitter.Parser` APIs.
- Because failures are not caught here, the responsibility for graceful degradation (e.g., skipping unparsable files) lies with the multiple call sites listed in the dependents (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`), consistent with this file being a shared low-level utility rather than a policy-owning component.
- The LRU cache (`parse_cache`) only affects performance and memory usage, not error handling: a failed parse is never cached, so a subsequent call for the same `file_path` will retry the full read/parse sequence and can raise the same or a different error again.
- No logging calls exist in this file, so error visibility depends entirely on how calling code manages exceptions (e.g., whether it wraps calls in its own try-except or lets them propagate further, as seen in `pipeline.py`'s reliance on `parse_cache.clear()` for cleanup, which is unaffected by parse failures).

# Summary

Parses source files into tree-sitter ASTs with LRU caching. Public API: `parse_file(file_path: str) -> tuple[Node, bytes]` reads/parses a file (language chosen via extension using `TREE_SITTER_LANGUAGES`); `parse_cache: OrderedDict[str, tuple[Node, bytes]]` module-level cache, cleared via `parse_cache.clear()`. Eviction bounded by `PARSE_CACHE_MAX_FILES` (0 = unbounded). No error handling; exceptions propagate.
