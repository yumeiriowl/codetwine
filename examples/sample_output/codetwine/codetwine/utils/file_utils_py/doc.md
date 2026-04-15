# Design Document: codetwine/utils/file_utils.py

# Overview & Purpose

## 1. Module Summary

Provides utility functions for converting between project-relative file paths and the copy-destination directory structure used throughout the CodeTwine pipeline, along with file hashing helpers for change detection.

## 2. When to Use This Module

- **Converting a relative path to its output directory path**: Call `resolve_file_output_dir(base_output_dir, file_rel)` to obtain the absolute path of the output directory for a given source file. Used by `pipeline.py`, `output.py`, and `doc_creator.py` when reading or writing per-file artifacts such as `doc.json` and `file_dependencies.json`.
- **Building a copy-destination path from a relative path**: Call `rel_to_copy_path(rel_path)` to convert a project-relative path (e.g. `"src/utils.py"`) into the structured copy-destination path (e.g. `"src/utils_py/utils.py"`). Used by `output.py` and `dependency_graph.py` when recording file references in output JSON.
- **Recovering the original relative path from a copy-destination path**: Call `copy_path_to_rel(copy_path)` to reverse the transformation applied by `rel_to_copy_path`. Used by `output.py` and `pipeline.py` when interpreting stored paths back to source-relative form.
- **Recovering the original relative path from a full output path**: Call `output_path_to_rel(output_path)` to strip the project-name prefix and reverse the copy-destination transformation. Used by `output.py` and `doc_creator.py` when resolving file references that include the project name.
- **Detecting whether a source file has changed**: Call `is_file_unchanged(source_path, copied_path)` to compare SHA-256 hashes of the original file and its previously copied counterpart. Used by `pipeline.py` to skip re-processing unmodified files.
- **Computing a file's hash independently**: Call `compute_file_hash(file_path)` to obtain the SHA-256 hex digest of any file.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `rel_to_copy_path` | `rel_path` (str) | str | Converts a project-relative path to the copy-destination directory structure path (`{parent}/{stem}_{ext}/{filename}`). |
| `copy_path_to_rel` | `copy_path` (str) | str | Reverses `rel_to_copy_path`, restoring the copy-destination path to the original project-relative path. |
| `output_path_to_rel` | `output_path` (str) | str | Removes the project-name prefix from a `"project_name/copy_path"` format path and delegates to `copy_path_to_rel` to recover the source-relative path. |
| `resolve_file_output_dir` | `base_output_dir` (str), `file_rel` (str) | str | Returns the absolute output directory path for a file by combining `base_output_dir` with the parent portion of the copy-destination path. |
| `compute_file_hash` | `file_path` (str) | str | Returns the SHA-256 hash of a file as a hex string, reading in 8 KB chunks. |
| `is_file_unchanged` | `source_path` (str), `copied_path` (str) | bool | Returns `True` if the source file and its copy share the same SHA-256 hash; returns `False` if the copy does not exist. |

## 4. Design Decisions

- **Collision-free output directory naming**: By transforming `stem.ext` into `stem_ext` as the containing directory name (via the internal `_to_dir_name` helper), files that share a base name but differ in extension (e.g. `utils.c` and `utils.h`) are placed in distinct output directories, preventing path collisions.
- **Invertible path transformation**: `rel_to_copy_path` and `copy_path_to_rel` are designed as strict inverses. `copy_path_to_rel` identifies the inserted directory by checking whether the second-to-last path component matches `_to_dir_name(filename)`, making the reversal reliable without storing metadata.
- **Copy-destination path as the common path currency**: All pipeline components (output generation, dependency graph, doc creation) express file locations using the same `{parent}/{stem}_{ext}/{filename}` structure, centralizing the path logic in this module and keeping dependents consistent.

# Definition Design Specifications

---

## `_to_dir_name(filename: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `filename: str` → `str` |
| **Visibility** | Module-private (underscore prefix) |

**Responsibility:** Converts a bare filename into a directory name by replacing the extension separator `.` with `_`, so that the resulting string is safe to use as a directory name without a dot.

