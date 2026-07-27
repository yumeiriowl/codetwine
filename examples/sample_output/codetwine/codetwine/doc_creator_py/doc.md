# Design Document: codetwine/doc_creator.py

# Overview & Purpose

## Purpose and Responsibilities

`codetwine/doc_creator.py` is the core document-generation engine of the codetwine pipeline. Its responsibility is to take the project's dependency graph (produced elsewhere and passed in as `project_dep_list`) together with per-file source copies and `file_dependencies.json` metadata, and drive an LLM (`LLMClient`) to produce structured "design documents" (Markdown + JSON) for every source file in the project.

It exists as a separate file because it encapsulates a distinct, self-contained concern: **prompt construction, ordered/parallel generation, context-overflow fallback, incremental reuse, and persistence of design documents** — separate from dependency extraction (`dependency_graph.py`), path/file utilities (`file_utils.py`), and the LLM transport layer (`llm/client.py`). This keeps prompt-engineering and orchestration logic isolated from lower-level concerns, and allows `pipeline.py` to invoke the whole subsystem through a single entry point (`generate_all_docs`).

Key responsibilities handled within this module:
- **Topological ordering**: files are processed level-by-level so that a file's dependencies (callees) are documented before the file itself, allowing dependency summaries to be reused as context (`_topological_sort_by_level`).
- **Prompt assembly**: building structured prompts per section, combining source code, callee/caller usage information, dependency doc summaries, and (for headers) the paired implementation file (`_build_section_prompt`, `_build_summary_prompt`).
- **Context-overflow fallback**: a staged degradation strategy (drop caller bodies → drop callee doc summaries → summarize large callee symbols → summarize large definitions in the source itself) when the LLM reports `ContextWindowExceededError` (`_generate_section_with_fallback` and its helpers).
- **Incremental regeneration**: skipping unchanged files (and their unaffected dependents) by reusing existing `doc.json` when `changed_files` is supplied, while still allowing manual Markdown edits to sync back into JSON (`_sync_md_to_json`).
- **Persistence**: writing each design document as both `doc.md` and `doc.json` (`_save_doc`).
- **Parallel execution**: processing each dependency level in batches of `max_workers` concurrent tasks via `asyncio`.

## Main Public Interface

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `generate_all_docs` | `base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int = MAX_WORKERS, changed_files: set[str] \| None = None` | `None` | Main entry point; orchestrates topological ordering, level-by-level parallel generation, reuse of unchanged docs, and saving of all design documents. |

All other functions/classes in the module (`_topological_sort_by_level`, `_build_section_prompt`, `_build_summary_prompt`, `_build_callee_context_summary`, `_summarize_code`, `_reduce_caller_usages`, `_summarize_callee_usages`, `_select_outermost_large_definitions`, `_splice_large_definitions`, `_build_implementation_context`, `_generate_section_with_fallback`, `_generate_file_doc`, `_generate_summary`, `_find_source_file`, `_save_doc`, `_parse_md_sections`, `_sync_md_to_json`) are prefixed with an underscore and are internal implementation details, not part of the module's public interface. As confirmed by the "External Files Using This File" section, only `generate_all_docs` is imported and called externally (from `codetwine/pipeline.py`).

## Design Patterns and Decisions

- **Staged/progressive degradation (fallback pipeline)**: `_generate_section_with_fallback` implements a deliberate multi-stage strategy that reduces prompt size incrementally only as needed (cheap non-LLM reductions first — dropping caller usage bodies, dropping callee doc-context — followed by LLM-based summarization stages), minimizing unnecessary LLM calls while maximizing the chance of successful generation under context-window limits.
- **Shared caching**: a single `summary_cache` dict (keyed by SHA256 of code text) is threaded through the whole run so that the same code symbol is never summarized twice across files/sections, reducing redundant LLM calls.
- **Kahn's algorithm (BFS-based topological sort) with cycle handling**: `_topological_sort_by_level` groups files into dependency-depth levels, explicitly detecting and logging circular dependencies by placing leftover files in a final level rather than failing.
- **Incremental build design**: `_needs_regeneration` and `_is_doc_complete` implement a change-propagation rule — a file is regenerated if it changed, or if any of its dependencies changed or was regenerated in the same run — avoiding unnecessary LLM work on unaffected files.
- **Bidirectional Markdown/JSON sync**: `_save_doc` and `_sync_md_to_json` allow manual edits to the generated Markdown to be reflected back into the canonical JSON representation, based on modification-time comparison, supporting a human-in-the-loop editing workflow.
- **Separation of prompt-building from execution**: prompt construction (`_build_section_prompt`, `_build_summary_prompt`) is kept as pure string-building functions separate from the async generation/fallback logic, aiding testability and clarity.

