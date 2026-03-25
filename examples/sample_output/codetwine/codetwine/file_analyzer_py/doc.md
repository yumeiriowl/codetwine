# Design Document: codetwine/file_analyzer.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary
Analyze a single source file to extract its definitions, outbound symbol usages (callee), and inbound symbol usages (caller), returning a structured dict that serves as the per-file record in `file_dependencies.json`.

## 2. When to Use This Module
- **Generating per-file dependency data**: Call `get_file_dependencies(target_file, project_dir, project_dep_list)` from `codetwine/pipeline.py` (or any pipeline stage) to obtain a dict describing what a file defines, what project-internal symbols it uses, and which other project files use its symbols.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` | Parse a source file, extract its definitions and import-based usage relationships, and return a unified record with `file`, `definitions`, `callee_usages`, and `caller_usages` keys. |

## 4. Design Decisions
- **Language-gated import analysis**: Import and usage analysis is only performed when `get_import_params` returns a non-`None` `(language, import_query_str)` pair, allowing the module to silently skip unsupported file extensions without raising errors.
- **Delegation to focused extractors**: Each concern (parsing, definition extraction, import extraction, symbol-to-file resolution, usage analysis) is handled by a dedicated external module. This file acts solely as an orchestration layer, composing their outputs into the final dict rather than implementing any extraction logic itself.
- **Relative path normalization**: All file paths stored in the output use forward-slash-normalized relative paths (`os.relpath` + `replace("\\", "/")`) to ensure cross-platform consistency in the JSON output.

## Definition Design Specifications

# Definition Design Specifications

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
| `project_dep_list` | `list[dict]` | Pre-built dependency list from the pipeline; each dict contains at minimum `"file"` and `"callers"` keys |

**Return type:** `dict` with the following fixed keys:

| Key | Value type | Description |
|---|---|---|
| `"file"` | `str` | Project-root-relative path of the analyzed file (forward-slash normalized) |
| `"definitions"` | `list[dict]` | All named definitions found in the file, each with `name`, `type`, `start_line`, `end_line`, `context` |
| `"callee_usages"` | `list[dict]` | Usage records for project-internal symbols this file calls/references |
| `"caller_usages"` | `list[dict]` | Usage records describing where this file's definitions are used by other project files |

---

### Responsibility

Orchestrates the full per-file analysis pipeline—parsing, definition extraction, import resolution, and bidirectional usage tracking—and returns the structured result that becomes the source data for `file_dependencies.json`.

### When to Use

Called once per project file by the pipeline (via `process_all_files`) after the project-level dependency list has already been constructed.

---

### Design Decisions

- **Relative path normalization:** The target file path is converted to a project-root-relative path with forward slashes at the start, ensuring cross-platform consistency in the output keys and lookups.
- **Language-gated import analysis:** Import extraction and usage tracking are skipped entirely when `get_import_params` returns `(None, None)`, allowing graceful handling of file types with no defined import query. The `usage_list` and `caller_usages` fields remain empty lists in that case rather than being absent.
- **Definition context embedding:** Each definition record includes a `"context"` field containing the raw source lines spanning the definition's full line range, extracted by slicing the decoded content lines. This avoids a second file read.
- **Project file set construction:** A `set[str]` of relative file paths is derived inline from `project_dep_list` to enable O(1) membership tests during symbol-to-file resolution.
- **Separation of callee and caller directions:** Outbound symbol usage (`callee_usages`) and inbound usage by other files (`caller_usages`) are computed by separate functions (`build_usage_info_list` and `build_caller_usages` respectively), each consuming different subsets of the shared `project_dep_list`.

---

### Constraints & Edge Cases

- `target_file` must be an absolute path readable by `parse_file`; no validation is performed inside this function.
- `project_dep_list` must already be fully populated before this function is called; it is consumed read-only for file-set construction and caller lookup, but `build_caller_usages` searches it by `"file"` key equality.
- For file extensions not present in `DEFINITION_DICTS`, `definition_dict` resolves to `None`, which is passed directly to `extract_definitions`—the behavior for a `None` dict is delegated to that function.
- The `content` bytes returned by `parse_file` are decoded as UTF-8; files with non-UTF-8 encodings will raise a `UnicodeDecodeError`.
- Line slicing for `"context"` uses a 1-based `start_line` adjusted to a 0-based index; a definition reported at `start_line == end_line` produces a single-line context string.
- `symbol_to_file_map` is mutated by `build_usage_info_list` (typed alias entries are added to it); this mutation does not affect subsequent steps because the map is not reused after that call.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to read and parse the target source file into a tree-sitter AST root node and raw byte content.

- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to traverse the AST and produce a list of named definitions (functions, classes, variables, etc.) from the target file.

- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : uses `build_usage_info_list` to identify where project-internal imported symbols are used within the target file, and `build_caller_usages` to collect locations in other project files where symbols defined in the target file are used.

- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/import_to_path.py` : uses `get_import_params` to retrieve the tree-sitter `Language` object and import query string for the target file's extension, and `build_symbol_to_file_map` to construct a mapping from imported symbol names to their definition file paths.

- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/extractors/imports.py` : uses `extract_imports` to parse import statements from the AST into structured `ImportInfo` objects, which are then consumed by `build_symbol_to_file_map`.

- `codetwine/file_analyzer_py/file_analyzer.py` → `codetwine/config/settings.py` : uses `DEFINITION_DICTS` to retrieve the per-language definition extraction configuration dict keyed by file extension.

## Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/file_analyzer_py/file_analyzer.py` : calls `get_file_dependencies` as part of a per-file analysis pipeline, passing the absolute file path, project root directory, and project dependency list to obtain the structured dependency result dict (definitions, callee_usages, caller_usages) for each file being processed.

## Dependency Direction

All relationships are **unidirectional**:

- `file_analyzer.py` depends on `ts_parser.py`, `definitions.py`, `usage_analysis.py`, `import_to_path.py`, `imports.py`, and `settings.py` — none of these modules import from `file_analyzer.py`.
- `pipeline.py` depends on `file_analyzer.py` — `file_analyzer.py` does not import from `pipeline.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute file path string |
| `project_dir` | Caller (`pipeline.py`) | Absolute directory path string |
| `project_dep_list` | Caller (`pipeline.py`), produced by `save_project_dependencies` | `list[dict]`, each dict containing at minimum `"file"` and `"callers"` keys |
| File content | Disk read via `parse_file` | Raw bytes, decoded to UTF-8 for line splitting |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | `dict[str, dict[str, str]]` mapping file extension to definition extraction config |

---

## 2. Transformation Overview

### Stage 1 — Path and Extension Normalization
`target_file` is converted to a project-relative path (`target_file_rel`) using `project_dir`, and the file extension is extracted. The extension is used to look up `definition_dict` from `DEFINITION_DICTS`, which may be `None` for unsupported languages.

### Stage 2 — AST Parsing
`parse_file(target_file)` reads the file from disk and returns a tree-sitter AST root node (`root_node`) and the raw file content as bytes. The bytes are decoded and split into lines (`content_lines`) for later source extraction.

### Stage 3 — Definition Extraction
`extract_definitions(root_node, definition_dict)` traverses the AST via BFS and returns a list of `DefinitionInfo` objects. Each is transformed into a plain dict by pairing metadata fields with a `context` string sliced from `content_lines` using the definition's line range.

### Stage 4 — Import Parameter Resolution
`get_import_params(file_ext)` returns a `(language, import_query_str)` tuple. If either value is `None` (unsupported language), Stages 5–7 are skipped entirely and `usage_list` and `caller_usages` remain empty lists.

### Stage 5 — Symbol-to-File Map Construction
The set of all project-relative file paths is built from `project_dep_list`. Then `extract_imports(root_node, language, import_query_str)` parses import statements from the AST into `ImportInfo` objects. These are passed to `build_symbol_to_file_map`, which resolves module strings to project-internal file paths and returns two dicts: `symbol_to_file_map` (imported name → definition file path) and `alias_to_original` (alias name → original name).

### Stage 6 — Callee Usage Analysis
`build_usage_info_list` walks the AST to find occurrences of names present in `symbol_to_file_map`, groups them by `(source_file, name)`, and enriches each group with the definition's source code retrieved from disk. The result is a list of usage dicts (`usage_list`).

### Stage 7 — Caller Usage Analysis
`build_caller_usages` consults `project_dep_list` to find which other project files import from `target_file_rel`, then for each caller re-parses its AST, extracts its imports, identifies which names originate from the target, and collects the line numbers where those names are used. The result is a list of usage dicts (`caller_usages`).

### Stage 8 — Result Assembly
All collected data is assembled into a single return dict keyed by `"file"`, `"definitions"`, `"callee_usages"`, and `"caller_usages"`.

---

## 3. Outputs

The function returns a single `dict` to the caller in `pipeline.py`. No files are written and there are no side effects within this module itself.

| Key | Type | Content |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file |
| `"definitions"` | `list[dict]` | Extracted definitions with source context |
| `"callee_usages"` | `list[dict]` | Usage locations of names this file imports from other project files |
| `"caller_usages"` | `list[dict]` | Usage locations of names defined here, as used in other project files |

---

## 4. Key Data Structures

### `definition_list` entry (produced internally, returned under `"definitions"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the defined symbol |
| `"type"` | `str` | AST node type of the definition (e.g. `"function_definition"`) |
| `"start_line"` | `int` | First line of the definition (1-based) |
| `"end_line"` | `int` | Last line of the definition (1-based) |
| `"context"` | `str` | Raw source code of the definition extracted from `content_lines` |

