# Design Document: examples/rlm_qa/qa_tools.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Provides stateful tool functions for querying a loaded project knowledge graph, enabling retrieval of source file contents, dependent file relationships, and BFS-based dependency graph traversal.

## 2. When to Use This Module

- **When you need to read the raw source code of a project file**: Call `read_source_file(path)` with a file path as listed in the project knowledge JSON to receive the file's text content.
- **When you need to find which files depend on a given file**: Call `get_files_using(target_file)` with a partial file path string to receive a list of files that reference it through their `callee_usages`.
- **When you need to explore the dependency graph around a named definition**: Call `graph_search(name, hops, direction)` to perform a BFS traversal from a definition, collecting reachable nodes and edges within a specified hop count and direction.
- **When initializing the module for use**: Set the module-level variables `project_data` and `base_dir` externally (as done in `rlm_qa_agent.py`) before calling any tool function, since all three functions depend on these shared state variables.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `read_source_file` | `path: str` | `str` | Reads and returns the content of a source file located under `base_dir`, stripping a leading project name prefix from the path if present. Returns an error message string on failure. |
| `get_files_using` | `target_file: str` | `list` | Returns a list of `{"file": str, "usage": dict}` entries for all files whose `callee_usages` contain a `from` field partially matching `target_file`. |
| `graph_search` | `name: str`, `hops: int` (default `1`), `direction: str` (default `"both"`) | `dict` | Performs BFS from the definition matching `name`, traversing outgoing (dependencies), incoming (dependents), or both directions up to `hops` levels, returning discovered nodes and edges. |
| `project_data` | — | `None` \| `dict` | Module-level variable holding the loaded `project_knowledge.json` contents; must be set externally before calling tool functions. |
| `base_dir` | — | `None` \| `str` | Module-level variable holding the base directory for resolving source file paths; must be set externally before calling `read_source_file`. |

## 4. Design Decisions

- **Shared mutable module-level state**: Rather than passing `project_data` and `base_dir` as arguments to each function, the module exposes them as top-level variables intended to be set by an external loader (e.g., `load_project()` in `rlm_qa_agent.py`). This makes the functions directly usable as standalone tool callables without requiring a wrapper or partial application.
- **Exact-then-partial match fallback in `graph_search`**: Definition lookup first attempts an exact name match and falls back to a case-insensitive partial match only if no exact match is found, prioritizing precision while maintaining usability when the full name is unknown.
- **`__module__` sentinel for module-level scope**: In `graph_search`, usages that occur outside any named definition's line range are attributed to a synthetic `__module__` node, allowing module-level code to participate in the graph without being discarded.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Variables

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `None` \| `dict` | Holds the entire parsed `project_knowledge.json` structure. Must be set externally via `load_project()` before any tool function is called. |
| `base_dir` | `None` \| `str` | Holds the base directory path for resolving source file paths. Must be set externally alongside `project_data`. |

Both variables are initialized to `None` and are set by `rlm_qa_agent.load_project()`, which assigns them as `qa_tools.project_data` and `qa_tools.base_dir` respectively.

---

## `read_source_file`

**Signature:**
```python
def read_source_file(path: str) -> str
```

- `path`: A file path string as it appears in the JSON `"file"` field (e.g., `"project_name/subdir/file.py"`).
- Returns: The full text content of the file, or an error message string on failure.

**Responsibility:** Reads a source file from the output directory so that tool-calling agents can inspect raw source text. Abstracts away path normalization relative to the project name prefix.

**When to use:** When a caller needs to retrieve the source code of a specific file identified from `project_data["files"]` entries, particularly to extract code at known line ranges from definition metadata.

**Design decisions:**
- Strips a leading `project_name/` prefix from `path` before joining with `base_dir`, since paths stored in the JSON may include the project name as a root segment.
- On any read failure, returns an error string rather than raising an exception, keeping behavior safe for LLM agent tool calls.

**Constraints & edge cases:**
- Returns an error string (not an exception) if `base_dir` is `None`.
- If `project_name` is an empty string, the prefix-stripping logic is skipped.
- File encoding is assumed to be UTF-8.

---

## `get_files_using`

**Signature:**
```python
def get_files_using(target_file: str) -> list
```

- `target_file`: A partial file path string used to match against the `"from"` field of callee usage entries.
- Returns: A list of dicts, each with the shape `{"file": str, "usage": dict}`, where `"file"` is the path of the dependent file and `"usage"` is the raw callee usage entry.

