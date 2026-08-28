# Design Document: codetwine/knowledge_db.py

# Overview & Purpose

## 1. Module Summary
Builds and serves a SQLite-backed representation of the whole-project analysis result, streaming per-file JSON data into the database one file at a time and exposing read-only query functions equivalent to `project_knowledge.json`.

## 2. When to Use This Module
- **Generating the consolidated knowledge database after a project analysis run**: call `save_consolidated_sqlite(...)` to build (or rebuild) `project_knowledge.sqlite` from the per-file `file_dependencies.json`/`doc.json` outputs, the symbol-level dependency graph, and the summary map, without loading the whole project into memory (used by `codetwine/pipeline.py`).
- **Opening an existing knowledge database for querying**: call `open_knowledge(path)` to get a read-only `sqlite3.Connection` with `sqlite3.Row` rows.
- **Retrieving the analyzed project's name**: call `get_project_name(connection)`.
- **Iterating over all analyzed files or all dependency summaries** (e.g., to reproduce the consolidated JSON structure incrementally): call `iter_files(connection)` or `iter_dependencies(connection)`.
- **Looking up a single file's consolidated entry**: call `get_file(connection, file)`.
- **Finding a file's dependency edges**: call `callees_of(connection, file)` for files it depends on, or `callers_of(connection, file)` for files depending on it.
- **Searching for a symbol/definition by name across the whole project without reading file bodies**: call `find_definitions(connection, name, partial=...)`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `SCHEMA_VERSION` | (constant, `str`) | — | Version tag recorded in the `meta` table; bumped when the schema layout changes. |
| `save_consolidated_sqlite` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Rebuilds the SQLite database at `output_path` from per-file JSON outputs, writing `meta`, `files`, `definitions`, and `file_edges` tables one file at a time. |
| `open_knowledge` | `path: str` | `sqlite3.Connection` | Opens an existing knowledge database in read-only mode with row access by column name; raises `FileNotFoundError` if missing. |
| `get_project_name` | `connection: sqlite3.Connection` | `str \| None` | Returns the analyzed project's name from the `meta` table. |
| `iter_files` | `connection: sqlite3.Connection` | `Iterator[dict]` | Yields each file's consolidated entry (`file`, optional `file_dependencies`, optional `doc`) in insertion order. |
| `iter_dependencies` | `connection: sqlite3.Connection` | `Iterator[dict]` | Yields each file's `{file, summary, callers, callees}` dependency summary in insertion order. |
| `get_file` | `connection: sqlite3.Connection`, `file: str` | `dict \| None` | Returns one file's consolidated entry, or `None` if the file is not in the database. |
| `callees_of` | `connection: sqlite3.Connection`, `file: str` | `list[str]` | Returns the sorted list of files that the given file depends on. |
| `callers_of` | `connection: sqlite3.Connection`, `file: str` | `list[str]` | Returns the sorted list of files recorded as depending on the given file. |
| `find_definitions` | `connection: sqlite3.Connection`, `name: str`, `partial: bool` | `list[dict]` | Returns all definitions matching `name` (exact or case-insensitive substring match) as `{file, name, type, start_line, end_line}` dicts, without reading file bodies. |

## 4. Design Decisions
- **Streaming build over in-memory aggregation**: `save_consolidated_sqlite` reads, inserts, and releases one file's JSON at a time so the whole project is never held in memory simultaneously, unlike `project_knowledge.json`.
- **Database as a rebuildable cache, not a source of truth**: the per-file JSON files remain authoritative; the SQLite database at `output_path` is always deleted and rebuilt from scratch on each run rather than incrementally updated.
- **Asymmetric edge storage**: `file_edges` stores `caller` and `callee` directions independently, as produced by the symbol-level dependency analysis (`symbol_deps`), without deriving one direction from the other — mirroring the fact that callers and callees come from separate analyses that don't always agree.
- **Read/write separation**: writing is confined to `save_consolidated_sqlite`, while all other public functions (`open_knowledge`, `get_project_name`, `iter_files`, `iter_dependencies`, `get_file`, `callees_of`, `callers_of`, `find_definitions`) only read, with `open_knowledge` enforcing read-only access at the connection level (`mode=ro`).
- **`_row_to_entry`/`_definition_rows` as private normalization helpers**: internal-only functions convert between row and JSON/consolidated-entry shapes and are not part of the public API.

