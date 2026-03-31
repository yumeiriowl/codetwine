# Design Document: codetwine/doc_creator.py

## Overview & Purpose

### 1. Module Summary

Generates structured LLM-based design documents for every source file in a project by assembling context-rich prompts from source code, dependency graphs, and prior document summaries, then saving the results in both Markdown and JSON formats.

### 2. When to Use This Module

- **Generating design documents for an entire project**: Call `generate_all_docs(base_output_dir, project_dep_list, llm_client)` from `codetwine/pipeline.py` after dependency analysis is complete. It returns `None` and writes `doc.md` and `doc.json` into each file's output directory.
- **Incremental regeneration after source changes**: Pass a `changed_files: set[str]` to `generate_all_docs(...)` to skip unchanged files and reuse their existing `doc.json`, while still regenerating any file whose dependencies were updated.

### 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Entry point: topologically sorts all project files, generates design documents level by level in parallel, and writes results to disk |

### 4. Design Decisions

- **Topological level-based parallelism**: Files are sorted into dependency depth levels so that each file's dependencies are fully documented before it is processed. Within a level, files are processed concurrently in batches of `max_workers`. This ensures callee summaries are always available as context when a caller's document is generated.

- **Progressive context fallback on context-window overflow**: When the LLM rejects a prompt due to token limits (`ContextWindowExceededError`), the prompt is retried up to three times with progressively reduced callee context: full summaries → summaries truncated to 100 characters each → no callee context. This avoids hard failures for files with many or verbose dependencies.

- **MD-to-JSON sync for manual edits**: Before reusing an existing `doc.json`, the module checks whether `doc.md` has a newer modification timestamp. If so, it parses the Markdown back into sections using known section titles as delimiters and writes the edits into `doc.json`, preserving manual corrections across incremental runs.

- **Incremental regeneration propagation**: A `regenerated_files` set tracks which files were regenerated in the current run. A file is marked for regeneration not only if it appears in `changed_files`, but also if any of its callees appear in `changed_files` or `regenerated_files`, ensuring that downstream documents remain consistent with updated dependencies.

- **Header file implementation context**: For C/C++ header files (`.h`, `.hpp`, `.hh`, `.hxx`), the module searches the output directory for a corresponding implementation file (`.cpp`, `.c`, etc.) and injects its source code into the prompt, giving the LLM visibility into how declared interfaces are actually implemented.

## Definition Design Specifications

---

## Module-Level Constants

| Constant | Type | Purpose |
|---|---|---|
| `HEADER_TARGET_FILE` | `str` | Prompt heading template identifying the target file |
| `HEADER_SOURCE_CODE` | `str` | Prompt section heading for source code block |
| `HEADER_CALLEE_USAGES` | `str` | Prompt section heading for dependencies (callee symbols) |
| `CALLEE_USAGES_SCHEMA_NOTE` | `str` | Schema description injected before each callee usage list |
| `CALLEE_SOURCE_CODE_LABEL` | `str` | Label prefix for inline dependency source code in the prompt |
| `HEADER_CALLER_USAGES` | `str` | Prompt section heading for dependents (caller files) |
| `CALLER_USAGES_SCHEMA_NOTE` | `str` | Schema description injected before each caller usage list |
| `CALLER_SOURCE_CODE_LABEL` | `str` | Label prefix for inline caller source code in the prompt |
| `HEADER_CALLEE_CONTEXT` | `str` | Prompt section heading for design document summaries of dependencies |
| `CALLEE_CONTEXT_NOTE` | `str` | Explanatory note preceding callee design document summaries |
| `HEADER_REQUEST` | `str` | Prompt section heading for the LLM instruction block |
| `SECTION_REQUEST_TEMPLATE` | `str` | Template for per-section LLM instruction; `{title}` is substituted at call time |
| `OUTPUT_LANGUAGE_INSTRUCTION` | `str` | Language instruction appended to every prompt; `{language}` is substituted |
| `FACTUAL_ACCURACY_INSTRUCTION` | `str` | Warning instruction appended to every section prompt prohibiting speculation |
| `HEADER_IMPL_CONTEXT` | `str` | Prompt section heading for corresponding C/C++ implementation file |
| `IMPL_CONTEXT_NOTE` | `str` | Explanatory note preceding implementation file source code |
| `HEADER_DOC_CONTENT` | `str` | Prompt section heading for full design document content (summary prompts) |
| `SUMMARY_CHAR_LIMIT` | `str` | Character limit instruction template for summary prompts; `{max_chars}` is substituted |
| `_HEADER_EXTENSIONS` | `set[str]` | C/C++ header file extensions: `.h`, `.hpp`, `.hh`, `.hxx` |
| `_IMPL_EXTENSIONS` | `list[str]` | Implementation file extensions paired with header files: `cpp`, `c`, `cc`, `cxx` |

