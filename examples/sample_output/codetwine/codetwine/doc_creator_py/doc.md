# Design Document: codetwine/doc_creator.py

# Design Specification

**Overview**

Generate comprehensive design documents for source files by assembling LLM prompts from source code, dependency metadata, and design document summaries, with progressive fallback strategies for context window overflow.

- Call `generate_all_docs()` to process all files in topologically sorted dependency order, parallelizing within each level, building a map of file summaries for use as context in downstream files, and returning a list of files that failed to generate complete documents.
- Call `load_doc()` to retrieve a previously generated design document from a file's output directory (doc.json format).
- Call `_generate_file_doc()` indirectly via `generate_all_docs()` to produce a single file's design document, including sections and a summary, from source code and file_dependencies.json.
- Call `_build_section_prompt()` to assemble the complete LLM prompt for one documentation section, incorporating source code, dependency information, design document summaries of callees, and section-specific instructions.
- Call `_topological_sort_by_level()` to arrange files by dependency depth so files with no dependencies are processed first, enabling their summaries to inform the documentation of dependent files.

The file depends on `codetwine/config/settings.py` for LLM configuration, output language, character limits, feature flags, and template paths; `codetwine/llm/client.py` to call the LLM for text generation; `codetwine/utils/file_utils.py` to read source files, compute hashes, resolve output directories, and transform paths between project-relative and output formats; and `codetwine/config/logger.py` for progress reporting. It is called by `codetwine/pipeline.py` to generate all design documents after dependency analysis, passing changed files to enable incremental regeneration.

Design decisions: The file implements a four-stage fallback strategy when LLM calls exceed the context window, progressively dropping caller usage bodies, dependency summaries, and then summarizing large dependency symbols and source definitions via cached LLM calls, to ensure documents can be generated even for files with extensive dependencies or large implementations. Manual edits to doc.md are synchronized back to doc.json when the markdown file is newer, preserving user modifications across regeneration cycles. Summaries of dependency files are carried forward in a map between topological levels to provide contextual information without retaining full section text, reducing memory overhead.

**Definitions**

## `_topological_sort_by_level`

Arrange files by dependency depth using Kahn's algorithm, grouping files into levels where level 0 contains files with no dependencies and level N contains files depending only on files at level N-1 or below, enabling parallel processing within each level and sequential context-passing between levels. Returns files as a list of lists (one inner list per level), with circular dependencies detected and logged, placing such files in the final level.

## `_usage_part_list`

Format a list of dependency or dependent usages (from callee_usages or caller_usages in file_dependencies.json) into prompt lines, each usage appearing as a bullet point with symbol name and source file path, optionally followed by an indented code block when source code context is available. Used internally to build the "External Functions/Classes Used" and "External Files Using" sections of the prompt.

## `_build_section_prompt`

Assemble the complete LLM prompt for one documentation section by combining the target file's name, source code, callee and caller usage information (with source code snippets), design document summaries of dependency files, section-specific instructions, output language requirement, and factual accuracy constraints. Returns the full prompt string ready to send to the LLM.

## `_build_summary_prompt`

Construct the LLM prompt for generating a concise design document summary from all previously generated sections, including the target file's name, each section's title and content, summary instructions from the template, a character limit from SUMMARY_MAX_CHARS, and output language specification. Returns the assembled prompt string.

## `_build_callee_context_summary`

Extract design document summaries from the doc_summary_map for all files listed in the target file's callee_usages (dependencies), deduplicate them by file, and concatenate them into a single string formatted as bullet points. This context is inserted into section prompts to help the LLM understand the responsibilities and public interfaces of external modules.

## `_line_count`

Return the number of lines in a text block by counting newlines and adding one, used to determine whether a code symbol is large enough to trigger LLM summarization during context-overflow fallback.

## `_summarize_code`

Generate a concise behavior description of a code symbol via the LLM, caching results by SHA256 hash to avoid re-summarizing identical code across files and sections. On failure or context window exceeded, returns a deterministic fallback consisting of the code's first line (signature) and a note that the summary is unavailable.

## `_reduce_caller_usages`

Return a shallow copy of file_dependencies.json with usage_context bodies (source code snippets) removed from caller_usages, retaining name, file, and line information. Used as stage 1 of context-overflow fallback to shrink the prompt without invoking the LLM.

## `_summarize_callee_usages`

