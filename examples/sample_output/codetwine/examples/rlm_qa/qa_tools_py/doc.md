# Design Document: examples/rlm_qa/qa_tools.py

# Overview & Purpose

## 1. Module Summary

Provide a set of stateful tool functions for querying a loaded project knowledge graph, enabling LLM-driven agents to read source files, trace file-level dependencies, and perform BFS-based graph searches over definition relationships.

## 2. When to Use This Module

- **Reading source file contents**: Call `read_source_file(path)` when you need to retrieve the raw text of a source file identified by its path as recorded in `project_knowledge.json`.
- **Finding which files depend on a given file**: Call `get_files_using(target_file)` when you need to identify all files that import or use symbols from a specific file, along with each specific usage entry.
- **Exploring dependency relationships around a definition**: Call `graph_search(name, hops, direction)` when you need to traverse the dependency graph outward (what a definition uses), inward (what uses a definition), or both, up to a specified number of hops.
- **Initializing the module before using any tool**: Set `project_data` and `base_dir` (as done in `rlm_qa_agent.py` via `load_project()`) before calling any of the three tool functions, since all tools depend on these module-level variables.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `project_data` | — | `dict \| None` | Module-level variable holding the parsed contents of `project_knowledge.json`; must be set before calling tool functions |
| `base_dir` | — | `str \| None` | Module-level variable holding the base directory path for resolving source files; must be set before calling `read_source_file` |
| `read_source_file` | `path: str` | `str` | Reads and returns the content of a source file by its project-relative path; returns an error message string on failure |
| `get_files_using` | `target_file: str` | `list` | Returns a list of `{"file": str, "usage": dict}` entries for all files whose `callee_usages` partially match the given file path |
| `graph_search` | `name: str`, `hops: int`, `direction: str` | `dict` | Performs a BFS traversal of the definition dependency graph from a named definition, returning discovered nodes and edges within the specified hop count and direction |

## 4. Design Decisions

- **Module-level mutable state**: `project_data` and `base_dir` are intentionally exposed as module-level variables rather than passed as arguments, so that the tool functions can be handed directly to an LLM tool-calling interface (as seen in `rlm_qa_agent.py`) without requiring the caller to manage context objects per invocation.
- **Exact-then-partial match fallback in `graph_search`**: The function first attempts an exact match on definition names, then falls back to case-insensitive partial matching. This makes the function more forgiving when an LLM agent provides an approximate symbol name.
- **`__module__` as a synthetic scope name**: When a `caller_usage` line falls outside any named definition's line range, `graph_search` assigns the sentinel name `"__module__"` to represent module-level code, allowing it to participate in the graph as a node without requiring a named definition.

# Definition Design Specifications

---

## Module-Level Variables

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `None` \| `dict` | Holds the entire parsed `project_knowledge.json` content. Must be set by an external caller (e.g., `load_project()` in `rlm_qa_agent.py`) before any tool function is invoked. |
| `base_dir` | `None` \| `str` | Holds the base directory path from which source files are resolved. Set alongside `project_data` by the external loader. |

Both variables are initialized to `None` and act as shared state across all tool functions in this module. They are not set internally; all tool functions treat their absence as an error condition.

---

## Functions

---

### `read_source_file`

**Signature:**
```python
def read_source_file(path: str) -> str
```

- `path`: A file path string as it appears in the `"file"` field of the JSON data (e.g., `"code_analyzer/extract_imports/extract_imports.py"`).
- Returns: The full text content of the file as a string, or a human-readable error message string on failure.

**Responsibility:**
Resolves a logical file path from the project knowledge JSON to an absolute filesystem path and returns the file's text content. Provides a safe, single-entry-point for all source file reads by LLM tool calls.

**When to use:**
When a caller needs to inspect the raw source code of a file identified in `project_data`, typically to extract a specific function body or verify implementation details.

**Design decisions:**

