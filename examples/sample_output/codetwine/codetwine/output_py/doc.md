# Design Document: codetwine/output.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`output.py` is the **output aggregation module** for the CodeTwine pipeline. It exists as a separate file to encapsulate all logic concerned with transforming and writing analysis results into the various output formats consumed downstream (consolidated JSON, lightweight dependency-summary JSON, and Mermaid flowchart Markdown). By isolating these concerns, the pipeline (`pipeline.py`) can invoke high-level output functions without knowing the details of path conversion, file loading, or serialisation.

Its responsibilities are:

1. **Path conversion** — translating project-relative file paths into the canonical `project_name/copy_path` output format used consistently across all output files.
2. **Dependency aggregation** — reading each file's `file_dependencies.json` to build a symbol-level caller/callee graph across the whole project.
3. **Summary aggregation** — reading each file's `doc.json` to collect LLM-generated summaries into a single lookup map.
4. **Multi-format output** — writing three distinct output artefacts: a full consolidated JSON (`project_knowledge.json`), a lightweight dependency+summary JSON (`project_dependency_summary.json`), and a Mermaid diagram Markdown file (`dependency_graph.md`).

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative path to `project_name/copy_path` format for use in output files. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` and returns a map of relative path → summary text (or `None` if absent). |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Reads each file's `file_dependencies.json` and builds a project-wide map of relative path → `{"callers": set, "callees": set}` based on actual symbol-level usage. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict`, `summary_map: dict` | `None` | Writes a lightweight JSON combining symbol-level dependencies and summaries for every file. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict`, `summary_map: dict` | `None` | Writes a full consolidated JSON merging `file_dependencies.json`, `doc.json`, and the dependency graph for every file. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict` | `None` | Generates a Mermaid `graph LR` flowchart from the symbol-level callee graph and writes it as a Markdown file. |

---

## Design Decisions

- **Pre-computed shared inputs.** `pipeline.py` calls `build_symbol_level_deps` and `build_summary_map` once and passes the results into all three `save_*` functions. This avoids redundant file I/O and makes the individual save functions pure transformers over already-loaded data.

- **Canonical path format (`project_name/copy_path`).** All file references inside every output artefact use the `to_output_path` format rather than raw relative paths. This mirrors the physical on-disk layout produced by the copy step and is the single consistent identifier used across all output files.

- **Graceful absence handling.** Both `build_summary_map` and `save_consolidated_json` treat missing `doc.json` or `file_dependencies.json` files as non-fatal: summaries default to `None` and files without any analysis results are omitted from `files_list` with a warning log rather than raising an error.

- **Symbol-level rather than import-level dependencies.** `build_symbol_level_deps` derives callers and callees from the `callee_usages.from` and `caller_usages.file` fields inside `file_dependencies.json`, reflecting actual symbol usage rather than coarse import statements.

## Definition Design Specifications

# Definition Design Specifications

---

## `to_output_path(base_output_dir: str, rel_path: str) -> str`

Converts a project-relative file path to the canonical `"project_name/copy_path"` format used consistently across all output JSON and Markdown files.

- **`base_output_dir`**: Absolute or relative path to the base output directory; its final path component is treated as the project name.
- **`rel_path`**: Project-relative path of the source file (e.g., `"src/foo.py"`).
- **Returns**: A string of the form `"<project_name>/<copy_path>"`, where `copy_path` is produced by `rel_to_copy_path`.

**Design intent**: Centralises the construction of the output-format path so that all consumers (callers in `pipeline.py` and within this module) produce identical path strings, making cross-referencing between JSON fields reliable.

**Edge cases**: The project name is always derived from `os.path.basename(base_output_dir)`, so trailing path separators on `base_output_dir` would produce an empty project name.

---

## `build_summary_map(base_output_dir: str, all_file_list: list[str]) -> dict[str, str | None]`

Reads each file's `doc.json` from its per-file output directory and returns a mapping from project-relative path to its `"summary"` field value.

- **`base_output_dir`**: Base output directory used to resolve each file's per-file output directory.
- **`all_file_list`**: List of project-relative paths of all files to include.
- **Returns**: A `dict` keyed by project-relative path; the value is the summary string if a `doc.json` with a `"summary"` key exists, or `None` if the file is absent or the key is missing.

**Design intent**: Isolates summary collection into a single pass so that downstream functions (`save_dependency_summary`, `save_consolidated_json`) can share one pre-built map rather than each performing redundant file I/O.

**Edge cases**: Every file in `all_file_list` is guaranteed to appear as a key in the returned dict, even when no `doc.json` exists. Missing files or a missing `"summary"` key both result in `None`.

---

## `build_symbol_level_deps(base_output_dir: str, all_file_list: list[str]) -> dict[str, dict[str, set[str]]]`

