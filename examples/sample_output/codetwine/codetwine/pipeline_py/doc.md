# Design Document: codetwine/pipeline.py

# Overview & Purpose

## 1. Module Summary

Orchestrates the full project analysis pipeline by coordinating dependency graph construction, per-file dependency extraction, LLM-based document generation, and artifact serialization into a single end-to-end workflow.

## 2. When to Use This Module

- **Running a full project analysis**: Call `process_all_files(project_dir, output_dir, llm_client)` to analyze an entire project directory and produce all output artifacts (per-file `file_dependencies.json`, `project_knowledge.json`, `project_dependency_summary.json`, and `dependency_graph.md`).
- **Integrating with a CLI entry point**: `main.py` imports and invokes `process_all_files` as the sole top-level operation after resolving directories and constructing an `LLMClient`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int` | `None` | Runs the complete analysis pipeline: builds the dependency graph, extracts per-file dependencies, optionally generates LLM design documents, and writes all consolidated output artifacts. |

## 4. Design Decisions

- **Incremental processing via change detection**: `process_all_files` always re-runs per-file dependency extraction for all files (Step 2) to maintain consistency, but passes a `changed_files` set to `generate_all_docs` so that LLM document generation is skipped for files whose source and transitive callees are unchanged. Files that lack a `file_dependencies.json` in the output are also treated as changed to recover from partial prior runs.
- **Path format duality**: The pipeline maintains two distinct path formats internally. `build_project_dependencies` returns paths in `"project_name/copy_path"` format; `_convert_dep_list_to_internal_paths` strips the project name prefix and reverses the copy-path encoding back to project-relative paths for internal use. Paths are converted back to output format immediately before being written to `file_dependencies.json` via `to_output_path`.
- **Shared `symbol_deps` computation**: `build_symbol_level_deps` is called once and its result is shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json` to avoid redundant I/O across the three artifact-writing steps.
- **Parse cache invalidation**: `parse_cache.clear()` is called at the end of the pipeline to release memory held by the tree-sitter parse cache after all processing is complete.

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
- `project_dep_list_raw`: List of dicts with `"file"`, `"callers"`, `"callees"` keys, where paths are in `"project_name/copy_path"` format (as produced by `build_project_dependencies`).
- `project_name`: The bare project directory name (e.g., `"my-project"`).
- Returns: A list of dicts with the same shape, but paths converted to project-relative format (e.g., `"src/foo.py"`).

**Responsibility:** Translates the `"project_name/copy_path"` path format used in `project_dependencies.json` into plain project-relative paths used throughout the internal pipeline.

**When to use:** Called immediately after `build_project_dependencies` to normalize path formats before any internal processing begins.

**Design decisions:**
- Uses a nested `to_internal` helper closure to keep the stripping and `copy_path_to_rel` transformation co-located and reusable across all three path fields (`file`, `callers`, `callees`).
- Prefix stripping is guarded by `startswith` to tolerate entries that may already lack the project-name prefix.

**Constraints & edge cases:**
- If a path does not start with the expected `project_name/` prefix, it is passed through to `copy_path_to_rel` without stripping.
- The shape of each output dict is fixed to exactly `"file"`, `"callers"`, `"callees"`; any other fields in input dicts are silently dropped.

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
- `all_file_list`: List of relative paths (from the project root) of all files to consider.
- `project_dir`: Absolute path to the project root directory.
- `base_output_dir`: Absolute path to the root of the output directory.
- Returns: A `set[str]` of relative paths for files that are detected as changed.

**Responsibility:** Identifies which source files need reprocessing by comparing source and output-copy hashes and checking for incomplete prior outputs.

**When to use:** Called once per pipeline run after the file list is established, before dependency extraction, to determine the impact set for document regeneration.

