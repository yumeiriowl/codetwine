# Design Document: examples/rlm_qa/qa_tools.py

# Overview & Purpose

## 1. Module Summary

Provides stateful tool functions for querying a loaded project knowledge graph, enabling an LLM agent to read source files, find file dependents, and traverse definition-level dependency relationships via BFS.

## 2. When to Use This Module

- **Reading source file content**: Call `read_source_file(path)` with a file path as recorded in `project_knowledge.json` to retrieve the raw text of that source file from the output directory.
- **Finding which files depend on a given file**: Call `get_files_using(target_file)` to retrieve all files that reference the specified file through their `callee_usages`, along with the specific usage entries.
- **Exploring the dependency graph around a definition**: Call `graph_search(name, hops, direction)` to perform a BFS traversal starting from a named definition and collect all reachable definitions and edges within the specified hop count and direction (`"outgoing"`, `"incoming"`, or `"both"`).
- **Initializing module state before any tool call**: The caller (e.g., `rlm_qa_agent.py`) must set the module-level variables `project_data` and `base_dir` before invoking any tool function, as all three functions depend on these variables being populated.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `project_data` | — | `dict \| None` | Module-level variable holding the entire parsed `project_knowledge.json`; must be set by the caller before use. |
| `base_dir` | — | `str \| None` | Module-level variable holding the base directory path for resolving source files; must be set by the caller before use. |
| `read_source_file` | `path: str` | `str` | Reads and returns the content of the source file at the given path, stripping a leading project name prefix if present; returns an error message string on failure. |
| `get_files_using` | `target_file: str` | `list[dict]` | Returns a list of `{"file": str, "usage": dict}` entries for all files whose `callee_usages` partially match the given file path. |
| `graph_search` | `name: str`, `hops: int`, `direction: str` | `dict` | Performs BFS over the definition dependency graph from the named definition, returning discovered nodes and edges up to the specified hop count and direction. |

## 4. Design Decisions

- **Module-level mutable state**: `project_data` and `base_dir` are intentionally exposed as module-level variables rather than passed as function arguments. This allows the tool functions to conform to a no-extra-argument signature required when registered as LLM agent tools (as seen in `rlm_qa_agent.py`), with initialization delegated to a separate `load_project()` call in the agent setup.
- **Exact-match with partial-match fallback in `graph_search`**: Definition lookup first attempts exact name matching; only if no results are found does it fall back to case-insensitive partial matching. This prioritizes precision while remaining usable when the exact name is unknown.
- **BFS edge deduplication via `seen_edges`**: Edges are tracked by a `(source, target, direction)` tuple to prevent duplicate edge entries even when multiple usage lines in the same file reference the same definition.

# Definition Design Specifications

---

## Module-Level Variables

| Variable | Type | Initial Value | Purpose |
|---|---|---|---|
| `project_data` | `dict \| None` | `None` | Holds the entire parsed `project_knowledge.json` content. Set externally by `rlm_qa_agent.load_project()`. |
| `base_dir` | `str \| None` | `None` | Holds the base directory path from which source files are resolved. Set externally by `rlm_qa_agent.load_project()`. |

**Constraint:** Both variables must be assigned before any tool function is called. They are not initialized by this module; the caller (`rlm_qa_agent.py`) is solely responsible for populating them.

---

## `read_source_file`

**Signature:**
```python
def read_source_file(path: str) -> str
```

- `path`: A file path string as it appears in the `"file"` field of `project_knowledge.json` (e.g., `"myproject/module/file.py"`).
- Returns the full text content of the file, or an error message string on failure.

**Responsibility:** Reads and returns the raw text of a source file from the output directory, enabling callers to inspect actual source code when JSON metadata alone is insufficient.

**When to use:** When a caller needs to retrieve the full or partial source text of a file whose path is known from `project_data`.

**Design decisions:**
- If `project_data` contains a `"project_name"` and the path starts with that name followed by `/`, the project-name prefix is stripped before joining with `base_dir`. This normalizes paths that include the project name as a leading component.
- Read failures return a formatted error string rather than raising an exception, keeping the interface uniform for LLM tool consumers that expect string output.

