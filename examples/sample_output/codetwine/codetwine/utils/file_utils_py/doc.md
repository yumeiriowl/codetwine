# Design Document: codetwine/utils/file_utils.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Provides utility functions for converting between project-relative file paths and the copy-destination directory structure used throughout the CodeTwine pipeline, as well as computing and comparing file hashes to detect changes.

## 2. When to Use This Module

- **When writing a file to the output directory**: Call `resolve_file_output_dir(base_output_dir, file_rel)` to obtain the absolute output directory path for a given source file, ensuring files with the same name but different extensions (e.g. `utils.c` and `utils.h`) do not collide.
- **When constructing an output path string from a relative path**: Call `rel_to_copy_path(rel_path)` to convert a project-relative path (e.g. `"src/foo.py"`) into the copy-destination directory structure path (e.g. `"src/foo_py/foo.py"`).
- **When recovering the original relative path from a copy-destination path**: Call `copy_path_to_rel(copy_path)` to strip the inserted `{stem}_{ext}` directory and restore the original project-relative path.
- **When recovering the original relative path from a full output path that includes the project name prefix**: Call `output_path_to_rel(output_path)` to strip the project name and convert the remainder back to a project-relative path.
- **When detecting whether a source file has changed since it was last copied**: Call `is_file_unchanged(source_path, copied_path)` to compare SHA256 hashes of the original and copied files; returns `False` if the copy does not yet exist.
- **When computing the hash of a file independently**: Call `compute_file_hash(file_path)` to obtain the SHA256 hex digest of any file.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to a copy-destination directory structure path of the form `{parent_dir}/{stem}_{ext}/{filename}`. |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; removes the inserted `{stem}_{ext}` directory segment to recover the original project-relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Inverse of `to_output_path` in `output.py`; strips the leading project name segment and converts the remainder via `copy_path_to_rel`. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a source file by joining `base_output_dir` with the parent directory portion of the copy-destination path. |
| `compute_file_hash` | `file_path: str` | `str` | Reads a file in 8 KB chunks and returns its SHA256 hash as a hex string. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` if the SHA256 hashes of the source file and its copy match; returns `False` if the copy does not exist. |

## 4. Design Decisions

- **Extension-as-suffix directory naming**: The `{stem}_{ext}` directory convention (e.g. `foo_py` for `foo.py`) is used consistently across path-conversion functions and output directory resolution. This prevents output directory collisions between files that share a stem but differ in extension (e.g. `utils.c` vs. `utils.h`), and files without extensions (e.g. `Makefile`) are handled by using the filename itself as the directory name.
- **Symmetric path conversion pair**: `rel_to_copy_path` and `copy_path_to_rel` are designed as strict inverses of each other, and `output_path_to_rel` composes `copy_path_to_rel` with a project-name prefix strip to form a second inverse pair with `to_output_path` in `output.py`. This symmetry is relied upon by multiple dependents (`output.py`, `pipeline.py`, `doc_creator.py`, `dependency_graph.py`) to round-trip between stored paths and source-relative paths.

## Definition Design Specifications

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `filename: str` → `str` |
| **Visibility** | Module-private (leading underscore) |

**Responsibility:** Converts a bare filename into a directory-name-safe string by replacing the dot separating the extension with an underscore. Exists as a shared helper to ensure consistent naming between path-building and path-parsing logic.

**When to use:** Called internally whenever a directory name must be derived from a filename, both when constructing and when verifying copy-destination paths.

**Design decisions:**
- Uses `os.path.splitext` to detect the extension boundary, so only the last dot (the extension separator) is replaced; dots in the stem are preserved.
- Files without extensions are returned unchanged, meaning a file like `Makefile` maps to a directory also named `Makefile`.

**Constraints & edge cases:**
- Input is expected to be a bare filename, not a path with directory components.
- Hidden files that begin with a dot (e.g., `.gitignore`) are treated by `os.path.splitext` as having no extension and an empty stem, so they are returned as-is.

---

## `rel_to_copy_path(rel_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `rel_path: str` → `str` |

**Responsibility:** Converts a project-relative file path into the copy-destination directory structure path used when source files are copied into the output tree, inserting a `{stem}_{ext}` directory level between the parent directory and the filename.

**When to use:** Called whenever a caller needs to know the output-tree path that corresponds to a given source-relative path—for example, when constructing dependency graph entries or output path strings.

**Design decisions:**
- The inserted `{stem}_{ext}` directory prevents name collisions between files that share a stem but differ in extension (e.g., `utils.c` and `utils.h` each get their own subdirectory).
- Delegates extension-to-suffix conversion entirely to `_to_dir_name` to keep the mapping consistent with `copy_path_to_rel`.

**Constraints & edge cases:**
- `rel_path` must use forward slashes or the platform separator; the function does not normalize separators before calling `os.path.dirname`/`os.path.basename`.
- Top-level files (no parent directory) produce a two-component path; files with parent directories produce three or more components.

---

## `copy_path_to_rel(copy_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `copy_path: str` → `str` |

**Responsibility:** Inverts `rel_to_copy_path` by detecting and removing the inserted `{stem}_{ext}` directory level, recovering the original project-relative path.

**When to use:** Called whenever an output-tree or dependency-graph path must be translated back to a source-relative path—for example, when resolving `from` or `file` fields in dependency records.

**Design decisions:**
- Normalizes backslashes to forward slashes before splitting, making the function safe on Windows-originated paths.
- The removal is conditional: the second-to-last path component is only dropped if it exactly matches what `_to_dir_name` would produce for the filename, ensuring non-matching paths are returned unchanged rather than silently corrupted.

**Constraints & edge cases:**
- Paths with fewer than two components are returned as-is without modification.
- If the second-to-last directory name does not match `_to_dir_name(filename)`, the original `copy_path` is returned unchanged.

---

## `output_path_to_rel(output_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `output_path: str` → `str` |

**Responsibility:** Strips the project-name prefix from a `"project_name/copy_destination_path"` format string and then delegates to `copy_path_to_rel` to recover the source-relative path.

**When to use:** Called when processing paths stored in output files or dependency records that include the project name as the leading component.

**Design decisions:**
- Splits only on the first `/`, so the project name is isolated regardless of how many slashes appear in the remainder of the path.
- Paths that do not contain a `/` are returned unchanged, providing a safe fallback.

**Constraints & edge cases:**
- Assumes the first path component is always the project name; a path that begins with a directory that happens to match the project name but is not a project-name prefix will be incorrectly stripped.
- Depends entirely on `copy_path_to_rel` for the second stage of inversion; all constraints of that function apply to the remainder after prefix removal.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `base_output_dir: str`, `file_rel: str` → `str` |

**Responsibility:** Computes the absolute output directory path for a given source file by combining the base output directory with the parent-directory portion of the file's copy-destination path.

**When to use:** Called before reading or writing any per-file output artifacts (e.g., `doc.json`, `file_dependencies.json`, copied source) to obtain the correct directory to create or search within.

**Design decisions:**
- Reuses `rel_to_copy_path` and then takes its `os.path.dirname` rather than duplicating the path-construction logic, ensuring the output directory always matches the copy-destination structure exactly.

**Constraints & edge cases:**
- Does not create the directory; callers are responsible for calling `os.makedirs` if needed.
- The returned path is only as absolute as `base_output_dir`; if `base_output_dir` is relative, the result is also relative.

---

## `compute_file_hash(file_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `file_path: str` → `str` |

**Responsibility:** Produces a SHA-256 hex-digest fingerprint of a file's binary content, reading the file in fixed-size chunks to avoid loading large files entirely into memory.

**When to use:** Called when a content-based identity check is needed for a file, typically as part of change detection.

**Design decisions:**
- Reads in 8 KB chunks to bound memory usage regardless of file size.
- Returns a hex string rather than raw bytes for human-readable storage and easy comparison.

**Constraints & edge cases:**
- `file_path` must exist and be readable; the function does not handle missing files and will raise an `OSError` if the file is absent.
- Hash is computed over raw bytes, so encoding-agnostic.

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

| Item | Detail |
|---|---|
| **Signature** | `source_path: str`, `copied_path: str` → `bool` |

**Responsibility:** Determines whether a source file and its copy in the output directory have identical content by comparing their SHA-256 hashes, treating a missing copy as a changed (not-unchanged) state.

**When to use:** Called during incremental pipeline runs to decide whether a file needs to be reprocessed, avoiding redundant work when the source has not changed since the last run.

**Design decisions:**
- Returns `False` (changed) rather than raising an error when the copy is absent, making the function safe to call before any output has been produced.
- Delegates hashing to `compute_file_hash` for both paths, so both reads use the same chunked strategy.

**Constraints & edge cases:**
- `source_path` must exist and be readable; absence of the source file (as opposed to the copy) is not handled and will propagate as an `OSError` from `compute_file_hash`.
- A `True` result guarantees only hash equality, not byte-for-byte identity (SHA-256 collision probability is negligible in practice).

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `hashlib`) and defines utility functions consumed by other modules.

