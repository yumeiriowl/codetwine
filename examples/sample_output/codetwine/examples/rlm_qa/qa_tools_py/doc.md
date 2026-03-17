# Design Document: examples/rlm_qa/qa_tools.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

`qa_tools.py` provides a set of query and traversal utility functions that operate on a loaded project knowledge graph (stored as a JSON structure in the module-level variable `project_data`). It exists as a separate file to encapsulate all data-access and graph-navigation logic used during Q&A or analysis sessions, keeping tool implementations isolated from project loading and session orchestration code. The module relies on two module-level variables (`project_data` and `base_dir`) that must be initialized externally before calling any tool function.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `read_source_file` | `path: str` | `str` | Reads and returns the raw text content of a source file located under `base_dir`, stripping any leading project-name prefix from the path. |
| `get_files_using` | `target_file: str` | `list[{"file": str, "usage": dict}]` | Returns all file entries whose `callee_usages` contain a `from` field that partially matches `target_file`, i.e., the reverse-dependency (dependent) lookup. |
| `graph_search` | `name: str`, `hops: int = 1`, `direction: str = "both"` | `dict` | Performs a BFS traversal of the definition dependency graph starting from a named definition, collecting reachable nodes and edges within the specified hop count and direction (`"outgoing"`, `"incoming"`, or `"both"`). |

---

## Design Decisions

- **Module-level shared state**: `project_data` and `base_dir` are intentionally module-level variables rather than function parameters, so tool functions can be called without repeatedly passing context. This implies that `load_project()` (defined elsewhere) must populate these variables before any tool is used; each function performs a guard check and returns an error value if the state is uninitialized.

- **Error returns instead of exceptions**: All three functions return error strings or dicts (e.g., `"Error: base_dir not initialized..."`, `{"error": "..."}`) rather than raising exceptions, making them safe to call in interactive or agent-driven Q&A contexts where a traceback would be disruptive.

- **BFS with visited set and edge deduplication**: `graph_search` uses a `deque`-based BFS and tracks visited node keys in a `set` to prevent cycles. Edges are also deduplicated via a `seen_edges` set keyed on `(source, target, direction)` tuples.

- **Exact-then-partial name matching**: `graph_search` first attempts an exact match on definition names; only if no results are found does it fall back to a case-insensitive substring match, providing predictable behavior while still accommodating imprecise queries.

- **Line-range scoping for outgoing edges**: When traversing `callee_usages` for outgoing edges, the function restricts matches to usage lines that fall within the current definition's `start_line`–`end_line` range, ensuring that edges are attributed to the correct definition rather than the file as a whole. The special sentinel name `"__module__"` covers usages that fall outside any named definition.

## Definition Design Specifications

# Definition Design Specifications

---

## `read_source_file(path: str) -> str`

**Responsibility:** Resolves a file path from the project knowledge JSON into an absolute path on disk and returns the file's raw text content. Exists to centralize the path-resolution logic so all tool functions share a consistent file-reading contract.

**Arguments:**
- `path` (`str`): A file path exactly as it appears in the `"file"` field of a `project_knowledge.json` entry (e.g. `"myproject/module/file.py"`).

**Returns:** `str` — the full UTF-8 text of the file, or an error message string if `base_dir` is uninitialized or the file cannot be opened.

**Design decisions:**
- Returns an error message string (rather than raising an exception) on failure, keeping the caller interface uniform: the return value is always a string regardless of success or failure.
- Automatically strips the leading `project_name/` prefix from `path` before joining with `base_dir`, because JSON entries record paths that include the project name, but the files on disk are stored relative to `base_dir` without that prefix.

**Preconditions / constraints:**
- Both module-level variables `base_dir` and `project_data` must be set (via `load_project()`) before calling; otherwise an error string is returned immediately.
- `path` is expected to use forward slashes as recorded in the JSON; behavior on other separators is OS-dependent.

---

## `get_files_using(target_file: str) -> list`

**Responsibility:** Answers the question "which files depend on this file?" by scanning the `callee_usages` entries of every file in the project and collecting those whose `from` field contains `target_file` as a substring.

**Arguments:**
- `target_file` (`str`): A partial or full file path to match against the `from` field of usage records.

**Returns:** `list` — a list of dicts, each with:
- `"file"` (`str`): path of the file that contains the usage.
- `"usage"` (`dict`): the raw `callee_usages` entry that matched.

An empty list is returned if no dependents are found.

**Design decisions:**
- Uses substring (partial) matching rather than exact matching so callers can query with a short, distinctive path fragment without needing to know the full canonical path.
- Returns the raw usage dict alongside the file path so callers have access to all usage metadata (name, lines, etc.) without a second lookup.