# Definition Design Specifications

## `SCHEMA_VERSION`

- **Type/Value**: `str`, currently `"1"`
- **Responsibility**: Identifies the version of the table layout defined in `_SCHEMA`, stored in the `meta` table so consumers can detect incompatible schema changes.
- **When to use**: Read via the `meta` table by any code that needs to verify database compatibility before querying.
- **Design decisions**: Kept as a manually-bumped string constant rather than derived automatically; must be incremented by hand whenever `_SCHEMA` changes.
- **Constraints & edge cases**: No validation logic exists in this file to enforce that a database's stored version matches this constant; that check is left to callers.

---

## `_SCHEMA`

- **Type/Value**: `str` containing a multi-statement SQL script (`CREATE TABLE`/`CREATE INDEX` statements for `meta`, `files`, `file_edges`, `definitions`).
- **Responsibility**: Single source of truth for the on-disk schema, executed once via `executescript` when building a new database.
- **When to use**: Applied exactly once per database creation, inside `save_consolidated_sqlite`.
- **Design decisions**:
  - `files.file` is the primary key, matching the `"project_name/copy_path"` format used throughout the project.
  - `file_edges` uses a composite primary key `(file, direction, other)` to naturally deduplicate edges and support `INSERT OR IGNORE`.
  - Indexes (`idx_file_edges_other`, `idx_definitions_name`, `idx_definitions_file`) are added to support the reverse-lookup and search query patterns used by `find_definitions` and edge queries.
- **Constraints & edge cases**: `definitions` has no primary key of its own (allows duplicate rows if source JSON contains duplicates); `summary`, `file_dependencies`, `doc`, and definition columns are nullable to represent missing per-file analysis artifacts.

---

## `_definition_rows(file_path: str, file_deps: dict) -> Iterator[tuple]`

- **Responsibility**: Converts one file's `file_dependencies` JSON structure into row tuples ready for bulk insertion into the `definitions` table.
- **When to use**: Called internally by `save_consolidated_sqlite` once per file that has a non-empty `file_dependencies` payload.
- **Design decisions**: Implemented as a generator to avoid materializing a full list when a file has many definitions, consistent with the file's stated one-file-at-a-time memory policy.
- **Constraints & edge cases**:
  - Expects `file_deps` to optionally contain a `"definitions"` list; missing key yields no rows.
  - Each definition dict's `name`, `type`, `start_line`, `end_line` are read with `.get()`, so any of them may be `None` without raising an error.
  - Does not validate types of `start_line`/`end_line` beyond passing through whatever the JSON provided.

---

## `save_consolidated_sqlite(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

- **Signature details**:
  - `base_output_dir: str` — base output directory; its basename becomes the project name.
  - `all_file_list: list[str]` — relative paths (from project root) of all files to include.
  - `output_path: str` — target `.sqlite` file path; overwritten if it exists.
  - `symbol_deps: dict[str, dict[str, set[str]]]` — per-file-relative-path map to `{"callers": set[str], "callees": set[str]}` (project-relative paths, pre-`to_output_path` conversion).
  - `summary_map: dict[str, str | None]` — per-file-relative-path map to an optional summary string.
  - Returns `None`.
