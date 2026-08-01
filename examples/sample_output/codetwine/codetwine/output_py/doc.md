# Design Document: codetwine/output.py

# Overview & Purpose

`codetwine/output.py` is the reporting/serialization layer of the pipeline. While per-file analysis (dependency extraction and documentation generation) produces individual `file_dependencies.json` and `doc.json` artifacts scattered across per-file output directories, this module is responsible for **aggregating those scattered per-file results into project-level, human- and tool-consumable outputs**: a consolidated knowledge JSON, a lightweight dependency+summary JSON, and a Mermaid dependency graph. It exists as a separate file to isolate this "collect, normalize path format, and emit final artifacts" concern from both the analysis logic (which produces the raw per-file JSONs) and the low-level path-conversion utilities in `file_utils.py`, which it relies on to translate between source-relative paths, copy-destination paths, and the public `project_name/copy_path` output format.

It is invoked exclusively from `codetwine/pipeline.py`, which drives the overall pipeline and calls these functions in sequence (build symbol-level deps → build summary map → save dependency summary → save Mermaid graph → save consolidated JSON), sharing intermediate results (`symbol_deps`, `summary_map`) across the calls to avoid redundant file reads.

### Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str, rel_path: str` | `str` | Converts a project-relative path into the public `"project_name/copy_path"` output format. |
| `build_summary_map` | `base_output_dir: str, all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` (if present) and maps file relative path to its `summary` (or `None`). |
| `save_consolidated_json` | `base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict, summary_map: dict` | `None` | Merges each file's `file_dependencies.json` and `doc.json`, plus the symbol-level dependency graph and summaries, into a single project-wide JSON file. |
| `build_symbol_level_deps` | `base_output_dir: str, all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Derives actual symbol-usage-based caller/callee relationships per file from each file's `callee_usages`/`caller_usages` in `file_dependencies.json`. |
| `save_dependency_summary` | `base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict, summary_map: dict` | `None` | Writes a lightweight JSON combining symbol-level dependencies and doc summaries for every file (without full per-file dependency/doc detail). |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str, output_path: str, symbol_deps: dict` | `None` | Renders the symbol-level dependency graph as a Mermaid flowchart and writes it to a Markdown file. |

### Design Decisions

- **Single source of dependency computation**: `build_symbol_level_deps` centralizes derivation of caller/callee relationships from raw `callee_usages`/`caller_usages` data so that `save_consolidated_json`, `save_dependency_summary`, and `save_dependency_graph_as_mermaid` all consume the same precomputed `symbol_deps` structure rather than recomputing it, keeping outputs consistent and avoiding redundant file I/O (this sharing is enforced by the caller in `pipeline.py`).
- **Uniform path representation**: All file paths in generated outputs are normalized to the `"project_name/copy_path"` format via `to_output_path`, while internal computations that need to compare/deduplicate raw relative paths (e.g., in `build_symbol_level_deps`) use `output_path_to_rel` to convert back — keeping the public output format consistent while internal set-based logic operates on canonical relative paths.
- **Graceful degradation for missing artifacts**: Functions check `os.path.exists` before reading `doc.json`/`file_dependencies.json`, so files lacking documentation or dependency data are still included with `null`/absent fields rather than causing failures; `save_consolidated_json` explicitly logs a warning and skips files with no analysis results at all.
- **Field de-duplication on merge**: When consolidating, the redundant per-JSON `"file"` field is popped before nesting into the consolidated entry, since the file path is already promoted to the entry's top level — avoiding duplicate/conflicting path representations in the output.
- **Local helper functions for Mermaid generation**: `to_mermaid_node_id` and `to_display_label` are defined as nested functions inside `save_dependency_graph_as_mermaid` since they are only meaningful in the context of rendering that specific graph, keeping them out of the module's public surface.

# Definition Design Specifications

## `to_output_path`

Takes `base_output_dir` (str, the base output directory whose trailing path component is treated as the project name) and `rel_path` (str, a project-relative source file path), and returns a str in the format `"{project_name}/{copy_path}"`.

This function exists to provide a single canonical way of encoding a source-relative path into the externally-visible identifier format used throughout consolidated JSON outputs and the Mermaid graph, so that every downstream artifact references files consistently regardless of where they physically live under `base_output_dir`.