**Responsibility:** Discovers which files depend on a given file by searching all `callee_usages` entries across the project for references to that file. Provides the inverse dependency direction (dependents rather than dependencies).

**When to use:** When a caller needs to find all files that import from or call into a specific file, such as during impact analysis or tracing usage of a module.

**Design decisions:**
- Uses partial string matching (`target_file in usage["from"]`) rather than exact matching, allowing flexible queries without requiring exact path knowledge.
- Operates directly on the module-level `project_data` without requiring it as a parameter, relying on it being pre-loaded.

**Constraints & edge cases:**
- Will raise an error (e.g., `TypeError`) if `project_data` is `None`; there is no guard for uninitialized state.
- Partial matching may return unintended results if `target_file` is a short substring that matches multiple paths.
- Does not deduplicate results; the same file may appear multiple times if it has multiple usages of the target.

---

## `graph_search`

**Signature:**
```python
def graph_search(name: str, hops: int = 1, direction: str = "both") -> dict
```

- `name`: The definition name to start the search from. Exact match is attempted first; partial (case-insensitive) match is used as a fallback.
- `hops`: Maximum BFS depth from the start node. `1` means only direct neighbors; `2` includes neighbors of neighbors.
- `direction`: Controls which edge direction(s) to traverse.
  - `"outgoing"` — follows dependencies (definitions that the start uses).
  - `"incoming"` — follows dependents (definitions that use the start).
  - `"both"` — traverses in both directions.
- Returns: A dict with the following structure:

| Key | Type | Description |
|---|---|---|
| `"start"` | `str` | The start node key in `"file:name"` format. |
| `"hops"` | `int` | The hop limit used for this search. |
| `"direction"` | `str` | The direction parameter used. |
| `"nodes"` | `list[dict]` | Discovered neighbor nodes (not including the start node itself). |
| `"edges"` | `list[dict]` | Edges traversed, each with `"source"`, `"target"`, and `"hop"`. |

Each node dict in `"nodes"` has:

| Key | Type | Description |
|---|---|---|
| `"key"` | `str` | `"file:name"` identifier. |
| `"file"` | `str` | File path of the definition. |
| `"name"` | `str` | Definition name. |
| `"type"` | `str` | Definition type (e.g., function, class). |
| `"hop"` | `int` | Distance from the start node. |
| `"via"` | `str` | `"outgoing"` or `"incoming"`, indicating how the node was reached. |

**Responsibility:** Provides a BFS-based graph traversal over the project's dependency graph, treating definitions as nodes and import/call relationships as edges, enabling multi-hop impact and dependency analysis.

**When to use:** When a caller needs to understand the dependency neighborhood of a specific definition — for example, to find what a function depends on (`"outgoing"`), what depends on it (`"incoming"`), or both — within a bounded number of hops.

**Design decisions:**
- Uses node keys in `"file:name"` format to uniquely identify definitions across files, since the same name may appear in multiple files.
- A `visited` set prevents revisiting nodes and avoids infinite cycles in graphs with circular dependencies.
- A `seen_edges` set prevents duplicate edges between the same pair of nodes in the same direction.
- For **outgoing** edges, usage lines are checked against the current definition's line range to confirm the usage originates inside that definition. A special `"__module__"` pseudo-name covers module-level code outside any definition.
- For **incoming** edges, the source definition is identified by finding which definition in the caller's file contains the usage line. Defaults to `"__module__"` if no enclosing definition is found.
- The start node itself is not included in `"nodes"`; only discovered neighbors are listed.
- The BFS queue carries the hop count per node, and nodes at exactly `hops` depth are added to results but not enqueued for further expansion.

**Constraints & edge cases:**
- Returns `{"error": ...}` if `project_data` is `None` or if no matching definition is found after both exact and partial search.
- When multiple candidates match `name`, only the first candidate found is used as the start node.
- If a `target_file` or `source_file` referenced in a usage entry does not exist in `file_index`, the type field for that node will be an empty string.
- `"__module__"` is a synthetic name representing module-level code and is not a real definition in the source.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

Based on the provided source code and dependency information, this file has **no project-internal module dependencies**. It imports only standard library modules (`os`, `collections.deque`) and operates solely on module-level variables (`project_data`, `base_dir`) that are injected externally by its dependent module.

## Dependents (modules that import this file)

