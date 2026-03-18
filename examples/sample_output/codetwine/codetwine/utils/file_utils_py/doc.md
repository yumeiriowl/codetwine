# Design Document: codetwine/utils/file_utils.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Provides utility functions for converting between project-relative file paths and the copy-destination directory structure used throughout the CodeTwine pipeline, along with file hashing helpers to detect source file changes.

## 2. When to Use This Module

- **Converting a relative path to its output directory path**: Call `resolve_file_output_dir(base_output_dir, file_rel)` to get the absolute output directory where a file's artifacts (e.g., `doc.json`, `file_dependencies.json`, copied source) are stored.
- **Constructing a copy-destination path from a relative path**: Call `rel_to_copy_path(rel_path)` to produce the `{parent_dir}/{stem}_{ext}/{filename}` path string used when placing a file in the output directory structure.
- **Recovering a project-relative path from a copy-destination path**: Call `copy_path_to_rel(copy_path)` to strip the inserted `{stem}_{ext}` directory segment and restore the original relative path.
- **Recovering a project-relative path from a full output path**: Call `output_path_to_rel(output_path)` to strip the leading project-name segment from a `{project_name}/{copy_destination_path}` string and return the original relative path.
- **Detecting whether a source file has changed since it was last copied**: Call `is_file_unchanged(source_path, copied_path)` to compare SHA256 hashes of the original and its copy, driving incremental processing in the pipeline.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path: str` | `str` | Converts a project-relative path to the copy-destination directory structure path `{parent_dir}/{stem}_{ext}/{filename}`. |
| `copy_path_to_rel` | `copy_path: str` | `str` | Inverse of `rel_to_copy_path`; removes the inserted `{stem}_{ext}` directory to restore the original relative path. |
| `output_path_to_rel` | `output_path: str` | `str` | Strips the leading project-name segment from a `{project_name}/{copy_destination_path}` string and delegates to `copy_path_to_rel`. |
| `resolve_file_output_dir` | `base_output_dir: str`, `file_rel: str` | `str` | Returns the absolute output directory path for a file by combining `base_output_dir` with the parent portion of `rel_to_copy_path`. |
| `compute_file_hash` | `file_path: str` | `str` | Computes and returns the SHA256 hash of a file as a hex string, reading in 8 KB chunks. |
| `is_file_unchanged` | `source_path: str`, `copied_path: str` | `bool` | Returns `True` if the SHA256 hash of the original file matches that of its copy; returns `False` if the copy does not exist. |

## 4. Design Decisions

- **Extension-as-suffix directory naming**: The `{stem}_{ext}` intermediate directory (e.g., `client_py/` for `client.py`) is deliberately inserted to avoid collisions between files that share a stem but differ in extension (e.g., `utils.c` and `utils.h`). This convention is shared consistently across `rel_to_copy_path`, `copy_path_to_rel`, and `resolve_file_output_dir`, making the path transformations mutually invertible.
- **Symmetric path conversion**: `rel_to_copy_path` and `copy_path_to_rel` are designed as inverses of each other, as are `output_path_to_rel` and `to_output_path` (defined externally in `output.py`), enabling round-trip path resolution across the pipeline.
- **Hash-based change detection rather than timestamps**: `is_file_unchanged` uses SHA256 content hashing instead of file modification timestamps, making change detection robust against clock skew or copy operations that reset timestamps.

## Definition Design Specifications

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

**Responsibility:** Produces a filesystem-safe directory name from a filename by replacing the dot separator between stem and extension with an underscore. This is the canonical naming rule shared across all path-conversion functions in this module.

**When to use:** Called internally whenever a filename must be converted to its corresponding directory segment in the copy-destination path structure.

**Design decisions:**
- Files without extensions (e.g. `Makefile`) are returned unchanged, so they still produce a valid directory name.
- Only the leading dot of the extension is removed; the extension characters themselves are preserved verbatim.

**Constraints & edge cases:**
- `filename` must be a bare filename (no directory separators); passing a full path yields an incorrect result.
- Multiple dots in the stem (e.g. `archive.tar.gz`) are handled by `os.path.splitext`, which treats only the last dot as the extension separator.

---

## `rel_to_copy_path(rel_path: str) -> str`

**Responsibility:** Converts a project-relative file path to the copy-destination directory structure path, inserting a `{stem}_{ext}` directory so that files sharing the same name but different extensions never collide at the same destination.

**When to use:** Called whenever a relative source path must be mapped to the path it will occupy inside the output directory (e.g. when constructing output paths in `output.py` and `dependency_graph.py`).

**Design decisions:**
- The inserted directory is always the immediate parent of the filename; deeper ancestor directories are preserved as-is.
- Top-level files (no parent directory) and nested files follow the same `{dir_name}/{filename}` leaf pattern; the difference is whether a prefix is prepended.

**Constraints & edge cases:**
- `rel_path` must use forward slashes or the platform separator; mixing separators is not normalised internally.
- The function does not verify that `rel_path` refers to an existing file.

---

## `copy_path_to_rel(copy_path: str) -> str`

**Responsibility:** Reverses `rel_to_copy_path`, stripping the inserted `{stem}_{ext}` directory to recover the original project-relative path.

**When to use:** Called when a path read from output structures (JSON dependency files, Mermaid diagrams, etc.) must be converted back to a source-relative path for further processing.

**Design decisions:**
- Backslashes are normalised to forward slashes before splitting, making the function safe for paths originating on Windows.
- The removal of the inserted directory is conditional: the second-to-last segment is only dropped if it exactly matches `_to_dir_name(filename)`, so non-standard paths are returned unchanged rather than silently corrupted.

**Constraints & edge cases:**
- Paths with fewer than two segments are returned as-is without modification.
- If a path was not produced by `rel_to_copy_path` but happens to have a matching second-to-last segment, it will be incorrectly stripped.

---

## `output_path_to_rel(output_path: str) -> str`

**Responsibility:** Strips the leading project-name prefix from an output path (format `project_name/copy_destination_path`) and delegates to `copy_path_to_rel` to recover the source-relative path.

**When to use:** Called when dependency entries or documentation references carry full output paths that must be resolved back to their original source locations (used in `output.py` and `doc_creator.py`).

**Design decisions:**
- Only the first `/` is used as the split point, so project names containing slashes are unsupported but nested copy-destination paths are handled correctly.
- Paths that contain no `/` are returned unchanged, providing a safe fallback.

**Constraints & edge cases:**
- Assumes the project name itself contains no forward slash.
- Does not validate that the prefix actually matches any known project name.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

**Responsibility:** Computes the absolute output directory path for a given source file by combining the base output directory with the parent portion of the file's copy-destination path.

**When to use:** Called before writing any per-file output artifacts (documentation JSON, dependency JSON, copied source) to determine which directory to create and target.

**Design decisions:**
- Delegates entirely to `rel_to_copy_path` for the path structure, so the collision-avoidance guarantee of that function is inherited automatically.
- Returns the *directory* (not the file path) by taking `os.path.dirname` of the full copy path, which callers then use with `os.makedirs`.

**Constraints & edge cases:**
- `base_output_dir` should be an absolute path; a relative base yields a relative result.
- The returned directory does not necessarily exist; callers are responsible for creating it.

---

## `compute_file_hash(file_path: str) -> str`

**Responsibility:** Produces a SHA-256 hex digest of a file's binary contents, used as a stable fingerprint for change detection.

**When to use:** Called directly by `is_file_unchanged` and potentially by any caller needing a content-based identifier for a file.

**Design decisions:**
- Reads the file in fixed 8 KB chunks to bound memory usage regardless of file size.
- Returns the raw hex string with no additional metadata, keeping the output directly comparable between two calls.

**Constraints & edge cases:**
- `file_path` must point to an existing, readable file; an absent or unreadable file raises an OS-level exception.
- Hash correctness depends on the file not being modified between chunk reads (no locking is applied).

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

**Responsibility:** Determines whether a source file and its copy in the output directory have identical content, enabling incremental processing to skip files that have not changed.

**When to use:** Called during the change-detection phase of the pipeline (in `pipeline.py`) to decide which files need to be reprocessed.

**Design decisions:**
- A missing copy is treated as "changed" (`False`) rather than raising an error, so newly added files are automatically included in the processing set without special-casing.
- Uses SHA-256 content hashing rather than timestamps to avoid false negatives from filesystem clock skew or copy operations that preserve modification times.

**Constraints & edge cases:**
- Both paths must be absolute (or consistently relative) for the comparison to be meaningful.
- Does not handle the case where `source_path` itself is absent; that raises an exception from `compute_file_hash`.
- Hash collisions are theoretically possible but treated as negligible for this use case.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `hashlib`) and defines utility functions used by other modules in the project.

---

## Dependents (modules that import this file)

The following project-internal modules depend on `file_utils.py`:

- **`codetwine/output.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `rel_to_copy_path` to construct output paths in `"project_name/copy_path"` format; uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists; uses `output_path_to_rel` to recover source-relative paths from output-format paths when resolving dependency relationships; uses `copy_path_to_rel` to strip the inserted `{stem}_{ext}` directory segment and recover the original relative path.

