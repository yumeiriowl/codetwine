# Design Document: codetwine/utils/file_utils.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

`file_utils.py` is a utility module that centralises all file-system path manipulation and file-integrity logic used across the CodeTwine pipeline. It exists as a separate file so that the canonical rules for the **copy-destination directory structure** — where a `{stem}_{ext}` directory is inserted to prevent name collisions between files that share a stem but differ in extension (e.g. `utils.c` vs `utils.h`) — are defined in a single place and reused consistently by `output.py`, `pipeline.py`, `doc_creator.py`, and `dependency_graph.py`. Without this centralisation, each consumer would have to independently implement and maintain the same path-encoding/decoding logic.

## Public Interface

| Name | Arguments | Return Type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to the copy-destination directory structure path (`{parent}/{stem}_{ext}/{filename}`). |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; strips the inserted `{stem}_{ext}` directory to recover the original project-relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Strips the leading project-name segment from a `project_name/copy_path` string, then delegates to `copy_path_to_rel` to recover the relative path. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a given file by combining `base_output_dir` with the parent of the file's copy-destination path. |
| `compute_file_hash` | `file_path: str` | `str` | Computes and returns the SHA-256 hex digest of a file, reading in 8 KB chunks. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` if the copied file exists and its SHA-256 hash matches the source; treats a missing copy as changed. |

## Design Decisions

- **Single encoding rule, two directions.** The path encoding (`rel_to_copy_path`) and decoding (`copy_path_to_rel`) are co-located in this module. `output_path_to_rel` is a thin wrapper that strips the project-name prefix before calling `copy_path_to_rel`, keeping the inversion logic consistent.
- **Private helper `_to_dir_name`.** The directory-name generation rule (`stem_ext`) is factored into a private function used by both `rel_to_copy_path` (encoding) and `copy_path_to_rel` (decoding for verification), ensuring both sides use identical logic.
- **Collision avoidance by design.** Inserting the extension as a directory-name suffix is an explicit design decision documented in the docstrings of both `rel_to_copy_path` and `resolve_file_output_dir` to prevent output directory collisions for same-stem files with different extensions.
- **Incremental processing via hashing.** `is_file_unchanged` uses SHA-256 comparison (via `compute_file_hash`) rather than timestamps to reliably detect whether a source file has changed since it was last copied, enabling the pipeline to skip unchanged files.

## Definition Design Specifications

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

**Arguments:** `filename` — a bare filename (no directory components), e.g. `"settings.py"`.  
**Returns:** A directory-safe string where the extension dot is replaced with an underscore, or the filename unchanged if it has no extension.

Exists to produce a collision-free directory name from a filename so that files sharing the same stem but differing only in extension (e.g. `utils.c` / `utils.h`) map to distinct directories (`utils_c` / `utils_h`).

**Edge cases:** Files with no extension (e.g. `Makefile`) are returned as-is, so the stem itself serves as the directory name.

---

## `rel_to_copy_path(rel_path: str) -> str`

**Arguments:** `rel_path` — a project-root-relative path, e.g. `"repo_graphrag/llm/client.py"`.  
**Returns:** A copy-destination structured path of the form `{parent_dir}/{stem}_{ext}/{filename}`, e.g. `"repo_graphrag/llm/client_py/client.py"`. For top-level files the parent-directory prefix is omitted.

Exists to define the canonical mapping from a source file's relative path to its location inside the output directory tree, ensuring that same-named files with different extensions never collide at the destination.

**Design decision:** The extension-suffixed intermediate directory (`_to_dir_name`) is inserted immediately above the filename, preserving the rest of the original directory hierarchy verbatim so that the original path is trivially recoverable.

---

## `copy_path_to_rel(copy_path: str) -> str`

**Arguments:** `copy_path` — a copy-destination structured path as produced by `rel_to_copy_path`, e.g. `"repo_graphrag/llm/client_py/client.py"`.  
**Returns:** The original project-relative path, e.g. `"repo_graphrag/llm/client.py"`.

Exists as the exact inverse of `rel_to_copy_path`, allowing callers to recover a source-relative path from any stored or serialised copy-destination path.

**Design decision:** Before removing the inserted directory, the function verifies that the second-to-last path component actually matches `_to_dir_name(filename)`. If the check fails the input is returned unchanged, making the function safe to call on paths that were not produced by `rel_to_copy_path`.

**Edge cases:** Both forward slashes and backslashes are normalised to `/` before splitting, so Windows-style paths are handled correctly.

---

## `output_path_to_rel(output_path: str) -> str`

**Arguments:** `output_path` — a path in `"project_name/copy_destination_path"` format, e.g. `"js_project/src/emitter_js/emitter.js"`.  
**Returns:** The project-relative source path, e.g. `"src/emitter.js"`.

Exists as the inverse of `to_output_path()` (defined in `output.py`), stripping the project-name prefix and then delegating to `copy_path_to_rel` to recover the original relative path. Used throughout the codebase when reading back file references stored in JSON dependency records.

**Edge cases:** If the input contains no `/` separator the string is returned unchanged.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

**Arguments:**  
- `base_output_dir` — absolute path of the root output directory.  
- `file_rel` — project-root-relative file path, e.g. `"src/foo.py"`.  

**Returns:** The absolute path of the per-file output directory (the parent of where the copied file and its generated artefacts are written), e.g. `"{base_output_dir}/src/foo_py"`.

Exists to centralise the resolution of a file's output directory so that all callers (pipeline, doc creator, output builder) agree on the same location without duplicating the path-construction logic.

**Design decision:** Delegates path construction entirely to `rel_to_copy_path` and then takes its `dirname`, keeping the collision-avoidance guarantee of that function without re-implementing it.

---

## `compute_file_hash(file_path: str) -> str`

**Arguments:** `file_path` — absolute path of the file to hash.  
**Returns:** The SHA-256 digest of the file's contents as a lowercase hex string.

Exists to provide a content fingerprint used by `is_file_unchanged` to detect whether a source file differs from its copy in the output directory, avoiding redundant re-processing.

**Design decision:** The file is read in fixed 8 KB chunks rather than loading it all at once, keeping memory usage bounded for large files.

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

**Arguments:**  
- `source_path` — absolute path of the original file in the project.  
- `copied_path` — absolute path of the corresponding copy in the output directory.  

**Returns:** `True` if both files exist and their SHA-256 hashes are identical; `False` otherwise.

Exists to let the processing pipeline skip files that have not changed since the last run, enabling incremental processing.

**Design decision:** A missing copy at the destination is treated as "changed" (returns `False`) rather than raising an error, so callers can uniformly treat the return value as a "needs processing" signal without separately checking for the file's existence.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. It relies solely on standard library modules (`os` and `hashlib`) to implement its path manipulation and file hashing utilities.

---

### Dependents (what uses this file)

Several files across the project import utilities from this module. The dependency direction is strictly unidirectional: all dependents consume from this file; this file does not import from any of them.

**`codetwine/output.py`**
Uses `rel_to_copy_path` to construct output paths in `"project_name/copy_path"` format for individual files. Uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file list entries. Uses `output_path_to_rel` to recover project-relative paths from stored output paths when resolving caller/callee dependency entries. Uses `copy_path_to_rel` internally for the same path-reversal purpose within its own Mermaid diagram building logic.

**`codetwine/pipeline.py`**
Uses `resolve_file_output_dir` to determine where each file's processed output (copied source, dependency JSON, etc.) should be written and read from. Uses `is_file_unchanged` to detect which files need reprocessing by comparing the source file's hash against its previously copied version. Uses `copy_path_to_rel` to convert stored copy-destination paths back to project-relative paths during dependency normalization.

**`codetwine/doc_creator.py`**
Uses `resolve_file_output_dir` to locate the output directory for a given file when loading or writing design documents. Uses `output_path_to_rel` to convert caller and callee file paths (stored in output-path format) into human-readable project-relative paths when assembling documentation text.

**`codetwine/extractors/dependency_graph.py`**
Uses `rel_to_copy_path` to format file paths into `"project_name/copy_path"` format when recording each file's entry, callers, and callees in the dependency graph output structure.

## Data Flow

# Data Flow

## Overview

This file provides path-conversion and file-integrity utilities. Data flows through a chain of pure string transformations (no I/O except in the hash functions).

---

## Path Transformation Pipeline

```
Project-relative path (rel_path)
        │
        ▼
  rel_to_copy_path()
        │  Inserts a {stem}_{ext} directory between the parent dir and filename
        ▼
