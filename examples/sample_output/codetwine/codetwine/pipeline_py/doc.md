# Design Document: codetwine/pipeline.py

# Overview & Purpose

## 1. Module Summary
Orchestrates the end-to-end analysis pipeline for a project: it coordinates dependency-graph extraction, per-file dependency analysis, change detection, design-document generation, and consolidated output generation (JSON, SQLite, Mermaid diagram) for a single project directory.

## 2. When to Use This Module
- **Running a full project analysis from an entry point**: Call `process_all_files(project_dir, output_dir, llm_client, max_workers)` to analyze an entire codebase and produce all output artifacts (per-file dependency JSON, design docs, dependency graph, consolidated knowledge JSON/SQLite). This is the module's only intended external entry point, as used by `main.py`.
- **Incremental re-runs**: When re-invoking analysis on a project that was previously analyzed, `process_all_files` automatically detects changed files (via internal hash comparison against the output copies) and skips regenerating design documents for unaffected files, while still refreshing per-file dependency data for all files.
- **Recovering from partial/interrupted runs**: If a previous run failed midway (missing `file_dependencies.json` in the output), calling `process_all_files` again will treat those files as changed and reprocess them.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int = MAX_WORKERS` | `None` | Runs the full pipeline: builds the project dependency graph, detects changed files, extracts per-file dependency info, generates design documents (if enabled), and writes consolidated dependency summary, Mermaid graph, and knowledge outputs (JSON and/or SQLite per `KNOWLEDGE_FORMAT`). |

*Note: `_convert_dep_list_to_internal_paths`, `_detect_changed_files`, and `_process_file_dependencies` are internal helpers (prefixed with `_`) and are not part of the public interface.*

## 4. Design Decisions
- **Path format duality**: The pipeline converts between two path representations — the "project_name/copy_path" format used in persisted outputs (`project_dependencies.json`, output file JSON) and plain project-relative paths used internally for file I/O and lookups. `_convert_dep_list_to_internal_paths` and `to_output_path` mark the conversion boundaries explicitly, keeping internal logic simpler while preserving a flattened, collision-free output directory layout.
- **Shared computation reuse**: Lookups that are identical across all files in a project (`project_file_set`, `source_root_set`, `caller_map`) are computed once in `_process_file_dependencies` and passed into each `get_file_dependencies` call, avoiding redundant recomputation per file.
- **Change-driven regeneration**: Change detection (`_detect_changed_files`) is performed once and its result (`changed_files`) is passed to `generate_all_docs`, decoupling "what changed" detection from "what needs regeneration" logic (the latter also considers callee changes, handled inside `doc_creator.py`).
- **Symbol-level dependency reuse**: `build_symbol_level_deps` is computed once and shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json`, and `save_consolidated_sqlite` to avoid redundant re-parsing of per-file dependency JSON across multiple output-generation steps.
- **Optional/pluggable output formats**: Document generation is gated by `ENABLE_LLM_DOC`, and knowledge output format is selected via `KNOWLEDGE_FORMAT` (`"json"`, `"sqlite"`, or `"both"`), allowing the pipeline to run without an LLM client or produce only the desired output artifact(s).
- **Explicit cache lifecycle**: The tree-sitter `parse_cache` is cleared at the end of the run to bound memory usage across pipeline invocations, since parsing is cached internally by `ts_parser.py` during analysis.

# Definition Design Specifications

## Module-level constants

| Name | Value / Source | Purpose |
|---|---|---|
| `logger` | `logging.getLogger(__name__)` | Module-scoped logger for progress/error reporting throughout the pipeline. |

---

## `_convert_dep_list_to_internal_paths(project_dep_list_raw: list[dict], project_name: str) -> list[dict]`

**Responsibility:** Translates the project-wide dependency graph (produced with `"project_name/copy_path"`-formatted paths) back into plain project-relative paths so the rest of the pipeline can operate on a single, consistent path format.

**When to use:** Called once, immediately after `build_project_dependencies` returns, before any other file processing occurs.

**Design decisions:**
- Uses a local closure `to_internal(path: str) -> str` to apply a two-step transform (strip the `"{project_name}/"` prefix, then reverse the copy-path flattening via `copy_path_to_rel`) uniformly to `file`, `callers`, and `callees`.
- Prefix stripping is defensive: paths not starting with the expected prefix are passed through unchanged rather than raising.