Constructs symbol-level caller/callee dependency sets for each file by reading `file_dependencies.json` entries.

- **`base_output_dir`**: Base output directory used to locate each file's `file_dependencies.json`.
- **`all_file_list`**: List of project-relative paths of all files to analyse.
- **Returns**: A `dict` keyed by project-relative path; each value is `{"callers": set[str], "callees": set[str]}` where the set members are project-relative paths restored via `output_path_to_rel`.

**Design intent**: Produces a dependency graph based on actual symbol usage (from `callee_usages` and `caller_usages` records) rather than coarser import-level relationships, and converts stored output-format paths back to relative paths for uniform internal representation.

**Design decisions**: Callees are derived from the `"from"` field of `callee_usages`; callers from the `"file"` field of `caller_usages`. All files in `all_file_list` are pre-initialised with empty sets so the returned dict has complete coverage even when no `file_dependencies.json` exists.

**Edge cases**: Files without a `file_dependencies.json` retain empty caller and callee sets. Path conversion via `output_path_to_rel` assumes stored paths conform to the `"project_name/copy_path"` format.

---

## `save_dependency_summary(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

Writes a lightweight JSON file combining symbol-level dependencies and summaries for every file in the project.

- **`base_output_dir`**: Used to derive the project name for output-format path conversion.
- **`all_file_list`**: Ordered list of project-relative paths determining the output order.
- **`output_path`**: Filesystem path at which the JSON file is written.
- **`symbol_deps`**: Pre-built dependency map from `build_symbol_level_deps`.
- **`summary_map`**: Pre-built summary map from `build_summary_map`.
- **Returns**: `None`; side effect is writing the JSON file and emitting a log entry.

**Design intent**: Provides a compact, human- and machine-readable snapshot of the dependency graph annotated with summaries, suitable for consumption without loading the heavier consolidated JSON.

**Design decisions**: Caller and callee sets are converted to sorted lists to ensure deterministic output. Files with no summary contribute `null` rather than being omitted, preserving complete file coverage. The log message includes both total file count and the count of files that have a non-null summary.

---

## `save_consolidated_json(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

Combines the project-level dependency graph with each file's `file_dependencies.json` and `doc.json` into a single comprehensive JSON file.

- **`base_output_dir`**: Used to resolve per-file output directories and derive the project name.
- **`all_file_list`**: Ordered list of project-relative paths.
- **`output_path`**: Filesystem path at which the consolidated JSON is written.
- **`symbol_deps`**: Pre-built dependency map from `build_symbol_level_deps`.
- **`summary_map`**: Pre-built summary map from `build_summary_map`.
- **Returns**: `None`; side effect is writing the JSON file and emitting a log entry.

**Design intent**: Provides a single authoritative file containing all analysis artefacts so downstream tools or humans do not need to traverse the per-file directory tree.

**Design decisions**: The top-level `"file"` field is unified at the entry level and removed from the embedded JSON payloads to avoid redundancy. Paths stored inside `file_dependencies.json` are already in output format (converted during individual file save in `pipeline.py`) and are embedded as-is. Files that contribute neither a `file_dependencies.json` nor a `doc.json` are excluded from the `"files"` list and trigger a warning log; they still appear in `"project_dependencies"` via `symbol_deps`. The log message reports how many files produced entries relative to the total.

**Edge cases**: A file is excluded from `"files"` if its `entry` dict contains only the `"file"` key (i.e., both per-file JSON files are absent). The `"project_dependencies"` section always covers all files in `all_file_list` regardless of whether per-file JSON files exist.

---

## `save_dependency_graph_as_mermaid(base_output_dir: str, output_path: str, symbol_deps: dict[str, dict[str, set[str]]]) -> None`

Generates a Mermaid `graph LR` flowchart from the symbol-level dependency graph and writes it as a Markdown file.

- **`base_output_dir`**: Used to convert project-relative paths to output-format paths and derive the project name.
- **`output_path`**: Filesystem path at which the Markdown file is written.
- **`symbol_deps`**: Pre-built dependency map from `build_symbol_level_deps`.
- **Returns**: `None`; side effect is writing the Markdown file.

**Design intent**: Produces a visual representation of inter-file dependencies in a format that renders directly in Markdown viewers, without requiring any external tooling beyond a Mermaid-capable renderer.

**Design decisions**: Node IDs are derived by replacing `/` and `.` with `_` to satisfy Mermaid's identifier constraints. Display labels are the original source-relative paths (recovered via `copy_path_to_rel`), providing readability while keeping IDs syntactically valid. Both nodes and edges are sorted before output to produce deterministic, diff-friendly Markdown. Only callee edges are rendered (directed from caller to callee); caller relationships are implicitly captured by the inverse edges. Nodes are collected from both the keys of `symbol_deps` and all callee targets, ensuring that files referenced only as callees appear in the graph even if they are not in `all_file_list`.

