# Design Document: codetwine/pipeline.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`pipeline.py` is the top-level orchestration module for the codetwine analysis pipeline. It exists as a separate file to centralize the multi-step workflow that transforms a raw source project into structured analysis artifacts, coordinating all other modules (parsing, dependency extraction, per-file analysis, LLM document generation, and output serialization) without implementing any of those concerns itself.

Its primary responsibility is to sequence the following steps for a given project directory:
1. Build the project-wide dependency graph.
2. Detect which files have changed since the last run (to enable incremental processing).
3. Extract and persist per-file dependency information.
4. Optionally generate LLM-based design documents in topological order.
5. Produce consolidated output artifacts: a dependency summary JSON, a Mermaid dependency graph, and a full consolidated knowledge JSON.

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int` | `None` (async) | Top-level entry point: orchestrates all pipeline steps for the given project directory and writes all output artifacts |

## Internal Helpers (module-private)

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_convert_dep_list_to_internal_paths` | `project_dep_list_raw: list[dict]`, `project_name: str` | `list[dict]` | Strips the `project_name/` prefix and reverses copy-path encoding so dependency entries use plain project-relative paths for internal pipeline use |
| `_detect_changed_files` | `all_file_list: list[str]`, `project_dir: str`, `base_output_dir: str` | `set[str]` | Compares source file hashes against output copies and flags files missing a `file_dependencies.json` as changed |
| `_process_file_dependencies` | `files_to_process: list[str]`, `project_dir: str`, `base_output_dir: str`, `project_dep_list: list[dict]` | `None` | Runs per-file dependency analysis, converts paths to output format, writes `file_dependencies.json`, and copies the source file to the output directory |

## Design Decisions

- **Two-format path convention**: The pipeline maintains a clear boundary between two path formats. Internally, all processing uses project-relative POSIX paths. Externally persisted artifacts use the `project_name/copy_path` format. `_convert_dep_list_to_internal_paths` enforces this boundary at the point where `build_project_dependencies` output enters the pipeline.

- **Incremental processing**: `_detect_changed_files` enables partial re-execution. Changed files are detected once (Step 1.5) and passed to `generate_all_docs`, which uses them to skip LLM calls for files whose content and callee dependencies are unchanged. Notably, Step 2 (dependency extraction) always processes all files unconditionally to maintain consistency.

- **`symbol_deps` computed once and shared**: `build_symbol_level_deps` is called a single time and its result is passed to all three output-writing functions (`save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json`), avoiding redundant file reads.

- **Empty file exclusion**: Files that are empty (whitespace-only or unreadable) are removed from both `project_dep_list` and `all_file_list` before any processing begins, preventing downstream errors in parsers and analyzers.

- **Parse cache cleanup**: `parse_cache.clear()` is called at the end of `process_all_files` to release the in-memory tree-sitter parse cache held by `ts_parser.py`, limiting memory growth after the pipeline completes.

## Definition Design Specifications

# Definition Design Specifications

---

## `_convert_dep_list_to_internal_paths`

**Signature:** `(project_dep_list_raw: list[dict], project_name: str) -> list[dict]`

**Responsibility:** Translates paths stored in `project_dependencies.json` (which use `"project_name/copy_path"` format) back to the project-relative paths that the internal pipeline uses for all subsequent processing.

**Design decisions:** The two-step transformation—first stripping the project name prefix, then calling `copy_path_to_rel` to undo the `{stem}_{ext}` directory insertion—mirrors the inverse of how paths are written by `build_project_dependencies`. The function produces a new list with only `"file"`, `"callers"`, and `"callees"` keys, discarding any other fields from the raw format; callers and callees default to an empty list via `dep.get("callers", [])` / `dep.get("callees", [])` to handle missing keys safely.

**Edge cases:** Paths that do not start with the project name prefix are passed through `copy_path_to_rel` unchanged (the prefix strip is guarded). The function does not validate that the resulting paths actually exist on disk.

---

## `_detect_changed_files`

**Signature:** `(all_file_list: list[str], project_dir: str, base_output_dir: str) -> set[str]`

**Responsibility:** Identifies which source files require reprocessing by comparing SHA-256 hashes between source files and their output copies, enabling incremental pipeline runs.

