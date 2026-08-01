# Design Document: codetwine/parsers/ts_parser.py

# Overview & Purpose

`ts_parser.py` provides a single, centralized entry point for parsing source files into tree-sitter ASTs across the CodeTwine project. It exists as its own module to decouple the mechanics of language detection, tree-sitter `Parser` initialization, and file reading from the higher-level analysis logic (definition extraction, import resolution, usage/dependency graph building) that depends on parsed ASTs. By isolating this concern, all consumers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`, `pipeline.py`) share one consistent parsing implementation and one cache, ensuring the same file is never parsed more than once during a run.

The module relies on `TREE_SITTER_LANGUAGES` (from `codetwine/config/settings.py`) to map file extensions to the appropriate tree-sitter `Language` object, keeping language-specific configuration out of the parser itself.

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_file(file_path: str)` | `file_path`: absolute path of the file to parse | `tuple[Node, bytes]` — (AST root node, raw file byte content) | Reads a file, parses it with the tree-sitter `Parser` selected via file extension, caches the result, and returns the root AST node plus raw content |
| `parse_cache: dict[str, tuple[Node, bytes]]` | — | Module-level dict mapping file path → (root node, content) | Exposes the shared parse cache so other modules (e.g., `pipeline.py`) can clear it to free memory after analysis completes |

### Design Decisions

- **Module-level caching**: `parse_cache` memoizes parse results keyed by `file_path`, avoiding redundant disk reads and re-parsing when multiple parts of the pipeline (definition extraction, import resolution, usage/dependency analysis) need the AST for the same file. The cache is intentionally exposed at module scope so external code (`pipeline.py`) can explicitly clear it once analysis is complete.
- **Extension-based language dispatch**: The file extension (derived via `os.path.splitext`) is used to look up the correct tree-sitter `Language` from `TREE_SITTER_LANGUAGES`, delegating all language-specific configuration to `settings.py` and keeping this file purely mechanical (I/O + parsing + caching).
- **Binary file reading**: Content is read as raw bytes (`"rb"` mode) since tree-sitter's `Parser.parse` operates on byte content, and the same byte content is returned alongside the AST for downstream consumers that need to slice source text by byte/line ranges.

# Definition Design Specifications

## `parse_file`

Parses a source file into a tree-sitter AST and returns the root node along with the raw file bytes, memoizing results at module scope so repeated requests for the same path skip re-reading and re-parsing.

**Arguments:**
- `file_path` (`str`): Absolute path to the source file to parse. Used both as the file to read and as the cache key.

**Returns:**
- `tuple[Node, bytes]`: A pair of the parsed AST's root `Node` and the exact byte content read from disk. Returning raw bytes alongside the node lets callers slice source text using tree-sitter's byte-offset-based node ranges without re-reading the file.

**Design intent:** Centralizes file reading and tree-sitter parsing so that all consumers (definition extraction, import resolution, usage/dependency analysis) share a single, consistent entry point and avoid redundant I/O/parsing for files referenced from multiple analysis passes across the pipeline.

**Design decisions:**
- The cache is a simple module-level `dict` keyed by `file_path`, not an LRU or size-bounded cache; it assumes the file set analyzed in one run is small enough to fully retain in memory, and it must be explicitly cleared by the caller (e.g., `pipeline.py` calls `parse_cache.clear()` at the end of analysis) to free memory between runs.
- Language selection is derived purely from the file extension (via `_language_map`/`TREE_SITTER_LANGUAGES`), keeping this function decoupled from any language-specific logic.
- Content is read in binary mode and returned as `bytes` rather than decoded text, matching tree-sitter's expectation of byte content and preserving exact byte offsets for downstream node-to-text extraction.

**Edge cases/constraints:**
- The file extension must exist as a key in `_language_map`; otherwise a `KeyError` is raised when constructing the `Parser`.
- No handling for missing files or unreadable content—file I/O errors propagate to the caller.
- Not thread-safe: concurrent calls for the same uncached `file_path` could result in duplicate parsing work (last write wins in the cache), since there is no locking around the cache read/write.

# Dependency Description

### Dependencies (what this file uses)

- **codetwine/config/settings.py** (`TREE_SITTER_LANGUAGES`): This file relies on `TREE_SITTER_LANGUAGES` to map a file's extension to its corresponding tree-sitter `Language` object. This mapping is essential for instantiating the correct `Parser` for the file being parsed, since tree-sitter requires a language-specific grammar to build an AST.

### Dependents (what uses this file)

- **codetwine/file_analyzer.py**: Uses `parse_file` to obtain the AST root node and raw file content, which are then used to extract source code definitions and their corresponding line ranges.
- **codetwine/import_to_path.py**: Uses `parse_file` to parse a file and retrieve its AST root node, which is then passed to definition extraction logic to register symbol names for import-to-path resolution.
- **codetwine/pipeline.py**: Uses `parse_cache.clear()` to release the module-level parse cache after the analysis pipeline completes, freeing memory.
- **codetwine/extractors/usage_analysis.py**: Uses `parse_file` to parse both target and caller files, extracting their AST root nodes to identify definitions and imports for usage analysis.
- **codetwine/extractors/dependency_graph.py**: Uses `parse_file` to parse callee and source files, obtaining AST root nodes needed to resolve dependency relationships and import statements between project files.

