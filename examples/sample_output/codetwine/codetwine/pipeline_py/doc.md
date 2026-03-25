# Design Document: codetwine/pipeline.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Orchestrates the full project analysis pipeline by sequentially building a dependency graph, extracting per-file dependency data, optionally generating LLM-based design documents, and emitting all consolidated output artifacts.

## 2. When to Use This Module

- **To run a complete project analysis**: Call `process_all_files(project_dir, output_dir, llm_client)` to execute the entire pipeline end-to-end, producing per-file `file_dependencies.json`, a project-wide `project_knowledge.json`, `project_dependency_summary.json`, and `dependency_graph.md` under `output_dir/<project_name>/`.
- **To integrate analysis into a CLI entry point**: `main.py` imports and calls `process_all_files` directly via `asyncio.run`, passing a pre-constructed `LLMClient` (or `None` when `ENABLE_LLM_DOC` is false).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int` | `None` | Runs the full analysis pipeline: builds the dependency graph, extracts per-file dependencies, optionally generates design documents, and writes all consolidated output files. |

## 4. Design Decisions

- **Incremental processing via change detection**: `process_all_files` always re-extracts file-level dependency data for all files (Step 2), but passes the set of changed files to `generate_all_docs` so that LLM document generation is skipped for files whose content and transitive dependencies have not changed. Change detection compares SHA-256 hashes of source files against output copies and checks for the presence of `file_dependencies.json`.
- **Path format duality**: The pipeline maintains two distinct path formats—an internal relative path (from the project root) used throughout pipeline processing, and an external `"project_name/copy_path"` format stored in output JSON files. The private helper `_convert_dep_list_to_internal_paths` bridges between them at pipeline startup by stripping the project name prefix and reversing the copy-path transformation.
- **Single `symbol_deps` computation shared across output steps**: `build_symbol_level_deps` is called once and its result is passed to `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`, avoiding redundant I/O across the three output-generation steps.
- **Parse cache eviction**: `parse_cache.clear()` is called at the end of the pipeline to release all tree-sitter AST results held in memory during the run.

## Definition Design Specifications

# Definition Design Specifications

---

## `_convert_dep_list_to_internal_paths`

**Signature:**
```python
def _convert_dep_list_to_internal_paths(
    project_dep_list_raw: list[dict],
    project_name: str,
) -> list[dict]
```
- `project_dep_list_raw`: List of dicts, each with `"file"`, `"callers"`, and `"callees"` keys, where all paths are in `"project_name/copy_path"` format (as produced by `build_project_dependencies`).
- `project_name`: The bare project folder name (e.g., `"my-project"`).
- Returns: A new list of dicts with the same shape, but all paths converted to project-root-relative paths (e.g., `"src/foo.py"`).

**Responsibility:** Bridges the external `"project_name/copy_path"` path format used in `project_dependencies.json` to the internal relative path format expected by the rest of the pipeline. Without this conversion, downstream functions that accept project-relative paths would receive unrecognizable strings.

**When to use:** Called once immediately after `build_project_dependencies` returns, before the dependency list is passed to any internal processing step.

**Design decisions:**
- An inner function `to_internal` is defined to apply both the prefix strip and `copy_path_to_rel` inversion in one step, keeping the transformation composable.
- All three path fields (`"file"`, `"callers"`, `"callees"`) in every entry are transformed; a missing `"callers"` or `"callees"` key is handled gracefully by defaulting to an empty list.

**Constraints & edge cases:**
- If a path does not start with the expected `"project_name/"` prefix, the prefix stripping is skipped and `copy_path_to_rel` is still applied to the unmodified path.
- The function does not validate that the resulting relative paths actually exist on disk.

---

## `_detect_changed_files`

**Signature:**
```python
def _detect_changed_files(
    all_file_list: list[str],
    project_dir: str,
    base_output_dir: str,
) -> set[str]
```
- `all_file_list`: List of project-root-relative paths for every tracked file.
- `project_dir`: Absolute path to the project root.
- `base_output_dir`: Absolute path to the pipeline's output root.
- Returns: A `set[str]` of relative paths identifying files considered changed.

**Responsibility:** Enables incremental processing by identifying which source files have been modified or whose prior output is incomplete, so that downstream steps (particularly document generation) can skip unchanged files.

**When to use:** Called once per pipeline run before processing begins, using the full file list derived from the dependency graph.

**Design decisions:**
- A file is considered changed under two independent conditions: its SHA-256 hash differs from the output copy, **or** the expected `file_dependencies.json` is absent. The second condition recovers from partial failures in previous pipeline runs.
- Uses `is_file_unchanged` from `file_utils` rather than comparing timestamps, providing hash-based accuracy.

**Constraints & edge cases:**
- Files that are new (no output copy yet) will always appear in the returned set because `is_file_unchanged` returns `False` when the destination does not exist.
- Files listed in `all_file_list` that do not exist on disk are not explicitly handled; behavior depends on `is_file_unchanged`.

---

## `_process_file_dependencies`

**Signature:**
```python
def _process_file_dependencies(
    files_to_process: list[str],
    project_dir: str,
    base_output_dir: str,
    project_dep_list: list[dict],
) -> None
```
- `files_to_process`: Relative paths of files to analyze in this invocation.
- `project_dir`: Absolute path to the project root.
- `base_output_dir`: Absolute path to the output root.
- `project_dep_list`: Project-wide dependency list in internal path format (output of `_convert_dep_list_to_internal_paths`).

**Responsibility:** Performs per-file dependency extraction and persists both the structured `file_dependencies.json` result and a verbatim copy of the original source file to the output directory. This produces the stable, queryable output that all later pipeline stages read from.

**When to use:** Called once per pipeline run with the full file list to ensure all output data is up to date before document generation and consolidation.

**Design decisions:**
- Path fields inside `dep_result` (`"file"`, `"from"` in callee usages, `"file"` in caller usages) are converted to `"project_name/copy_path"` format before being written to disk, so that the persisted JSON uses the canonical output-relative format.
- Failures for individual files are caught and logged without aborting the entire run; the file is simply skipped.
- The source file is copied via `shutil.copy2` (which preserves metadata) after the JSON is written, so that a missing copy signals an incomplete run to `_detect_changed_files` on the next invocation.

**Constraints & edge cases:**
- Processing is strictly sequential (no parallelism within this function).
- If `get_file_dependencies` raises an exception, neither `file_dependencies.json` nor the source copy is written for that file.
- The output directory for each file is created with `exist_ok=True`; existing files are silently overwritten.

---

## `process_all_files`

**Signature:**
```python
async def process_all_files(
    project_dir: str,
    output_dir: str,
    llm_client: LLMClient | None,
    max_workers: int = MAX_WORKERS,
) -> None
```
- `project_dir`: Absolute path to the root of the project being analyzed.
- `output_dir`: Absolute path to the parent directory where all output is written; actual output lands in `output_dir/<project_name>/`.
- `llm_client`: An initialized `LLMClient` instance used for design document generation, or `None` if LLM features are disabled.
- `max_workers`: Maximum number of concurrent file-level tasks during design document generation.

**Responsibility:** Serves as the top-level entry point that orchestrates the complete analysis pipeline—from dependency graph construction through consolidated output generation—for a single project directory.

**Async semantics:** This is an `async` function. It `await`s `generate_all_docs`, which internally parallelizes document generation across files at the same dependency depth level using `asyncio.gather`. All other pipeline stages within this function execute sequentially.

**When to use:** Called once per analysis session from `main.py` via `asyncio.run(process_all_files(...))`.

**Design decisions:**

| Stage | Design choice |
|-------|--------------|
| Empty file exclusion | Files that are blank or unreadable are filtered from `project_dep_list` and `all_file_list` before any processing, preventing spurious empty analysis records. |
| Change detection before full processing | Changed files are detected (Step 1.5) before Step 2, so the `changed_files` set can be forwarded to `generate_all_docs` without a second scan. |
| Always process all files in Step 2 | `_process_file_dependencies` receives `all_file_list`, not just `changed_files`, to keep dependency JSON consistent even when source has not changed (avoids stale cross-references). |
| Shared `symbol_deps` | `build_symbol_level_deps` is called once and its result is passed to `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`, avoiding redundant I/O. |
| LLM gating | Document generation is entirely skipped when `ENABLE_LLM_DOC` is `False`; the rest of the pipeline still runs to produce dependency JSON and Mermaid output. |
| Cache clearing | `parse_cache.clear()` is called at the end to release AST memory held throughout the run. |

**Constraints & edge cases:**
- `llm_client` may be `None`; this is only valid when `ENABLE_LLM_DOC` is `False`, since `generate_all_docs` is not called in that case.
- `base_output_dir` (`output_dir/<project_name>/`) is created if absent; existing content is not deleted, enabling incremental re-runs.
- Empty files are identified by attempting a UTF-8 read; files that raise `OSError` or `UnicodeDecodeError` are silently excluded from the empty-file set and remain in the processing list.
- The `max_workers` parameter only affects concurrency inside `generate_all_docs`; no other stage uses it.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

- `codetwine/pipeline_py/pipeline.py` → `codetwine/parsers/ts_parser.py` : imports `parse_cache` to call `parse_cache.clear()` after full pipeline execution, freeing cached AST parse results from memory.

- `codetwine/pipeline_py/pipeline.py` → `codetwine/extractors/dependency_graph.py` : imports `build_project_dependencies` to construct the project-wide inter-file dependency graph (callers/callees per file) as the first step of the pipeline.

- `codetwine/pipeline_py/pipeline.py` → `codetwine/file_analyzer.py` : imports `get_file_dependencies` to perform per-file dependency analysis (definitions, callee usages, caller usages) for each source file.

- `codetwine/pipeline_py/pipeline.py` → `codetwine/output.py` : imports `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, and `build_summary_map` to convert internal paths to output format, build symbol-level dependency maps, collect doc summaries, and produce the three project-level output artifacts (`project_knowledge.json`, `project_dependency_summary.json`, `dependency_graph.md`).

