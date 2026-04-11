# Design Document: codetwine/utils/file_utils.py

# Overview & Purpose

## 1. Module Summary

Provides utility functions for converting between project-relative file paths and the copy-destination directory structure used throughout the CodeTwine pipeline, as well as computing and comparing file hashes to detect changes.

## 2. When to Use This Module

- **Converting a relative path to its copy-destination path**: Call `rel_to_copy_path(rel_path)` to obtain the path under which a source file will be stored in the output directory (e.g., for constructing output paths in `output.py` and `dependency_graph.py`).
- **Resolving the absolute output directory for a file**: Call `resolve_file_output_dir(base_output_dir, file_rel)` to get the absolute directory path where a file's generated artifacts (e.g., `doc.json`, `file_dependencies.json`) are written. Used in `output.py`, `pipeline.py`, and `doc_creator.py`.
- **Recovering the original relative path from a copy-destination path**: Call `copy_path_to_rel(copy_path)` to strip the inserted `{stem}_{ext}` directory segment and restore the project-relative path. Used in `output.py` and `pipeline.py`.
- **Recovering the original relative path from a full output path**: Call `output_path_to_rel(output_path)` to strip the leading project-name prefix and then apply `copy_path_to_rel`. Used in `output.py` and `doc_creator.py`.
- **Detecting whether a source file has changed**: Call `is_file_unchanged(source_path, copied_path)` to compare the SHA256 hashes of the original file and its copy, determining whether reprocessing is needed. Used in `pipeline.py`.
- **Hashing a single file**: Call `compute_file_hash(file_path)` to obtain a SHA256 hex digest of any file.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to the copy-destination directory structure path (`{parent_dir}/{stem}_{ext}/{filename}`). |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; removes the inserted `{stem}_{ext}` directory to recover the original relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Strips the leading project-name segment from a `project_name/copy_destination_path` format path and delegates to `copy_path_to_rel`. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a given file by combining `base_output_dir` with the parent directory portion of the copy-destination path. |
| `compute_file_hash` | `file_path: str` | `str` | Returns the SHA256 hash of a file as a hex string, reading in 8 KB chunks. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` if the source file and its copy share identical SHA256 hashes; returns `False` if the copy does not exist. |

## 4. Design Decisions

- **Collision-free output directories via extension suffixing**: The copy-destination structure inserts a `{stem}_{ext}` directory between the parent directory and the filename (e.g., `utils_c/utils.c` and `utils_h/utils.h`). This ensures that files sharing the same stem but differing in extension never map to the same output directory, which would otherwise silently overwrite artifacts.
- **Symmetric path conversion**: `rel_to_copy_path` and `copy_path_to_rel` are designed as strict inverses, with `copy_path_to_rel` using `_to_dir_name` to verify that the second-to-last path segment was actually inserted before removing it, avoiding incorrect stripping on paths that were not produced by `rel_to_copy_path`.
- **Hash-based change detection over timestamps**: `is_file_unchanged` uses content hashing (SHA256) rather than file modification timestamps, making change detection reliable across file copies and system clock variations.

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

**Responsibility:** Converts a filename into a directory name by replacing the extension separator `.` with `_`, producing a safe, collision-resistant directory segment.

**When to use:** Called internally whenever a directory name must be derived from a filename (by both `rel_to_copy_path` and `copy_path_to_rel`).

**Design decisions:**
- Files with no extension (e.g., `Makefile`) are returned unchanged, since there is no `.` to replace.
- Only the extension dot is replaced; dots within the stem are left as-is.

**Constraints & edge cases:**

| Case | Behavior |
|---|---|
| File with extension | Returns `{stem}_{ext_without_dot}` |
| File without extension | Returns the filename as-is |
| Multiple dots in stem | Only the final extension dot is affected |

---

## `rel_to_copy_path(rel_path: str) -> str`

**Responsibility:** Converts a project-relative file path into the copy-destination directory structure path used when source files are copied to output directories.

**When to use:** Call when building the output path for a file that is about to be copied, or when constructing path strings in formats like `{project_name}/{copy_path}` (used by `output.py` and `extractors/dependency_graph.py`).

**Design decisions:**
- A `{stem}_{ext}` directory is inserted between the parent directory and the filename. This prevents collisions between files sharing the same stem but different extensions (e.g., `utils.c` vs. `utils.h`).
- Top-level files (no parent directory) omit the leading separator.

**Constraints & edge cases:**

| Case | Output format |
|---|---|
| Top-level file with extension | `{stem}_{ext}/{filename}` |
| Nested file with extension | `{parent}/{stem}_{ext}/{filename}` |
| File without extension | `{filename}/{filename}` |

---

## `copy_path_to_rel(copy_path: str) -> str`

**Responsibility:** Reverses the transformation applied by `rel_to_copy_path`, recovering the original project-relative path from a copy-destination path.

**When to use:** Call when reading a copy-destination path (e.g., from a JSON dependency record) and the original source-relative path is needed — used in `output.py` and `pipeline.py`.

**Design decisions:**
- Before splitting, backslashes are normalized to forward slashes to handle Windows-style paths.
- The reversal is conditional: the second-to-last path segment is only removed if it matches `_to_dir_name(filename)`, ensuring the function is safe to call on paths that were not produced by `rel_to_copy_path`.
- If the path has fewer than two segments or the inserted directory cannot be detected, the input is returned unchanged.

**Constraints & edge cases:**

| Case | Behavior |
|---|---|
| Valid copy-destination path | Inserted directory segment removed |
| Path not matching expected structure | Returned unchanged |
| Windows-style backslash separators | Normalized before processing |

---

## `output_path_to_rel(output_path: str) -> str`

**Responsibility:** Strips the leading project-name prefix from a `{project_name}/{copy_path}` format path and then converts the remainder to a project-relative path.

**When to use:** Call when processing paths found in dependency JSON records (used in `output.py` and `doc_creator.py`) where paths are stored with a project-name prefix.

**Design decisions:**
- Only the first `/` is used as the split point, so project names containing `/` are not a concern, but nested paths after the prefix are preserved intact.
- Delegates to `copy_path_to_rel` for the second transformation step.
- If the input has no `/`, it is returned unchanged.

**Constraints & edge cases:**
- Assumes the project name contains no `/`.
- A path with no prefix separator passes through without modification.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

**Responsibility:** Produces the absolute path of the output directory for a given source file by combining the base output directory with the copy-destination directory structure.

**When to use:** Call before writing any output artifact (copied source, `doc.json`, `file_dependencies.json`) for a file, to determine where those artifacts should be placed — used in `output.py`, `pipeline.py`, and `doc_creator.py`.

**Design decisions:**
- Delegates path structure computation entirely to `rel_to_copy_path`, then takes the parent of the resulting path as the output directory. This ensures the directory structure is consistent with where files are actually copied.

**Constraints & edge cases:**
- `base_output_dir` is expected to be an absolute path; no validation is performed.
- The returned path is not guaranteed to exist; callers are responsible for creating it (e.g., with `os.makedirs`).

---

## `compute_file_hash(file_path: str) -> str`

**Responsibility:** Computes the SHA-256 hash of a file's binary content and returns it as a hexadecimal string, used for change detection.

**When to use:** Call when a stable content fingerprint is needed for a file — called internally by `is_file_unchanged`.

**Design decisions:**
- Reads the file in fixed 8 KB chunks rather than loading the entire content into memory, making it suitable for large files.

**Constraints & edge cases:**
- `file_path` must exist and be readable; no existence check is performed internally.
- Returns a lowercase hex string (64 characters for SHA-256).

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

**Responsibility:** Determines whether a source file and its previously copied counterpart are identical by comparing their SHA-256 hashes, enabling incremental processing in `pipeline.py`.

**When to use:** Call during the change-detection phase of a pipeline run to decide whether reprocessing a file can be skipped.

**Design decisions:**
- A missing copy is treated as "changed" (returns `False`) rather than raising an error, so that newly encountered files are always processed.
- Hash comparison is used rather than modification-time comparison, providing content-based accuracy regardless of filesystem timestamp behavior.

**Constraints & edge cases:**

| Condition | Return value |
|---|---|
| Copy does not exist | `False` (treated as changed) |
| Hashes match | `True` |
| Hashes differ | `False` |

- Both paths must be readable if they exist; no permission handling is performed.

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `hashlib`) and defines utilities consumed by other modules.

---

## Dependents (modules that import this file)

The following project-internal modules import symbols from `codetwine/utils/file_utils.py`:

- **`codetwine/output.py` → `codetwine/utils/file_utils.py`** : Uses `rel_to_copy_path` to construct output paths in `"project_name/copy_path"` format; uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists; uses `output_path_to_rel` to recover source-relative paths from output-format paths in dependency maps; uses `copy_path_to_rel` to strip the inserted `{stem}_{ext}` directory segment when building Mermaid diagrams.

- **`codetwine/pipeline.py` → `codetwine/utils/file_utils.py`** : Uses `resolve_file_output_dir` to determine the output directory for each file during change detection and file processing; uses `is_file_unchanged` to compare the source file against its copy in the output directory to detect whether reprocessing is needed; uses `copy_path_to_rel` to convert copy-destination paths back to project-relative paths.

- **`codetwine/doc_creator.py` → `codetwine/utils/file_utils.py`** : Uses `output_path_to_rel` to recover readable source-relative file names when constructing LLM prompts from dependency data; uses `resolve_file_output_dir` to locate the output directory for a given file when loading or writing design documents.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils.py`** : Uses `rel_to_copy_path` to format the `"file"`, `"callers"`, and `"callees"` path fields in the dependency graph output by constructing `"project_name/copy_path"` strings for each file and its caller/callee relationships.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output.py` → `codetwine/utils/file_utils.py`
- `codetwine/pipeline.py` → `codetwine/utils/file_utils.py`
- `codetwine/doc_creator.py` → `codetwine/utils/file_utils.py`
- `codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils.py`

`codetwine/utils/file_utils.py` does not import from any of these modules; it serves purely as a utility provider, with the data flow going from each dependent inward toward this file.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `filename` | Caller argument | Plain string (e.g., `"settings.py"`, `"Makefile"`) |
| `rel_path` | Caller argument | Relative path string from the project root (e.g., `"repo_graphrag/llm/client.py"`) |
| `copy_path` | Caller argument | Copy-destination directory structure path string (e.g., `"repo_graphrag/llm/client_py/client.py"`) |
| `output_path` | Caller argument | `"project_name/copy_destination_path"` format string |
| `base_output_dir` | Caller argument | Absolute directory path string |
| `file_rel` | Caller argument | Relative path string from the project root |
| `file_path` / `source_path` / `copied_path` | Caller argument | Absolute file path strings |

No file reads, configuration values, or environment state are consumed — all inputs arrive exclusively as function arguments.

---

## 2. Transformation Overview

### Path encoding pipeline (`rel_to_copy_path`)

```
rel_path
  └─ os.path.dirname / os.path.basename
        └─ filename, parent_dir
              └─ _to_dir_name(filename)          # "." in extension → "_"
                    └─ f"{parent_dir}/{dir_name}/{filename}"   # copy-destination path
