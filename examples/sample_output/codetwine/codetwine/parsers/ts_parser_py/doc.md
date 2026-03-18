# Design Document: codetwine/parsers/ts_parser.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`ts_parser.py` serves as the centralized tree-sitter parsing layer for the CodeTwine project. Its sole responsibility is to read source files from disk, parse them into Abstract Syntax Trees (ASTs) using tree-sitter, and return the resulting root node together with the raw byte content. By isolating this parsing concern in a dedicated module, the rest of the codebase (file analyzer, dependency graph extractor, usage analyzer, import mapper, and pipeline orchestrator) can obtain ASTs through a single, uniform interface without duplicating file I/O or parser initialization logic.

The module bridges the project's language configuration (`TREE_SITTER_LANGUAGES` from `settings.py`, which maps file extensions to tree-sitter `Language` objects) and the consumers that need parsed ASTs. It determines which tree-sitter `Language` to use by inspecting the file extension, constructs a `Parser` instance accordingly, and reads the file in binary mode as required by tree-sitter.

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file, parses it with tree-sitter using the language inferred from the file extension, caches the result, and returns the AST root node and raw byte content. |
| `parse_cache` | — | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their previously computed `(root_node, content)` tuples; exposed so external callers (e.g., the pipeline) can clear it to free memory. |

## Design Decisions

- **Module-level result cache**: `parse_cache` is a plain `dict` at module scope. Because multiple consumers (dependency graph, usage analysis, file analyzer, import mapper) independently call `parse_file` on the same files during a single pipeline run, caching avoids redundant disk reads and repeated parser invocations. The cache is intentionally exposed publicly so the pipeline can call `parse_cache.clear()` after analysis completes to reclaim memory.
- **Extension-driven language dispatch**: Rather than requiring callers to specify a language, `parse_file` derives the tree-sitter `Language` from the file extension via the module-level `_language_map` (a reference to `TREE_SITTER_LANGUAGES`). This keeps the interface minimal and ensures all language configuration remains centralized in `settings.py`.
- **Binary file I/O**: Files are read in `"rb"` mode and passed directly to tree-sitter, which operates on bytes, avoiding any encoding conversion at the parsing stage. The raw `bytes` are returned alongside the AST so consumers can perform text-level operations (e.g., line extraction) by decoding themselves.

## Definition Design Specifications

# Definition Design Specifications

## Module-Level Variables

### `_language_map`
A module-level alias for `TREE_SITTER_LANGUAGES` imported from `settings.py`. Holds a `dict[str, Language]` mapping file extensions (e.g., `"py"`, `"ts"`) to their corresponding tree-sitter `Language` objects. Exists to give the module a local reference to the extension-to-language mapping without repeatedly referencing the imported name.

### `parse_cache`
A module-level `dict[str, tuple[Node, bytes]]` keyed by absolute file path, storing previously computed `(root_node, content)` pairs. Shared across all callers in the same process; cleared explicitly by pipeline consumers (e.g., `pipeline.py`) when memory should be reclaimed.

---

## Functions

### `parse_file`

**Signature:** `parse_file(file_path: str) -> tuple[Node, bytes]`

**Arguments:**
- `file_path`: The absolute path of the source file to parse. The file extension (after stripping the leading `.`) must exist as a key in `_language_map`; otherwise a `KeyError` is raised.

**Return value:** A `(root_node, content)` tuple where `root_node` is the tree-sitter AST root `Node` for the file, and `content` is the raw byte string of the file as read from disk.

**Responsibility / design intent:** Provides a single, cached entry point for obtaining a tree-sitter AST for any supported source file. Caching avoids repeated disk reads and re-parsing when multiple analysis passes (definition extraction, import resolution, usage analysis) consume the same file.

**Important design decisions:**
- The cache key is the raw `file_path` string as supplied by the caller, meaning callers are responsible for supplying a consistent (e.g., always absolute) path to ensure cache hits are reliable.
- The file is read in binary mode and passed directly to the tree-sitter parser; the raw `bytes` content is also returned so callers can perform their own text-level operations (e.g., line splitting) without re-reading the file.
- The `Parser` is instantiated per call (not reused), initialized with the `Language` object looked up by file extension.

**Edge cases and constraints:**
- If `file_path` is not in `parse_cache` and the derived extension is not present in `_language_map`, a `KeyError` is raised; no explicit fallback or error handling is provided.
- If the file does not exist or cannot be read, the standard `open` call raises an `OSError`; this is not caught.
- The cache is never invalidated automatically—if the file changes on disk after it has been parsed, the cached result will be stale until `parse_cache.clear()` is called externally.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **`codetwine/config/settings.py` (`TREE_SITTER_LANGUAGES`)**: Used to obtain the mapping from file extensions to tree-sitter `Language` objects. This mapping is assigned to the module-level `_language_map` and is looked up during parsing to select the correct language parser for a given file extension.

---

### Dependents (what uses this file)

- **`codetwine/import_to_path.py`**: Uses `parse_file` to obtain the AST root node of a source file, which is then passed to definition extraction logic to register symbol names in a symbol-to-file mapping.

