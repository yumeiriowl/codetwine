# Design Document: codetwine/file_analyzer.py

# Design Specification

**Overview**
Analyzes a single project source file and assembles a unified dict of its definitions, outbound (callee) usages, and inbound (caller) usages for downstream JSON serialization.

- A pipeline processing all project files calls `get_file_dependencies` once per file to obtain the per-file dependency record that feeds `file_dependencies.json`.
- Callers needing definition metadata (functions, classes, variables with source snippets) get it via the `definitions` key of the returned dict, built on top of `extract_definitions`.
- Callers needing "what this file imports and uses from elsewhere" get it via the `callee_usages` key, built by resolving imports (`build_symbol_to_file_map`, `get_import_params`, `extract_imports`) and usage locations (`build_usage_info_list`).
- Callers needing "who else in the project uses definitions from this file" get it via the `caller_usages` key, built with `build_caller_usages` using a precomputed `caller_map`.
- Callers needing unsupported-language handling (no import query/definition dict registered) still get a valid result with empty `callee_usages`/`caller_usages`, since the file degrades gracefully instead of failing.

This file relies on `parse_file` (codetwine/parsers/ts_parser.py) to obtain the AST and raw byte content of the target file; on `DEFINITION_DICTS` (codetwine/config/settings.py) to select language-specific definition node rules; on `extract_definitions` (codetwine/extractors/definitions.py) to enumerate named definitions; on `get_import_params` and `build_symbol_to_file_map` (codetwine/import_to_path.py) to resolve import statements into project file paths; on `extract_imports` (codetwine/extractors/imports.py) to parse raw import statements from the AST; and on `build_usage_info_list` / `build_caller_usages` (codetwine/extractors/usage_analysis.py) to compute callee and caller usage records. It is used by `codetwine/pipeline.py`, which calls `get_file_dependencies` for each project file (after ensuring the output directory exists) and writes the returned dict to per-file dependency output.

Import/usage analysis is skipped entirely (leaving `usage_list` and `caller_usages` empty) when `get_import_params` returns `(None, None)` for the file's extension, allowing unsupported languages to still produce a definitions-only result rather than erroring.

**Definitions**

## `get_file_dependencies`
Produces the complete per-file dependency analysis dict consumed by the pipeline to build `file_dependencies.json`. It computes the file's project-relative path and extension, parses the file via `parse_file`, extracts source-backed definition entries (name, type, start_line, end_line, context) using `extract_definitions` and the language's `DEFINITION_DICTS` entry, then—if the language supports import analysis—resolves imports to project files via `build_symbol_to_file_map`/`get_import_params`/`extract_imports`, gathers callee usage locations with source context via `build_usage_info_list`, and gathers caller usage locations across dependent files via `build_caller_usages` using the shared `caller_map`. Callers invoke it once per target file, passing precomputed `project_file_set`, `source_root_set`, and `caller_map` that are shared across all files in the project to avoid recomputation; the returned dict always contains `file`, `definitions`, `callee_usages`, and `caller_usages` keys, with the latter two empty lists for languages lacking import query configuration.

# Summary

Analyzes a single project source file to build a unified per-file dependency record for JSON output. Its main public function, get_file_dependencies, computes file path/extension, parses the file, extracts definitions (functions, classes, variables), resolves imports to project files, and gathers callee and caller usage information using shared precomputed maps. Handles unsupported languages gracefully by returning empty usage lists. Key terms: file analysis, definitions extraction, import resolution, callee/caller usage, dependency mapping, pipeline integration.