**Design decisions:**
- A file is marked changed under two distinct conditions: its hash differs from the output copy, **or** its `file_dependencies.json` is absent in the output directory. The second condition recovers from a previous run that failed partway through processing.
- Uses `is_file_unchanged` (which returns `False` if the copy does not exist) as the primary hash check, then independently checks for `file_dependencies.json` existence.

**Constraints & edge cases:**
- Files not present in `all_file_list` are never evaluated.
- If the output directory for a file does not exist at all, both conditions will evaluate to "changed" since neither the copied file nor `file_dependencies.json` will be found.

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
- `files_to_process`: List of relative paths of files to analyze and write outputs for.
- `project_dir`: Absolute path to the project root.
- `base_output_dir`: Absolute path to the output root.
- `project_dep_list`: Project-wide dependency list in internal path format (output of `_convert_dep_list_to_internal_paths`).

**Responsibility:** Drives per-file dependency extraction, converts result paths to output format, writes `file_dependencies.json`, and copies the source file into the output directory.

**When to use:** Called once per pipeline run to populate (or refresh) the per-file output directories with dependency data.

**Design decisions:**
- Path fields in the `get_file_dependencies` result (`"file"`, `callee_usages[*]["from"]`, `caller_usages[*]["file"]`) are converted to `"project_name/copy_path"` format via `to_output_path` before being serialized, aligning the stored JSON with the consolidated output's path convention.
- Per-file errors are caught and logged individually so that a single failure does not abort processing of remaining files.
- Uses `shutil.copy2` to preserve file metadata on the output copy, which is necessary for accurate hash-based change detection in subsequent runs.

**Constraints & edge cases:**
- No parallelism; files are processed sequentially.
- If `get_file_dependencies` raises, the output directory may exist without a `file_dependencies.json`, which `_detect_changed_files` will treat as changed in the next run.
- The function processes exactly the files in `files_to_process`; callers are responsible for filtering.

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
- `output_dir`: Absolute path to the parent directory where output is written; actual output goes into `output_dir/<project_name>/`.
- `llm_client`: An instantiated `LLMClient` for LLM-based document generation, or `None` to skip that stage.
- `max_workers`: Maximum number of files processed concurrently during document generation.

**Responsibility:** Top-level async orchestrator that drives the entire analysis pipeline from dependency graph construction through consolidated JSON output.

**When to use:** Called once per invocation from `main.py` (via `asyncio.run`) to analyze a project and produce all output artifacts.

**Design decisions:**

| Stage | Scope | Notes |
|---|---|---|
| 1 — Dependency graph | All files | Always rebuilt; empty files excluded before any further processing |
| 1.5 — Change detection | All files | Used only to scope doc regeneration in Stage 3 |
| 2 — File dependency extraction | All files | Always processes all files regardless of change status |
| 3 — Doc generation | Changed impact set | Skipped entirely if `ENABLE_LLM_DOC=False` |
| 3.5 — Summary + dep graph JSON | All files | `symbol_deps` built once and reused across Steps 3.5, 4, and 5 |
| 4 — Mermaid graph | All files | Uses shared `symbol_deps` |
| 5 — Consolidated JSON | All files | Uses shared `symbol_deps` and `summary_map` |

- `symbol_deps` is computed once from `build_symbol_level_deps` and passed into all three subsequent output functions to avoid redundant disk reads.
- Empty files (files that contain only whitespace or are unreadable) are excluded from `project_dep_list` and `all_file_list` before any processing begins.
- `parse_cache.clear()` is called at the end to release the tree-sitter parse cache accumulated during the run.
- Document generation is delegated to `generate_all_docs`, which internally handles topological ordering and parallelism up to `max_workers`.

