# Design Document: codetwine/doc_creator.py

## Overview & Purpose

## Overview & Purpose

`doc_creator.py` is the design-document generation engine for the codetwine pipeline. Its sole responsibility is to take the dependency graph and copied source files produced by earlier pipeline stages and drive an LLM to write structured, multi-section design documents for every file in the project. It exists as a separate module because document generation is an isolated, asynchronous, LLM-heavy concern with its own scheduling logic (topological ordering, batched parallelism, incremental regeneration, and context-window fallback) that would not belong in dependency analysis or file-scanning code.

### Main Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Top-level entry point: topologically sorts files, generates documents level-by-level in bounded parallel batches, reuses unchanged docs, and saves results as JSON + Markdown |

All other functions in the module are module-private helpers (prefixed `_`) called only from `generate_all_docs` or each other.

### Key Internal Helpers (private)

| Name | Brief Responsibility |
|---|---|
| `_topological_sort_by_level` | Kahn's BFS over the dependency graph; returns files grouped by dependency depth level |
| `_build_section_prompt` | Assembles the full LLM prompt for one template section (source, dependencies, callee context, caller info, instructions) |
| `_build_summary_prompt` | Assembles the LLM prompt for generating a one-paragraph document summary |
| `_build_callee_context_summary` | Collects dependency-file summaries from `doc_map` for use as context; supports compact truncation |
| `_build_implementation_context` | For C/C++ header files, reads the paired `.cpp`/`.c` source to include in the prompt |
| `_generate_section_with_fallback` | Attempts LLM section generation up to three times with progressively reduced context on `ContextWindowExceededError` |
| `_generate_file_doc` | Orchestrates per-file document generation: reads source + deps JSON, iterates sections, calls summary generation |
| `_generate_summary` | Generates the condensed summary paragraph for a completed document |
| `_find_source_file` | Locates the copied source file inside the output directory |
| `_save_doc` | Serializes a document dict to both `doc.md` (Markdown) and `doc.json` |
| `_parse_md_sections` | Splits a `doc.md` text into a title→content dict using known section headings as delimiters |
| `_sync_md_to_json` | Propagates manual edits made to `doc.md` back into `doc.json` when the MD file is newer |

### Design Decisions

1. **Topological level scheduling.** Files are processed level-by-level (dependencies before dependents) using Kahn's algorithm so that each file can reference already-generated summaries of its dependencies as LLM context. Files within the same level are independent and run in parallel batches of `max_workers`.

2. **Progressive context-window fallback.** `_generate_section_with_fallback` retries a failed LLM call up to three times: first with full callee summaries, then with summaries truncated to 100 characters, then with no callee context at all. This avoids hard failures on large files without requiring manual tuning.

3. **Incremental regeneration.** When `changed_files` is supplied, a file is regenerated only if it or any of its direct callees changed or were themselves regenerated in the current run. Unchanged files reuse their existing `doc.json`, making iterative runs fast.

4. **Bidirectional MD ↔ JSON sync.** `_save_doc` writes both formats; `_sync_md_to_json` detects when a user has manually edited `doc.md` (via mtime comparison) and propagates those edits back to `doc.json`, keeping both representations consistent without requiring users to edit raw JSON.

5. **C/C++ header awareness.** For header files (`.h`, `.hpp`, `.hh`, `.hxx`), the module locates and injects the paired implementation file's source code into the prompt, giving the LLM concrete implementation detail to reference when documenting declarations-only headers.

## Definition Design Specifications

## Definition Design Specifications

---

### Module-Level Constants

String constants define the fixed text fragments assembled into LLM prompts. They are declared at module scope so prompt wording can be updated without touching function logic. Constants cover: target file header, source code heading, callee/caller usage headings and schema notes, source code labels, callee context heading and note, request heading, section request template, output language instruction, factual accuracy instruction, implementation context heading and note, doc content heading, and summary character limit.

**`_HEADER_EXTENSIONS`** — `set[str]`
Canonical set of C/C++ header file extensions (`{".h", ".hpp", ".hh", ".hxx"}`). Used as the gate condition before searching for a corresponding implementation file.

**`_IMPL_EXTENSIONS`** — `list[str]`
Ordered list of implementation file extensions (`["cpp", "c", "cc", "cxx"]`) tried in sequence when locating a counterpart implementation file. Order determines search priority.

---

### `_topological_sort_by_level`

**Signature:** `(project_dep_list: list[dict]) -> list[list[str]]`

Produces a breadth-first level ordering of all files in the dependency graph so that every file's dependencies appear at a strictly earlier level. Level 0 contains leaf files (no outgoing dependencies); each subsequent level contains files whose dependencies have all been assigned to earlier levels.

