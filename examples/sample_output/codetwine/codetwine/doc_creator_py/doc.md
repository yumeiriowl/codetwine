# Design Document: codetwine/doc_creator.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Generates structured LLM-based design documents for every source file in a project by reading dependency graphs, assembling context-aware prompts, and writing the results as both Markdown and JSON artifacts.

## 2. When to Use This Module

- **Generating documentation for an entire project**: Call `generate_all_docs(base_output_dir, project_dep_list, llm_client, max_workers, changed_files)` from `codetwine/pipeline.py` to produce a `doc.md` and `doc.json` for every file in the project, processed in topological dependency order.
- **Incremental regeneration after a change**: Pass a non-`None` `changed_files` set to `generate_all_docs`; only files that have changed or whose dependencies have changed (or were regenerated) will be re-processed. Unchanged files with a complete existing `doc.json` are reused without an LLM call.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Entry point: topologically sorts all project files, generates design documents level by level in parallel, reuses unchanged documents, and writes JSON + Markdown output for each file. |

## 4. Design Decisions

- **Topological level-by-level processing**: Files are sorted into dependency depth levels so that when a file's document is generated, the summaries of all its dependencies are already available in `doc_map`. This allows callee design document summaries to be injected as context into downstream prompts without requiring a second pass.

- **Progressive context fallback on context window overflow**: When the LLM raises `ContextWindowExceededError`, `_generate_section_with_fallback` retries the same section up to three times with progressively smaller context: first with full callee summaries, then with summaries truncated to 100 characters each, and finally with no callee context at all. This avoids hard failures on large files without requiring manual configuration.

- **MD-to-JSON sync for manual edits**: `_sync_md_to_json` detects when `doc.md` has a newer modification timestamp than `doc.json` and parses the Markdown back into the JSON structure, preserving manual corrections made directly to the Markdown file across subsequent runs.

- **Incremental regeneration propagation**: The `regenerated_files` set tracks which files had their documents regenerated in the current run. Files that depend on a regenerated callee are themselves marked for regeneration even if the callee was not in the original `changed_files` set, ensuring consistency across the dependency graph.

- **Parallel batching within each level**: Within a single topological level, files are processed concurrently using `asyncio.gather` in batches of up to `max_workers`, balancing throughput against API rate limits.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Constant | Type | Purpose |
|---|---|---|
| `HEADER_TARGET_FILE` | `str` | Prompt heading template identifying the target file. Contains `{file}` placeholder. |
| `HEADER_SOURCE_CODE` | `str` | Section heading for the source code block in prompts. |
| `HEADER_CALLEE_USAGES` | `str` | Section heading for dependency (callee) symbol listings. |
| `CALLEE_USAGES_SCHEMA_NOTE` | `str` | Schema description and usage note for callee entries. |
| `CALLEE_SOURCE_CODE_LABEL` | `str` | Label prefixing the dependency source code block for each callee. |
| `HEADER_CALLER_USAGES` | `str` | Section heading for dependent (caller) symbol listings. |
| `CALLER_USAGES_SCHEMA_NOTE` | `str` | Schema description for caller entries. |
| `CALLER_SOURCE_CODE_LABEL` | `str` | Label prefixing the usage location source code block for each caller. |
| `HEADER_CALLEE_CONTEXT` | `str` | Section heading for dependency design document summaries. |
| `CALLEE_CONTEXT_NOTE` | `str` | Explanatory note accompanying the callee summary context section. |
| `HEADER_REQUEST` | `str` | Section heading introducing the LLM instruction. |
| `SECTION_REQUEST_TEMPLATE` | `str` | Per-section instruction template; `{title}` is substituted with the section title. |
| `OUTPUT_LANGUAGE_INSTRUCTION` | `str` | Instruction specifying the output language; `{language}` is substituted. |
| `FACTUAL_ACCURACY_INSTRUCTION` | `str` | Mandatory closing instruction prohibiting speculative or contradictory content. |
| `HEADER_IMPL_CONTEXT` | `str` | Section heading for the corresponding implementation file (C/C++ header support). |
| `IMPL_CONTEXT_NOTE` | `str` | Explanatory note for the implementation file context section. |
| `HEADER_DOC_CONTENT` | `str` | Section heading for assembled design document content in the summary prompt. |
| `SUMMARY_CHAR_LIMIT` | `str` | Character limit instruction template for summary generation; `{max_chars}` is substituted. |
| `_HEADER_EXTENSIONS` | `set[str]` | Frozen set of C/C++ header file extensions: `.h`, `.hpp`, `.hh`, `.hxx`. |
| `_IMPL_EXTENSIONS` | `list[str]` | Ordered list of implementation file extensions to search: `cpp`, `c`, `cc`, `cxx`. |

