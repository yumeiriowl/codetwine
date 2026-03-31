# Design Document: codetwine/parsers/ts_parser.py

## Overview & Purpose

## 1. Module Summary

Parses source files using tree-sitter and returns the resulting AST root node together with the raw byte content, caching results at module level to avoid redundant parsing.

## 2. When to Use This Module

- **Extracting definitions from a file**: Call `parse_file(abs_path)` and use the returned root `Node` as input to definition-extraction utilities (e.g., `extract_definitions`).
- **Analyzing file content alongside its AST**: Call `parse_file(target_file)` to receive both the root `Node` and the `bytes` content, then decode the content for line-level operations (e.g., retrieving source code for each definition's line range).
- **Resolving imports and usage relationships**: Call `parse_file(abs_path)` to obtain the root `Node` for import extraction or usage analysis across caller and callee files.
- **Freeing cached memory after a pipeline run**: Access `parse_cache` directly and call `parse_cache.clear()` to release all cached parse results.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file in binary mode, parses it with the tree-sitter language matching the file extension, and returns the AST root node and raw byte content; results are cached by file path. |
| `parse_cache` | — (module-level `dict`) | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously computed `(root_node, content)` results; can be cleared externally to free memory. |

## 4. Design Decisions

- **Module-level cache (`parse_cache`)**: Parse results are stored in a plain module-level dictionary rather than inside `parse_file` itself, making the cache accessible to external callers (e.g., `pipeline.py`) for explicit invalidation via `parse_cache.clear()` without requiring any additional API.
- **Language resolution via file extension**: The tree-sitter `Language` object is selected solely from the file extension using the `TREE_SITTER_LANGUAGES` mapping imported from `settings.py`, keeping language configuration centralized in one place and decoupled from this module.

## Definition Design Specifications

---

## Module-Level Constants

### `_language_map`

| Property | Detail |
|---|---|
| Type | `dict[str, Language]` |
| Source | Alias for `TREE_SITTER_LANGUAGES` from `codetwine/config/settings.py` |

**Responsibility:** Provides a module-local reference to the extension-to-`Language`-object mapping, allowing `parse_file` to look up the correct tree-sitter `Language` for any supported file extension.

---

### `parse_cache`

| Property | Detail |
|---|---|
| Type | `dict[str, tuple[Node, bytes]]` |
| Keys | Absolute file path strings |
| Values | A 2-tuple of `(AST root Node, raw byte content)` |

**Responsibility:** Acts as a module-level memoization store so that any file parsed once is not re-parsed on subsequent calls within the same process lifetime.

**Constraints & edge cases:**
- The cache is never automatically invalidated; if a file changes on disk after its first parse, the stale result will be returned.
- Callers (e.g., `pipeline.py`) are responsible for calling `parse_cache.clear()` to release memory when the cached data is no longer needed.

---

## Functions

### `parse_file`

**Signature:**
```python
def parse_file(file_path: str) -> tuple[Node, bytes]
```

- `file_path`: Absolute path to the source file to be parsed.
- Returns `tuple[Node, bytes]`: A 2-tuple where `Node` is the tree-sitter AST root node for the file and `bytes` is the raw binary content of the file.

**Responsibility:** Reads a source file from disk, selects the appropriate tree-sitter `Language` by file extension, constructs a `Parser`, and returns the resulting AST root node together with the file's byte content. Caches the result in `parse_cache` to avoid redundant I/O and parsing on repeated calls for the same path.

**When to use:** Call whenever another module needs the tree-sitter AST or raw content of a source file, such as during definition extraction, import analysis, or usage analysis.

**Design decisions:**
- **Cache-first lookup:** If the `file_path` key already exists in `parse_cache`, the function returns immediately without touching the filesystem or constructing a `Parser`.
- **Extension-based language resolution:** The file extension (stripped of its leading dot) is used as the lookup key into `_language_map`, delegating all language-to-extension configuration to `settings.py`.
- **Binary file read:** The file is opened in binary mode, matching tree-sitter's expectation of `bytes` input and preserving the original encoding for downstream byte-offset operations.

**Constraints & edge cases:**
- `file_path` must be an absolute path; the function performs no path normalization or existence checks before opening the file.
- The file's extension must be a key present in `_language_map`; an unrecognized extension will raise a `KeyError`.
- The file must be readable; any I/O error will propagate to the caller.
- Results are cached under the exact string provided as `file_path`; two strings referring to the same file but differing in representation (e.g., with/without trailing separator) are treated as distinct cache entries.

## Dependency Description

## Dependencies (modules this file imports)

- `codetwine/parsers/ts_parser_py/ts_parser.py` → `codetwine/config/settings.py` : imports `TREE_SITTER_LANGUAGES` (a `dict[str, Language]`) to obtain the mapping from file extension strings to tree-sitter `Language` objects, which is used to initialize the correct `Parser` instance for a given file type.

## Dependents (modules that import this file)

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain the AST root node of a source file, which is then passed to `extract_definitions` to register symbol-to-file mappings.

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain both the AST root node and the raw byte content of the target file, using the root node for definition extraction and the byte content for reconstructing source text lines.

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : accesses `parse_cache` directly and calls `parse_cache.clear()` after pipeline completion to free memory held by cached parse results.

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain AST root nodes for both target files (to extract definition names) and caller files (to extract import statements for usage analysis).

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser_py/ts_parser.py` : calls `parse_file` to obtain AST root nodes for callee files (to resolve definition references) and for files under analysis (to extract and resolve import statements when building the dependency graph).

## Dependency Direction

All relationships are **unidirectional**:

- `ts_parser.py` → `codetwine/config/settings.py` : one-way; `ts_parser.py` consumes `TREE_SITTER_LANGUAGES` from `settings.py`, and `settings.py` has no knowledge of `ts_parser.py`.
- Each dependent module → `ts_parser.py` : one-way; the dependent modules consume `parse_file` and/or `parse_cache` from `ts_parser.py`, while `ts_parser.py` has no knowledge of any of those dependent modules.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `file_path` | Caller argument | Absolute path string to a source code file |
| File content | Binary file read from `file_path` | `bytes` |
| `TREE_SITTER_LANGUAGES` | Imported from `codetwine/config/settings.py` | `dict[str, Language]` mapping file extension strings to tree-sitter `Language` objects |

The file extension is derived from `file_path` by stripping the leading dot (e.g., `"py"`, `"ts"`), and used as the lookup key into `_language_map`.

---

## 2. Transformation Overview

```
file_path
    │
    ▼
[Cache Lookup] ──── hit ──────────────────────────────────► (root_node, content)
    │
   miss
    │
    ▼
[Extension Extraction]
  os.path.splitext(file_path)[1].lstrip(".")
  → ext: str
    │
    ▼
[Language Resolution]
  _language_map[ext]
  → Language object
    │
    ▼
[Parser Initialization]
  Parser(Language)
  → parser: Parser
    │
    ▼
[File Read]
  open(file_path, "rb").read()
  → content: bytes
    │
    ▼
[Tree-sitter Parsing]
  parser.parse(content)
  → tree: Tree → tree.root_node: Node
    │
    ▼
[Cache Store]
  parse_cache[file_path] = (root_node, content)
    │
    ▼
  (root_node, content)
```

On a cache miss, the pipeline proceeds through all stages sequentially. On a cache hit, the stored tuple is returned immediately, bypassing file I/O and parsing entirely.

---

## 3. Outputs

| Output | Type | Description |
|---|---|---|
| Return value | `tuple[Node, bytes]` | `(root_node, content)` — the AST root node and the raw binary file content |
| `parse_cache` side effect | `dict[str, tuple[Node, bytes]]` | Module-level cache is populated with the result keyed by `file_path` |

Callers use the returned tuple in two ways:
- `parse_file(path)[0]` — access only the `Node` for AST traversal and definition/usage extraction.
- `root_node, content = parse_file(path)` — use both the `Node` and the `bytes` content (e.g., decoding to text lines for source code extraction).

The cache can be explicitly cleared by callers via `parse_cache.clear()` (as done in `pipeline.py` after analysis completes) to free memory.

---

## 4. Key Data Structures

### `parse_cache`

The module-level cache that stores parse results indexed by file path.

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute file path used as the cache key |
| value | `tuple[Node, bytes]` | Tuple of `(root_node, content)` — the tree-sitter AST root and the raw file bytes |

### Return value tuple

| Index | Type | Purpose |
|---|---|---|
| `[0]` — `root_node` | `tree_sitter.Node` | Root node of the parsed AST; used by callers for tree traversal, definition extraction, and usage analysis |
| `[1]` — `content` | `bytes` | Raw binary content of the file; used by callers for decoding to text (e.g., extracting source lines) |

### `_language_map`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File extension without leading dot (e.g., `"py"`, `"ts"`) |
| value | `tree_sitter.Language` | The tree-sitter `Language` object used to initialize the parser for that extension |

## Error Handling

## 1. Overall Strategy

This module adopts a **fail-fast** strategy with no explicit error handling. All errors propagate immediately to the caller as unhandled exceptions. There are no try-except blocks, fallback mechanisms, retry logic, or logging of error conditions within the module itself. The module assumes that inputs (file paths, file system state, and language registry contents) are valid and consistent at the time of invocation.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `KeyError` | The file extension extracted from `file_path` is not present as a key in `_language_map` (i.e., `TREE_SITTER_LANGUAGES`) | None — exception propagates to caller | No | Call to `parse_file` terminates with an unhandled exception |
| `FileNotFoundError` | The file at `file_path` does not exist when opened in binary mode | None — exception propagates to caller | No | Call to `parse_file` terminates with an unhandled exception |
| `OSError` / `PermissionError` | The file at `file_path` exists but cannot be read due to OS-level access restrictions | None — exception propagates to caller | No | Call to `parse_file` terminates with an unhandled exception |
| `Exception` (tree-sitter internal) | `parser.parse(content)` fails due to an internal tree-sitter error | None — exception propagates to caller | No | Call to `parse_file` terminates with an unhandled exception |

---

## 3. Design Notes

- **No defensive guards at the module boundary**: The module performs no pre-validation of the file path, file existence, or extension support before attempting the operation. Responsibility for providing valid inputs is delegated entirely to callers.
- **Cache is written only on success**: Because there are no try-except blocks, the `parse_cache` dictionary is populated only when all steps — extension lookup, file read, and parsing — complete without error. A failed parse leaves no partial or invalid entry in the cache.
- **Error context is not enriched**: Exceptions are not caught and re-raised with additional context (e.g., the file path or extension that caused the failure), meaning callers receive raw exceptions from the standard library or tree-sitter.
- **Implicit trust in `TREE_SITTER_LANGUAGES`**: The module directly indexes into `_language_map` without checking for key presence, reflecting a design assumption that the settings layer guarantees a complete and correct extension-to-language mapping for all files that will be passed to this module.

## Summary

`ts_parser.py` parses source files via tree-sitter and caches results. `parse_file(file_path: str) -> tuple[Node, bytes]` reads a file, resolves its tree-sitter `Language` from `_language_map` (`dict[str, Language]`, aliased from `TREE_SITTER_LANGUAGES`) by file extension, parses it, and returns `(root_node, content)`. Results are stored in `parse_cache: dict[str, tuple[Node, bytes]]`, keyed by absolute file path, which callers can clear via `parse_cache.clear()`.
