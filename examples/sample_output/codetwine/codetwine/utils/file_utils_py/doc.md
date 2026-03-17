# Design Document: codetwine/utils/file_utils.py

## Overview & Purpose

# Overview & Purpose

## Role

`file_utils.py` is a utility module that centralizes all file-path transformation and file-integrity logic used across the codetwine pipeline. It exists as a separate file so that the bidirectional mapping between a project-relative path and the copy-destination directory structure is defined in exactly one place, preventing drift between the components that write output (`pipeline.py`, `output.py`, `dependency_graph.py`) and those that read it back (`doc_creator.py`, `output.py`).

---

## Public Interface

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to a copy-destination path of the form `{parent_dir}/{stem}_{ext}/{filename}`. |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; strips the inserted `{stem}_{ext}` directory to recover the original relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Strips the leading project-name segment from a `project_name/copy_path` string, then delegates to `copy_path_to_rel`. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a given file by combining `base_output_dir` with the parent portion of its copy-destination path. |
| `compute_file_hash` | `file_path: str` | `str` | Computes and returns the SHA-256 hex digest of a file, reading it in 8 KB chunks. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` when the copied file exists and its SHA-256 hash matches the source file; `False` otherwise. |

---

## Design Decisions

- **Collision-avoidance via `{stem}_{ext}` directories.** Both `rel_to_copy_path` and `resolve_file_output_dir` insert a directory named `{stem}_{ext}` (e.g. `utils_c`, `utils_h`) so that files sharing a stem but differing in extension (e.g. `utils.c` and `utils.h`) are placed in distinct output directories without colliding.

- **Single private helper `_to_dir_name`.** The directory-name derivation rule (replace the `.` in the extension with `_`) is isolated in the private function `_to_dir_name`, which is reused by both `rel_to_copy_path` and `copy_path_to_rel` to guarantee the forward and inverse transforms apply exactly the same rule.

- **Strictly invertible path transforms.** `rel_to_copy_path` / `copy_path_to_rel` and `output_path_to_rel` form explicit inverse pairs, enabling the pipeline to freely round-trip between internal storage paths and project-relative paths without any external lookup.

- **Streaming hash computation.** `compute_file_hash` reads files in 8 KB chunks rather than loading the entire file into memory, keeping memory usage bounded regardless of file size.

## Definition Design Specifications

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

**Arguments:**
- `filename`: A bare filename (no directory component), e.g. `"settings.py"`.

**Returns:** `str` — The filename with the extension separator `.` replaced by `_`. Files without extensions are returned unchanged.

**Responsibility:** Produces a filesystem-safe directory name that encodes both the stem and the extension of a file, used as the intermediate directory component in the copy-destination path structure.

**Design decision:** Replacing `.` with `_` rather than omitting the extension ensures that files sharing a stem but differing in extension (e.g. `utils.c` / `utils.h`) map to distinct directory names, preventing collisions. Files without an extension (e.g. `Makefile`) are returned as-is because there is no `.` to replace.

**Edge cases:** Only the leading `.` of the extension is dropped (`ext[1:]`); the extension itself may contain no additional dots. The function operates on bare filenames only — callers are responsible for stripping directory components before passing the value.

---

## `rel_to_copy_path(rel_path: str) -> str`

**Arguments:**
- `rel_path`: A project-relative file path, e.g. `"repo_graphrag/llm/client.py"`.

**Returns:** `str` — A path in the format `{parent_dir}/{stem}_{ext}/{filename}` (or `{stem}_{ext}/{filename}` for top-level files).

**Responsibility:** Converts a project-relative path into the copy-destination directory structure that `process_single_file` uses when placing source files in the output tree.

**Design decision:** The extra `{stem}_{ext}` directory level prevents two files with the same name but different extensions from writing into the same output directory. Preserving the original `parent_dir` prefix keeps the output tree's namespace aligned with the project's directory hierarchy.

**Edge cases:** Top-level files (no parent directory) omit the leading `/` separator. The function delegates extension-to-suffix conversion entirely to `_to_dir_name`, so extension-less files produce a directory with the same name as the file itself (e.g. `Makefile/Makefile`).

---

## `copy_path_to_rel(copy_path: str) -> str`

**Arguments:**
- `copy_path`: A copy-destination directory structure path as produced by `rel_to_copy_path`, e.g. `"repo_graphrag/llm/client_py/client.py"`.

**Returns:** `str` — The original project-relative path, e.g. `"repo_graphrag/llm/client.py"`.

**Responsibility:** Inverts `rel_to_copy_path` by removing the inserted `{stem}_{ext}` directory component to recover the original relative path.

**Design decision:** The inserted directory is identified by checking whether the second-to-last path component equals `_to_dir_name(filename)`. This avoids any string parsing of the extension encoding and remains consistent with however `_to_dir_name` is defined. Backslashes are normalised to forward slashes before splitting so that Windows-style paths are handled uniformly.

**Edge cases:** If the path has fewer than two components, or if the second-to-last component does not match `_to_dir_name(filename)` (i.e. the path was not produced by `rel_to_copy_path`), the input is returned unchanged. The function does not validate that the path actually exists on disk.

---

## `output_path_to_rel(output_path: str) -> str`

**Arguments:**
- `output_path`: A path in `"project_name/copy_destination_path"` format, e.g. `"js_project/src/emitter_js/emitter.js"`.

**Returns:** `str` — The project-relative source path, e.g. `"src/emitter.js"`.

**Responsibility:** Inverts `to_output_path()` from `output.py` by stripping the leading project-name component and then delegating to `copy_path_to_rel` to recover the original relative path.

**Design decision:** The split is limited to the first `/` (`split("/", 1)`) so that the remainder is passed intact to `copy_path_to_rel`, regardless of how many directory levels the copy path contains.

**Edge cases:** If the input contains no `/` separator, the path is returned unchanged. The function does not validate that the project-name prefix is correct.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

**Arguments:**
- `base_output_dir`: The absolute base output directory path.
- `file_rel`: A project-relative file path, e.g. `"src/foo.py"`.

**Returns:** `str` — The absolute path of the per-file output directory, e.g. `"{base_output_dir}/src/foo_py"`.

**Responsibility:** Derives the absolute output directory for a single file by combining the base output directory with the parent portion of the file's copy-destination path, giving each file an isolated output directory that matches the copy-destination structure.

**Design decision:** Reusing `rel_to_copy_path` and taking its `dirname` keeps this function strictly consistent with the path structure defined there; the extension-suffix collision-avoidance property is inherited automatically.

---

## `compute_file_hash(file_path: str) -> str`

**Arguments:**
- `file_path`: Absolute path of the file to hash.

**Returns:** `str` — The SHA-256 digest of the file contents as a lowercase hexadecimal string.

**Responsibility:** Provides a content fingerprint for a file so that callers can detect whether the file has changed since a previous operation.

**Design decision:** Files are read in 8 KB chunks to bound memory usage regardless of file size. SHA-256 is chosen as the hash algorithm; its collision resistance is sufficient for change-detection purposes within this tool.

**Edge cases:** Raises an `OSError` (or subclass) if `file_path` does not exist or is not readable. The file is opened in binary mode, so line-ending differences between platforms are reflected in the hash.

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

**Arguments:**
- `source_path`: Absolute path of the original file in the project.
- `copied_path`: Absolute path of the corresponding copy in the output directory.

**Returns:** `bool` — `True` if both files exist and their SHA-256 hashes match; `False` otherwise.

**Responsibility:** Determines whether a source file's content is identical to its previously copied counterpart, enabling the pipeline to skip re-processing files that have not changed.

**Design decision:** A missing copy is treated as "changed" (`False`) rather than raising an error, so callers can unconditionally add files to the processing queue when no prior output exists without requiring separate existence checks.

**Edge cases:** Only the copy's existence is explicitly checked; if `source_path` does not exist, `compute_file_hash` will raise an `OSError`. The comparison is purely content-based (hash equality) and does not consider file timestamps or metadata.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. It relies solely on standard library modules (`os` and `hashlib`) to implement its utility functions.

---

### Dependents (what uses this file)

Multiple files across the project depend on this file's utilities for path conversion and file comparison.

- **`codetwine/output.py`**
  Uses `rel_to_copy_path` to construct output paths in `"project_name/copy_path"` format. Uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists. Uses `output_path_to_rel` and `copy_path_to_rel` to convert stored output paths back to project-relative paths when resolving dependency relationships and building diagram data.

- **`codetwine/pipeline.py`**
  Uses `resolve_file_output_dir` to determine where each file's output artifacts (copied source, dependency JSON) reside. Uses `is_file_unchanged` to detect which files have changed since the last run by comparing source and copied file hashes, avoiding redundant processing. Uses `copy_path_to_rel` to convert copy-destination paths back to project-relative paths when normalizing dependency data.

- **`codetwine/doc_creator.py`**
  Uses `resolve_file_output_dir` to locate the output directory for a given file when loading its design document. Uses `output_path_to_rel` to convert caller/callee file paths stored in dependency records back to human-readable project-relative paths when composing documentation text.

- **`codetwine/extractors/dependency_graph.py`**
  Uses `rel_to_copy_path` to format file, caller, and callee entries in the dependency graph output, constructing paths in `"project_name/copy_destination_path"` format that are consistent with the rest of the pipeline's path conventions.

**Direction of dependency:** All dependencies are unidirectional — `file_utils.py` is a pure utility module that does not import from any of the files listed above.

## Data Flow

# Data Flow

## Overview

This file provides utility functions for converting between file path representations and computing file hashes. All functions are pure transformations with no side effects (except `compute_file_hash` and `is_file_unchanged`, which read from disk).

---

## Path Representation Types

| Name | Format | Example |
|---|---|---|
| `rel_path` | Project-relative path | `repo_graphrag/llm/client.py` |
| `copy_path` | Inserted `{stem}_{ext}` dir before filename | `repo_graphrag/llm/client_py/client.py` |
| `output_path` | `{project_name}/{copy_path}` | `my_project/repo_graphrag/llm/client_py/client.py` |

---

## Transformation Flow

```
rel_path
   │
   ▼  rel_to_copy_path()
