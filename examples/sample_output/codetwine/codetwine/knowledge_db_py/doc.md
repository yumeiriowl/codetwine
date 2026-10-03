# Design Document: codetwine/knowledge_db.py

# Design Specification

**Overview**

Build and query a SQLite database containing consolidated project-wide code analysis results including file dependencies, definitions, and documentation.

This file is used to:
- Call `save_consolidated_sqlite()` from the analysis pipeline to write the entire project's analysis results into a SQLite database, replacing the previous database atomically.
- Call `open_knowledge()` to open an existing knowledge database for read-only querying.
- Call `get_project_name()` to retrieve the analyzed project's name from database metadata.
- Call `iter_dependencies()` or `iter_files()` to stream file dependency and documentation entries from the database one at a time.
- Call `get_file()`, `callees_of()`, `callers_of()`, or `find_definitions()` to query specific files, their dependencies, or definitions by name.

The file depends on `codetwine/output.py` for `build_file_entry()` to read per-file analysis results from disk and `to_output_path()` to standardize file paths into the "project_name/copy_path" format. The file is used by `codetwine/pipeline.py` to save analysis results and by `examples/rlm_qa/knowledge_store.py` as a query interface to the database for accessing file entries, dependencies, project metadata, and definition locations.

The database is written to a temporary file and atomically moved to the final location only after all writes succeed, ensuring an existing database is never left in a corrupted or incomplete state. The schema includes tables for file metadata, file-level dependencies, and symbol-level definitions, with indexes on the definitions table for efficient name-based lookups.

**Definitions**

## `SCHEMA_VERSION`

Constant string identifying the database schema version; stored in the meta table and used for compatibility checking when opening an existing database.

## `_SCHEMA`

SQL text defining the complete database schema, including five tables (meta, files, file_edges, definitions) and indexes for efficient querying by file, direction, and definition name.

## `_iter_definition_row`

Generator that yields tuples of (file, name, type, start_line, end_line) extracted from a file's "definitions" list in file_dependencies, one tuple per definition to be inserted into the definitions table.

## `save_consolidated_sqlite`

Reads per-file analysis results from disk using `build_file_entry()`, writes them to a SQLite database at a temporary path along with file-level and symbol-level dependency edges, and atomically moves the temporary database to the final output path. Logs the count of successfully written files. Called during the analysis pipeline to persist consolidated knowledge to disk; the database replaces any existing database at the output path.

## `open_knowledge`

Opens a knowledge database at a given path for read-only access, raising FileNotFoundError if the path does not exist. Returns a connection with row factory set to sqlite3.Row so result rows behave like dictionaries.

## `get_project_name`

Queries the meta table for the "project_name" key and returns its value, or None if the key is not present; used by consuming code to identify which project the database analyzes.

## `_row_to_entry`

Converts one row from the files table back into a consolidated entry dict with "file" as a required key and "file_dependencies" and "doc" as optional keys parsed from JSON text, reconstructing the structure produced by `build_file_entry()`.

## `iter_files`

Yields every file entry from the files table in insertion order, with each entry reconstructed to match the structure of consolidated JSON "files" elements, allowing streaming reads without loading the entire database into memory.

## `iter_dependencies`

Yields one dict per file in insertion order, containing "file", "summary", "callers" (list of dependent file paths), and "callees" (list of dependency target file paths), matching the structure of consolidated JSON "project_dependencies" elements and querying both file_edges directions for each file.

## `get_file`

Retrieves a single file's consolidated entry (file, file_dependencies, doc) from the files table by exact file path match, returning None if the file is not in the database.

## `_edge_list`

Queries the file_edges table for all edges from a given file in a given direction (caller or callee), returning the file paths at the other end sorted alphabetically; used internally by `callees_of()` and `callers_of()`.

## `callees_of`

Returns the sorted list of file paths that a given file depends on, queried from the file_edges table with direction "callee".

## `callers_of`

Returns the sorted list of file paths that depend on a given file, queried from the file_edges table with direction "caller".

## `find_definitions`

Searches the definitions table for definitions matching a given name, supporting exact name matching or partial case-insensitive matching with the partial parameter. Returns a list of dicts with "file", "name", "type", "start_line", and "end_line" keys, sorted by file and line number; escapes literal % and _ characters in partial searches to match them literally.

# Summary

# Summary of codetwine/knowledge_db.py

**Single Responsibility**
Build, persist, and query a SQLite database of consolidated project-wide code analysis results, providing atomic writes and efficient lookups of files, dependencies, and symbol definitions.

**Main Public Definitions**
`save_consolidated_sqlite()`, `open_knowledge()`, `get_project_name()`, `iter_files()`, `iter_dependencies()`, `get_file()`, `callees_of()`, `callers_of()`, `find_definitions()`.

**Key Terms**
SQLite database schema with tables for file metadata, file-level dependencies (edges), and symbol-level definitions; atomic temporary-file writes; read-only database access; row factory for dict-like result rows; streaming iteration without full in-memory loading; indexed definitions table for efficient name-based searches; exact and partial case-insensitive definition matching; dependency direction tracking (caller/callee).
