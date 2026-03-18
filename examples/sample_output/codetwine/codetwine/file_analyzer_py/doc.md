# Design Document: codetwine/file_analyzer.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyzes a single source file to extract its definitions, callee usages (symbols this file imports and uses from other project files), and caller usages (locations in other project files that use symbols defined in this file), returning all results as a structured dict.

## 2. When to Use This Module

- **Generating per-file dependency data for the pipeline**: Call `get_file_dependencies(target_file, project_dir, project_dep_list)` from `pipeline.py` to obtain a structured record of definitions, callee usages, and caller usages for a given file. The returned dict is the source data written to `file_dependencies.json`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` | Parses the target file, extracts its definitions and import-based usage relationships (both directions), and returns a dict with keys `"file"`, `"definitions"`, `"callee_usages"`, and `"caller_usages"`. |

## 4. Design Decisions

- **Short-circuit on unsupported languages**: Import and usage analysis is conditionally executed only when `get_import_params` returns a non-`None` result for the file's extension. Files in languages without import query support still receive definition extraction but produce empty `callee_usages` and `caller_usages` lists, keeping the return shape consistent across all file types.
- **Relative path normalization**: All file paths in the output use forward-slash-separated paths relative to `project_dir` (backslashes replaced), ensuring the output is platform-independent regardless of the host OS.
- **Definition source code embedding**: Each definition entry includes a `"context"` field containing the raw source lines spanning its line range, extracted directly from the decoded file content rather than re-reading the file, since `parse_file` already returns the byte content.

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
| `project_dep_list` | `list[dict]` | Pre-built dependency list from `save_project_dependencies`; each dict contains at minimum `"file"` and `"callers"` keys |

**Return type:** `dict` with the following fixed keys:

| Key | Value type | Description |
|---|---|---|
| `"file"` | `str` | Project-root-relative path of the analyzed file (forward-slash normalized) |
| `"definitions"` | `list[dict]` | All named definitions extracted from the file |
| `"callee_usages"` | `list[dict]` | Usages of project-internal symbols imported by this file |
| `"caller_usages"` | `list[dict]` | Locations in other files where symbols from this file are used |

Each entry in `"definitions"` contains:

| Field | Type | Description |
|---|---|---|
| `"name"` | `str` | Symbol name |
| `"type"` | `str` | AST node type of the definition |
| `"start_line"` | `int` | 1-based start line |
| `"end_line"` | `int` | 1-based end line |
| `"context"` | `str` | Raw source text of the definition spanning `start_line`–`end_line` |

---

### Responsibility

Orchestrates the full per-file analysis pipeline — AST parsing, definition extraction, import resolution, callee usage tracking, and caller usage tracking — and returns all results in a single structured dict suitable for serialization into `file_dependencies.json`.

### When to Use

Called once per project file by the pipeline (`pipeline.py`) as part of a multi-file analysis run, receiving the project-wide dependency list that was previously assembled for cross-file lookup.

---

### Design Decisions

- **Language-conditional import analysis:** Import resolution and usage analysis are skipped entirely when `get_import_params` returns `(None, None)` for the file's extension, leaving `callee_usages` and `caller_usages` as empty lists. This avoids crashing on unsupported file types while still producing definition data.

- **`definition_dict` lookup by extension:** The appropriate AST node-to-name mapping is selected from `DEFINITION_DICTS` using the file extension, yielding `None` for unsupported languages and allowing `extract_definitions` to be called with `None` without requiring an explicit guard here.

- **Path normalization:** The relative path stored in `"file"` is normalized to forward slashes via `.replace("\\", "/")` to ensure consistent keys regardless of the host OS.

- **`project_file_set` derived inline:** The set of known project file paths is constructed from `project_dep_list` inside this function rather than being passed in, keeping the caller's interface minimal.

- **`context` field extraction:** Definition source text is derived by slicing the decoded, line-split file content using the 1-based `start_line`/`end_line` from `DefinitionInfo`, avoiding a second file read by reusing the bytes already returned by `parse_file`.

---

### Constraints & Edge Cases

- `target_file` must be an absolute path readable by the filesystem; `parse_file` will raise an error for missing or unreadable files.
- `project_dep_list` must already contain an entry for `target_file`'s relative path if caller usage data is expected; if no matching entry exists, `build_caller_usages` returns an empty list without error.
- For file extensions not present in `DEFINITION_DICTS`, `definition_dict` is `None`, which is passed directly to `extract_definitions` — the behavior for a `None` dict is defined by that function.
- File content is decoded as UTF-8; files with other encodings will raise a `UnicodeDecodeError` at the `content.decode("utf-8")` call.
- `callee_usages` and `caller_usages` both remain empty lists for any file extension for which `get_import_params` returns `(None, None)`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to read and parse the target source file into a tree-sitter AST root node and raw byte content for downstream analysis.

- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to extract named definitions (functions, classes, variables, etc.) from the parsed AST, using a per-language `definition_dict` obtained from settings.

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : uses `extract_imports` (indirectly via `build_symbol_to_file_map` and `build_caller_usages`) to parse import statements from the AST into structured `ImportInfo` records.

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : uses `build_usage_info_list` to produce callee usage records (where imported names are used in this file) and `build_caller_usages` to produce caller usage records (where names defined in this file are used by other project files).

- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : uses `build_symbol_to_file_map` to construct a mapping from imported symbol names to their definition file paths, and `get_import_params` to retrieve the tree-sitter `Language` object and import query string for the file's extension.

- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : uses `DEFINITION_DICTS` to look up the per-language definition extraction configuration keyed by file extension.

## Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : calls `get_file_dependencies` for each project file, passing the absolute file path, project root directory, and the project-wide dependency list, and consumes the returned dict (containing `file`, `definitions`, `callee_usages`, and `caller_usages`) to build the `file_dependencies.json` output.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : unidirectional (file_analyzer depends on ts_parser; ts_parser has no dependency on file_analyzer)
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : unidirectional (pipeline depends on file_analyzer; file_analyzer has no dependency on pipeline)

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute file path string |
| `project_dir` | Caller (`pipeline.py`) | Absolute directory path string |
| `project_dep_list` | Caller (`pipeline.py`) | List of dicts from `save_project_dependencies`, each with at least `"file"` and `"callers"` keys |
| File content | Disk read via `parse_file` | Binary bytes, decoded to UTF-8 |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | `dict[str, dict[str, str]]` mapping file extension to AST node-type config |
| Language/query config | `get_import_params` → `codetwine/config/settings.py` | `(Language, str)` tuple or `(None, None)` |

---

## 2. Transformation Overview

### Stage 1: Resolve File Identity
`target_file` is converted to a relative path (`target_file_rel`) and its extension (`file_ext`) is extracted. The per-language `definition_dict` is looked up from `DEFINITION_DICTS` using `file_ext`.

### Stage 2: Parse File
`parse_file(target_file)` reads the file from disk, parses it via tree-sitter, and returns `(root_node, content)`. The binary `content` is decoded and split into `content_lines` for later source extraction.

### Stage 3: Extract Definitions
`extract_definitions(root_node, definition_dict)` traverses the AST and returns a list of `DefinitionInfo` objects. Each is converted into a plain dict, with the source text of the definition's line range spliced from `content_lines` and stored under `"context"`.

### Stage 4: Resolve Import Language Parameters
`get_import_params(file_ext)` returns the tree-sitter `Language` object and an import query string. If either is unavailable (unsupported language), the import/usage stages are skipped entirely and both `usage_list` and `caller_usages` remain empty lists.

### Stage 5: Build Project File Set
The `project_dep_list` is iterated to collect all known project-relative file paths into `project_file_set`, used as a filter in subsequent resolution steps.

### Stage 6: Extract Imports and Build Symbol Map
`extract_imports(root_node, language, import_query_str)` parses import statements from the AST into a list of `ImportInfo` objects. These are passed to `build_symbol_to_file_map`, which resolves each imported module name to its project-relative file path, producing:
- `symbol_to_file_map`: `{imported_name → definition_file_rel}`
- `alias_to_original`: `{alias_name → original_name}`

### Stage 7: Build Callee Usages
`build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` scans the AST for identifier nodes matching names in `symbol_to_file_map`. Usages are grouped by `(source_file, name)`, deduplicated, and each group is enriched with the definition's source code (`target_context`) read from the dependency file.

### Stage 8: Build Caller Usages
`build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set)` iterates over files that import `target_file_rel`, parses each caller's AST, extracts lines where names from `target_file_rel` are used, and attaches surrounding source lines as `usage_context`.

### Stage 9: Assemble and Return Result
The four collected data items are assembled into a single dict and returned to the caller.

---

## 3. Outputs

The function returns a single `dict` to the caller (`pipeline.py`). No files are written and there are no side effects (file I/O is encapsulated inside dependencies).

| Key | Type | Description |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file |
| `"definitions"` | `list[dict]` | Definitions extracted from the file |
| `"callee_usages"` | `list[dict]` | Usages of names imported from other project files |
| `"caller_usages"` | `list[dict]` | Usages of this file's names in other project files |

---

## 4. Key Data Structures

### Return value dict

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Relative path of the analyzed file |
| `"definitions"` | `list[dict]` | Per-definition records (see below) |
| `"callee_usages"` | `list[dict]` | Callee usage records (see below) |
| `"caller_usages"` | `list[dict]` | Caller usage records (see below) |

### Definition record dict (element of `"definitions"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the defined symbol |
| `"type"` | `str` | AST node type of the definition |
| `"start_line"` | `int` | 1-based first line of the definition |
| `"end_line"` | `int` | 1-based last line of the definition |
| `"context"` | `str` | Source text of the definition's line range |

