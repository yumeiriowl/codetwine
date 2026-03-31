# Design Document: codetwine/output.py

## Overview & Purpose

## 1. Module Summary

Aggregates per-file analysis results (dependency data and design document summaries) into project-level output artifacts: a dependency summary JSON, a consolidated knowledge JSON, and a Mermaid dependency graph Markdown file.

## 2. When to Use This Module

- **To build symbol-level dependency relationships across all project files**: Call `build_symbol_level_deps(base_output_dir, all_file_list)` to produce a `{file_rel: {"callers": set, "callees": set}}` dict derived from each file's `file_dependencies.json`.
- **To collect per-file summary text from design documents**: Call `build_summary_map(base_output_dir, all_file_list)` to produce a `{file_rel: summary_or_None}` dict read from each file's `doc.json`.
- **To emit a lightweight JSON combining dependencies and summaries**: Call `save_dependency_summary(...)` with the outputs of `build_symbol_level_deps` and `build_summary_map` to write `project_dependency_summary.json`.
- **To emit a single JSON containing all per-file dependency info and design documents**: Call `save_consolidated_json(...)` to write `project_knowledge.json` that merges `file_dependencies.json` and `doc.json` for every file.
- **To emit a Mermaid flowchart of the dependency graph**: Call `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)` to write a Markdown file containing a `graph LR` Mermaid diagram.
- **To convert a project-relative path to the `project_name/copy_path` output format**: Call `to_output_path(base_output_dir, rel_path)` — used by `codetwine/pipeline.py` when rewriting path fields inside per-file JSON results before saving them.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative file path to `project_name/copy_path` format using `os.path.basename` of the output dir as the project name. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads `doc.json` for each file and returns a mapping from relative path to its `summary` field, or `None` if absent. |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Reads each file's `file_dependencies.json` and returns a mapping from relative path to `{"callers": set, "callees": set}` of relative paths. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a JSON file with project name, and per-file entries containing the output-format path, summary, sorted callers, and sorted callees. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a JSON file merging `project_dependencies` (symbol-level deps with summaries) and `files` (per-file `file_dependencies.json` and `doc.json` contents). |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]` | `None` | Writes a Markdown file containing a Mermaid `graph LR` flowchart of callee edges, with node labels showing source-relative paths. |

## 4. Design Decisions