**Edge cases / constraints:**
- Assumes `project_data` is already populated; no guard is present, so calling before `load_project()` will raise an `AttributeError`.
- A broad `target_file` substring (e.g. a single character) may produce a large or unexpected result set.

---

## `graph_search(name: str, hops: int = 1, direction: str = "both") -> dict`

**Responsibility:** Performs a breadth-first traversal of the project's definition dependency graph starting from a named definition, up to a specified depth and in a specified direction. Provides a unified way to explore both "what does this definition depend on" and "what depends on this definition" without requiring callers to traverse the raw JSON manually.

**Arguments:**
- `name` (`str`): The definition name to use as the traversal root. Exact match is attempted first; if no exact match is found, a case-insensitive substring match is used as a fallback. The first match found is used as the start node.
- `hops` (`int`, default `1`): Maximum traversal depth. A value of `1` returns only direct neighbours; `2` returns neighbours of neighbours, and so on.
- `direction` (`str`, default `"both"`): Controls which edge types are followed.
  - `"outgoing"`: follow `callee_usages` — definitions that the current node calls or imports.
  - `"incoming"`: follow `caller_usages` — definitions that call or import the current node.
  - `"both"`: follow both directions.

**Returns:** `dict` with the following keys:
- `"start"` (`str`): The canonical key of the start node in `"file:name"` format.
- `"hops"` (`int`): The `hops` argument as provided.
- `"direction"` (`str`): The `direction` argument as provided.
- `"results"` (`list`): Each element is a dict describing a reached definition: `key`, `file`, `name`, `type`, `hop` (the distance from start), and `via` (`"outgoing"` or `"incoming"`).
- `"edges"` (`list`): Each element is a dict with `source`, `target` (both in `"file:name"` format), and `hop`.
- `"error"` (`str`): Present only when `project_data` is not loaded or the start definition cannot be found; in these cases the other keys are absent.

**Design decisions:**
- Nodes are identified by a `"file:name"` composite key rather than name alone to handle definitions with the same name in different files without collision.
- Outgoing edge scope is constrained to usages whose source lines fall within the line range of the current definition, ensuring that usages belonging to sibling definitions in the same file are not incorrectly attributed to the current node. A special `"__module__"` sentinel represents module-level code (lines not enclosed by any definition).
- Incoming edge traversal identifies the enclosing definition in the calling file by checking which definition's line range contains the usage line, also assigning `"__module__"` when no enclosing definition is found.
- Both `results` and `edges` deduplicate via `visited` and `seen_edges` sets respectively, so each node appears once in `results` and each directed edge appears once in `edges` even when multiple usage lines produce the same logical edge.
- The BFS queue is pruned at `current_hop >= hops`, meaning nodes at exactly the `hops` distance are recorded in `results` but are not themselves expanded further.

**Edge cases / constraints:**
- If `name` matches multiple definitions (after exact or partial matching), only the first match encountered during file iteration is used as the start node.
- If `project_data` is `None`, returns a dict containing only `"error"`.
- `hops=0` causes the BFS loop body to be skipped entirely (the start node is not added to `results`), so the function returns an empty `results` and `edges` with only the `start` key populated.
- Definitions referenced in `callee_usages` or `caller_usages` that do not exist in `file_index` (e.g. external dependencies) are included in `results` with an empty `type`, but cannot be expanded further because no `file_data` is available for them.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports (`os`, `collections.deque`) are standard library modules. The module operates on data passed in via the module-level variables `project_data` and `base_dir`, which are expected to be populated externally by a `load_project()` call, but no project-internal module is imported directly by this file.

### Dependents (what uses this file)

No dependent information is provided for this file.

### Direction of Dependency

Not applicable — this file neither imports project-internal modules nor has documented dependents using it.

## Data Flow

# Data Flow

## Module-Level State

Two module variables act as shared state, initialized externally via `load_project()`:

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `dict` | Entire parsed `project_knowledge.json`; root of all lookups |
| `base_dir` | `str` | Filesystem base directory for resolving source file paths |

All three functions read these variables directly; neither accepts them as parameters.

---

## `read_source_file(path)`

```
Input:  path (str) — file path as stored in JSON "file" field
        project_data["project_name"] — used to strip leading prefix

  Strip "project_name/" prefix from path
  Join base_dir + stripped path
  Open and read file

Output: file content (str) | error message (str)
```

**Path normalization rule:**
```
raw path:   "myproject/src/foo.py"
stripped:   "src/foo.py"
resolved:   os.path.join(base_dir, "src/foo.py")
```

---

## `get_files_using(target_file)`

