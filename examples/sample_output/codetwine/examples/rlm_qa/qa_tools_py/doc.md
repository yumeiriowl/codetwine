# Design Document: examples/rlm_qa/qa_tools.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Provide stateless tool functions for reading source files and traversing dependency graphs defined in a loaded `project_knowledge.json`, intended to be registered as callable tools in an LLM-driven code Q&A agent.

## 2. When to Use This Module

- **Reading the raw source of a project file**: Call `read_source_file(path)` with a file path as recorded in the JSON (e.g., `"code_analyzer/extract_imports/extract_imports.py"`) to retrieve its full text content.
- **Finding all files that depend on a given file**: Call `get_files_using(target_file)` with a partial file path string to obtain every file that references it through `callee_usages`, along with the specific usage entry.
- **Exploring the dependency graph around a named definition**: Call `graph_search(name, hops, direction)` to perform a BFS traversal from a named definition, returning reachable nodes and edges within the specified number of hops and direction (`"outgoing"`, `"incoming"`, or `"both"`).
- **Initializing the module before any tool call**: The caller (e.g., `rlm_qa_agent.py`) must set the module-level variables `project_data` and `base_dir` before invoking any tool function; all three functions depend on these variables being populated.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `read_source_file` | `path: str` | `str` | Reads and returns the content of a project source file located under `base_dir`, stripping any leading `project_name/` prefix from the path. Returns an error message string on failure. |
| `get_files_using` | `target_file: str` | `list` | Returns a list of `{"file": str, "usage": dict}` entries for every file whose `callee_usages` contains a `from` field partially matching `target_file`. |
| `graph_search` | `name: str`, `hops: int = 1`, `direction: str = "both"` | `dict` | Performs a BFS from the first definition matching `name` (exact, then partial), returning discovered nodes and directed edges within `hops` steps. Supports `"outgoing"` (dependencies), `"incoming"` (dependents), or `"both"` traversal directions. |
| `project_data` | — | `dict \| None` | Module-level variable holding the parsed contents of `project_knowledge.json`; must be set externally before calling any tool function. |
| `base_dir` | — | `str \| None` | Module-level variable holding the base directory path for resolving source files; must be set externally before calling `read_source_file`. |

## 4. Design Decisions

- **Module-level mutable state (`project_data`, `base_dir`)**: Rather than passing project data as parameters, the module exposes these as settable module-level variables. This allows the functions to match the zero-configuration calling convention expected by LLM tool frameworks (such as `dspy.RLM`), where tools are registered as plain callables without injected context.
- **Exact-then-partial-match fallback in `graph_search`**: Definition lookup first attempts an exact name match; only if no candidates are found does it fall back to a case-insensitive partial match. This prioritises precision while remaining useful for exploratory queries.
- **BFS with edge deduplication via `seen_edges`**: `graph_search` tracks seen edges as `(source_key, target_key, direction)` triples independently from visited nodes, preventing duplicate edges when the same dependency relationship is reachable through multiple paths.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Variables

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `dict \| None` | Holds the entire parsed `project_knowledge.json` content. Initialized to `None`; must be populated by `load_project()` in `rlm_qa_agent.py` before any tool function is called. |
| `base_dir` | `str \| None` | Filesystem path to the directory containing the source files. Derived from the JSON file's location. Initialized to `None`; must be set alongside `project_data`. |

Both variables are module-level globals written directly by the caller (`rlm_qa_agent.py`) via attribute assignment (`qa_tools.project_data = ...`, `qa_tools.base_dir = ...`). All three tool functions depend on these being non-`None`.

---

## `read_source_file`

**Signature**
```
read_source_file(path: str) -> str
```

**Responsibility**
Reads a source file from the local filesystem using a path as recorded in `project_knowledge.json`, returning its full text content. Provides a safe error message string instead of raising an exception on failure.

**When to use**
Call this when the raw source text of a file is needed — for example, to extract a specific function body using line-number metadata from `project_data`.

**Design decisions**
- Automatically strips a leading `project_name/` prefix from `path` before constructing the filesystem path, handling the common mismatch between JSON-recorded paths and actual file locations under `base_dir`.
- Returns an error message string (rather than raising) on any read failure, keeping tool call behavior predictable for the LLM agent.

**Constraints & edge cases**
- Returns an error string (not `None` or an exception) if `base_dir` is `None`.
- `project_data` must be set before calling, because the prefix-stripping logic reads `project_data["project_name"]`.
- If the path does not start with `project_name/`, no stripping occurs and the path is used as-is relative to `base_dir`.

