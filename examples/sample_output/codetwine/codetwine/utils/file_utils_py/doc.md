# Design Document: codetwine/utils/file_utils.py

# Overview & Purpose

`codetwine/utils/file_utils.py` is a shared utility module that centralizes all path-conversion and file-comparison logic used throughout the codetwine pipeline. It exists as a separate file because multiple independent components (`pipeline.py`, `output.py`, `doc_creator.py`, and `extractors/dependency_graph.py`) need a single, consistent, bidirectional mapping between:

- **project-relative source paths** (e.g., `src/foo.py`),
- **copy-destination directory paths** used when source files are copied into the output tree (e.g., `src/foo_py/foo.py`), and
- **fully-qualified output paths** that additionally prefix the project name (e.g., `my_project/src/foo_py/foo.py`).

By isolating this logic in one module, all dependents apply the exact same naming/collision-avoidance convention (appending the file extension as a suffix directory, e.g., `_py`, `_h`) and can reliably invert it, ensuring path handling stays consistent across the copy, documentation-generation, and summary/graph-building stages. The module also provides file-hashing utilities used to detect whether a source file has changed since it was last copied/processed, supporting incremental processing in the pipeline.

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path into the copy-destination directory structure path (`{parent_dir}/{stem}_{ext}/{filename}`). |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; strips the inserted `{stem}_{ext}` directory to recover the original relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Inverse of `to_output_path` (in `output.py`); strips the project-name prefix and delegates to `copy_path_to_rel` to recover the source-relative path. |
| `resolve_file_output_dir` | `base_output_dir: str, file_rel: str` | `str` | Computes the absolute output directory for a given source file by combining `base_output_dir` with the parent directory of its copy path. |
| `compute_file_hash` | `file_path: str` | `str` | Computes and returns the SHA256 hex digest of a file, reading it in 8KB chunks. |
| `is_file_unchanged` | `source_path: str, copied_path: str` | `bool` | Compares SHA256 hashes of a source file and its copied counterpart to determine if the file is unchanged; returns `False` if the copy doesn't exist. |

### Design Decisions

- **Extension-as-suffix collision avoidance**: Instead of nesting by filename alone, each file gets its own directory named `{stem}_{ext}` (dot replaced with underscore), so files like `utils.c` and `utils.h` map to distinct output directories (`utils_c/` and `utils_h/`) without collision.
- **Symmetric encode/decode pairs**: `rel_to_copy_path`/`copy_path_to_rel` and (indirectly) `to_output_path`/`output_path_to_rel` are designed as strict inverses, with `_to_dir_name` acting as the single source of truth for the naming rule used by both directions.
- **Private helper isolation**: `_to_dir_name` is kept internal (prefixed with `_`) since it's an implementation detail shared only by the encode/decode functions within this module.
- **Simple, dependency-free hashing**: File change detection relies on straightforward SHA256 comparison via chunked reads, keeping the incremental-processing logic in `pipeline.py` simple and decoupled from hashing details.

# Definition Design Specifications

## `_to_dir_name`

Converts a filename into a directory-safe name by replacing the extension's leading "." with "_" (e.g. "settings.py" -> "settings_py").

This exists to derive a unique, filesystem-friendly directory name per source file that still encodes the original extension, so that the extension information is not lost when building output directory structures.

Design decision: extensionless files (e.g. "Makefile") are returned unchanged rather than raising an error or appending a suffix, since there is no "." to transform.

Constraint: only the final extension (as returned by `os.path.splitext`) is considered; filenames with multiple dots (e.g. "archive.tar.gz") will only have the last extension segment transformed.

## `rel_to_copy_path`

Converts a project-relative file path (`rel_path: str`) into the corresponding copy-destination path (`str`) of the form `{parent_dir}/{stem}_{ext}/{filename}`.

This function exists to centralize and make consistent the directory-structure logic used when copying source files into the output tree, ensuring the same transformation is applied everywhere a copy-destination path is needed.