- **`codetwine/pipeline.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `resolve_file_output_dir` to determine the output directory for each file during change detection and file processing; uses `is_file_unchanged` to compare SHA256 hashes of source and copied files to detect changes; uses `copy_path_to_rel` to convert copy-destination paths back to project-relative paths when normalizing internal path representations.

- **`codetwine/doc_creator.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `resolve_file_output_dir` to locate the output directory for a given file when creating design documents; uses `output_path_to_rel` to display human-readable source-relative paths when formatting callee and caller usage entries in documentation.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils_py/file_utils.py`** : Uses `rel_to_copy_path` to construct `"project_name/copy_path"` format strings for the `file`, `callers`, and `callees` fields when building the dependency graph file-info list.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/output.py` → `file_utils.py` (one-way)
- `codetwine/pipeline.py` → `file_utils.py` (one-way)
- `codetwine/doc_creator.py` → `file_utils.py` (one-way)
- `codetwine/extractors/dependency_graph.py` → `file_utils.py` (one-way)

`file_utils.py` itself imports no other project-internal modules, making it a **pure utility leaf module** in the dependency graph. All dependencies flow inward toward this file; it has no outward project-internal dependencies.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Function(s) | Format |
|-------|-------------|--------|
| `filename` | `_to_dir_name` | Plain string filename (e.g. `"settings.py"`, `"Makefile"`) |
| `rel_path` | `rel_to_copy_path`, `resolve_file_output_dir` | Relative path string from the project root (e.g. `"repo_graphrag/llm/client.py"`) |
| `copy_path` | `copy_path_to_rel` | Copy-destination directory structure path string (e.g. `"repo_graphrag/llm/client_py/client.py"`) |
| `output_path` | `output_path_to_rel` | Path string in `"project_name/copy_destination_path"` format |
| `base_output_dir` | `resolve_file_output_dir` | Absolute path string of the base output directory |
| `file_path` | `compute_file_hash` | Absolute path string of a file to read from disk |
| `source_path`, `copied_path` | `is_file_unchanged` | Absolute path strings of two files to read from disk |