- **Path prefix stripping:** If `path` begins with `project_name + "/"`, that prefix is removed before joining with `base_dir`. This normalizes paths that are stored in the JSON with the project name as a leading component.
- **Error as return value (not exception):** Read failures return an error string rather than raising an exception, making the function safe for direct use as an LLM tool where exceptions would interrupt the agent loop.

**Constraints & edge cases:**

- Requires `base_dir` and `project_data` to be initialized; returns an error string if `base_dir` is `None`.
- If `project_data` has no `"project_name"` key or it is empty, prefix stripping is skipped.
- File encoding is assumed to be UTF-8; files with other encodings will produce an error string.

---

### `get_files_using`

**Signature:**
```python
def get_files_using(target_file: str) -> list
```

- `target_file`: A partial or full file path string to search for among dependency references.
- Returns: A list of dicts, each with the shape `{"file": str, "usage": dict}`, where `file` is the path of the dependent file and `usage` is the matching `callee_usages` entry from that file.

**Responsibility:**
Performs a reverse dependency lookup by scanning every file's `callee_usages` entries for references that originate from `target_file`. Surfaces all files that directly consume symbols from a given source file.

**When to use:**
When a caller needs to identify which files are impacted by or depend upon a specific file, such as during impact analysis or tracing the spread of a dependency.

**Design decisions:**

- **Partial match on `"from"` field:** Uses substring containment (`target_file in usage["from"]`) rather than exact equality, allowing flexible queries with partial paths or filenames.
- **Flat output structure:** Each matching usage generates a separate result entry even if multiple usages originate from the same dependent file, preserving per-usage granularity.

**Constraints & edge cases:**

- Assumes `project_data` is loaded; will raise an unhandled `TypeError` if `project_data` is `None`.
- Partial matching may produce false positives if one file path is a substring of another unrelated path.
- Returns an empty list if no matches are found.

---

### `graph_search`

**Signature:**
```python
def graph_search(name: str, hops: int = 1, direction: str = "both") -> dict
```

- `name`: The definition name to use as the BFS starting node. Exact match is attempted first; falls back to case-insensitive partial match.
- `hops`: Maximum traversal depth from the start node. `1` means only direct neighbors; `2` extends to neighbors of neighbors.
- `direction`: Controls which edges are followed.
  - `"outgoing"` — follows symbols that the current definition calls/uses (dependencies).
  - `"incoming"` — follows symbols that call/use the current definition (dependents).
  - `"both"` — follows both directions.
- Returns: A dict with the following structure:

| Key | Type | Description |
|---|---|---|
| `"start"` | `str` | The starting node key in `"file:name"` format. |
| `"hops"` | `int` | The requested hop limit. |
| `"direction"` | `str` | The direction argument as provided. |
| `"nodes"` | `list[dict]` | Discovered neighbor nodes, each with `key`, `file`, `name`, `type`, `hop`, and `via`. |
| `"edges"` | `list[dict]` | Discovered edges, each with `source`, `target`, and `hop`. |

Each node dict in `"nodes"`:

| Field | Type | Description |
|---|---|---|
| `key` | `str` | Unique node identifier in `"file:name"` format. |
| `file` | `str` | File path containing the definition. |
| `name` | `str` | Definition name. |
| `type` | `str` | Definition type as recorded in the JSON (e.g., `"function"`, `"class"`). |
| `hop` | `int` | Distance from the start node at which this node was discovered. |
| `via` | `"outgoing"` \| `"incoming"` | Direction through which this node was reached. |

**Responsibility:**
Provides a structured BFS traversal of the project's inter-definition dependency graph, enabling callers to discover what a definition depends on, what depends on it, or both, within a bounded radius.

**When to use:**
When a caller needs to understand the dependency neighborhood of a specific function, class, or other named definition, particularly for tracing call chains or assessing coupling.

**Design decisions:**

