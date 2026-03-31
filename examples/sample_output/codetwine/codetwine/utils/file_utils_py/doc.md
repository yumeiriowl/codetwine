# Design Document: codetwine/utils/file_utils.py

## Overview & Purpose

## 1. Module Summary

Provides utility functions for converting between project-relative file paths and the copy-destination directory structure used throughout the CodeTwine pipeline, as well as computing and comparing file hashes to detect changes.

## 2. When to Use This Module

- **Converting a relative path to its output directory path**: Call `resolve_file_output_dir(base_output_dir, file_rel)` to obtain the absolute path of the output directory where a file's generated artifacts (e.g., `doc.json`, `file_dependencies.json`) are stored.
- **Building a copy-destination path from a relative path**: Call `rel_to_copy_path(rel_path)` when constructing the `"project_name/copy_path"` format strings used in dependency graph entries and output path references.
- **Recovering a project-relative path from a copy-destination path**: Call `copy_path_to_rel(copy_path)` when stripping the inserted `{stem}_{ext}` directory segment to get back the original relative path.
- **Recovering a project-relative path from a full output path**: Call `output_path_to_rel(output_path)` to remove the project name prefix and the `{stem}_{ext}` directory segment from a `"project_name/copy_destination_path"` string.
- **Detecting whether a file has changed**: Call `is_file_unchanged(source_path, copied_path)` to compare SHA-256 hashes of the original file and its copy, determining whether reprocessing is needed.
- **Hashing a single file**: Call `compute_file_hash(file_path)` to obtain the SHA-256 hex digest of a file directly.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to the copy-destination directory structure path (`{parent_dir}/{stem}_{ext}/{filename}`). |
| `copy_path_to_rel` | `copy_path: str` | `str` | Restores a copy-destination directory structure path to the original project-relative path by removing the inserted `{stem}_{ext}` directory. |
| `output_path_to_rel` | `output_path: str` | `str` | Strips the leading project name segment from a `"project_name/copy_destination_path"` string and delegates to `copy_path_to_rel` to recover the relative path. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a given file by joining `base_output_dir` with the parent portion of the file's copy-destination path. |
| `compute_file_hash` | `file_path: str` | `str` | Returns the SHA-256 hex digest of a file, reading in 8 KB chunks. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` if the copied file exists and its SHA-256 hash matches the source file's hash; returns `False` if the copy does not exist. |

## 4. Design Decisions

- **Extension-suffixed directory names to prevent collisions**: The copy-destination structure inserts a `{stem}_{ext}` directory between the parent directory and the filename (e.g., `src/utils.c` → `src/utils_c/utils.c`). This ensures that files sharing the same stem but different extensions (e.g., `utils.c` and `utils.h`) are placed in distinct output directories and do not overwrite each other's artifacts.
- **Symmetric path conversion**: `rel_to_copy_path` and `copy_path_to_rel` are designed as strict inverses of each other. `copy_path_to_rel` verifies the inverse by checking whether the second-to-last path segment equals `_to_dir_name(filename)` before removing it, making the conversion safe even for paths that were not originally produced by `rel_to_copy_path`.
- **Hash-based change detection over timestamps**: `is_file_unchanged` uses SHA-256 content hashing rather than file modification timestamps, ensuring reliable detection of actual content changes regardless of filesystem metadata.

## Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

**Signature:** `filename: str` → `str`

**Responsibility:** Converts a bare filename into a directory-name token by replacing the dot before the extension with an underscore. Files without extensions are returned unchanged.

**When to use:** Call when you need the canonical directory token for a given filename, specifically as a building block for path-conversion functions in this module.

**Design decisions:**
- Uses `os.path.splitext` to split stem and extension; the extension always begins with `.`, so `ext[1:]` strips that dot.
- No-extension files (e.g., `Makefile`) are returned as-is rather than receiving a trailing underscore.

**Constraints & edge cases:**
- Input must be a bare filename, not a path with directory components.
- Files with multiple dots (e.g., `foo.tar.gz`) split at the last dot only (`foo.tar` → stem, `.gz` → ext), yielding `foo.tar_gz`.

---

## `rel_to_copy_path(rel_path: str) -> str`

**Signature:** `rel_path: str` → `str`

**Responsibility:** Converts a project-relative file path into the copy-destination path structure used when source files are written to the output directory. Inserting a `{stem}_{ext}` directory prevents name collisions between files that share a stem but differ in extension.

**When to use:** Call when constructing the destination path for a source file that is being copied into the output directory, or when generating output-relative paths for dependency graphs and manifests.

**Design decisions:**
- The injected directory is always the immediate parent of the filename; existing parent directories are preserved verbatim.
- Top-level files (no parent directory) and nested files follow the same `{stem}_{ext}/{filename}` pattern, differing only in whether a leading parent prefix is prepended.

**Constraints & edge cases:**
- `rel_path` must use forward slashes or be a native OS path; only `os.path` functions are used for splitting, so behavior on Windows paths with backslashes depends on the OS.
- Files without extensions produce a directory token equal to the stem (via `_to_dir_name`).

---

## `copy_path_to_rel(copy_path: str) -> str`

**Signature:** `copy_path: str` → `str`

**Responsibility:** Reverses `rel_to_copy_path` by detecting and removing the inserted `{stem}_{ext}` directory segment, recovering the original project-relative path.

**When to use:** Call when reading a copy-destination path (e.g., from a stored JSON field or filesystem scan) and needing to recover the original source-relative path.

**Design decisions:**
- Normalizes backslashes to forward slashes before splitting, making it safe to process paths produced on Windows.
- The detection heuristic checks whether the second-to-last path component equals `_to_dir_name(filename)`. If the check fails, the path is returned unchanged, making the function safe to call on paths that were not produced by `rel_to_copy_path`.

**Constraints & edge cases:**
- Requires at least two path components; single-component inputs are returned as-is.
- If an existing project directory happens to match the `{stem}_{ext}` pattern, the function may incorrectly strip it.

---

## `output_path_to_rel(output_path: str) -> str`

**Signature:** `output_path: str` → `str`

**Responsibility:** Strips the leading project-name prefix from a `{project_name}/{copy_destination_path}` string and delegates to `copy_path_to_rel` to recover the source-relative path.

**When to use:** Call when resolving file identifiers stored in output JSON files (e.g., `callee_usages[].from`, `caller_usages[].file`) back to project-relative paths for display or further processing.

**Design decisions:**
- Splits on the first `/` only, so the project name may not itself contain a slash, but the remainder of the path may contain arbitrary depth.
- Falls back to returning the input unchanged when no `/` is present.

**Constraints & edge cases:**
- Assumes the first path component is always the project name; callers must not pass plain copy-destination paths (without a project-name prefix).

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

**Signature:**

| Parameter | Type | Description |
|---|---|---|
| `base_output_dir` | `str` | Absolute path of the root output directory |
| `file_rel` | `str` | Project-relative path of the source file |

→ `str` (absolute path of the file's output directory)

**Responsibility:** Computes the absolute output directory for a given source file by combining the base output directory with the parent portion of the file's copy-destination path.

**When to use:** Call before reading or writing any per-file output artifacts (copied source, `doc.json`, `file_dependencies.json`) to obtain the correct directory path.

**Design decisions:**
- Reuses `rel_to_copy_path` to derive the directory structure, ensuring consistency with all other path-conversion logic in the module.
- Takes `os.path.dirname` of the copy path to exclude the filename itself, yielding only the containing directory.

**Constraints & edge cases:**
- Does not create the directory; callers are responsible for calling `os.makedirs` if needed.
- `base_output_dir` should be an absolute path; relative base paths will produce relative results.

---

## `compute_file_hash(file_path: str) -> str`

**Signature:** `file_path: str` → `str`

**Responsibility:** Produces a SHA-256 hex digest of a file's contents, enabling content-based change detection without loading the entire file into memory.

**When to use:** Call when you need a stable, content-derived fingerprint for a file, typically as part of `is_file_unchanged`.

**Design decisions:**
- Reads the file in 8 KB chunks to bound memory usage regardless of file size.
- Returns a lowercase hexadecimal string (64 characters), consistent with `hashlib` defaults.

**Constraints & edge cases:**
- `file_path` must exist and be readable; the function does not handle missing files.
- Opens in binary mode, so hashes are byte-exact and platform-independent.

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

**Signature:**

| Parameter | Type | Description |
|---|---|---|
| `source_path` | `str` | Absolute path of the original source file |
| `copied_path` | `str` | Absolute path of the file copy in the output directory |

→ `bool` (`True` if both files have identical SHA-256 digests)

**Responsibility:** Determines whether a source file's content matches its previously copied counterpart, allowing the pipeline to skip re-processing unchanged files.

**When to use:** Call during incremental pipeline runs to identify which files require re-processing before committing to expensive operations such as LLM-based documentation generation.

**Design decisions:**
- A missing copy is treated as changed (`False`) rather than raising an error, simplifying caller logic for first-run or partially completed outputs.
- Delegates to `compute_file_hash` for both files; no timestamp or size pre-check is performed, so the result is always content-authoritative.

**Constraints & edge cases:**
- `source_path` must exist; no guard is applied to the source file's existence.
- Hash computation reads both files fully; performance scales with file size.

## Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `hashlib`) and defines utility functions consumed by other modules in the project.

---

## Dependents (modules that import this file)

The following project-internal modules depend on `codetwine/utils/file_utils_py/file_utils.py`:

- **`codetwine/output.py` → this module** : Uses `rel_to_copy_path` to construct `"project_name/copy_path"` formatted output paths; uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists; uses `output_path_to_rel` to convert output-format paths back to project-relative paths when resolving dependency relationships; uses `copy_path_to_rel` to strip the inserted `{stem}_{ext}` directory segment from paths when building Mermaid diagrams.

- **`codetwine/pipeline.py` → this module** : Uses `resolve_file_output_dir` to determine the output directory for each file during change detection and processing; uses `is_file_unchanged` to compare source files against their copies in the output directory to identify which files need reprocessing; uses `copy_path_to_rel` to convert copy-destination paths back to project-relative paths when normalizing internal path representations.

- **`codetwine/doc_creator.py` → this module** : Uses `output_path_to_rel` to convert output-format paths to human-readable relative paths when rendering callee and caller usage entries in documentation; uses `resolve_file_output_dir` to locate the output directory for a given file when loading its design document.

- **`codetwine/extractors/dependency_graph.py` → this module** : Uses `rel_to_copy_path` to construct `"project_name/copy_path"` formatted path strings for each file, caller, and callee entry when building the dependency graph's file info list.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output.py` → `this module` (one-way)
- `codetwine/pipeline.py` → `this module` (one-way)
- `codetwine/doc_creator.py` → `this module` (one-way)
- `codetwine/extractors/dependency_graph.py` → `this module` (one-way)