---

### Dependents (modules that import this file)

The following project-internal modules import symbols from this file:

- **`codetwine/output.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `rel_to_copy_path` to construct output paths in `"project_name/copy_path"` format; uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists; uses `output_path_to_rel` to convert output-format paths back to project-relative paths when resolving dependency entries; uses `copy_path_to_rel` to strip the inserted `{stem}_{ext}` directory segment from copy-destination paths.

- **`codetwine/pipeline.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `resolve_file_output_dir` to determine the output directory for each file during change detection and file processing; uses `is_file_unchanged` to compare source and copied file hashes to detect whether a file needs reprocessing; uses `copy_path_to_rel` to convert prefixed copy-destination paths back to project-relative paths.

- **`codetwine/doc_creator.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `resolve_file_output_dir` to locate the output directory for a given file when loading design documents; uses `output_path_to_rel` to convert output-format paths to project-relative paths when formatting callee and caller usage entries in documentation.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `rel_to_copy_path` to construct `"project_name/copy_path"` formatted strings for the `file`, `callers`, and `callees` fields when building the dependency graph file info list.

---

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output.py` → `codetwine/utils/file_utils_py/file_utils.py`
- `codetwine/pipeline.py` → `codetwine/utils/file_utils_py/file_utils.py`
- `codetwine/doc_creator.py` → `codetwine/utils/file_utils_py/file_utils.py`
- `codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils_py/file_utils.py`

