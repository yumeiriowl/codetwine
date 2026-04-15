# Design Document: codetwine/parsers/ts_parser.py

# Overview & Purpose

## 1. Module Summary

Parses source files into tree-sitter ASTs and caches the results, providing a single entry point for obtaining a parsed syntax tree and raw byte content from any project file whose extension is registered in `TREE_SITTER_LANGUAGES`.

## 2. When to Use This Module

- **Extracting definitions from a file** – call `parse_file(abs_path)` and pass `result[0]` (the root `Node`) to a definition extractor (e.g., `extract_definitions`), as done in `import_to_path.py` and `usage_analysis.py`.
- **Analyzing file content alongside its AST** – call `parse_file(target_file)` and use both `result[0]` (root node) and `result[1]` (raw bytes, e.g., decoded to text lines) together, as done in `file_analyzer.py`.
- **Parsing import statements or building a dependency graph** – call `parse_file(file_path)` to obtain the root node and feed it to import extraction utilities, as done in `dependency_graph.py`.
- **Freeing cached parse results after a pipeline run** – access `parse_cache` directly and call `parse_cache.clear()` to release memory, as done in `pipeline.py`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file in binary mode, parses it with the tree-sitter `Parser` selected by file extension, and returns the AST root node paired with the raw byte content; results are served from `parse_cache` on repeated calls. |
| `parse_cache` | — (module-level `dict`) | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously parsed `(root_node, content)` tuples; can be cleared externally to release memory. |

## 4. Design Decisions

- **Module-level parse cache** – `parse_cache` is a plain `dict` held at module scope rather than inside `parse_file`. This makes it directly accessible to callers (e.g., `pipeline.py`) that need to clear it explicitly, while still providing transparent caching for every call to `parse_file` within a single process lifetime.
- **Extension-driven language dispatch** – the parser language is resolved solely from the file extension via `TREE_SITTER_LANGUAGES`, delegating all language-registration concerns to `codetwine/config/settings.py` and keeping this module free of per-language logic.

# Definition Design Specifications

---

## Module-Level Constants

### `_language_map`

| Property | Detail |
|---|---|
| Type | `dict[str, Language]` |
| Source | Alias of `TREE_SITTER_LANGUAGES` from `codetwine/config/settings.py` |

**Responsibility:** Provides a module-local reference to the extension-to-`Language`-object mapping, avoiding repeated imports and serving as the lookup table for all parser initialization within this module.

---

### `parse_cache`

| Property | Detail |
|---|---|
| Type | `dict[str, tuple[Node, bytes]]` — keys are absolute file paths (strings); values are two-element tuples of a tree-sitter AST root node and the raw binary file content |

**Responsibility:** Acts as a module-level memoization store so that any file parsed once is not re-parsed on subsequent calls during the same process lifetime.

**Constraints:**
- Must be explicitly cleared by callers (e.g., `parse_cache.clear()`) when memory should be reclaimed.
- Not thread-safe; concurrent writes from multiple threads could corrupt entries.

---

## Functions

### `parse_file`

**Signature:**
```python
def parse_file(file_path: str) -> tuple[Node, bytes]
```

- `file_path`: Absolute path string of the file to parse.
- Returns: A two-element tuple where the first element is the tree-sitter `Node` representing the AST root and the second element is the raw binary (`bytes`) content of the file.

**Responsibility:** Centralizes all file-reading and tree-sitter parsing logic for the project, ensuring every module obtains a consistent `(root_node, content)` pair from a single source.

**When to use:** Call this whenever any component needs a parsed AST or the raw byte content of a source file, rather than invoking tree-sitter directly.

**Design decisions:**

| Decision | Rationale |
|---|---|
| Cache keyed on `file_path` | Avoids redundant disk I/O and re-parsing when multiple callers request the same file in one pipeline run. |
| File opened in binary mode | tree-sitter operates on byte sequences; binary content is also returned directly to callers for text extraction. |
| `Parser` instantiated per call | A new `Parser` instance is created each invocation; no shared mutable parser state is retained between calls. |
| Language resolved from file extension | The extension (stripped of its leading dot) is used as the key into `_language_map`, delegating all language-to-extension configuration to `settings.py`. |

**Constraints & edge cases:**

