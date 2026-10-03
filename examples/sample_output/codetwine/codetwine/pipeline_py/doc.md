# Design Document: codetwine/pipeline.py

# Design Specification

**Overview**

Orchestrate the entire project analysis workflow, extracting per-file dependencies and design documents, then consolidating results into dependency graphs and knowledge bases in multiple formats.

- Call `process_all_files()` from main.py to analyze a project directory and receive a dict reporting file counts, failed analyses, and detected encodings.
- Invoke `_process_file_dependencies()` internally to extract dependency information for all files and save file_dependencies.json and source copies to output directories.
- Use `_detect_change_file_set()` to identify which files have changed since the last run by comparing source hashes with recorded doc.json values.
- Call `_to_internal_dep_list()` to convert project dependency paths from "project_name/copy_path" format to internal relative paths for processing.
- Invoke `_ext_count_line()` to generate human-readable file extension counts for logging.

The file serves as the central orchestration point for the entire analysis pipeline. It depends on `build_project_dependencies()` from extractors/dependency_graph.py to build the project-wide dependency graph, `get_file_dependencies()` from file_analyzer.py to analyze individual files, and functions from output.py (`build_symbol_level_deps()`, `build_summary_map()`, `save_dependency_summary()`, `save_dependency_graph_as_mermaid()`, `save_consolidated_json()`) to consolidate and output results. It also depends on `generate_all_docs()` from doc_creator.py to generate design documents when LLM is enabled, and `save_consolidated_sqlite()` from knowledge_db.py to export to SQLite format. The file is called exclusively by main.py's entry point to drive the entire analysis process. It validates configuration (KNOWLEDGE_FORMAT, SOURCE_ENCODING) before processing begins and clears language-specific caches (parse_cache, module_tree_cache, file_index_cache, reference_target_cache, namespace_index_cache, csharp_target_cache, r_name_index_cache, r_target_cache) after completion to free memory.

Design documents are generated only when ENABLE_LLM_DOC is True, and only for files with a language detected by `has_language()`. Change detection works incrementally by comparing file hashes to avoid regenerating unchanged documents. The pipeline processes files in five stages: building the project dependency graph, extracting per-file dependencies, detecting changes and generating design documents in topological order, generating consolidated dependency summaries and Mermaid diagrams, and outputting final consolidated results in JSON and/or SQLite format based on KNOWLEDGE_FORMAT.

**Definitions**

## `_to_internal_dep_list`

Converts the "project_name/copy_path" format paths returned by `build_project_dependencies()` back to project-relative paths by calling `output_path_to_rel()` on each file, caller, and callee entry; used internally to restore paths to the format expected by downstream processing functions.

## `_detect_change_file_set`

Identifies which files have changed since the last run by comparing SHA256 hashes of source files against hashes recorded in each file's doc.json, returning a set of relative paths; files with no readable doc.json are treated as changed. Used before design document generation to support incremental regeneration.

## `_ext_count_line`

Counts the number of files per extension using Counter, returning a human-readable string like "py 2, md 1, (none) 1" with extensions sorted by frequency; used for progress logging to summarize analyzed file composition.

## `_to_output_format`

Rewrites file paths in a `get_file_dependencies()` result from project-relative format to "project_name/copy_path" format by calling `to_output_path()` on the file field and on the from and file fields within callee_usages and caller_usages respectively; modifies the result dict in place.

## `_process_file_dependencies`

Analyzes dependency information for each file in a list by calling `get_file_dependencies()`, saves file_dependencies.json and a source file copy to each file's output directory, and removes stale outputs when analysis fails; returns a tuple of (fail_file_list, detected_encoding_dict) tracking files whose analysis failed and files whose encoding had to be detected. Removes design documents (doc.json, doc.md) for files without a language, as such files cannot have design documents.

## `process_all_files`

Main async entry point that orchestrates the entire project analysis pipeline: builds the project dependency graph via `build_project_dependencies()`, extracts per-file dependencies for all files, detects changes and generates design documents in topological dependency order (when ENABLE_LLM_DOC is True), generates consolidated dependency summaries and Mermaid diagrams, exports results in JSON and/or SQLite format based on KNOWLEDGE_FORMAT, and clears all language-specific caches. Validates KNOWLEDGE_FORMAT and SOURCE_ENCODING before processing, and returns a dict with file_count, dependency_fail_list, doc_count, doc_fail_list, and detected_encoding_dict. Raises ValueError if KNOWLEDGE_FORMAT is invalid or SOURCE_ENCODING names an unknown codec.

# Summary

# Summary

**Responsibility:** Orchestrates the complete project analysis pipeline, coordinating extraction of file dependencies and design documents, then consolidating results into dependency graphs and knowledge bases in multiple output formats.

**Main Public Definitions:** `process_all_files()` – async entry point that validates configuration, builds project dependency graphs, extracts per-file dependencies, detects changes, generates design documents in topological order (when LLM enabled), produces consolidated summaries and Mermaid diagrams, exports to JSON/SQLite, and clears language caches.

**Key Terms:** Project-wide dependency orchestration, incremental change detection via file hashing, per-file dependency extraction, design document generation, multi-format consolidation and export, configuration validation, cache management.