**Design decisions:** A file is also treated as changed when its `file_dependencies.json` is absent in the output directory, even if the copied file hash matches. This guards against partial failures where the file was copied but dependency extraction did not complete. Returning a `set` rather than a list makes membership tests O(1) in downstream impact-propagation logic.

**Edge cases:** If the output copy does not exist at all, `is_file_unchanged` returns `False` by contract, so the file is naturally included. Files that cannot be read by `is_file_unchanged` (e.g., permission errors) propagate exceptions from the utility function rather than being silently included or excluded.

---

## `_process_file_dependencies`

**Signature:** `(files_to_process: list[str], project_dir: str, base_output_dir: str, project_dep_list: list[dict]) -> None`

**Responsibility:** Drives per-file dependency extraction for a given file list, converting result paths to output format and persisting both `file_dependencies.json` and a copy of the source file to the output directory.

**Design decisions:** Path conversion to `"project_name/copy_path"` format is applied to `"file"`, `"callee_usages[].from"`, and `"caller_usages[].file"` fields immediately after extraction, so all downstream consumers reading `file_dependencies.json` always see output-format paths rather than relative paths. The source file is copied with `shutil.copy2` (preserving metadata) after the JSON is written, meaning a missing copy reliably signals an incomplete run and will re-trigger processing on the next invocation via `_detect_changed_files`. Per-file errors are caught and logged without aborting the loop so that one malformed file does not prevent others from being processed.

**Edge cases:** The function does not filter `files_to_process`; the caller is responsible for passing only the intended subset. Files for which `get_file_dependencies` raises an exception are skipped with an error log entry.

---

## `process_all_files`

**Signature:** `(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int = MAX_WORKERS) -> None` (async)

**Responsibility:** Top-level async orchestrator for the full analysis pipeline—from dependency graph construction through per-file extraction, optional LLM document generation, and all consolidated output generation—for an entire project directory.

**Design decisions:** The output root is `output_dir/project_name`, where `project_name` is derived from the basename of `project_dir`, ensuring that outputs for different projects placed in the same `output_dir` do not collide. Empty files (whitespace-only or unreadable) are excluded before any processing to avoid feeding meaningless content to the parser and LLM stages; this exclusion is applied consistently to both `project_dep_list` and `all_file_list`. Changed file detection runs before Step 2 rather than being used to skip Step 2 itself—Step 2 always processes all files for consistency—but its result is forwarded to `generate_all_docs` to enable incremental document regeneration in Step 3. `symbol_deps` is computed once and shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json` to avoid redundant JSON reads. `parse_cache.clear()` is called at the end to release memory held by the module-level tree-sitter parse cache.

**Edge cases:** When `ENABLE_LLM_DOC` is `False`, `generate_all_docs` is skipped entirely and `llm_client` may be `None`; downstream output functions handle absent `doc.json` files gracefully with `null` summaries. `max_workers` controls only the concurrency within `generate_all_docs`; dependency extraction in Step 2 is sequential. The function does not return any value; all results are persisted to disk.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **codetwine/extractors/dependency_graph.py** (`build_project_dependencies`): Used to perform the initial project-wide scan that discovers all supported source files and resolves inter-file import relationships, producing the raw caller/callee graph that drives the rest of the pipeline.

- **codetwine/file_analyzer.py** (`get_file_dependencies`): Used to perform per-file analysis, extracting symbol definitions, callee usages, and caller usages for each individual source file.

- **codetwine/output.py** (`save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, `build_summary_map`): Used to build shared symbol-level dependency structures and summaries from per-file artifacts, and to write the final project-level outputs — the lightweight dependency summary JSON, the Mermaid diagram, and the consolidated knowledge JSON.

- **codetwine/doc_creator.py** (`generate_all_docs`): Used to drive LLM-based design document generation in topological order, incorporating incremental regeneration logic based on detected file changes.

- **codetwine/llm/client.py** (`LLMClient`): Used as the type annotation for the LLM client parameter passed into the pipeline and forwarded to `generate_all_docs`.

- **codetwine/utils/file_utils.py** (`copy_path_to_rel`, `is_file_unchanged`, `resolve_file_output_dir`): Used for path transformations (converting copy-destination paths back to project-relative paths), detecting whether source files have changed by comparing SHA-256 hashes against output copies, and resolving the output directory for each file.

