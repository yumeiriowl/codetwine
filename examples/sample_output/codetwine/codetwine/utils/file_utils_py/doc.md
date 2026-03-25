# Design Document: codetwine/utils/file_utils.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Provide utility functions for converting between project-relative file paths and copy-destination directory structure paths, resolving output directories, and detecting whether a source file has changed since it was last copied.

## 2. When to Use This Module

- **Converting a relative path to a copy-destination path**: Call `rel_to_copy_path(rel_path)` to obtain the path string used when writing a source file into the output directory (e.g., `"src/foo.py"` → `"src/foo_py/foo.py"`).
- **Recovering the original relative path from a copy-destination path**: Call `copy_path_to_rel(copy_path)` to strip the inserted `{stem}_{ext}` directory segment and restore the project-relative path.
- **Recovering the original relative path from a full output path**: Call `output_path_to_rel(output_path)` to strip the leading project-name segment and then recover the project-relative path; used when reading path strings stored inside JSON dependency files.
- **Resolving the absolute output directory for a file**: Call `resolve_file_output_dir(base_output_dir, file_rel)` to get the absolute directory path where a file's generated artifacts (e.g., `doc.json`, `file_dependencies.json`) are written.
- **Detecting whether a source file needs reprocessing**: Call `is_file_unchanged(source_path, copied_path)` to compare SHA-256 hashes of the original and its copy, allowing callers to skip unchanged files.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to the copy-destination directory structure path (`{parent}/{stem}_{ext}/{filename}`). |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; removes the inserted `{stem}_{ext}` directory to recover the project-relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Strips the leading project-name segment from a `project_name/copy_destination_path` string and returns the project-relative path. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a given file by combining the base output directory with the parent portion of the copy-destination path. |
| `compute_file_hash` | `file_path: str` | `str` | Computes and returns the SHA-256 hex digest of a file, reading it in chunks. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` if the copied file exists and its SHA-256 hash matches the source; returns `False` if the copy does not exist or hashes differ. |

## 4. Design Decisions

- **Collision-free output directories via `{stem}_{ext}` naming**: The copy-destination path inserts a `{stem}_{ext}` directory between the parent directory and the filename (e.g., `utils_c/utils.c` vs. `utils_h/utils.h`). This ensures files sharing the same stem but different extensions never share an output directory. Files without extensions (e.g., `Makefile`) use their name unchanged as the directory.
- **`rel_to_copy_path` / `copy_path_to_rel` as symmetric inverse pair**: The two functions are explicitly designed as inverses. `copy_path_to_rel` verifies the inverse relationship by checking whether the second-to-last path component equals `_to_dir_name(filename)` before removing it, making the transformation safe to apply on paths that may not follow the convention.
- **`output_path_to_rel` as a two-stage strip**: Rather than duplicating logic, `output_path_to_rel` delegates to `copy_path_to_rel` after removing only the first path segment (the project name), composing the two simpler transformations.

## Definition Design Specifications

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `_to_dir_name(filename: str) -> str` |
| **Visibility** | Module-private (underscore prefix) |

**Responsibility:** Converts a filename into a safe directory name by replacing the extension separator `.` with `_`, allowing filenames with different extensions but the same stem to map to distinct directory names.

**When to use:** Called internally by `rel_to_copy_path` and `copy_path_to_rel` to produce or recognize the inserted directory component in the copy-destination path structure.

**Design decisions:**
- Files without extensions (no `.` in the extension component) are returned unchanged, preserving names like `Makefile` as-is.
- Only the leading `.` of the extension is replaced; the extension itself is appended without the dot separator.

**Constraints & edge cases:**
- Input must be a bare filename, not a full path; the result is undefined for paths containing directory separators.
- A filename whose stem is empty (e.g., `.gitignore`) would produce `_gitignore` as the directory name, since `os.path.splitext` treats `.gitignore` as stem=`.gitignore`, ext=`""` — meaning it is returned as-is.

---

## `rel_to_copy_path(rel_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `rel_to_copy_path(rel_path: str) -> str` |

**Responsibility:** Converts a project-relative file path to the copy-destination directory structure path used by the pipeline when copying source files to the output directory.

**When to use:** Called when constructing the output path for a source file — for example, when building file metadata entries (`output.py`, `dependency_graph.py`) that record where a file's copy lives.

**Design decisions:**
- An intermediate directory named `{stem}_{ext}` is inserted between the parent directory and the filename. This prevents output directory collisions between files that share a stem but differ in extension (e.g., `utils.c` and `utils.h`).
- Top-level files (no parent directory) omit any leading separator, producing `{dir_name}/{filename}`.

**Constraints & edge cases:**
- `rel_path` must use forward slashes or the OS separator; the function uses `os.path.dirname` and `os.path.basename`, which are OS-aware.
- This function is the authoritative definition of the copy-destination path structure; callers in `output.py` and `dependency_graph.py` rely on it being consistent with `resolve_file_output_dir`.

---

## `copy_path_to_rel(copy_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `copy_path_to_rel(copy_path: str) -> str` |

**Responsibility:** Inverse of `rel_to_copy_path`; strips the inserted `{stem}_{ext}` directory component from a copy-destination path to recover the original project-relative path.

**When to use:** Called when reading back stored file paths from output artifacts or dependency graphs that were recorded using `rel_to_copy_path` — for example, in `output.py` and `pipeline.py` when resolving stored paths back to source-relative identifiers.

**Design decisions:**
- Uses `_to_dir_name` on the terminal filename to verify that the second-to-last path segment is indeed the inserted directory, rather than assuming it unconditionally. This guards against inputs that were not produced by `rel_to_copy_path`.
- Backslashes are normalized to forward slashes before splitting, providing Windows path compatibility.
- If the verification check fails, the path is returned unchanged rather than raising an error.

**Constraints & edge cases:**
- Paths with fewer than two components are returned unchanged.
- If the second-to-last directory does not match `_to_dir_name(filename)`, the original `copy_path` is returned without modification.

---

## `output_path_to_rel(output_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `output_path_to_rel(output_path: str) -> str` |

**Responsibility:** Strips the leading project-name prefix from an output path and then delegates to `copy_path_to_rel` to recover the source-relative path; serves as the inverse of `to_output_path()` in `output.py`.

**When to use:** Called when processing stored dependency metadata (e.g., `callee_usages`, `caller_usages` entries in `output.py` and `doc_creator.py`) where file paths are stored in `{project_name}/{copy_path}` format and must be resolved back to source-relative paths.

**Design decisions:**
- The project name is removed by splitting only on the first `/`, making the function agnostic to how many slashes appear in the remainder of the path.
- Paths that cannot be split into two parts are returned unchanged rather than raising an error.

**Constraints & edge cases:**
- Assumes the first path segment is exactly the project name as set by `to_output_path()`.
- Does not validate that the project name segment is non-empty.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str` |
| **Return type** | `str` — an absolute filesystem path |

**Responsibility:** Computes the absolute output directory path for a given source file by combining the base output directory with the copy-destination directory structure defined by `rel_to_copy_path`.

**When to use:** Called before reading or writing per-file output artifacts (e.g., `doc.json`, `file_dependencies.json`, copied source) to determine the correct destination directory on disk, as used in `output.py`, `pipeline.py`, and `doc_creator.py`.

**Design decisions:**
- Reuses `rel_to_copy_path` to ensure path structure is consistent across the codebase; the output directory is the parent directory of the copy-destination path, not the path itself.
- Returns an absolute path by joining with `base_output_dir` via `os.path.join`, making it directly usable with filesystem operations.

**Constraints & edge cases:**
- Does not create the directory; callers are responsible for calling `os.makedirs` if needed.
- `base_output_dir` should be an absolute path for the return value to be absolute.

---

## `compute_file_hash(file_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `compute_file_hash(file_path: str) -> str` |
| **Return type** | `str` — lowercase hexadecimal SHA-256 digest |

**Responsibility:** Produces a SHA-256 hash of a file's binary content, used for change detection without loading the entire file into memory.

**When to use:** Called by `is_file_unchanged` to compare a source file against its copied counterpart; may also be used independently whenever a content fingerprint is required.

**Design decisions:**
- Reads the file in fixed 8 KB chunks to bound memory usage regardless of file size.
- Uses SHA-256 for a strong collision resistance guarantee suitable for change detection.

**Constraints & edge cases:**
- Raises `FileNotFoundError` or `PermissionError` if `file_path` is inaccessible; no exception handling is performed internally.
- `file_path` must be an absolute or resolvable path.

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

| Item | Detail |
|---|---|
| **Signature** | `is_file_unchanged(source_path: str, copied_path: str) -> bool` |
| **Return type** | `bool` — `True` if the file content is identical, `False` if changed or copy is absent |

**Responsibility:** Determines whether a source file has changed relative to its output-directory copy by comparing SHA-256 hashes, enabling the pipeline to skip unchanged files.

**When to use:** Called during the change-detection phase in `pipeline.py` to decide which files need reprocessing before running expensive analysis steps.

**Design decisions:**
- A missing copy at `copied_path` is treated as a change (returns `False`) rather than an error, so newly added files are automatically included in the processing set.
- Delegates hash computation to `compute_file_hash` for both files, keeping change-detection logic separate from I/O mechanics.

**Constraints & edge cases:**
- `source_path` must exist and be readable; no existence check is performed for it.
- Hash collision could theoretically produce a false `True`, but SHA-256 makes this negligible in practice.
- Does not check file metadata (timestamps, size); only content hashes are compared.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `hashlib`) and does not import any symbols from other modules within the codetwine project.

---

## Dependents (modules that import this file)

Multiple modules within the codetwine project depend on this file for path conversion and file integrity utilities.

- **`codetwine/output.py`** → `codetwine/utils/file_utils_py/file_utils.py` : uses `rel_to_copy_path` to construct output path strings in `"project_name/copy_path"` format; uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists; uses `output_path_to_rel` to recover project-relative paths from output-format paths found in dependency usage records; uses `copy_path_to_rel` to strip the inserted `{stem}_{ext}` directory segment from copy-destination paths.

- **`codetwine/pipeline.py`** → `codetwine/utils/file_utils_py/file_utils.py` : uses `resolve_file_output_dir` to determine the output directory for each file during change detection and processing; uses `is_file_unchanged` to compare the source file against its copied counterpart (via SHA256 hash) to decide whether reprocessing is needed; uses `copy_path_to_rel` to convert copy-destination paths back to project-relative paths when normalizing dependency path references.

- **`codetwine/doc_creator.py`** → `codetwine/utils/file_utils_py/file_utils.py` : uses `resolve_file_output_dir` to locate the output directory for a given file when loading design documents; uses `output_path_to_rel` to display human-readable relative paths for callee and caller usage entries in generated documentation.

- **`codetwine/extractors/dependency_graph.py`** → `codetwine/utils/file_utils_py/file_utils.py` : uses `rel_to_copy_path` to format the `"file"`, `"callers"`, and `"callees"` fields in dependency graph entries by prepending the project name and converting relative paths to copy-destination path structure.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output.py` → `this module` (one-way)
- `codetwine/pipeline.py` → `this module` (one-way)
- `codetwine/doc_creator.py` → `this module` (one-way)
- `codetwine/extractors/dependency_graph.py` → `this module` (one-way)

This file does not import from any of its dependents. It acts as a pure utility leaf module with no inbound imports from within the project.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `filename` | Caller argument | String filename (e.g. `"settings.py"`, `"Makefile"`) |
| `rel_path` | Caller argument | String relative path from project root (e.g. `"repo_graphrag/llm/client.py"`) |
| `copy_path` | Caller argument | String copy-destination path (e.g. `"repo_graphrag/llm/client_py/client.py"`) |
| `output_path` | Caller argument | String prefixed with project name (e.g. `"my_project/config_py/config.py"`) |
| `base_output_dir` | Caller argument | String absolute path to the base output directory |
| `file_rel` | Caller argument | String relative path of a file within the project |
| `file_path` | Caller argument | String absolute path to a file on disk |
| `source_path` | Caller argument | String absolute path to the original source file |
| `copied_path` | Caller argument | String absolute path to the copied file in the output directory |

No configuration files or environment variables are read. File reads occur only in `compute_file_hash` (binary chunked reads) and in `is_file_unchanged` (via `os.path.exists` and `compute_file_hash`).

---

## 2. Transformation Overview

### Path encoding pipeline (`rel_path` → copy-destination path)

```
filename string
    → os.path.splitext()         [stem, ext]
    → _to_dir_name()             "{stem}_{ext}" or stem as-is (no extension)
    → rel_to_copy_path()         "{parent_dir}/{dir_name}/{filename}"
    → resolve_file_output_dir()  os.path.join(base_output_dir, dirname(copy_path))
```

`_to_dir_name` is a shared building block consumed by both `rel_to_copy_path` (path construction) and `copy_path_to_rel` (path deconstruction) to ensure the two functions remain inverse operations.

### Path decoding pipeline (copy-destination path → `rel_path`)

```
copy_path string
    → normalize separators ("\" → "/"), split on "/"
    → check parts[-2] == _to_dir_name(parts[-1])
    → if matched: drop inserted directory segment  "{parent_parts}/{filename}"
    → copy_path_to_rel()

output_path string ("project_name/copy_path")
    → split on "/" (first occurrence only)          [project_name, rest]
    → copy_path_to_rel(rest)
    → output_path_to_rel()
```

### File hash and change-detection pipeline

```
file_path
    → open in binary mode, read in 8 KB chunks
    → SHA256 update per chunk
    → hexdigest string
    → compute_file_hash()

(source_path, copied_path)
    → os.path.exists(copied_path)  → False (early exit: file treated as changed)
    → compute_file_hash(source_path)
    → compute_file_hash(copied_path)
    → hash comparison → bool
    → is_file_unchanged()
```

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `_to_dir_name` | `str` | Directory name derived from a filename, with `"."` in extension replaced by `"_"` |
| `rel_to_copy_path` | `str` | Copy-destination path with an inserted `{stem}_{ext}` directory segment |
| `copy_path_to_rel` | `str` | Original project-relative path recovered by removing the inserted directory segment |
| `output_path_to_rel` | `str` | Project-relative path recovered from a `"project_name/copy_path"` prefixed string |
| `resolve_file_output_dir` | `str` | Absolute path to the output directory for a given file |
| `compute_file_hash` | `str` | SHA256 digest as a lowercase hex string |
| `is_file_unchanged` | `bool` | `True` if both files exist and their SHA256 hashes match; `False` otherwise |

No files are written and no side effects are produced by any function in this module.

---

## 4. Key Data Structures

This module operates exclusively on primitive Python types. No dataclasses, TypedDicts, or named compound structures are defined or produced. The intermediate values used internally are documented below for clarity.

### Path component tuple (internal to `_to_dir_name`)

| Field | Type | Purpose |
|---|---|---|
| `stem` | `str` | Filename without extension, as returned by `os.path.splitext` |
| `ext` | `str` | Extension including the leading `"."`, or empty string if absent |

### Path parts list (internal to `copy_path_to_rel`)

| Index | Type | Purpose |
|---|---|---|
| `parts[:-2]` | `list[str]` | Zero or more parent directory segments preceding the inserted `{stem}_{ext}` directory |
| `parts[-2]` | `str` | The inserted `{stem}_{ext}` directory segment to be removed |
| `parts[-1]` | `str` | The filename, which is preserved in the output |

### SHA256 hash state (internal to `compute_file_hash`)

| Field | Type | Purpose |
|---|---|---|
| `h` | `hashlib.sha256` | Accumulates binary file content in 8 KB chunks |
| return value | `str` | Hex-encoded 64-character SHA256 digest |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file follows a **fail-fast** approach. No try-except blocks are present; all functions propagate exceptions directly to callers without catching or suppressing them. The single exception to unhandled propagation is a deliberate pre-condition check in `is_file_unchanged`, which performs an explicit existence guard and returns a safe boolean value rather than raising — representing a localized graceful degradation for a well-defined absent-file scenario.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `FileNotFoundError` / `OSError` | `compute_file_hash` attempts to open a file that does not exist or is inaccessible | Propagated unhandled to the caller | No | Calling pipeline or doc-creator operation fails at the call site |
| Missing copy file (existence check) | `is_file_unchanged` is called when the copied file does not yet exist at the destination | Returns `False` (treated as "changed") without raising | Yes | Caller treats the file as requiring processing; no crash |
| `OSError` | `os.path` operations (`dirname`, `basename`, `join`, `exists`) encounter filesystem errors | Propagated unhandled to the caller | No | The operation invoking the path utility fails |
| Malformed path string | `copy_path_to_rel` or `output_path_to_rel` receives a path that does not match the expected structure | The original input string is returned as-is (identity fallback) | Yes | Path conversion silently produces the unmodified input; downstream consumers receive an unexpected value |

---

## 3. Design Notes

- **Deliberate absence of defensive wrapping**: The utility functions in this file are pure path-transformation or hashing helpers. Exceptions arising from invalid filesystem state (missing files, permission errors) are intentionally left to propagate so that callers — `pipeline.py`, `output.py`, `doc_creator.py` — retain full control over error recovery policy at the orchestration level.
- **Boolean guard as a protocol contract**: The `False` return in `is_file_unchanged` when the copy does not exist is not error handling in the exception sense; it is a defined part of the function's contract, explicitly documented in the docstring. This avoids conflating a normal "not yet processed" state with a filesystem failure.
- **Silent identity fallback in path inversion**: `copy_path_to_rel` and `output_path_to_rel` return the input unchanged when the expected directory-name pattern is not recognized. This prevents crashes in dependent consumers but may silently pass through malformed paths, delegating the responsibility for detecting such anomalies to the caller.

## Summary

**codetwine/utils/file_utils.py** — Converts between project-relative paths and copy-destination paths, resolves output directories, and detects file changes via SHA-256 hashing.

**Public functions:**
- `rel_to_copy_path(rel_path: str) → str`
- `copy_path_to_rel(copy_path: str) → str`
- `output_path_to_rel(output_path: str) → str`
- `resolve_file_output_dir(base_output_dir: str, file_rel: str) → str`
- `compute_file_hash(file_path: str) → str`
- `is_file_unchanged(source_path: str, copied_path: str) → bool`

**Key data:** All inputs/outputs are primitive `str` or `bool`; copy-destination paths follow `{parent}/{stem}_{ext}/{filename}` structure.
