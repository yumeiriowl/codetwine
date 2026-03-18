# Design Document: examples/rlm_qa/qa_tools.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Provides a set of stateful tool functions for querying a loaded project knowledge graph, enabling callers to read source files, look up file dependents, and traverse definition-level dependency graphs via BFS.

## 2. When to Use This Module

- **Reading source file content**: Call `read_source_file(path)` when you need the raw text of a source file identified by its path as recorded in `project_knowledge.json`.
- **Finding files that depend on a given file**: Call `get_files_using(target_file)` when you need to know which files import or reference a specific file, receiving a list of file paths and their specific usages.
- **Exploring the dependency graph around a definition**: Call `graph_search(name, hops, direction)` when you need to discover which definitions a given symbol depends on (`"outgoing"`), which definitions depend on it (`"incoming"`), or both, up to N hops away.
- **Initializing module state before calling any tool**: Set `project_data` and `base_dir` (as done in `rlm_qa_agent.py`) before invoking any of the above functions; all three tool functions rely on these module-level variables.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `project_data` | — | `dict \| None` | Module-level variable holding the parsed contents of `project_knowledge.json`; must be set before calling tool functions |
| `base_dir` | — | `str \| None` | Module-level variable holding the base directory for resolving source file paths; must be set before calling `read_source_file` |
| `read_source_file` | `path: str` | `str` | Returns the full text content of the source file at the given path, or an error message string on failure |
| `get_files_using` | `target_file: str` | `list` | Returns a list of `{"file": str, "usage": dict}` entries for every file whose `callee_usages` partially match the given file path |
| `graph_search` | `name: str`, `hops: int`, `direction: str` | `dict` | Performs BFS from a named definition and returns reachable definitions and edges within the specified hop count and direction |

## 4. Design Decisions

- **Module-level mutable state**: `project_data` and `base_dir` are module-level variables rather than function parameters or a class instance. This allows the tool functions to be passed as plain callables (e.g., into a `tools=[...]` list in `rlm_qa_agent.py`) without requiring callers to supply context on every invocation.
- **Exact-then-partial match fallback in `graph_search`**: The BFS start node search first attempts an exact match on definition name, then falls back to a case-insensitive partial match. This makes the function usable with approximate or abbreviated names while still preferring precise matches.
- **Line-range scoping for outgoing edges**: `graph_search` restricts `callee_usages` to those whose usage line numbers fall within the current definition's `start_line`–`end_line` range, ensuring that outgoing edges are attributed to the specific definition that contains the call rather than to the file as a whole.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Variables

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `dict \| None` | Holds the entire parsed `project_knowledge.json` content. Must be set externally (by `load_project()` in `rlm_qa_agent.py`) before any tool function is called. |
| `base_dir` | `str \| None` | Filesystem path to the directory containing the source files. Derived from the JSON file's location. Must be set alongside `project_data`. |

**Constraints:** Both variables default to `None`. All three tool functions depend on at least one of them being initialized. Neither variable is set by any function within this file itself.

---

## `read_source_file`

**Signature:**
```
read_source_file(path: str) -> str
```

**Responsibility:** Resolves a file path recorded in the JSON metadata to an absolute filesystem path and returns the file's text content. Exists to let callers retrieve raw source code for inspection given a path entry from `project_data`.

**When to use:** When a caller needs the raw text of a source file identified by a path string from a `project_data["files"]` entry.

**Design decisions:**
- Automatically strips a leading `<project_name>/` prefix from `path` before joining with `base_dir`, accommodating the convention that JSON file fields include the project name as a prefix component.
- Returns a human-readable error string (rather than raising an exception) on any read failure, making it safe to use inside agent tool loops where exceptions would interrupt execution.

**Constraints & edge cases:**
- Returns an error string if `base_dir` is `None`.
- If `project_data` has no `"project_name"` key, no prefix stripping occurs.
- Prefix stripping only applies when the path starts with exactly `<project_name>/`; deeper nesting or alternative separators are not handled.

---

## `get_files_using`

**Signature:**
```
get_files_using(target_file: str) -> list
```
`list` — a list of dicts, each with shape `{"file": str, "usage": dict}`, where `"file"` is the path of the dependent file and `"usage"` is the raw `callee_usages` entry that matched.

**Responsibility:** Performs a reverse dependency lookup across all files, finding every file that declares a usage originating from `target_file`. Exists to answer "what files depend on this file?"

**When to use:** When a caller needs to identify all files that import from or reference a specific source file.

**Design decisions:**
- Uses partial string matching (`target_file in usage["from"]`) rather than exact matching, so callers can pass a short identifying substring rather than a full path.
- Searches `callee_usages` entries across all files in `project_data`, making it a full-project scan.

