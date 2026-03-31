# Design Document: codetwine/pipeline.py

## Overview & Purpose

## 1. Module Summary

Orchestrates the end-to-end analysis pipeline for a single project directory, coordinating dependency graph construction, per-file dependency extraction, LLM-based document generation, and all consolidated output artifact production.

## 2. When to Use This Module

- **Triggering a full project analysis**: Call `process_all_files(project_dir, output_dir, llm_client)` to run the complete pipeline from raw source files to all output artifacts (`file_dependencies.json`, `project_knowledge.json`, `project_dependency_summary.json`, `dependency_graph.md`, and per-file copies).
- **Running from the CLI entry point (`main.py`)**: This module's `process_all_files` is the sole public entry point invoked by `main.py` after argument resolution and LLM client construction.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int` | `None` | Runs the full analysis pipeline: builds the dependency graph, detects changed files, extracts per-file dependencies, optionally generates LLM design documents, and writes all consolidated output artifacts. |

## 4. Design Decisions

- **Incremental processing via change detection**: Before extracting per-file dependencies, the pipeline detects which files have changed by comparing SHA-256 hashes of source files against their output copies. However, dependency extraction (Step 2) always processes all files unconditionally to maintain consistency; only LLM document generation (Step 3) uses the changed-file set to skip unchanged files and their unaffected dependents.

- **Path format separation**: Two distinct path formats are maintained throughout the pipeline. The `project_name/copy_path` format is used in all persisted JSON artifacts for portability. An internal relative path format (plain project-root-relative POSIX paths) is used during in-memory processing. The private helpers `_convert_dep_list_to_internal_paths` and `to_output_path` manage conversion at the boundary between these two representations.

- **Symbol-level dependency computation is performed once and shared**: `build_symbol_level_deps` is called once and its result (`symbol_deps`) is passed to `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`, avoiding redundant file I/O across the three output-generation steps.

- **Conditional LLM document generation**: Document generation is gated by the `ENABLE_LLM_DOC` configuration constant, allowing the pipeline to run in a dependency-analysis-only mode without requiring an LLM client.

## Definition Design Specifications

---

## Module-Level Constants / Imports

No module-level constants are defined. The module imports and re-exports configuration values `MAX_WORKERS` and `ENABLE_LLM_DOC` from `codetwine/config/settings.py` for use as default argument values.

---

## `_convert_dep_list_to_internal_paths`

**Signature:**
```python
def _convert_dep_list_to_internal_paths(
    project_dep_list_raw: list[dict],
    project_name: str,
) -> list[dict]
```

| Parameter | Type | Description |
|---|---|---|
| `project_dep_list_raw` | `list[dict]` | Raw dependency list from `build_project_dependencies`; each dict has `"file"`, `"callers"`, `"callees"` keys with paths in `"project_name/copy_path"` format |
| `project_name` | `str` | The base directory name of the project (e.g. `"my-project"`) |
| **Returns** | `list[dict]` | Same structure as input, with all paths converted to project-relative paths |

**Responsibility:** Translates the `"project_name/copy_path"` path format used in `project_dependencies.json` into plain project-relative paths (`"src/foo.py"`) for use throughout the internal pipeline.

**When to use:** Called once at startup within `process_all_files`, immediately after `build_project_dependencies` returns its raw result.

**Design decisions:**
- Uses a nested `to_internal` helper that first strips the `project_name/` prefix, then delegates to `copy_path_to_rel` to reverse the copy-path encoding. This two-step process is required because the raw format compounds two transformations.
- Paths that do not start with the expected prefix are passed through unchanged (defensive fallback).
- Produces a new list with reconstructed dicts containing exactly `"file"`, `"callers"`, and `"callees"` keys; no other keys from the raw input are preserved.

**Constraints & edge cases:**
- Assumes the `"callers"` and `"callees"` keys may be absent; uses `.get()` with `[]` as default.
- The prefix stripping is exact: paths must begin with `"{project_name}/"` to be stripped.

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

