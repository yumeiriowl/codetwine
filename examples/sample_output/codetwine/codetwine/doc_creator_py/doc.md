# Design Document: codetwine/doc_creator.py

# Overview & Purpose

## Role in the Project

`codetwine/doc_creator.py` is the core design-document generation engine of Codetwine. It orchestrates the process of turning per-file dependency metadata (`file_dependencies.json`) and source code copies produced elsewhere in the pipeline into structured, LLM-generated design documents (`doc.json` / `doc.md`) for every file in a project.

It exists as a separate module because it encapsulates several distinct but tightly related concerns that would otherwise clutter the pipeline layer:

- **Dependency ordering**: topologically sorting files by dependency depth so that documentation for a dependency is generated (and its summary available) before the files that depend on it are processed.
- **Prompt assembly**: building structured LLM prompts from source code, dependency usage information (`callee_usages`/`caller_usages`), prior design-document summaries, and (for header files) the corresponding implementation file.
- **Context-window overflow handling**: a staged fallback strategy that progressively shrinks prompts (dropping caller bodies, dropping dependency summaries, then LLM-summarizing large callee symbols and finally large in-file definitions) when the LLM raises `ContextWindowExceededError`.
- **Incremental regeneration**: reusing existing documents for unchanged files (and their unchanged dependencies) to avoid redundant LLM calls, including syncing manual edits made directly to `doc.md` back into `doc.json`.
- **Parallel, level-by-level orchestration**: driving the whole process asynchronously with bounded concurrency (`max_workers`) across dependency levels.

This module is invoked by `codetwine/pipeline.py` via its single public entry point, `generate_all_docs`, and depends on `LLMClient` for LLM calls, `ContextWindowExceededError` for overflow detection, path utilities from `file_utils.py`, and various configuration constants from `settings.py`.

## Main Public Interface

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `generate_all_docs` | `base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int = MAX_WORKERS, changed_files: set[str] \| None = None` | `None` | Top-level entry point: loads the doc template, topologically sorts files into dependency levels, generates (or reuses) design documents level-by-level in parallel, and maintains a running map of file summaries for use as context in later levels. |

All other functions and module-level constants (e.g. `_build_section_prompt`, `_generate_file_doc`, `_summarize_code`, `_save_doc`, `_topological_sort_by_level`, `HEADER_*`/`*_TEMPLATE`/`*_NOTE` prompt-text constants) are prefixed with an underscore or are internal prompt-building strings, indicating they are private implementation details not intended for use outside this module.

## Design Patterns & Key Design Decisions