**Constraints & edge cases:**
- Returns an empty list if no matches are found; raises no error.
- Partial matching can produce false positives if `target_file` is a substring of an unrelated path.
- Depends on `project_data` being initialized; will raise `TypeError` if called before `project_data` is set.

---

## `graph_search`

**Signature:**
```
graph_search(name: str, hops: int = 1, direction: str = "both") -> dict
```

**Return type:** A dict with the following structure:

| Key | Type | Description |
|---|---|---|
| `"start"` | `str` | The starting node key in `"<file>:<name>"` format |
| `"hops"` | `int` | The requested hop limit |
| `"direction"` | `str` | The requested direction |
| `"results"` | `list[dict]` | Found definitions reachable within the hop limit |
| `"edges"` | `list[dict]` | Directed edges traversed during the search |

Each entry in `"results"`:

| Field | Type | Description |
|---|---|---|
| `"key"` | `str` | `"<file>:<name>"` identifier |
| `"file"` | `str` | File path of the definition |
| `"name"` | `str` | Symbol name |
| `"type"` | `str` | Definition type (e.g., function, class) |
| `"hop"` | `int` | Distance from the start node |
| `"via"` | `"outgoing" \| "incoming"` | Direction through which this node was reached |

Each entry in `"edges"`:

| Field | Type | Description |
|---|---|---|
| `"source"` | `str` | `"<file>:<name>"` of the source node |
| `"target"` | `str` | `"<file>:<name>"` of the target node |
| `"hop"` | `int` | Hop number at which this edge was discovered |

**Responsibility:** Performs a BFS over the dependency graph rooted at a named definition, collecting reachable definitions and the edges connecting them up to a specified depth. Exists to enable structured dependency exploration for agent-driven code analysis.

**When to use:** When a caller needs to understand what a definition depends on, what depends on it, or both, up to an arbitrary depth.

**Design decisions:**
- **Name resolution:** Performs exact match first; falls back to case-insensitive partial match if no exact match is found. Only the first match is used as the start node.
- **Node key format:** Uses `"<file_path>:<symbol_name>"` as a composite key to distinguish identically named symbols in different files.
- **Module-level code:** Usage lines that fall outside any named definition's line range are attributed to a synthetic node named `"__module__"`, representing top-level module code.
- **Outgoing edge filtering:** For a definition node, only `callee_usages` entries whose usage lines fall within that definition's declared line range are considered its outgoing edges, scoping each edge to a specific definition rather than the whole file.
- **Duplicate suppression:** Both visited nodes and seen edges are tracked separately using sets, preventing cycles from causing infinite traversal and preventing duplicate edges in the output.
- **BFS termination:** Nodes at exactly `hops` depth are added to results but not enqueued for further expansion, enforcing the depth limit strictly.

**Constraints & edge cases:**
- Returns `{"error": ...}` if `project_data` is `None` or if `name` cannot be matched.
- `direction` must be one of `"outgoing"`, `"incoming"`, or `"both"`; no validation is performed — an unrecognized value silently produces no results.
- If a `callee_usages` entry references a file not present in `project_data`, its `target_type` is left as an empty string and traversal into it is skipped.
- For `"__module__"` nodes, outgoing edge detection uses a different containment check than named definitions.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies are present. This file (`qa_tools.py`) imports only from the Python standard library (`os`, `collections.deque`) and defines tool functions that operate on module-level variables (`project_data`, `base_dir`) injected by an external caller.

## Dependents (modules that import this file)

- `examples/rlm_qa/rlm_qa_agent.py` → `codetwine/examples/rlm_qa/qa_tools_py/qa_tools.py` : The agent module uses this file for the following purposes:
  - **`qa_tools.project_data`** and **`qa_tools.base_dir`**: Directly assigns these module-level variables after loading a `project_knowledge.json` file, initializing the shared state required by all tool functions.
  - **`qa_tools.read_source_file`**: Registers this function as a callable tool for reading source file contents from the loaded project.
  - **`qa_tools.get_files_using`**: Registers this function as a callable tool for finding files that depend on a given file.
  - **`qa_tools.graph_search`**: Registers this function as a callable tool for performing BFS-based dependency graph searches.

## Dependency Direction