`file_utils.py` itself imports no project-internal modules, making it a **pure utility leaf module** in the dependency graph — consumed by multiple modules but depending on none of them in return.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `filename` | Caller argument | Plain string (e.g., `"settings.py"`, `"Makefile"`) |
| `rel_path` | Caller argument | POSIX-style relative path string from project root (e.g., `"repo_graphrag/llm/client.py"`) |
| `copy_path` | Caller argument | Copy-destination directory structure path string (e.g., `"repo_graphrag/llm/client_py/client.py"`) |
| `output_path` | Caller argument | `"project_name/copy_destination_path"` format string |
| `base_output_dir` | Caller argument | Absolute directory path string |
| `file_path` / `source_path` / `copied_path` | Caller argument | Absolute file path strings; `copied_path` may not exist on disk |

No configuration files or environment variables are read by this module. File system access is limited to `os.path.exists()` checks and binary file reads inside `compute_file_hash` and `is_file_unchanged`.

---

## 2. Transformation Overview

The module contains two independent transformation pipelines:

### Path Encoding Pipeline (`rel_path` → copy/output path)

```
rel_path (project-relative string)
    │
    ├─ _to_dir_name(filename)
    │       splits stem and extension via os.path.splitext;
    │       joins them with "_" (e.g. "client.py" → "client_py")
    │
    ├─ rel_to_copy_path
    │       inserts the dir_name segment between parent dir and filename
    │       e.g. "repo/llm/client.py" → "repo/llm/client_py/client.py"
    │
    └─ resolve_file_output_dir
            calls rel_to_copy_path, then strips the filename via os.path.dirname,
            and prepends base_output_dir via os.path.join
            e.g. base="/out", "src/foo.py" → "/out/src/foo_py"
```

