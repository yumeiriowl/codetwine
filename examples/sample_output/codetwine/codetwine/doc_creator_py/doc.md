# Design Document: codetwine/doc_creator.py

## Overview & Purpose

## Overview & Purpose

`doc_creator.py` is the **LLM-driven design document generation engine** for CodeTwine. It is responsible for transforming the static analysis artifacts produced by the pipeline (copied source files and `file_dependencies.json` files) into structured, human-readable design documents in both Markdown and JSON formats. It exists as a separate module to cleanly isolate all concerns related to prompt construction, LLM orchestration, topological scheduling, incremental regeneration, and document persistence from the broader pipeline coordination logic in `pipeline.py`.

Its primary responsibilities are:
- Topologically sorting the project's dependency graph so that documents for dependency files are always generated before documents for their dependents, allowing callee summaries to be injected as context.
- Constructing section-level and summary-level LLM prompts by assembling source code, dependency information, callee context, caller context, and template instructions.
- Driving LLM generation with a progressive fallback strategy when context window limits are exceeded.
- Implementing incremental regeneration: reusing existing `doc.json` when neither the file nor any of its dependencies have changed, and syncing manual Markdown edits back to JSON.
- Persisting each document in both `doc.md` and `doc.json` formats.

---

### Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Top-level entry point: topologically sorts files, drives parallel per-level document generation with incremental reuse, and persists all results. |

---

### Private / Internal Functions (key internal structure)

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_topological_sort_by_level` | `project_dep_list: list[dict]` | `list[list[str]]` | Kahn's algorithm BFS over the dependency graph, grouping files by dependency depth level; appends remaining files on cycle detection. |
| `_build_section_prompt` | `section`, `source_code`, `file_deps`, `callee_context`, `implementation_context` | `str` | Assembles the full LLM prompt for a single template section, including source, dependencies, callee/caller usages, and instructions. |
| `_build_summary_prompt` | `file_path`, `section_contents`, `summary_prompt`, `summary_max_chars` | `str` | Assembles the LLM prompt for generating a summary from all already-generated sections. |
| `_build_callee_context_summary` | `file_deps`, `doc_map`, `compact: bool` | `str` | Extracts and concatenates design document summaries of dependency files from `doc_map`; `compact=True` truncates each to 100 characters. |
| `_build_implementation_context` | `file_rel`, `file_output_dir` | `str` | For C/C++ header files, locates and returns the source code of the corresponding implementation file from the output directory. |
| `_generate_section_with_fallback` | `section`, `source_code`, `file_deps`, `callee_context_summary`, `callee_context_compact`, `file_path`, `llm_client`, `implementation_context` | `str \| None` | Attempts LLM section generation with three progressive fallbacks on `ContextWindowExceededError`: full summary → compact summary → no callee context. |
| `_generate_file_doc` | `file_rel`, `file_output_dir`, `doc_map`, `template`, `llm_client` | `dict \| None` | Orchestrates full document generation for one file: reads source and deps, generates all sections and summary, returns the document dict. |
| `_generate_summary` | `file_path`, `section_list`, `template`, `llm_client` | `str \| None` | Generates the document summary from all completed sections via a single LLM call. |
| `_find_source_file` | `output_dir: str`, `file_rel: str` | `str \| None` | Locates the copied source file inside the file's output directory by basename. |
| `_save_doc` | `doc: dict`, `output_dir: str` | `None` | Persists the design document as both `doc.md` (Markdown) and `doc.json` (JSON), with MD written first. |
| `_parse_md_sections` | `md_text: str`, `section_titles: list[str]` | `dict[str, str]` | Splits a `doc.md` file by known `## {title}` headings using regex, returning a title-to-content mapping. |
| `_sync_md_to_json` | `output_dir: str` | `None` | When `doc.md` is newer than `doc.json`, parses the MD and applies changed section content back to the JSON, then re-saves both files. |

---

### Design Decisions