| Parameter | Type | Description |
|---|---|---|
| `all_file_list` | `list[str]` | Project-relative paths of all files to check |
| `project_dir` | `str` | Absolute path to the project root |
| `base_output_dir` | `str` | Absolute path to the output root directory |
| **Returns** | `set[str]` | Set of project-relative paths for files detected as changed |

**Responsibility:** Identifies which source files have changed since the last run by comparing SHA256 hashes, enabling selective document regeneration downstream.

**When to use:** Called once in `process_all_files` before dependency extraction to establish the change set for the current run.

**Design decisions:**
- A file is classified as changed under two conditions: its hash differs from the output copy, **or** its `file_dependencies.json` does not exist. The second condition ensures recovery from partial/failed previous runs.
- Delegates hash comparison entirely to `is_file_unchanged`, keeping detection logic thin.

**Constraints & edge cases:**
- Files where the output copy does not exist at all are detected as changed (via `is_file_unchanged` returning `False` for missing copies).
- Files that cannot be read by `is_file_unchanged` are treated as changed.

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

| Parameter | Type | Description |
|---|---|---|
| `files_to_process` | `list[str]` | Project-relative paths of files to analyze |
| `project_dir` | `str` | Absolute path to the project root |
| `base_output_dir` | `str` | Absolute path to the output root directory |
| `project_dep_list` | `list[dict]` | Project-wide dependency list in internal path format |

**Responsibility:** Runs per-file dependency analysis for every file in the list, writing `file_dependencies.json` and a copy of the source file to each file's output directory.

**When to use:** Called once per pipeline run in `process_all_files` to populate the per-file output directories before document generation and consolidation.

**Design decisions:**
- All paths stored in `file_dependencies.json` are converted to `"project_name/copy_path"` output format via `to_output_path` before saving. This includes `dep_result["file"]`, `"from"` fields in `callee_usages`, and `"file"` fields in `caller_usages`.
- Errors for individual files are caught, logged, and skipped; processing continues for remaining files. This prevents a single unparseable file from aborting the entire run.
- The output directory is created with `exist_ok=True` before writing, so partial output from previous runs does not cause failures.
- Runs sequentially (not parallel), despite the outer function accepting `max_workers`.

**Constraints & edge cases:**
- `files_to_process` may be any subset of `all_file_list`; in the current pipeline it is always the full list.
- Exceptions from `get_file_dependencies` or file I/O are silently swallowed per-file after logging.

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

| Parameter | Type | Description |
|---|---|---|
| `project_dir` | `str` | Absolute path to the project root to analyze |
| `output_dir` | `str` | Absolute path to the parent output directory |
| `llm_client` | `LLMClient \| None` | LLM client for design document generation; `None` disables LLM steps |
| `max_workers` | `int` | Maximum concurrency for parallel document generation within each dependency level |

**Responsibility:** Orchestrates the full analysis pipeline for an entire project, from dependency graph construction through per-file analysis, document generation, and output artifact creation.

**Async semantics:** This is an `async` function. It `await`s `generate_all_docs`, which internally uses `asyncio.gather` for parallel LLM calls. All other steps (dependency building, file processing, JSON output) are synchronous within this coroutine.

**When to use:** Called once by the CLI entry point in `main.py` via `asyncio.run(process_all_files(...))` to execute the complete analysis pipeline.

**Design decisions:**

- **Empty file exclusion:** Files that contain only whitespace are excluded from `all_file_list` and `project_dep_list` before any processing begins, preventing downstream parser errors.
- **Change detection before full processing:** `_detect_changed_files` is called before dependency extraction to build `changed_files`, which is passed to `generate_all_docs` to skip regenerating documents for unchanged files. Dependency extraction itself always runs for all files to maintain consistency.
- **`symbol_deps` computed once and shared:** `build_symbol_level_deps` is called a single time and its result is passed to both `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`, avoiding redundant JSON reads.
- **LLM step is gated by `ENABLE_LLM_DOC`:** When the flag is `False`, the entire `generate_all_docs` call is skipped; `summary_map` values will be `None` throughout the output artifacts.
- **Cache cleared at end:** `parse_cache.clear()` is called after all processing to release tree-sitter parse results from memory.
- **Output directory structure:** `base_output_dir` is `output_dir / project_name`, so all artifacts for a project are scoped under its name.

