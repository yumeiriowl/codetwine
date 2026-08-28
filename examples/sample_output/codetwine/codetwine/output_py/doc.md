# Design Document: codetwine/output.py

# Overview & Purpose

## 1. Module Summary
Aggregates per-file dependency and documentation JSON artifacts produced during analysis into project-wide output formats (consolidated JSON, dependency+summary JSON, and a Mermaid dependency graph).

## 2. When to Use This Module
- **Building a symbol-level dependency graph across the project**: call `build_symbol_level_deps(base_output_dir, all_file_list)` to derive `{file: {"callers": set, "callees": set}}` from each file's `file_dependencies.json`, based on actual symbol usage rather than raw imports.
- **Collecting design-doc summaries for every analyzed file**: call `build_summary_map(base_output_dir, all_file_list)` to get `{file relative path: summary or None}` by reading each file's `doc.json`.
- **Producing a single-file view of dependencies + summaries per file**: call `save_dependency_summary(...)` to write `project_dependency_summary.json`, combining `symbol_deps` and `summary_map` into a lightweight `"files"` array, streamed to disk to avoid holding everything in memory.
- **Producing the full consolidated knowledge artifact**: call `save_consolidated_json(...)` to write a JSON file containing `"project_dependencies"` (dependency graph + summaries) and `"files"` (each file's merged `file_dependencies.json` + `doc.json`), used as the source for `codetwine/knowledge_db.py`'s SQLite build via `build_file_entry` and `to_output_path`.
- **Reading one file's consolidated analysis result**: call `build_file_entry(base_output_dir, file_rel)` to get a single dict with `"file"`, `"file_dependencies"`, and `"doc"` keys (or `None` if no analysis results exist), e.g. when populating a database row.
- **Generating a visual dependency graph**: call `save_dependency_graph_as_mermaid(base_output_dir, output_path, symbol_deps)` to write a Mermaid flowchart Markdown file showing callee relationships between files.
- **Converting a project-relative path into the project's output-facing path format**: call `to_output_path(base_output_dir, rel_path)` to get a `"project_name/copy_path"` string, used elsewhere (e.g. `codetwine/pipeline.py`, `codetwine/knowledge_db.py`) when normalizing paths for output JSON or edge storage.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `to_output_path` | `base_output_dir: str`, `rel_path: str` | `str` | Convert a project-relative path to `"project_name/copy_path"` format. |
| `build_summary_map` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, str \| None]` | Read each file's `doc.json` summary into a `{file_rel: summary}` map. |
| `iter_dependency_entries` | `base_output_dir: str`, `all_file_list: list[str]`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `Iterator[dict]` | Yield one `{file, summary, callers, callees}` entry per file, with all paths in output format. |
| `build_file_entry` | `base_output_dir: str`, `file_rel: str` | `dict \| None` | Merge one file's `file_dependencies.json` and `doc.json` into a single entry (`None` if neither exists). |
| `save_consolidated_json` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Stream-write the full consolidated project knowledge JSON (`project_dependencies` + `files`). |
| `build_symbol_level_deps` | `base_output_dir: str`, `all_file_list: list[str]` | `dict[str, dict[str, set[str]]]` | Compute per-file `{"callers", "callees"}` sets from `callee_usages`/`caller_usages` in each `file_dependencies.json`. |
| `save_dependency_summary` | `base_output_dir: str`, `all_file_list: list[str]`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]`, `summary_map: dict[str, str \| None]` | `None` | Stream-write a lightweight JSON of dependency graph + summaries per file. |
| `save_dependency_graph_as_mermaid` | `base_output_dir: str`, `output_path: str`, `symbol_deps: dict[str, dict[str, set[str]]]` | `None` | Generate and write a Mermaid `graph LR` flowchart Markdown file from the symbol dependency graph. |