- **Topological level-based parallelism**: Files are processed level by level using `asyncio.gather` with `max_workers` batching. Within a level all files are independent, so they run concurrently; across levels the ordering guarantees callee summaries are always available before dependents are processed.
- **Progressive context fallback**: Rather than failing hard on a context window overflow, the module retries up to three times with decreasing context richness (full summaries → 100-char truncated summaries → no callee context), maximising the chance of successful generation.
- **Incremental regeneration**: A file's document is reused from `doc.json` unless the file itself, or any of its transitive callees regenerated in the current run, has changed. This avoids redundant LLM calls in iterative workflows.
- **MD↔JSON bidirectional sync**: `_sync_md_to_json` allows human editors to modify `doc.md` directly; on the next run, edits are detected via file timestamps and propagated back into `doc.json`, preserving a single source of truth.
- **C/C++ header awareness**: The module detects `.h/.hpp/.hh/.hxx` files and automatically injects the corresponding implementation file's source code into the prompt, giving the LLM visibility into both the declaration and its implementation.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

### Prompt Header/Label Constants
A set of string constants defining the structural components of LLM prompts. These include section headings (`HEADER_TARGET_FILE`, `HEADER_SOURCE_CODE`, `HEADER_CALLEE_USAGES`, `HEADER_CALLER_USAGES`, `HEADER_CALLEE_CONTEXT`, `HEADER_REQUEST`, `HEADER_IMPL_CONTEXT`, `HEADER_DOC_CONTENT`), schema notes, source code labels, and instruction templates. They are defined at module scope to centralize prompt structure and allow modification without hunting through function bodies.

### `_HEADER_EXTENSIONS`
`set[str]` — The set `{".h", ".hpp", ".hh", ".hxx"}`. Defines which file extensions are treated as C/C++ header files, used as the gate for implementation-context lookup.

### `_IMPL_EXTENSIONS`
`list[str]` — `["cpp", "c", "cc", "cxx"]`. The ordered list of implementation file extensions searched when resolving a header file's corresponding implementation. Order determines search priority.

---

## Functions

### `_topological_sort_by_level`
```
_topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]
```
**Responsibility:** Converts a flat project dependency list into a level-ordered (BFS) list of file groups, where level 0 contains files with no dependencies and each subsequent level contains files whose dependencies were all processed at earlier levels. This ordering guarantees that when a file's document is generated, all its dependencies' documents are already available in `doc_map`.

**Input:** Each element of `project_dep_list` must be a dict with keys `"file"` (str) and `"callees"` (list of str file paths). The `"callers"` key is accepted but unused.

**Algorithm design:** Uses Kahn's algorithm on the *reverse* graph (dependents → dependencies) rather than the forward graph. A file enters level 0 when its `reverse_in_degree` is 0, meaning it has no callers depending on it, which corresponds to files with no callees (leaf nodes) in the original dependency graph.

**Edge cases:**
- Files referenced as callees but absent from the top-level list are still added to `all_files` and given their own adjacency/in-degree entries.
- Circular dependencies cause some files to never reach `reverse_in_degree == 0`; these are collected in `remaining`, appended as a final level, and a warning is logged.
- Returns an empty list if `project_dep_list` is empty.

---

### `_build_section_prompt`
```
_build_section_prompt(
    section: dict,
    source_code: str,
    file_deps: dict,
    callee_context: str,
    implementation_context: str = "",
) -> str
```
**Responsibility:** Assembles the complete LLM prompt string for a single template section. It is the single point of control for prompt structure, ensuring all prompts include target file identification, source code, optional dependency context, optional implementation context, and the section-specific instruction.

**Arguments:**
- `section`: Dict containing at minimum `"id"`, `"title"`, and `"prompt"` keys, sourced from the template.
- `source_code`: Full raw source text of the target file.
- `file_deps`: Parsed `file_dependencies.json` dict; `"callee_usages"` and `"caller_usages"` lists are read from it.
- `callee_context`: Pre-built text of dependency design document summaries; if empty string, the callee context section is omitted.
- `implementation_context`: Source code of the corresponding `.cpp`/`.c` file; if empty string (default), the implementation context section is omitted entirely.

**Return:** A single `"\n".join(parts)` string ready to pass to `LLMClient.generate`.

**Design decisions:** `output_path_to_rel` is called on `u['from']` and `u['file']` values before embedding paths into the prompt, ensuring the LLM sees source-relative paths rather than output-directory-encoded paths. The factual accuracy instruction and output language instruction are always appended last.

---

### `_build_summary_prompt`
```
_build_summary_prompt(
    file_path: str,
    section_contents: list[dict],
    summary_prompt: str,
    summary_max_chars: int,
) -> str
```
**Responsibility:** Assembles the LLM prompt for generating a whole-document summary after all sections have been generated. Unlike `_build_section_prompt`, this prompt does not include source code or dependency information — it works solely from the already-generated section texts.