---

## `get_files_using`

**Signature**
```
get_files_using(target_file: str) -> list
```
Return type is a list of dicts, each with the shape `{"file": str, "usage": dict}`, where `file` is the path of the dependent file and `usage` is the raw `callee_usages` entry from `project_data`.

**Responsibility**
Performs a reverse dependency lookup: given a file path fragment, finds all files that import or call something from it, based on `callee_usages` records in `project_data`.

**When to use**
Call this to determine which files depend on a specific module or file — for example, to assess the blast radius of a change to a given file.

**Design decisions**
- Uses a partial/substring match against the `from` field of each `callee_usages` entry, so callers do not need to supply an exact full path.
- Scans all files in `project_data["files"]` unconditionally; there is no index-based optimization.

**Constraints & edge cases**
- Assumes `project_data` is already loaded; performs no guard check and will raise `TypeError` if `project_data` is `None`.
- A short or common `target_file` substring may match unintended entries.
- Returns an empty list if no matches are found.

---

## `graph_search`

**Signature**
```
graph_search(name: str, hops: int = 1, direction: str = "both") -> dict
```

Return type is a dict with the following structure:

| Key | Type | Description |
|---|---|---|
| `start` | `str` | Start node in `"file_path:definition_name"` format |
| `hops` | `int` | The `hops` argument as provided |
| `direction` | `str` | The `direction` argument as provided |
| `nodes` | `list[dict]` | Definitions discovered during traversal |
| `edges` | `list[dict]` | Directed edges between definition nodes |
| `error` | `str` | Present only when initialization or lookup fails |

Each entry in `nodes` has the shape:

| Field | Type | Description |
|---|---|---|
| `key` | `str` | `"file:name"` unique identifier |
| `file` | `str` | File path of the definition |
| `name` | `str` | Definition name |
| `type` | `str` | Definition type (e.g., `"function"`, `"class"`) |
| `hop` | `int` | Distance from start node |
| `via` | `"outgoing" \| "incoming"` | Direction this node was reached |

Each entry in `edges` has the shape:

| Field | Type | Description |
|---|---|---|
| `source` | `str` | `"file:name"` of the calling/dependent definition |
| `target` | `str` | `"file:name"` of the called/dependency definition |
| `hop` | `int` | Hop level at which this edge was discovered |

**Responsibility**
Performs a BFS traversal of the definition dependency graph within `project_data`, discovering definitions that are reachable from a named start node within a bounded number of hops, in either or both dependency directions.

**When to use**
Call this to understand the dependency neighborhood of a specific definition — for example, to find what a function calls (outgoing), what calls it (incoming), or both, up to a specified depth.

**Design decisions**
- **Exact-match-then-partial-match fallback**: searches for `name` as an exact match first; only if no results are found does it fall back to case-insensitive substring matching. The first candidate found is used as the start node.
- **BFS boundary**: nodes at depth equal to `hops` are recorded but not expanded further, so the graph never exceeds the requested hop depth.
- **Outgoing edge scoping**: for outgoing direction, `callee_usages` entries are filtered to only those whose line numbers fall within the current definition's declared line range, attributing usages to the correct definition rather than the file as a whole. Module-level usages (lines not within any named definition) are attributed to a synthetic `__module__` node.
- **Incoming edge attribution**: for incoming direction, `caller_usages` entries are resolved to the specific named definition in the source file whose line range contains the usage line, defaulting to `__module__` when no enclosing definition is found.
- **Deduplication**: both nodes (via a `visited` set keyed on `"file:name"`) and edges (via a `seen_edges` set keyed on `(source, target, direction)`) are deduplicated across the traversal.
- A file-keyed index is built once at the start to avoid repeated linear scans during traversal.

**Constraints & edge cases**
- Returns `{"error": ...}` if `project_data` is `None` or if `name` cannot be matched at all.
- `direction` must be one of `"outgoing"`, `"incoming"`, or `"both"`; no validation is performed and an unrecognized value will silently produce no results.
- When multiple definitions match `name`, only the first candidate (as encountered in `project_data["files"]` order) is used as the start node.
- Definitions referenced in usages but absent from `file_index` are still recorded as nodes, but their `type` field will be an empty string and they will not be expanded.
- `hops=1` means only direct neighbors of the start node are discovered; the start node itself does not appear in `nodes`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module imports are present in this file. `qa_tools.py` depends solely on standard library modules (`os`, `collections.deque`). All project knowledge data is injected externally via the module-level variables `project_data` and `base_dir` rather than through import statements.