Copy-destination path  e.g. "repo_graphrag/llm/client_py/client.py"
        │
        ▼  (prepended with project_name by callers in output.py / dependency_graph.py)
Output path  e.g. "my_project/repo_graphrag/llm/client_py/client.py"
```

**Inverse direction:**

```
Output path
        │
        ▼  output_path_to_rel()  — strips the project_name prefix, then delegates
        │
        ▼  copy_path_to_rel()   — removes the inserted {stem}_{ext} directory
        │
        ▼
Project-relative path
```

---

## Input / Output Formats

| Function | Input | Output |
|---|---|---|
| `_to_dir_name(filename)` | bare filename (`"utils.py"`) | directory-safe name (`"utils_py"`) |
| `rel_to_copy_path(rel_path)` | project-relative path | copy-destination path with `{stem}_{ext}` directory inserted |
| `copy_path_to_rel(copy_path)` | copy-destination path | project-relative path |
| `output_path_to_rel(output_path)` | `"project_name/copy_dest_path"` | project-relative path |
| `resolve_file_output_dir(base_output_dir, file_rel)` | base dir + relative path | absolute output directory path |
| `compute_file_hash(file_path)` | absolute file path | SHA-256 hex string |
| `is_file_unchanged(source_path, copied_path)` | two absolute file paths | `bool` |

---

## Path Structure Transformation Detail

```
Input rel_path:  "repo_graphrag/llm/client.py"