**Constraints & edge cases:**
- Returns an error string (not an exception) if `base_dir` is `None`.
- Returns an error string if the file cannot be opened (e.g., missing file, permission error).
- Path stripping only applies when `project_name` is non-empty and the path begins with exactly `project_name + "/"`.

---

## `get_files_using`

**Signature:**
```python
def get_files_using(target_file: str) -> list
```

- `target_file`: A partial or full file path to search for among dependency `"from"` fields.
- Returns a list of dicts, each with the shape `{"file": str, "usage": dict}`, where `"file"` is the path of the dependent file and `"usage"` is the raw `callee_usages` entry that matched.

**Responsibility:** Identifies all files in the project that declare a usage of something originating from `target_file`, providing a reverse-lookup from a file to its dependents.

**When to use:** When determining which files depend on a specific file, for impact analysis or tracing usage propagation.

**Design decisions:**
- Matching is performed as a substring check (`target_file in usage["from"]`), so both partial paths and full paths are accepted.
- Iterates over all files' `callee_usages`, not `caller_usages`, because `callee_usages` records the origin (`"from"`) of each used symbol.

**Constraints & edge cases:**
- Assumes `project_data` is already populated; no guard is applied (will raise `TypeError` if `project_data` is `None`).
- Partial path strings may produce false positives if a short search string matches multiple unrelated paths.
- Returns an empty list if no matches are found.

---

## `graph_search`

**Signature:**
```python
def graph_search(name: str, hops: int = 1, direction: str = "both") -> dict
```

- `name`: The exact (or partial) name of a definition to use as the BFS start node.
- `hops`: Maximum graph distance to traverse (default `1`). `1` means only direct neighbors are collected; `2` includes neighbors of neighbors, etc.
- `direction`: Controls which edge types are followed.
  - `"outgoing"` — follows symbols that the start definition uses (dependencies).
  - `"incoming"` — follows definitions that use the start definition (dependents).
  - `"both"` — follows both directions.
- Returns a dict with the following structure:

| Key | Type | Description |
|---|---|---|
| `"start"` | `str` | Start node key in `"file:name"` format |
| `"hops"` | `int` | The `hops` argument as provided |
| `"direction"` | `str` | The `direction` argument as provided |
| `"nodes"` | `list[dict]` | All discovered neighbor nodes (excluding the start node) |
| `"edges"` | `list[dict]` | All discovered edges between nodes |

Each entry in `"nodes"`:

| Field | Type | Description |
|---|---|---|
| `"key"` | `str` | `"file:name"` identifier |
| `"file"` | `str` | File path of the definition |
| `"name"` | `str` | Definition name |
| `"type"` | `str` | Definition type (e.g., `"function"`, `"class"`) |
| `"hop"` | `int` | Distance from the start node |
| `"via"` | `str` | `"outgoing"` or `"incoming"` |

Each entry in `"edges"`:

| Field | Type | Description |
|---|---|---|
| `"source"` | `str` | `"file:name"` of the source node |
| `"target"` | `str` | `"file:name"` of the target node |
| `"hop"` | `int` | Hop index at which this edge was discovered |

**Responsibility:** Performs a breadth-first traversal of the project's definition dependency graph starting from a named symbol, collecting reachable nodes and edges within a bounded number of hops.

**When to use:** When understanding the dependency neighborhood of a specific function, class, or other definition—either to find what it depends on, what depends on it, or both.

**Design decisions:**

