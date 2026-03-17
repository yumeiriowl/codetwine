# Design Document: codetwine/file_analyzer.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`file_analyzer.py` is the per-file analysis orchestrator within the codetwine pipeline. It exists as a separate module to encapsulate the complete analysis workflow for a single source file, aggregating the outputs of several independent subsystems (parsing, definition extraction, import resolution, and usage tracking) into a single normalized result dict. Its separation isolates the "analyze one file" concern from both the higher-level pipeline loop (in `pipeline.py`) and the lower-level extraction primitives, acting as a thin coordination layer that wires those primitives together in the correct order.

The file is called once per source file by `pipeline.py`'s `get_file_dependencies` invocation and produces the structured data that ultimately populates `file_dependencies.json`.

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` with keys `"file"`, `"definitions"`, `"callee_usages"`, `"caller_usages"` | Parses one source file, extracts its definitions and import-based usages (both callee and caller directions), and returns all results as a single structured dict |

## Design Decisions

- **Conditional import/usage analysis**: Import resolution and usage analysis are guarded by `if language and import_query_str`, delegating the "is this language supported?" decision entirely to `get_import_params`. Files in unsupported languages still produce a valid result with empty `callee_usages` and `caller_usages`, ensuring the output schema is always uniform.

- **Relative path normalization**: All file paths in the output use forward-slash-normalized relative paths (`os.path.relpath` + `.replace("\\", "/")`) so results are consistent across operating systems.

- **Definition context embedding**: Source lines of each definition are sliced directly from the decoded file content (`content_lines[d.start_line - 1 : d.end_line]`) and embedded as `"context"` strings, co-locating definition metadata and source text in a single list entry.

- **Project file set materialized locally**: The set of all project file paths is built from `project_dep_list` inside this function rather than received as a parameter, keeping the public interface minimal while providing the set-based lookup that `build_symbol_to_file_map` requires.

## Definition Design Specifications

# Definition Design Specifications

## `get_file_dependencies`

**Signature:**
```
get_file_dependencies(
    target_file: str,
    project_dir: str,
    project_dep_list: list[dict],
) -> dict
```

**Arguments:**
- `target_file`: Absolute path of the file to analyze.
- `project_dir`: Absolute path to the project root, used for computing relative paths and resolving imports.
- `project_dep_list`: The full project-level dependency list produced by a prior pipeline stage (`save_project_dependencies`). Each entry is a dict containing at minimum `"file"` and `"callers"` keys; used both to build the set of known project files and to identify which files reference the current target.

**Return value:** A `dict` with four keys:
- `"file"`: Project-relative path of the analyzed file (POSIX separators).
- `"definitions"`: List of dicts, each describing one definition found in the file (`name`, `type`, `start_line`, `end_line`, `context`).
- `"callee_usages"`: List of usage records describing where project-internal imported names are used in this file, with attached definition source.
- `"caller_usages"`: List of usage records describing where names defined in this file are used by other project files.

**Responsibility:** This function is the per-file orchestration entry point for dependency analysis. It composes the parsing, definition extraction, import resolution, and usage tracking pipeline for a single file into one structured result that feeds `file_dependencies.json`.

**Design decisions:**

- The `"file"` key is stored as a POSIX-style relative path (backslashes replaced) so that output is platform-independent and consistent with the paths stored in `project_dep_list`.
- `definition_dict` is looked up from `DEFINITION_DICTS` by file extension; a `None` result for unsupported languages is passed directly to `extract_definitions`, which gracefully handles it.
- Import and usage analysis (callee and caller) is gated on whether `get_import_params` returns a valid `(language, import_query_str)` pair. Files whose extensions have no registered import query produce empty `callee_usages` and `caller_usages` rather than raising an error.
- `project_file_set` is materialized from `project_dep_list` within this function rather than passed in, keeping the public interface minimal and ensuring consistency with the same `project_dep_list` used by `build_caller_usages`.
- The `context` field of each definition entry is the raw source text sliced from the decoded content lines using 1-based `start_line`/`end_line` values, so consumers receive complete source snippets without needing a separate file read.

**Edge cases and constraints:**
- `target_file` must be a path parseable by tree-sitter; if the extension is unrecognized by `parse_file`, an error propagates unhandled.
- When `file_ext` is not present in `DEFINITION_DICTS`, `definition_dict` is `None`, and the resulting `definition_list` will be empty rather than raising an error, provided `extract_definitions` handles a `None` dict.
- If `project_dep_list` contains no entry for `target_file_rel`, `build_caller_usages` will produce an empty `caller_usages` list (no callers found).
- File content is decoded as UTF-8; files with non-UTF-8 encoding will raise a `UnicodeDecodeError` that is not caught here.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **`codetwine/parsers/ts_parser.py`** (`parse_file`): Used to parse the target source file into a tree-sitter AST root node and raw byte content, which serve as the foundation for all subsequent definition and usage extraction steps.

- **`codetwine/extractors/definitions.py`** (`extract_definitions`): Used to walk the AST and extract all named definitions (functions, classes, variables, etc.) from the target file, along with their line ranges. The results are combined with source text lines to produce the `definitions` output.

- **`codetwine/extractors/imports.py`** (`extract_imports`): Used to parse import statements from the target file's AST into structured `ImportInfo` records, which are then handed off to `build_symbol_to_file_map` to resolve imported names to their source files.

- **`codetwine/import_to_path.py`** (`get_import_params`, `build_symbol_to_file_map`): `get_import_params` is used to retrieve the language-specific tree-sitter `Language` object and import query string needed to perform import analysis; the result also gates whether import/usage analysis proceeds at all for a given file type. `build_symbol_to_file_map` is used to resolve extracted import information into a mapping of symbol names to their definition file paths, along with alias-to-original-name mappings, enabling downstream usage tracking.

- **`codetwine/extractors/usage_analysis.py`** (`build_usage_info_list`, `build_caller_usages`): `build_usage_info_list` is used to identify where imported project-internal symbols are referenced within the target file and attach their definition source code, producing the `callee_usages` output. `build_caller_usages` is used to scan other project files for locations where symbols defined in the target file are consumed, producing the `caller_usages` output.

- **`codetwine/config/settings.py`** (`DEFINITION_DICTS`): Used to look up the per-language definition extraction configuration for the target file's extension, which is passed to `extract_definitions` to control which AST node types are recognized as definitions.

---

### Dependents (what uses this file)

- **`codetwine/pipeline.py`** (`get_file_dependencies`): The pipeline calls `get_file_dependencies` once per project file as part of a full project analysis run, passing the target file path, project root, and the pre-built project dependency list. It consumes the returned dict — containing `file`, `definitions`, `callee_usages`, and `caller_usages` — to produce the final `file_dependencies.json` output.

**Direction of dependency**: Unidirectional. `pipeline.py` depends on `file_analyzer.py`; `file_analyzer.py` has no knowledge of `pipeline.py`.

## Data Flow

# Data Flow

## Input

| Parameter | Type | Source |
|-----------|------|--------|
| `target_file` | `str` (absolute path) | Caller (`pipeline.py`) |
| `project_dir` | `str` (absolute path) | Caller (`pipeline.py`) |
| `project_dep_list` | `list[dict]` | Output of `save_project_dependencies` via caller |

### `project_dep_list` entry structure
| Field | Description |
|-------|-------------|
| `"file"` | Relative path of a project file |
| `"callers"` | List of relative paths of files that import this file |

---

## Transformation Flow

```
target_file (abs path)
    │
    ├─► parse_file()           → root_node (AST), content (bytes)
    │
    ├─► content.splitlines()   → content_lines (list[str])
    │
    ├─► extract_definitions()  → list[DefinitionInfo]
    │       │
    │       └─► slice content_lines by [start_line-1 : end_line]
    │               → definition_list (list[dict])
    │
    └─► [if language supported]
            │
            ├─► project_dep_list → project_file_set (set[str] of relative paths)
            │
            ├─► extract_imports()         → import_info_list
            │
            ├─► build_symbol_to_file_map()
            │       → symbol_to_file_map { symbol_name → relative_file_path }
            │       → alias_to_original  { alias → original_name }
            │
            ├─► build_usage_info_list()   → usage_list (callee_usages)
            │
            └─► build_caller_usages()     → caller_usages