---

## `_topological_sort_by_level`

**Signature:**
```
_topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]
```

**Responsibility:** Determines a processing order for all project files such that a file's dependencies are always processed before the file itself, grouping files at the same dependency depth into the same level for parallel processing.

**When to use:** Called once at the start of `generate_all_docs` to establish the batch order for document generation.

**Design decisions:**
- Implements Kahn's algorithm on a **reverse graph** (edges point from callee to caller) so that files with no dependencies naturally form level 0.
- Files not reached by Kahn's algorithm (due to circular dependencies) are appended as a final level with a logged warning rather than causing a hard failure.
- Within each level, files are sorted alphabetically to ensure deterministic output order.

**Constraints & edge cases:**
- `project_dep_list` elements must have at least a `"file"` key; `"callees"` is optional and defaults to an empty list.
- Files appearing only as callees (not as top-level entries) are still included via `all_files`.
- Circular dependency groups are processed last but are not skipped.

---

## `_build_section_prompt`

**Signature:**
```
_build_section_prompt(
    section: dict,
    source_code: str,
    file_deps: dict,
    callee_context: str,
    implementation_context: str = "",
) -> str
```

**Responsibility:** Assembles a complete LLM prompt string for generating one design document section by combining source code, dependency information, optional implementation context, and section-specific instructions.

**When to use:** Called by `_generate_section_with_fallback` for each attempt (with varying `callee_context` values).

**Design decisions:**
- `callee_context` is a variable input across retry attempts; passing an empty string effectively removes the callee summary block.
- Implementation context (for C/C++ header files) is included as a separate block only when non-empty, placed immediately after the source code block.
- `OUTPUT_LANGUAGE_INSTRUCTION` and `FACTUAL_ACCURACY_INSTRUCTION` are unconditionally appended to every prompt.

**Constraints & edge cases:**
- `file_deps` must contain `"callee_usages"` and `"caller_usages"` keys (may be empty lists).
- Each usage entry in `callee_usages` may optionally include `"target_context"`; each entry in `caller_usages` may optionally include `"usage_context"`. Both are omitted from the prompt when absent.
- `output_path_to_rel` is applied to dependency paths before rendering them in the prompt.

---

## `_build_summary_prompt`

**Signature:**
```
_build_summary_prompt(
    file_path: str,
    section_contents: list[dict],
    summary_prompt: str,
    summary_max_chars: int,
) -> str
```

**Responsibility:** Assembles the LLM prompt for generating a short summary of the entire design document from its already-generated sections.

**When to use:** Called once per file by `_generate_summary` after all sections have been generated.

**Constraints & edge cases:**
- `section_contents` elements must have `"title"` and `"content"` keys.
- The character limit instruction is appended as informational text; the LLM is not technically constrained to it.

---

## `_build_callee_context_summary`

**Signature:**
```
_build_callee_context_summary(
    file_deps: dict,
    doc_map: dict[str, dict],
    compact: bool = False,
) -> str
```