**Pipeline stages (summary):**

| Stage | Description |
|---|---|
| 1 | Build project-wide dependency graph; convert paths to internal format; exclude empty files |
| 1.5 | Detect changed files via hash comparison |
| 2 | Extract per-file dependency info and write `file_dependencies.json` + source copy |
| 3 | Generate LLM design documents in topological order (skipped if `ENABLE_LLM_DOC=False`) |
| 3.5 | Compute `symbol_deps` and `summary_map`; write `project_dependency_summary.json` |
| 4 | Write `dependency_graph.md` (Mermaid diagram) |
| 5 | Write `project_knowledge.json` (consolidated JSON) |

**Constraints & edge cases:**
- `llm_client=None` is valid and results in the document generation stage being skipped regardless of `ENABLE_LLM_DOC`.
- `max_workers` only affects concurrency inside `generate_all_docs`; it has no effect on the synchronous dependency extraction loop.
- The function does not return any value; all results are written to disk.

## Dependency Description

### Dependencies (modules this file imports)

**`codetwine/pipeline_py/pipeline.py` → `codetwine/parsers/ts_parser.py`**
Imports `parse_cache` (a `dict[str, tuple[Node, bytes]]`) to call `parse_cache.clear()` at the end of processing, freeing memory held by cached parse results.

**`codetwine/pipeline_py/pipeline.py` → `codetwine/extractors/dependency_graph.py`**
Imports `build_project_dependencies` to perform Step 1 of the pipeline: scanning all project source files and constructing the project-wide inter-file dependency graph (callers/callees in `project_name/copy_path` format).

**`codetwine/pipeline_py/pipeline.py` → `codetwine/file_analyzer.py`**
Imports `get_file_dependencies` to perform Step 2: analyzing each individual source file and extracting its definitions, callee usages, and caller usages into a structured dict that is saved as `file_dependencies.json`.

**`codetwine/pipeline_py/pipeline.py` → `codetwine/output.py`**
Imports multiple symbols to drive Steps 3.5–5:
- `to_output_path` — converts project-relative paths to `project_name/copy_path` format when writing `file_dependencies.json` entries.
- `build_symbol_level_deps` — builds symbol-level caller/callee dependency maps from each file's `file_dependencies.json`, shared across subsequent output steps.
- `build_summary_map` — reads `doc.json` summaries for all files into a `dict[str, str|None]`.
- `save_dependency_summary` — writes `project_dependency_summary.json` combining symbol-level deps and summaries.
- `save_dependency_graph_as_mermaid` — writes `dependency_graph.md` as a Mermaid flowchart.
- `save_consolidated_json` — writes `project_knowledge.json` consolidating all per-file analysis results.

**`codetwine/pipeline_py/pipeline.py` → `codetwine/doc_creator.py`**
Imports `generate_all_docs` to perform Step 3: generating LLM-based design documents for all files in topological dependency order, skipping unchanged files when possible.

**`codetwine/pipeline_py/pipeline.py` → `codetwine/llm/client.py`**
Imports `LLMClient` as a type annotation for the `llm_client` parameter of `process_all_files`, and passes the client instance through to `generate_all_docs`.

**`codetwine/pipeline_py/pipeline.py` → `codetwine/utils/file_utils.py`**
Imports three utilities:
- `copy_path_to_rel` — used in `_convert_dep_list_to_internal_paths` to strip the `project_name/copy_path` prefix and restore project-relative paths for internal pipeline use.
- `is_file_unchanged` — used in `_detect_changed_files` to compare SHA256 hashes of source files against their output copies.
- `resolve_file_output_dir` — used throughout to compute the output directory path for any given file-relative path.

**`codetwine/pipeline_py/pipeline.py` → `codetwine/config/settings.py`**
Imports two configuration constants:
- `MAX_WORKERS` — default value for the `max_workers` parameter of `process_all_files`.
- `ENABLE_LLM_DOC` — boolean flag controlling whether Step 3 (design document generation) is executed.

---

### Dependents (modules that import this file)

