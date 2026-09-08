# Design Document: codetwine/parsers/ts_parser.py

# Design Specification

**Overview**

Parses a source file with tree-sitter and caches the resulting AST so repeated requests for the same file avoid re-parsing.

- Call `parse_file` to obtain a file's AST root node and raw byte content for definition extraction, import extraction, or usage analysis.
- Call `parse_cache.clear` at the end of a pipeline run to release cached trees and free memory once analysis is complete.
- Inspect or reuse `parse_cache` directly when needing to check whether a file's parse result is already available at module level.

This file depends on `codetwine/config/settings.py` for `TREE_SITTER_LANGUAGES` (extension-to-Language mapping used to select the correct tree-sitter grammar) and `PARSE_CACHE_MAX_FILES` (cache size limit). It is used by `file_analyzer.py`, `import_to_path.py`, `extractors/usage_analysis.py`, and `extractors/dependency_graph.py` to obtain parsed ASTs for definition extraction, import resolution, and cross-file usage/dependency analysis, and by `pipeline.py` which clears `parse_cache` after analysis completes.

Caching policy: results are stored in an `OrderedDict` keyed by file path, with least-recently-used entries evicted once the cache exceeds `PARSE_CACHE_MAX_FILES`; setting `PARSE_CACHE_MAX_FILES` to 0 disables the size limit entirely, allowing unbounded growth of the cache. Each cache hit moves the entry to the most-recently-used position, giving true LRU behavior.

**Definitions**

## `_language_map`

Module-level alias binding `TREE_SITTER_LANGUAGES` to a local name, used internally by `parse_file` to look up the tree-sitter `Language` object for a given file extension when constructing a `Parser`.

## `parse_cache`

Module-level `OrderedDict` mapping absolute file paths to `(root_node, content)` tuples, ordered from least to most recently used; serves as the shared parse-result cache read and updated by `parse_file` and cleared externally (e.g., by `pipeline.py`) to release memory after a full analysis run. Holding the root `Node` in the cache keeps its underlying tree-sitter tree alive.

## `parse_file`

Reads a file's bytes, parses it into a tree-sitter AST using the `Language` resolved from the file's extension via `_language_map` (`TREE_SITTER_LANGUAGES`), and returns the `(root_node, content)` pair; this is the sole entry point other modules use to obtain a parsed representation of a project file. On a cache hit for `file_path` it returns the cached tuple immediately and promotes the entry to most-recently-used via `move_to_end`, avoiding redundant parsing; on a miss it opens the file in binary mode, parses with a fresh `Parser`, stores the result in `parse_cache`, and evicts the oldest entry with `popitem(last=False)` whenever the cache size exceeds `PARSE_CACHE_MAX_FILES` (no eviction occurs if the limit is 0). Callers such as `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, and `dependency_graph.py` rely on it to get the AST root node for definition, import, and usage extraction without managing parsing or caching themselves.

# Summary

Parses source files with tree-sitter and caches results by file path to avoid redundant re-parsing. Provides `parse_file`, the sole entry point returning (root_node, content) tuples, using `_language_map` (from TREE_SITTER_LANGUAGES) to select grammar by file extension. Maintains `parse_cache`, an LRU OrderedDict bounded by PARSE_CACHE_MAX_FILES, with move_to_end on hits and popitem eviction on overflow. Used by file_analyzer, import_to_path, usage_analysis, and dependency_graph for AST access; cleared by pipeline.py after analysis. Key terms: AST caching, tree-sitter parsing, LRU cache, language mapping.
