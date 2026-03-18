# Design Document: codetwine/pipeline.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`pipeline.py` is the central orchestration module of the CodeTwine analysis pipeline. It exists as a separate file to encapsulate the end-to-end workflow that transforms a raw project directory into a fully analyzed output: dependency graphs, per-file dependency JSON files, LLM-generated design documents, a Mermaid diagram, and a consolidated knowledge JSON. All other modules handle individual concerns (parsing, extraction, LLM calls, output formatting); this module sequences and coordinates them.

Its concrete responsibilities are:

- Converting paths between the `project_name/copy_path` output format and the internal relative-path format used during processing.
- Detecting which files have changed since the last run (by hash comparison and presence of prior output artifacts), enabling incremental processing.
- Driving per-file dependency extraction and persisting results to `file_dependencies.json` alongside a copy of the original source file.
- Conditionally invoking LLM-based design document generation (controlled by `ENABLE_LLM_DOC`).
- Coordinating the three aggregated output steps: dependency summary JSON, Mermaid graph, and consolidated knowledge JSON.
- Clearing the tree-sitter parse cache after the run to free memory.

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `process_all_files` | `project_dir: str`, `output_dir: str`, `llm_client: LLMClient \| None`, `max_workers: int = MAX_WORKERS` | `None` (async) | Top-level pipeline entry point: runs all analysis steps (dependency graph, file extraction, doc generation, output saving) for the given project directory. |