### Callee usage record dict (element of `"callee_usages"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Deduplicated, sorted line numbers where the name is used |
| `"name"` | `str` | The imported name (or attribute path) being used |
| `"from"` | `str` | Project-relative path of the file where the name is defined |
| `"target_context"` | `str` | Source code of the definition in the dependency file |

### Caller usage record dict (element of `"caller_usages"`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Deduplicated, sorted line numbers in the caller file |
| `"name"` | `str` | Name defined in this file that is used by the caller |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Surrounding source lines from the caller at usage locations |

### `project_dep_list` element dict (input)

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative file path |
| `"callers"` | `list[str]` | Relative paths of files that import this file |

### Intermediate structures

| Name | Type | Purpose |
|---|---|---|
| `symbol_to_file_map` | `dict[str, str]` | Maps each imported symbol name to the project-relative path of its definition file |
| `alias_to_original` | `dict[str, str]` | Maps alias names to their original imported names |
| `project_file_set` | `set[str]` | Set of all project-relative file paths, used as a resolution filter |
| `content_lines` | `list[str]` | UTF-8 source lines of the target file, used to slice definition source text |

## Error Handling

# Error Handling

## 1. Overall Strategy

`file_analyzer.py` adopts a **delegation-and-trust** strategy: it contains no explicit `try-except` blocks of its own and performs no local error catching. All error handling responsibility is fully delegated to the dependency modules it calls (`parse_file`, `extract_definitions`, `build_symbol_to_file_map`, etc.). Within the file itself, the only form of defensive logic is **conditional branching**: import/usage analysis is gated behind a truthiness check on the `language` and `import_query_str` values returned by `get_import_params`, causing that entire analysis path to be silently skipped for unsupported file extensions rather than raising an error.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (import analysis) | `get_import_params` returns `(None, None)` for an unrecognized extension | Conditional branch skips the entire import/usage analysis block; `usage_list` and `caller_usages` remain empty lists | Yes | No import or usage data is produced; definition extraction still proceeds |
| Unsupported file extension (definition extraction) | `DEFINITION_DICTS.get(file_ext)` returns `None` for an unrecognized extension | `None` is passed as `definition_dict` to `extract_definitions`; behavior depends on the downstream implementation | Yes (delegated) | Definition list may be empty; no local error is raised |
| File read / parse failure | `parse_file` encounters an unreadable or unparseable file | Not handled locally; propagates as an unhandled exception from `parse_file` | No | Function terminates with an exception; caller (`pipeline.py`) receives the exception |
| Symbol resolution failure | `build_symbol_to_file_map` cannot resolve an import to a project file | Not handled locally; delegated entirely to `build_symbol_to_file_map` | Yes (delegated) | Unresolvable symbols are silently omitted from `symbol_to_file_map` |
| Usage extraction failure | `build_usage_info_list` or `build_caller_usages` encounters an error | Not handled locally; propagates as an unhandled exception | No | Function terminates with an exception |
| UTF-8 decode failure | `content.decode("utf-8")` fails on binary or non-UTF-8 file content | Not handled locally; propagates as an unhandled exception | No | Function terminates; `content_lines` and `definition_list` are not produced |

## 3. Design Notes

The absence of local error handling reflects a deliberate **separation of concerns**: `get_file_dependencies` is responsible solely for orchestrating the analysis pipeline, while each dependency module is responsible for its own error conditions. The only locally-enforced safety boundary is the `language and import_query_str` guard, which represents a known, expected condition (unsupported language) rather than an exceptional one, and is handled as normal control flow rather than error recovery. Unexpected failures (I/O errors, parse errors, decode errors) are allowed to propagate upward to the caller in `pipeline.py`, which is the appropriate level to decide how to handle per-file failures in the context of a full project analysis run.

## Summary

`file_analyzer.py` orchestrates per-file dependency analysis for a single source file. Public function: `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`. Returns a dict with keys `"file"` (str), `"definitions"` (list[dict] with name, type, start_line, end_line, context), `"callee_usages"` (list[dict] with lines, name, from, target_context), and `"caller_usages"` (list[dict] with lines, name, file, usage_context). Consumes `DEFINITION_DICTS`, `project_dep_list`, and results from `parse_file`, `extract_definitions`, `build_symbol_to_file_map`, `build_usage_info_list`, and `build_caller_usages`.
