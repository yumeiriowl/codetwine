# Design Document: codetwine/output.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Aggregates per-file analysis results (dependency graphs and design document summaries) into project-level output artifacts — a dependency summary JSON, a consolidated knowledge JSON, and a Mermaid flowchart Markdown file.

## 2. When to Use This Module

- **Building symbol-level dependency maps**: Call `build_symbol_level_deps(base_output_dir, all_file_list)` to read each file's `file_dependencies.json` and produce a `{file_rel: {"callers": set, "callees": set}}` map based on actual symbol usage.
- **Collecting per-file summaries**: Call `build_summary_map(base_output_dir, all_file_list)` to read each file's `doc.json` and produce a `{file_rel: summary_text | None}` map.
- **Generating a lightweight dependency + summary JSON**: Call `save_dependency_summary(...)` with the results of the above two functions to write `project_dependency_summary.json`.
- **Generating a full consolidated knowledge JSON**: Call `save_consolidated_json(...)` to merge all `file_dependencies.json` and `doc.json` contents into a single `project_knowledge.json`.
- **Generating a Mermaid dependency diagram**: Call `save_dependency_graph_as_mermaid(...)` to write a Markdown file containing a `graph LR` Mermaid flowchart of the project's dependency edges.
- **Converting a relative path to output format**: Call `to_output_path(base_output_dir, rel_path)` to produce a `project_name/copy_path` string used consistently across all output artifacts.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative file path to the `project_name/copy_path` format used in all output JSON files. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` and returns a map of relative path to summary text (or `None` if absent). |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Reads each file's `file_dependencies.json` and returns a map of relative path to its caller and callee sets. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a lightweight JSON combining symbol-level dependency edges and per-file summaries. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Merges all per-file `file_dependencies.json` and `doc.json` contents with the dependency graph into a single project-wide JSON. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]` | `None` | Generates a Mermaid `graph LR` flowchart from the symbol-level dependency graph and writes it as a Markdown file. |

## 4. Design Decisions

- **Shared pre-computation**: `build_symbol_level_deps` and `build_summary_map` are intentionally separated from the save functions so their results can be computed once and passed to multiple output functions (`save_dependency_summary`, `save_consolidated_json`, `save_dependency_graph_as_mermaid`) without re-reading files.
- **Uniform path format**: All file paths in every output artifact are normalized to the `project_name/copy_path` format via `to_output_path`. This format mirrors the physical output directory structure established by `rel_to_copy_path` in `file_utils.py`, keeping on-disk layout and JSON references consistent.
- **Graceful handling of missing artifacts**: Files with no `doc.json` or `file_dependencies.json` are represented with `null` summaries or omitted from the `files` list in the consolidated JSON (with a warning logged), rather than causing a failure.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level

| Item | Detail |
|---|---|
| Logger | `logger = logging.getLogger(__name__)` — module-scoped logger used for info and warning messages throughout all output functions. |

---

## `to_output_path`

**Signature:**
```python
def to_output_path(base_output_dir: str, rel_path: str) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Absolute or relative path to the base output directory; its trailing component is treated as the project name. |
| `rel_path` | `str` | A file path relative to the project root. |
| **Returns** | `str` | A path in `"project_name/copy_path"` format. |

**Responsibility:** Produces the canonical output-format path used consistently throughout all JSON and Mermaid output, ensuring every file reference carries the project name prefix.

**When to use:** Call this whenever a file's relative path must be serialized into an output artifact (JSON entry, Mermaid node, etc.).

**Design decisions:**
- The project name is derived solely from `os.path.basename(base_output_dir)`; no additional configuration is required.
- Delegates copy-path conversion to `rel_to_copy_path`, keeping path-structure logic centralized in `file_utils`.

**Constraints & edge cases:**
- `base_output_dir` must end with the intended project name directory; if it ends with a path separator, `os.path.basename` may return an empty string.
- `rel_path` must be a valid project-relative path accepted by `rel_to_copy_path`.

---

## `build_summary_map`

**Signature:**
```python
def build_summary_map(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, str | None]
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory used to locate each file's `doc.json`. |
| `all_file_list` | `list[str]` | Project-relative paths of all files to include. |
| **Returns** | `dict[str, str \| None]` | Maps each project-relative path to its summary string, or `None` if no `doc.json` exists or the summary key is absent. |