**When to use:** Called internally whenever a per-file output directory name must be derived from a filename.

**Design decisions:**
- Uses `os.path.splitext` so the split is consistent with the OS path library's definition of an extension.
- Files without extensions (e.g. `Makefile`) are returned unchanged, not given a trailing `_`.
- The leading `.` of the extension is stripped by taking `ext[1:]`, so `_py` is produced rather than `_.py`.

**Constraints & edge cases:**
- Input is expected to be a bare filename, not a path with directory components; behavior is undefined for inputs containing path separators.
- Hidden files (e.g. `.gitignore`) are treated by `os.path.splitext` as having no extension (stem = `.gitignore`, ext = `""`), so they are returned unchanged.

---

## `rel_to_copy_path(rel_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `rel_path: str` → `str` |

**Responsibility:** Transforms a project-relative file path into the copy-destination directory structure path used throughout the output layer, inserting a `{stem}_{ext}` directory between the parent directory and the filename.

**When to use:** Called whenever a relative source path must be mapped to the corresponding location in the output directory tree, e.g. when writing copied source files or building dependency graph entries.

**Design decisions:**
- The intermediate `{stem}_{ext}` directory prevents name collisions between files that share a stem but differ in extension (e.g. `utils.c` vs. `utils.h`).
- Top-level files (no parent directory) omit the leading `/` by using a separate branch.

**Constraints & edge cases:**
- `rel_path` must use forward slashes or be handled by `os.path.dirname`/`os.path.basename`.
- The function is the authoritative definition of the copy-destination path structure; all callers must stay consistent with it.

---

## `copy_path_to_rel(copy_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `copy_path: str` → `str` |

**Responsibility:** Inverts `rel_to_copy_path`, removing the inserted `{stem}_{ext}` directory component to recover the original project-relative path.

**When to use:** Called when reading back an output-tree path and needing to identify which source file it corresponds to.

**Design decisions:**
- Normalises Windows backslashes to forward slashes before splitting, making the function cross-platform.
- The second-to-last path component is validated against `_to_dir_name(filename)` before removal, so paths that do not follow the expected structure are returned unchanged rather than silently corrupted.

**Constraints & edge cases:**
- Relies on the invariant that `_to_dir_name` is the same function used during path creation; any drift between the two will cause incorrect inversion.
- Paths with fewer than two components are returned as-is.

---

## `output_path_to_rel(output_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `output_path: str` → `str` |

**Responsibility:** Strips the project-name prefix from a full output path (format `project_name/copy_destination_path`) and delegates to `copy_path_to_rel` to recover the source-relative path.

**When to use:** Called when processing paths stored inside JSON dependency documents, where paths are recorded in `project_name/copy_destination_path` format, and the caller needs the original source-relative path.

**Design decisions:**
- Splits on the first `/` only (limit `1`) to correctly handle project names that contain no slashes while leaving the rest of the path intact for `copy_path_to_rel`.
- Falls back to returning the input unchanged when no `/` separator is found.

**Constraints & edge cases:**
- Assumes the first path segment is exactly the project name with no nested structure.
- Input paths with only one segment are returned unchanged, which may or may not be correct depending on the caller's context.

---

## `resolve_file_output_dir(base_output_dir: str, file_rel: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `base_output_dir: str`, `file_rel: str` → `str` |

**Responsibility:** Computes the absolute path of the output directory for a given source file by combining the base output directory with the directory component of the file's copy-destination path.

**When to use:** Called before reading or writing any per-file output artefact (copied source, `doc.json`, `file_dependencies.json`) to resolve the correct directory on disk.

**Design decisions:**
- Delegates path structure computation entirely to `rel_to_copy_path`, so the output directory layout is always consistent with the copy-destination path structure.
- Returns only the directory (via `os.path.dirname` of the copy path), not the full file path, because callers construct specific filenames themselves.

