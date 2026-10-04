# Design Document: examples/rlm_qa/knowledge_store.py

# Design Specification

**Overview**

Provide a unified interface to query code analysis knowledge from either JSON or SQLite storage formats, allowing callers to retrieve file summaries, definitions, dependencies, and design documentation without knowing the underlying storage mechanism.

- Call `open_store()` with a knowledge file path to obtain a store instance that automatically selects the appropriate backend based on file extension.
- Call `dependencies()` on a store to retrieve the complete file dependency graph with summaries for all files in the project.
- Call `entry()` with a file path to fetch detailed information about a single file, including its definitions, usages, and design documentation.
- Call `iter_entries()` to iterate through all files in the project one at a time without loading everything into memory at once.
- Call `find_definitions()` with a symbol name to locate all definitions across the project, supporting both exact matching and partial case-insensitive search.

The file depends on `codetwine/knowledge_db.py` to query a SQLite knowledge database when a `.sqlite` path is provided; it wraps those database functions in the `SqliteStore` class. The file is used by `examples/rlm_qa/rlm_qa_agent.py`, which calls `open_store()` to initialize a store, accesses `project_name`, and invokes `iter_entries()` to extract documentation sections for agent instructions.

Both `JsonStore` and `SqliteStore` implement identical method signatures and return the same data structures, allowing them to be used interchangeably; `JsonStore` loads the entire knowledge file into memory on initialization, while `SqliteStore` queries the database per request, trading memory efficiency for query efficiency depending on access patterns.

**Definitions**

## `JsonStore`

A knowledge store backed by a project knowledge JSON file held entirely in memory. It loads the JSON at initialization, indexes file entries by path for fast lookup, and answers queries by iterating or indexing its in-memory dictionary. Use this store when the knowledge file is small enough to fit in memory and rapid lookups are needed without database overhead.

## `JsonStore.__init__`

Load a project knowledge JSON file into memory, extract the project name, establish the base directory, and build an index mapping file paths to their entries for efficient lookup. Called once when opening a JSON-backed store.

## `JsonStore.dependencies`

Return the project dependency graph as a list of summary objects, one per file, containing file path, summary text, and lists of callers and callees. Extracted directly from the `project_dependencies` key in the loaded knowledge dictionary.

## `JsonStore.entry`

Retrieve the detailed entry for a single file, containing its definitions, usages, and design documentation, or return None if the file does not exist in the knowledge base. Uses the indexed file dictionary for constant-time lookup.

## `JsonStore.iter_entries`

Yield each file's entry one at a time from the loaded knowledge dictionary without loading additional data. Allows iteration through the entire project without keeping entries in separate memory beyond what was loaded at initialization.

## `JsonStore.find_definitions`

Search for all definitions matching a given name, optionally using partial case-insensitive matching, and return results as a list of dictionaries containing file path, definition name, type, and line numbers. Iterates through all file entries and their definitions to locate matches.

## `JsonStore.close`

Clear the in-memory knowledge dictionary and entry index to release memory. Provided for interface compatibility with `SqliteStore` so both stores are closed the same way.

## `SqliteStore`

A knowledge store backed by a project knowledge SQLite database queried on demand. It maintains only a connection to the database and delegates queries to functions in `knowledge_db`, retrieving results as needed without holding the entire database in memory. Use this store for large knowledge bases where on-demand querying is more efficient than loading everything at initialization.

## `SqliteStore.__init__`

Open a read-only connection to a project knowledge SQLite database using `knowledge_db.open_knowledge()`, retrieve and store the project name, and record the base directory from the database file path. Called once when opening a SQLite-backed store.

## `SqliteStore.dependencies`

Return the project dependency graph by querying the database via `knowledge_db.iter_dependencies()`, yielding summary objects with file path, summary text, callers, and callees. Each call queries the database afresh.

## `SqliteStore.entry`

Retrieve the detailed entry for a single file from the database via `knowledge_db.get_file()`, containing its definitions, usages, and design documentation, or return None if not found. Queries the database only for the requested file.

## `SqliteStore.iter_entries`

Yield each file's entry one at a time from the database via `knowledge_db.iter_files()` without loading all entries into memory simultaneously. Allows full project iteration with constant memory usage relative to file count.

## `SqliteStore.find_definitions`

Search the database for all definitions matching a given name using exact or partial case-insensitive matching via `knowledge_db.find_definitions()`, returning results as a list of dictionaries with file path, definition name, type, and line numbers. Delegates pattern matching and escaping to the database layer.

## `SqliteStore.close`

Close the database connection. Called to release the connection resource when the store is no longer needed.

## `Store`

A type alias representing either a `JsonStore` or `SqliteStore`. Used to indicate that a function accepts or returns a store without specifying which implementation is required.

## `open_store`

Inspect the file extension of a knowledge file path and return a `SqliteStore` for `.sqlite` paths or a `JsonStore` for all other paths. This is the sole entry point for creating a store, ensuring the correct backend is selected based on the file format.

# Summary

# knowledge_store.py Summary

This module provides a unified interface to query code analysis knowledge from JSON or SQLite storage backends. The `open_store()` function selects the appropriate store type based on file extension, returning either `JsonStore` (in-memory JSON) or `SqliteStore` (on-demand database queries). Both implementations expose identical methods: `dependencies()` retrieves the project dependency graph, `entry()` fetches details for a single file, `iter_entries()` streams through all files, and `find_definitions()` searches for symbol definitions across the project. The module handles file summaries, definitions, usages, design documentation, and dependency graphs, allowing callers to access code analysis knowledge without knowing the underlying storage mechanism.