- **Node key format (`"file:name"`):** Combines file path and definition name into a single string key to uniquely identify definitions across files, since the same name may appear in multiple files.
- **BFS with visited set:** Prevents re-visiting the same node regardless of the direction through which it was first encountered, avoiding duplicate results and infinite loops in cyclic graphs.
- **Deduplication of edges:** A `seen_edges` set tracks `(source_key, target_key, direction)` triples so that the same logical edge is not emitted more than once.
- **`__module__` sentinel:** When an incoming usage line does not fall within any named definition's line range in the source file, the caller is labeled `__module__`, representing module-level code rather than a named definition.
- **Outgoing scope check via line ranges:** For outgoing edges, a `callee_usages` entry is only attributed to the current definition if at least one of its usage line numbers falls within that definition's declared line range. Module-level code is handled as a special case when `current_name == "__module__"`.
- **Start node excluded from `nodes` output:** Only discovered neighbors are listed in `nodes`; the start node itself is only recorded in `"start"`.
- **BFS stops expanding at `hops` depth:** Nodes at depth equal to `hops` are added to results but are not enqueued for further expansion.

**Constraints & edge cases:**

- Returns `{"error": ...}` if `project_data` is `None` or if no definition matching `name` is found.
- When multiple definitions match `name` (exact or partial), only the first candidate found is used as the start node; the selection order depends on file iteration order in `project_data["files"]`.
- If a `callee_usages` entry references a file not present in `file_index`, the target node is still emitted but its `type` will be an empty string.
- If an incoming `caller_usages` entry references a file not in `file_index`, `source_name` defaults to `"__module__"` and `source_type` to `""`.
- `hops=0` produces an empty `nodes` and `edges` result (the BFS loop body is never executed for the start node).

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies are present. `qa_tools.py` imports only standard library modules (`os`, `collections.deque`) and does not import any project-internal modules.

---

## Dependents (modules that import this file)

- `examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py` : Uses this module in the following ways:
  - **`qa_tools.project_data`** — Writes the loaded JSON data into this module-level variable to initialize the shared project knowledge state that tool functions depend on at runtime.
  - **`qa_tools.base_dir`** — Writes the base directory path (derived from the JSON file location) into this module-level variable so that `read_source_file` can resolve relative file paths correctly.
  - **`qa_tools.read_source_file`** — Registers this function as a tool for the RLM agent, enabling the agent to read source file contents by path.
  - **`qa_tools.get_files_using`** — Registers this function as a tool for the RLM agent, enabling the agent to look up which files depend on a specified file.
  - **`qa_tools.graph_search`** — Registers this function as a tool for the RLM agent, enabling the agent to perform BFS-based dependency graph traversal by definition name.

---

## Dependency Direction

| Relationship | Direction |
|---|---|
| `rlm_qa_agent.py` → `qa_tools.py` | **Unidirectional** — `rlm_qa_agent.py` imports and writes into `qa_tools.py`; `qa_tools.py` does not reference `rlm_qa_agent.py` in any way. |

`qa_tools.py` sits at the leaf of the dependency graph within this project: it has no project-internal imports of its own, and it exposes module-level mutable state (`project_data`, `base_dir`) alongside tool functions that `rlm_qa_agent.py` populates and consumes.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `project_data` (module variable) | `dict` (parsed JSON) | Entire project knowledge structure loaded externally by `rlm_qa_agent.py` via `qa_tools.project_data = json.load(f)` |
| `base_dir` (module variable) | `str` | Base directory path set externally by `rlm_qa_agent.py` via `qa_tools.base_dir = os.path.dirname(json_path)` |
| `path` argument to `read_source_file` | `str` | File path as listed in the JSON `"file"` field (e.g. `"code_analyzer/extract_imports/extract_imports.py"`) |
| `target_file` argument to `get_files_using` | `str` | Partial file path string used for substring matching |
| `name`, `hops`, `direction` arguments to `graph_search` | `str`, `int`, `str` | Definition name, BFS depth limit, and traversal direction |

This module does not read configuration files itself. All shared state (`project_data`, `base_dir`) is injected by the external caller (`rlm_qa_agent.py`) before any tool function is invoked.

---

## 2. Transformation Overview

### `read_source_file(path)`

