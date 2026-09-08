# Design Document: examples/rlm_qa/knowledge_store.py

# Design Specification

**Overview**

Provides a uniform read-only interface for accessing a codetwine knowledge file, regardless of whether it is stored as JSON or SQLite.

- Use `open_store` when a knowledge file path is known but its format (`.json` or `.sqlite`) may vary, to obtain a ready-to-use store instance.
- Use `dependencies()` on a store to get the project-wide file graph (summaries, callers, callees) for building an overview or dependency map.
- Use `entry(file)` to fetch a single file's definitions, usages and design document when answering a question scoped to one file.
- Use `iter_entries()` to walk every file's entry, one at a time, for project-wide scans without loading everything at once.
- Use `find_definitions(name, partial)` to locate where a symbol (function, class, etc.) is defined across the whole project, supporting exact or substring/partial matching.

This file relies on `codetwine.knowledge_db` for all SQLite-backed operations (opening the connection, reading the project name, iterating dependencies and files, fetching a single file's entry, and searching definitions); it performs no such logic itself for the SQLite case. It is used by `rlm_qa_agent.py`, which calls `open_store` to instantiate the correct store from a knowledge file path and then uses the resulting `Store` (either `JsonStore` or `SqliteStore`) to build documentation schemas and answer agent queries, treating both implementations interchangeably through the shared `Store` type alias.

Design decisions worth knowing: `JsonStore` eagerly loads and holds the entire knowledge file in memory and indexes file entries by path (`_by_file`) for O(1) lookup in `entry()`, trading memory for fast repeated access; `SqliteStore` instead queries the database per call, trading per-call latency for a small memory footprint. Both stores expose identical method signatures so callers can select an implementation purely by file extension without branching on the underlying storage.

**Definitions**

## `JsonStore`
A store implementation that loads a `project_knowledge.json` file entirely into memory on construction, parsing project name, dependency graph, and per-file entries; used when the knowledge file is not a `.sqlite` file. It builds an internal `_by_file` dictionary keyed by file path to support fast `entry()` lookups.

## `JsonStore.__init__`
Opens and parses the JSON knowledge file at the given path, extracts `project_name`, computes `base_dir` from the path's directory, and indexes the `files` list into `_by_file` by each entry's `"file"` key; called once when a JSON-backed store is created.

## `JsonStore.dependencies`
Returns the `project_dependencies` list straight from the loaded JSON data, each entry containing `"file"`, `"summary"`, `"callers"` and `"callees"`; used to retrieve the whole-project file graph and summaries without touching per-file details.

## `JsonStore.entry`
Looks up and returns a single file's entry (`"file"`, `"file_dependencies"`, `"doc"`) from the in-memory `_by_file` index by exact file path match, returning `None` if the file is not present; used when a caller needs one file's definitions, usages and design document.

## `JsonStore.iter_entries`
Yields every file entry from the loaded `files` list one at a time via an iterator; used for sequential, whole-project traversal without requiring random access.

## `JsonStore.find_definitions`
Scans every file's `file_dependencies.definitions` list, matching each definition's `name` against the target either by exact equality or case-insensitive substring (`partial=True`), and returns matches as `{"file", "name", "type", "start_line", "end_line"}`; used to locate all places in the project where a given symbol is defined.

## `JsonStore.close`
Clears the in-memory `_data` and `_by_file` structures to release the loaded JSON content; exists so callers can treat `JsonStore` and `SqliteStore` interchangeably with a uniform cleanup call, even though there is no real connection to close.

## `SqliteStore`
A store implementation backed by a `project_knowledge.sqlite` database, querying it per call rather than loading it into memory; used when the knowledge file path ends in `.sqlite`. It delegates all data access to `codetwine.knowledge_db`.

## `SqliteStore.__init__`
Opens a connection to the SQLite knowledge database via `knowledge_db.open_knowledge`, retrieves the project name via `knowledge_db.get_project_name`, and computes `base_dir` from the path's directory; called once when a SQLite-backed store is created, holding only the open connection afterward.

## `SqliteStore.dependencies`
Retrieves the project-wide file graph and summaries by calling `knowledge_db.iter_dependencies` and materializing the result into a list; used to get the same `{"file", "summary", "callers", "callees"}` data as `JsonStore.dependencies` but queried live from the database.

## `SqliteStore.entry`
Fetches a single file's `{"file", "file_dependencies", "doc"}` entry by delegating to `knowledge_db.get_file`, returning `None` if the file is not found; used when a caller needs one file's details without reading the whole database.

## `SqliteStore.iter_entries`
Delegates to `knowledge_db.iter_files` to yield file entries one at a time directly from the database, avoiding loading the entire project into memory; used for project-wide sequential scans on large knowledge bases.

## `SqliteStore.find_definitions`
Delegates to `knowledge_db.find_definitions`, passing through the `name` and `partial` matching flag, to search the database for definitions matching a symbol name; used identically to `JsonStore.find_definitions` but executed as a database query.

## `SqliteStore.close`
Closes the underlying SQLite connection to release the database file handle; used for proper cleanup when a `SqliteStore` is no longer needed.

## `Store`
A type alias (`JsonStore | SqliteStore`) representing either concrete store implementation; used in type hints (for example by `rlm_qa_agent.py`) so calling code can accept a knowledge store without depending on which backend format is in use.

## `open_store`
Selects and instantiates the appropriate store class based on the file extension of the given path, returning a `SqliteStore` for paths ending in `.sqlite` and a `JsonStore` otherwise; this is the single entry point callers use to open a knowledge file without needing to know or check its format themselves.

# Summary

Provides a unified, format-agnostic read-only interface to codetwine knowledge files (JSON or SQLite), letting callers query project dependencies, per-file entries, and symbol definitions without knowing the storage backend. Defines JsonStore (loads JSON fully into memory, indexed for fast lookup) and SqliteStore (queries codetwine.knowledge_db per call), unified by the Store type alias, plus open_store, which picks the right implementation by file extension. Key concepts: knowledge base access, dependency graphs, file entries, definition search, exact/partial matching, memory-vs-latency tradeoffs, uniform interface, cleanup via close.