**Arguments:**
- `section_contents`: List of dicts, each with at minimum `"title"` and `"content"` keys.
- `summary_prompt`: Instruction text extracted from the template's `"summary_prompt"` key.
- `summary_max_chars`: Character ceiling for the summary, embedded via `SUMMARY_CHAR_LIMIT`.

**Return:** A `"\n".join(parts)` string.

---

### `_build_callee_context_summary`
```
_build_callee_context_summary(
    file_deps: dict,
    doc_map: dict[str, dict],
    compact: bool = False,
) -> str
```
**Responsibility:** Extracts the `"summary"` field from already-generated design documents for each dependency file and concatenates them into a single context string. This is used to give the LLM high-level knowledge of what each dependency does, without including full dependency source code again.

**Arguments:**
- `file_deps`: Parsed `file_dependencies.json`; `"callee_usages"` entries with `"from"` keys are used to identify dependencies.
- `doc_map`: Maps source-relative file path → design document dict; `"summary"` is extracted from each matching entry.
- `compact`: When `True`, each summary is truncated to 100 characters with `"..."` appended if truncated. This is used as a fallback when context window limits are hit.

**Return:** A newline-joined string of `"- **{rel_path}**: {summary}"` lines, or an empty string if no matching summaries exist.

**Design decisions:** `callee_usages[*]["from"]` paths are in output format, so `output_path_to_rel` is applied before looking up `doc_map` keys. Dependency files are deduplicated via a `set` before iteration, and sorted for deterministic output order. Entries without a `"summary"` value are silently skipped.

---

### `_build_implementation_context`
```
_build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str
```
**Responsibility:** For C/C++ header files, locates and returns the source code of the corresponding implementation file, enabling the LLM to understand how declared interfaces are actually implemented.

**Arguments:**
- `file_rel`: Source-relative path of the target file; its extension is checked against `_HEADER_EXTENSIONS`.
- `file_output_dir`: The encoded output directory for the header file (e.g. `.../MainWindow_h/`); the parent directory is used to search sibling directories for implementation files.

**Return:** Full text content of the first matching implementation file found, or empty string if the file is not a header or no implementation file is found.

**Constraints:** Returns empty string immediately for any non-header extension. Implementation files are searched by iterating `_IMPL_EXTENSIONS` in order; the first match wins. The search path assumes implementation files reside in a sibling directory named `{stem}_{impl_ext}/` within the same parent directory as the header's output directory.

---

### `_generate_section_with_fallback`
```
_generate_section_with_fallback(
    section: dict,
    source_code: str,
    file_deps: dict,
    callee_context_summary: str,
    callee_context_compact: str,
    file_path: str,
    llm_client: LLMClient,
    implementation_context: str = "",
) -> str | None
```
**Responsibility:** Wraps `_build_section_prompt` + `LLMClient.generate` with a three-tier progressive fallback strategy that gracefully handles context window limits without failing the entire document.

**Return:** Generated section text as `str`, or `None` if all three attempts fail.

**Fallback sequence (ordered):**
1. Full callee summary context (`callee_context_summary`)
2. Compact (truncated) callee summary context (`callee_context_compact`)
3. No callee context (empty string)

**Design decisions:** Only `ContextWindowExceededError` triggers fallback; other exceptions from `LLMClient.generate` propagate normally. A `None` result from `generate` (non-exception failure) also falls through to the next attempt via the `if result is not None` guard. Each fallback step is logged at WARNING level.

---

### `_generate_file_doc`
```
_generate_file_doc(
    file_rel: str,
    file_output_dir: str,
    doc_map: dict[str, dict],
    template: dict,
    llm_client: LLMClient,
) -> dict | None
```
**Responsibility:** Orchestrates design document generation for a single file: reads its source and dependency data, builds callee context from `doc_map`, generates each template section via the LLM with fallback, then generates the summary. Returns a complete document dict or `None` on total failure.

**Return structure:** `{"file": str, "sections": list[dict], "summary": str}` where each section dict has `"id"`, `"title"`, and `"content"` keys.