- **codetwine/parsers/ts_parser.py** (`parse_cache`): Used to clear the in-memory parse result cache at the end of pipeline execution to free memory.

- **codetwine/config/settings.py** (`MAX_WORKERS`, `ENABLE_LLM_DOC`): Used to control the maximum concurrency for document generation and to conditionally skip the LLM document generation stage entirely.

---

### Dependents (what uses this file)

- **main.py** (`process_all_files`): The application entry point uses `process_all_files` as the top-level pipeline invocation. It resolves the project and output directories, constructs an `LLMClient` if LLM document generation is enabled, and delegates all analysis and output work to this function via `asyncio.run`.

**Direction of dependency**: Unidirectional — `main.py` depends on `pipeline.py`; `pipeline.py` has no dependency on `main.py`.

## Data Flow

# Data Flow

## Overview

`pipeline.py` orchestrates the full analysis pipeline: from raw project source files to structured JSON, Mermaid diagrams, and optional LLM design documents. All data flows through `process_all_files`, the single public entry point.

---

## Input

| Source | Format | Description |
|--------|--------|-------------|
| `project_dir` (filesystem) | Source files on disk | Walked by `build_project_dependencies` |
| `output_dir` (argument) | String path | Base directory for all outputs |
| `llm_client` (argument) | `LLMClient \| None` | Optional LLM for doc generation |

---

## Main Transformation Flow

```
project_dir (source files)
        │
        ▼
build_project_dependencies()
        │  list[dict]  {"file": "project_name/copy_path", "callers": [...], "callees": [...]}
        ▼
_convert_dep_list_to_internal_paths()
        │  list[dict]  {"file": "rel_path", "callers": [...], "callees": [...]}  ← internal format
        ▼
[empty file exclusion filter]
        │  filtered project_dep_list + all_file_list
        ▼
_detect_changed_files()
        │  set[str]  relative paths of changed files
        ▼
_process_file_dependencies()          (reads source; writes per-file artifacts)
        │  file_dependencies.json + source copy  →  base_output_dir/.../
        ▼
generate_all_docs()  [if ENABLE_LLM_DOC]
        │  doc.json + doc.md  →  base_output_dir/.../
        ▼
build_symbol_level_deps()
        │  dict[str, dict[str, set[str]]]  symbol-level caller/callee sets per file
        ▼
build_summary_map()
        │  dict[str, str | None]  file → LLM summary text or None
        ▼
┌───────────────────────────────────────────────┐
│  save_dependency_summary()                    │  → project_dependency_summary.json
│  save_dependency_graph_as_mermaid()           │  → dependency_graph.md
│  save_consolidated_json()                     │  → project_knowledge.json
└───────────────────────────────────────────────┘
        ▼
parse_cache.clear()
```

---

## Path Format Conversions

Two path formats flow through the pipeline:

| Format | Example | Used where |
|--------|---------|------------|
| **Internal** (project-relative) | `src/foo.py` | Inside pipeline, `project_dep_list`, `all_file_list` |
| **Output** (project_name/copy_path) | `my-project/src/foo_py/foo.py` | `file_dependencies.json` fields, all output JSON files |

`_convert_dep_list_to_internal_paths` strips the `project_name/` prefix and calls `copy_path_to_rel` to convert output-format paths from `build_project_dependencies` into internal format. `to_output_path` performs the reverse when writing output artifacts.

---

## Key Data Structures

### `project_dep_list` (internal format)
```
list[{
  "file":    str,        # project-relative path (e.g. "src/foo.py")
  "callers": list[str],  # files that import this file
  "callees": list[str],  # files imported by this file
}]
```
Produced by `_convert_dep_list_to_internal_paths`; consumed by `_process_file_dependencies` and `generate_all_docs`.

### `changed_files`
```
set[str]   # project-relative paths of files whose source hash differs
           # from their output copy, or whose file_dependencies.json is missing
```
Produced by `_detect_changed_files`; passed to `generate_all_docs` to limit LLM regeneration to the impact range.