Design decision: the project name is derived from `os.path.basename(base_output_dir)` rather than passed explicitly, keeping the base output directory as the single source of truth for the project identity. The copy-path portion is delegated to `rel_to_copy_path` to guarantee that the encoding is exactly invertible via `output_path_to_rel`/`copy_path_to_rel`.

Constraint: `base_output_dir` must not have a trailing slash that would make `os.path.basename` return an empty string; the function does no validation of this.

## `build_summary_map`

Takes `base_output_dir` (str) and `all_file_list` (list of relative file path strings), and returns a `dict[str, str | None]` mapping each relative file path to its `doc.json` "summary" field, or `None` if no summary is available.

Its responsibility is to centralize the reading of per-file design-document summaries so that multiple output builders (`save_consolidated_json`, `save_dependency_summary`) can share the same summary lookup without re-reading files.

Design decision: missing `doc.json` files (e.g., files skipped by LLM processing or where generation failed) are treated as a normal case rather than an error—they simply map to `None`—since downstream consumers are expected to render a null summary rather than fail.

Edge case: if `doc.json` exists but lacks a `"summary"` key, `.get("summary")` yields `None` as well, which is indistinguishable from a missing file in the returned map.

## `save_consolidated_json`

Takes `base_output_dir` (str), `all_file_list` (list of relative file paths), `output_path` (str, destination for the consolidated JSON), `symbol_deps` (the output of `build_symbol_level_deps`), and `summary_map` (the output of `build_summary_map`). Returns `None`; writes a single consolidated JSON file combining project-wide dependency summaries and per-file analysis artifacts.

This function exists to produce one authoritative "project knowledge" artifact that merges symbol-level dependency data, file-level dependency JSON, and design-document JSON per file, so downstream consumers don't need to stitch together many small per-file files themselves.

Key design decisions:
- All file references in the output (`file`, `callers`, `callees`) are normalized through `to_output_path` so the consolidated JSON is self-contained and independent of the local filesystem layout.
- Per-file `file` fields inside the loaded `file_dependencies.json`/`doc.json` are intentionally dropped and replaced with a single top-level `file` field per entry, since that value is redundant once merged into a unified entry and the top-level field already carries it in output-path form.
- A file is included in `files_list` only if it contributed more than the base `file` key (i.e., at least one of `file_dependencies` or `doc` was found); files with neither are skipped and logged with a warning, so JSON absence for some files doesn't fail the whole run but is still visible.

Edge case/constraint: assumes `symbol_deps` contains an entry for every path in `all_file_list` (accessed via direct indexing `symbol_deps[file_rel]`, which will raise `KeyError` if missing), whereas `summary_map` is accessed defensively via `.get`.

## `build_symbol_level_deps`

Takes `base_output_dir` (str) and `all_file_list` (list of relative file paths), and returns `dict[str, dict[str, set[str]]]`, mapping each relative file path to `{"callers": set[str], "callees": set[str]}` of relative paths (not yet converted to output format).

Its responsibility is to derive dependency edges from *actual symbol usage* recorded in each file's `file_dependencies.json` (via `callee_usages`/`caller_usages`), rather than from coarser import-level dependency declarations, giving a more precise dependency graph.

Design decisions:
- Every file in `all_file_list` is pre-seeded with empty `callers`/`callees` sets so the returned dict always has a complete, uniform key set, allowing safe direct indexing by later stages (e.g., `save_consolidated_json`).
- Results are deduplicated automatically via `set` semantics, since multiple usages can reference the same file.
- Paths recorded in `file_dependencies.json` are stored in output-path format (`project_name/copy_path`) at write time, so they must be converted back to relative form via `output_path_to_rel` before being stored in the returned map, keeping this function's return value in the same relative-path domain as its input `all_file_list`.

Edge case: files without an existing `file_dependencies.json` are silently skipped (left with empty sets), and usages lacking a `from`/`file` key are ignored rather than raising an error.

## `save_dependency_summary`

Takes `base_output_dir` (str), `all_file_list` (list of relative file paths), `output_path` (str), `symbol_deps` (output of `build_symbol_level_deps`), and `summary_map` (output of `build_summary_map`). Returns `None`; writes a lightweight JSON file pairing each file's symbol-level dependencies with its summary.