**Responsibility:** Provides a pre-built lookup table of per-file summaries so that downstream functions (`save_dependency_summary`, `save_consolidated_json`) do not each re-read `doc.json` independently.

**When to use:** Call once per pipeline run, immediately after all per-file analysis is complete and before any consolidated output is written.

**Design decisions:**
- Every file in `all_file_list` is guaranteed a key in the returned dict; missing or unreadable `doc.json` maps to `None` rather than being omitted.
- Summary extraction uses `dict.get("summary")`, so an absent key and an explicit `null` value in the JSON both yield `None`.

**Constraints & edge cases:**
- If `doc.json` is malformed JSON, `json.load` will raise; no error handling is applied.
- The `"summary"` field's presence inside `doc.json` is not enforced; a missing key silently produces `None`.

---

## `save_consolidated_json`

**Signature:**
```python
def save_consolidated_json(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory. |
| `all_file_list` | `list[str]` | Project-relative paths of all files to consolidate. |
| `output_path` | `str` | Destination file path for the written JSON. |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Maps each project-relative path to a `{"callers": set[str], "callees": set[str]}` dict of project-relative dependency paths. |
| `summary_map` | `dict[str, str \| None]` | Maps each project-relative path to a summary string or `None`. |
| **Returns** | `None` | Writes to disk; no return value. |

**Responsibility:** Produces the single comprehensive `project_knowledge.json` artifact by merging per-file `file_dependencies.json` and `doc.json` data with the symbol-level dependency graph.

**When to use:** Called once at the end of the pipeline to generate the project-wide knowledge file.

**Design decisions:**
- The output JSON contains two top-level arrays: `project_dependencies` (one entry per file, derived from `symbol_deps` and `summary_map`) and `files` (one entry per file that has at least one of `file_dependencies.json` or `doc.json`).
- Files for which neither `file_dependencies.json` nor `doc.json` exists are excluded from `files` with a `logger.warning`; they still appear in `project_dependencies`.
- The `"file"` key is removed from individually loaded JSONs before embedding them, as the canonical `"file"` value is set at the entry's top level.
- Caller/callee sets are converted to sorted lists for deterministic output.
- All file paths in the output use the `"project_name/copy_path"` format via `to_output_path`.
- Paths stored inside `file_dependencies.json` on disk are already in output format (converted during individual file save), so they are embedded as-is without re-conversion.

**Constraints & edge cases:**
- `symbol_deps` must contain a key for every element of `all_file_list`; a missing key raises `KeyError`.
- If `output_path`'s parent directory does not exist, `open` will raise.
- Caller/callee paths stored in `symbol_deps` must be valid project-relative paths accepted by `to_output_path`.

**Output structure:**
```
{
  "project_name": str,
  "project_dependencies": [ { "file", "summary", "callers", "callees" }, ... ],
  "files": [ { "file", "file_dependencies"?, "doc"? }, ... ]
}
```

---

## `build_symbol_level_deps`

**Signature:**
```python
def build_symbol_level_deps(
    base_output_dir: str,
    all_file_list: list[str],
) -> dict[str, dict[str, set[str]]]
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory used to locate each file's `file_dependencies.json`. |
| `all_file_list` | `list[str]` | Project-relative paths of all files to analyze. |
| **Returns** | `dict[str, dict[str, set[str]]]` | Maps each project-relative path to `{"callers": set[str], "callees": set[str]}`, where set elements are project-relative paths of dependent files. |