---

## Functions

---

### `_topological_sort_by_level`

```
_topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]
```

**Responsibility:** Partitions the full set of project files into dependency-depth levels so that files at level N can be processed only after all files they depend on (level < N) have been processed.

**When to use:** Called once at the start of `generate_all_docs` to determine parallel-safe processing batches.

**Design decisions:**

- Uses Kahn's BFS algorithm applied to the **reverse** dependency graph (callers → callees reversed to callees → callers), so that files with no callees (leaf dependencies) appear at level 0.
- All files referenced in any `callees` entry are added to `all_files` even if they lack their own top-level entry in `project_dep_list`, ensuring no file is silently dropped.
- Files involved in circular dependencies are not reachable by Kahn's algorithm and are appended as a final extra level after a logged warning.
- Within each level, files are sorted alphabetically for deterministic output.

**Constraints & edge cases:**

- Circular dependencies are tolerated; affected files are grouped into the last level and a warning is logged.
- An empty `project_dep_list` produces an empty return value.
- `callees` entries that name files not present as top-level entries are still included in the level computation.

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

**Responsibility:** Assembles the complete LLM prompt for a single template section by combining source code, dependency information, caller information, and section-specific instructions.

**When to use:** Called by `_generate_section_with_fallback` for each attempt, with varying `callee_context` values (full, compact, or empty).

**Design decisions:**

- Prompt sections are assembled as a `list[str]` and joined with `"\n"` at the end, avoiding repeated string concatenation.
- The `implementation_context` block is only emitted when the string is non-empty, keeping the prompt minimal for non-header files.
- `callee_usages` entries include the dependency's full source code only when a `target_context` key is present; absence is silently skipped.
- `caller_usages` entries include usage location source code only when a `usage_context` key is present.
- `callee_context` (design document summaries) is only appended when non-empty.
- `FACTUAL_ACCURACY_INSTRUCTION` is always appended last to give it highest positional emphasis.

**Constraints & edge cases:**

- `file_deps.get('file', 'unknown')` is used defensively; a missing `file` key results in `"unknown"` in the heading.
- `output_path_to_rel` is applied to each callee's `from` and each caller's `file` before display.

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

**Responsibility:** Assembles the LLM prompt that instructs the model to produce a concise summary from all already-generated section contents.

**When to use:** Called once per file by `_generate_summary` after all sections have been generated.

**Design decisions:**

- Prefixes each section with `### {title}` so the LLM can distinguish section boundaries within the assembled document.
- The character limit instruction (`SUMMARY_CHAR_LIMIT`) and the language instruction are appended after the `summary_prompt` text so they apply globally.
- Does not append `FACTUAL_ACCURACY_INSTRUCTION`; the summary derives from already-generated content rather than raw source code.

**Constraints & edge cases:**

- `section_contents` elements must each have `title` and `content` keys.
- An empty `section_contents` list produces a prompt with no section content between the headings.

---

### `_build_callee_context_summary`

```
_build_callee_context_summary(
    file_deps: dict,
    doc_map: dict[str, dict],
    compact: bool = False,
) -> str
```

**Responsibility:** Extracts the `summary` field from design documents of all dependency files and concatenates them into a single context string for inclusion in section prompts.

**When to use:** Called twice before generating each file's sections — once for full summaries and once for compact summaries — to prepare the two fallback context levels.

