# Design Document: codetwine/file_analyzer.py

# Design Specification

**Overview**

Analyze source files to extract definitions, usages, and dependencies for downstream JSON output by resolving references across single files and multi-file projects.

- Call `get_file_dependencies()` from `pipeline.py` to produce file-level analysis data containing definitions, callee usages, same-file usages, and caller usages for each project file.
- Query the returned dict's "definitions" key to access structured definition entries with name, type, line range, context, and optional language-specific fields (name_line, level, is_group for COBOL/BMS).
- Query "callee_usages" to see which external definitions this file references and where.
- Query "same_file_usages" to see which internal definitions this file references and where.
- Query "caller_usages" to see which other project files reference definitions in this file and where.

This file serves as the central per-file analysis entry point in the pipeline. It depends on `definition_source.py` and `definitions.py` to extract definitions via AST parsing, `usage_analysis.py` to group and filter resolved references, `reference_target.py` and `ts_parser.py` to resolve references across the codebase, `settings.py` to determine language-specific extraction rules, and `file_utils.py` to read and normalize file content. The `pipeline.py` module calls `get_file_dependencies()` once per file to build the complete dependency graph.

Files without a language (detected by `language_ext()` returning "") are handled gracefully by returning empty definition and usage lists; encoding detection is performed and reported for all files. Reference resolution follows language-specific strategies (COBOL, C#, R, or import-based) determined by file extension, with COBOL and R files requiring AST tree access only when same-file references need line-range filtering.

**Definitions**

## `_definition_entry`

Transform a single `DefinitionInfo` object and its file's content into a JSON-ready dict entry by including name, type, line range, and extracted source context, plus optional fields (name_line, level, is_group) when present for COBOL and BMS definitions. Called once per definition to build the "definitions" list returned by `get_file_dependencies()`.

## `get_file_dependencies`

Analyze a target file and return a dict containing its definitions, callee usages, same-file usages, and caller usages as strings formatted for JSON output. For each file, extracts definitions via `file_definition_list()`, resolves references via `reference_target_list()`, groups usages by target via `build_callee_usages()`, `build_same_file_usages()`, and `build_caller_usages()`, detects file encoding, and returns metadata including the file's language extension. Files without a language return empty usage lists and None for detected_encoding; reference resolution dispatches to language-specific strategies based on `reference_kind()`.

# Summary

# Summary: codetwine/file_analyzer.py

**Single Responsibility:** Analyzes individual source files to extract definitions, usages, and dependencies, serving as the central per-file analysis entry point in the pipeline that produces structured data for downstream JSON output.

**Main Public Definitions:**
- `get_file_dependencies()` — primary entry point called by pipeline.py for each file; returns definitions, callee usages, same-file usages, and caller usages
- `_definition_entry()` — transforms DefinitionInfo objects into JSON-ready dicts with name, type, line range, context, and language-specific fields

**Key Responsibilities:** Extracts definitions via AST parsing; resolves references across single files and projects using language-specific strategies (COBOL, C#, R, or import-based); groups and filters usages into three categories; detects file encoding; handles files without detected language gracefully by returning empty results. Integrates with definition_source.py, definitions.py, usage_analysis.py, reference_target.py, ts_parser.py, settings.py, and file_utils.py.