**Constraints & edge cases:**
- Assumes each dict in `project_dep_list_raw` has a `"file"` key; `callers`/`callees` are optional (`.get(..., [])`).
- Does not deduplicate or validate resulting paths.

---

## `_detect_changed_files(all_file_list: list[str], project_dir: str, base_output_dir: str) -> set[str]`

**Responsibility:** Identifies which source files require reprocessing, either because their content changed since the last run or because their previous output is incomplete.

**When to use:** Called once per pipeline run, after the file list is finalized (empty files excluded), to scope later regeneration (design docs) without forcing full reprocessing.

**Design decisions:**
- "Changed" is a union of two independent conditions: hash mismatch (`is_file_unchanged` returns `False`) OR missing `file_dependencies.json` — the latter guards against a prior run that crashed mid-file, ensuring recovery even if the file copy itself is unchanged.
- Relies on `resolve_file_output_dir` to locate where the previous copy/JSON would live, keeping path logic consistent with the rest of the pipeline.

**Constraints & edge cases:**
- If the output directory for a file has never been created, it is correctly treated as changed (copy doesn't exist, JSON doesn't exist).
- Does not compare file content directly; relies entirely on hash comparison logic in `is_file_unchanged`.

---

## `_process_file_dependencies(files_to_process: list[str], project_dir: str, base_output_dir: str, project_dep_list: list[dict]) -> None`

**Responsibility:** For each file in scope, extracts symbol-level dependency information, writes it as `file_dependencies.json`, and copies the source file into the output directory structure — the core per-file analysis step of the pipeline.

