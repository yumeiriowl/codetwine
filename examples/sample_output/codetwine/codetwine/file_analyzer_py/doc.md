# Design Document: codetwine/file_analyzer.py

## Overview & Purpose

## 1. Module Summary

Analyzes a single source file to extract its definitions, outbound symbol usages (callee usages), and inbound usages from other project files (caller usages), returning a structured dependency record used to build `file_dependencies.json`.

## 2. When to Use This Module

- **Building per-file dependency records**: Call `get_file_dependencies(target_file, project_dir, project_dep_list)` from the pipeline (e.g., `codetwine/pipeline.py`) for each file in the project to obtain a dict describing what that file defines and how it interacts with other project files.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` | Parses a source file, extracts its definitions, resolves imports to project-internal symbol mappings, and collects both outbound (callee) and inbound (caller) usages. Returns a dict with keys `file`, `definitions`, `callee_usages`, and `caller_usages`. |

**Return dict structure:**

| Key | Type | Content |
|---|---|---|
| `file` | `str` | Project-root-relative path of the analyzed file |
| `definitions` | `list[dict]` | Each definition's `name`, `type`, `start_line`, `end_line`, and `context` (source text) |
| `callee_usages` | `list[dict]` | Usages of symbols imported from other project files, with `lines`, `name`, `from`, and `target_context` |
| `caller_usages` | `list[dict]` | Locations in other project files that use symbols defined in this file, with `lines`, `name`, `file`, and `usage_context` |

## 4. Design Decisions

- **Unsupported language short-circuit**: When `get_import_params` returns `(None, None)` for an unrecognized file extension, all import and usage analysis is skipped, and `callee_usages` and `caller_usages` are returned as empty lists. Definition extraction still proceeds for supported AST languages.
- **Separation of callee and caller directions**: Outbound usage analysis (`build_usage_info_list`) operates on the current file's AST, while inbound usage analysis (`build_caller_usages`) iterates over other files listed in `project_dep_list`. This means the function handles both dependency directions within a single call, keeping the aggregation responsibility in one place rather than distributing it across the pipeline.
- **Relative path normalization**: All file paths in the returned dict use forward slashes and are relative to `project_dir`, regardless of OS path separator, ensuring consistent keys across platforms.

## Definition Design Specifications

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
| `project_dep_list` | `list[dict]` | Pre-built dependency list produced by `save_project_dependencies`; each entry contains at least `"file"` and `"callers"` keys |

**Return type:** `dict` with the following keys:

| Key | Type | Description |
|---|---|---|
| `"file"` | `str` | Project-root-relative path of the analyzed file (forward-slash normalized) |
| `"definitions"` | `list[dict]` | All named definitions extracted from the file, each with `name`, `type`, `start_line`, `end_line`, `context` |
| `"callee_usages"` | `list[dict]` | Usage records for project-internal symbols this file calls/references |
| `"caller_usages"` | `list[dict]` | Usage records for definitions in this file as seen from other project files |

---

### Responsibility

Orchestrates the full per-file analysis pipeline—definition extraction, import resolution, callee usage tracking, and caller usage collection—and returns all results as a single structured dict that feeds `file_dependencies.json`.

### When to Use

Called once per project file from `process_all_files` (in `codetwine/pipeline.py`) when building the project-wide dependency graph.

---

### Design Decisions

- **Path normalization:** The target file's relative path is computed with `os.path.relpath` and then has backslashes replaced by forward slashes, ensuring platform-independent path keys regardless of the operating system.

- **Language-gated analysis:** Import resolution, callee usage tracking, and caller usage collection are only performed when `get_import_params` returns a non-`None` `(language, import_query_str)` pair. Files in unsupported languages still receive definition extraction but have empty `callee_usages` and `caller_usages` lists rather than raising errors.

- **Definition context embedding:** Each definition entry includes a `"context"` field containing the raw source lines spanning `start_line` to `end_line` (inclusive). This is produced by splitting the decoded file bytes into lines and slicing—no secondary file read is required.

- **`project_file_set` construction:** The set of project-relative file paths is derived from `project_dep_list` entries rather than from a fresh directory scan, keeping the analysis consistent with the dependency list that was already computed upstream.

- **Separation of callee vs. caller direction:** `build_usage_info_list` finds where *this* file calls into other files (callee direction), while `build_caller_usages` finds where other files call into *this* file (caller direction). Both are delegated entirely to `usage_analysis.py`; `get_file_dependencies` only assembles inputs and collects outputs.

---

### Constraints & Edge Cases

- `target_file` must be an absolute path that `parse_file` can open; no fallback is provided for missing or unreadable files.
- `definition_dict` will be `None` for file extensions not registered in `DEFINITION_DICTS`, which is passed directly to `extract_definitions`—callers of that function must tolerate a `None` dict.
- If `project_dep_list` is empty or contains no entry matching `target_file_rel`, `build_caller_usages` returns an empty list (no error).
- The file's byte content is decoded as UTF-8; files with other encodings will raise a `UnicodeDecodeError`.
- `alias_to_original` is only populated when `language` and `import_query_str` are both available; it is not passed to `build_caller_usages`, which performs its own internal alias resolution.

## Dependency Description

### Dependencies (modules this file imports)

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to read and parse the target source file into a tree-sitter AST root node and raw byte content.

- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to traverse the AST and extract named definitions (functions, classes, variables, etc.) with their line ranges from the target file.

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` (indirectly via `build_symbol_to_file_map` and `build_caller_usages`) and directly through the imported symbols passed to `build_symbol_to_file_map`, to parse import statements from the AST into structured `ImportInfo` records.

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : Uses `build_usage_info_list` to produce callee usage records (where names imported from other project files are used in the target file), and `build_caller_usages` to produce caller usage records (where names defined in the target file are used in other project files).

- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : Uses `get_import_params` to retrieve the tree-sitter `Language` object and import query string for the target file's extension, and `build_symbol_to_file_map` to construct a mapping from imported symbol names to their definition file paths.

- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` to look up the per-language definition extraction configuration dictionary by file extension.

---

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : Calls `get_file_dependencies` for each file in the project to obtain per-file dependency data (definitions, callee usages, caller usages), which is used to build the project-wide `file_dependencies.json` output.

---

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` (one-way)
- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` (one-way)
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` (one-way)

None of the dependency or dependent modules import back from `codetwine/file_analyzer.py`, so no bidirectional relationships exist.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute path string to the file being analyzed |
| `project_dir` | Caller | Absolute path string to the project root |
| `project_dep_list` | Caller | List of dicts, each with at least `"file"` and `"callers"` keys, produced by a prior pipeline stage |
| File content | Disk (via `parse_file`) | Binary bytes, read and parsed into a tree-sitter AST |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | Dict mapping file extension strings to per-language definition extraction configs |
| Language/query params | `codetwine/import_to_path.py` via `get_import_params` | `(Language, import_query_str)` tuple or `(None, None)` for unsupported extensions |

---

## 2. Transformation Overview

### Stage 1 — Path and Language Resolution
`target_file` is converted to a project-relative path (`target_file_rel`) and its extension is extracted. The extension is used to look up `definition_dict` from `DEFINITION_DICTS` and to call `get_import_params`, which returns the tree-sitter `Language` object and import query string for the file's language (or `(None, None)` if unsupported).

### Stage 2 — Parsing
`parse_file(target_file)` reads the file from disk, parses it with tree-sitter, and returns `(root_node, content)`. The binary `content` is decoded to UTF-8 and split into lines for later source extraction.

### Stage 3 — Definition Extraction
`extract_definitions(root_node, definition_dict)` performs a BFS traversal of the AST and returns a sorted list of `DefinitionInfo` objects. Each object's `start_line`/`end_line` is used to slice `content_lines`, producing the `"context"` field. The result is serialized into a flat list of plain dicts (`definition_list`).

### Stage 4 — Import and Symbol Resolution (language-dependent)
If the language is supported (`language` and `import_query_str` are not `None`):

- A `project_file_set` is built from `project_dep_list` for fast membership lookup.
- `extract_imports(root_node, language, import_query_str)` walks the AST to extract all import statements as `ImportInfo` objects.
- `build_symbol_to_file_map(...)` resolves each imported module string to a project-relative file path and constructs two dicts: `symbol_to_file_map` (imported name → definition file) and `alias_to_original` (alias name → original name).

### Stage 5 — Callee Usage Analysis
`build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` scans the AST for occurrences of symbols present in `symbol_to_file_map`, groups usage line numbers by `(definition_file, name)`, and attaches the definition's source code as `"target_context"`. The result is a list of usage record dicts (`usage_list`).

### Stage 6 — Caller Usage Analysis
`build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set)` iterates over all other project files that are known to import from `target_file`, parses each caller, extracts its imports, matches names originating from `target_file`, and collects the line numbers and surrounding source snippets where those names appear. The result is a list of caller usage record dicts (`caller_usages`).

### Stage 7 — Assembly
The four collected structures are assembled into a single output dict and returned to the caller.

---

## 3. Outputs

The function returns a single `dict` to the caller in `pipeline.py`. No files are written and no side effects are produced by this module itself.

