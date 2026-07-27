# Design Document: examples/rlm_qa/qa_tools.py

# Overview & Purpose

`qa_tools.py` provides the tool functions and shared state used by the RLM-based Q&A agent (`rlm_qa_agent.py`) to answer questions about a codebase whose structure has been pre-extracted into a `project_knowledge.json` file. It exists as a separate module so that these tools can be passed directly as callables into `dspy.RLM(tools=[...])`, decoupled from the agent's orchestration logic (LM setup, interpreter creation, CLI loop, etc. in `rlm_qa_agent.py`).

The module holds two pieces of module-level state, populated externally by the agent's `load_project()` function before any tool is invoked:
- `project_data`: the parsed contents of `project_knowledge.json` (project metadata, per-file dependency/definition graphs).
- `base_dir`: the directory containing the source files referenced by `project_data`, used to resolve relative file paths.

Given this shared knowledge base, the module exposes read/query utilities that let the agent (and the LLM driving it) inspect source code and reason about static dependency relationships (who calls whom, what a definition depends on, etc.) without re-parsing the codebase.

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `project_data` (module variable) | — | `dict` or `None` | Holds the full parsed `project_knowledge.json`; set by `load_project()` in the agent and read by all tool functions. |
| `base_dir` (module variable) | — | `str` or `None` | Holds the base directory of source files; set by `load_project()` and used by `read_source_file` to resolve paths. |
| `read_source_file(path: str)` | `path`: file path as listed in the JSON `file` field | `str` — file content, or an error message string on failure | Reads and returns the contents of a source file copied into the output directory, stripping the project name prefix and resolving it against `base_dir`. |
| `get_files_using(target_file: str)` | `target_file`: file path substring to search for | `list` of `{"file": str, "usage": dict}` | Finds all files whose `callee_usages` entries reference `target_file` (partial match on the `from` field), i.e. lists dependents of a given file. |
| `graph_search(name: str, hops: int = 1, direction: str = "both")` | `name`: definition name (exact match, falls back to partial match); `hops`: BFS depth; `direction`: `"outgoing"`, `"incoming"`, or `"both"` | `dict` with `start`, `hops`, `direction`, `nodes` (list of definition nodes with `key`, `file`, `name`, `type`, `hop`, `via`), and `edges` (list of `source`/`target`/`hop`) | Performs a breadth-first search over the project's dependency graph, treating definitions as nodes and `callee_usages`/`caller_usages` as edges, to surface dependencies and/or dependents of a given definition within N hops. |

### Design Notes

- **Module-level shared state instead of a class**: `project_data` and `base_dir` are plain module globals rather than encapsulated in a class instance. This design allows the functions to be passed directly as plain callables to `dspy.RLM(tools=[...])` (which likely expects simple function references with introspectable signatures/docstrings for tool use), while initialization is deferred to an external `load_project()` call performed by the agent before tools are invoked.
- **Docstring-driven tool usage**: Each function's docstring includes an explicit `Usage` example, indicating these functions are designed to be discoverable and self-documenting for an LLM-driven tool-calling agent (RLM), not just for human maintainers.
- **Graceful degradation over exceptions**: `read_source_file` catches file read errors and returns a descriptive error string rather than raising, and `graph_search` returns an `{"error": ...}` dict when `project_data` is unset or a definition is not found — consistent with an interface meant to be consumed by an LLM agent that needs textual feedback rather than exceptions.
- **BFS with hop-bounded, deduplicated traversal**: `graph_search` uses a `deque`-based BFS with `visited` and `seen_edges` sets to avoid revisiting nodes/edges, bounding traversal by the `hops` parameter and supporting independent or combined traversal of outgoing (`callee_usages`) and incoming (`caller_usages`) edges.
- **Name resolution strategy**: `graph_search` first attempts an exact name match for the start node and falls back to a case-insensitive substring match only if no exact match is found, balancing precision with usability for approximate queries.

# Definition Design Specifications

## Module-level state

**`project_data`** (module variable, initially `None`)
Holds the fully parsed contents of `project_knowledge.json` after `load_project()` (in `rlm_qa_agent.py`) assigns it. All tool functions in this module read from this shared state rather than receiving it as a parameter, so that tools registered with the RLM interpreter can be called with simple signatures.

**`base_dir`** (module variable, initially `None`)
Holds the directory used as the root for resolving relative source file paths passed to `read_source_file`. Set alongside `project_data` by the loader.

Both variables exist as module-level globals (instead of being passed explicitly) because the tool functions are exposed directly to an LLM-driven interpreter, which calls them with minimal, natural arguments; the loading step is expected to run once before any tool is invoked.