- `codetwine/pipeline_py/pipeline.py` → `codetwine/doc_creator.py` : imports `generate_all_docs` to drive LLM-based design document generation for all files in topological order when `ENABLE_LLM_DOC` is enabled.

- `codetwine/pipeline_py/pipeline.py` → `codetwine/llm/client.py` : imports `LLMClient` as the type annotation for the LLM client parameter passed through to `generate_all_docs`.

- `codetwine/pipeline_py/pipeline.py` → `codetwine/utils/file_utils.py` : imports `copy_path_to_rel` to strip project-name-prefixed copy paths back to project-relative paths, `is_file_unchanged` to compare source and output file hashes for change detection, and `resolve_file_output_dir` to determine the output directory path for each file.

- `codetwine/pipeline_py/pipeline.py` → `codetwine/config/settings.py` : imports `MAX_WORKERS` as the default concurrency limit for document generation and `ENABLE_LLM_DOC` to conditionally skip the LLM document generation step.

---

### Dependents (modules that import this file)

- `main.py` → `codetwine/pipeline_py/pipeline.py` : imports `process_all_files` as the top-level entry point to execute the full project analysis pipeline, passing in the resolved project directory, output directory, and an `LLMClient` instance (or `None`).

---

### Dependency Direction

All relationships are **unidirectional**:

- `pipeline.py` → `ts_parser.py`: one-way; `ts_parser.py` has no knowledge of `pipeline.py`.
- `pipeline.py` → `dependency_graph.py`: one-way; `dependency_graph.py` does not reference `pipeline.py`.
- `pipeline.py` → `file_analyzer.py`: one-way; `file_analyzer.py` does not reference `pipeline.py`.
- `pipeline.py` → `output.py`: one-way; `output.py` does not reference `pipeline.py`.
- `pipeline.py` → `doc_creator.py`: one-way; `doc_creator.py` does not reference `pipeline.py`.
- `pipeline.py` → `llm/client.py`: one-way; `llm/client.py` does not reference `pipeline.py`.
- `pipeline.py` → `utils/file_utils.py`: one-way; `file_utils.py` does not reference `pipeline.py`.
- `pipeline.py` → `config/settings.py`: one-way; `settings.py` does not reference `pipeline.py`.
- `main.py` → `pipeline.py`: one-way; `pipeline.py` does not reference `main.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `project_dir` | Caller argument (`main.py`) | Absolute path string to the project root |
| `output_dir` | Caller argument (`main.py`) | Absolute path string to the output root |
| `llm_client` | Caller argument (`main.py`) | `LLMClient` instance or `None` |
| `max_workers` | Caller argument / `MAX_WORKERS` config | Integer |
| `ENABLE_LLM_DOC` | `codetwine/config/settings.py` | Boolean config value |
| `MAX_WORKERS` | `codetwine/config/settings.py` | Integer config value |
| Source files | `os.walk` via `build_project_dependencies` | Raw file bytes on disk |
| `file_dependencies.json` (existing) | Per-file output directory | JSON |
| Copied source files (existing) | Per-file output directory | Binary file copy |
| `doc.json` (existing) | Per-file output directory | JSON |

---

## 2. Transformation Overview

### Stage 1 — Project Dependency Graph Construction
`build_project_dependencies(project_dir)` walks the project filesystem and returns a raw list of per-file dependency records. Paths are in `"project_name/copy_path"` format. `_convert_dep_list_to_internal_paths` strips the project-name prefix and reverses the copy-path encoding (via `copy_path_to_rel`) to produce plain project-relative paths used throughout the rest of the pipeline. Empty files are detected by reading each source file and filtered out of `project_dep_list` and `all_file_list`.

### Stage 1.5 — Change Detection
`_detect_changed_files` iterates `all_file_list` and compares each source file against its previously copied output counterpart using SHA-256 hashing (`is_file_unchanged`). A file is marked changed if the hash differs or if its `file_dependencies.json` is absent. The result is a `set[str]` of changed relative paths, used later to scope document regeneration.

### Stage 2 — Per-File Dependency Extraction
`_process_file_dependencies` iterates all files (not just changed ones) and calls `get_file_dependencies` for each. The returned dict (containing `file`, `definitions`, `callee_usages`, `caller_usages`) has its path fields converted from project-relative format to `"project_name/copy_path"` format via `to_output_path`. The result is serialized to `file_dependencies.json` and the original source file is copied alongside it into the per-file output directory.

### Stage 3 — Design Document Generation (conditional)
If `ENABLE_LLM_DOC` is `True`, `generate_all_docs` is called with `project_dep_list`, `llm_client`, `max_workers`, and `changed_files`. Files are processed in topological dependency order; unchanged files (and files whose callees are all unchanged) reuse existing `doc.json`. Outputs are `doc.json` and `doc.md` per file. This stage is skipped entirely if `ENABLE_LLM_DOC` is `False`.

### Stage 3.5 — Symbol-Level Dependency & Summary Aggregation
`build_symbol_level_deps` reads every file's `file_dependencies.json` and derives a `dict` mapping each file's relative path to its actual (symbol-level) caller and callee sets. `build_summary_map` reads each file's `doc.json` and extracts the `summary` field. Both structures are computed once and passed to the three subsequent output stages to avoid redundant disk reads.

### Stage 4 — Mermaid Dependency Graph
`save_dependency_graph_as_mermaid` consumes `symbol_deps` and writes a Mermaid `graph LR` flowchart to `dependency_graph.md` under `base_output_dir`.

### Stage 5 — Consolidated JSON Outputs
Two JSON aggregation functions consume `symbol_deps` and `summary_map`:
- `save_dependency_summary` writes `project_dependency_summary.json` — a lightweight file-level dependency + summary listing.
- `save_consolidated_json` writes `project_knowledge.json` — the full consolidated record merging `file_dependencies.json` and `doc.json` for every file.

Finally, `parse_cache.clear()` frees the tree-sitter AST cache.

---

## 3. Outputs

| Output | Location | Format |
|---|---|---|
| `file_dependencies.json` | `{base_output_dir}/{copy_path_dir}/` per file | JSON |
| Copied source file | `{base_output_dir}/{copy_path_dir}/` per file | Binary copy of the original |
| `doc.json` + `doc.md` | `{base_output_dir}/{copy_path_dir}/` per file (LLM path only) | JSON + Markdown |
| `project_dependency_summary.json` | `{base_output_dir}/` | JSON |
| `dependency_graph.md` | `{base_output_dir}/` | Markdown (Mermaid diagram) |
| `project_knowledge.json` | `{base_output_dir}/` | JSON |

---

## 4. Key Data Structures

### `project_dep_list_raw` — raw output from `build_project_dependencies`
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Path in `"project_name/copy_path"` format |
| `callers` | `list[str]` | Paths of files that import this file, same format |
| `callees` | `list[str]` | Paths of files this file imports, same format |

### `project_dep_list` — internal path format (after `_convert_dep_list_to_internal_paths`)
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Project-relative path (e.g. `"src/foo.py"`) |
| `callers` | `list[str]` | Project-relative paths of callers |
| `callees` | `list[str]` | Project-relative paths of callees |

### `dep_result` — output of `get_file_dependencies`, written to `file_dependencies.json`
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Converted to `"project_name/copy_path"` format before saving |
| `definitions` | `list[dict]` | Named definitions (name, type, start_line, end_line, context) |
| `callee_usages` | `list[dict]` | Usages of symbols imported from other files; `from` field converted to output path format |
| `caller_usages` | `list[dict]` | Locations in other files that use symbols from this file; `file` field converted to output path format |

### `symbol_deps` — output of `build_symbol_level_deps`
| Field / Key | Type | Purpose |
|---|---|---|
| `{file_rel}` (key) | `str` | Project-relative path of the file |
| `callers` | `set[str]` | Relative paths of files that actually use symbols from this file |
| `callees` | `set[str]` | Relative paths of files whose symbols this file actually uses |

### `summary_map` — output of `build_summary_map`
| Field / Key | Type | Purpose |
|---|---|---|
| `{file_rel}` (key) | `str` | Project-relative path of the file |
| value | `str \| None` | LLM-generated summary text, or `None` if `doc.json` is absent or has no summary |

### `changed_files` — output of `_detect_changed_files`
| Field / Key | Type | Purpose |
|---|---|---|
| elements | `str` | Project-relative paths of files whose source hash differs from the output copy, or whose `file_dependencies.json` is missing |

## Error Handling

# Error Handling

## 1. Overall Strategy

The pipeline adopts a **logging-and-continue** strategy for per-file processing failures, combined with **graceful degradation** at the pipeline level. Individual file failures during dependency extraction are caught, logged as errors, and skipped without terminating the overall pipeline run. At the broader pipeline level, unrecoverable conditions (e.g., inability to build the project dependency graph or access the output directory) are not explicitly caught, allowing exceptions to propagate naturally and halt execution. LLM document generation errors are delegated entirely to the `generate_all_docs` function in `doc_creator.py`.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Per-file dependency extraction failure | Any exception raised during `get_file_dependencies`, JSON write, or file copy for a single file | Caught, logged at ERROR level (`FAIL: {file_rel}: {e}`), file skipped | Yes | That file's `file_dependencies.json` and output copy are not produced; downstream steps may lack data for the file |
| File read failure during empty-file detection | `OSError` or `UnicodeDecodeError` when opening a source file to check for empty content | Silently ignored (`pass`); file is not added to the empty-file exclusion set | Yes | The file is treated as non-empty and remains in the processing list |
| Missing or mismatched output copy (change detection) | Source file hash differs from output copy, or `file_dependencies.json` is absent in the output directory | File is added to the `changed_files` set for reprocessing; no error is raised | Yes | File is reprocessed in subsequent stages rather than reused |
| LLM document generation errors | Exceptions occurring within `generate_all_docs` | Delegated to `doc_creator.py`; pipeline continues after the call returns | Yes (per `doc_creator.py` policy) | Affected files may lack design documents; pipeline proceeds to subsequent steps |
| Project dependency graph build failure | Exception in `build_project_dependencies` | Not caught; exception propagates to the caller | No | Entire pipeline run aborts |
| Output directory creation failure | `os.makedirs` fails (e.g., permission error) | Not caught; exception propagates | No | Pipeline run aborts before processing begins |

---

## 3. Design Notes

- **Isolation of per-file failures**: The try-except in `_process_file_dependencies` intentionally isolates each file so that a failure in one does not prevent others from being processed. This reflects the expectation that individual file parse or I/O errors are non-fatal to the overall analysis.
- **Silent skip for empty-file detection**: Failures during the empty-content check are silently ignored rather than logged. The conservative behavior (keeping the file in the list) avoids incorrectly excluding files that could not be read due to transient errors.
- **No retry at the pipeline level**: Retry logic is absent from the pipeline itself; it exists only within `LLMClient._call_with_retry` for rate-limit errors, which is entirely outside this file's scope.
- **Fail-fast for structural preconditions**: Operations that must succeed for the pipeline to have any meaningful output—such as building the dependency graph and creating the output directory—are left unguarded, allowing immediate failure rather than producing partial or misleading results.
- **Change detection as resilience mechanism**: The detection of missing `file_dependencies.json` as a "changed" condition serves as implicit recovery from a prior incomplete pipeline run, ensuring previously failed files are retried on the next execution.

## Summary

Orchestrates the full project analysis pipeline. Public API: `async process_all_files(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int) -> None`. Consumes `project_dep_list` (list[dict] with `file`, `callers`, `callees` as project-relative paths) and produces `file_dependencies.json`, copied source files, `project_knowledge.json`, `project_dependency_summary.json`, and `dependency_graph.md`. Key intermediate structures: `symbol_deps` (dict mapping file path to caller/callee sets), `summary_map` (dict mapping file path to summary str), and `changed_files` (set[str]).