copy_path  ─────────────────────────────────────────►  copy_path_to_rel()  ──►  rel_path
   │                                                         ▲
   │  output_path = "{project_name}/" + copy_path            │
   │                                                         │
output_path  ──────────────────────────────►  output_path_to_rel()  ──►  rel_path
                                                (strips project prefix, then delegates to copy_path_to_rel)
```

**Key transformation logic (`_to_dir_name`):**
- `settings.py` → `settings_py` (replaces `.` in extension with `_`)
- `Makefile` → `Makefile` (no extension, returned as-is)

This inserted directory name is the single transformation artifact; its removal is how `copy_path_to_rel` recovers the original path. Recovery is verified by checking `parts[-2] == _to_dir_name(parts[-1])`.

---

## `resolve_file_output_dir` Data Flow

```
(base_output_dir, file_rel)
        │
        ▼  rel_to_copy_path(file_rel)
   copy_path
        │
        ▼  os.path.dirname(copy_path)
   parent portion (e.g. "src/emitter_js")
        │
        ▼  os.path.join(base_output_dir, ...)
   absolute output directory path
```

Callers (`output.py`, `pipeline.py`, `doc_creator.py`) use this directory to locate or write per-file artifacts (`doc.json`, `file_dependencies.json`, copied source file).

---

## File Hash Flow

```
file_path (absolute)
     │
     ▼  read in 8 KB chunks