**Design decisions:**

- Deduplicates dependency files from `callee_usages` using a set before iterating, preventing repeated summaries when the same file appears under multiple symbol names.
- `compact=True` truncates each summary to 100 characters and appends `"..."` when truncation occurs, reducing token usage for fallback attempts.
- `output_path_to_rel` is applied to each callee `from` value before looking up in `doc_map`, bridging the output-path / source-relative-path distinction.
- Dependencies without a matching `doc_map` entry or with an empty summary are silently skipped.

**Constraints & edge cases:**

- Returns an empty string when no relevant summaries are available.
- Callee file ordering is alphabetical (derived from `sorted(callee_set)`).

---

### `_build_implementation_context`

```
_build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str
```

**Responsibility:** Locates the implementation file (`.cpp`, `.c`, etc.) corresponding to a C/C++ header file and returns its full source code for inclusion in header-file prompts.

**When to use:** Called once per file in `_generate_file_doc`; immediately returns `""` for non-header files, so it is safe to call unconditionally.

**Design decisions:**

- Extension membership is checked against `_HEADER_EXTENSIONS` to gate all subsequent logic.
- Implementation files are searched in sibling directories of `file_output_dir` following the `{stem}_{impl_ext}/` naming convention used by `resolve_file_output_dir`.
- `_IMPL_EXTENSIONS` is iterated in order; the first matching file is returned, so `cpp` takes priority over `c`, `cc`, `cxx`.

**Constraints & edge cases:**

- Returns `""` for any non-header extension.
- Returns `""` when no matching implementation file is found under any of the four extensions.
- Assumes the copied implementation file resides in the expected output directory structure.

---

### `_generate_section_with_fallback` *(async)*

```
async _generate_section_with_fallback(
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

**Responsibility:** Generates one section's content with up to three progressive fallback attempts when context window limits are exceeded.

**When to use:** Called once per section per file inside `_generate_file_doc`.

**Design decisions:**

| Attempt | Callee Context Used | Trigger |
|---|---|---|
| 1 | Full summary (`callee_context_summary`) | Always tried first |
| 2 | Compact summary (`callee_context_compact`) | On `ContextWindowExceededError` from attempt 1 |
| 3 | Empty string (no callee context) | On `ContextWindowExceededError` from attempt 2 |

- Each attempt rebuilds the prompt from scratch via `_build_section_prompt` with the appropriate context level.
- Non-`ContextWindowExceededError` exceptions from `llm_client.generate` propagate upward (not caught here).
- A `None` return from `llm_client.generate` (non-exception failure) is also treated as failure and causes the loop to exit without trying the next level — only `ContextWindowExceededError` triggers the fallback chain.

**Constraints & edge cases:**

- Returns `None` only when all three attempts either raise `ContextWindowExceededError` or return `None`.
- `implementation_context` is passed through unchanged to every attempt.

---

### `_generate_file_doc` *(async)*

```
async _generate_file_doc(
    file_rel: str,
    file_output_dir: str,
    doc_map: dict[str, dict],
    template: dict,
    llm_client: LLMClient,
) -> dict | None
```

**Responsibility:** Orchestrates full design document generation for a single file by reading its source and dependency data, generating each section sequentially, and generating a summary.

**When to use:** Called from `process_one` when a file requires (re)generation.

**Design decisions:**

- Sections are generated **sequentially** (one `await` per section) rather than in parallel, preserving prompt isolation and avoiding concurrent token budget contention on a single file.
- A file whose `section_list` remains empty after all section attempts is considered a complete failure and returns `None`.
- `_find_source_file` and `deps_file` existence are both checked before any LLM calls; early `None` returns prevent partial output.

**Return value structure:**

| Key | Type | Description |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `sections` | `list[dict]` | List of `{id, title, content}` dicts |
| `summary` | `str` | Generated summary, or `""` on failure |

**Constraints & edge cases:**

- Returns `None` if the source file copy or `file_dependencies.json` is missing.
- Returns `None` if no sections are successfully generated.
- A failed summary (returns `None`) is stored as `""` rather than causing the entire document to fail.

---

### `_generate_summary` *(async)*

```
async _generate_summary(
    file_path: str,
    section_list: list[dict],
    template: dict,
    llm_client: LLMClient,
) -> str | None
```

**Responsibility:** Issues a single LLM call to produce a summary of the complete design document from all generated sections.

**When to use:** Called once per file at the end of `_generate_file_doc`, after all sections have been successfully generated.

**Design decisions:**

- Uses a broad `except Exception` catch to prevent a summary failure from propagating and aborting document saving; the caller substitutes `""` on `None`.
- No fallback chain is implemented (unlike `_generate_section_with_fallback`); a single attempt is made.
- `SUMMARY_MAX_CHARS` is read from module-level configuration at call time.

**Constraints & edge cases:**

- Returns `None` on any exception, including `ContextWindowExceededError`.
- `template["summary_prompt"]` must exist; no defensive check is performed.

---

### `_find_source_file`

```
_find_source_file(output_dir: str, file_rel: str) -> str | None
```

**Responsibility:** Locates the copied source file within an output directory by reconstructing the expected filename from the relative path.

**When to use:** Called by `_generate_file_doc` to obtain the source code path before reading.

**Constraints & edge cases:**

- Only checks a single candidate path (`{output_dir}/{basename}`); does not search recursively.
- Returns `None` if the file does not exist at the expected location.

---

### `_save_doc`

```
_save_doc(doc: dict, output_dir: str) -> None
```

**Responsibility:** Persists a design document to disk in both Markdown (`doc.md`) and JSON (`doc.json`) formats, with Markdown written first so JSON always has a newer or equal modification time.

**When to use:** Called by `process_one` immediately after a document is successfully generated.

**Design decisions:**

- Markdown is written before JSON intentionally; `_sync_md_to_json` uses mtime comparison (`md > json`) to detect user edits, so the post-generation state must have `json.mtime >= md.mtime`.
- The Markdown file is assembled line-by-line into a `list[str]` and joined, avoiding repeated string concatenation.
- The `summary` field is appended as a `## Summary` section only when it is non-empty.
- JSON is written with `indent=2` and `ensure_ascii=False`.