### `symbol_deps`
```
dict[str, {
  "callers": set[str],   # files that actually use symbols from this file
  "callees": set[str],   # files whose symbols this file actually uses
}]
```
Key is a project-relative path. Built once from `file_dependencies.json` artifacts by `build_symbol_level_deps` and shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`.

### `summary_map`
```
dict[str, str | None]   # project-relative path → LLM-generated summary or None
```
Read from each file's `doc.json`; used to enrich both `project_dependency_summary.json` and `project_knowledge.json`.

---

## Outputs

| File | Location | Contents |
|------|----------|----------|
| `file_dependencies.json` | `base_output_dir/<copy_path>/` | Per-file definitions, callee_usages, caller_usages (output-format paths) |
| Source file copy | `base_output_dir/<copy_path>/` | Unchanged source file; used for hash comparison on next run |
| `doc.json` / `doc.md` | `base_output_dir/<copy_path>/` | LLM-generated design document (only when `ENABLE_LLM_DOC=True`) |
| `project_dependency_summary.json` | `base_output_dir/` | Lightweight symbol-level deps + summaries for all files |
| `dependency_graph.md` | `base_output_dir/` | Mermaid flowchart of file-level dependencies |
| `project_knowledge.json` | `base_output_dir/` | Full consolidated artifact: all per-file deps + docs + dependency graph |

## Error Handling

# Error Handling

## Overall Strategy

`pipeline.py` adopts a **mixed strategy** that combines fail-fast behavior at the pipeline orchestration level with graceful degradation at the per-file processing level. The top-level `process_all_files` function does not catch exceptions from its major pipeline stages (dependency graph construction, document generation, consolidated output), allowing errors in those stages to propagate immediately and halt execution. In contrast, per-file dependency extraction inside `_process_file_dependencies` isolates individual file failures with a broad `except Exception` catch, logs the error, and continues processing the remaining files.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Exception during per-file dependency extraction | Caught by broad `except Exception`; error logged via `logger.error`; file is skipped | That file's `file_dependencies.json` and output copy are not written; pipeline continues for other files |
| `OSError` / `UnicodeDecodeError` when reading files for empty-file detection | Silently ignored (`pass`) | Affected file is not added to `empty_files`; it remains in the processing list |
| Missing output copy or mismatched hash in change detection | Treated as "changed" by `is_file_unchanged` returning `False`; file added to `changed_files` set | File is reprocessed in subsequent stages |
| Missing `file_dependencies.json` in output during change detection | Treated as "changed" via `os.path.exists` check | File is reprocessed to recover from incomplete prior runs |
| Errors within `build_project_dependencies`, `generate_all_docs`, output functions | Not caught in `pipeline.py`; propagate upward | Pipeline halts at the failing stage |
| LLM document generation skipped (`ENABLE_LLM_DOC=False` or `llm_client=None`) | Conditional branch bypasses the stage entirely; informational message printed and logged | Design documents are not generated; downstream stages proceed with `null` summaries |

## Design Considerations

The deliberate asymmetry between per-file isolation and pipeline-level fail-fast reflects the nature of each stage. Per-file dependency extraction is a parallelizable, file-scoped operation where one bad file should not abort the entire analysis; the output directory state after a partial failure is recoverable because the change-detection logic (`_detect_changed_files`) explicitly checks for incomplete outputs and re-queues affected files on the next run. By contrast, failures in structural pipeline stages—graph construction or consolidated output—indicate a systemic problem where partial results would be misleading, so propagation is the appropriate response.

Error logging uses `logger.error` for actionable failures and `logger.info`/`logger.warning` for informational state, but no centralized error aggregation or summary reporting is performed; callers receive only the log output and, for fatal stages, the raised exception.

## Summary

`pipeline.py` is the top-level orchestration module coordinating the full analysis pipeline. Its sole public function, `process_all_files(project_dir, output_dir, llm_client, max_workers)`, sequences: dependency graph construction, changed-file detection, per-file dependency extraction, optional LLM document generation, and consolidated output writing. Key data structures include `project_dep_list` (internal project-relative paths), `changed_files` (set of modified file paths), and `symbol_deps` (symbol-level caller/callee sets). Two path formats are maintained: internal project-relative paths and output `project_name/copy_path` format.
