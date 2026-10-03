# Design Document: examples/rlm_qa/qa_tools.py

# Design Specification

**Overview**

Provide query tools for a large language model agent to navigate and analyze source code project structure, definitions, dependencies, and design documentation stored in a KnowledgeStore.

This file is used to:
- Call `read_source_file()` to retrieve the full text of a source file by its project path for detailed code inspection.
- Call `get_file_detail()` to fetch a file's definitions (with line ranges), callee and caller usages, and design document for focused analysis.
- Call `search_text()` to find where a keyword appears across the project in documentation, definitions, and dependency contexts.
- Call `get_files_using()` to identify which files depend on a target file by examining callee usage patterns.
- Call `graph_search()` to explore definition dependencies as a graph, traversing outgoing dependencies (what a definition uses) or incoming dependents (what uses a definition) within a specified hop distance.

The file relies on `codetwine/utils/file_utils.py` to decode and normalize line endings when reading source text from the project's output directory. The file `examples/rlm_qa/rlm_qa_agent.py` uses all tool functions as methods passed to a DSPy RLM agent, along with accessing the module-level `store` variable to retrieve project metadata (name, dependencies list) and to build documentation schema for LLM instructions.

The module uses a module-level `store` variable (a KnowledgeStore instance) initialized by external code rather than created internally, allowing the store to be shared across multiple tool function calls and preventing the entire analysis from being held in memory at once. All tool functions check that `store` is initialized and return error dictionaries or lists on failure rather than raising exceptions, supporting graceful degradation in agent execution. The `graph_search()` function caches file entries it reads to avoid redundant store lookups during BFS traversal.

**Definitions**

## `store`

Module-level variable holding a KnowledgeStore instance (either backed by project_knowledge.json or project_knowledge.sqlite) that is initialized externally and shared across all tool function calls. Must be set before calling any tool function; all tools check this and return error messages if it is None.

## `SEARCH_HIT_LIMIT`

Constant set to 40 that defines the maximum number of search results `search_text()` returns before it stops scanning the project.

## `read_source_file()`

Read and return the full text content of a source file identified by its project-relative path (the same path as listed in project_data file fields). Strips a leading project_name/ prefix if present, constructs the full path within the store's base directory, and delegates to `read_source_text()` for encoding-aware reading with normalized line endings. Returns an error message string if the store is not initialized or if file reading fails with an OSError.

## `get_file_detail()`

Query the store for a single file's structured metadata: its definitions with start/end line numbers and context, callee usages (dependencies it has on other definitions) and caller usages (where it is used by other definitions), and its design documentation (summary and titled sections). Accepts the exact file path as it appears in project_data. Returns a dictionary with file_dependencies and doc keys on success, or an error dictionary if the file is not found or the store is uninitialized.

## `search_text()`

Perform a case-insensitive keyword search across the entire project, returning hits from design document summaries and section content, definition source context, and callee and caller usage contexts. Stops scanning and returns early once the hit limit (default 40) is reached. Each hit is a dictionary recording the kind (summary, section, definition, callee, or caller), file path, and name of the matched entity.

## `get_files_using()`

Find all files in the project that depend on a specified target file by traversing the callee_usages of every file and collecting entries whose from field partially matches the target_file path. Returns a list of dictionaries, each pairing a file path with the callee usage record that references the target file.

## `_find_definition()`

Search a list of definitions for the first one matching a given name and return it, or None if not found. Used internally to locate a definition by name within a file's definition list.

## `_is_usage_in()`

Determine whether a callee usage (a call to another definition) occurs within the line range of a current definition. Handles the pseudo definition "__module__" to mean usages outside all named definitions. Used by `graph_search()` to filter callee usages that belong to a specific definition.

## `_enclosing_definition()`

Find the definition in a file that contains a caller usage (a location where the current definition is called), returning both the definition name and type. Returns the tuple ("__module__", "") if no named definition contains any of the usage lines, indicating module-level usage. Used by `graph_search()` to identify which definition in the dependent file makes each call.

## `graph_search()`

Perform a breadth-first search for definitions reachable within a specified hop distance from a named definition, treating definitions as nodes and dependencies as edges. Supports direction control: "outgoing" follows callee usages (dependencies the definition has), "incoming" follows caller usages (definitions that depend on it), or "both" explores both directions. Performs exact match search first, then falls back to partial match if the definition is not found. Returns a dictionary containing the start node key, hop count, direction, lists of visited nodes (with their file, name, type, hop distance, and direction), and edge records (source, target, hop). Caches file entries during traversal to avoid redundant store reads.

# Summary

# Summary of qa_tools.py

This module provides query tools for LLM agents to explore project structure and code analysis through a shared KnowledgeStore. It enables retrieval of source files, file metadata (definitions, usages, documentation), keyword searching across the project, and graph-based traversal of definition dependencies in both directions. Main public functions are `read_source_file()`, `get_file_detail()`, `search_text()`, `get_files_using()`, and `graph_search()`. The module handles graceful error handling and result caching to support agent-driven code navigation without holding the entire project in memory.
