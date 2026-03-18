# Design Document: examples/rlm_qa/qa_tools.py

## Overview & Purpose

# Overview & Purpose

## Role and Purpose

`qa_tools.py` provides the core tool functions used during question-answering sessions over a loaded project knowledge base. It exists as a dedicated module to centralize all data-access and graph-traversal operations that operate on the in-memory project knowledge JSON (`project_data`) and the on-disk source file tree (`base_dir`). By isolating these utilities in one file, consuming code (e.g. a QA agent or REPL) can import exactly the tools it needs without duplicating file-reading or dependency-graph logic.

The module relies on two module-level variables (`project_data`, `base_dir`) that are expected to be populated by an external `load_project()` call before any tool function is invoked. This shared-state pattern means all three functions implicitly share the same project context without requiring it to be passed as an argument on every call.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `read_source_file` | `path: str` | `str` (file content or error message) | Reads and returns the raw text of a source file from disk, stripping the leading project-name prefix from the path before resolving it under `base_dir`. |
| `get_files_using` | `target_file: str` | `list[dict]` — `[{"file": str, "usage": dict}, ...]` | Scans `callee_usages` across all files and returns every file that has a usage whose `from` field partially matches `target_file`. |
| `graph_search` | `name: str`, `hops: int = 1`, `direction: str = "both"` | `dict` — start key, matched results list, and edges list | Performs a BFS over the definition-dependency graph starting from a named definition, collecting reachable nodes and traversed edges within the specified hop count and direction. |

---

## Design Decisions

- **Implicit shared state via module-level variables.** `project_data` and `base_dir` are module globals rather than parameters, so tool functions stay signature-simple and naturally form a stateful context once initialized externally.
- **Graceful degradation on missing state.** Each function checks for uninitialized globals and returns an error string or error-keyed dict rather than raising an exception, making failures observable without crashing a QA loop.
- **Exact-then-partial match fallback in `graph_search`.** The BFS start-node lookup first tries exact name matching; only if no match is found does it fall back to case-insensitive substring matching, minimizing accidental broad matches while still being user-friendly.
- **BFS with a seen-edge set.** `graph_search` tracks both visited nodes and seen edges separately, allowing the returned `edges` list to represent the full traversal structure without duplicates, independent of node deduplication.
- **Line-range containment for outgoing edges.** Rather than treating all `callee_usages` of a file as belonging to a single definition, `graph_search` filters usages by checking whether their line numbers fall within the current definition's `start_line`–`end_line` range, attributing each call site to the correct enclosing definition.

## Definition Design Specifications

# Definition Design Specifications

---

## `read_source_file(path: str) -> str`

**Responsibility:** Resolves a file path recorded in the JSON knowledge data to an absolute path on disk and returns the file's text content, providing a uniform access point for source file reads across all tool functions.

**Arguments:**
- `path` (`str`): A file path as it appears in the `"file"` field of `project_knowledge.json` entries (e.g., `"myproject/module/file.py"`). May include a leading `project_name/` prefix.

**Return value:** (`str`) The full UTF-8 text content of the file, or an error message string beginning with `"Error:"` if `base_dir` is uninitialized or if the file cannot be opened.

**Design decisions:**
- Returns an error string rather than raising an exception, keeping the interface uniform for LLM tool-call consumers that expect a string response in all cases.
- Strips the leading `project_name/` prefix before joining with `base_dir`, because the JSON field includes the project name as a path component while the physical file tree rooted at `base_dir` does not.

**Edge cases and constraints:**
- Requires `base_dir` and `project_data` module variables to be initialized by `load_project()` before calling; returns an error string immediately if `base_dir` is `None`.
- Prefix stripping only occurs when `project_name` is non-empty and the path literally starts with `project_name + "/"`.

---

## `get_files_using(target_file: str) -> list`

**Responsibility:** Answers the question "which files depend on this file?" by scanning the `callee_usages` of every file in the project and collecting entries whose origin matches the target.

**Arguments:**
- `target_file` (`str`): A partial or full file path string to match against the `"from"` field of each `callee_usages` entry (substring match).

**Return value:** (`list`) A list of dicts, each with:
- `"file"` (`str`): The path of the file that contains the dependency.
- `"usage"` (`dict`): The raw `callee_usages` entry that matched.

Returns an empty list if no dependents are found.

