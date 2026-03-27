# Design Document: codetwine/file_analyzer.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Orchestrates the full per-file dependency analysis pipeline by combining AST parsing, definition extraction, import resolution, and usage tracking into a single structured result dict for one target file.

## 2. When to Use This Module

- **Analyzing a single file's dependency information**: Call `get_file_dependencies(target_file, project_dir, project_dep_list)` to obtain all definitions declared in that file, all project-internal symbols it calls (callee usages), and all project files that call into it (caller usages). This is the primary entry point used by `codetwine/pipeline.py` when iterating over every file in a project.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` | Parses the target file, extracts its definitions and import-based usage relationships, and returns a dict with keys `"file"`, `"definitions"`, `"callee_usages"`, and `"caller_usages"`. |

**Return dict structure:**

| Key | Type | Content |
|---|---|---|
| `"file"` | `str` | Relative path of the target file from the project root |
| `"definitions"` | `list[dict]` | Each definition's `name`, `type`, `start_line`, `end_line`, and `context` (source text) |
| `"callee_usages"` | `list[dict]` | Symbols imported from other project files and where they are used |
| `"caller_usages"` | `list[dict]` | Locations in other project files where symbols defined here are used |

## 4. Design Decisions

- **Conditional import/usage analysis**: Import and usage analysis is skipped entirely for file extensions that `get_import_params` cannot resolve (returns `(None, None)`). This avoids failures on unsupported languages while still producing definition data, leaving `callee_usages` and `caller_usages` as empty lists.
- **Relative path normalization**: All file paths in the output use forward slashes (`/`) regardless of OS, produced by combining `os.path.relpath` with `.replace("\\", "/")`, ensuring consistent keys across the pipeline on Windows.
- **Delegation of all sub-tasks**: This module contains no analysis logic itself; it exclusively composes calls to `parse_file`, `extract_definitions`, `extract_imports`, `build_symbol_to_file_map`, `build_usage_info_list`, and `build_caller_usages`, acting as a thin orchestration layer.

## Definition Design Specifications

# Definition Design Specifications

---

## `get_file_dependencies`

### Signature

```python
def get_file_dependencies(
    target_file: str,
    project_dir: str,
    project_dep_list: list[dict],
) -> dict
```

| Parameter | Type | Description |
|---|---|---|
| `target_file` | `str` | Absolute path of the source file to analyze |
| `project_dir` | `str` | Absolute path to the project root directory |
| `project_dep_list` | `list[dict]` | Pre-built dependency list output by `save_project_dependencies`; each dict contains at minimum `"file"` and `"callers"` keys |

**Return type:** `dict` with the following keys:

| Key | Value type | Description |
|---|---|---|
| `"file"` | `str` | Relative path of the analyzed file from `project_dir` (forward-slash normalized) |
| `"definitions"` | `list[dict]` | Extracted named definitions with name, type, line range, and source context |
| `"callee_usages"` | `list[dict]` | Usage records for project-internal symbols this file imports and uses |
| `"caller_usages"` | `list[dict]` | Usage records describing where this file's own definitions are used by other project files |

Each entry in `"definitions"` contains:

| Field | Type | Description |
|---|---|---|
| `"name"` | `str` | Identifier name of the definition |
| `"type"` | `str` | AST node type (e.g., `"function_definition"`) |
| `"start_line"` | `int` | 1-based start line number |
| `"end_line"` | `int` | 1-based end line number |
| `"context"` | `str` | Full source text of the definition extracted from the file's content lines |

---

### Responsibility

Orchestrates the complete per-file analysis pipeline — parsing, definition extraction, import resolution, callee usage tracking, and caller usage collection — and returns all results as a single structured dict suitable for serialization into `file_dependencies.json`.

### When to Use

Called once per file from `process_all_files` (in `codetwine/pipeline.py`) during the project-wide dependency analysis pass.

---

### Design Decisions