## Dependents (modules that import this file)

- `examples/rlm_qa/rlm_qa_agent.py` → `codetwine/examples/rlm_qa/qa_tools_py/qa_tools.py` : The agent module imports this module and uses it in the following ways:
  - Writes to `qa_tools.project_data` and `qa_tools.base_dir` to initialize the module's shared state by loading data from `project_knowledge.json`
  - Passes `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` as tool functions to a `dspy.RLM` instance, making them available to the language model agent at runtime

## Dependency Direction

- The relationship between `qa_tools.py` and `rlm_qa_agent.py` is **unidirectional**: `rlm_qa_agent.py` depends on `qa_tools.py`. `qa_tools.py` does not import or reference `rlm_qa_agent.py` in any way. State is injected into `qa_tools.py` from the outside by directly assigning to its module-level variables, but this does not constitute a reverse dependency from `qa_tools.py` back to `rlm_qa_agent.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `project_data` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.project_data = json.load(f)` | Dict parsed from `project_knowledge.json` |
| `base_dir` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.base_dir = os.path.dirname(json_path)` | String (directory path) |
| `path` | Argument to `read_source_file()` | String file path as listed in the JSON `file` field |
| `target_file` | Argument to `get_files_using()` | String (partial file path for matching) |
| `name` | Argument to `graph_search()` | String (definition name, exact or partial) |
| `hops` | Argument to `graph_search()` | Integer (default: 1) |
| `direction` | Argument to `graph_search()` | String: `"outgoing"`, `"incoming"`, or `"both"` (default: `"both"`) |

---

## 2. Transformation Overview

### `read_source_file(path)`

```
path (string)
  → strip leading "project_name/" prefix if present
  → join with base_dir to form absolute path
  → read file from filesystem
  → return file content as string (or error message string on failure)
```

### `get_files_using(target_file)`

```
project_data["files"] (list of file entries)
  → iterate all file entries
  → for each file entry, iterate callee_usages
  → filter: keep usages where target_file is a substring of usage["from"]
  → collect matching {"file": ..., "usage": ...} pairs
  → return flat list of matches
```

### `graph_search(name, hops, direction)`

```
project_data["files"]
  → build file_index: {file_path → file_entry} for O(1) lookup

  → candidate search:
      exact match on definition["name"] == name
      fallback: partial match (name.lower() in definition["name"].lower())
  → select first candidate as start node; form start_key = "file:name"

  → BFS loop (queue initialized with start node at hop 0):
      for each dequeued node (key, file, name, hop):
        skip if hop >= hops limit

        if direction includes "outgoing":
          → scan callee_usages of current file
          → filter usages whose line numbers fall within current definition's line range
          → for each matching usage: form target_key = "from_file:usage_name"
          → look up target definition type in file_index
          → record edge {source, target, hop}
          → if target_key not yet visited: add to nodes list, enqueue

        if direction includes "incoming":
          → scan caller_usages of current file
          → filter entries where usage["name"] == current_name
          → for each match: identify which definition in source file contains the usage lines
            → if no enclosing definition found: assign source_name = "__module__"
          → form source_key = "source_file:source_name"
          → record edge {source, target, hop}
          → if source_key not yet visited: add to nodes list, enqueue

  → return result dict with start key, nodes list, edges list