- **All output paths use the `project_name/copy_path` format**: Every path written into output JSON files and the Mermaid diagram is converted via `to_output_path`, ensuring consistency with the copy-destination directory layout managed by `file_utils.py`. The inverse conversion (`output_path_to_rel`) is applied when reading paths back from `file_dependencies.json` in `build_symbol_level_deps`.
- **`build_symbol_level_deps` and `build_summary_map` are separated from the save functions**: The two data-building functions are designed to be called once and their results shared across `save_dependency_summary`, `save_consolidated_json`, and `save_dependency_graph_as_mermaid`, avoiding redundant disk reads (as reflected in `pipeline.py`'s usage).
- **Files missing both `file_dependencies.json` and `doc.json` are excluded from the `files` list in the consolidated JSON**: An entry is only appended to `files_list` when `len(entry) > 1` (i.e., at least one of the two sub-documents was loaded), and a warning is logged for skipped files.

## Definition Design Specifications

---

## `to_output_path`

**Signature:** `to_output_path(base_output_dir: str, rel_path: str) -> str`

**Responsibility:** Converts a project-relative file path into the `project_name/copy_path` format used uniformly across all output JSON files and the Mermaid graph.

**When to use:** Call whenever a relative file path must be expressed in the canonical output-path format before being written to any output artifact.

**Design decisions:** The project name is derived from the trailing component of `base_output_dir` rather than being passed explicitly, keeping the signature minimal. The copy-path segment is delegated entirely to `rel_to_copy_path`.

**Constraints & edge cases:**
- `base_output_dir` must have a non-empty final path component; an empty basename produces an empty project-name prefix.
- `rel_path` must be a valid project-relative path accepted by `rel_to_copy_path`.

---

## `build_summary_map`

**Signature:** `build_summary_map(base_output_dir: str, all_file_list: list[str]) -> dict[str, str | None]`

- `all_file_list`: list of project-relative paths for all files in scope.
- Return type: a mapping from each relative path to its summary string, or `None` if unavailable.

**Responsibility:** Reads the `"summary"` field from each file's `doc.json` and assembles a single lookup dict, allowing downstream functions to attach summaries without re-reading files.

**When to use:** Call once before invoking `save_dependency_summary` or `save_consolidated_json`, so both can share the same pre-built map.

**Design decisions:** Files whose `doc.json` is absent or whose JSON lacks a `"summary"` key are represented as `None` rather than being omitted, ensuring every entry in `all_file_list` always has a key in the returned dict.

**Constraints & edge cases:**
- Every path in `all_file_list` appears as a key in the result regardless of whether `doc.json` exists.
- The summary value is whatever `doc.json` stores under `"summary"`; no validation or transformation is applied.

---

## `save_consolidated_json`

**Signature:**
```
save_consolidated_json(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None
```

- `symbol_deps`: mapping from relative path → `{"callers": set of relative paths, "callees": set of relative paths}`.
- `summary_map`: mapping from relative path → summary string or `None`.

**Responsibility:** Merges per-file `file_dependencies.json` and `doc.json` data together with the symbol-level dependency graph into a single `project_knowledge.json`-style output file.

**When to use:** Call after all per-file analyses are complete and `build_symbol_level_deps` / `build_summary_map` have already been called.

**Design decisions:**
- The `"file"` key is removed from both `file_dependencies.json` and `doc.json` content before embedding, because the file identity is unified at the top-level `"file"` field of each entry.
- Files for which neither `file_dependencies.json` nor `doc.json` exists are excluded from `"files"` with a warning, but they still appear in `"project_dependencies"` (via `symbol_deps`).
- Caller/callee lists in `"project_dependencies"` are sorted to produce deterministic output.
- All file paths throughout the output use the `project_name/copy_path` format via `to_output_path`.

**Constraints & edge cases:**
- Every key in `all_file_list` must be present in `symbol_deps`; a missing key causes a `KeyError`.
- Paths stored inside `file_dependencies.json` are expected to already be in `project_name/copy_path` format (converted by `pipeline.py` before saving).
- Files with no analysis results (entry length ≤ 1) are logged as warnings and omitted from `"files"`.

**Output schema (top-level keys):**

| Key | Type | Description |
|-----|------|-------------|
| `project_name` | `str` | Basename of `base_output_dir` |
| `project_dependencies` | `list[dict]` | One entry per file: `file`, `summary`, `callers`, `callees` |
| `files` | `list[dict]` | One entry per file with results: `file`, `file_dependencies`?, `doc`? |

---

## `build_symbol_level_deps`

**Signature:** `build_symbol_level_deps(base_output_dir: str, all_file_list: list[str]) -> dict[str, dict[str, set[str]]]`

- Return type: mapping from relative path → `{"callers": set of relative paths, "callees": set of relative paths}`.

**Responsibility:** Aggregates actual symbol-usage–level caller/callee relationships across the project by reading each file's `file_dependencies.json`, producing a graph that reflects real usage rather than mere imports.

**When to use:** Call once before any of the three output-generation functions (`save_dependency_summary`, `save_consolidated_json`, `save_dependency_graph_as_mermaid`) that require the dependency graph.

**Design decisions:**
- Callees are sourced from the `"from"` field of `callee_usages`; callers are sourced from the `"file"` field of `caller_usages`. This matches the schema written by `pipeline.py`.
- All paths read from JSON are in `project_name/copy_path` format and are converted back to relative paths via `output_path_to_rel` before being stored, keeping the returned dict consistently in relative-path space.
- Uses `set` values to avoid duplicate edges.
- Every file in `all_file_list` is initialised with empty sets regardless of whether its `file_dependencies.json` exists.

**Constraints & edge cases:**
- Files without a `file_dependencies.json` contribute no edges but still appear in the result with empty sets.
- `"from"` / `"file"` values that are falsy are silently skipped.

---

## `save_dependency_summary`

**Signature:**
```
save_dependency_summary(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None
```

**Responsibility:** Writes a lightweight JSON file that pairs symbol-level dependency edges with per-file summaries, intended as a quick-reference overview without the full per-file analysis data.

**When to use:** Call after `build_symbol_level_deps` and `build_summary_map` when a compact dependency + summary artifact is needed (e.g., `project_dependency_summary.json`).

**Design decisions:**
- Structurally simpler than `save_consolidated_json`: outputs only `project_name` and a flat `files` list; per-file `file_dependencies.json`/`doc.json` content is not included.
- Caller/callee lists are sorted for deterministic output.
- Files without a summary are included with `null`; no files are omitted.
- Logs the count of files that have a non-null summary alongside the total file count.

**Constraints & edge cases:**
- Every key in `all_file_list` must exist in both `symbol_deps` and `summary_map`; missing keys cause a `KeyError`.

**Output schema (top-level keys):**

| Key | Type | Description |
|-----|------|-------------|
| `project_name` | `str` | Basename of `base_output_dir` |
| `files` | `list[dict]` | One entry per file: `file`, `summary`, `callers`, `callees` |

---

## `save_dependency_graph_as_mermaid`

**Signature:**
```
save_dependency_graph_as_mermaid(
    base_output_dir: str,
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
) -> None
```

**Responsibility:** Renders the symbol-level dependency graph as a Mermaid `graph LR` flowchart embedded in a Markdown code fence and writes it to a `.md` file.

**When to use:** Call after `build_symbol_level_deps` when a visual/human-readable dependency graph is needed.

**Design decisions:**
- Only callee edges are used to build the graph; caller relationships are implied by the reverse direction and are not drawn separately, avoiding duplicate arrows.
- Nodes are derived from both the keys of `symbol_deps` and all callee targets, so nodes that appear only as callees (and not as keys in `all_file_list`) are still rendered.
- Both node declarations and edge declarations are sorted before output, ensuring deterministic file content.
- Two inner helpers handle path-to-identifier conversion:

| Helper | Purpose |
|--------|---------|
| `to_mermaid_node_id(path)` | Replaces `/` and `.` with `_` to produce a valid Mermaid node identifier |
| `to_display_label(path)` | Strips the project-name prefix and converts the copy-path back to a source-relative path via `copy_path_to_rel` for the human-readable node label |

**Constraints & edge cases:**
- `to_mermaid_node_id` may produce collisions if two distinct paths differ only in `/` vs `.` characters (no deduplication is performed).
- `to_display_label` assumes the `project_name/copy_path` two-segment format; paths with fewer segments are returned unchanged.
- Files with no callees still appear as isolated nodes because they are always added to `node_set` from `symbol_deps` keys.

## Dependency Description

## Dependencies (modules this file imports)

**`codetwine/output_py/output.py` → `codetwine/utils/file_utils.py`** : path conversion and output directory resolution

The following symbols are imported from `codetwine/utils/file_utils.py`:

- **`rel_to_copy_path`** — used in `to_output_path` to convert a project-relative file path into the copy-destination directory structure path (`{stem}_{ext}/{filename}` format), which is then prefixed with the project name to produce the canonical output path format (`project_name/copy_path`).
- **`copy_path_to_rel`** — used inside `to_display_label` (within `save_dependency_graph_as_mermaid`) to strip the inserted `{stem}_{ext}` directory from a copy-destination path and recover the original source-relative filename for display in Mermaid labels.
- **`output_path_to_rel`** — used in `build_symbol_level_deps` to convert `project_name/copy_path`-format strings (read from `from` and `file` fields in `file_dependencies.json`) back to project-relative paths when populating the caller/callee sets.
- **`resolve_file_output_dir`** — used in `build_summary_map`, `save_consolidated_json`, and `build_symbol_level_deps` to compute the absolute output directory for a given file's relative path, enabling the code to locate each file's `doc.json` and `file_dependencies.json`.

---

## Dependents (modules that import this file)

**`codetwine/pipeline.py` → `codetwine/output_py/output.py`** : orchestrating the final output generation phase of the analysis pipeline

The following symbols are consumed by `codetwine/pipeline.py`:

- **`to_output_path`** — called directly in the pipeline to convert file paths and usage paths (in `callee_usages` and `caller_usages`) from project-relative format to the `project_name/copy_path` format before saving individual `file_dependencies.json` files.
- **`build_symbol_level_deps`** — called once to build the shared symbol-level dependency graph (`{file_rel: {"callers": set, "callees": set}}`) that is reused across the subsequent three output functions.
- **`build_summary_map`** — called to read each file's `doc.json` and assemble a `{file_rel: summary_text | None}` mapping, also shared across subsequent output functions.
- **`save_dependency_summary`** — called to write `project_dependency_summary.json`, a lightweight file combining symbol-level dependencies and summaries.
- **`save_dependency_graph_as_mermaid`** — called to write `dependency_graph.md`, a Mermaid flowchart of the symbol-level dependency graph.
- **`save_consolidated_json`** — called to write `project_knowledge.json`, the full consolidated file merging per-file `file_dependencies.json` and `doc.json` data with the dependency graph.

---

## Dependency Direction

- **`codetwine/output_py/output.py` → `codetwine/utils/file_utils.py`** : **Unidirectional.** `output.py` imports from `file_utils.py`; `file_utils.py` has no dependency on `output.py`.
- **`codetwine/pipeline.py` → `codetwine/output_py/output.py`** : **Unidirectional.** `pipeline.py` imports from `output.py`; `output.py` has no dependency on `pipeline.py`.

## Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| Function arguments | `str` | `base_output_dir`: filesystem path whose trailing component is the project name |
| Function arguments | `list[str]` | `all_file_list`: project-relative paths of files to analyze (e.g. `"src/foo.py"`) |
| Function arguments | `str` | `output_path`: destination path for output files |
| Function arguments | `dict[str, dict[str, set[str]]]` | `symbol_deps`: pre-built symbol-level dependency map (produced by `build_symbol_level_deps`) |
| Function arguments | `dict[str, str | None]` | `summary_map`: pre-built file-to-summary map (produced by `build_summary_map`) |
| Disk reads | JSON (`doc.json`) | Per-file design document; read from `{base_output_dir}/{copy_path_dir}/doc.json` |
| Disk reads | JSON (`file_dependencies.json`) | Per-file dependency info; read from `{base_output_dir}/{copy_path_dir}/file_dependencies.json` |

---

## 2. Transformation Overview

### Stage 1 — Path resolution (`resolve_file_output_dir`, `to_output_path`)
Every project-relative path in `all_file_list` is converted in two ways:
- **Output-format path** (`project_name/copy_path`): produced by `to_output_path`, which prefixes the project name (basename of `base_output_dir`) to `rel_to_copy_path(rel_path)`.
- **Filesystem output directory**: produced by `resolve_file_output_dir`, pointing to the directory that holds `doc.json` and `file_dependencies.json` for that file.

### Stage 2 — Build symbol-level dependency map (`build_symbol_level_deps`)
For each file in `all_file_list`, `file_dependencies.json` is read from disk. The function:
1. Reads `callee_usages[*].from` fields, converts each from output-format path back to a relative path via `output_path_to_rel`, and records it as a **callee**.
2. Reads `caller_usages[*].file` fields, converts each similarly, and records it as a **caller**.

The result is a `deps_map` keyed by relative path, with `"callers"` and `"callees"` sets.

### Stage 3 — Build summary map (`build_summary_map`)
For each file in `all_file_list`, `doc.json` is read if it exists and the `"summary"` field is extracted. Files with no `doc.json` map to `None`. The result is a flat `{rel_path: summary_text | None}` dict.

### Stage 4 — Serialize outputs
Three independent serialization functions consume `symbol_deps` and `summary_map`:

- **`save_dependency_summary`**: Iterates `all_file_list`, converts each file's relative path to output format, attaches its summary and sorted caller/callee lists, and writes a single lightweight JSON.

- **`save_dependency_graph_as_mermaid`**: Iterates `symbol_deps` to collect all node paths and directed callee edges; converts each path to a Mermaid-safe node ID (slashes and dots replaced with `_`) and a display label (output-format path converted back to source-relative via `copy_path_to_rel`); writes a fenced Mermaid `graph LR` block as Markdown.

- **`save_consolidated_json`**: Builds a `project_dependencies` list (output-format paths + summary + sorted callers/callees) and a `files` list (loads both `file_dependencies.json` and `doc.json` per file, strips the `"file"` key from each, and nests them under their respective keys). Files for which neither JSON exists are skipped with a warning. Writes the combined structure as a single JSON.

---

## 3. Outputs

| Output | Format | Producer | Description |
|---|---|---|---|
| Return value | `dict[str, dict[str, set[str]]]` | `build_symbol_level_deps` | Symbol-level dependency map keyed by relative path |
| Return value | `dict[str, str \| None]` | `build_summary_map` | Per-file summary text or `None` |
| Return value | `str` | `to_output_path` | Single `project_name/copy_path` formatted string |
| Disk write (JSON) | `project_dependency_summary.json` | `save_dependency_summary` | Lightweight dependency + summary JSON |
| Disk write (Markdown) | `dependency_graph.md` | `save_dependency_graph_as_mermaid` | Mermaid flowchart of the callee dependency graph |
| Disk write (JSON) | `project_knowledge.json` | `save_consolidated_json` | Full consolidated analysis JSON |
| Side effect | Log messages | all `save_*` functions | File counts and output paths logged via `logger` |

---

## 4. Key Data Structures

### `symbol_deps` — `dict[str, dict[str, set[str]]]`
Keyed by project-relative file path. Produced by `build_symbol_level_deps`, consumed by all three `save_*` functions.

| Field / Key | Type | Purpose |
|---|---|---|
| `"callers"` | `set[str]` | Relative paths of files that call symbols defined in this file |
| `"callees"` | `set[str]` | Relative paths of files whose symbols this file calls |

### `summary_map` — `dict[str, str | None]`
Produced by `build_summary_map`.

| Field / Key | Type | Purpose |
|---|---|---|
| `<file_rel>` | `str \| None` | Summary text from `doc.json`, or `None` if absent |

### `converted_deps` entry — `dict` (element of `project_dependencies` list)
Built inside `save_consolidated_json` and `save_dependency_summary`.

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Output-format path (`project_name/copy_path`) |
| `"summary"` | `str \| None` | Summary from `summary_map` |
| `"callers"` | `list[str]` | Sorted output-format paths of caller files |
| `"callees"` | `list[str]` | Sorted output-format paths of callee files |

### `files` entry — `dict` (element of the `files` list in consolidated JSON)
Built inside `save_consolidated_json`.

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Output-format path for this file |
| `"file_dependencies"` | `dict` | Contents of `file_dependencies.json` with `"file"` key removed |
| `"doc"` | `dict` | Contents of `doc.json` with `"file"` key removed |

### Mermaid node/edge sets — internal to `save_dependency_graph_as_mermaid`

| Structure | Type | Purpose |
|---|---|---|
| `node_set` | `set[str]` | All output-format paths that appear as nodes in the graph |
| `edge_set` | `set[tuple[str, str]]` | Directed edges as `(caller_output_path, callee_output_path)` pairs |

## Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation / logging-and-continue** approach. Missing files (e.g., `doc.json`, `file_dependencies.json`) are silently tolerated: the affected entry is populated with `None` or omitted partial data, and processing continues with the remaining files. The only explicit notification of a degraded result is a `logger.warning` call when an entry in the consolidated JSON lacks any analysis results beyond its file path. There are no `try-except` blocks; all error avoidance is achieved through `os.path.exists` pre-checks before any file I/O.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `doc.json` | `doc.json` does not exist for a given file in `build_summary_map` | `summary` is set to `None`; processing continues | Yes | That file's summary is `null` in all output JSON |
| Missing `doc.json` in consolidation | `doc.json` not found in `save_consolidated_json` | `doc` key is not added to the entry | Yes | Entry may be excluded from `files_list` if no other data exists |
| Missing `file_dependencies.json` in consolidation | `file_dependencies.json` not found in `save_consolidated_json` | `file_dependencies` key is not added to the entry | Yes | Entry may be excluded from `files_list` if no other data exists |
| Entry with no analysis results | Neither `file_dependencies.json` nor `doc.json` was found for a file during `save_consolidated_json` | `logger.warning` is emitted; entry is excluded from `files_list` | Yes | File is absent from `files` array in consolidated JSON; counted in log mismatch |
| Missing `file_dependencies.json` in `build_symbol_level_deps` | `file_dependencies.json` not found for a given file | File is skipped via `continue`; its entry remains with empty `callers`/`callees` sets | Yes | That file reports no symbol-level dependencies |
| Missing or absent field in `file_dependencies.json` | `callee_usages`/`caller_usages` key absent, or `from`/`file` field missing in a usage entry | `dict.get` returns `None`; the usage is skipped | Yes | Individual missing usage entries are silently ignored |

---

## 3. Design Notes

- **Pre-check over exception**: All file-existence checks use `os.path.exists` rather than catching `FileNotFoundError`, making the absence of expected output files a normal, anticipated condition rather than an exceptional one.
- **Partial-result tolerance**: The functions are designed to produce useful output even when LLM generation steps were skipped or failed for some files (indicated by absent `doc.json`). `None` summaries propagate cleanly through the entire pipeline into the final JSON.
- **Visibility of degradation**: The `logger.warning` in `save_consolidated_json` and the file-count mismatch reported in `logger.info` (e.g., `files: {len(files_list)}/{len(all_file_list)}`) are the only mechanisms that surface incomplete results to the operator; there is no hard failure or exception propagation.
- **No defensive handling of I/O errors**: Files that pass the `os.path.exists` check are opened without a surrounding `try-except`, meaning unexpected I/O errors (permissions, corruption) would propagate as unhandled exceptions to the caller.

## Summary

Aggregates per-file analysis results into project-level output artifacts. Public functions: `to_output_path(base_output_dir:str, rel_path:str)->str`; `build_symbol_level_deps(base_output_dir:str, all_file_list:list[str])->dict[str,dict[str,set[str]]]`; `build_summary_map(base_output_dir:str, all_file_list:list[str])->dict[str,str|None]`; `save_dependency_summary(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)->None`; `save_consolidated_json(same args)->None`; `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)->None`. Key structures: `symbol_deps` mapping rel-path→`{"callers":set[str],"callees":set[str]}`; `summary_map` mapping rel-path→`str|None`.