**Constraints & edge cases:**
- Does not create the directory; callers are responsible for calling `os.makedirs` if needed.
- `base_output_dir` should be an absolute path to yield a meaningful absolute result; a relative `base_output_dir` produces a relative result.

---

## `compute_file_hash(file_path: str) -> str`

| Item | Detail |
|---|---|
| **Signature** | `file_path: str` → `str` |

**Responsibility:** Produces a SHA-256 hex digest for a file, enabling content-based equality checks without loading the entire file into memory.

**When to use:** Called indirectly through `is_file_unchanged` to compare source and copied file contents; may also be called directly when a hash of a single file is needed.

**Design decisions:**
- Reads the file in fixed 8 KB chunks to bound memory usage regardless of file size.
- Opens in binary mode to ensure the hash is byte-exact across platforms (no newline translation).

**Constraints & edge cases:**
- Raises `FileNotFoundError` (or similar OS error) if `file_path` does not exist; callers must ensure the file is present before calling.
- `file_path` must be an absolute path or a valid path relative to the current working directory.

---

## `is_file_unchanged(source_path: str, copied_path: str) -> bool`

| Item | Detail |
|---|---|
| **Signature** | `source_path: str`, `copied_path: str` → `bool` |

**Responsibility:** Determines whether a source file and its output-directory copy are byte-identical by comparing their SHA-256 hashes, so that the pipeline can skip re-processing unchanged files.

**When to use:** Called during the change-detection phase of the pipeline to populate the set of files that require reprocessing.

**Design decisions:**
- A missing copy is treated as "changed" (returns `False`) rather than raising an error, because a missing copy simply means the file has never been processed.
- SHA-256 comparison is preferred over modification-time comparison because timestamps can be unreliable across copy operations.

**Constraints & edge cases:**
- Both paths must point to regular files if they exist; behaviour on directories or special files is unspecified.
- Hash collision (two different files producing the same SHA-256) is treated as equality; this is an accepted theoretical limitation.

# Dependency Description

### Dependencies (modules this file imports)

`codetwine/utils/file_utils.py` imports only from the Python standard library (`os`, `hashlib`). There are **no project-internal module dependencies** for this file.

---

### Dependents (modules that import this file)

The following project-internal modules depend on `codetwine/utils/file_utils.py`:

- **`codetwine/output.py`** → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to construct output path strings in `"project_name/copy_path"` format; uses `resolve_file_output_dir` to locate per-file output directories when building summary maps and file lists; uses `output_path_to_rel` to convert stored output paths back to project-relative paths when processing dependency usage entries; uses `copy_path_to_rel` to strip the copy-destination directory structure and recover original relative paths (e.g., for Mermaid diagram building).

- **`codetwine/pipeline.py`** → `codetwine/utils/file_utils.py` : Uses `resolve_file_output_dir` to determine per-file output directories during change detection and file processing; uses `is_file_unchanged` to compare source files against their copies and decide whether reprocessing is needed; uses `copy_path_to_rel` to convert prefixed copy-destination paths back to internal relative paths.

- **`codetwine/doc_creator.py`** → `codetwine/utils/file_utils.py` : Uses `output_path_to_rel` to recover human-readable relative paths from stored output paths when constructing documentation prompts and listing callee usages; uses `resolve_file_output_dir` to locate the output directory for a given file when loading or writing design documents.

- **`codetwine/extractors/dependency_graph.py`** → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to format file, caller, and callee entries in the dependency graph output as `"project_name/copy_path"` strings.

---

### Dependency Direction

| Relationship | Direction |
|---|---|
| `codetwine/output.py` → `codetwine/utils/file_utils.py` | Unidirectional |
| `codetwine/pipeline.py` → `codetwine/utils/file_utils.py` | Unidirectional |
| `codetwine/doc_creator.py` → `codetwine/utils/file_utils.py` | Unidirectional |
| `codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils.py` | Unidirectional |

