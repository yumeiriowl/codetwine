# Design Document: examples/rlm_qa/qa_tools.py

## Overview & Purpose

## 1. Module Summary

Provides stateful tool functions for querying a loaded project knowledge graph, enabling LLM agents to read source files, find file dependents, and traverse definition-level dependency relationships via BFS graph search.

## 2. When to Use This Module

- **Initialize shared state before calling any tool**: Set `qa_tools.project_data` and `qa_tools.base_dir` externally (as done in `rlm_qa_agent.py` via `load_project()`) before invoking any function in this module.
- **Read raw source file content**: Call `read_source_file(path)` with a file path as listed in the project JSON to retrieve the full text of a source file from the output directory.
- **Find which files depend on a given file**: Call `get_files_using(target_file)` with a partial file path string to get a list of all files whose `callee_usages` reference that file, along with the specific usage details.
- **Explore dependency relationships around a definition**: Call `graph_search(name, hops, direction)` to BFS-traverse the dependency graph starting from a named definition, collecting reachable definitions and the edges between them within a specified hop count and direction.
- **Register tools for an LLM agent**: Pass `read_source_file`, `get_files_using`, and `graph_search` directly as callable tool references to a `dspy.RLM` instance.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `project_data` | — | `None` (initially) | Module-level variable holding the parsed `project_knowledge.json` data; must be set externally before use |
| `base_dir` | — | `None` (initially) | Module-level variable holding the base directory for resolving source file paths; must be set externally before use |
| `read_source_file` | `path: str` | `str` | Returns the full content of a source file identified by its project-relative path, or an error message on failure |
| `get_files_using` | `target_file: str` | `list` | Returns a list of `{"file": str, "usage": dict}` entries for all files whose `callee_usages` partially match the given file path |
| `graph_search` | `name: str`, `hops: int`, `direction: str` | `dict` | BFS-traverses the definition dependency graph from a named definition and returns matched nodes and edges within the specified hop count and direction |

## 4. Design Decisions

- **Module-level mutable state**: `project_data` and `base_dir` are module-level variables rather than parameters, allowing tool functions to be passed as bare callables (without arguments for context) to an LLM agent framework. The caller is responsible for populating these variables before invoking any tool.
- **Exact-then-partial match fallback in `graph_search`**: Definition lookup first attempts an exact name match; only if no results are found does it fall back to case-insensitive partial matching, balancing precision with usability when an exact name is unknown.
- **`__module__` sentinel name**: When `graph_search` processes incoming usages and cannot attribute a usage line to any named definition in the source file, it assigns the synthetic name `"__module__"` to represent module-level (top-level) code as a graph node.

## Definition Design Specifications

---

## Module-Level Variables

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `dict \| None` | Holds the entire parsed `project_knowledge.json` content. Initialized to `None`; populated externally by `load_project()` in `rlm_qa_agent.py`. |
| `base_dir` | `str \| None` | Filesystem path to the directory containing the source files. Initialized to `None`; populated externally by `load_project()` in `rlm_qa_agent.py`. |

**Constraint:** Both variables must be set before any tool function is called. Neither is initialized within this module itself.

---

## `read_source_file`

**Signature:**
```python
def read_source_file(path: str) -> str
```

**Responsibility:** Reads and returns the text content of a source file referenced in the project knowledge JSON, resolving the path relative to `base_dir`.

**When to use:** When a caller needs the raw source text of a file listed in `project_data["files"]` — for example, to extract a specific function's code using line numbers from the JSON.

**Design decisions:**
- Automatically strips a leading `project_name/` prefix from `path` if present, allowing callers to pass the `file` field value from the JSON directly without manual path manipulation.
- Returns an error string (rather than raising an exception) on any read failure, making it safe for use as a tool called by an LLM agent that expects a string return in all cases.
- Guards against uninitialized state by returning an error string if `base_dir` is `None`.

**Constraints & edge cases:**
- Requires `base_dir` and `project_data` to be set before invocation.
- All errors (file not found, permission denied, encoding issues) are surfaced as a descriptive string return value, not as raised exceptions.
- Path stripping only applies to a single leading `project_name/` prefix; nested or repeated prefixes are not handled.

---

## `get_files_using`

**Signature:**
```python
def get_files_using(target_file: str) -> list
```
`list` here means a list of dicts, each with the shape `{"file": str, "usage": dict}`.