## Internal (Pipeline-Private) Functions

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_convert_dep_list_to_internal_paths` | `project_dep_list_raw: list[dict]`, `project_name: str` | `list[dict]` | Strips the `project_name/` prefix and reverses the copy-path encoding so dependency entries use plain project-relative paths during processing. |
| `_detect_changed_files` | `all_file_list: list[str]`, `project_dir: str`, `base_output_dir: str` | `set[str]` | Returns the set of relative file paths whose source hash differs from the output copy, or whose `file_dependencies.json` is absent (incomplete prior run). |
| `_process_file_dependencies` | `files_to_process: list[str]`, `project_dir: str`, `base_output_dir: str`, `project_dep_list: list[dict]` | `None` | Invokes `get_file_dependencies` for each file, converts paths to output format, writes `file_dependencies.json`, and copies the source file to the output directory. |

## Design Decisions

- **Incremental processing via hash comparison**: `_detect_changed_files` uses SHA-256 hashes (via `is_file_unchanged`) to avoid redundant work. The set of changed files is passed downstream to `generate_all_docs`, which applies its own callee-propagation logic to expand the regeneration scope only as far as necessary.
- **Path format boundary**: The pipeline maintains a strict internal/external path format boundary. The `project_name/copy_path` format is used in all persisted JSON files; internal processing uses plain relative paths. `_convert_dep_list_to_internal_paths` and `to_output_path` are the conversion points at that boundary.
- **Shared pre-built maps**: `build_symbol_level_deps` and `build_summary_map` are each called once and their results passed explicitly to all three downstream `save_*` functions, avoiding redundant disk reads.
- **Feature flag gating**: Design document generation is entirely skipped when `ENABLE_LLM_DOC` is `False`, making the LLM dependency optional with no structural change to the rest of the pipeline.
- **Empty file exclusion**: Files whose content is empty (or whitespace-only) are filtered from `all_file_list` and `project_dep_list` before any processing begins, preventing downstream analysis errors.

## Definition Design Specifications

# Definition Design Specifications

---

## `_convert_dep_list_to_internal_paths(project_dep_list_raw, project_name)`

**Arguments:**
- `project_dep_list_raw: list[dict]` — Raw dependency list as returned by `build_project_dependencies`, where all paths use the `project_name/copy_path` format.
- `project_name: str` — The base name of the project directory (e.g., `"my-project"`).

**Returns:** `list[dict]` — A dependency list with identical structure (`file`, `callers`, `callees` keys) where all path strings have been converted to project-root-relative paths.

**Responsibility:** Acts as a format bridge between `build_project_dependencies`'s output format (`project_name/copy_path`) and the internal representation used throughout the rest of the pipeline (plain relative paths). This decoupling allows the rest of the pipeline to remain agnostic of the output directory path encoding.

**Design decisions:** Applies `copy_path_to_rel` (from `file_utils`) after stripping the project-name prefix, reusing the canonical path-decoding logic rather than duplicating it. The prefix strip is guarded with a `startswith` check to tolerate paths that may already lack the prefix.

---

## `_detect_changed_files(all_file_list, project_dir, base_output_dir)`

**Arguments:**
- `all_file_list: list[str]` — Relative paths of all files in the project.
- `project_dir: str` — Absolute path to the project root.
- `base_output_dir: str` — Absolute path to the output root directory.

**Returns:** `set[str]` — Relative paths of files considered changed.

**Responsibility:** Implements incremental processing by identifying files that need to be re-analyzed, comparing source file hashes against their previously copied counterparts in the output directory.

**Edge cases and constraints:** A file is included in the changed set if *either* its hash differs from the output copy *or* its `file_dependencies.json` is absent. The second condition handles partial-failure recovery — if a previous run crashed between copying the file and writing the JSON, the file is re-processed rather than silently left in an inconsistent state.

---

## `_process_file_dependencies(files_to_process, project_dir, base_output_dir, project_dep_list)`

**Arguments:**
- `files_to_process: list[str]` — Relative paths of files to analyze.
- `project_dir: str` — Absolute path to the project root.
- `base_output_dir: str` — Absolute path to the output root directory.
- `project_dep_list: list[dict]` — Project-wide dependency list in internal path format.

**Returns:** `None`

**Responsibility:** Drives per-file dependency extraction and persists two artifacts to the output directory for each file: a `file_dependencies.json` containing the structured dependency record, and a verbatim copy of the original source file. These artifacts serve as the stable intermediate representation consumed by all downstream pipeline steps.

**Design decisions:** Path strings within the dependency result are converted to `project_name/copy_path` output format before serialization, so `file_dependencies.json` files are self-contained and portable relative to the output directory root. Errors for individual files are caught and logged without aborting the overall loop, so a single unparseable file does not block the rest of the project.

**Edge cases and constraints:** Callee path conversion targets `usage["from"]` keys inside `callee_usages`, and caller path conversion targets `usage["file"]` keys inside `caller_usages`; keys absent from a usage entry are skipped silently.

---

## `process_all_files(project_dir, output_dir, llm_client, max_workers)`

**Arguments:**
- `project_dir: str` — Absolute path to the root of the project being analyzed.
- `output_dir: str` — Absolute path to the directory where all output artifacts will be written.
- `llm_client: LLMClient | None` — An initialized LLM client used for design document generation, or `None` if LLM generation is disabled.
- `max_workers: int` — Maximum number of files processed concurrently during document generation. Defaults to the `MAX_WORKERS` configuration value.

**Returns:** `None`

**Responsibility:** Top-level async orchestrator for the full project analysis pipeline. It sequences all pipeline stages from dependency graph construction through to consolidated JSON output, managing state shared across stages (file lists, changed-file sets, symbol-level dependency maps, summary maps).

**Design decisions:**
- Empty files are detected and excluded before any further processing to prevent downstream parsers from operating on trivially empty inputs.
- Changed-file detection runs before dependency extraction, but Step 2 always re-processes *all* files regardless of change status, ensuring `file_dependencies.json` and source copies are consistent with the current project state. Incremental optimization is applied only in Step 3 (document generation), where regeneration is propagated transitively through the callee graph.
- `symbol_deps` and `summary_map` are computed once after document generation and passed explicitly to the three subsequent output functions (`save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json`) to avoid redundant I/O.
- `parse_cache.clear()` is called at the end to release the tree-sitter AST cache held in `ts_parser`, freeing memory after the pipeline completes.
- LLM document generation is gated on the `ENABLE_LLM_DOC` configuration flag; when disabled, the stage is skipped entirely and a message is emitted rather than silently omitting it.

**Edge cases and constraints:** `llm_client` may be `None` when `ENABLE_LLM_DOC` is `False`; in that case, `generate_all_docs` is never called. The function is `async` because `generate_all_docs` uses `asyncio`-based concurrency; callers must use `asyncio.run` or an equivalent mechanism.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

**`codetwine/extractors/dependency_graph.py`** (`build_project_dependencies`)
Used to perform the initial project-wide static analysis that produces the raw caller/callee graph. This is the first step of the pipeline, providing the foundational file relationship data that all subsequent processing stages depend on.

**`codetwine/file_analyzer.py`** (`get_file_dependencies`)
Used to perform per-file deep analysis, extracting symbol definitions, callee usages, and caller usages for each individual source file. The results are serialized as `file_dependencies.json` in the output directory.

**`codetwine/output.py`** (`save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `build_symbol_level_deps`, `to_output_path`, `build_summary_map`)
Used at the final stages of the pipeline to convert internal path formats and persist three output artifacts: the symbol-level dependency graph, the Mermaid flowchart Markdown, and the consolidated project knowledge JSON. `build_symbol_level_deps` and `build_summary_map` are used to pre-build shared data structures passed into all three save functions.

