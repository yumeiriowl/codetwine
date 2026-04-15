# Design Document: codetwine/file_analyzer.py

# Overview & Purpose

## 1. Module Summary

Orchestrates per-file dependency analysis by combining AST parsing, definition extraction, import resolution, and usage tracking into a single structured result that represents a file's complete inbound and outbound dependency information.

## 2. When to Use This Module

- **Building the project dependency dataset**: Call `get_file_dependencies(target_file, project_dir, project_dep_list)` from the pipeline (e.g., `codetwine/pipeline.py`) once per source file to obtain the structured dict written into `file_dependencies.json`.
- **Retrieving definitions declared in a file**: The returned dict's `"definitions"` key provides every named definition (functions, classes, variables, etc.) with its type, line range, and source code context—without needing to invoke the parser or extractor separately.
- **Retrieving callee usages (outbound dependencies)**: The `"callee_usages"` key in the returned dict identifies which project-internal symbols this file uses, on which lines, and provides the target definition's source code.
- **Retrieving caller usages (inbound dependencies)**: The `"caller_usages"` key identifies which other project files reference symbols defined in this file, including the usage line numbers and surrounding context.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` | Parses a source file, extracts its definitions and import-based usages, collects caller usages from other project files, and returns all results as a single dict with keys `"file"`, `"definitions"`, `"callee_usages"`, and `"caller_usages"`. |

### Returned dict structure

| Key | Value type | Content |
|---|---|---|
| `"file"` | `str` | Relative path of the analyzed file from the project root |
| `"definitions"` | `list[dict]` | Each entry: `name`, `type`, `start_line`, `end_line`, `context` (source text) |
| `"callee_usages"` | `list[dict]` | Each entry: `lines`, `name`, `from`, `target_context` |
| `"caller_usages"` | `list[dict]` | Each entry: `lines`, `name`, `file`, `usage_context` |

## 4. Design Decisions

- **Language-gated import analysis**: Import resolution and usage tracking are only performed when `get_import_params` returns a non-`None` language and query string. Files whose extension is unsupported by the import subsystem receive empty `callee_usages` and `caller_usages` lists, while definition extraction still runs unconditionally.
- **Separation of outbound and inbound usages**: Callee usages (what this file depends on) and caller usages (what depends on this file) are computed independently—callee usages are derived from the current file's own AST, while caller usages are derived by scanning other files listed in `project_dep_list`—keeping each direction's logic in its dedicated extractor module.
- **Relative path normalization**: All file paths in the returned dict use forward-slash separators regardless of OS, achieved via `os.path.relpath` followed by `replace("\\", "/")`.

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
| `project_dep_list` | `list[dict]` | List of per-file dependency records produced by `save_project_dependencies`; each dict contains at minimum `"file"` and `"callers"` keys |

**Return type:** `dict` with the following fixed keys:

| Key | Value type | Description |
|---|---|---|
| `"file"` | `str` | Relative path of the analyzed file from `project_dir`, with backslashes normalized to forward slashes |
| `"definitions"` | `list[dict]` | Definitions extracted from the file (see definition record structure below) |
| `"callee_usages"` | `list[dict]` | Usage records describing where this file uses symbols from other project files |
| `"caller_usages"` | `list[dict]` | Usage records describing where other project files use symbols defined in this file |

**Definition record structure** (each element of `"definitions"`):

| Key | Type | Description |
|---|---|---|
| `"name"` | `str` | Identifier name of the definition |
| `"type"` | `str` | AST node type category (e.g., `"function_definition"`) |
| `"start_line"` | `int` | 1-based line number where the definition begins |
| `"end_line"` | `int` | 1-based line number where the definition ends |
| `"context"` | `str` | Raw source text spanning from `start_line` to `end_line` (inclusive), joined with newlines |

---

### Responsibility

Orchestrates the full per-file analysis pipeline—definition extraction, import resolution, callee usage tracking, and caller usage tracking—and packages results into a single dict that becomes a record in `file_dependencies.json`.

### When to use

Called once per project file by `pipeline.py`'s `process_all_files` logic, passing the file's absolute path, the project root, and the pre-built project-wide dependency list.

---

### Design decisions

- **`project_file_set` is derived locally** from `project_dep_list` rather than accepted as a parameter, keeping the public signature minimal while still supporting efficient membership tests.
- **Import and usage analysis is gated** on `language and import_query_str` being truthy; files whose extension is not registered in `IMPORT_QUERIES` (unsupported languages) receive empty `callee_usages` and `caller_usages` lists rather than causing an error.
- **Definition extraction is unconditional**, relying only on `DEFINITION_DICTS.get(file_ext)` which may return `None`; `extract_definitions` is always called regardless of language support status, accepting `None` as `definition_dict`.
- **Path normalization** converts OS-native separators to forward slashes on the relative path, ensuring cross-platform consistency in output JSON.
- **Source-root detection** (`detect_source_roots`) is performed per file call so that it correctly reflects the full `project_file_set` assembled from `project_dep_list`.

---

### Constraints & edge cases

- `target_file` must be an absolute path readable by the file system; `parse_file` will raise if the file cannot be opened.
- File content is decoded as UTF-8; files with other encodings will raise `UnicodeDecodeError` at the `content.decode("utf-8")` step.
- If `file_ext` is not present in `DEFINITION_DICTS`, `definition_dict` is `None`, and the behavior of `extract_definitions` with a `None` dict is determined by that function's own handling.
- `caller_usages` depends on `project_dep_list` being populated with correct `"callers"` fields; missing or stale data in `project_dep_list` will silently produce an empty or incomplete `caller_usages` list.
- The function has no retry or partial-failure recovery; an exception in any sub-call propagates to the caller.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse the target source file into a tree-sitter AST root node and raw byte content.

- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to extract named definitions (functions, classes, variables, etc.) from the parsed AST, supplying per-language `definition_dict` settings.

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` (indirectly via `build_symbol_to_file_map` inputs) to parse import statements from the AST into structured `ImportInfo` objects.

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : Uses `build_usage_info_list` to produce callee usage records (where this file uses symbols imported from other project files) and `build_caller_usages` to produce caller usage records (where other project files use symbols defined in this file).

- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : Uses `get_import_params` to retrieve the tree-sitter `Language` object and import query string for the file's extension; `detect_source_roots` to identify source root prefixes present in the project; and `build_symbol_to_file_map` to construct the mapping from imported symbol names to their definition file paths.

- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` to retrieve the per-language definition extraction configuration dict keyed by file extension.

## Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : Calls `get_file_dependencies` for each project file, passing the absolute file path, project directory, and the project-wide dependency list, to obtain the per-file analysis result (definitions, callee usages, caller usages) that is written to `file_dependencies.json`.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : unidirectional

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute path string of the file to analyze |
| `project_dir` | Caller (`pipeline.py`) | Absolute path string of the project root |
| `project_dep_list` | Caller (`pipeline.py`) | List of dicts, each containing at minimum `"file"` and `"callers"` keys, produced by `save_project_dependencies` |
| File content | Disk read via `parse_file` | Raw bytes read from `target_file` |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | Dict mapping file extension strings to per-language definition extraction configuration dicts |

---

## 2. Transformation Overview

### Stage 1 — Path and Extension Normalization
`target_file` is converted to a project-relative path (`target_file_rel`) and its extension is extracted. The extension is used to look up `definition_dict` from `DEFINITION_DICTS`, which may be `None` for unsupported languages.

### Stage 2 — Parsing
`parse_file(target_file)` reads the file from disk and produces an AST root node (`root_node`) and raw byte content. The byte content is decoded to UTF-8 and split into lines (`content_lines`) for later source extraction.

### Stage 3 — Definition Extraction
`extract_definitions(root_node, definition_dict)` traverses the AST and returns a list of `DefinitionInfo` objects. Each object is then transformed into a plain dict by joining the corresponding source lines from `content_lines` as the `"context"` field. The result is `definition_list`.

### Stage 4 — Import Parameter Resolution
`get_import_params(file_ext)` returns a `(language, import_query_str)` pair. If both are `None` (unsupported language), Stages 5–8 are skipped entirely; `usage_list` and `caller_usages` remain empty lists.

### Stage 5 — Project File Set and Source Root Detection
All `"file"` values from `project_dep_list` are collected into `project_file_set` (a set of relative paths). `detect_source_roots(project_file_set)` checks for known source-root prefixes, producing `source_root_set`.

### Stage 6 — Symbol-to-File Mapping
`extract_imports(root_node, language, import_query_str)` parses import statements from the AST into a list of `ImportInfo` objects. These are passed to `build_symbol_to_file_map`, which resolves each imported name to the project-internal file that defines it, yielding `symbol_to_file_map` (imported name → file path) and `alias_to_original` (alias name → original name).

### Stage 7 — Callee Usage Analysis
`build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` scans the AST for identifiers matching tracked symbols, groups usage occurrences by `(source_file, name)`, and attaches the definition source code from the target file. The result is `usage_list`.

### Stage 8 — Caller Usage Analysis
`build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set)` iterates over all project files that are known callers of `target_file_rel`, re-parses each caller file, resolves which names it imports from the target, and collects the lines where those names are used. The result is `caller_usages`.

### Stage 9 — Result Assembly
The four pieces of data are assembled into a single return dict.

---

## 3. Outputs

The function returns a single dict with the following structure. No file writes or side effects occur in this module directly.

| Key | Type | Content |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file |
| `"definitions"` | `list[dict]` | One entry per definition found in the file |
| `"callee_usages"` | `list[dict]` | Usage records for symbols this file imports from other project files |
| `"caller_usages"` | `list[dict]` | Usage records for symbols defined here that are used in other project files |

---

## 4. Key Data Structures

### `definition_list` entry (element of `"definitions"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Identifier name of the definition |
| `"type"` | `str` | AST node type (e.g. `"function_definition"`, `"class_definition"`) |
| `"start_line"` | `int` | 1-based line number where the definition begins |
| `"end_line"` | `int` | 1-based line number where the definition ends |
| `"context"` | `str` | Raw source code text spanning `start_line` to `end_line` |

