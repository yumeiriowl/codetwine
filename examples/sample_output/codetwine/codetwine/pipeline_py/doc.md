# Design Document: codetwine/pipeline.py

# Overview & Purpose

## 1. Module Summary

Orchestrates the full project analysis pipeline: building a dependency graph, extracting per-file dependency information, optionally generating LLM-based design documents, and producing all output artifacts (per-file JSONs, consolidated JSON, dependency summary, and Mermaid diagram) for a given project directory.

## 2. When to Use This Module

- **Triggering a full project analysis**: Call `process_all_files(project_dir, output_dir, llm_client)` to run the complete end-to-end pipeline from a project root directory and receive all analysis artifacts written to `output_dir`.
- **Integrating with a CLI or entry point**: `main.py` calls `process_all_files` directly via `asyncio.run`; any other caller that needs to analyze a project programmatically should use this same function.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int` | `None` | Runs the full analysis pipeline: builds the dependency graph, detects changed files, extracts per-file dependencies, optionally generates LLM design documents, and writes all output artifacts (per-file JSON, consolidated JSON, dependency summary, Mermaid diagram). |

## 4. Design Decisions

- **Incremental processing via change detection**: `_detect_changed_files` compares SHA256 hashes of source files against their output copies and checks for the presence of `file_dependencies.json`. This allows `generate_all_docs` to skip unchanged files, but Step 2 (dependency extraction) always processes all files to maintain consistency across the full project graph.
- **Path format duality**: The pipeline maintains two distinct path formats internally. `build_project_dependencies` returns paths in `project_name/copy_path` format; `_convert_dep_list_to_internal_paths` strips the project name prefix and reverses the copy-path transformation so that the rest of the pipeline operates on plain project-relative paths. Output artifacts then re-apply `to_output_path` to restore the `project_name/copy_path` format.
- **Shared pre-computation of `symbol_deps`**: `build_symbol_level_deps` is called once and its result is passed to all three downstream output functions (`save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json`) to avoid redundant file reads.
- **Parse cache eviction**: `parse_cache.clear()` is called at the end of `process_all_files` to release tree-sitter AST memory after the pipeline completes.

# Definition Design Specifications

---

## Module-Level

### `logger`
- **Type:** `logging.Logger`
- **Responsibility:** Module-scoped logger for structured diagnostic output throughout the pipeline.

---

## `_convert_dep_list_to_internal_paths`

**Signature:**
```python
def _convert_dep_list_to_internal_paths(
    project_dep_list_raw: list[dict],
    project_name: str,
) -> list[dict]
```
- `project_dep_list_raw`: List of dicts with `"file"`, `"callers"`, `"callees"` keys, where all paths are in `"project_name/copy_path"` format as returned by `build_project_dependencies`.
- `project_name`: The bare project directory name (e.g., `"my-project"`).
- **Returns:** A new list of dicts with the same shape, but all paths converted to project-root-relative format (e.g., `"src/foo.py"`).

**Responsibility:** Bridges the external path format used in `project_dependencies.json` (prefixed with project name and using copy-path directory structure) to the internal relative-path format expected by the rest of the pipeline.

**When to use:** Called once immediately after `build_project_dependencies` returns, before the result is passed to any downstream pipeline step.

**Design decisions:**
- Defines an inner function `to_internal(path: str) -> str` that chains two transformations: stripping the `"project_name/"` prefix, then calling `copy_path_to_rel` to undo the copy-path directory structure.
- Processes `"callers"` and `"callees"` lists in addition to `"file"`, ensuring all path fields are uniformly converted.

**Constraints & edge cases:**
- If a path does not start with `project_name/`, the prefix stripping is skipped and `copy_path_to_rel` is applied directly.
- Only the three keys `"file"`, `"callers"`, and `"callees"` are preserved; any additional keys in input dicts are dropped.

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
- `all_file_list`: List of project-root-relative file paths to check.
- `project_dir`: Absolute path to the project root directory.
- `base_output_dir`: Absolute path to the output root directory.
- **Returns:** A `set` of relative file paths (strings) for which a change was detected.

**Responsibility:** Identifies which source files need reprocessing by comparing their current content against previously saved output copies, enabling incremental pipeline runs.

**When to use:** Called once per pipeline run, after the file list is assembled and before dependency extraction, to determine the changed-file set used by the document generation stage.

**Design decisions:**
- A file is considered changed under either of two conditions: its source hash differs from the output copy (via `is_file_unchanged`), or its `file_dependencies.json` is absent from the output directory. The second condition recovers from partially failed prior runs.
- Uses `resolve_file_output_dir` to locate the output directory for each file, keeping path resolution logic centralized.

**Constraints & edge cases:**
- Files that could not be opened (e.g., binary files excluded upstream) are not present in `all_file_list` and are never checked.
- A missing output copy is treated as changed (delegated to `is_file_unchanged` returning `False`).

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
- `files_to_process`: List of project-root-relative paths of files to analyze.
- `project_dir`: Absolute path to the project root.
- `base_output_dir`: Absolute path to the output root directory.
- `project_dep_list`: Project-wide dependency list in internal path format (output of `_convert_dep_list_to_internal_paths`).

**Responsibility:** Drives per-file dependency extraction and persists results—both the analysis JSON and a copy of the source file—to the output directory structure.

**When to use:** Called once per pipeline run (Step 2), processing all files regardless of change status to maintain output consistency.

**Design decisions:**
- Path fields within the `dep_result` dict (specifically `"file"`, `"from"` in `callee_usages`, and `"file"` in `caller_usages`) are converted to `"project_name/copy_path"` output format via `to_output_path` before writing to disk.
- Individual file failures are caught, logged as errors, and skipped rather than aborting the entire batch.
- Each file's output directory is created with `exist_ok=True` to be idempotent.

**Constraints & edge cases:**
- `get_file_dependencies` is called with the absolute file path but the internal project-relative dep list; callers must ensure `project_dep_list` uses internal path format.
- Any exception during a single file's processing is caught at the per-file level; the function always completes for all files.

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
- `project_dir`: Absolute path to the root of the project to analyze.
- `output_dir`: Absolute path to the root output directory. Results are written under `output_dir/<project_name>/`.
- `llm_client`: An initialized `LLMClient` instance for LLM-based doc generation, or `None` to skip it.
- `max_workers`: Maximum number of concurrent async tasks used during document generation.
- **Returns:** `None`. All results are written to disk as side effects.

**Responsibility:** Orchestrates the complete analysis pipeline for a project—dependency graph construction, per-file dependency extraction, optional LLM document generation, and all consolidated output files.

**When to use:** Called once from the application entry point (`main.py`) via `asyncio.run`, with the project directory, output directory, and LLM client resolved from CLI arguments and settings.

**Async semantics:** This is an `async` function. It awaits `generate_all_docs`, which internally schedules per-file document generation tasks in parallel batches using `asyncio.create_task` and `asyncio.gather`. All other pipeline steps (Steps 1, 2, 3.5, 4, 5) execute synchronously within the coroutine.

**Processing stages (in order):**

| Stage | Description |
|---|---|
| 1 | Build project-wide dependency graph via `build_project_dependencies`; convert paths to internal format |
| 1.5 | Exclude empty files; detect changed files via `_detect_changed_files` |
| 2 | Extract per-file dependency info and write `file_dependencies.json` + source copy via `_process_file_dependencies` |
| 3 | (If `ENABLE_LLM_DOC`) Generate design documents in topological order via `generate_all_docs` |
| 3.5 | Build `symbol_deps` and `summary_map`; write `project_dependency_summary.json` |
| 4 | Write Mermaid dependency graph (`dependency_graph.md`) |
| 5 | Write consolidated `project_knowledge.json` |
| post | Clear `parse_cache` to free memory |

**Design decisions:**
- `symbol_deps` (from `build_symbol_level_deps`) is computed once and shared across Steps 3.5, 4, and 5 to avoid redundant disk reads.
- Empty files (files whose content is blank after stripping) are detected and excluded from `all_file_list` and `project_dep_list` before any processing begins, with exclusions logged.
- `changed_files` is passed to `generate_all_docs` for selective regeneration, but Step 2 always processes all files to maintain output consistency.
- `parse_cache.clear()` is called unconditionally at the end to release tree-sitter parse results from memory regardless of whether errors occurred.
- LLM document generation is gated on the `ENABLE_LLM_DOC` configuration flag; when disabled, the stage is skipped entirely with a log message.

**Constraints & edge cases:**
- `project_name` is derived solely from `os.path.basename(project_dir)`; the caller must ensure `project_dir` does not end with a path separator.
- `llm_client` may be `None`; when `ENABLE_LLM_DOC` is `True` but `llm_client` is `None`, `generate_all_docs` is still called—behavior in that case is determined by `generate_all_docs` internals.
- All file processing in Step 2 is synchronous and sequential despite the surrounding async context.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` : uses `build_project_dependencies` to construct the project-wide import dependency graph as the first step of the analysis pipeline
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : uses `get_file_dependencies` to perform per-file dependency extraction (definitions, callee usages, caller usages)
- `codetwine/pipeline.py` → `codetwine/output.py` : uses `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, and `build_summary_map` to produce all project-level output artifacts
- `codetwine/pipeline.py` → `codetwine/doc_creator.py` : uses `generate_all_docs` to drive LLM-based design document generation in topological order
- `codetwine/pipeline.py` → `codetwine/llm/client.py` : receives `LLMClient` as a parameter type annotation and passes the client instance into the document generation step
- `codetwine/pipeline.py` → `codetwine/utils/file_utils.py` : uses `copy_path_to_rel` to convert output-format paths back to project-relative paths, `is_file_unchanged` to detect file changes via hash comparison, and `resolve_file_output_dir` to determine per-file output directory paths
- `codetwine/pipeline.py` → `codetwine/config/settings.py` : reads `MAX_WORKERS` (concurrency limit) and `ENABLE_LLM_DOC` (feature flag controlling whether design document generation runs)
- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` : uses `parse_cache.clear()` at the end of processing to release cached AST parse results from memory