**Responsibility:** Derives a file-level dependency graph from the symbol-level usage data in each file's `file_dependencies.json`, which is more precise than import-level analysis.

**When to use:** Called once per pipeline run before any consolidated output function, so that the result can be shared across `save_dependency_summary`, `save_dependency_graph_as_mermaid`, and `save_consolidated_json`.

**Design decisions:**
- All files in `all_file_list` are pre-initialized with empty `callers`/`callees` sets, ensuring every key is present in the result regardless of whether a `file_dependencies.json` exists.
- Callee paths come from `callee_usages[*].from`; caller paths come from `caller_usages[*].file`. Both are stored in output format on disk and are converted back to project-relative paths via `output_path_to_rel` before being stored in the returned dict.
- Uses `set` to deduplicate multiple symbol usages pointing to the same file.

**Constraints & edge cases:**
- Files without a `file_dependencies.json` remain with empty sets; no warning is emitted.
- If `callee_usages` or `caller_usages` entries lack the expected key (`"from"` or `"file"`), they are silently skipped.
- The returned sets contain project-relative paths, not output-format paths.

---

## `save_dependency_summary`

**Signature:**
```python
def save_dependency_summary(
    base_output_dir: str,
    all_file_list: list[str],
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
    summary_map: dict[str, str | None],
) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory (used for project name derivation via `to_output_path`). |
| `all_file_list` | `list[str]` | Project-relative paths of all files to include. |
| `output_path` | `str` | Destination file path for the written JSON. |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (same shape as returned by `build_symbol_level_deps`). |
| `summary_map` | `dict[str, str \| None]` | Per-file summary map (same shape as returned by `build_summary_map`). |
| **Returns** | `None` | Writes to disk; no return value. |

**Responsibility:** Produces a lightweight `project_dependency_summary.json` combining only dependency edges and summaries, without the full per-file documentation content included in the consolidated JSON.

**When to use:** Called once per pipeline run for consumers that need dependency structure and summaries but not the full `file_dependencies.json`/`doc.json` content.

**Design decisions:**
- Every file in `all_file_list` is always included, regardless of whether it has a summary; files without summaries appear with `"summary": null`.
- Caller/callee sets are converted to sorted lists for deterministic output.
- Logs the count of files with non-null summaries for observability.

**Constraints & edge cases:**
- `symbol_deps` must contain a key for every element of `all_file_list`; a missing key raises `KeyError`.
- Caller/callee paths in `symbol_deps` must be valid project-relative paths accepted by `to_output_path`.

**Output structure:**
```
{
  "project_name": str,
  "files": [ { "file", "summary", "callers", "callees" }, ... ]
}
```

---

## `save_dependency_graph_as_mermaid`

**Signature:**
```python
def save_dependency_graph_as_mermaid(
    base_output_dir: str,
    output_path: str,
    symbol_deps: dict[str, dict[str, set[str]]],
) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Base output directory (used for project name derivation). |
| `output_path` | `str` | Destination `.md` file path for the Mermaid diagram. |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (same shape as returned by `build_symbol_level_deps`). |
| **Returns** | `None` | Writes to disk; no return value. |

**Responsibility:** Renders the file-level dependency graph as a Mermaid `graph LR` flowchart embedded in a Markdown code fence, enabling visualization in Markdown-aware tools.

**When to use:** Called once per pipeline run after `build_symbol_level_deps` to generate the `dependency_graph.md` artifact.

**Design decisions:**
- Nodes and edges are collected into `set` structures before rendering, so duplicate nodes (a file that is both a caller and a callee of other files) appear only once.
- Node IDs in Mermaid syntax are derived by replacing `/` and `.` with `_` to avoid characters illegal in Mermaid identifiers.
- Node display labels use `copy_path_to_rel` (via the inner `to_display_label`) to show human-readable source-relative paths rather than the internal output-format paths.
- Only callee edges are rendered (directed from caller to callee); caller entries in `symbol_deps` contribute nodes implicitly through the edge source, not through separate iteration of `callers`.
- Nodes and edges are sorted before output for deterministic file content.

