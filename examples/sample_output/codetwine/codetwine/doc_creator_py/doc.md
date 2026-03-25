# Design Document: codetwine/doc_creator.py

## Overview & Purpose

## Overview & Purpose

### 1. Module Summary

Orchestrates LLM-based design document generation for every source file in a project, assembling structured prompts from source code and dependency metadata, invoking the LLM per template section, and persisting results as paired JSON and Markdown files in topological dependency order.

---

### 2. When to Use This Module

- **Generating design documents for all project files**: Call `generate_all_docs(base_output_dir, project_dep_list, llm_client)` from `codetwine/pipeline.py` to produce a design document for every file discovered in the project's dependency graph.
- **Incremental regeneration after code changes**: Pass a `changed_files` set to `generate_all_docs` to skip files whose source and dependencies have not changed, reusing existing `doc.json` output where valid.
- **Resuming incomplete runs**: `generate_all_docs` checks whether each existing `doc.json` contains all expected template sections and a non-empty summary; incomplete documents are automatically regenerated regardless of the `changed_files` filter.
- **Propagating dependency changes**: Because `generate_all_docs` processes files in topological order (dependencies before dependents), callee summaries are available as context when generating documents for caller files.

---

### 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Entry point: topologically sorts all project files, generates or reuses design documents level by level in parallel batches, and saves each result as `doc.json` + `doc.md`. |

---

### 4. Design Decisions

