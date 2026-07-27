# Design Document: codetwine/output.py

# Overview & Purpose

`codetwine/output.py` is the final-stage reporting module of the codetwine pipeline. After per-file analysis (dependency extraction and documentation generation) has produced individual `file_dependencies.json` and `doc.json` artifacts under each file's own output directory, this module is responsible for aggregating those scattered per-file artifacts into project-wide, human- and machine-consumable outputs: a consolidated JSON knowledge base, a lightweight dependency+summary JSON, and a Mermaid dependency graph in Markdown. It exists as a separate file to isolate the "aggregation/reporting" concern from the per-file analysis logic (handled elsewhere in the pipeline) and from low-level path manipulation (delegated to `codetwine/utils/file_utils.py`). All path values in its outputs are normalized to a uniform `"project_name/copy_path"` public path format, decoupling downstream consumers from the internal copy-directory layout used during per-file processing.

## Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str, rel_path: str` | `str` | Converts a project-relative path into the public `"project_name/copy_path"` format. |
| `build_summary_map` | `base_output_dir: str, all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` (if present) and builds a map from relative path to its summary text (or `None`). |
| `save_consolidated_json` | `base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict, summary_map: dict` | `None` | Merges each file's dependency info, doc, and summary into one `project_knowledge.json`, including project-wide `project_dependencies` and per-file `files` sections; logs a warning for files with no analysis results. |
| `build_symbol_level_deps` | `base_output_dir: str, all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Derives actual symbol-usage-based callers/callees per file from `callee_usages`/`caller_usages` in each `file_dependencies.json`. |
| `save_dependency_summary` | `base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict, summary_map: dict` | `None` | Writes a lightweight JSON (`project_dependency_summary.json`) combining symbol-level dependencies and doc summaries per file. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str, output_path: str, symbol_deps: dict` | `None` | Renders the symbol-level dependency graph as a Mermaid `graph LR` flowchart and writes it to a Markdown file. |

## Design Notes

- **Separation of raw vs. symbol-level dependencies**: `build_symbol_level_deps` recomputes dependency edges strictly from actual symbol usages (`callee_usages`/`caller_usages`) rather than raw import-level data, producing a more precise, deduplicated (via `set`) dependency graph shared across the three downstream output functions (consolidated JSON, dependency summary, Mermaid graph) — computed once in the caller (`pipeline.py`) and passed in, avoiding redundant file I/O.
- **Path normalization boundary**: All outward-facing artifacts use `to_output_path` to present paths in the stable `"project_name/copy_path"` format, while internal file lookups use `resolve_file_output_dir` on raw relative paths — cleanly separating internal storage layout from external presentation format, with `output_path_to_rel`/`copy_path_to_rel` used for the inverse conversion when needed.
- **Graceful degradation for missing artifacts**: Functions tolerate missing `doc.json` (summary becomes `None`) and missing `file_dependencies.json` (dependency section omitted, with a logged warning in `save_consolidated_json`), ensuring partial analysis failures don't abort aggregation.
- **Mermaid ID/label conversion**: Local helper functions (`to_mermaid_node_id`, `to_display_label`) inside `save_dependency_graph_as_mermaid` handle Mermaid-safe identifier generation and restoring human-readable labels from the output path format, keeping this Mermaid-specific logic encapsulated rather than polluting the public API.

# Definition Design Specifications

## `to_output_path(base_output_dir: str, rel_path: str) -> str`

- **Arguments**: `base_output_dir` — base output directory whose last path component is treated as the project name; `rel_path` — file path relative to the project root.
- **Return value**: A string in `"project_name/copy_path"` format.
- **Responsibility**: Provides a single canonical way to express any project file path in the consolidated output artifacts (JSON files, Mermaid graph), namespacing the copy-destination path with the project name so multiple projects' outputs don't collide when referenced externally.
- **Design decision**: Reuses `rel_to_copy_path` rather than the raw relative path, keeping output paths consistent with the on-disk copy-destination structure used elsewhere in the pipeline. This is the inverse operation of `output_path_to_rel`.
- **Constraints**: Assumes `base_output_dir` is non-empty so `os.path.basename` yields a meaningful project name.

## `build_summary_map(base_output_dir: str, all_file_list: list[str]) -> dict[str, str | None]`