- **Relative path normalization:** The target file's path is converted to a project-root-relative path with backslashes replaced by forward slashes, ensuring cross-platform consistency in output keys.
- **Conditional import/usage analysis:** Import resolution and all usage analysis (both callee and caller directions) are skipped entirely when `get_import_params` returns `(None, None)` for the file's extension. This means `callee_usages` and `caller_usages` are always empty lists for unsupported languages rather than raising errors.
- **Project file set construction:** A `set[str]` of relative file paths is built inline from `project_dep_list` solely for use as a fast membership-test structure during symbol resolution. This avoids requiring callers to pass in a pre-built set.
- **Definition context extraction:** Source text for each definition is reconstructed by slicing the decoded content lines using the `start_line`/`end_line` values from `DefinitionInfo`. The content bytes decoded once from the `parse_file` result are reused for both definition context extraction and downstream parsing, avoiding redundant file I/O.
- **Separation of callee and caller directions:** Callee usages (what this file uses from others) and caller usages (what others use from this file) are computed by separate delegated functions and stored under distinct output keys, keeping each concern independently queryable.

---

### Constraints & Edge Cases

- `target_file` must be an absolute path readable by `parse_file`; no fallback handling is present for missing or unreadable files.
- `definition_dict` is `None` for file extensions not registered in `DEFINITION_DICTS`; `extract_definitions` is called regardless, so the behavior for unsupported extensions depends on `extract_definitions`'s handling of a `None` dict.
- If `project_dep_list` does not contain an entry for `target_file_rel`, `build_caller_usages` will find no callers and return an empty list without error.
- The function does not validate that `project_dir` is a prefix of `target_file`; incorrect paths produce unreliable relative path values in the output.
- File content is assumed to be UTF-8 decodable; no fallback encoding is attempted.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/parsers/ts_parser.py`**
Imports `parse_file` to parse the target source file into a tree-sitter AST and retrieve raw byte content, which serve as the foundation for all subsequent extraction steps.

**`codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/definitions.py`**
Imports `extract_definitions` to traverse the AST and produce a list of `DefinitionInfo` objects (functions, classes, variables, etc.), which are then enriched with source code context and included in the returned dict under `"definitions"`.

**`codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/usage_analysis.py`**
Imports `build_usage_info_list` to identify where project-imported symbols are used within the target file (producing `callee_usages`), and `build_caller_usages` to identify where symbols defined in the target file are used by other project files (producing `caller_usages`).

**`codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/import_to_path.py`**
Imports `build_symbol_to_file_map` to construct a mapping from imported symbol names to their definition file paths, and `get_import_params` to retrieve the tree-sitter `Language` object and import query string appropriate for the target file's extension. Both are prerequisites for import and usage analysis.

**`codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/imports.py`**
Imports `extract_imports` to parse import statements from the AST into structured `ImportInfo` objects, which are then passed to `build_symbol_to_file_map` for symbol resolution.

**`codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/config/settings.py`**
Imports `DEFINITION_DICTS` to look up the per-language definition extraction configuration keyed by file extension, which is passed directly to `extract_definitions`.

---

## Dependents (modules that import this file)

**`codetwine/pipeline.py` → `codetwine/file_analyzer_py/file_analyzer.py`**
Imports `get_file_dependencies` and calls it per file during the project analysis pipeline, passing the absolute file path, project root, and the project-level dependency list, then consuming the returned dict (containing `"file"`, `"definitions"`, `"callee_usages"`, and `"caller_usages"`) to assemble the final `file_dependencies.json` output.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/pipeline.py` → `codetwine/file_analyzer_py/file_analyzer.py` (pipeline drives the analyzer; the analyzer has no knowledge of the pipeline)
- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/parsers/ts_parser.py` (one-way consumption of parsing results)
- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/definitions.py` (one-way consumption of definition extraction)
- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` (one-way consumption of usage analysis results)
- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/import_to_path.py` (one-way consumption of import resolution utilities)
- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/imports.py` (one-way consumption of import extraction)
- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/config/settings.py` (one-way read of configuration data)

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute path string to the file being analyzed |
| `project_dir` | Caller (`pipeline.py`) | Absolute path string to the project root |
| `project_dep_list` | Caller (`pipeline.py`) | List of dicts produced by `save_project_dependencies`, each containing at minimum `"file"` and `"callers"` keys |
| File content | `parse_file(target_file)` | Raw bytes read from disk, returned as `bytes` alongside an AST `Node` |
| Language configuration | `DEFINITION_DICTS.get(file_ext)` | Per-language dict mapping AST node type strings to name-extraction strategies |
| Import query parameters | `get_import_params(file_ext)` | A `(Language, import_query_str)` tuple from `settings.py` registry |