- **`codetwine/file_analyzer.py`**: Uses `parse_file` to obtain both the AST root node and the raw byte content of a target file. The byte content is decoded and split into lines for source code extraction, while the root node is used for definition extraction.

- **`codetwine/pipeline.py`**: Uses `parse_cache.clear()` to release cached parse results and free memory after a full analysis pipeline run completes.

- **`codetwine/extractors/usage_analysis.py`**: Uses `parse_file` to obtain AST root nodes for both target files (to extract their defined names) and caller files (to extract import information for usage analysis).

- **`codetwine/extractors/dependency_graph.py`**: Uses `parse_file` to obtain AST root nodes for callee files (to analyze definitions) and for files being scanned for import statements during dependency graph construction.

**Direction of dependency**: All dependencies are unidirectional. This file consumes `TREE_SITTER_LANGUAGES` from `settings.py`, and all dependent files consume `parse_file` and `parse_cache` from this file. No circular dependencies exist.

## Data Flow

# Data Flow

## Input Data Format and Source

| Input | Format | Source |
|---|---|---|
| `file_path` | Absolute path string | Callers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py`) |
| `_language_map` | `dict[str, Language]` (extension → tree-sitter `Language` object) | `TREE_SITTER_LANGUAGES` from `settings.py` |
| File content | Raw bytes read from disk | File at `file_path` |

---

## Transformation Flow

```
file_path (str)
     │
     ▼
[Cache lookup: parse_cache]
     │ hit                   │ miss
     │                       ▼
     │          extract extension  ──► lookup _language_map[ext]
     │                       │              (Language object)
     │                       ▼
     │               Parser(Language)
     │                       │
     │               read file → bytes
     │                       │
     │               parser.parse(bytes)
     │                       │
     │               tree.root_node + bytes
     │                       │
     │               store in parse_cache
     │◄──────────────────────┘
     ▼
(root_node: Node, content: bytes)
```

---

## Output Data Format and Destination

| Output | Format | Destination |
|---|---|---|
| Return value | `tuple[Node, bytes]` — AST root node + raw file bytes | All callers; callers index `[0]` for the node or unpack both |
| `parse_cache` entry | Same `tuple[Node, bytes]` keyed by `file_path` | Module-level cache; cleared by `pipeline.py` via `parse_cache.clear()` |

---

## Key Data Structures

### `parse_cache`

```
parse_cache: dict[str, tuple[Node, bytes]]
  key   → absolute file path (str)
  value → (root_node, content)
           ├─ root_node : tree-sitter Node (root of the parsed AST)
           └─ content   : raw file bytes (used by callers for line-based text extraction)
```

### `_language_map`

```
_language_map: dict[str, Language]
  key   → file extension without leading dot (e.g. "py", "ts", "js")
  value → tree-sitter Language object used to initialise Parser
```

The `content` bytes in the return tuple serve a dual purpose: tree-sitter node positions (byte offsets) reference them for span resolution, and callers decode them to UTF-8 text lines for source extraction.

## Error Handling

# Error Handling

## Overall Strategy

`ts_parser.py` adopts a **fail-fast** strategy. The module performs no explicit exception catching; all errors that arise during file I/O, language lookup, or parsing propagate immediately to the caller as unhandled exceptions. The design assumes that inputs (file paths and extensions) are valid and that the environment (language registry, file system) is correctly configured before any call to `parse_file` is made.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `KeyError` from `_language_map[ext]` | Unhandled — propagates to caller | Parsing aborts if the file extension has no registered `Language` object in `TREE_SITTER_LANGUAGES` |
| `FileNotFoundError` / `IOError` from `open()` | Unhandled — propagates to caller | Parsing aborts if the file does not exist or cannot be read |
| Any exception raised by `parser.parse()` | Unhandled — propagates to caller | Parsing aborts if tree-sitter encounters an internal error |
| Stale or invalid cache entry | Not applicable — cache entries are never invalidated except via explicit `parse_cache.clear()` | If a cached result exists for a path, it is returned unconditionally without re-reading the file |

---

## Design Considerations

- **Responsibility delegation**: Error handling is fully delegated to callers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, etc.). The module treats itself as a low-level utility with no opinion on recovery logic.
- **Cache invalidation boundary**: The only cache management surface exposed to external code is `parse_cache.clear()`, which is called at the pipeline level after a full analysis run. There is no per-entry eviction or staleness check, meaning error resilience around cache consistency is also left to the caller's operational discipline.
- **Implicit contract with settings**: The absence of a guard around `_language_map[ext]` implicitly relies on `TREE_SITTER_LANGUAGES` in `settings.py` being fully and correctly populated for every extension that callers may supply, establishing a configuration-time rather than runtime contract.

## Summary

## ts_parser.py Summary

Centralized tree-sitter parsing layer. Reads source files from disk, parses them into ASTs using the language inferred from the file extension (via `TREE_SITTER_LANGUAGES` from `settings.py`), and returns `(root_node, content)` tuples.

**Public interface:**
- `parse_file(file_path: str) -> tuple[Node, bytes]`: Cached parse entry point; returns AST root node and raw bytes.
- `parse_cache: dict[str, tuple[Node, bytes]]`: Module-level cache keyed by file path; externally clearable.

Adopts a fail-fast error strategy, delegating all exception handling to callers.