**Nested helpers**:
- `to_mermaid_node_id(path: str) -> str`: Replaces `/` and `.` with `_` in an output-format path to produce a valid Mermaid node identifier.
- `to_display_label(path: str) -> str`: Strips the project-name prefix from an output-format path and converts the remainder via `copy_path_to_rel` to recover the original source-relative path for use as the visible node label.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file depends on `codetwine/utils/file_utils.py` for all path encoding and decoding operations:

- **`rel_to_copy_path`**: Used to convert a project-relative file path into the collision-avoiding `{stem}_{ext}` directory structure. This is called within `to_output_path` to construct the `project_name/copy_path` format strings that are used consistently throughout all output JSON files and Mermaid graphs.

- **`copy_path_to_rel`**: Used within `save_dependency_graph_as_mermaid` to convert a `project_name/copy_path` format string back into a human-readable source-relative path for use as a display label in the Mermaid flowchart nodes.

- **`output_path_to_rel`**: Used in `build_symbol_level_deps` to decode file paths stored in `file_dependencies.json` (which are already in `project_name/copy_path` format) back into project-relative paths, so that the in-memory dependency map can be keyed consistently by relative path.

- **`resolve_file_output_dir`**: Used throughout multiple functions (`build_summary_map`, `save_consolidated_json`, `build_symbol_level_deps`) to locate the per-file output directory where `doc.json` and `file_dependencies.json` are stored, given the base output directory and a file's relative path.

### Dependents (what uses this file)

This file is used exclusively by `codetwine/pipeline.py`, which drives the overall analysis pipeline:

- **`to_output_path`**: The pipeline calls this to convert file paths in dependency analysis results (including `file` fields, `callee_usages[].from` fields, and `caller_usages[].file` fields) into the `project_name/copy_path` format before saving individual per-file JSON outputs.

- **`build_symbol_level_deps`**: The pipeline calls this once to build the shared symbol-level dependency graph from per-file `file_dependencies.json` files, and passes the result to subsequent output-generation functions.

- **`build_summary_map`**: The pipeline calls this to collect per-file summaries from `doc.json` files, and passes the result alongside the dependency graph to subsequent output-generation functions.

- **`save_dependency_summary`**: The pipeline uses this to write the lightweight `project_dependency_summary.json` combining dependencies and summaries.

- **`save_dependency_graph_as_mermaid`**: The pipeline uses this to produce the `dependency_graph.md` Mermaid flowchart from the dependency graph.

- **`save_consolidated_json`**: The pipeline uses this to produce the comprehensive `project_knowledge.json` that merges all per-file analysis results into a single document.

The dependency relationship is **unidirectional**: `pipeline.py` depends on `output.py`, and `output.py` depends on `file_utils.py`. Neither `file_utils.py` nor `pipeline.py` is depended upon by `output.py` in reverse.

## Data Flow

# Data Flow

## Input Data

| Input | Format | Source |
|---|---|---|
| `base_output_dir` | Directory path string | Caller (`pipeline.py`) |
| `all_file_list` | `list[str]` of project-relative paths | Caller (`pipeline.py`) |
| `file_dependencies.json` | JSON file per file | Written by earlier pipeline steps |
| `doc.json` | JSON file per file | Written by earlier pipeline steps |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Return value of `build_symbol_level_deps` |
| `summary_map` | `dict[str, str \| None]` | Return value of `build_summary_map` |

---

## Path Encoding

All internal paths are transformed before being stored in output files:

```
project-relative path  →  rel_to_copy_path()  →  copy_path
copy_path              →  "{project_name}/{copy_path}"  →  output_path format
output_path format     →  output_path_to_rel()  →  project-relative path  (reverse)
```

The `to_output_path()` helper encapsulates the forward direction and is used by every output-producing function.

---

## Main Transformation Flows

### `build_symbol_level_deps`

```
all_file_list
  → for each file: read file_dependencies.json
      → callee_usages[].from  → output_path_to_rel() → add to deps_map[file]["callees"]
      → caller_usages[].file  → output_path_to_rel() → add to deps_map[file]["callers"]
  → deps_map: dict[rel_path, {"callers": set[rel_path], "callees": set[rel_path]}]
```

Paths stored in `file_dependencies.json` are in output-path format (written by `pipeline.py`); they are decoded back to project-relative paths here.

### `build_summary_map`

```
all_file_list
  → for each file: read doc.json → extract "summary" field
  → summary_map: dict[rel_path, str | None]
```

### `save_dependency_summary`

```
symbol_deps + summary_map
  → for each file: encode paths with to_output_path()
  → files_list entries (see structure below)
  → write project_dependency_summary.json
```

### `save_consolidated_json`

