# Design Document: codetwine/parsers/ts_parser.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`ts_parser.py` provides a thin, reusable parsing layer that bridges raw source files and the tree-sitter AST library for the rest of the codetwine project. It exists as a dedicated module to centralize two concerns that would otherwise be duplicated across every analysis component:

1. **Language dispatch** – mapping a file's extension to the appropriate tree-sitter `Language` object (sourced from `TREE_SITTER_LANGUAGES` in `settings.py`) and constructing a `Parser` instance for it.
2. **Parse-result caching** – maintaining a module-level `parse_cache` dictionary so that any file parsed once is not read from disk or re-processed by tree-sitter again during the same pipeline run. The cache is exposed publicly so callers (e.g., `pipeline.py`) can clear it explicitly to release memory.

Multiple consumers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py`) call `parse_file` independently; without this shared module those callers would either duplicate parsing logic or silently re-parse the same files repeatedly.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a source file from disk, parses it with tree-sitter using the language matched to its extension, caches and returns the AST root node together with the raw byte content. |
| `parse_cache` | — (module-level `dict`) | `dict[str, tuple[Node, bytes]]` | Module-level cache mapping absolute file paths to their `(root_node, content)` parse results; exposed so external callers can inspect or clear it. |

---

## Design Decisions

- **Module-level cache (`parse_cache`)** – A plain dictionary at module scope acts as a memoization store. Because Python modules are singletons within a process, the cache is shared across all importers for the lifetime of the process, making the pattern equivalent to a lightweight flyweight. Exposing it as a public name (rather than hiding it behind an accessor) allows `pipeline.py` to call `parse_cache.clear()` directly after a pipeline run to reclaim memory.
- **Extension-driven language lookup** – Language selection is fully data-driven via `_language_map` (aliased from `TREE_SITTER_LANGUAGES`), keeping the parser itself free of hard-coded language names; adding support for a new language requires only a change to `settings.py`.
- **Binary file I/O** – Files are read in `"rb"` mode and the raw `bytes` object is returned alongside the AST node, allowing callers to decode content as needed (e.g., `content.decode("utf-8")` in `file_analyzer.py`) without a second disk read.

## Definition Design Specifications

# Definition Design Specifications

## Module-Level Variables

### `_language_map`
An alias for `TREE_SITTER_LANGUAGES`, a `dict[str, Language]` mapping file extensions (without leading dot) to tree-sitter `Language` objects. Acts as the sole lookup table for resolving file extensions to parser grammars.

### `parse_cache`
A module-level `dict[str, tuple[Node, bytes]]` mapping absolute file paths to their previously computed `(root_node, content)` parse results. Shared across all callers within the same process lifetime; cleared externally (e.g., by `pipeline.py`) to reclaim memory.

---

## Functions

### `parse_file`

**Signature:** `parse_file(file_path: str) -> tuple[Node, bytes]`

**Arguments:**
- `file_path`: Absolute path of the file to be parsed. Must correspond to a file whose extension is present as a key in `_language_map`.

**Return value:** A tuple of `(root_node, content)` where `root_node` is the tree-sitter AST root `Node` and `content` is the raw binary file content as `bytes`.

**Responsibility:** Provides a single entry point for parsing source files into tree-sitter ASTs, decoupling all callers from parser instantiation and file I/O details.

**Design decisions:**
- Results are memoized in `parse_cache` keyed by `file_path`, so repeated calls for the same file skip disk reads and re-parsing. This is intentional for performance in pipeline workflows that access the same file from multiple analysis passes.
- The parser is instantiated per call (not shared), which avoids any cross-call state concerns with tree-sitter's `Parser` object.
- File content is read in binary mode and returned alongside the AST so callers can decode or index into the raw bytes without a second file read.

**Edge cases and constraints:**
- If `file_path`'s extension is absent from `_language_map`, a `KeyError` is raised. The caller is responsible for ensuring the file extension is supported before invoking this function.
- If `file_path` does not exist or is unreadable, the underlying `open` call raises an `OSError`; no error handling is performed here.
- The cache is never evicted automatically within this module; external code must call `parse_cache.clear()` to release memory.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **`codetwine/config/settings.py`** (`TREE_SITTER_LANGUAGES`): Used to obtain the mapping from file extensions to tree-sitter `Language` objects. This mapping is assigned to the module-level `_language_map` and is consulted during parsing to select the correct grammar for a given file's extension when initializing the `Parser` instance.

---

### Dependents (what uses this file)

- **`codetwine/import_to_path.py`** (`parse_file`): Calls `parse_file` to obtain the AST root node of a source file, which is then passed to definition extraction logic to register symbol-to-file mappings.

- **`codetwine/file_analyzer.py`** (`parse_file`): Calls `parse_file` to retrieve both the AST root node and raw byte content of a target file. The byte content is decoded and split into lines to associate extracted definitions with their source text.

- **`codetwine/pipeline.py`** (`parse_cache.clear`): Accesses the module-level `parse_cache` dict directly to clear all cached parse results after analysis is complete, freeing memory.

- **`codetwine/extractors/usage_analysis.py`** (`parse_file`): Calls `parse_file` to parse both target files (for definition extraction) and caller files (for import extraction) as part of usage analysis.

- **`codetwine/extractors/dependency_graph.py`** (`parse_file`): Calls `parse_file` to parse callee files for definition lookup and to parse individual project files for import statement extraction during dependency graph construction.

**Direction of dependency**: All dependencies are unidirectional. `ts_parser.py` depends on `settings.py` for configuration, and the dependent files consume `ts_parser.py`'s `parse_file` function and `parse_cache` object without `ts_parser.py` having any knowledge of its callers.

## Data Flow

# Data Flow

## Input Data Format and Source

| Input | Format | Source |
|---|---|---|
| `file_path` | Absolute path string | Callers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py`) |
| `_language_map` | `dict[str, Language]` | `TREE_SITTER_LANGUAGES` from `settings.py` (extension → tree-sitter `Language` object) |
| File content | Raw bytes (binary read) | File system via `open(file_path, "rb")` |