**`main.py` → `codetwine/pipeline_py/pipeline.py`**
Imports `process_all_files` as the top-level entry point of the analysis pipeline. `main.py` resolves the project and output directories, conditionally constructs an `LLMClient`, and delegates all project analysis work to `process_all_files` via `asyncio.run(...)`.

---

### Dependency Direction

All relationships are **unidirectional**:

- `pipeline.py` → `ts_parser.py`: one-way; `ts_parser.py` does not import `pipeline.py`.
- `pipeline.py` → `dependency_graph.py`: one-way; `dependency_graph.py` does not import `pipeline.py`.
- `pipeline.py` → `file_analyzer.py`: one-way; `file_analyzer.py` does not import `pipeline.py`.
- `pipeline.py` → `output.py`: one-way; `output.py` does not import `pipeline.py`.
- `pipeline.py` → `doc_creator.py`: one-way; `doc_creator.py` does not import `pipeline.py`.
- `pipeline.py` → `llm/client.py`: one-way; `llm/client.py` does not import `pipeline.py`.
- `pipeline.py` → `utils/file_utils.py`: one-way; `file_utils.py` does not import `pipeline.py`.
- `pipeline.py` → `config/settings.py`: one-way; `settings.py` does not import `pipeline.py`.
- `main.py` → `pipeline.py`: one-way; `pipeline.py` does not import `main.py`.

## Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `project_dir` argument | `str` (absolute path) | Root directory of the project to analyze |
| `output_dir` argument | `str` (absolute path) | Root directory for writing all output artifacts |
| `llm_client` argument | `LLMClient \| None` | LLM API wrapper; `None` when `ENABLE_LLM_DOC=False` |
| `max_workers` argument | `int` | Concurrency limit for document generation |
| `ENABLE_LLM_DOC` config | `bool` | Whether to run LLM-based document generation |
| `MAX_WORKERS` config | `int` | Default value for `max_workers` |
| Source files on disk | Raw file content | Read transitively via `build_project_dependencies`, `get_file_dependencies`, and hash comparison |
| Per-file `file_dependencies.json` | JSON file | Read by `build_symbol_level_deps` and `save_consolidated_json` to reconstruct previous analysis state |
| Per-file `doc.json` | JSON file | Read by `build_summary_map` and `save_consolidated_json` to retrieve previously generated summaries |
| Per-file copied source files | Binary/text file | Read by `is_file_unchanged` for change detection via SHA256 hash comparison |

---

## 2. Transformation Overview

### Stage 1 — Project dependency graph construction
`build_project_dependencies(project_dir)` walks the project directory and returns `project_dep_list_raw`: a flat list of dicts with `file`, `callers`, and `callees` keys, where all paths are in `project_name/copy_path` format.

### Stage 2 — Path normalization to internal format
`_convert_dep_list_to_internal_paths` strips the `project_name/` prefix from each path and then applies `copy_path_to_rel` to recover the original project-relative path. This produces `project_dep_list` (internal format), which is used throughout the rest of the pipeline for file addressing.

### Stage 3 — File list extraction and empty file exclusion
`all_file_list` is extracted as the `file` fields from `project_dep_list`. Each file is opened and checked for non-whitespace content; files that are empty are removed from both `project_dep_list` and `all_file_list`.

### Stage 4 — Change detection
`_detect_changed_files` iterates `all_file_list`, resolving each file's output directory via `resolve_file_output_dir`. It calls `is_file_unchanged` (SHA256 hash comparison between source and output copy) and checks for the existence of `file_dependencies.json`. Files that fail either check are collected into `changed_files: set[str]`.

### Stage 5 — Per-file dependency extraction and serialization
`_process_file_dependencies` iterates all files (unconditionally, not just changed ones). For each file, `get_file_dependencies` returns a dict with `file`, `definitions`, `callee_usages`, and `caller_usages`. Paths inside this dict are converted to `project_name/copy_path` format via `to_output_path`. The result is written to `file_dependencies.json` in the file's output directory, and the source file itself is copied alongside it.

### Stage 6 — LLM document generation (conditional)
When `ENABLE_LLM_DOC=True`, `generate_all_docs` receives `project_dep_list`, `llm_client`, `max_workers`, and `changed_files`. It processes files in topological dependency order, skipping files whose neither content nor callees have changed. Outputs are `doc.json` and `doc.md` per file.