SHA256 hash object  ──►  hexdigest string
```

`is_file_unchanged` takes two absolute paths, computes SHA256 for each, and returns `True` if hashes match. Returns `False` immediately if the copied file does not exist on disk.

```
(source_path, copied_path)
        │
        ├─ copied_path does not exist ──►  False
        │
        ▼
compute_file_hash(source_path) == compute_file_hash(copied_path)
        │
        ▼
      bool
```

Used in `pipeline.py` to detect whether a source file has changed since its last copy.

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **fail-fast** approach. No exceptions are caught or suppressed internally; all errors propagate directly to the caller. There is no graceful degradation logic within this module. The sole exception to pure fail-fast behavior is in `is_file_unchanged`, which performs a single explicit pre-check and returns a defined sentinel value rather than raising, but this is a deliberate design choice for a boolean predicate function rather than error suppression.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| `FileNotFoundError` / `OSError` from `open()` in `compute_file_hash` | Not caught; propagates to caller | Unhandled at this layer; callers (e.g., `pipeline.py`) must handle or accept propagation |
| Missing copy file in `is_file_unchanged` | Explicit existence check via `os.path.exists`; returns `False` (treated as "changed") without raising | Safely signals the file is absent; callers proceed to process the file as if it changed |
| Invalid or malformed path strings in path conversion functions (`rel_to_copy_path`, `copy_path_to_rel`, `output_path_to_rel`) | Not validated; behavior depends on `os.path` stdlib functions with whatever input is provided | Malformed input may produce unexpected output paths silently rather than raising |
| `OSError` from `os.path` operations (e.g., `os.path.dirname`, `os.path.basename`) | Not caught; propagates to caller | Unlikely with string inputs, but any OS-level failure propagates upward |

## Design Considerations

The absence of internal error handling is a deliberate boundary decision: this file is a low-level utility module, and error handling responsibility is delegated entirely to callers such as `pipeline.py`, `output.py`, and `doc_creator.py`. This keeps each utility function focused on a single transformation or computation without mixing error-recovery logic into path and hash utilities.

The `is_file_unchanged` sentinel return (`False` on missing file) is consistent with its semantic contract as a boolean predicate — a missing copy is a well-defined, expected state during first-run or incremental processing, not an error condition. This design allows callers like `pipeline.py` to treat it as a simple conditional without needing exception handling.

## Summary

`file_utils.py` centralizes file-path transformation and file-integrity logic for the codetwine pipeline. It provides bidirectional mapping between project-relative paths (`rel_path`), copy-destination paths (`copy_path`, inserting a `{stem}_{ext}` directory to prevent collisions), and output paths (`{project_name}/{copy_path}`). Public functions: `rel_to_copy_path`, `copy_path_to_rel`, `output_path_to_rel`, `resolve_file_output_dir`, `compute_file_hash` (SHA-256, 8 KB chunks), and `is_file_unchanged` (returns `False` if copy is missing). Depends only on `os` and `hashlib`. All errors propagate to callers except missing-copy in `is_file_unchanged`.