**Constraints & edge cases:**

- `doc` must contain `file`, `sections` (list of `{title, content}`), and optionally `summary`.
- Overwrites any existing `doc.md` and `doc.json` without warning.

---

### `_parse_md_sections`

```
_parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]
```

**Responsibility:** Splits a Markdown document into named sections using known `## {title}` headings as delimiters, returning the trimmed content between consecutive known headings.

**When to use:** Called by `_sync_md_to_json` to extract current section content from a user-edited `doc.md`.

**Design decisions:**

- Only `## ` headings whose titles exactly match an entry in `section_titles` are treated as delimiters; other `##` headings in the content are preserved as body text.
- Uses a compiled regex with `re.MULTILINE` and `re.escape` per title to safely handle titles containing special characters.
- Section content boundaries are determined by consecutive regex match positions, not by line scanning.

**Constraints & edge cases:**

- Returns an empty dict if no known section headings are found in the text.
- Content is stripped of leading/trailing whitespace.
- Sections present in `md_text` but not in `section_titles` are ignored.

---

### `_sync_md_to_json`

```
_sync_md_to_json(output_dir: str) -> None
```

**Responsibility:** Propagates manual edits made to `doc.md` back into `doc.json` when the Markdown file is newer, then re-saves `doc.md` from the updated JSON to realign timestamps.

**When to use:** Called by `process_one` before reuse decisions when a file does not need regeneration, ensuring user edits are not lost.

**Design decisions:**

- Mtime comparison (`md.mtime > json.mtime`) gates all sync work; no action is taken if JSON is up-to-date.
- A section is updated in JSON only when **both** the section itself **and** the immediately following section (in JSON order) are found in the parsed Markdown; this guards against inaccurate boundary detection when intermediate headings are missing.
- The final section's "next title" is treated as `"Summary"` for boundary validation.
- After updating JSON, `_save_doc` is called to re-emit both files, which resets mtime so the sync does not re-trigger on the next run.
- Silent `return` on `json.JSONDecodeError` or `OSError` prevents a corrupt JSON from causing an exception.