**Constraints:**
- Returns `None` if the source file is not found via `_find_source_file`.
- Returns `None` if `file_dependencies.json` is absent from `file_output_dir`.
- Returns `None` if every section fails generation (`section_list` remains empty).
- Individual failed sections are logged at WARNING and omitted from the output rather than causing total failure.
- Summary failure is non-fatal; `summary` is set to `""` on failure.

---

### `_generate_summary`
```
_generate_summary(
    file_path: str,
    section_list: list[dict],
    template: dict,
    llm_client: LLMClient,
) -> str | None
```
**Responsibility:** Delegates summary prompt construction and LLM invocation to a single location, isolating exception handling for the summary step from the section-generation loop in `_generate_file_doc`.

**Return:** Generated summary string, or `None` if any exception occurs (logged at WARNING). Unlike section generation, no fallback strategy is applied.

**Design note:** `SUMMARY_MAX_CHARS` is read from settings at call time rather than passed as a parameter, making this function's signature independent of that configuration detail.

---

### `_find_source_file`
```
_find_source_file(output_dir: str, file_rel: str) -> str | None
```
**Responsibility:** Resolves the path of the copied source file within a file's output directory, abstracting the fact that only the basename is stored there.

**Return:** Absolute path string if the file exists, `None` otherwise.

**Constraint:** Only the basename of `file_rel` is used; directory components are discarded. No glob or recursive search is performed.

---

### `_save_doc`
```
_save_doc(doc: dict, output_dir: str) -> None
```
**Responsibility:** Persists a generated design document in both Markdown (`doc.md`) and JSON (`doc.json`) formats. Writing Markdown first ensures the JSON file always has a `mtime ≥` the Markdown file's mtime, which is the invariant used by `_sync_md_to_json` to detect user edits.

**Arguments:**
- `doc`: Must contain `"file"` (str), `"sections"` (list of dicts with `"title"` and `"content"`), and optionally `"summary"` (str).

**Design decisions:** The Markdown format uses `##` for section headings to match the heading level assumed by `_parse_md_sections`. The summary is rendered as a dedicated `## Summary` section if non-empty. JSON is written with `indent=2, ensure_ascii=False` for human readability and Unicode support.

---

### `_parse_md_sections`
```
_parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]
```
**Responsibility:** Extracts section content from a Markdown document by splitting on `## {title}` lines that exactly match a known set of titles. This is the parsing half of the MD→JSON sync feature, enabling manual edits to `doc.md` to be reflected back into `doc.json`.

**Arguments:**
- `section_titles`: List of exact section title strings used as delimiters; titles are regex-escaped before use.
- Returns only sections whose `## {title}` heading is present in the text; absent sections are omitted from the result dict.

**Return:** `dict[str, str]` mapping title → stripped content text.

**Design decisions:** Only `##`-level headings that exactly match a known title are used as delimiters; `##` headings inside section content with different titles are treated as content. The regex uses `re.MULTILINE` so `^` matches the start of any line. Content boundaries are determined by the positions of adjacent matches, not by blank lines.

---

### `_sync_md_to_json`
```
_sync_md_to_json(output_dir: str) -> None
```
**Responsibility:** Propagates manual edits made to `doc.md` back into `doc.json` by comparing file modification timestamps and re-parsing the Markdown. This allows human reviewers to edit the rendered Markdown without needing to hand-edit JSON.

**Preconditions:** Both `doc.json` and `doc.md` must exist in `output_dir`; if either is absent, the function returns immediately. Only executes sync when `mtime(doc.md) > mtime(doc.json)`.

**Design decisions:** Section boundaries are considered reliable only when both the section's own title *and* the next section's title (in JSON order) are present in the parsed Markdown; if the next title is absent, that section is skipped to avoid incorrectly attributing content across merged boundaries. After updating `doc.json`, `_save_doc` is called to regenerate `doc.md` from the updated JSON, resetting the timestamp invariant. `json.JSONDecodeError` and `OSError` on JSON read cause a silent return rather than an exception.

---

### `generate_all_docs`
```
generate_all_docs(
    base_output_dir: str,
    project_dep_list: list,
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> None
```
**Responsibility:** Top-level orchestrator for the entire design document generation pipeline. It enforces dependency-order processing (via topological sort), manages parallelism within each level, maintains the `doc_map` for callee context, and implements incremental regeneration logic.