**Inner functions:**

| Name | Signature | Purpose |
|---|---|---|
| `to_mermaid_node_id` | `(path: str) -> str` | Replaces `/` and `.` with `_` to produce a valid Mermaid node identifier from an output-format path. |
| `to_display_label` | `(path: str) -> str` | Strips the project-name prefix from an output-format path and converts the remainder back to a source-relative path via `copy_path_to_rel`. |

**Constraints & edge cases:**
- Files with no callees still appear as isolated nodes because the outer loop adds every `file_rel` to `node_set`.
- If `base_output_dir`'s basename contains characters that are invalid in Mermaid IDs, those characters propagate into node IDs (only `/` and `.` are sanitized by `to_mermaid_node_id`).
- The output file is written as plain text joined by `\n`; no trailing newline is added after the closing fence.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`codetwine/output_py/output.py` → `codetwine/utils/file_utils.py`** : path conversion and output directory resolution

The following symbols are imported and used:

- `rel_to_copy_path` — used in `to_output_path()` to convert a project-relative path into the copy-destination directory structure path (`{stem}_{ext}/{filename}` format) before prepending the project name.
- `copy_path_to_rel` — used indirectly via `to_display_label()` in `save_dependency_graph_as_mermaid()` to restore a copy-destination path back to a human-readable source-relative path for Mermaid node labels.
- `output_path_to_rel` — used in `build_symbol_level_deps()` to convert the `from` and `file` fields stored in `file_dependencies.json` (which are in `project_name/copy_path` format) back to project-relative paths when building the caller/callee dependency maps.
- `resolve_file_output_dir` — used in `build_summary_map()`, `save_consolidated_json()`, and `build_symbol_level_deps()` to resolve the absolute output directory path for each file, enabling location of `doc.json` and `file_dependencies.json` artifacts.

## Dependents (modules that import this file)

**`codetwine/pipeline.py` → `codetwine/output_py/output.py`** : orchestrating the full project-level output generation pipeline

- Uses `to_output_path` to convert file paths in dependency analysis results (`file`, `from` in `callee_usages`, `file` in `caller_usages`) to the canonical `project_name/copy_path` format before saving individual `file_dependencies.json` files.
- Uses `build_symbol_level_deps` to construct symbol-level caller/callee dependency maps once, sharing the result across the subsequent output steps.
- Uses `build_summary_map` to collect per-file summaries from `doc.json` files, also shared across output steps.
- Uses `save_dependency_summary` to write a lightweight `project_dependency_summary.json` combining dependencies and summaries.
- Uses `save_dependency_graph_as_mermaid` to produce `dependency_graph.md` as a Mermaid flowchart.
- Uses `save_consolidated_json` to write `project_knowledge.json` combining all dependency info and design documents into a single file.

## Dependency Direction

Both relationships are **unidirectional**:

- `codetwine/output_py/output.py` → `codetwine/utils/file_utils.py` : one-way; `file_utils.py` has no dependency on `output.py`.
- `codetwine/pipeline.py` → `codetwine/output_py/output.py` : one-way; `output.py` has no dependency on `pipeline.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `base_output_dir` argument | `str` | Filesystem path to the project's output root directory; its trailing component is treated as the project name |
| `all_file_list` argument | `list[str]` | Ordered list of project-relative file paths (e.g. `"src/foo.py"`) |
| `file_dependencies.json` (file read) | JSON object on disk | Per-file dependency record written by an earlier pipeline stage; contains `callee_usages` and `caller_usages` arrays with path fields already in `project_name/copy_path` format |
| `doc.json` (file read) | JSON object on disk | Per-file design document; contains at minimum a `"summary"` string field |
| `symbol_deps` argument | `dict[str, dict[str, set[str]]]` | Pre-built symbol-level dependency map keyed by relative path |
| `summary_map` argument | `dict[str, str \| None]` | Pre-built map from relative path to summary text or `None` |

---

## 2. Transformation Overview

### Stage 1 — Path normalisation (`to_output_path`)
Every relative path entering the module is converted to a canonical `"project_name/copy_path"` string. This is done by extracting `os.path.basename(base_output_dir)` as the project name and delegating the copy-path segment to `rel_to_copy_path`.

### Stage 2 — Resolve per-file output directories (`build_symbol_level_deps`, `build_summary_map`)
For each entry in `all_file_list`, `resolve_file_output_dir` maps the relative path to the on-disk directory where that file's analysis artefacts live.

### Stage 3 — Read and invert dependency records (`build_symbol_level_deps`)
Each `file_dependencies.json` is read. The `"from"` field of every `callee_usages` entry is extracted and converted back to a relative path via `output_path_to_rel`, populating the **callees** set for that file. Likewise, the `"file"` field of every `caller_usages` entry populates the **callers** set. The result is a fully populated `deps_map` covering all files in `all_file_list`.

### Stage 4 — Collect summaries (`build_summary_map`)
Each `doc.json` is read and its `"summary"` value extracted. Files whose `doc.json` is absent receive `None`. The result is a flat `{rel_path: summary_or_None}` dict.

### Stage 5 — Assemble output records
Three independent serialisation functions consume the two maps produced in stages 3–4:

- **`save_dependency_summary`** — zips `symbol_deps` and `summary_map` into a lightweight list of per-file dicts and writes `project_dependency_summary.json`.
- **`save_dependency_graph_as_mermaid`** — traverses the callee edges in `symbol_deps` to collect a `node_set` and `edge_set`, converts every path to a Mermaid-safe node ID and a human-readable label, then writes `dependency_graph.md`.
- **`save_consolidated_json`** — builds both a `project_dependencies` list (from `symbol_deps` + `summary_map`) and a `files` list (by re-reading `file_dependencies.json` and `doc.json` for each file and merging them under a single top-level `"file"` key), then writes `project_knowledge.json`.

---

## 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| Return value of `build_symbol_level_deps` | `dict[str, dict[str, set[str]]]` | Callers/callees sets keyed by relative path |
| Return value of `build_summary_map` | `dict[str, str \| None]` | Summary text (or `None`) keyed by relative path |
| Return value of `to_output_path` | `str` | Single `"project_name/copy_path"` string |
| `project_dependency_summary.json` (file write) | JSON | Lightweight dependency + summary list |
| `dependency_graph.md` (file write) | Markdown with fenced Mermaid block | Flowchart of file-level callee edges |
| `project_knowledge.json` (file write) | JSON | Full consolidated analysis: `project_dependencies`, `files` with embedded `file_dependencies` and `doc` sub-objects |

---

## 4. Key Data Structures

### `deps_map` / `symbol_deps` — `dict[str, dict[str, set[str]]]`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| *(outer key)* | `str` | Project-relative file path |
| `"callers"` | `set[str]` | Relative paths of files that call into this file |
| `"callees"` | `set[str]` | Relative paths of files this file calls into |

---

### `summary_map` — `dict[str, str | None]`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| *(outer key)* | `str` | Project-relative file path |
| *(value)* | `str \| None` | Summary text from `doc.json`, or `None` if absent |

---

### Per-file entry in `project_dependencies` / `save_dependency_summary` `files` list
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | File path in `"project_name/copy_path"` format |
| `"summary"` | `str \| None` | Summary text from `doc.json` |
| `"callers"` | `list[str]` | Sorted caller paths in `"project_name/copy_path"` format |
| `"callees"` | `list[str]` | Sorted callee paths in `"project_name/copy_path"` format |

---

### Per-file entry in `save_consolidated_json` `files` list
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | File path in `"project_name/copy_path"` format |
| `"file_dependencies"` | `dict` | Full contents of `file_dependencies.json` with the `"file"` key removed |
| `"doc"` | `dict` | Full contents of `doc.json` with the `"file"` key removed |

---

### Top-level consolidated JSON object
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"project_name"` | `str` | Trailing directory name of `base_output_dir` |
| `"project_dependencies"` | `list[dict]` | One entry per file with callers/callees/summary |
| `"files"` | `list[dict]` | One entry per file with merged `file_dependencies` and `doc` sub-objects |