| Key | Type | Content |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file |
| `"definitions"` | `list[dict]` | All named definitions found in the file |
| `"callee_usages"` | `list[dict]` | Usages of names imported from other project files |
| `"caller_usages"` | `list[dict]` | Usages of this file's definitions in other project files |

---

## 4. Key Data Structures

### `definition_list` — element schema (`list[dict]`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Identifier name of the definition |
| `"type"` | `str` | AST node type (e.g. `"function_definition"`) |
| `"start_line"` | `int` | 1-based first line of the definition |
| `"end_line"` | `int` | 1-based last line of the definition |
| `"context"` | `str` | Raw source code spanning the definition's line range |

### `symbol_to_file_map` (`dict[str, str]`)
| Field / Key | Type | Purpose |
|---|---|---|
| imported symbol name | `str` (key) | The name as it appears in the current file's source |
| project-relative file path | `str` (value) | The file where that symbol is defined |

### `alias_to_original` (`dict[str, str]`)
| Field / Key | Type | Purpose |
|---|---|---|
| alias name | `str` (key) | The local alias used in the import statement |
| original name | `str` (value) | The name as defined in the source file |

### `usage_list` / `callee_usages` — element schema (`list[dict]`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers where the name is used |
| `"name"` | `str` | The symbol name as it appears in this file |
| `"from"` | `str` | Project-relative path of the file where the symbol is defined |
| `"target_context"` | `str` | Source code of the definition extracted from the dependency file |

### `caller_usages` — element schema (`list[dict]`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers within the caller file |
| `"name"` | `str` | The symbol name from this file being used by the caller |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Source code snippets surrounding the usage locations in the caller |

### `project_dep_list` — element schema (`list[dict]`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative path of a project file |
| `"callers"` | `list[str]` | Project-relative paths of files that import from this file |

### `project_file_set` (`set[str]`)
| Field / Key | Type | Purpose |
|---|---|---|
| project-relative file path | `str` | Flat set of all known project file paths, built from `project_dep_list` for O(1) membership testing |

## Error Handling

## 1. Overall Strategy

`file_analyzer.py` adopts a **no-explicit-error-handling / propagation-by-default** strategy. The function `get_file_dependencies` contains no try-except blocks of its own; all exceptions raised by called functions propagate directly to the caller (`pipeline.py`). Graceful degradation is achieved structurally rather than through exception handling: optional analysis branches (import parsing, usage analysis, caller usage collection) are guarded by a conditional check (`if language and import_query_str`) that silently skips those branches when a language is unsupported, leaving the corresponding output fields as empty lists without raising an error.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (no import query) | `get_import_params` returns `(None, None)` for an unrecognized or unsupported file extension | The `if language and import_query_str` branch is skipped entirely; `usage_list` and `caller_usages` remain empty lists | Yes | Import/usage analysis is omitted; definition extraction and output dict are still produced normally |
| Unsupported file extension (no definition dict) | `DEFINITION_DICTS.get(file_ext)` returns `None` | `definition_dict` is `None`; passed directly to `extract_definitions`, which must handle it internally | Yes (delegated) | Definition extraction behavior depends on `extract_definitions`'s own handling of a `None` dict |
| File I/O failure | `parse_file` cannot open or read `target_file` | Not caught; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |
| AST parse failure | `parse_file` encounters a tree-sitter parsing error | Not caught; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |
| Encoding error | `content.decode("utf-8")` fails on binary or non-UTF-8 file content | Not caught; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |
| Dependency resolution failure | Any exception raised inside `build_symbol_to_file_map`, `build_usage_info_list`, or `build_caller_usages` | Not caught; exception propagates to the caller | No | Entire `get_file_dependencies` call fails |

---

## 3. Design Notes

The absence of internal error handling reflects a deliberate **separation of concerns**: `get_file_dependencies` treats itself as a data-transformation function and delegates error recovery decisions entirely to its caller in `pipeline.py`. The only defensive logic present is the **language-capability gate** (`if language and import_query_str`), which is a normal control-flow guard rather than exception handling—it ensures that analysis steps dependent on language support are simply not attempted rather than failing at runtime. This means the function's partial-output guarantee (definitions are always attempted; import/usage analysis is conditional) is encoded in structure, not in exception recovery logic.

## Summary

**file_analyzer.py** orchestrates per-file dependency analysis for a single source file.

**Public function:** `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`

**Output dict keys:** `file` (str), `definitions` (list[dict] with name/type/start_line/end_line/context), `callee_usages` (list[dict] with lines/name/from/target_context), `caller_usages` (list[dict] with lines/name/file/usage_context).

Delegates to `parse_file`, `extract_definitions`, `build_symbol_to_file_map`, `build_usage_info_list`, and `build_caller_usages`.