This file is a pure utility leaf module. It imports no project-internal modules and is only consumed by others; there are no circular or bidirectional relationships.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Function(s) | Format |
|---|---|---|
| `filename` | `_to_dir_name` | Plain string filename (e.g. `"settings.py"`) |
| `rel_path` | `rel_to_copy_path`, `resolve_file_output_dir` | POSIX-style relative path string from the project root (e.g. `"src/foo.py"`) |
| `copy_path` | `copy_path_to_rel` | String in `{parent_dir}/{stem}_{ext}/{filename}` format |
| `output_path` | `output_path_to_rel` | String in `{project_name}/{copy_destination_path}` format |
| `base_output_dir` | `resolve_file_output_dir` | Absolute directory path string |
| `file_path` | `compute_file_hash` | Absolute file path string; file is read in binary 8 KB chunks |
| `source_path`, `copied_path` | `is_file_unchanged` | Absolute file path strings; both files are read from disk |

---

## 2. Transformation Overview

### Path encoding pipeline (`rel_to_copy_path`)

```
rel_path
  └─ split into parent_dir + filename         (os.path.dirname / os.path.basename)
        └─ filename → dir_name                 (_to_dir_name: replaces "." in ext with "_")
              └─ reassembled as               {parent_dir}/{dir_name}/{filename}
```

### Path decoding pipeline (`copy_path_to_rel`)

```
copy_path
  └─ split into parts by "/"
        └─ check: parts[-2] == _to_dir_name(parts[-1])?
              ├─ YES → remove the inserted directory segment → original rel_path
              └─ NO  → return copy_path unchanged
```

### Output-path decoding pipeline (`output_path_to_rel`)

```
output_path
  └─ split on first "/" → [project_name, remainder]
        └─ remainder → copy_path_to_rel → original rel_path
```

### Output directory resolution (`resolve_file_output_dir`)

```
file_rel
  └─ rel_to_copy_path → copy_path
        └─ os.path.dirname(copy_path) → relative output sub-path
              └─ os.path.join(base_output_dir, sub-path) → absolute output directory
```

### File hash and change-detection pipeline

```
file_path
  └─ read in 8 KB binary chunks → fed into SHA256 → hex digest string

source_path + copied_path
  └─ existence check on copied_path
        ├─ missing → False  (treated as changed)
        └─ present → compute_file_hash(source_path) == compute_file_hash(copied_path)
              └─ bool (True = unchanged)
```

---

## 3. Outputs

