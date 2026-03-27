# Design Document: codetwine/doc_creator.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Orchestrates LLM-based design document generation for all files in a codebase by processing them in topological dependency order and assembling structured prompts from source code, dependency metadata, and previously generated summaries.

## 2. When to Use This Module

- **Generating design documents for an entire project**: Call `generate_all_docs(base_output_dir, project_dep_list, llm_client)` to produce a `doc.json` and `doc.md` for every file discovered in the dependency list, processed level by level so that each file's document can reference summaries of its dependencies.
- **Incremental regeneration after code changes**: Pass a `changed_files` set to `generate_all_docs` to skip files whose source and dependencies are unchanged, reusing existing `doc.json` outputs and avoiding unnecessary LLM calls.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `async generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Entry point: topologically sorts all project files, generates design documents level by level in parallel batches, saves results as JSON and Markdown, and maintains a `doc_map` of summaries for use as context in subsequent levels. |

## 4. Design Decisions

- **Topological level processing**: Files are grouped into dependency depth levels via Kahn's algorithm (`_topological_sort_by_level`) and processed level by level. This guarantees that when a file's document is generated, design document summaries of all its dependencies are already available in `doc_map` to be injected as context into the LLM prompt.
- **Progressive context fallback**: When an LLM call fails with `ContextWindowExceededError`, `_generate_section_with_fallback` retries up to three times with decreasing context: full callee summaries → truncated callee summaries (first 100 chars each) → no callee context. This avoids hard failures caused by large dependency trees.
- **MD ↔ JSON synchronization**: If a user manually edits `doc.md` after generation, `_sync_md_to_json` detects that the MD timestamp is newer than the JSON and propagates the edits back into `doc.json` before reuse decisions are made, preserving manual corrections across incremental runs.
- **Callee-propagated regeneration**: The `changed_files` skip logic also marks a file for regeneration if any of its direct callees were changed or regenerated in the current run (`regenerated_files` set), ensuring that documents reflecting stale dependency context are not reused.
- **Header file implementation context**: For C/C++ header files (`.h`, `.hpp`, `.hh`, `.hxx`), `_build_implementation_context` locates and injects the corresponding implementation file's source code into the prompt, giving the LLM visibility into how declared interfaces are implemented.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Constant | Type | Purpose |
|---|---|---|
| `HEADER_TARGET_FILE` | `str` | Prompt template string for the target file heading line. |
| `HEADER_SOURCE_CODE` | `str` | Heading for the source code block in prompts. |
| `HEADER_CALLEE_USAGES` | `str` | Heading for the dependencies section in prompts. |
| `CALLEE_USAGES_SCHEMA_NOTE` | `str` | Schema explanation appended under the callee usages heading. |
| `CALLEE_SOURCE_CODE_LABEL` | `str` | Label prefixing each dependency's source code block. |
| `HEADER_CALLER_USAGES` | `str` | Heading for the dependents section in prompts. |
| `CALLER_USAGES_SCHEMA_NOTE` | `str` | Schema explanation appended under the caller usages heading. |
| `CALLER_SOURCE_CODE_LABEL` | `str` | Label prefixing each caller's usage context block. |
| `HEADER_CALLEE_CONTEXT` | `str` | Heading for the dependency design document summaries section. |
| `CALLEE_CONTEXT_NOTE` | `str` | Explanatory note prepended to callee summary context. |
| `HEADER_REQUEST` | `str` | Heading for the LLM request section. |
| `SECTION_REQUEST_TEMPLATE` | `str` | Template string; `{title}` is replaced with the section title. |
| `OUTPUT_LANGUAGE_INSTRUCTION` | `str` | Template string; `{language}` is replaced with the configured language. |
| `FACTUAL_ACCURACY_INSTRUCTION` | `str` | Appended warning to restrict LLM to source-backed descriptions only. |
| `HEADER_IMPL_CONTEXT` | `str` | Heading for the corresponding C/C++ implementation file section. |
| `IMPL_CONTEXT_NOTE` | `str` | Explanatory note for the implementation file context block. |
| `HEADER_DOC_CONTENT` | `str` | Heading for the full design document content in summary prompts. |
| `SUMMARY_CHAR_LIMIT` | `str` | Template string; `{max_chars}` is replaced with the configured limit. |
| `_HEADER_EXTENSIONS` | `set[str]` | C/C++ header file extensions: `.h`, `.hpp`, `.hh`, `.hxx`. |
| `_IMPL_EXTENSIONS` | `list[str]` | Implementation file extensions paired with header extensions: `cpp`, `c`, `cc`, `cxx`. |

---

## Functions

---

### `_topological_sort_by_level`

**Signature:**
```python
def _topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]
```
- `project_dep_list`: Each element is a dict with keys `"file"` (str), `"callers"` (list), `"callees"` (list).
- Returns a list of levels, each level being a list of file path strings. Index 0 = files with no dependencies.

**Responsibility:** Organises all project files into dependency-depth levels so that documents for dependency files are always generated before their dependents.

**When to use:** Called once at the start of `generate_all_docs` to determine the processing order for all files.

**Design decisions:**
- Uses Kahn's BFS algorithm on the **reverse graph** (dependent→dependency direction) so that files with no outgoing callees are in level 0.
- In-degree is computed on the reverse graph, not the original, to correctly assign level-0 membership.
- Circular dependencies: any files not drained by Kahn's algorithm are appended as an extra final level; a warning is logged.

**Constraints & edge cases:**
- Files referenced only as callees but not present as a `"file"` entry in `project_dep_list` are still included via `all_files`.
- Circular dependency groups are never silently dropped; they appear at the last level.

---

### `_build_section_prompt`

**Signature:**
```python
def _build_section_prompt(
    section: dict,
    source_code: str,
    file_deps: dict,
    callee_context: str,
    implementation_context: str = "",
) -> str
```
- `section`: Dict with keys `"id"`, `"title"`, `"prompt"`.
- `file_deps`: Dict with keys `"file"`, `"callee_usages"` (list), `"caller_usages"` (list), matching the shape of `file_dependencies.json`.
- `callee_context`: Pre-built callee summary text; may be empty.
- `implementation_context`: Source code of the paired `.cpp`/`.c` file; empty for non-header files.
- Returns a single newline-joined prompt string.

**Responsibility:** Assembles the complete LLM prompt for a single template section by combining source code, dependency/dependent usage information, callee summaries, and section-specific instructions.

**When to use:** Called inside `_generate_section_with_fallback` for each of the three fallback attempts, with a different `callee_context` value each time.

**Design decisions:**
- Each optional block (implementation context, callee usages, caller usages, callee summaries) is appended only when non-empty, keeping prompts minimal.
- `OUTPUT_LANGUAGE_INSTRUCTION` and `FACTUAL_ACCURACY_INSTRUCTION` are always appended last to ensure they are never omitted regardless of other content.
- `output_path_to_rel` is applied to file paths in usages so paths presented to the LLM are source-relative, not output-directory paths.

**Constraints & edge cases:**
- Does not validate that `section` contains all required keys; callers are responsible.
- `callee_context` being an empty string suppresses the entire callee context block.

---

### `_build_summary_prompt`

**Signature:**
```python
def _build_summary_prompt(
    file_path: str,
    section_contents: list[dict],
    summary_prompt: str,
    summary_max_chars: int,
) -> str
```
- `section_contents`: Each element is a dict with keys `"id"`, `"title"`, `"content"`.
- Returns a single newline-joined prompt string.

**Responsibility:** Constructs the LLM prompt that requests a short summary of the entire already-generated design document.

**When to use:** Called once per file inside `_generate_summary`, after all section content has been generated.

**Constraints & edge cases:**
- No fallback mechanism; if the summary prompt itself exceeds the context window the exception propagates to `_generate_summary`.

---

### `_build_callee_context_summary`

**Signature:**
```python
def _build_callee_context_summary(
    file_deps: dict,
    doc_map: dict[str, dict],
    compact: bool = False,
) -> str
```
- `doc_map`: Maps source-relative file path → design document dict with at least a `"summary"` key.
- `compact`: When `True`, each dependency summary is truncated to 100 characters followed by `"..."` if longer.
- Returns a newline-joined string of bullet lines, one per dependency with a known summary.

**Responsibility:** Extracts only the summary text from previously generated design documents of callee files and concatenates them so they can be injected into the current file's prompt as lightweight context.

**When to use:** Called in `_generate_file_doc` to prepare both the full and compact variants of callee context before section generation begins.

**Design decisions:**
- Deduplicates callee files via a set before iterating, avoiding repeated summaries when the same file is referenced by multiple usages.
- `output_path_to_rel` is applied when looking up `doc_map` because `callee_usages[*].from` is in output-path format while `doc_map` is keyed by source-relative paths.
- Dependencies with no entry in `doc_map` or with an empty summary are silently skipped.

**Constraints & edge cases:**
- Returns an empty string when no callee summaries are available; callers treat this as "no context."

---

### `_build_implementation_context`

**Signature:**
```python
def _build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str
```
- Returns the full text of the found implementation file, or an empty string.

**Responsibility:** For C/C++ header files, locates and reads the paired implementation file so its source code can be included in the LLM prompt.

**When to use:** Called once per file in `_generate_file_doc` before section generation; the result is passed through to every section prompt.

**Design decisions:**
- Searches the sibling output directories at the same level as `file_output_dir`, using the naming convention `{stem}_{ext}/` to locate the implementation directory.
- Iterates `_IMPL_EXTENSIONS` in order; the first match is returned, so `.cpp` takes precedence over `.c`.
- Returns empty string immediately if the file extension is not in `_HEADER_EXTENSIONS`, incurring no filesystem I/O for non-header files.

**Constraints & edge cases:**
- Only finds implementation files that have already been copied to the output directory structure; source-tree paths are not searched.

---

### `_generate_section_with_fallback`

**Signature:**
```python
async def _generate_section_with_fallback(
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
- Returns the generated section text, or `None` if all three attempts raise `ContextWindowExceededError` or return `None`.
- **Async:** awaits `llm_client.generate(prompt)` sequentially through up to three attempts.

**Responsibility:** Wraps the LLM call for a single section with a three-stage fallback that progressively reduces prompt size when the context window is exceeded.

**When to use:** Called once per template section inside `_generate_file_doc`.

**Design decisions:**

| Attempt | Callee context passed |
|---|---|
| 1 | Full callee summary (`callee_context_summary`) |
| 2 | Compact callee summary (`callee_context_compact`, first 100 chars each) |
| 3 | No callee context (empty string) |

- Only `ContextWindowExceededError` triggers a fallback; all other exceptions propagate normally.
- The three attempts are sequential, not parallel, because each subsequent attempt is only made if the previous one fails.

**Constraints & edge cases:**
- Returns `None` if the LLM returns `None` on any attempt (non-exception failure path does not retry).

---

### `_generate_file_doc`

**Signature:**
```python
async def _generate_file_doc(
    file_rel: str,
    file_output_dir: str,
    doc_map: dict[str, dict],
    template: dict,
    llm_client: LLMClient,
) -> dict | None
```
- `template`: Loaded JSON template dict with `"sections"` (list of section dicts) and `"summary_prompt"` (str).
- Returns a design document dict `{"file": str, "sections": list[dict], "summary": str}`, or `None` if no sections could be generated.
- **Async:** awaits section generation and summary generation sequentially.

**Responsibility:** Orchestrates the full generation pipeline for one source file: reading its source and dependency data, generating each section via the LLM, and generating the summary.

**When to use:** Called from `process_one` (inside `generate_all_docs`) when a file needs regeneration.

**Design decisions:**
- Sections that fail (return `None`) are individually skipped with a warning rather than aborting the entire document.
- Returns `None` only when zero sections were successfully generated, treating a partial document as acceptable.
- `doc_map` is consulted at call time (not captured earlier) so the most up-to-date summaries of already-processed files are used.

**Constraints & edge cases:**
- Requires both the source file copy and `file_dependencies.json` to exist in `file_output_dir`; returns `None` if either is missing.
- `_find_source_file` is used rather than constructing the path directly, supporting filenames that differ from the directory name.

---

### `_generate_summary`

**Signature:**
```python
async def _generate_summary(
    file_path: str,
    section_list: list[dict],
    template: dict,
    llm_client: LLMClient,
) -> str | None
```
- Returns the generated summary text, or `None` on any exception.
- **Async:** awaits a single `llm_client.generate` call.

**Responsibility:** Generates a short summary of the entire design document from all already-produced sections, using the character limit from `SUMMARY_MAX_CHARS`.

**When to use:** Called once at the end of `_generate_file_doc` after all sections have been generated.

**Constraints & edge cases:**
- Any exception during the LLM call is caught, logged as a warning, and `None` is returned; the calling code stores this as an empty string `""`.

---

### `_find_source_file`

**Signature:**
```python
def _find_source_file(output_dir: str, file_rel: str) -> str | None
```
- Returns the absolute path of the source copy, or `None` if not found.

**Responsibility:** Resolves the path of the copied source file within an output directory by using only the basename of `file_rel`.

**When to use:** Called at the start of `_generate_file_doc` to locate the source code to read.

**Constraints & edge cases:**
- Only the file's basename is used for the lookup; directory components of `file_rel` are ignored.

---

### `_save_doc`

**Signature:**
```python
def _save_doc(doc: dict, output_dir: str) -> None
```
- `doc`: Dict with keys `"file"` (str), `"sections"` (list of `{"id", "title", "content"}`), `"summary"` (str).

**Responsibility:** Persists a generated design document to disk in both Markdown (`doc.md`) and JSON (`doc.json`) formats.

**When to use:** Called after a successful `_generate_file_doc` and also by `_sync_md_to_json` after re-syncing content.

**Design decisions:**
- Markdown is written first; JSON is written second so that `doc.json` always has a `mtime` ≥ `doc.md`, which is the invariant checked by `_sync_md_to_json` to determine whether the MD has been manually edited.
- The summary is appended as a `## Summary` section at the end of the Markdown file only when non-empty.

---

### `_parse_md_sections`

**Signature:**
```python
def _parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]
```
- `section_titles`: Exact section titles to use as split delimiters (including `"Summary"`).
- Returns a dict mapping title → stripped content text. Titles not found in the MD are absent from the result.

**Responsibility:** Splits a Markdown document into named sections using only `## {known_title}` lines as boundaries, so that `##` headings inside LLM-generated content are not mistakenly treated as section delimiters.

**When to use:** Called inside `_sync_md_to_json` to extract section text from a user-edited `doc.md`.

**Design decisions:**
- Builds a compiled regex from the known titles using alternation and `re.MULTILINE`, ensuring only exact-match headings delimit sections.
- Uses `re.finditer` to collect match positions and slices between them for content, which handles an arbitrary number of sections without nested parsing.

**Constraints & edge cases:**
- Returns an empty dict if no known headings are found in the text.
- Titles are `re.escape`d before compilation, so titles containing regex metacharacters are handled safely.

---

### `_sync_md_to_json`

**Signature:**
```python
def _sync_md_to_json(output_dir: str) -> None
```

**Responsibility:** Propagates manual edits made to `doc.md` back into `doc.json` by comparing file modification timestamps and overwriting changed sections.

**When to use:** Called inside `process_one` when a file is being reused (no regeneration needed), before the existing `doc.json` is loaded and returned.

**Design decisions:**
- The MD→JSON sync direction is determined solely by `mtime(doc.md) > mtime(doc.json)`; if JSON is newer, no action is taken.
- A **safety gate** skips updating a section if the immediately following section (in JSON order) is absent from the parsed MD, preventing partial-parse boundary errors from corrupting content.
- After updating JSON, `_save_doc` is called to re-write both files, resetting `doc.json` `mtime` ≥ `doc.md` to prevent re-syncing on the next run.

**Constraints & edge cases:**
- Silently returns if either `doc.json` or `doc.md` does not exist.
- Returns without writing if `doc.json` cannot be parsed (malformed JSON or I/O error).
- Returns without writing if `_parse_md_sections` finds no recognised headings.

---

### `generate_all_docs`

**Signature:**
```python
async def generate_all_docs(
    base_output_dir: str,
    project_dep_list: list,
    llm_client: LLMClient,
    max_workers: int = MAX_WORKERS,
    changed_files: set[str] | None = None,
) -> None
```
- `project_dep_list`: List of dicts, each with keys `"file"`, `"callers"`, `"callees"`.
- `changed_files`: Set of source-relative file paths that changed since the last run. `None` means regenerate all files.
- **Async:** uses `asyncio.gather` for intra-level parallelism and awaits each batch.

**Responsibility:** Top-level orchestrator that generates design documents for every file in dependency order, reusing existing documents where nothing has changed.

**When to use:** Called once from `codetwine/pipeline.py` when `ENABLE_LLM_DOC` is set.

**Design decisions:**
- Files within the same level are processed in batches of `max_workers` using `asyncio.gather`, providing controlled concurrency without exceeding rate limits.
- `doc_map` is populated level by level so that callee summaries are always available before a dependent file is processed.
- `regenerated_files` tracks which files were re-generated in the current run so that a dependent of a regenerated file is also regenerated even if the dependent's source file itself is unchanged.
- Exceptions from individual tasks in `asyncio.gather` are caught via `return_exceptions=True` and logged without aborting the run.

**Constraints & edge cases:**
- Levels are processed strictly sequentially; only files within a single level run in parallel.
- A file whose output directory does not exist on disk is skipped with a warning.
- An existing `doc.json` is reused only if `_is_doc_complete` returns `True`; incomplete documents are regenerated.

---

### Nested function: `_needs_regeneration` (inside `generate_all_docs`)

**Signature:**
```python
def _needs_regeneration(file_rel: str) -> bool
```

**Responsibility:** Determines whether a file's design document must be regenerated based on change detection and callee regeneration state.

**Design decisions:** Closes over `changed_files`, `file_callees`, and `regenerated_files` from the enclosing scope, allowing it to reflect the current run state without additional parameters.

| Condition | Returns |
|---|---|
| `changed_files is None` | `True` (full mode) |
| `file_rel in changed_files` | `True` |
| Any callee is in `changed_files` or `regenerated_files` | `True` |
| None of the above | `False` |

---

### Nested function: `_is_doc_complete` (inside `generate_all_docs`)

**Signature:**
```python
def _is_doc_complete(doc: dict) -> bool
```

**Responsibility:** Validates that a loaded `doc.json` contains exactly the sections defined in the current template and a non-empty summary, so partially-generated documents are not reused.

**Design decisions:** Closes over `template` from the enclosing scope. Uses set equality on section IDs so order does not matter, but extra or missing sections both cause `False`.

---

### Nested async function: `process_one` (inside `generate_all_docs`)

**Signature:**
```python
async def process_one(file_rel: str) -> tuple[str, dict | None]
```
- Returns a tuple of `(file_rel, doc_dict)` where `doc_dict` is `None` on failure.
- **Async:** awaits `_generate_file_doc`.

**Responsibility:** Handles the full reuse-or-regenerate decision and I/O for a single file within the parallel batch, returning the result for insertion into `doc_map`.

**Design decisions:** Closes over `base_output_dir`, `doc_map`, `template`, `llm_client`, `regenerated_files`, and the nested helper functions, keeping the task submission loop in `generate_all_docs` clean.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

**`doc_creator.py` → `codetwine/utils/file_utils.py`** : Path resolution utilities
- `output_path_to_rel` — converts output-format paths (e.g. `project/src/foo_py/foo.py`) back to source-relative paths (e.g. `src/foo.py`) when building prompts and callee context summaries
- `resolve_file_output_dir` — resolves the absolute output directory path for a given file's relative path when locating source copies and dependency JSON files

**`doc_creator.py` → `codetwine/config/settings.py`** : Configuration constants
- `MAX_WORKERS` — controls the degree of parallelism (batch size) when processing files within a level
- `DOC_TEMPLATE_PATH` — filesystem path to the JSON template file that defines document sections and prompts
- `OUTPUT_LANGUAGE` — target natural language for LLM-generated content, injected into every section prompt
- `SUMMARY_MAX_CHARS` — maximum character limit enforced on generated summaries, injected into summary prompts

**`doc_creator.py` → `codetwine/llm/client.py`** : LLM invocation
- `LLMClient` — used to send assembled prompts to the configured LLM via `generate(prompt)` for both per-section generation and summary generation

**`doc_creator.py` → `codetwine/llm/__init__.py`** : LLM exception handling
- `ContextWindowExceededError` — caught during section generation to trigger the progressive fallback strategy (full callee context → compact callee context → no callee context)

---

### Dependents (modules that import this file)

**`codetwine/pipeline.py` → `doc_creator.py`** : Invokes `generate_all_docs` as the LLM documentation generation step within the overall pipeline. The pipeline passes the base output directory, the project-wide dependency list, an `LLMClient` instance, the worker count, and the set of changed files to `generate_all_docs`, which is called conditionally when `ENABLE_LLM_DOC` is true.

---

### Dependency Direction

All relationships in this file are **unidirectional**:

- `doc_creator.py → codetwine/utils/file_utils.py`: unidirectional; `file_utils.py` has no knowledge of `doc_creator.py`
- `doc_creator.py → codetwine/config/settings.py`: unidirectional; `settings.py` is a passive configuration source
- `doc_creator.py → codetwine/llm/client.py`: unidirectional; `LLMClient` is a general-purpose LLM wrapper with no dependency on `doc_creator.py`
- `doc_creator.py → codetwine/llm/__init__.py`: unidirectional; the `__init__.py` re-exports `ContextWindowExceededError` without any reference back to `doc_creator.py`
- `codetwine/pipeline.py → doc_creator.py`: unidirectional; `doc_creator.py` exposes `generate_all_docs` as a callable entry point but does not import from `pipeline.py`

## Data Flow

## Data Flow

### 1. Inputs

| Source | Format | Description |
|---|---|---|
| `DOC_TEMPLATE_PATH` (config) | JSON file | Template defining sections (`id`, `title`, `prompt`) and `summary_prompt` |
| `project_dep_list` (argument) | `list[dict]` | Per-file dependency records from `project_dependencies.json` |
| `base_output_dir` (argument) | `str` (path) | Root directory where per-file output subdirectories reside |
| `changed_files` (argument) | `set[str] \| None` | Relative paths of files changed since the last run; `None` means full regeneration |
| `max_workers` (argument) | `int` | Parallelism limit within each dependency level |
| `file_output_dir/{filename}` (file read) | Plain text | Copied source file for the target file |
| `file_output_dir/file_dependencies.json` (file read) | JSON | Per-file dependency record: `callee_usages`, `caller_usages`, `definitions` |
| `file_output_dir/doc.json` (file read, conditional) | JSON | Previously generated design document, read for reuse or MD→JSON sync |
| `file_output_dir/doc.md` (file read, conditional) | Markdown | Potentially hand-edited document; read when its mtime exceeds `doc.json` mtime |
| `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `MAX_WORKERS` (config) | scalar values | Control output language, summary length cap, and concurrency |
| `LLMClient` (argument) | object | Async interface to the language model |

---

### 2. Transformation Overview

**Stage 1 — Dependency-level ordering**

`project_dep_list` is consumed by `_topological_sort_by_level`, which builds a reverse adjacency graph and applies Kahn's BFS to produce `level_list: list[list[str]]`. Files with no dependencies appear at level 0; files that depend on others appear at higher levels. Circular remainders are appended as a final level.

**Stage 2 — Per-level fan-out (parallel)**

`generate_all_docs` iterates `level_list`. Within each level, files are batched into groups of `max_workers`. Each batch is launched as a set of `asyncio.Task` objects via `asyncio.gather`, so multiple files are processed concurrently. Results merge back into a shared `doc_map` dict after each batch completes, making summaries available for the next level.

**Stage 3 — Per-file reuse or regeneration decision**

For each file, `_needs_regeneration` checks:
1. `changed_files is None` → always regenerate.
2. File itself is in `changed_files` → regenerate.
3. Any callee appears in `changed_files` or `regenerated_files` → regenerate.

If regeneration is not needed, `_sync_md_to_json` is called first (Stage 3a below), then the existing `doc.json` is loaded and validated by `_is_doc_complete`; if complete, it is returned as-is.

**Stage 3a — MD→JSON sync (conditional)**

`_sync_md_to_json` compares file modification times. When `doc.md` is newer than `doc.json`, the MD text is parsed by `_parse_md_sections` using a regex that delimits on `## {known title}` lines. Differing section contents are written back into the JSON dict, and both files are re-saved via `_save_doc`.

**Stage 4 — Prompt assembly (per section)**

`_generate_file_doc` reads the source file and `file_dependencies.json`, then calls `_build_callee_context_summary` twice (full and compact) to produce callee context strings from `doc_map`. For C/C++ header files, `_build_implementation_context` locates a sibling implementation file and reads its text.

For each template section, `_build_section_prompt` assembles a multi-part string containing:
- Target file header + source code block
- (header files only) implementation source block
- `callee_usages` entries with optional `target_context` source blocks
- `caller_usages` entries with optional `usage_context` source blocks
- Callee design-document summaries from `doc_map`
- Section-specific instructions, output language directive, factual accuracy instruction

**Stage 5 — LLM generation with progressive fallback**

`_generate_section_with_fallback` attempts up to three prompt variants in order: full callee summary → compact callee summary (first 100 chars per entry) → no callee context. On `ContextWindowExceededError` it falls back to the next attempt; success returns the generated string.

After all sections, `_build_summary_prompt` assembles a prompt from all generated section contents plus the `summary_prompt` and `SUMMARY_MAX_CHARS` limit, and `_generate_summary` sends it to the LLM.

**Stage 6 — Output serialization**

`_save_doc` writes `doc.md` (Markdown with `## {title}` headings) and `doc.json` (structured dict) into `file_output_dir`. The completed doc dict is added to `doc_map` for downstream levels.

```
project_dep_list
      │
      ▼
_topological_sort_by_level → level_list
      │
      ▼  (per level, batched by max_workers)
┌─────────────────────────────────────────────────────┐
│  _needs_regeneration?                               │
│      │ No → _sync_md_to_json → load existing doc   │
│      │ Yes ↓                                        │
│  read source + file_dependencies.json               │
│      │                                              │
│  _build_callee_context_summary (full + compact)     │
│  _build_implementation_context (header files)       │
│      │                                              │
│  for each section:                                  │
│    _build_section_prompt → LLM → section text       │
│    (fallback: full → compact → no callee context)   │
│      │                                              │
│  _build_summary_prompt → LLM → summary text         │
│      │                                              │
│  _save_doc (doc.md + doc.json)                      │
│      │                                              │
│  doc_map[file_rel] = doc                            │
└─────────────────────────────────────────────────────┘
      │
      ▼
  doc_map (feeds callee context for next levels)
```

---

### 3. Outputs

| Output | Format | Description |
|---|---|---|
| `doc.md` per file | Markdown file | Human-readable design document with `## {section title}` headings and a `## Summary` section |
| `doc.json` per file | JSON file | Structured document dict (see Key Data Structures below) |
| `doc_map` (in-memory) | `dict[str, dict]` | Accumulated design documents for all processed files; consumed within the run for callee context |
| Console / log output | strings | Progress messages (`REUSE`, `OK`, `SKIP`, `INCOMPLETE`) and level/completion summaries |

---

### 4. Key Data Structures

**`project_dep_list` element** (input from caller)

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the file |
| `callees` | `list[str]` | Relative paths of files this file depends on |
| `callers` | `list[str]` | Relative paths of files that depend on this file |

---

**`file_deps`** — loaded from `file_dependencies.json`

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the target file (output format) |
| `callee_usages` | `list[dict]` | Symbols this file imports/calls; each entry has `name`, `from`, optional `target_context` |
| `caller_usages` | `list[dict]` | Symbols exported by this file that other files use; each entry has `name`, `file`, optional `usage_context` |

**`callee_usages` / `caller_usages` entry**

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name |
| `from` | `str` | (callee) Output-format path of the file defining the symbol |
| `file` | `str` | (caller) Output-format path of the file using the symbol |
| `target_context` | `str \| None` | (callee) Full source text of the dependency file |
| `usage_context` | `str \| None` | (caller) Source snippet at the usage site |

---

**`doc` / `doc_map` value** — the design document dict

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of the source file |
| `sections` | `list[dict]` | Ordered list of generated sections |
| `summary` | `str` | LLM-generated summary of the whole document |

**`sections` element**

| Field / Key | Type | Purpose |
|---|---|---|
| `id` | `str` | Section identifier from the template |
| `title` | `str` | Display title for the section |
| `content` | `str` | LLM-generated text for the section |

---

**`level_list`** — output of `_topological_sort_by_level`

| Field / Key | Type | Purpose |
|---|---|---|
| outer `list` index | `int` | Dependency depth level (0 = no dependencies) |
| inner `list[str]` | `list[str]` | Sorted file relative paths at that level |

---

**`template`** — loaded from `DOC_TEMPLATE_PATH`

| Field / Key | Type | Purpose |
|---|---|---|
| `sections` | `list[dict]` | Ordered section definitions; each has `id`, `title`, `prompt` |
| `summary_prompt` | `str` | Instruction text for the summary generation step |

## Error Handling

## Error Handling

### 1. Overall Strategy

The module adopts a **graceful degradation with logging-and-continue** approach. No single file failure is permitted to abort the overall document generation pipeline. Errors are logged at the appropriate severity level (`warning` or `error`), and processing advances to the next file or section. The one exception to pure continuation is context window overflow, which triggers a structured **retry-with-fallback** sequence before a section is skipped.

---

### 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ContextWindowExceededError` | Prompt exceeds the LLM's context window during section generation | Retried up to three times with progressively reduced context: full callee summaries → compact callee summaries (100 chars each) → no callee context | Yes (up to 3 attempts) | Section skipped if all attempts fail |
| Section generation failure (`None` result) | LLM returns `None` for a section after all fallback attempts | Logged at `warning`; section is omitted from the output | Yes (section skipped) | Missing section in the document; other sections unaffected |
| All sections fail | Every section in the template fails to generate | Logged at `error`; `_generate_file_doc` returns `None` | Yes (file skipped) | File produces no document; pipeline continues |
| Summary generation failure | Any exception during LLM call in `_generate_summary` | Caught broadly; logged at `warning`; `None` returned | Yes (summary omitted) | Document saved with empty summary string |
| Source file not found | No matching source file exists in `file_output_dir` | Logged at `warning`; `_generate_file_doc` returns `None` | Yes (file skipped) | No document for that file |
| `file_dependencies.json` missing | JSON file absent from the output directory | Logged at `warning`; `_generate_file_doc` returns `None` | Yes (file skipped) | No document for that file |
| Output directory missing | `resolve_file_output_dir` resolves to a non-existent directory | Logged at `warning`; `process_one` returns `(file_rel, None)` | Yes (file skipped) | No document for that file |
| `doc.json` read failure on reuse | `JSONDecodeError` or `OSError` when reading existing doc | Silently falls through to regeneration | Yes (regenerated) | Slightly increased LLM usage; no data loss |
| `_sync_md_to_json` JSON corruption | `JSONDecodeError` or `OSError` when loading `doc.json` for sync | Returns early without modifying files | Yes (sync skipped) | Manual MD edits not propagated to JSON |
| Task-level exception in `asyncio.gather` | Unhandled exception in any `process_one` coroutine | Caught via `return_exceptions=True`; logged at `error`; result skipped | Yes (file skipped) | File absent from `doc_map`; pipeline continues |

---

### 3. Design Notes

- **Fallback granularity is at the section level**, not the file level. This means a document can be partially complete—containing successfully generated sections while omitting those that exceeded context even after all fallback attempts—rather than being entirely absent.

- **The `asyncio.gather` call uses `return_exceptions=True`**, which prevents an exception in one parallel task from propagating and cancelling sibling tasks within the same batch. Each exception is inspected individually after all tasks complete.

- **Reuse-path failures are silent by design**: if an existing `doc.json` cannot be read, the code falls back to regeneration without logging, treating it as a non-critical degradation rather than an error condition.

- **Summary failure is isolated from section output**: because the summary is generated after all sections, its failure does not affect the sections already produced. The document is saved with an empty string for `summary` rather than being discarded.

- **No termination-level (`CRITICAL`) errors are raised** within this module. All failure paths lead to either skipping the affected unit (section, file) or substituting a safe default (empty string, `None`), ensuring the pipeline always runs to completion.

## Summary

Orchestrates LLM-based design document generation for all project files in topological dependency order. Public interface: `async generate_all_docs(base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int, changed_files: set[str]|None) -> None`. Consumes `project_dep_list` (list of `{file, callees, callers}` dicts) and `doc_map` (dict mapping file path → `{file, sections, summary}`); produces per-file `doc.json` and `doc.md` outputs and populates `doc_map` with generated summaries for downstream callee context injection.
