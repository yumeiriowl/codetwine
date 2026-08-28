# Design Document: examples/rlm_qa/knowledge_store.py

# Overview & Purpose

## 1. Module Summary
Provide uniform read access to a codetwine knowledge file (either JSON or SQLite form) so callers can query per-file definitions, usages, and dependencies without knowing the underlying storage format.

## 2. When to Use This Module
- **Opening a knowledge file without knowing its format**: Call `open_store(knowledge_path)` to get the appropriate `Store` (`SqliteStore` for `.sqlite` paths, `JsonStore` otherwise), then use the returned object uniformly.
- **Retrieving the file dependency graph and summaries**: Call `Store.dependencies()` to get a list of `{"file", "summary", "callers", "callees"}` entries for every file, useful for building an overview of the project.
- **Fetching a single file's detailed knowledge**: Call `Store.entry(file)` to get that file's `{"file", "file_dependencies", "doc"}` entry, or `None` if the file isn't present.
- **Iterating over all files' knowledge**: Call `Store.iter_entries()` to walk through every file's entry one at a time, e.g. for building aggregate reports.
- **Searching for a definition by name**: Call `Store.find_definitions(name, partial=False)` to get matching definitions as `{"file", "name", "type", "start_line", "end_line"}`, either exact or partial (case-insensitive) match.
- **Releasing resources after use**: Call `Store.close()` to free the in-memory JSON data or close the SQLite connection.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `JsonStore` | `path` (str) | — | Load an entire `project_knowledge.json` into memory and expose it via the common store interface |
| `JsonStore.dependencies` | — | `list[dict]` | Return the project-wide file/summary/callers/callees list |
| `JsonStore.entry` | `file` (str) | `dict \| None` | Return one file's definitions/usages/doc entry, or `None` if absent |
| `JsonStore.iter_entries` | — | `Iterator[dict]` | Yield every file's entry from the in-memory data |
| `JsonStore.find_definitions` | `name` (str), `partial` (bool) | `list[dict]` | Find definitions across all files matching `name`, exact or partial |
| `JsonStore.close` | — | `None` | Clear the in-memory data structures |
| `SqliteStore` | `path` (str) | — | Open a `project_knowledge.sqlite` connection and expose it via the common store interface |
| `SqliteStore.dependencies` | — | `list[dict]` | Return the project-wide file/summary/callers/callees list, queried from the DB |
| `SqliteStore.entry` | `file` (str) | `dict \| None` | Return one file's definitions/usages/doc entry, queried from the DB, or `None` if absent |
| `SqliteStore.iter_entries` | — | `Iterator[dict]` | Yield every file's entry, queried from the DB one at a time |
| `SqliteStore.find_definitions` | `name` (str), `partial` (bool) | `list[dict]` | Find definitions across all files matching `name`, exact or partial, via a DB query |
| `SqliteStore.close` | — | `None` | Close the SQLite connection |
| `Store` (type alias) | — | `JsonStore \| SqliteStore` | Represent either concrete store type for callers that don't care which form is used |
| `open_store` | `knowledge_path` (str) | `Store` | Choose and construct the correct store implementation based on the path's file extension |

## 4. Design Decisions
- **Uniform interface across two storage backends**: Both `JsonStore` and `SqliteStore` expose the identical method signatures (`dependencies`, `entry`, `iter_entries`, `find_definitions`, `close`, plus `project_name` and `base_dir` attributes), so calling code can treat the two interchangeably via the `Store` type alias without branching on format.
- **Format selection is centralized**: `open_store` is the single entry point that decides which backend to instantiate based on the file extension, keeping format-detection logic out of the rest of the codebase.
- **Trade-off between memory and I/O**: `JsonStore` reads and holds the entire knowledge file in memory upfront, while `SqliteStore` queries the database per request, delegating query execution to `codetwine.knowledge_db`—reflecting a deliberate difference in how each backend handles the same responsibilities.

# Definition Design Specifications

## `JsonStore`

A store backed by a `project_knowledge.json` file, fully parsed and held in memory.

### `JsonStore.__init__(self, path: str) -> None`