```

`_to_dir_name` is the shared primitive: it splits a filename into stem and extension via `os.path.splitext`, then concatenates them with `_` as separator (or returns the stem unchanged when no extension is present). All higher-level path functions build on this primitive.

### Path decoding pipeline (`copy_path_to_rel`)

```
copy_path
  └─ split by "/"
        └─ parts[-1] = filename
              └─ _to_dir_name(filename) == parts[-2]?   # was the directory inserted?
                    YES → join(parts[:-2] + [filename])  # remove inserted directory
                    NO  → return copy_path unchanged
```

### Two-level decoding (`output_path_to_rel`)

```
output_path
  └─ split("/", 1)            # strip project_name prefix
        └─ parts[1] = copy_path
              └─ copy_path_to_rel(copy_path)   # decode copy-destination structure
                    └─ rel_path
```

### Output directory resolution (`resolve_file_output_dir`)

```
file_rel
  └─ rel_to_copy_path(file_rel)        # encode to copy-destination path
        └─ os.path.dirname(copy_path)  # drop the filename, keep the directory portion
              └─ os.path.join(base_output_dir, ...)   # absolute output directory
```

### File change detection (`is_file_unchanged`)

```
source_path, copied_path
  └─ os.path.exists(copied_path)       # fast-exit: False if copy is absent
        └─ compute_file_hash(source_path)
        └─ compute_file_hash(copied_path)
              └─ compare hex strings → bool