---

## 2. Transformation Overview

### Stage 1 – Path and Language Resolution
`target_file` is converted to a project-relative path (`target_file_rel`) and its extension is extracted. The extension drives two lookups: `DEFINITION_DICTS.get(file_ext)` retrieves per-language definition extraction configuration, and `get_import_params(file_ext)` retrieves the tree-sitter `Language` object and import query string. If the language is unsupported, import/usage stages are skipped entirely.

### Stage 2 – Parsing
`parse_file(target_file)` reads the file from disk (or returns a cached result) and produces an AST `root_node` plus raw `content` bytes. The bytes are decoded to UTF-8 and split into lines to support source extraction in later stages.

### Stage 3 – Definition Extraction
`extract_definitions(root_node, definition_dict)` traverses the AST via BFS and returns a list of `DefinitionInfo` objects. Each object is then projected into a plain dict by joining the corresponding source lines from `content_lines` using the definition's `start_line`/`end_line` range, producing `definition_list`.

### Stage 4 – Project File Set Construction
The `project_dep_list` is iterated to collect all known project-relative file paths into `project_file_set` (a `set[str]`). This set is used downstream to distinguish project-internal imports from third-party ones.

### Stage 5 – Import Parsing and Symbol Map Construction
`extract_imports(root_node, language, import_query_str)` scans the AST for import statements, returning a list of `ImportInfo` objects. These are passed to `build_symbol_to_file_map`, which resolves each imported module to a project-internal file path and produces two dicts: `symbol_to_file_map` (imported name → definition file path) and `alias_to_original` (alias name → original name).

### Stage 6 – Callee Usage Analysis
`build_usage_info_list` walks the AST to find all locations where names in `symbol_to_file_map` are referenced, groups occurrences by `(source_file, name)`, and attaches the definition's source code. The result is `usage_list`, representing the current file's outbound usages of project symbols.

### Stage 7 – Caller Usage Analysis
`build_caller_usages` uses `project_dep_list` to find all other project files that import from `target_file_rel`, then inspects each such file's AST to locate usage lines. The result is `caller_usages`, representing inbound references to symbols defined in the current file.

### Stage 8 – Result Assembly
The four collected artifacts are assembled into a single return dict keyed by `"file"`, `"definitions"`, `"callee_usages"`, and `"caller_usages"`.

---

## 3. Outputs

The function returns a single `dict` to the caller (`pipeline.py`). No file writes or side effects are produced by this module directly.

| Key | Type | Content |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file |
| `"definitions"` | `list[dict]` | All named definitions found in the file with source context |
| `"callee_usages"` | `list[dict]` | Usages of project-internal symbols imported by this file |
| `"caller_usages"` | `list[dict]` | Usages of this file's symbols in other project files |

---

## 4. Key Data Structures

### `definition_list` entry (element of `"definitions"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Identifier name of the definition |
| `"type"` | `str` | AST node type (e.g. `"function_definition"`, `"class_definition"`) |
| `"start_line"` | `int` | 1-based line number where the definition begins |
| `"end_line"` | `int` | 1-based line number where the definition ends |
| `"context"` | `str` | Source code text spanning `start_line` to `end_line` |

### `usage_list` entry (element of `"callee_usages"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers where the name is used in this file |
| `"name"` | `str` | The imported name (or attribute access path) as it appears in usage |
| `"from"` | `str` | Project-relative path of the file where the name is defined |
| `"target_context"` | `str` | Source code of the definition extracted from the dependency file |

### `caller_usages` entry (element of `"caller_usages"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers in the caller file where the name is used |
| `"name"` | `str` | Name defined in the target file that is referenced by the caller |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Source code snippets surrounding usage locations in the caller file |