**Responsibility:** Identifies all files in the project that depend on a given file by scanning `callee_usages` entries across all files for references whose `from` field contains `target_file`.

**When to use:** When a caller wants to know which files import from or otherwise reference a specific source file — i.e., to find the reverse-dependency (dependent) set of a file.

**Design decisions:**
- Uses partial string matching (`target_file in usage["from"]`) rather than exact matching, allowing callers to provide a short identifying substring rather than a full path.
- Returns every matching usage individually rather than grouping by file, so callers can inspect the specific symbol and location of each dependency.

**Constraints & edge cases:**
- Requires `project_data` to be populated before invocation (no guard check is present; will raise `TypeError` if `project_data` is `None`).
- Partial matching may produce false positives if `target_file` is a common substring of multiple file paths.
- The `usage` dict in each result is the raw entry from the JSON's `callee_usages` array; its structure depends entirely on the JSON schema.

---

## `graph_search`

**Signature:**
```python
def graph_search(name: str, hops: int = 1, direction: str = "both") -> dict
```

**Return type — plain-language description:**
A dict with keys:
| Key | Type | Description |
|---|---|---|
| `"start"` | `str` | The starting node in `"file:name"` format |
| `"hops"` | `int` | The hop limit used for the search |
| `"direction"` | `str` | The direction used for the search |
| `"nodes"` | `list[dict]` | All discovered nodes within the hop limit |
| `"edges"` | `list[dict]` | All directed edges traversed during the search |

Each node dict has keys: `key`, `file`, `name`, `type`, `hop`, `via`.  
Each edge dict has keys: `source`, `target`, `hop`.

**Responsibility:** Performs a breadth-first search over the project's definition dependency graph, discovering related definitions up to a specified hop depth in the outgoing (uses), incoming (used-by), or both directions.

**When to use:** When a caller needs to understand the dependency neighborhood of a named definition — for example, finding all definitions that a function calls, all callers of a function, or both, up to N levels deep.

**Design decisions:**
- Uses BFS rather than DFS, ensuring the shortest-hop path to each node is recorded first.
- Node keys use a `"file:name"` composite string, enabling disambiguation of same-named definitions across files.
- Falls back from exact name matching to case-insensitive partial matching when no exact match is found, improving usability as an LLM tool where the caller may not know the precise definition name.
- For outgoing edges, usage lines are checked against the current definition's line range to attribute a `callee_usage` to the correct definition within a file. A special `"__module__"` sentinel name covers module-level usages that fall outside any named definition's range.
- Deduplicates edges using a set of `(source, target, direction)` tuples and deduplicates nodes using a `visited` set, preventing cycles from causing infinite loops or duplicate results.
- For incoming edges, if a usage line does not fall within any named definition in the source file, the source node is attributed to `"__module__"` rather than being discarded.

**Constraints & edge cases:**
- Returns `{"error": ...}` if `project_data` is `None` or if the name cannot be found even with partial matching.
- When multiple definitions match the given `name`, only the first candidate found is used as the start node.
- The `direction` parameter must be one of `"outgoing"`, `"incoming"`, or `"both"`; no validation is performed and other values will silently produce no traversal results.
- `hops=1` means only direct neighbors are discovered; nodes at the start are not included in the `nodes` list.
- The `type` field on nodes depends on the JSON schema; it will be an empty string if the definition has no `type` field or if the target file is not found in the index.

## Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies are present. This file (`qa_tools.py`) imports only from the Python standard library (`os`, `collections.deque`) and does not import any project-internal modules.

---

## Dependents (modules that import this file)

- `examples/rlm_qa/rlm_qa_agent.py` → `codetwine/examples/rlm_qa/qa_tools_py/qa_tools.py` : Uses this module in two distinct ways:
  1. **State initialization** — Directly assigns to the module-level variables `qa_tools.project_data` and `qa_tools.base_dir` to inject the loaded project knowledge JSON and its base directory path into this module before any tool functions are called.
  2. **Tool registration** — Passes `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` as callable tools to a `dspy.RLM` instance, making these functions available as reasoning tools for the LLM agent.

---

## Dependency Direction

| Relationship | Direction |
|---|---|
| `qa_tools.py` → any project-internal module | None (no project-internal imports) |
| `rlm_qa_agent.py` → `qa_tools.py` | **Unidirectional** — `rlm_qa_agent.py` depends on `qa_tools.py`; `qa_tools.py` has no reference back to `rlm_qa_agent.py` |