**Constraints & edge cases:**

- No-ops when either `doc.json` or `doc.md` is absent.
- No-ops when parsed sections dict is empty.
- No-ops when no content actually changed (guards against unnecessary writes).

---

### `generate_all_docs` *(async)*

```
async generate_all_docs(
    base_output_dir: str,
    project_dep_list: list,
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> None
```

**Responsibility:** Top-level orchestrator that generates design documents for every file in a project, processing dependency levels in order and files within each level in parallel batches.

**When to use:** Called once by the pipeline (`pipeline.py`) when `ENABLE_LLM_DOC` is enabled.

**Design decisions:**

- Files are processed **level by level** (sequential across levels, parallel within a level) to ensure each file's dependency summaries are available in `doc_map` when it is processed.
- Within a level, files are batched into groups of `max_workers` and dispatched as concurrent `asyncio.Task` objects via `asyncio.gather`.
- `doc_map` accumulates successfully generated documents across all levels; it is passed by reference into each `_generate_file_doc` call.
- `regenerated_files` tracks files whose documents were newly produced in this run, so that callers of changed dependencies are also marked for regeneration even if those callers' own source files did not change.
- Exceptions from individual `process_one` tasks are caught via `return_exceptions=True` in `gather` and logged, preventing one file's failure from aborting the entire batch.

**Nested function — `_needs_regeneration(file_rel: str) -> bool`:**

| Condition | Result |
|---|---|
| `changed_files is None` | `True` (full regeneration mode) |
| `file_rel in changed_files` | `True` |
| Any callee in `changed_files` or `regenerated_files` | `True` |
| None of the above | `False` |

**Nested function — `_is_doc_complete(doc: dict) -> bool`:**
- Returns `False` if the set of section IDs in the doc does not exactly match the template's expected set, or if a `summary_prompt` is defined in the template but the doc has no summary.

**Nested coroutine — `process_one(file_rel: str) -> tuple[str, dict | None]` *(async)*:**
- Resolves the output directory, optionally syncs MD→JSON edits, attempts reuse of an existing complete `doc.json`, and falls back to full regeneration.
- Returns `(file_rel, doc)` where `doc` is `None` on complete failure.
- Adds `file_rel` to `regenerated_files` only when LLM generation actually occurs (not on reuse).

**Constraints & edge cases:**

- `changed_files=None` forces regeneration of all files regardless of existing output.
- A file whose output directory does not exist is skipped with a warning.
- An incomplete existing `doc.json` (wrong sections or missing summary) is treated as requiring regeneration even when `_needs_regeneration` returns `False`.
- Exceptions during individual file generation are logged but do not halt the run.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

**`doc_creator.py` → `codetwine/llm/__init__.py`**
Imports `ContextWindowExceededError` to catch context window overflow exceptions raised during LLM generation, enabling progressive fallback retry logic across section generation attempts.

**`doc_creator.py` → `codetwine/llm/client.py`**
Imports `LLMClient` to send assembled prompts to the configured LLM and receive generated text for each design document section and summary.

**`doc_creator.py` → `codetwine/utils/file_utils.py`**
Imports two functions:
- `output_path_to_rel` — to convert output-format paths (e.g. `project_name/copy_dest_path`) back to source-relative paths when displaying dependency symbols and building callee context summaries.
- `resolve_file_output_dir` — to resolve the absolute output directory path for a given file's relative path when locating source copies and dependency JSON files during document generation.

**`doc_creator.py` → `codetwine/config/settings.py`**
Imports four configuration constants:
- `MAX_WORKERS` — controls the degree of parallelism when processing files within each topological level.
- `DOC_TEMPLATE_PATH` — provides the filesystem path to the JSON template defining section structure and prompts.
- `OUTPUT_LANGUAGE` — specifies the natural language in which LLM output should be written, injected into section and summary prompts.
- `SUMMARY_MAX_CHARS` — sets the maximum character count constraint passed to the summary generation prompt.

