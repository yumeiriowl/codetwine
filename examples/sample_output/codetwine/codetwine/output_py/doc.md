# Design Document: codetwine/output.py

# Overview & Purpose

## 1. Module Summary

Aggregates per-file analysis results (dependency graphs, summaries, and design documents) scattered across the output directory tree into consolidated, project-level output files (JSON and Mermaid Markdown).

## 2. When to Use This Module

- **After all per-file analysis is complete**, call `build_symbol_level_deps` to compute the project-wide symbol-level caller/callee dependency graph from each file's `file_dependencies.json`.
- **To attach human-readable summaries to the dependency graph**, call `build_summary_map` to collect the `summary` field from each file's `doc.json`.
- **To produce a lightweight dependency + summary overview file** (`project_dependency_summary.json`), call `save_dependency_summary` with the results of the two builders above.
- **To generate a Mermaid flowchart of the dependency graph** as a Markdown file, call `save_dependency_graph_as_mermaid`.
- **To produce a single comprehensive project knowledge file** (`project_knowledge.json`) that combines dependency info, design documents, and summaries for all files, call `save_consolidated_json`.
- **To convert a file's relative path to the `project_name/copy_path` output format** used consistently across all output files, call `to_output_path`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative file path to the `project_name/copy_path` format used in all output files. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads `doc.json` for each file and returns a mapping from relative path to its summary text (or `None` if absent). |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Builds a project-wide symbol-level dependency map (`callers`/`callees` sets per file) from each file's `file_dependencies.json`. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a lightweight JSON combining symbol-level dependencies and summaries for all files. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]` | `None` | Generates a Mermaid `graph LR` flowchart from the dependency graph and writes it as a Markdown file. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Merges `file_dependencies.json` and `doc.json` for every file, together with the dependency graph, into a single comprehensive project knowledge JSON file. |

## 4. Design Decisions

- **Separation of building from saving**: Dependency graph construction (`build_symbol_level_deps`) and summary collection (`build_summary_map`) are decoupled from the save functions so that their results can be computed once and shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json` without redundant disk reads.
- **Consistent output path format**: All output files uniformly use the `project_name/copy_path` format (via `to_output_path`) for file references, matching the directory structure created by the pipeline's file-copy step and enabling round-trip conversion via `output_path_to_rel`.
- **Graceful handling of missing analysis results**: Both `save_consolidated_json` and `build_summary_map` treat absent `doc.json` or `file_dependencies.json` files as partial results rather than errors, emitting `null` summaries and logging warnings for files with no output at all, so the pipeline can produce useful output even when individual file analysis fails.

# Definition Design Specifications

---

## `to_output_path`