### Stage 7 — Symbol-level dependency aggregation
`build_symbol_level_deps` reads all `file_dependencies.json` files and aggregates `callee_usages[].from` and `caller_usages[].file` fields into a shared `symbol_deps` dict mapping each relative file path to its actual symbol-level `callers` and `callees` sets. This structure is computed once and reused across stages 8, 9, and 10.

`build_summary_map` reads each file's `doc.json` and extracts the `summary` field into a dict keyed by relative file path. Also reused across subsequent stages.

### Stage 8 — Dependency summary JSON
`save_dependency_summary` combines `symbol_deps` and `summary_map` into `project_dependency_summary.json`, emitting a lightweight file-level dependency + summary listing.

### Stage 9 — Mermaid diagram
`save_dependency_graph_as_mermaid` converts `symbol_deps` edges into a Mermaid `graph LR` flowchart written to `dependency_graph.md`.

### Stage 10 — Consolidated knowledge JSON
`save_consolidated_json` merges per-file `file_dependencies.json`, `doc.json`, `symbol_deps`, and `summary_map` into a single `project_knowledge.json`.

### Stage 11 — Cache cleanup
`parse_cache.clear()` releases the in-memory tree-sitter parse cache accumulated during analysis.

---

## 3. Outputs

| Artifact | Format | Location | Written By |
|----------|--------|----------|------------|
| `file_dependencies.json` | JSON file | `{base_output_dir}/{copy_path_dir}/` per file | `_process_file_dependencies` |
| Copied source file | Binary/text file | Same directory as `file_dependencies.json` | `_process_file_dependencies` |
| `doc.json` / `doc.md` | JSON + Markdown files | Same per-file output directory | `generate_all_docs` (conditional) |
| `project_dependency_summary.json` | JSON file | `{base_output_dir}/` | `save_dependency_summary` |
| `dependency_graph.md` | Markdown (Mermaid) | `{base_output_dir}/` | `save_dependency_graph_as_mermaid` |
| `project_knowledge.json` | JSON file | `{base_output_dir}/` | `save_consolidated_json` |

`process_all_files` itself has no return value (`-> None`); all results are side effects (file writes and console/log output).

---

## 4. Key Data Structures

### `project_dep_list_raw` — raw output of `build_project_dependencies`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | File path in `project_name/copy_path` format |
| `callers` | `list[str]` | Paths of files that import this file, same format |
| `callees` | `list[str]` | Paths of files this file imports, same format |

### `project_dep_list` — internal path format after `_convert_dep_list_to_internal_paths`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | Project-relative path (e.g. `src/foo.py`) |
| `callers` | `list[str]` | Project-relative paths of callers |
| `callees` | `list[str]` | Project-relative paths of callees |

### `dep_result` — output of `get_file_dependencies`, written to `file_dependencies.json`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | File path (converted to `project_name/copy_path` format before write) |
| `definitions` | `list[dict]` | Symbols defined in this file (name, type, line range, source context) |
| `callee_usages` | `list[dict]` | Usage records for symbols imported from other files; `from` field holds dependency file path |
| `caller_usages` | `list[dict]` | Usage records for symbols from this file used in other files; `file` field holds caller file path |

### `symbol_deps` — output of `build_symbol_level_deps`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| key | `str` | Project-relative file path |
| value `callers` | `set[str]` | Relative paths of files that actually use symbols from this file |
| value `callees` | `set[str]` | Relative paths of files whose symbols this file actually uses |

### `summary_map` — output of `build_summary_map`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| key | `str` | Project-relative file path |
| value | `str \| None` | LLM-generated summary text, or `None` if no `doc.json` exists |

### `changed_files` — output of `_detect_changed_files`
| Type | Purpose |
|------|---------|
| `set[str]` | Project-relative paths of files whose source hash differs from output copy, or whose `file_dependencies.json` is absent; passed to `generate_all_docs` to limit LLM regeneration scope |

## Error Handling

## 1. Overall Strategy

