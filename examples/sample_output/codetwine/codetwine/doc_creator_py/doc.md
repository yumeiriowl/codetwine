# Design Document: codetwine/doc_creator.py

# Overview & Purpose

## 1. Module Summary

Generates structured LLM-based design documents for every source file in a project by assembling context-aware prompts from source code, dependency graphs, and prior design document summaries, then saving the results as both Markdown and JSON.

## 2. When to Use This Module

- **Generating design documents for an entire project**: Call `generate_all_docs(base_output_dir, project_dep_list, llm_client)` from `codetwine/pipeline.py` after dependency analysis is complete. It returns when all documents have been written to disk as `doc.md` and `doc.json` in each file's output directory.
- **Incremental regeneration after code changes**: Pass a `changed_files` set to `generate_all_docs`. Files that have not changed and whose dependencies have not changed reuse their existing `doc.json`, skipping LLM calls entirely.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|------|-----------------|-------------|----------------|
| `async generate_all_docs` | `base_output_dir: str`, `project_dep_list: list`, `llm_client: LLMClient`, `max_workers: int`, `changed_files: set[str] \| None` | `None` | Entry point: topologically sorts all project files by dependency depth, generates design documents level by level with bounded parallelism, and saves each result as `doc.md` + `doc.json`. |

## 4. Design Decisions