- **Arguments**: `base_output_dir` — base output directory; `all_file_list` — relative paths of files to analyze.
- **Return value**: Dict mapping each file's relative path to its `doc.json` summary string, or `None` if unavailable.
- **Responsibility**: Centralizes reading of per-file generated documentation summaries so downstream output-generation functions (consolidated JSON, dependency summary) can attach summaries without re-reading files repeatedly.
- **Design decision**: Missing `doc.json` (file not yet documented, or LLM generation skipped/failed) is treated as a normal case rather than an error — the summary is simply `None`, ensuring the map always has an entry for every input file.
- **Edge cases**: If `doc.json` exists but lacks a `"summary"` key, `None` is used via `.get`.

## `save_consolidated_json(base_output_dir, all_file_list, output_path, symbol_deps, summary_map) -> None`

- **Arguments**: `base_output_dir` — base output directory; `all_file_list` — relative paths of analyzed files; `output_path` — destination path for the combined JSON; `symbol_deps` — output of `build_symbol_level_deps`; `summary_map` — output of `build_summary_map`.
- **Return value**: `None` (writes JSON to disk).
- **Responsibility**: Produces the single "source of truth" JSON artifact (`project_knowledge.json`) that merges per-file dependency data, generated documentation, and the symbol-level dependency graph with summaries, so consumers don't need to traverse the per-file directory structure.
- **Design decisions**:
  - All paths in the output are normalized to the `"project_name/copy_path"` format via `to_output_path`, ensuring consistency with `project_dependencies` entries and with other generated artifacts.
  - The `"file"` field is deduplicated: it's set once at the entry's top level and stripped from the nested `file_dependencies`/`doc` payloads to avoid redundant/conflicting values.
  - A file is only included in `files_list` if it contributed at least one of `file_dependencies` or `doc` (i.e., `len(entry) > 1`); otherwise a warning is logged, since a file with no analysis output is not actionable for consumers.
  - `project_dependencies` entries are built independently from `symbol_deps` (not from the per-file JSON walk) to guarantee every file in `all_file_list` has a dependency entry regardless of whether analysis artifacts exist.
- **Edge cases/constraints**: Assumes `symbol_deps` contains an entry for every `file_rel` in `all_file_list` (it will `KeyError` otherwise, since it uses direct indexing rather than `.get`). Handles missing `file_dependencies.json`/`doc.json` per file gracefully by simply omitting those keys from the entry.

## `build_symbol_level_deps(base_output_dir: str, all_file_list: list[str]) -> dict[str, dict[str, set[str]]]`

- **Arguments**: `base_output_dir` — base output directory; `all_file_list` — relative paths of files to analyze.
- **Return value**: Dict mapping each file's relative path to `{"callers": set[str], "callees": set[str]}`, where the sets contain relative paths (already restored via `output_path_to_rel`) of other project files.
- **Responsibility**: Derives a project-wide, symbol-usage-based dependency graph (as opposed to coarser import-level dependencies) by aggregating each file's `file_dependencies.json`, giving a more accurate picture of which files actually use symbols from which other files.
- **Design decisions**:
  - Every file in `all_file_list` is pre-initialized with empty caller/callee sets so the returned map is total over the input list, letting callers safely index it directly (as `save_consolidated_json` and `save_dependency_summary` do) without existence checks.
  - `callees` are derived from `callee_usages[].from` (files this file depends on) and `callers` from `caller_usages[].file` (files that depend on this file) — these come from two distinct fields in the per-file dependency JSON reflecting the two/directional relationship.
  - Values in `file_dependencies.json` are already in `"project_name/copy_path"` output format (converted upstream, per `pipeline.py`), so `output_path_to_rel` is applied to convert them back to plain relative paths for internal use as dict keys.
  - Uses sets (not lists) to naturally deduplicate multiple usages referencing the same file.
- **Edge cases**: Files without an existing `file_dependencies.json` are skipped (left with empty caller/callee sets, not an error). Usage entries missing the `"from"`/`"file"` key are ignored.

## `save_dependency_summary(base_output_dir, all_file_list, output_path, symbol_deps, summary_map) -> None`