**Design decisions:**
- Uses substring (`in`) matching rather than exact path equality so callers can pass a short identifying fragment without knowing the full canonical path.
- Exposes the raw usage dict rather than a simplified summary, giving callers access to all usage metadata (name, lines, etc.) without an additional lookup.

**Edge cases and constraints:**
- Relies on `project_data` being initialized; no guard is present, so calling before `load_project()` will raise `TypeError`.
- A very short or generic `target_file` string may produce false-positive matches across unrelated files.

---

## `graph_search(name: str, hops: int = 1, direction: str = "both") -> dict`

**Responsibility:** Performs a breadth-first traversal of the definition dependency graph starting from a named definition, enabling callers to discover transitive dependencies and/or dependents up to a specified depth.

**Arguments:**
- `name` (`str`): The definition name to use as the BFS starting node. Exact match is attempted first; partial case-insensitive match is used as a fallback.
- `hops` (`int`, default `1`): Maximum traversal depth. A value of `1` returns only direct neighbors; `2` includes neighbors of neighbors, and so on.
- `direction` (`str`, default `"both"`): Controls which edges are followed. `"outgoing"` follows dependencies (things this definition calls/uses), `"incoming"` follows dependents (things that call/use this definition), and `"both"` follows both.

**Return value:** (`dict`) with keys:
- `"start"` (`str`): The starting node key in `"file:name"` format.
- `"hops"` (`int`): The `hops` argument as provided.
- `"direction"` (`str`): The `direction` argument as provided.
- `"results"` (`list[dict]`): Each discovered node, with fields `key`, `file`, `name`, `type`, `hop` (the hop depth at which it was first reached), and `via` (`"outgoing"` or `"incoming"`).
- `"edges"` (`list[dict]`): Each traversed edge, with fields `source`, `target`, and `hop`.
- `"error"` (`str`): Present instead of the above keys if `project_data` is not loaded or the starting definition cannot be found.

**Design decisions:**
- Nodes are keyed as `"file:name"` composites rather than name alone, because the same definition name can exist in multiple files; this composite key keeps nodes globally unique across the project.
- Outgoing edges are scoped to the line range of the current definition: a `callee_usages` entry is only attributed to a definition if at least one of its usage line numbers falls within that definition's `start_line`–`end_line` range. For a special `"__module__"` pseudo-definition, lines that fall outside all declared definitions are attributed to it.
- Deduplication of both nodes (`visited` set) and edges (`seen_edges` set) prevents infinite loops in cyclic graphs and avoids redundant output.
- When `direction="incoming"`, the traversal identifies which definition inside the caller file is responsible for the usage by finding the definition whose line range contains the caller's reported usage lines, defaulting to `"__module__"` if none match.
- The first exact match found is used as the start node when multiple files define the same name; no error or warning is issued for ambiguous names.

**Edge cases and constraints:**
- Returns `{"error": ...}` immediately if `project_data` is `None` or if no definition matching `name` can be found by either exact or partial search.
- `hops=0` causes the BFS loop body to skip immediately for all dequeued items, producing empty `results` and `edges`.
- Partial-match fallback may yield an unintended starting node if the name fragment matches multiple unrelated definitions; only the first match in file iteration order is used.
- `target_type` for outgoing edges is resolved from the file index at traversal time; if the target file is absent from the index, `target_type` defaults to an empty string without error.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports (`os`, `collections.deque`) are standard library modules. The module operates entirely on data passed in through the module-level variables `project_data` and `base_dir`, which are set externally by a `load_project()` function defined outside this file.

### Dependents (what uses this file)

No dependent information available.

## Data Flow

# Data Flow

## Module-Level State

Two module variables act as shared state, initialized externally via `load_project()`:

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `dict` | Entire parsed project knowledge JSON |
| `base_dir` | `str` | Filesystem root for resolving source file paths |

All three tool functions read directly from these module globals.

---

## `read_source_file(path)`

```
Input:  path (str) — relative file path from JSON "file" field
          │
          ▼
Strip leading "project_name/" prefix if present
          │
          ▼
Join with base_dir → absolute filesystem path
          │
          ▼
Output: raw file content (str), or error message (str) on failure
```

---

## `get_files_using(target_file)`