Exists to provide a smaller, dependency-and-summary-only alternative to the full consolidated JSON, useful when only the dependency graph and doc summaries are needed without the full per-file `file_dependencies`/`doc` payloads.

Design decision: unlike `save_consolidated_json`, this function does not filter out files lacking analysis artifacts—every file in `all_file_list` gets an entry (with `summary: null` and empty caller/callee lists if nothing was found), keeping the output structurally complete and easy to iterate regardless of data availability. Also logs a count of files that actually have a summary, for quick visibility into coverage.

## `save_dependency_graph_as_mermaid`

Takes `base_output_dir` (str), `output_path` (str, destination Markdown file), and `symbol_deps` (output of `build_symbol_level_deps`). Returns `None`; writes a Mermaid `graph LR` flowchart (wrapped in a fenced code block) representing the file-level dependency graph.

Exists to give a human-readable, renderable visualization of the dependency graph derived from the same symbol-level data used for the JSON outputs, so both a machine-readable and a diagram output are generated from a single source of truth.

Design decisions:
- Nodes and edges are collected first into `set`s to deduplicate before rendering, then sorted before emission to produce deterministic, diff-friendly Markdown output.
- Only `callees` edges are used to build edges (not `callers`), since caller/callee are inverse relations of the same edge set and using only one avoids duplicate/inverted arrows.
- Node IDs are derived from the output-path string with `/` and `.` replaced by `_` because Mermaid node identifiers cannot contain slashes or dots; labels shown in the diagram are converted back to a short, human-friendly relative path via `to_display_label` rather than showing the full project-prefixed output path.

## `to_mermaid_node_id`

Nested helper of `save_dependency_graph_as_mermaid`. Takes `path` (str, an output-path formatted string) and returns a str safe for use as a Mermaid node identifier, replacing `/` and `.` with `_`.

Exists solely to satisfy Mermaid's syntactic constraints on node IDs, isolating this formatting concern from the graph-building logic.

Constraint: the transformation is not guaranteed to be collision-free in theory (different paths could map to the same ID if they differ only by translated characters), but this is accepted as a practical simplification.

## `to_display_label`

Nested helper of `save_dependency_graph_as_mermaid`. Takes `path` (str, expected to be in `"project_name/copy_path"` format) and returns a str: the original source-relative path with the project name prefix stripped and the copy-path structure reversed via `copy_path_to_rel`.

Exists to keep the rendered Mermaid diagram readable by showing the original short relative filename/path rather than the internal project-prefixed copy-path encoding.

Edge case: if `path` does not contain a `/` (i.e., cannot be split into a project-name and remainder), it is returned unchanged as a fallback rather than raising an error.

# Dependency Description

### Dependencies (what this file uses)

`output.py` depends entirely on `codetwine/utils/file_utils.py` for path conversion between source-relative paths and their output/copy-destination representations:

- **`rel_to_copy_path`**: Used in `to_output_path` to convert a project-relative file path into its copy-destination directory structure, which is then combined with the project name to form the standardized "project_name/copy_path" output format used throughout consolidated JSON and Mermaid outputs.
- **`resolve_file_output_dir`**: Used in `build_summary_map`, `save_consolidated_json`, and `build_symbol_level_deps` to locate the per-file output directory where `doc.json` and `file_dependencies.json` are stored, so these artifacts can be read and merged.
- **`output_path_to_rel`**: Used in `build_symbol_level_deps` to convert the "project_name/copy_path" formatted `from`/`file` fields found inside `file_dependencies.json` (callee_usages/caller_usages) back into plain relative paths, allowing dependency sets to be built keyed by relative path.
- **`copy_path_to_rel`**: Used in `save_dependency_graph_as_mermaid`'s internal `to_display_label` helper to convert an output-formatted path back to the original source-relative path for use as a human-readable Mermaid node label.

The dependency direction is unidirectional: `output.py` relies on `file_utils.py` for pure path-string transformations, while `file_utils.py` has no knowledge of or dependency on `output.py`.

### Dependents (what uses this file)

All dependent usage originates from `codetwine/pipeline.py`:

- **`to_output_path`**: Used while processing dependency analysis results to convert file paths (including the `from` and `file` fields inside `callee_usages`/`caller_usages`) into the "project_name/copy_path" output format before persisting them.
- **`build_symbol_level_deps`**: Used to compute the shared symbol-level dependency map (callers/callees per file), which is then reused across the subsequent summary, dependency-graph, and consolidated-JSON generation steps.
- **`build_summary_map`**: Used to gather each file's summary from its `doc.json`, producing a map later passed into the dependency-summary and consolidated-JSON generation functions.
- **`save_dependency_summary`**: Used to write the lightweight `project_dependency_summary.json`, combining symbol-level dependencies and summaries.
- **`save_dependency_graph_as_mermaid`**: Used to generate `dependency_graph.md`, a Mermaid flowchart representation of the project's file dependency graph.
- **`save_consolidated_json`**: Used to produce `project_knowledge.json`, the final consolidated output combining dependency info, design documents, and summaries for the whole project.

The dependency direction is unidirectional: `pipeline.py` orchestrates the overall analysis pipeline and calls into `output.py` to generate all output artifacts, while `output.py` has no dependency on `pipeline.py`.

# Data Flow

## Input

| Source | Format | Description |
|---|---|---|
| `base_output_dir` | `str` | Root output directory; its basename is used as `project_name` |
| `all_file_list` | `list[str]` | Relative source file paths to process |
| `{output_dir}/file_dependencies.json` (per file, read from disk) | JSON | Contains `callee_usages` (each with `from`) and `caller_usages` (each with `file`), both already in `project_name/copy_path` output format |
| `{output_dir}/doc.json` (per file, read from disk) | JSON | Contains `summary` and other design-doc fields |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Produced by `build_symbol_level_deps`; passed into other functions |
| `summary_map` | `dict[str, str \| None]` | Produced by `build_summary_map`; passed into other functions |

## Main Transformation Flow

```
all_file_list ──► resolve_file_output_dir ──► read doc.json / file_dependencies.json
                                                       │
                 ┌─────────────────────────────────────┼─────────────────────────┐
                 ▼                                     ▼                         ▼
        build_summary_map                    build_symbol_level_deps    save_consolidated_json
   (file_rel -> summary or None)      (file_rel -> {callers, callees}    (merges deps + doc + graph
                                        as source-relative path sets,      into one JSON, per-file
                                        decoded via output_path_to_rel)    paths converted via
                                                                           to_output_path)
                 │                                     │                         │
                 └──────────────┬──────────────────────┘                         │
                                ▼                                                ▼
                   save_dependency_summary                          project_knowledge.json
              (lightweight file+summary+deps JSON,                  (file, project_dependencies,
               paths converted via to_output_path)                   files list with merged docs)
                                │
                                ▼
                 save_dependency_graph_as_mermaid
          (node/edge sets built from symbol_deps, paths
           converted to output format, then to Mermaid IDs
           via to_mermaid_node_id / labels via to_display_label)
```

Key conversions:
- **Encoding for output**: `to_output_path` = `project_name` + `rel_to_copy_path(rel_path)` — used whenever paths are written into consolidated JSON output.
- **Decoding from stored dependency JSON**: `output_path_to_rel` — used in `build_symbol_level_deps` to normalize `from`/`file` fields (already in output format) back to relative paths for internal set storage.
- **Mermaid rendering**: output-format paths are turned into safe node IDs (`to_mermaid_node_id`, replacing `/` and `.` with `_`) and human-readable labels (`to_display_label`, stripping the project prefix and restoring original relative path via `copy_path_to_rel`).

## Output