- `examples/rlm_qa/rlm_qa_agent.py` → `codetwine/examples/rlm_qa/qa_tools_py/qa_tools.py` : The agent module uses this module in the following ways:
  - Writes to `qa_tools.project_data` and `qa_tools.base_dir` to initialize the module's shared state after loading a `project_knowledge.json` file, enabling the tool functions to operate against a specific project.
  - Registers `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` as callable tools provided to an agent interpreter, making the three analysis functions available as the agent's tool set.

## Dependency Direction

- The relationship between `qa_tools.py` and `rlm_qa_agent.py` is **unidirectional**: `rlm_qa_agent.py` depends on `qa_tools.py`. `qa_tools.py` does not import or reference `rlm_qa_agent.py` in any way.
- `qa_tools.py` itself has no outgoing project-internal dependencies, making it a **leaf module** in the project's internal dependency graph.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `project_data` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.project_data = json.load(f)` | Dict parsed from `project_knowledge.json` |
| `base_dir` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.base_dir = os.path.dirname(json_path)` | String (directory path) |
| `path` | Argument to `read_source_file()` | String file path as listed in the JSON `"file"` field |
| `target_file` | Argument to `get_files_using()` | String (partial file path for matching) |
| `name` | Argument to `graph_search()` | String (definition name, exact or partial) |
| `hops` | Argument to `graph_search()` | Integer (default: 1) |
| `direction` | Argument to `graph_search()` | String: `"outgoing"`, `"incoming"`, or `"both"` (default: `"both"`) |

---

## 2. Transformation Overview

### `read_source_file(path)`

```
path (string)
  → Strip leading "project_name/" prefix if present
  → Join with base_dir to form full filesystem path
  → Read file from disk
  → Return file content as string (or error message string on failure)
```

### `get_files_using(target_file)`

```
project_data["files"] (list of file entries)
  → Iterate all files and their callee_usages entries
  → Filter: keep only usages where target_file is a substring of usage["from"]
  → For each match, pair the containing file path with the usage dict
  → Return list of {"file": ..., "usage": ...} dicts
```

### `graph_search(name, hops, direction)`

```
project_data["files"]
  → Build file_index dict keyed by file path for O(1) lookup

  → Candidate search:
      Exact match on definition["name"] == name
      Fallback: case-insensitive partial match if no exact match found
      Take first candidate as start node

  → BFS traversal (queue of (key, file, name, hop)):
      For each dequeued node (while current_hop < hops):

        [Outgoing edges — if direction is "outgoing" or "both"]
          → Scan callee_usages of current file
          → Filter usages whose line numbers fall within the current
            definition's line range (or module-level lines for "__module__")
          → Each qualifying usage becomes a target node: target_key = "file:name"
          → Resolve target definition type from file_index
          → Append edge {source, target, hop} and node {key, file, name, type, hop, via="outgoing"}
          → Enqueue unvisited target nodes

        [Incoming edges — if direction is "incoming" or "both"]
          → Scan caller_usages of current file
          → Filter entries where usage["name"] matches current_name
          → Identify the enclosing definition in the source file by matching
            usage line numbers against definition line ranges; default to "__module__"
          → Each match becomes a source node: source_key = "file:name"
          → Append edge {source, target, hop} and node {key, file, name, type, hop, via="incoming"}
          → Enqueue unvisited source nodes

  → Return result dict with start key, hops, direction, nodes list, edges list