- **Responsibility**: Builds (or rebuilds) the consolidated knowledge database from scratch by reading each file's per-file JSON artifacts one at a time and writing them into SQLite tables, plus recording the caller/callee edge graph.
- **When to use**: Invoked once per pipeline run after per-file analysis and symbol/summary aggregation are complete, to produce `project_knowledge.sqlite`.
- **Design decisions**:
  - Deletes any pre-existing file at `output_path` before writing, so the database is always fully rebuilt rather than incrementally updated — the per-file JSON files remain the single source of truth.
  - Processes `all_file_list` file-by-file (via `build_file_entry`) to insert `files` and `definitions` rows, keeping memory usage bounded to one file's data at a time rather than loading a full consolidated JSON.
  - Performs the `file_edges` insertion in a *separate* loop over `all_file_list` after all `files`/`definitions` rows are written, iterating both `"caller"` and `"callee"` directions independently and inserting them exactly as recorded in `symbol_deps` (no derivation of one direction from the other), matching the module docstring's note that callers/callees come from separate analyses.
  - Uses `INSERT OR IGNORE` for edges to tolerate duplicate entries without raising a primary-key violation.
  - Commits once at the end of all inserts inside a single connection/transaction, and always closes the connection in a `finally` block.
  - Logs a summary count (`written_count` vs. total) at `INFO` level after completion.
- **Constraints & edge cases**:
  - Files for which `build_file_entry` returns `None` (neither `file_dependencies.json` nor `doc.json` present) are silently skipped and not counted toward `written_count`.
  - `symbol_deps` is assumed to contain an entry for every path in `all_file_list`; a missing key would raise `KeyError`.
  - `summary_map.get(file_rel)` defaults to `None` if the file isn't present in the map, rather than raising.
  - JSON serialization of `file_dependencies`/`doc` uses `ensure_ascii=False`; falsy (e.g., empty dict) values are stored as `NULL` due to the truthiness check.

---

## `open_knowledge(path: str) -> sqlite3.Connection`