```

---

## 3. Outputs

| Output | From | Format |
|---|---|---|
| File content string | `read_source_file()` return value | Plain string; error message string on failure |
| List of dependent file/usage pairs | `get_files_using()` return value | `list[{"file": str, "usage": dict}]` |
| BFS graph result | `graph_search()` return value | Dict with `"start"`, `"hops"`, `"direction"`, `"nodes"`, `"edges"` keys |
| Error dict | `graph_search()` on failure | `{"error": str}` |

No file writes or other side effects occur in any function. All state mutation is limited to the module-level variables `project_data` and `base_dir`, which are written externally by `rlm_qa_agent.py`.

---

## 4. Key Data Structures

### `get_files_using()` — result list element

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Path of the file that contains the usage |
| `usage` | `dict` | The raw `callee_usages` entry from `project_data` for the matched usage |

---

### `graph_search()` — return dict

| Field / Key | Type | Purpose |
|---|---|---|
| `start` | `str` | Start node key in `"file:name"` format |
| `hops` | `int` | The hop limit used for the search |
| `direction` | `str` | The direction used: `"outgoing"`, `"incoming"`, or `"both"` |
| `nodes` | `list[dict]` | All definition nodes discovered by BFS (excluding the start node) |
| `edges` | `list[dict]` | All directed edges discovered during BFS |

---

### `nodes` list element

| Field / Key | Type | Purpose |
|---|---|---|
| `key` | `str` | Unique node identifier in `"file:name"` format |
| `file` | `str` | Source file path containing this definition |
| `name` | `str` | Definition name |
| `type` | `str` | Definition type (e.g., function, class); empty string if not found |
| `hop` | `int` | BFS hop distance from the start node |
| `via` | `str` | Direction of traversal that discovered this node: `"outgoing"` or `"incoming"` |

---

### `edges` list element

| Field / Key | Type | Purpose |
|---|---|---|
| `source` | `str` | Source node key in `"file:name"` format |
| `target` | `str` | Target node key in `"file:name"` format |
| `hop` | `int` | BFS hop at which this edge was discovered |

---

### Internal `file_index`

| Field / Key | Type | Purpose |
|---|---|---|
| `key` (file path) | `str` | Full file path string from `project_data["files"]` |
| `value` (file entry) | `dict` | The corresponding file entry dict from `project_data["files"]` |

Used internally in `graph_search()` to avoid repeated linear scans of `project_data["files"]` during BFS traversal.

## Error Handling

# Error Handling

## 1. Overall Strategy

This module follows a **graceful degradation** approach. Rather than raising exceptions or terminating the process, errors are surfaced as structured return values — either error-message strings or dictionaries containing an `"error"` key — that callers (including the LLM agent in `rlm_qa_agent.py`) can inspect and act upon. Initialization-state violations are checked eagerly at the entry point of each function, but file I/O and lookup failures are absorbed and reported inline without propagating exceptions.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Uninitialized `base_dir` | `read_source_file` is called before `load_project()` sets `base_dir` | Returns a descriptive error string immediately | No | The file read is skipped entirely; the caller receives an error string instead of file content |
| Uninitialized `project_data` | `graph_search` is called before `load_project()` sets `project_data` | Returns a dict with an `"error"` key immediately | No | The search is aborted; the caller receives an error dict instead of graph results |
| File I/O failure | The file at the resolved path cannot be opened or read (e.g., missing file, permission error) | Exception is caught; a formatted error string including the exception message is returned | No | The file read is skipped; the caller receives an error string instead of file content |
| Definition not found (exact match) | No definition in `project_data` has a name exactly matching the `name` argument to `graph_search` | Falls back to case-insensitive partial-match search across all definitions | Yes (via fallback) | Search continues with partial-match candidates; no error is raised unless partial match also fails |
| Definition not found (partial match) | Neither exact nor partial-match search yields any candidate definition | Returns a dict with an `"error"` key | No | The graph search is aborted; the caller receives an error dict |
| Missing file entry in index during BFS | A file referenced by a dependency edge is not present in `file_index` | The BFS iteration for that node is silently skipped (`continue`) | Yes (node skipped) | Only the unreachable node is omitted; BFS continues for remaining nodes |

---

## 3. Design Notes

- **Return-value signaling over exceptions.** All error conditions communicate failure through the return value rather than by raising. This is consistent with the module's role as a tool library consumed by an LLM agent (`dspy.RLM`), where a raised exception would interrupt the agent loop rather than allowing it to reason about and recover from the failure.
- **Two distinct error formats.** `read_source_file` returns a plain string on error (matching its normal `str` return type), while `graph_search` returns a dict with an `"error"` key (matching its normal `dict` return type). Each function preserves its declared return type even in the failure path.
- **Exact-then-partial fallback in `graph_search`.** The two-stage lookup treats exact matching as the preferred, unambiguous path and partial matching as a best-effort recovery, ensuring the function remains useful when the caller supplies an abbreviated or case-varied name.
- **Silent skip for missing BFS nodes.** Absent file entries encountered mid-traversal are silently ignored rather than reported. This keeps the BFS result self-consistent (no partial error entries mixed into the `nodes` list) at the cost of not surfacing missing-file conditions to the caller.

## Summary

**qa_tools.py** provides stateless tool functions for reading source files and traversing a dependency graph loaded from `project_knowledge.json`.

Module-level globals `project_data: dict` and `base_dir: str` must be set externally before use.

Public functions:
- `read_source_file(path: str) -> str`
- `get_files_using(target_file: str) -> list[{"file": str, "usage": dict}]`
- `graph_search(name: str, hops: int, direction: str) -> dict` — returns `{"start", "hops", "direction", "nodes": list[dict], "edges": list[dict]}`
