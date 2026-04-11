# Design Document: codetwine/parsers/ts_parser.py

# Overview & Purpose

## 1. Module Summary

Parses source files into tree-sitter ASTs, returning the root node and raw byte content for downstream analysis, with a module-level cache to avoid redundant parsing of the same file.

## 2. When to Use This Module

- **Extract definition nodes from a file** — call `parse_file(abs_path)` and use the returned `root_node` as input to definition extractors (e.g., `extract_definitions`).
- **Access raw file content alongside the AST** — call `parse_file(file_path)` and unpack the `(root_node, content)` tuple; `content` can be decoded to retrieve source text lines for code extraction.
- **Parse import statements or usage references** — call `parse_file(abs_path)` to obtain the `root_node` required by import and usage query functions.
- **Release cached parse results after a pipeline run** — call `parse_cache.clear()` to free memory once all analysis over a set of files is complete.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file, selects the tree-sitter language from the file extension, parses the content into an AST, caches the result, and returns `(root_node, content)`. |
| `parse_cache` | — (module-level `dict`) | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously parsed `(root_node, content)` results; can be cleared externally to free memory. |

## 4. Design Decisions

- **Module-level parse cache** — `parse_cache` is a plain dict stored at module scope rather than inside `parse_file`, so it persists across all callers within a process. This means every module that imports `parse_file` benefits from the cache transparently, and the cache can also be cleared externally (e.g., by `pipeline.py`) without going through `parse_file` itself.
- **Language selection via extension mapping** — the parser is configured purely from the file extension using `TREE_SITTER_LANGUAGES` from `settings.py`, keeping language configuration centralized and decoupled from this module.

# Definition Design Specifications

---

## Module-level Constants

### `_language_map`

| Attribute | Detail |
|---|---|
| Type | `dict[str, Language]` |
| Source | Assigned from `TREE_SITTER_LANGUAGES` imported from `codetwine/config/settings.py` |

**Responsibility:** Provides a module-local alias to the extension-to-`Language`-object mapping, used internally by `parse_file` to resolve the correct tree-sitter language for a given file extension.

---

### `parse_cache`

| Attribute | Detail |
|---|---|
| Type | `dict[str, tuple[Node, bytes]]` |
| Key | Absolute file path (`str`) |
| Value | A `(root_node, content)` pair — the parsed AST root and the raw binary content of the file |

**Responsibility:** Module-level memoization store that prevents redundant re-parsing of the same file across multiple callers within the same process lifetime.

**Usage note:** Exposed publicly so external callers (e.g., `pipeline.py`) can call `parse_cache.clear()` to release memory after a pipeline run completes.

---

## Functions

### `parse_file`

**Signature:**
```
parse_file(file_path: str) -> tuple[Node, bytes]
```

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Absolute path to the source file to be parsed |

**Return type:** `tuple[Node, bytes]`
- `Node`: The root node of the tree-sitter AST for the parsed file.
- `bytes`: The raw binary content of the file as read from disk.

**Responsibility:** Reads a source file, determines its language from the file extension, parses it using tree-sitter, and returns the AST root node together with the original byte content. Serves as the single entry point for all AST parsing needs across the codetwine system.

**When to use:** Any time a caller needs an AST or the raw bytes of a source file — used by file analysis, definition extraction, import resolution, dependency graph construction, and usage analysis.

**Design decisions:**
- **Cache-first lookup:** Before performing any I/O or parsing, the function checks `parse_cache` by `file_path`. If a cached result exists, it is returned immediately, making repeated calls for the same file cost-free.
- **Cache population:** Results are stored in the module-level `parse_cache` after the first successful parse, so all subsequent callers in the same process share the result without coordination.
- **Extension-based language resolution:** The language is determined solely from the file extension (after stripping the leading `.`), looked up in `_language_map`. No content-based language detection is performed.
- **Binary file reading:** The file is read in binary mode and passed directly to the tree-sitter parser, which expects `bytes`. The raw bytes are also returned so callers can decode them independently (e.g., for line-based text operations).