### Dependents (modules that import this file)

- `main.py` → `codetwine/pipeline.py` : calls `process_all_files` as the top-level entry point to execute the full project analysis pipeline, passing the resolved project directory, output directory, and an `LLMClient` instance (or `None` when `ENABLE_LLM_DOC` is false)

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/output.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/doc_creator.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/llm/client.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/utils/file_utils.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/config/settings.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` : unidirectional
- `main.py` → `codetwine/pipeline.py` : unidirectional

# Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `project_dir` argument | `str` (absolute path) | Root directory of the project to analyze |
| `output_dir` argument | `str` (absolute path) | Root directory for writing all analysis outputs |
| `llm_client` argument | `LLMClient \| None` | LLM client instance for design document generation |
| `max_workers` argument | `int` | Maximum parallel workers for document generation |
| Source files on disk | Raw file bytes | Read by `build_project_dependencies` and `get_file_dependencies` |
| Existing output files | JSON, copied source files | Read during change detection (`file_dependencies.json`, copied source file hash comparison) |
| `MAX_WORKERS` config | `int` | Default value for `max_workers` from settings |
| `ENABLE_LLM_DOC` config | `bool` | Controls whether design document generation is executed |

---

## 2. Transformation Overview

### Stage 1 — Project-wide dependency graph construction
`build_project_dependencies(project_dir)` walks the project directory and returns `project_dep_list_raw`: a list of dicts with paths in `"project_name/copy_path"` format. `_convert_dep_list_to_internal_paths` strips the project name prefix and restores original relative paths via `copy_path_to_rel`, producing `project_dep_list` in internal format. `all_file_list` is extracted as the list of relative file paths.

### Stage 1.5 — Empty file exclusion
Each file in `all_file_list` is opened and checked for non-whitespace content. Empty files are removed from both `project_dep_list` and `all_file_list`.

### Stage 1.75 — Change detection
`_detect_changed_files` iterates `all_file_list` and compares each source file's SHA256 hash against its output copy via `is_file_unchanged`. Files are also marked changed if no `file_dependencies.json` exists in their output directory. The result is `changed_files: set[str]` of relative paths.

### Stage 2 — Per-file dependency extraction
`_process_file_dependencies` iterates all files (not just changed ones). For each file it calls `get_file_dependencies`, which returns a dict with `file`, `definitions`, `callee_usages`, and `caller_usages`. The `file`, `callee_usages[].from`, and `caller_usages[].file` path fields are converted from internal relative format to `"project_name/copy_path"` format via `to_output_path`. The result is written as `file_dependencies.json` into the file's output directory, and the original source file is copied alongside it.

### Stage 3 — Design document generation (conditional)
If `ENABLE_LLM_DOC` is `True`, `generate_all_docs` processes files in topological dependency order. It uses `changed_files` to skip regeneration for unchanged files and their unaffected callers, reusing existing `doc.json` where valid. LLM-generated output is written as `doc.json` and `doc.md` per file.

### Stage 3.5 — Symbol-level dependency and summary aggregation
`build_symbol_level_deps` reads all `file_dependencies.json` files and derives a `symbol_deps` map (`file_rel → {callers: set, callees: set}`) based on actual symbol usage (from `callee_usages.from` and `caller_usages.file` fields). `build_summary_map` reads each file's `doc.json` and extracts the `summary` field, producing `summary_map` (`file_rel → str | None`). These two structures are computed once and shared by the three subsequent output functions.

### Stage 4 — Mermaid dependency diagram
`save_dependency_graph_as_mermaid` consumes `symbol_deps` and writes a Mermaid `graph LR` flowchart to `dependency_graph.md`.

### Stage 5 — Consolidated outputs
`save_dependency_summary` writes `project_dependency_summary.json` combining `symbol_deps` and `summary_map`. `save_consolidated_json` merges each file's `file_dependencies.json`, `doc.json`, and the dependency graph into a single `project_knowledge.json`.

### Cleanup
`parse_cache.clear()` releases the in-memory tree-sitter AST cache.

---

## 3. Outputs

| Output | Format | Location |
|---|---|---|
| `file_dependencies.json` per file | JSON | `{base_output_dir}/{copy_path_dir}/file_dependencies.json` |
| Copied source file per file | Original source | `{base_output_dir}/{copy_path_dir}/{filename}` |
| `doc.json` per file (if LLM enabled) | JSON | `{base_output_dir}/{copy_path_dir}/doc.json` |
| `doc.md` per file (if LLM enabled) | Markdown | `{base_output_dir}/{copy_path_dir}/doc.md` |
| `project_dependency_summary.json` | JSON | `{base_output_dir}/project_dependency_summary.json` |
| `dependency_graph.md` | Markdown (Mermaid) | `{base_output_dir}/dependency_graph.md` |
| `project_knowledge.json` | JSON | `{base_output_dir}/project_knowledge.json` |

---

## 4. Key Data Structures

### `project_dep_list_raw` — raw output of `build_project_dependencies`

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | File path in `"project_name/copy_path"` format |
| `callers` | `list[str]` | Paths of files that import this file, same format |
| `callees` | `list[str]` | Paths of files this file imports, same format |

### `project_dep_list` — internal format after `_convert_dep_list_to_internal_paths`

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path from project root (e.g. `"src/foo.py"`) |
| `callers` | `list[str]` | Relative paths of caller files |
| `callees` | `list[str]` | Relative paths of callee files |

### `dep_result` — output of `get_file_dependencies`, written to `file_dependencies.json`

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Converted to `"project_name/copy_path"` format before write |
| `definitions` | `list[dict]` | Named definitions extracted from the file |
| `callee_usages` | `list[dict]` | Usage sites of symbols from dependencies; `from` field converted to output path |
| `caller_usages` | `list[dict]` | Usage sites of this file's symbols in other files; `file` field converted to output path |

### `symbol_deps` — output of `build_symbol_level_deps`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File relative path (internal format) |
| `callers` | `set[str]` | Relative paths of files that actually use symbols from this file |
| `callees` | `set[str]` | Relative paths of files whose symbols this file actually uses |

### `summary_map` — output of `build_summary_map`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | File relative path (internal format) |
| value | `str \| None` | LLM-generated summary text, or `None` if `doc.json` absent or summary missing |

### `changed_files` — output of `_detect_changed_files`

| Type | Purpose |
|---|---|
| `set[str]` | Relative paths of files whose source hash differs from their output copy, or whose `file_dependencies.json` is absent; used to scope LLM regeneration |

# Error Handling

## 1. Overall Strategy

`pipeline.py` applies a **logging-and-continue** strategy for per-file processing failures, combined with **silent skipping** for non-critical infrastructure operations. The pipeline does not fail-fast at the orchestration level: a failure in one file's dependency extraction is logged and skipped, allowing the remaining files to be processed. Higher-level steps (dependency graph construction, document generation, consolidated JSON output) are not individually guarded and propagate exceptions upward to the caller.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Per-file dependency extraction failure | Any exception raised during `get_file_dependencies`, JSON serialization, or file copy within `_process_file_dependencies` | Logged at `ERROR` level via `logger.error`; iteration continues to the next file | Yes | The affected file's `file_dependencies.json` and copied source file are not produced; downstream steps will see missing data for that file |
| Unreadable or non-UTF-8 source file | `OSError` or `UnicodeDecodeError` when opening a file to check for emptiness | Silently ignored via bare `except`; file is not added to `empty_files` | Yes | File is treated as non-empty and remains in the processing list |
| Missing `file_dependencies.json` in output | A previous run failed mid-way, leaving no `file_dependencies.json` for a file | Detected by `_detect_changed_files`; file is added to `changed_files` set, triggering reprocessing | Yes | File is unconditionally reprocessed in the current run |
| Source file hash mismatch | Source file content has changed since the last run | Detected by `is_file_unchanged` returning `False`; file is added to `changed_files` set | Yes | File is reprocessed; downstream document regeneration is triggered for the file and its callers |
| LLM document generation failure | Any exception during `generate_all_docs` (delegated entirely to `doc_creator.py`) | Handled within `generate_all_docs`; individual file failures are logged and skipped | Yes (per file) | Affected file's `doc.json` is absent; consolidated JSON and summary map will contain `null` summary for that file |
| LLM document generation disabled | `ENABLE_LLM_DOC` is `False` | Entire Step 3 is skipped with a print and log message; no exception raised | Yes (by design) | No `doc.json` files are produced; summary fields in all outputs will be `null` |
| Consolidated output failures (Steps 3.5–5) | Exceptions during `save_dependency_summary`, `save_dependency_graph_as_mermaid`, or `save_consolidated_json` | No explicit guard in `pipeline.py`; exceptions propagate to the caller | No | Pipeline terminates; partial output files may exist |

---

## 3. Design Notes

**Per-file isolation in extraction, not in aggregation.** The `try/except` guard is applied only within the per-file loop in `_process_file_dependencies`. This means individual file failures are contained without aborting the batch, but the aggregation and output steps (Steps 3.5–5) are treated as must-succeed operations with no local recovery.

**Incremental correctness via change detection.** The `_detect_changed_files` function treats any file lacking a `file_dependencies.json` as changed. This serves as an implicit recovery mechanism: if a previous run's per-file processing failed partway through, the incomplete state is detected and the affected files are requeued, rather than relying on explicit error state tracking.

**Dependency on delegate error policies.** Error handling for LLM calls (retry on rate limits, immediate fail on API errors) is fully delegated to `LLMClient` and `generate_all_docs`. The pipeline itself imposes no additional retry or fallback logic around these calls. Similarly, `parse_cache.clear()` is called unconditionally at the end of the pipeline with no guard, implying it is expected to never raise.

# Summary

**pipeline.py** orchestrates the full project analysis pipeline from source directory to all output artifacts. Public API: `process_all_files(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int) -> None`. Key data structures: `project_dep_list` (list[dict] with `file`, `callers`, `callees` as relative paths), `symbol_deps` (dict mapping file→`{callers: set, callees: set}`), `summary_map` (dict mapping file→`str | None`), `changed_files` (set[str]).