- **Level-based topological processing (Kahn's algorithm variant)**: `_topological_sort_by_level` groups files into dependency levels via BFS over a reverse dependency graph, enabling files within the same level to be documented concurrently while guaranteeing dependencies are documented first. Files involved in circular dependencies are placed in a final level with a logged warning rather than blocking generation.
- **Progressive degradation / staged fallback pattern**: `_generate_section_with_fallback` implements an explicit 5-stage (0–4) cumulative prompt-reduction strategy in response to `ContextWindowExceededError`, escalating from cheap, non-LLM reductions (dropping caller usage bodies, dropping dependency doc summaries) to LLM-assisted summarization (of large callee symbols, then of large in-file definitions), gated by the `ENABLE_CODE_SUMMARY` flag.
- **Shared caching**: `summary_cache`, keyed by SHA256 hash of code text (`_summarize_code`), ensures a given code symbol is summarized by the LLM only once per run, even if referenced by multiple files/sections.
- **Incremental/idempotent regeneration**: `_needs_regeneration` and `_is_doc_complete` implement a change-detection strategy so that only files that changed, or whose dependencies changed/were regenerated, are re-processed; complete existing documents are reused otherwise.
- **Bidirectional MD/JSON sync**: `_sync_md_to_json`/`_parse_md_sections` allow manual edits to the human-readable `doc.md` to be reflected back into the canonical `doc.json` (based on file modification time comparison), treating Markdown as an editable view of the same underlying document data.
- **Bounded, batched concurrency**: within each dependency level, files are processed in batches of `max_workers` via `asyncio.gather`, balancing throughput against LLM rate/resource limits.

# Definition Design Specifications

## `_topological_sort_by_level`

Takes `project_dep_list` (a list of dicts, each with `"file"`, `"callers"`, `"callees"` keys as produced by `save_project_dependencies`) and returns a `list[list[str]]` grouping file paths into dependency-depth levels, where level 0 contains files with no callees.

Exists to establish a safe processing order for document generation: dependencies must be documented (and summarized) before the files that depend on them, so their summaries can be used as context.

Internally builds a forward adjacency map (file → callees) and a reverse adjacency map (file → callers), then performs a Kahn's-algorithm-style BFS on the reverse graph, peeling off files whose reverse in-degree reaches zero one level at a time. This guarantees that no file appears before any of its callees.

Edge case: if circular dependencies prevent some files from reaching in-degree zero, those remaining files are logged as a warning and appended together as the final level, so the function always terminates and returns every file exactly once.

## `_build_section_prompt`

Assembles the full LLM prompt string for generating a single design-document section, given the `section` template entry (`id`/`title`/`prompt`), the target file's `source_code`, its `file_deps` (parsed `file_dependencies.json`), an optional `callee_context` (dependency doc summaries), and an optional `implementation_context` (for header files).

Its responsibility is to deterministically compose all contextual information the model needs — target file identity, source, implementation counterpart, callee/caller usage info with source snippets, dependency summaries, and the section-specific instructions — into one prompt string, always ending with the output-language directive and a factual-accuracy warning.

Design intent: each context block (implementation file, callee usages, caller usages, callee summaries) is included conditionally only when present, keeping prompts as small as possible while covering all available information. Field access uses `output_path_to_rel` to normalize dependency paths back to source-relative form for display.

Constraint: `file_deps` and `section` are expected to already contain the keys used (`title`, `prompt`, `callee_usages`, `caller_usages`, `file`); missing optional sub-keys (`target_context`, `usage_context`) are tolerated via `.get()`.

## `_build_summary_prompt`

Builds the prompt string used to request a whole-document summary, from `file_path`, the list of already-generated `section_contents` (each `{id, title, content}`), the template's `summary_prompt` instruction, and `summary_max_chars`.

Exists to let the LLM produce a single condensed synopsis of a file's design document after all sections have been generated, for reuse as dependency context by other files.

Concatenates every section's title and content verbatim, then appends the summary instruction, a character-limit note, and the output-language instruction. No content filtering or truncation is performed here — all sections are always included in full.

## `_build_callee_context_summary`

Given a file's `file_deps` and `doc_summary_map` (relative path → previously generated summary text), returns a single formatted string listing the design-document summaries of that file's dependency files (as derived from unique `from` entries in `callee_usages`).

Exists to provide condensed prior-knowledge about dependencies (as opposed to their full source in callee_usages) so the prompt can reference "what a dependency file is responsible for" without needing its full document.

Design decision: paths are deduplicated and sorted for determinism, and `from` values (which are in output-directory format) are converted with `output_path_to_rel` before being looked up in `doc_summary_map`, since that map is keyed by source-relative paths. Dependencies with no summary yet (not yet processed, per topological order) are silently skipped.

## `_line_count`

Returns the number of lines in a text block as `text.count("\n") + 1`.

A small utility used purely to decide, elsewhere, whether a code block exceeds the configured trigger-line threshold for summarization. No special edge-case handling beyond the newline-count formula; an empty string counts as 1 line.

## `_summarize_code`

Async function that asks the LLM to produce a concise behavior summary of a `code` block associated with symbol `name`, using a shared `summary_cache` (keyed by SHA256 hash of the code text) so identical symbols are only summarized once per run.

Exists to shrink large dependency/definition code blocks (used elsewhere as context-overflow fallback stages) while retaining enough information — signature plus behavior description — for downstream sections to still understand what the code does.

Design decision: caching is content-hash based rather than name based, so identical code appearing under different usages is only sent to the LLM once, reducing cost. If the LLM call raises `ContextWindowExceededError` (i.e., even the individual symbol is too large to summarize) or returns nothing, a deterministic fallback is used instead: the code's first line (its signature) followed by `CODE_SUMMARY_FAILED_NOTE`, ensuring the function never fails outright and callers always receive usable text.

## `_reduce_caller_usages`

Returns a shallow copy of `file_deps` where each entry in `caller_usages` has its `usage_context` key removed, while keeping other keys (e.g. name, file) intact.

Exists as the first, cost-free (no LLM call) fallback stage for shrinking an oversized prompt: dropping the verbatim source snippets of caller usage locations, since dependents' full usage code is less essential to a file's own documentation than its own source and its callees.

If `file_deps` has no `caller_usages`, the original object is returned unchanged (no copy made), avoiding unnecessary allocation.

## `_summarize_callee_usages`

Async function returning a shallow copy of `file_deps` where each `callee_usages` entry's `target_context` is replaced with an LLM-generated behavior summary (via `_summarize_code`) only if that context's line count exceeds `CODE_SUMMARY_TRIGGER_LINES`; smaller contexts are left verbatim.

Exists as fallback stage 3: reduces prompt size by summarizing only the large dependency code blocks, since small ones are cheap to keep as-is and preserving them intact avoids unnecessary information loss.

If there are no `callee_usages`, returns `file_deps` unchanged.

## `_select_outermost_large_definitions`

Given a `definitions` list (each with `start_line`/`end_line`) and a `trigger_lines` threshold, returns the subset of definitions whose line span exceeds the threshold and which are not nested inside another already-selected large definition, sorted by `start_line`.

Exists to support splicing large code blocks out of a source file (fallback stage 4) without double-summarizing: if a class and its methods are both "large," only the outer class should be replaced, since summarizing an already-summarized region would corrupt line-based splicing.

Design decision: candidates are sorted "outer-first" — by ascending `start_line`, and on ties by descending `end_line` (widest range first) — then a single pass keeps a definition only if its `start_line` falls after the current `covered_end`, guaranteeing no overlapping selections and giving priority to the outermost span.

Constraint: only definitions with both `start_line` and `end_line` present (truthy) are considered; others are excluded from candidacy entirely.

## `_splice_large_definitions`

Async function that replaces large definitions within `source_code` (per `_select_outermost_large_definitions`) with LLM-generated behavior summaries (via `_summarize_code`), returning the modified full source text; used as the final, last-resort fallback stage (stage 4) when even the target file's own source (combined with reduced dependency context) is too large for the LLM.

Design intent: operates on a line-by-line reconstruction using 1-based line numbers matching tree-sitter's row convention (`source_code.split("\n")`), so each selected definition's `[start_line, end_line]` range is skipped over and replaced by a `CODE_SUMMARY_MARKER` header line followed by the summary text, while all other lines (small definitions and non-definition code) are preserved verbatim.

Design decision: uses each definition's stored `context` field if present, otherwise reconstructs the code slice directly from `lines[start_line-1:end_line]`, providing a fallback in case `context` was not captured during dependency analysis.

Edge case: if no definitions exceed the trigger threshold, the original `source_code` is returned completely unchanged.

## `_build_implementation_context`

Given a header file's relative path `file_rel` and its `file_output_dir`, searches sibling output directories in the same parent directory for a correspondingly-named implementation file (`.cpp`, `.c`, `.cc`, or `.cxx`, per `_IMPL_EXTENSIONS`) and returns its full source text if found.

Exists so header-file documentation can reference how declared symbols are actually implemented, since headers alone often lack behavioral detail.

Design decision: only files whose extension is in `_HEADER_EXTENSIONS` (`.h`, `.hpp`, `.hh`, `.hxx`) trigger a search; for other extensions the function immediately returns an empty string. The search follows the project's fixed output-directory naming convention (`{stem}_{ext}/{stem}.{ext}`) rather than scanning the filesystem generically, and returns the first matching implementation extension found (in `_IMPL_EXTENSIONS` order), reading only its first match.

## `_generate_section_with_fallback`

Async function that generates a single template `section`'s content for a file via `llm_client`, progressively reducing the prompt through defined stages when `ContextWindowExceededError` occurs, and returns the generated text or `None` if every stage fails.

Exists to keep documentation generation resilient to model context-window limits without giving up on a section outright, by cheaply reducing prompt size first and only invoking additional LLM calls (for summarization) as a last resort.

Design decision: fallback stages are strictly cumulative and ordered by cost — (0) full prompt, (1) drop caller usage source bodies (no extra LLM cost), (2) additionally drop dependency doc summaries (no extra LLM cost), (3) summarize oversized callee dependency code (LLM calls, cached), (4) summarize oversized definitions within the target file's own source (LLM calls, cached) — stopping at the first stage that succeeds. Stages 3 and 4 are skipped entirely if `ENABLE_CODE_SUMMARY` is `False`, since they incur additional LLM cost. Each failed attempt is logged with the file/section id and the label of the stage.

## `_generate_file_doc`

Async function producing a full design-document dict (`{file, sections, summary}`) for one file: locates its copied source file and `file_dependencies.json` in `file_output_dir`, builds callee-context summaries and (for headers) implementation context, generates every section defined in `template["sections"]` via `_generate_section_with_fallback`, and finally requests an overall summary via `_generate_summary`.

Its responsibility is to orchestrate the end-to-end single-file document generation pipeline, isolating individual-file failures from the caller.

Design decision: failure of an individual section only logs a warning and skips that section (partial documents are acceptable); however if *no* section succeeds, generation is considered a complete failure and the function returns `None`, since an empty document provides no value. Missing source file or missing `file_dependencies.json` also causes immediate failure (`None`) with a warning logged, since neither of the pipeline's two primary inputs can be substituted.

## `_generate_summary`

Async function that builds a summary prompt (via `_build_summary_prompt`) from the file's already-generated `section_list` and template's `summary_prompt`/`SUMMARY_MAX_CHARS`, sends it to `llm_client`, and returns the resulting text or `None` on any exception.

Exists as the final step of per-file document generation, producing the condensed text later reused as dependency context (`doc_summary_map`) for files that depend on this one.

Design decision: unlike section generation, this function does not implement context-window fallback reduction — any exception (including `ContextWindowExceededError`) is caught broadly and logged as a warning, resulting in an empty/`None` summary rather than blocking the whole document, since a missing summary is treated as non-fatal to overall generation.

## `_find_source_file`

Given `output_dir` and a file's relative path `file_rel`, returns the absolute path of the copied source file (matched by basename) inside `output_dir` if it exists, otherwise `None`.

Exists as a small lookup helper isolating the assumption that a file's original source is copied flat into its output directory under its base filename, so callers do not need to know this storage convention directly.

## `_save_doc`

Persists a generated `doc` dict (`{file, sections, summary}`) to `output_dir` in two formats: `doc.md` (human-readable Markdown) and `doc.json` (machine-readable), with Markdown written first so its mtime is not older than the JSON's (a timestamp invariant relied upon by `_sync_md_to_json`).

Exists as the single point where a generated/loaded document is written back to disk, keeping the two representations (edit-friendly Markdown and structured JSON) synchronized.

Design decision: before writing, each section's `content` has any leading duplicate heading matching its own `title` stripped via regex, since LLM output sometimes redundantly repeats the section title as a markdown heading, which would otherwise create duplicate headings in the assembled Markdown. Section and summary content are written under level-1 (`#`) Markdown headings; the summary section is only appended if `doc["summary"]` is non-empty.

## `_parse_md_sections`

Given the full Markdown text of a `doc.md` and a list of `section_titles` (including `"Summary"`), splits the text on lines exactly matching `# {title}` for any known title, returning a dict mapping each found title to its trailing content up to the next matching heading (or end of text).

Exists to support round-tripping manual edits made directly to `doc.md` back into the structured JSON representation, since the Markdown format uses these headings as the only section delimiters.

Design decision: matching uses `re.escape` on titles and requires the heading to be on its own line (`^# (...)$`, `re.MULTILINE`), so headings must exactly match one of the known section titles verbatim; any titles not found in the text are simply omitted from the returned dict (not treated as an error), since a user may have deleted a section entirely.

## `_sync_md_to_json`

Reconciles manual edits a user made in `doc.md` back into `doc.json`: only runs if both files exist and `doc.md`'s modification time is strictly newer than `doc.json`'s, then parses the Markdown via `_parse_md_sections` and overwrites matching section/summary content in the loaded JSON, re-saving both files (via `_save_doc`) if any content actually changed.

Exists to let users hand-edit generated documentation in Markdown (the more readable format) without losing those edits when the pipeline is re-run, while the JSON remains the authoritative structured store used for reuse/caching logic.

Design decision: for each JSON section, the sync is skipped (retaining the original content) unless *both* that section's title and the *next* section's title (in JSON order, with `"Summary"` implicitly following the last section) are present in the parsed Markdown; this guards against applying a section boundary that may be inaccurate because a neighboring heading was removed or renamed by the user. JSON parse errors on the existing file cause silent early return (no exception is raised to the caller), since a corrupted or missing JSON simply means there's nothing to sync into.

## `generate_all_docs`

Top-level async entry point that generates design documents for every file described by `project_dep_list`, writing results under `base_output_dir` using `llm_client`, processing files level-by-level (via `_topological_sort_by_level`) in batches of at most `max_workers` concurrent tasks per level, and optionally skipping regeneration for files unaffected by `changed_files`.

Its responsibility is to drive the whole documentation pipeline: loading the section template from `DOC_TEMPLATE_PATH`, maintaining `doc_summary_map` (file → summary, carried across levels as callee context) and a shared `summary_cache` (code-hash → summary, reused across the entire run for the context-overflow fallback), and reporting progress via `print`/logging at each level.

Design decision: processing strictly follows dependency levels so that a file's callees are always documented (and their summaries available) before the file itself is processed; within a level, files are batched to bound concurrency to `max_workers` regardless of level size.

Nested helper `_needs_regeneration(file_rel)`: returns whether a file's document must be regenerated — always true if `changed_files` is `None` (full-regeneration mode), or if the file itself is listed in `changed_files`, or if any of its callees is in `changed_files` or in the running `regenerated_files` set (propagating regeneration transitively to dependents of changed files).

Nested helper `_is_doc_complete(doc)`: validates a loaded existing `doc.json` has exactly the section ids defined by the current template and a non-empty `summary` (when the template defines a `summary_prompt`), used to detect and discard stale/partial documents (e.g., from a template change) rather than reusing them blindly.

Nested helper `process_one(file_rel)`: per-file worker that resolves the file's output directory (skipping with a warning if it doesn't exist), reuses an existing complete `doc.json` (after syncing any manual Markdown edits via `_sync_md_to_json`) when `_needs_regeneration` is false, and otherwise calls `_generate_file_doc`, saves the result via `_save_doc`, and records the file in `regenerated_files`; always returns `(file_rel, doc_or_None)` so failures during `asyncio.gather` (caught as exceptions) can be logged without aborting the whole run.