# Definition Design Specifications

## Module-level constants (prompt templates and text fragments)

The file defines a large set of string constants (`HEADER_TARGET_FILE`, `HEADER_SOURCE_CODE`, `HEADER_CALLEE_USAGES`, `CALLEE_USAGES_SCHEMA_NOTE`, `HEADER_CALLER_USAGES`, `CALLER_USAGES_SCHEMA_NOTE`, `HEADER_CALLEE_CONTEXT`, `CALLEE_CONTEXT_NOTE`, `HEADER_REQUEST`, `SECTION_REQUEST_TEMPLATE`, `OUTPUT_LANGUAGE_INSTRUCTION`, `FACTUAL_ACCURACY_INSTRUCTION`, `HEADER_IMPL_CONTEXT`, `IMPL_CONTEXT_NOTE`, `HEADER_DOC_CONTENT`, `SUMMARY_CHAR_LIMIT`, `CODE_SUMMARY_PROMPT`, `CODE_SUMMARY_MARKER`, `CODE_SUMMARY_FAILED_NOTE`). These exist to centralize and version-control the exact wording sent to the LLM, so prompt structure can be audited/tuned independently of the assembly logic. `_HEADER_EXTENSIONS` (set of C/C++ header suffixes) and `_IMPL_EXTENSIONS` (ordered list of implementation suffixes) drive header/implementation-file pairing; the ordered list implies a priority when multiple implementation extensions could match.

## `_topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]`

Groups all files (including callees not present as top-level entries) into dependency-depth levels using a reverse-graph Kahn's algorithm (BFS by in-degree of the "depended-upon" direction), so that a file only appears once all its dependencies have appeared in earlier levels. Level 0 holds leaf files with no dependencies. Design intent: this ordering lets document generation for a file safely reference already-generated summaries of its dependencies. Each level's file list is sorted alphabetically for deterministic output. Circular dependencies are handled by design: any files left unprocessed after the BFS drains (`remaining`) are forced into a final level and a warning is logged, rather than raising an error, so generation can still proceed. Assumes `callers`/`callees` entries reference file paths consistent with `dep_info["file"]`.

## `_build_section_prompt(section, source_code, file_deps, callee_context, implementation_context="") -> str`

Builds the full LLM prompt for generating one design-document section. Responsibility: consistently interleave the target file's source, optional implementation-file context (for headers), dependency (`callee_usages`) and dependent (`caller_usages`) listings with their attached source/usage snippets, dependency summary context, and the section-specific request/instructions — always in a fixed order to keep prompt structure predictable across calls. Only sections with non-empty data (e.g., empty `callee_usages`) are included, minimizing prompt size when information is absent. `output_path_to_rel` is applied to all file paths pulled from `file_deps` to present source-relative rather than internal output-directory paths to the LLM. Language and factual-accuracy instructions are always appended last, ensuring they cannot be diluted or overridden by section-specific instructions earlier in the prompt.

## `_build_summary_prompt(file_path, section_contents, summary_prompt, summary_max_chars) -> str`

Assembles the prompt for compressing an already-generated multi-section document into a single summary. Concatenates each section's title and content verbatim (no filtering), then appends the template-supplied `summary_prompt`, an explicit character-count ceiling, and the output-language instruction. Exists to decouple summary-generation prompting from section-generation prompting, since the input is generated content rather than source code, and no factual-accuracy/source-fidelity instruction is needed (the source is the design doc itself).

## `_build_callee_context_summary(file_deps, doc_map) -> str`

Reduces potentially large per-dependency design documents down to just their one-line `summary` field, producing a compact context block usable across many downstream prompts. Deduplicates and sorts dependency files by their `from` field before lookup for deterministic ordering. A path-format mismatch is explicitly handled: `callee_usages[].from` uses "output" path format while `doc_map` keys use source-relative paths, so `output_path_to_rel` is applied before lookup. Dependencies whose document isn't yet in `doc_map` (not yet generated, e.g. due to cycles) or that have no summary are silently skipped, which keeps the function resilient to partial `doc_map` state without failing the caller.

