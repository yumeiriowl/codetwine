# Design Document: codetwine/output.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Converts per-file analysis results (dependency JSON and design documents) stored under the output directory into consolidated project-level output files: a dependency summary JSON, a full knowledge JSON, and a Mermaid dependency graph Markdown.

## 2. When to Use This Module

- **After per-file analysis is complete**, call `build_symbol_level_deps(base_output_dir, all_file_list)` to derive actual symbol-level caller/callee relationships by reading each file's `file_dependencies.json`.
- **To collect file summaries**, call `build_summary_map(base_output_dir, all_file_list)` to read the `summary` field from each file's `doc.json`, returning a mapping of relative path → summary text (or `None` if absent).
- **To produce a lightweight dependency + summary overview**, call `save_dependency_summary(...)` to write `project_dependency_summary.json` combining symbol-level deps and summaries.
- **To produce the full consolidated knowledge file**, call `save_consolidated_json(...)` to write `project_knowledge.json` merging `file_dependencies.json`, `doc.json`, and project-level dependency info for every file.
- **To visualize the dependency graph**, call `save_dependency_graph_as_mermaid(...)` to write a Mermaid `graph LR` flowchart to a Markdown file.
- **To convert a relative file path to the project-scoped output path format**, call `to_output_path(base_output_dir, rel_path)` to obtain a `"project_name/copy_path"` string used throughout all output files.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Converts a project-relative path to `"project_name/copy_path"` format used in all output files. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Reads each file's `doc.json` and returns a mapping of relative path → summary text or `None`. |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Reads each file's `file_dependencies.json` and returns a mapping of relative path → `{"callers": set, "callees": set}` based on actual symbol usage. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a lightweight JSON combining symbol-level dependencies and summaries for every file. |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Writes a full consolidated JSON merging `file_dependencies.json`, `doc.json`, and project-level dependency graph for every file. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]` | `None` | Writes a Mermaid `graph LR` flowchart Markdown file from the symbol-level dependency graph. |

## 4. Design Decisions

- **All output paths use the `"project_name/copy_path"` format.** This mirrors the directory structure created during source copying (via `rel_to_copy_path`) and ensures path references within output files are self-consistent and project-scoped. `to_output_path` is the single point responsible for this conversion, and it is used both internally and by `pipeline.py` when writing individual file results.
- **`build_symbol_level_deps` and `build_summary_map` are computed once and shared.** Both `save_dependency_summary` and `save_consolidated_json` accept pre-built `symbol_deps` and `summary_map` as arguments rather than recomputing them, avoiding redundant disk reads across the multiple output-generation steps.
- **Files with no analysis results are excluded from the `files` list in the consolidated JSON with a warning**, while still appearing in `project_dependencies`. This preserves a complete dependency graph while clearly marking gaps in documentation coverage.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level

| Item | Value |
|---|---|
| Logger | `logging.getLogger(__name__)` — module-scoped logger used for info and warning messages throughout this file |

---

## `to_output_path`

**Signature:**
```python
def to_output_path(base_output_dir: str, rel_path: str) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Absolute or relative path to the base output directory; its final component is used as the project name |
| `rel_path` | `str` | Relative path from the project root to a source file |
| **Returns** | `str` | Path in `"project_name/copy_path"` format |

**Responsibility:** Converts a source-relative file path into the canonical output-path format used throughout all JSON outputs and the Mermaid graph, so every path reference in generated artifacts is consistently namespaced under the project name.

**When to use:** Call this whenever a file's relative path must be written into a JSON or Markdown output as a stable, human-readable identifier.

**Design decisions:**
- The project name is derived solely from the trailing directory component of `base_output_dir` via `os.path.basename`, making it independent of how the caller constructed the path.
- Delegates copy-path formatting entirely to `rel_to_copy_path`, keeping path-shape logic in one place.