Edge case/constraint: `doc_summary_map` is only updated for files that actually returned a successful `doc`, so a failed or skipped file contributes no summary context to files depending on it in later levels.

# Dependency Description

## Dependencies (what this file uses)

`doc_creator.py` relies on several project-internal modules to perform its core responsibility of generating design documents for source files:

- **`codetwine/utils/file_utils.py`** (`output_path_to_rel`, `resolve_file_output_dir`): `output_path_to_rel` is used throughout prompt construction to convert internal output-directory paths back into human-readable, source-relative paths when displaying the target file, its dependencies, and its dependents. `resolve_file_output_dir` is used to locate the correct output directory for a given file so that its source copy and `file_dependencies.json` can be read and its generated docs saved.

- **`codetwine/llm/client.py`** (`LLMClient`): Used as the async interface for sending prompts to the LLM and receiving generated text. It underlies section generation, summary generation, and code summarization used in context-overflow fallback handling.

- **`codetwine/llm/__init__.py`** (`ContextWindowExceededError`): Caught throughout the fallback logic (`_generate_section_with_fallback`, `_summarize_code`) to detect when a prompt is too large for the model and trigger progressive prompt-reduction strategies.

- **`codetwine/config/settings.py`** (`MAX_WORKERS`, `DOC_TEMPLATE_PATH`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS`): Supplies configuration values that control document generation behavior — the parallelism level for processing files, the path to the document template, the language used for LLM output, the summary length limit, whether large-code summarization fallback is enabled, the line-count threshold that triggers such summarization, and the character limit for individual code summaries.

## Dependents (what uses this file)

- **`codetwine/pipeline.py`** (`generate_all_docs`): The pipeline invokes `generate_all_docs` as the main entry point for the design-document generation stage, passing in the base output directory, project dependency list, LLM client, worker count, and changed-files set. This is how the overall pipeline triggers document generation for the whole project.

No other dependent information is available.

The dependency direction is unidirectional: `doc_creator.py` depends on the configuration, LLM client, and file utility modules, while `codetwine/pipeline.py` depends on `doc_creator.py`.

# Data Flow

## Input Data

| Source | Format | Description |
|---|---|---|
| `project_dep_list` (arg to `generate_all_docs`) | `list[dict]` | Each item: `{"file": str, "callers": list, "callees": list}` — project-wide dependency graph |
| `DOC_TEMPLATE_PATH` file | JSON | Template dict: `{"sections": [{id, title, prompt}, ...], "summary_prompt": str}` |
| `{output_dir}/<basename>` (per file) | raw source text | Copied source code of the target file |
| `{output_dir}/file_dependencies.json` | JSON | Per-file dependency info: `definitions`, `callee_usages` (`name`, `from`, `target_context`), `caller_usages` (`name`, `file`, `usage_context`) |
| existing `doc.json` / `doc.md` | JSON / Markdown | Prior generated docs, reused/synced when unchanged |
| `changed_files` (optional arg) | `set[str]` | Relative paths of files that changed since last run |

## Main Transformation Flow

```
project_dep_list
      │
      ▼
_topological_sort_by_level()  ──►  level_list: list[list[str]]  (files grouped by dependency depth)
      │
      ▼
for each level (sequential) → for each file (parallel, batched by max_workers)
      │
      ▼
_needs_regeneration()? ──No──► reuse existing doc.json (optionally synced from edited doc.md
      │                         via _sync_md_to_json)
      Yes
      ▼
_generate_file_doc()
   ├─ read source_code (from copied file)
   ├─ read file_deps (file_dependencies.json)
   ├─ _build_callee_context_summary()  → callee_context string (from doc_summary_map)
   ├─ _build_implementation_context()  → implementation_context (for header files)
   ├─ for each template section:
   │     _generate_section_with_fallback()
   │        ├─ _build_section_prompt() → prompt string
   │        ├─ llm_client.generate(prompt) → section text
   │        └─ on ContextWindowExceededError: progressively reduce prompt
   │             (drop caller bodies → drop callee_context → summarize callee
   │              usages via _summarize_code → summarize large source
   │              definitions via _splice_large_definitions)
   ├─ collect section_list: [{id, title, content}, ...]
   └─ _generate_summary() → summary string (via _build_summary_prompt + LLM)
      │
      ▼
doc = {"file": file_rel, "sections": [...], "summary": str}
      │
      ▼
_save_doc(doc, output_dir)  →  writes doc.md then doc.json
      │
      ▼
doc_summary_map[file_rel] = doc["summary"]  (carried forward to later levels)
```

## Output Data

| Destination | Format | Content |
|---|---|---|
| `{output_dir}/doc.md` | Markdown | `# Design Document: {file}`, one `# {title}` block per section, trailing `# Summary` block |
| `{output_dir}/doc.json` | JSON | `{"file": str, "sections": [{id, title, content}], "summary": str}` |
| `doc_summary_map` (in-memory) | `dict[str, str]` | file path → summary, used as context input for later levels |
| `summary_cache` (in-memory) | `dict[str, str]` | SHA256(code) → LLM behavior summary, reused across files/sections in one run |
| stdout / logger | text | Progress messages (`REUSE`, `OK`, `SKIP`, `INCOMPLETE`) |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `level_list` | `list[list[str]]` | Files grouped by dependency depth (index = level), for ordered/parallel processing |
| `file_deps` | `{definitions, callee_usages, caller_usages, file}` | Per-file dependency metadata read from JSON, feeds prompt building |
| `section` (template) | `{id, title, prompt}` | Defines what to ask the LLM to generate per section |
| `section_list` / `doc["sections"]` | `[{id, title, content}]` | Accumulated generated section outputs for a file |
| `doc_summary_map` | file path → summary text | Cross-level context propagation (callee doc summaries) |
| `summary_cache` | code hash → summary text | Dedupe/cache LLM summarization of large code blocks |
| `file_callees` | file path → `set[str]` | Used by `_needs_regeneration` to detect indirect change propagation |
| `regenerated_files` | `set[str]` | Tracks which files were regenerated this run, to cascade regeneration to callers |

# Error Handling

## Overall Strategy

This module follows a **graceful degradation** policy at multiple levels rather than a fail-fast approach: individual failures (LLM call errors, missing files, JSON parse errors) are logged and contained locally so that the overall document generation run continues for other files/sections. Only structural configuration errors (e.g., missing template file) are allowed to propagate and abort the run. Within a single file's document generation, `ContextWindowExceededError` is handled through a staged, cumulative prompt-reduction fallback (dropping caller usage bodies, dropping dependency doc summaries, then LLM-based summarization of callee symbols and finally of the target file's own large definitions), so that a section is only marked as failed after all reduction stages have been exhausted.

