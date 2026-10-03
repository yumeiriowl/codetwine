# Design Document: codetwine/output.py

# Design Specification

**Overview**

Consolidate and output the project's analyzed code dependencies and documentation into multiple formats (JSON, Mermaid graph) for knowledge base and visualization purposes.

This file is used when:
- `save_consolidated_json()` is called to generate a complete project knowledge file combining each file's dependency information and design document into a single JSON.
- `save_dependency_summary()` is called to output a lightweight JSON with symbol-level dependencies and summaries for each analyzed file.
- `save_dependency_graph_as_mermaid()` is called to generate a Mermaid flowchart visualization of the file-level dependency graph.
- `build_symbol_level_deps()` is called to extract actual symbol usage dependencies from each file's analysis results.
- `build_summary_map()` is called to collect design document summaries across all analyzed files.

The file depends on `codetwine/utils/file_utils.py` for path conversion utilities (`rel_to_copy_path()`, `output_path_to_rel()`, `resolve_file_output_dir()`) that transform between project-relative paths and the pipeline's output directory structure. It is used by `codetwine/pipeline.py` to generate the three main output formats (consolidated JSON, dependency summary JSON, and Mermaid graph) and by `codetwine/knowledge_db.py` to build the SQLite knowledge database from consolidated analysis results.

The file writes JSON output to a temporary file with a `.tmp` suffix and atomically moves it to the final destination on success, ensuring that existing output files are not left in a corrupted state if writing fails. JSON is streamed to disk incrementally during writing to minimize memory usage, reading and processing one file's analysis results at a time.

**Definitions**

## `_to_mermaid_node_id`

Convert a file path string into a valid Mermaid node identifier by replacing slashes and dots with underscores, used when generating the Mermaid flowchart representation of the dependency graph.

## `_load_json`

Load and parse a JSON file from disk, returning the parsed dictionary or None if the file does not exist, used internally to read analysis results (file_dependencies.json and doc.json) for each source file.

## `to_output_path`

Transform a file's project-relative path into the standardized "project_name/copy_path" format used throughout output files, where the project name is extracted from the base output directory's final path component.

## `build_summary_map`

Read the "summary" field from each file's doc.json and return a mapping of project-relative paths to summary text (or None), used to attach LLM-generated design document summaries to dependency entries in consolidated output.

## `iter_dependency_entries`

Yield one entry per analyzed file containing the file path, summary, callers, and callees in "project_name/copy_path" format, used by both `save_dependency_summary()` and `save_consolidated_json()` to generate the "files" array with dependency information.

## `build_file_entry`

Read a single file's file_dependencies.json and doc.json into a consolidated entry structure with the "file" field deduplicated at the top level, returning None if neither file exists; used to build rows for the SQLite knowledge database and the consolidated JSON.

## `_write_object_start`

Write the opening brace and "project_name" field of the top-level JSON object to an output file.

## `_write_array_item`

Write one JSON array element to an output file with proper indentation and comma separation, used by JSON output functions to incrementally stream entries without holding the entire array in memory.

## `save_consolidated_json`

Generate a complete project knowledge file by combining each file's file_dependencies.json (dependency info) and doc.json (design document) with the symbol-level dependency graph into a single JSON, writing incrementally to a temporary file and atomically moving it to the final destination.

## `build_symbol_level_deps`

Extract symbol-level (actual usage-based) dependencies from each file's file_dependencies.json by collecting callee files from callee_usages' "from" fields and caller files from caller_usages' "file" fields, returning a mapping of file relative paths to their callers and callees sets.

## `save_dependency_summary`

Output a lightweight JSON combining symbol-level dependencies and file summaries into a single file, used to provide a quick reference of dependencies and LLM-generated summaries without the full file_dependencies and doc content.

## `save_dependency_graph_as_mermaid`

Generate a Mermaid flowchart diagram of the file-level dependency graph and output it as a Markdown file, with nodes labeled by their project-relative paths and edges representing callee relationships.

# Summary

# Summary

Consolidates and outputs analyzed code dependencies and documentation in multiple formats (JSON, Mermaid graphs) for knowledge bases and visualization. Transforms project-relative paths into standardized output formats, reads analysis results incrementally from disk, and writes JSON atomically to prevent corruption. Main definitions include `save_consolidated_json()`, `save_dependency_summary()`, `save_dependency_graph_as_mermaid()`, `build_symbol_level_deps()`, and `build_summary_map()`. Handles symbol-level and file-level dependencies, design document summaries, and Mermaid flowchart generation for dependency visualization.