---

### Dependents (modules that import this file)

**`codetwine/pipeline.py` → `doc_creator.py`**
Imports `generate_all_docs` as the entry point for the design document generation pipeline stage. The pipeline calls it after dependency analysis is complete, passing the base output directory, the full project dependency list, the LLM client instance, the configured worker count, and the set of changed files to enable incremental regeneration.

---

### Dependency Direction

All relationships are **unidirectional**:

- `doc_creator.py → codetwine/llm/__init__.py`: unidirectional — `doc_creator.py` consumes the exception class; `__init__.py` has no reference back.
- `doc_creator.py → codetwine/llm/client.py`: unidirectional — `doc_creator.py` invokes `LLMClient.generate()`; `client.py` has no reference back.
- `doc_creator.py → codetwine/utils/file_utils.py`: unidirectional — `doc_creator.py` calls utility functions; `file_utils.py` has no reference back.
- `doc_creator.py → codetwine/config/settings.py`: unidirectional — `doc_creator.py` reads configuration constants; `settings.py` has no reference back.
- `codetwine/pipeline.py → doc_creator.py`: unidirectional — `pipeline.py` calls `generate_all_docs`; `doc_creator.py` has no reference back to the pipeline.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `base_output_dir` | Caller (`pipeline.py`) | `str` — filesystem path to the root output directory |
| `project_dep_list` | Caller (`pipeline.py`) | `list[dict]` — output of `save_project_dependencies` |
| `llm_client` | Caller | `LLMClient` instance |
| `max_workers` | Config / caller | `int` — parallelism limit per level batch |
| `changed_files` | Caller | `set[str] \| None` — relative paths of changed files |
| `DOC_TEMPLATE_PATH` | Config (`settings.py`) | JSON file path; loaded into `dict` with `sections` and `summary_prompt` |
| `file_dependencies.json` | Per-file output directory | JSON file — `{file, callee_usages, caller_usages, definitions}` |
| Source file copy | Per-file output directory | Plain text — the copied source code of the target file |
| `doc.json` / `doc.md` | Per-file output directory | JSON / Markdown — pre-existing design documents (for reuse or MD→JSON sync) |
| `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `MAX_WORKERS` | `settings.py` | Scalar config values |

---

## 2. Transformation Pipeline

### Stage 1 — Initialization
`generate_all_docs` loads the JSON template from `DOC_TEMPLATE_PATH` and builds two auxiliary maps from `project_dep_list`:
- `level_list`: files sorted topologically into dependency-depth levels via `_topological_sort_by_level`. Level 0 contains files with no dependencies; level N contains files whose dependencies all reside at levels < N.
- `file_callees`: `dict[str, set[str]]` mapping each file to its set of callee paths, used for incremental regeneration checks.

### Stage 2 — Level-by-Level Fan-Out
For each level in `level_list`, files are batched into groups of at most `max_workers`. Each batch is dispatched as a set of concurrent `asyncio.Task` objects via `asyncio.gather`. Within a batch, all files are processed in parallel; the next batch in the same level, and the next level entirely, wait for the current batch to complete. This ensures that when a file is processed, all its dependencies already have entries in `doc_map`.

### Stage 3 — Per-File Regeneration Decision (`_needs_regeneration`)
Before generating, each file is evaluated:
1. If `changed_files is None` → always regenerate.
2. If the file itself is in `changed_files` → regenerate.
3. If any callee appears in `changed_files` or `regenerated_files` → regenerate.
4. Otherwise → attempt reuse of `doc.json` (after MD→JSON sync).

If reuse is chosen, `_sync_md_to_json` is called first to propagate any manual edits from `doc.md` into `doc.json` (triggered only when `doc.md` mtime > `doc.json` mtime).

### Stage 4 — Per-File Document Generation (`_generate_file_doc`)
For a file that needs regeneration:
1. **Source code** is read from the copied file inside `file_output_dir`.
2. **`file_dependencies.json`** is read to obtain `callee_usages` and `caller_usages`.
3. **Callee context** is built by `_build_callee_context_summary`: summaries from `doc_map` for all dependency files are concatenated into two variants — full (`callee_context_summary`) and compact (first 100 chars each, `callee_context_compact`).
4. **Implementation context** is built by `_build_implementation_context`: for C/C++ header files, the corresponding `.cpp`/`.c` source is read from a sibling directory.
5. **Section generation**: for each section in the template, `_generate_section_with_fallback` assembles a prompt via `_build_section_prompt` and calls `llm_client.generate`. On `ContextWindowExceededError`, three attempts are made in order: full callee context → compact callee context → no callee context.
6. **Summary generation**: once all sections are collected, `_generate_summary` assembles a prompt via `_build_summary_prompt` containing all section contents and calls `llm_client.generate`.

### Stage 5 — Prompt Assembly
`_build_section_prompt` concatenates the following blocks into a single string prompt:
- Target file header + source code block
- (Optional) Implementation file source code block
- Callee usages list with embedded dependency source code
- Caller usages list with embedded usage context source code
- Callee design document summaries (`callee_context`)
- Section-specific instruction + output language + factual accuracy constraint

`_build_summary_prompt` concatenates all generated section headings and contents, followed by the summary instruction and character limit.

### Stage 6 — Output Persistence and `doc_map` Update
The returned `doc` dict is:
- Written to `doc.md` (Markdown) and `doc.json` (JSON) via `_save_doc`.
- Stored in `doc_map[file_rel]` so subsequent levels can reference its `summary`.
- Its relative path is added to `regenerated_files`.

---

## 3. Outputs

| Output | Destination | Format |
|---|---|---|
| `doc.md` | `{file_output_dir}/doc.md` | Markdown — one `##` heading per section, plus a `## Summary` block |
| `doc.json` | `{file_output_dir}/doc.json` | JSON — `{file, sections:[{id, title, content}], summary}` |
| `doc_map` | In-memory, consumed by subsequent levels | `dict[str, dict]` — accumulated design documents keyed by file relative path |
| Console / log output | stdout + logger | Progress messages (`REUSE`, `OK`, `SKIP`, `INCOMPLETE`, level counts) |