os.path.dirname  →  parent_dir = "repo_graphrag/llm"
os.path.basename →  filename   = "client.py"
_to_dir_name     →  dir_name   = "client_py"
                                      │
                                      ▼
Result: "repo_graphrag/llm/client_py/client.py"
         └─────────────┘ └────────┘ └────────┘
           parent_dir     dir_name   filename
```

`copy_path_to_rel` reverses this by checking whether `parts[-2] == _to_dir_name(parts[-1])`; if so, `parts[-2]` is dropped.

---

## File Integrity Check Flow

```
source_path ──► compute_file_hash() ──┐
                                      ├──► hashes equal? ──► bool
copied_path ──► compute_file_hash() ──┘
     │
     └──► os.path.exists() == False ──► return False (treated as changed)
```

Files are read in **8 KB chunks** and fed into a `hashlib.sha256` object; the final `hexdigest()` string is compared between the two paths.

---

## Data Consumers (Dependents)

| Dependent file | Functions consumed | Purpose |
|---|---|---|
| `codetwine/output.py` | `rel_to_copy_path`, `copy_path_to_rel`, `output_path_to_rel`, `resolve_file_output_dir` | Build output paths and restore relative paths for summary/dependency maps |
| `codetwine/pipeline.py` | `resolve_file_output_dir`, `copy_path_to_rel`, `is_file_unchanged` | Detect changed files and locate per-file output directories |
| `codetwine/doc_creator.py` | `resolve_file_output_dir`, `output_path_to_rel` | Locate doc output dirs; convert paths in usage annotations |
| `codetwine/extractors/dependency_graph.py` | `rel_to_copy_path` | Construct `"project_name/copy_path"` keys for caller/callee entries in the dependency graph |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **fail-fast** approach. No exceptions are caught or suppressed internally; all errors propagate directly to the caller. The functions contain no `try/except` blocks, relying instead on explicit precondition checks (e.g., verifying file existence before hashing) to guard against predictable failure cases at defined boundaries.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Missing copied file (`os.path.exists` returns `False`) | Returns `False` explicitly without raising | Caller treats the file as changed; triggers reprocessing |
| File I/O errors (e.g., `open` on a non-existent or unreadable file) | Not caught; propagates as `OSError`/`IOError` to caller | Caller (e.g., `pipeline.py`) is responsible for handling or reporting the failure |
| Invalid or malformed path strings | Not caught; propagates as whatever the underlying `os.path` or string operation raises | Path construction behaves correctly for well-formed inputs; malformed input consequences are the caller's responsibility |
| Hash computation failure | Not caught; propagates from `hashlib` or file reading | Comparison cannot proceed; exception surfaces to the caller |

## Design Considerations

The single explicit guard — checking for the existence of the copied file before comparing hashes in `is_file_unchanged` — represents the only deliberate defensive check in the file. This check has a specific semantic meaning (absence equals "changed") rather than being error recovery. All other failure modes are left entirely to the OS and standard library to surface, consistent with a utility layer that expects its callers (`pipeline.py`, `output.py`, `doc_creator.py`) to own error handling and recovery logic for their respective workflows.

## Summary

`file_utils.py` centralises file-system path manipulation and file-integrity logic for the CodeTwine pipeline. Its core responsibility is encoding/decoding a collision-avoidance directory structure where a `{stem}_{ext}` directory is inserted above each file. Public functions: `rel_to_copy_path` encodes a project-relative path; `copy_path_to_rel` inverts it; `output_path_to_rel` strips the project-name prefix then delegates to `copy_path_to_rel`; `resolve_file_output_dir` returns the absolute per-file output directory; `compute_file_hash` produces a SHA-256 digest; `is_file_unchanged` compares source and copy hashes to enable incremental processing. Depends only on `os` and `hashlib`.