**Direction of dependency**: Unidirectional — `ts_parser.py` depends on `codetwine/config/settings.py` for language configuration, while multiple other modules (`file_analyzer.py`, `import_to_path.py`, `pipeline.py`, `usage_analysis.py`, `dependency_graph.py`) depend on `ts_parser.py` for file parsing and cache management. There is no reverse dependency from `settings.py` back to `ts_parser.py`.

# Data Flow

## Input
- **Source**: `file_path` (absolute path string) passed by callers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`).
- **Format**: plain string representing a filesystem path to a source code file.

## Transformation Flow

```
file_path
   │
   ├─► [Cache Lookup] parse_cache.get(file_path)
   │        │
   │        ├─ hit  ──► return cached (root_node, content)
   │        │
   │        └─ miss ──► continue below
   │
   ├─► [Extension Extraction] os.path.splitext(file_path) → ext
   │
   ├─► [Language Resolution] _language_map[ext] (TREE_SITTER_LANGUAGES lookup)
   │        └─► instantiate tree_sitter.Parser(language)
   │
   ├─► [File Read] open(file_path, "rb") → raw bytes content
   │
   ├─► [Parse] parser.parse(content) → tree
   │        └─► extract tree.root_node
   │
   ├─► [Result Assembly] result = (root_node, content)
   │
   └─► [Cache Store] parse_cache[file_path] = result
            │
            └─► return result
```

Core transformation: `file_path (str)` → `ext (str)` → `Language object` → `Parser instance` → `raw bytes` → `AST (root_node)` → `(root_node, content) tuple`.

## Output
- **Format**: `tuple[Node, bytes]` — `(root_node, content)`
  - `root_node`: tree-sitter `Node`, the AST root, used by dependents for definition/import extraction (`extract_definitions`, `extract_imports`, etc.).
  - `content`: raw file bytes, later decoded to text lines by callers (e.g., `content.decode("utf-8").splitlines()`).
- **Destination**: returned directly to calling functions across the codebase; not persisted to disk.

## Key Data Structures

| Structure | Type | Fields / Structure | Purpose |
|---|---|---|---|
| `_language_map` | `dict[str, Language]` (alias of `TREE_SITTER_LANGUAGES`) | key: file extension (str), value: tree-sitter `Language` object | Resolves which grammar/language to use for parsing based on file extension |
| `parse_cache` | `dict[str, tuple[Node, bytes]]` | key: `file_path` (str), value: `(root_node, content)` | Module-level memoization to avoid re-parsing the same file across multiple calls/modules; cleared externally via `parse_cache.clear()` (in `pipeline.py`) to free memory after analysis completes |
| return value `result` | `tuple[Node, bytes]` | `(root_node, content)` | Standard unit of parsed output consumed by all downstream extractors |

# Error Handling

## Overall Strategy

`ts_parser.py` follows a **fail-fast, no-defensive-handling** policy. It performs no explicit try/except blocks and does not validate inputs before use. All error conditions—unsupported file extensions, unreadable files, or missing parser configuration—are allowed to propagate as native Python exceptions raised by underlying operations (dictionary lookups, file I/O, tree-sitter parsing). This delegates responsibility for error handling to callers (e.g., `pipeline.py`, `file_analyzer.py`, `import_to_path.py`, extractors), keeping the module itself minimal and predictable.

## Main Error Patterns

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported/unknown file extension (not present in `TREE_SITTER_LANGUAGES`) | No existence check; direct dictionary access via `_language_map[ext]` | Raises a `KeyError`, propagated to the caller; no fallback parser or default language is used |
| File not found or unreadable (`open(file_path, "rb")`) | No pre-check of file existence/permissions | Raises `FileNotFoundError`/`OSError`, propagated uncaught |
| Malformed/invalid source content passed to tree-sitter | No validation of content before parsing | tree-sitter's `parser.parse` is generally tolerant and produces a best-effort AST (possibly with error nodes) rather than raising; no additional handling is applied on top of this |
| Repeated parse requests for the same file | Cache lookup (`parse_cache`) short-circuits re-parsing | Avoids redundant I/O/parsing; if a prior call failed before caching, no stale/error result is stored since caching happens only after successful completion of read+parse |

## Design Considerations

- The absence of explicit error handling is a deliberate simplicity choice: this module acts as a thin, cacheable wrapper around tree-sitter parsing, and pushes decisions about how to react to missing languages or unreadable files to higher-level orchestration code.
- Because `parse_cache` is a module-level dict, only successful `(root_node, content)` results are stored; a call that raises an exception (e.g., `KeyError` or `OSError`) does not populate the cache, so subsequent calls for the same `file_path` will retry the full read-and-parse path rather than returning a cached error.
- Cache invalidation/clearing is handled externally (e.g., `pipeline.py` calls `parse_cache.clear()`), meaning this file has no internal mechanism to expire or bound the cache; long-running processes rely on callers to manage memory via this exposed hook.

# Summary

`ts_parser.py` centralizes tree-sitter AST parsing for CodeTwine. Its core interface, `parse_file(file_path)`, reads a file as bytes, selects a tree-sitter `Language` via extension lookup in `TREE_SITTER_LANGUAGES`, parses it, and returns `(root_node, content)`. Results are memoized in module-level `parse_cache` (dict keyed by file_path), shared across consumers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`) to avoid redundant I/O/parsing. No error handling—unsupported extensions or unreadable files raise natively. `pipeline.py` clears the cache post-analysis.