The relationship is strictly unidirectional: `rlm_qa_agent.py` drives this module by populating its shared state variables and consuming its tool functions, while `qa_tools.py` itself remains unaware of its caller.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `project_data` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.project_data = json.load(f)` | Dict parsed from `project_knowledge.json` |
| `base_dir` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.base_dir = os.path.dirname(json_path)` | String (directory path) |
| `path` argument | Caller of `read_source_file()` | String file path as listed in the JSON `file` field |
| `target_file` argument | Caller of `get_files_using()` | String (partial match pattern) |
| `name`, `hops`, `direction` arguments | Caller of `graph_search()` | String, int, string |

Both `project_data` and `base_dir` are shared mutable module-level variables. All three tool functions depend on these being initialized before invocation; they do not accept these as parameters.

---

## 2. Transformation Overview

### `read_source_file(path)`

```
path (JSON field value)
  → Strip leading "project_name/" prefix if present
  → Join with base_dir to form absolute path
  → Read file from disk
  → Return raw file content as string
```

### `get_files_using(target_file)`

```
project_data["files"] (all file entries)
  → Iterate all files × their callee_usages entries
  → Filter: keep usages where target_file is a substring of usage["from"]
  → Collect matching pairs as {"file": ..., "usage": ...}
  → Return list of matched pairs
```

### `graph_search(name, hops, direction)`

```
project_data["files"]
  → Build file_index: dict keyed by file path for O(1) lookup

  → Find start definition:
      Exact match on definition["name"] == name
      → Fallback: case-insensitive partial match
      → Take first candidate; form start_key = "file:name"

  → BFS loop (queue of (key, file, name, hop)):
      For each dequeued node at hop < hops:

        [direction="outgoing" or "both"]
          → Scan callee_usages of current file
          → Filter: usage lines must fall within current definition's line range
          → For each match: form target_key = "target_file:target_name"
          → Look up target definition type from file_index
          → Record edge (source→target) and enqueue target node

        [direction="incoming" or "both"]
          → Scan caller_usages of current file
          → Filter: usage["name"] must equal current_name
          → Identify which definition in the source file contains the usage lines
            (defaults to "__module__" if no definition contains the lines)
          → Form source_key = "source_file:source_name"
          → Record edge (source→target) and enqueue source node

      Deduplication: visited set prevents re-enqueuing keys;
                     seen_edges set prevents duplicate edge entries

  → Return result dict with start, nodes, edges
```

---

## 3. Outputs

| Function | Return Value | Format |
|---|---|---|
| `read_source_file()` | File content, or error message string | `str` |
| `get_files_using()` | List of dependent file/usage pairs | `list[dict]` — see Key Data Structures |
| `graph_search()` | BFS traversal result with nodes and edges | `dict` — see Key Data Structures |

There are no file writes or other side effects in any of the three functions. The module-level variables `project_data` and `base_dir` are written by external code (`rlm_qa_agent.py`), not by this module itself.

---

## 4. Key Data Structures

### `project_data` — top-level structure (input, from JSON)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_name` | `str` | Used to strip the leading path prefix in `read_source_file` |
| `files` | `list[dict]` | All file entries; iterated by all three functions |

### File entry (element of `project_data["files"]`)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path used as node identity and for partial-match filtering |
| `file_dependencies` | `dict` | Contains `definitions`, `callee_usages`, `caller_usages` |

### `file_dependencies` sub-structure

| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Definitions declared in this file (name, type, start_line, end_line) |
| `callee_usages` | `list[dict]` | Symbols this file calls/imports (name, from, lines) |
| `caller_usages` | `list[dict]` | Symbols in this file used by other files (name, file, lines) |

### Definition entry (element of `definitions`)

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name; used as node identity in BFS |
| `type` | `str` | Symbol kind (e.g., function, class); propagated to graph nodes |
| `start_line` | `int` | Used to determine whether a usage falls inside this definition |
| `end_line` | `int` | Used to determine whether a usage falls inside this definition |

### `get_files_using()` result element

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Path of the file that depends on `target_file` |
| `usage` | `dict` | The full callee_usage entry that matched (name, from, lines, etc.) |

### `graph_search()` return dict

| Field / Key | Type | Purpose |
|---|---|---|
| `start` | `str` | Start node key in `"file:name"` format |
| `hops` | `int` | The `hops` argument passed by the caller |
| `direction` | `str` | The `direction` argument passed by the caller |
| `nodes` | `list[dict]` | All reachable nodes discovered by BFS |
| `edges` | `list[dict]` | All edges traversed during BFS |