All relationships are unidirectional: `codetwine/utils/file_utils.py` is a pure utility module that depends on no other project-internal modules, while multiple higher-level modules consume its path manipulation and file hashing utilities.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `filename` | Caller argument | Plain string (e.g., `"settings.py"`, `"Makefile"`) |
| `rel_path` | Caller argument | Relative path string from project root (e.g., `"repo_graphrag/llm/client.py"`) |
| `copy_path` | Caller argument | Copy-destination directory structure path string (e.g., `"repo_graphrag/llm/client_py/client.py"`) |
| `output_path` | Caller argument | `"project_name/copy_destination_path"` format string |
| `base_output_dir` | Caller argument | Absolute directory path string |
| `file_path` / `source_path` / `copied_path` | Caller argument | Absolute file path strings |

No configuration files or environment variables are read by this module. File system reads occur only in `compute_file_hash` (binary file read) and `is_file_unchanged` (existence check via `os.path.exists`).

---

## 2. Transformation Overview

This module contains several independent transformation pipelines rather than a single linear chain.

### Path Encoding Pipeline (`rel_path` → copy-destination path)

```
rel_path (e.g., "repo_graphrag/llm/client.py")
    │
    ├─ os.path.dirname  →  parent_dir ("repo_graphrag/llm")
    └─ os.path.basename →  filename   ("client.py")
                                │
                         _to_dir_name()
                         os.path.splitext → stem="client", ext=".py"
                         → dir_name = "client_py"
                                │
                    f"{parent_dir}/{dir_name}/{filename}"
                                │
                    "repo_graphrag/llm/client_py/client.py"  ← rel_to_copy_path output
                                │
                    os.path.dirname → "repo_graphrag/llm/client_py"
                                │
                    os.path.join(base_output_dir, ...)       ← resolve_file_output_dir output
```

### Path Decoding Pipeline (copy-destination path → `rel_path`)

```
copy_path (e.g., "repo_graphrag/llm/client_py/client.py")
    │
    split("/") → ["repo_graphrag", "llm", "client_py", "client.py"]
    │
    filename = parts[-1]  →  "client.py"
    parts[-2]             →  "client_py"
    _to_dir_name("client.py") → "client_py"   [match confirmed]
    │
    "/".join(parts[:-2] + [filename])
    │
    "repo_graphrag/llm/client.py"              ← copy_path_to_rel output
```

### Output Path Decoding Pipeline (`output_path` → `rel_path`)

```
output_path (e.g., "js_project/src/emitter_js/emitter.js")
    │
    split("/", 1) → ["js_project", "src/emitter_js/emitter.js"]
    │
    parts[1] → copy_path_to_rel("src/emitter_js/emitter.js")
    │
    "src/emitter.js"                           ← output_path_to_rel output
```

### File Integrity Pipeline

```
source_path, copied_path
    │
    os.path.exists(copied_path) ──[missing]──→ False
    │ [exists]
    compute_file_hash(source_path)    ← SHA256, read in 8 KB chunks
    compute_file_hash(copied_path)    ← SHA256, read in 8 KB chunks
    │
    hash comparison
    │
    True / False                               ← is_file_unchanged output
```

---

## 3. Outputs

| Function | Output | Format |
|---|---|---|
| `_to_dir_name` | Directory name with extension separator replaced | String (e.g., `"client_py"`, `"Makefile"`) |
| `rel_to_copy_path` | Copy-destination path with inserted `{stem}_{ext}` directory | String (e.g., `"repo_graphrag/llm/client_py/client.py"`) |
| `copy_path_to_rel` | Original project-relative path recovered from copy-destination path | String (e.g., `"repo_graphrag/llm/client.py"`) |
| `output_path_to_rel` | Project-relative path recovered from `project_name/copy_path` format | String (e.g., `"src/emitter.js"`) |
| `resolve_file_output_dir` | Absolute output directory path for a given source file | String (absolute path, no trailing slash) |
| `compute_file_hash` | SHA256 digest of a file | Hex string (64 characters) |
| `is_file_unchanged` | Whether source and copied file contents are identical | Boolean |

No files are written by this module. The only side effect is file system reads performed inside `compute_file_hash`.

---