```
Input path (str)
  → Strip leading "project_name/" prefix if present
  → Join with base_dir to form absolute path
  → Read file from filesystem
  → Return raw file content (str) or error message (str)
```

### `get_files_using(target_file)`

```
project_data["files"] (list of file entries)
  → Iterate all files → iterate each file's callee_usages[]
  → Filter: keep usages where target_file is a substring of usage["from"]
  → Collect matching pairs: {file entry's "file" field, matched usage dict}
  → Return list of result dicts
```

### `graph_search(name, hops, direction)`

```
project_data["files"]
  → Build file_index: dict keyed by file path for O(1) lookup

  → Find start node:
      Exact match on definition["name"] == name
      → Fallback: case-insensitive partial match
      → Take first candidate → derive start_key ("file:name")

  → BFS loop (queue of (key, file, name, hop)):
      For each node dequeued at hop < hops:

        [Outgoing edges] if direction in ("outgoing", "both"):
          → Read callee_usages of current file
          → Filter usages whose call-site lines fall within current definition's line range
          → For each qualifying usage:
              → Derive target_key ("target_file:target_name")
              → Look up target definition type in file_index
              → Append edge; if target not visited → append node, enqueue

        [Incoming edges] if direction in ("incoming", "both"):
          → Read caller_usages of current file
          → Filter usages where usage["name"] == current_name
          → Identify which definition in the source file contains the call-site lines
            (falls back to "__module__" if no definition spans those lines)
          → Derive source_key ("source_file:source_name")
          → Append edge; if source not visited → append node, enqueue

  → Return result dict: {start, hops, direction, nodes[], edges[]}
```

---

## 3. Outputs

| Function | Return Type | Description |
|----------|-------------|-------------|
| `read_source_file` | `str` | Raw file content, or an error message string on failure |
| `get_files_using` | `list[dict]` | List of `{"file": str, "usage": dict}` entries for each matching dependent |
| `graph_search` | `dict` | BFS result containing start node, discovered nodes, and edges (see Key Data Structures) |

No file writes or persistent side effects occur. Module variables `project_data` and `base_dir` are written exclusively by the external caller, not by any function in this module.

---

## 4. Key Data Structures

### `project_data` — top-level JSON structure (input)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"project_name"` | `str` | Used to strip the leading prefix from file paths in `read_source_file` |
| `"files"` | `list[dict]` | List of file entry objects iterated by all three tool functions |

### File entry object (element of `project_data["files"]`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | File path used as node identifier and for path matching |
| `"file_dependencies"` | `dict` | Contains `definitions`, `callee_usages`, and `caller_usages` for the file |

### `file_dependencies` sub-structure

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"definitions"` | `list[dict]` | Definitions declared in the file (functions, classes, etc.) |
| `"callee_usages"` | `list[dict]` | Symbols this file calls or uses, with source file reference |
| `"caller_usages"` | `list[dict]` | Records of other files calling symbols defined in this file |

### Definition dict (element of `definitions`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"name"` | `str` | Symbol name; used as the BFS node identifier component |
| `"type"` | `str` | Symbol type (e.g. function, class); attached to BFS nodes |
| `"start_line"` | `int` | Start of definition scope; used to determine which usages belong to this definition |
| `"end_line"` | `int` | End of definition scope |

### Callee usage dict (element of `callee_usages`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"name"` | `str` | Name of the symbol being used |
| `"from"` | `str` | File path where the symbol is defined; used for dependent lookup and outgoing edge targets |
| `"lines"` | `list[int]` | Line numbers of the call sites; used to assign usages to enclosing definitions |

### Caller usage dict (element of `caller_usages`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"name"` | `str` | Name of the symbol being called; matched against current BFS node name |
| `"file"` | `str` | File path of the caller; becomes the incoming edge source |
| `"lines"` | `list[int]` | Line numbers in the calling file; used to identify the enclosing definition |

### `get_files_using` result element

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | Path of the file that depends on the target |
| `"usage"` | `dict` | The full callee usage dict entry that matched the target_file |