**Design decisions:**
- Uses Kahn's algorithm on the *reverse* dependency graph (caller → callee direction is reversed) so that files with no callees receive in-degree 0 and become the first level.
- Files in each level are sorted alphabetically to produce deterministic output.
- Files remaining after the main BFS pass (i.e., participants in a cycle) are appended as a final level and a warning is logged rather than raising an error, preserving forward progress.

**Edge cases:** Files that appear only as callees (never as a top-level entry in `project_dep_list`) are added to `all_files` via the callee traversal and receive their own adjacency and in-degree entries. An empty input produces an empty return list.

---

### `_build_section_prompt`

**Signature:** `(section: dict, source_code: str, file_deps: dict, callee_context: str, implementation_context: str = "") -> str`

Assembles the complete LLM prompt for one template section by concatenating fixed headings, source code, optional implementation context, callee/caller usage blocks, optional callee design-document summaries, and the section-specific instruction. Returns the fully assembled string.

**Design decisions:**
- Each block is included only when its data is non-empty, keeping prompts minimal when context is unavailable.
- The output language instruction and factual accuracy instruction are always appended last, ensuring they are never buried in the middle of a long prompt.
- `output_path_to_rel` is applied to file paths displayed in usage lists to keep displayed paths in project-relative form regardless of storage format.

**Constraints:** `section` must contain `"id"`, `"title"`, and `"prompt"` keys. `file_deps` must contain at least a `"file"` key; missing `"callee_usages"` / `"caller_usages"` keys are treated as empty lists via `.get()`.

---

### `_build_summary_prompt`

**Signature:** `(file_path: str, section_contents: list[dict], summary_prompt: str, summary_max_chars: int) -> str`

Assembles the LLM prompt used to generate a condensed summary of an already-generated design document. Includes all section headings and their generated content, followed by the summary instruction and character limit. Returns the assembled string.

**Constraints:** Each element of `section_contents` must contain `"title"` and `"content"` keys.

---

### `_build_callee_context_summary`

**Signature:** `(file_deps: dict, doc_map: dict[str, dict], compact: bool = False) -> str`

Extracts summary text from previously generated design documents for each of the target file's direct dependencies (callee files) and returns them concatenated as a bullet list.

**Design decisions:**
- `compact=True` truncates each summary to 100 characters with a trailing ellipsis, providing a fallback for prompts that would otherwise exceed the context window.
- Dependency files are deduplicated via a set before iteration; the sorted order ensures deterministic output.
- `output_path_to_rel` converts stored output-format paths to project-relative paths before looking up in `doc_map` and before displaying.

**Edge cases:** Returns an empty string when no callee has an entry in `doc_map` or when all summaries are absent.

---

### `_build_implementation_context`

**Signature:** `(file_rel: str, file_output_dir: str) -> str`

Returns the full source code of the `.cpp`/`.c`/`.cc`/`.cxx` implementation file that corresponds to a given C/C++ header file. Returns an empty string for non-header files or when no implementation file is found.

**Design decisions:**
- Searches the *sibling* directories of `file_output_dir` (i.e., `os.path.dirname(file_output_dir)`) using the naming convention `{stem}_{impl_ext}/{stem}.{impl_ext}`, mirroring the output directory structure produced by `resolve_file_output_dir`.
- Extension membership is checked against `_HEADER_EXTENSIONS` before any filesystem access, so non-header files incur no I/O.
- Extensions in `_IMPL_EXTENSIONS` are tried in order; the first match wins.

---

### `_generate_section_with_fallback`

**Signature:** `(section: dict, source_code: str, file_deps: dict, callee_context_summary: str, callee_context_compact: str, file_path: str, llm_client: LLMClient, implementation_context: str = "") -> str | None` *(async)*

Attempts to generate text for one template section, retrying with progressively reduced context whenever a `ContextWindowExceededError` is raised. Returns the generated text string on the first successful attempt, or `None` if all three attempts fail.

**Design decisions:**
- Three attempts are tried in order: full callee summary → compact callee summary → no callee context. This degrades context quality gracefully rather than failing immediately.
- Any non-exception `None` return from `llm_client.generate` is also treated as a failure and the result is returned as-is (not retried), since `None` from the client indicates an API-level failure rather than a context-window issue.

---

### `_generate_file_doc`

**Signature:** `(file_rel: str, file_output_dir: str, doc_map: dict[str, dict], template: dict, llm_client: LLMClient) -> dict | None` *(async)*