**When to use:** Called once per run, always with the full `all_file_list` (per the module's design comment, always processed for consistency regardless of change detection).

**Design decisions:**
- Precomputes three lookups shared identically across all files (`project_file_set`, `source_root_set` via `detect_source_roots`, `caller_map`) once, outside the loop, to avoid redundant recomputation per file — these are passed into `get_file_dependencies` for every iteration.
- Converts internal relative paths to output (`"project_name/copy_path"`) format only *after* dependency extraction, mutating `dep_result["file"]` and the `"from"`/`"file"` fields inside `callee_usages`/`caller_usages` lists in place.
- Per-file failures are caught and logged individually (`try/except` inside the loop) so one bad file does not abort processing of the rest.

**Constraints & edge cases:**
- Silently skips usages lacking a `"from"` or `"file"` key (checked via `if "from" in usage` / `if "file" in usage`) rather than erroring.
- Exceptions are only logged (`logger.error`), not re-raised — callers of this function cannot detect partial failure except via logs.
- Creates the output directory (`os.makedirs(..., exist_ok=True)`) before writing, so it does not assume prior directory existence.

---

## `async process_all_files(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int = MAX_WORKERS) -> None`

**Responsibility:** Top-level orchestration entry point that runs the entire analysis pipeline for one project — dependency graph construction, per-file extraction, design-doc generation, and consolidated knowledge output (JSON/SQLite/Mermaid).

**When to use:** Invoked once per project analysis run; this is the sole public entry point of the module and is called directly from `main.py`.

**Concurrency semantics:**
- `async def`, but only one internal call is actually asynchronous: `await generate_all_docs(...)` (Step 3). All other steps (dependency graph build, file processing, JSON/SQLite/Mermaid generation) run synchronously and sequentially within the coroutine.
- Because `generate_all_docs` is awaited directly (not scheduled concurrently with other work), there is no parallelism at this function's level — concurrency, if any, is internal to `generate_all_docs`.

**Design decisions:**
- **Empty-file exclusion:** Files whose stripped content is empty are filtered out of both `project_dep_list` and `all_file_list` before any processing, with a printed/logged summary of exclusions. Read errors (`OSError`, `UnicodeDecodeError`) during this check are silently ignored (file is treated as non-empty/kept).
- **Change detection is advisory, not gating:** `changed_files` is computed (Step 1.5) but Step 2 (`_process_file_dependencies`) still processes *all* files regardless — change detection is used only to scope Step 3's regeneration decisions (passed into `generate_all_docs`), not to skip extraction.
- **Shared computation reuse:** `symbol_deps` (via `build_symbol_level_deps`) and `summary_map` (via `build_summary_map`) are each computed exactly once and passed into all three downstream consumers (`save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json`/`save_consolidated_sqlite`) to avoid redundant file I/O and JSON parsing.
- **Conditional doc generation:** Design document generation (Step 3) is entirely gated by the `ENABLE_LLM_DOC` config flag; when disabled, the step is skipped with a log/print message and the pipeline proceeds directly to dependency-summary generation.
- **Conditional knowledge output format:** Step 5 branches on `KNOWLEDGE_FORMAT` (`"json"`, `"sqlite"`, or `"both"`) to decide whether to call `save_consolidated_json`, `save_consolidated_sqlite`, both, or (implicitly) neither.
- **Output path derivation:** `base_output_dir` is deterministically derived as `{output_dir}/{project_name}` where `project_name = os.path.basename(project_dir)`, establishing the namespace used consistently by all downstream path-conversion helpers (`to_output_path`, `resolve_file_output_dir`).
- **Cache cleanup:** Explicitly clears the module-level `parse_cache` (from `ts_parser`) at the end of the run to free memory, since the cache is otherwise unbounded across the whole project's parse operations.

**Constraints & edge cases:**
- `llm_client` may be `None`; this is only valid when `ENABLE_LLM_DOC` is `False`, since `generate_all_docs` (called only when `ENABLE_LLM_DOC` is `True`) expects a usable `LLMClient`.
- Assumes `project_dir` exists and is readable; no validation of `project_dir`/`output_dir` is performed before use.
- The dependency graph (`project_dep_list`) drives the definitive file list (`all_file_list`) — any file not captured by `build_project_dependencies` (e.g., unsupported extensions) is excluded from the entire pipeline, not just from dependency analysis.
- Progress reporting is duplicated via both `print()` and `logger.info()` at every major step, with no single-sourcing — indicates console output and log output are treated as equally important channels by design.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/pipeline.py` → `codetwine/parsers/ts_parser.py` (`parse_cache`) : to clear the tree-sitter parse result cache at the end of the pipeline run and free memory.
- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` (`build_project_dependencies`) : to build the project-wide file dependency graph (file/callers/callees) as the starting point of the analysis.
- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` (`get_file_dependencies`) : to analyze a single file's definitions, callee usages, and caller usages for `file_dependencies.json` generation.
- `codetwine/pipeline.py` → `codetwine/output.py` (`save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, `build_summary_map`) : to convert internal paths to output-format paths, build symbol-level dependency maps and summary maps, and generate the consolidated JSON, dependency summary JSON, and Mermaid dependency graph outputs.
- `codetwine/pipeline.py` → `codetwine/doc_creator.py` (`generate_all_docs`) : to generate per-file design documents in topological order via the LLM, respecting the set of changed files for regeneration.
- `codetwine/pipeline.py` → `codetwine/import_to_path.py` (`detect_source_roots`) : to detect source root prefixes present in the project, used as shared lookup data for per-file dependency extraction.
- `codetwine/pipeline.py` → `codetwine/knowledge_db.py` (`save_consolidated_sqlite`) : to write the consolidated project knowledge into a SQLite database when SQLite output is enabled.
- `codetwine/pipeline.py` → `codetwine/llm/client.py` (`LLMClient`) : as the type for the LLM client instance passed through the pipeline for design document generation.
- `codetwine/pipeline.py` → `codetwine/utils/file_utils.py` (`copy_path_to_rel`, `is_file_unchanged`, `resolve_file_output_dir`) : to convert between copy-destination paths and project-relative paths, detect changed files by comparing source and output copies, and resolve each file's output directory.
- `codetwine/pipeline.py` → `codetwine/config/settings.py` (`MAX_WORKERS`, `ENABLE_LLM_DOC`, `KNOWLEDGE_FORMAT`) : to control concurrency level, whether LLM-based documentation is enabled, and which knowledge output format(s) to generate.

### Dependents (modules that import this file)

- `main.py` → `codetwine/pipeline.py` (`process_all_files`) : to run the full analysis pipeline (dependency extraction, document generation, and consolidated output generation) for a given project and output directory, using an `LLMClient` constructed based on `ENABLE_LLM_DOC`.

### Dependency Direction