**`codetwine/doc_creator.py`** (`generate_all_docs`)
Used to invoke LLM-driven design document generation in topological order when `ENABLE_LLM_DOC` is enabled. Receives the set of changed files detected earlier in the pipeline to enable incremental regeneration.

**`codetwine/llm/client.py`** (`LLMClient`)
Used as a type annotation for the `llm_client` parameter of `process_all_files`, and passed through to `generate_all_docs`. The pipeline itself does not call LLM methods directly but acts as a carrier of the client instance.

**`codetwine/utils/file_utils.py`** (`copy_path_to_rel`, `is_file_unchanged`, `resolve_file_output_dir`)
Used for three distinct utility purposes: `copy_path_to_rel` converts the `project_name/copy_path` format back to project-relative paths for internal pipeline use; `is_file_unchanged` compares source and output file hashes to detect which files need reprocessing; `resolve_file_output_dir` computes the per-file output directory path used when writing `file_dependencies.json` and copying the original file.

**`codetwine/config/settings.py`** (`MAX_WORKERS`, `ENABLE_LLM_DOC`)
Used to read configuration values that control concurrency (`MAX_WORKERS` as the default for parallel document generation) and conditionally enable or skip the LLM documentation stage (`ENABLE_LLM_DOC`).

**`codetwine/parsers/ts_parser.py`** (`parse_cache`)
Used exclusively at the end of `process_all_files` to clear the module-level AST parse cache and free memory after all file processing is complete.

---

### Dependents (what uses this file)

**`main.py`** (`process_all_files`)
`main.py` is the sole dependent. It resolves the project and output directories from command-line arguments, conditionally instantiates an `LLMClient`, and delegates the entire analysis pipeline to `process_all_files`. The dependency is strictly unidirectional: `main.py` calls into `pipeline.py`, and `pipeline.py` has no knowledge of `main.py`.

## Data Flow

# Data Flow

## Overview

`pipeline.py` is the top-level orchestrator. It accepts a project directory, runs analysis stages in sequence, and writes structured JSON and Markdown files to an output directory.

---

## Input Sources

| Input | Format | Source |
|---|---|---|
| `project_dir` | Absolute directory path | Caller (`main.py`) |
| `output_dir` | Absolute directory path | Caller (`main.py`) |
| `llm_client` | `LLMClient \| None` | Caller (`main.py`) |
| Source files | Language source files on disk | `project_dir` tree |
| Existing output copies | Files + `file_dependencies.json` | Previous run under `output_dir` |

---

## Main Transformation Flow

```
project_dir (source tree)
        │
        ▼
build_project_dependencies()
        │ list[dict] — "project_name/copy_path" format
        ▼
_convert_dep_list_to_internal_paths()
        │ list[dict] — relative path format (internal)
        │   project_dep_list: [{file, callers, callees}]
        ▼
_detect_changed_files()
        │ set[str] — relative paths of changed files
        ▼
_process_file_dependencies()  ← writes file_dependencies.json + file copy per file
        │
        ▼
generate_all_docs()  (if ENABLE_LLM_DOC)  ← writes doc.json + doc.md per file
        │
        ▼
build_symbol_level_deps()
        │ symbol_deps: {rel_path → {callers: set, callees: set}}
        ▼
build_summary_map()
        │ summary_map: {rel_path → str | None}
        ▼
save_dependency_summary()      → project_dependency_summary.json
save_dependency_graph_as_mermaid() → dependency_graph.md
save_consolidated_json()       → project_knowledge.json
```

---

## Path Format Conversions

The pipeline uses two path formats internally and converts between them at boundaries:

| Format | Example | Used where |
|---|---|---|
| **Internal relative path** | `src/foo.py` | Inside pipeline functions, `project_dep_list` |
| **Output format** | `my-project/src_py/foo.py` | `file_dependencies.json` fields, consolidated JSON |

`_convert_dep_list_to_internal_paths` strips the `project_name/` prefix and calls `copy_path_to_rel` to reverse the `{stem}_{ext}` directory insertion, converting output-format paths from `build_project_dependencies` into internal relative paths.

`to_output_path` performs the inverse when writing results.

---

## Key Data Structures

### `project_dep_list` (internal format)
```
[
  {
    "file":    "src/foo.py",        # relative path from project root
    "callers": ["src/bar.py"],      # files that import this file
    "callees": ["src/baz.py"],      # files this file imports
  },
  ...
]
```
Drives all downstream stages: change detection, per-file analysis, doc generation, and graph building.

---