```
symbol_deps + summary_map  →  project_dependencies list (encoded paths)
all_file_list
  → for each file:
      read file_dependencies.json  →  strip "file" field  →  entry["file_dependencies"]
      read doc.json                →  strip "file" field  →  entry["doc"]
  → files_list
→ write project_knowledge.json
```

### `save_dependency_graph_as_mermaid`

```
symbol_deps
  → collect node_set (all files appearing as source or callee)
  → collect edge_set (caller → callee pairs)
  → for each node: to_mermaid_node_id() for ID, copy_path_to_rel() for display label
  → write Mermaid fenced code block to dependency_graph.md
```

---

## Key Data Structures

### `deps_map` / `symbol_deps`
```
{
  "src/foo.py": {
    "callers": {"src/bar.py", ...},   # files that call into this file
    "callees": {"src/baz.py", ...}    # files this file calls into
  },
  ...
}
```
Values are `set[str]` of project-relative paths internally; sorted lists when serialized.

### `summary_map`
```
{
  "src/foo.py": "One-sentence summary string",  # from doc.json["summary"]
  "src/bar.py": None,                           # doc.json absent or field missing
  ...
}
```

### `files_list` entry (in `save_dependency_summary` / `save_consolidated_json`)
| Field | Type | Content |
|---|---|---|
| `file` | `str` | `"{project_name}/{copy_path}"` format |
| `summary` | `str \| null` | From `summary_map` |
| `callers` | `list[str]` | Sorted output-path strings |
| `callees` | `list[str]` | Sorted output-path strings |
| `file_dependencies` | `dict` | Contents of `file_dependencies.json` minus `"file"` key (consolidated JSON only) |
| `doc` | `dict` | Contents of `doc.json` minus `"file"` key (consolidated JSON only) |

---

## Output Files

| Function | Output File | Format |
|---|---|---|
| `save_dependency_summary` | `project_dependency_summary.json` | JSON: `{project_name, files[]}` |
| `save_consolidated_json` | `project_knowledge.json` | JSON: `{project_name, project_dependencies[], files[]}` |
| `save_dependency_graph_as_mermaid` | `dependency_graph.md` | Markdown with Mermaid `graph LR` block |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** strategy. Rather than aborting the entire pipeline when data is missing or incomplete for an individual file, the code continues processing and emits warnings via the logger. Missing analysis artifacts (e.g., `doc.json`, `file_dependencies.json`) result in `None` or empty values being recorded rather than exceptions being raised.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Missing `doc.json` for a file | `os.path.exists` check; if absent, `summary` is recorded as `None` | That file's summary is `null` in all output JSON; processing continues normally |
| Missing `file_dependencies.json` for a file | `os.path.exists` check; if absent, the file is skipped (`continue`) in `build_symbol_level_deps` and the entry is omitted in `save_consolidated_json` | Dependency edges for that file are empty sets; no callers/callees are recorded |
| File entry with no analysis artifacts (neither `doc.json` nor `file_dependencies.json`) | Entry is excluded from `files_list` in `save_consolidated_json`; a `logger.warning` is emitted | File is absent from the `"files"` array in the consolidated JSON; a warning message identifies it |
| Invalid or unexpected field values in loaded JSON | `dict.get` with a `None` default is used for optional fields such as `"summary"`, `"from"`, and `"file"` | Missing fields produce `None` or are silently skipped; no exception is raised |
| I/O errors during file reads or final JSON writes | No explicit try/except; unhandled exceptions propagate to the caller | Any OS-level I/O failure will surface as an unhandled exception, interrupting the pipeline |

---

## Design Considerations

The file applies a **check-before-open** pattern (`os.path.exists` prior to any `open` call) as its primary guard against missing files, rather than wrapping I/O in exception handlers. This keeps the code straightforward but means truly unexpected I/O failures (e.g., permission errors, disk errors) are not caught locally and propagate upward. The design implicitly assumes that if a file passes the existence check, it is readable and contains valid JSON; malformed JSON would raise an unhandled `json.JSONDecodeError`. The logging of warnings for missing artifacts provides observability for partial results without halting the pipeline, which aligns with the overall graceful-degradation intent.

## Summary

**codetwine/output.py** aggregates pipeline analysis results into three output formats: a consolidated JSON (`project_knowledge.json`), a lightweight dependency+summary JSON (`project_dependency_summary.json`), and a Mermaid flowchart Markdown (`dependency_graph.md`).

Public interface: `to_output_path` (path format conversion), `build_summary_map` (collects per-file LLM summaries), `build_symbol_level_deps` (builds caller/callee graph from `file_dependencies.json`), and three `save_*` functions that consume the pre-built maps.

Key data structures: `symbol_deps` maps relative paths to `{"callers": set, "callees": set}`; `summary_map` maps relative paths to summary strings or `None`. Missing artifacts degrade gracefully rather than raising errors.
