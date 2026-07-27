# Design Document: codetwine/parsers/ts_parser.py

# Overview & Purpose

`codetwine/parsers/ts_parser.py` provides a single, centralized entry point for parsing source files into tree-sitter ASTs. It exists as a dedicated module to isolate tree-sitter parser setup and file-reading logic from the rest of the codebase, and to provide a shared, memoized parse result so that multiple consumers (definition extraction, import resolution, usage analysis, dependency graph construction) can request the AST for the same file without redundant disk I/O or re-parsing.

The module resolves the appropriate `Language` object based on file extension using the `TREE_SITTER_LANGUAGES` mapping from `codetwine/config/settings.py`, reads the target file as bytes, and produces a tree-sitter root `Node` alongside the raw byte content, which downstream consumers use both for AST traversal and for slicing out source text by byte/line ranges.

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads the given file, parses it with the tree-sitter parser matching its extension, and returns the AST root node along with the raw file content (with results cached). |
| `parse_cache` | (module-level dict) `dict[str, tuple[Node, bytes]]` | — | Module-level cache mapping file paths to previously computed `(root_node, content)` results; exposed so callers (e.g., the pipeline) can explicitly clear it to free memory. |

### Design Decisions

- **Memoization via module-level cache**: `parse_file` checks `parse_cache` before parsing and stores results after parsing, avoiding repeated parsing of the same file across different analysis stages (definition extraction, import resolution, usage/dependency analysis) that all operate on overlapping sets of files.
- **Extension-based language dispatch**: The file extension is extracted via `os.path.splitext` and used to look up the correct `Language` object from `_language_map` (aliased from `TREE_SITTER_LANGUAGES`), keeping language-specific configuration fully delegated to `codetwine/config/settings.py` and out of this parsing module.
- **Explicit cache lifecycle control**: The cache is exposed as a public module-level object (`parse_cache`) rather than hidden behind an internal variable, allowing external code (e.g., the pipeline) to explicitly clear it (`parse_cache.clear()`) once analysis completes to free memory.
- **Binary file reading**: Files are read in binary mode (`"rb"`) since tree-sitter operates on byte content, and this raw byte content is also returned so callers can perform byte/line-accurate slicing of source text.

# Definition Design Specifications

## `parse_cache: dict[str, tuple[Node, bytes]]`

Module-level dictionary mapping absolute file paths to their parsed `(root_node, content)` results.

- **Responsibility**: Acts as a process-wide memoization store so that repeated requests to parse the same file (from multiple consumer modules such as `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, and `dependency_graph.py`) avoid redundant I/O and re-parsing.
- **Design decision**: Exposed at module level (rather than hidden inside a class) so that external code (e.g., `pipeline.py`) can explicitly call `parse_cache.clear()` to release memory once analysis is complete, since parsed ASTs and raw file bytes can be large and are only needed during the analysis phase.
- **Constraint**: Keyed by the raw `file_path` string passed to `parse_file`; callers must pass consistent (e.g., absolute) paths for cache hits to work correctly, as no path normalization is performed.

## `parse_file(file_path: str) -> tuple[Node, bytes]`

- **Arguments**:
  - `file_path`: Absolute path of the source file to parse.
- **Returns**: A tuple `(root_node, content)` where `root_node` is the tree-sitter AST root (`Node`) and `content` is the raw file bytes read from disk.
- **Responsibility**: Provides a single, cached entry point for turning a source file into a tree-sitter AST plus its raw byte content, decoupling all downstream extraction/analysis logic (definitions, imports, usages) from parser setup and language selection details.
- **Design decisions**:
  - Uses the file extension (via `os.path.splitext`, stripped of the leading dot) as the lookup key into `TREE_SITTER_LANGUAGES` to select the appropriate `Language` grammar, keeping language dispatch declarative and centralized in `settings.py` rather than hardcoded here.
  - Returns raw bytes (not decoded text) alongside the AST because tree-sitter node byte offsets are defined against the original byte content; callers needing text (e.g., `file_analyzer.py`) are responsible for decoding it themselves.
  - Reads the cache before doing any extension lookup or file I/O, making repeated calls for the same path effectively O(1) after the first parse.
- **Edge cases / constraints**:
  - Assumes `file_path`'s extension exists as a key in `_language_map` (`TREE_SITTER_LANGUAGES`); an unsupported/unknown extension will raise a `KeyError` since no fallback or error handling is implemented.
  - Assumes the file exists and is readable; no explicit handling of `FileNotFoundError` or decoding errors (file is read in binary mode, so encoding issues are deferred to callers).
  - Not safe for concurrent mutation of `parse_cache` in a multi-threaded context, since dictionary access/insertion is unsynchronized.

# Dependency Description

### Dependencies (what this file uses)

- **`TREE_SITTER_LANGUAGES` (from `codetwine/config/settings.py`)**: Used as a mapping from file extension to the corresponding tree-sitter `Language` object. `ts_parser.py` relies on this map to select the correct grammar for initializing the `Parser` based on the extension of the file being parsed, enabling language-agnostic parsing across multiple supported languages.

### Dependents (what uses this file)

- **`codetwine/file_analyzer.py`**: Calls `parse_file` to obtain the AST root node and raw file content for a target file, which is then used together with per-language definition extraction settings to extract source code for each definition.
- **`codetwine/import_to_path.py`**: Calls `parse_file` to parse a resolved file path and obtain its AST root node, which is then used to extract definitions and register their names into a symbol-to-file mapping.
- **`codetwine/pipeline.py`**: Calls `parse_cache.clear()` to clear the module-level parse result cache after analysis completes, freeing memory.
- **`codetwine/extractors/usage_analysis.py`**: Calls `parse_file` to parse target and caller files, using the resulting AST root nodes to extract definition names and to analyze import statements/usages for dependency tracking.
- **`codetwine/extractors/dependency_graph.py`**: Calls `parse_file` to parse callee and caller files, using the resulting AST root nodes to resolve definitions and extract import information for building the dependency graph.

The dependency direction is unidirectional: `ts_parser.py` depends on `codetwine/config/settings.py` for language configuration, while the listed files depend on `ts_parser.py` for file parsing and cache management functionality. There is no reverse dependency from `ts_parser.py` back to any of its dependents.

# Data Flow

**Input**
- `file_path: str` — absolute path to a source file, supplied by callers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`).