### `changed_files: set[str]`
Relative paths where either the source hash differs from the output copy or `file_dependencies.json` is absent. Controls incremental doc regeneration in `generate_all_docs`.

---

### `symbol_deps`
```
{
  "src/foo.py": {
    "callers": {"src/bar.py"},   # files that actually use symbols from foo
    "callees": {"src/baz.py"},   # files whose symbols foo actually uses
  }
}
```
Built from `callee_usages[*].from` and `caller_usages[*].file` fields in each `file_dependencies.json`. Represents actual symbol-level usage rather than raw import edges. Shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`.

---

### `summary_map`
```
{
  "src/foo.py": "One-line LLM summary text",
  "src/bar.py": None,   # no doc.json or generation failed
}
```
Read from each file's `doc.json`. Injected into the three consolidated output files.

---

## Output Destinations

| File | Location | Contents |
|---|---|---|
| `file_dependencies.json` | `base_output_dir/<copy_path_dir>/` per file | Definitions, callee_usages, caller_usages (output-format paths) |
| File copy | Same directory as above | Verbatim copy of source file (used for hash-based change detection) |
| `doc.json` / `doc.md` | Same per-file directory | LLM-generated design document |
| `project_dependency_summary.json` | `base_output_dir/` | Symbol-level dep graph + summaries, lightweight |
| `dependency_graph.md` | `base_output_dir/` | Mermaid flowchart of file-level dependencies |
| `project_knowledge.json` | `base_output_dir/` | Full consolidated JSON merging all per-file artifacts |

## Error Handling

# Error Handling

## Overall Strategy

`pipeline.py` adopts a **mixed strategy**: individual file processing uses graceful degradation to allow the pipeline to continue despite per-file failures, while the overall orchestration flow (`process_all_files`) applies no top-level exception handling and will propagate unexpected errors to the caller (`main.py`).

The guiding principle is that a failure in one file should not abort analysis of the remaining files, but structural failures at the project or pipeline level are allowed to surface as uncaught exceptions.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Exception during per-file dependency extraction (`get_file_dependencies`, JSON write, file copy) | Caught by a broad `except Exception`; error is logged at `ERROR` level and processing continues with the next file | The failed file's `file_dependencies.json` and output copy are not produced; it may be retried on the next run due to missing artifacts |
| `OSError` / `UnicodeDecodeError` when reading a file to check if it is empty | Silently suppressed (`pass`) | The file is not added to the empty-files exclusion set; it remains in the processing list and may fail later at extraction time |
| Missing or unreadable `file_dependencies.json` during change detection | Detected via `os.path.exists` check; the file is added to `changed_files` rather than raising an error | The file is conservatively treated as changed and will be re-processed |
| Failures inside `generate_all_docs`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`, `save_consolidated_json` | No handling at the pipeline level; exceptions propagate upward | The pipeline aborts at the failing step; partial results from earlier steps remain on disk |
| LLM-related errors | Delegated entirely to `LLMClient` and `generate_all_docs`; not handled in this file | Handled by the dependency layer; `pipeline.py` is insulated from LLM-specific error types |

## Design Considerations

The per-file `try/except` in `_process_file_dependencies` deliberately uses a broad catch rather than specific exception types. This reflects a pragmatic decision: the variety of reasons a single file might fail (parse errors, encoding issues, OS errors, unexpected data shapes) is open-ended, and the cost of skipping one file is low compared to aborting the entire pipeline run.

Conversely, no equivalent guard is applied to the pipeline-level aggregation steps (steps 3.5–5). This asymmetry implies that individual file failures are considered recoverable operational noise, whereas failures in the consolidation or output phase indicate a more serious structural problem that warrants surfacing to the caller.

The conservative change-detection policy—treating a file with a missing `file_dependencies.json` as changed—acts as an implicit recovery mechanism: if a previous run was interrupted mid-way, the next run will re-process affected files rather than silently skipping them with incomplete state.

## Summary

**codetwine/pipeline.py** is the central orchestrator that transforms a project directory into analyzed outputs. Its sole public function, `process_all_files(project_dir, output_dir, llm_client, max_workers)`, sequences: project-wide dependency graph construction, incremental change detection (SHA-256 hashes), per-file dependency extraction (writing `file_dependencies.json`), optional LLM doc generation, and three consolidated outputs (`project_dependency_summary.json`, `dependency_graph.md`, `project_knowledge.json`). Key data structures include `project_dep_list` (file/callers/callees), `changed_files` (set of modified paths), `symbol_deps` (actual symbol-level usage map), and `summary_map` (per-file LLM summaries).
