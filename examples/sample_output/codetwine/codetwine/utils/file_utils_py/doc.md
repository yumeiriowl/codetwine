# Design Document: codetwine/utils/file_utils.py

# Design Specification

**Overview**

Provides path-translation and file-hashing utilities that let the codebase map between a project's source-relative file paths and the flattened, collision-free directory structure used to store generated documentation/output artifacts.

- Building an output path for a source file: call `rel_to_copy_path` to get the `{parent_dir}/{stem}_{ext}/{filename}` layout used when copying or writing per-file docs.
- Locating or creating the on-disk output folder for a given source file: call `resolve_file_output_dir` to get the absolute directory (e.g. to write `doc.json` or check existence).
- Recovering the original source-relative path from a stored copy-destination path (e.g. from JSON metadata referencing another file): call `copy_path_to_rel`.
- Recovering the original source-relative path from a full `project_name/...` output path (e.g. a "file" or "from" field in dependency metadata): call `output_path_to_rel`.
- Detecting whether a source file has changed since the last documentation run: call `compute_file_hash` and compare against a previously stored `source_hash`.

This file has no dependencies on other project-internal files (only standard library `os` and `hashlib`). It is relied upon extensively by `doc_creator.py`, `pipeline.py`, `output.py`, and `dependency_graph.py`: `doc_creator.py` and `pipeline.py` use `resolve_file_output_dir`/`compute_file_hash` to locate per-file output folders and detect changed files for incremental processing, and `output_path_to_rel`/`copy_path_to_rel` to convert stored dependency paths back to relative paths; `output.py` and `dependency_graph.py` use `rel_to_copy_path` to construct the `project_name/...` output paths embedded in generated documentation and dependency graphs, and use the reverse functions to resolve them back.

The path-conversion functions never raise on malformed or unexpected input: `copy_path_to_rel` and `output_path_to_rel` silently return the input unchanged if it doesn't match the expected `{stem}_{ext}/{filename}` or `project_name/...` pattern, favoring graceful fallback over strict validation.

**Definitions**

## `_to_dir_name`
Derives the per-file output subdirectory name by joining the filename stem and its extension (without the dot) with an underscore, so files with the same stem but different extensions (e.g. `utils.c` and `utils.h`) map to distinct directories; files without an extension (e.g. `Makefile`) are returned unchanged. It is the shared naming rule used internally by `rel_to_copy_path` and `copy_path_to_rel` to keep the forward and reverse path conversions consistent.

## `rel_to_copy_path`
Converts a project-relative source path into the copy-destination directory structure path (`{parent_dir}/{stem}_{ext}/{filename}`) used whenever generated output is organized per source file. Callers use it to compute where a given source file's copy or generated artifacts should live, ensuring same-named files with different extensions do not collide in the output tree.

## `copy_path_to_rel`
Reverses `rel_to_copy_path` by detecting and stripping the inserted `{stem}_{ext}` directory segment, restoring the original project-relative path. Used when reading back file references stored in copy-destination form (e.g. dependency "from"/"file" fields) to recover the actual source path; if the expected inserted segment isn't found, it returns the path unchanged rather than failing.

## `output_path_to_rel`
Removes the leading `project_name/` prefix from a full output path and delegates to `copy_path_to_rel` to recover the source-relative path, inverting the `project_name/copy_destination_path` format produced elsewhere (`to_output_path` in `output.py`). Used by consumers of stored dependency/documentation metadata (e.g. `output_path_to_rel` calls in `doc_creator.py` and `output.py`) to display or resolve human-readable relative file references for callers/callees.

## `resolve_file_output_dir`
Computes the absolute output directory for a given source file by combining a base output directory with the parent portion of `rel_to_copy_path`'s result. Used throughout the pipeline and documentation generation (`doc_creator.py`, `pipeline.py`, `output.py`) to locate or create the directory where a file's `doc.json` or other per-file artifacts are read from or written to.

## `compute_file_hash`
Computes and returns the SHA256 hex digest of a file by streaming it in 8KB chunks, avoiding loading large files fully into memory. Used for incremental-processing change detection: callers compare the returned hash against a previously stored `source_hash` value to decide whether a file's documentation needs regeneration.

# Summary

File utils module offering path-translation and hashing helpers between source-relative file paths and a flattened, collision-free per-file output directory layout. Public functions: rel_to_copy_path, copy_path_to_rel, output_path_to_rel, resolve_file_output_dir, compute_file_hash (plus internal _to_dir_name). Handles building/reversing "{parent_dir}/{stem}_{ext}/{filename}" and "project_name/..." paths, resolving absolute output dirs, and SHA256 file hashing for incremental change detection. No internal dependencies; used by doc_creator, pipeline, output, and dependency_graph modules.