```

---

## Output

The function returns a single `dict`:

```python
{
    "file":          str,         # target_file relative to project_dir
    "definitions":   list[dict],  # definitions found in target_file
    "callee_usages": list[dict],  # usages of imported project symbols in target_file
    "caller_usages": list[dict],  # usages of target_file's symbols in other project files
}
```

Consumed by `get_file_dependencies` call site in `pipeline.py` as `dep_result`.

---

## Key Data Structure Schemas

### `definition_list` entry
| Field | Type | Description |
|-------|------|-------------|
| `"name"` | `str` | Symbol name |
| `"type"` | `str` | AST node type (e.g. `function_definition`) |
| `"start_line"` | `int` | 1-based start line |
| `"end_line"` | `int` | 1-based end line |
| `"context"` | `str` | Source text sliced from `content_lines` over the line range |

### `callee_usages` entry (from `build_usage_info_list`)
| Field | Description |
|-------|-------------|
| `"lines"` | Sorted list of line numbers where the symbol is used |
| `"name"` | Symbol name as used in source |
| `"from"` | Relative path of the file where the symbol is defined |
| `"target_context"` | Source code of the definition in the dependency file |

### `caller_usages` entry (from `build_caller_usages`)
| Field | Description |
|-------|-------------|
| `"lines"` | Sorted list of line numbers where the symbol is used |
| `"name"` | Symbol name |
| `"file"` | Relative path of the caller file |
| `"usage_context"` | Code snippet from the caller file around the usage location |

### Intermediate maps
| Variable | Type | Purpose |
|----------|------|---------|
| `project_file_set` | `set[str]` | Set of relative paths for all project files; built from `project_dep_list` to filter out non-project imports |
| `symbol_to_file_map` | `dict[str, str]` | Maps each imported symbol name to the relative path of its defining file; drives usage lookup |
| `alias_to_original` | `dict[str, str]` | Maps alias names to original names for correct definition resolution |

## Error Handling

# Error Handling

## Overall Strategy

`file_analyzer.py` adopts a **fail-fast** strategy. The function `get_file_dependencies` contains no explicit `try/except` blocks; all error handling is delegated entirely to the dependency modules it calls. If any dependency raises an exception—file I/O failure, parse error, missing language support, etc.—the exception propagates immediately to the caller (`pipeline.py`).

The one form of graceful degradation present is **conditional execution**: import and usage analysis is skipped entirely when `get_import_params` returns `(None, None)`, meaning the file's language is unsupported. In this case, `callee_usages` and `caller_usages` are returned as empty lists rather than triggering a failure, and the `definitions` list is still populated.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported file extension (no import query defined) | `get_import_params` returns `(None, None)`; the `if language and import_query_str:` guard causes the entire import/usage block to be skipped | `callee_usages` and `caller_usages` are empty; `definitions` is still attempted |
| File read or parse failure (e.g., unreadable file, invalid syntax) | No local handling; exceptions from `parse_file` propagate immediately to the caller | Entire `get_file_dependencies` call fails |
| Missing or unresolvable imports | Handled inside `build_symbol_to_file_map` and `resolve_module_to_project_path`; unresolvable entries are silently omitted | Affected symbols are absent from `callee_usages`; no exception raised in this file |
| Definition extraction failure (name extraction returns nothing) | Handled inside `extract_definitions` via BFS fallback; no exception surfaces to this file | Affected definitions may be absent from `definition_list` |
| Content decoding failure (`content.decode("utf-8")`) | No local handling; a `UnicodeDecodeError` propagates immediately | Entire `get_file_dependencies` call fails |

## Design Considerations

The absence of local exception handling reflects an architectural assumption: `get_file_dependencies` is one step in a pipeline orchestrated by `pipeline.py`, and it is the pipeline's responsibility to decide how to handle per-file failures (e.g., logging and continuing vs. aborting). This keeps `file_analyzer.py` focused on its core transformation logic without embedding recovery policy. The single guarded path (language support check) is a deliberate data-driven gate rather than error recovery—it reflects a known, expected condition (not all extensions support import analysis) rather than an exceptional failure state.

## Summary

`file_analyzer.py` orchestrates per-file dependency analysis, called once per file by `pipeline.py`. Its sole public function, `get_file_dependencies(target_file, project_dir, project_dep_list)`, composes parsing, definition extraction, import resolution, and usage tracking into a single result dict. Output keys: `"file"` (POSIX-relative path), `"definitions"` (named symbols with source context), `"callee_usages"` (imported project symbols used in this file), `"caller_usages"` (this file's symbols used elsewhere). Import/usage analysis is skipped for unsupported languages, returning empty lists. Errors propagate to the caller without local handling.