**Constraints & edge cases:**
- The function is `async`; it must be awaited or run via `asyncio.run`.
- `llm_client=None` is valid when `ENABLE_LLM_DOC=False`; passing `None` with `ENABLE_LLM_DOC=True` would propagate `None` into `generate_all_docs`.
- `changed_files` is computed before Stage 2 (full re-extraction) but is only used to gate Stage 3 doc regeneration; Stage 2 always processes all files.
- `project_name` is derived from the trailing component of `project_dir` using `os.path.basename`, so the caller must ensure the path does not end with a separator.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` : imports `parse_cache` to call `parse_cache.clear()` after analysis completes, freeing memory held by the AST parse result cache.

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` : imports `build_project_dependencies` to construct the project-wide inter-file dependency graph (callers/callees per file) as the first step of the pipeline.

- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : imports `get_file_dependencies` to perform per-file dependency analysis (definitions, callee usages, caller usages) for each file in the project.

- `codetwine/pipeline.py` → `codetwine/output.py` : imports `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, and `build_summary_map` to convert internal paths to output format, build symbol-level dependency maps, collect per-file summaries, and write the consolidated JSON, dependency summary JSON, and Mermaid diagram artifacts.

- `codetwine/pipeline.py` → `codetwine/doc_creator.py` : imports `generate_all_docs` to drive LLM-based design document generation for all files in topological dependency order, conditioned on the detected changed-file set.

- `codetwine/pipeline.py` → `codetwine/llm/client.py` : imports `LLMClient` as the type annotation for the LLM client parameter passed into `process_all_files` and forwarded to `generate_all_docs`.

- `codetwine/pipeline.py` → `codetwine/utils/file_utils.py` : imports `copy_path_to_rel` to strip the project-name prefix and restore original relative paths from `project_dependencies.json` copy-path format; imports `is_file_unchanged` to compare source and output copy hashes during change detection; imports `resolve_file_output_dir` to compute the per-file output directory path from a relative file path.

- `codetwine/pipeline.py` → `codetwine/config/settings.py` : imports `MAX_WORKERS` as the default concurrency limit for document generation and `ENABLE_LLM_DOC` as the feature flag controlling whether LLM-based design document generation runs.

## Dependents (modules that import this file)

- `main.py` → `codetwine/pipeline.py` : imports `process_all_files` as the top-level entry point; `main.py` resolves the project and output directories, conditionally constructs an `LLMClient`, and drives the entire analysis pipeline by calling `asyncio.run(process_all_files(project_dir, output_dir, llm_client))`.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/output.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/doc_creator.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/llm/client.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/utils/file_utils.py` : unidirectional
- `codetwine/pipeline.py` → `codetwine/config/settings.py` : unidirectional
- `main.py` → `codetwine/pipeline.py` : unidirectional

No dependency cycles exist; `pipeline.py` sits at the orchestration layer, consuming all subordinate modules and being consumed only by the top-level entry point `main.py`.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `project_dir` argument | `str` (absolute path) | Root directory of the project to analyze |
| `output_dir` argument | `str` (absolute path) | Root directory for all output artifacts |
| `llm_client` argument | `LLMClient \| None` | LLM client instance for doc generation; `None` when `ENABLE_LLM_DOC=False` |
| `max_workers` argument | `int` | Concurrency limit for document generation |
| Source files on disk | UTF-8 text | Read by `build_project_dependencies` and `get_file_dependencies` via AST parsers |
| `ENABLE_LLM_DOC` config | `bool` | Controls whether Step 3 (LLM doc generation) executes |
| `MAX_WORKERS` config | `int` | Default value for `max_workers` |
| Existing output directory contents | `file_dependencies.json`, copied source files, `doc.json` | Read during change detection and document reuse |

---

## 2. Transformation Overview

### Stage 1 — Build project-wide dependency graph
`build_project_dependencies(project_dir)` walks the source tree, resolves inter-file imports, and returns `project_dep_list_raw`: a list of dicts in `"project_name/copy_path"` format. This is immediately converted by `_convert_dep_list_to_internal_paths` into `project_dep_list`, where all paths are project-relative strings (e.g. `"src/foo.py"`). The list of all file relative paths, `all_file_list`, is extracted from this structure.