| Function | Destination | Format |
|---|---|---|
| `build_summary_map` | in-memory dict | `{file_rel: summary or None}` |
| `build_symbol_level_deps` | in-memory dict | `{file_rel: {"callers": set[str], "callees": set[str]}}` (relative paths) |
| `save_consolidated_json` | `output_path` (JSON file, e.g. `project_knowledge.json`) | `{"project_name": str, "project_dependencies": [{"file", "summary", "callers", "callees"}], "files": [{"file", "file_dependencies"?, "doc"?}]}` |
| `save_dependency_summary` | `output_path` (JSON file, e.g. `project_dependency_summary.json`) | `{"project_name": str, "files": [{"file", "summary", "callers", "callees"}]}` |
| `save_dependency_graph_as_mermaid` | `output_path` (Markdown file, e.g. `dependency_graph.md`) | Mermaid `graph LR` fenced code block with node declarations and `-->` edges |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `symbol_deps` entry | `callers: set[str]`, `callees: set[str]` | Symbol-level dependency edges per file, keyed by relative path |
| `converted_deps` entry (consolidated JSON) | `file`, `summary`, `callers` (sorted list), `callees` (sorted list) | Project-wide dependency summary per file, output-path formatted |
| `files_list` entry (consolidated JSON) | `file`, `file_dependencies?` (raw JSON minus `file` key), `doc?` (raw JSON minus `file` key) | Merges per-file artifacts into a single record; omitted if neither exists (logs warning) |
| `files_list` entry (dependency summary JSON) | `file`, `summary`, `callers`, `callees` | Lightweight per-file dependency+summary record |
| `node_set` / `edge_set` (Mermaid) | `set[str]` / `set[tuple(src, dst)]` | Deduplicated graph nodes and directed edges before rendering |

# Error Handling

This module follows a **graceful degradation** strategy: missing or incomplete per-file artifacts (`file_dependencies.json`, `doc.json`) are treated as expected, non-fatal conditions rather than errors. Instead of raising exceptions, the code checks for file existence before reading and substitutes safe defaults (`None`, empty sets/lists) when artifacts are absent, allowing aggregation over the whole project to proceed even if some files lack analysis results. The only explicit signal for a missing/incomplete case is a `logger.warning` in `save_consolidated_json`; there is no retry, recovery, or fallback-computation logic anywhere in the file. JSON parsing, path conversions, and file I/O are not wrapped in try/except, so malformed JSON, permission issues, or disk errors during read/write propagate as unhandled exceptions and abort the current build step (fail-fast for genuinely unexpected I/O/parsing failures).

| Error type | Handling | Impact |
|---|---|---|
| Missing `doc.json` for a file | Checked via `os.path.exists`; skipped, `summary` set to `None` | File still included in output with a null summary; no interruption |
| Missing `file_dependencies.json` for a file | Checked via `os.path.exists`; skipped, entry key omitted / callers-callees stay empty sets | File still processed with partial data; no interruption |
| A file with neither `doc.json` nor `file_dependencies.json` | Detected by entry having no additional keys beyond `"file"`; excluded from `files_list`, logged via `logger.warning` | File is silently dropped from `project_knowledge.json`; total/consolidated counts reflect the gap in the final log message |
| Malformed/corrupt JSON in `doc.json` or `file_dependencies.json` | Not caught; `json.load` raises `json.JSONDecodeError` | Propagates up and aborts the current save/build function (fail-fast) |
| Missing key in `symbol_deps` for a file in `all_file_list` (e.g. inconsistent input lists) | Not caught; raises `KeyError` on dict access | Propagates up and aborts processing (fail-fast) |
| File write failures (`output_path`, disk/permission issues) | Not caught | Propagates as `OSError`/`IOError`, aborting the save operation |

**Design considerations:**
- The distinction between "missing artifact" (tolerated) and "malformed artifact" (fatal) reflects an assumption that upstream steps (dependency extraction, doc generation) may legitimately not produce output for every file (e.g., LLM failure, non-code file), but once a file is present it is expected to be well-formed JSON — corruption is treated as an unexpected condition worth failing on rather than silently ignoring.
- Aggregation functions (`build_summary_map`, `build_symbol_level_deps`) are designed to always return a complete map keyed by `all_file_list`, ensuring downstream consumers (`save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`) can rely on lookups succeeding via `.get()` or pre-initialized dict entries, reducing the need for additional error handling in later stages.
- Logging is used only for informational/diagnostic purposes (`logger.warning` for missing analysis results, `logger.info` for completion summaries with counts), giving visibility into partial results without halting the overall pipeline.

# Summary

`codetwine/output.py` aggregates scattered per-file `file_dependencies.json`/`doc.json` artifacts into project-level outputs: consolidated JSON (`project_knowledge.json`), a lightweight dependency+summary JSON, and a Mermaid dependency graph (`.md`). Key functions: `to_output_path` (path normalization), `build_summary_map`, `build_symbol_level_deps` (symbol-level callers/callees), `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`. Depends on `file_utils.py` for path conversions; used only by `pipeline.py`. Uses graceful degradation for missing artifacts, fail-fast on malformed JSON.
