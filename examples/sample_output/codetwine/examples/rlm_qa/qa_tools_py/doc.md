# Design Document: examples/rlm_qa/qa_tools.py

# Overview & Purpose

`qa_tools.py` provides the tool functions and shared state used by the RLM-based question-answering agent (`rlm_qa_agent.py`) to explore a previously analyzed codebase via its `project_knowledge.json` representation. It exists as a separate module so that these tools can be passed directly as callables into `dspy.RLM(tools=[...])` and so that the loaded project data/base directory can be held as module-level state (`project_data`, `base_dir`) accessible both to the tool functions and to the agent that populates them via `load_project()`-style initialization (seen in `rlm_qa_agent.py`, which sets `qa_tools.project_data` and `qa_tools.base_dir`).

The module's responsibility is narrowly scoped to read-only inspection of the analyzed project:
- Reading raw source file contents from disk.
- Querying which files/usages depend on a given file.
- Performing graph-based BFS traversal over definitions and their dependency/dependent relationships, as encoded in the project knowledge JSON.

### Public Interface

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `project_data` (module variable) | — | `dict` or `None` | Holds the entire parsed `project_knowledge.json`, set externally before tool use. |
| `base_dir` (module variable) | — | `str` or `None` | Holds the base directory for resolving source file paths, set externally before tool use. |
| `read_source_file(path: str)` | `path`: file path as listed in the JSON `file` field | `str` | Reads and returns the content of a source file (stripping the leading project name from the path), or an error message on failure. |
| `get_files_using(target_file: str)` | `target_file`: file path to search for (partial match) | `list[{"file": str, "usage": dict}]` | Finds files whose `callee_usages` entries reference `target_file`, returning the dependent files and matching usage records. |
| `graph_search(name: str, hops: int = 1, direction: str = "both")` | `name`: definition name (exact, falling back to partial match); `hops`: BFS depth; `direction`: `"outgoing"`, `"incoming"`, or `"both"` | `dict` with `start`, `hops`, `direction`, `nodes`, `edges` | Performs a BFS over the dependency graph of definitions (using `callee_usages`/`caller_usages` and line ranges) to collect dependency (outgoing) and/or dependent (incoming) definitions up to N hops from the starting definition. |

### Design Notes

- **Module-level shared state instead of a class**: `project_data` and `base_dir` are plain module globals rather than encapsulated in an object, allowing the RLM tool functions to be passed as simple, stateless-signature callables (required by the tool interface) while still sharing context set once by the caller (`rlm_qa_agent.py`).
- **Graceful degradation via string/dict error returns**: rather than raising exceptions, functions like `read_source_file` and `graph_search` return descriptive error strings/dicts (e.g., `"Error reading {path}: {e}"`, `{"error": ...}`), which is suited for consumption by an LLM-driven agent that inspects tool output as text.
- **Exact-then-partial match fallback**: `graph_search` first attempts an exact name match for the start node and falls back to a case-insensitive substring match, improving usability when exact definition names are unknown.
- **Graph reconstruction from flat usage records**: dependency/dependent edges are not stored explicitly as a graph but are derived on-the-fly from each file's `callee_usages`/`caller_usages` and definitions' line ranges, using line-range containment checks to attribute a usage to the enclosing definition (or to a synthetic `__module__` node when no enclosing definition is found).
- **BFS traversal with deduplication**: `graph_search` uses a `deque`-based BFS with `visited` node keys and a `seen_edges` set to avoid duplicate nodes/edges when multiple usages point to the same target.

# Definition Design Specifications

## `project_data`

Module-level variable holding the entire parsed `project_knowledge.json` document (a dict with `project_name` and `files` list). It is `None` until `load_project()` (in the caller module) assigns it, and all other functions in this module read from it as their sole data source. Centralizing state as a module-level variable allows the tool functions to be passed directly as callables (e.g., to `dspy.RLM`) without needing to carry an explicit context/session object.

## `base_dir`