Empty files are detected by reading each source file; their entries are removed from both `project_dep_list` and `all_file_list` before further processing.

### Stage 1.5 — Change detection
`_detect_changed_files` compares each source file's SHA-256 hash against its previously copied counterpart in the output directory. A file is marked changed if the hashes differ or if no `file_dependencies.json` exists in its output directory. The result is `changed_files: set[str]` of relative paths.

### Stage 2 — Per-file dependency extraction
`_process_file_dependencies` iterates `all_file_list` sequentially. For each file:
- `get_file_dependencies` is called with the absolute source path, `project_dir`, and `project_dep_list`, producing a raw result dict with project-relative paths.
- The `"file"`, `"callee_usages[].from"`, and `"caller_usages[].file"` fields are converted to `"project_name/copy_path"` format via `to_output_path`.
- The transformed dict is serialized as `file_dependencies.json` in the file's output directory.
- The original source file is copied into the same output directory (enabling future hash comparison).

### Stage 3 — LLM design document generation (conditional)
If `ENABLE_LLM_DOC` is `True`, `generate_all_docs` is called with `base_output_dir`, `project_dep_list`, `llm_client`, `max_workers`, and `changed_files`. It processes files in topological dependency order; only files in `changed_files` or whose callees changed are regenerated. Outputs are `doc.json` and `doc.md` per file.

### Stage 3.5 — Symbol-level dependency graph and summary construction
`build_symbol_level_deps` reads every file's `file_dependencies.json` to construct `symbol_deps`: a map from relative path to actual caller/callee sets (based on symbol usage, not raw imports). `build_summary_map` reads each file's `doc.json` to collect LLM-generated summaries into `summary_map`. Both structures are computed once and reused across the three remaining output steps.

`save_dependency_summary` combines `symbol_deps` and `summary_map` to write `project_dependency_summary.json`.

### Stage 4 — Mermaid diagram generation
`save_dependency_graph_as_mermaid` consumes `symbol_deps` and writes `dependency_graph.md` containing a Mermaid `graph LR` flowchart of all file-level dependencies.

### Stage 5 — Consolidated JSON generation
`save_consolidated_json` merges each file's `file_dependencies.json`, `doc.json`, `symbol_deps`, and `summary_map` into a single `project_knowledge.json`.

Finally, `parse_cache.clear()` releases the in-memory tree-sitter AST cache.

---

## 3. Outputs

| Artifact | Format | Location |
|----------|--------|----------|
| `file_dependencies.json` | JSON | `{base_output_dir}/{copy_path_parent}/file_dependencies.json` per file |
| Source file copy | Original file bytes | `{base_output_dir}/{copy_path_parent}/{filename}` per file |
| `doc.json` / `doc.md` | JSON / Markdown | Same per-file output directory (written by `generate_all_docs`) |
| `project_dependency_summary.json` | JSON | `{base_output_dir}/project_dependency_summary.json` |
| `dependency_graph.md` | Markdown (Mermaid) | `{base_output_dir}/dependency_graph.md` |
| `project_knowledge.json` | JSON | `{base_output_dir}/project_knowledge.json` |

---

## 4. Key Data Structures

### `project_dep_list_raw` — raw output of `build_project_dependencies`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | File path in `"project_name/copy_path"` format |
| `callers` | `list[str]` | Paths (same format) of files that import this file |
| `callees` | `list[str]` | Paths (same format) of files this file imports |

### `project_dep_list` — internal pipeline format (after `_convert_dep_list_to_internal_paths`)
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | Project-relative path (e.g. `"src/foo.py"`) |
| `callers` | `list[str]` | Project-relative paths of files that import this file |
| `callees` | `list[str]` | Project-relative paths of files this file imports |