---

## `read_source_file(path: str) -> str`

- **Arguments**: `path` — a file path as recorded in the JSON project data's `file` field (may be prefixed with the project name).
- **Returns**: The full text content of the requested source file, or a descriptive error string if the file cannot be read.
- **Responsibility**: Provides raw source access so that an agent/tool consumer can retrieve code snippets referenced by other analysis data (e.g., definition line ranges) without needing to know the on-disk layout directly.
- **Design decisions**:
  - Errors are returned as strings rather than raised, since this function is intended to be called as a tool by an LLM agent, and a string error is easier for the caller to interpret in an automated dialogue than an exception.
  - The function strips a leading `project_name/` segment from `path` because paths stored in the JSON are prefixed with the project name, while `base_dir` already points at the corresponding local directory root.
- **Constraints/edge cases**:
  - Requires `base_dir` to have been initialized (via the external `load_project()`) before use; otherwise returns an explicit initialization error.
  - Assumes `project_data` is already loaded when computing `project_name`; behavior depends on `project_data.get("project_name", "")` returning an empty string if unset.
  - File reads use UTF-8 encoding; any read failure (missing file, permission, encoding issues) is caught and reported inline rather than propagated.

---

## `get_files_using(target_file: str) -> list`

- **Arguments**: `target_file` — a file path (or substring of one) to search for among dependency records.
- **Returns**: A list of dictionaries, each with `"file"` (the dependent file's path) and `"usage"` (the matching usage record dictionary), representing files that use something from `target_file`.
- **Responsibility**: Answers "who depends on this file" queries by scanning `callee_usages` entries across every file in the project data and matching against their `from` field.
- **Design decisions**: Uses substring (partial) matching on `from` rather than exact equality, allowing callers to search with a short or partial path (e.g., a module name) without needing the exact full path recorded in the data.
- **Constraints/edge cases**:
  - Assumes `project_data` is already loaded; it is not itself guarded with a `None` check (unlike `graph_search`), so calling it before loading will raise an error.
  - Relies on each file entry having a `file_dependencies` dict with an optional `callee_usages` list; entries lacking these keys are safely skipped via `.get()` defaults.

---

## `graph_search(name: str, hops: int = 1, direction: str = "both") -> dict`

- **Arguments**:
  - `name`: the definition name to start the search from. Matched exactly first; if no exact match exists, falls back to a case-insensitive substring match.
  - `hops`: maximum BFS depth to traverse (1 = only direct dependencies/dependents).
  - `direction`: one of `"outgoing"` (what the definition depends on), `"incoming"` (what depends on the definition), or `"both"`.
- **Returns**: A dictionary describing the search: the resolved `start` node key (`"file:name"`), the `hops` and `direction` used, a `nodes` list (each entry describing a discovered definition along with its file, type, hop distance, and traversal direction it was found via), and an `edges` list connecting node keys with the hop at which the edge was traversed.
- **Responsibility**: Builds an on-the-fly dependency graph rooted at a given definition, treating definitions as nodes and their usage relationships as edges, so that callers can explore how a function/class relates to others within a bounded neighborhood without materializing a full project-wide graph upfront.
- **Design decisions**:
  - Definitions are identified as nodes keyed by `"file:name"` strings (rather than numeric IDs) since this directly mirrors the identifying information already present in the JSON data and keeps node references human-readable in results.
  - Exact-match lookup is attempted before falling back to partial (substring, case-insensitive) match, so common/unique names resolve precisely while still tolerating imprecise queries.
  - Outgoing edges are derived from `callee_usages` filtered to those whose line numbers fall within the current definition's `start_line`/`end_line` range, associating each usage with the specific definition that contains it (rather than attributing all file-level usages to every definition in that file).
  - A special `"__module__"` pseudo-definition name represents module-level code (usages not falling within any known definition's line range), allowing module-level dependencies/dependents to be represented as graph nodes even though they aren't in the `definitions` list.
  - Incoming edges are derived from `caller_usages`, with the calling definition determined by locating which definition's line range contains the usage's line numbers in the source file; if none matches, the source is attributed to `"__module__"`.
  - Edges and visited nodes are deduplicated (`seen_edges`, `visited`) to avoid duplicate BFS expansion and redundant edges when multiple usages point to the same source/target pair.
- **Constraints/edge cases**:
  - Returns an `{"error": ...}` dictionary if `project_data` has not been loaded, or if no definition (exact or partial) matches `name` — these are the only two documented failure modes.
  - When multiple candidate definitions match (exact or partial), only the first candidate found is used as the search's starting point; other matches are silently ignored.
  - BFS depth is bounded by `hops`; nodes reached exactly at the hop limit are recorded but not expanded further (their queue entries are dequeued and skipped without generating further edges).
  - Assumes usage entries (`callee_usages`, `caller_usages`) include a `lines` list used to correlate them with specific definitions; usages without matching line coverage in any definition and not otherwise classified as module-level are excluded from outgoing traversal.

# Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. It only relies on standard library modules (`os`, `collections.deque`) to implement file path handling and BFS traversal logic. All data it operates on (`project_data`) is expected to be injected externally at runtime rather than imported from another project module.

### Dependents (what uses this file)

`examples/rlm_qa/rlm_qa_agent.py` depends on this file in the following ways:

- It sets `qa_tools.project_data` by loading project knowledge data from a JSON file, providing the dataset that this file's functions operate on.
- It sets `qa_tools.base_dir` to the directory of the loaded JSON file, which this file uses to resolve source file paths for reading.
- It uses `qa_tools.read_source_file` to retrieve the content of source files referenced in the project data.
- It uses `qa_tools.get_files_using` to find files that depend on a given target file.
- It uses `qa_tools.graph_search` to perform dependency graph searches starting from a given definition name.

The dependency direction is unidirectional: `rlm_qa_agent.py` depends on `qa_tools.py` by configuring its module-level state (`project_data`, `base_dir`) and invoking its tool functions, while `qa_tools.py` does not reference or depend on `rlm_qa_agent.py` in any way.

# Data Flow

## Input Data

| Source | Format | Set By |
|---|---|---|
| `project_data` | dict parsed from `project_knowledge.json` (`{"project_name": str, "files": [{"file": str, "file_dependencies": {"definitions": [...], "callee_usages": [...], "caller_usages": [...]}}, ...]}`) | External caller (`rlm_qa_agent.py`) assigns `qa_tools.project_data` before use |
| `base_dir` | string path to directory containing source files | External caller assigns `qa_tools.base_dir` (derived from JSON file location) |
| Function arguments | `path` (file path string), `target_file` (partial path string), `name`/`hops`/`direction` (search parameters) | Passed by tool callers (e.g. an LLM agent invoking these functions as tools) |

Both module-level variables act as shared, externally-injected state that all three functions read from; none of the functions mutate them.

## Processing Flow Overview

```
project_knowledge.json ──(external loader)──▶ project_data (in-memory dict)
                                              base_dir (string)
                                                   │
        ┌──────────────────────────────────────────┼───────────────────────────────┐
        ▼                                          ▼                               ▼
read_source_file(path)                 get_files_using(target_file)      graph_search(name, hops, direction)
   │                                          │                                     │
   ▼                                          ▼                                     ▼
strip project_name prefix          scan every file's callee_usages     locate start definition (exact
join with base_dir                 filter by substring match on         then partial match on name)
open & read file                   'from' field                        BFS over definitions/usages up to
   │                                          │                          N hops, following:
   ▼                                          ▼                          - callee_usages (outgoing edges)
raw file text (str)               list of {file, usage} dict entries    - caller_usages (incoming edges)
                                                                          │
                                                                          ▼
                                                             nodes list + edges list describing
                                                             a dependency subgraph
```

- **read_source_file**: Resolves a JSON-listed path into an actual filesystem path (stripping the project name prefix), reads it, and returns raw source text or an error string.
- **get_files_using**: Performs a linear scan across all files' `callee_usages`, matching entries whose `from` field contains the target substring; aggregates matches with their owning file.
- **graph_search**: Builds an in-memory index of files by path, resolves a starting definition, then performs BFS. At each step it determines which usages fall within the current definition's line range (for outgoing edges) or which definition contains the usage's lines (for incoming edges), expanding the graph until the hop limit is reached, while deduplicating nodes/edges.

## Output Data

| Function | Output Format | Destination |
|---|---|---|
| `read_source_file` | `str` (file content or `"Error reading {path}: {e}"`) | Returned to caller (tool invocation, typically consumed by an LLM/agent) |
| `get_files_using` | `list[{"file": str, "usage": dict}]` | Returned to caller |
| `graph_search` | `dict` with `start`, `hops`, `direction`, `nodes`, `edges` (or `{"error": str}`) | Returned to caller |

## Key Data Structures

**`project_data["files"][i]`** (per-file entry)
| Field | Purpose |
|---|---|
| `file` | File path as recorded in JSON, used as dependency key |
| `file_dependencies.definitions` | List of `{name, type, start_line, end_line}` describing functions/classes in the file |
| `file_dependencies.callee_usages` | List of `{name, from, lines}` — symbols this file calls, and where |
| `file_dependencies.caller_usages` | List of `{name, file, lines}` — external callers using this file's definitions |

**`graph_search` node entry**
| Field | Purpose |
|---|---|
| `key` | Unique identifier `"file:name"` |
| `file`, `name`, `type` | Location and kind of the definition |
| `hop` | BFS distance from start node |
| `via` | Whether discovered through `"outgoing"` or `"incoming"` relation |

**`graph_search` edge entry**
| Field | Purpose |
|---|---|
| `source`, `target` | `"file:name"` keys of connected nodes |
| `hop` | Hop level at which edge was discovered |

**`get_files_using` result entry**
| Field | Purpose |
|---|---|
| `file` | File that contains the matching usage |
| `usage` | Raw usage dict (`name`, `from`, `lines`) matched against `target_file` |

# Error Handling

This module adopts a **graceful degradation** strategy overall, favoring the return of informative error values or empty results over raising exceptions. This design choice reflects the module's role as a set of tool functions invoked by an LLM agent (via `dspy.RLM`), where uncaught exceptions could disrupt the calling agent's flow. Only `read_source_file` performs explicit exception handling; the other functions rely on precondition checks or simply return empty/partial results when data is missing.

| Error Type | Handling | Impact |
|---|---|---|
| `base_dir` not initialized (`load_project()` not called) | `read_source_file` checks `base_dir is None` and returns an error string prefixed with `"Error:"` | Caller receives a descriptive string instead of a crash; no exception propagates |
| File read failure in `read_source_file` (missing file, permission issue, encoding issue, etc.) | Caught via broad `except Exception as e`, returns a formatted string `f"Error reading {path}: {e}"` | Failure is surfaced as a string return value rather than a raised exception, allowing the agent to continue |
| `project_data` not loaded (`load_project()` not called) in `graph_search` | Explicit `if project_data is None` check returns a dict `{"error": "..."}` | Caller gets a structured error result instead of an `AttributeError`/`TypeError` on `None` access |
| `project_data` not loaded in `get_files_using` | No explicit check; accessing `project_data["files"]` would raise an unhandled exception if `project_data` is `None` | Inconsistent with `graph_search`; this function is not protected against uninitialized state |
| Definition name not found in `graph_search` (no exact or partial match) | Returns `{"error": f"Definition '{name}' not found"}` after attempting exact match then partial (case-insensitive substring) match | Search terminates early with a clear error message instead of proceeding with empty candidates |
| Missing/absent optional fields (`file_dependencies`, `callee_usages`, `caller_usages`, `definitions`, `lines`, `from`, `name`, etc.) | Consistently accessed via `dict.get(key, default)` (e.g., `.get("file_dependencies", {})`, `.get("callee_usages", [])`) throughout `get_files_using` and `graph_search` | Missing fields silently degrade to empty collections/strings rather than raising `KeyError`, allowing iteration and matching logic to proceed without failure |
| File or definition lookups that yield no match (e.g., `file_index.get(...)`, definition type lookup) | Falls back to `None`/empty string defaults (`target_type = ""`, `source_name = "__module__"`) and skips further processing when the lookup is `None` | Partial or incomplete graph data is silently tolerated; traversal continues with default placeholder values instead of stopping |

### Design Considerations
- Error reporting is inconsistent in form: `read_source_file` returns error messages as plain strings, while `graph_search` returns a structured dict with an `"error"` key. `get_files_using` has no dedicated error reporting path at all.
- The heavy use of `.get()` with defaults throughout `get_files_using` and `graph_search` reflects a deliberate tolerance for incomplete or inconsistently structured JSON data (`project_data`), prioritizing continued traversal/search over strict validation.
- Initialization-state checks (`base_dir is None`, `project_data is None`) are only present in `read_source_file` and `graph_search`; `get_files_using` assumes `project_data` has already been set by `load_project()`, making its error handling less defensive than the other two functions.

# Summary

qa_tools.py supplies tool functions and shared state for the RLM Q&A agent. It holds module-level globals `project_data` and `base_dir` (set externally by the agent's `load_project()`), and exposes: `read_source_file(path)` to fetch source text; `get_files_using(target_file)` to find file dependents via `callee_usages`; and `graph_search(name, hops, direction)`, a BFS over definitions/usages returning nodes/edges. Design favors graceful degradation (error strings/dicts) over exceptions, enabling safe use as LLM-callable tools.