### `project_dep_list` entry (input, per element)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative path of a file in the project |
| `"callers"` | `list[str]` | Relative paths of files that import from this file |

### `project_file_set`
| Type | Purpose |
|---|---|
| `set[str]` | Flat set of project-relative file paths, derived from `project_dep_list`; used for import resolution and source-root detection |

### `symbol_to_file_map`
| Field / Key | Type | Purpose |
|---|---|---|
| `<imported name>` | `str` (key) | The name as it appears in the current file (or alias) |
| `<definition file path>` | `str` (value) | Project-relative path of the file where that name is defined |

### `alias_to_original`
| Field / Key | Type | Purpose |
|---|---|---|
| `<alias name>` | `str` (key) | The local alias used in the current file |
| `<original name>` | `str` (value) | The original name in the source module |

### `usage_list` entry (element of `"callee_usages"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers where the name is used |
| `"name"` | `str` | The used symbol name (possibly with attribute access, e.g. `"module.func"`) |
| `"from"` | `str` | Project-relative path of the file where the symbol is defined |
| `"target_context"` | `str` | Source code of the definition extracted from the dependency file |

### `caller_usages` entry (element of `"caller_usages"`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers in the caller file where the name is used |
| `"name"` | `str` | The symbol name as referenced in the caller |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Source code snippet surrounding the usage location in the caller file |

# Error Handling

## 1. Overall Strategy

`file_analyzer.py` adopts a **delegation-without-guard** policy: the function `get_file_dependencies` contains no explicit error handling of its own. All error propagation is left entirely to the caller (`pipeline.py`) and to the dependency modules invoked. Within this file, the only form of conditional logic that resembles defensive handling is the **feature-gate pattern**: import and usage analysis is skipped entirely (producing empty lists) when `get_import_params` returns `(None, None)`, meaning the file's language is unsupported. This constitutes graceful degradation for unsupported languages, while all other failures are allowed to propagate upward uncaught.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported language / missing import config | `get_import_params` returns `(None, None)` for the file extension | Import and usage analysis blocks are skipped; `usage_list` and `caller_usages` remain empty lists | Yes | Output dict is returned with empty `callee_usages` and `caller_usages`; `definitions` are still populated |
| File I/O error during parsing | `parse_file` cannot read the target file (e.g., missing file, permission error) | No handling; exception propagates to caller | No | Entire call to `get_file_dependencies` aborts |
| AST parsing failure | `parse_file` or tree-sitter internals raise an exception | No handling; exception propagates to caller | No | Entire call to `get_file_dependencies` aborts |
| Definition extraction failure | `extract_definitions` raises an exception | No handling; exception propagates to caller | No | Entire call to `get_file_dependencies` aborts |
| Import resolution failure | `build_symbol_to_file_map` or `resolve_module_to_project_path` raises an exception | No handling; exception propagates to caller | No | Entire call to `get_file_dependencies` aborts |
| Usage analysis failure | `build_usage_info_list` or `build_caller_usages` raises an exception | No handling; exception propagates to caller | No | Entire call to `get_file_dependencies` aborts |
| Content decoding error | `content.decode("utf-8")` fails on non-UTF-8 file bytes | No handling; exception propagates to caller | No | Entire call to `get_file_dependencies` aborts |

---

## 3. Design Notes

The absence of try-except blocks in this file reflects a conscious architectural separation of concerns: `file_analyzer.py` is a **pure orchestration layer** that assembles results from dependency modules, and those dependency modules are individually responsible for their own internal error handling (e.g., `build_caller_usages` in `usage_analysis.py` catches `OSError`/`UnicodeDecodeError` internally). The only error resilience implemented directly in this file is the language-support gate, which prevents exceptions from being raised in the first place when no import analysis infrastructure exists for a given extension. All other failure modes are treated as unrecoverable at this level and bubble up to the pipeline layer that invokes `get_file_dependencies`.

# Summary

`file_analyzer.py` orchestrates per-file dependency analysis into a single structured result. Public function: `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`. Returns dict with keys: `"file"` (str), `"definitions"` (list[dict] with name/type/start_line/end_line/context), `"callee_usages"` (list[dict] with lines/name/from/target_context), `"caller_usages"` (list[dict] with lines/name/file/usage_context). Consumes `project_dep_list` (list[dict] with "file"/"callers" keys).