**Signature:**
```python
def to_output_path(base_output_dir: str, rel_path: str) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Absolute or relative path to the base output directory; its trailing component is used as the project name |
| `rel_path` | `str` | Relative path from the project root |
| **Returns** | `str` | Path in `"project_name/copy_path"` format |

**Responsibility:** Converts a project-relative file path into the canonical output path format used throughout all JSON outputs and the Mermaid graph, by prepending the project name and transforming the path via `rel_to_copy_path`.

**When to use:** Call this whenever a file's relative path must be serialized into a consolidated or summary output artifact (e.g., when writing JSON entries or Mermaid node labels).

**Design decisions:** The project name is derived solely from `os.path.basename(base_output_dir)`, not from any configuration; the directory name itself is the authoritative project name.

**Constraints & edge cases:** `base_output_dir` must have a non-empty final component; a path ending in a separator would produce an empty project name. `rel_path` must be a valid project-relative path accepted by `rel_to_copy_path`.

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
| `base_output_dir` | `str` | Base output directory |
| `all_file_list` | `list[str]` | Relative paths of all files to include |
| **Returns** | `dict[str, str \| None]` | Map from each relative path to its summary string, or `None` if unavailable |

**Responsibility:** Reads the `"summary"` field from each file's `doc.json` and aggregates the results into a single lookup map, so downstream functions do not need to perform per-file I/O.

**When to use:** Call once before invoking `save_dependency_summary` or `save_consolidated_json`, and pass the result to those functions.

**Design decisions:** Every file in `all_file_list` is guaranteed an entry in the returned dict; files whose `doc.json` is absent or lacks a `"summary"` key receive `None` rather than being omitted.

**Constraints & edge cases:** Files whose `doc.json` exists but contains malformed JSON will raise a parse error. Files not in `all_file_list` are not represented in the output.

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
| `all_file_list` | `list[str]` | Relative paths of all files to consolidate |
| `output_path` | `str` | Filesystem path where the JSON file is written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Map from relative path to `{"callers": set[str], "callees": set[str]}` (output of `build_symbol_level_deps`) |
| `summary_map` | `dict[str, str \| None]` | Map from relative path to summary text or `None` (output of `build_summary_map`) |

**Responsibility:** Writes a single comprehensive JSON file (`project_knowledge.json`) that unifies the dependency graph, per-file `file_dependencies.json` content, and per-file `doc.json` content for every analyzed file.

**When to use:** Call at the final reporting stage of the pipeline after all per-file analysis and dependency resolution are complete.

**Design decisions:**
- The output JSON has three top-level keys: `"project_name"`, `"project_dependencies"`, and `"files"`.
- `"project_dependencies"` entries contain `"callers"` and `"callees"` as sorted lists (converted from sets) using the `"project_name/copy_path"` format.
- In `"files"` entries, the `"file"` key is lifted to the top level and removed from nested `file_dependencies` and `doc` sub-objects to avoid duplication.
- Files for which neither `file_dependencies.json` nor `doc.json` exists are excluded from `"files"` with a warning log; they still appear in `"project_dependencies"`.
- All paths within the JSON use `to_output_path` format, consistent with how individual JSON files were saved by the pipeline.

**Constraints & edge cases:** `symbol_deps` must contain an entry for every element of `all_file_list`. Files missing both output JSON files produce a warning and are omitted from `"files"` but not from `"project_dependencies"`.

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
| `all_file_list` | `list[str]` | Relative paths of all files to analyze |
| **Returns** | `dict[str, dict[str, set[str]]]` | Map from relative path to `{"callers": set[str], "callees": set[str]}`; sets contain relative paths |

**Responsibility:** Aggregates actual symbol-level caller/callee relationships from each file's `file_dependencies.json` into a project-wide dependency map, operating at finer granularity than import-level analysis.

**When to use:** Call once before the output-generation steps (`save_dependency_summary`, `save_consolidated_json`, `save_dependency_graph_as_mermaid`) and pass the result to all three.

**Design decisions:**
- Callees are derived from the `"from"` field of each `"callee_usages"` entry; callers from the `"file"` field of each `"caller_usages"` entry.
- Stored values in the sets are relative paths (via `output_path_to_rel`), not output-format paths, keeping internal representations uniform with `all_file_list`.
- Every file in `all_file_list` receives an initialized entry with empty sets, ensuring callers/callee-less files are present in the result rather than absent.
- Files without a `file_dependencies.json` are skipped without error.

**Constraints & edge cases:** Depends on `file_dependencies.json` files having been written with output-format paths (as performed by the pipeline prior to this call). Callee/caller files not in `all_file_list` are still added to the relevant sets.

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
| `all_file_list` | `list[str]` | Relative paths of all files to include |
| `output_path` | `str` | Filesystem path where the JSON file is written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (output of `build_symbol_level_deps`) |
| `summary_map` | `dict[str, str \| None]` | Summary map (output of `build_summary_map`) |

**Responsibility:** Writes a lightweight JSON file (`project_dependency_summary.json`) combining only symbol-level dependency structure and per-file summaries, omitting the full `file_dependencies.json` and `doc.json` content present in the consolidated JSON.

**When to use:** Call after `build_symbol_level_deps` and `build_summary_map` to produce the summary artifact before generating the Mermaid graph and consolidated JSON.

**Design decisions:**
- All files in `all_file_list` always appear in the output regardless of whether a summary exists; those without a summary have `"summary": null`.
- `"callers"` and `"callees"` are serialized as sorted lists in `"project_name/copy_path"` format.
- The log message reports both total file count and the count of files that have a non-`None` summary.

**Constraints & edge cases:** `symbol_deps` must contain an entry for every element of `all_file_list`; a missing key raises `KeyError`. Unlike `save_consolidated_json`, no files are excluded from the output.

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
| `base_output_dir` | `str` | Base output directory |
| `output_path` | `str` | Filesystem path where the Markdown file is written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (output of `build_symbol_level_deps`) |

**Responsibility:** Generates a `dependency_graph.md` file containing a Mermaid `graph LR` flowchart visualizing directed callee edges between files.

**When to use:** Call after `build_symbol_level_deps` to produce the Mermaid visualization artifact.

**Design decisions:**
- Nodes and edges are deduplicated using `set` collections before rendering, preventing duplicate declarations regardless of how many symbols connect the same pair of files.
- Node IDs in the Mermaid syntax are derived by replacing `/` and `.` with `_` (via inner function `to_mermaid_node_id`), since Mermaid node IDs cannot contain those characters.
- Node display labels show the original source-relative path (via inner function `to_display_label`, which strips the project name prefix and reverses the copy-path transformation), keeping the diagram human-readable.
- Both nodes and edges are emitted in sorted order for deterministic output.
- Only callee edges (caller → callee direction) are rendered; caller relationships are not separately drawn as edges.
- The output is wrapped in a fenced ` ```mermaid ``` ` code block, making it renderable directly in Markdown viewers.

**Constraints & edge cases:** Callee files referenced in `symbol_deps` that are not themselves keys in `symbol_deps` are still added as nodes. If `symbol_deps` is empty, the output is a valid but empty Mermaid graph.

### Inner functions of `save_dependency_graph_as_mermaid`

| Name | Signature | Purpose |
|---|---|---|
| `to_mermaid_node_id` | `(path: str) → str` | Replaces `/` and `.` with `_` to produce a valid Mermaid node identifier |
| `to_display_label` | `(path: str) → str` | Strips the project-name prefix from an output-format path and restores the original source-relative path for use as a human-readable node label |

# Dependency Description

### Dependencies (modules this file imports)

- **`codetwine/output.py` → `codetwine/utils/file_utils.py`** : path conversion and output directory resolution utilities

  Specific symbols imported:
  - `rel_to_copy_path` — used in `to_output_path()` to convert a project-relative file path into the `{stem}_{ext}/{filename}` copy-destination format when constructing `project_name/copy_path` output paths.
  - `copy_path_to_rel` — used in `save_dependency_graph_as_mermaid()` (via `to_display_label()`) to restore a copy-destination path back to its original source-relative path for human-readable Mermaid node labels.
  - `output_path_to_rel` — used in `build_symbol_level_deps()` to convert `project_name/copy_path` format strings (read from `file_dependencies.json`) back to project-relative paths when building the caller/callee sets.
  - `resolve_file_output_dir` — used in `build_summary_map()`, `save_consolidated_json()`, and `build_symbol_level_deps()` to resolve the absolute output directory for a given file's relative path, enabling location of `doc.json` and `file_dependencies.json` files.

---

### Dependents (modules that import this file)

- **`codetwine/pipeline.py` → `codetwine/output.py`** : orchestrates the full output generation phase of the pipeline by calling all major public functions exported by this module.
  - Uses `to_output_path` to normalize file paths to `project_name/copy_path` format when writing dependency results (`file_dependencies.json` entries including `callee_usages.from` and `caller_usages.file` fields).
  - Uses `build_symbol_level_deps` to compute the symbol-level caller/callee dependency graph across all analyzed files, producing a shared data structure reused by the subsequent output steps.
  - Uses `build_summary_map` to collect per-file summaries from `doc.json` files into a shared map, also reused by subsequent output steps.
  - Uses `save_dependency_summary` to write `project_dependency_summary.json` combining symbol-level dependencies and summaries.
  - Uses `save_dependency_graph_as_mermaid` to write `dependency_graph.md` as a Mermaid flowchart.
  - Uses `save_consolidated_json` to write `project_knowledge.json` as the fully consolidated project analysis artifact.

---

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output.py` → `codetwine/utils/file_utils.py` : one-way; `file_utils.py` provides pure utility functions and has no knowledge of `output.py`.
- `codetwine/pipeline.py` → `codetwine/output.py` : one-way; `output.py` exposes output-generation functions consumed by the pipeline orchestrator, but `output.py` does not import from `pipeline.py`.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `base_output_dir` argument | `str` (directory path) | Root output directory; its basename is used as the project name |
| `all_file_list` argument | `list[str]` | Project-relative file paths to include in analysis |
| `symbol_deps` argument | `dict[str, dict[str, set[str]]]` | Pre-built symbol-level dependency map (callers/callees per file) |
| `summary_map` argument | `dict[str, str \| None]` | Pre-built map of file relative path → summary text or `None` |
| `file_dependencies.json` (disk read) | JSON file per analyzed file | Dependency info including `callee_usages` and `caller_usages` arrays |
| `doc.json` (disk read) | JSON file per analyzed file | Design document including a `summary` field |