## `_line_count(text: str) -> int`

Returns a simple newline-based line count (`count("\n") + 1`). Exists as a single reusable primitive so `CODE_SUMMARY_TRIGGER_LINES` comparisons are computed consistently everywhere a "is this code block large?" decision is needed.

## `async _summarize_code(code, name, llm_client, summary_cache) -> str`

Produces (and caches) a concise LLM-generated behavior description for a code block, used to shrink prompts that would otherwise exceed the LLM's context window. Design decisions:
- Caching key is `sha256(code)`, not `name` or symbol identity — this ensures identical code text (e.g., a widely-reused dependency symbol) is summarized only once per run regardless of how many files/sections reference it, trading a small hash-collision risk for large token savings.
- On `ContextWindowExceededError` (i.e., even the summarization prompt is too large) or an empty/`None` result, a deterministic fallback is used instead of failing: the original first line (assumed to be the signature) plus `CODE_SUMMARY_FAILED_NOTE`. This guarantees callers always receive non-empty, still-somewhat-informative text rather than needing to handle `None`.
- The fallback and successful summaries are cached identically, so a permanently-failing symbol is not repeatedly retried within the same run.

## `_reduce_caller_usages(file_deps: dict) -> dict`

First-stage, no-LLM prompt-size reduction: returns a shallow copy of `file_deps` with `usage_context` stripped from each `caller_usages` entry while retaining `name`/`file` (and any other non-context keys). Rationale: caller usage snippets are considered lower-value context than the callee dependency source, so they are dropped before anything requiring an extra LLM call. Returns the input unchanged (not copied) when there are no caller usages, avoiding unnecessary allocation.

## `async _summarize_callee_usages(file_deps, llm_client, summary_cache) -> dict`

Third-stage reduction (first stage requiring LLM calls): replaces only `callee_usages[].target_context` entries whose line count exceeds `CODE_SUMMARY_TRIGGER_LINES` with an LLM-generated summary via `_summarize_code`; smaller entries are left verbatim since they are assumed not to meaningfully contribute to context overflow. Returns a shallow copy of `file_deps` with a new `callee_usages` list — original `file_deps` is not mutated, since it must still be available if the caller falls back further or needs the original elsewhere.

## `_select_outermost_large_definitions(definitions, trigger_lines) -> list[dict]`

Filters `definitions` to those spanning more than `trigger_lines` lines (using inclusive `end_line - start_line + 1`) and requiring both `start_line` and `end_line` to be present (definitions lacking line info are never candidates). Design intent: when a large class and its large methods are both present as separate definition entries, only the outermost enclosing definition should be summarized/spliced — summarizing a nested method independently after its enclosing class has already been replaced would be redundant or produce an inconsistent result. This is achieved by sorting candidates by `(start_line, -end_line)` (i.e., earliest and widest first) and then greedily accepting definitions whose `start_line` falls after the current selection's `covered_end`, skipping any that start inside an already-selected range. This is a variant of the classic interval-scheduling/merging algorithm applied specifically to enforce a non-overlapping, outermost-only selection.

## `async _splice_large_definitions(source_code, definitions, llm_client, summary_cache) -> str`

Last-resort (stage 4) reduction: rewrites the target file's own source, replacing each selected large definition's line range with a `CODE_SUMMARY_MARKER` header plus an LLM-generated behavior summary, while leaving all other lines (including small definitions and non-definition code) untouched. Key design points:
- Uses `str.split("\n")` (not `splitlines()`) specifically to preserve an exact 1-based line-number mapping matching the tree-sitter-derived `start_line`/`end_line` values in `definitions`, since row numbers from tree-sitter are newline-delimited.
- Prefers the definition's own precomputed `context` field for the code to summarize, falling back to slicing `lines[start_line-1:end_line]` only if `context` is absent — avoiding redundant re-extraction when the data is already available.
- Returns the original `source_code` unchanged when no definition qualifies, so callers can treat the "no reduction possible" case without special-casing.
- Constraint: assumes `definitions` line numbers are accurate and non-overlapping after `_select_outermost_large_definitions` filtering; overlapping/incorrect line data would corrupt the splice.

## `_build_implementation_context(file_rel, file_output_dir) -> str`