The pipeline adopts a **logging-and-continue** strategy for per-file processing failures, combined with **graceful degradation** at the pipeline level. Individual file failures are caught, logged at the ERROR level, and skipped without halting the overall pipeline. The pipeline as a whole does not implement retry logic at this layer (retries are delegated to `LLMClient`). Unrecoverable errors at the pipeline orchestration level (e.g., inability to build the dependency graph or create output directories) are not explicitly caught and will propagate as unhandled exceptions, effectively acting as fail-fast for critical setup steps.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| File read error (`OSError`, `UnicodeDecodeError`) | A file in `all_file_list` cannot be opened or decoded when checking for empty content | Silently caught with `pass`; file is not added to `empty_files` | Yes | File proceeds to processing as-is |
| Per-file dependency extraction failure (`Exception`) | Any exception raised during `get_file_dependencies`, JSON serialization, or file copy for a single file | Caught; error logged at ERROR level via `logger.error`; file is skipped | Yes | That file's `file_dependencies.json` and output copy are not produced; downstream steps (summary, consolidated JSON) will find no output for it |
| Document generation error (`Exception`) | An exception is raised within a `process_one` async task inside `generate_all_docs` | Caught by `asyncio.gather` with `return_exceptions=True`; logged at ERROR level; task result skipped | Yes | That file's `doc.json` and `doc.md` are not generated; `doc_map` does not receive an entry for it |
| LLM rate limit (`RateLimitError`) | LLM API returns a 429 response | Handled inside `LLMClient._call_with_retry`; retried up to `MAX_RETRIES` times with `RETRY_WAIT` delay | Yes (up to max retries) | Returns `None` after exhausting retries; file doc is skipped |
| LLM context window exceeded (`ContextWindowExceededError`) | Prompt exceeds the model's context window | Re-raised immediately inside `LLMClient`; not retried | No (propagates) | Propagates up through `generate_all_docs` task; caught by `asyncio.gather` as an exception result |
| LLM API error (`openai.APIError`) | Non-rate-limit API error from the LLM provider | Logged at ERROR level; returns `None` immediately without retry | Yes (skipped) | File doc generation returns `None`; file is skipped |
| Missing `file_dependencies.json` in output | Previous pipeline run failed partway through for a file | Detected in `_detect_changed_files`; file is added to `changed_files` set | Yes | File is reprocessed in the current run |
| Missing output copy of source file | Source file was never copied to the output directory | `is_file_unchanged` returns `False`; file added to `changed_files` | Yes | File is reprocessed in the current run |

---

## 3. Design Notes

- **Isolation of per-file failures**: The `_process_file_dependencies` loop wraps each file independently so that a single file's failure does not block processing of remaining files. This is a deliberate trade-off prioritizing pipeline completion over strict correctness.
- **Incomplete state recovery**: The `_detect_changed_files` function explicitly checks for the presence of both the copied source file and `file_dependencies.json`. Files missing either artifact are treated as changed and reprocessed, providing resilience against partially failed prior runs without requiring a full reanalysis.
- **Delegation of retry logic**: Retry behavior is entirely encapsulated within `LLMClient`; the pipeline itself performs no retries. This keeps the pipeline orchestration layer simple and places retry policy decisions in the LLM client layer.
- **Silent suppression for empty-file detection**: `OSError` and `UnicodeDecodeError` during empty-file checking are silently ignored. Since this check is purely an optimization to exclude empty files, failures here are treated as non-critical and the file is allowed to proceed.
- **Unhandled critical paths**: Steps that are essential for the pipeline to function at all—such as `build_project_dependencies`, `os.makedirs`, and the final output writes—have no explicit error handling in this file and will raise unhandled exceptions, terminating the process.

## Summary

`pipeline.py` orchestrates the full project analysis pipeline. Public API: `async process_all_files(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int) -> None`. Consumes `project_dep_list` (`list[dict]` with `file`, `callers`, `callees` as project-relative paths), `changed_files` (`set[str]`), `symbol_deps` (`dict[str, {callers, callees}]`), and `summary_map` (`dict[str, str | None]`). Produces `file_dependencies.json`, `project_knowledge.json`, `project_dependency_summary.json`, and `dependency_graph.md`.