- `doc_map` is a mapping from source-relative file path to a design document dict containing at minimum a `"summary"` key.

**Responsibility:** Extracts summary text from previously generated design documents of the target file's dependencies and concatenates them into a single context string for inclusion in LLM prompts.

**When to use:** Called in `_generate_file_doc` to build both the full and compact callee context strings before section generation begins.

**Design decisions:**
- Dependency files are deduplicated from `callee_usages` using a set before iteration.
- `compact=True` truncates each summary to 100 characters (appending `"..."` if truncated), used as a fallback when the full context exceeds the context window.
- `output_path_to_rel` is applied to convert `callee_usages`' `"from"` paths to the format used as keys in `doc_map`.

**Constraints & edge cases:**
- Dependencies whose files have no entry in `doc_map` (not yet generated) are silently skipped.
- Dependencies with an empty `"summary"` are silently skipped.

---

## `_build_implementation_context`

**Signature:**
```
_build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str
```

**Responsibility:** Locates and returns the source code of the C/C++ implementation file corresponding to a header file, to be included as supplementary context in LLM prompts.

**When to use:** Called in `_generate_file_doc` before section generation; the result is passed to `_generate_section_with_fallback`.

**Design decisions:**
- Returns an empty string immediately for non-header files (extension not in `_HEADER_EXTENSIONS`), making it safe to call unconditionally.
- Searches for implementation files by looking for sibling directories named `{stem}_{impl_ext}` under the parent of `file_output_dir`, following the output directory naming convention of `resolve_file_output_dir`.
- Iterates `_IMPL_EXTENSIONS` in order; returns the first match found.

**Constraints & edge cases:**
- Only reads the first matching implementation file; multiple implementations for one header are not supported.
- Returns empty string if no implementation file is found.

---

## `_generate_section_with_fallback` *(async)*

**Signature:**
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

**Responsibility:** Generates a single design document section via the LLM with a three-tier progressive fallback strategy to handle context window overflow.

**When to use:** Called once per template section inside `_generate_file_doc`.

**Concurrency:** This is an `async` function. Each attempt `await`s `llm_client.generate()`; attempts are sequential, not parallel (the next is only tried on `ContextWindowExceededError`).

**Design decisions:**

| Attempt | Callee context used | Trigger |
|---|---|---|
| 1 | Full summary (`callee_context_summary`) | Initial attempt |
| 2 | Compact summary (`callee_context_compact`) | `ContextWindowExceededError` on attempt 1 |
| 3 | Empty string (no callee context) | `ContextWindowExceededError` on attempt 2 |

- Non-context-window exceptions from `llm_client.generate()` are not caught here; only `ContextWindowExceededError` triggers fallback.
- A `None` result from `llm_client.generate()` (LLM call failed without exception) terminates the loop for that attempt and continues to the next.

**Constraints & edge cases:**
- Returns `None` if all three attempts fail or return `None`.

---

## `_generate_file_doc` *(async)*

**Signature:**
```
async _generate_file_doc(
    file_rel: str,
    file_output_dir: str,
    doc_map: dict[str, dict],
    template: dict,
    llm_client: LLMClient,
) -> dict | None
```

- Return type `dict` has shape `{file: str, sections: list[dict], summary: str}`.

**Responsibility:** Orchestrates complete design document generation for a single file by loading its source and dependency data, generating all template sections sequentially, and producing a final summary.

**When to use:** Called by `process_one` inside `generate_all_docs` when a file requires (re)generation.

**Concurrency:** `async`; awaits `_generate_section_with_fallback` for each section sequentially, then awaits `_generate_summary`.

**Design decisions:**
- Returns `None` (rather than raising) if the source file or `file_dependencies.json` cannot be found.
- Returns `None` if no sections were successfully generated.
- Sections that individually fail are skipped with a warning; partial documents (some sections missing) are still returned.
- Summary generation failure (`_generate_summary` returns `None`) results in an empty string `""` in the returned dict rather than a hard failure.

