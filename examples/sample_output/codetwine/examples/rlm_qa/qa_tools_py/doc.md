# Design Document: examples/rlm_qa/qa_tools.py

# Overview & Purpose

## 1. Module Summary
Expose a set of read-only query tools that let an LLM agent inspect a pre-built project knowledge store (source files, definitions, dependencies, and design docs) without ever loading the full analysis into the agent's context.

## 2. When to Use This Module
- **Reading raw source of a file**: call `read_source_file(path)` when you need the actual text of a source file (e.g., to extract a specific function's code by line range).
- **Inspecting a single file's structure and docs**: call `get_file_detail(file)` when you have already narrowed down to one file and need its definitions, callee/caller usages, and design document (summary + sections).
- **Keyword lookup across the whole project**: call `search_text(keyword, limit)` when you don't know which file to look at and want to find where a term appears in summaries, doc sections, definition source, or dependency contexts.
- **Finding dependents of a file**: call `get_files_using(target_file)` when you need to know which files import/use a given file, based on partial path matching against `callee_usages`.
- **Exploring dependency relationships around a symbol**: call `graph_search(name, hops, direction)` when you need a bounded-hop dependency graph (callers/callees/both) rooted at a specific definition name, useful for impact analysis or tracing call chains.
- **Initializing the module before use**: the module-level `store` variable must be set (typically via `qa_tools.store = knowledge_store.open_store(...)` performed by the caller, e.g. `rlm_qa_agent.py`) before any tool function is called; all tools return an error dict/message/list if `store` is `None`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `store` | — (module-level variable, `KnowledgeStore` or `None`) | — | Holds the active knowledge store instance that all tool functions query; must be set via `load_project()` before use. |
| `SEARCH_HIT_LIMIT` | — (constant, `int`) | — | Default maximum number of hits returned by `search_text`. |
| `read_source_file` | `path` (str) | `str` | Reads and returns the content of a source file copied into the output directory, stripping the project name prefix; returns an error string on failure. |
| `get_file_detail` | `file` (str) | `dict` | Retrieves one file's definitions, callee/caller dependency usages, and design document from the store. |
| `search_text` | `keyword` (str), `limit` (int, default `SEARCH_HIT_LIMIT`) | `list` | Case-insensitively searches doc summaries/sections, definition source, and dependency contexts across the whole project, stopping once the hit limit is reached. |
| `get_files_using` | `target_file` (str) | `list` | Finds files whose `callee_usages` entries partially match the given target file path, i.e., the file's dependents. |
| `graph_search` | `name` (str), `hops` (int, default `1`), `direction` (str, default `"both"`) | `dict` | Performs a BFS over definitions and their outgoing/incoming dependency edges starting from a named definition, up to a given number of hops. |

## 4. Design Decisions
- **Module-level singleton store**: rather than passing a store instance to each function, the module relies on a single shared `store` variable set externally by `load_project()`, keeping tool function signatures simple for LLM tool-calling while avoiding holding the entire project analysis in the sandbox/agent context.
- **Per-file lazy entry caching in `graph_search`**: `deps_of` reads each file's dependency data through the store only once per search via an internal cache (`entry_cache`), avoiding redundant store lookups during BFS traversal.
- **Early-exit search with limit-checking callback**: `search_text` uses an internal `add()` closure that both records a hit and signals when the limit is reached, allowing the search loop to terminate scanning as soon as enough hits are found rather than scanning the entire project unconditionally.
- **Exact-then-partial match fallback in `graph_search`**: the start definition is first looked up via exact match (`store.find_definitions(name)`) and only falls back to partial matching if no exact match exists, prioritizing precision over recall for the search entry point.

# Definition Design Specifications

## Module variable: `store`

| Aspect | Description |
|---|---|
| Type | `KnowledgeStore` instance or `None` |
| Responsibility | Holds the single, process-wide handle to the project's knowledge base (backed by `project_knowledge.json` or `project_knowledge.sqlite`), so every tool function can query it without receiving it as a parameter. |
| When to use | Set once by the caller (`rlm_qa_agent.py`) via `qa_tools.store = knowledge_store.open_store(knowledge_path)` right after project load; all tool functions read it thereafter. |
| Design decisions | Module-level singleton instead of dependency injection, chosen so that the analysis data is not held inside the LLM sandbox/agent context — only the store handle is shared, keeping large project data out of the reasoning context. |
| Constraints & edge cases | Every public tool function must guard against `store is None` and return an error dict/list/string; this guard is duplicated in each function rather than centralized. |

## Constant: `SEARCH_HIT_LIMIT`

| Aspect | Description |
|---|---|
| Type | `int` (value `40`) |
| Responsibility | Default cap on the number of hits `search_text` returns before it stops scanning further entries. |
| When to use | Used automatically as the default value of `search_text`'s `limit` parameter; callers can override it per call. |
| Constraints & edge cases | Purely a default; does not enforce an upper bound if a caller passes a larger `limit`. |

## Function: `read_source_file(path: str) -> str`

| Aspect | Description |
|---|---|
| Responsibility | Reads and returns the full text of a source file that was copied into the output directory, resolving it relative to the loaded project's base directory. |
| When to use | Called after narrowing down to a specific file (e.g., via `get_file_detail`) when the caller needs the actual source text, such as to extract a function body by line range. |
| Design decisions | Strips a leading `"<project_name>/"` prefix from `path` before joining with `store.base_dir`, because the `file` field in stored data is prefixed with the project name but the on-disk copy is not. |
| Constraints & edge cases | Requires `store` to be initialized (`load_project()` called first) or returns an error string. Returns a human-readable error string (not an exception) on any `OSError` during read, so callers must check for an `"Error"`-prefixed string. Assumes file is UTF-8 encoded. |

## Function: `get_file_detail(file: str) -> dict`

| Aspect | Description |
|---|---|
| Responsibility | Returns the detailed record for a single file — its definitions, callee/caller usages, and design document — data that is intentionally excluded from the bulk `project_data` to keep it light. |
| When to use | Called once a caller has identified a specific file of interest (e.g., from `project_dependencies` or a search result) and needs its structural details. |
| Return type explanation | Returns a nested `dict` combining three record shapes: `file` (path string), `file_dependencies` (dict of three lists: `definitions`, `callee_usages`, `caller_usages`, each a list of small dicts), and `doc` (dict with `summary` and a list of `sections`). On failure, a single-key `{"error": str}` dict is returned instead. |
| Design decisions | Delegates matching entirely to `store.entry(file)`; the function itself has no matching logic (exact key expected). |
| Constraints & edge cases | Requires `store` to be initialized. Returns `{"error": ...}` both when the store is uninitialized and when the file is not found — callers must check for the `"error"` key in either case since the shape differs from the success case. |

## Function: `search_text(keyword: str, limit: int = SEARCH_HIT_LIMIT) -> list`

| Aspect | Description |
|---|---|
| Responsibility | Performs a case-insensitive full-text search across all entries' doc summaries, doc sections, definition source contexts, and both callee/caller usage contexts, to let the caller locate relevant files/definitions by content. |
| When to use | Called when the caller has a topic/term/name in mind but does not yet know which file(s) are relevant. |
| Design decisions | Uses a nested `add()` closure that both appends a hit and reports whether the `limit` has been reached, allowing the outer loops to short-circuit (`return hits`) as soon as the cap is hit rather than scanning the whole project — an early-exit strategy across five different match categories in a single pass per entry. |
| Constraints & edge cases | If `store` is `None`, returns a **list containing one error dict** (`[{"error": ...}]`), which is a different failure shape than `get_file_detail`'s bare dict — callers must handle this inconsistency. Matching is purely substring-based (`in`), not tokenized or fuzzy. Missing/`None` fields (e.g., `doc.get("summary")`) are defensively treated as empty strings before lowering. `limit` caps total hits across all five kinds combined, not per kind. |

## Function: `get_files_using(target_file: str) -> list`

| Aspect | Description |
|---|---|
| Responsibility | Finds all recorded usages across the project whose `callee_usages[].from` field partially matches the given file path, i.e., identifies dependents of a target file. |
| When to use | Called when the caller wants to know which other files/modules depend on a specific file before deciding whether it is safe to describe/change it. |
| Design decisions | Matching is a plain substring check (`target_file in usage.get("from", "")`), not a path-normalized or exact match, so a short/partial path fragment can over-match multiple files. |
| Constraints & edge cases | If `store` is `None`, returns `[{"error": ...}]` (list-wrapped error, consistent with `search_text`). No result deduplication — the same file can appear multiple times if it has multiple matching usages. |

## Function: `graph_search(name: str, hops: int = 1, direction: str = "both") -> dict`

| Aspect | Description |
|---|---|
| Responsibility | Performs a breadth-first search over the dependency graph implied by definitions and their callee/caller usages, starting from a named definition, to surface its dependencies and/or dependents up to a hop limit. |
| When to use | Called when the caller needs to understand the local dependency neighborhood of a specific function/class/definition (e.g., "what does X call, and what calls X") rather than a single file's details. |
| Design decisions | • Definition lookup first tries an exact match via `store.find_definitions(name)`, falling back to a partial match (`partial=True`) only if the exact search yields nothing — favors precision over recall by default.<br>• Internally caches file entries in `entry_cache` (a local `dict[str, dict \| None]`) keyed by file path so that any file touched during the BFS is read from the store at most once, even if visited via multiple edges.<br>• Nodes and edges are keyed by the composite string `"file:name"` rather than object identity, so definitions with the same name in different files are distinguished, but two different anonymous/module-level regions in the same file are not.<br>• Special-cases the pseudo-definition name `"__module__"` for outgoing traversal: when the current node is not an actual definition (i.e., `current_def` is `None`) but is literally named `"__module__"`, usage lines that fall outside every known definition's line range are attributed to module-level code.<br>• For incoming edges, the calling definition is resolved by scanning the source file's definitions for one whose line range contains the usage's line(s); if none is found, the source is attributed to `"__module__"` with an empty type.<br>• Deduplicates edges via a `seen_edges` set keyed by `(source_key, target_key, direction_label)`, and deduplicates nodes via `visited`, so a BFS revisiting the same edge/node through a different path is not recorded twice. |
| Constraints & edge cases | Requires `store` to be initialized, else returns `{"error": ...}` (bare dict, differing again from the list-wrapped error shape used elsewhere). Returns `{"error": f"Definition '{name}' not found"}` if neither exact nor partial match succeeds. The `hops` parameter bounds BFS depth via `current_hop >= hops` check before expanding a node — nodes at exactly `hops` distance are recorded, but their own edges are not expanded further. `direction` must be one of `"outgoing"`, `"incoming"`, `"both"`; any other value silently results in neither branch executing (no outgoing/incoming expansion), effectively returning only the start node with no edges. Target/source definition "type" lookups depend on the target/source file's definitions list matching by name only (not by line range), so overloaded/duplicate names in the same file can yield an inexact type. |

# Dependency Description

## Dependencies (modules this file imports)

This file has no project-internal module dependencies. Its only imports (`os`, `collections.deque`) are standard library modules and are therefore excluded per the exclusion rule. The `store` module variable referenced throughout the file is not a static import but an external object assigned at runtime by `examples/rlm_qa/rlm_qa_agent.py` (see Dependents below); this file does not itself depend on any other project module to define its own logic.

## Dependents (modules that import this file)

- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : assigns the `store` attribute (`qa_tools.store = knowledge_store.open_store(knowledge_path)`) so that the tool functions in this file have access to a `KnowledgeStore` instance to query.
- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : reads `qa_tools.store.project_name` and `qa_tools.store.dependencies()` to build the `project_data` dictionary passed to the agent.
- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : registers `qa_tools.get_file_detail` as a tool for `dspy.RLM`, allowing the agent to retrieve a file's definitions, dependency usages, and design document.
- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : registers `qa_tools.search_text` as a tool for `dspy.RLM`, allowing the agent to search the project for keyword occurrences.
- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : registers `qa_tools.read_source_file` as a tool for `dspy.RLM`, allowing the agent to read raw source file content.
- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : registers `qa_tools.get_files_using` as a tool for `dspy.RLM`, allowing the agent to find files that depend on a given file.
- `examples/rlm_qa/rlm_qa_agent.py` → `qa_tools.py` : registers `qa_tools.graph_search` as a tool for `dspy.RLM`, allowing the agent to perform BFS-based dependency graph searches from a definition name.

## Dependency Direction

The relationship between this file and `examples/rlm_qa/rlm_qa_agent.py` is unidirectional: `rlm_qa_agent.py` depends on and drives `qa_tools.py`, both by initializing its `store` state and by consuming its exposed tool functions. `qa_tools.py` itself does not import or reference `rlm_qa_agent.py` or any other project-internal module.

# Data Flow

## 1. Inputs

- **`store` (module-level global)**: A `KnowledgeStore` instance assigned externally by the caller (`rlm_qa_agent.py` via `load_project()`), exposing `project_name`, `base_dir`, `entry(file)`, `iter_entries()`, `dependencies()`, and `find_definitions(name, partial=False)`. All tool functions read from this object rather than holding their own copy of the analysis data.
- **Function arguments** (supplied by the calling agent/LLM tool invocation):
  - `read_source_file(path: str)` — a file path string as it appears in the project's JSON/knowledge data (possibly prefixed with the project name).
  - `get_file_detail(file: str)` — a file path string matching a key in the store's entries.
  - `search_text(keyword: str, limit: int = SEARCH_HIT_LIMIT)` — a search keyword and an optional result cap.
  - `get_files_using(target_file: str)` — a (partial) file path string.
  - `graph_search(name: str, hops: int = 1, direction: str = "both")` — a definition name, hop count, and traversal direction.
- **Indirect file reads**: `read_source_file` performs an OS-level file read (`open(...)`) under `store.base_dir` joined with the (possibly stripped) path.
- **Store-backed data reads**: `get_file_detail`, `search_text`, `get_files_using`, and `graph_search` pull per-file entries (`file_dependencies`, `doc`) through `store.entry(...)` / `store.iter_entries()`, without loading the whole analysis into memory at once.

## 2. Transformation Overview

**`read_source_file`**
1. Validate `store` is initialized.
2. Normalize `path` by stripping a leading `"{project_name}/"` prefix if present.
3. Join with `store.base_dir` to build a full filesystem path.
4. Open and read the file as UTF-8 text.
5. Return raw string content, or an error string on `OSError`.

**`get_file_detail`**
1. Validate `store`.
2. Fetch entry via `store.entry(file)`.
3. Return the entry dict as-is (containing `file`, `file_dependencies`, `doc`), or an error dict if not found.

**`search_text`**
1. Validate `store`; lower-case the keyword.
2. Iterate every entry from `store.iter_entries()` (streamed, one file at a time).
3. For each entry, sequentially scan: doc summary → doc sections → definitions' `context` → callee_usages' `target_context` → caller_usages' `usage_context`, each case-insensitively checked against the keyword.
4. Each match is appended as a `{"kind", "file", "name"}` hit via the local `add()` helper, which also checks the `limit`.
5. Early-exit (return) as soon as `limit` hits are collected; otherwise continue to the next entry.
6. Return the accumulated hits list (fan-in of scattered matches into one flat list).

**`get_files_using`**
1. Validate `store`.
2. Iterate every entry's `callee_usages`.
3. Filter usages whose `from` field contains `target_file` as a substring.
4. Collect matches into `{"file", "usage"}` dicts.
5. Return the list.

**`graph_search`**
1. Validate `store`; set up an `entry_cache` dict to memoize `store.entry(file_path)` lookups (avoids re-reading the same file entry multiple times during traversal).
2. Resolve the start node: `store.find_definitions(name)` (exact), falling back to `partial=True` if no exact match; error out if nothing is found. Build `start_key = "file:name"`.
3. Run BFS using a `deque` queue seeded with `(start_key, start_file, start_name, hop=0)` and a `visited` set to prevent revisits.
4. At each dequeued node (stopping once `current_hop >= hops`):
   - Look up the current definition's line range (`current_def`) within its file's `definitions`.
   - **Outgoing branch** (if `direction` is `"outgoing"` or `"both"`): scan `callee_usages`, keep only usages whose line numbers fall inside the current definition's range (or, for `"__module__"`, lines outside all definitions), resolve target file/name/type, add new `edges` and `nodes` (deduplicated via `seen_edges`/`visited`), and enqueue unvisited targets at `next_hop`.
   - **Incoming branch** (if `direction` is `"incoming"` or `"both"`): scan `caller_usages` matching the current definition's name, resolve the calling definition (or `"__module__"` if none matches) in the source file via its own definitions' line ranges, add edges/nodes, and enqueue unvisited sources at `next_hop`.
5. This fans out one node into potentially many edges/nodes per hop, and merges back into a single shared `nodes`/`edges`/`visited` collection (BFS frontier expansion, not real concurrency — purely sequential).
6. Terminate when the queue is exhausted or hop depth exceeded; assemble the final result dict.

## 3. Outputs

- **`read_source_file`** → `str`: full file content, or an `"Error reading {path}: {e}"` message string.
- **`get_file_detail`** → `dict`: the file's entry (`file`, `file_dependencies`, `doc`) or `{"error": str}`.
- **`search_text`** → `list[dict]`: list of hits `{"kind", "file", "name"}`, or `[{"error": str}]` if store not initialized.
- **`get_files_using`** → `list[dict]`: list of `{"file", "usage"}` entries, or `[{"error": str}]`.
- **`graph_search`** → `dict`: BFS traversal result (`start`, `hops`, `direction`, `nodes`, `edges`), or `{"error": str}`.
- **Side effects**: None of the functions write files or mutate `store`; they are read-only against the store and filesystem (except in-function local caches like `entry_cache`, `hits`, `visited`, which are discarded on return).

## 4. Key Data Structures

### `file_dependencies` (embedded in store entries, consumed by all functions except `read_source_file`)

| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | `list[dict]` | Definitions (functions/classes) in the file |
| `definitions[].name` | `str` | Definition identifier |
| `definitions[].type` | `str` | Kind of definition (e.g., function, class) |
| `definitions[].start_line` / `end_line` | `int` | Line range used to map usages to owning definitions |
| `definitions[].context` | `str` | Source snippet searched by `search_text` |
| `callee_usages` | `list[dict]` | Symbols this file's code calls/imports |
| `callee_usages[].lines` | `list[int]` | Line numbers of usage, used to locate owning definition |
| `callee_usages[].name` | `str` | Name of the used symbol |
| `callee_usages[].from` | `str` | File path the symbol originates from |
| `callee_usages[].target_context` | `str` | Source context of the target, searched by `search_text` |
| `caller_usages` | `list[dict]` | Places elsewhere that use this file's definitions |
| `caller_usages[].lines` | `list[int]` | Line numbers of usage in the caller file |
| `caller_usages[].name` | `str` | Name of the definition being used |
| `caller_usages[].file` | `str` | File path of the caller |
| `caller_usages[].usage_context` | `str` | Source context searched by `search_text` |

### `doc` (embedded in store entries)

| Field / Key | Type | Purpose |
|---|---|---|
| `summary` | `str` | Free-text file summary, searched by `search_text` |
| `sections` | `list[dict]` | Structured documentation sections |
| `sections[].id` | `str` | Section identifier |
| `sections[].title` | `str` | Section title, returned as `name` in `search_text` "section" hits |
| `sections[].content` | `str` | Section body text, searched by `search_text` |

### `search_text` hit dict

| Field / Key | Type | Purpose |
|---|---|---|
| `kind` | `str` | One of `"summary"`, `"section"`, `"definition"`, `"callee"`, `"caller"` |
| `file` | `str` | File path where the match occurred |
| `name` | `str` | Definition/section/usage name associated with the match |

### `get_files_using` result item

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File whose `callee_usages` matched |
| `usage` | `dict` | The matching `callee_usages` entry (see `file_dependencies.callee_usages[]` above) |

### `graph_search` result

| Field / Key | Type | Purpose |
|---|---|---|
| `start` | `str` | `"file:name"` key of the BFS root |
| `hops` | `int` | Requested max hop depth |
| `direction` | `str` | `"outgoing"`, `"incoming"`, or `"both"` |
| `nodes` | `list[dict]` | Discovered definition nodes |
| `edges` | `list[dict]` | Discovered dependency edges |

### `graph_search` node dict

| Field / Key | Type | Purpose |
|---|---|---|
| `key` | `str` | `"file:name"` unique node identifier |
| `file` | `str` | File containing the definition |
| `name` | `str` | Definition name (or `"__module__"` for module-level usage) |
| `type` | `str` | Definition type, when resolvable |
| `hop` | `int` | BFS distance from the start node |
| `via` | `str` | `"outgoing"` or `"incoming"`, indicating discovery direction |

### `graph_search` edge dict

| Field / Key | Type | Purpose |
|---|---|---|
| `source` | `str` | `"file:name"` key of the edge's source node |
| `target` | `str` | `"file:name"` key of the edge's target node |
| `hop` | `int` | Hop level at which the edge was discovered |

# Error Handling

## 1. Overall Strategy

This file follows a **graceful degradation, return-value-based** error handling policy rather than raising exceptions. Every public tool function checks preconditions (primarily whether the module-level `store` has been initialized) and, on failure, returns an error indicator embedded in the function's normal return type (a string, dict, or list) instead of throwing. This allows the calling agent/LM loop to inspect the result and decide how to proceed without a crash. The only place a native exception is caught explicitly is file I/O in `read_source_file`, which follows a **catch-and-report** approach: the `OSError` is caught and turned into a descriptive string message. There is no retry logic, no logging, and no process termination anywhere in this file; all failures are surfaced to the caller as data.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Store not initialized | `store is None` when calling `read_source_file`, `get_file_detail`, `search_text`, `get_files_using`, or `graph_search` before `load_project()` sets `qa_tools.store` | Return an error string/dict/list (e.g. `"Error: store not initialized..."`, `{"error": "..."}`, `[{"error": "..."}]`) instead of raising | Yes (caller can call `load_project()` and retry) | Requested operation yields no data but the process continues |
| File read failure | `open()` raises `OSError` in `read_source_file` (e.g. file missing, permission issue, bad path) | Caught via `except OSError as e` and converted to string `f"Error reading {path}: {e}"` | Yes (caller can inspect message and choose a different path) | Only that single file read fails; no other tool affected |
| File not found in project | `get_file_detail` called with a `file` not present in the store (`store.entry(file)` returns `None`) | Return `{"error": f"File '{file}' not found"}` | Yes (caller can adjust the file argument) | Only that lookup returns no detail |
| Definition not found | `graph_search` cannot find `name` via exact match or partial match in `store.find_definitions` | Return `{"error": f"Definition '{name}' not found"}` | Yes (caller can adjust the search term) | Graph traversal is skipped entirely; no partial result returned |
| Missing/partial dependency data | `deps_of()` returns an empty dict when `store.entry()` yields `None` or lacks `file_dependencies`; similarly missing `doc`/`sections` fields in `search_text` are defaulted with `or {}` / `or []` | Silently treated as empty structures, loop/branch is skipped (`if not deps: continue`) | Yes (implicitly, no failure raised) | That file/node contributes no further nodes/edges/hits, but overall search/BFS continues |
| Search hit limit reached | `search_text` accumulates hits and reaches `limit` during scanning | Scanning stops early (`add()` returns True, causing `return hits`) | Yes (not a failure, deliberate short-circuit) | Search terminates before scanning the whole project; results may be incomplete but function returns normally |

## 3. Design Notes

- **Uniform "return-as-error" convention**: Every tool mirrors its normal successful return shape (string, dict, or list) when reporting an error, so callers (including an LLM-driven agent) can use consistent, type-predictable inspection logic rather than needing exception handling.
- **No logging or side effects**: The module performs no logging of errors; all error information is only ever conveyed through the return value, keeping the tools stateless and side-effect-free aside from reads through `store`.
- **Defensive defaulting over exceptions**: Throughout `search_text` and `graph_search`, missing dict keys (`doc`, `sections`, `file_dependencies`, `definitions`, etc.) are handled with `.get(..., default)` / `or {}` patterns rather than exceptions, favoring silent continuation over failure when the underlying knowledge store has incomplete entries.
- **Caching to limit repeated failures**: `graph_search` caches file entries in `entry_cache` so a file whose lookup fails (`None`) is only attempted once per search rather than repeatedly triggering the same missing-data path across BFS iterations.
- **No global fail-fast except for the initialization guard**: The only condition treated uniformly across all tools as a blocking precondition is the uninitialized `store`; beyond that, all other lookups (missing file, missing definition, missing dependency data) degrade gracefully rather than halting the tool.

# Summary

Provides read-only LLM tool functions querying a module-level `store` (KnowledgeStore) to inspect project knowledge without loading it into agent context. Functions: `read_source_file(path:str)->str`, `get_file_detail(file:str)->dict`, `search_text(keyword:str, limit:int)->list`, `get_files_using(target_file:str)->list`, `graph_search(name:str, hops:int, direction:str)->dict`. Consumes/produces `file_dependencies` (definitions, callee_usages, caller_usages dicts), `doc` (summary/sections), search hit dicts, and BFS graph result (`nodes`/`edges` dicts).
