# Design Document: codetwine/file_analyzer.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

`file_analyzer.py` is the per-file analysis entry point within the CodeTwine pipeline. Its sole responsibility is to orchestrate a complete dependency analysis of a single source file by coordinating four distinct subsystems—AST parsing, definition extraction, import resolution, and usage tracking—and consolidating their outputs into a single structured dict. It exists as a separate file to isolate the file-level analysis unit from both the project-level pipeline (which calls it in a loop via `pipeline.py`) and the lower-level extractors, keeping orchestration logic independent of parsing and extraction concerns.

Given a target file path, it:
1. Parses the file into an AST via `ts_parser.py`
2. Extracts named definitions (functions, classes, variables, etc.) with their source text
3. Resolves imports to project-internal file paths and maps imported symbols to their origin files
4. Builds callee usages (symbols this file uses from other project files) and caller usages (locations in other files that reference symbols defined here)

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` with keys `"file"`, `"definitions"`, `"callee_usages"`, `"caller_usages"` | Orchestrates full per-file analysis and returns a single structured result dict suitable for serialization into `file_dependencies.json` |

## Design Decisions

- **Orchestration-only module**: The file contains no parsing, extraction, or resolution logic itself; it delegates entirely to `parse_file`, `extract_definitions`, `extract_imports`, `build_symbol_to_file_map`, `build_usage_info_list`, and `build_caller_usages`. This keeps the file thin and makes each subsystem independently testable.

- **Graceful degradation for unsupported languages**: Import and usage analysis is conditionally skipped when `get_import_params` returns `(None, None)`, leaving `callee_usages` and `caller_usages` as empty lists. Definition extraction always runs regardless of language support for imports, since `definition_dict` is looked up separately via `DEFINITION_DICTS`.

- **Relative path normalization**: All file paths in the output are expressed as forward-slash relative paths from `project_dir` (via `os.path.relpath` + `replace("\\", "/")`), ensuring cross-platform consistency in the output JSON.

- **Inline source text per definition**: The `context` field in each definition entry is computed directly from the decoded content lines using the 1-based `start_line`/`end_line` range returned by `extract_definitions`, avoiding a second file read by reusing the `content` bytes already returned by `parse_file`.

## Definition Design Specifications

# Definition Design Specifications

## `get_file_dependencies(target_file, project_dir, project_dep_list) -> dict`

**Arguments:**
- `target_file: str` — Absolute path of the source file to analyze.
- `project_dir: str` — Absolute path to the project root, used as the base for computing relative paths.
- `project_dep_list: list[dict]` — Pre-built dependency list for the entire project (output of `save_project_dependencies`), consumed for caller-usage resolution.

**Return value:** A `dict` with four keys:
- `"file"` — Project-root-relative path of the analyzed file (forward-slash normalized).
- `"definitions"` — List of dicts, each describing a named definition (name, type, start/end line, source context).
- `"callee_usages"` — List of dicts describing where this file references symbols imported from other project files.
- `"caller_usages"` — List of dicts describing where other project files reference symbols defined in this file.

**Responsibility:** Acts as the single orchestration point for per-file static analysis, composing AST parsing, definition extraction, import resolution, and bidirectional usage analysis into one cohesive result record that feeds `file_dependencies.json`.

**Design decisions:**

- The relative path is computed once and reused throughout (for map lookups, output keys, and as the identity passed to sub-functions), ensuring consistency across all callers.
- The `definition_dict` is looked up from `DEFINITION_DICTS` by file extension before calling `extract_definitions`, making language support purely configuration-driven with no language-specific branching inside this function.
- Definition `context` (source text) is assembled here rather than inside `extract_definitions`, because `extract_definitions` deliberately returns only structural metadata (`DefinitionInfo`) and remains unaware of raw content. The decoded content lines are sliced by `start_line`/`end_line` (1-based, end-inclusive) to produce the context string.
- Import and usage analysis (callee and caller) is gated behind `language and import_query_str` being non-`None`, so files in unsupported languages produce an empty `usage_list` and `caller_usages` without errors, rather than requiring the caller to pre-filter.
- `project_file_set` is materialized as a `set[str]` from `project_dep_list` locally within this function so that `build_symbol_to_file_map` and `build_caller_usages` receive O(1)-lookup membership structures.

**Edge cases and constraints:**
- `target_file` must be parseable by the tree-sitter parser registered for its extension; unsupported extensions will raise a `KeyError` inside `parse_file` (fail-fast, no recovery here).
- For file extensions absent from `DEFINITION_DICTS`, `definition_dict` is `None`, which is forwarded to `extract_definitions`; the behavior for a `None` dict is defined by `extract_definitions` itself.
- If `file_ext` is not registered in `IMPORT_QUERIES` or `TREE_SITTER_LANGUAGES`, `get_import_params` returns `(None, None)` and the entire import/usage branch is skipped; the returned dict still contains the `"definitions"` key populated.
- Backslashes in the relative path (Windows) are normalized to forward slashes to ensure cross-platform consistency in output.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **codetwine/parsers/ts_parser.py** (`parse_file`): Used to parse the target source file into a tree-sitter AST root node and raw byte content. This is the entry point for all subsequent AST-based analysis performed in this file.

- **codetwine/extractors/definitions.py** (`extract_definitions`): Used to traverse the parsed AST and extract named definitions (functions, classes, variables, etc.) from the target file. The results are enriched with source code context and stored as the `definitions` output.

- **codetwine/extractors/imports.py** (`extract_imports`): Used to parse import statements from the target file's AST into structured `ImportInfo` objects, which are then passed to `build_symbol_to_file_map` for resolution to project file paths.

- **codetwine/import_to_path.py** (`get_import_params`, `build_symbol_to_file_map`): `get_import_params` retrieves the tree-sitter `Language` object and query string appropriate for the file's extension, enabling import extraction. `build_symbol_to_file_map` resolves extracted import information into a mapping of symbol names to their defining project files, which drives usage analysis.

- **codetwine/extractors/usage_analysis.py** (`build_usage_info_list`, `build_caller_usages`): `build_usage_info_list` identifies where the target file references symbols imported from other project files, producing the `callee_usages` output. `build_caller_usages` identifies other project files that reference symbols defined in the target file, producing the `caller_usages` output.

- **codetwine/config/settings.py** (`DEFINITION_DICTS`): Used to retrieve the per-language definition extraction configuration keyed by file extension. This configuration is passed directly to `extract_definitions` to control which AST node types are recognized as definitions in the target file's language.

### Dependents (what uses this file)

- **codetwine/pipeline.py** (`get_file_dependencies`): Calls `get_file_dependencies` as part of a pipeline that processes all project files. It supplies the target file path, project root directory, and the project-wide dependency list, then consumes the returned dict (containing `file`, `definitions`, `callee_usages`, and `caller_usages`) to build the project's file dependency output.

The dependency relationship between this file and `pipeline.py` is unidirectional: `pipeline.py` depends on `file_analyzer.py`, and `file_analyzer.py` has no knowledge of or dependency on `pipeline.py`.

## Data Flow

# Data Flow

## Input Data

| Parameter | Type | Source |
|---|---|---|
| `target_file` | `str` (absolute path) | Caller (`pipeline.py`) |
| `project_dir` | `str` (absolute path) | Caller (`pipeline.py`) |
| `project_dep_list` | `list[dict]` | Caller (`pipeline.py`), output of `save_project_dependencies` |

### `project_dep_list` entry structure
```
{
  "file":    str,   # relative path of a project file
  "callers": list[str]  # relative paths of files that import this file
}
```

---

## Transformation Flow

```
target_file (abs path)
        │
        ▼
  parse_file()  ──────────────────────────► (root_node, content: bytes)
        │                                          │
        │                               content decoded to text lines
        │                                          │
        ▼                                          ▼