- **Responsibility**: Opens an existing knowledge database strictly for reading, with row access by column name.
- **When to use**: Called by any downstream consumer (CLI, query tools) that needs to read a previously built `project_knowledge.sqlite`.
- **Design decisions**: Opens the connection in SQLite URI read-only mode (`mode=ro`) to prevent accidental writes by readers, and sets `row_factory = sqlite3.Row` so callers can access columns by name (as used throughout this module's own query functions).
- **Constraints & edge cases**:
  - **Raises `FileNotFoundError`** if `path` does not exist, checked explicitly before attempting to open (since SQLite's URI read-only mode would otherwise fail with a less specific error).
  - Caller is responsible for closing the returned connection.

---

## `get_project_name(connection: sqlite3.Connection) -> str | None`

- **Responsibility**: Retrieves the analyzed project's name stored in the `meta` table.
- **When to use**: Called by consumers needing to display or verify which project a knowledge database corresponds to.
- **Constraints & edge cases**: Returns `None` if no `project_name` row exists in `meta` (e.g., corrupted or foreign database), rather than raising.

---

## `_row_to_entry(row: sqlite3.Row) -> dict`

- **Responsibility**: Reconstructs a consolidated JSON-like entry (`{"file", "file_dependencies"?, "doc"?}`) from a single `files` table row, decoding the JSON-text columns back into Python objects.
- **When to use**: Internal helper called by `iter_files` and `get_file` whenever a `files` row needs to be presented in the same shape as the original consolidated JSON.
- **Design decisions**: Omits `file_dependencies`/`doc` keys entirely (rather than setting them to `None`) when the corresponding column is `NULL`, mirroring how `build_file_entry` only adds those keys when the source JSON files exist.
- **Constraints & edge cases**: Assumes the row was produced by this module's own schema (i.e., `file_dependencies`/`doc` columns, when non-null, contain valid JSON).

---

## `iter_files(connection: sqlite3.Connection) -> Iterator[dict]`

- **Responsibility**: Streams every file's consolidated entry from the database in original insertion order, without loading the whole table into memory at once.
- **When to use**: Used by consumers that need to reprocess or export all per-file entries (e.g., regenerating a consolidated JSON view) without holding everything in memory simultaneously.
- **Design decisions**: Orders by `rowid` to preserve the original insertion order (i.e., the order of `all_file_list` at write time) since `files.file` is the declared primary key but not necessarily insertion-ordered.
- **Constraints & edge cases**: Is a generator — the underlying SQLite cursor stays open for the duration of iteration; the connection must remain open until iteration completes.

---

## `iter_dependencies(connection: sqlite3.Connection) -> Iterator[dict]`

- **Responsibility**: Streams a per-file summary/callers/callees view equivalent to the original consolidated JSON's `"project_dependencies"` list.
- **When to use**: Used by consumers needing the dependency-graph view of the project (summary plus edges) rather than the full file/doc content.
- **Design decisions**: For each file row, issues two additional queries (`callers_of`, `callees_of`) rather than a single joined query, trading extra round-trips for reuse of the existing single-purpose lookup functions.
- **Constraints & edge cases**: Like `iter_files`, is a generator that keeps a cursor open across the whole iteration; per-file edge queries add O(n) additional queries where n is the number of files.

---

## `get_file(connection: sqlite3.Connection, file: str) -> dict | None`

- **Responsibility**: Looks up a single file's consolidated entry by its exact `"project_name/copy_path"` key.
- **When to use**: Called when a consumer needs full detail (file_dependencies + doc) for one specific, already-known file path.
- **Constraints & edge cases**: Returns `None` when no row matches `file`; `file` must match the stored path format exactly (no partial/path-normalization matching is performed here).

---

## `callees_of(connection: sqlite3.Connection, file: str) -> list[str]`

- **Responsibility**: Returns the sorted list of files that `file` depends on (its callees), as recorded in `file_edges`.
- **When to use**: Called directly by consumers wanting only the outgoing dependency edges of a file, and internally by `iter_dependencies`.
- **Constraints & edge cases**: Returns an empty list (not `None`) when `file` has no recorded callee edges or does not exist in the database; sort order is guaranteed by the SQL `ORDER BY other`.

---

## `callers_of(connection: sqlite3.Connection, file: str) -> list[str]`

- **Responsibility**: Returns the sorted list of files recorded as depending on `file` (its callers), as recorded in `file_edges`.
- **When to use**: Called directly by consumers wanting only the incoming dependency edges of a file, and internally by `iter_dependencies`.
- **Design decisions**: Mirrors `callees_of` exactly except for the `direction` filter value, consistent with the module's design that caller/callee edges are independently recorded rather than derived from each other.
- **Constraints & edge cases**: Returns an empty list when there are no caller edges for `file`.

---

## `find_definitions(connection: sqlite3.Connection, name: str, partial: bool = False) -> list[dict]`

- **Responsibility**: Looks up definitions (functions/classes/etc.) by name across the whole project without needing to read any source file body, returning file/location metadata only.
- **When to use**: Called by consumers implementing a "go to definition" or symbol search feature over the aggregated knowledge base.
- **Design decisions**:
  - `partial=False` (default) performs an exact `WHERE name = ?` match.
  - `partial=True` performs a case-insensitive substring match using SQL `LIKE '%...%'`, with `%`, `_`, and `\` in the user-supplied `name` escaped (via `ESCAPE '\\'`) so that literal underscores/percent signs in symbol names aren't misinterpreted as SQL wildcards.
  - Results are always ordered by `(file, start_line)` for stable, file-grouped output regardless of match mode.
- **Constraints & edge cases**:
  - Case-insensitivity for partial search relies on SQLite's default `LIKE` behavior for ASCII; behavior for non-ASCII casing follows SQLite's built-in collation rules, not custom logic.
  - Returns an empty list when no definitions match.
  - Each result dict has keys `{"file", "name", "type", "start_line", "end_line"}`, any of which may be `None` if the source JSON lacked that field at insertion time.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/knowledge_db.py` → `codetwine/output.py` (`build_file_entry`): Used to read one file's `file_dependencies.json` and `doc.json` and consolidate them into a single entry dict (`{"file", "file_dependencies", "doc"}`), which `save_consolidated_sqlite` then writes into the `files` and `definitions` tables of the database.

- `codetwine/knowledge_db.py` → `codetwine/output.py` (`to_output_path`): Used to convert relative file paths into the canonical "project_name/copy_path" format, both when building `file_edges` rows (for the `file` and `other` columns) and when computing the file path used as the primary key in the `files` table.

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/knowledge_db.py` (`save_consolidated_sqlite`): The pipeline calls this function at the end of the whole-project analysis to build the consolidated SQLite knowledge database from the per-file JSON outputs, passing in the base output directory, the list of files, the target database path, the symbol-level dependency map, and the summary map.

### Dependency Direction

- The relationship between `codetwine/knowledge_db.py` and `codetwine/output.py` is **unidirectional**: `knowledge_db.py` depends on `output.py`'s helper functions (`build_file_entry`, `to_output_path`) to obtain per-file data and normalized paths; `output.py` does not depend on `knowledge_db.py`.
- The relationship between `codetwine/pipeline.py` and `codetwine/knowledge_db.py` is **unidirectional**: `pipeline.py` invokes `save_consolidated_sqlite` to trigger database generation; `knowledge_db.py` has no dependency back on `pipeline.py`.

# Data Flow

## 1. Inputs

`save_consolidated_sqlite` (the write path) receives:

- `base_output_dir: str` — base output directory; its basename is used as `project_name`.
- `all_file_list: list[str]` — relative paths (from project root) of every analyzed file.
- `output_path: str` — target filesystem path for the SQLite database file.
- `symbol_deps: dict[str, dict[str, set[str]]]` — per-file (relative path) map with `"callers"` and `"callees"` keys, each a set of relative paths.
- `summary_map: dict[str, str | None]` — relative path → summary text or `None`.

Indirectly, for each `file_rel` in `all_file_list`, `build_file_entry(base_output_dir, file_rel)` reads `file_dependencies.json` and `doc.json` from disk (JSON files, one per analyzed file) and returns a merged dict, or `None` if neither file exists.

The read path (`open_knowledge`, `get_project_name`, `iter_files`, `iter_dependencies`, `get_file`, `callers_of`, `callees_of`, `find_definitions`) takes:

- `path: str` — path to an existing SQLite database (must already exist).
- `connection: sqlite3.Connection` — an open connection (row factory `sqlite3.Row`).
- Query parameters: `file: str` (a `"project_name/copy_path"` string), `name: str` (a definition name), `partial: bool`.

## 2. Transformation Overview

**Write pipeline (`save_consolidated_sqlite`):**

1. **Setup** — Derive `project_name` from `base_output_dir`. Remove any pre-existing database at `output_path`. Open a new SQLite connection and run `_SCHEMA` to create `meta`, `files`, `file_edges`, `definitions` tables/indexes.
2. **Meta insertion** — Insert `project_name`, `schema_version`, and a UTC `created_at` timestamp into `meta`.
3. **Per-file ingestion loop** — For each `file_rel` in `all_file_list`:
   - Call `build_file_entry` to load that file's `file_dependencies` and `doc` JSON (or skip if `None`).
   - Insert one row into `files`: `file` path, `summary` from `summary_map`, `file_dependencies`/`doc` serialized to JSON text (or `NULL` when absent).
   - If `file_dependencies` is present, derive its `definitions` list via `_definition_rows` and bulk-insert into `definitions` (one row per definition: file, name, type, start_line, end_line).
   - Track `written_count`.
4. **Edge ingestion loop** — For each `file_rel`, convert to output-path form via `to_output_path`, then for each `direction` in `("caller", "callee")`, take `symbol_deps[file_rel][direction+"s"]` (a set of relative paths), convert each `other` path via `to_output_path`, sort, and bulk `INSERT OR IGNORE` into `file_edges` (avoiding duplicate `(file, direction, other)` triples).
5. **Commit and close** — Commit the transaction, close the connection, log a summary line with counts.

Both loops read source data file-by-file and release it before moving to the next, so at most one file's JSON content is held in memory at a time; the whole project is never assembled in memory.

**Read pipeline (query functions):**

1. `open_knowledge` opens a read-only connection with `sqlite3.Row` row factory.
2. Each accessor issues a targeted SQL query (`SELECT` with `WHERE`, ordering) against `files`, `file_edges`, or `definitions`.
3. `_row_to_entry` reconstructs a consolidated JSON-shaped entry from a `files` row, JSON-decoding `file_dependencies`/`doc` text columns back into dicts when present.
4. `iter_files` streams all rows from `files` (ordered by `rowid`) through `_row_to_entry`, yielding one entry at a time (generator, no full materialization).
5. `iter_dependencies` streams `(file, summary)` pairs from `files` and, for each, calls `callers_of`/`callees_of` to attach caller/callee lists, yielding one merged dict per file.
6. `callers_of`/`callees_of` each query `file_edges` filtered by `file` and `direction`, returning sorted lists of `other` paths.
7. `find_definitions` builds a `LIKE`-based (partial, case-insensitive, escaped) or exact-match query against `definitions`, returning matching rows as plain dicts.

## 3. Outputs

- **Side effect:** A SQLite database file written/overwritten at `output_path`, containing tables `meta`, `files`, `file_edges`, `definitions` (schema as defined in `_SCHEMA`).
- **Side effect:** A log line reporting `written_count` vs. total files in `all_file_list`.
- **Return values from read functions:**
  - `open_knowledge` → `sqlite3.Connection` (read-only, `Row` factory).
  - `get_project_name` → `str | None`.
  - `iter_files` → `Iterator[dict]`, each `{"file": str, "file_dependencies"?: dict, "doc"?: dict}`.
  - `iter_dependencies` → `Iterator[dict]`, each `{"file": str, "summary": str|None, "callers": list[str], "callees": list[str]}`.
  - `get_file` → `dict | None` (same shape as one `iter_files` element).
  - `callees_of` / `callers_of` → `list[str]` (sorted output-format file paths).
  - `find_definitions` → `list[dict]`, each `{"file": str, "name": str, "type": str|None, "start_line": int|None, "end_line": int|None}`.

## 4. Key Data Structures

**`files` table row / consolidated entry (returned by `_row_to_entry`, `get_file`, `iter_files`)**

| Field / Key | Type | Purpose |
|---|---|---|
| file | str | Primary key, `"project_name/copy_path"` identifier for the file |
| summary | str \| None | Doc summary text, or `None` when absent |
| file_dependencies | dict (JSON-decoded) | Parsed `file_dependencies.json` body (present only if the source JSON existed) |
| doc | dict (JSON-decoded) | Parsed `doc.json` body (present only if the source JSON existed) |

**`file_edges` table row**

| Field / Key | Type | Purpose |
|---|---|---|
| file | str | The file the edge is recorded from |
| direction | str ("caller" \| "callee") | Which relation this row records, as originally analyzed |
| other | str | The related file's path, in output format |

**`definitions` table row (also returned by `find_definitions`)**

| Field / Key | Type | Purpose |
|---|---|---|
| file | str | File the definition belongs to |
| name | str | Definition name |
| type | str \| None | Definition type/kind |
| start_line | int \| None | Starting line number |
| end_line | int \| None | Ending line number |

**`meta` table row**

| Field / Key | Type | Purpose |
|---|---|---|
| key | str | Metadata key (`project_name`, `schema_version`, `created_at`) |
| value | str | Corresponding metadata value |

**`symbol_deps` (input, per-file)**

| Field / Key | Type | Purpose |
|---|---|---|
| callers | set[str] | Relative paths of files that call into this file |
| callees | set[str] | Relative paths of files this file calls into |

**`iter_dependencies` yielded dict**

| Field / Key | Type | Purpose |
|---|---|---|
| file | str | File path in output format |
| summary | str \| None | File summary |
| callers | list[str] | Sorted list of files depending on this file |
| callees | list[str] | Sorted list of files this file depends on |

# Error Handling

### 1. Overall Strategy

This file uses almost no explicit exception handling of its own. It follows a **fail-fast** approach for structural/IO failures (schema creation, SQLite writes, JSON parsing) and lets exceptions propagate to the caller uncaught, relying on the `try/finally` around the connection only to guarantee the database handle is closed. The one exception is per-file skipping during database construction: when a file's analysis artifacts are missing, that file is logged and skipped (**logging-and-continue**) rather than aborting the whole run. Read-side lookup functions treat "no matching row" as a normal, expected outcome (returning `None` or an empty list) rather than an error condition, and `open_knowledge` explicitly fails fast with a typed exception when the database file itself does not exist.

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing per-file analysis artifacts | `build_file_entry` returns `None` for a given `file_rel` (neither `file_dependencies.json` nor `doc.json` found) | The file is skipped via `continue`; a warning is logged inside `build_file_entry`; `written_count` is not incremented | Yes (skipped, processing continues) | That file is absent from the `files`/`definitions` tables; final log line reports reduced `written_count` vs. total file count |
| Database file already exists at output path | `os.path.exists(output_path)` is True before writing | Existing file is removed with `os.remove` before creating a fresh connection | Yes (handled proactively) | Ensures a clean rebuild; no stale data mixed with new run |
| SQLite write/schema errors (e.g. disk I/O failure, executescript/execute/executemany failure) | Any failure during `executescript`, `execute`, or `executemany` calls inside `save_consolidated_sqlite` | Not caught; exception propagates up; `finally` block still closes the connection | No (process terminates / propagates to caller) | Database may be left incomplete or absent; caller (`pipeline.py`) receives the exception |
| Duplicate `file_edges` rows | Same `(file, direction, other)` tuple inserted more than once (e.g. from re-processing symbol_deps) | `INSERT OR IGNORE` silently discards the duplicate row | Yes (no error raised) | No duplicate edges; data integrity preserved without failing the run |
| Missing knowledge database file on open | `open_knowledge(path)` called when `path` does not exist | Raises `FileNotFoundError` explicitly before attempting to connect | No (explicit, intentional failure) | Caller must handle the exception; no silent fallback |
| Malformed JSON in stored `file_dependencies`/`doc` columns | `json.loads` fails while reconstructing an entry in `_row_to_entry` | Not caught; exception propagates | No | `iter_files`, `get_file`, and `iter_dependencies` calls fail for that row |
| No matching row for a lookup (`get_file`, `find_definitions`, `callers_of`, `callees_of`, `get_project_name`) | Query returns zero rows | Explicit `if row else None` / list comprehension yields empty result | Yes (treated as normal "not found" case) | Caller receives `None` or an empty list, not an exception |

### 3. Design Notes

- The database is treated as fully derivable and disposable: since it is always rebuilt from the per-file JSON files (`os.remove` + `executescript`), there is no need for incremental error recovery or transactional rollback logic beyond SQLite's own default transaction semantics — a single `commit()` is issued at the end, so a mid-write failure naturally leaves no partial commit.
- Per-file skipping (`build_file_entry` returning `None`) is the only place where a "soft" error is absorbed, reflecting the reality that some files may lack analysis output without invalidating the whole project database; the skipped/total count is surfaced in the final log message for visibility rather than raised as an error.
- `INSERT OR IGNORE` for `file_edges` is a deliberate idempotency mechanism given the primary key constraint, rather than an error-catching mechanism — it avoids needing conflict-handling logic for edges that may be discovered redundantly from `symbol_deps`.
- Read-side functions distinguish "absence of data" (empty result, `None`) from "corruption/misuse" (propagated exceptions like `FileNotFoundError` or JSON decode errors), keeping the read API simple for callers who only need to check for `None`/empty list in normal operation.

# Summary

Builds/queries a SQLite knowledge DB mirroring project_knowledge.json. save_consolidated_sqlite(base_output_dir:str, all_file_list:list[str], output_path:str, symbol_deps:dict[str,dict[str,set[str]]], summary_map:dict[str,str|None])->None rebuilds it file-by-file. Read functions: open_knowledge(path:str)->Connection, get_project_name, iter_files/iter_dependencies (Iterator[dict]), get_file(file:str)->dict|None, callees_of/callers_of(file:str)->list[str], find_definitions(name:str,partial:bool)->list[dict]. Tables: meta, files, file_edges, definitions.