### `project_dep_list` entry (input)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative file path |
| `"callers"` | `list[str]` | Relative paths of files that import from this file |

### `symbol_to_file_map` (intermediate)
| Field / Key | Type | Purpose |
|---|---|---|
| imported name | `str` (key) | Symbol name as referenced in source code |
| definition file path | `str` (value) | Project-relative path of the file where the symbol is defined |

### `alias_to_original` (intermediate)
| Field / Key | Type | Purpose |
|---|---|---|
| alias name | `str` (key) | The name used locally in the file (the alias) |
| original name | `str` (value) | The name as exported from the source module |

### `callee_usages` entry (returned under `"callee_usages"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers where the name is used |
| `"name"` | `str` | The used name (possibly with attribute access, e.g. `"helper.process"`) |
| `"from"` | `str` | Project-relative path of the file where the name is defined |
| `"target_context"` | `str` | Source code of the definition in the dependency file |

### `caller_usages` entry (returned under `"caller_usages"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers where the name is used in the caller |
| `"name"` | `str` | The name defined in the target file that is being used |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Source code snippet surrounding the usage location in the caller |

## Error Handling

# Error Handling

## 1. Overall Strategy

`file_analyzer.py` follows a **delegation-and-trust** strategy: it contains no explicit `try-except` blocks of its own, instead delegating all error handling entirely to its dependency modules. The function assumes that its callers (`pipeline.py`) and its dependencies (`parse_file`, `extract_definitions`, `build_usage_info_list`, etc.) handle or surface any exceptions that arise. If a dependency raises an unhandled exception, it propagates upward uncaught — a **fail-fast** posture at this layer.

Graceful degradation is expressed structurally rather than through exception handling: import and usage analysis is conditionally skipped by branching on the return value of `get_import_params`. When a language is unsupported, `usage_list` and `caller_usages` remain as empty lists and the function returns a valid (partial) result dict without error.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (import analysis) | `get_import_params` returns `(None, None)` for an unrecognized extension | The `if language and import_query_str:` branch is skipped; `usage_list` and `caller_usages` default to empty lists | Yes | `callee_usages` and `caller_usages` fields are empty in the output dict; `definitions` is still populated |
| Unsupported file extension (definition extraction) | `DEFINITION_DICTS.get(file_ext)` returns `None` | `definition_dict` is `None`; passed to `extract_definitions`, which must handle it | Delegated to dependency | Depends on `extract_definitions` behavior |
| File read or parse failure | `parse_file` cannot read or parse `target_file` | No local handling; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |
| AST extraction failure | `extract_definitions` or `build_usage_info_list` raises an exception | No local handling; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |
| Symbol resolution failure | `build_symbol_to_file_map` cannot resolve an import to a project file | Handled inside `build_symbol_to_file_map`; unresolvable entries are silently skipped | Yes (within dependency) | Affected symbols absent from `symbol_to_file_map`; no usage entries generated for them |
| Caller file read failure | A caller file cannot be opened during `build_caller_usages` | Handled inside `build_caller_usages` (catches `OSError`, `UnicodeDecodeError`) | Yes (within dependency) | `usage_context` may be absent for affected caller entries |

---

## 3. Design Notes

- **No defensive wrapping at the orchestration layer:** `get_file_dependencies` is a data-assembly coordinator. It trusts each dependency to enforce its own contracts and does not introduce redundant catch blocks that would obscure the origin of failures.
- **Structural fallback over exception fallback:** The primary resilience mechanism is the conditional guard on `language and import_query_str`. This cleanly handles the "unsupported language" case without exceptions, ensuring the output dict is always structurally complete even when analysis is partial.
- **Implicit coupling to dependency error policies:** Because all exception handling is delegated, the effective error handling behavior of `get_file_dependencies` is determined by the combined policies of its dependencies (e.g., `build_caller_usages` catching `OSError`/`UnicodeDecodeError`, `parse_file` failing fast on unreadable files).

## Summary

**file_analyzer.py** orchestrates per-file dependency analysis by composing outputs from parsing, extraction, and resolution modules into a structured record.

**Public function:** `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`

**Key structures produced:** dict with keys `"file"` (str), `"definitions"` (list[dict] with name/type/start\_line/end\_line/context), `"callee_usages"` (list[dict] with lines/name/from/target\_context), `"caller_usages"` (list[dict] with lines/name/file/usage\_context).

**Consumes:** `project_dep_list` (list[dict] with `"file"` and `"callers"` keys).