| Relationship | Direction |
|---|---|
| `qa_tools.py` → any project-internal module | None (no project-internal imports) |
| `rlm_qa_agent.py` → `qa_tools.py` | **Unidirectional**: `rlm_qa_agent.py` depends on `qa_tools.py`; `qa_tools.py` has no knowledge of or reference to `rlm_qa_agent.py` |

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `project_data` | Module-level variable, set externally by `rlm_qa_agent.py` via `qa_tools.project_data = json.load(f)` | `dict` parsed from `project_knowledge.json` |
| `base_dir` | Module-level variable, set externally by `rlm_qa_agent.py` via `qa_tools.base_dir = os.path.dirname(json_path)` | `str` (directory path) |
| `path` argument | Caller of `read_source_file()` | `str` (relative file path as listed in the JSON `"file"` field) |
| `target_file` argument | Caller of `get_files_using()` | `str` (partial file path for matching) |
| `name`, `hops`, `direction` arguments | Caller of `graph_search()` | `str`, `int`, `str` |

The module has no initialization logic of its own. All shared state (`project_data`, `base_dir`) is injected by the external caller (`rlm_qa_agent.py`) before any tool function is invoked.

---

## 2. Transformation Overview

### `read_source_file(path)`

```
path (str)
  → Strip leading "project_name/" prefix if present
  → Join with base_dir to form an absolute filesystem path
  → Read file from disk
  → Return raw file content (str)
```

### `get_files_using(target_file)`

```
project_data["files"] (list of file entries)
  → Iterate all files → iterate each file's callee_usages[]
  → Filter: keep usages where target_file is a substring of usage["from"]
  → Collect matching entries as {"file": file_entry["file"], "usage": usage_dict}
  → Return list of matches
```

### `graph_search(name, hops, direction)`

```
project_data["files"]
  → Build file_index: {file_path → file_entry} for O(1) lookup

  → Find start definition(s):
      Exact match on definition["name"] == name
      → Fallback: partial match (name.lower() in definition["name"].lower())
      → Take first match as start node

  → BFS from start_key = "file_path:def_name":
      Each iteration pops (current_key, current_file, current_name, current_hop)
      If current_hop >= hops → skip (boundary enforcement)

      direction="outgoing" or "both":
        → Scan callee_usages of current file
        → Filter usages whose line numbers fall within current definition's line range
        → Each match → build target_key = "target_file:target_name"
        → Look up target definition type from file_index
        → Record edge {source, target, hop}
        → If target_key unvisited → add to results, enqueue

      direction="incoming" or "both":
        → Scan caller_usages of current file, filter by name == current_name
        → For each caller line, identify which definition in the source file contains it
          → source_name = that definition's name (or "__module__" if none found)
        → Build source_key = "source_file:source_name"
        → Record edge {source, target, hop}
        → If source_key unvisited → add to results, enqueue

  → Return structured result dict with start, hops, direction, results[], edges[]
```

---

## 3. Outputs

| Function | Output | Format |
|---|---|---|
| `read_source_file()` | File contents, or an error message string on failure | `str` |
| `get_files_using()` | List of files that depend on the target file, with their matching usage entries | `list[dict]` — see Key Data Structures |
| `graph_search()` | BFS traversal result with discovered nodes and edges | `dict` — see Key Data Structures |

No file writes or side effects are produced by any tool function. All output is returned to the caller as return values.

---

## 4. Key Data Structures

### `project_data` (injected module variable)

The top-level structure read from `project_knowledge.json`:

| Field / Key | Type | Purpose |
|---|---|---|
| `"project_name"` | `str` | Used to strip the leading path prefix in `read_source_file()` |
| `"files"` | `list[dict]` | All file entries iterated by every tool function |

### File entry (element of `project_data["files"]`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Relative path of the source file |
| `"file_dependencies"` | `dict` | Contains definitions, callee_usages, and caller_usages for the file |

### `file_dependencies` dict

| Field / Key | Type | Purpose |
|---|---|---|
| `"definitions"` | `list[dict]` | Definitions declared in this file |
| `"callee_usages"` | `list[dict]` | Symbols this file calls/uses from other files |
| `"caller_usages"` | `list[dict]` | Records of other files calling symbols defined in this file |

### Definition entry (element of `definitions`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Symbol name; used as a node identifier in BFS |
| `"type"` | `str` | Symbol type (e.g., function, class); included in `graph_search` results |
| `"start_line"` | `int` | Used in `graph_search` to determine if a usage falls within this definition |
| `"end_line"` | `int` | Used in `graph_search` to determine if a usage falls within this definition |

### Callee usage entry (element of `callee_usages`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the symbol being used |
| `"from"` | `str` | File path where the symbol originates; matched against `target_file` in `get_files_using()` |
| `"lines"` | `list[int]` | Line numbers of the usage; used in `graph_search` for range filtering |