No configuration values or global state are consumed; all inputs are provided as function arguments or read directly from the filesystem.

---

## 2. Transformation Overview

### Path encoding pipeline (`rel_to_copy_path` / `resolve_file_output_dir`)

```
rel_path (e.g. "repo_graphrag/llm/client.py")
    │
    ├─ os.path.dirname  → parent_dir ("repo_graphrag/llm")
    ├─ os.path.basename → filename   ("client.py")
    │
    └─ _to_dir_name(filename)
           │
           ├─ os.path.splitext → stem ("client"), ext (".py")
           └─ ext present? → dir_name = "client_py"
                              ext absent? → dir_name = filename as-is
    │
    ├─ parent_dir present →  "repo_graphrag/llm/client_py/client.py"  (copy_path)
    └─ top-level file    →  "client_py/client.py"                     (copy_path)

resolve_file_output_dir:
    copy_path → os.path.dirname → "repo_graphrag/llm/client_py"
              → os.path.join(base_output_dir, ...) → absolute output directory path
```

### Path decoding pipeline (`copy_path_to_rel`)

```
copy_path (e.g. "repo_graphrag/llm/client_py/client.py")
    │
    └─ split on "/" → parts ["repo_graphrag", "llm", "client_py", "client.py"]
           │
           ├─ filename = parts[-1]          ("client.py")
           ├─ _to_dir_name(filename)        → "client_py"
           └─ parts[-2] == "client_py"? YES
                  → drop parts[-2], rejoin + filename
                  → "repo_graphrag/llm/client.py"
```

### Output path decoding pipeline (`output_path_to_rel`)

```
output_path (e.g. "js_project/src/emitter_js/emitter.js")
    │
    └─ split on "/" with maxsplit=1
           → project_name = "js_project"   (discarded)
           → remainder    = "src/emitter_js/emitter.js"
    │
    └─ copy_path_to_rel(remainder) → "src/emitter.js"
```

### File hashing pipeline (`compute_file_hash` / `is_file_unchanged`)

```
file_path
    │
    └─ open in binary mode → read in 8 KB chunks → feed each chunk to SHA256
    └─ hexdigest() → 64-character hex string

is_file_unchanged:
    source_path  → compute_file_hash → hash_a
    copied_path  → exists? NO → return False
                 → compute_file_hash → hash_b
    hash_a == hash_b → True / False
```

---

## 3. Outputs