## 4. Key Data Structures

This module operates exclusively on primitive strings and booleans; no dataclasses, TypedDicts, or compound data structures are defined or produced. The following table documents the intermediate string components that appear as implicit structures within path manipulation:

### Path Components (implicit, within `rel_to_copy_path` / `copy_path_to_rel`)

| Component | Type | Purpose |
|---|---|---|
| `parent_dir` | `str` | Zero-or-more directory segments preceding the filename (e.g., `"repo_graphrag/llm"`); empty string for top-level files |
| `filename` | `str` | Bare filename including extension (e.g., `"client.py"`) |
| `stem` | `str` | Filename without extension, from `os.path.splitext` (e.g., `"client"`) |
| `ext` | `str` | Extension including leading dot, from `os.path.splitext` (e.g., `".py"`); empty string if no extension |
| `dir_name` | `str` | Inserted intermediate directory name with dot replaced by underscore (e.g., `"client_py"`); equals `stem` when no extension |
| `parts` | `list[str]` | Path split on `/` used in `copy_path_to_rel` to identify and remove the inserted directory segment |

# Error Handling

## 1. Overall Strategy

This file adopts a **fail-fast** approach. No `try-except` blocks are present; all functions either complete successfully or propagate exceptions directly to callers. The single explicit defensive check—the existence test in `is_file_unchanged`—is a **graceful degradation** case: a missing file is treated as a changed file rather than an error, allowing callers to proceed normally. All other error conditions (I/O failures, invalid paths, missing files opened for hashing) are left unhandled and surface as unmodified Python exceptions.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `FileNotFoundError` / `OSError` | `compute_file_hash` attempts to open a file that does not exist or is inaccessible | None — exception propagates to caller | No | Calling operation (e.g., change detection in `pipeline.py`) terminates or crashes |
| Missing copy at destination | `is_file_unchanged` is called when the copied file does not yet exist at `copied_path` | Returns `False` without raising; file is treated as changed | Yes | File is scheduled for (re)processing; no termination |
| `OSError` / `PermissionError` | Path operations (`os.path.splitext`, `os.path.join`, `os.path.dirname`) receive an inaccessible or malformed path | None — exception propagates to caller | No | Dependent pipeline or output operation fails |
| Malformed `copy_path` input | `copy_path_to_rel` receives a path whose second-to-last component does not match the expected `_to_dir_name` pattern | Falls back to returning the input path unchanged | Yes (partial) | The path is passed through as-is; callers receive an unrestored path |

---

## 3. Design Notes

- **Minimal surface area for error handling.** The utility functions in this file are pure path transformations or straightforward file-system operations. The design delegates all error responsibility to callers (e.g., `pipeline.py`, `output.py`), keeping each function focused on a single task without coupling it to recovery logic.

- **Existence check as a domain rule, not an error handler.** The guard in `is_file_unchanged` reflects an intentional domain decision—an absent copy is semantically equivalent to a changed file—rather than a defensive workaround for an exceptional condition. This allows change-detection loops in callers to run without requiring pre-checks or exception handling on their side.

- **Passthrough fallback in `copy_path_to_rel`.** When the path structure does not match the expected insertion pattern, the function returns the input unchanged. This is the only other case where a potentially incorrect input is tolerated silently rather than surfacing an error, prioritising forward progress over strict validation.

# Summary

**file_utils.py** — Path conversion and file hashing utilities for the CodeTwine pipeline.

**Public functions:**
- `rel_to_copy_path(rel_path: str) → str`
- `copy_path_to_rel(copy_path: str) → str`
- `output_path_to_rel(output_path: str) → str`
- `resolve_file_output_dir(base_output_dir: str, file_rel: str) → str`
- `compute_file_hash(file_path: str) → str`
- `is_file_unchanged(source_path: str, copied_path: str) → bool`

**Key data:** All inputs/outputs are primitive `str` or `bool`. Copy-destination paths follow `{parent}/{stem}_{ext}/{filename}` format; output paths use `project_name/copy_path` format.