All relationships are unidirectional. `codetwine/pipeline.py` depends on the listed internal modules (parsers, extractors, file_analyzer, output, doc_creator, import_to_path, knowledge_db, llm.client, utils.file_utils, config.settings) to perform its orchestration logic, while `main.py` depends on `codetwine/pipeline.py` to invoke that orchestration. There is no circular dependency between `codetwine/pipeline.py` and any of these modules.

# Data Flow

## 1. Inputs

- **`project_dir`** (str): Absolute path to the root of the project to analyze. Passed into `process_all_files` and threaded through nearly every helper.
- **`output_dir`** (str): Absolute path to the output root; combined with `project_name` (derived from `os.path.basename(project_dir)`) to form `base_output_dir`.
- **`llm_client`** (`LLMClient | None`): Passed through to `generate_all_docs`; may be `None` when `ENABLE_LLM_DOC` is disabled.
- **`max_workers`** (int, default `MAX_WORKERS` from config): Concurrency limit for document generation.
- **Config values** read at import time: `MAX_WORKERS`, `ENABLE_LLM_DOC` (bool), `KNOWLEDGE_FORMAT` (str: `"json" | "sqlite" | "both"`).
- **File reads**:
  - Raw project source files (read to check emptiness, and copied/analyzed for dependencies).
  - Existing output artifacts (`file_dependencies.json`, `doc.json`) checked for change detection and reused data.
  - Copied file hashes compared against source hashes (via `is_file_unchanged`).

## 2. Transformation Overview

**Stage 1 — Project-wide dependency graph construction**
`build_project_dependencies(project_dir)` walks the filesystem and returns `project_dep_list_raw`: a list of dicts with paths in `"project_name/copy_path"` format (`file`, `callers`, `callees`).
`_convert_dep_list_to_internal_paths` strips the `project_name/` prefix and converts each `copy_path` back to a project-relative path (via `copy_path_to_rel`), producing `project_dep_list` in internal relative-path format. `all_file_list` is derived as the flat list of `file` values.

**Stage 1a — Empty-file filtering**
Each file in `all_file_list` is read; files whose stripped content is empty are collected into `empty_files` and removed from both `project_dep_list` and `all_file_list`.

**Stage 1.5 — Change detection**
`_detect_changed_files` compares each source file's hash against its copy in `base_output_dir` (via `is_file_unchanged`) and checks for the existence of `file_dependencies.json`. Produces `changed_files: set[str]`, used later to scope document regeneration.

**Stage 2 — Per-file dependency extraction**
`_process_file_dependencies` builds shared lookup structures once (`project_file_set`, `source_root_set` via `detect_source_roots`, `caller_map`). For every file in `all_file_list`:
- Calls `get_file_dependencies(...)` → returns a dict (`file`, `definitions`, `callee_usages`, `caller_usages`).
- Converts internal paths in the result to output format (`project_name/copy_path`) via `to_output_path`.
- Writes the dict to `file_dependencies.json` in the file's resolved output directory (`resolve_file_output_dir`).
- Copies the original source file into the same output directory (`shutil.copy2`).
This stage fans out over all files sequentially (loop, not parallel) but is independent per file (errors caught and logged per-file without stopping the loop).

**Stage 3 — Design document generation (conditional, async)**
If `ENABLE_LLM_DOC` is true, `generate_all_docs(base_output_dir, project_dep_list, llm_client, max_workers, changed_files)` is awaited. Internally this fans out LLM calls in batches (bounded by `max_workers`) across dependency-topological levels, writing `doc.json`/`doc.md` per file and merging summaries into an internal `doc_summary_map`. This pipeline file does not directly see that map — it re-derives summaries afterward in Stage 3.5.

**Stage 3.5 — Symbol-level aggregation**
- `build_symbol_level_deps(base_output_dir, all_file_list)` reads each file's `file_dependencies.json` and produces `symbol_deps: dict[str, dict[str, set[str]]]` (per-file `callers`/`callees` sets, from actual usages rather than raw imports).
- `build_summary_map(base_output_dir, all_file_list)` reads each file's `doc.json` and produces `summary_map: dict[str, str | None]`.
- `save_dependency_summary` merges `symbol_deps` and `summary_map` into `project_dependency_summary.json`.