## 4. Design Decisions
- **Streaming JSON writers**: `save_consolidated_json` and `save_dependency_summary` manually write JSON array/object boundaries (`{`, `[`, `,\n`, etc.) and serialize one entry at a time via `_write_array_item`/`iter_dependency_entries`, so that only one file's data is held in memory at a time rather than building the full structure before serialization.
- **Shared computation reuse**: `symbol_deps` (from `build_symbol_level_deps`) and `summary_map` (from `build_summary_map`) are computed once by the caller and passed into both `save_consolidated_json` and `save_dependency_summary`, avoiding redundant file re-reads across the two output artifacts.
- **Path normalization at the boundary**: all paths exposed in generated outputs are normalized through `to_output_path`/`output_path_to_rel` (backed by `rel_to_copy_path`/`copy_path_to_rel` from `file_utils.py`), keeping the internal analysis representation (relative paths) separate from the external output representation (`project_name/copy_path`).

# Definition Design Specifications

## `_ARRAY_ITEM_INDENT` (module-level constant)

- **Signature**: `_ARRAY_ITEM_INDENT: str = "    "` (4 spaces)
- **Responsibility**: Defines the indentation prefix applied to each serialized element when manually streaming a JSON array to a file, so the output stays human-readable despite being written incrementally rather than via a single `json.dump`.
- **When to use**: Used internally by `_write_array_item` whenever an array element is appended to an in-progress JSON file.
- **Design decisions**: Kept as a private module constant instead of a hardcoded literal so all array-writing call sites share the same indentation level.
- **Constraints & edge cases**: None; it is a fixed formatting value, not user-configurable.

---

## `to_output_path(base_output_dir: str, rel_path: str) -> str`

- **Responsibility**: Converts a project-relative source path into the externally-visible `"project_name/copy_path"` identifier format used throughout all generated JSON/Markdown artifacts.
- **When to use**: Whenever a file path needs to be written into output artifacts (JSON entries, Mermaid diagrams) instead of the internal relative path representation.
- **Design decisions**: Derives `project_name` from `os.path.basename(base_output_dir)`, tying the output identifier to the output directory's name rather than storing it separately; delegates the copy-path structural transformation entirely to `rel_to_copy_path`.
- **Constraints & edge cases**: `base_output_dir` must be a non-empty path whose basename is meaningful as a project name (e.g., not `"."` or `"/"`, which would produce a degenerate project name).

---

## `build_summary_map(base_output_dir: str, all_file_list: list[str]) -> dict[str, str | None]`

- **Responsibility**: Aggregates each analyzed file's `doc.json` summary field into a single lookup table keyed by relative path, so downstream functions don't need to re-read `doc.json` per file.
- **When to use**: Called once per pipeline run before generating any summary-bearing JSON output (dependency summary, consolidated JSON), then passed by reference to those functions.
- **Design decisions**: Files without a `doc.json` are still added to the map with a `None` value rather than being omitted, guaranteeing every file in `all_file_list` has an entry (simplifies `.get()` calls elsewhere).
- **Constraints & edge cases**: If `doc.json` exists but lacks a `"summary"` key, `doc.get("summary")` yields `None` rather than raising; malformed/non-JSON `doc.json` content will raise `json.JSONDecodeError`.

---

## `iter_dependency_entries(base_output_dir: str, all_file_list: list[str], symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> Iterator[dict]`