**Arguments:**
- `base_output_dir`: Root directory containing per-file subdirectories as structured by `resolve_file_output_dir`.
- `project_dep_list`: Must conform to the format produced by `save_project_dependencies`; each element has `"file"`, `"callees"`, and optionally `"callers"`.
- `max_workers`: Maximum number of files processed concurrently within a single dependency level. Does not limit across levels.
- `changed_files`: When `None`, all files are unconditionally regenerated. When provided, a file is regenerated only if it or any of its callees appears in `changed_files` or `regenerated_files`.

**Design decisions:** Levels are processed strictly sequentially so that `doc_map` is fully populated for each level before the next begins. Within a level, files are processed in batches of `max_workers` using `asyncio.gather`. The `regenerated_files` set propagates regeneration up the dependency chain: if a callee was regenerated (even if not in `changed_files`), its callers are also regenerated. Existing `doc.json` is reused only if `_is_doc_complete` confirms all expected template sections and a non-empty summary are present.

#### Nested: `_needs_regeneration(file_rel: str) -> bool`
Determines whether a file's document must be regenerated. Returns `True` if `changed_files is None` (full mode), if the file itself is in `changed_files`, or if any callee of the file is in `changed_files` or `regenerated_files`.

#### Nested: `_is_doc_complete(doc: dict) -> bool`
Validates that a loaded `doc.json` contains exactly the set of section IDs defined in the current template and a non-empty summary (when `"summary_prompt"` is present in the template). Any mismatch causes the document to be treated as incomplete and regenerated.

#### Nested: `process_one(file_rel: str) -> tuple[str, dict | None]`
Async per-file task. Checks `_needs_regeneration`, applies `_sync_md_to_json` and attempts reuse of an existing complete doc, then falls back to `_generate_file_doc`. Saves and registers newly generated docs in `regenerated_files`. Returns `(file_rel, doc)` in all cases; `doc` is `None` on failure.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **`codetwine/llm/client.py` (`LLMClient`)**: Used to send assembled prompts to the LLM and retrieve generated text for each document section and summary. All LLM calls in this file are delegated to `LLMClient.generate()`.

- **`codetwine/llm/__init__.py` (`ContextWindowExceededError`)**: Used to catch context window overflow errors thrown by `LLMClient.generate()`, enabling the progressive fallback logic in `_generate_section_with_fallback` (retrying with compressed or omitted callee context).

- **`codetwine/utils/file_utils.py` (`output_path_to_rel`)**: Used when constructing prompts to convert internal output-format paths (e.g., `project_name/stem_ext/file`) back to human-readable source-relative paths for display in callee and caller usage sections.

- **`codetwine/utils/file_utils.py` (`resolve_file_output_dir`)**: Used in `generate_all_docs` to resolve the absolute per-file output directory from a source-relative file path, locating where source copies and `file_dependencies.json` are stored.

- **`codetwine/config/settings.py` (`DOC_TEMPLATE_PATH`)**: Used to locate and load the JSON template file that defines section structure, prompts, and the summary prompt for design document generation.

- **`codetwine/config/settings.py` (`OUTPUT_LANGUAGE`)**: Used to inject the configured output language into each section prompt and the summary prompt via `OUTPUT_LANGUAGE_INSTRUCTION`.

- **`codetwine/config/settings.py` (`SUMMARY_MAX_CHARS`)**: Used to set the character limit in the summary prompt, controlling the length of generated summaries.

- **`codetwine/config/settings.py` (`MAX_WORKERS`)**: Used as the default value for the `max_workers` parameter in `generate_all_docs`, controlling the degree of parallelism when processing files within each dependency level.

### Dependents (what uses this file)

- **`codetwine/pipeline.py` (`generate_all_docs`)**: The pipeline module calls `generate_all_docs` as the LLM-based documentation generation step of the overall CodeTwine pipeline. It passes the base output directory, the project dependency list, an `LLMClient` instance, the worker count, and the set of changed files. This file's function is invoked only when the `ENABLE_LLM_DOC` flag is active.

### Direction of Dependency

The dependency relationship is strictly **unidirectional**. `doc_creator.py` depends on `llm/client.py`, `llm/__init__.py`, `utils/file_utils.py`, and `config/settings.py` — none of those modules reference `doc_creator.py` in return. Similarly, `pipeline.py` depends on `doc_creator.py`, but `doc_creator.py` has no knowledge of `pipeline.py`.