- **Arguments**: Same shapes as `save_consolidated_json`'s corresponding parameters.
- **Return value**: `None` (writes JSON to disk).
- **Responsibility**: Produces a lighter-weight companion artifact (`project_dependency_summary.json`) exposing only the dependency graph plus doc summaries, without the full per-file dependency/documentation payloads that `save_consolidated_json` includes — intended for quick consumption of "what depends on what" plus a one-line description.
- **Design decision**: Unlike `save_consolidated_json`, every file in `all_file_list` is unconditionally included in `files_list` (no filtering/warning for missing analysis), reflecting that this artifact's purpose is a complete graph rather than a report of fully-analyzed files; files without a summary simply carry `null`.
- **Constraints**: Assumes `symbol_deps` has an entry for every file in `all_file_list` (direct indexing, no `.get`).

## `save_dependency_graph_as_mermaid(base_output_dir: str, output_path: str, symbol_deps: dict[str, dict[str, set[str]]]) -> None`

- **Arguments**: `base_output_dir` — base output directory (used to build display-format paths); `output_path` — destination path for the Markdown file; `symbol_deps` — output of `build_symbol_level_deps`.
- **Return value**: `None` (writes a Markdown file containing a fenced Mermaid `graph LR` block).
- **Responsibility**: Renders the symbol-level dependency graph as a human-readable Mermaid flowchart for visualization/documentation purposes.
- **Design decisions**:
  - Nodes and edges are collected into sets first (`node_set`, `edge_set`) to deduplicate before rendering, since multiple files could reference the same node/edge combination.
  - Callee nodes are added dynamically while iterating `symbol_deps.items()` (rather than being restricted to `symbol_deps`'s own keys), so any dependency target file is represented as a node even if it wasn't itself part of the analyzed file list's callee/caller computation loop — this keeps the graph complete relative to referenced files.
  - Only `callees` edges are drawn (not `callers`), since caller/callee is a symmetric relationship — drawing callees alone avoids duplicate/inverse edges.
  - Node IDs are derived from the output path with `/` and `.` replaced by `_`, since Mermaid node IDs cannot contain those characters; labels instead show the short original relative filename (via `to_display_label`) reconstructed by stripping the project-name prefix and undoing the copy-path transformation, for readability.
  - Output is sorted (`sorted(node_set)`, `sorted(edge_set)`) to produce deterministic, diff-friendly file content across runs.
- **Nested helper `to_mermaid_node_id(path: str) -> str`**: Pure string-sanitization helper; exists solely to satisfy Mermaid's node ID syntax constraints.
- **Nested helper `to_display_label(path: str) -> str`**: Converts an output-format path back to a source-relative path for a readable node label; falls back to returning the input unchanged if it doesn't contain the expected `"/"`-separated project-name prefix (defensive fallback for malformed input).

# Dependency Description

### Dependencies (what this file uses)

`output.py` depends entirely on `codetwine/utils/file_utils.py` for path conversion between source-relative paths and the project's copy-destination/output path conventions:

- **`rel_to_copy_path`**: Used in `to_output_path` to convert a file's relative path into the copy-destination directory structure path, which is then combined with the project name to build the unified "project_name/copy_path" output format used throughout the consolidated JSON and dependency outputs.
- **`copy_path_to_rel`**: Used in `save_dependency_graph_as_mermaid`'s internal `to_display_label` helper to convert a "project_name/copy_path" formatted node path back into a readable source-relative path for display as a Mermaid node label.
- **`output_path_to_rel`**: Used in `build_symbol_level_deps` to convert the `from`/`file` fields recorded in each file's `file_dependencies.json` (which are stored in output-path format) back into source-relative paths, so dependency maps are keyed consistently by relative path.
- **`resolve_file_output_dir`**: Used across `build_summary_map`, `save_consolidated_json`, and `build_symbol_level_deps` to locate the per-file output directory (containing `doc.json` and `file_dependencies.json`) from a given base output directory and file relative path.

### Dependents (what uses this file)

Only `codetwine/pipeline.py` is identified as a dependent, and the relationship is unidirectional (pipeline.py depends on output.py, not vice versa):

- **`to_output_path`**: Used by pipeline.py to convert file paths recorded inside dependency analysis results (`file`, `callee_usages[].from`, `caller_usages[].file`) into the unified "project_name/copy_path" format before persisting them.
- **`build_symbol_level_deps`**: Used to compute a shared symbol-level dependency map (callers/callees per file) that is reused across subsequent output-generation steps.
- **`build_summary_map`**: Used to gather each file's design-document summary (or `None` if absent) for inclusion in downstream outputs.
- **`save_dependency_summary`**: Used to write the lightweight dependency-plus-summary JSON output file.
- **`save_dependency_graph_as_mermaid`**: Used to generate the Mermaid-format dependency graph markdown file.
- **`save_consolidated_json`**: Used to produce the final consolidated project knowledge JSON combining dependencies, summaries, and per-file documentation.

# Data Flow

## Input Data
- **`base_output_dir`** (str): Root output directory; its basename is used as `project_name`.
- **`all_file_list`** (list[str]): Project-relative file paths to process.
- **Per-file JSON artifacts on disk** (read via `resolve_file_output_dir`):
  - `file_dependencies.json` — contains `callee_usages` (list of usages with a `from` field) and `caller_usages` (list of usages with a `file` field), plus other dependency metadata.
  - `doc.json` — contains a `summary` field (and possibly other design-doc fields).
- **`symbol_deps`** (dict, produced by `build_symbol_level_deps`): `{file_rel: {"callers": set[str], "callees": set[str]}}`.
- **`summary_map`** (dict, produced by `build_summary_map`): `{file_rel: str | None}`.

## Transformation Flow

```
all_file_list ──┬─► build_symbol_level_deps ──► symbol_deps (raw rel paths)
                └─► build_summary_map        ──► summary_map (rel_path -> summary|None)

symbol_deps + summary_map + all_file_list ──► save_consolidated_json / save_dependency_summary
   - path conversion via to_output_path (rel_path -> "project_name/copy_path")
   - merge with file_dependencies.json / doc.json contents (for consolidated JSON)
   - write JSON to disk

symbol_deps ──► save_dependency_graph_as_mermaid
   - convert paths to output format
   - build node/edge sets
   - convert to Mermaid node IDs and display labels
   - write Markdown (mermaid code block)
```

Key steps:
1. **`build_symbol_level_deps`**: For each file, reads `file_dependencies.json`; extracts callee files from `callee_usages[].from` and caller files from `caller_usages[].file`, converting each via `output_path_to_rel` back to relative paths, aggregating into per-file `callers`/`callees` sets.
2. **`build_summary_map`**: For each file, reads `doc.json` (if present) and extracts `summary`; missing files map to `None`.
3. **`to_output_path`**: Central path-formatting helper — combines `project_name` (from `base_output_dir`) with `rel_to_copy_path(rel_path)` to produce the canonical `"project_name/copy_path"` output format used throughout all output artifacts.
4. **`save_consolidated_json`**: Builds `project_dependencies` entries (file, summary, sorted callers/callees in output-path format) from `symbol_deps`/`summary_map`; separately builds `files` entries per file by loading and merging `file_dependencies.json` (minus its own `file` field) and `doc.json` (minus its own `file` field) under one `entry` dict keyed by the unified `file` field; skips files with no data found, logging a warning.
5. **`save_dependency_summary`**: Simpler variant — for each file, emits a flat record combining `file`, `summary`, sorted `callers`/`callees` (all in output-path format); no merging of raw JSON docs.
6. **`save_dependency_graph_as_mermaid`**: Converts `symbol_deps` into a graph: collects unique nodes (source + callee paths in output format) and edges (source→callee pairs); node IDs are output paths with `/` and `.` replaced by `_`; node labels are the original source-relative path (recovered via `copy_path_to_rel` after stripping the project-name prefix); emits sorted Mermaid `graph LR` syntax.

## Output Data

| Function | Output | Format |
|---|---|---|
| `save_consolidated_json` | `output_path` (e.g. `project_knowledge.json`) | `{"project_name": str, "project_dependencies": [{"file","summary","callers","callees"}], "files": [{"file","file_dependencies"?,"doc"?}]}` |
| `save_dependency_summary` | `output_path` (e.g. `project_dependency_summary.json`) | `{"project_name": str, "files": [{"file","summary","callers","callees"}]}` |
| `save_dependency_graph_as_mermaid` | `output_path` (e.g. `dependency_graph.md`) | Markdown file with a fenced ```mermaid graph LR``` block: node declarations `id["label"]` and edge lines `src --> dst` |

All outputs are written as UTF-8 files (JSON via `json.dump`, Mermaid via plain text write); `save_consolidated_json`/`save_dependency_summary` also emit a log message summarizing counts.

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `symbol_deps` (`dict[str, dict[str, set[str]]]`) | `{file_rel: {"callers": set[rel_path], "callees": set[rel_path]}}` | Central symbol-level dependency graph shared across all three output functions |
| `summary_map` (`dict[str, str \| None]`) | `{file_rel: summary_text_or_None}` | Attaches doc summaries to dependency entries; `None` if no `doc.json` |
| `converted_deps` (list[dict]) | `file`, `summary`, `callers` (sorted output-paths), `callees` (sorted output-paths) | Project-wide dependency section of consolidated JSON |
| `entry` (dict, per file) | `file`, optional `file_dependencies` (raw contents minus `file` key), optional `doc` (raw contents minus `file` key) | Per-file combined analysis result in consolidated JSON; omitted (with warning) if empty besides `file` |
| `node_set` / `edge_set` | `set[str]` of output-format paths / `set[tuple(str,str)]` of (src, dst) output-format path pairs | Deduplicated graph elements for Mermaid rendering |

# Error Handling

This module follows a **graceful degradation** strategy for missing per-file analysis artifacts, while relying on Python's default fail-fast behavior (unhandled exceptions propagate) for structural or unexpected errors such as malformed JSON, missing dictionary keys, or I/O failures on writes.

Missing intermediate files (`doc.json`, `file_dependencies.json`) are treated as an expected, normal condition — a file may legitimately lack analysis output (e.g., no LLM was used, generation failed, or dependency extraction was skipped) — so their absence is checked explicitly via `os.path.exists` and handled by substituting `None`/empty defaults rather than raising. Any other failure (e.g., a file that exists but contains invalid JSON, or `symbol_deps` missing an expected key) is not caught and will propagate as an exception, stopping execution.

| Error Pattern | Handling | Impact |
|---|---|---|
| `doc.json` does not exist for a file | Checked via `os.path.exists`; `summary` is set to `None` | File proceeds without a summary; no exception raised |
| `file_dependencies.json` does not exist for a file | Checked via `os.path.exists`; dependency section is simply omitted / left empty | File's callers/callees remain empty sets; no exception raised |
| A file has neither `doc.json` nor `file_dependencies.json` (in `save_consolidated_json`) | Entry is excluded from `files_list`; a warning is logged via `logger.warning` | File is dropped from the consolidated JSON output but does not stop processing of other files |
| Malformed/corrupt JSON in an existing `doc.json` or `file_dependencies.json` | Not caught; `json.load` raises and propagates | Entire consolidation/summary/graph generation aborts (fail-fast) |
| Missing entry in `symbol_deps` for a file in `all_file_list` | Not caught; `KeyError` propagates from direct dict indexing | Processing aborts immediately |
| Output file write failures (`open(..., "w")`, `json.dump`) | Not caught; exception propagates | Function aborts without producing partial/corrupt output silently |

**Design considerations:**
- The distinction between "file has no analysis output" (expected, tolerated) and "file has analysis output but it's malformed" (unexpected, fatal) is deliberate: existence checks (`os.path.exists`) guard only against the former, leaving JSON parsing and key-access errors unguarded so they surface loudly.
- Logging is used only for the tolerated case (`build_summary_map`'s implicit `None` and `save_consolidated_json`'s explicit warning for fully-missing files), giving visibility into partial results without halting the batch of `all_file_list`.
- Aggregate statistics logged at the end of `save_consolidated_json` and `save_dependency_summary` (e.g., `files: X/Y`, `with summary: N`) serve as a post-hoc signal for how many files lacked analysis artifacts, complementing the per-file warnings.

# Summary

`codetwine/output.py` aggregates per-file `file_dependencies.json`/`doc.json` artifacts into project-wide outputs: `project_knowledge.json`, `project_dependency_summary.json`, and a Mermaid dependency graph. Key functions: `to_output_path` (normalizes paths to "project_name/copy_path"), `build_summary_map`, `build_symbol_level_deps` (symbol-level callers/callees), `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`. Uses `symbol_deps`/`summary_map` as shared structures; tolerates missing artifacts, fails fast on malformed data. Depends on `file_utils.py`; used only by `pipeline.py`.
