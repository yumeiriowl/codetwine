# Design Document: codetwine/extractors/dependency_graph.py

# Design Specification

**Overview**

Build a dependency graph of inter-file dependencies within a project by analyzing import statements and cross-file references, returning a structured representation of which files call which other files.

- Call `build_project_dependencies()` to analyze a project directory and obtain a list of dependency information dictionaries, each mapping a file to its callers and callees within the project.
- Use `_collect_text_file_list()` or `_filter_text_file_list()` to gather candidate source files from a project directory, filtering out binary files, symbolic links, and paths matching exclusion patterns.
- Invoke `_collect_callee_dict()` to map each file to the set of project files it depends on through import statements and cross-file references.
- Call `_to_output_entry_list()` to convert dependency information from absolute paths to output-format paths using the "project_name/copy_path" structure.

This file is the entry point for dependency graph analysis in the codetwine pipeline. It relies on language-specific modules to extract imports (`extract_imports()`), resolve import module names to project files (`resolve_module_to_project_path()`), resolve references to definitions (`reference_target_list()`), and build language-specific indexes (COBOL file index, C# namespace index, R name index, Rust module tree). It also consults configuration modules to determine which files have a language (`has_language()`, `language_ext()`), which files should be treated specially (COBOL copybooks via `register_copy_target()`, R Markdown without chunks via `set_no_language_file()`), and file encoding settings (`read_source()`, `lone_cr_to_lf()`). The pipeline calls `build_project_dependencies()` to generate the dependency graph, converting its output to the internal representation used by downstream analysis steps.

The function clears all language-specific caches and indexes at the start of each project analysis to ensure fresh results, preventing stale data from previous analyses from contaminating dependency resolution. Exceptions during import or reference resolution are caught individually and logged as warnings, allowing partial dependency information to be returned even when some files fail to analyze completely.

**Definitions**

## `_is_own_file`

Determine whether a file path is a genuine project file rather than a symbolic link or a file under a linked directory. Used during file collection to exclude symbolic references that would duplicate files or escape the project boundary; returns true only when the real path of the file matches the expected real path computed from the project directory and relative path.

## `_collect_text_file_list`

Walk a project directory tree and collect all non-empty text file paths, excluding directories and files matching EXCLUDE_PATTERNS, symbolic links, and binary or unreadable files. This is the primary file collection method when no file list is provided to `build_project_dependencies()`, and returns absolute paths in os.walk order after logging counts of skipped files.

## `_filter_text_file_list`

Filter a supplied list of file paths relative to the project root, returning only those that are non-empty text files within the project boundary and not matching EXCLUDE_PATTERNS. Used when `build_project_dependencies()` is given an explicit file list; returns absolute paths in input order without duplicates, and logs counts of skipped files.

## `_to_rel`

Convert an absolute file path to a project-relative path using "/" separators. A utility for normalizing paths to the format expected by project file sets and import resolution functions across different operating systems.

## `_no_chunk_document_list`

Identify R Markdown and Quarto files that contain no R code chunks, returning their relative paths for later registration as files without a language. These files should be analyzed without language-specific processing since they contain no executable R code.

## `_import_callee_rel_set`

Resolve the files that a source file's import statements lead to within the project. For files resolved through import statements, this queries the ImportBinder; for COBOL files, it resolves COPY and CALL statements. Returns relative paths of resolved project files, or an empty set if the file has no import statements or belongs to a language without import resolution configuration.

## `_log_graph_failure`

Log a warning that a file is being added to the dependency graph without its import or reference dependencies due to an exception during analysis. Used to notify users of partial failures while allowing the analysis to continue.

## `_reference_callee_rel_list`

Resolve the files that a source file's cross-file references lead to, dispatching to language-specific reference resolution (COBOL qualification chains, C# namespaces, R names, or import-based binding). For R files, also includes scripts imported via `source()` and `box::use()`, returning relative paths without duplicates.

## `_collect_callee_dict`

Build a mapping from each file in the project to the set of files it depends on, by resolving both its import statements and its cross-file references. Exceptions during either resolution step are caught and logged individually, so a file may have partial dependency information if one step fails; the mapping uses absolute paths as keys and values.

## `_to_output_entry_list`

Convert dependency information into the output format by replacing absolute paths with "project_name/copy_path" paths and building one entry per file with sorted caller and callee lists. Files without a language receive empty caller and callee lists since they do not participate in import or reference resolution.

## `build_project_dependencies`

Orchestrate the complete analysis of inter-file dependencies within a project, returning a list of dependency information dictionaries in the format `[{"file": "...", "callers": [...], "callees": [...]}, ...]`. This is the main entry point: it collects all text files (by walking the project or filtering a supplied list), clears all cached indexes from prior analyses, determines which files have a language and which should be treated specially (COBOL copybooks and R Markdown without chunks), builds the set of files each file imports or references, constructs the reverse caller mapping, and converts paths to output format. The caches cleared include parse results (kept for reuse), syntax trees and indexes (Rust module trees, C# namespace indexes, R name indexes, COBOL file indexes), resolved references through import statements, definitions and their sources, and JavaScript/TypeScript path configuration settings.

# Summary

# Summary: codetwine/extractors/dependency_graph.py

**Single Responsibility**
Analyzes inter-file dependencies within a project by extracting import statements and cross-file references, producing a structured dependency graph showing which files depend on which others.

**Main Public Definition**
`build_project_dependencies()` — orchestrates complete dependency analysis, collecting project files, resolving imports and references, and returning dependency information with callers and callees for each file.

**Key Capabilities**
Walks project directories to collect text files while filtering binaries and symbolic links; resolves imports to project files using language-specific modules; identifies cross-file references through code analysis; builds caller-callee mappings; clears language-specific caches between analyses to prevent stale data; catches individual exceptions during resolution to return partial results when some files fail.

**Key Terms**
Import resolution, reference resolution, dependency mapping, caller-callee relationships, language-specific analysis, cache clearing, file filtering, path normalization.
