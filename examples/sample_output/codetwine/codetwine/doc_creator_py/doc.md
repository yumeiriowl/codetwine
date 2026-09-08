# Design Document: codetwine/doc_creator.py

# Design Specification

**Overview**

Orchestrates end-to-end generation, caching, and persistence of per-file design documents by prompting an LLM with source code, dependency context, and prior summaries, with progressive context-window fallback and incremental reuse.

- Call `generate_all_docs` to produce design documents for an entire project: it topologically sorts files by dependency level, generates or reuses each file's `doc.json`/`doc.md`, and returns nothing (side effect: files written to `base_output_dir`).
- Call `load_doc` to read an existing file's `doc.json` (used by pipeline code to detect whether a file's source hash changed and whether regeneration is needed).
- Use `_topological_sort_by_level` when a caller needs files grouped by dependency depth (level 0 = no dependencies) so that dependent files are documented after their dependencies.
- Rely on `_generate_file_doc` / `_generate_section_with_fallback` when building a single file's document programmatically, including handling of large source or dependency contexts that exceed the LLM's context window.

This file depends on `codetwine/utils/file_utils.py` for path translation (`output_path_to_rel`, `resolve_file_output_dir`) and file hashing (`compute_file_hash`); on `codetwine/llm/client.py`'s `LLMClient` to issue prompts; on `codetwine/llm/__init__.py`'s `ContextWindowExceededError` to detect and recover from oversized prompts; and on `codetwine/config/settings.py` for tunables (`MAX_WORKERS`, `DOC_TEMPLATE_PATH`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS`). It is used by `codetwine/pipeline.py`, which calls `load_doc` to detect changed files via `source_hash` comparison and calls `generate_all_docs` as the main documentation-generation step of the pipeline.

Notable design decisions: documents are generated level-by-level with intra-level parallelism (`asyncio.gather`, batched by `max_workers`); on `ContextWindowExceededError` the prompt is shrunk through four cumulative fallback stages (drop caller usage bodies, drop dependency doc summaries, LLM-summarize large callee symbols, then LLM-summarize large in-file definitions), with summaries cached in-memory by SHA256 of the code to avoid re-summarizing the same symbol; incremental runs skip regeneration when `changed_files` shows neither the file nor any of its callees changed, reusing existing `doc.json` only if it is structurally complete; and manual edits to `doc.md` are synced back into `doc.json` when the Markdown file's mtime is newer.

**Definitions**

## `HEADER_TARGET_FILE`
Format-string constant used as the opening prompt line naming the file under documentation; consumed by `_build_section_prompt` and `_build_summary_prompt`.

## `HEADER_SOURCE_CODE`
Static heading text inserted before the fenced source-code block in section prompts.

## `HEADER_CALLEE_USAGES`
Heading text introducing the list of external symbols this file depends on, used only when `callee_usages` is non-empty in `file_deps`.

## `CALLEE_USAGES_SCHEMA_NOTE`
Explanatory text clarifying the `name`/`from` schema and the meaning of dependency source code shown under each callee entry, inserted right after `HEADER_CALLEE_USAGES`.

## `CALLEE_SOURCE_CODE_LABEL`
Label preceding each callee's `target_context` code block in the prompt.

## `HEADER_CALLER_USAGES`
Heading text introducing the list of external files that use this file, shown only when `caller_usages` is non-empty.

## `CALLER_USAGES_SCHEMA_NOTE`
Explanatory text describing the `name`/`file` schema for caller usage entries.

## `CALLER_SOURCE_CODE_LABEL`
Label preceding each caller's `usage_context` code snippet in the prompt.

## `HEADER_CALLEE_CONTEXT`
Heading introducing the block of previously generated dependency design-document summaries (`callee_context`).

## `CALLEE_CONTEXT_NOTE`
Explanatory note telling the LLM to use dependency summaries as reference for understanding external module responsibilities.

## `HEADER_REQUEST`
Heading marking the instructional part of the prompt, used in both section and summary prompts.

## `SECTION_REQUEST_TEMPLATE`
Format string that states which section title the LLM must write content for.

## `OUTPUT_LANGUAGE_INSTRUCTION`
Format string appended to prompts to force the LLM's output language, filled with `OUTPUT_LANGUAGE`.

## `FACTUAL_ACCURACY_INSTRUCTION`
Static instruction appended at the end of section prompts forbidding speculative or contradictory content relative to the source code; part of the factual-accuracy/error-avoidance policy.

## `HEADER_IMPL_CONTEXT`
Heading introducing the corresponding `.cpp`/`.c` implementation source when documenting a header file.

## `IMPL_CONTEXT_NOTE`
Explanatory note telling the LLM to use the paired implementation file to understand declarations in a header.

## `HEADER_DOC_CONTENT`
Heading introducing the concatenated already-generated section contents in the summary prompt.

## `SUMMARY_CHAR_LIMIT`
Format string specifying the maximum character count constraint appended to the summary-generation request.

## `CODE_SUMMARY_PROMPT`
Format string used to ask the LLM to compress a large code symbol into a short behavior description while preserving its signature line; used by `_summarize_code` for the context-overflow fallback path.

## `CODE_SUMMARY_MARKER`
Placeholder header (`# [summarized] {name}`) inserted in place of a spliced-out large definition, used by `_splice_large_definitions`.

## `CODE_SUMMARY_FAILED_NOTE`
Deterministic fallback comment appended after a code symbol's signature line when `_summarize_code`'s LLM call fails or returns nothing, keeping the block informative without crashing generation.

## `_HEADER_EXTENSIONS`
Set of C/C++ header file extensions (`.h`, `.hpp`, `.hh`, `.hxx`) used by `_build_implementation_context` to decide whether to search for a paired implementation file.

## `_IMPL_EXTENSIONS`
Ordered list of implementation extensions (`cpp`, `c`, `cc`, `cxx`) tried by `_build_implementation_context` when locating the file paired with a header.

## `_topological_sort_by_level`
Performs a level-based (Kahn's-algorithm-style) topological sort of `project_dep_list` using each entry's `callees`, producing dependency-ordered batches so that files are documented only after their dependencies; on cycle detection it logs a warning and forces remaining files into the final level rather than failing, ensuring `generate_all_docs` always terminates.

## `_build_section_prompt`
Assembles the full LLM prompt for one template section by concatenating the target file's header, source code, optional implementation context, `callee_usages`/`caller_usages` listings with their attached source snippets, dependency doc-summary context, and the section-specific request plus language and factual-accuracy instructions; called at every fallback stage in `_generate_section_with_fallback` with progressively reduced inputs.

## `_build_summary_prompt`
Builds the prompt requesting a whole-document summary by combining all generated section titles/contents with the template's `summary_prompt`, `SUMMARY_MAX_CHARS` limit, and `OUTPUT_LANGUAGE`; used by `_generate_summary`.

## `_build_callee_context_summary`
Collects the unique set of files referenced in `callee_usages`, looks up each one's previously generated summary in `doc_summary_map` (converting via `output_path_to_rel` since callee `from` paths are output-format), and concatenates them into a bullet list; supplies the `callee_context` argument consumed by section-prompt building.

## `_line_count`
Counts lines in a text block by counting newlines plus one; used to decide whether a definition or dependency symbol exceeds `CODE_SUMMARY_TRIGGER_LINES` and needs LLM summarization.

## `_summarize_code`
Generates and caches (by SHA256 of the code text, in `summary_cache`) a concise LLM-produced behavior summary of a code symbol via `CODE_SUMMARY_PROMPT`, ensuring the same symbol is summarized only once per run; falls back to a deterministic signature-plus-`CODE_SUMMARY_FAILED_NOTE` string on `ContextWindowExceededError` or empty result so callers always receive usable text.

## `_reduce_caller_usages`
Returns a shallow copy of `file_deps` with each `caller_usages` entry's `usage_context` body stripped while keeping `name`/`file`, shrinking the prompt without any LLM call; used as fallback stage 1 in `_generate_section_with_fallback`.

## `_summarize_callee_usages`
Returns a copy of `file_deps` where each `callee_usages` entry's `target_context` longer than `CODE_SUMMARY_TRIGGER_LINES` lines is replaced with an LLM summary via `_summarize_code`, leaving short entries verbatim; used as fallback stage 3 to shrink dependency context.

## `_select_outermost_large_definitions`
Filters `definitions` (using `start_line`/`end_line`) to those spanning more than `trigger_lines` lines, sorts them outer-first, and drops any nested inside an already-selected outer range so no source range is summarized twice; supports `_splice_large_definitions`.

## `_splice_large_definitions`
Replaces selected large in-file definitions with `_summarize_code`-generated summaries directly in the source text (using 1-based line splicing matched to tree-sitter row numbers), leaving smaller definitions and non-definition lines untouched; used as the last-resort fallback stage 4 when the target file itself is too large for the LLM's context window.

## `_build_implementation_context`
Looks up, for a header file (`.h`/`.hpp`/`.hh`/`.hxx`), the sibling implementation directory/file (`{stem}_{impl_ext}/{stem}.{impl_ext}`) under the same base output directory and returns its full source, or an empty string if the file is not a header or no implementation file is found; provides `implementation_context` for section prompts on header files.

## `_generate_section_with_fallback`
Generates one template section's content by calling the LLM and, on `ContextWindowExceededError`, progressively retries with reduced context in a fixed order (drop caller bodies, drop callee doc summaries, summarize large callee symbols, summarize large source definitions), the last two gated by `ENABLE_CODE_SUMMARY`; returns `None` only if every stage fails, allowing the caller to skip that section rather than abort the whole document.

## `_generate_file_doc`
Drives generation of a complete design document for one file: locates the copied source via `_find_source_file`, loads `file_dependencies.json`, builds callee-summary and implementation context, generates every template section (skipping and logging failures rather than aborting), generates the whole-document summary via `_generate_summary`, and returns a dict with `file`, `sections`, `summary`, and `source_hash` (via `compute_file_hash`); returns `None` if the source file, dependency JSON, or every section fails.

## `_generate_summary`
Requests a whole-document summary from the LLM using `template["summary_prompt"]` and `SUMMARY_MAX_CHARS`, catching any exception and logging a warning so a summary failure does not abort document generation; returns `None` on failure.

## `_find_source_file`
Locates the copied source file for a given relative path inside its output directory by basename match, returning its absolute path or `None` if absent; used to load source text before documentation generation.

## `load_doc`
Reads and JSON-parses `doc.json` from a file's output directory, returning `None` on missing file or decode error; used by `generate_all_docs`/`process_one` for reuse checks and by `codetwine/pipeline.py` to compare `source_hash` for change detection.

## `_save_doc`
Persists a design document to both `doc.md` and `doc.json` in the given output directory, stripping any duplicate section-title headers the LLM may have echoed at the start of section content, writing Markdown first (so its mtime does not exceed the JSON's) followed by pretty-printed JSON; called after generation and again by `_sync_md_to_json` to re-normalize output after applying manual edits.

## `_parse_md_sections`
Splits `doc.md` text into a title-to-content mapping using `# {title}` lines matching the provided `section_titles` (including "Summary") as delimiters; used by `_sync_md_to_json` to recover manually edited section text.

## `_sync_md_to_json`
Detects manual edits to `doc.md` by comparing file modification times against `doc.json`, and if the Markdown is newer, parses it via `_parse_md_sections` and overwrites matching section/summary content in the JSON (skipping a section if its next expected heading is missing from the Markdown, to avoid inaccurate boundaries), then re-saves via `_save_doc`; called before reuse in `generate_all_docs` so user edits are not silently lost or overwritten.

## `generate_all_docs`
Top-level pipeline entry point that loads the doc template (`DOC_TEMPLATE_PATH`), topologically sorts `project_dep_list` via `_topological_sort_by_level`, and processes files level by level in parallel batches of `max_workers` using `asyncio.gather`; for each file it decides via `_needs_regeneration` and `_is_doc_complete` whether to reuse an existing complete `doc.json` (syncing manual Markdown edits first) or regenerate through `_generate_file_doc` and persist via `_save_doc`, propagating each file's summary into `doc_summary_map` for use as dependency context by later levels, and treats caller files of any regenerated dependency as needing regeneration too.

# Summary

This module orchestrates automated design-document generation for a codebase's source files, driving an LLM through prompt templates built from source code, dependency usages, and prior summaries. Main definitions: `generate_all_docs`, `load_doc`, `_topological_sort_by_level`, `_generate_file_doc`, `_generate_section_with_fallback`, `_save_doc`, `_sync_md_to_json`. Key concerns: dependency-ordered/parallel generation, context-window overflow fallback via progressive summarization, incremental caching via source hashes, doc.md/doc.json persistence, and reconciling manual Markdown edits back into JSON.