**Constraints & edge cases:**
- `file_path` must be an absolute path; relative paths are accepted syntactically but correctness of caching depends on path stability.
- The file's extension must exist as a key in `_language_map`; an unrecognized extension raises a `KeyError`.
- The file must be readable; missing or permission-denied files raise standard I/O exceptions.
- No cache invalidation mechanism exists: if the file on disk changes after the first parse, the stale cached result is returned for the remainder of the process lifetime.
- Thread safety of `parse_cache` is not guaranteed; concurrent writes from multiple threads could cause race conditions.

# Dependency Description

## Dependencies (modules this file imports)

**codetwine/parsers/ts_parser.py → codetwine/config/settings.py : obtain the extension-to-Language mapping**

- Symbol used: `TREE_SITTER_LANGUAGES` (a `dict[str, Language]`)
- `ts_parser.py` assigns this mapping to the module-level `_language_map` variable and uses it inside `parse_file` to look up the correct tree-sitter `Language` object for a given file extension before constructing a `Parser` instance.

---

## Dependents (modules that import this file)

**codetwine/import_to_path.py → codetwine/parsers/ts_parser.py : parse source files to extract symbol definitions**
- Uses `parse_file` to obtain the AST root node of an absolute file path, then passes that node to `extract_definitions` to register definition names in a symbol-to-file mapping.

**codetwine/file_analyzer.py → codetwine/parsers/ts_parser.py : parse source files to obtain both the AST root node and raw byte content**
- Uses `parse_file` to retrieve both the root node and byte content of a target file; the byte content is decoded and split into lines for subsequent source extraction.

**codetwine/pipeline.py → codetwine/parsers/ts_parser.py : manage the module-level parse cache**
- Uses `parse_cache.clear()` to release cached parse results and free memory after a full analysis pipeline run completes.

**codetwine/extractors/usage_analysis.py → codetwine/parsers/ts_parser.py : parse target and caller files for definition and import extraction**
- Uses `parse_file` to obtain the AST root node of target files (for definition name collection) and of caller files (for import extraction and usage analysis).

**codetwine/extractors/dependency_graph.py → codetwine/parsers/ts_parser.py : parse callee and caller files for dependency graph construction**
- Uses `parse_file` to obtain the AST root node of callee files (to resolve usage references) and of caller files (to extract import statements and resolve them to project paths).

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/parsers/ts_parser.py → codetwine/config/settings.py`: one-way; `ts_parser.py` consumes `TREE_SITTER_LANGUAGES` from `settings.py`, and `settings.py` has no knowledge of `ts_parser.py`.
- Each dependent module → `codetwine/parsers/ts_parser.py`: one-way; `import_to_path.py`, `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, and `dependency_graph.py` each consume symbols exported by `ts_parser.py`, while `ts_parser.py` does not import any of them.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `file_path` | Caller argument | Absolute file path string |
| File content | Disk read (binary mode) | `bytes` |
| `TREE_SITTER_LANGUAGES` | `codetwine/config/settings.py` | `dict[str, Language]` mapping file extension strings to tree-sitter `Language` objects |
| `parse_cache` | Module-level state | `dict[str, tuple[Node, bytes]]` keyed by absolute file path |

## 2. Transformation Overview

```
file_path
    │
    ▼
[Cache Lookup] ──── hit ───────────────────────────────────► (Node, bytes)
    │ miss
    ▼
[Extension Extraction]
  os.path.splitext → strip leading "." → ext (e.g. "py", "ts")
    │
    ▼
[Language Resolution]
  _language_map[ext] → tree-sitter Language object
    │
    ▼
[Parser Initialization]
  Parser(Language) → parser instance
    │
    ▼
[File Read]
  open(file_path, "rb") → content: bytes
    │
    ▼
[Parsing]
  parser.parse(content) → Tree → tree.root_node: Node
    │
    ▼
[Cache Store]
  parse_cache[file_path] = (root_node, content)
    │
    ▼
(Node, bytes)
```