For header files (extension in `_HEADER_EXTENSIONS`), attempts to locate a corresponding implementation file by checking, in the priority order defined by `_IMPL_EXTENSIONS`, sibling output directories named `{stem}_{impl_ext}` under the same parent as the header's own output directory, and returns the first match's full contents. Returns `""` immediately for non-header extensions (early exit, no filesystem access), and `""` if no implementation file is found after trying all extensions. Design intent: headers often lack behavioral detail (only declarations), so pairing with the `.cpp`/`.c` implementation gives the LLM the information needed to describe actual behavior. Only a single, first-found implementation is used — not all matches — implying headers are assumed to pair with at most one implementation file within this naming scheme.

## `async _generate_section_with_fallback(section, source_code, file_deps, callee_context, file_path, llm_client, summary_cache, implementation_context="") -> str | None`

Orchestrates a single section's generation with a strict, cumulative 5-stage prompt-reduction pipeline triggered only by `ContextWindowExceededError` (stages 0–4, as documented in the module-level docstring: full → drop caller bodies → drop callee context → summarize callee usages → summarize source definitions). Design rationale for the ordering: cheaper/no-LLM-cost reductions (stages 1–2) are always attempted before LLM-cost-incurring reductions (stages 3–4), to avoid unnecessary extra LLM calls when a simple drop of low-value context resolves the overflow. Stages 3 and 4 are gated behind `ENABLE_CODE_SUMMARY`, so the whole fallback mechanism can be disabled for cost/latency reasons, in which case failure after stage 2 is final. Each reduction stage's inputs are reused/extended from the prior stage's fully-reduced state (e.g., stage 2 onward always uses `deps_no_caller`), so reductions compound rather than reset. Returns `None` only if every stage (allowed under the current `ENABLE_CODE_SUMMARY` setting) is exhausted, signaling total failure for this section to the caller.

## `async _generate_file_doc(file_rel, file_output_dir, doc_map, template, llm_client, summary_cache) -> dict | None`

Top-level per-file generation routine invoked once per file. Responsibilities: locate and read the copied source file (via `_find_source_file`), load `file_dependencies.json`, build the reusable `callee_context` summary and (if applicable) `implementation_context` once per file (not per section, avoiding redundant filesystem/computation work), then generate each template section independently via `_generate_section_with_fallback`. Design decisions:
- Missing source file or missing `file_dependencies.json` are treated as unrecoverable for this file — logged and `None` returned immediately, rather than attempting partial generation.
- Failure of an individual section (returns `None`) is non-fatal: it is logged and skipped, allowing the document to still be produced from the remaining successful sections. Only if *all* sections fail does the whole file generation fail (logged as an error, `None` returned) — reflecting a design choice to prefer a partial document over none when possible.
- The document summary is generated last, using only the sections that succeeded, and its failure does not invalidate section content (summary falls back to `""`).

## `async _generate_summary(file_path, section_list, template, llm_client) -> str | None`

Thin wrapper that builds the summary prompt and delegates to `llm_client.generate`, treating *any* exception (not just `ContextWindowExceededError`) as a non-fatal failure — logged and `None` returned — since a missing summary is acceptable (the section content is the primary artifact) and there is no fallback/reduction strategy defined for summary generation.

## `_find_source_file(output_dir, file_rel) -> str | None`