### Node entry (element of `nodes`)

| Field / Key | Type | Purpose |
|---|---|---|
| `key` | `str` | Unique node identity in `"file:name"` format |
| `file` | `str` | File path where the definition lives |
| `name` | `str` | Definition name |
| `type` | `str` | Definition type looked up from the target file's definitions |
| `hop` | `int` | BFS distance from the start node |
| `via` | `str` | `"outgoing"` or `"incoming"` — which direction traversal reached this node |

### Edge entry (element of `edges`)

| Field / Key | Type | Purpose |
|---|---|---|
| `source` | `str` | Source node key (`"file:name"`) |
| `target` | `str` | Target node key (`"file:name"`) |
| `hop` | `int` | BFS hop at which this edge was discovered |

## Error Handling

## 1. Overall Strategy

The module applies a **graceful degradation** strategy. Rather than raising exceptions and terminating the calling process, functions return structured error values — either error-prefixed strings or dictionaries containing an `"error"` key — that allow the LLM agent caller to inspect and reason about the failure. Initialization-state errors (unset module-level variables) are surfaced immediately as early-return error messages rather than allowing execution to proceed into undefined behavior.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Uninitialized `base_dir` | `read_source_file` called before `load_project()` sets `base_dir` | Returns an error string `"Error: base_dir not initialized. Call load_project() first."` | Yes — calling `load_project()` and retrying resolves it | File content is unavailable; the tool call returns an error string instead of file text |
| Uninitialized `project_data` | `graph_search` called before `load_project()` sets `project_data` | Returns `{"error": "project_data not loaded. Call load_project() first."}` | Yes — calling `load_project()` and retrying resolves it | Graph search is entirely skipped; structured error dict is returned |
| File read failure | `open()` raises any exception (file not found, permission error, encoding issue, etc.) | Exception is caught; returns a formatted error string `"Error reading {path}: {e}"` | Yes — the agent can continue with other tools | The specific file's content is unavailable; no process termination |
| Definition not found (exact) | No definition in any file exactly matches the `name` argument passed to `graph_search` | Falls back to case-insensitive partial match before declaring failure | Yes — partial match is attempted automatically | No impact if partial match succeeds |
| Definition not found (partial) | No definition in any file partially matches `name` after the exact-match fallback | Returns `{"error": f"Definition '{name}' not found"}` | Yes — caller can retry with a different name | BFS graph traversal is skipped entirely; structured error dict is returned |
| Missing file in graph index | A `callee_usages` or `caller_usages` entry references a file not present in the index | The file lookup returns `None`; the entry is silently skipped via `continue` | Yes — BFS continues with remaining nodes | The specific missing dependency edge is omitted from results; traversal is otherwise unaffected |

---

## 3. Design Notes

- **Return-value signaling over exceptions.** All error conditions are communicated through return values rather than raised exceptions. This is consistent with the module's role as a tool library consumed by an LLM agent (`dspy.RLM`): the agent can read the error string or dict and decide how to proceed, rather than encountering an unhandled exception that would abort the tool call.

- **Two distinct error formats.** String-returning functions (`read_source_file`) use prefixed error strings, while dict-returning functions (`graph_search`) use a `{"error": ...}` dict. Each format matches the expected return type of the function, preserving type consistency for the caller.

- **State validation at function entry.** Checks for uninitialized module-level variables (`base_dir`, `project_data`) are performed at the start of each function that depends on them, providing an immediate and explicit signal if `load_project()` has not been called, rather than producing a later `AttributeError` or `TypeError`.

- **Silent skip for graph traversal gaps.** When a referenced file is absent from the in-memory index during BFS, the gap is silently skipped rather than reported as an error. This tolerates incomplete or partially indexed projects without aborting the search, prioritizing partial results over strict completeness.

## Summary

**qa_tools.py** — Provides stateful tool functions for querying a project knowledge graph loaded from `project_knowledge.json`.

Module-level variables `project_data` (dict) and `base_dir` (str) must be set externally before use.

**Public functions:**
- `read_source_file(path: str) → str`
- `get_files_using(target_file: str) → list[{"file": str, "usage": dict}]`
- `graph_search(name: str, hops: int, direction: str) → dict` with keys: `start`, `hops`, `direction`, `nodes` (list of `{key, file, name, type, hop, via}`), `edges` (list of `{source, target, hop}`)