Orchestrates full design document generation for a single file: reads the source copy and `file_dependencies.json`, builds callee context, generates each template section via `_generate_section_with_fallback`, then generates a summary. Returns a dict `{file, sections, summary}` or `None` if no section was generated.

**Design decisions:**
- Returns `None` rather than raising when required files are missing or all sections fail, so batch processing can continue for other files.
- `implementation_context` is computed once and forwarded to all section generation calls.

**Constraints:** `file_output_dir` must contain a file whose name matches `os.path.basename(file_rel)` and a `file_dependencies.json`. `template` must contain a `"sections"` list where each element has `"id"`, `"title"`, and `"prompt"`, and a `"summary_prompt"` string.

---

### `_generate_summary`

**Signature:** `(file_path: str, section_list: list[dict], template: dict, llm_client: LLMClient) -> str | None` *(async)*

Generates a summary of the entire design document from its already-generated sections. Returns the summary text, or `None` on any exception (logged as a warning). Does not retry on context window errors.

---

### `_find_source_file`

**Signature:** `(output_dir: str, file_rel: str) -> str | None`

Resolves the absolute path of the source file copy within an output directory by joining `output_dir` with the basename of `file_rel`. Returns the path if the file exists, otherwise `None`.

---

### `_save_doc`

**Signature:** `(doc: dict, output_dir: str) -> None`

Persists a design document in two formats: Markdown (`doc.md`) and JSON (`doc.json`). Markdown is written first so that the JSON file always has a newer or equal modification time, which is the criterion used by `_sync_md_to_json` to detect user edits.

**Design decisions:**
- Write order (MD before JSON) is intentional: it ensures the JSON mtime ≥ MD mtime immediately after generation, so `_sync_md_to_json`'s timestamp guard correctly identifies only *subsequent* manual edits.
- The summary, if present, is appended as a final `## Summary` section in the Markdown output.

---

### `_parse_md_sections`

**Signature:** `(md_text: str, section_titles: list[str]) -> dict[str, str]`

Splits a Markdown document into named sections by matching `## {title}` lines against a provided list of known titles. Returns a dict mapping each found title to its stripped content.

**Design decisions:**
- Uses a compiled regex anchored to line boundaries (`re.MULTILINE`) that matches *only* the known titles, so `##` headings inside LLM-generated content (which are not in the known-titles list) are treated as plain content rather than section delimiters.
- Titles are regex-escaped before joining into the alternation pattern to handle titles containing special characters.

**Edge cases:** Returns an empty dict if no known-title headings are found. Sections not present in the text are omitted from the result rather than receiving empty-string values.

---

### `_sync_md_to_json`

**Signature:** `(output_dir: str) -> None`

Detects manual edits made to `doc.md` after the last JSON write and propagates those changes back into `doc.json`, then re-saves both files to align their content and timestamps.

**Design decisions:**
- The modification-time guard (`mtime(md) > mtime(json)`) is the sole signal for detecting user edits; no content hashing is performed.
- Sections are updated conservatively: a section's content in JSON is replaced only if both that section's title *and* the immediately following section's title (or `"Summary"` for the last section) are present in the parsed MD. This prevents incorrect boundary detection when the user deletes a section heading from the MD.
- Calls `_save_doc` at the end to reset both files' content and timestamps to a consistent state, preventing repeated re-sync on subsequent runs.

**Constraints:** Silently returns without action if either file is absent, if the MD is not newer than the JSON, or if JSON cannot be parsed.

---

### `generate_all_docs`

**Signature:** `(base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int = MAX_WORKERS, changed_files: set[str] | None = None) -> None` *(async)*

Top-level entry point that generates design documents for all project files in topological order, with per-level parallel execution and incremental regeneration support.

**Design decisions:**
- Files within each level are batched into groups of `max_workers` and processed with `asyncio.gather`, bounding concurrency without requiring a thread pool.
- `changed_files=None` triggers full regeneration; a non-None set enables incremental mode where a file is skipped unless it or any of its callees has changed or was regenerated in the current run. The `regenerated_files` set propagates regeneration transitively up the dependency graph across levels.
- `_sync_md_to_json` is called for reused files before loading their JSON, ensuring that any manual Markdown edits are incorporated into `doc_map` before dependent files use the summary as context.
- Exceptions from individual `process_one` tasks are caught via `return_exceptions=True` in `asyncio.gather` and logged without aborting the entire run.

**Constraints:** `base_output_dir` must contain per-file subdirectories produced by the earlier pipeline stages. `project_dep_list` elements must each have a `"file"` key and an optional `"callees"` list.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **`codetwine/llm/client.py` (`LLMClient`)**: Used to send assembled prompts to the LLM and receive generated text for each design document section and summary. All LLM calls in this file are delegated to `LLMClient.generate()`.

