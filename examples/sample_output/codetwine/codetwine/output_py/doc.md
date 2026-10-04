# Design Document: codetwine/output.py

# Design Specification

**Overview**

Consolidate and output analysis results from individual file processing into project-wide JSON summaries and dependency visualizations.

This file is used to:
- Generate a dependency graph with file summaries by calling `save_dependency_summary()`, which combines symbol-level dependencies with LLM-generated design document summaries for each file.
- Create a comprehensive consolidated JSON by calling `save_consolidated_json()`, which merges file_dependencies.json and doc.json analysis results into a single queryable project knowledge file.
- Visualize dependencies as a Mermaid flowchart by calling `save_dependency_graph_as_mermaid()`, which renders the symbol-level dependency graph as a Markdown diagram.
- Build the symbol-level dependency graph by calling `build_symbol_level_deps()`, which extracts actual symbol usage dependencies from each file's file_dependencies.json.
- Retrieve file summaries by calling `build_summary_map()`, which reads doc.json files to collect LLM-generated summaries indexed by file relative path.

This file depends on `codetwine/utils/file_utils.py` for path transformations between relative paths and copy-destination format, output directory resolution, and inverse conversion from output paths back to relative paths. It is consumed by `codetwine/pipeline.py` to drive the final output generation stages and by `codetwine/knowledge_db.py` to source consolidated file entries and dependency edges for database population.

File writes use atomic file replacement: consolidated JSON is written to a temporary file and moved into place only after completion, preventing partial files from overwriting valid outputs when errors occur.

**Definitions**

## `_to_mermaid_node_id`

Converts a file path string into a valid Mermaid flowchart node identifier by replacing path separators and extension dots with underscores, enabling safe embedding of file paths in graph syntax.

## `_load_json`

Reads and parses a JSON file from the filesystem, returning the parsed object or None if the file does not exist, used internally to load doc.json and file_dependencies.json results for each analyzed file.

## `to_output_path`

Transforms a file's project-relative path into the "project_name/copy_path" output format, where copy_path follows the destination directory structure created during file processing (e.g., "config_py/config.py"), used to normalize all file paths in output JSON files and dependency tracking.

## `build_summary_map`

Reads the "summary" field from each file's doc.json and returns a dict mapping relative paths to summary text or None, supporting cases where files lack LLM-generated design documents.

## `iter_dependency_entries`

Yields one dependency entry per file in list order, each containing the file path, summary, sorted callers, and sorted callees in output format, supplying the structure for both the project_dependencies array and dependency summary JSON.

## `build_file_entry`

Reads a file's file_dependencies.json and doc.json into a single consolidated dict entry with the file path held at the top level, returning None if neither analysis result exists, used to populate the files array in the consolidated knowledge JSON.

## `_write_object_start`

Writes the opening of a top-level JSON object and its "project_name" member field, used internally to initialize both project_dependency_summary.json and project_knowledge.json files.

## `_write_array_item`

Writes one element of a top-level JSON array with proper indentation and comma separation, handling both the first item (no leading comma) and subsequent items, used internally for streaming output of large JSON arrays to avoid holding entire results in memory.

## `save_consolidated_json`

Consolidates each file's file_dependencies.json (dependency info) and doc.json (design document) into a single project_knowledge.json file, with dependency entries in project_dependencies including summaries and per-file entries including full analysis data, written atomically via temporary file replacement to prevent data loss.

## `build_symbol_level_deps`

Extracts symbol-level dependencies from each file's file_dependencies.json by collecting callee files from callee_usages' from field and caller files from caller_usages' file field, returning a dict mapping relative paths to their actual symbol-usage-based callers and callees, used to build dependency graphs based on real usage rather than import structure.

## `save_dependency_summary`

Outputs a lightweight project_dependency_summary.json combining symbol-level dependencies with doc.json summaries for each file, supporting cases where no LLM analysis was performed by setting summary to null while preserving dependency structure.

## `save_dependency_graph_as_mermaid`

Generates a Mermaid flowchart markdown representation of the symbol-level dependency graph, with one node per file labeled with its relative path and edges representing callee relationships, output to a .md file for visualization in documentation systems.

## `_ARRAY_ITEM_INDENT`

Constant indentation string applied to each top-level JSON array element during streaming output, ensuring consistent formatting across consolidated and summary JSON files.

# Summary

# Summary

This file consolidates and outputs codetwine analysis results into project-wide JSON summaries and dependency visualizations. Its main responsibility is transforming per-file analysis data (dependencies and design documents) into unified project knowledge artifacts.

Main public definitions: `save_consolidated_json()` merges all file analyses into queryable project knowledge; `save_dependency_summary()` creates lightweight dependency summaries with design document snippets; `save_dependency_graph_as_mermaid()` renders dependencies as Mermaid diagrams; `build_symbol_level_deps()` extracts actual symbol-usage dependencies; `build_summary_map()` collects LLM-generated file summaries.

Key terms: dependency graph visualization, symbol-level dependencies, atomic file writes, JSON consolidation, streaming output for large datasets, path normalization between relative and output formats.