## Error Patterns and Handling

| Error Type | Handling | Impact |
|---|---|---|
| `ContextWindowExceededError` during section generation | Caught per stage in `_generate_section_with_fallback`; triggers progressive prompt-reduction fallback (stages 0–4); logged as a warning at each stage | Section generation may succeed at a later stage with reduced context; if all stages fail, the section is skipped (returns `None`) |
| `ContextWindowExceededError` during code summarization (`_summarize_code`) | Caught and replaced with a deterministic fallback summary (signature line + fixed note) | Summarization never raises; downstream prompt still gets usable, if less informative, text |
| Missing source file / `file_dependencies.json` for a target file | Checked explicitly; logged as a warning; function returns `None` | Document generation for that file is aborted; file is skipped (`SKIP` logged) in the overall run |
| All sections fail to generate for a file | Logged as an error; function returns `None` | File is skipped entirely; no partial document is saved |
| Failure to generate the summary (`_generate_summary`) | Any exception is caught, logged as a warning, `None` returned | Document is still saved with an empty summary string instead of failing the whole file |
| Malformed/corrupt `doc.json` when checking for reuse or MD/JSON sync | `json.JSONDecodeError` / `OSError` caught | For reuse checks, falls back to regeneration; for MD→JSON sync, the sync is silently skipped |
| Exceptions raised inside a per-file task during batched parallel processing | `asyncio.gather(..., return_exceptions=True)` collects exceptions instead of propagating; each is logged as an error | One file's failure does not stop processing of other files in the same or later batches/levels |
| Circular dependencies in topological sort | Detected via leftover unprocessed files after BFS; logged as a warning; remaining files placed in a final processing level | Generation still completes for all files, but circularly-dependent files may lack full dependency-doc context |