## Data Flow

## Data Flow

### Inputs

| Source | Format | Description |
|---|---|---|
| `base_output_dir` + `file_rel` | Directory path | Per-file output directory resolved via `resolve_file_output_dir` |
| `{output_dir}/{filename}` | Plain text file | Source code of the target file (copied into output dir) |
| `{output_dir}/file_dependencies.json` | JSON | Symbol-level dependency data for the target file |
| `doc_template.json` (path from `DOC_TEMPLATE_PATH`) | JSON | Section definitions (`id`, `title`, `prompt`) and `summary_prompt` |
| `project_dep_list` | `list[dict]` | Project-wide dependency graph (`file`, `callers`, `callees` per entry) |
| `doc_map` | `dict[str, dict]` | Accumulating store of already-generated design documents (keyed by `file_rel`) |
| `changed_files` | `set[str] \| None` | Optional set of changed file paths to restrict regeneration |

---

### Central Data Structures

**`file_deps` (loaded from `file_dependencies.json`)**

| Field | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the target file |
| `callee_usages` | `list[dict]` | Symbols this file imports/calls; each has `name`, `from`, `target_context` |
| `caller_usages` | `list[dict]` | Symbols in this file used by others; each has `name`, `file`, `usage_context` |

**`doc` (design document dict)**

| Field | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the target file |
| `sections` | `list[dict]` | Each entry has `id`, `title`, `content` (LLM-generated text) |
| `summary` | `str` | Short summary generated from all sections |

**`doc_map`** — `dict[str, dict]`
Accumulates completed `doc` dicts keyed by `file_rel`. Used as a read source when building callee context for later-level files.

**`level_list`** — `list[list[str]]`
Output of topological sort. Each inner list is a set of files at the same dependency depth that can be processed in parallel.

---

### Main Transformation Flow

```
project_dep_list
       │
       ▼
_topological_sort_by_level()
       │  Kahn's BFS on reversed dependency graph
       ▼
level_list  ── [ [file_a, file_b], [file_c], [file_d] ... ]
       │
       │  For each level (sequential), batches of max_workers (parallel):
       ▼
process_one(file_rel)
  ├─ _needs_regeneration?  ──No──▶  _sync_md_to_json() ──▶ load & return existing doc.json
  │                                                          (adds to doc_map, skips LLM)
  └─ Yes:
       │
       ▼
_generate_file_doc()
  ├─ Read source file text
  ├─ Read file_dependencies.json  ──▶  file_deps
  ├─ _build_callee_context_summary(file_deps, doc_map)  ──▶  callee_context_summary / compact
  ├─ _build_implementation_context()  ──▶  implementation_context (header files only)
  │
  │  For each section in template["sections"]:
  ▼
_generate_section_with_fallback()
  │  Attempts in order (stops on success):
  │    1. full callee summary context
  │    2. compact callee summary (first 100 chars each)
  │    3. no callee context
  │
  ├─ _build_section_prompt()
  │    Assembles: target file header + source code + (impl context)
  │              + callee_usages list + caller_usages list
  │              + callee design doc summaries
  │              + section instructions + language + accuracy directives
  │    ──▶ prompt string
  │
  └─ LLMClient.generate(prompt)  ──▶  section["content"]  (str | None)
       │
       ▼
  section_list  ──  [ {id, title, content}, ... ]
       │
       ▼
_generate_summary()
  ├─ _build_summary_prompt()
  │    Assembles: target file header + all section contents + summary instructions
  │    ──▶ prompt string
  └─ LLMClient.generate(prompt)  ──▶  summary string
       │
       ▼
  doc  ──  { file, sections, summary }
       │
       ▼
_save_doc(doc, output_dir)
  ├─ Writes  doc.md   (Markdown: H2 per section + Summary)
  └─ Writes  doc.json (JSON dump of doc dict)
       │
       ▼
doc_map[file_rel] = doc   ◀── fed forward to callee-context of later levels
```

---

### Outputs

| Destination | Format | Description |
|---|---|---|
| `{output_dir}/doc.md` | Markdown | Human-readable design document; each template section as `## {title}`, summary appended last |
| `{output_dir}/doc.json` | JSON | Machine-readable `doc` dict (`file`, `sections`, `summary`) |
| `doc_map` (in-memory) | `dict[str, dict]` | Passed forward within `generate_all_docs` so later topological levels can incorporate dependency summaries into their prompts |