- **Responsibility:** Load the entire knowledge JSON file into memory and index its file entries for fast lookup.
- **When to use:** Called by `open_store` when the given path does not end in `.sqlite`.
- **Design decisions:** Builds `self._by_file`, a dict keyed by file path, so `entry()` lookups are O(1) instead of scanning the `files` list repeatedly.
- **Constraints & edge cases:**
  - Requires the file to exist and contain valid UTF-8 JSON; otherwise raises the underlying `open`/`json.load` exception.
  - `project_name` defaults to `""` if missing from the JSON.
  - `base_dir` is derived from the directory portion of `path`, not from any field inside the JSON.

| Attribute | Type | Purpose |
|---|---|---|
| `_data` | `dict` | Full parsed JSON content of the knowledge file. |
| `project_name` | `str` | Project name extracted from `_data`, or `""` if absent. |
| `base_dir` | `str` | Directory portion of the input `path`. |
| `_by_file` | `dict[str, dict]` | Index mapping each file's path to its entry dict, for O(1) lookup. |

### `JsonStore.dependencies(self) -> list[dict]`

- **Responsibility:** Expose the project-wide dependency graph and per-file summaries.
- **When to use:** Called when a caller needs the small, whole-project overview (file graph, callers/callees, summaries) rather than per-file detail.
- **Constraints & edge cases:** Returns `[]` if the `project_dependencies` key is absent from the loaded JSON.

### `JsonStore.entry(self, file: str) -> dict | None`

- **Name and signature:** Takes a file path string; returns a dict (`{"file", "file_dependencies", "doc"}`) or `None`. `dict | None` represents "the entry if found, otherwise nothing."
- **Responsibility:** Retrieve one file's definitions, usages, and design document.
- **When to use:** Called by agent tools that need detail on a specific file already known by path.
- **Constraints & edge cases:** Returns `None` for any file path not present in `_by_file` (e.g., unknown or mistyped path).

### `JsonStore.iter_entries(self) -> Iterator[dict]`

- **Responsibility:** Provide sequential access to every file's entry.
- **When to use:** Called when a caller needs to walk all files (e.g., for search operations like `find_definitions`) without requiring random access by path.
- **Design decisions:** Simply wraps the in-memory `files` list with `iter()`; since data is already fully loaded, this offers no memory savings over the underlying list, unlike the sqlite variant.
- **Constraints & edge cases:** Yields nothing if `files` key is missing from the JSON.

### `JsonStore.find_definitions(self, name: str, partial: bool = False) -> list[dict]`

- **Responsibility:** Search across all files for definitions (functions, classes, constants, etc.) matching a given name.
- **When to use:** Called when a caller wants to locate where a symbol is defined, either by exact name or by substring.
- **Design decisions:**
  - `partial=False` (default) requires an exact, case-sensitive match on `d["name"]`.
  - `partial=True` performs a case-insensitive substring match (`name.lower() in d["name"].lower()`).
  - Iterates every entry and every definition therein linearly; no indexing by name is built.
- **Constraints & edge cases:**
  - Assumes each definition dict has `"name"`, `"start_line"`, and `"end_line"` keys; `"type"` is optional (defaults to `""`).
  - Returns an empty list if no matches are found.
  - Performance scales linearly with total number of definitions across all files.

### `JsonStore.close(self) -> None`

- **Responsibility:** Release the in-memory data so it can be garbage collected.
- **When to use:** Called when the caller is done with the store, to mirror `SqliteStore.close()`'s connection-closing behavior.
- **Design decisions:** Resets `_data` and `_by_file` to empty containers rather than deleting attributes, keeping the object structurally valid but functionally empty after closing.
- **Constraints & edge cases:** Calling any other method after `close()` will behave as though the file were empty (e.g., `entry()` returns `None`, `dependencies()` returns `[]`), not raise an error.

---

## `SqliteStore`

A store backed by a `project_knowledge.sqlite` database, querying it per file rather than loading everything into memory.

### `SqliteStore.__init__(self, path: str) -> None`