## Design Considerations

- Error containment is scoped per file and per section so that a single problematic file cannot block the rest of the batch/level or the overall pipeline.
- The prompt-reduction fallback chain is deterministic and stage-ordered, ensuring cheaper reductions (dropping context) are tried before more expensive ones (LLM-based summarization), and only when `ENABLE_CODE_SUMMARY` is enabled are the LLM-summarization stages attempted.
- Caching of code summaries (`summary_cache`, keyed by SHA256 of code text) avoids redundant LLM calls for the same symbol across files/sections, indirectly reducing the chance of repeated context-window failures during fallback.
- Logging is used consistently (via `print` and `logger`) to make skipped, incomplete, or reused documents visible during a run, supporting observability without interrupting execution.

# Summary

`doc_creator.py` is Codetwine's design-document generation engine, invoked by `pipeline.py` via its sole public entry point `generate_all_docs(base_output_dir, project_dep_list, llm_client, max_workers, changed_files)`. It topologically sorts files by dependency depth, then generates/reuses LLM-based docs (`doc.json`/`doc.md`) per file in parallel batches, using prompt assembly, context-overflow fallback strategies, code-summary caching, incremental regeneration, and Markdown/JSON sync. Depends on `LLMClient`, `ContextWindowExceededError`, `file_utils`, and `settings`. Key structures: `doc_summary_map`, `summary_cache`, `level_list`.