**Constraints & edge cases:**
- `doc_map` must already contain entries for all dependency files that should contribute callee context; it is not modified by this function.
- The source file is located via `_find_source_file`; returns `None` if not found.

---

## `_generate_summary` *(async)*

**Signature:**
```
async _generate_summary(
    file_path: str,
    section_list: list[dict],
    template: dict,
    llm_client: LLMClient,
) -> str | None
```

**Responsibility:** Generates a short summary of the complete design document by sending all section contents to the LLM in a single prompt.

**When to use:** Called once at the end of `_generate_file_doc` after all sections have been generated.

**Concurrency:** `async`; a single `await` on `llm_client.generate()`.

**Constraints & edge cases:**
- All exceptions from `llm_client.generate()` are caught; returns `None` on any failure.
- `summary_max_chars` is sourced from the `SUMMARY_MAX_CHARS` setting.
- Does not implement fallback on `ContextWindowExceededError`; the full section content is always sent.

---

## `_find_source_file`

**Signature:**
```
_find_source_file(output_dir: str, file_rel: str) -> str | None
```

**Responsibility:** Locates the copied source file within an output directory by constructing its expected path from the original filename.

**When to use:** Called by `_generate_file_doc` to resolve the readable source file path before loading its contents.

**Constraints & edge cases:**
- Returns `None` if the expected file does not exist; caller is responsible for handling `None`.

---

## `_save_doc`

**Signature:**
```
_save_doc(doc: dict, output_dir: str) -> None
```

- `doc` has shape `{file: str, sections: list[dict], summary: str}`.

**Responsibility:** Persists a generated design document to disk in both Markdown (`doc.md`) and JSON (`doc.json`) formats.

**When to use:** Called by `process_one` immediately after a successful `_generate_file_doc` call.

**Design decisions:**
- Markdown is written first; JSON is written second so that `doc.json` always has a timestamp equal to or newer than `doc.md`, which is the signal used by `_sync_md_to_json` to determine whether a sync is needed.
- Before writing, any duplicate section title heading that the LLM may have prepended to section content is stripped using a regex on each section's `"content"` field. This mutation is applied in-place on the `doc` dict.
- The summary is appended as a `## Summary` section in the Markdown only when it is non-empty.

---

## `_parse_md_sections`

**Signature:**
```
_parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]
```

**Responsibility:** Parses a `doc.md` file into a mapping of section title to section content, using known section titles as delimiters while treating `##` headings in LLM content as part of section bodies unless they exactly match a known title.

**When to use:** Called by `_sync_md_to_json` to extract editable section content from a manually modified Markdown file.

**Design decisions:**
- Matches only `## {known_title}` lines (exact match, multiline mode); arbitrary `##` headings inside section content are not treated as delimiters.
- Uses `re.escape` on all section titles to prevent regex injection from unusual title characters.

**Constraints & edge cases:**
- Sections not present in `md_text` are omitted from the returned dict rather than included with empty content.
- `section_titles` should include `"Summary"` as the final entry to capture the summary block.

---

## `_sync_md_to_json`

**Signature:**
```
_sync_md_to_json(output_dir: str) -> None
```

**Responsibility:** Propagates manual edits made to `doc.md` back into `doc.json` when the Markdown file has a newer modification timestamp, keeping both formats consistent.

**When to use:** Called by `process_one` before reusing an existing `doc.json`, ensuring user edits in Markdown are not silently discarded.

**Design decisions:**
- Timestamp guard (`mtime(md) > mtime(json)`) prevents unnecessary parsing and write operations when no edits have been made.
- A section's content is only updated if the **next** section (in JSON order) is also present in the parsed Markdown. This prevents incorrect boundary detection from partially overwriting content.
- After updating `doc.json`, `_save_doc` is called to re-render `doc.md` from the updated JSON, normalizing formatting and ensuring `mtime(json) >= mtime(md)`.
- `json.JSONDecodeError` and `OSError` during JSON loading are silently caught; the function returns without making changes.