File reads are resolved using `resolve_file_output_dir(base_output_dir, file_rel)` to construct the per-file output directory path, under which `file_dependencies.json` and `doc.json` reside.

---

## 2. Transformation Overview

### Stage 1 — Path normalization (`to_output_path`)
Raw project-relative paths (`rel_path`) are converted to the canonical `"project_name/copy_path"` format used throughout all outputs. This is the single shared path transformation applied wherever a file path appears in output data. It combines `os.path.basename(base_output_dir)` for the project name with `rel_to_copy_path(rel_path)` for the copy-destination structure.

### Stage 2 — Dependency graph construction (`build_symbol_level_deps`)
For each file in `all_file_list`, `file_dependencies.json` is read from disk. The `callee_usages[].from` fields are collected and converted back to relative paths via `output_path_to_rel`, populating the `callees` set. The `caller_usages[].file` fields are similarly collected and populate the `callers` set. The result is a complete `deps_map` covering all files, initialized with empty sets for files whose JSON does not exist.

### Stage 3 — Summary collection (`build_summary_map`)
For each file, `doc.json` is read from disk and the `"summary"` key is extracted. Files without a `doc.json` or without a `"summary"` key receive `None`. The result is a flat `{file_rel: summary_or_None}` dict.

### Stage 4 — Output serialization (three parallel write paths)
The pre-built `symbol_deps` and `summary_map` are consumed by three independent serialization functions, each writing a different output artifact:

