# Design Document: codetwine/output.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Aggregates per-file analysis results (dependency JSON and design document JSON) stored under the output directory into project-level output artifacts: a consolidated knowledge JSON, a lightweight dependency-summary JSON, and a Mermaid flowchart Markdown file.

## 2. When to Use This Module

- **After all per-file analysis is complete**, call `build_symbol_level_deps(base_output_dir, all_file_list)` to compute the symbol-level caller/callee graph across all files from their individual `file_dependencies.json` files.
- **To attach human-readable summaries to dependency data**, call `build_summary_map(base_output_dir, all_file_list)` to read each file's `doc.json` and collect its `summary` field.
- **To produce a full project knowledge file**, call `save_consolidated_json(...)` with the symbol deps and summary map to write `project_knowledge.json`, which merges `file_dependencies.json` and `doc.json` data for every file alongside the dependency graph.
- **To produce a lightweight dependency + summary file**, call `save_dependency_summary(...)` to write `project_dependency_summary.json` containing only the symbol-level callers/callees and summaries.
- **To visualize the dependency graph**, call `save_dependency_graph_as_mermaid(...)` to write a `dependency_graph.md` file containing a Mermaid `graph LR` flowchart.
- **To convert a project-relative file path to the `project_name/copy_path` format** used throughout all output artifacts, call `to_output_path(base_output_dir, rel_path)`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative path to `project_name/copy_path` format using the base output directory's trailing name as the project name. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` and returns a map of relative path → summary text (or `None` if absent). |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Reads each file's `file_dependencies.json` and returns a map of relative path → `{"callers": set, "callees": set}` derived from actual symbol usage. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a single JSON file combining `project_dependencies` (symbol-level graph + summaries) and `files` (merged `file_dependencies.json` + `doc.json` per file). |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a lightweight JSON containing only symbol-level callers/callees and summaries for each file. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]` | `None` | Writes a Mermaid `graph LR` flowchart Markdown file from the symbol-level dependency graph, using source-relative paths as node labels. |

## 4. Design Decisions

- **Unified path format across all outputs.** All file paths in every output artifact are stored in `project_name/copy_path` format (via `to_output_path`), not as raw relative paths. This matches the directory structure written by the file-copying pipeline, making paths in output JSON consistent with the actual output filesystem layout.
- **`build_symbol_level_deps` is computed once and shared.** As reflected in the pipeline usage, `symbol_deps` is built a single time and passed into all three save functions, avoiding redundant disk reads of `file_dependencies.json`.
- **Symbol-level rather than import-level dependency tracking.** `build_symbol_level_deps` derives caller/callee relationships from the `callee_usages.from` and `caller_usages.file` fields inside each `file_dependencies.json`, capturing only files with actual symbol usage rather than every import statement.
- **Files with no analysis results are excluded from `files_list` in `save_consolidated_json`.** An entry is only appended when it contains more than just the `file` key (i.e., at least one of `file_dependencies.json` or `doc.json` was found), with a warning logged for omitted files.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level

| Item | Value |
|---|---|
| Logger name | `codetwine.output_py.output` (via `__name__`) |

---

## `to_output_path`

**Signature:**
```python
def to_output_path(base_output_dir: str, rel_path: str) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Absolute or relative path whose final directory component is the project name |
| `rel_path` | `str` | File path relative to the project root |
| **Returns** | `str` | Path in `"project_name/copy_path"` format |

**Responsibility:** Produces the canonical output-format path used throughout consolidated JSON and Mermaid output, combining the project name with the copy-destination path encoding of a relative file path.

**When to use:** Call whenever a project-relative file path must be expressed in the `"project_name/copy_path"` format expected by JSON output files and Mermaid diagrams.

**Design decisions:**
- The project name is always derived from the trailing component of `base_output_dir` via `os.path.basename`, so the caller does not supply it separately.
- Delegates all copy-path encoding logic to `rel_to_copy_path`, keeping this function's responsibility purely to prefix the project name.

**Constraints & edge cases:**
- If `base_output_dir` ends with a separator (`/`), `os.path.basename` returns an empty string, producing a path that starts with `/`.
- `rel_path` must be a valid project-relative path accepted by `rel_to_copy_path`.

---

## `build_summary_map`