**Processing Flow**

```
file_path
   │
   ▼
[cache lookup] ── hit ──► return cached (Node, bytes)
   │ miss
   ▼
extract extension ──► lookup Language in TREE_SITTER_LANGUAGES (_language_map)
   │
   ▼
instantiate tree_sitter.Parser(language)
   │
   ▼
read file as raw bytes (binary mode)
   │
   ▼
parser.parse(content) ──► tree_sitter Tree
   │
   ▼
extract tree.root_node ──► build result tuple (root_node, content)
   │
   ▼
store result in parse_cache[file_path]
   │
   ▼
return result
```

- The extension string (e.g. `"py"`, `"ts"`) is used purely as a key to select the correct tree-sitter `Language` from `_language_map`.
- Raw file bytes are fed directly into tree-sitter's parser without any pre-transformation; tree-sitter internally converts them into an AST (`Node` tree).
- No transformation is applied to the parsed node itself — the file’s byte content and its AST root are simply paired and cached as-is.

**Output**
- Return value: `tuple[Node, bytes]`
  - `Node`: root of the tree-sitter AST for the file, used by callers to run queries/extract definitions or imports.
  - `bytes`: raw file content, used by callers (e.g. `file_analyzer.py`) to decode into text and slice source lines by byte/row ranges.
- Destination: returned directly to caller functions; also persisted in the module-level `parse_cache`.

**Main Data Structures**

| Structure | Type | Key | Value | Purpose |
|---|---|---|---|---|
| `_language_map` | `dict[str, Language]` | file extension (no dot) | tree-sitter `Language` object | Resolves correct grammar/parser per file type |
| `parse_cache` | `dict[str, tuple[Node, bytes]]` | absolute file path | `(root_node, content)` | Avoids re-parsing the same file across multiple callers/pipeline stages; cleared explicitly at pipeline end (`parse_cache.clear()`) |

# Error Handling

## Overall Strategy

This module follows a **fail-fast** approach with no explicit exception handling. It performs no validation, try/except blocks, or defensive checks of its own, relying entirely on Python's built-in exceptions and the behavior of underlying dependencies (`os.path`, `Parser`, file I/O, and `TREE_SITTER_LANGUAGES`) to surface errors. Any failure condition propagates directly to the caller as an unhandled exception.

## Error Patterns and Handling Policy

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported file extension (extension not present in `TREE_SITTER_LANGUAGES`) | No handling; dictionary lookup (`_language_map[ext]`) raises `KeyError` naturally | Propagates to caller; `parse_file` call fails entirely for that file |
| Non-existent or unreadable file path | No handling; `open(file_path, "rb")` raises `FileNotFoundError`/`OSError` naturally | Propagates to caller; no partial or cached result is stored |
| Malformed/unparseable source content | No handling; tree-sitter's `Parser.parse` is relied upon to always return a tree (even if partially error-recovered), so no exception is expected here | If tree-sitter returns an error-recovered tree, it is silently cached and returned as-is without validation |
| Cache key collisions or stale cache entries | No handling; `file_path` is used verbatim as the cache key with no normalization | Callers must supply consistent absolute paths; inconsistent path forms bypass the cache without warning |

## Design Considerations

- The module intentionally keeps parsing logic minimal and delegates all error responsibility to the caller and to the underlying `tree_sitter` library and OS-level file operations.
- The module-level `parse_cache` only stores successful parse results; since exceptions are raised before the cache is populated, failed parses are never cached, so a subsequent call with the same path will retry from scratch.
- No logging or error wrapping is performed within this file, meaning error diagnostics rely entirely on Python's default traceback and the semantics of the raised exception types (`KeyError`, `FileNotFoundError`, `OSError`, etc.).

# Summary

`ts_parser.py` centralizes tree-sitter parsing: `parse_file(file_path)` reads a file as bytes, selects a `Language` by extension via `TREE_SITTER_LANGUAGES`, parses it, and returns `(root_node, content)`. Results are memoized in the module-level dict `parse_cache` (keyed by exact file_path) to avoid redundant I/O/parsing across consumers (definition extraction, import resolution, usage/dependency analysis); `pipeline.py` calls `parse_cache.clear()` post-analysis. No error handling—unsupported extensions, missing files, or unsynchronized concurrent access raise natural exceptions (`KeyError`, `FileNotFoundError`).