**Constraints & edge cases:**
- No-ops if either `doc.json` or `doc.md` does not exist.
- No-ops if the parsed Markdown yields no recognized sections.
- Does not sync if no content differences are detected (`changed` remains `False`).

---

## `generate_all_docs` *(async)*

**Signature:**
```
async generate_all_docs(
    base_output_dir: str,
    project_dep_list: list,
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> None
```

- `changed_files`: a set of project-relative file paths, or `None` to process all files.

**Responsibility:** Entry point for the document generation pipeline; processes all project files in dependency order, parallelizing within each level while ensuring dependency documents are available for context in subsequent levels.

**When to use:** Called from `codetwine/pipeline.py` when `ENABLE_LLM_DOC` is active, after dependency analysis is complete.

**Concurrency:** `async`; within each level, files are batched into groups of `max_workers` and each batch is dispatched as concurrent `asyncio.Task`s via `asyncio.gather`. Levels are processed sequentially.

**Design decisions:**
- `doc_map` is accumulated across all levels; each completed document is immediately added so that files in later levels can reference earlier documents as callee context.
- `regenerated_files` tracks which files were actually regenerated in the current run. A file that was not changed itself but has a regenerated dependency is still marked for regeneration, propagating changes through the dependency graph.
- Exceptions from individual `process_one` tasks are caught via `return_exceptions=True` in `asyncio.gather` and logged rather than halting the entire pipeline.

**Inner functions:**

| Name | Signature | Responsibility |
|---|---|---|
| `_needs_regeneration` | `(file_rel: str) -> bool` | Determines if a file must be regenerated based on `changed_files`, `file_callees`, and `regenerated_files`. Returns `True` when `changed_files is None` (full regeneration). |
| `_is_doc_complete` | `(doc: dict) -> bool` | Validates that an existing `doc.json` contains exactly the sections defined in the template and a non-empty summary (when `"summary_prompt"` is present). |
| `process_one` | `async (file_rel: str) -> tuple[str, dict \| None]` | Handles the full lifecycle for one file: directory check, reuse decision, sync, generation, and saving. Returns `(file_rel, doc)`. |

**Constraints & edge cases:**
- Files whose output directory does not exist are skipped with a warning.
- If `doc.json` cannot be read (corrupt or missing), `process_one` falls through to regeneration.
- Files for which `_generate_file_doc` returns `None` are logged as `SKIP` and contribute `None` to `doc_map` (they are not inserted).

## Dependency Description

### Dependencies (modules this file imports)

**`doc_creator.py` → `codetwine/utils/file_utils.py`**
- Symbols: `output_path_to_rel`, `resolve_file_output_dir`
- Purpose: `output_path_to_rel` converts output-format paths back to project-relative source paths (used when labeling callee/caller usages in prompts and building callee context summaries). `resolve_file_output_dir` resolves the absolute output directory path for a given file's relative path (used to locate source copies, `file_dependencies.json`, and doc output targets).

**`doc_creator.py` → `codetwine/llm/client.py`**
- Symbol: `LLMClient`
- Purpose: Provides the async LLM API wrapper used to generate section content and summaries for each design document via `LLMClient.generate()`.