```
Input:  target_file (str) — partial path string to match against

project_data["files"]
    └── each file_entry
            └── file_dependencies.callee_usages[]
                    └── usage["from"] contains target_file?
                                │ yes
                                ▼
                        collect {"file": file_entry["file"], "usage": usage}

Output: list of {"file": str, "usage": dict}
```

**Output record structure:**

| Field | Type | Content |
|---|---|---|
| `file` | `str` | Path of the file that has the dependency |
| `usage` | `dict` | Raw `callee_usages` entry (includes `name`, `from`, `lines`) |

---

## `graph_search(name, hops, direction)`

### Input Resolution

```
name (str) ──► exact match in definitions → fallback to partial (case-insensitive)
                        │
                        ▼
              start_key = "file_path:def_name"   (first match used)
```

### BFS Traversal

```
queue: deque of (current_key, current_file, current_name, current_hop)
visited: set of keys already enqueued

While queue not empty AND current_hop < hops:
    │
    ├─ direction "outgoing" / "both":
    │       Scan callee_usages of current file
    │       Filter: usage lines fall within current definition's line range
    │       → target_key = "from_file:usage_name"
    │       → Look up target type from file_index
    │       → Append edge + result, enqueue target
    │
    └─ direction "incoming" / "both":
            Scan caller_usages of current file where name == current_name
            Identify which definition in source_file contains caller lines
            → source_key = "source_file:def_name" (default: "__module__")
            → Append edge + result, enqueue source
```

### Internal Data Structures

**`file_index`** — built once per call for O(1) lookup:
```
{ "file_path": file_entry_dict, ... }
```

**`results` entry:**

| Field | Type | Content |
|---|---|---|
| `key` | `str` | `"file_path:def_name"` — unique node identifier |
| `file` | `str` | Source file path |
| `name` | `str` | Definition name |
| `type` | `str` | Definition type (from JSON, may be empty) |
| `hop` | `int` | Distance from start node |
| `via` | `str` | `"outgoing"` or `"incoming"` |

**`edges` entry:**

| Field | Type | Content |
|---|---|---|
| `source` | `str` | Source node key |
| `target` | `str` | Target node key |
| `hop` | `int` | Hop number at which edge was discovered |

**`seen_edges`** — set of `(source_key, target_key, direction)` tuples preventing duplicate edges.

### Output

```
{
  "start":     str,    — starting node key
  "hops":      int,    — requested hop depth
  "direction": str,    — requested direction
  "results":   [...],  — list of reachable definition nodes
  "edges":     [...]   — list of traversed edges
}
```

## Error Handling

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** approach. Rather than raising exceptions and halting execution, functions return descriptive error values (strings or dicts) to the caller. This design allows interactive or agent-driven callers to inspect and react to failures without requiring try/except wrappers at the call site.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Uninitialized module state (`base_dir` or `project_data` is `None`) | Returns an error string or `{"error": "..."}` dict immediately | Prevents further execution within the function; caller receives a descriptive message |
| File read failure (e.g., file not found, permission denied) | Exception caught internally; returns an error string containing the path and exception message | Function returns a string instead of file content; no exception propagates |
| Definition not found by exact match | Falls back to case-insensitive partial match search before returning an error | Reduces hard failures for minor naming variations; only errors if both strategies yield nothing |
| Definition not found after partial match fallback | Returns `{"error": "..."}` dict | Caller receives a structured error; no exception raised |
| Missing or absent keys in JSON data | Handled via `.get()` with empty defaults (`{}`, `[]`, `""`) throughout traversal | Silently skips malformed or incomplete entries rather than raising `KeyError` |

---

## Design Considerations

The consistent use of return-value-based error signaling (strings for `read_source_file`, dicts for `graph_search`) means callers must inspect the return type or content to detect failures — there is no unified error contract across functions. The reliance on `.get()` defaults for JSON traversal prioritizes robustness over strictness, accepting that incomplete data entries will be silently ignored rather than surfaced as explicit errors.

## Summary

`qa_tools.py` provides three tool functions for querying a project knowledge base: `read_source_file` reads source files from disk by resolving JSON-recorded paths under `base_dir`; `get_files_using` returns all files that depend on a target file via substring-matching callee usages; `graph_search` performs BFS over the definition-dependency graph, returning reachable nodes and traversed edges within a specified hop count and direction. All functions share implicit state through module-level `project_data` and `base_dir` variables initialized externally. Errors are returned as strings or dicts rather than raised as exceptions.