- **`save_dependency_summary`**: Combines per-file callers, callees (converted to output-path format), and summaries into a lightweight JSON list. Writes `project_dependency_summary.json`.
- **`save_dependency_graph_as_mermaid`**: Iterates the callees edges in `symbol_deps` to collect nodes and directed edges. Node IDs are sanitized (slashes and dots → underscores); display labels are recovered to source-relative paths via `copy_path_to_rel`. Writes a Mermaid `graph LR` fenced code block as a Markdown file.
- **`save_consolidated_json`**: Builds a `project_dependencies` list (callers/callees/summaries), then for each file reads both `file_dependencies.json` and `doc.json` from disk, strips the `"file"` field from each, and nests them under `"file_dependencies"` and `"doc"` keys respectively. Files with neither JSON present are excluded with a warning. Writes `project_knowledge.json`.

---

## 3. Outputs

| Output | Format | Description |
|---|---|---|
| Return value of `build_symbol_level_deps` | `dict[str, dict[str, set[str]]]` | Caller/callee sets keyed by file relative path |
| Return value of `build_summary_map` | `dict[str, str \| None]` | Summary text (or `None`) keyed by file relative path |
| Return value of `to_output_path` | `str` | Single path in `"project_name/copy_path"` format |
| `project_dependency_summary.json` (disk write) | JSON | Lightweight dependency + summary file |
| `dependency_graph.md` (disk write) | Markdown with Mermaid fenced block | Visual dependency graph |
| `project_knowledge.json` (disk write) | JSON | Consolidated per-file dependency info and design documents |

---

## 4. Key Data Structures

### `deps_map` / `symbol_deps` — `dict[str, dict[str, set[str]]]`
| Field / Key | Type | Purpose |
|---|---|---|
| outer key | `str` | File relative path (project root–relative) |
| `"callers"` | `set[str]` | Relative paths of files that call into this file |
| `"callees"` | `set[str]` | Relative paths of files that this file calls into |

### `summary_map` — `dict[str, str | None]`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File relative path |
| value | `str \| None` | Summary text from `doc.json`, or `None` if unavailable |

### `converted_deps` entry (element of `project_dependencies` list)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | File path in `"project_name/copy_path"` format |
| `"summary"` | `str \| None` | Summary text or `None` |
| `"callers"` | `list[str]` | Sorted caller paths in output-path format |
| `"callees"` | `list[str]` | Sorted callee paths in output-path format |

