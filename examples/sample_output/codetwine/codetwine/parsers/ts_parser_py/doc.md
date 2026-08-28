# Design Document: codetwine/parsers/ts_parser.py

# Overview & Purpose

### Role within the project

`ts_parser.py` centralizes all tree-sitter parsing logic for the codebase. It provides a single, cached entry point for turning a source file into a parsed AST, so that every other module that needs to inspect source code (definition extraction, import resolution, usage/dependency analysis) does not have to re-implement file reading, language selection, or tree-sitter setup. By isolating this concern in one file, the project avoids duplicated parsing code and redundant re-parsing of the same file across multiple analysis passes (e.g., `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, and `dependency_graph.py` all rely on it).

It exists as a separate file because parsing is a cross-cutting infrastructure concern: it depends on tree-sitter internals and language configuration (`TREE_SITTER_LANGUAGES`, `PARSE_CACHE_MAX_FILES` from `config/settings.py`), but the resulting `(root_node, content)` pairs are consumed by many unrelated extractors/analyzers throughout the project. Keeping this logic in one module ensures consistent caching behavior and a single source of truth for how files are read and parsed.

### Main public interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `parse_file` | `file_path: str` | `tuple[Node, bytes]` | Reads a file, parses it with tree-sitter based on its extension, caches the result, and returns the AST root node plus raw file content. |
| `parse_cache` | — (module-level `OrderedDict[str, tuple[Node, bytes]]`) | — | Exposes the LRU-ordered cache of parsed files so callers (e.g., `pipeline.py`) can clear it explicitly to free memory. |

### Design patterns / decisions

- **Module-level cache (Memoization / Singleton cache)**: `parse_cache` is a module-level `OrderedDict` shared across all callers, avoiding repeated disk I/O and re-parsing for files accessed from multiple analysis stages.
- **LRU eviction policy**: The cache tracks usage order manually—`move_to_end` marks an entry as most recently used on cache hit, and `popitem(last=False)` evicts the least recently used entry when the cache exceeds `PARSE_CACHE_MAX_FILES`. A value of `0` disables the size limit entirely, per the configured design.
- **Extension-based language dispatch**: The parser selects the appropriate tree-sitter `Language` object by looking up the file's extension in `TREE_SITTER_LANGUAGES` (built and expanded in `config/settings.py`), decoupling language-specific configuration from the parsing logic itself.
- **Explicit cache invalidation hook**: By exposing `parse_cache` itself (not just `parse_file`), the design allows external code (`pipeline.py`) to clear the cache at the end of an analysis run, since a `Node` keeps its whole underlying tree alive in memory.

# Definition Design Specifications

## `parse_cache`

Module-level `OrderedDict` mapping absolute file paths to `(root_node, content)` tuples, ordered from least to most recently used. Exists to persist tree-sitter parse results across calls within a single analysis run, avoiding redundant re-parsing of the same file by multiple callers (definition extraction, import resolution, usage/dependency analysis). Using an `OrderedDict` allows O(1) LRU tracking via `move_to_end` on access and `popitem(last=False)` for eviction. Because a tree-sitter `Node` keeps its entire owning tree alive, each cache entry effectively retains the full parsed AST and raw file bytes in memory until evicted; capping cache size bounds memory usage. Exposed at module level so external code (e.g., `pipeline.py`) can explicitly call `parse_cache.clear()` to release memory once analysis completes.

## `parse_file`

Reads a source file, parses it into a tree-sitter AST, and returns the result, using a bounded module-level LRU cache to avoid redundant re-parsing.

- **Arguments**: `file_path: str` — absolute path of the file to parse.
- **Returns**: `tuple[Node, bytes]` — the AST root node and the raw file content read in binary mode.
- **Responsibility/intent**: Centralizes file reading and tree-sitter parsing so all other modules (definition extraction, import resolution, usage/dependency graph building) obtain a consistent, cached AST for a given file instead of re-parsing it on every request.
- **Design decisions**:
  - Cache lookups and insertions use the file's absolute path as the key; a cache hit triggers `move_to_end` to mark the entry most-recently-used, implementing LRU eviction with an `OrderedDict`.
  - The language used to construct the `Parser` is selected by looking up the file extension (lowercased, without the leading dot) in `TREE_SITTER_LANGUAGES`; this ties parsing behavior directly to the extension-to-language mapping defined in configuration.
  - Returning `(root_node, content)` together (rather than root_node alone) lets callers map byte-offset ranges in the AST back to source text (e.g., for extracting line ranges) without re-reading the file.
  - Cache eviction only occurs when `PARSE_CACHE_MAX_FILES > 0`; a value of 0 disables the size limit entirely, allowing unbounded caching if configured.
  - Eviction removes entries one at a time via `popitem(last=False)` until the cache size is within the configured limit, ensuring only the least-recently-used entries are dropped.
- **Edge cases/constraints**:
  - `file_path` must correspond to a file whose extension exists as a key in `TREE_SITTER_LANGUAGES`; otherwise a `KeyError` occurs when constructing the `Parser`.
  - The file is expected to exist and be readable; no explicit error handling is performed for missing files or read failures beyond what `open` raises.
  - Extensions are derived via `os.path.splitext` and stripped of the leading dot, so files without an extension or with unsupported extensions are not handled gracefully.

# Dependency Description

### Dependencies (what this file uses)

- **`codetwine/config/settings.py`**
  - `TREE_SITTER_LANGUAGES`: Provides the extension-to-`Language` object mapping used to select the correct tree-sitter grammar for a given file based on its file extension. This mapping is consumed via `_language_map` when initializing the `Parser` instance for a target file.
  - `PARSE_CACHE_MAX_FILES`: Supplies the configured upper bound on the number of cached parse results. This value is used to determine when the least-recently-used entries in `parse_cache` should be evicted, controlling memory usage of the module-level cache.

### Dependents (what uses this file)

- **`codetwine/file_analyzer.py`**: Calls `parse_file` to obtain the AST root node and raw byte content of a target file, which is then used for definition extraction and source-line lookups.
- **`codetwine/import_to_path.py`**: Calls `parse_file` to parse a file and obtain its root node, which is used to extract definitions and register them in a symbol-to-file mapping.
- **`codetwine/pipeline.py`**: Accesses `parse_cache` directly to clear the module-level cache after analysis completes, freeing memory held by cached ASTs.
- **`codetwine/extractors/usage_analysis.py`**: Calls `parse_file` to parse both target definition files and caller files, using the resulting root nodes to extract definitions and imports for usage analysis.
- **`codetwine/extractors/dependency_graph.py`**: Calls `parse_file` to parse callee files and files containing import statements, using the root nodes to resolve dependencies and build the dependency graph.

The dependency direction is unidirectional: this file depends on `codetwine/config/settings.py` for language and caching configuration, while the listed dependents rely on this file's `parse_file` function (and, in one case, direct access to `parse_cache`) to obtain parsed ASTs; none of these relationships are reciprocated by `ts_parser.py`.

# Data Flow

## Input
| Source | Format |
|---|---|
| `file_path` argument | Absolute path string of a source file to parse |
| Module-level `parse_cache` | In-memory `OrderedDict[str, tuple[Node, bytes]]` keyed by file path, checked before doing any file I/O |
| `TREE_SITTER_LANGUAGES` | dict mapping file extension → tree-sitter `Language` object, used to select the correct grammar |
| `PARSE_CACHE_MAX_FILES` | int config value controlling cache size limit (0 = unlimited) |
| File system | Raw file bytes read from `file_path` in binary mode |

## Transformation Flow
```
file_path
   │
   ├─► [1] Cache lookup: parse_cache.get(file_path)
   │        │ hit → move_to_end (mark as most-recently-used) → return cached result
   │        │ miss → continue
   │
   ├─► [2] Extension extraction: os.path.splitext(file_path) → ext
   │
   ├─► [3] Language resolution: TREE_SITTER_LANGUAGES[ext] → Language object
   │        → Parser(language) constructed
   │
   ├─► [4] File read: open(file_path, "rb") → raw bytes (content)
   │
   ├─► [5] Parse: parser.parse(content) → tree
   │        → result = (tree.root_node, content)
   │
   └─► [6] Cache update:
            parse_cache[file_path] = result
            if PARSE_CACHE_MAX_FILES > 0:
                evict least-recently-used entries (popitem(last=False))
                until len(parse_cache) <= PARSE_CACHE_MAX_FILES
```

## Output
| Destination | Format |
|---|---|
| Return value to caller | `tuple[Node, bytes]` — `(root_node, content)`: tree-sitter AST root node and raw file bytes |
| `parse_cache` (side effect) | Updated/reordered entry `file_path → (root_node, content)`, potentially with oldest entries evicted |

## Key Data Structures

**`parse_cache: OrderedDict[str, tuple[Node, bytes]]`**
- Key: absolute file path (string)
- Value: tuple of `(root_node, content)`
  - `root_node`: tree-sitter `Node`, root of the parsed AST; keeps the entire tree alive in memory
  - `content`: raw file bytes as read from disk
- Ordering: least-recently-used (front) to most-recently-used (back), maintained via `move_to_end` on cache hits and insertion on cache misses; used to implement LRU eviction bounded by `PARSE_CACHE_MAX_FILES`

**Result tuple `(Node, bytes)`**
- Returned to all downstream consumers (`file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`)
- `Node` (AST root) is used by callers for definition/import extraction via tree-sitter queries
- `bytes` (raw content) is used by callers for decoding text (e.g., extracting source lines)

# Error Handling

**Overall strategy:** `parse_file` follows a fail-fast approach with no explicit exception handling. It performs no validation or error suppression of its own; any failure in file access, extension lookup, or parsing is allowed to propagate directly to the caller as an unhandled exception. There is no fallback, retry, or default-value behavior.

**Main error patterns and handling policies:**

| Error type | Handling | Impact |
|---|---|---|
| Unsupported/unknown file extension (extension not present in `TREE_SITTER_LANGUAGES`) | Not caught; dictionary lookup (`_language_map[ext]`) raises `KeyError` | Propagates to caller; parsing aborts for that file |
| File does not exist / permission denied when opening the file | Not caught; `open(file_path, "rb")` raises `OSError`/`FileNotFoundError` | Propagates to caller; no cache entry is created |
| Malformed or invalid source content causing parser issues | Not caught; relies on tree-sitter's `Parser.parse` behavior (tree-sitter itself is generally tolerant and produces an error-containing tree rather than raising) | If tree-sitter does raise, it propagates; otherwise a tree with error nodes is returned and cached as-is |
| Cache miss vs. hit | Handled via a simple `dict.get` check (`cached is not None`); no error is raised for a miss, it simply proceeds to parse | Normal control flow, not an error condition |

**Design considerations:**
- The function intentionally delegates error responsibility to callers (e.g., `file_analyzer.py`, `import_to_path.py`, `dependency_graph.py`, `usage_analysis.py`), which are expected to ensure the file path and extension are valid before calling `parse_file`.
- Because failures are not caught, a failed parse never pollutes `parse_cache`—entries are only stored after `parser.parse` and file reading succeed, keeping the cache consistent.
- No logging or error wrapping is performed at this layer, keeping the module minimal and leaving diagnostic/error-reporting concerns to higher-level orchestration code (e.g., `pipeline.py`).

# Summary

`ts_parser.py` centralizes tree-sitter parsing: reads source files, selects language via extension (`TREE_SITTER_LANGUAGES`), and returns cached `(root_node, content)` ASTs. Public interface: `parse_file(file_path)` and module-level `parse_cache` (LRU `OrderedDict`, bounded by `PARSE_CACHE_MAX_FILES`, clearable externally). Used by file_analyzer, import_to_path, usage_analysis, dependency_graph for consistent parsing; pipeline.py clears cache post-run. No error handling—failures (bad extension, missing file) propagate to callers. Ensures single source of truth for parsing/caching across the project.