### `graph_search` return dict

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"start"` | `str` | Start node key in `"file:name"` format |
| `"hops"` | `int` | The hop limit passed as input |
| `"direction"` | `str` | The direction passed as input |
| `"nodes"` | `list[dict]` | All discovered nodes excluding the start node |
| `"edges"` | `list[dict]` | All directed edges discovered during BFS |

### BFS node dict (element of `graph_search["nodes"]`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"key"` | `str` | Unique node identifier in `"file:name"` format |
| `"file"` | `str` | File path where the definition resides |
| `"name"` | `str` | Definition name; `"__module__"` when no enclosing definition is found |
| `"type"` | `str` | Symbol type from the definition dict |
| `"hop"` | `int` | BFS distance from the start node |
| `"via"` | `str` | `"outgoing"` or `"incoming"` indicating traversal direction |

### BFS edge dict (element of `graph_search["edges"]`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"source"` | `str` | Source node key (`"file:name"`) |
| `"target"` | `str` | Target node key (`"file:name"`) |
| `"hop"` | `int` | The hop count at which this edge was discovered |

# Error Handling

## 1. Overall Strategy

The file follows a **graceful degradation** strategy. Rather than raising exceptions or terminating execution, all error conditions return descriptive error values (strings or dicts) to the caller. This preserves the calling agent's ability to interpret and react to failures at runtime. Uninitialized state errors are surfaced early as string or dict messages, preventing silent misbehavior without crashing the process.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Uninitialized `base_dir` | `read_source_file()` called before `load_project()` sets `base_dir` | Returns an error string describing the missing initialization | Yes – caller can call `load_project()` and retry | File content not returned; tool call yields an error message |
| File read failure | `open()` raises any exception (e.g., file not found, permission denied) | Exception is caught; returns a formatted error string including the path and exception message | Yes – caller receives a descriptive message and can decide next action | File content not returned; tool call yields an error message |
| Uninitialized `project_data` | `graph_search()` called before `load_project()` sets `project_data` | Returns a dict with an `"error"` key describing the missing initialization | Yes – caller can call `load_project()` and retry | Graph search not performed; structured error dict returned |
| Definition not found (exact match) | No definition in any file matches the provided `name` exactly | Falls back to case-insensitive partial match across all definitions | Yes – search continues with partial match | May return unintended matches; first partial match candidate is used |
| Definition not found (partial match) | No definition matches even partially | Returns a dict with an `"error"` key | Yes – caller receives a structured error and can adjust the query | Graph search not performed; structured error dict returned |

---

## 3. Design Notes

- **Error return types are inconsistent by function**: `read_source_file()` returns a plain string on error (consistent with its normal return type), while `graph_search()` returns a dict with an `"error"` key (consistent with its normal return type). This allows each function's callers to handle errors within the same type contract as successful results.
- **No logging is performed**: Errors are communicated solely through return values. There is no side-channel output (e.g., `print`, `logging`), which keeps the functions self-contained and suitable for use as agent tools where the LLM interprets return values directly.
- **`get_files_using()` has no explicit error handling**: It relies on `project_data` being initialized and valid. If `project_data` is `None` or malformed, this function will raise an unhandled exception, unlike the other two functions. This represents an intentional or implicit assumption that `load_project()` has been called prior to use.
- **The exact-then-partial fallback in `graph_search()`** is a deliberate recovery mechanism for flexible name resolution, accepting the trade-off that the first partial match may not be the intended target.

# Summary

`qa_tools.py` provides stateful tool functions for querying a project knowledge graph loaded from `project_knowledge.json`. Module-level variables `project_data: dict` and `base_dir: str` must be set before use. Functions: `read_source_file(path: str) -> str`; `get_files_using(target_file: str) -> list[{"file": str, "usage": dict}]`; `graph_search(name: str, hops: int, direction: str) -> dict` with keys `start`, `nodes`, `edges`. Consumes file-entry dicts containing `definitions`, `callee_usages`, and `caller_usages`.