```

`compute_file_hash` reads a file in 8 KB chunks, feeding each chunk into a SHA-256 digest, and returns the final hex string. It does not accumulate the full file content in memory.

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `_to_dir_name` | `str` | Filename with extension dot replaced by `_`, or bare stem |
| `rel_to_copy_path` | `str` | Copy-destination path with an inserted `{stem}_{ext}` directory |
| `copy_path_to_rel` | `str` | Original project-relative path with the inserted directory removed |
| `output_path_to_rel` | `str` | Project-relative path, with both the project-name prefix and the inserted directory removed |
| `resolve_file_output_dir` | `str` | Absolute path of the output directory for a given source file |
| `compute_file_hash` | `str` | SHA-256 hex digest of the file at the given path |
| `is_file_unchanged` | `bool` | `True` if source and copy have matching SHA-256 hashes; `False` if the copy is absent or hashes differ |

No files are written and no side effects are produced by any function in this module.

---

## 4. Key Data Structures

This module operates entirely on primitive strings and booleans; it defines no dataclasses, TypedDicts, or named domain structures. The only internal compound value is the list produced by path splitting:

### Path parts list (internal to `copy_path_to_rel`)

| Index | Type | Purpose |
|---|---|---|
| `parts[0...-2]` | `list[str]` | Parent directory components preceding the inserted `{stem}_{ext}` directory |
| `parts[-2]` | `str` | The inserted `{stem}_{ext}` directory, verified against `_to_dir_name(filename)` |
| `parts[-1]` | `str` | The original filename (basename), used as the restored final path component |

# Error Handling

## 1. Overall Strategy

This file adopts a **fail-fast with minimal silent fallback** approach. Most functions perform no internal error handling and allow exceptions to propagate directly to the caller. The single exception to this pattern is `is_file_unchanged`, which applies a deliberate, localized graceful degradation: a missing file at the copy destination is treated as a changed state rather than an error, returning `False` without raising an exception. All other operations—file I/O, hashing, and path computation—rely entirely on the calling layer (e.g., `pipeline.py`, `output.py`) to handle any failures.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `FileNotFoundError` / `OSError` | `compute_file_hash` opens a file that does not exist or is inaccessible | Not caught; propagates to caller | No (at this layer) | Caller must handle; unhandled propagation halts the operation |
| `FileNotFoundError` / `OSError` | `is_file_unchanged` attempts to hash `source_path` that is inaccessible | Not caught; propagates to caller | No (at this layer) | Caller must handle |
| Missing copy destination file | `is_file_unchanged` is called but `copied_path` does not exist | Explicitly checked via `os.path.exists`; returns `False` | Yes — treated as a changed file, triggering reprocessing | Downstream pipeline marks the file for reprocessing; no exception raised |
| Invalid or malformed path input | `rel_to_copy_path`, `copy_path_to_rel`, `output_path_to_rel`, `resolve_file_output_dir` receive unexpected path strings | No validation; functions return a best-effort result based on `os.path` behavior | Yes (path computed as-is) | Potentially incorrect output path; no exception raised unless `os.path` itself raises |
| `copy_path_to_rel` path not matching expected structure | The second-to-last path component does not match `_to_dir_name(filename)` | Falls back to returning the original `copy_path` unchanged | Yes — original path returned | Caller receives the unmodified input; no exception raised |

---

## 3. Design Notes

- **Explicit existence check over exception catching:** In `is_file_unchanged`, the absence of the copied file is a known, expected condition in the pipeline (a file has simply not been processed yet). Using `os.path.exists` rather than catching `FileNotFoundError` makes this intentional state explicit and keeps the function's return type clean (`bool`), avoiding exception-based control flow for a normal operational scenario.

- **No internal validation in path utilities:** The path transformation functions (`rel_to_copy_path`, `copy_path_to_rel`, `output_path_to_rel`) perform no input validation. This keeps them lightweight and purely computational, delegating correctness guarantees to callers. The `copy_path_to_rel` fallback (returning the input unchanged when the structure is unrecognized) is the only defensive measure, preventing silent data corruption from an unrecognized path format.

- **Delegation to callers for I/O errors:** File I/O errors in `compute_file_hash` and `is_file_unchanged` are fully propagated. Dependents such as `pipeline.py` contain the orchestration logic that determines whether such failures should halt processing or be logged and skipped, keeping error policy decisions out of this utility layer.

# Summary

**file_utils.py** converts project-relative paths to/from copy-destination paths and detects file changes via hashing.

**Public functions:**
- `rel_to_copy_path(rel_path: str) → str`
- `copy_path_to_rel(copy_path: str) → str`
- `output_path_to_rel(output_path: str) → str`
- `resolve_file_output_dir(base_output_dir: str, file_rel: str) → str`
- `compute_file_hash(file_path: str) → str`
- `is_file_unchanged(source_path: str, copied_path: str) → bool`

**Key data:** All inputs/outputs are primitive `str` or `bool`. Copy-destination paths follow `{parent}/{stem}_{ext}/{filename}` format.