extract_definitions(root_node, definition_dict) ──► definition_list
        │
        │   [if language supports import analysis]
        ▼
extract_imports(root_node, language, import_query_str)
        │
        ▼
build_symbol_to_file_map(import_info_list, ...)
        │
        ├── symbol_to_file_map  { name → relative file path }
        └── alias_to_original   { alias → original name }
                │
                ▼
        build_usage_info_list(root_node, symbol_to_file_map, ...)
                │
                └──► usage_list (callee_usages)

project_dep_list ──► build_caller_usages(target_file_rel, ...)
                              │
                              └──► caller_usages
```

---

## Key Intermediate Data Structures

### `symbol_to_file_map`
```
{ "MyClass": "src/models/my_class.py", "helper": "src/utils/helper.py", ... }
```
Maps each imported name visible in the current file to the project-relative path of the file where it is defined. Used as the lookup table for usage analysis.

### `alias_to_original`
```
{ "aliasName": "originalName", ... }
```
Records `import X as Y` and `from M import X as Y` renames so that definition lookups can use the original name even when usage sites reference the alias.

### `definition_list` entry
| Field | Type | Description |
|---|---|---|
| `name` | `str` | Identifier of the definition |
| `type` | `str` | AST node type (e.g. `function_definition`) |
| `start_line` | `int` | 1-based start line |
| `end_line` | `int` | 1-based end line |
| `context` | `str` | Source text extracted from the line range |

### `usage_list` (callee_usages) entry
| Field | Type | Description |
|---|---|---|
| `name` | `str` | Symbol name as used (may include attribute path) |
| `from` | `str` | Relative path of the definition file |
| `lines` | `list[int]` | Deduplicated sorted line numbers of usages |
| `target_context` | `str` | Source code of the referenced definition |

### `caller_usages` entry
| Field | Type | Description |
|---|---|---|
| `name` | `str` | Symbol name used in the caller |
| `file` | `str` | Relative path of the caller file |
| `lines` | `list[int]` | Deduplicated sorted line numbers of usages |
| `usage_context` | `str` | Surrounding source lines at usage sites |

---

## Output

The function returns a single `dict` consumed by `pipeline.py`:

```python
{
  "file":          str,        # project-relative path of the analyzed file
  "definitions":   list[dict], # definitions declared in this file
  "callee_usages": list[dict], # symbols this file uses from other project files
  "caller_usages": list[dict], # locations in other files that use symbols from this file
}
```

If the file's language does not support import analysis (i.e., `get_import_params` returns `(None, None)`), both `callee_usages` and `caller_usages` are returned as empty lists; only `definitions` is populated.

## Error Handling

# Error Handling

## Overall Strategy

`file_analyzer.py` adopts a **fail-fast** strategy. The function `get_file_dependencies` contains no explicit `try/except` blocks; all errors encountered during file parsing, AST traversal, import resolution, or usage analysis are allowed to propagate directly to the caller (`pipeline.py`). The file delegates error containment entirely to its dependencies and to the pipeline layer above it.

The one form of graceful degradation present is **conditional execution**: if `get_import_params` returns `(None, None)` for an unsupported file extension, the import and usage analysis stages are skipped entirely, and the function returns with empty `callee_usages` and `caller_usages` lists. This is a deliberate design choice rather than error recovery.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported file extension (no import query defined) | `get_import_params` returns `(None, None)`; import/usage analysis block is bypassed via conditional check | `callee_usages` and `caller_usages` return as empty lists; `definitions` are still extracted |
| File I/O failure (unreadable target file) | Not caught; propagates from `parse_file` to the caller | Entire `get_file_dependencies` call aborts |
| Parse failure (malformed or unrecognized syntax) | Not caught; propagates from `parse_file` or `extract_definitions` to the caller | Entire `get_file_dependencies` call aborts |
| Definition extraction failure (name not resolvable in AST) | Handled internally by `extract_definitions` via BFS fallback; no error surface at this layer | Affected definition is silently omitted from `definition_list` |
| Import resolution failure (module not found in project) | Handled internally by `build_symbol_to_file_map`; unresolvable imports are silently skipped | Unresolved symbols are absent from `symbol_to_file_map`; their usages are not tracked |
| Usage analysis failure | Not caught; propagates from `build_usage_info_list` or `build_caller_usages` to the caller | Entire `get_file_dependencies` call aborts |

---

## Design Considerations

The fail-fast approach at this layer is consistent with the responsibilities of `file_analyzer.py` as a pure data-transformation function: it constructs a structured result dict from well-defined inputs, and any unexpected failure indicates a programming error or an unrecoverable environmental problem (missing file, broken AST library) that the pipeline layer is better positioned to handle or report. Graceful degradation for unsupported languages is implemented as a first-class feature rather than error recovery, keeping the distinction between "unsupported" and "broken" explicit.

## Summary

**codetwine/file_analyzer.py**

Orchestrates per-file dependency analysis by coordinating AST parsing, definition extraction, import resolution, and usage tracking. Exposes one public function: `get_file_dependencies(target_file, project_dir, project_dep_list) -> dict`, returning a structured record with four keys: `"file"` (relative path), `"definitions"` (named symbols declared in the file), `"callee_usages"` (symbols this file uses from other project files), and `"caller_usages"` (locations in other files referencing this file's symbols). Contains no parsing or extraction logic itself; delegates entirely to `ts_parser`, `definitions`, `imports`, `import_to_path`, and `usage_analysis`. Import/usage analysis is skipped for unsupported languages, returning empty lists.
