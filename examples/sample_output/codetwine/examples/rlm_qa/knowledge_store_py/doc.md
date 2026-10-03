# Design Document: examples/rlm_qa/knowledge_store.py

# Design Specification

**Overview**

Provide a unified interface to query project knowledge from either JSON or SQLite storage formats, presenting the same methods to retrieve file dependencies, individual file entries, and symbol definitions.

- Call `open_store()` with a knowledge file path to obtain a store instance that automatically selects between JSON and SQLite backends based on file extension.
- Call `dependencies()` on a store to retrieve the project's file graph with summaries and caller/callee relationships for all files.
- Call `entry()` with a file path to retrieve that file's definitions, usages, and design document.
- Call `iter_entries()` to stream through all files without loading the entire knowledge base into memory at once.
- Call `find_definitions()` with a symbol name to locate all definitions matching exactly or partially (case-insensitive), across all files.

This file bridges `rlm_qa_agent.py` and the `knowledge_db` module: it wraps `knowledge_db`'s SQLite query functions (open_knowledge, get_project_name, iter_dependencies, get_file, iter_files, find_definitions) into a consistent interface, while also supporting direct JSON file parsing as an alternative backend. The `rlm_qa_agent` module uses `open_store()` to initialize storage and calls `Store` methods to populate project metadata and extract documentation schemas.

The two store implementations maintain memory/speed tradeoffs: `JsonStore` loads the entire knowledge file into memory for fast repeated access but consumes more RAM, while `SqliteStore` queries the database per request without holding the full dataset in memory. Both stores provide a `close()` method to release resources, ensuring consistent cleanup semantics across backends.

**Definitions**

## `JsonStore`

A knowledge store that loads a `project_knowledge.json` file entirely into memory and serves queries by searching in-memory dictionaries. Used when the knowledge source is JSON format; supports fast repeated lookups at the cost of full file loading.

## `JsonStore.__init__`

Reads the JSON knowledge file from disk, extracts the project name and base directory, and builds an index dictionary mapping file paths to their entry objects for O(1) lookup by `entry()`.

## `JsonStore.dependencies`

Returns the project-level file graph stored in the "project_dependencies" key, containing one entry per file with "file", "summary", "callers", and "callees" fields.

## `JsonStore.entry`

Looks up and returns a single file's entry by file path from the in-memory index, or None if not found; the entry contains "file", "file_dependencies", and "doc" keys.

## `JsonStore.iter_entries`

Yields each file entry from the "files" array one at a time, allowing streaming iteration without additional memory overhead beyond the already-loaded JSON.

## `JsonStore.find_definitions`

Searches all file entries' definitions for matches on name, supporting exact matching or case-insensitive partial matching; returns results as a list of dicts with "file", "name", "type", "start_line", and "end_line" keys.

## `JsonStore.close`

Clears the in-memory dictionaries to release memory; present for API consistency with `SqliteStore`.

## `SqliteStore`

A knowledge store backed by a `project_knowledge.sqlite` database, querying per file without holding the full dataset in memory. Used when the knowledge source is SQLite format; supports memory-efficient access for large codebases.

## `SqliteStore.__init__`

Opens a read-only SQLite database connection using `knowledge_db.open_knowledge()`, retrieves the project name from the database metadata, and stores the base directory; the connection is kept open for subsequent queries.

## `SqliteStore.dependencies`

Queries and returns the dependency graph by calling `knowledge_db.iter_dependencies()`, converting the iterator to a list; includes one entry per file with "file", "summary", "callers", and "callees" fields.

## `SqliteStore.entry`

Queries the database for a single file's entry by path using `knowledge_db.get_file()`, returning a consolidated entry or None; the entry contains "file", "file_dependencies", and "doc" keys.

## `SqliteStore.iter_entries`

Delegates to `knowledge_db.iter_files()` to yield file entries from the database in insertion order, streaming results without loading all files into memory.

## `SqliteStore.find_definitions`

Delegates to `knowledge_db.find_definitions()` to search the definitions table for exact or partial case-insensitive name matches, returning results ordered by file and start line.

## `SqliteStore.close`

Closes the SQLite database connection to release system resources.

## `Store`

A union type alias representing either `JsonStore` or `SqliteStore`, indicating that callers can use either implementation interchangeably through their common interface.

## `open_store`

Factory function that examines the knowledge file path extension and returns a `SqliteStore` for ".sqlite" paths or a `JsonStore` otherwise, providing transparent format detection and instantiation.

# Summary

# Summary: knowledge_store.py

This module provides a unified interface for querying project knowledge from either JSON or SQLite storage formats. Its single responsibility is to abstract away backend differences, allowing callers to use identical methods regardless of storage type.

Main public definitions are `open_store()` factory function, the `Store` union type, and two store implementations: `JsonStore` (loads entire JSON file into memory for fast lookup) and `SqliteStore` (queries SQLite database per request for memory efficiency).

Key capabilities include retrieving project-level file dependency graphs, looking up individual file entries with definitions and usages, streaming through files without loading everything at once, and searching for symbol definitions across the codebase with exact or case-insensitive partial matching. Both stores provide consistent `close()` methods for resource cleanup.