**Stage 4 — Mermaid graph generation**
`save_dependency_graph_as_mermaid` consumes `symbol_deps` to build node/edge sets and writes a Mermaid flowchart to `dependency_graph.md`.

**Stage 5 — Consolidated knowledge output**
Depending on `KNOWLEDGE_FORMAT`:
- `save_consolidated_json` merges per-file `file_dependencies.json` + `doc.json` + `symbol_deps` + `summary_map` into `project_knowledge.json`.
- `save_consolidated_sqlite` performs the equivalent merge into `project_knowledge.sqlite`.
Both re-read per-file JSON from disk rather than reusing in-memory results directly (aside from the shared `symbol_deps`/`summary_map`).

**Cleanup**
`parse_cache.clear()` empties the tree-sitter parse cache (module-level `OrderedDict`) to free memory at the end of the run.

## 3. Outputs

- **Side-effect file writes** (all under `base_output_dir = output_dir/project_name`):
  - `{output_file_dir}/file_dependencies.json` — one per source file.
  - `{output_file_dir}/{basename}` — copy of the original source file.
  - `{output_file_dir}/doc.json` / `doc.md` — one per source file (written by `generate_all_docs`, only if `ENABLE_LLM_DOC`).
  - `project_dependency_summary.json` — project-wide symbol dependency + summary JSON.
  - `dependency_graph.md` — Mermaid diagram of symbol-level dependencies.
  - `project_knowledge.json` — full consolidated knowledge (if `KNOWLEDGE_FORMAT` is `"json"` or `"both"`).
  - `project_knowledge.sqlite` — full consolidated knowledge DB (if `KNOWLEDGE_FORMAT` is `"sqlite"` or `"both"`).
- **Logging/console output**: progress messages via `print` and `logger.info`/`logger.error` at each stage.
- **Return value**: `process_all_files` returns `None`; all results are communicated via the filesystem.
- **In-memory cache mutation**: `parse_cache` cleared at the end (module-level global in `ts_parser.py`).

## 4. Key Data Structures

### `project_dep_list` / `project_dep_list_raw` (list of dicts)
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | str | File path (raw: `"project_name/copy_path"`; internal: project-relative path) |
| `callers` | list[str] | Files depending on this file |
| `callees` | list[str] | Files this file depends on |

### `all_file_list`
| Type | Purpose |
|---|---|
| `list[str]` | Project-relative paths of all (non-empty) files to analyze |

### `changed_files`
| Type | Purpose |
|---|---|
| `set[str]` | Relative paths of files detected as changed or missing dependency output |

### `dep_result` (return of `get_file_dependencies`, mutated before saving)
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | str | Target file path (converted to output format before writing) |
| `definitions` | list[dict] | Extracted symbol definitions (`name`, `type`, `start_line`, `end_line`, `context`) |
| `callee_usages` | list[dict] | Usage records; each has a `from` key (converted to output path) |
| `caller_usages` | list[dict] | Usage records; each has a `file` key (converted to output path) |

### `symbol_deps` (from `build_symbol_level_deps`)
| Field / Key | Type | Purpose |
|---|---|---|
| `<file_rel>` | str (key) | Project-relative file path |
| `callers` | set[str] | Files that use symbols from this file |
| `callees` | set[str] | Files whose symbols this file uses |

### `summary_map` (from `build_summary_map`)
| Field / Key | Type | Purpose |
|---|---|---|
| `<file_rel>` | str (key) | Project-relative file path |
| value | str \| None | Summary text from `doc.json`, or `None` if absent |

### `project_file_set`, `source_root_set`, `caller_map` (built once in `_process_file_dependencies`)
| Name | Type | Purpose |
|---|---|---|
| `project_file_set` | set[str] | All project-relative file paths, used for import resolution |
| `source_root_set` | set[str] | Detected source-root prefixes (e.g. `"src/main/java/"`) |
| `caller_map` | dict[str, list[str]] | File → list of caller files, used to compute `caller_usages` |

### `empty_files`
| Type | Purpose |
|---|---|
| `set[str]` | Relative paths of files excluded due to empty content |

# Error Handling

### 1. Overall Strategy

