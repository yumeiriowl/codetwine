# Design Document: codetwine/knowledge_db.py

# Design Specification

**Overview**

Persist whole-project code analysis results into a SQLite database for efficient querying and cross-file dependency traversal.

This file is used to:
- `save_consolidated_sqlite()` writes all per-file analysis data (dependencies, definitions, documentation) into a SQLite database with indexed tables for fast lookups
- `open_knowledge()` establishes a read-only connection to an existing knowledge database for downstream consumers
- `get_project_name()` retrieves the analyzed project's identifier from database metadata
- `iter_files()`, `iter_dependencies()`, `get_file()` retrieve consolidated file entries and their dependency relationships
- `find_definitions()` searches for symbol definitions by name with exact or partial matching
- `callees_of()`, `callers_of()` query file-level dependency edges in both directions

The file depends on `codetwine/output.py` to read per-file analysis results via `build_file_entry()` and convert file paths to the normalized "project_name/copy_path" format via `to_output_path()`. Pipeline orchestration in `codetwine/pipeline.py` calls `save_consolidated_sqlite()` to build the database as part of consolidation. Examples in `examples/rlm_qa/knowledge_store.py` use the public query functions to access project knowledge for retrieval-augmented generation workflows.

The database is written atomically to a temporary file then moved to the final location, ensuring existing databases are never corrupted if the write fails. All file and definition data is stored as JSON text within the database rows rather than normalized relational tables, allowing flexible schema evolution. Definition lookups use SQL LIKE patterns with escape handling for partial matching, and file edges use compound primary keys to prevent duplicate dependency records.

**Definitions**

## `SCHEMA_VERSION`

String constant tracking the current table layout version, stored in the meta table to detect schema compatibility when reading existing databases.

## `_SCHEMA`

SQL DDL string defining five tables: meta (project metadata), files (one row per analyzed file with embedded JSON), file_edges (caller/callee relationships with indexed lookups), and definitions (symbol definitions indexed by name and file for search). Executed once when initializing a new database.

## `_iter_definition_row()`

Yields tuples of (file_path, name, type, start_line, end_line) extracted from the "definitions" array within a file's file_dependencies object, used to bulk-insert rows into the definitions table during database creation.

## `save_consolidated_sqlite()`

Reads per-file analysis results from the output directory and writes them into a new SQLite database, replacing any existing database atomically via temporary file and move. Iterates through all_file_list, loads each file's entry via `build_file_entry()`, inserts consolidated data and indexed definitions, builds file_edges from symbol_deps dependency information, and logs the count of successfully written files. Cleans up any leftover temporary files from interrupted previous runs.

## `open_knowledge()`

Opens an existing SQLite database at the given path in read-only mode, raising FileNotFoundError if absent, and configures rows to return as sqlite3.Row objects for named column access.

## `get_project_name()`

Queries the meta table for the "project_name" key and returns its value, or None if the key is absent.

## `_row_to_entry()`

Converts a files table row back into the consolidated entry structure by parsing JSON text columns (file_dependencies, doc) and returning a dict with "file" plus those keys when present.

## `iter_files()`

Yields every file entry in insertion order by selecting all rows from the files table, deserializing JSON columns, and returning consolidated dicts with the same structure as the JSON's "files" array.

## `iter_dependencies()`

Yields file summary and dependency information in insertion order by reading each files row and querying file_edges to construct "callers" and "callees" lists, producing dicts matching the consolidated JSON's "project_dependencies" structure.

## `get_file()`

Retrieves a single file's consolidated entry (with file_dependencies and doc) by exact file path match, returning None if not found.

## `_edge_list()`

Queries file_edges for all edges of a given direction (caller or callee) from a specific file, returning the sorted list of opposite-end file paths.

## `callees_of()`

Returns the sorted list of files that the given file depends on, obtained by querying file_edges with direction "callee".

## `callers_of()`

Returns the sorted list of files that depend on the given file, obtained by querying file_edges with direction "caller".

## `find_definitions()`

Searches the definitions table for symbols by name, supporting exact matching (default) or case-insensitive partial matching via SQL LIKE with escaped wildcards, and returns results sorted by file and line number as dicts containing file, name, type, start_line, and end_line.

# Summary

# Summary: codetwine/knowledge_db.py

**Single Responsibility**
Persists whole-project code analysis into a SQLite database and provides efficient query access to files, dependencies, definitions, and cross-file relationships.

**Main Public Definitions**
- `save_consolidated_sqlite()` — writes analysis results to database atomically
- `open_knowledge()` — opens read-only connection to existing database
- `get_project_name()` — retrieves project identifier from metadata
- `iter_files()`, `iter_dependencies()`, `get_file()` — retrieve file entries and relationships
- `find_definitions()` — search symbols by name with exact or partial matching
- `callees_of()`, `callers_of()` — query dependency edges in both directions

**Key Terms**
SQLite persistence, indexed tables, JSON storage, symbol definitions, file dependencies, dependency traversal, atomic writes, schema versioning, named column access, partial matching with escape handling.