---

### Callee Context Construction

`_build_callee_context_summary` reads `callee_usages[*].from` from `file_deps`, deduplicates them, looks each up in `doc_map` (converting output-format paths via `output_path_to_rel`), and concatenates their `summary` fields into a single bullet-list string. This string is injected into the prompt under `## Design Document Summaries of Dependency Files`, giving the LLM knowledge of upstream module responsibilities without repeating their full source code.

## Error Handling

## Error Handling

### Overall Strategy

`doc_creator.py` follows a **graceful degradation** policy throughout. Rather than aborting the entire pipeline on failure, the module is designed to maximize the number of successfully generated documents even in the presence of partial failures. Individual file failures are logged and skipped; section-level failures within a file are similarly skipped, and the remaining sections are still assembled into a document. Only when all sections for a given file fail does the file-level result become `None` and the file treated as unprocessable.

---

### Error Pattern Summary

| Error Type | Handling | Impact |
|---|---|---|
| `ContextWindowExceededError` from LLM | Progressive fallback: retry with full callee summary → compact callee summary → no callee context | Section may lose dependency context but generation continues; only fails if all three attempts are exhausted |
| All fallback attempts exhausted for a section | Section is skipped; a warning is logged | That section is absent from the final document; other sections are unaffected |
| All sections fail for a file | `_generate_file_doc` returns `None`; error is logged | File is printed as `SKIP` and omitted from `doc_map` |
| Summary generation failure (any exception) | Exception is caught, warning is logged, `None` is returned | Document is saved without a summary field (empty string) |
| Missing source file in output directory | Warning logged, `None` returned from `_generate_file_doc` | File is skipped entirely |
| Missing `file_dependencies.json` | Warning logged, `None` returned from `_generate_file_doc` | File is skipped entirely |
| Missing or non-existent output directory in `process_one` | Warning logged, returns `(file_rel, None)` | File is skipped; not added to `doc_map` |
| `doc.json` read failure (`JSONDecodeError`, `OSError`) | Exception is silently swallowed; falls back to regeneration | Triggers a fresh LLM generation pass rather than reusing stale/corrupt data |
| Exception raised from `asyncio.gather` task | Caught as `Exception` instance in results; error is logged | Only the affected concurrent task is skipped; remaining batch results are processed normally |
| Implementation file not found for a header | Empty string returned from `_build_implementation_context` | Header-file prompt is generated without implementation context; no error raised |
| Corrupt or incomplete existing `doc.json` (missing sections or summary) | Detected by `_is_doc_complete`; triggers regeneration | Ensures stale incomplete documents are not silently reused |

---

### Design Considerations

**Fallback isolation at the section level.** The context-window fallback mechanism operates independently per section (`_generate_section_with_fallback`). This means one large section hitting the token limit does not degrade the context available to other sections in the same file.

**Silent swallow on JSON read errors is intentional.** When an existing `doc.json` cannot be parsed or read, the code falls through to regeneration rather than surfacing the error. This prioritizes pipeline continuity over strict error visibility, accepting that a corrupt cache entry is automatically healed.

**Circular dependency handling is advisory, not fatal.** Files involved in circular dependencies are collected into a final processing level with a logged warning. The pipeline continues; topological ordering is merely best-effort in that case.

**Concurrency errors are isolated to tasks.** `asyncio.gather` is called with `return_exceptions=True` implicitly by the explicit isinstance check, ensuring that a crash in one concurrent `process_one` task does not cancel sibling tasks within the same batch.

**`_sync_md_to_json` is defensively non-fatal.** If either file is missing, timestamps are unfavorable, or the JSON is unreadable, the function returns silently without modifying any state.

## Summary

`doc_creator.py` is CodeTwine's LLM-driven design document generation engine. Its sole public entry point, `generate_all_docs`, topologically sorts the project dependency graph, processes files level-by-level in parallel, and generates per-file Markdown and JSON design documents (`doc.md`, `doc.json`). It builds LLM prompts from source code, dependency metadata, and callee summaries aggregated in `doc_map` (a `dict[str, dict]` keyed by relative file path). A three-tier context fallback handles token limit errors. Incremental regeneration reuses existing documents when inputs are unchanged, and bidirectional MD↔JSON sync preserves manual edits.