- **Topological level-by-level processing**: Files are sorted into dependency depth levels via `_topological_sort_by_level` (Kahn's algorithm on the reverse graph). Documents at level N are generated only after all level N−1 documents are complete, so each file's prompt can include accurate design document summaries of its dependencies as context. Files with circular dependencies are collected into a final level with a warning rather than failing outright.

- **Progressive context fallback on context window exceeded**: When the LLM rejects a prompt due to context length, `_generate_section_with_fallback` retries the same section up to three times with progressively reduced callee context: full dependency summaries → 100-character-truncated summaries → no callee context at all. This avoids hard failures on large files without requiring the caller to pre-estimate token counts.

- **MD-to-JSON sync for manual edits**: Before reusing a cached `doc.json`, `_sync_md_to_json` checks whether `doc.md` has a newer modification timestamp. If it does, manual edits made directly in Markdown are parsed and written back into the JSON so that the JSON remains the authoritative source for downstream consumers.

- **Completeness gate on cached documents**: `_is_doc_complete` verifies that a cached `doc.json` contains exactly the section IDs defined in the current template and a non-empty summary before accepting it as reusable. An incomplete cached document triggers full regeneration rather than a partial update.

- **Callee context propagation via `doc_map`**: Rather than re-reading previously written JSON files from disk, completed design document dicts are held in the in-process `doc_map` dict and passed directly to each subsequent file's prompt builder, avoiding redundant I/O within a single run.

# Definition Design Specifications

---

## Module-Level Constants

| Constant | Type | Purpose |
|---|---|---|
| `HEADER_TARGET_FILE` | `str` | Prompt heading template indicating the target file path. |
| `HEADER_SOURCE_CODE` | `str` | Prompt section heading for source code block. |
| `HEADER_CALLEE_USAGES` | `str` | Prompt section heading for dependency symbols used by the file. |
| `CALLEE_USAGES_SCHEMA_NOTE` | `str` | Explanation of the callee usage schema and attached source code semantics. |
| `CALLEE_SOURCE_CODE_LABEL` | `str` | Label preceding the dependency source code block in prompts. |
| `HEADER_CALLER_USAGES` | `str` | Prompt section heading for dependent files that reference this file. |
| `CALLER_USAGES_SCHEMA_NOTE` | `str` | Explanation of the caller usage schema. |
| `CALLER_SOURCE_CODE_LABEL` | `str` | Label preceding the usage-location source code block in prompts. |
| `HEADER_CALLEE_CONTEXT` | `str` | Prompt section heading for design document summaries of dependency files. |
| `CALLEE_CONTEXT_NOTE` | `str` | Instructions to the LLM on how to use the dependency summaries. |
| `HEADER_REQUEST` | `str` | Prompt section heading marking the LLM instruction block. |
| `SECTION_REQUEST_TEMPLATE` | `str` | Template for the per-section LLM instruction; `{title}` is substituted. |
| `OUTPUT_LANGUAGE_INSTRUCTION` | `str` | Instruction appended to every prompt specifying the output language; `{language}` is substituted. |
| `FACTUAL_ACCURACY_INSTRUCTION` | `str` | Instruction prohibiting speculation or contradiction of source code. |
| `HEADER_IMPL_CONTEXT` | `str` | Prompt section heading for implementation file context (header files only). |
| `IMPL_CONTEXT_NOTE` | `str` | Instructions explaining the implementation file's role in the prompt. |
| `HEADER_DOC_CONTENT` | `str` | Prompt section heading used in summary generation prompts. |
| `SUMMARY_CHAR_LIMIT` | `str` | Character-limit note template appended to summary prompts; `{max_chars}` is substituted. |
| `_HEADER_EXTENSIONS` | `set[str]` | C/C++ header file extensions: `.h`, `.hpp`, `.hh`, `.hxx`. |
| `_IMPL_EXTENSIONS` | `list[str]` | Ordered list of implementation file extensions to search for: `cpp`, `c`, `cc`, `cxx`. |

---

## Functions

---

### `_topological_sort_by_level`

**Signature:**
```
_topological_sort_by_level(project_dep_list: list[dict]) -> list[list[str]]
```
- `project_dep_list`: List of dicts, each with keys `"file"` (str), `"callers"` (list), `"callees"` (list).
- Returns: A list of levels, where each level is a sorted list of file paths. Index 0 = files with no dependencies.

**Responsibility:** Determines the correct processing order for design document generation by grouping files into dependency-depth levels so that a file's dependencies are always processed before the file itself.

**When to use:** Called once per run inside `generate_all_docs` to establish the leveled processing schedule.

**Design decisions:**
- Operates on a **reverse adjacency graph** (callers pointing to callees) with a Kahn's BFS algorithm to assign levels by dependency depth rather than reverse topological order.
- Files not reachable by BFS due to circular dependencies are appended as a final extra level with a warning, rather than raising an error, ensuring graceful degradation.
- All files appearing in any `callee` list are included as nodes even if they have no own entry in `project_dep_list`.

**Constraints & edge cases:**
- Circular dependencies are tolerated; involved files are grouped at the last level.
- Files listed only as callees (with no own entry) are still assigned levels.
- Level order within a level is alphabetically sorted for determinism.

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
- `section`: One section definition dict from the template (keys: `id`, `title`, `prompt`).
- `file_deps`: Parsed content of `file_dependencies.json` for the target file.
- `callee_context`: Pre-built text of dependency design document summaries; may be empty.
- `implementation_context`: Source code of a paired `.cpp`/`.c` file; empty for non-header files.
- Returns: A complete LLM prompt string.

**Responsibility:** Assembles the full textual prompt for generating one design document section by composing all available context (source code, dependencies, dependents, summaries, header/impl pairing) and appending the section-specific instruction.

**When to use:** Called once per template section per file inside `_generate_section_with_fallback`, with varying `callee_context` on each fallback attempt.

**Design decisions:**
- The `implementation_context` block is included only when non-empty, making the function applicable to both header and non-header files without branching at the call site.
- `callee_usages` and `caller_usages` sections are each omitted entirely when the respective list is empty, keeping prompts concise.
- `output_path_to_rel` is applied to all file paths before embedding them in the prompt to show source-relative paths rather than output-directory paths.
- `FACTUAL_ACCURACY_INSTRUCTION` and `OUTPUT_LANGUAGE_INSTRUCTION` are unconditionally appended last.

**Constraints & edge cases:**
- `file_deps.get('file', 'unknown')` is used defensively; a missing `file` key results in `'unknown'` in the prompt header.
- `target_context` and `usage_context` per usage entry are optional; blocks are skipped if absent.

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
- `section_contents`: List of already-generated section dicts, each with keys `id`, `title`, `content`.
- Returns: A complete LLM prompt string for summary generation.

**Responsibility:** Assembles the prompt used to generate a concise summary of the entire design document by providing all section content and a character-limit constraint.

**When to use:** Called once per file inside `_generate_summary` after all sections have been generated.

**Constraints & edge cases:**
- Does not append `FACTUAL_ACCURACY_INSTRUCTION`; only `OUTPUT_LANGUAGE_INSTRUCTION` and the character limit are appended.

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
- `doc_map`: Maps source-relative file path → design document dict with at least a `"summary"` key.
- `compact`: If `True`, each summary is truncated to the first 100 characters followed by `"..."`.
- Returns: A single string of bullet-point lines, one per dependency file that has a summary. Empty string if none found.

**Responsibility:** Builds the callee-context block injected into section prompts by extracting and optionally compressing design document summaries of dependency files.

**When to use:** Called twice per file inside `_generate_file_doc`—once for the full version and once for the compact version—to support fallback strategies in `_generate_section_with_fallback`.

**Design decisions:**
- Dependency files are deduplicated via a set before iteration, preventing duplicate summary entries when a callee appears in multiple `callee_usages` entries.
- `output_path_to_rel` is applied to callee paths when looking up `doc_map` keys, reconciling the output-path format stored in `callee_usages` with the source-relative keys in `doc_map`.

**Constraints & edge cases:**
- Files present in `callee_usages` but absent from `doc_map` (not yet processed or generation failed) are silently skipped.
- Files with an empty `"summary"` field are also skipped.

---

### `_build_implementation_context`

**Signature:**
```
_build_implementation_context(
    file_rel: str,
    file_output_dir: str,
) -> str
```
- Returns: Full source code text of the discovered implementation file, or empty string.

**Responsibility:** Provides the source code of the paired implementation file for C/C++ header files so that prompts for headers can include concrete implementation details.

**When to use:** Called once per file inside `_generate_file_doc`; for non-header files it returns immediately without filesystem access.

**Design decisions:**
- Extensions are searched in the fixed order defined by `_IMPL_EXTENSIONS`; the first match wins.
- The implementation file is located by looking one directory level above `file_output_dir` (i.e., sibling output directories share a common parent), then constructing the candidate path as `{base_dir}/{stem}_{impl_ext}/{stem}.{impl_ext}`.

**Constraints & edge cases:**
- Returns empty string for any file whose extension is not in `_HEADER_EXTENSIONS`.
- Returns empty string if no matching implementation file exists on disk.

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
- Returns: Generated section text as a string, or `None` if all three attempts fail.

**Responsibility:** Shields section generation from context-window failures by retrying with progressively smaller prompts before giving up.

**When to use:** Called once per template section per file inside `_generate_file_doc`; it awaits `llm_client.generate` and is itself awaited sequentially per section.

**Design decisions:** Three attempts are tried in order:

| Attempt | Callee context used |
|---|---|
| 1 | Full callee summary (`callee_context_summary`) |
| 2 | Compact callee summary (`callee_context_compact`, 100-char truncation) |
| 3 | No callee context (empty string) |

- Only `ContextWindowExceededError` triggers a fallback; other exceptions propagate normally from `llm_client.generate`.
- A warning is logged on each fallback step identifying the file and section.

**Constraints & edge cases:**
- Returns `None` only when all three attempts raise `ContextWindowExceededError` or `generate` returns `None` on every attempt.

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
- Returns: Design document dict with keys `file` (str), `sections` (list of `{id, title, content}`), `summary` (str); or `None` on complete failure.

**Responsibility:** Orchestrates all LLM calls required to produce one complete design document: reading inputs, generating each section sequentially, and generating the summary.

**When to use:** Called once per file inside `process_one` (the inner coroutine of `generate_all_docs`) when regeneration is required.

**Design decisions:**
- Sections are generated **sequentially** (one `await` per section) rather than in parallel to avoid issuing too many concurrent LLM calls within a single file.
- If any individual section fails completely, a warning is logged and that section is omitted rather than aborting the whole document.
- Returns `None` only when zero sections were successfully generated.

**Constraints & edge cases:**
- Requires both a source file (discovered via `_find_source_file`) and a `file_dependencies.json` to be present in `file_output_dir`; returns `None` with a warning if either is missing.

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
- Returns: Summary text string, or `None` on any exception.

**Responsibility:** Generates a concise summary of the entire design document by sending all section content to the LLM.

**When to use:** Called once per file at the end of `_generate_file_doc`, after all sections are collected.

**Constraints & edge cases:**
- Any exception (not just `ContextWindowExceededError`) is caught and logged; the function returns `None` rather than propagating.
- `SUMMARY_MAX_CHARS` from settings is used as the character-limit value in the prompt.

---

### `_find_source_file`

**Signature:**
```
_find_source_file(output_dir: str, file_rel: str) -> str | None
```
- Returns: Absolute path to the source file copy, or `None` if not found.

**Responsibility:** Locates the copied source file within the output directory using only the base filename.

**When to use:** Called inside `_generate_file_doc` to obtain the path to read source code from.

**Constraints & edge cases:**
- Only the base filename (not subdirectory structure) is used for the lookup; files with identical names in different subdirectories cannot be distinguished.

---

### `_save_doc`

**Signature:**
```
_save_doc(doc: dict, output_dir: str) -> None
```
- `doc`: Design document dict with keys `file`, `sections` (list of `{id, title, content}`), and optional `summary`.

**Responsibility:** Persists a completed design document to disk in both Markdown (human-readable) and JSON (machine-readable) formats.

**When to use:** Called inside `process_one` immediately after a successful `_generate_file_doc` call.

**Design decisions:**
- Markdown is written **before** JSON so that JSON always has an equal or newer `mtime`, which is the invariant used by `_sync_md_to_json` to detect manual edits.
- A regex substitution strips any duplicate `# {title}` heading that the LLM may have prepended to section content before writing.
- The summary is appended as a `# Summary` section at the end of the Markdown file only when present.

**Constraints & edge cases:**
- Modifies `doc["sections"][*]["content"]` in-place during the heading-strip step, affecting the caller's dict.

---

### `_parse_md_sections`

**Signature:**
```
_parse_md_sections(md_text: str, section_titles: list[str]) -> dict[str, str]
```
- `section_titles`: Titles used as delimiters; must match the `# {title}` pattern exactly.
- Returns: Dict mapping title string → content text (stripped). Titles not found are absent from the result.

**Responsibility:** Extracts section content from a Markdown design document by splitting on known `# {title}` headings.

**When to use:** Called inside `_sync_md_to_json` to parse the current state of `doc.md` for comparison with `doc.json`.

**Design decisions:**
- Uses a compiled regex that matches `# {title}` only at line boundaries (`re.MULTILINE`) and only for the exact set of known titles via an alternation pattern.
- Content boundaries are determined by adjacent match positions; the last section extends to end-of-file.

**Constraints & edge cases:**
- Only `#`-level headings (not `##` or deeper) are used as delimiters.
- Sections whose heading does not appear in `section_titles` are not parsed.

---

### `_sync_md_to_json`

**Signature:**
```
_sync_md_to_json(output_dir: str) -> None
```

**Responsibility:** Propagates manual edits made to `doc.md` back into `doc.json` and re-saves both files in a consistent state.

**When to use:** Called inside `process_one` for files that do not require regeneration, before attempting to reuse an existing `doc.json`.

**Design decisions:**
- Sync is skipped entirely when `doc.md` is not newer than `doc.json` (by `mtime`), making the operation a no-op for the common case.
- A section in `doc.md` is only applied if the **next** section (in JSON order) is also present in the parsed MD; this guards against cases where an intermediate heading is missing and boundaries would be misidentified.
- After updating JSON, `_save_doc` is called to regenerate `doc.md` from the updated JSON, ensuring both files are consistent and resetting `mtime` ordering.

**Constraints & edge cases:**
- Silently returns if either file is missing or `doc.json` is unreadable.
- Sections present in JSON but absent from MD are left unchanged.

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
- `project_dep_list`: Same structure as the output of `save_project_dependencies` (list of `{file, callers, callees}` dicts).
- `changed_files`: Set of source-relative file paths. `None` means full regeneration; an empty set means no file changes.

**Responsibility:** Entry point for the design document generation pipeline; processes all project files in dependency-level order, reusing existing documents where possible and parallelizing within each level.

**When to use:** Called once per pipeline run from `codetwine/pipeline.py` when `ENABLE_LLM_DOC` is set.

**Design decisions:**
- Files within each level are processed in **parallel batches** of up to `max_workers` tasks using `asyncio.gather`; levels themselves are processed **sequentially** to ensure dependency documents are in `doc_map` before dependent files are processed.
- `doc_map` accumulates only successfully generated or reused documents; it is shared across levels but mutated only after each level's batch completes.
- `regenerated_files` tracks files regenerated in the current run so that callers of a regenerated file are also marked for regeneration, even if the caller file itself is not in `changed_files`.
- `_is_doc_complete` validates that a reused document contains exactly the expected section IDs from the current template and a non-empty summary; incomplete documents are regenerated.
- `asyncio.gather(..., return_exceptions=True)` is used so one task's exception does not cancel sibling tasks; exceptions are logged individually.

**Inner functions:**

| Name | Signature | Purpose |
|---|---|---|
| `_needs_regeneration` | `(file_rel: str) -> bool` | Determines whether a file requires regeneration based on `changed_files` and `regenerated_files`. |
| `_is_doc_complete` | `(doc: dict) -> bool` | Validates that a cached document has all template sections and a summary. |
| `process_one` *(async)* | `(file_rel: str) -> tuple[str, dict \| None]` | Coordinates reuse-or-regenerate logic for a single file; returns `(file_rel, doc)`. |

**Constraints & edge cases:**
- Files whose output directory does not exist on disk are skipped with a warning.
- If `doc.json` is unreadable (corrupt JSON or OS error), the file falls back to regeneration silently.
- `changed_files=None` forces regeneration of every file regardless of existing documents.

# Dependency Description

## Dependencies (modules this file imports)

**`codetwine/doc_creator.py` → `codetwine/utils/file_utils.py`**
Symbols: `output_path_to_rel`, `resolve_file_output_dir`
Purpose: Converts output-format paths back to source-relative paths (used when building prompts and looking up `doc_map`), and resolves the absolute output directory path for a given file's relative path (used when locating source copies and dependency JSON files).

**`codetwine/doc_creator.py` → `codetwine/config/settings.py`**
Symbols: `MAX_WORKERS`, `DOC_TEMPLATE_PATH`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`
Purpose: Reads runtime configuration constants — the default parallelism level for batch processing, the file system path to the document template, the target output language for LLM-generated text, and the maximum character limit for generated summaries.

**`codetwine/doc_creator.py` → `codetwine/llm/client.py`**
Symbol: `LLMClient`
Purpose: Sends assembled prompts to the LLM and retrieves generated text for each document section and for the per-file summary.

**`codetwine/doc_creator.py` → `codetwine/llm/__init__.py`**
Symbol: `ContextWindowExceededError`
Purpose: Catches this exception during section generation to implement the progressive fallback strategy (full callee context → compact callee context → no callee context).

---

## Dependents (modules that import this file)

**`codetwine/pipeline.py` → `codetwine/doc_creator.py`**
Symbol: `generate_all_docs`
Purpose: The pipeline module calls `generate_all_docs` as the final stage of the overall processing pipeline to produce LLM-based design documents for all analysed project files, passing in the base output directory, the project-level dependency list, the LLM client, the worker count, and the set of changed files.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/doc_creator.py` → `codetwine/utils/file_utils.py` (one-way; `file_utils` does not import from `doc_creator`)
- `codetwine/doc_creator.py` → `codetwine/config/settings.py` (one-way; `settings` does not import from `doc_creator`)
- `codetwine/doc_creator.py` → `codetwine/llm/client.py` (one-way; `client` does not import from `doc_creator`)
- `codetwine/doc_creator.py` → `codetwine/llm/__init__.py` (one-way; `llm/__init__` does not import from `doc_creator`)
- `codetwine/pipeline.py` → `codetwine/doc_creator.py` (one-way; `doc_creator` does not import from `pipeline`)

# Data Flow

### 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `base_output_dir` (arg) | `str` | Root directory containing per-file output subdirectories |
| `project_dep_list` (arg) | `list[dict]` | Project-wide dependency list from `project_dependencies.json` |
| `llm_client` (arg) | `LLMClient` | Async LLM API client used for text generation |
| `max_workers` (arg / config) | `int` | Max parallel tasks per dependency level (default: `MAX_WORKERS`) |
| `changed_files` (arg) | `set[str] \| None` | Relative paths of files changed since last run; `None` = full rebuild |
| `DOC_TEMPLATE_PATH` (config) | JSON file path | Template defining section definitions and summary prompt |
| `{file_output_dir}/file_dependencies.json` (file read) | JSON | Per-file callee/caller usage data and symbol definitions |
| `{file_output_dir}/{filename}` (file read) | plain text | Copied source code of the target file |
| `{file_output_dir}/doc.json` (file read, conditional) | JSON | Previously generated design document (reuse path) |
| `{file_output_dir}/doc.md` (file read, conditional) | Markdown | Potentially hand-edited design document (MD→JSON sync path) |
| `{stem}_{impl_ext}/{stem}.{impl_ext}` (file read, conditional) | plain text | Implementation file source code for C/C++ header files |

---

### 2. Transformation Overview

```
project_dep_list
       │
       ▼
_topological_sort_by_level()
       │  Kahn's BFS on the reverse dependency graph
       │  produces level_list: list[list[str]]
       ▼
For each level (outermost loop, sequential):
  For each batch of ≤ max_workers files (inner loop, parallel):
    ┌─────────────────────────────────────────────────────┐
    │ process_one(file_rel)                               │
    │                                                     │
    │  ① _needs_regeneration()                           │
    │     • changed_files / callee membership check      │
    │     │                                              │
    │     ├─ NO → _sync_md_to_json() → load doc.json    │
    │     │       → _is_doc_complete() → REUSE           │
    │     │                                              │
    │     └─ YES → _generate_file_doc()                  │
    │               │                                    │
    │               ├─ read source file (plain text)     │
    │               ├─ read file_dependencies.json       │
    │               ├─ _build_callee_context_summary()   │
    │               │    doc_map[callee]["summary"]       │
    │               │    → bullet-list string            │
    │               ├─ _build_implementation_context()   │
    │               │    (header files only)             │
    │               │                                    │
    │               └─ For each template section:        │
    │                   _generate_section_with_fallback()│
    │                     attempt 1: full callee context │
    │                     attempt 2: compact context     │
    │                     attempt 3: no callee context   │
    │                     → _build_section_prompt()      │
    │                     → llm_client.generate(prompt)  │
    │                     → section content str          │
    │                                                    │
    │               _generate_summary()                  │
    │                 → _build_summary_prompt()          │
    │                 → llm_client.generate(prompt)      │
    │                 → summary str                      │
    │                                                    │
    │               → doc dict {file, sections, summary} │
    │               → _save_doc() → doc.md + doc.json   │
    └─────────────────────────────────────────────────────┘
       │
       ▼
  doc_map[file_rel] = doc        ← feeds callee context for next levels
  regenerated_files.add(file_rel) ← propagates regeneration to callers
```

**Fan-out / merge:** Within each level, all files are independent and processed concurrently via `asyncio.gather`. Results are collected and merged sequentially into `doc_map` before the next level begins, ensuring that downstream files (higher levels) always see complete callee summaries.

**Fallback cascade in `_generate_section_with_fallback`:** If the LLM raises `ContextWindowExceededError`, the prompt is rebuilt with progressively less callee context (full → 100-char truncated → none) and retried without propagating the error to the caller.

**MD→JSON sync path:** Before reusing an existing `doc.json`, `_sync_md_to_json` compares file modification times. If `doc.md` is newer, it parses the Markdown with `_parse_md_sections`, diffs each section against the JSON, and overwrites the JSON. This path only fires in the reuse branch (`_needs_regeneration` returns `False`).

---

### 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| `{file_output_dir}/doc.md` | Markdown file | Human-readable design document with one `# {title}` heading per section plus a `# Summary` section |
| `{file_output_dir}/doc.json` | JSON file | Machine-readable design document dict (see Key Data Structures) |
| `doc_map` (in-memory, internal) | `dict[str, dict]` | Accumulates all generated docs; consumed within the same run as callee context |
| `regenerated_files` (in-memory, internal) | `set[str]` | Tracks which files were rebuilt; used to propagate regeneration necessity to dependent files |
| Console / log output | text | Per-file status lines (`REUSE`, `OK`, `SKIP`, `INCOMPLETE`) and level progress messages |

---

### 4. Key Data Structures

#### `project_dep_list` element (input)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | Project-relative path of the file |
| `callees` | `list[str]` | Paths of files this file depends on |
| `callers` | `list[str]` | Paths of files that depend on this file |

#### `file_dependencies.json` / `file_deps` dict (per-file input)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | Output-format path of the target file |
| `callee_usages` | `list[dict]` | Symbols this file imports/calls from other files |
| `caller_usages` | `list[dict]` | Symbols other files import/call from this file |

##### `callee_usages` element

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `name` | `str` | Symbol name being used |
| `from` | `str` | Output-format path of the file defining the symbol |
| `target_context` | `str \| None` | Full source code of the dependency file (optional) |

##### `caller_usages` element

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `name` | `str` | Symbol name being used |
| `file` | `str` | Output-format path of the file that uses the symbol |
| `usage_context` | `str \| None` | Source code snippet from the calling file (optional) |

#### Design document dict (output, stored as `doc.json`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `file` | `str` | Project-relative path of the source file |
| `sections` | `list[dict]` | Ordered list of generated section dicts |
| `summary` | `str` | Short summary of the entire document (≤ `SUMMARY_MAX_CHARS` chars) |

##### `sections` element

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `id` | `str` | Section identifier matching the template |
| `title` | `str` | Human-readable section heading |
| `content` | `str` | LLM-generated text for this section |

#### `doc_map` (in-memory accumulator)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| key | `str` | Project-relative file path |
| value | `dict` | Full design document dict (`file`, `sections`, `summary`) |

#### `level_list` (intermediate, from topological sort)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| outer index | `int` | Dependency depth level (0 = no dependencies) |
| element | `list[str]` | Sorted file relative paths at that level, safe to process in parallel |

# Error Handling

## 1. Overall Strategy

The module follows a **graceful degradation with logging-and-continue** strategy. No single file failure is allowed to abort the entire document generation pipeline. Errors are caught at the finest granularity available (individual LLM calls, individual sections, individual files), logged at the appropriate severity level, and processing continues with the remaining work. Where retries are meaningful (context window overflow), a progressive fallback sequence is applied before giving up on a unit of work.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ContextWindowExceededError` on a section | Prompt for a section exceeds the model's context window | Retried up to three times with progressively reduced callee context: (1) full summary, (2) compact 100-char summary, (3) no callee context | Yes | Section may be generated with less dependency context |
| All fallback attempts fail for a section | `ContextWindowExceededError` persists after all three fallback attempts | Section is skipped; warning logged | Yes (section skipped) | That section is absent from the final document |
| All sections fail for a file | Every section generation returns `None` | File document skipped entirely; error logged | Yes (file skipped) | No document produced for that file; `doc_map` entry absent |
| Summary generation failure | Any exception during summary LLM call | Warning logged; summary set to empty string `""` | Yes | Document saved without a summary |
| `asyncio.gather` task raises an exception | Unhandled exception in `process_one` coroutine | Exception caught by the gather result loop; error logged; tuple result skipped | Yes | That file produces no `doc_map` entry |
| Source file not found in output directory | `_find_source_file` returns `None` (file absent) | Warning logged; `_generate_file_doc` returns `None` | Yes (file skipped) | No document for that file |
| `file_dependencies.json` missing | File absent from the expected output directory | Warning logged; `_generate_file_doc` returns `None` | Yes (file skipped) | No document for that file |
| `doc.json` unreadable or malformed JSON | `json.JSONDecodeError` or `OSError` when loading existing doc | Silently falls through to full regeneration | Yes (fallback to regeneration) | LLM call is made instead of reusing cached doc |
| `doc.json` or `doc.md` unreadable in sync | `json.JSONDecodeError` or `OSError` in `_sync_md_to_json` | Function returns early without changes | Yes | MD→JSON sync skipped for that file |
| Output directory missing | `resolve_file_output_dir` returns a path that is not a directory | Warning logged; `process_one` returns `(file_rel, None)` | Yes (file skipped) | No document for that file |
| Circular dependency in project graph | Kahn's algorithm leaves unprocessed files | Remaining files appended to the last processing level; warning logged | Yes | Those files are processed last without guaranteed correct ordering |

---

## 3. Design Notes

**Progressive context fallback** is the core resilience mechanism for LLM calls. Because context window limits are an inherent constraint of the underlying model, the design avoids a hard failure and instead sacrifices progressively less critical context (dependency design document summaries) before ultimately generating a section with no callee context at all. This ensures that even large files with many dependencies can still produce a document.

**Coarse-to-fine failure granularity** is deliberate: a failure at the section level does not fail the file, and a failure at the file level does not fail the pipeline. This is appropriate for a batch documentation generation workload where partial output is more valuable than no output.

**Silent fallback to regeneration** on JSON read errors (rather than propagating the exception) reflects a conservative assumption: if the cached state cannot be trusted, re-generating from the LLM is always a safe default.

**`asyncio.gather` exception isolation** ensures that a coroutine crash in one file's processing task does not cancel sibling tasks within the same batch, preserving parallelism and overall throughput.

# Summary

Generates LLM-based design documents for all project source files. Public entry point: `generate_all_docs(base_output_dir: str, project_dep_list: list, llm_client: LLMClient, max_workers: int, changed_files: set[str]|None) -> None`. Consumes `project_dep_list` (list of `{file, callers, callees}` dicts) and per-file `file_dependencies.json`. Produces `doc.md` and `doc.json` per file; `doc.json` contains `{file: str, sections: list[{id, title, content}], summary: str}`. Maintains in-memory `doc_map: dict[str, dict]` to propagate dependency summaries across levels.