**`doc_creator.py` → `codetwine/llm/__init__.py`**
- Symbol: `ContextWindowExceededError`
- Purpose: Caught during section generation to trigger progressive fallback logic (retrying with compressed or omitted callee context when the prompt exceeds the LLM's context window).

**`doc_creator.py` → `codetwine/config/settings.py`**
- Symbols: `MAX_WORKERS`, `DOC_TEMPLATE_PATH`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`
- Purpose: Supplies runtime configuration constants — `MAX_WORKERS` controls per-level parallelism, `DOC_TEMPLATE_PATH` locates the section template JSON, `OUTPUT_LANGUAGE` is injected into prompts to specify the output language, and `SUMMARY_MAX_CHARS` sets the character limit for generated summaries.

---

### Dependents (modules that import this file)

**`codetwine/pipeline.py` → `doc_creator.py`**
- Symbol: `generate_all_docs`
- Purpose: The pipeline calls `generate_all_docs` as the top-level entry point to drive the entire design document generation process, passing it the base output directory, the project dependency list, the LLM client instance, the worker count, and the set of changed files.

---

### Dependency Direction

All relationships are **unidirectional**:

- `doc_creator.py` → `codetwine/utils/file_utils.py` — `doc_creator.py` consumes path utilities; `file_utils.py` has no knowledge of `doc_creator.py`.
- `doc_creator.py` → `codetwine/llm/client.py` — `doc_creator.py` drives LLM calls; `LLMClient` has no knowledge of `doc_creator.py`.
- `doc_creator.py` → `codetwine/llm/__init__.py` — `doc_creator.py` catches the re-exported exception; the `llm` package has no knowledge of `doc_creator.py`.
- `doc_creator.py` → `codetwine/config/settings.py` — `doc_creator.py` reads configuration constants; `settings.py` has no knowledge of `doc_creator.py`.
- `codetwine/pipeline.py` → `doc_creator.py` — the pipeline invokes `generate_all_docs`; `doc_creator.py` has no knowledge of `pipeline.py`.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `base_output_dir` | Caller (`pipeline.py`) | `str` — filesystem path to the root output directory |
| `project_dep_list` | Caller (`pipeline.py`) | `list[dict]` — each entry: `{file, callers, callees}` |
| `llm_client` | Caller (`pipeline.py`) | `LLMClient` instance |
| `max_workers` | Config (`MAX_WORKERS`) / caller | `int` — parallelism limit per level |
| `changed_files` | Caller (`pipeline.py`) | `set[str] \| None` — relative paths of changed files |
| `DOC_TEMPLATE_PATH` | Config / filesystem | JSON file containing `{sections, summary_prompt}` |
| Per-file source code | Filesystem (`output_dir/<filename>`) | Raw text |
| `file_dependencies.json` | Filesystem (per-file output dir) | JSON: `{file, callee_usages, caller_usages, definitions}` |
| `doc.json` / `doc.md` | Filesystem (per-file output dir) | Existing design documents (for reuse or MD→JSON sync) |
| `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS` | Config | `str`, `int` |

---

## 2. Transformation Overview

```
project_dep_list
       │
       ▼
_topological_sort_by_level()
       │  Produces level_list: list[list[str]]
       │  (Level 0 = no-dependency leaf files; Level N = files depending on N-1)
       ▼
For each level (sequential), batches of max_workers (parallel):
       │
       ├─► _needs_regeneration(file_rel)
       │         │ false → _sync_md_to_json() + reuse existing doc.json
       │         │ true  ↓
       │
       ├─► _generate_file_doc(file_rel, output_dir, doc_map, template, llm_client)
       │         │
       │         ├─ Read source code (filesystem)
       │         ├─ Read file_dependencies.json (filesystem)
       │         │
       │         ├─ _build_callee_context_summary(file_deps, doc_map)
       │         │       Extracts summaries from doc_map for callee files → callee_context_summary (str)
       │         │       Also produces callee_context_compact (truncated to 100 chars each)
       │         │
       │         ├─ _build_implementation_context(file_rel, file_output_dir)
       │         │       For .h/.hpp headers: reads paired .cpp/.c source → implementation_context (str)
       │         │
       │         └─ For each section in template["sections"]:
       │               _generate_section_with_fallback(...)
       │                     │
       │                     ├─ Attempt 1: _build_section_prompt(..., callee_context_summary)
       │                     ├─ Attempt 2: _build_section_prompt(..., callee_context_compact)  [on ContextWindowExceededError]
       │                     └─ Attempt 3: _build_section_prompt(..., "")                      [on ContextWindowExceededError]
       │                           │
       │                           └─ llm_client.generate(prompt) → section content (str)
       │         │
       │         └─ _generate_summary(file_rel, section_list, template, llm_client)
       │                 _build_summary_prompt(...) → llm_client.generate(prompt) → summary (str)
       │
       ├─► _save_doc(doc, output_dir)
       │         Writes doc.md + doc.json
       │
       └─► doc_map[file_rel] = doc
               (accumulated across levels; used as callee context for higher levels)
```

The key sequential constraint is that each level waits for all prior levels to complete before starting, because `doc_map` must contain summaries of all dependencies before a file's prompt is assembled.

Within a level, files are processed in concurrent batches (`asyncio.gather`) up to `max_workers` at a time.

---

## 3. Outputs

| Output | Destination | Format |
|---|---|---|
| `doc.md` | `{output_dir}/doc.md` | Markdown: title heading, one `##` section per template section, optional `## Summary` |
| `doc.json` | `{output_dir}/doc.json` | JSON: `{file, sections: [{id, title, content}], summary}` |
| `doc_map` (in-memory) | Passed as context to subsequent levels | `dict[str, dict]` — accumulated design document dicts keyed by relative file path |
| Console / log output | stdout + logger | Progress messages: level counts, `REUSE`, `OK`, `SKIP`, `INCOMPLETE` per file |

---

## 4. Key Data Structures

### `project_dep_list` element
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `callers` | `list` | Files that use symbols from this file |
| `callees` | `list[str]` | Files whose symbols this file uses (dependency paths) |

### `template` (loaded from `DOC_TEMPLATE_PATH`)
| Field / Key | Type | Purpose |
|---|---|---|
| `sections` | `list[dict]` | Ordered list of section definitions |
| `sections[].id` | `str` | Unique section identifier |
| `sections[].title` | `str` | Section heading text |
| `sections[].prompt` | `str` | LLM instruction for this section |
| `summary_prompt` | `str` | LLM instruction for the overall summary |

### `file_deps` (from `file_dependencies.json`)
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of this file |
| `callee_usages` | `list[dict]` | Symbols this file consumes from external files |
| `callee_usages[].name` | `str` | Symbol name |
| `callee_usages[].from` | `str` | Output-format path of the file defining the symbol |
| `callee_usages[].target_context` | `str \| None` | Full source of the dependency file |
| `caller_usages` | `list[dict]` | External files consuming symbols from this file |
| `caller_usages[].name` | `str` | Symbol name |
| `caller_usages[].file` | `str` | Path of the file using the symbol |
| `caller_usages[].usage_context` | `str \| None` | Source snippet of the usage location |

### `doc` / `doc_map` value
| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the documented file |
| `sections` | `list[dict]` | Generated section list (id, title, content) |
| `sections[].id` | `str` | Section identifier matching template |
| `sections[].title` | `str` | Section heading |
| `sections[].content` | `str` | LLM-generated section text |
| `summary` | `str` | LLM-generated short summary (≤ `SUMMARY_MAX_CHARS` chars) |

### `level_list`
| Field / Key | Type | Purpose |
|---|---|---|
| Outer list index | `int` | Level number (0 = no dependencies) |
| Inner `list[str]` | `list[str]` | Relative paths of files at this dependency depth |

## Error Handling

### 1. Overall Strategy

The file employs a **graceful degradation with logging-and-continue** strategy. No error causes the entire generation pipeline to terminate. Instead, failures at each granularity level (individual LLM call, section, file) are caught, logged, and skipped, allowing the remaining work to proceed. For LLM context window overflows specifically, a **progressive fallback** retry sequence is applied before giving up on a section.

---

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ContextWindowExceededError` | Prompt exceeds the LLM's context window | Retried up to 3 times with progressively reduced context: full callee summary → compact callee summary (100 chars each) → no callee context | Yes | Section may be generated with reduced context; if all attempts fail, section is skipped |
| LLM returns `None` | `llm_client.generate()` returns `None` (e.g. after max retries inside `LLMClient`) | Section or summary is treated as a failure; warning is logged | Yes (section skipped) | Affected section is omitted from the document |
| All sections fail to generate | Every section in `_generate_file_doc` yields `None` | Error logged; `_generate_file_doc` returns `None` | Yes (file skipped) | File's document is not produced; file is not added to `doc_map` |
| Summary generation failure | Any exception from `llm_client.generate()` during summary generation | Warning logged; summary is set to empty string `""` | Yes | Document is saved without a summary field |
| Source file not found | `_find_source_file` finds no copied source in the output directory | Warning logged; `_generate_file_doc` returns `None` | Yes (file skipped) | File's document is not produced |
| `file_dependencies.json` missing | The JSON file does not exist in the expected output directory | Warning logged; `_generate_file_doc` returns `None` | Yes (file skipped) | File's document is not produced |
| Output directory missing | `resolve_file_output_dir` returns a path that does not exist as a directory | Warning logged; `process_one` returns `(file_rel, None)` | Yes (file skipped) | File is silently skipped in the current level |
| Corrupt or unreadable `doc.json` | `json.JSONDecodeError` or `OSError` when loading an existing document for reuse | Exception silently suppressed; falls back to full regeneration | Yes | LLM is called even though a cached document exists |
| Corrupt or unreadable `doc.json` during MD→JSON sync | `json.JSONDecodeError` or `OSError` in `_sync_md_to_json` | Exception silently suppressed; sync is aborted | Yes (sync skipped) | Manual MD edits are not propagated to JSON for this file |
| `asyncio.gather` task raises an exception | An unhandled exception propagates from a `process_one` task | Exception is caught via `return_exceptions=True`; logged at ERROR level; result skipped | Yes (file skipped) | File is not added to `doc_map`; downstream dependents lose its context |
| Circular dependencies | Kahn's algorithm cannot fully drain the dependency graph | Remaining files are collected into a final level; warning logged | Yes | Circularly dependent files are processed last, potentially without each other's context |

---

### 3. Design Notes

- **Granularity isolation**: Errors are contained at the smallest possible scope—section, then file, then batch—so a single failing LLM call never aborts the level or the overall run.
- **Progressive context reduction for context overflow**: Rather than immediately discarding callee context on a `ContextWindowExceededError`, the fallback sequence attempts to preserve as much context as possible (full → compact → none), reflecting a deliberate trade-off between context richness and prompt feasibility.
- **Silent suppression for cache reads**: Failures reading existing `doc.json` files are suppressed without logging and treated as a cache miss. This prioritises forward progress over surfacing storage errors.
- **`doc_map` integrity**: Files that fail generation return `None` and are excluded from `doc_map`, meaning their summaries are unavailable to dependent files processed in later levels. This is an accepted consequence of the graceful-skip strategy rather than a propagated error.
- **`return_exceptions=True` in `asyncio.gather`**: Ensures that an exception in one concurrent task does not cancel sibling tasks within the same batch, consistent with the overall non-terminating philosophy.

## Summary

`doc_creator.py` generates LLM-based design documents for all project source files in dependency order. Public entry point: `generate_all_docs(base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int, changed_files: set[str]|None) -> None`. Consumes `project_dep_list` (`list[dict]` with `file`, `callers`, `callees`) and a `template` dict (`sections[{id, title, prompt}]`, `summary_prompt`). Produces per-file `doc.md` and `doc.json` (`{file: str, sections: list[dict], summary: str}`) and accumulates an in-memory `doc_map: dict[str, dict]` used as callee context for dependent files.