```
Input:  target_file (str) — partial path string to match against

  For each file in project_data["files"]:
    For each entry in file_dependencies.callee_usages:
      if target_file in usage["from"]:  ← partial string match
        collect {file, usage}

Output: list of match records
```

**Output record structure:**

| Field | Type | Content |
|---|---|---|
| `file` | `str` | Path of the file that contains the usage |
| `usage` | `dict` | Raw `callee_usages` entry (includes `name`, `from`, `lines`) |

---

## `graph_search(name, hops, direction)`

### Data Sources

```
project_data["files"][*]
  └── file_dependencies
        ├── definitions    — [{name, type, start_line, end_line}, ...]
        ├── callee_usages  — [{name, from, lines}, ...]   (outgoing edges)
        └── caller_usages  — [{name, file, lines}, ...]   (incoming edges)
```

### BFS Transformation Flow

```
name (str)
  │
  ▼
Exact-match search in all definitions
  → fallback: partial (case-insensitive) match
  → starts[0] selected as seed node
  │
  ▼
start_key = "file_path:definition_name"   ← canonical node identifier
  │
  ▼
BFS queue: (key, file, name, hop)
  │
  ├─[outgoing]─► callee_usages entries within current definition's line range
  │               → target_key = "from_file:usage_name"
  │               → look up type from target file's definitions
  │
  └─[incoming]─► caller_usages entries matching current definition name
                  → identify which definition in source file contains the call line
                  → source_key = "source_file:enclosing_definition" (or ":__module__")

  Deduplication via: visited set (nodes), seen_edges set (edges)
  BFS stops expanding when current_hop >= hops
```

### Output Structure

```json
{
  "start":     "file:name",
  "hops":      int,
  "direction": "outgoing"|"incoming"|"both",
  "results": [
    { "key": "file:name", "file": str, "name": str,
      "type": str, "hop": int, "via": "outgoing"|"incoming" }
  ],
  "edges": [
    { "source": "file:name", "target": "file:name", "hop": int }
  ]
}
```

**Key field semantics:**

| Field | Purpose |
|---|---|
| `key` | Canonical node ID: `"<file_path>:<definition_name>"` |
| `hop` | Distance from start node (1 = direct neighbor) |
| `via` | Edge direction that discovered this node |
| `source`/`target` in edges | Always oriented as caller → callee regardless of traversal direction |
| `__module__` | Sentinel name used when a call site falls outside any named definition |

## Error Handling

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** approach. Rather than raising exceptions and halting execution, functions absorb errors internally and return fallback values (error message strings, empty lists, or error-keyed dicts) to the caller. The module assumes that `load_project()` has been called to initialize `project_data` and `base_dir` before any tool function is used; violations of this precondition are surfaced as soft errors rather than exceptions.

---

## Main Error Patterns

| Error Type | Handling | Impact |
|---|---|---|
| `base_dir` not initialized (`None`) | Returns an error message string immediately | `read_source_file` cannot operate; caller receives a string instead of file content |
| `project_data` not initialized (`None`) | Returns a dict with an `"error"` key immediately | `graph_search` cannot operate; caller receives an error dict instead of results |
| File I/O failure (e.g., file not found, permission denied) | Exception caught; returns a formatted error message string | Only the requested file is affected; other operations are unaffected |
| Definition name not found (exact match) | Falls back to partial (case-insensitive) match; returns an error dict only if both passes fail | BFS may proceed on a partially matched node rather than the intended one |
| Missing or unknown file in the dependency graph during BFS | Silently skips the node and continues traversal | Portions of the dependency graph may be omitted from results without notification |

---

## Design Considerations

- **Uniform return types are not enforced across functions.** `read_source_file` uses a plain error string, while `graph_search` uses a structured `{"error": ...}` dict. Callers must apply different checks per function to distinguish success from failure.
- **Precondition violations are treated as soft errors.** The absence of initialization (unset module-level variables) is detected at call time rather than at import time, keeping the module importable in any state but deferring the failure signal to first use.
- **BFS errors are silent by design.** Missing graph nodes during traversal are skipped without any warning, prioritizing traversal continuity over completeness guarantees.

## Summary

`qa_tools.py` provides graph traversal and file-access utilities for a project knowledge graph. It exposes three functions: `read_source_file` reads source files from disk after stripping the project-name prefix; `get_files_using` performs reverse-dependency lookup by substring-matching callee usages; `graph_search` runs BFS over the definition dependency graph with configurable depth and direction. All functions operate on module-level variables `project_data` (parsed JSON knowledge graph) and `base_dir` (filesystem root), which must be initialized externally. Errors are returned as strings or dicts rather than raised as exceptions.