### `files_list` entry (element of `files` list in consolidated JSON)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | File path in `"project_name/copy_path"` format |
| `"file_dependencies"` | `dict` | Contents of `file_dependencies.json` with `"file"` key stripped |
| `"doc"` | `dict` | Contents of `doc.json` with `"file"` key stripped |

### Consolidated JSON root object
| Field / Key | Type | Purpose |
|---|---|---|
| `"project_name"` | `str` | Basename of `base_output_dir` |
| `"project_dependencies"` | `list[dict]` | Per-file caller/callee/summary entries |
| `"files"` | `list[dict]` | Per-file merged dependency info and design documents |

### Dependency summary JSON root object
| Field / Key | Type | Purpose |
|---|---|---|
| `"project_name"` | `str` | Basename of `base_output_dir` |
| `"files"` | `list[dict]` | Per-file entries with `file`, `summary`, `callers`, `callees` |

# Error Handling

## 1. Overall Strategy

The file adopts a **logging-and-continue (graceful degradation)** strategy. Missing files and absent data are treated as non-fatal conditions: the pipeline proceeds with partial or null values rather than raising exceptions. No try-except blocks are present; the sole explicit error-handling mechanism is `os.path.exists()` guards combined with `logger.warning()` for missing analysis results.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `doc.json` | `doc.json` does not exist in a file's output directory | `summary` is set to `None` without raising an error | Yes | The affected file's summary is `null` in all outputs; dependency structure is unaffected |
| Missing `file_dependencies.json` | `file_dependencies.json` does not exist in a file's output directory | File is skipped silently in `build_symbol_level_deps`; entry is omitted from `file_dependencies` field in consolidated JSON | Yes | The affected file has empty callers/callees sets; no `file_dependencies` key in consolidated JSON entry |
| Consolidated JSON entry with no analysis results | Both `doc.json` and `file_dependencies.json` are absent for a file (entry has only the `file` key) | `logger.warning()` is emitted; the entry is excluded from `files_list` in the consolidated JSON | Yes | File is omitted from the `files` array in `project_knowledge.json`; a warning is logged |
| Missing `callee_usages` / `caller_usages` keys | Loaded `file_dependencies.json` does not contain these keys | `dict.get()` returns an empty list; iteration produces no results | Yes | No callee or caller entries are added for that file; dependency sets remain empty |
| Missing `from` / `file` fields in usage entries | An individual usage object lacks the expected key | `usage.get(...)` returns `None`; the `if callee_file:` / `if caller_file:` guard skips the entry | Yes | That specific dependency link is silently omitted |

---

## 3. Design Notes

**Existence checks over exception handling:** All file-read operations are preceded by `os.path.exists()` rather than catching `FileNotFoundError`. This makes the "file absent" branch an expected, first-class code path rather than an exceptional one, reflecting the design assumption that LLM-based generation steps may legitimately fail or be skipped for some files.

**Null propagation instead of defaults:** Missing summaries propagate as `None` through `summary_map` and appear as `null` in all JSON outputs. This preserves a clear distinction between "summary not generated" and "empty summary," and allows downstream consumers to detect incomplete analysis without additional metadata.

**Warning threshold:** A `logger.warning()` is emitted only when an entry would be completely absent from the consolidated output (both analysis artifacts missing). Partial absences (only `doc.json` or only `file_dependencies.json` missing) are handled silently, treating them as normal incremental-run conditions.

**No retry or fallback computation:** There is no attempt to reconstruct missing data from alternative sources. The file's responsibility is output assembly, so recovery logic (e.g., re-running analysis) is explicitly out of scope and delegated to upstream pipeline stages.

# Summary

Aggregates per-file analysis into project-level outputs. Functions: `to_output_path(base_output_dir:str, rel_path:str)→str`; `build_symbol_level_deps(base_output_dir:str, all_file_list:list[str])→dict[str,dict[str,set[str]]]`; `build_summary_map(base_output_dir:str, all_file_list:list[str])→dict[str,str|None]`; `save_dependency_summary(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)`; `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)`; `save_consolidated_json(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)`. Key structures: `symbol_deps` maps rel-path→`{callers:set[str], callees:set[str]}`; `summary_map` maps rel-path→`str|None`.