| Output | Function(s) | Format |
|---|---|---|
| Directory-safe name | `_to_dir_name` | String with `"."` in extension replaced by `"_"` (e.g. `"settings_py"`) |
| Copy-destination path | `rel_to_copy_path` | String `{parent_dir}/{stem}_{ext}/{filename}` |
| Recovered relative path | `copy_path_to_rel`, `output_path_to_rel` | POSIX-style relative path string |
| Absolute output directory | `resolve_file_output_dir` | Absolute filesystem path string (no trailing slash) |
| SHA256 hex digest | `compute_file_hash` | 64-character hex string |
| Unchanged flag | `is_file_unchanged` | `bool` (`True` = hashes match; `False` = copy missing or hashes differ) |

No function in this module writes to the filesystem or produces side effects other than reading files for hashing.

---

## 4. Key Data Structures

This module operates entirely on primitive Python types; no dataclasses, TypedDicts, or composite data structures are defined or returned. The relevant scalar types are documented below.

### Intermediate string representations

| Name | Type | Purpose |
|---|---|---|
| `stem` | `str` | Filename portion before the extension, produced by `os.path.splitext` |
| `ext` | `str` | Extension including the leading `"."` (e.g. `".py"`), or `""` for extension-less files |
| `dir_name` | `str` | Collision-safe directory name derived from `filename` (e.g. `"config_py"`) |
| `parent_dir` | `str` | Directory portion of `rel_path`; empty string for top-level files |
| `parts` | `list[str]` | Path split by `"/"`, used in `copy_path_to_rel` and `output_path_to_rel` to locate and strip the inserted segment |
| `h` | `hashlib.SHA256` | Accumulates binary file content; `.hexdigest()` yields the final hash string |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **fail-fast** approach. No try-except blocks are present; all functions either succeed or propagate exceptions directly to the caller. The single explicit guard is a pre-condition check in `is_file_unchanged`, which uses a boolean return value rather than an exception to signal a missing file—treating the absent copy as a "changed" state and allowing the caller to decide how to proceed. All I/O errors (file not found, permission denied, read failures) are left unhandled and bubble up to callers in `pipeline.py`, `output.py`, `doc_creator.py`, and `dependency_graph.py`.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing copied file | `copied_path` does not exist when `is_file_unchanged` is called | Returns `False` (treated as "changed") without raising | Yes — caller re-processes the file | File is added to the changed set and re-processed by `pipeline.py` |
| `FileNotFoundError` / `IOError` | `compute_file_hash` opens a non-existent or inaccessible file | Unhandled; exception propagates to caller | No — propagation terminates the current operation | Calling pipeline operation fails at the point of invocation |
| `OSError` / `PermissionError` | Any `os.path.*` or `os.path.join` call on an inaccessible path | Unhandled; exception propagates | No | Caller-level failure |
| Invalid path input | `copy_path_to_rel` or `output_path_to_rel` receives a path that does not match the expected structure | Returns the input path unchanged (identity fallback) | Yes — path is passed through as-is | Callers receive the original string; no crash, but downstream logic may produce incorrect results |

---

## 3. Design Notes

- **Boundary responsibility**: This file is a utility layer. Error handling responsibility is intentionally delegated to callers (`pipeline.py`, `output.py`, `doc_creator.py`), which have the necessary context to decide whether to skip, log, or abort processing.
- **Boolean guard over exception**: `is_file_unchanged` uses a boolean return (`False`) for the missing-file case because a missing copy is a normal, expected state during incremental processing—not an exceptional failure. This keeps the change-detection loop in `pipeline.py` simple.
- **Identity fallback in path conversion**: `copy_path_to_rel` and `output_path_to_rel` return the input unchanged when the path does not match the expected directory structure. This is a silent degradation that avoids crashes but does not signal the mismatch to callers, placing the burden of correctness on the input format.
- **No retry or logging**: The utility functions contain no retry logic and emit no log output. All observability and recovery decisions are left entirely to dependent modules.

## Summary

**codetwine/utils/file_utils.py** — Converts between project-relative paths and copy-destination paths, and detects file changes via SHA256 hashing.

**Public functions:**
- `rel_to_copy_path(rel_path: str) -> str`
- `copy_path_to_rel(copy_path: str) -> str`
- `output_path_to_rel(output_path: str) -> str`
- `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`
- `compute_file_hash(file_path: str) -> str`
- `is_file_unchanged(source_path: str, copied_path: str) -> bool`

**Key data:** copy-destination path strings in `{parent_dir}/{stem}_{ext}/{filename}` format; SHA256 hex digest strings (64-char).
