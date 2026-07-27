# Design Document: codetwine/pipeline.py

# Overview & Purpose

`codetwine/pipeline.py` is the **orchestration layer** of the codetwine analysis tool. It defines the top-level workflow that turns a raw project directory into a full set of analysis artifacts (per-file dependency JSON, design documents, dependency summary, Mermaid graph, and consolidated knowledge JSON). It exists as a separate module to decouple *sequencing/coordination logic* from the lower-level concerns handled by its dependencies (parsing, dependency extraction, doc generation, output serialization, LLM calls, file utilities). This separation lets `main.py` invoke a single entry point (`process_all_files`) without needing to know about internal staging, change detection, or path-format conversions between "project_name/copy_path" (used for output/storage) and plain relative paths (used internally for analysis).

## Main Public Interface

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `process_all_files` | `project_dir: str, output_dir: str, llm_client: LLMClient \| None, max_workers: int = MAX_WORKERS` | `None` (async) | Top-level pipeline entry point: builds the project dependency graph, detects changed files, extracts per-file dependencies, generates design docs, and writes summary/graph/consolidated JSON outputs. |

(Note: `_convert_dep_list_to_internal_paths`, `_detect_changed_files`, and `_process_file_dependencies` are internal helper functions, prefixed with `_`, and not intended as the module's public interface; they support `process_all_files` internally.)

## Design Decisions & Patterns

- **Staged pipeline pattern**: `process_all_files` executes a fixed, sequential set of numbered stages (build graph → detect changes → extract per-file deps → generate docs → build summary/graph → generate consolidated JSON), each delegated to a specialized module, making the overall flow easy to follow and modify stage-by-stage.
- **Path-format boundary**: Internally the pipeline operates on plain project-relative paths, while stored/output artifacts use "project_name/copy_path" format. `_convert_dep_list_to_internal_paths` and `to_output_path` calls mark the explicit conversion boundary between these two representations, avoiding path-format leakage across the codebase.
- **Incremental reprocessing via change detection**: `_detect_changed_files` compares source file hashes against previously copied output files (via `is_file_unchanged`) and also treats files missing `file_dependencies.json` as changed, allowing the pipeline to recover from partially failed prior runs and to limit doc regeneration (via `changed_files` passed into `generate_all_docs`) to the actual impact range of changes.
- **Always-reprocess dependency extraction, selective doc regeneration**: Step 2 (`_process_file_dependencies`) always reprocesses *all* files for consistency of dependency data, whereas Step 3 (design doc generation) uses `changed_files` to skip unaffected files—an explicit trade-off between correctness of dependency graphs and cost of LLM calls.
- **Shared intermediate state reuse**: `symbol_deps` (from `build_symbol_level_deps`) and `summary_map` (from `build_summary_map`) are each computed once and passed into multiple downstream output-generation functions (`save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json`), avoiding redundant file I/O and recomputation.
- **Resource cleanup**: `parse_cache.clear()` is called at the end of the pipeline to release cached parse trees/content once analysis is complete, bounding memory usage across pipeline runs.
- **Empty-file exclusion**: Files with no non-whitespace content are filtered out of both `project_dep_list` and `all_file_list` before any further processing, preventing wasted analysis effort on trivial files.

# Definition Design Specifications

## `_convert_dep_list_to_internal_paths(project_dep_list_raw, project_name) -> list[dict]`

**Arguments:**
- `project_dep_list_raw`: list of dicts with `file`, `callers`, `callees` keys, in "project_name/copy_path" format as produced by `build_project_dependencies`.
- `project_name`: the project's base directory name, used as the prefix to strip.

**Returns:** A list of dicts with the same keys, but paths converted to plain project-relative paths.

**Responsibility:** Acts as a translation boundary between `build_project_dependencies`'s external ("project_name/copy_path") path format and the internal pipeline representation, which operates purely on relative paths from the project root. This isolates the rest of the pipeline from the copy-path naming convention.

**Design decisions:** Prefix stripping is defensive (`if path.startswith(prefix)`) rather than assumed, so paths that don't match the expected format pass through `copy_path_to_rel` unchanged instead of raising errors.

**Edge cases:** Assumes all paths in `project_dep_list_raw` are prefixed with `project_name/`; malformed/unprefixed entries are passed to `copy_path_to_rel` as-is, which may or may not correctly restore them depending on their structure.

---

## `_detect_changed_files(all_file_list, project_dir, base_output_dir) -> set[str]`

**Arguments:**
- `all_file_list`: relative paths of all project files to check.
- `project_dir`: absolute path to the project root.
- `base_output_dir`: absolute path to the output root for this project.

**Returns:** A set of relative paths of files considered "changed."

**Responsibility:** Supports incremental processing by identifying which files need document regeneration in Stage 3/3.5, avoiding redundant LLM calls for unchanged files (used later by `generate_all_docs`'s `changed_files` parameter).

**Design decisions:** A file is treated as changed not only when its hash differs from its output copy, but also when `file_dependencies.json` is missing in the output directory — this recovers from a prior run that was interrupted mid-processing (partial output state), treating it as if the file were newly changed.

**Edge cases:** Relies on `is_file_unchanged` returning `False` when the copied file doesn't exist, so a first-time run naturally marks all files as changed.

---

## `_process_file_dependencies(files_to_process, project_dir, base_output_dir, project_dep_list) -> None`

**Arguments:**
- `files_to_process`: relative paths of files to analyze (in practice, all project files).
- `project_dir`: absolute project root.
- `base_output_dir`: absolute output root.
- `project_dep_list`: project-wide dependency list in internal (relative) path format.

**Returns:** `None` (side effects only: writes `file_dependencies.json` and copies source files into per-file output directories).

**Responsibility:** Per-file analysis stage of the pipeline — computes each file's dependency metadata via `get_file_dependencies` and persists it alongside a copy of the original source, establishing the on-disk artifacts that later stages (`build_symbol_level_deps`, `generate_all_docs`, consolidated JSON) read.

**Design decisions:**
- Converts the `file`, `callee_usages[].from`, and `caller_usages[].file` fields to the output-facing "project_name/copy_path" format (via `to_output_path`) before writing, since downstream consumers of the JSON expect that format, while internal computation used relative paths.
- Copies the original file into the output directory to serve `is_file_unchanged`'s hash comparison in subsequent runs and to make the output tree self-contained.
- Wraps each file's processing in a try/except so a single file's failure (e.g., parse error) is logged and skipped without aborting the whole batch.

**Edge cases:** Always processes *all* files (not just changed ones) "for consistency" per the pipeline's documented flow, even though change detection was already performed — ensuring `file_dependencies.json` output stays fully in sync with current source.

---

## `async process_all_files(project_dir, output_dir, llm_client, max_workers=MAX_WORKERS) -> None`

**Arguments:**
- `project_dir`: root directory of the project to analyze.
- `output_dir`: root directory under which per-project output is written (actual output path is `output_dir/project_name`).
- `llm_client`: `LLMClient | None`, used for design doc generation; may be `None` when LLM doc generation is disabled.
- `max_workers`: concurrency limit for parallel file processing (default from `MAX_WORKERS` config).

**Returns:** `None` — this is the top-level orchestration entry point, invoked by `main.py`; all results are written to disk.

**Responsibility:** Coordinates the entire analysis pipeline for a project: building the dependency graph, detecting changes, extracting per-file dependencies, generating design documents, and producing consolidated output artifacts (dependency summary, Mermaid graph, consolidated JSON).

**Design decisions:**
- Empty files are explicitly filtered out of both `project_dep_list` and `all_file_list` before any further processing, since they carry no meaningful definitions or dependencies and would otherwise pollute documentation/dependency output; file reads for this check tolerate `OSError`/`UnicodeDecodeError` by silently skipping (treating unreadable files as non-empty rather than failing the whole pipeline).
- Change detection (Step 1.5) happens *before* full dependency extraction (Step 2) but its result (`changed_files`) is only consumed later by `generate_all_docs`, allowing doc generation to skip unaffected files/branches of the dependency graph while dependency JSON is still refreshed for everything.
- `symbol_deps` (from `build_symbol_level_deps`) and `summary_map` (from `build_summary_map`) are computed once and explicitly shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json` to avoid redundant re-reading of every `file_dependencies.json`/`doc.json` three times.
- Design document generation is gated by the `ENABLE_LLM_DOC` config flag, allowing the pipeline to run in a "dependency-analysis only" mode without any LLM calls.
- `parse_cache.clear()` is called at the end to release memory held by cached tree-sitter parse trees, since the pipeline may be run repeatedly in a long-lived process (e.g., across multiple projects).

**Edge cases / constraints:**
- Assumes `project_dir` exists and is readable; no explicit validation is performed before calling `build_project_dependencies`.
- Output directory `base_output_dir` is derived from `os.path.basename(project_dir)`, so behavior may be ambiguous for a `project_dir` ending in a path separator or matching an existing unrelated directory name.
- `llm_client` may legitimately be `None`; this is only safe when `ENABLE_LLM_DOC` is `False`, since `generate_all_docs` is only invoked under that flag.

# Dependency Description

## Dependencies (what this file uses)

`pipeline.py` orchestrates the entire project analysis pipeline and depends on numerous internal modules to perform each stage:

- **`codetwine/parsers/ts_parser.py` (`parse_cache`)**: Used to clear the module-level parse result cache after analysis completes, freeing memory held from tree-sitter parsing across the pipeline run.

- **`codetwine/extractors/dependency_graph.py` (`build_project_dependencies`)**: Used to build the project-wide file dependency graph (callers/callees per file) as the foundational data structure for all subsequent processing.

- **`codetwine/file_analyzer.py` (`get_file_dependencies`)**: Used to analyze each individual file's definitions, callee usages, and caller usages, which are then persisted as `file_dependencies.json`.

- **`codetwine/output.py` (`save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, `build_summary_map`)**: Used to convert internal paths to output path format, build symbol-level dependency maps and summary maps shared across output generation, and produce the final consolidated JSON, dependency summary JSON, and Mermaid diagram artifacts.

- **`codetwine/doc_creator.py` (`generate_all_docs`)**: Used to generate LLM-based design documents for all files in topological order, leveraging change detection to regenerate only impacted files.

- **`codetwine/llm/client.py` (`LLMClient`)**: Used as the client passed through to `generate_all_docs` for LLM-based document generation; its type is referenced for function signatures.

- **`codetwine/utils/file_utils.py` (`copy_path_to_rel`, `is_file_unchanged`, `resolve_file_output_dir`)**: Used to convert output copy-path structures back to relative paths, detect changed files by comparing source and output copy hashes, and resolve each file's output directory location.

- **`codetwine/config/settings.py` (`MAX_WORKERS`, `ENABLE_LLM_DOC`)**: Used to control the concurrency level of file processing and to toggle whether LLM-based document generation is performed at all.

## Dependents (what uses this file)

- **`main.py`**: Calls `process_all_files` as the entry point to run the full analysis pipeline, passing in the resolved project/output directories and an `LLMClient` instance (or `None` if LLM documentation is disabled).

The dependency direction is unidirectional: `main.py` depends on `pipeline.py` to execute the analysis workflow, while `pipeline.py` has no dependency back on `main.py`.

# Data Flow

## Input
| Source | Format |
|---|---|
| `project_dir` (CLI/caller arg) | Absolute path to project root |
| `output_dir` (CLI/caller arg) | Absolute path to output root |
| `llm_client` | `LLMClient \| None` |
| Filesystem: source files under `project_dir` | Raw source text (any supported language) |
| Filesystem: prior run artifacts under `base_output_dir` (`file_dependencies.json`, `doc.json`, copied source files) | JSON / raw files, used for change/incompleteness detection |

## Main Transformation Flow

```
project_dir
   │  build_project_dependencies()
   ▼
project_dep_list_raw  ["project/copy_path" format dicts: {file, callers, callees}]
   │  _convert_dep_list_to_internal_paths()  (strip "project_name/", copy_path_to_rel)
   ▼
project_dep_list  [internal-relative-path dicts: {file, callers, callees}]
   │  derive all_file_list = [d["file"] ...]
   │  filter out empty files (read + strip check)
   ▼
all_file_list (relative paths) ──┬─► _detect_changed_files()
                                  │      compares source hash vs output copy hash (is_file_unchanged)
                                  │      + checks presence of file_dependencies.json
                                  │      ▼
                                  │   changed_files: set[str]
                                  │
                                  ├─► _process_file_dependencies()
                                  │      for each file: get_file_dependencies(file_abs, project_dir, project_dep_list)
                                  │      → dep_result {file, definitions, callee_usages, caller_usages}
                                  │      paths rewritten to output format via to_output_path()
                                  │      → written as file_dependencies.json + source file copied (shutil.copy2)
                                  │
                                  ├─► generate_all_docs() [if ENABLE_LLM_DOC]
                                  │      uses project_dep_list + changed_files to decide regen vs reuse
                                  │      LLM calls via llm_client → doc.json/doc.md per file
                                  │
                                  ├─► build_symbol_level_deps(base_output_dir, all_file_list)
                                  │      reads each file_dependencies.json's callee_usages/caller_usages
                                  │      ▼
                                  │   symbol_deps: {file_rel: {"callers": set, "callees": set}}
                                  │
                                  ├─► build_summary_map(base_output_dir, all_file_list)
                                  │      reads each doc.json's "summary"
                                  │      ▼
                                  │   summary_map: {file_rel: summary str | None}
                                  │
                                  ├─► save_dependency_summary() → project_dependency_summary.json
                                  ├─► save_dependency_graph_as_mermaid() → dependency_graph.md
                                  └─► save_consolidated_json() → project_knowledge.json
```

Finally, `parse_cache.clear()` releases the in-memory tree-sitter parse cache.

## Output
| Destination | Format | Producer |
|---|---|---|
| `{base_output_dir}/{output_file_dir}/file_dependencies.json` | `{file, definitions, callee_usages, caller_usages}` with paths in `"project_name/copy_path"` format | `_process_file_dependencies` |
| `{output_file_dir}/{basename}` | copied original source file | `_process_file_dependencies` (`shutil.copy2`) |
| `{output_file_dir}/doc.json` / `doc.md` | generated design doc `{file, sections, summary}` | `generate_all_docs` (external) |
| `project_dependency_summary.json` | `{project_name, files: [{file, summary, callers, callees}]}` | `save_dependency_summary` |
| `dependency_graph.md` | Mermaid flowchart of file-level dependencies | `save_dependency_graph_as_mermaid` |
| `project_knowledge.json` | `{project_name, project_dependencies, files: [{file, file_dependencies, doc}]}` | `save_consolidated_json` |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `project_dep_list` (internal) | `file` (rel path), `callers` (list of rel paths), `callees` (list of rel paths) | Drives file discovery, per-file dependency extraction, and doc-generation ordering |
| `all_file_list` | list of relative file paths | Master list of files to process across all stages (post empty-file exclusion) |
| `changed_files` | `set[str]` of relative paths | Used to limit doc regeneration to the impacted subset in `generate_all_docs` |
| `dep_result` (per file) | `file`, `definitions`, `callee_usages` (with `from`), `caller_usages` (with `file`) | Intermediate per-file analysis result before path conversion and JSON write |
| `symbol_deps` | `{file_rel: {callers: set, callees: set}}` | Symbol-usage-accurate dependency graph, shared across summary/mermaid/consolidated outputs |
| `summary_map` | `{file_rel: summary str \| None}` | LLM-generated per-file summaries, shared across the same three output builders |

## Path Convention Note
Two path formats coexist and are converted deliberately:
- **Internal/relative** (`src/foo.py`) — used for filesystem access and `project_dep_list`/`all_file_list` processing.
- **Output** (`project_name/copy_path`, e.g. `proj/foo_py/foo.py`) — used inside emitted JSON/Markdown artifacts, via `to_output_path` (write side) and `copy_path_to_rel`/`output_path_to_rel` (read side).

# Error Handling

This module applies a mixed strategy: per-file resilience during dependency extraction (graceful degradation with logging), combined with fail-fast behavior for project-wide setup steps and downstream document/JSON generation, where exceptions are allowed to propagate naturally rather than being caught locally.

Per-file dependency extraction (`_process_file_dependencies`) is the only stage with explicit error isolation—each file is processed independently inside a try/except, so a failure analyzing one file does not abort the batch or the overall pipeline run. Failures are logged and the file is simply skipped (no partial `file_dependencies.json` or copy is left in an inconsistent state, since writes occur after successful analysis). Empty-file exclusion also degrades gracefully: files that cannot be opened/decoded are silently treated as non-empty rather than raising.

In contrast, project-wide graph construction (`build_project_dependencies`), change detection, document generation (`generate_all_docs`), and the final consolidated JSON/summary/Mermaid generation steps have no local exception handling in this file. Any error in these stages propagates up and stops `process_all_files`, since correctness of the whole-project graph and consolidated outputs is treated as a prerequisite for subsequent steps.

| Error type | Handling | Impact |
|---|---|---|
| Exception during single-file dependency extraction/copy (`_process_file_dependencies`) | Caught per file, logged as `FAIL: {file}: {e}`, processing continues with next file | That file has no `file_dependencies.json`/copy in output; subsequent steps treat it as unchanged/missing and it will be re-detected as "changed" on the next run |
| File unreadable/undecodable during empty-file detection | Caught via `except (OSError, UnicodeDecodeError): pass`, file is not marked empty | File proceeds to normal processing rather than being excluded |
| Errors in `build_project_dependencies`, `_detect_changed_files`, `generate_all_docs`, `build_symbol_level_deps`, `build_summary_map`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json` | Not caught in this file; exceptions propagate to the caller (`main.py`) | Aborts `process_all_files`; no consolidated outputs are produced for that run |
| Missing `file_dependencies.json`/`doc.json` for a given file (not an exception, but a missing-artifact case) | Detected via `os.path.exists` checks in change detection and delegated to `output.py` helpers, which tolerate absence | File is treated as "changed" for reprocessing, or included with `null` summary/omitted sections in outputs, without raising |

Design considerations: the incremental design (hash comparison plus presence of `file_dependencies.json`) is itself a recovery mechanism—files affected by a previous partial failure are automatically re-detected as "changed" and reprocessed on the next run, so per-file isolation combines with idempotent re-execution rather than requiring rollback logic. Conversely, project-level steps are intentionally left fail-fast: since later stages (document generation, symbol-level dependency aggregation, consolidated JSON) all depend on a complete and consistent project dependency graph and file list, silently continuing after a failure there would risk producing an incomplete or misleading final output.

# Summary

`pipeline.py` is codetwine's orchestration layer, exposing one public async entry point: `process_all_files(project_dir, output_dir, llm_client, max_workers)`. It sequentially builds the project dependency graph, detects changed files (incremental reprocessing), extracts per-file dependencies (always, for consistency), optionally generates LLM design docs, then produces shared `symbol_deps`/`summary_map` structures feeding dependency summary, Mermaid graph, and consolidated JSON outputs. Internal helpers handle path-format conversion (relative vs. "project/copy_path"), change detection, and per-file processing with isolated error handling; project-wide steps are fail-fast.