Module-level variable storing the directory path from which relative source file paths (as recorded in `project_data`) should be resolved. Must be set by the caller (typically to the directory containing the loaded JSON file) before `read_source_file` is used; if left `None`, `read_source_file` reports an initialization error instead of failing with a low-level exception.

## `read_source_file`

Reads and returns the full text content of a source file referenced by a `file` field in `project_data`.

- `path` (str): a file path as it appears in the JSON `file` field, optionally prefixed with the project name (e.g. `"code_anarizer/extract_imports/extract_imports.py"`).
- Returns (str): the file's full text content, or a human-readable error string if the file cannot be read.

This function exists to let downstream consumers (e.g., an LLM-driven agent) fetch actual source code referenced by structural metadata in the project knowledge graph, since the JSON itself does not embed file contents.

Design notes:
- The function strips the leading `project_name/` segment from the input path before joining it with `base_dir`, since paths in the JSON are project-relative but files were copied into a separate `base_dir` without that prefix.
- Failures (missing file, encoding issues, permission errors, etc.) are caught and returned as a formatted string rather than raised, so that this function is safe to call directly as an agent tool without crashing the caller on bad input.
- Requires `base_dir` to have been initialized beforehand; otherwise returns an explicit error message instead of attempting a read.

## `get_files_using`

Finds all recorded usages across the entire project whose dependency `from` path contains the given `target_file` as a substring, effectively listing dependents of a file.

- `target_file` (str): a file path or path fragment to search for; matched via substring containment against each usage's `from` field.
- Returns (list): a list of `{"file": str, "usage": dict}` entries, where `file` is the path of the file that performs the usage and `usage` is the raw usage record (as found in `callee_usages`) pointing at `target_file`.

This function exists to answer "who depends on this file" queries by scanning `callee_usages` across all files, complementing the per-definition dependency data with a file-level reverse lookup.

Design notes / constraints:
- Matching is a simple substring check (`target_file in usage["from"]`), not exact path equality, so partial or ambiguous fragments can produce false positives across similarly named files or directories.
- Assumes `project_data` has already been loaded; it does not guard against `project_data` being `None` and will raise if called before `load_project()`.

## `graph_search`

Performs a breadth-first search over the project's definitions (functions/classes/etc. across all files) treating definitions as graph nodes and their usage relationships (`callee_usages` / `caller_usages`) as edges, returning the subgraph reachable within a given number of hops from a starting definition.

- `name` (str): the definition name to start from. First looked up via exact match against definition names across all files; if no exact match exists, falls back to a case-insensitive substring match.
- `hops` (int): maximum BFS depth to traverse (1 = only direct neighbors of the start node).
- `direction` (str): one of `"outgoing"` (follow what the current definition calls/uses), `"incoming"` (follow what calls/uses the current definition), or `"both"`.
- Returns (dict): `{"start": "file:name", "hops": int, "direction": str, "nodes": [...], "edges": [...]}` where each node is `{"key", "file", "name", "type", "hop", "via"}` and each edge is `{"source", "target", "hop"}`, using `"file:name"` as the node key format.

This function exists to let an agent explore call/usage relationships transitively (beyond the single-hop views provided by `get_files_using`), reconstructing a local dependency graph around a definition of interest for reasoning about impact or context.

Design notes:
- Node identity is the composite key `"file:name"` rather than name alone, since the same definition name can exist in multiple files; this avoids collisions when merging nodes from different files during BFS.
- For outgoing edges, a usage is only considered "inside" a definition if its recorded line numbers fall within that definition's `start_line`/`end_line` range; this is how the code attributes module-level usages (lines not inside any known definition) to a synthetic `"__module__"` node instead of dropping them.
- For incoming edges, the calling definition is identified by finding which definition in the source file contains the usage's line numbers; if no definition contains it, the caller is likewise attributed to `"__module__"`.
- Edges and visited nodes are deduplicated using `(source, target, direction)` identity, so the same relationship is not duplicated even if referenced by multiple usage records.
- If `name` matches no definition at all (neither exact nor partial), an `{"error": ...}` dict is returned instead of an empty graph, distinguishing "not found" from "found but no neighbors."
- Requires `project_data` to be loaded; returns an explicit error dict (rather than raising) if it is `None`.