Return a copy of file_dependencies.json where large callee target_context symbols (those exceeding CODE_SUMMARY_TRIGGER_LINES) are replaced by LLM-generated behavior summaries, while small symbols are kept verbatim. Implements stage 3 of context-overflow fallback, using the shared summary_cache to avoid redundant summarization.

## `_select_outermost_large_definitions`

Identify definitions in the source file that exceed CODE_SUMMARY_TRIGGER_LINES in length, then filter to keep only outermost definitions (excluding nested ones like class methods when the class itself is already selected). Returns the filtered list sorted by start_line.

## `_splice_large_definitions`

Replace large definitions in the source code with LLM-generated behavior summaries, inserting summary blocks at the positions of the original definitions while preserving non-definition lines and small definitions unchanged. Implements stage 4 of context-overflow fallback to shrink the source itself when the file is too large, relying on 1-based line numbers and start_line/end_line ranges from file_dependencies.json definitions.

## `_build_implementation_context`

Retrieve the source code of a C/C++ implementation file (.cpp, .c, .cc, .cxx) corresponding to a header file (.h, .hpp, .hh, .hxx) by searching for a same-named implementation file in the output directory tree. Returns empty string for non-header files or when no implementation file is found, used to provide implementation details to the LLM when documenting header files.

## `_generate_section_with_fallback`

Attempt to generate one documentation section via the LLM, falling back through four cumulative prompt reduction stages if context window is exceeded: stage 0 is the full prompt, stage 1 drops caller usage source snippets, stage 2 drops dependency design document summaries, stage 3 summarizes large callee dependency symbols, and stage 4 summarizes large definitions in the source itself. Stages 3–4 only run when ENABLE_CODE_SUMMARY is true and use the shared summary_cache. Returns the generated section text or None if all stages fail.

## `_generate_file_doc`

Generate a complete design document for one file by reading its source code and file_dependencies.json from the output directory, building callee context from doc_summary_map, fetching the corresponding implementation file context if it is a header, generating each template section via `_generate_section_with_fallback()`, generating a summary from all sections, and returning a document dict containing file path, sections, summary, and source file hash. Returns None if the source file or dependencies metadata cannot be read, or if no sections are successfully generated.

## `_generate_summary`

Generate a summary of the complete design document by calling the LLM with a prompt built from the target file's path, all section contents, summary instructions from the template, and SUMMARY_MAX_CHARS character limit. Returns the summary text or None on failure.

## `_find_source_file`

Locate the copied source file in a file's output directory by checking for a file with the same basename as the target file in the given output directory. Returns the absolute path if found, None otherwise.

## `load_doc`

Read a design document (doc.json) from a file's output directory, parsing and returning it as a Python dict. Returns None if the file does not exist or cannot be read due to JSON decode or I/O errors.

## `_save_doc`

Write a design document to both Markdown and JSON formats in the output directory: doc.md (human-editable format) and doc.json (structured format for programmatic access). Strips duplicate section title headers that the LLM may have included in its response before writing. The Markdown file is written first to ensure doc.json is never older than doc.md, supporting subsequent timestamp-based synchronization.

## `_parse_md_sections`

Split a Markdown file's text by known section title delimiters (matching `# {title}` lines) and return a dict mapping each section title to its content. Sections not present in the Markdown are omitted from the result, used to extract manually edited content when synchronizing doc.md back to doc.json.

## `_sync_md_to_json`

Synchronize manual edits from doc.md back to doc.json when the Markdown file is newer, parsing section content from doc.md, comparing it against the JSON, and re-saving the JSON if any section or summary differs. Skips synchronization if the next section heading (in JSON order) is missing from the Markdown, as section boundaries would be inaccurate. Re-writes doc.md after updating JSON to align timestamps and content.

## `_is_doc_complete`

Check whether a design document contains all expected sections (as defined by the template) and a non-empty summary (when the template includes a summary_prompt). Returns false if the section set is incomplete or the summary is missing, true otherwise.

## `_needs_regeneration`

Determine whether a file's design document needs to be regenerated by checking whether changed_files is None (full regeneration mode), the file itself is in changed_files, or any of its callee dependencies (from callee_set_dict) are in changed_files or new_doc_file_set (files regenerated so far in this run). Used during incremental processing to skip files unaffected by changes.

## `generate_all_docs`