| Output | Function(s) | Format |
|--------|-------------|--------|
| Directory name with `_`-joined extension | `_to_dir_name` | String (e.g. `"client_py"`, `"Makefile"`) |
| Copy-destination path | `rel_to_copy_path` | String in `{parent_dir}/{stem}_{ext}/{filename}` format |
| Restored relative path | `copy_path_to_rel`, `output_path_to_rel` | String relative path from project root |
| Absolute output directory path | `resolve_file_output_dir` | Absolute filesystem path string |
| SHA256 digest | `compute_file_hash` | 64-character lowercase hex string |
| Unchanged flag | `is_file_unchanged` | `bool` — `True` if both file hashes match, `False` if copy is absent or hashes differ |

No files are written by this module. The only filesystem side effects are **reads**: binary reads of source and copied files in `compute_file_hash` / `is_file_unchanged`.

---

## 4. Key Data Structures

This module operates exclusively on primitive Python types. The structures below document the implicit schemas carried through function boundaries.

### Implicit path segment list (inside `copy_path_to_rel`)

Produced by splitting the copy path on `"/"`:

| Index | Type | Purpose |
|-------|------|---------|
| `parts[0..n-3]` | `list[str]` | Parent directory components of the original relative path |
| `parts[-2]` | `str` | The inserted `{stem}_{ext}` directory name; removed if it matches `_to_dir_name(parts[-1])` |
| `parts[-1]` | `str` | The original filename (e.g. `"client.py"`) |

### Implicit two-segment split (inside `output_path_to_rel`)

| Index | Type | Purpose |
|-------|------|---------|
| `parts[0]` | `str` | Project name prefix; discarded |
| `parts[1]` | `str` | Remainder copy-destination path forwarded to `copy_path_to_rel` |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **fail-fast** approach. No try-except blocks are present; all functions propagate exceptions directly to callers without catching or suppressing them. The sole explicit defensive check is a pre-condition guard in `is_file_unchanged`, which avoids operating on a nonexistent file by returning a defined sentinel value (`False`) rather than raising an exception. All other error conditions — invalid paths, unreadable files, OS-level failures — are left to Python's standard runtime exception mechanism and surface immediately to the calling layer.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `FileNotFoundError` / `OSError` | `compute_file_hash` is called with a path that does not exist or is not readable | Not caught; propagates to caller | No | Calling pipeline or doc creation step fails at that point |
| Missing copy file | `is_file_unchanged` is called and the copied file does not exist at the destination | Returns `False` (treated as changed) without raising | Yes | The file is marked as changed and scheduled for reprocessing |
| `OSError` / path errors | `rel_to_copy_path`, `copy_path_to_rel`, `resolve_file_output_dir`, or `output_path_to_rel` receive malformed or OS-invalid path strings | Not caught; propagates to caller | No | The caller (e.g., `pipeline.py`, `output.py`) receives the exception directly |
| Malformed copy-path structure | `copy_path_to_rel` or `output_path_to_rel` receives a path that does not match the expected directory structure | Returns the input unchanged as a fallback | Yes | The path is passed through as-is; callers receive the unmodified string |

---

## 3. Design Notes

- **Sentinel return as the only soft failure:** The single case where an error condition is handled without propagation is the missing-file check in `is_file_unchanged`. This is a deliberate design choice: a missing copy is a normal, expected state during incremental processing (i.e., a file that has never been processed), not an exceptional condition. Returning `False` cleanly integrates into the change-detection logic used by `pipeline.py`.

- **Passthrough fallback for unrecognized paths:** `copy_path_to_rel` and `output_path_to_rel` apply a structural validation (checking whether the second-to-last path component matches `_to_dir_name` of the filename) before stripping the inserted directory. If the structure does not match, the original input is returned unchanged. This prevents silent data corruption when paths originating from external sources do not conform to the expected format.

- **Delegation of error responsibility:** Because this file contains pure utility functions used across `pipeline.py`, `output.py`, `doc_creator.py`, and `dependency_graph.py`, the design intentionally keeps error handling out of the utilities themselves. Each dependent is responsible for deciding how to handle failures appropriate to its own context (e.g., logging, skipping, or aborting).

## Summary

**file_utils.py** converts project-relative paths to/from copy-destination paths and detects file changes via hashing.

**Public functions:**
- `rel_to_copy_path(rel_path: str) → str`
- `copy_path_to_rel(copy_path: str) → str`
- `output_path_to_rel(output_path: str) → str`
- `resolve_file_output_dir(base_output_dir: str, file_rel: str) → str`
- `compute_file_hash(file_path: str) → str`
- `is_file_unchanged(source_path: str, copied_path: str) → bool`

**Key data:** copy-destination paths in `{parent}/{stem}_{ext}/{filename}` format (str); SHA256 hex digest (str); unchanged flag (bool).