---

## Transformation Flow

```
file_path (str)
      │
      ▼
[Cache lookup: parse_cache]
      │ hit                │ miss
      │                    ▼
      │         ext = splitext(file_path) → lstrip(".")
      │                    │
      │                    ▼
      │         _language_map[ext] → Language object
      │                    │
      │                    ▼
      │         Parser(Language) constructed
      │                    │
      │                    ▼
      │         open(file_path, "rb") → content: bytes
      │                    │
      │                    ▼
      │         parser.parse(content) → Tree
      │                    │
      │                    ▼
      │         tree.root_node → Node (AST root)
      │                    │
      │                    ▼
      │         result = (Node, bytes)  stored in parse_cache
      │◄──────────────────-┘
      ▼
tuple[Node, bytes]  returned to caller
```

---

## Output Data Format and Destination

| Output | Format | Destination |
|---|---|---|
| Return value | `tuple[Node, bytes]` | Callers index `[0]` for `Node` (AST root) or destructure both `root_node, content` |
| `parse_cache` | Module-level `dict[str, tuple[Node, bytes]]` | Persists across calls; cleared by `pipeline.py` via `parse_cache.clear()` |

---

## Main Data Structures

### `parse_cache`
```
parse_cache: dict[str, tuple[Node, bytes]]
  key   → absolute file path string
  value → (root_node, content)
             │              │
             │              └─ raw file bytes (used by callers to decode into text lines)
             └─ tree-sitter Node (AST root, used by callers to run tree-sitter queries)
```

### `_language_map`
```
_language_map: dict[str, Language]
  key   → file extension string (e.g., "py", "ts")
  value → tree-sitter Language object required to construct a Parser
```

The extension is derived from `file_path` at call time and used as the lookup key into `_language_map` to select the correct grammar before parsing.

## Error Handling

# Error Handling

## Overall Strategy

`ts_parser.py` adopts a **fail-fast** strategy. The module contains no explicit `try/except` blocks or defensive error-handling logic. All error conditions are allowed to propagate immediately as unhandled exceptions to the caller. This places the responsibility for error recovery entirely on the calling code.

## Error Patterns and Handling Policies

| Error Type | Trigger Condition | Handling | Impact |
|---|---|---|---|
| `KeyError` | `file_path` extension is not present in `_language_map` (i.e., not registered in `TREE_SITTER_LANGUAGES`) | Unhandled — propagates to caller | Parsing aborts; the returned tuple is never produced |
| `FileNotFoundError` / `OSError` | `file_path` does not exist or is not readable when opening the file in binary mode | Unhandled — propagates to caller | Parsing aborts; no cache entry is written |
| Any exception from `Parser` or `tree-sitter` internals | Malformed content or internal tree-sitter error during `parser.parse()` | Unhandled — propagates to caller | Parsing aborts; no cache entry is written |

## Design Considerations

Because no result is written to `parse_cache` unless parsing fully succeeds, the cache is guaranteed to contain only valid `(root_node, content)` pairs. This is a natural consequence of the fail-fast approach: a failed call leaves the cache in its prior state, so a subsequent retry (if the caller chooses to handle the exception) will attempt a fresh parse rather than returning a corrupt or partial result. Beyond this implicit cache-integrity property, error handling is entirely delegated upward by design, with no logging, wrapping, or fallback behaviour at this layer.

## Summary

`ts_parser.py` centralizes tree-sitter parsing for the codetwine pipeline. It maps file extensions to tree-sitter `Language` objects via `_language_map` (sourced from `settings.py`) and exposes one public function, `parse_file(file_path: str) -> tuple[Node, bytes]`, which reads a file in binary mode, constructs a parser, and returns the AST root node with raw byte content. Results are memoized in the public module-level dict `parse_cache` (keyed by absolute path), shared across all callers and cleared externally by `pipeline.py`. Errors propagate immediately with no handling; the cache holds only fully successful parse results.