The pipeline follows a **logging-and-continue** strategy at the per-file level combined with **fail-fast** at the project level. During the per-file dependency extraction stage (`_process_file_dependencies`), each file is processed independently inside a try-except block; a failure on one file is logged and the loop proceeds to the next file, so a single malformed or unparsable file does not abort the entire project analysis. In contrast, project-wide setup steps (building the dependency graph, detecting changed files, generating consolidated outputs) have no exception handling in this file and will propagate errors upward, terminating the pipeline run. Empty-file exclusion uses a narrow, explicit catch to tolerate expected I/O/encoding issues while treating anything else as an unexpected failure that should surface. Overall, the design accepts partial per-file failures as tolerable noise in a batch analysis job, while structural/setup failures are treated as fatal since subsequent stages depend on their success.

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Per-file dependency extraction failure | `get_file_dependencies`, file copy, or JSON write raises inside `_process_file_dependencies` | Caught by generic `except Exception`, logged via `logger.error` with file path and message; processing continues to next file | Yes (skipped, loop continues) | That file has no `file_dependencies.json`/copy for this run; it will be re-detected as "changed" in a future run since the deps JSON is missing |
| Empty file read failure | Reading a file to check emptiness raises `OSError` or `UnicodeDecodeError` | Caught by specific `except (OSError, UnicodeDecodeError)`, silently passed (file is not marked empty, not excluded) | Yes (file remains in normal processing flow) | File is treated as non-empty and proceeds through the normal pipeline; if truly unreadable, downstream stages may fail for it |
| Missing/changed source file detection | `is_file_unchanged` finds no copy at destination, or hash mismatch, or missing `file_dependencies.json` | File added to `changed_files` set for downstream doc regeneration targeting | Yes (drives designed reprocessing, not an error path) | Ensures doc generation regenerates affected files; not a failure but a recovery/consistency mechanism for incomplete prior runs |
| Project dependency graph build failure | `build_project_dependencies` raises (e.g., unreadable project directory, parser failure) | Not caught in this file; exception propagates | No | Entire `process_all_files` call fails; no output directory content is produced for this run |
| Document generation task failure | Individual file doc generation raises inside `generate_all_docs` (external module, awaited here) | Delegated entirely to `doc_creator.generate_all_docs`; this file does not add its own handling around the `await` call | Depends on external module's internal handling | If unhandled inside `generate_all_docs`, propagates and aborts pipeline; this file provides no additional safety net |
| Consolidated output generation failure (JSON/SQLite/Mermaid) | `save_consolidated_json`, `save_consolidated_sqlite`, `save_dependency_summary`, or `save_dependency_graph_as_mermaid` raise (e.g., disk write failure) | Not caught in this file; exception propagates | No | Entire pipeline run terminates after partial per-file outputs were already written to disk |

### 3. Design Notes

- The asymmetry between per-file processing (tolerant) and project-wide steps (fatal) reflects that per-file failures are isolated and their absence is self-describing (missing `file_dependencies.json` acts as a natural marker for retry on the next run), whereas failures in graph-building or consolidation stages would leave the overall knowledge base structurally incomplete or inconsistent, so no attempt is made to degrade gracefully there.
- The change-detection mechanism (`_detect_changed_files`) doubles as an implicit error-recovery mechanism: any file lacking a `file_dependencies.json` (e.g., due to a prior failed or interrupted run) is automatically treated as changed and reprocessed, without requiring explicit retry logic.
- Exception handling in `_process_file_dependencies` uses a broad `except Exception` deliberately, since the underlying causes (parser errors, encoding issues, filesystem issues across many file types/languages) are heterogeneous and not enumerable individually; the priority is keeping the batch moving rather than precisely categorizing failures.
- No retry logic exists in this file itself for transient failures (e.g., filesystem contention); retry behavior for LLM calls is delegated entirely to `LLMClient` and `doc_creator`, keeping this file focused on orchestration rather than resilience primitives.

# Summary

Orchestrates full project analysis: dependency graph build, per-file extraction, change detection, doc generation, and consolidated output writing.

Public API: `async process_all_files(project_dir: str, output_dir: str, llm_client: LLMClient | None, max_workers: int = MAX_WORKERS) -> None`.

Key data: `project_dep_list`/`all_file_list` (list[dict]/list[str]), `changed_files` (set[str]), `symbol_deps` (dict[str, dict[str, set[str]]]), `summary_map` (dict[str, str|None]).