Looks up the copied source file strictly by basename (`os.path.basename(file_rel)`) inside `output_dir`, returning `None` if absent. Exists as an isolated, easily-testable single-purpose lookup rather than inlining it, since the copy layout (basename directly under the file's own output dir) is a convention that other functions should not need to know about directly.

## `_save_doc(doc: dict, output_dir: str) -> None`

Persists a generated document as both `doc.md` (human-editable) and `doc.json` (machine-authoritative), writing MD first and JSON second so that JSON's mtime is guaranteed `>=` MD's mtime — this ordering is a deliberate invariant relied upon by `_sync_md_to_json`'s "MD newer than JSON means user-edited" timestamp check. Before writing Markdown, each section's content has a duplicate leading Markdown heading matching its own title stripped via regex (`\A\s*#+\s+{title}\s*\n*`), because the LLM sometimes echoes the section title as a heading in its response, which would otherwise duplicate the heading this function itself adds. The summary is appended as a final `# Summary` section only if non-empty, keeping the two output formats byte-for-byte reconstructible from the same `doc` dict (used later by round-trip logic).

## `_parse_md_sections(md_text, section_titles) -> dict[str, str]`

Parses an edited `doc.md` back into a title→content mapping, using a regex that matches only exact, whole-line `# {title}` headings (anchored with `^`/`$` and `re.MULTILINE`) among the known `section_titles`, so arbitrary user-added headings elsewhere in a section's body do not get misidentified as section boundaries. Content between one matched heading and the next (or end of text for the last one) is stripped of leading/trailing whitespace. Titles not found in the text are simply absent from the result rather than raising, since partial user edits (e.g., a user deleting a section heading) must not break the caller.

## `_sync_md_to_json(output_dir: str) -> None`

Implements one-way "manual edit" propagation: if a user hand-edits `doc.md` (detected purely via file modification time being newer than `doc.json`'s), those edits are parsed and merged back into `doc.json`, then both files are re-saved (via `_save_doc`, which also resets mtimes to the MD-first invariant). Key constraints/design decisions:
- Nothing happens if either file is missing, or if MD is not strictly newer than JSON — this is a cheap, best-effort check rather than a content diff, so a no-op re-save of MD without content changes will not trigger a sync (mtime advances but content is identical, and even if it does trigger a sync, no real change would be applied).
- A malformed JSON (`JSONDecodeError`/`OSError`) silently aborts the sync rather than raising, since this is a background reconciliation step and should not crash pipeline execution.
- Per-section merging is deliberately conservative: a section's content is only accepted from the parsed MD if *both* that section's own title *and* the title of its immediate next section (by original JSON order; `"Summary"` sentinel for the last section) are present in the parsed MD. This guards against ambiguous/incorrect content boundaries when the user has deleted or renamed a heading, at the cost of skipping otherwise-valid edits to the last remaining section if a subsequent heading is missing.
- The summary section is synced separately/unconditionally if present in `parsed`, since it has no "next section" ambiguity concern (it's always last).
- If nothing actually changed (`changed` remains `False`), no write and no re-save occurs, avoiding unnecessary mtime churn.

## `async generate_all_docs(base_output_dir, project_dep_list, llm_client, max_workers=MAX_WORKERS, changed_files=None) -> None`

Top-level pipeline entry point orchestrating full-project document generation. Responsibilities and design decisions:
- Loads the section/summary template once from `DOC_TEMPLATE_PATH` and computes topological levels once up front, then processes strictly level-by-level (never crossing levels concurrently) so that `doc_map` used for `callee_context` is guaranteed complete for all of a file's dependencies before that file is processed — this is why the reverse-graph leveling in `_topological_sort_by_level` is a hard prerequisite, not an optimization.
- Within a level, files are processed in a shared, run-wide `summary_cache` (passed down uniformly) and dispatched in batches limited to `max_workers` concurrent `asyncio` tasks, trading full-level parallelism for bounded concurrency (e.g., to respect API rate limits).
- `_needs_regeneration` (nested closure) implements incremental-build logic: regeneration is required if `changed_files` is `None` (full rebuild), the file itself changed, or *any* of its callees either changed or were *already regenerated in this run* (`regenerated_files`) — this transitive propagation ensures that a change deep in the dependency graph correctly invalidates all its transitive dependents' cached documents, not just direct ones with stale content, without needing to recompute a full transitive closure ahead of time (it is discovered incrementally as levels are processed, since callees are always in earlier or equal levels).
- `_is_doc_complete` (nested closure) validates a reused/cached `doc.json` by comparing its section `id`s exactly against the current template's expected section `id`s (both missing and extra section ids are treated as incomplete) and requiring a non-empty `summary` whenever the template defines a `summary_prompt`. This guards against reusing stale documents generated from an older/different template version.
- `process_one` (nested closure) is the actual per-file unit of work: it skips generation entirely (reads existing `doc.json`, after first syncing any manual `doc.md` edits) when `_needs_regeneration` is false *and* the existing doc passes `_is_doc_complete`; otherwise it fully regenerates via `_generate_file_doc`, saves via `_save_doc`, and records the file in `regenerated_files` (used by later levels' `_needs_regeneration` checks). A missing output directory is treated as a hard failure for that file (logged, `None` returned) rather than attempting to create it.
- Exceptions raised by individual `process_one` tasks (via `asyncio.gather(..., return_exceptions=True)`) are caught, logged, and do not abort processing of the rest of the batch/level — an intentional resilience choice so one file's unexpected failure does not halt the entire multi-file run.
- `doc_map` is updated only with successfully generated/reused documents (`if doc:`), immediately after each batch completes (not deferred to end-of-level), so subsequent batches within the same level can already use documents from earlier batches in that same level.

# Dependency Description

## Dependencies (what this file uses)

This file relies on several project-internal modules to perform its role of generating design documents:

- **`codetwine/utils/file_utils.py`** (`output_path_to_rel`, `resolve_file_output_dir`): Used to convert stored output-format paths back into source-relative paths for display in prompts and headers, and to resolve the absolute output directory for a given source file so that source copies and dependency JSON can be located and documents can be saved.

- **`codetwine/llm/client.py`** (`LLMClient`): Provides the `generate()` method used to send assembled prompts to the LLM and receive generated section content, summaries, and code behavior summaries.

- **`codetwine/llm/__init__.py`** (`ContextWindowExceededError`): Caught throughout the fallback pipeline (`_summarize_code`, `_generate_section_with_fallback`) to detect when a prompt exceeds the model's context window, triggering progressive prompt-reduction strategies.

- **`codetwine/config/settings.py`** (`MAX_WORKERS`, `DOC_TEMPLATE_PATH`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS`): Supplies configuration values controlling parallelism (`MAX_WORKERS`), the path to the section-definition template (`DOC_TEMPLATE_PATH`), the language for generated text (`OUTPUT_LANGUAGE`), the summary length limit (`SUMMARY_MAX_CHARS`), whether the code-summarization fallback is active (`ENABLE_CODE_SUMMARY`), the line-count threshold that triggers summarization (`CODE_SUMMARY_TRIGGER_LINES`), and the character limit for code summaries (`CODE_SUMMARY_MAX_CHARS`).

## Dependents (what uses this file)

- **`codetwine/pipeline.py`** (`generate_all_docs`): Invokes `generate_all_docs` as part of the overall processing pipeline, passing in the base output directory, the project dependency list, the LLM client, worker count, and the set of changed files, in order to trigger design document generation for the whole project when documentation generation is enabled.

## Direction of dependency

The dependency relationship is unidirectional: this file depends on `file_utils.py`, `client.py`, `llm/__init__.py`, and `settings.py` for its internal operations, while `pipeline.py` depends on this file to orchestrate document generation. None of the dependency modules depend back on this file.

# Data Flow

## Overview

`generate_all_docs` is the entry point. It orchestrates document generation for a set of project files, driven by dependency-ordered levels, per-file prompt construction, LLM calls with progressive fallback, and JSON/Markdown output.

```
project_dep_list (JSON-derived list)
        │
        ▼
_topological_sort_by_level  →  level_list: list[list[file_rel]]
        │
        ▼ (per level, in batches of max_workers)
process_one(file_rel)
        │
        ├─ reuse path: doc.json (+ doc.md sync) ──────────► existing_doc
        │
        └─ regenerate path:
             source_code (from copied file)
             file_dependencies.json (definitions, callee_usages, caller_usages)
                    │
                    ▼
           _generate_file_doc
                    │
             per-section: _generate_section_with_fallback
                    │        (builds prompt, calls LLMClient.generate,
                    │         shrinks context on ContextWindowExceededError)
                    ▼
             section_list: [{id, title, content}, ...]
                    │
                    ▼
             _generate_summary → summary text
                    │
                    ▼
             doc: {file, sections, summary}
                    │
                    ▼
             _save_doc → doc.json + doc.md
        │
        ▼
doc_map[file_rel] = doc  (fed as callee context to later levels)
```

## Input Data

| Source | Format | Purpose |
|---|---|---|
| `project_dep_list` (param) | `list[dict]`: `{"file": str, "callers": list, "callees": list}` | Drives topological leveling |
| `<output_dir>/<file>` (copied source) | raw text | Source code embedded in prompts |
| `<output_dir>/file_dependencies.json` | dict: `definitions`, `callee_usages` (`name`, `from`, `target_context`), `caller_usages` (`name`, `file`, `usage_context`) | Dependency/usage context for prompts |
| `DOC_TEMPLATE_PATH` JSON | dict: `sections` (`id`, `title`, `prompt`), `summary_prompt` | Defines what to ask the LLM per section/summary |
| `doc_map` (in-memory, growing) | `{file_rel: {file, sections, summary}}` | Supplies `summary` text of already-processed dependency files |
| `changed_files` (optional set) | file paths | Determines reuse vs. regeneration |
| existing `doc.json` / `doc.md` | JSON / Markdown | Reuse or manual-edit sync source |

## Transformation Flow

1. **Leveling**: `_topological_sort_by_level` builds forward/reverse adjacency from `callees`, computes in-degrees, and produces `level_list` (BFS by dependency depth; leftover cycle files appended last).
2. **Per-file decision**: `_needs_regeneration` checks `changed_files`/`regenerated_files` against the file and its callees to choose reuse or regeneration; `_is_doc_complete` validates a reused doc against the template's section IDs and summary requirement.
3. **Prompt assembly** (`_build_section_prompt`): concatenates target file header, source code, optional implementation-file context (headers only, via `_build_implementation_context`), callee/caller usage listings with source snippets, callee dependency-doc summaries (`_build_callee_context_summary`, pulled from `doc_map`), and the section-specific request + language/factual-accuracy instructions.
4. **Generation with fallback** (`_generate_section_with_fallback`): tries the LLM call at progressively reduced context on `ContextWindowExceededError`:
   - Stage 0: full prompt
   - Stage 1: drop caller `usage_context` (`_reduce_caller_usages`)
   - Stage 2: drop `callee_context` text
   - Stage 3: summarize large callee `target_context` via LLM (`_summarize_callee_usages` → `_summarize_code`, cached by code hash)
   - Stage 4: summarize large in-file definitions (`_splice_large_definitions` + `_select_outermost_large_definitions`), replacing large code ranges with summary markers in the source text itself
5. **Summary generation** (`_generate_summary` / `_build_summary_prompt`): concatenates all generated section titles/contents into one prompt asking for a bounded-length overall summary.
6. **Result aggregation**: sections + summary assembled into a `doc` dict; on success it's persisted and registered in `doc_map` for subsequent levels; on failure, warnings/errors are logged and the file is skipped.
7. **Persistence** (`_save_doc`): strips duplicate title headers from section content, writes `doc.md` (headings per section + summary), then writes `doc.json` (mirrors the same dict) after the MD file so JSON mtime ≥ MD mtime.
8. **Manual-edit sync** (`_sync_md_to_json`, only on reuse path): if `doc.md` is newer than `doc.json`, parses MD sections by `# {title}` headings, overwrites matching JSON section content/summary when boundaries are unambiguous, and re-saves both files.

## Output Data

| Destination | Format | Content |
|---|---|---|
| `<output_dir>/doc.json` | JSON | `{"file": str, "sections": [{"id","title","content"}], "summary": str}` |
| `<output_dir>/doc.md` | Markdown | `# Design Document: <file>` + one `# <title>` block per section + `# Summary` block |
| `doc_map` (in-memory) | dict | Accumulated docs, consumed as callee context for later-level files |
| logs / stdout | text | Progress (`OK`/`REUSE`/`SKIP`/`INCOMPLETE`), warnings on fallback stages and failures |

## Key Data Structures

- **`level_list: list[list[str]]`** — files grouped by dependency depth; processed level-by-level, batch-by-batch (`max_workers`).
- **`file_deps: dict`** — `definitions` (with `start_line`/`end_line`/`context`/`name`), `callee_usages` (`name`, `from`, `target_context`), `caller_usages` (`name`, `file`, `usage_context`); progressively reduced/summarized across fallback stages.
- **`summary_cache: dict[str,str]`** — SHA256(code) → behavior summary; shared across the whole run to avoid re-summarizing identical code blocks.
- **`doc: dict`** — `{file, sections: [{id, title, content}], summary}`; the unit persisted to JSON/MD and cached in `doc_map`.
- **`section: dict`** (from template) — `{id, title, prompt}`; drives one `_generate_section_with_fallback` call each.

# Error Handling

## Overall Strategy

`doc_creator.py` follows a **graceful degradation** philosophy at every level: individual failures (a missing file, a section that cannot be generated, an oversized prompt, a summary call that fails) are logged and either skipped or substituted with a deterministic fallback, so that a single bad input never aborts the entire multi-file, multi-level generation run. The one exception is per-task-level isolation: `asyncio.gather(..., return_exceptions=True)` ensures that an unexpected exception in one file's processing does not crash the batch or other concurrent tasks — it is logged and treated as a failure for that file only.

For LLM context-window overflow specifically, the file implements a **progressive, cumulative fallback pipeline** (`_generate_section_with_fallback`): rather than failing outright, the prompt is repeatedly shrunk (dropping caller source snippets, dropping dependency doc summaries, then LLM-summarizing large callee dependencies, then LLM-summarizing large definitions in the source itself) until generation succeeds or all stages are exhausted, at which point the section is skipped rather than the whole file failing.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `ContextWindowExceededError` during section generation | Caught per stage in `_generate_section_with_fallback`; triggers progressive prompt reduction (stages 0–4); logged as a warning per stage | Section generation is retried with a smaller prompt; only fails if all stages are exhausted |
| `ContextWindowExceededError` during code summarization (`_summarize_code`) | Caught; summary is set to `None`, triggering a deterministic fallback (signature line + `CODE_SUMMARY_FAILED_NOTE`) | Summarization never raises; downstream prompt always receives usable placeholder text |
| Section generation completely fails (all fallback stages exhausted) | `result is None`; logged as a warning and the section is skipped (not added to `section_list`) | Document is generated with fewer sections rather than failing entirely |
| All sections fail for a file | Logged as an error in `_generate_file_doc`; function returns `None` | File is skipped (`SKIP` logged) but does not stop processing of other files |
| Summary generation failure (`_generate_summary`) | Broad `except Exception` catches any error, logs a warning, returns `None` | Document is still saved with an empty `summary` field rather than failing |
| Missing source file or `file_dependencies.json` | Checked explicitly; logged as a warning; function returns `None` | File is skipped entirely for document generation |
| Missing/invalid output directory for a file (`process_one`) | Checked via `os.path.isdir`; logged as a warning; returns `(file_rel, None)` | File is skipped without stopping other files or levels |
| Corrupted/unreadable existing `doc.json` during reuse check | `json.JSONDecodeError` / `OSError` caught and silently ignored (`pass`) | Falls back to full regeneration instead of failing |
| Corrupted/unreadable `doc.json` during MD→JSON sync (`_sync_md_to_json`) | Same exceptions caught; function returns early without syncing | Sync is skipped silently; original JSON remains untouched |
| Exception raised inside a per-file task during batch processing | `asyncio.gather(..., return_exceptions=True)`; exception object is checked via `isinstance` and logged as an error | The failing file is skipped; other files in the same batch/level continue processing |
| Circular dependencies in topological sort | Detected via leftover unprocessed nodes after Kahn's algorithm; logged as a warning; remaining files are forced into the last level | Sorting still completes and produces a usable (if reordered) level list instead of raising |
| Incomplete existing design document (missing sections/summary) on reuse | Detected via `_is_doc_complete`; logged as info; regeneration is triggered instead of reuse | Ensures stale/partial documents are not silently reused |

## Design Considerations

- **Caching amortizes retries**: the `summary_cache` (keyed by SHA256 of code text) ensures that once a symbol's fallback summary is computed (successfully or via the deterministic fallback), it is never regenerated, limiting the cost of repeated failures across files and sections.
- **Fail-fast is avoided in favor of partial results**: at nearly every layer (section, file, batch, sort), the design prefers logging + skipping/degrading over propagating exceptions upward, so that `generate_all_docs` can always complete and report how many of the total files succeeded.
- **Exception scope is deliberately narrow** in JSON-read paths (`json.JSONDecodeError, OSError`) rather than catching all exceptions, so that unrelated bugs are not masked, while in summary generation a broader `Exception` catch is used to guarantee the pipeline never halts on an LLM call it does not fully control.

# Summary

Core document-generation engine of codetwine: given a project dependency graph and per-file source/dependency metadata, drives an LLM to produce Markdown+JSON design docs for every file. Public interface: `generate_all_docs(base_output_dir, project_dep_list, llm_client, max_workers, changed_files)`. Handles topological leveling, prompt assembly, context-overflow fallback (progressive prompt reduction/summarization), incremental reuse via doc.json/doc.md sync, caching, and parallel batch execution. Key structures: level_list, file_deps, doc_map, summary_cache, doc {file, sections, summary}.