Main entry point to generate design documents for all files in the project by loading the template, topologically sorting files by dependency depth, processing files level-by-level in parallel batches, maintaining a doc_summary_map of generated summaries for use as context in downstream files, and saving each document in JSON and Markdown formats. When changed_files is specified, reuses existing documents for unchanged files whose dependencies are also unchanged, supporting incremental regeneration. Returns a list of relative file paths that failed to generate complete documents (missing sections or summary).

## `HEADER_TARGET_FILE`

Format string for the prompt section heading identifying the target file, used in section and summary prompts.

## `HEADER_SOURCE_CODE`

Section heading label for the target file's source code in prompts.

## `HEADER_CALLEE_USAGES`

Section heading label for external functions and classes used by the target file (its dependencies).

## `CALLEE_USAGES_SCHEMA_NOTE`

Explanation of the schema for callee_usages items (name and from fields) and a note that dependency source code snippets are provided to clarify external dependencies.

## `CALLEE_SOURCE_CODE_LABEL`

Label prefix printed before a dependency's source code snippet in the prompt.

## `HEADER_CALLER_USAGES`

Section heading label for external files using the target file (its dependents).

## `CALLER_USAGES_SCHEMA_NOTE`

Explanation of the schema for caller_usages items (name and file fields).

## `CALLER_SOURCE_CODE_LABEL`

Label prefix printed before a caller's usage location source code in the prompt.

## `HEADER_CALLEE_CONTEXT`

Section heading label for design document summaries of dependency files.

## `CALLEE_CONTEXT_NOTE`

Explanation that the following content is summaries of design documents for dependency files, to be used as reference when understanding external module responsibilities.

## `HEADER_REQUEST`

Section heading label for the LLM request or instruction portion of the prompt.

## `SECTION_REQUEST_TEMPLATE`

Format string for the LLM instruction to generate a specific documentation section, with {title} replaced by the section title.

## `OUTPUT_LANGUAGE_INSTRUCTION`

Format string for the instruction to write output in a specific language (from OUTPUT_LANGUAGE setting), with {language} replaced.

## `FACTUAL_ACCURACY_INSTRUCTION`

Instruction emphasizing that descriptions must be based only on provided source code and dependency information, prohibiting speculation or contradictions with the implementation.

## `HEADER_IMPL_CONTEXT`

Section heading label for the corresponding implementation file context when documenting header files.

## `IMPL_CONTEXT_NOTE`

Explanation that the following is the source code of the implementation file matching a header file, to clarify how declarations are implemented.

## `HEADER_DOC_CONTENT`

Section heading label for the design document content when building a summary prompt.

## `SUMMARY_CHAR_LIMIT`

Format string for specifying the character limit for the summary, with {max_chars} replaced by SUMMARY_MAX_CHARS.

## `CODE_SUMMARY_PROMPT`

Format string for the LLM prompt to summarize a code symbol's behavior, with {name} (symbol name), {max_chars} (character limit), {language} (output language), and {code} (the code itself) replaced. Used during context-overflow fallback to shrink large code blocks while retaining their functional meaning.

## `CODE_SUMMARY_MARKER`

Header prefix (e.g., "# [summarized] {name}") inserted into the source code before an LLM-generated behavior summary during large-definition splicing, with {name} replaced by the symbol name.

## `CODE_SUMMARY_FAILED_NOTE`

Deterministic fallback text appended when code summarization fails, indicating that the body is omitted and summary generation is unavailable.

## `_HEADER_EXT_SET`

Set of C/C++ header file extensions (.h, .hpp, .hh, .hxx) used to detect header files for which implementation context should be retrieved.

## `_IMPL_EXT_LIST`

List of implementation file extensions (cpp, c, cc, cxx) searched when locating the implementation file matching a header.

# Summary

# doc_creator.py Summary

Generates comprehensive design documents for source files by orchestrating LLM calls with progressive fallback strategies for context window overflow. Main entry point `generate_all_docs()` processes files in topological dependency order, parallelizing within levels and maintaining a summary map for downstream context. Core functions include `_generate_file_doc()` for single-file documentation, `_build_section_prompt()` for assembling LLM prompts with source code and dependency information, and `_generate_section_with_fallback()` for handling context limits by successively dropping caller snippets, summaries, and large code blocks. Supports incremental regeneration via changed-file tracking, manual markdown synchronization, and C/C++ header-implementation pairing. Depends on LLM client, file utilities, settings, and logger.