### Path Decoding Pipeline (copy/output path → `rel_path`)

```
copy_path or output_path
    │
    ├─ copy_path_to_rel
    │       splits on "/"; checks if parts[-2] == _to_dir_name(parts[-1]);
    │       if so, removes that inserted segment
    │       e.g. "repo/llm/client_py/client.py" → "repo/llm/client.py"
    │
    └─ output_path_to_rel
            splits off the project-name prefix (first segment),
            then delegates the remainder to copy_path_to_rel
            e.g. "my_proj/src/foo_py/foo.py" → "src/foo.py"
```

### File Hash / Change-Detection Pipeline

```
file_path (absolute)
    │
    └─ compute_file_hash
            reads file in 8 KB binary chunks,
            feeds each chunk into a SHA-256 accumulator,
            returns hex digest string
                │
    is_file_unchanged(source_path, copied_path)
            checks existence of copied_path;
            calls compute_file_hash on both paths;
            returns bool (True = hashes match = file unchanged)
```

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `_to_dir_name` | `str` | Directory-safe name with extension dot replaced by `_` |
| `rel_to_copy_path` | `str` | Copy-destination path with inserted `{stem}_{ext}` directory segment |
| `copy_path_to_rel` | `str` | Original project-relative path with the inserted segment removed |
| `output_path_to_rel` | `str` | Project-relative path with both the project-name prefix and inserted segment removed |
| `resolve_file_output_dir` | `str` | Absolute path of the output directory for a given source file |
| `compute_file_hash` | `str` | SHA-256 hex digest of the file's binary content |
| `is_file_unchanged` | `bool` | `True` if source and copy have identical SHA-256 hashes; `False` if hashes differ or copy is absent |