### `project_dep_list` entry (input)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative path of a file in the project |
| `"callers"` | `list[str]` | Project-relative paths of files that import from this file |

### `symbol_to_file_map` (intermediate)
| Field / Key | Type | Purpose |
|---|---|---|
| *(imported name)* | `str` → `str` | Maps each imported symbol name to the project-relative path of its definition file |

### `alias_to_original` (intermediate)
| Field / Key | Type | Purpose |
|---|---|---|
| *(alias name)* | `str` → `str` | Maps an alias used in the current file back to the original imported name |

### `project_file_set` (intermediate)
| Structure | Type | Purpose |
|---|---|---|
| flat set | `set[str]` | All project-relative file paths, used to filter non-project imports |

## Error Handling

# Error Handling

## 1. Overall Strategy

`file_analyzer.py` adopts a **delegation-and-implicit-propagation** strategy. The function `get_file_dependencies` contains no explicit `try/except` blocks of its own; it relies entirely on the error handling behavior of its dependency modules. Any unhandled exception raised by a called function propagates directly to the caller (`pipeline.py`). For unsupported languages, the function applies **graceful degradation** by skipping import and usage analysis entirely rather than raising an error, returning a structurally complete result with empty fields for the omitted sections.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (import analysis) | `get_import_params` returns `(None, None)` for an extension not in `IMPORT_QUERIES` | The `if language and import_query_str:` guard skips all import/usage analysis; `usage_list` and `caller_usages` remain empty lists | Yes | `callee_usages` and `caller_usages` are empty in the output dict; `definitions` is still populated |
| Unsupported file extension (definition extraction) | `DEFINITION_DICTS.get(file_ext)` returns `None` for an unrecognized extension | `definition_dict` is `None`; passed to `extract_definitions`, which handles it internally | Yes (delegated) | Definition extraction output depends on how `extract_definitions` handles a `None` dict |
| File I/O or parse failure | `parse_file` cannot read or parse the target file | No local handling; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |
| Symbol resolution failure | `build_symbol_to_file_map` cannot resolve an import to a project file | No local handling; resolution failures are silently skipped inside the dependency module | Yes (delegated) | Unresolvable imports are absent from `symbol_to_file_map` without error |
| Usage extraction failure | `build_usage_info_list` or `build_caller_usages` encounters an error | No local handling; exceptions propagate to the caller | No | Entire `get_file_dependencies` call fails |
| Binary content decoding failure | `content.decode("utf-8")` fails for a file with non-UTF-8 encoding | No local handling; `UnicodeDecodeError` propagates to the caller | No | Entire `get_file_dependencies` call fails |

---

## 3. Design Notes

- **Thin orchestration layer**: `get_file_dependencies` is designed as a coordination function that composes results from specialized modules. Error handling responsibility is intentionally delegated to those modules, keeping the orchestrator simple.
- **Conditional path as the primary safety gate**: The `if language and import_query_str:` check is the sole explicit guard in the function. It doubles as both a feature-flag for unsupported languages and the mechanism that ensures the output dict always has a consistent shape (`callee_usages` and `caller_usages` default to empty lists), regardless of language support.
- **No logging at this layer**: Despite importing `logging`, no log calls appear in the function body. Diagnostic output is similarly delegated to dependency modules.
- **Fail-fast for unexpected errors**: Because there are no catch-all exception handlers, any unexpected runtime error (I/O failure, decoding error, internal module exception) terminates the current file's analysis immediately and surfaces to the pipeline layer, making failures visible rather than silently producing incomplete data.

## Summary

`file_analyzer.py` orchestrates per-file dependency analysis by composing parsing, definition extraction, import resolution, and usage tracking into a single result. Public API: `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`. Returns dict with keys `"file"` (str), `"definitions"` (list[dict] with name/type/start_line/end_line/context), `"callee_usages"` (list[dict] with lines/name/from/target_context), `"caller_usages"` (list[dict] with lines/name/file/usage_context). Skips import/usage analysis for unsupported file extensions.