```

---

## 3. Outputs

| Function | Output | Format |
|---|---|---|
| `read_source_file()` | File contents, or an error message | Plain string |
| `get_files_using()` | List of files that depend on the target file, each paired with the specific usage | `list[dict]` — see structure below |
| `graph_search()` | BFS-traversed dependency graph rooted at the named definition | `dict` — see structure below |

Side effects: None. All three functions are read-only with respect to the filesystem and `project_data`.

Module-level variables `project_data` and `base_dir` are written by the external caller (`rlm_qa_agent.py`), not by any function within this module.

---

## 4. Key Data Structures

### `get_files_using()` — result list element

Each element of the returned list:

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Path of the file that depends on `target_file` |
| `usage` | `dict` | The raw `callee_usages` entry from `project_data` for that dependency |

---

### `graph_search()` — return value

Top-level dict:

| Field / Key | Type | Purpose |
|---|---|---|
| `start` | `str` | Key of the BFS start node, formatted as `"file_path:definition_name"` |
| `hops` | `int` | The `hops` argument value used for the search |
| `direction` | `str` | The `direction` argument value used for the search |
| `nodes` | `list[dict]` | All definition nodes discovered during BFS (excluding the start node) |
| `edges` | `list[dict]` | All edges discovered during BFS |

---

### `graph_search()` — node entry (element of `nodes`)

| Field / Key | Type | Purpose |
|---|---|---|
| `key` | `str` | Unique node identifier formatted as `"file_path:definition_name"` |
| `file` | `str` | File path containing this definition |
| `name` | `str` | Definition name (or `"__module__"` for module-level code) |
| `type` | `str` | Definition type as recorded in `project_data` (e.g. function, class); empty string if not found |
| `hop` | `int` | BFS hop distance from the start node |
| `via` | `str` | Direction of the edge that led to this node: `"outgoing"` or `"incoming"` |

---

### `graph_search()` — edge entry (element of `edges`)

| Field / Key | Type | Purpose |
|---|---|---|
| `source` | `str` | Key of the node that the edge originates from (`"file_path:definition_name"`) |
| `target` | `str` | Key of the node that the edge points to (`"file_path:definition_name"`) |
| `hop` | `int` | BFS hop level at which this edge was discovered |

---

### Internal: `file_index` (local to `graph_search()`)

| Field / Key | Type | Purpose |
|---|---|---|
| `<file_path>` | `str` (key) | File path string as it appears in `project_data["files"][*]["file"]` |
| `<file_entry>` | `dict` (value) | Full file entry from `project_data["files"]`, enabling O(1) lookup by path |

## Error Handling

# Error Handling

## 1. Overall Strategy

The module follows a **graceful degradation** approach. Rather than raising exceptions or terminating the process, errors are surfaced as structured return values — either an error message string (for file-reading operations) or an error-keyed dictionary (for graph operations). This design allows the calling agent (`rlm_qa_agent.py`) to receive and interpret error conditions as ordinary tool outputs without encountering unhandled exceptions.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Uninitialized `base_dir` | `read_source_file` is called before `load_project()` sets `base_dir` | Returns an error string describing the uninitialized state | No | The file read is skipped entirely; no file content is returned |
| File I/O failure | `open()` on the resolved file path raises any exception | Returns an error string containing the path and exception message | No | The file read is skipped; the error message is returned in place of content |
| Uninitialized `project_data` | `graph_search` is called before `load_project()` sets `project_data` | Returns a dict with an `"error"` key describing the uninitialized state | No | The entire graph search is aborted; an error dict is returned |
| Definition not found (exact) | No definition in `project_data` exactly matches the `name` argument | Falls back to case-insensitive partial match across all definitions | Yes | Search continues using the first partial match candidate |
| Definition not found (partial) | Neither exact nor partial match finds any definition for `name` | Returns a dict with an `"error"` key describing the missing definition | No | The entire graph search is aborted; an error dict is returned |
| Unknown file in graph traversal | A dependency references a file path not present in `file_index` | Silently skips that node during BFS traversal | Yes | That branch of the dependency graph is not explored; traversal continues |

---

## 3. Design Notes

**Return-value-as-error over exceptions:** All error conditions are communicated through the function's return type rather than by raising exceptions. This is consistent with the module's role as a tool provider for an LLM agent: the agent receives tool outputs as text or structured data, so embedding error information in the return value makes errors directly interpretable at the agent level without requiring exception-handling logic in the caller.

**Two distinct error formats:** `read_source_file` returns a plain error string, while `graph_search` returns a dictionary with an `"error"` key. This reflects the differing normal return types of the two functions — string versus dict — keeping error returns type-consistent with successful returns.

**Silent skip for missing graph nodes:** When a file referenced in a dependency edge cannot be found in the index, the node is silently skipped rather than flagged. This avoids surfacing noise for stale or incomplete dependency data and allows partial graph results to still be returned.

**Fallback search preserves usability:** The exact-to-partial match fallback in `graph_search` means that minor naming discrepancies in queries do not immediately result in failure, reducing the frequency of hard errors during agent-driven exploration.

## Summary

**qa_tools.py** provides stateful tool functions for querying a loaded project knowledge graph via module-level variables `project_data` (dict) and `base_dir` (str) set externally.

**Public functions:**
- `read_source_file(path: str) → str`
- `get_files_using(target_file: str) → list[{"file": str, "usage": dict}]`
- `graph_search(name: str, hops: int, direction: str) → dict`

`graph_search` returns `{"start": str, "hops": int, "direction": str, "nodes": list[{"key", "file", "name", "type", "hop", "via"}], "edges": list[{"source", "target", "hop"}]}`.