- **Responsibility:** Open a connection to the knowledge database without loading its contents into memory.
- **When to use:** Called by `open_store` when the given path ends in `.sqlite`.
- **Design decisions:** Delegates all schema/query knowledge to the `codetwine.knowledge_db` module; this class holds only the connection and derived metadata.
- **Constraints & edge cases:** Requires `knowledge_db.open_knowledge` to succeed; behavior on missing/corrupt database files depends entirely on that external function.

| Attribute | Type | Purpose |
|---|---|---|
| `_conn` | connection object (as returned by `knowledge_db.open_knowledge`) | Database handle used by all query methods. |
| `project_name` | `str` | Project name fetched via `knowledge_db.get_project_name`, or `""` if none. |
| `base_dir` | `str` | Directory portion of the input `path`. |

### `SqliteStore.dependencies(self) -> list[dict]`

- **Responsibility:** Return the project-wide dependency graph and summaries, same shape as `JsonStore.dependencies`.
- **When to use:** Called when a caller needs the small overview data without touching per-file detail tables.
- **Design decisions:** Wraps `knowledge_db.iter_dependencies` (a generator) in `list()`, materializing the full result set for the caller since this data is expected to be small.

### `SqliteStore.entry(self, file: str) -> dict | None`

- **Responsibility:** Retrieve one file's definitions, usages, and design document by querying the database for that file only.
- **When to use:** Called by agent tools needing detail for a specific file, without pulling in unrelated files' data.
- **Design decisions:** Queries the database directly per call rather than caching, keeping memory usage low at the cost of repeated I/O for repeated lookups of the same file.
- **Constraints & edge cases:** Returns whatever `knowledge_db.get_file` returns for an unknown file (per its own contract), presumably `None`.

### `SqliteStore.iter_entries(self) -> Iterator[dict]`

- **Responsibility:** Provide sequential, streaming access to every file's entry directly from the database.
- **When to use:** Called when a caller needs to process all files' entries without holding them all in memory at once (e.g., large projects).
- **Design decisions:** Returns the generator from `knowledge_db.iter_files` directly, unlike `JsonStore` which wraps an already-materialized list—this is the store's key memory advantage.

### `SqliteStore.find_definitions(self, name: str, partial: bool = False) -> list[dict]`

- **Responsibility:** Search for definitions matching a name via a database query, mirroring `JsonStore.find_definitions`'s result shape and semantics.
- **When to use:** Called when a caller wants exact or substring name matches for definitions across the project.
- **Design decisions:** Delegates the matching logic entirely to `knowledge_db.find_definitions`, keeping this class free of query implementation details.
- **Constraints & edge cases:** Exact matching vs. partial matching semantics depend on the external `knowledge_db.find_definitions` implementation, but the interface contract mirrors `JsonStore`.

### `SqliteStore.close(self) -> None`

- **Responsibility:** Close the underlying database connection to release resources.
- **When to use:** Called when the caller is finished with the store.
- **Constraints & edge cases:** Calling any other method after `close()` will raise whatever error the closed connection object raises on use (not handled by this class).

---

## Module-level type alias

### `Store = JsonStore | SqliteStore`