---

### Mermaid intermediate sets
| Structure | Type | Purpose |
|-----------|------|---------|
| `node_set` | `set[str]` | All unique file paths (in `"project_name/copy_path"` format) that appear as nodes |
| `edge_set` | `set[tuple[str, str]]` | All directed callee edges as `(caller_output_path, callee_output_path)` pairs |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation with logging-and-continue** strategy. Missing files or absent data are treated as non-fatal conditions: the affected entry is either populated with a `null` value or silently skipped, while processing continues for all remaining files. No exceptions are raised to the caller; the only active error signal is a `logger.warning` emitted when an expected analysis result is entirely absent for a given file.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `doc.json` | `doc.json` does not exist for a file in `base_output_dir` | `summary` is set to `None` and processing continues | Yes | That file's summary is `null` in all output JSON |
| Missing `file_dependencies.json` (build_symbol_level_deps) | `file_dependencies.json` does not exist for a file | File is skipped via `continue`; its deps remain empty sets | Yes | File has empty `callers`/`callees` in dependency output |
| Missing `file_dependencies.json` (save_consolidated_json) | `file_dependencies.json` does not exist for a file | `file_dependencies` key is simply omitted from the entry | Yes | Entry appears without dependency data in consolidated JSON |
| Missing `doc.json` (save_consolidated_json) | `doc.json` does not exist for a file | `doc` key is omitted from the entry | Yes | Entry appears without doc data in consolidated JSON |
| Entry with no analysis results (save_consolidated_json) | Both `file_dependencies.json` and `doc.json` are absent, leaving `entry` with only the `file` key | Entry is excluded from `files_list`; `logger.warning` is emitted | Yes | File is absent from `files` array; count mismatch logged |
| Missing `from`/`file` field in usage records | A usage dict in `callee_usages` or `caller_usages` lacks the expected key | Entry is silently skipped via truthiness check | Yes | That specific dependency edge is not recorded |

---

## 3. Design Notes

- **No try-except blocks are present anywhere in the file.** All error tolerance is achieved exclusively through `os.path.exists` guards before file I/O, preventing `FileNotFoundError` from ever being raised rather than catching it after the fact.
- The choice to emit `null` for missing summaries rather than omitting the key entirely reflects a deliberate data-contract decision: downstream consumers can always rely on the `summary` key existing, with `None` signaling absence of LLM-generated content.
- The `logger.warning` for fully absent analysis results is the only case where the handling is made externally observable beyond the structural absence of data, providing a minimal audit trail without disrupting pipeline execution.
- The file count logged at the end of `save_consolidated_json` (`files: {len(files_list)}/{len(all_file_list)}`) serves as a passive diagnostic of how many files were silently degraded.

## Summary

Aggregates per-file analysis results into project-level output artifacts. Public functions: `to_output_path(base_output_dir:str, rel_path:str)→str`; `build_summary_map(base_output_dir:str, all_file_list:list[str])→dict[str,str|None]`; `build_symbol_level_deps(base_output_dir:str, all_file_list:list[str])→dict[str,dict[str,set[str]]]`; `save_dependency_summary(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)`; `save_consolidated_json(...)`, `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)`. Produces `project_dependency_summary.json`, `project_knowledge.json`, and `dependency_graph.md`.
