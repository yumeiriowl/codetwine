# Design Document: codetwine/output.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`output.py` is the final-stage output module of the codetwine pipeline. It is responsible for aggregating per-file analysis artifacts (dependency JSON files and design document JSON files produced by earlier pipeline stages) into project-level output artifacts. It exists as a separate file to isolate all output-serialization and path-conversion concerns from analysis logic, providing a clean boundary between "analysis" and "reporting."

Concretely, this module:
- Converts internal relative file paths to the canonical `"project_name/copy_path"` output-path format used across all emitted JSON and Markdown files.
- Reads per-file `file_dependencies.json` and `doc.json` artifacts from the file output directories resolved via `file_utils`.
- Assembles and writes three distinct project-level output files: a lightweight dependency-summary JSON, a full consolidated knowledge JSON, and a Mermaid flowchart Markdown file.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative file path to `"project_name/copy_path"` format for use in all emitted output files. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` and returns a mapping of relative path → summary string (or `None` if absent). |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Reads each file's `file_dependencies.json` and builds a map of relative path → `{"callers": set, "callees": set}` derived from actual symbol-usage records. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict`, `summary_map: dict` | `None` | Writes a lightweight JSON combining symbol-level dependencies and summaries for every file in the project. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict`, `summary_map: dict` | `None` | Writes a full consolidated JSON (`project_knowledge.json`) merging per-file `file_dependencies.json`, `doc.json`, and the project-level dependency graph into a single file. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict` | `None` | Generates a Mermaid `graph LR` flowchart from the symbol-level callee dependency edges and writes it as a Markdown file. |

---

## Design Decisions

- **Separation of data-building from data-writing.** `build_symbol_level_deps` and `build_summary_map` are pure data-assembly functions that return plain dicts. The three `save_*` functions consume those results. This allows the pipeline (`pipeline.py`) to build the shared data once and pass it to all three output functions, avoiding redundant file reads.

- **Canonical path format.** All paths stored in emitted JSON and Markdown use the `"project_name/copy_path"` format, enforced through `to_output_path` (which delegates to `rel_to_copy_path` from `file_utils`). Reconstruction of relative paths from stored output paths is handled by `output_path_to_rel` and `copy_path_to_rel` from the same utility module.

- **Graceful absence handling.** All functions that read per-file artifacts (`doc.json`, `file_dependencies.json`) use `os.path.exists` guards before opening files and treat missing artifacts as `None` or empty rather than raising errors, ensuring partial pipeline runs still produce valid output.

- **Deduplication via sets.** `build_symbol_level_deps` accumulates callers and callees in `set` objects to naturally deduplicate repeated symbol-usage records before the sets are sorted at serialization time in the `save_*` functions.

## Definition Design Specifications

# Definition Design Specifications

---

## `to_output_path(base_output_dir: str, rel_path: str) -> str`

Converts a project-relative file path into the canonical `"project_name/copy_path"` format used throughout all output artifacts.

- **`base_output_dir`**: Absolute or relative path to the base output directory; its trailing component is treated as the project name.
- **`rel_path`**: Project-relative path to the source file.
- **Returns**: A string of the form `"<project_name>/<copy_path>"`, where `copy_path` is the collision-safe copy-destination path produced by `rel_to_copy_path`.

**Design intent**: Centralizes the path-format conversion so all output-writing functions produce consistent identifiers. The project name is always derived from `base_output_dir`'s basename rather than being passed explicitly, keeping callsites simple.

---

## `build_summary_map(base_output_dir: str, all_file_list: list[str]) -> dict[str, str | None]`

Reads the `"summary"` field from each file's `doc.json` and returns a complete mapping covering every file in `all_file_list`.

- **`base_output_dir`**: Base output directory used to resolve per-file output subdirectories.
- **`all_file_list`**: Full list of project-relative source file paths to include.
- **Returns**: A `dict` keyed by project-relative path; the value is the summary string if `doc.json` exists and contains a `"summary"` key, otherwise `None`.

**Design intent**: Provides a single pre-built lookup that downstream functions (`save_dependency_summary`, `save_consolidated_json`) can share without re-reading `doc.json` multiple times.

**Edge cases**: Files with no `doc.json`, or whose `doc.json` lacks a `"summary"` key, map to `None`. No error is raised for missing files.

---

## `save_consolidated_json(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

Merges per-file `file_dependencies.json` and `doc.json` artifacts together with the symbol-level dependency graph into a single `project_knowledge.json`-style file.