The pipeline is strictly sequential and synchronous. On a cache hit, all intermediate stages are bypassed and the previously stored result is returned directly.

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| Return value of `parse_file` | `tuple[Node, bytes]` | The AST root node produced by tree-sitter and the raw binary content of the parsed file |
| `parse_cache` (side effect) | `dict[str, tuple[Node, bytes]]` | Module-level cache updated with the new parse result for `file_path`; persists across calls until explicitly cleared via `parse_cache.clear()` |

The `Node` object is the root of the tree-sitter AST and is consumed by callers to perform definition extraction, import extraction, and usage analysis. The `bytes` content is used by callers (e.g., `file_analyzer.py`) to decode and split source text into lines for further processing.

## 4. Key Data Structures

### `parse_cache`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute file path used as the unique cache identifier |
| value | `tuple[Node, bytes]` | Cached parse result: the AST root node and the raw binary file content |

### Return value of `parse_file`

| Position | Type | Purpose |
|---|---|---|
| `[0]` — `root_node` | `tree_sitter.Node` | Root node of the tree-sitter AST representing the full parsed file |
| `[1]` — `content` | `bytes` | Raw binary content of the file as read from disk |

### `_language_map`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File extension without leading dot (e.g., `"py"`, `"ts"`, `"js"`) |
| value | `tree_sitter.Language` | tree-sitter `Language` object used to initialize the parser for that extension |

# Error Handling

## 1. Overall Strategy

`ts_parser.py` adopts a **fail-fast** strategy. The module contains no explicit exception handling; all error conditions propagate immediately to the caller as unhandled exceptions. There is no retry logic, fallback mechanism, or logging-and-continue behavior within this file. The module assumes that preconditions (valid file path, supported extension, readable file) are satisfied by the caller.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | The file extension extracted from `file_path` is not present as a key in `_language_map` (i.e., the language is not registered in `TREE_SITTER_LANGUAGES`) | None — exception propagates to caller | No | Call stack unwinds; the calling operation (indexing, analysis, usage extraction) is aborted |
| `FileNotFoundError` / `OSError` | `file_path` does not exist on disk or is not readable when opened in binary mode | None — exception propagates to caller | No | Call stack unwinds; the file is not parsed and no result is cached |
| `TypeError` / Parser initialization error | `_language_map[ext]` returns a value that is not a valid `Language` object accepted by `Parser` | None — exception propagates to caller | No | Call stack unwinds; no parse result is produced |

---

## 3. Design Notes

- **No defensive guarding within the module.** The module relies entirely on the correctness of the configuration supplied by `settings.py` (`TREE_SITTER_LANGUAGES`) and on callers providing valid, supported file paths. This keeps the module minimal and places responsibility for precondition validation on upstream code.
- **Cache interaction under failure.** Because results are written to `parse_cache` only after successful parsing, a failed parse attempt leaves no partial or invalid entry in the cache. A subsequent call for the same path will re-attempt the full parse operation rather than returning a cached error state.
- **Module-level cache exposure.** `parse_cache` is publicly accessible, and callers (e.g., `pipeline.py`) explicitly invoke `parse_cache.clear()`. No error handling surrounds this operation within the module itself; any issues arising from concurrent or unexpected access are the caller's responsibility.

# Summary

**ts_parser.py** parses source files into tree-sitter ASTs for downstream analysis.

- `parse_file(file_path: str) → tuple[Node, bytes]`: reads a file, resolves language via extension lookup in `_language_map: dict[str, Language]`, parses with tree-sitter, caches and returns `(root_node, content)`.
- `parse_cache: dict[str, tuple[Node, bytes]]`: module-level cache keyed by absolute file path; clearable externally via `parse_cache.clear()`.