- **Node key format `"file:name"`:** Combines file path and definition name into a single string to uniquely identify definitions across the project, since the same name may appear in multiple files.
- **`__module__` sentinel:** When an incoming usage cannot be attributed to any named definition in the source file (i.e., the usage line falls outside all known definition ranges), the source node is assigned the synthetic name `"__module__"`. Outgoing edges from the start node also use this sentinel when the start definition is `"__module__"` itself, in which case only lines not covered by any definition are considered in-range.
- **Exact-then-partial name matching:** The BFS start node is resolved by first attempting an exact name match across all definitions. Only if that yields no candidates does it fall back to a case-insensitive substring match. The first candidate found is used.
- **Visited set prevents cycles:** A `visited` set of node keys prevents the same definition from being enqueued more than once, ensuring termination on cyclic dependency graphs.
- **Duplicate edge suppression:** A `seen_edges` set keyed on `(source_key, target_key, direction_label)` prevents the same logical edge from appearing multiple times in the output.
- **Outgoing edge attribution:** A `callee_usages` entry is attributed to the current definition only if at least one of its usage line numbers falls within that definition's declared line range, ensuring edges represent actual in-body usage rather than module-level co-occurrence.

**Constraints & edge cases:**

- Returns `{"error": ...}` if `project_data` is `None` or if the named definition cannot be found even with partial matching.
- When multiple definitions share the same name, the first candidate encountered during file iteration is used as the start node; no disambiguation is performed.
- The start node itself is never included in the `"nodes"` list; only discovered neighbors are listed.
- Nodes at exactly `hops` distance are added to `"nodes"` but not further expanded (BFS expands nodes only when `current_hop < hops`).
- `type` for a neighbor node will be an empty string if the neighbor's file or definition cannot be found in the index.

# Dependency Description

## Dependencies (modules this file imports)

Based on the provided source code and dependency information, `examples/rlm_qa/qa_tools.py` has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `collections.deque`). No project-internal symbols are imported by this file.

---

## Dependents (modules that import this file)

`examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/qa_tools.py` : uses this module as the data layer and tool provider for a QA agent. Specifically:

- `rlm_qa_agent` → `qa_tools` : writes to `qa_tools.project_data` and `qa_tools.base_dir` to initialize shared module-level state (loading the project knowledge JSON and setting the base directory for source file resolution).
- `rlm_qa_agent` → `qa_tools` : registers `qa_tools.read_source_file`, `qa_tools.get_files_using`, and `qa_tools.graph_search` as callable tools passed into the RLM (reasoning language model) agent, enabling it to query source files and navigate the project dependency graph at runtime.

---

## Dependency Direction

| Relationship | Direction |
|---|---|
| `qa_tools` → (project-internal modules) | None — no project-internal imports |
| `rlm_qa_agent` → `qa_tools` | **Unidirectional**: `rlm_qa_agent` depends on `qa_tools`; `qa_tools` has no reference back to `rlm_qa_agent` |

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `project_data` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.project_data = json.load(f)` | Dict parsed from `project_knowledge.json` |
| `base_dir` | Module-level variable set externally by `rlm_qa_agent.py` via `qa_tools.base_dir = os.path.dirname(json_path)` | String (directory path) |
| `path` argument | Caller of `read_source_file()` | String file path as recorded in the JSON `"file"` field |
| `target_file` argument | Caller of `get_files_using()` | String (partial file path for matching) |
| `name`, `hops`, `direction` arguments | Caller of `graph_search()` | String, int, string |

The module itself does not load any configuration or files at import time. All shared state (`project_data`, `base_dir`) is injected by the external agent module before any tool function is called.

---

## 2. Transformation Overview

### `read_source_file(path)`

```
Input path string
  → Strip leading "project_name/" prefix if present
  → Join with base_dir to form full filesystem path
  → Read file content from disk
  → Return raw file content string (or error message string)
```

### `get_files_using(target_file)`

```
project_data["files"] (all file entries)
  → Iterate each file's callee_usages list
  → Filter usages where usage["from"] contains target_file (partial match)
  → Collect matching {file, usage} pairs into a flat list
  → Return list of result dicts
```

### `graph_search(name, hops, direction)`

```
project_data["files"]
  → Build file_index dict keyed by file path (fast lookup)
  → Search definitions for exact name match; fall back to case-insensitive partial match
  → Initialize BFS queue with start node (file:name, hop=0)