- **`base_output_dir`**: Base output directory.
- **`all_file_list`**: Ordered list of project-relative paths to include.
- **`output_path`**: Filesystem path where the consolidated JSON is written.
- **`symbol_deps`**: Return value of `build_symbol_level_deps`; supplies caller/callee sets per file.
- **`summary_map`**: Return value of `build_summary_map`; supplies per-file summary text.
- **Returns**: `None`; side effect is writing the JSON file.

**Design intent**: Produces a single artifact that a consumer can use to understand the whole project without accessing individual per-file output directories.

**Output structure**: The written JSON has three top-level keys: `"project_name"`, `"project_dependencies"` (an array with caller/callee/summary per file), and `"files"` (an array with the full content of each file's `file_dependencies.json` and `doc.json`, minus their redundant `"file"` fields).

**Edge cases**: A file entry is included in `"files"` only if at least one of `file_dependencies.json` or `doc.json` exists; otherwise a warning is logged and the entry is omitted. The `"file"` key is stripped from the individual JSON payloads to avoid duplication with the top-level `"file"` field. All paths in the output use the `"project_name/copy_path"` format. `callers` and `callees` arrays are sorted for deterministic output.

**Preconditions**: Every key in `all_file_list` must exist in both `symbol_deps` and `summary_map`.

---

## `build_symbol_level_deps(base_output_dir: str, all_file_list: list[str]) -> dict[str, dict[str, set[str]]]`

Constructs symbol-level caller/callee relationships by reading the `"callee_usages"` and `"caller_usages"` arrays from each file's `file_dependencies.json`.

- **`base_output_dir`**: Base output directory used to locate per-file `file_dependencies.json`.
- **`all_file_list`**: Full list of project-relative source file paths.
- **Returns**: A `dict` keyed by project-relative path; each value is `{"callers": set[str], "callees": set[str]}` where set members are project-relative paths converted via `output_path_to_rel`.

**Design intent**: Produces a dependency graph based on actual symbol usage rather than module-level imports, which is more precise for understanding real call relationships. The result is computed once and shared across `save_dependency_summary`, `save_consolidated_json`, and `save_dependency_graph_as_mermaid`.

**Design decisions**: `callee` files are sourced from `callee_usages[*].from`; `caller` files are sourced from `caller_usages[*].file`. Using `set` for both collections ensures duplicate relationships are deduplicated automatically. Files with no `file_dependencies.json` retain empty sets rather than being absent from the map.

**Edge cases**: All files in `all_file_list` are guaranteed an entry in the returned dict even if their `file_dependencies.json` is missing. Paths stored in `file_dependencies.json` are already in `"project_name/copy_path"` format and are converted back to relative paths via `output_path_to_rel`.

---

## `save_dependency_summary(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

Writes a lightweight JSON file combining symbol-level caller/callee dependencies with per-file summaries, intentionally omitting the full `file_dependencies.json` and `doc.json` content.

- **`base_output_dir`**: Base output directory.
- **`all_file_list`**: Ordered list of project-relative paths.
- **`output_path`**: Filesystem path for the output file.
- **`symbol_deps`**: Return value of `build_symbol_level_deps`.
- **`summary_map`**: Return value of `build_summary_map`.
- **Returns**: `None`; side effect is writing the JSON file.

**Design intent**: Provides a compact, human-readable artifact for quickly surveying project structure without the verbosity of the consolidated JSON. Files without a summary are included with `null` rather than being excluded, preserving complete dependency coverage.

**Output structure**: Top-level keys are `"project_name"` and `"files"`. Each entry in `"files"` contains `"file"` (output-path format), `"summary"` (string or `null`), `"callers"` (sorted list), and `"callees"` (sorted list).

**Preconditions**: Every key in `all_file_list` must exist in both `symbol_deps` and `summary_map`.

---

## `save_dependency_graph_as_mermaid(base_output_dir: str, output_path: str, symbol_deps: dict[str, dict[str, set[str]]]) -> None`

Generates a Mermaid `graph LR` flowchart from the symbol-level dependency graph and writes it as a Markdown code block.

- **`base_output_dir`**: Base output directory; its basename is used as the project name when constructing output-format paths.
- **`output_path`**: Filesystem path for the output `.md` file.
- **`symbol_deps`**: Return value of `build_symbol_level_deps`.
- **Returns**: `None`; side effect is writing the Markdown file.

**Design intent**: Produces a directly renderable Mermaid diagram so project consumers can visualize the dependency graph without additional tooling.

**Design decisions**: Node IDs are derived by replacing `/` and `.` with `_` in the output-format path to satisfy Mermaid's identifier syntax. Display labels show the human-readable source-relative path (restored via `copy_path_to_rel`) rather than the internal output-format path. Both nodes and edges are sorted before emission to ensure deterministic, diff-friendly output. Nodes are collected from both the source file set and callee targets, so files referenced only as callees still appear as nodes.

**Edge cases**: Only callee edges are used to build the graph; callers are not traversed again since the same edges are captured from the callee side. Files with no callees appear as isolated nodes.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file depends on `codetwine/utils/file_utils.py` for all file-path transformation operations:

- **`rel_to_copy_path`**: Used to convert project-relative file paths into the copy-destination directory structure format (`{stem}_{ext}/{filename}`). This is needed when constructing the `"project_name/copy_path"` output path format used throughout the consolidated JSON and Mermaid outputs.
- **`copy_path_to_rel`**: Used in the Mermaid graph generation to convert a copy-destination path back into a human-readable source-relative path for display labels on graph nodes.
- **`output_path_to_rel`**: Used when parsing `file_dependencies.json` files to convert stored output-format paths (e.g., `"project_name/copy_path"`) back into project-relative paths for building the symbol-level dependency map.
- **`resolve_file_output_dir`**: Used to locate the output directory for each analyzed file, enabling loading of per-file artifacts (`file_dependencies.json` and `doc.json`) during dependency graph construction and consolidated JSON generation.

### Dependents (what uses this file)

**`codetwine/pipeline.py`** is the sole dependent, using this file as the output/aggregation layer of the analysis pipeline:

- It calls `build_symbol_level_deps` and `build_summary_map` to collect project-wide dependency and summary data after per-file analysis is complete.
- It calls `save_dependency_summary` to write a lightweight JSON combining symbol-level dependencies and summaries.
- It calls `save_dependency_graph_as_mermaid` to emit a Mermaid flowchart Markdown file from the dependency graph.
- It calls `save_consolidated_json` to produce a single comprehensive JSON artifact (`project_knowledge.json`) covering all files.
- It calls `to_output_path` directly to normalize file paths into the `"project_name/copy_path"` format when writing individual per-file dependency results before the consolidation steps.

The dependency is **unidirectional**: `pipeline.py` depends on this file, and this file has no knowledge of `pipeline.py`.

## Data Flow

# Data Flow

## Overview

This module aggregates per-file analysis artifacts (JSON files written by earlier pipeline steps) and transforms them into project-level output files.

---

## Input Data Sources

| Source | Format | Description |
|---|---|---|
| `file_dependencies.json` (per file) | JSON file on disk | Caller/callee usage records for one source file |
| `doc.json` (per file) | JSON file on disk | LLM-generated design document including a `summary` field |
| `all_file_list` | `list[str]` | Relative paths (from project root) of all files to process |
| `base_output_dir` | `str` | Root of the output tree; its `basename` becomes `project_name` |

Artifact files are located via `resolve_file_output_dir(base_output_dir, file_rel)`, which uses the `{stem}_{ext}` directory convention from `file_utils`.

---

## Path Formats in Play

Three path formats circulate through this module:

| Format | Example | Produced by |
|---|---|---|
| `rel_path` | `src/foo.py` | Input (`all_file_list`) |
| `copy_path` | `src/foo_py/foo.py` | `rel_to_copy_path()` |
| `output_path` | `myproject/src/foo_py/foo.py` | `to_output_path()` (prepends `project_name`) |

Stored paths inside `file_dependencies.json` are already in `output_path` format (converted by `pipeline.py` at write time), so `output_path_to_rel()` is used to convert them back when building the dependency graph.

---

## Main Transformation Flows

### 1. `build_symbol_level_deps` — Dependency Graph Construction

```
all_file_list
    │
    ▼ (per file) read file_dependencies.json
    │   callee_usages[].from  ──output_path_to_rel──► callees set
    │   caller_usages[].file  ──output_path_to_rel──► callers set
    ▼
deps_map: { rel_path → { "callers": set[rel_path], "callees": set[rel_path] } }
```

### 2. `build_summary_map` — Summary Extraction

```
all_file_list
    │
    ▼ (per file) read doc.json → doc["summary"]
    ▼
summary_map: { rel_path → str | None }
```

### 3. `save_dependency_summary` — Lightweight Output

```
deps_map + summary_map
    │
    ▼ rel_path ──to_output_path──► output_path  (for file, callers, callees)
    ▼
project_dependency_summary.json
```

### 4. `save_consolidated_json` — Full Aggregated Output

```
deps_map + summary_map
    │
    ├─► project_dependencies[]: converted dep entries with summaries
    │
    └─► files[]: per-file merged entries
            ├─ file_dependencies.json content (file field stripped, kept as-is)
            └─ doc.json content (file field stripped)
    ▼
project_knowledge.json
```

### 5. `save_dependency_graph_as_mermaid` — Visualization

```
deps_map
    │
    ▼ rel_path ──to_output_path──► output_path
    │   nodes: all files appearing as caller or callee
    │   edges: (output_path_caller → output_path_callee)
    │
    ▼ output_path ──to_mermaid_node_id──► ID (slashes/dots → "_")
      output_path ──to_display_label──►  label (strip project prefix, copy_path_to_rel)
    ▼
dependency_graph.md  (Mermaid ```graph LR``` block)
```

---

## Key Data Structure Schemas

### `symbol_deps` (`dict[str, dict[str, set[str]]]`)
```
{
  "<rel_path>": {
    "callers": { "<rel_path>", ... },   # files that call into this file
    "callees": { "<rel_path>", ... }    # files this file calls into
  },
  ...
}
```

### `summary_map` (`dict[str, str | None]`)
```
{ "<rel_path>": "<summary text>" | None, ... }
```

### `project_dependency_summary.json`
```json
{
  "project_name": "myproject",
  "files": [
    {
      "file":     "<output_path>",
      "summary":  "<str | null>",
      "callers":  ["<output_path>", ...],   // sorted
      "callees":  ["<output_path>", ...]    // sorted
    }
  ]
}
```

### `project_knowledge.json`
```json
{
  "project_name": "myproject",
  "project_dependencies": [ /* same structure as files[] above */ ],
  "files": [
    {
      "file":               "<output_path>",
      "file_dependencies":  { /* file_dependencies.json body, file key removed */ },
      "doc":                { /* doc.json body, file key removed */ }
    }
  ]
}
```
Files missing both `file_dependencies.json` and `doc.json` are omitted from `files[]` with a warning.

---

## Output Destinations

| Function | Output File | Consumer |
|---|---|---|
| `save_dependency_summary` | `<base_output_dir>/project_dependency_summary.json` | `pipeline.py` |
| `save_consolidated_json` | `<base_output_dir>/project_knowledge.json` | `pipeline.py` |
| `save_dependency_graph_as_mermaid` | `<base_output_dir>/dependency_graph.md` | `pipeline.py` |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** strategy. Rather than aborting when individual file data is missing or incomplete, functions continue processing the remaining files and produce partial results. Missing data is represented as `null` (JSON) or omitted from output, and warnings are emitted via the logger instead of raising exceptions. I/O errors from `open()`, `json.load()`, or `json.dump()` are **not caught** and propagate directly to the caller (fail-fast for unrecoverable I/O failures).

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `doc.json` not found for a file | `os.path.exists` check; skipped silently; `summary` set to `None` | That file's summary is `null` in output; processing continues |
| `file_dependencies.json` not found for a file | `os.path.exists` check; skipped silently | No dependency info for that file; `callers`/`callees` remain empty sets |
| File has no analysis results at all (neither JSON present) | Entry excluded from `files_list`; `logger.warning` emitted | File absent from consolidated JSON; logged as a warning |
| Key absent within a loaded JSON document | `dict.get()` with `None` default | Field treated as absent/null; no exception raised |
| File I/O errors (`open`, `json.load`, `json.dump`) | Not caught; propagate to caller | Entire pipeline call fails |
| `symbol_deps` missing a key for a file | Not guarded; `KeyError` propagates | Entire function call fails |

---

## Design Considerations

- The `os.path.exists` guard before every file read is the primary defensive mechanism, reflecting the expectation that analysis steps for individual files may legitimately fail or be skipped (e.g., LLM not used, generation failed).
- The distinction between a **warning log** (no analysis results at all) and **silent continuation** (only one of the two JSONs missing) encodes an implicit severity hierarchy: a completely unanalyzed file is noteworthy, while partially analyzed files are treated as normal.
- Propagating I/O errors on **write** paths (output JSON, Mermaid file) is intentional: a failure to persist final output is considered unrecoverable and must surface to the caller.
- No validation is performed on the content structure of loaded JSON beyond `dict.get()` access, so structurally malformed JSON files will raise `json.JSONDecodeError` and propagate as unhandled exceptions.

## Summary

`output.py` is the final pipeline stage that aggregates per-file analysis artifacts into project-level outputs. It converts relative paths to canonical `"project_name/copy_path"` format via `to_output_path`. Key functions: `build_symbol_level_deps` reads `file_dependencies.json` per file to build caller/callee sets; `build_summary_map` reads `doc.json` summaries. Three write functions consume these: `save_dependency_summary` writes a lightweight JSON, `save_consolidated_json` merges all per-file artifacts into `project_knowledge.json`, and `save_dependency_graph_as_mermaid` emits a Mermaid flowchart. Missing artifacts are handled gracefully; write errors propagate.