---

## 4. Key Data Structures

### `project_dep_list` element
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the file |
| `callees` | `list[str]` | Relative paths of files this file depends on |
| `callers` | `list[str]` | Relative paths of files that depend on this file |

### `file_deps` (contents of `file_dependencies.json`)
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the target file |
| `callee_usages` | `list[dict]` | External symbols this file uses (see below) |
| `caller_usages` | `list[dict]` | External files using this file's symbols (see below) |

### `callee_usages` element
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name being used |
| `from` | `str` | Output-format path of the file where the symbol is defined |
| `target_context` | `str \| None` | Full source code of the dependency file |

### `caller_usages` element
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name being used by the caller |
| `file` | `str` | Output-format path of the file that uses the symbol |
| `usage_context` | `str \| None` | Source code excerpt at the usage location |

### `doc` (design document dict)
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `sections` | `list[dict]` | Ordered list of generated section dicts |
| `summary` | `str` | LLM-generated summary of the entire document |

### `sections` element
| Field / Key | Type | Purpose |
|---|---|---|
| `id` | `str` | Section identifier from the template |
| `title` | `str` | Section display title |
| `content` | `str` | LLM-generated text for this section |

### `doc_map`
| Field / Key | Type | Purpose |
|---|---|---|
| `{file_rel}` | `dict` | Maps each processed file's relative path to its full `doc` dict |

### `level_list`
| Field / Key | Type | Purpose |
|---|---|---|
| Outer index | `int` | Dependency depth level (0 = no dependencies) |
| Inner element | `str` | Relative file path assigned to that level |

### `file_callees`
| Field / Key | Type | Purpose |
|---|---|---|
| `{file_rel}` | `set[str]` | Set of callee relative paths for the keyed file, used in regeneration checks |

## Error Handling

## Error Handling

### 1. Overall Strategy