- **Name and signature:** A `Union` type alias; not a class or function.
- **Responsibility:** Documents that callers can treat a `JsonStore` and a `SqliteStore` interchangeably since both expose the same method set.
- **When to use:** Used as a type annotation for functions/parameters that accept either store implementation (e.g., `open_store`'s return type, or `build_doc_schema(store: knowledge_store.Store)` in the dependent file).
- **Constraints & edge cases:** Purely a static-typing aid; it has no runtime behavior of its own.

---

## `open_store(knowledge_path: str) -> Store`

- **Name and signature:** Takes a file path string; returns `Store`, i.e., either a `JsonStore` or `SqliteStore` instance.
- **Responsibility:** Serve as the single entry point for opening a knowledge file, hiding the choice of backing format from callers.
- **When to use:** Called whenever code needs to open a `project_knowledge.json` or `project_knowledge.sqlite` file and doesn't want to select the implementation manually — for example, `rlm_qa_agent.py` calls this to initialize `qa_tools.store`.
- **Design decisions:** Dispatch is based solely on whether the path string ends with `.sqlite`; any other extension (or no extension) is treated as JSON.
- **Constraints & edge cases:**
  - No validation that the file actually exists or is well-formed before dispatch; errors surface from within the chosen store's `__init__`.
  - A path ending in `.sqlite` but containing invalid data will fail inside `SqliteStore.__init__` via `knowledge_db.open_knowledge`.

# Dependency Description

**[Dependencies (modules this file imports)]**

- `examples/rlm_qa/knowledge_store.py` → `codetwine.knowledge_db` : needs the underlying SQLite-backed knowledge access functions used by `SqliteStore`, including `knowledge_db.open_knowledge` (open the database connection), `knowledge_db.get_project_name` (retrieve project name), `knowledge_db.iter_dependencies` (yield dependency entries), `knowledge_db.get_file` (retrieve a single file's entry), `knowledge_db.iter_files` (yield all file entries), and `knowledge_db.find_definitions` (search definitions by name).

**[Dependents (modules that import this file)]**

- `examples/rlm_qa/rlm_qa_agent.py` → `examples/rlm_qa/knowledge_store.py` : uses the `Store` type alias as a type annotation for a parameter (`build_doc_schema(store: knowledge_store.Store)`) representing an opened knowledge store, and uses `open_store` to open a knowledge file (JSON or SQLite) and assign the resulting store object to `qa_tools.store`, subsequently accessing its `project_name` attribute.

**[Dependency Direction]**

- The relationship between `knowledge_store.py` and `codetwine.knowledge_db` is unidirectional: `knowledge_store.py` depends on `knowledge_db` for database access functionality, but `knowledge_db` has no dependency back on `knowledge_store.py`.
- The relationship between `rlm_qa_agent.py` and `knowledge_store.py` is unidirectional: `rlm_qa_agent.py` depends on `knowledge_store.py` for opening and typing knowledge stores, while `knowledge_store.py` has no dependency on `rlm_qa_agent.py`.

# Data Flow

## 1. Inputs

- **`knowledge_path` / `path` (str)**: A filesystem path to a knowledge file, ending in either `.json` or `.sqlite`. Supplied by the caller (e.g., `rlm_qa_agent.py`) when opening a store via `open_store()`.
- **File contents**:
  - For `JsonStore`: the entire JSON file is read via `open()` and parsed with `json.load`. Expected top-level JSON keys are `"project_name"` (str), `"project_dependencies"` (list of dicts), and `"files"` (list of dicts).
  - For `SqliteStore`: no file is read directly by this module; instead a connection is opened via `knowledge_db.open_knowledge(path)`, and all further reads are delegated to `knowledge_db` query functions.
- **Query arguments** passed into store methods by callers:
  - `file` (str): a file path key used to look up a single file's entry.
  - `name` (str): a definition name to search for.
  - `partial` (bool): flag indicating whether `name` should be matched as a substring (case-insensitive) or an exact match.

## 2. Transformation Overview

The module acts as a **read-only access layer** that normalizes two different underlying storage formats into one common interface. The transformation pipeline differs by store type but converges on the same output shapes.

**Stage 1 — Store selection**
`open_store()` inspects the suffix of `knowledge_path` and instantiates either `SqliteStore` (for `.sqlite`) or `JsonStore` (default, including `.json`).

**Stage 2 — Initialization / Loading**
- `JsonStore.__init__`: reads and fully parses the JSON file into `self._data` (an in-memory dict). It then derives:
  - `self.project_name` from `self._data["project_name"]`.
  - `self.base_dir` from the directory portion of `path`.
  - `self._by_file`, an index dict built by iterating `self._data["files"]` and keying each entry by its `"file"` field, enabling O(1) lookup by path.
- `SqliteStore.__init__`: opens a database connection (`self._conn`) via `knowledge_db.open_knowledge(path)`, and fetches `self.project_name` via `knowledge_db.get_project_name`. No bulk data is loaded into memory; `self.base_dir` is derived the same way as `JsonStore`.

**Stage 3 — Per-query transformation (fan-out by method, not by concurrency)**
Each public method transforms the internal state into a specific output shape, using either in-memory lookups (`JsonStore`) or delegated database queries (`SqliteStore`):
- `dependencies()`: returns the file-graph/summary list — from `self._data["project_dependencies"]` (JSON) or `knowledge_db.iter_dependencies(self._conn)` (SQLite), converted to a `list[dict]`.
- `entry(file)`: returns a single file's full entry — a direct dict lookup in `self._by_file` (JSON) or a targeted DB fetch via `knowledge_db.get_file(self._conn, file)` (SQLite).
- `iter_entries()`: returns an iterator over all file entries — `iter(self._data["files"])` (JSON, already in memory) or `knowledge_db.iter_files(self._conn)` (SQLite, streamed per query).
- `find_definitions(name, partial)`:
  - `JsonStore`: iterates every entry (via `iter_entries()`), then iterates each entry's `file_dependencies.definitions` list, applying either a case-insensitive substring match or an exact match on `d["name"]`, accumulating matches into a result list.
  - `SqliteStore`: delegates the equivalent filtering logic to `knowledge_db.find_definitions(self._conn, name, partial=partial)`.

**Stage 4 — Teardown**
`close()` releases resources: `JsonStore` discards its in-memory dicts (`self._data`, `self._by_file`) by resetting them to empty; `SqliteStore` closes the database connection (`self._conn.close()`).

There is no async or parallel processing in this module — all operations are synchronous, sequential, and per-call.

## 3. Outputs

- **`project_name` (str)**: attribute available immediately after construction on either store.
- **`base_dir` (str)**: attribute holding the directory of the opened knowledge file.
- **`dependencies()` → `list[dict]`**: one entry per file, each shaped as `{"file", "summary", "callers", "callees"}`.
- **`entry(file)` → `dict | None`**: a single file's entry shaped as `{"file", "file_dependencies", "doc"}`, or `None` if not found.
- **`iter_entries()` → `Iterator[dict]`**: yields file entries one at a time, same shape as above.
- **`find_definitions(name, partial)` → `list[dict]`**: matching definitions shaped as `{"file", "name", "type", "start_line", "end_line"}`.
- **Side effect — `close()`**: releases in-memory data or closes the DB connection; no return value.
- **Downstream consumer (`rlm_qa_agent.py`)**: uses `open_store()` to construct a `Store`, assigns it to `qa_tools.store`, and reads `.project_name` to build a `project_data` dict (with `"project_name"` / `"project_dependencies"` keys) — confirming that `dependencies()` output feeds directly into that caller's project summary structure.

## 4. Key Data Structures

**Dependency entry** (returned by `dependencies()`, list element)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | str | File path identifying the entry |
| `summary` | str (implied) | Short description of the file |
| `callers` | list | Files/symbols that call into this file |
| `callees` | list | Files/symbols this file calls into |

**File entry** (returned by `entry()` / `iter_entries()`)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | str | File path, used as the lookup key in `_by_file` |
| `file_dependencies` | dict | Contains definitions and dependency data for the file |
| `doc` | dict/str | The file's design document content |

**`file_dependencies` sub-structure** (one level of nesting, used by `find_definitions`)

| Field / Key | Type | Purpose |
|---|---|---|
| `definitions` | list[dict] | List of definition records within the file |

**Definition record** (element of `file_dependencies["definitions"]`, and shape of items returned by `find_definitions`)

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | str | Definition's identifier, matched against the query `name` |
| `type` | str | Kind of definition (e.g., function, class) |
| `start_line` | int | Starting line number of the definition |
| `end_line` | int | Ending line number of the definition |
| `file` | str | *(added by `find_definitions`, not part of raw record)* File path the definition belongs to |

**`Store` type alias**

| Field / Key | Type | Purpose |
|---|---|---|
| `Store` | `JsonStore \| SqliteStore` | Union type representing either concrete store implementation, used by callers that don't care which backing format is in use |

# Error Handling

### 1. Overall Strategy

This file implements no explicit error handling of its own — there are no `try/except` blocks, no custom exceptions, and no logging calls anywhere in `JsonStore`, `SqliteStore`, or `open_store`. The module follows an implicit **fail-fast / propagate-upward** strategy: any error condition (missing file, malformed JSON, missing key, closed connection, etc.) is allowed to raise its natural exception (e.g., `FileNotFoundError`, `json.JSONDecodeError`, `KeyError`) straight out to the caller (the tool layer in `rlm_qa_agent.py`). Lookups that are expected to legitimately miss (`entry()`, `Store.entry`) use `dict.get`-style access to return `None` rather than raising, which is the only form of graceful degradation present.

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| File not found / IO error | `path` passed to `JsonStore.__init__` or `knowledge_db.open_knowledge` does not exist or is unreadable | None — exception propagates from `open()` / `knowledge_db.open_knowledge` | No | Store construction fails; `open_store` raises, caller cannot proceed |
| Malformed JSON content | `project_knowledge.json` is not valid JSON | None — `json.load` raises `json.JSONDecodeError` uncaught | No | `JsonStore` construction aborts |
| Missing expected keys in JSON structure | A file entry lacks `"file"`, `"name"`, `"start_line"`, `"end_line"`, etc. | None — dictionary/key access raises `KeyError` directly | No | `find_definitions` or `_by_file` construction fails at first offending entry |
| Unknown/absent file entry lookup | `entry(file)` called with a `file` not present in the knowledge store | Returns `None` via `dict.get` (JsonStore) or `knowledge_db.get_file` returning `None` (SqliteStore) | Yes (caller can check for `None` and skip) | Caller must handle `None`; no exception raised |
| Unsupported/invalid path extension | `open_store` called with a path that is neither clearly `.sqlite` nor a valid JSON path | Defaults to `JsonStore` for any non-`.sqlite` path; downstream failure occurs only when `open()`/`json.load` is attempted | No (deferred) | Misclassification surfaces later as an IO/JSON error rather than at dispatch time |
| Backing database/connection issues (SqliteStore) | `knowledge_db.open_knowledge` fails, or subsequent query functions (`iter_dependencies`, `get_file`, `find_definitions`) error | None — errors propagate from `knowledge_db` module, uncaught here | No | `SqliteStore` methods fail and raise to caller |
| Use-after-close | Any method called on a store after `close()` has been invoked | None — `JsonStore` operates on emptied dicts (returns empty results, not an error); `SqliteStore` would raise from the closed connection when `knowledge_db` functions are called | Partially (JsonStore silently returns empty/`None`; SqliteStore raises) | Inconsistent behavior between the two store implementations after close |

### 3. Design Notes

- The module deliberately keeps both store implementations minimal and symmetrical: neither adds validation or exception handling beyond what `json.load`, `open()`, or the `knowledge_db` module already provide, so that errors are surfaced as close as possible to their true source rather than being wrapped or masked.
- Read-only, per-file "get" operations (`entry`) are designed to return `None` on a miss instead of raising, aligning with typical dictionary-based lookup semantics and letting callers decide whether a missing file is an error condition.
- Iteration and search operations (`iter_entries`, `find_definitions`) assume well-formed knowledge files; they do not defend against malformed entries, reflecting an assumption that the knowledge file was already validated/produced correctly upstream (by codetwine) before being handed to this reader.
- The `close()` methods are asymmetric in effect: `JsonStore.close()` clears its in-memory structures (making later calls degrade gracefully to empty results), while `SqliteStore.close()` closes the underlying connection (making later calls fail), reflecting the different resource models (in-memory dict vs. external DB connection) rather than a unified post-close contract.

# Summary

Provides uniform access to codetwine knowledge files (JSON/SQLite) via `JsonStore`/`SqliteStore` (path:str), unified as `Store` alias, chosen by `open_store(knowledge_path:str)->Store`. Both expose `dependencies()->list[dict]`, `entry(file:str)->dict|None`, `iter_entries()->Iterator[dict]`, `find_definitions(name:str, partial:bool)->list[dict]`, `close()->None`, plus `project_name`/`base_dir` attrs. Key structures: dependency entries `{file,summary,callers,callees}`, file entries `{file,file_dependencies,doc}`, definition records `{file,name,type,start_line,end_line}`.
