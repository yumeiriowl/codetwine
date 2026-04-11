# Design Document: codetwine/file_analyzer.py

# Overview & Purpose

## 1. Module Summary

Analyzes a single source file to extract its definitions, callee usages, and caller usages, assembling them into a structured dependency record that represents the file's complete inbound and outbound symbol relationships within a project.

## 2. When to Use This Module

- **Generating per-file dependency data during pipeline execution**: Call `get_file_dependencies(target_file, project_dir, project_dep_list)` to obtain a dict containing the file's definitions, the project-internal symbols it calls (`callee_usages`), and the project files that call symbols defined within it (`caller_usages`). This result is consumed directly by `codetwine/pipeline.py` to produce `file_dependencies.json`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` | Parses a source file, extracts its definitions and import-based symbol usages, collects reverse caller usages from other project files, and returns all data as a single structured dict with keys `file`, `definitions`, `callee_usages`, and `caller_usages`. |

## 4. Design Decisions

- **Early exit for unsupported languages**: Import and usage analysis (callee and caller) is skipped entirely when `get_import_params` returns `(None, None)` for the file's extension. Definition extraction still proceeds regardless of language support, so the returned dict always contains a `definitions` list.
- **`project_dep_list` as a pre-computed input**: Rather than re-scanning the project for caller relationships on every call, the function accepts `project_dep_list` from a prior project-wide pass. This keeps per-file analysis stateless and avoids redundant filesystem traversal.
- **Relative path normalization**: All file paths in the output use forward-slash-separated project-relative paths (`os.path.relpath` + `replace("\\", "/")`), ensuring consistent keys across platforms.

# Definition Design Specifications

---

## `get_file_dependencies`

**Signature:**
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
| `project_dep_list` | `list[dict]` | Pre-computed project-wide dependency list; each dict contains at minimum `"file"` and `"callers"` keys |

**Return type:** `dict` with the following keys:

| Key | Type | Description |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file (forward-slash normalized) |
| `"definitions"` | `list[dict]` | Definitions extracted from the file, each with `name`, `type`, `start_line`, `end_line`, `context` |
| `"callee_usages"` | `list[dict]` | Usage records for names this file imports from other project files |
| `"caller_usages"` | `list[dict]` | Usage records for names defined in this file that are used by other project files |

---

### Responsibility

Acts as the primary analysis entry point for a single file, orchestrating AST parsing, definition extraction, import resolution, and bidirectional usage analysis into a single structured result. This result serves as the source data for `file_dependencies.json`.

### When to Use

Called once per file by `pipeline.py`'s `process_all_files` routine when building the project-wide dependency graph.

---

### Design Decisions

- **Language-conditional import analysis:** Import resolution and usage analysis are only performed when `get_import_params` returns a non-`None` language and query string. Files in unsupported languages produce empty `callee_usages` and `caller_usages` rather than failing.
- **Project file set construction:** The set of all project-relative file paths is derived from `project_dep_list` rather than from a filesystem scan, keeping this function stateless with respect to directory traversal.
- **Definition `context` field:** The raw source text for each definition is sliced from the decoded content lines using the definition's `start_line`/`end_line` range and joined into a single string. This is computed inline rather than delegated to a helper.
- **Path normalization:** The relative path of the target file is normalized to forward slashes immediately, ensuring consistent key representation regardless of the host OS.
- **`alias_to_original` threading:** The alias mapping returned by `build_symbol_to_file_map` is forwarded directly into `build_usage_info_list`, allowing aliased imports to be traced back to their original definition names during usage analysis.

---

### Constraints & Edge Cases

- `target_file` must be an absolute path to a file parseable by tree-sitter via `parse_file`; unsupported extensions will raise an error inside `parse_file` or `_language_map` lookup.
- `project_dep_list` must already contain an entry for `target_file` (relative form) if `caller_usages` are expected; if no matching entry is found, `build_caller_usages` returns an empty list without error.
- Files whose extension is absent from `DEFINITION_DICTS` cause `definition_dict` to be `None`; `extract_definitions` must tolerate a `None` definition dict in that case.
- Content is decoded as UTF-8; files with other encodings will raise a `UnicodeDecodeError` at the `content.decode("utf-8")` call.
- `callee_usages` and `caller_usages` are both returned as empty lists for languages where `get_import_params` returns `(None, None)`.

# Dependency Description

### Dependencies (modules this file imports)

**`codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py`** : imports `parse_file` to parse the target source file into a tree-sitter AST root node and raw byte content, which serve as the inputs for all subsequent extraction steps.