The file adopts a **graceful degradation with logging-and-continue** strategy. No single file failure terminates the overall document generation pipeline. Errors at the section level trigger a structured retry-with-fallback sequence before a section is skipped; errors at the file level are logged and the file is omitted from the output while processing continues for remaining files. This ensures maximum coverage across a project even when individual LLM calls or file I/O operations fail.

---

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ContextWindowExceededError` | Prompt exceeds the LLM's context window during section generation | Retried up to 3 times with progressively smaller prompts: full callee summary → compact callee summary (100-char truncation) → no callee context | Yes | Section may be generated with reduced context; no context-window failure logged if final attempt succeeds |
| `ContextWindowExceededError` (all attempts exhausted) | All three fallback attempts for a section fail due to context overflow | Section is skipped; warning logged | Yes (section skipped) | That section is absent from the output document; other sections continue |
| LLM returns `None` | `LLMClient.generate` returns `None` (e.g. rate-limit retries exhausted) | Section result treated as `None`; warning logged; section skipped | Yes (section skipped) | Same as above |
| All sections fail to generate | Every section in the template returns `None` for a given file | Error logged; `_generate_file_doc` returns `None`; file printed as `SKIP` | Yes (file skipped) | File has no output document; excluded from `doc_map` |
| Summary generation failure | Any exception raised by `LLMClient.generate` during summary step | Warning logged; summary set to empty string `""` | Yes | Document saved without a summary field |
| Source file not found | No file matching the target's basename exists in `output_dir` | Warning logged; `_generate_file_doc` returns `None` | Yes (file skipped) | File skipped entirely |
| `file_dependencies.json` missing | JSON file absent from expected output directory | Warning logged; `_generate_file_doc` returns `None` | Yes (file skipped) | File skipped entirely |
| `doc.json` read failure | `JSONDecodeError` or `OSError` when loading existing doc during reuse check | Exception silently caught; falls through to full regeneration | Yes | File regenerated instead of reused |
| `doc.md` / `doc.json` sync failure | Either file missing, or MD timestamp ≤ JSON timestamp, or malformed JSON in `doc.json` | Early return or silent catch; sync skipped | Yes | Manual MD edits may not be reflected in JSON |
| Task-level exception during `asyncio.gather` | Unhandled exception propagates from `process_one` coroutine | Caught via `return_exceptions=True`; error logged; result skipped | Yes (file skipped) | File excluded from `doc_map`; processing continues |
| Output directory missing | `resolve_file_output_dir` returns a path that does not exist as a directory | Warning logged; `process_one` returns `(file_rel, None)` | Yes (file skipped) | File excluded from `doc_map` |

---

### 3. Design Notes

- **Fallback is ordered by context size, not by quality.** The three-attempt sequence in `_generate_section_with_fallback` is designed to preserve as much dependency context as possible while satisfying the LLM's token budget. The most informative prompt is always tried first; context is stripped only as a last resort.
- **Section-level granularity prevents total loss.** Because each section is generated and stored independently, a failure in one section does not block generation of others. The document is saved with whatever sections succeeded.
- **`asyncio.gather` with `return_exceptions=True`** ensures that an unhandled exception in one concurrent `process_one` task does not cancel sibling tasks within the same batch.
- **Silent catch on reuse-path I/O errors** is intentional: a corrupt or unreadable `doc.json` is treated as a cache miss and triggers regeneration rather than halting the pipeline.
- **Summary failure is non-fatal by design.** The summary is consumed downstream as callee context for dependent files; an empty summary degrades that context but does not break the pipeline.

## Summary

Generates LLM-based design documents for all project source files. Public entry point: `generate_all_docs(base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int, changed_files: set[str]|None) -> None`. Consumes `project_dep_list` (`list[dict]` with `file`, `callees`, `callers`), `file_dependencies.json` (`callee_usages`, `caller_usages`), and a JSON template. Produces `doc.md`/`doc.json` per file and an in-memory `doc_map: dict[str, dict]` keyed by file path, each value containing `file`, `sections: list[{id, title, content}]`, and `summary`.