- `file_path` must be an absolute path; no normalization or existence check is performed before opening.
- The file extension must exist as a key in `_language_map`; an absent extension raises a `KeyError`.
- If the file cannot be opened (permissions, missing file), a standard I/O exception propagates uncaught.
- The cache is never invalidated automatically; if file content changes on disk after the first parse, the stale cached result is returned.
- Files with no extension produce an empty string key after `lstrip(".")`, which will fail the `_language_map` lookup unless an empty-string key is registered.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py` : imports `TREE_SITTER_LANGUAGES` (a `dict[str, Language]`) to build the module-level `_language_map`, which is used to look up the appropriate tree-sitter `Language` object for a given file extension when initialising the `Parser`.

## Dependents (modules that import this file)

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to obtain the AST root node of a source file, which is then passed to `extract_definitions` in order to register definition names in a symbol-to-file mapping.

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to obtain both the AST root node and the raw byte content of the target file; the byte content is decoded and split into lines for source-code extraction alongside definition analysis.

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` : accesses the module-level `parse_cache` object directly in order to call `parse_cache.clear()` and free memory after a full analysis pipeline run.

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to parse both the target file (to enumerate its definitions) and each caller file (to extract import statements for further usage analysis).

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to parse callee files (to resolve attribute-access references) and caller files (to extract import statements for dependency-graph construction).

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py` is unidirectional; `settings.py` has no knowledge of `ts_parser.py`.
- Each dependent module → `codetwine/parsers/ts_parser.py` is unidirectional; `ts_parser.py` has no knowledge of any of its dependents (`import_to_path.py`, `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, `dependency_graph.py`).

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `file_path` | Caller argument | Absolute path string to a source file |
| `_language_map` | `TREE_SITTER_LANGUAGES` from `codetwine/config/settings.py` | `dict[str, Language]` mapping file extensions (without leading `.`) to tree-sitter `Language` objects |
| File content | Binary read from `file_path` | `bytes` |

## 2. Transformation Overview

```
file_path
    │
    ▼
[Cache Lookup] ──── hit ────► return cached (root_node, content)
    │
  miss
    │
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
  Parser(Language) → configured Parser instance
    │
    ▼
[File Read]
  open(file_path, "rb") → content: bytes
    │
    ▼
[Tree-sitter Parse]
  parser.parse(content) → Tree → tree.root_node: Node
    │
    ▼
[Cache Store & Return]
  parse_cache[file_path] = (root_node, content)
  return (root_node, content)
```

## 3. Outputs

| Output | Destination | Format |
|---|---|---|
| `(root_node, content)` return value | Callers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py`) | `tuple[Node, bytes]` — the AST root node and raw file bytes |
| `parse_cache` side effect | Module-level state; cleared externally by `pipeline.py` | `dict[str, tuple[Node, bytes]]` keyed by absolute file path |

## 4. Key Data Structures

### `parse_cache`

The module-level cache that stores already-parsed results.

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute file path used to identify a cached entry |
| value | `tuple[Node, bytes]` | The parsed AST root node and the raw binary file content for that path |

### Return value of `parse_file`

| Position | Type | Purpose |
|---|---|---|
| `[0]` — `root_node` | `tree_sitter.Node` | Root node of the AST produced by tree-sitter; used by callers to traverse and query syntax structure |
| `[1]` — `content` | `bytes` | Raw binary content of the parsed file; used by callers (e.g., `file_analyzer.py`) to reconstruct text lines |

### `_language_map`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File extension without leading `.` (e.g., `"py"`, `"ts"`) |
| value | `tree_sitter.Language` | The tree-sitter `Language` object used to initialize a `Parser` for that extension |

# Error Handling

## 1. Overall Strategy

`ts_parser.py` adopts a **fail-fast** strategy. The module contains no explicit exception handling; all errors propagate immediately to the caller as unhandled exceptions. There is no retry logic, fallback mechanism, or logging at this layer. The module assumes that preconditions (valid file paths, supported extensions, readable files) are satisfied by the caller.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | The file extension extracted from `file_path` is not present as a key in `_language_map` (i.e., the language is not registered in `TREE_SITTER_LANGUAGES`) | None — exception propagates to caller | No | Parsing aborted; caller receives unhandled `KeyError` |
| `FileNotFoundError` / `OSError` | The file at `file_path` does not exist or cannot be opened for reading in binary mode | None — exception propagates to caller | No | Parsing aborted; no cache entry is written |
| `Exception` (tree-sitter internal) | `parser.parse(content)` fails due to malformed or unprocessable content | None — exception propagates to caller | No | Parsing aborted; no cache entry is written |

---

## 3. Design Notes

- **No defensive guards at this layer.** The module delegates all validation responsibility to callers. Observed callers (e.g., `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) perform their own precondition checks (such as `os.path.isfile` and extension-to-dict lookups) before invoking `parse_file`, which is the intended guard boundary.
- **Cache atomicity by design.** Because the cache entry is written only after both file reading and parsing succeed, a failed parse never results in a poisoned cache entry. A subsequent call with the same path would retry the full operation rather than returning a corrupted result.
- **No partial recovery.** The absence of try-except blocks means that a single unsupported extension or unreadable file terminates the parsing operation entirely for that invocation, consistent with a fail-fast philosophy at the module boundary.

# Summary

**ts_parser.py** parses source files into tree-sitter ASTs and caches results. Public interface: `parse_file(file_path: str) -> tuple[Node, bytes]` returns the AST root node and raw file bytes; `parse_cache: dict[str, tuple[Node, bytes]]` is a module-level dict mapping absolute paths to cached parse results, clearable externally. Language selection uses `_language_map: dict[str, Language]` (alias of `TREE_SITTER_LANGUAGES`), keyed by file extension without leading dot.