**`codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py`** : imports `extract_definitions` to traverse the AST and produce a list of `DefinitionInfo` objects (name, type, start/end lines), which are then enriched with source text and stored as the `definitions` output.

**`codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py`** : imports `build_usage_info_list` to produce callee usage records (locations in this file where imported project symbols are used) and `build_caller_usages` to produce caller usage records (locations in other project files where symbols defined in this file are used).

**`codetwine/file_analyzer.py` → `codetwine/import_to_path.py`** : imports `get_import_params` to retrieve the tree-sitter `Language` object and import query string appropriate for the file's extension, and `build_symbol_to_file_map` to map imported symbol names to their definition file paths, enabling symbol-to-file resolution for usage analysis.

**`codetwine/file_analyzer.py` → `codetwine/extractors/imports.py`** : imports `extract_imports` to parse import statements from the AST into structured `ImportInfo` records, which are then passed to `build_symbol_to_file_map` to resolve symbols to project files.

**`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : imports `DEFINITION_DICTS` to look up the per-language definition extraction configuration (a mapping of AST node types to name-extraction strategies) keyed by file extension.

---

### Dependents (modules that import this file)

**`codetwine/pipeline.py` → `codetwine/file_analyzer.py`** : imports `get_file_dependencies` and calls it once per project file, passing the absolute file path, project root directory, and the project-level dependency list, to obtain the per-file analysis result dict (`file`, `definitions`, `callee_usages`, `caller_usages`) used to build `file_dependencies.json`.

---

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : unidirectional
- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : unidirectional

None of the dependency modules import back from `codetwine/file_analyzer.py`, and `codetwine/file_analyzer.py` does not import from `codetwine/pipeline.py`.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute file path string |
| `project_dir` | Caller (`pipeline.py`) | Absolute directory path string |
| `project_dep_list` | Caller (`pipeline.py`) | List of dicts with `file` and `callers` keys, produced by `save_project_dependencies` |
| File content | `parse_file()` reads the file from disk | Raw bytes, decoded to UTF-8 for line extraction |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | `dict[str, dict[str, str]]` mapping file extension to definition node config |
| Language/query config | `get_import_params()` from `import_to_path.py` | `(Language, str)` tuple or `(None, None)` |

---

## 2. Transformation Overview

### Stage 1: Path and Config Resolution
`target_file` is converted to a project-relative path (`target_file_rel`) and a file extension is extracted. The extension is used to look up `definition_dict` from `DEFINITION_DICTS` and to retrieve the tree-sitter `language` object and `import_query_str` via `get_import_params()`.

### Stage 2: AST Parsing
`parse_file(target_file)` reads the file from disk and returns an AST `root_node` and raw `content` bytes. The bytes are decoded and split into lines (`content_lines`) for later source-code extraction.

### Stage 3: Definition Extraction
`extract_definitions(root_node, definition_dict)` traverses the AST and returns a list of `DefinitionInfo` objects. Each object is projected into a plain dict by joining the corresponding slice of `content_lines` into a `context` string. The result is `definition_list`.

### Stage 4: Import Analysis (conditional on language support)
If `language` and `import_query_str` are non-None:

- A `project_file_set` is built by collecting the `"file"` key from every entry in `project_dep_list`.
- `extract_imports(root_node, language, import_query_str)` parses import statements from the AST, returning `list[ImportInfo]`.
- `build_symbol_to_file_map(...)` resolves each imported module name to a project-relative file path, producing `symbol_to_file_map` (imported name → definition file) and `alias_to_original` (alias name → original name).

### Stage 5: Callee Usage Construction
`build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` scans the AST for identifier usages that match entries in `symbol_to_file_map`, groups them by `(definition file, name)`, and attaches the definition source code. The result is `usage_list`.

### Stage 6: Caller Usage Construction
`build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set)` iterates over other project files that import from `target_file_rel`, extracts usages of definitions from this file in those callers, and groups them by `(name, caller file)`. The result is `caller_usages`.

### Stage 7: Result Assembly
All four products (`target_file_rel`, `definition_list`, `usage_list`, `caller_usages`) are assembled into a single return dict.

---

## 3. Outputs

The function returns a single dict. There are no file writes or other side effects.

| Key | Type | Content |
|---|---|---|
| `"file"` | `str` | Project-relative path of the analyzed file |
| `"definitions"` | `list[dict]` | All named definitions found in the file |
| `"callee_usages"` | `list[dict]` | Usages of project-internal symbols imported by this file |
| `"caller_usages"` | `list[dict]` | Usages of this file's symbols in other project files |

---

## 4. Key Data Structures

### `project_dep_list` entry (input)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative path of a file in the project |
| `"callers"` | `list[str]` | Relative paths of files that import from this file |

### `definition_list` entry (produced in Stage 3)
| Field / Key | Type | Purpose |
|---|---|---|
| `"name"` | `str` | Name of the defined symbol |
| `"type"` | `str` | AST node type (e.g. `"function_definition"`) |
| `"start_line"` | `int` | First line of the definition (1-based) |
| `"end_line"` | `int` | Last line of the definition (1-based) |
| `"context"` | `str` | Source code spanning `start_line` to `end_line` |

### `symbol_to_file_map` (produced in Stage 4, consumed in Stage 5)
| Field / Key | Type | Purpose |
|---|---|---|
| imported name | `str` (key) | Symbol name as it appears in the source file |
| definition file path | `str` (value) | Project-relative path of the file defining that symbol |

### `alias_to_original` (produced in Stage 4, consumed in Stage 5)
| Field / Key | Type | Purpose |
|---|---|---|
| alias name | `str` (key) | Name used in the current file (e.g. `b` from `import a as b`) |
| original name | `str` (value) | Name as defined in the source module (e.g. `a`) |

### `usage_list` entry / `callee_usages` entry (produced in Stage 5)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Deduplicated, sorted line numbers where the name is used |
| `"name"` | `str` | The imported name (or attribute access expression) as used |
| `"from"` | `str` | Project-relative path of the file where the symbol is defined |
| `"target_context"` | `str` | Source code of the definition extracted from the dependency file |

### `caller_usages` entry (produced in Stage 6)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Deduplicated, sorted line numbers within the caller file |
| `"name"` | `str` | Name from this file that is used in the caller |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Source snippet around the usage location in the caller |

# Error Handling

## 1. Overall Strategy

`file_analyzer.py` adopts a **delegation-and-propagation** strategy. The file contains no explicit `try-except` blocks of its own; it delegates all error handling to its dependency modules and allows any unhandled exceptions to propagate naturally to the caller (`pipeline.py`). For the subset of work that depends on language support, the file uses a **conditional-skip** pattern: if `get_import_params` returns `(None, None)` for an unsupported file extension, the entire import/usage analysis block is bypassed without raising an error, and the function returns a partial result.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (import analysis) | `get_import_params` returns `(None, None)` for a file extension not registered in `IMPORT_QUERIES` | The `if language and import_query_str:` guard skips the entire import/usage analysis block | Yes | `callee_usages` and `caller_usages` are returned as empty lists; `definitions` are still populated |
| Unsupported file extension (definition extraction) | `DEFINITION_DICTS.get(file_ext)` returns `None` for an unregistered extension | `definition_dict` is set to `None` and passed to `extract_definitions`; behavior depends on that function's handling | Yes (delegated) | Definition list may be empty; no exception raised at this layer |
| File read or parse failure | `parse_file` cannot open or parse the target file (e.g., file not found, encoding error, unsupported language in `_language_map`) | Not caught; exception propagates to the caller | No | Processing of the target file terminates and propagates up to `pipeline.py` |
| UTF-8 decode failure | `content.decode("utf-8")` fails for non-UTF-8 encoded file content | Not caught; exception propagates to the caller | No | Processing of the target file terminates |
| Import resolution or usage extraction failure | Errors raised inside `build_symbol_to_file_map`, `build_usage_info_list`, or `build_caller_usages` | Not caught; exception propagates to the caller | No | Processing of the target file terminates |

## 3. Design Notes

The absence of explicit error handling in this file reflects its role as a **thin orchestration layer**: it sequences calls to well-defined dependency functions and structures their outputs into a result dict. Error resilience for lower-level operations (file I/O, AST parsing, definition extraction) is considered the responsibility of the respective dependency modules. The only explicit defensive logic in this file is the language-support guard (`if language and import_query_str:`), which represents a known, expected condition—unsupported file types—rather than an exceptional failure, enabling graceful degradation to a definitions-only result without aborting the pipeline.

# Summary

**file_analyzer.py** orchestrates per-file dependency analysis by coordinating AST parsing, definition extraction, import resolution, and bidirectional usage analysis into a single structured result.

**Public:** `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`

**Produces:** `{"file": str, "definitions": list[dict], "callee_usages": list[dict], "caller_usages": list[dict]}`

**Consumes:** `project_dep_list` entries with `"file"` and `"callers"` keys; `symbol_to_file_map` (name→file), `alias_to_original` (alias→original name).