**Constraints & edge cases:**
- `base_output_dir` must have at least one path component; passing an empty string results in an empty project-name prefix.
- The `copy_path` segment follows the `{parent}/{stem}_{ext}/{filename}` convention defined by `rel_to_copy_path`.

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
| `base_output_dir` | `str` | Base output directory used to locate per-file `doc.json` files |
| `all_file_list` | `list[str]` | Relative paths (from project root) of all files to consider |
| **Returns** | `dict[str, str \| None]` | Mapping from each relative path to its summary string, or `None` if `doc.json` is absent or has no `"summary"` key |

**Responsibility:** Provides a single consolidated lookup of per-file LLM-generated summaries so that downstream functions (`save_dependency_summary`, `save_consolidated_json`) do not each need to read `doc.json` independently.

**When to use:** Call once after all per-file analysis has run, before calling any function that needs to attach summaries to output entries.

**Design decisions:**
- Every file in `all_file_list` is guaranteed a key in the returned dict, even when its `doc.json` is missing; the value is `None` in that case, enabling uniform downstream handling.
- Uses `doc.get("summary")` so that a `doc.json` that lacks the `"summary"` key also yields `None` rather than raising an error.

**Constraints & edge cases:**
- Files whose `doc.json` exists but is malformed JSON will raise a `json.JSONDecodeError`.
- The `"summary"` value is returned exactly as stored; no type coercion is applied.

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
| `base_output_dir` | `str` | Base output directory for locating per-file artifacts |
| `all_file_list` | `list[str]` | Ordered list of source-relative file paths |
| `output_path` | `str` | Filesystem path where the consolidated JSON will be written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Keyed by relative path; each value has `"callers"` and `"callees"` sets of relative paths (return value of `build_symbol_level_deps`) |
| `summary_map` | `dict[str, str \| None]` | Relative path → summary text or `None` (return value of `build_summary_map`) |

**Responsibility:** Produces the complete project knowledge artifact by merging the symbol-level dependency graph, per-file `file_dependencies.json` content, and per-file `doc.json` content into a single structured JSON file.

**When to use:** Call as the final step in the pipeline when a self-contained, all-in-one project knowledge file is needed.

**Design decisions:**
- The output JSON contains two parallel top-level arrays: `project_dependencies` (lightweight graph entries with summary) and `files` (full per-file artifacts). This separation allows consumers to scan the graph without loading full docs.
- Callers and callees in `project_dependencies` are sorted deterministically.
- A file is included in `files` only when it contributes at least one additional field beyond `"file"` (i.e., at least one of `file_dependencies.json` or `doc.json` was found). Files failing this condition emit a warning log instead of a silent omission.
- The `"file"` key is stripped from both `file_dependencies.json` and `doc.json` before embedding them to avoid redundancy with the top-level `"file"` field.
- Paths in `file_dependencies.json` are stored in output-path format at write time (by `pipeline.py`), so they are used as-is here without reconversion.

**Constraints & edge cases:**
- `symbol_deps` must contain a key for every entry in `all_file_list`; a missing key raises a `KeyError`.
- If neither `file_dependencies.json` nor `doc.json` exists for a file, it is excluded from `files` and a warning is logged.
- The output file is overwritten without confirmation if it already exists.

**Output structure:**