- **`codetwine/llm/__init__.py` (`ContextWindowExceededError`)**: Used to catch context-window overflow errors thrown during LLM generation, enabling the progressive fallback mechanism that retries with progressively smaller context (full callee summaries → compact summaries → no callee context).

- **`codetwine/utils/file_utils.py` (`output_path_to_rel`, `resolve_file_output_dir`)**: `output_path_to_rel` is used to convert output-format paths back to project-relative paths when displaying dependency paths in prompts and when looking up entries in `doc_map`. `resolve_file_output_dir` is used to determine the output directory path for each file being processed.

- **`codetwine/config/settings.py` (`MAX_WORKERS`, `DOC_TEMPLATE_PATH`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`)**: Supplies runtime configuration values — the level of parallelism within each dependency level, the path to the document template JSON, the target output language for LLM-generated text, and the character limit for generated summaries.

### Dependents (what uses this file)

- **`codetwine/pipeline.py` (`generate_all_docs`)**: The top-level pipeline module calls `generate_all_docs` as the final stage of the document generation pipeline, passing the base output directory, the full project dependency list, the shared `LLMClient` instance, the worker count, and the set of changed files. This file is purely a consumer of the pipeline's outputs and has no knowledge of this file beyond the single public function it invokes.

### Direction of Dependency

All dependencies are strictly unidirectional. This file depends on `llm/client.py`, `llm/__init__.py`, `utils/file_utils.py`, and `config/settings.py`, none of which reference `doc_creator.py` in return. Likewise, `pipeline.py` depends on this file unidirectionally — `doc_creator.py` has no reference back to `pipeline.py`.

## Data Flow

## Data Flow

### High-Level Flow

```
project_dep_list (list[dict])
        │
        ▼
_topological_sort_by_level()
        │
        ▼
level_list: list[list[str]]   ← files grouped by dependency depth
        │
        ▼  (per level, batched by max_workers)
process_one(file_rel)
    ├── resolve_file_output_dir()  → output_dir (str)
    ├── _find_source_file()        → source_code (str)
    ├── file_dependencies.json     → file_deps (dict)
    ├── _build_callee_context_*()  → callee_context strings
    ├── _build_implementation_context() → implementation_context (str)
    └── _generate_file_doc()
            │
            ├── per section: _generate_section_with_fallback()
            │       └── _build_section_prompt() → prompt (str)
            │               └── LLMClient.generate() → section content (str)
            │
            └── _generate_summary()
                    └── _build_summary_prompt() → prompt (str)
                            └── LLMClient.generate() → summary (str)
        │
        ▼
    doc (dict)  ──── _save_doc() ──► doc.md + doc.json (files)
        │
        ▼
    doc_map[file_rel] = doc   ← feeds callee context for subsequent levels
```

---

### Input Data

| Source | Format | Description |
|---|---|---|
| `project_dep_list` | `list[dict]` | Each entry: `{file: str, callers: list, callees: list}` |
| `file_dependencies.json` | JSON file | Per-file dependency detail; loaded into `file_deps` dict |
| Source file copy | Plain text file | Read from `output_dir/{filename}` |
| `doc_template.json` | JSON file | Template with `sections[]` and `summary_prompt` |
| `doc.md` (optional) | Markdown file | User-edited; synced back to JSON if newer than `doc.json` |

---

### Key Data Structures

**`file_deps` (dict)** — loaded from `file_dependencies.json`:

| Field | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the target file |
| `callee_usages` | `list[dict]` | Each: `{name, from, target_context?}` — symbols this file depends on |
| `caller_usages` | `list[dict]` | Each: `{name, file, usage_context?}` — symbols other files use from this file |

**`doc` (dict)** — the generated design document:

| Field | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path |
| `sections` | `list[dict]` | Each: `{id, title, content}` — one LLM-generated section |
| `summary` | `str` | Short summary of the whole document |

**`doc_map` (dict[str, dict])** — accumulates completed docs across levels:
- Key: file relative path (`str`)
- Value: `doc` dict above
- Consumed by `_build_callee_context_summary()` to inject dependency summaries into prompts for later levels

**`level_list` (list[list[str]])** — topological sort output:
- Outer index = dependency depth (level 0 = files with no dependencies)
- Inner list = file relative paths at that level, processed in parallel batches

**`file_callees` (dict[str, set[str]])** — built from `project_dep_list`:
- Key: file relative path
- Value: set of callee file paths
- Used by `_needs_regeneration()` to determine whether a file must be re-processed

---

### Prompt Assembly Flow

```
source_code
file_deps  (callee_usages, caller_usages)        ─┐
callee_context_summary / _compact / ""            ├─► _build_section_prompt() → prompt str
implementation_context (header files only)        ─┘
        │
        └──► LLMClient.generate() → section["content"] (str)

section_list (all generated sections)
summary_prompt (from template)                   ─┬─► _build_summary_prompt() → prompt str
summary_max_chars (from settings)                ─┘
        │
        └──► LLMClient.generate() → doc["summary"] (str)
```

The callee context used in section prompts is selected by progressive fallback:
`callee_context_summary` → `callee_context_compact` (first 100 chars each) → `""` (empty), triggered on `ContextWindowExceededError`.

---

### Output

| Destination | Format | Contents |
|---|---|---|
| `{output_dir}/doc.json` | JSON | Full `doc` dict (`file`, `sections[]`, `summary`) |
| `{output_dir}/doc.md` | Markdown | Human-readable rendering of sections and summary |
| `doc_map` (in-memory) | `dict[str, dict]` | Accumulated docs; used as callee context for higher dependency levels |

## Error Handling

# Error Handling

## Overall Strategy: Graceful Degradation

`doc_creator.py` adopts a **graceful degradation** strategy throughout. No single file failure is allowed to abort the broader document generation run. Instead, failures are absorbed at the granularity of the section, the file, and the batch, with warnings or errors logged and processing continuing for all remaining items.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `ContextWindowExceededError` during section generation | Retried up to three times with progressively reduced callee context (full summary → compact summary → no callee context) | Section may be generated with less context; if all three attempts fail, section is skipped and a warning is logged |
| Section generation returns `None` (all fallback attempts exhausted) | Section is omitted from the document; a warning is logged | Resulting document is incomplete but still saved if at least one section succeeded |
| All sections fail for a file | File document generation is aborted; an error is logged; `None` is returned to the caller | File is skipped entirely (`SKIP` printed); no `doc.json` or `doc.md` is written |
| Summary generation failure (any exception) | Exception is caught, a warning is logged, and `None` is returned | Document is saved without a summary field (empty string substituted) |
| Missing source file or `file_dependencies.json` | Warning is logged, `None` returned immediately | File is treated as if generation failed; no document produced |
| `json.JSONDecodeError` / `OSError` on reading existing `doc.json` | Exception is silently swallowed; processing falls through to regeneration | File is regenerated rather than reused |
| `json.JSONDecodeError` / `OSError` on reading `doc.json` during MD→JSON sync | Exception is silently swallowed; sync is aborted for that file | Manual MD edits are not reflected in JSON for that run; no crash |
| Exception raised by an `asyncio.Task` within a batch | Caught via `isinstance(result, Exception)` after `asyncio.gather`; error is logged | Only the affected file in the batch is lost; remaining batch results are processed normally |
| Output directory for a file does not exist | Warning is logged, `None` returned immediately | File is skipped |
| Circular dependency in topological sort | Remaining files are appended to the final level; a warning is logged | Circular-dependency files are still processed, but ordering guarantees are lost for that group |

---

## Design Considerations

- **Progressive fallback for context window errors** is the most nuanced error-handling concern. Because LLM context limits are encountered at runtime based on prompt size, the three-attempt ladder (full context → compact context → no context) avoids discarding an entire section purely due to size, while still preferring the richest available context.
- **`asyncio.gather` with `return_exceptions=True`** is used for batch processing, ensuring that an unhandled exception in one coroutine does not cancel sibling tasks. Exception results are inspected and logged individually after the gather completes.
- **No exceptions propagate to the public API** (`generate_all_docs`). All failure modes are converted to `None` return values or logged warnings before reaching the caller in `pipeline.py`, preserving the pipeline's ability to continue regardless of documentation failures.
- **MD→JSON sync errors are silently suppressed** to ensure that a corrupted or unreadable file never blocks a pipeline run. The cost is that a manual edit to `doc.md` may silently fail to sync in that scenario.

## Summary

`doc_creator.py` generates structured, multi-section design documents for every project file by driving an LLM in topological dependency order. Its sole public function, `generate_all_docs`, sorts files by dependency depth, processes each level in parallel batches, and supports incremental regeneration via a `changed_files` set. Key data structures include `doc_map` (accumulated docs keyed by file path), `file_deps` (per-file dependency detail), and `doc` (generated document with sections and summary). Outputs are saved as both `doc.json` and `doc.md`, with bidirectional sync to preserve manual edits. Context-window errors trigger progressive fallback reducing callee context across up to three retries.