# Dependency Description

**Dependencies (what this file uses)**

This file relies only on standard library modules (`os` and `collections.deque`) for file path handling and BFS queue management; it has no project-internal file dependencies.

**Dependents (what uses this file)**

`examples/rlm_qa/rlm_qa_agent.py` depends on this file. It sets `qa_tools.project_data` and `qa_tools.base_dir` after loading the project's JSON knowledge file, initializing the module-level state that the tool functions in this file rely on to operate. It also uses `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` as callable tools registered with an RLM instance, enabling the agent to read source file contents, find files that use a given file, and perform dependency graph searches over the project's definitions.

The dependency between the two files is unidirectional: `rlm_qa_agent.py` depends on `qa_tools.py` for its data state and tool functions, while `qa_tools.py` does not reference or depend on `rlm_qa_agent.py` in any way.

# Data Flow

## Input

| Source | Format | Set by |
|---|---|---|
| `project_data` | `dict` parsed from `project_knowledge.json` (must contain `project_name` and `files` list) | External caller (`rlm_qa_agent.py`) assigns directly to module-level variable before tool functions are invoked |
| `base_dir` | `str` — directory containing the source JSON (used as root for relative file paths) | External caller assigns directly to module-level variable |

Both variables are `None` until externally initialized (e.g., via a `load_project()`-style function in the dependent module). Each tool function checks for `None` and returns/handles an error state instead of proceeding.

## Core Data Structure: `project_data`

```
project_data = {
  "project_name": str,
  "files": [
    {
      "file": str,                     # source file path
      "file_dependencies": {
        "definitions":     [ {name, type, start_line, end_line}, ... ],
        "callee_usages":   [ {name, from, lines: [int, ...]}, ... ],
        "caller_usages":   [ {name, file, lines: [int, ...]}, ... ]
      }
    },
    ...
  ]
}
```

- **definitions**: functions/classes declared in a file, with their line ranges (used to map "which usage line belongs to which definition").
- **callee_usages**: references *made by* this file to definitions elsewhere (`from` = target file, `name` = target symbol).
- **caller_usages**: references *made to* this file's definitions by other files (`file` = source file, `lines` = call-site lines).

## Processing Flow per Function

### `read_source_file(path)`
```
path (JSON-listed path)
   → strip leading "project_name/" prefix
   → join with base_dir
   → open & read file
   → return file content (str) or error message (str)
```
Output: raw text content of a source file, or a formatted error string.

### `get_files_using(target_file)`
```
project_data.files
   → for each file, iterate callee_usages
   → filter usages where target_file is a substring of usage["from"]
   → collect {file, usage} pairs
```
Output: `list[dict]` — each item is `{"file": <consumer file path>, "usage": <matched callee_usage entry>}`. Represents dependents of `target_file`.

### `graph_search(name, hops, direction)`
```
Step 1: Locate start definition
   project_data.files.definitions → exact name match → fallback to case-insensitive substring match
   → start_key = "file:name"

Step 2: BFS traversal (queue-based, up to `hops` levels)
   For each dequeued node (file, definition, hop):
     - outgoing (direction ∈ {outgoing, both}):
         callee_usages of current file
           → filter usages whose lines fall within current definition's line range
           → resolve target definition's type by looking up target file's definitions
           → build edge (current → target) and new node
     - incoming (direction ∈ {incoming, both}):
         caller_usages of current file matching current definition's name
           → resolve source definition by locating which definition's line range contains the usage line
             (falls back to "__module__" if no enclosing definition found)
           → build edge (source → current) and new node

Step 3: Deduplicate nodes (visited set) and edges (seen_edges set) to avoid cycles/repeats
```