No files are written and no side effects are produced by any function in this module.

---

## 4. Key Data Structures

This module operates entirely on primitive strings and booleans; no dataclasses, TypedDicts, or composite data structures are defined or returned. The intermediate values worth noting are:

### Path segment list (internal to `copy_path_to_rel`)

| Index | Type | Purpose |
|---|---|---|
| `parts[0...-2]` | `list[str]` | Leading path components (project dirs above the inserted segment) |
| `parts[-2]` | `str` | The inserted `{stem}_{ext}` directory, verified against `_to_dir_name(parts[-1])` |
| `parts[-1]` | `str` | The bare filename (e.g., `"client.py"`) |

### `os.path.splitext` decomposition (internal to `_to_dir_name`)

| Variable | Type | Purpose |
|---|---|---|
| `stem` | `str` | Filename without extension (e.g., `"client"`) |
| `ext` | `str` | Extension including leading dot, or empty string for extension-less files (e.g., `".py"` or `""`) |

## Error Handling

## 1. Overall Strategy

This file follows a **fail-fast** approach with minimal defensive checks. Most functions perform no internal exception handling and allow errors to propagate directly to callers. The only explicit defensive logic is a pre-condition guard in `is_file_unchanged`, which checks for the existence of the copied file before attempting a hash comparison, returning a safe default (`False`) rather than raising an exception. All other errors — such as I/O failures, missing files, or invalid path formats — are left unhandled and will raise exceptions naturally up the call stack.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `FileNotFoundError` / `OSError` | `compute_file_hash` is called with a non-existent or unreadable file path | No handling; exception propagates to caller | No | Caller (e.g., `pipeline.py`) receives the exception |
| Missing copied file | `is_file_unchanged` is called and the copied file does not exist at `copied_path` | Returns `False` (treated as "changed") without raising | Yes | The file is marked as changed and queued for reprocessing |
| Invalid or malformed path | `copy_path_to_rel` or `output_path_to_rel` receive a path that does not match the expected directory structure | Returns the input path unchanged; no exception raised | Yes | Caller receives the original path without transformation |
| Path with fewer than 2 parts | `copy_path_to_rel` or `output_path_to_rel` receive a single-segment path | Returns the input path unchanged | Yes | Caller receives the original path without transformation |

---

## 3. Design Notes

- **Separation of concerns**: Error handling responsibility is deliberately delegated to callers (e.g., `pipeline.py`, `output.py`). This file treats itself as a utility layer that does not own retry logic or logging.
- **Safe default for existence checks**: The guard in `is_file_unchanged` reflects the specific semantic that a missing copy is a valid, expected state (first run or deleted output), not an error condition. Returning `False` encodes the conservative assumption that the file must be reprocessed.
- **Silent pass-through for unrecognized paths**: Path conversion functions (`copy_path_to_rel`, `output_path_to_rel`) return the input unchanged when the structure does not match expectations, avoiding crashes in callers that may encounter paths outside the expected format while iterating over file lists.

## Summary

**file_utils.py** converts project-relative paths to/from copy-destination paths and detects file changes via hashing.

**Public functions:**
- `rel_to_copy_path(rel_path: str) → str`
- `copy_path_to_rel(copy_path: str) → str`
- `output_path_to_rel(output_path: str) → str`
- `resolve_file_output_dir(base_output_dir: str, file_rel: str) → str`
- `compute_file_hash(file_path: str) → str`
- `is_file_unchanged(source_path: str, copied_path: str) → bool`

**Key data:** All inputs/outputs are primitive `str` or `bool`. Copy-destination paths follow `{parent}/{stem}_{ext}/{filename}` format; output paths follow `{project_name}/{copy_destination_path}`.