**Signature:**
```python
def build_summary_map(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, str | None]
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory used to locate per-file output subdirectories |
| `all_file_list` | `list[str]` | Project-relative paths of all files to include |
| **Returns** | `dict[str, str | None]` | Map from project-relative file path to summary string, or `None` if no `doc.json` exists or it contains no `"summary"` key |

**Responsibility:** Collects the `"summary"` field from each file's `doc.json` into a single lookup dict so downstream functions can attach summaries without re-reading individual JSON files.

**When to use:** Call once before invoking `save_dependency_summary` or `save_consolidated_json` so both can share the same summary data without repeated disk reads.

**Constraints & edge cases:**
- Every entry in `all_file_list` is guaranteed to appear as a key in the returned dict; the value is `None` when `doc.json` is absent or when the `"summary"` key is missing from the JSON.
- No validation of `doc.json` structure beyond reading the `"summary"` key.

---

## `save_consolidated_json`

**Signature:**
```python
def save_consolidated_json(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory |
| `all_file_list` | `list[str]` | Project-relative paths of all files |
| `output_path` | `str` | Filesystem path where the consolidated JSON file is written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Keyed by project-relative path; inner dict has `"callers"` and `"callees"` each as a set of project-relative paths |
| `summary_map` | `dict[str, str \| None]` | Project-relative path → summary text or `None` |
| **Returns** | `None` | — |

**Responsibility:** Merges per-file `file_dependencies.json` and `doc.json` data with symbol-level dependency information into a single project-wide JSON file for downstream consumption.

**When to use:** Call after all per-file analysis artifacts have been written and after `build_symbol_level_deps` and `build_summary_map` have been called.

**Design decisions:**
- Produces two parallel top-level arrays: `"project_dependencies"` (lightweight dependency + summary rows) and `"files"` (full merged content of per-file JSON artifacts).
- Files that have neither `file_dependencies.json` nor `doc.json` (i.e., an `entry` dict that contains only the `"file"` key) are excluded from `"files"` and a warning is logged; they still appear in `"project_dependencies"`.
- All file paths in the output use `to_output_path` format.
- The `"file"` key is removed from loaded `file_dependencies.json` and `doc.json` objects before nesting them under `entry`, because the path is already captured at the top-level `"file"` key of the entry.
- Callers and callees in `"project_dependencies"` are sorted lexicographically for deterministic output.

**Constraints & edge cases:**
- `symbol_deps` must contain an entry for every path in `all_file_list`; a missing key raises `KeyError`.
- Paths stored inside `file_dependencies.json` are assumed to already be in output format (converted at write time); this function does not re-convert them.
- Files present in `all_file_list` but lacking both artifact files are logged as warnings and excluded from `"files"`.

---

## `build_symbol_level_deps`

**Signature:**
```python
def build_symbol_level_deps(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, dict[str, set[str]]]
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory |
| `all_file_list` | `list[str]` | Project-relative paths of all files |
| **Returns** | `dict[str, dict[str, set[str]]]` | Keyed by project-relative path; inner dict has `"callers"` and `"callees"`, each a set of project-relative paths |

**Responsibility:** Derives actual symbol-usage-level caller/callee relationships by parsing each file's `file_dependencies.json`, rather than relying on import-level graph data.

**When to use:** Call once per pipeline run before any output-generation functions that require dependency graph data (`save_dependency_summary`, `save_consolidated_json`, `save_dependency_graph_as_mermaid`).

**Design decisions:**
- All files in `all_file_list` are pre-populated with empty sets so the result always contains an entry for every requested file, even if no `file_dependencies.json` exists.
- Callee files are read from each `callee_usages[*].from` field; caller files are read from each `caller_usages[*].file` field.
- Paths from JSON (in output format) are converted back to project-relative paths via `output_path_to_rel` before storage, ensuring internal consistency.

**Constraints & edge cases:**
- Files whose `file_dependencies.json` is absent are silently skipped; their sets remain empty.
- Only the `"from"` and `"file"` fields of individual usage objects are examined; other fields are ignored.
- Dependencies on files outside `all_file_list` are stored without validation.

---

## `save_dependency_summary`

**Signature:**
```python
def save_dependency_summary(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory |
| `all_file_list` | `list[str]` | Project-relative paths of all files |
| `output_path` | `str` | Filesystem path where the output JSON file is written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (same shape as `build_symbol_level_deps` return) |
| `summary_map` | `dict[str, str \| None]` | Project-relative path → summary text or `None` |
| **Returns** | `None` | — |

**Responsibility:** Writes a lightweight JSON file pairing each file's symbol-level dependency edges with its LLM-generated summary, intended as a compact project overview artifact.

**When to use:** Call after `build_symbol_level_deps` and `build_summary_map` when a lightweight dependency-plus-summary artifact is needed without full per-file JSON content.

**Design decisions:**
- The output schema is a flat list under `"files"`, each entry containing `"file"`, `"summary"`, `"callers"`, and `"callees"` — a simpler projection than the full consolidated JSON.
- All files in `all_file_list` are included unconditionally, with `null` summary when absent.
- Callers and callees are sorted for deterministic output.
- The info log message includes a count of files with non-null summaries to surface LLM coverage.

**Constraints & edge cases:**
- `symbol_deps` must contain an entry for every path in `all_file_list`.
- No filtering is applied; files with no dependencies and no summary still appear in the output.

---

## `save_dependency_graph_as_mermaid`

**Signature:**
```python
def save_dependency_graph_as_mermaid(
    base_output_dir: str,
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory (provides the project name) |
| `output_path` | `str` | Filesystem path where the Markdown file is written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map; only `"callees"` edges are rendered |
| **Returns** | `None` | — |

**Responsibility:** Renders the project's callee dependency graph as a Mermaid `graph LR` flowchart embedded in a Markdown fenced code block.

**When to use:** Call after `build_symbol_level_deps` when a visual dependency graph artifact is required.

**Design decisions:**
- Only callee edges (not caller edges) are used to build the graph, avoiding duplicate edges since callee of A → B is the same relationship as caller of B ← A.
- Nodes are collected from both the keys of `symbol_deps` and any callee files referenced by those keys, so files that appear only as callees are still represented as nodes.
- Nodes and edges are sorted before rendering for deterministic output.
- Uses two private helper functions:

| Helper | Signature | Purpose |
|---|---|---|
| `to_mermaid_node_id` | `(path: str) -> str` | Replaces `/` and `.` with `_` to produce a valid Mermaid node identifier |
| `to_display_label` | `(path: str) -> str` | Strips the project-name prefix and converts the copy-path portion back to a source-relative path via `copy_path_to_rel` for human-readable labels |

**Constraints & edge cases:**
- The output file is plain text (Markdown); no validation of Mermaid syntax beyond the fixed template structure.
- If two files have paths that, after `to_mermaid_node_id` transformation, produce the same string, they will be treated as the same node in the diagram.
- `to_display_label` falls back to returning the original path unchanged if the input does not contain a `/` separator after splitting off the project name.
- Files with no callees still appear as isolated nodes if they are keys in `symbol_deps`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- **`codetwine/output_py/output.py` → `codetwine/utils/file_utils.py`** : Imports four path-conversion utilities to handle all path transformations used throughout this module.
  - `rel_to_copy_path` — used in `to_output_path()` to convert a project-relative path into copy-destination path format, which is then prefixed with the project name.
  - `copy_path_to_rel` — used inside `save_dependency_graph_as_mermaid()` (via `to_display_label()`) to strip the copy-destination directory layer and recover the original relative path for Mermaid node labels.
  - `output_path_to_rel` — used in `build_symbol_level_deps()` to convert the `"from"` and `"file"` fields stored in `file_dependencies.json` (which are in `project_name/copy_path` format) back into project-relative paths when populating the callee/caller sets.
  - `resolve_file_output_dir` — used in `build_summary_map()`, `save_consolidated_json()`, and `build_symbol_level_deps()` to determine the absolute output directory for a given file, from which `doc.json` and `file_dependencies.json` are read.

## Dependents (modules that import this file)

- **`codetwine/pipeline.py` → `codetwine/output_py/output.py`** : Uses this module as the primary output-generation layer for the analysis pipeline. Specifically:
  - `to_output_path` — called to convert project-relative file paths and usage paths (in `callee_usages[].from` and `caller_usages[].file`) to `project_name/copy_path` format before saving individual `file_dependencies.json` files.
  - `build_symbol_level_deps` — called once after analysis to build the shared symbol-level dependency graph (`symbol_deps`), which is then passed to subsequent output functions.
  - `build_summary_map` — called to collect per-file summaries from `doc.json` files into a shared map (`summary_map`).
  - `save_dependency_summary` — called to write `project_dependency_summary.json`, a lightweight file combining dependency structure and summaries.
  - `save_dependency_graph_as_mermaid` — called to write `dependency_graph.md` as a Mermaid flowchart.
  - `save_consolidated_json` — called to write `project_knowledge.json`, the full consolidated output of all per-file analysis results.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output_py/output.py` → `codetwine/utils/file_utils.py` : one-way; `file_utils` has no dependency on `output.py`.
- `codetwine/pipeline.py` → `codetwine/output_py/output.py` : one-way; `output.py` has no dependency on `pipeline.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `base_output_dir` argument | `str` (filesystem path) | Base output directory; its trailing component is used as the project name |
| `all_file_list` argument | `list[str]` | Project-relative paths of all files to process |
| `file_dependencies.json` (file read) | JSON object on disk | Per-file dependency info including `callee_usages` and `caller_usages` arrays |
| `doc.json` (file read) | JSON object on disk | Per-file design document containing at minimum a `"summary"` field |
| `symbol_deps` argument | `dict[str, dict[str, set[str]]]` | Pre-built symbol-level dependency map (callers/callees per file) |
| `summary_map` argument | `dict[str, str \| None]` | Pre-built map from file relative path to summary text or `None` |
| `output_path` argument | `str` (filesystem path) | Destination path for each output file |

File locations are resolved via `resolve_file_output_dir(base_output_dir, file_rel)`, which places each file's artifacts under `{base_output_dir}/{parent_dir}/{stem}_{ext}/`.

---

## 2. Transformation Overview

### `build_symbol_level_deps`
1. **Initialize** — Create an empty `{"callers": set(), "callees": set()}` entry for every file in `all_file_list`.
2. **Read per-file JSON** — For each file, load `file_dependencies.json` from the resolved output directory.
3. **Extract callees** — Collect the `"from"` field of every entry in `callee_usages`; convert each from output-path format to a project-relative path via `output_path_to_rel`.
4. **Extract callers** — Collect the `"file"` field of every entry in `caller_usages`; convert each via `output_path_to_rel`.
5. **Return** — A fully populated `deps_map` keyed by project-relative file path.

### `build_summary_map`
1. **Read per-file JSON** — For each file in `all_file_list`, load `doc.json` from the resolved output directory (if it exists).
2. **Extract summary** — Pull the `"summary"` key; leave as `None` if the file is absent or the key is missing.
3. **Return** — A flat `{rel_path: summary_or_None}` dict.

### `save_dependency_summary`
1. **Build file entries** — For each file, combine the output-format path (via `to_output_path`), its summary from `summary_map`, and its sorted caller/callee lists from `symbol_deps` (each converted via `to_output_path`).
2. **Wrap in result object** — Package the entries list under `"project_name"` and `"files"` keys.
3. **Write JSON** — Serialize to the given `output_path`.

### `save_dependency_graph_as_mermaid`
1. **Collect nodes and edges** — Iterate `symbol_deps`; for every file and its callees, add nodes to `node_set` and directed pairs to `edge_set`, both in output-path format.
2. **Generate node declarations** — For each unique node, produce a Mermaid node line using a sanitized ID (`/` and `.` replaced with `_`) and a human-readable label (restored to source-relative path via `copy_path_to_rel`).
3. **Generate edge declarations** — For each caller→callee pair, emit a Mermaid `-->` arrow line.
4. **Write Markdown** — Join all lines and write a fenced `mermaid` code block to `output_path`.

### `save_consolidated_json`
1. **Build `project_dependencies`** — For each file, assemble a flat entry combining output-format path, summary, and sorted caller/callee lists from `symbol_deps`.
2. **Build `files` list** — For each file, load `file_dependencies.json` (removing its redundant `"file"` key) and `doc.json` (removing its redundant `"file"` key); merge both under a single entry keyed by the output-format path. Entries with no data beyond the `"file"` key are skipped with a warning.
3. **Wrap in consolidated object** — Package `project_name`, `project_dependencies`, and `files` together.
4. **Write JSON** — Serialize to the given `output_path`.

### `to_output_path` (shared helper)
- Extracts `project_name` from `os.path.basename(base_output_dir)`.
- Converts `rel_path` to copy-destination format via `rel_to_copy_path`.
- Returns a combined `"{project_name}/{copy_path}"` string.

---

## 3. Outputs

| Output | Function | Format | Description |
|--------|----------|--------|-------------|
| Return value | `build_symbol_level_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level callers/callees per file (project-relative paths) |
| Return value | `build_summary_map` | `dict[str, str \| None]` | Summary text per file |
| Return value | `to_output_path` | `str` | Path in `"project_name/copy_path"` format |
| File write | `save_dependency_summary` | JSON file | Lightweight dependency + summary JSON |
| File write | `save_dependency_graph_as_mermaid` | Markdown file | Fenced `mermaid` code block with `graph LR` flowchart |
| File write | `save_consolidated_json` | JSON file | Full project knowledge base combining dependencies and design docs |

---

## 4. Key Data Structures

### `deps_map` / `symbol_deps` — returned by `build_symbol_level_deps`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| *(outer key)* | `str` | Project-relative file path |
| `"callers"` | `set[str]` | Project-relative paths of files that call into this file |
| `"callees"` | `set[str]` | Project-relative paths of files this file calls into |

---

### `summary_map` — returned by `build_summary_map`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| *(outer key)* | `str` | Project-relative file path |
| *(value)* | `str \| None` | Summary text from `doc.json`, or `None` if absent |

---

### Per-file entry in `project_dependencies` (inside consolidated JSON)
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | File path in `"project_name/copy_path"` format |
| `"summary"` | `str \| None` | Summary text from `doc.json` |
| `"callers"` | `list[str]` | Sorted list of caller file paths in output format |
| `"callees"` | `list[str]` | Sorted list of callee file paths in output format |

---

### Per-file entry in `files` list (inside consolidated JSON)
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | File path in `"project_name/copy_path"` format |
| `"file_dependencies"` | `dict` | Contents of `file_dependencies.json` with `"file"` key removed |
| `"doc"` | `dict` | Contents of `doc.json` with `"file"` key removed |

---

### Consolidated JSON root object
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"project_name"` | `str` | Trailing directory name of `base_output_dir` |
| `"project_dependencies"` | `list[dict]` | Flat dependency + summary entries for every file |
| `"files"` | `list[dict]` | Full per-file entries combining dependency info and design docs |

---

### Dependency summary JSON root object
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"project_name"` | `str` | Trailing directory name of `base_output_dir` |
| `"files"` | `list[dict]` | Per-file entries with path, summary, callers, and callees |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** approach for missing or absent data, combined with **silent omission** for incomplete entries. No try-except blocks are present; error handling relies entirely on defensive existence checks (`os.path.exists`) before reading files, and on safe dictionary access (`dict.get`) with `None` defaults. When expected files are absent, the code proceeds with gracefully degraded output (e.g., `null` values or omitted entries) rather than raising exceptions or halting the pipeline.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `doc.json` | `doc.json` does not exist in a file's output directory | `summary` is set to `None`; file is not added to `files_list` in `save_consolidated_json` (unless `file_dependencies.json` is also present) | Yes | That file's entry appears with `null` summary; a `WARNING` is logged if neither JSON exists |
| Missing `file_dependencies.json` | `file_dependencies.json` does not exist in a file's output directory | File entry is skipped in `build_symbol_level_deps`; no dependency info added; entry omitted from `files_list` in `save_consolidated_json` if no JSON at all | Yes | File has empty callers/callees sets; a `WARNING` is logged if neither JSON exists |
| Both `doc.json` and `file_dependencies.json` absent | Neither file exists in the output directory for a given `file_rel` | Entry has only the `"file"` key (`len(entry) == 1`); it is excluded from `files_list` and a `WARNING` log is emitted | Yes (file skipped) | File is absent from consolidated output; logged as missing |
| Missing field in `callee_usages`/`caller_usages` | A usage entry lacks a `"from"` or `"file"` key | `usage.get(...)` returns `None`; the `if callee_file` / `if caller_file` guard skips the entry silently | Yes | That usage's dependency link is not recorded; no log emitted |
| Missing `summary` field in `doc.json` | `doc.json` exists but contains no `"summary"` key | `doc.get("summary")` returns `None`; stored as `None` in `summary_map` | Yes | Summary appears as `null` in output JSON |

---

## 3. Design Notes

- **No exceptions are raised or caught.** The module assumes that upstream pipeline stages are responsible for producing well-formed output files; this module only reads them. Missing files are treated as expected, not exceptional, states.
- The `len(entry) > 1` guard in `save_consolidated_json` serves as the sole gating condition for inclusion in consolidated output, implicitly merging the "missing both files" scenario into a single warning path without separate error categorization.
- The `WARNING` log for fully absent analysis results is the only active signal emitted for degraded data; all other missing-data cases are handled silently through `None` defaults, reflecting a design preference for partial output over pipeline interruption.
- Dependency counts in log messages (`files: {len(files_list)}/{len(all_file_list)}`) provide passive observability into how many files were successfully consolidated, without requiring explicit error tracking.

## Summary

Aggregates per-file analysis artifacts into project-level outputs. Public functions: `to_output_path(base_output_dir:str, rel_path:str)->str`; `build_symbol_level_deps(base_output_dir:str, all_file_list:list[str])->dict[str,dict[str,set[str]]]`; `build_summary_map(base_output_dir:str, all_file_list:list[str])->dict[str,str|None]`; `save_consolidated_json(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)->None`; `save_dependency_summary(...)->None`; `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)->None`. Key structures: `symbol_deps` maps project-relative paths to `{"callers":set[str],"callees":set[str]}`; `summary_map` maps paths to `str|None`.