Output structure:
```
{
  "start": "file:name",
  "hops": int,
  "direction": str,
  "nodes": [ {key, file, name, type, hop, via}, ... ],
  "edges": [ {source, target, hop}, ... ]
}
```
Or `{"error": str}` if `project_data` is unset or the definition is not found.

## Output Destinations

All three functions return plain Python data (`str`, `list`, `dict`) directly to the caller — there is no file writing or external I/O beyond the read in `read_source_file`. The dependent module (`rlm_qa_agent.py`) registers these functions as callable tools for an LLM-driven agent (`dspy.RLM`), meaning their return values are ultimately consumed as tool outputs within that agent's reasoning loop.

# Error Handling

**Overall Strategy**

This module adopts a **graceful degradation** approach over fail-fast. Errors are captured and returned as part of the normal return value (strings or dict fields) rather than raised as exceptions, allowing the calling agent/LLM tool pipeline to continue operating without crashing. Uninitialized state and missing data are treated as recoverable conditions signaled to the caller through descriptive return values instead of exceptions.

**Error Patterns and Handling Policy**

| Error Type | Handling | Impact |
|---|---|---|
| `base_dir` not initialized (`load_project()` not called) | Returns an explicit error string instructing to call `load_project()` first, instead of raising | `read_source_file` returns a message string in place of file content; caller must detect this via string content |
| File read failure (missing file, permission, encoding issue) | Caught via broad `except Exception`, returns formatted error string including path and exception message | `read_source_file` silently returns an error string rather than propagating the exception, so downstream code must inspect the returned string |
| `project_data` not loaded (`None`) in `graph_search` | Explicit `None` check returns a dict with an `"error"` key | `graph_search` returns early with a structured error object instead of raising `AttributeError` |
| Definition `name` not found (`graph_search`) | Falls back from exact match to case-insensitive partial match; if still not found, returns a dict with an `"error"` key | Caller receives a clear indication that no matching definition exists rather than an empty/ambiguous result |
| Missing/absent nested fields in JSON structure (e.g., `file_dependencies`, `callee_usages`, `caller_usages`, `lines`) | Consistently accessed via `.get(...)` with default empty dict/list, avoiding `KeyError` | Absent fields are silently treated as empty collections, so traversal continues without interruption but may silently omit expected data |
| `get_files_using` with no matches | No explicit error handling; simply returns an empty list | Caller receives an empty list, indistinguishable from "no dependents found" vs. potential misuse (e.g., malformed `target_file`) |
| `project_data` not loaded in `get_files_using` | No `None` check is performed (unlike `graph_search`) | Will raise an unhandled exception (e.g., `TypeError`) if called before `load_project()`, differing in behavior from `graph_search` |

**Design Considerations**

- The module relies on module-level global state (`project_data`, `base_dir`) that must be set externally by `load_project()` in `rlm_qa_agent.py`; error handling for uninitialized state is only partially and inconsistently applied across functions (`read_source_file` and `graph_search` check for it, `get_files_using` does not).
- Returning errors as data (strings or dict fields) rather than exceptions appears intentional to keep these functions usable as tool-call targets in an LLM-driven interpreter/agent context, where uncaught exceptions could break the calling loop.
- Use of `.get()` with defaults throughout the dependency-traversal logic reflects a defensive stance toward variability/incompleteness in the JSON dependency graph structure, prioritizing continued traversal over strict validation.

# Summary

qa_tools.py supplies read-only tool functions and shared module state (`project_data`, `base_dir`) for an RLM QA agent to explore a project_knowledge.json codebase graph. Public API: `read_source_file(path)` reads source text; `get_files_using(target_file)` finds dependents via substring match on callee_usages; `graph_search(name, hops, direction)` BFS-traverses definitions/usages into a subgraph (nodes/edges keyed "file:name"). Errors return as strings/dicts, not exceptions. State is set externally by rlm_qa_agent.py; no internal dependencies beyond stdlib.