```
{
  "project_name": str,
  "project_dependencies": [
    {
      "file": str,         // output-path format
      "summary": str|null,
      "callers": [str],    // sorted, output-path format
      "callees": [str]     // sorted, output-path format
    }
  ],
  "files": [
    {
      "file": str,
      "file_dependencies": { ... },  // present if file_dependencies.json exists
      "doc": { ... }                 // present if doc.json exists
    }
  ]
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
| `base_output_dir` | `str` | Base output directory for locating per-file `file_dependencies.json` |
| `all_file_list` | `list[str]` | Relative paths of all files to consider |
| **Returns** | `dict[str, dict[str, set[str]]]` | Keyed by relative path; each value is `{"callers": set[str], "callees": set[str]}` where set members are source-relative paths |

**Responsibility:** Aggregates actual symbol-level usage relationships (rather than import-level relationships) across all files into a single dependency map that can be shared by multiple downstream output functions.

**When to use:** Call once before any of the output functions that require `symbol_deps`, so the data is built and traversed only once.

**Design decisions:**
- Callees are derived from the `"from"` field of `callee_usages` entries; callers from the `"file"` field of `caller_usages` entries. Both fields hold output-path format strings and are converted back to relative paths via `output_path_to_rel`.
- Uses `set` for callers and callees to naturally deduplicate multi-symbol references to the same file.
- Every file in `all_file_list` is pre-seeded with empty sets, so callers/callees missing from `file_dependencies.json` never cause `KeyError` in downstream consumers.
- Files without a `file_dependencies.json` are silently skipped (their entries remain with empty sets).

**Constraints & edge cases:**
- Paths stored in `file_dependencies.json` must be in output-path format; if they are not, `output_path_to_rel` may return unexpected values.
- `all_file_list` must be exhaustive; files not in the list are not allocated entries and cannot appear as keys in the result.

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
| `base_output_dir` | `str` | Used to derive project name and convert paths |
| `all_file_list` | `list[str]` | Ordered list of source-relative file paths |
| `output_path` | `str` | Filesystem path where the output JSON will be written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (return value of `build_symbol_level_deps`) |
| `summary_map` | `dict[str, str \| None]` | Relative path → summary text or `None` (return value of `build_summary_map`) |

**Responsibility:** Produces a lightweight project-level JSON combining only the dependency graph and summaries, without the full `file_dependencies.json` and `doc.json` content, for use cases that need a compact overview.

**When to use:** Call when a small, quickly-loadable artifact summarizing project structure is needed, as distinct from the full `save_consolidated_json` output.

**Design decisions:**
- Callers and callees are sorted for deterministic output.
- The info log includes the count of files with non-null summaries, making it easy to audit how many files successfully went through LLM analysis.
- All files in `all_file_list` are unconditionally included in `files`, regardless of whether summaries or dependencies are present; absent values appear as empty lists or `null`.

**Constraints & edge cases:**
- `symbol_deps` must contain a key for every entry in `all_file_list`.
- The output file is overwritten without confirmation if it already exists.

**Output structure:**

```
{
  "project_name": str,
  "files": [
    {
      "file": str,         // output-path format
      "summary": str|null,
      "callers": [str],    // sorted, output-path format
      "callees": [str]     // sorted, output-path format
    }
  ]
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
| `base_output_dir` | `str` | Used to derive project name and convert file paths to output-path format |
| `output_path` | `str` | Filesystem path where the Mermaid Markdown file will be written |
| `symbol_deps` | `dict[str, dict[str, set[str]]]` | Symbol-level dependency map (return value of `build_symbol_level_deps`) |

**Responsibility:** Renders the symbol-level dependency graph as a Mermaid `graph LR` flowchart embedded in a Markdown code fence, producing a human-readable and renderable dependency diagram.

**When to use:** Call after `build_symbol_level_deps` when a visual dependency graph artifact is required.

**Design decisions:**
- Only `callees` edges are traversed to build the graph; `callers` edges are implicitly captured as the reverse of some other file's callee, avoiding duplicate edges.
- Nodes and edges are both sorted before output to ensure deterministic, diff-friendly files.
- Node labels use `copy_path_to_rel` (via `to_display_label`) to show the original source-relative path, making the diagram more readable than the internal output-path format.
- Node IDs are sanitized by replacing `/` and `.` with `_` (via `to_mermaid_node_id`) to comply with Mermaid syntax constraints.

**Constraints & edge cases:**
- If a callee path in `symbol_deps` does not belong to `all_file_list`, it is still added as a node; no filtering against a known-file set is applied.
- Files that have no callees and are not referenced as a callee by any other file do not appear as nodes in the graph.
- The output file is overwritten without confirmation if it already exists.

### Nested helper functions

| Function | Signature | Purpose |
|---|---|---|
| `to_mermaid_node_id` | `(path: str) -> str` | Converts an output-path string to a valid Mermaid node identifier by replacing `/` and `.` with `_` |
| `to_display_label` | `(path: str) -> str` | Strips the project-name prefix and converts the copy-path remainder back to the original source-relative path for use as a human-readable node label |

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- **`codetwine/output_py/output.py` → `codetwine/utils/file_utils.py`** : Requires path conversion and output directory resolution utilities.
  - `rel_to_copy_path` — used in `to_output_path()` to convert a project-relative file path into the copy-destination path structure when constructing the `"project_name/copy_path"` format string.
  - `copy_path_to_rel` — used in `save_dependency_graph_as_mermaid()` (via `to_display_label()`) to restore a copy-destination path back to a human-readable source-relative path for Mermaid node labels.
  - `output_path_to_rel` — used in `build_symbol_level_deps()` to convert output-format paths stored in `file_dependencies.json` (`from` and `file` fields) back to project-relative paths when populating the caller/callee sets.
  - `resolve_file_output_dir` — used in `build_summary_map()`, `save_consolidated_json()`, and `build_symbol_level_deps()` to resolve the absolute path of the per-file output directory (where `doc.json` and `file_dependencies.json` reside).

## Dependents (modules that import this file)

- **`codetwine/pipeline.py` → `codetwine/output_py/output.py`** : Uses this module as the primary output-generation layer for the analysis pipeline.
  - `to_output_path` — called to convert project-relative file paths to the `"project_name/copy_path"` format when rewriting path fields in dependency results (`file`, `from`, and `file` within `callee_usages` and `caller_usages`).
  - `build_symbol_level_deps` — called once to build the shared symbol-level dependency graph (`symbol_deps`) that is subsequently passed to the output-saving functions.
  - `build_summary_map` — called to collect per-file summary text from `doc.json` files into a shared `summary_map` dict.
  - `save_dependency_summary` — called to write the lightweight `project_dependency_summary.json` file combining dependencies and summaries.
  - `save_dependency_graph_as_mermaid` — called to write the `dependency_graph.md` Mermaid flowchart file.
  - `save_consolidated_json` — called to write the full `project_knowledge.json` consolidating all per-file analysis results.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output_py/output.py` → `codetwine/utils/file_utils.py` : one-way; `file_utils.py` has no knowledge of `output.py`.
- `codetwine/pipeline.py` → `codetwine/output_py/output.py` : one-way; `output.py` has no knowledge of `pipeline.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|-------|--------|--------|
| `base_output_dir` | Caller argument | `str` — absolute path to the project's output root directory; its trailing component is the project name |
| `all_file_list` | Caller argument | `list[str]` — project-relative file paths (e.g., `"src/foo.py"`) |
| `symbol_deps` | Caller argument (return value of `build_symbol_level_deps`) | `dict[str, dict[str, set[str]]]` — per-file caller/callee sets keyed by relative path |
| `summary_map` | Caller argument (return value of `build_summary_map`) | `dict[str, str | None]` — per-file summary text keyed by relative path |
| `file_dependencies.json` | File read inside `build_symbol_level_deps` and `save_consolidated_json` | JSON object containing `callee_usages` and `caller_usages` arrays with `from` and `file` fields holding output-format paths |
| `doc.json` | File read inside `build_summary_map` and `save_consolidated_json` | JSON object containing at least a `summary` key and a `file` key |

---

## 2. Transformation Overview

### `build_symbol_level_deps`

1. **Initialize** an empty `{callers: set(), callees: set()}` map for every entry in `all_file_list`.
2. **Read** each file's `file_dependencies.json` from the resolved output directory (`resolve_file_output_dir` → join `"file_dependencies.json"`).
3. **Extract callees** from each element of `callee_usages[*].from`; convert the output-format path back to a relative path via `output_path_to_rel`, and add it to the file's `callees` set.
4. **Extract callers** from each element of `caller_usages[*].file`; convert similarly and add to `callers` set.
5. **Return** the fully populated `deps_map`.

### `build_summary_map`

1. **Resolve** the output directory for each file and locate `doc.json`.
2. **Read** `doc.json` if it exists and extract the `"summary"` value; otherwise record `None`.
3. **Return** the `{file_rel: summary | None}` dict.

### `save_dependency_summary`

1. **Receive** `symbol_deps` and `summary_map` as pre-built inputs.
2. **Convert** each file's entry to output-format paths via `to_output_path`; sort caller/callee lists.
3. **Assemble** a result dict with `project_name` and a `files` list.
4. **Write** to `output_path` as formatted JSON.

### `save_consolidated_json`

1. **Build `project_dependencies`**: iterate `all_file_list`, look up `symbol_deps` and `summary_map`, convert all paths to output format, sort callers/callees → produce `converted_deps` list.
2. **Build `files`**: for each file, load `file_dependencies.json` and `doc.json` from the resolved output directory, strip the redundant `"file"` key from each, and merge both into a single entry dict keyed by output-format `"file"`. Skip files where neither JSON exists (emit a warning).
3. **Assemble** the top-level consolidated dict with `project_name`, `project_dependencies`, and `files`.
4. **Write** to `output_path` as formatted JSON.

### `save_dependency_graph_as_mermaid`

1. **Collect nodes and edges** by iterating `symbol_deps`: every file and every callee becomes a node; every (file → callee) pair becomes a directed edge. Both are stored in sets to deduplicate.
2. **Generate node ID strings** by replacing `/` and `.` with `_` (via `to_mermaid_node_id`).
3. **Generate display labels** by stripping the project-name prefix and converting the copy-path back to a relative path (via `copy_path_to_rel` inside `to_display_label`).
4. **Emit sorted** node declarations and edge arrows into a Mermaid fenced code block.
5. **Write** the Markdown text to `output_path`.

### `to_output_path` (utility, used by all stages above)

Combines `os.path.basename(base_output_dir)` (project name) with `rel_to_copy_path(rel_path)` to produce the canonical `"project_name/copy_path"` string used throughout all output files.

---

## 3. Outputs

| Output | Function | Format |
|--------|----------|--------|
| `dict[str, dict[str, set[str]]]` (return value) | `build_symbol_level_deps` | In-memory dependency map; keys are relative paths, values hold `callers`/`callees` sets of relative paths |
| `dict[str, str | None]` (return value) | `build_summary_map` | In-memory map; keys are relative paths, values are summary strings or `None` |
| `project_dependency_summary.json` (file write) | `save_dependency_summary` | JSON with `project_name` and a `files` array; paths in output format, callers/callees as sorted arrays |
| `project_knowledge.json` (file write) | `save_consolidated_json` | JSON with `project_name`, `project_dependencies` array, and `files` array merging dependency and doc data |
| `dependency_graph.md` (file write) | `save_dependency_graph_as_mermaid` | Markdown file containing a single Mermaid `graph LR` fenced block |
| Log warnings | `save_consolidated_json` | Warning emitted for each file in `all_file_list` that has neither `file_dependencies.json` nor `doc.json` |

---

## 4. Key Data Structures

### `deps_map` / `symbol_deps` — returned by `build_symbol_level_deps`

| Field / Key | Type | Purpose |
|-------------|------|---------|
| *(outer key)* | `str` | Project-relative file path (e.g., `"src/foo.py"`) |
| `"callers"` | `set[str]` | Relative paths of files that call symbols defined in this file |
| `"callees"` | `set[str]` | Relative paths of files whose symbols are called by this file |

### `summary_map` — returned by `build_summary_map`

| Field / Key | Type | Purpose |
|-------------|------|---------|
| *(outer key)* | `str` | Project-relative file path |
| *(value)* | `str | None` | Summary text from `doc.json`, or `None` if absent |

### Entry in `project_dependencies` / `files_list` of `save_dependency_summary`

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | Output-format path (`"project_name/copy_path"`) |
| `"summary"` | `str | None` | Summary text from `summary_map` |
| `"callers"` | `list[str]` | Sorted list of output-format caller paths |
| `"callees"` | `list[str]` | Sorted list of output-format callee paths |

### Entry in `files` list of `save_consolidated_json`

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"file"` | `str` | Output-format path for the file |
| `"file_dependencies"` | `dict` | Contents of `file_dependencies.json` minus its original `"file"` key |
| `"doc"` | `dict` | Contents of `doc.json` minus its original `"file"` key |

### Top-level consolidated JSON object

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `"project_name"` | `str` | Trailing directory name of `base_output_dir` |
| `"project_dependencies"` | `list[dict]` | Per-file caller/callee summary with output-format paths |
| `"files"` | `list[dict]` | Per-file merged dependency and doc data |

### Mermaid graph data (internal to `save_dependency_graph_as_mermaid`)

| Structure | Type | Purpose |
|-----------|------|---------|
| `node_set` | `set[str]` | Deduplicated output-format paths representing graph nodes |
| `edge_set` | `set[tuple[str, str]]` | Deduplicated `(caller_output_path, callee_output_path)` pairs representing directed edges |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** (graceful degradation) strategy. Missing output artifacts (e.g., `doc.json`, `file_dependencies.json`) are treated as expected conditions rather than fatal errors. When a file's analysis results are absent, the file is either silently skipped or included in output with `null`/empty values, and a warning is logged. The pipeline continues processing all remaining files regardless of individual missing artifacts.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `doc.json` | `doc.json` does not exist in a file's output directory | `summary` is set to `None`; file continues to be processed | Yes | That file appears in output with a `null` summary |
| Missing `file_dependencies.json` | `file_dependencies.json` does not exist in a file's output directory | The file's dependency entry is skipped; callers/callees remain empty sets | Yes | That file has no dependency data in the symbol-level deps map |
| No analysis results for a file in consolidated JSON | Neither `file_dependencies.json` nor `doc.json` exists for a file (entry has only the `file` key) | Entry is excluded from `files_list`; a `WARNING` is logged | Yes | File is absent from `files` array in `project_knowledge.json` |
| Missing `from` or `file` field in usage entries | A usage object in `callee_usages` or `caller_usages` lacks the expected key | The usage is silently skipped (falsy check on the value) | Yes | That specific dependency edge is omitted from the graph |

---

## 3. Design Notes

- **No exceptions are raised by this file.** All error conditions are handled through existence checks (`os.path.exists`) before file I/O, and via `.get()` with implicit `None` defaults for missing dictionary keys. This means I/O errors on files that *do* exist (e.g., permission errors, malformed JSON) are **not** explicitly caught and would propagate as unhandled exceptions to the caller.
- The distinction between a file with no results (excluded from `files_list` with a warning) and a file with partial results (included with `null` summary or empty deps) reflects a deliberate tiered degradation: complete absence is flagged, partial absence is silently tolerated.
- The `summary_map` always contains an entry for every file in `all_file_list` (value is `None` if absent), ensuring that downstream consumers never encounter a `KeyError` when accessing summary data.

## Summary

**`codetwine/output.py`** consolidates per-file analysis artifacts into project-level output files.

**Public functions:**
- `to_output_path(base_output_dir: str, rel_path: str) -> str`
- `build_symbol_level_deps(base_output_dir: str, all_file_list: list[str]) -> dict[str, dict[str, set[str]]]`
- `build_summary_map(base_output_dir: str, all_file_list: list[str]) -> dict[str, str | None]`
- `save_dependency_summary(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)`
- `save_consolidated_json(base_output_dir, all_file_list, output_path, symbol_deps, summary_map)`
- `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)`

**Key structures:** `symbol_deps` (`dict[str, {"callers": set[str], "callees": set[str]}]`), `summary_map` (`dict[str, str | None]`). Writes `project_dependency_summary.json`, `project_knowledge.json`, and `dependency_graph.md`.