- **Topological level-based parallelism**: Files are sorted into dependency depth levels by `_topological_sort_by_level` (Kahn's algorithm on the reverse graph). Within each level all files are independent, so they are processed in parallel batches of `max_workers`. Files at deeper levels can safely use the completed summaries of shallower-level dependencies as LLM context. Circular dependencies are detected and appended as a final level with a warning rather than causing a hard failure.

- **Progressive context fallback for context-window errors**: Section generation attempts the prompt three times in degrading order — full callee summary context → compact (100-character truncated) callee summaries → no callee context — catching `ContextWindowExceededError` between attempts. This avoids outright failure on large files without requiring manual prompt tuning.

- **MD-to-JSON sync for manual edits**: Before reusing a cached document, `_sync_md_to_json` compares the modification timestamps of `doc.md` and `doc.json`. When `doc.md` is newer, its sections are parsed and diffed back into `doc.json`, preserving human edits across incremental runs.

- **Regeneration propagation**: The module tracks which files were regenerated in the current run (`regenerated_files`). A file whose callee was regenerated (even if that callee was not in the original `changed_files` set) is itself marked for regeneration, ensuring that callee-context summaries embedded in dependent documents stay consistent.

- **Header-file implementation context**: For C/C++ header files (`.h`, `.hpp`, `.hh`, `.hxx`), the module locates and injects the corresponding implementation file's source code into the prompt, giving the LLM visibility into how declared interfaces are actually implemented.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Constant | Type | Purpose |
|---|---|---|
| `HEADER_TARGET_FILE` | `str` | Prompt heading template indicating the target file path. |
| `HEADER_SOURCE_CODE` | `str` | Section heading for the source code block. |
| `HEADER_CALLEE_USAGES` | `str` | Section heading for dependency (callee) usage listing. |
| `CALLEE_USAGES_SCHEMA_NOTE` | `str` | Schema explanation note prepended to callee usage entries. |
| `CALLEE_SOURCE_CODE_LABEL` | `str` | Label preceding inline dependency source code blocks. |
| `HEADER_CALLER_USAGES` | `str` | Section heading for dependent (caller) usage listing. |
| `CALLER_USAGES_SCHEMA_NOTE` | `str` | Schema explanation note prepended to caller usage entries. |
| `CALLER_SOURCE_CODE_LABEL` | `str` | Label preceding inline caller source code blocks. |
| `HEADER_CALLEE_CONTEXT` | `str` | Section heading for design document summaries of dependency files. |
| `CALLEE_CONTEXT_NOTE` | `str` | Explanatory note prepended to the callee context block. |
| `HEADER_REQUEST` | `str` | Section heading for the LLM instruction block. |
| `SECTION_REQUEST_TEMPLATE` | `str` | Template for the per-section LLM instruction line; `{title}` is substituted. |
| `OUTPUT_LANGUAGE_INSTRUCTION` | `str` | Instruction appended to every prompt specifying the output language; `{language}` is substituted. |
| `FACTUAL_ACCURACY_INSTRUCTION` | `str` | Warning appended to every section prompt prohibiting speculative content. |
| `HEADER_IMPL_CONTEXT` | `str` | Section heading for the corresponding C/C++ implementation file block. |
| `IMPL_CONTEXT_NOTE` | `str` | Explanatory note prepended to the implementation source code block. |
| `HEADER_DOC_CONTENT` | `str` | Section heading in the summary prompt containing all previously generated sections. |
| `SUMMARY_CHAR_LIMIT` | `str` | Character-limit instruction for the summary prompt; `{max_chars}` is substituted. |
| `_HEADER_EXTENSIONS` | `set[str]` | Set of C/C++ header file extensions (`".h"`, `".hpp"`, `".hh"`, `".hxx"`). |
| `_IMPL_EXTENSIONS` | `list[str]` | Ordered list of implementation file extensions to probe (`"cpp"`, `"c"`, `"cc"`, `"cxx"`). |

---

## Functions

---

### `_topological_sort_by_level`

**Signature:**
```
_topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]
```

- **`project_dep_list`**: Each element is `{"file": str, "callers": list, "callees": list}` — the project-wide dependency list produced by `save_project_dependencies`.
- **Returns**: A list of levels; each level is a sorted list of file paths. Index 0 contains files with no dependencies; index N contains files whose dependencies are all at level N-1 or below.

**Responsibility:** Arranges all project files into dependency-depth levels so that design documents for leaf dependencies are generated before the files that depend on them.

**When to use:** Called once at the start of `generate_all_docs` to determine the per-level processing order.

**Design decisions:**
- Implements Kahn's BFS algorithm on the **reverse** dependency graph so that files with no callees (leaves) enter the queue first without explicitly inverting the traversal direction.
- Each level is sorted alphabetically before being appended, ensuring deterministic output.

**Constraints & edge cases:**
- Files that appear only in `callees` lists (never as a top-level `"file"` key) are still added to `all_files` and processed.
- Circular dependencies are detected as nodes that remain unprocessed after BFS completes; they are appended as an extra final level and a warning is logged.

---

### `_build_section_prompt`

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

| Parameter | Type | Description |
|---|---|---|
| `section` | `dict` | Single section definition with keys `id`, `title`, `prompt`. |
| `source_code` | `str` | Full source text of the target file. |
| `file_deps` | `dict` | Parsed `file_dependencies.json` containing `file`, `callee_usages`, `caller_usages`. |
| `callee_context` | `str` | Pre-built summary text from dependency design documents (may be empty). |
| `implementation_context` | `str` | Source code of the paired implementation file for C/C++ headers (empty otherwise). |

- **Returns**: A single assembled prompt string.

**Responsibility:** Composes all contextual information (source, dependencies, callee summaries, implementation context, and per-section instructions) into the exact prompt string submitted to the LLM.

**When to use:** Called by `_generate_section_with_fallback` for every combination of section and callee-context fallback level.

**Design decisions:**
- Sections are assembled into a `parts: list[str]` and joined with `"\n"`, avoiding repeated string concatenation.
- `callee_usages` and `caller_usages` blocks are omitted entirely when the respective lists are empty, keeping prompts lean.
- `implementation_context` block is only inserted when the string is non-empty, making the function transparent for non-header files.
- `OUTPUT_LANGUAGE_INSTRUCTION` and `FACTUAL_ACCURACY_INSTRUCTION` are always appended last, after the section-specific prompt.

**Constraints & edge cases:**
- `output_path_to_rel` is applied to all file path values displayed in the prompt to show source-relative paths.
- Inline `target_context` and `usage_context` blocks in usages are omitted silently when not present in the dict.

---

### `_build_summary_prompt`

**Signature:**
```
_build_summary_prompt(
    file_path: str,
    section_contents: list[dict],
    summary_prompt: str,
    summary_max_chars: int,
) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Relative path of the target file. |
| `section_contents` | `list[dict]` | Already-generated sections, each `{"id": str, "title": str, "content": str}`. |
| `summary_prompt` | `str` | Summary instruction text from the template. |
| `summary_max_chars` | `int` | Maximum character count for the summary. |

- **Returns**: Assembled prompt string for summary generation.

**Responsibility:** Constructs the prompt used to ask the LLM to synthesize all section content into a concise summary.

**When to use:** Called by `_generate_summary` after all per-section content has been generated successfully.

**Constraints & edge cases:**
- Does not append `FACTUAL_ACCURACY_INSTRUCTION`; only `OUTPUT_LANGUAGE_INSTRUCTION` and `SUMMARY_CHAR_LIMIT` are appended.

---

### `_build_callee_context_summary`

**Signature:**
```
_build_callee_context_summary(
    file_deps: dict,
    doc_map: dict[str, dict],
    compact: bool = False,
) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `file_deps` | `dict` | Target file's `file_dependencies.json`. |
| `doc_map` | `dict[str, dict]` | Map of source-relative path → design document dict for all already-processed files. |
| `compact` | `bool` | If `True`, each summary is truncated to 100 characters with an ellipsis. |

- **Returns**: A newline-joined string of `"- **{path}**: {summary}"` lines, or an empty string if no callee summaries are available.

**Responsibility:** Extracts and optionally compresses dependency design document summaries into a context block for inclusion in section prompts.

**When to use:** Called in `_generate_file_doc` to produce both the full and compact callee context strings passed to `_generate_section_with_fallback`.

**Design decisions:**
- Dependency files are deduplicated via a `set` before iteration and then sorted for deterministic output.
- `output_path_to_rel` is applied to convert `callee_usages[*].from` (output-format paths) to source-relative paths used as `doc_map` keys.
- Entries for which no design document exists in `doc_map` are silently skipped.

**Constraints & edge cases:**
- `compact=True` truncates to exactly 100 characters and appends `"..."` only if the original exceeded that length.

---

### `_build_implementation_context`

**Signature:**
```
_build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str
```

| Parameter | Type | Description |
|---|---|---|
| `file_rel` | `str` | Relative path of the target file (used to detect header extensions and derive the base name). |
| `file_output_dir` | `str` | Output directory of the target file. |

- **Returns**: Full source code text of the matched implementation file, or an empty string.

**Responsibility:** Provides C/C++ header files with the source code of their paired implementation file so the LLM can reason about both declaration and definition together.

**When to use:** Called in `_generate_file_doc` for every file; returns immediately for non-header files.

**Design decisions:**
- Probes `_IMPL_EXTENSIONS` in order (`cpp`, `c`, `cc`, `cxx`) and returns the first match found, so `cpp` takes precedence over `c`.
- The implementation file is expected at `{parent_of_output_dir}/{stem}_{impl_ext}/{stem}.{impl_ext}`, mirroring the output directory naming convention.
- Returns `""` for any file whose extension is not in `_HEADER_EXTENSIONS` without any filesystem access.

**Constraints & edge cases:**
- Only files that physically exist at the expected path are matched; the function does not search recursively.

---

### `_generate_section_with_fallback` *(async)*

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

- **Returns**: Generated section text string, or `None` if all three attempts fail.

**Responsibility:** Wraps LLM section generation with a three-stage fallback strategy that progressively reduces prompt size when the context window is exceeded.

**When to use:** Called once per section per file inside `_generate_file_doc`.

**Design decisions (fallback order):**

| Attempt | Callee context used |
|---|---|
| 1 | Full callee summary (`callee_context_summary`) |
| 2 | Compact callee summary (`callee_context_compact`, first 100 chars each) |
| 3 | No callee context (`""`) |

- Only `ContextWindowExceededError` triggers progression to the next attempt; all other exceptions propagate from `llm_client.generate`.
- A warning is logged on each fallback transition.

**Constraints & edge cases:**
- Returns `None` only when all three attempts raise `ContextWindowExceededError` or when all return `None`.
- This is an `async` function; each `llm_client.generate` call is individually awaited, so the three attempts are sequential, not concurrent.

---

### `_generate_file_doc` *(async)*

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

- **`doc_map`**: `dict[str, dict]` — map of already-processed file paths to their design document dicts; used read-only to build callee context.
- **Returns**: Design document dict `{"file": str, "sections": list[dict], "summary": str}`, or `None` if generation completely fails.

**Responsibility:** Orchestrates the complete design document generation for a single file: reads source and dependency data, calls the LLM for each section and the summary, and assembles the result.

**When to use:** Called by the `process_one` inner function inside `generate_all_docs` for every file that requires regeneration.

**Design decisions:**
- Skips the entire file (returns `None`) if either the source file or `file_dependencies.json` cannot be found.
- Returns `None` (with an error log) if no sections were generated at all; a partial result with at least one section is returned as-is.
- Section generation uses `_generate_section_with_fallback` sequentially; the summary is generated afterwards via a separate `_generate_summary` call.
- This is `async`; all LLM calls are `await`-ed sequentially within a single file, while parallelism across files is managed by the caller.

**Constraints & edge cases:**
- `summary` is set to `""` if `_generate_summary` returns `None`.
- Skipped (missing) sections are logged as warnings and omitted from `section_list`, not replaced with placeholder text.

---

### `_generate_summary` *(async)*

**Signature:**
```
async _generate_summary(
    file_path: str,
    section_list: list[dict],
    template: dict,
    llm_client: LLMClient,
) -> str | None
```

- **Returns**: Summary text string, or `None` on any exception.

**Responsibility:** Requests a concise summary of the complete design document from the LLM by sending all generated section content in a single prompt.

**When to use:** Called at the end of `_generate_file_doc` after all sections have been generated.

**Constraints & edge cases:**
- Catches all exceptions (`Exception`) rather than only `ContextWindowExceededError`; failures produce a warning log and return `None` without retrying.
- `SUMMARY_MAX_CHARS` from settings is used as the character limit embedded in the prompt.

---

### `_find_source_file`

**Signature:**
```
_find_source_file(output_dir: str, file_rel: str) -> str | None
```

- **Returns**: Absolute path to the copied source file, or `None` if not found.

**Responsibility:** Locates the source file copy within an output directory using only the file's base name.

**When to use:** Called at the start of `_generate_file_doc` to obtain the path of the source code to read.

**Constraints & edge cases:**
- Only the base name of `file_rel` is used for the lookup; no subdirectory search is performed.

---

### `_save_doc`

**Signature:**
```
_save_doc(doc: dict, output_dir: str) -> None
```

| Parameter | Type | Description |
|---|---|---|
| `doc` | `dict` | Design document with keys `file`, `sections` (list of `{id, title, content}`), `summary`. |
| `output_dir` | `str` | Directory where `doc.md` and `doc.json` are written. |

**Responsibility:** Persists a design document to disk in both human-readable Markdown and machine-readable JSON formats.

**When to use:** Called after successful document generation in `process_one`, and also called by `_sync_md_to_json` after applying edits.

**Design decisions:**
- Markdown is written **before** JSON so that JSON always has an equal or newer `mtime`, which is the condition `_sync_md_to_json` uses to decide whether to sync.
- `summary` is appended as a `"## Summary"` section in Markdown only when the field is non-empty.

---

### `_parse_md_sections`

**Signature:**
```
_parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]
```

- **`section_titles`**: Known section titles used as delimiters; matches are case-sensitive and must appear as `## {title}` on their own line.
- **Returns**: `dict[str, str]` mapping each matched title to its stripped content text. Titles not found in `md_text` are omitted.

**Responsibility:** Extracts the content of known sections from a Markdown document, tolerating arbitrary `##` headings inside section content that are not in the known-titles list.

**When to use:** Called by `_sync_md_to_json` to parse a manually edited `doc.md` before diffing against `doc.json`.

**Design decisions:**
- The regex anchors `## {title}` to a full line (`^...$` with `re.MULTILINE`) and requires exact matches against escaped known titles, so arbitrary `##` lines inside content are never treated as section boundaries.

**Constraints & edge cases:**
- Returns an empty dict if no known titles are found in `md_text`.
- Title matching is whitespace-tolerant on the trailing end (`\s*$`) but case-sensitive.

---

### `_sync_md_to_json`

**Signature:**
```
_sync_md_to_json(output_dir: str) -> None
```

**Responsibility:** Propagates manual edits made to `doc.md` back into `doc.json` when the Markdown file is newer, then re-saves both files to keep them in sync.

**When to use:** Called by `process_one` before reusing an existing `doc.json`, ensuring any manual Markdown edits are not lost.

**Design decisions:**
- Uses `mtime` comparison as a lightweight change-detection mechanism; if `doc.md` is not newer than `doc.json`, no action is taken.
- A section's content is only updated in the JSON if the **next** section in JSON order is also present in the parsed Markdown; this guards against inaccurate boundary detection when intermediate sections are absent from the MD.
- After modifying the JSON, `_save_doc` is called to regenerate both files, resetting `mtime` ordering.

**Constraints & edge cases:**
- No-ops if either `doc.json` or `doc.md` is absent.
- `json.JSONDecodeError` and `OSError` during JSON loading both cause a silent early return.
- Only sections present in both the MD parse result and the JSON are candidates for update; extra or missing sections in MD do not cause errors.

---

### `generate_all_docs` *(async)*

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

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Root directory under which per-file output directories are resolved. |
| `project_dep_list` | `list` | Project-wide dependency list from `save_project_dependencies`. |
| `llm_client` | `LLMClient` | Initialized async LLM client. |
| `max_workers` | `int` | Maximum number of files processed concurrently within a single level. |
| `changed_files` | `set[str] \| None` | Relative paths of files that changed. `None` triggers full regeneration. |

**Responsibility:** Top-level orchestrator that generates design documents for an entire project in topological order, respecting dependency depth, parallelism limits, and incremental regeneration.

**When to use:** Called from `codetwine/pipeline.py` when `ENABLE_LLM_DOC` is enabled, after dependency extraction.

**Design decisions:**
- Files within the same level are processed in batches of `max_workers` using `asyncio.gather`, so intra-level files are concurrent while inter-level ordering is strictly sequential.
- Completed design documents are accumulated in `doc_map` and made available as callee context for all subsequent levels.
- `regenerated_files` tracks files regenerated in the current run; a caller is marked for regeneration even if the caller file itself is unchanged but one of its callees was regenerated.
- Incremental mode (`changed_files` is not `None`) also invokes `_sync_md_to_json` and `_is_doc_complete` before reusing an existing document.

**Constraints & edge cases:**
- `changed_files=None` forces regeneration of every file regardless of existing output.
- Exceptions raised by individual `process_one` tasks are caught via `return_exceptions=True` in `asyncio.gather` and logged as errors without aborting the run.
- Files whose output directory does not exist are skipped with a warning.

---

#### Inner Functions of `generate_all_docs`

---

##### `_needs_regeneration(file_rel: str) -> bool`

**Responsibility:** Encapsulates the three-condition regeneration decision (full mode, file changed, or any callee changed/regenerated) as a readable predicate.

**Design decisions:** Checks `regenerated_files` in addition to `changed_files` so that callers of a freshly regenerated dependency are also included, even if they were not in the original change set.

---

##### `_is_doc_complete(doc: dict) -> bool`

**Responsibility:** Validates that an existing design document contains exactly the set of sections defined by the template and a non-empty summary, so incomplete documents from previous interrupted runs are not silently reused.

**Constraints & edge cases:**
- Returns `False` on any section-id mismatch (extra or missing sections).
- The summary check is only applied when the template defines a `"summary_prompt"`.

---

##### `process_one(file_rel: str) -> tuple[str, dict | None]` *(async)*

**Signature:**
```
async process_one(file_rel: str) -> tuple[str, dict | None]
```

- **Returns**: `(file_rel, doc)` where `doc` is the design document dict or `None` on failure.

**Responsibility:** Wraps the full per-file pipeline — reuse check, sync, generation, and save — as a single awaitable unit suitable for `asyncio.create_task`.

**Design decisions:**
- Prints and logs `REUSE`, `INCOMPLETE`, `OK`, or `SKIP` status for each file, providing progress visibility.
- On successful generation, `_save_doc` is called and `file_rel` is added to `regenerated_files` before returning.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

**`doc_creator.py` → `codetwine/utils/file_utils.py`** : Requires `output_path_to_rel` to convert output-format paths (e.g. `project_name/copy_destination_path`) back to source-relative paths when building prompts and callee context summaries, and requires `resolve_file_output_dir` to compute the absolute output directory path for a given file's relative path during document generation and processing.

**`doc_creator.py` → `codetwine/llm/client.py`** : Requires `LLMClient` to send assembled prompts to the LLM and retrieve generated text for each document section and summary via its `generate` async method.

**`doc_creator.py` → `codetwine/llm/__init__.py`** : Requires `ContextWindowExceededError` to catch context window overflow exceptions during LLM generation and trigger the progressive fallback mechanism (full summary → compact summary → no callee context).

**`doc_creator.py` → `codetwine/config/settings.py`** : Requires the following configuration constants:
- `MAX_WORKERS` — controls the degree of parallelism when processing files within a level.
- `DOC_TEMPLATE_PATH` — filesystem path to the JSON template defining document sections and summary prompt.
- `OUTPUT_LANGUAGE` — target language string injected into every section and summary prompt.
- `SUMMARY_MAX_CHARS` — maximum character count enforced in the summary generation prompt.

---

### Dependents (modules that import this file)

**`codetwine/pipeline.py` → `doc_creator.py`** : Imports and calls `generate_all_docs` as the top-level entry point for the LLM-based design document generation phase of the pipeline. It passes the base output directory, the full project dependency list, an `LLMClient` instance, the worker concurrency limit, and an optional set of changed files to enable incremental regeneration.

---

### Dependency Direction

All relationships are **unidirectional**:

- `doc_creator.py → codetwine/utils/file_utils.py` : unidirectional (file_utils does not reference doc_creator).
- `doc_creator.py → codetwine/llm/client.py` : unidirectional (LLMClient does not reference doc_creator).
- `doc_creator.py → codetwine/llm/__init__.py` : unidirectional (the llm package does not reference doc_creator).
- `doc_creator.py → codetwine/config/settings.py` : unidirectional (settings does not reference doc_creator).
- `codetwine/pipeline.py → doc_creator.py` : unidirectional (doc_creator does not reference pipeline.py).

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `base_output_dir` | Caller (`pipeline.py`) | `str` — filesystem path to the root output directory |
| `project_dep_list` | Caller (`pipeline.py`) | `list[dict]` — each element has `file`, `callees`, `callers` keys |
| `llm_client` | Caller (`pipeline.py`) | `LLMClient` instance |
| `max_workers` | Config (`MAX_WORKERS`) / caller | `int` — parallelism limit per level batch |
| `changed_files` | Caller (`pipeline.py`) | `set[str] \| None` — relative paths of changed files |
| `DOC_TEMPLATE_PATH` | Config (`settings.py`) | JSON file path; loaded into a `dict` with `sections` and `summary_prompt` keys |
| `file_output_dir/{filename}` | Filesystem | Source code file copied to the output directory |
| `file_output_dir/file_dependencies.json` | Filesystem | JSON file with `file`, `callee_usages`, `caller_usages`, `definitions` keys |
| `file_output_dir/doc.json` | Filesystem (optional) | Previously generated design document; reused when unchanged |
| `file_output_dir/doc.md` | Filesystem (optional) | Manually edited Markdown; synced back to JSON when newer |

---

## 2. Transformation Overview

### Stage 1 — Dependency Level Ordering

`project_dep_list` is consumed by `_topological_sort_by_level`, which builds an adjacency graph from `file` → `callees` relationships and runs a reverse-graph BFS (Kahn's algorithm). The output is `level_list: list[list[str]]` — a sorted grouping of file relative paths from leaves (no dependencies) at level 0 up to roots.

### Stage 2 — Per-Level Parallel Dispatch

`generate_all_docs` iterates `level_list` level by level. Within each level, files are batched into chunks of `max_workers`. Each batch is dispatched as a set of concurrent `asyncio` tasks via `asyncio.gather`. Results from completed levels accumulate into `doc_map` before the next level begins, ensuring that callee summaries are always available before their dependents are processed.

### Stage 3 — Regeneration Guard (per file)

For each file, `_needs_regeneration` checks:
1. Whether `changed_files` is `None` (full mode).
2. Whether the file itself is in `changed_files`.
3. Whether any callee appears in `changed_files` or `regenerated_files`.

If regeneration is not needed, `_sync_md_to_json` is called first (syncing any manual Markdown edits back to JSON), then the existing `doc.json` is loaded and validated by `_is_doc_complete`. If complete, the document is returned immediately and added to `doc_map` without any LLM call.

### Stage 4 — Context Assembly (per file)

`_generate_file_doc` reads the source file and `file_dependencies.json` from disk, then builds two variants of callee context via `_build_callee_context_summary`:
- **Full**: one `- **file**: {summary}` line per unique dependency file found in `callee_usages`.
- **Compact**: same, but each summary is truncated to 100 characters.

For C/C++ header files (`.h`, `.hpp`, `.hh`, `.hxx`), `_build_implementation_context` searches the sibling output directory for a matching implementation file (`.cpp`, `.c`, `.cc`, `.cxx`) and reads its source code as `implementation_context`.

### Stage 5 — Section Prompt Construction and LLM Generation

For each section in `template["sections"]`, `_build_section_prompt` assembles a multi-part prompt string by concatenating:
- Target file header + source code block
- (Optional) implementation file source code block
- `callee_usages` list with per-symbol `target_context` source blocks
- `caller_usages` list with per-symbol `usage_context` source blocks
- Callee summary context block
- Section-specific instruction, output language directive, and factual accuracy instruction

The assembled prompt is passed to `llm_client.generate`. If a `ContextWindowExceededError` is raised, `_generate_section_with_fallback` retries up to three times in order: full callee context → compact callee context → no callee context.

### Stage 6 — Summary Generation

Once all sections are generated, `_generate_summary` calls `_build_summary_prompt`, which assembles a prompt from all generated section titles and contents, appending the `summary_prompt` instruction and `SUMMARY_MAX_CHARS` character limit. The resulting prompt is sent to `llm_client.generate`.

### Stage 7 — Output Serialization

`_save_doc` writes the completed document to two files in `output_dir`:
- `doc.md` — rendered Markdown with `## {title}` headings
- `doc.json` — structured JSON (written after MD so its `mtime` is ≥ MD's)

The file's relative path is stored in `doc_map` for use as callee context by subsequent levels.

### MD → JSON Sync (side path)

When `_sync_md_to_json` detects that `doc.md` has a newer `mtime` than `doc.json`, it parses the Markdown using `_parse_md_sections` (regex-based split on `## {known_title}` lines), diffs each section against the JSON, applies changes, and calls `_save_doc` to re-emit both files.

---

## 3. Outputs

| Output | Destination | Format |
|---|---|---|
| `doc.md` | `{file_output_dir}/doc.md` | Markdown file with `## {section title}` headings and a trailing `## Summary` block |
| `doc.json` | `{file_output_dir}/doc.json` | JSON file with `file`, `sections`, `summary` keys |
| `doc_map` | In-memory (used within `generate_all_docs`) | `dict[str, dict]` — accumulated design documents keyed by file relative path |
| Console / log output | `stdout` and logger | Status lines: `REUSE`, `INCOMPLETE`, `OK`, `SKIP` per file; level progress messages |

---

## 4. Key Data Structures

### `project_dep_list` element

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `callees` | `list[str]` | Relative paths of files this file depends on |
| `callers` | `list[str]` | Relative paths of files that depend on this file |

### `file_dependencies.json` / `file_deps`

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `callee_usages` | `list[dict]` | Symbols used by this file from external files |
| `caller_usages` | `list[dict]` | Symbols from this file used by other files |

### `callee_usages` element

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name being used |
| `from` | `str` | Output-format path of the file defining the symbol |
| `target_context` | `str \| None` | Full source code of the dependency file (optional) |

### `caller_usages` element

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name being used |
| `file` | `str` | Path of the file that uses the symbol |
| `usage_context` | `str \| None` | Source code snippet from the using file (optional) |

### `doc` (design document dict)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `sections` | `list[dict]` | Ordered list of generated section dicts |
| `summary` | `str` | Short summary of the entire design document |

### `sections` element

| Field / Key | Type | Purpose |
|---|---|---|
| `id` | `str` | Section identifier from the template |
| `title` | `str` | Human-readable section title |
| `content` | `str` | LLM-generated text for this section |

### `doc_map`

| Field / Key | Type | Purpose |
|---|---|---|
| `{file_rel}` (key) | `str` | Source-relative file path |
| value | `dict` | Design document dict (`file`, `sections`, `summary`) |

### `level_list`

| Field / Key | Type | Purpose |
|---|---|---|
| outer index | `int` | Dependency depth level (0 = no dependencies) |
| inner element | `str` | File relative path belonging to that level |

### `file_callees`

| Field / Key | Type | Purpose |
|---|---|---|
| `{file_rel}` (key) | `str` | Source-relative file path |
| value | `set[str]` | Set of callee relative paths for that file |

## Error Handling

# Error Handling

## 1. Overall Strategy

The module employs **graceful degradation with logging-and-continue** as its primary strategy. No single file failure is permitted to abort the overall document generation pipeline. Instead, errors are caught, logged at the appropriate severity level (`warning` or `error`), and processing continues with the remaining files. For LLM-specific capacity constraints, the module additionally applies a **progressive fallback** strategy: when a context window is exceeded, the prompt is progressively simplified through up to three attempts before the section is abandoned.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ContextWindowExceededError` (attempt 1) | Prompt with full callee summary context exceeds the LLM's context window | Retry with compact callee summaries (first 100 chars each) | Yes | Reduced context fidelity |
| `ContextWindowExceededError` (attempt 2) | Compact callee summary prompt still exceeds the context window | Retry with no callee context at all | Yes | No dependency context in output |
| `ContextWindowExceededError` (attempt 3) | All three prompt variants exceed the context window | Returns `None`; section is skipped with a `warning` log | No | Section omitted from document |
| Section generation failure (`None` result) | LLM returns `None` for a section after all fallback attempts | Section is skipped; a `warning` is logged | Yes (file continues) | One section missing from document |
| All sections failed | Every section for a file fails to generate | `_generate_file_doc` returns `None`; an `error` is logged | No (file skipped) | File has no document produced |
| Summary generation failure | LLM raises any exception or returns `None` during summary generation | Caught broadly; `warning` logged; summary set to empty string `""` | Yes | File document lacks a summary |
| Source file not found | No copied source file exists in the expected output directory | Returns `None` with a `warning` log | No (file skipped) | File has no document produced |
| `file_dependencies.json` missing | Dependency JSON does not exist in the output directory | Returns `None` with a `warning` log | No (file skipped) | File has no document produced |
| Output directory missing | `resolve_file_output_dir` resolves to a non-existent directory | Returns `(file_rel, None)` with a `warning` log | No (file skipped) | File has no document produced |
| `doc.json` unreadable or malformed | `json.JSONDecodeError` or `OSError` when loading an existing doc for reuse | Silently falls through to regeneration | Yes | Full regeneration performed |
| `doc.json` incomplete | Existing doc is missing expected sections or has an empty summary | Triggers regeneration instead of reuse | Yes | Full regeneration performed |
| Task-level exception in `asyncio.gather` | An unhandled exception propagates from a `process_one` coroutine | Caught via `isinstance(result, Exception)`; `error` logged; task skipped | Yes (other tasks unaffected) | One file has no document |
| `_sync_md_to_json` I/O or parse failure | `OSError` or `json.JSONDecodeError` reading files during MD→JSON sync | Returns silently without modifying files | Yes | Sync skipped; files unchanged |
| Circular dependency detected | Kahn's algorithm leaves unprocessed files after BFS completes | Remaining files appended to the last level; `warning` logged | Yes | Files processed at final level |

---

## 3. Design Notes

- **Progressive fallback scope is per-section, not per-file.** A context window failure on one section does not affect other sections of the same file. This allows a partial document to be produced even when some sections require degraded context.

- **`asyncio.gather` is used with `return_exceptions=True`** (implicitly, via checking `isinstance(result, Exception)`), ensuring that a fatal exception in one concurrent task does not cancel sibling tasks processing other files in the same batch.

- **Incomplete documents trigger regeneration rather than reuse.** The `_is_doc_complete` check enforces that skipping LLM calls is only permitted when a structurally complete, valid document already exists, preventing silent propagation of partial outputs across incremental runs.

- **Summary failures are intentionally non-fatal.** Summary generation is treated as best-effort; a missing summary degrades the callee context available to dependent files in subsequent topological levels but does not block document output for the file itself.

- **MD→JSON sync errors are silently swallowed.** This reflects the design priority of never letting auxiliary sync operations interfere with the primary generation pipeline.

## Summary

**doc_creator.py** orchestrates LLM-based design document generation for all project source files in topological dependency order.

**Public API:** `generate_all_docs(base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int, changed_files: set[str]|None) -> None`

**Key data structures:**
- `project_dep_list`: `list[dict]` with `file`, `callees`, `callers`
- `doc_map`: `dict[str, dict]` mapping file paths to design document dicts (`file`, `sections`, `summary`)
- `level_list`: `list[list[str]]` — files grouped by dependency depth
- Outputs `doc.json` and `doc.md` per file