### `dep_result` — output of `get_file_dependencies`, written as `file_dependencies.json`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | Path in `"project_name/copy_path"` format after conversion |
| `definitions` | `list[dict]` | Extracted symbol definitions (name, type, line range, source) |
| `callee_usages` | `list[dict]` | Usages of symbols from dependency files; `from` field converted to output-path format |
| `caller_usages` | `list[dict]` | Locations in other files that use symbols from this file; `file` field converted to output-path format |

### `symbol_deps` — output of `build_symbol_level_deps`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `{file_rel}` (key) | `str` | Project-relative path of the file |
| `callers` | `set[str]` | Relative paths of files that actually use symbols from this file |
| `callees` | `set[str]` | Relative paths of files whose symbols this file actually uses |

### `summary_map` — output of `build_summary_map`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `{file_rel}` (key) | `str` | Project-relative path of the file |
| value | `str \| None` | LLM-generated summary text, or `None` if no `doc.json` exists |

### `changed_files` — output of `_detect_changed_files`
| Type | Purpose |
|------|---------|
| `set[str]` | Project-relative paths of files whose source hash differs from their output copy, or whose `file_dependencies.json` is absent |

# Error Handling

## 1. Overall Strategy

`pipeline.py` follows a **logging-and-continue** strategy at the per-file processing level, combined with **silent skip** for non-critical I/O operations. The pipeline as a whole does not fail fast; instead, individual file failures are caught, logged, and processing continues with the remaining files. Higher-level orchestration errors (e.g., missing directories, unreadable files) propagate to callers without explicit handling within this file.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `Exception` during per-file dependency extraction | Any error raised inside `get_file_dependencies`, JSON serialization, `shutil.copy2`, or `os.makedirs` for a single file | Caught by bare `except Exception`, logged at ERROR level with file path and error message | Yes — file is skipped, loop continues | That file's `file_dependencies.json` and output copy are not produced |
| `OSError` / `UnicodeDecodeError` when reading a file for empty-check | File is unreadable or contains non-UTF-8 content during the empty-file detection pass | Silently caught with `pass`; file is treated as non-empty and remains in the processing list | Yes — file is retained | File proceeds to full analysis even if it could not be read for the size check |
| Missing `file_dependencies.json` in output directory | A previous run failed mid-way, leaving no deps JSON for a file | Detected in `_detect_changed_files`; file is added to `changed_files` set | Yes — file is reprocessed in the current run | File is marked as changed and re-analyzed |
| Missing output copy of source file | Copy does not exist at the expected output path | `is_file_unchanged` returns `False`; file is added to `changed_files` set | Yes — file is reprocessed | File is re-analyzed and re-copied |
| LLM document generation errors | Failures within `generate_all_docs` (delegated to `doc_creator.py`) | Handled inside the called function; `pipeline.py` does not add additional handling | Yes (per doc_creator policy) | Affected file's design document is skipped or reused |

---

## 3. Design Notes

The per-file `try/except` in `_process_file_dependencies` is intentionally broad, using `Exception` as the catch type. This reflects a deliberate trade-off: a single file's parse failure or I/O error must not abort analysis of the entire project. Failures are surfaced only through log output (`logger.error`), with no re-raise or accumulation into a final error report.

The empty-file detection silently ignores read failures, meaning unreadable files are conservatively included in the processing list rather than excluded. This avoids incorrectly dropping files that might be valid but temporarily inaccessible.

The change detection mechanism (`_detect_changed_files`) doubles as an implicit recovery mechanism: the absence of `file_dependencies.json` is treated as evidence of an incomplete prior run, ensuring idempotency across interrupted executions without requiring explicit state tracking.

# Summary

**pipeline.py** orchestrates the full project analysis pipeline end-to-end.

Public API: `async process_all_files(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int) -> None`

Key structures consumed/produced: `project_dep_list` (list[dict] with `file`, `callers`, `callees` as project-relative paths), `symbol_deps` (dict mapping file path → caller/callee sets), `summary_map` (dict mapping file path → str|None), `changed_files` (set[str] of modified relative paths).