Design decision: the extension is embedded as a suffix directory (via `_to_dir_name`) rather than being dropped, specifically to avoid path collisions between same-named files with different extensions (e.g. `utils.c` vs `utils.h`).

Edge case: for top-level files (no parent directory), the parent directory segment is omitted from the result.

## `copy_path_to_rel`

Reverses `rel_to_copy_path`, converting a copy-destination path (`copy_path: str`) back into the original project-relative path (`str`).

This exists so that once files are copied/reorganized under the `{stem}_{ext}` directory scheme, other parts of the system can recover the original relative path for lookups, comparisons, or display.

Design decision: the function verifies that the second-to-last path segment actually matches `_to_dir_name(filename)` before stripping it, rather than blindly removing the second-to-last segment; this guards against incorrectly transforming paths that do not follow the expected copy-destination structure. If the check fails, the input is returned unchanged.

Constraint: path separators are normalized (backslashes converted to forward slashes) before splitting, so the function is tolerant of Windows-style paths.

## `output_path_to_rel`

Converts a full output path in `"project_name/copy_destination_path"` format (`output_path: str`) back into a source-relative path (`str`) by stripping the leading project-name segment and delegating the rest to `copy_path_to_rel`.

This exists as the inverse of the output-path construction used elsewhere (`output.py`'s `to_output_path`), allowing downstream consumers (e.g. dependency/usage references stored with project-name prefixes) to recover plain relative paths.

Edge case: if the input does not contain at least one "/" separator (i.e., no project-name prefix can be split off), the original string is returned unchanged.

## `resolve_file_output_dir`

Computes the absolute output directory (`str`) for a given file, based on a `base_output_dir: str` and the file's project-relative path `file_rel: str`.

This exists to provide a single, reusable way to locate where a given source file's copy and associated artifacts (e.g. dependency JSON, doc JSON) live under the output tree, keeping this logic consistent with `rel_to_copy_path`.

Design decision: it reuses `rel_to_copy_path` and takes the parent directory of the result, rather than reimplementing the path-construction logic, ensuring the two functions never diverge in their directory-naming scheme.

## `compute_file_hash`

Computes the SHA256 hash (`str`, hex-encoded) of the file at `file_path: str`.

This exists to provide a lightweight, reusable content-fingerprinting utility used to detect whether a source file's contents have changed since it was last processed/copied.

Design decision: the file is read and hashed in fixed 8KB chunks rather than loaded fully into memory, to keep memory usage bounded regardless of file size.

Constraint: `file_path` must reference an existing, readable file; the function does not itself handle missing-file errors.

## `is_file_unchanged`

Determines whether a source file (`source_path: str`) is unchanged relative to its previously copied version (`copied_path: str`), returning a `bool`.

This exists to support incremental processing: only files whose content differs from their existing copy (or that have no copy yet) need to be reprocessed.

Design decision: comparison is done via SHA256 hash equality rather than timestamps or file size, to reliably detect any content change regardless of metadata differences.

Edge case: if `copied_path` does not exist, the function returns `False` (treated as changed) rather than raising an error, since a missing copy inherently means the file needs to be (re)processed.

# Dependency Description

### Dependencies (what this file uses)

This file does not depend on any other project-internal modules. It relies only on the standard library (`os`, `hashlib`) for path manipulation and file hashing, which are excluded from this description per the scope of internal dependencies.

### Dependents (what uses this file)

This file is a foundational utility module consumed by several project-internal files, all in a unidirectional manner (they depend on `file_utils.py`; this file has no reverse dependency on them):

- **codetwine/doc_creator.py**
  - Uses `output_path_to_rel` to convert stored output paths (target file and callee usage sources) back into project-relative paths when building documentation prompts.
  - Uses `resolve_file_output_dir` to locate the output directory corresponding to a given source file's relative path, in order to check for and read design documents.

- **codetwine/pipeline.py**
  - Uses `copy_path_to_rel` to convert copy-destination paths back to internal relative paths.
  - Uses `resolve_file_output_dir` to determine where a source file's copied output and dependency JSON should be located, both when detecting changed files and when processing files for output.
  - Uses `is_file_unchanged` to compare a source file against its previously copied version to decide whether it needs reprocessing.

- **codetwine/output.py**
  - Uses `rel_to_copy_path` to construct the copy-destination path from a project-relative path when building output path strings.
  - Uses `resolve_file_output_dir` to find the output directory for a file in order to read its summary document (`doc.json`) and construct file entries.
  - Uses `output_path_to_rel` to convert stored caller/callee usage file references back to relative paths when building the dependency map.
  - Uses `copy_path_to_rel` to reverse copy-destination paths back to relative paths when building Mermaid diagram text.

- **codetwine/extractors/dependency_graph.py**
  - Uses `rel_to_copy_path` to construct output-style paths (prefixed with project name) for the file itself and its callers/callees when building the dependency graph information.

Overall, `file_utils.py` acts as a shared, one-way dependency for path conversion and file-comparison utilities used across the documentation, pipeline, output generation, and dependency extraction components of the project.

# Data Flow

## Inputs
| Function | Input | Source |
|---|---|---|
| `_to_dir_name` | `filename` (str) | Basename of a project file |
| `rel_to_copy_path` | `rel_path` (str, project-relative path) | Called by `output.py`, `dependency_graph.py` with source file relative paths |
| `copy_path_to_rel` | `copy_path` (str) | Called by `pipeline.py`, `output.py` with copy-destination paths |
| `output_path_to_rel` | `output_path` (str, `"project_name/copy_path"`) | Called by `doc_creator.py`, `output.py` with stored dependency file references (e.g. `usage['from']`, `usage['file']`) |
| `resolve_file_output_dir` | `base_output_dir` (str), `file_rel` (str) | Called by `pipeline.py`, `doc_creator.py`, `output.py` during file processing/output resolution |
| `compute_file_hash` | `file_path` (str, absolute path) | Raw file bytes read from disk |
| `is_file_unchanged` | `source_path`, `copied_path` (absolute paths) | Original project file vs. copied output file |

## Transformation Flow
1. **Path encoding (`rel_to_copy_path`)**: Splits a relative path into `parent_dir` + `filename`, converts filename via `_to_dir_name` (replacing `.ext` with `_ext`), and reassembles as `{parent_dir}/{stem}_{ext}/{filename}`. This avoids collisions between same-named files with different extensions.
2. **Path decoding (`copy_path_to_rel`)**: Reverses step 1 by splitting on `/`, checking if the second-to-last segment equals `_to_dir_name(filename)`, and if so, stripping it out to restore the original relative path.
3. **Output path decoding (`output_path_to_rel`)**: Strips the leading `project_name/` segment, then delegates to `copy_path_to_rel` for the remainder.
4. **Directory resolution (`resolve_file_output_dir`)**: Reuses `rel_to_copy_path` to compute the copy path, then joins its parent directory with `base_output_dir` to get the absolute output directory.
5. **Hashing (`compute_file_hash`)**: Streams file content in 8KB chunks into a SHA256 accumulator, producing a hex digest.
6. **Change detection (`is_file_unchanged`)**: Compares hashes of `source_path` and `copied_path`; returns `False` immediately if `copied_path` doesn't exist (treated as changed).

## Outputs
| Function | Output format | Destination/Usage |
|---|---|---|
| `_to_dir_name` | str (dir name) | Internal helper used by path conversion functions |
| `rel_to_copy_path` | str, `"{parent}/{stem}_{ext}/{filename}"` | Used by `output.py`/`dependency_graph.py` to build `"project/..."` output references and directory structures |
| `copy_path_to_rel` | str, original relative path | Used by `pipeline.py`/`output.py` to recover source-relative paths for diffing and Mermaid graph rendering |
| `output_path_to_rel` | str, relative path | Used by `doc_creator.py` to resolve dependency file references back to source paths |
| `resolve_file_output_dir` | str, absolute directory path | Used by `pipeline.py`/`doc_creator.py`/`output.py` to locate per-file output artifacts (`doc.json`, `file_dependencies.json`, copied source) |
| `compute_file_hash` | str, SHA256 hex digest | Consumed by `is_file_unchanged` |
| `is_file_unchanged` | bool | Used by `pipeline.py` to determine which files changed since last run |

## Key Data Structures
- **Path string convention**: `{parent_dir}/{stem}_{ext}/{filename}` is the core recurring structure representing a "copy destination" path, symmetric between encode (`rel_to_copy_path`) and decode (`copy_path_to_rel`) operations.
- **`output_path`**: Two-part composite string `"{project_name}/{copy_path}"`, split once on `/` to separate project prefix from the copy-path structure.
- No in-memory maps/structs are defined in this file; all data is transformed via string manipulation and file I/O (hashing).

# Error Handling

This module favors graceful degradation over fail-fast behavior for path-manipulation utilities, while allowing exceptions to propagate naturally for filesystem/IO operations. No custom exception types are defined or raised; the module relies on returning safe fallback values (typically the original input) when structural assumptions do not hold, rather than raising errors.

| Error Type | Handling | Impact |
|---|---|---|
| Path with no parent directory / no extension (e.g. "Makefile") passed to `_to_dir_name` / `rel_to_copy_path` | Handled explicitly via conditional branches (`if ext`, `if parent_dir`); no exception raised | Function returns a sensible degraded output (e.g. stem-only directory name, top-level path without prefix) instead of failing |
| Malformed or unexpected `copy_path` / `output_path` passed to `copy_path_to_rel` / `output_path_to_rel` (e.g. too few path segments, directory name not matching expected pattern) | Falls back silently to returning the input unchanged when the expected structure is not detected | Caller receives the original path as-is; no exception is surfaced, so inconsistent paths may pass through undetected |
| Missing file at `copied_path` in `is_file_unchanged` | Explicitly checked with `os.path.exists`; treated as "changed" (`return False`) rather than raising | Callers (e.g. `pipeline.py`) can rely on a boolean result to decide whether to reprocess a file, without needing to catch exceptions |
| Missing/unreadable file in `compute_file_hash` (used by `is_file_unchanged`) | No explicit handling; `open()` and read errors (e.g. `FileNotFoundError`, `PermissionError`) propagate directly | Exception bubbles up to the caller; this module does not catch or wrap I/O errors during hashing |

**Design considerations:**
- The path-transformation functions (`_to_dir_name`, `rel_to_copy_path`, `copy_path_to_rel`, `output_path_to_rel`) are designed to be safely reversible for well-formed inputs but degrade to identity/fallback behavior for inputs that don't match the expected structure, avoiding exceptions in path-string processing since these are pure string/path operations with no side effects.
- I/O-bound operations (`compute_file_hash`, and indirectly `is_file_unchanged`) do not include explicit error handling for file access failures, other than the explicit existence check in `is_file_unchanged`. This places responsibility on callers (e.g. `pipeline.py`) to ensure valid file paths are supplied for hashing, or to handle propagated I/O exceptions themselves.
- The asymmetry between strict existence checking in `is_file_unchanged` (for the destination copy) and the absence of such a check for `source_path` reflects an assumption that source files are expected to reliably exist, while copies may legitimately be absent (e.g., not yet generated).

# Summary

`file_utils.py` is a dependency-free shared utility centralizing path-conversion and file-comparison logic used by pipeline.py, output.py, doc_creator.py, and dependency_graph.py. It provides symmetric encode/decode functions (`rel_to_copy_path`/`copy_path_to_rel`, `output_path_to_rel`) that map relative source paths to collision-safe copy-destination paths (`{parent}/{stem}_{ext}/{filename}`) and output paths (project-prefixed), plus `resolve_file_output_dir` for locating output artifacts, and `compute_file_hash`/`is_file_unchanged` for SHA256-based incremental-processing checks. Favors fallback/identity returns over exceptions for malformed paths.