- **Signature notes**: `symbol_deps` maps a file's relative path to `{"callers": set[str], "callees": set[str]}` (relative paths of files that use/are used by it). Returns a generator of dicts shaped `{"file": str, "summary": str | None, "callers": list[str], "callees": list[str]}`.
- **Responsibility**: Produces the per-file dependency+summary record structure shared by both `project_dependency_summary.json` and the `"project_dependencies"` section of the consolidated JSON, avoiding duplicated entry-building logic.
- **When to use**: Called by `save_consolidated_json` and `save_dependency_summary` to obtain a uniform stream of dependency entries in `all_file_list` order.
- **Design decisions**: Implemented as a generator (`yield`) rather than returning a list, enabling the incremental/streaming write pattern used by both callers (one file's data processed and released at a time, keeping memory bounded). Caller/callee sets are converted to sorted lists at yield time for deterministic, reproducible JSON output.
- **Constraints & edge cases**: Requires `symbol_deps[file_rel]` to exist for every `file_rel` in `all_file_list` (raises `KeyError` otherwise); `summary_map.get(file_rel)` tolerates missing keys by returning `None`.

---

## `build_file_entry(base_output_dir: str, file_rel: str) -> dict | None`

- **Responsibility**: Merges a single file's `file_dependencies.json` and `doc.json` (if present) into one consolidated record, forming the canonical "one file's full analysis result" unit used both by the consolidated JSON output and by the SQLite knowledge database importer.
- **When to use**: Invoked once per file when building either `project_knowledge.json` (`"files"` array) or the SQLite `files` table row set.
- **Design decisions**: The `"file"` key is hoisted to the top level and stripped out of the nested `file_dependencies`/`doc` dicts (via `pop("file", None)`) to avoid redundant duplication of the same path string three times in one entry. Returns `None` (instead of a partially empty dict) when neither JSON source file exists, letting callers filter out files with no analysis results via a simple `is None` check; a warning is logged in that case.
- **Constraints & edge cases**: If only one of `file_dependencies.json`/`doc.json` exists, the entry still returns non-`None` and simply omits the missing key. Malformed JSON in either file will raise `json.JSONDecodeError`. Path values inside `file_dependencies.json` are assumed already converted to output format by the writer (`pipeline.py`), so no path conversion is done here.

---

## `_write_array_item(f: TextIO, entry: dict, is_first: bool) -> None`

- **Responsibility**: Writes a single JSON object as one element of a manually-streamed top-level array, handling the comma separator between elements and applying consistent indentation.
- **When to use**: Called by `save_consolidated_json` and `save_dependency_summary` for every entry appended to an array section (`"project_dependencies"`, `"files"`) while the enclosing braces/brackets are written separately by the caller.
- **Design decisions**: Uses `is_first` (rather than tracking file position) to decide whether to prepend a comma+newline, keeping the function stateless and reusable across multiple independent array sections within the same file handle. Uses `textwrap.indent` combined with `json.dumps(..., indent=2)` to nest a pretty-printed sub-document under the array's indentation level.
- **Constraints & edge cases**: Assumes `f` is already positioned immediately after the array-opening `[` (i.e., no trailing content management is done here); it never writes the closing bracket or trailing newline for the array — that is the caller's responsibility. `entry` must be JSON-serializable (no `set` or other non-native types); relies on `ensure_ascii=False` for proper Unicode output.

---

## `save_consolidated_json(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

- **Responsibility**: Produces the single `project_knowledge.json` artifact combining project-wide dependency graph info (`"project_dependencies"`) and full per-file analysis results (`"files"`) into one file.
- **When to use**: Invoked once at the end of the pipeline when the consolidated knowledge JSON output format is enabled.
- **Design decisions**: Manually streams the JSON structure via raw file writes (`{`, key literals, array brackets) instead of building the whole dict in memory and calling `json.dump`, so that only one file's `build_file_entry`/`iter_dependency_entries` result is held in memory at any time, per the documented memory constraint. Skips (does not count) files for which `build_file_entry` returns `None`, but still logs the ratio of successfully written files vs. total files requested.
- **Constraints & edge cases**: The generated JSON's validity depends entirely on `_write_array_item` and manual literal writes being correctly balanced (e.g., final file ends with `"\n  ]\n}"` with no trailing comma) — if `all_file_list` is empty, the array sections will be written as empty arrays with no dangling commas since the comma is only added when `is_first` is `False`. Does not close/flush explicitly beyond the `with` block's automatic file close.

---

## `build_symbol_level_deps(base_output_dir: str, all_file_list: list[str]) -> dict[str, dict[str, set[str]]]`

- **Signature notes**: Returns a mapping from each file's relative path to `{"callers": set[str], "callees": set[str]}`, where the sets contain relative paths (not output-format paths) of dependent/dependency files.
- **Responsibility**: Derives an accurate, symbol-usage-based dependency graph (as opposed to raw import-level dependencies) by inspecting each file's recorded `callee_usages`/`caller_usages` in `file_dependencies.json`.
- **When to use**: Called once per pipeline run to build the shared dependency graph consumed by `iter_dependency_entries` and `save_dependency_graph_as_mermaid`.
- **Design decisions**: Pre-initializes every file in `all_file_list` with empty `callers`/`callees` sets before scanning, guaranteeing every requested file has an entry even if its `file_dependencies.json` is missing or contains no usages. Converts the output-format paths found in `usage["from"]`/`usage["file"]` back to relative paths via `output_path_to_rel`, since `file_dependencies.json` stores paths already in output format (per `pipeline.py`'s writing behavior) while this function's internal representation and return keys use plain relative paths. Silently skips files with a missing `file_dependencies.json` (`continue`), leaving that file's entry with empty dependency sets.
- **Constraints & edge cases**: Assumes usage entries missing the `"from"`/`"file"` key or having falsy values are simply ignored (no error). Because `deps_map` is pre-populated only for files in `all_file_list`, if `callee_usages`/`caller_usages` reference a file *not* in `all_file_list`, that referenced file's path is still added to the current file's `callees`/`callers` set, but no reciprocal entry is created for it in `deps_map` (it won't appear as a top-level key).

---

## `save_dependency_summary(base_output_dir: str, all_file_list: list[str], output_path: str, symbol_deps: dict[str, dict[str, set[str]]], summary_map: dict[str, str | None]) -> None`

- **Responsibility**: Produces the lightweight `project_dependency_summary.json` artifact containing only the dependency graph plus one-line summaries per file (no full `file_dependencies`/`doc` payloads), intended as a smaller/faster-to-load overview than the consolidated JSON.
- **When to use**: Invoked once per pipeline run to generate a compact project overview, independent of (and typically before) the full consolidated JSON generation.
- **Design decisions**: Streams the array using the same `_write_array_item`/`iter_dependency_entries` pair as `save_consolidated_json`'s `"project_dependencies"` section, ensuring structural consistency between the two artifacts' dependency-entry format. Logs both the number of files written and the count of files that actually had a non-`None` summary, giving visibility into doc-generation coverage.
- **Constraints & edge cases**: `written_count` here always equals `len(all_file_list)` (every dependency entry is written, unlike `build_file_entry` in `save_consolidated_json` which can skip files); the log line's `files:` count therefore does not reflect analysis-result availability, only summary presence via `with summary`.

---

## `save_dependency_graph_as_mermaid(base_output_dir: str, output_path: str, symbol_deps: dict[str, dict[str, set[str]]]) -> None`

- **Responsibility**: Renders the symbol-level dependency graph as a Mermaid `graph LR` flowchart embedded in a Markdown code block, for human-readable visualization of file-to-file dependencies.
- **When to use**: Invoked once per pipeline run, after `build_symbol_level_deps`, to produce `dependency_graph.md`.
- **Design decisions**:
  - Builds `node_set`/`edge_set` by iterating only `deps["callees"]` (not `callers`), since caller/callee relationships are symmetric duplicates in `symbol_deps` — this avoids emitting duplicate edges in both directions.
  - Converts every path to the `"project_name/copy_path"` output format via `to_output_path` before building node IDs/labels, so the graph vocabulary matches other output artifacts.
  - Nodes and edges are collected into `set`s first and only sorted at render time, guaranteeing deterministic, duplicate-free output regardless of dict/set iteration order.
  - Two nested helper functions are defined for this rendering pass only (not reusable elsewhere):
    - **`to_mermaid_node_id(path: str) -> str`**: Sanitizes a path string into a Mermaid-safe node identifier by replacing `/` and `.` with `_` (since Mermaid node IDs cannot contain those characters).
    - **`to_display_label(path: str) -> str`**: Strips the `project_name/` prefix and reverses the copy-path transformation (via `copy_path_to_rel`) to show the original human-readable relative path as the node's visible label, falling back to the raw path unchanged if it doesn't contain the expected `/` separator.
- **Constraints & edge cases**: If `symbol_deps` is empty, the output degenerates to a Mermaid block with only the `graph LR` line and no nodes/edges. Node ID collisions are theoretically possible if two distinct paths sanitize to the same string after `/`/`.` replacement (not guarded against). The file is written with `f.write("\n".join(line_list))`, with no trailing newline at end of file.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/output.py` → `codetwine/utils/file_utils.py` (`rel_to_copy_path`): used in `to_output_path` to convert a project-relative path into the copy-destination directory structure segment that forms the "project_name/copy_path" output format.
- `codetwine/output.py` → `codetwine/utils/file_utils.py` (`copy_path_to_rel`): used in `save_dependency_graph_as_mermaid`'s inner `to_display_label` helper to restore a copy-destination path back to the original source-relative path for display labels in the Mermaid diagram.
- `codetwine/output.py` → `codetwine/utils/file_utils.py` (`output_path_to_rel`): used in `build_symbol_level_deps` to convert callee/caller file references stored in "project_name/copy_path" format (from `file_dependencies.json`) back into project-relative paths for the internal dependency map.
- `codetwine/output.py` → `codetwine/utils/file_utils.py` (`resolve_file_output_dir`): used in `build_summary_map`, `build_file_entry`, and `build_symbol_level_deps` to locate each file's per-file output directory (containing `doc.json` and `file_dependencies.json`) from its relative path.

### Dependents (modules that use this file)

- `codetwine/knowledge_db.py` → `codetwine/output.py` (`build_file_entry`): retrieves each file's consolidated file/doc/dependency entry to build one row of the SQLite knowledge database.
- `codetwine/knowledge_db.py` → `codetwine/output.py` (`to_output_path`): converts relative file paths and dependency edge targets into the "project_name/copy_path" format used when inserting file edge rows into the database.
- `codetwine/pipeline.py` → `codetwine/output.py` (`to_output_path`): converts file paths and usage entries' "from"/"file" fields into output-format paths during dependency result processing.
- `codetwine/pipeline.py` → `codetwine/output.py` (`build_symbol_level_deps`): builds the shared symbol-level caller/callee dependency map used across subsequent summary, Mermaid, and consolidated JSON generation steps.
- `codetwine/pipeline.py` → `codetwine/output.py` (`build_summary_map`): builds the shared map of file relative paths to their doc.json summaries, reused by later output steps.
- `codetwine/pipeline.py` → `codetwine/output.py` (`save_dependency_summary`): generates the lightweight `project_dependency_summary.json` combining symbol-level dependencies and file summaries.
- `codetwine/pipeline.py` → `codetwine/output.py` (`save_dependency_graph_as_mermaid`): generates the Mermaid flowchart Markdown file representing the symbol-level dependency graph.
- `codetwine/pipeline.py` → `codetwine/output.py` (`save_consolidated_json`): generates the full `project_knowledge.json` consolidating dependency graph, summaries, and per-file analysis results.

### Dependency Direction

All relationships are unidirectional. `codetwine/output.py` depends on `codetwine/utils/file_utils.py` for path-conversion utilities, and `codetwine/knowledge_db.py` / `codetwine/pipeline.py` depend on `codetwine/output.py` for building and writing analysis output artifacts. There is no circular dependency: `file_utils.py` does not depend on `output.py`, and `output.py` does not depend on `knowledge_db.py` or `pipeline.py`.

# Data Flow

## 1. Inputs

- **`base_output_dir: str`** — Base output directory whose trailing path component is treated as the project name; used to resolve per-file output directories and to build `"project_name/copy_path"` style paths.
- **`all_file_list: list[str]`** — Ordered list of project-relative file paths (e.g. `"src/foo.py"`) that determines processing/output order for all list-producing functions.
- **`file_rel: str`** — A single project-relative file path passed to per-file helpers (`build_file_entry`, and internally in `build_summary_map`/`build_symbol_level_deps`).
- **On-disk JSON artifacts** (read via `open()` + `json.load`), located under `resolve_file_output_dir(base_output_dir, file_rel)`:
  - `doc.json` — expected to contain a `"summary"` key (and possibly a `"file"` key removed by `build_file_entry`).
  - `file_dependencies.json` — expected to contain `"callee_usages"` (list of dicts with `"from"`) and `"caller_usages"` (list of dicts with `"file"`), plus a `"file"` key (removed by `build_file_entry`). Paths inside are already in output (`project_name/copy_path`) format.
- **`symbol_deps: dict[str, dict[str, set[str]]]`** — Precomputed result of `build_symbol_level_deps`, passed into several functions (`iter_dependency_entries`, `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`) rather than recomputed.
- **`summary_map: dict[str, str | None]`** — Precomputed result of `build_summary_map`, passed into functions that need per-file summaries.
- **`output_path: str`** — Destination file path for JSON/Markdown outputs.

## 2. Transformation Overview

The module has two independent transformation pipelines that converge in higher-level "save" functions, plus a path-format helper used throughout.

**Stage A — Path format conversion**
`to_output_path` combines `os.path.basename(base_output_dir)` (project name) with `rel_to_copy_path(rel_path)` (delegated to `file_utils`) to produce the canonical `"project_name/copy_path"` string used everywhere downstream. This is the single normalization point for all outward-facing file identifiers.

**Stage B — Summary extraction (`build_summary_map`)**
For each `file_rel` in `all_file_list`: resolve its output directory → check for `doc.json` → if present, load JSON and extract `"summary"` (else `None`) → accumulate into a flat `{file_rel: summary_or_None}` dict. This dict is held entirely in memory before being handed to later stages.

**Stage C — Symbol-level dependency extraction (`build_symbol_level_deps`)**
Initialize `{file_rel: {"callers": set(), "callees": set()}}` for every file. For each `file_rel`, if `file_dependencies.json` exists, load it and:
- iterate `callee_usages`, take each `"from"` field, convert it back to a relative path via `output_path_to_rel`, and add to that file's `"callees"` set.
- iterate `caller_usages`, take each `"file"` field, convert via `output_path_to_rel`, and add to that file's `"callers"` set.

This produces a per-file adjacency map keyed by relative path, with values converted back from output-path format to relative-path format (inverse of Stage A), so it can be reused/merged consistently regardless of how paths were stored on disk.

**Stage D — Dependency-entry generation (`iter_dependency_entries`, a generator)**
For each `file_rel`, looks up `symbol_deps[file_rel]` and `summary_map.get(file_rel)`, and re-applies Stage A (`to_output_path`) to the file itself and to every caller/callee (sorted), yielding one dict per file lazily (streaming, not batched).

**Stage E — Consolidated file-entry generation (`build_file_entry`)**
Per file: resolve output dir → conditionally load `doc.json` and `file_dependencies.json` (already in output-path format on disk, so no re-conversion needed) → strip redundant `"file"` sub-keys → merge under one entry keyed by `to_output_path(...)` at the top level. Returns `None` (with a warning) if neither file exists, signaling "no analysis result" to callers.

**Stage F — Streaming JSON array serialization (`_write_array_item` + the `save_*` functions)**
`save_consolidated_json` and `save_dependency_summary` open the output file, write JSON object/array boilerplate by hand (not via a single `json.dump`), then iterate through Stage D/E generators/loops one entry at a time, serializing each entry independently with `json.dumps` + `textwrap.indent`, and writing commas/newlines to keep only one entry in memory at a time. `save_consolidated_json` writes two arrays (`project_dependencies` via Stage D, then `files` via Stage E); `save_dependency_summary` writes one array (`files` via Stage D only).

**Stage G — Graph aggregation for Mermaid (`save_dependency_graph_as_mermaid`)**
Iterate `symbol_deps.items()`; for each file and its callees, apply Stage A to build a `node_set` (unique output-path strings) and `edge_set` (unique `(caller_output_path, callee_output_path)` tuples). Then, for rendering, each node/edge path is transformed into a Mermaid-safe ID (`to_mermaid_node_id`: slashes/dots → underscores) and a human-readable label (`to_display_label`: strips project-name prefix and restores original relative path via `copy_path_to_rel`). These are assembled into a list of Mermaid syntax lines and written as one Markdown string.

No async/parallel processing is used; all fan-out is via sequential loops/generators, and results are merged only within simple dict/set accumulators.

## 3. Outputs

- **`build_summary_map`** → returns `dict[str, str | None]` in memory (no file writes).
- **`build_symbol_level_deps`** → returns `dict[str, dict[str, set[str]]]` in memory (no file writes).
- **`iter_dependency_entries`** → yields `dict` objects (generator, not materialized as a list); consumed by callers.
- **`build_file_entry`** → returns a `dict` (or `None`) in memory per call; also emits a `logger.warning` when no data is found.
- **`save_consolidated_json`** → writes one JSON file at `output_path` with top-level keys `"project_name"`, `"project_dependencies"` (list), `"files"` (list); emits a `logger.info` summary line. No return value.
- **`save_dependency_summary`** → writes one JSON file at `output_path` with top-level keys `"project_name"`, `"files"` (list); emits a `logger.info` summary line. No return value.
- **`save_dependency_graph_as_mermaid`** → writes one Markdown file at `output_path` containing a fenced ```` ```mermaid ```` code block with `graph LR`, node declarations, and edge declarations. No return value.
- **`to_output_path`** → returns a `str` in `"project_name/copy_path"` format; used pervasively by this module and by external callers (`pipeline.py`, `knowledge_db.py`).

## 4. Key Data Structures

**`summary_map` — `dict[str, str | None]`**

| Field / Key | Type | Purpose |
|---|---|---|
| key: `file_rel` | `str` | Project-relative path of a file |
| value: summary | `str \| None` | Summary text extracted from that file's `doc.json`, or `None` if missing |

**`symbol_deps` — `dict[str, dict[str, set[str]]]`**

| Field / Key | Type | Purpose |
|---|---|---|
| key: `file_rel` | `str` | Project-relative path of a file |
| value: `{"callers": ..., "callees": ...}` | `dict[str, set[str]]` | Per-file dependency sets |
| `"callers"` | `set[str]` | Relative paths of files that call into this file |
| `"callees"` | `set[str]` | Relative paths of files this file calls into |

**Dependency entry (yielded by `iter_dependency_entries`)**

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | This file's path in `"project_name/copy_path"` format |
| `"summary"` | `str \| None` | Summary text from `summary_map`, or `None` |
| `"callers"` | `list[str]` | Sorted list of caller file paths in output-path format |
| `"callees"` | `list[str]` | Sorted list of callee file paths in output-path format |

**Consolidated file entry (returned by `build_file_entry`)**

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | This file's path in `"project_name/copy_path"` format |
| `"file_dependencies"` | `dict` (optional) | Contents of `file_dependencies.json` with its own `"file"` key removed |
| `"doc"` | `dict` (optional) | Contents of `doc.json` with its own `"file"` key removed |

**`file_dependencies.json` on-disk schema (as consumed)**

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | File identifier (removed when building consolidated entry) |
| `"callee_usages"` | `list[dict]` | Each dict has `"from"`: str — output-path of the file defining a used symbol |
| `"caller_usages"` | `list[dict]` | Each dict has `"file"`: str — output-path of the file calling into this file |

**`doc.json` on-disk schema (as consumed)**

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | File identifier (removed when building consolidated entry) |
| `"summary"` | `str` | Design/document summary text for the file |

**Mermaid graph intermediate structures**

| Field / Key | Type | Purpose |
|---|---|---|
| `node_set` | `set[str]` | Unique file paths (output-path format) participating in the graph |
| `edge_set` | `set[tuple[str, str]]` | Unique `(caller_output_path, callee_output_path)` pairs |
| `line_list` | `list[str]` | Accumulated Mermaid syntax lines, joined into final Markdown output |

**Consolidated JSON output file top-level schema**

| Field / Key | Type | Purpose |
|---|---|---|
| `"project_name"` | `str` | Basename of `base_output_dir` |
| `"project_dependencies"` | `list[dict]` | List of dependency entries (see above) |
| `"files"` | `list[dict]` | List of consolidated file entries (see above) |

**Dependency-summary JSON output file top-level schema**

| Field / Key | Type | Purpose |
|---|---|---|
| `"project_name"` | `str` | Basename of `base_output_dir` |
| `"files"` | `list[dict]` | List of dependency entries (see above) |

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation with logging-and-continue** strategy rather than fail-fast. There are no explicit try-except blocks anywhere in the module; instead, error handling is achieved through defensive conditional checks (`os.path.exists`, `.get()` with defaults, `dict.pop(..., None)`) before performing operations that could otherwise raise exceptions. Missing per-file artifacts (`doc.json`, `file_dependencies.json`) are treated as expected, normal conditions rather than failures — the pipeline is designed to produce partial, best-effort output even when some files lack complete analysis results. Only one case (`build_file_entry` finding neither JSON file) triggers an explicit `logger.warning`, signaling a soft failure that is surfaced to the caller as `None` for the caller to skip, without stopping the overall consolidation process. There is no retry logic; any I/O or JSON parsing errors (e.g., malformed JSON, permission issues) are left unhandled and will propagate up and terminate the process, since the file assumes upstream stages produce well-formed JSON.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing `doc.json` | File has no generated design document (LLM not used or generation failed) | `os.path.exists` check; `summary` set to `None`; no exception raised | Yes | Entry proceeds with `"summary": null`; downstream summary counts reflect the gap |
| Missing `file_dependencies.json` | File has no dependency analysis output | `os.path.exists` check in `build_symbol_level_deps` and `build_file_entry`; skipped (`continue`) or key omitted from entry | Yes | Entry omits `"file_dependencies"` key or file contributes empty caller/callee sets |
| Both `doc.json` and `file_dependencies.json` missing | File was never successfully analyzed | `build_file_entry` returns `None` after logging a warning | Yes | Caller (`save_consolidated_json`) skips the file via `if entry is None: continue`; `written_count` differs from total file count |
| Missing `symbol_deps` entry for a file | `all_file_list` contains a file not present in `symbol_deps` (contract violation between caller and this file) | No handling; direct dict indexing (`symbol_deps[file_rel]`) | No | Raises `KeyError`, halting the entire consolidation process |
| Malformed/unreadable JSON file | Corrupted or invalid `doc.json`/`file_dependencies.json` content | No handling; `json.load` called directly | No | Raises `JSONDecodeError`/`OSError`, halting the process |
| Missing `"from"`/`"file"` keys in usage entries | Dependency JSON entries lack expected keys | `.get()` with implicit `None`, guarded by `if callee_file:` / `if caller_file:` | Yes | Entry silently skipped from callee/caller sets, no crash |

## 3. Design Notes

- **Absence is expected, not exceptional**: The design treats missing per-file JSON artifacts as a normal outcome of earlier pipeline stages (e.g., LLM generation may be skipped or fail for some files), so existence checks substitute for exception handling in the common case.
- **Streaming write with partial skip**: `save_consolidated_json` writes entries incrementally as they are read, and simply omits files with no analysis results (`entry is None`) rather than aborting, preserving memory efficiency and maximizing output completeness even under partial data availability.
- **Warning as the only alerting mechanism**: A single `logger.warning` call marks the one case considered noteworthy enough to flag (a file with zero analysis output), while all other "missing data" scenarios are handled silently, reflecting a policy that only fully-unanalyzed files warrant operator attention.
- **No defense against structural contract violations**: The file assumes `all_file_list`, `symbol_deps`, and `summary_map` are mutually consistent (as guaranteed by the caller in `pipeline.py`) and does not guard against `KeyError` from mismatched keys, reflecting a fail-fast boundary at the integration level rather than within this module.
- **No retry or fallback for I/O/JSON errors**: Since `json.load` and file open calls are unguarded, any corruption or I/O failure is allowed to propagate and terminate the run, indicating that data integrity of previously-written intermediate JSON files is assumed rather than verified.

# Summary

Aggregates per-file dependency/doc JSON into project-wide outputs (consolidated JSON, dependency summary, Mermaid graph). Key functions: `to_output_path(base_output_dir:str, rel_path:str)->str`, `build_summary_map(base_output_dir:str, all_file_list:list[str])->dict[str,str|None]`, `build_symbol_level_deps(...)->dict[str,dict[str,set[str]]]`, `iter_dependency_entries(...)->Iterator[dict]`, `build_file_entry(...)->dict|None`, `save_consolidated_json`, `save_dependency_summary`, `save_dependency_graph_as_mermaid`. Core structures: `symbol_deps` (callers/callees sets), `summary_map`, dependency entries `{file,summary,callers,callees}`.