BFS loop (up to `hops` depth):
  For each dequeued node:
    → (if direction is "outgoing" or "both")
        Retrieve callee_usages from current file
        Filter usages whose line numbers fall within current definition's line range
        Each matching usage → target node (target_file:target_name)
        Add edge (source→target) and enqueue target if not yet visited

    → (if direction is "incoming" or "both")
        Retrieve caller_usages from current file
        Filter usages whose name matches current definition name
        Resolve which definition in the source file contains the usage line
        Each match → source node (source_file:source_name or __module__)
        Add edge (source→target) and enqueue source if not yet visited

  → Accumulate nodes and edges lists
→ Return result dict
```

Visited node tracking (`visited` set) and seen-edge tracking (`seen_edges` set) prevent duplicate processing. The BFS halts expansion (but does not enqueue further) once `current_hop >= hops`.

---

## 3. Outputs

| Function | Output | Format |
|---|---|---|
| `read_source_file()` | File content on success; error message on failure | Plain string |
| `get_files_using()` | List of files that depend on the target, with their specific usage entries | `list[dict]` — see structure below |
| `graph_search()` | Graph of definitions reachable within N hops, with nodes and edges | `dict` — see structure below |

Side effects: none. The module performs no file writes. `read_source_file()` performs a filesystem read, but this is transparent to the caller (result is returned as a string).

---

## 4. Key Data Structures

### Module-level shared state

| Variable | Type | Purpose |
|---|---|---|
| `project_data` | `dict` | Entire parsed `project_knowledge.json`; contains `"project_name"` and `"files"` list |
| `base_dir` | `str` | Directory prefix used to resolve relative source file paths to absolute filesystem paths |

---

### `project_data["files"]` — each file entry (input structure consumed by all three tools)

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Relative file path (may include project name prefix) |
| `"file_dependencies"` | `dict` | Contains `definitions`, `callee_usages`, and `caller_usages` |
| `"file_dependencies"]["definitions"]` | `list[dict]` | Definitions declared in this file |
| `"file_dependencies"]["callee_usages"]` | `list[dict]` | External symbols called by this file |
| `"file_dependencies"]["caller_usages"]` | `list[dict]` | Symbols in this file called by other files |

---

### Definition entry (within `definitions` list)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Symbol name |
| `"type"` | `str` | Kind of definition (e.g., function, class) |
| `"start_line"` | `int` | First line of the definition's source range |
| `"end_line"` | `int` | Last line of the definition's source range |

---

### Callee usage entry (within `callee_usages` list)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the external symbol being used |
| `"from"` | `str` | File path of the file that defines the symbol |
| `"lines"` | `list[int]` | Line numbers in the current file where this usage occurs |

---

### Caller usage entry (within `caller_usages` list)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the symbol (in this file) that is being called externally |
| `"file"` | `str` | File path of the file that calls this symbol |
| `"lines"` | `list[int]` | Line numbers in the calling file where this usage occurs |

---

### `get_files_using()` — result list element

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Path of the file that depends on the target |
| `"usage"` | `dict` | The raw callee usage entry from that file (see callee usage structure above) |

---

### `graph_search()` — return dict

| Field / Key | Type | Purpose |
|---|---|---|
| `"start"` | `str` | Start node key in `"file:name"` format |
| `"hops"` | `int` | Maximum hop depth requested |
| `"direction"` | `str` | Direction of traversal (`"outgoing"`, `"incoming"`, or `"both"`) |
| `"nodes"` | `list[dict]` | All discovered definitions within the hop limit (see node structure below) |
| `"edges"` | `list[dict]` | All dependency edges discovered (see edge structure below) |
| `"error"` | `str` | Present only when `project_data` is unloaded or the name is not found |

---

### `graph_search()` — node entry (within `"nodes"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"key"` | `str` | Unique node identifier in `"file:name"` format |
| `"file"` | `str` | File path containing this definition |
| `"name"` | `str` | Definition name; `"__module__"` if usage is at module level |
| `"type"` | `str` | Definition type; empty string if not resolvable |
| `"hop"` | `int` | BFS hop distance from the start node |
| `"via"` | `str` | `"outgoing"` or `"incoming"` indicating which traversal direction found this node |

---

### `graph_search()` — edge entry (within `"edges"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"source"` | `str` | Source node key (`"file:name"`) |
| `"target"` | `str` | Target node key (`"file:name"`) |
| `"hop"` | `int` | Hop count at which this edge was discovered |

# Error Handling

## 1. Overall Strategy

This module adopts a **graceful degradation** approach. Rather than raising exceptions or terminating execution, error conditions are surfaced as structured return values — either error-message strings or error-keyed dictionaries — that callers and the LLM agent can inspect and act upon. Initialization state is guarded at the entry point of each tool function, and file-system failures are caught and converted to human-readable strings. No retries are attempted.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Uninitialized `base_dir` | `read_source_file` is called before `load_project()` has set the module-level `base_dir` variable | Returns a descriptive error string (`"Error: base_dir not initialized. Call load_project() first."`) | Yes — caller can invoke `load_project()` and retry | The file cannot be read; the agent receives the error string as the tool result |
| Uninitialized `project_data` | `graph_search` is called before `load_project()` has set the module-level `project_data` variable | Returns a dict `{"error": "project_data not loaded. Call load_project() first."}` | Yes — caller can invoke `load_project()` and retry | Graph search produces no results; the agent receives the error dict as the tool result |
| File I/O failure | `open()` raises any exception while reading a source file (e.g., file not found, permission denied, encoding error) | The exception is caught and returned as a formatted error string (`"Error reading {path}: {e}"`) | Yes — other tool calls are unaffected | Only the requested file is unavailable; no process termination occurs |
| Definition not found (exact) | `graph_search` finds no definition whose `name` exactly matches the `name` argument | Falls back silently to a partial (case-insensitive substring) match | Yes — partial match is attempted automatically | Search continues with partial-match candidates; no error is surfaced unless partial match also fails |
| Definition not found (partial) | `graph_search` finds no definition matching even the partial-match criterion | Returns a dict `{"error": "Definition '{name}' not found"}` | Yes — caller can retry with a different name | Graph search produces no results; the agent receives the error dict as the tool result |
| Referenced file absent from index | During BFS in `graph_search`, a dependency edge points to a file path not present in `file_index` | The iteration silently skips that node (`continue`) | Yes — remaining nodes are still processed | The subgraph reachable through the missing file is omitted from results without notification |

---

## 3. Design Notes

- **Uniform return-value signalling.** Errors are communicated through the normal return type of each function (string for `read_source_file`, dict for `graph_search`), keeping the interface consistent with the LLM agent's tool-calling contract. The agent can read the error content and decide how to proceed without special exception handling on its side.

- **Two-tier initialization guard.** `read_source_file` checks `base_dir` while `graph_search` checks `project_data`; `get_files_using` performs no explicit guard and relies implicitly on `project_data` being a non-`None` iterable. This reflects the assumption that `get_files_using` is always called after a successful `load_project()`.

- **Silent skip for missing graph nodes.** During BFS traversal, absent file entries are silently skipped rather than flagged, prioritising partial result delivery over strict completeness. This means the caller receives the best available graph without knowing which nodes were dropped.

- **Fallback search in `graph_search`.** The two-stage name resolution (exact → partial) is an implicit error-recovery step that improves usability when the agent supplies an approximate name, at the cost of potentially matching an unintended definition.

# Summary

`qa_tools.py` provides stateful graph-query tools for an LLM agent over a loaded `project_knowledge.json`. Module-level `project_data: dict` and `base_dir: str` must be set externally before use. Public functions: `read_source_file(path: str) -> str`; `get_files_using(target_file: str) -> list[dict]` returning `{"file": str, "usage": dict}` entries; `graph_search(name: str, hops: int, direction: str) -> dict` returning `{"start", "hops", "direction", "nodes": list[dict], "edges": list[dict]}` where nodes carry `{key, file, name, type, hop, via}` and edges carry `{source, target, hop}`.