### Caller usage entry (element of `caller_usages`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the symbol being called; matched against `current_name` in BFS |
| `"file"` | `str` | File path of the caller |
| `"lines"` | `list[int]` | Line numbers of the call; used to identify the containing definition in the caller file |

### `get_files_using()` return value — list element

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Path of the file that depends on the target |
| `"usage"` | `dict` | The full callee_usage entry that matched the target_file |

### `graph_search()` return value

| Field / Key | Type | Purpose |
|---|---|---|
| `"start"` | `str` | Start node key in `"file_path:def_name"` format |
| `"hops"` | `int` | The `hops` argument passed to the function |
| `"direction"` | `str` | The `direction` argument passed to the function |
| `"results"` | `list[dict]` | All discovered nodes within the hop limit |
| `"edges"` | `list[dict]` | All traversed edges between nodes |

### `graph_search()` result node (element of `"results"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"key"` | `str` | Node identifier in `"file_path:def_name"` format |
| `"file"` | `str` | File path containing the definition |
| `"name"` | `str` | Definition name |
| `"type"` | `str` | Definition type looked up from `file_index` |
| `"hop"` | `int` | BFS distance from start node |
| `"via"` | `str` | `"outgoing"` or `"incoming"` indicating traversal direction |

### `graph_search()` edge (element of `"edges"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"source"` | `str` | Source node key in `"file_path:def_name"` format |
| `"target"` | `str` | Target node key in `"file_path:def_name"` format |
| `"hop"` | `int` | BFS hop level at which this edge was discovered |

## Error Handling

# Error Handling

## 1. Overall Strategy

The module adopts a **graceful degradation** approach. Rather than raising exceptions that would terminate the caller, errors are surfaced as in-band return values — either descriptive error strings (for string-returning functions) or error-keyed dicts (for dict-returning functions). This keeps the LLM agent's tool-call loop alive even when individual tool invocations encounter problems. Uninitialized module-level state (`project_data`, `base_dir`) is detected eagerly at the entry point of each function and short-circuited before any further processing occurs.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Uninitialized `base_dir` | `read_source_file` is called before `load_project()` sets `base_dir` | Returns a fixed error string indicating that `load_project()` must be called first | No | The file read is aborted; the caller receives an error string instead of file content |
| Uninitialized `project_data` | `graph_search` is called before `load_project()` sets `project_data` | Returns a dict with an `"error"` key and a descriptive message | No | The entire graph search is aborted; the caller receives an error dict |
| File read failure | The target file does not exist, cannot be opened, or any other OS/IO exception occurs during `open()` | Exception is caught and its message is embedded in a returned error string | No | The file read is aborted; the caller receives an error string describing the exception |
| Definition not found (exact) | No definition in any file exactly matches the `name` argument passed to `graph_search` | Falls back silently to a partial (case-insensitive substring) match search | Yes | Search continues with partial-match candidates; no error is surfaced if any partial match exists |
| Definition not found (partial) | Neither exact nor partial match finds any definition for `name` | Returns a dict with an `"error"` key stating the definition was not found | No | The graph search is aborted; the caller receives an error dict |
| Missing file entry during BFS | A dependency edge references a file path that has no corresponding entry in `file_index` | The BFS iteration for that node is silently skipped via a `continue` | Yes | The unreachable node and its subtree are omitted from results; traversal continues for other nodes |

---

## 3. Design Notes

- **In-band error signaling** is used consistently rather than raising exceptions, matching the expectation that tool functions called by an LLM agent return values the agent can inspect and reason about.
- **State initialization guard** is applied at the function boundary rather than at module load time, because `project_data` and `base_dir` are intentionally set externally by `rlm_qa_agent.py` after import. Each function that depends on these variables independently checks their validity.
- **Exact-then-partial fallback** in `graph_search` is a silent policy: no warning or indicator is returned to the caller to signal that a fallback occurred. The caller receives results as if the partial match were the intended start point.
- **Missing BFS nodes** are silently skipped rather than flagged, meaning result completeness is not guaranteed when the dependency graph references files absent from the loaded project data.

## Summary

**qa_tools.py** provides stateful tool functions for querying a project knowledge graph loaded from `project_knowledge.json`.

Module-level variables `project_data: dict|None` and `base_dir: str|None` must be set externally before use.

Public functions:
- `read_source_file(path: str) -> str`
- `get_files_using(target_file: str) -> list[{"file": str, "usage": dict}]`
- `graph_search(name: str, hops: int, direction: str) -> {"start": str, "hops": int, "direction": str, "results": list[dict], "edges": list[dict]}`

Consumes `project_data["files"]` entries containing `definitions`, `callee_usages`, and `caller_usages` dicts.
