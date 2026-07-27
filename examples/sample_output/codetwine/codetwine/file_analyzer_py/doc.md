# Design Document: codetwine/file_analyzer.py

# Overview & Purpose

`file_analyzer.py` acts as the **per-file orchestration layer** in the codetwine dependency analysis pipeline. It exists as a separate module to decouple the *single-file analysis workflow* (parsing → definition extraction → import resolution → usage analysis) from the project-wide orchestration handled by `pipeline.py`. Rather than implementing any parsing, extraction, or resolution logic itself, this file composes calls to specialized modules (`ts_parser`, `extractors.definitions`, `extractors.imports`, `extractors.usage_analysis`, `import_to_path`) and assembles their outputs into a single structured result per file. This separation keeps each concern (parsing, definition extraction, import resolution, usage tracking) independently testable while providing one coherent entry point (`get_file_dependencies`) for callers like `pipeline.py`.

## Main Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` with keys `"file"`, `"definitions"`, `"callee_usages"`, `"caller_usages"` | Analyzes a single target file: parses it, extracts its definitions with source context, resolves its imports to project-internal files, and collects both outbound (callee) and inbound (caller) usage information, returning a unified dependency record for that file. |

## Design Decisions

- **Facade/Orchestrator pattern**: The module exposes a single function that hides the multi-step pipeline (parse → extract definitions → resolve imports → build usage lists) behind one call, so consumers (`pipeline.py`) don't need to know the internal sequencing or which sub-modules are involved.
- **Language-agnostic, config-driven behavior**: Instead of branching per language, the function looks up language-specific settings (`DEFINITION_DICTS`, and via `get_import_params` the tree-sitter `Language`/import query) keyed by file extension, gracefully skipping import/usage analysis (`language and import_query_str` check) for unsupported languages while still returning definitions.
- **Graceful degradation over exceptions**: If a file extension has no import query configured, `usage_list` and `caller_usages` simply remain empty lists rather than raising, keeping the pipeline resilient to partially supported languages.
- **Line-range based source extraction**: Definition source snippets (`context`) are derived directly from the decoded file content lines using each definition's `start_line`/`end_line`, avoiding redundant re-parsing or re-reading of the file for this purpose.
- **Relative path normalization**: The target file path is immediately converted to a project-relative, forward-slash-normalized path (`target_file_rel`), ensuring consistent path keys are used across all downstream calls (`build_symbol_to_file_map`, `build_caller_usages`) and in the final output.

# Definition Design Specifications

## `get_file_dependencies(target_file: str, project_dir: str, project_dep_list: list[dict]) -> dict`

**Arguments:**
- `target_file`: Absolute path of the file to analyze.
- `project_dir`: Absolute path to the project root, used to compute relative paths and to resolve other project files during import/usage analysis.
- `project_dep_list`: List of dependency info dicts (each containing at least `"file"` and `"callers"` keys) produced by `save_project_dependencies`, used both to build the set of known project files and to look up which files call into `target_file`.

**Return value:**
A dict with keys:
- `"file"`: the project-root-relative path of the target file (POSIX-style separators).
- `"definitions"`: list of dicts describing each definition found in the file (`name`, `type`, `start_line`, `end_line`, `context`).
- `"callee_usages"`: list of usage records describing where the target file uses symbols imported from elsewhere in the project.
- `"caller_usages"`: list of usage records describing where other project files use symbols defined in the target file.

**Responsibility / design intent:**
This function is the single per-file entry point that assembles all dependency-relevant data for one source file, combining static definition extraction, import resolution, and bidirectional (callee/caller) usage analysis into one unified record. It exists to decouple `pipeline.py` (which drives directory-wide iteration) from the details of AST parsing, language-specific import handling, and usage tracking.

**Important design decisions:**
- The file extension is used as the single dispatch key (via `DEFINITION_DICTS.get` and `get_import_params`) to determine language-specific behavior; unsupported extensions simply yield `None`/`(None, None)` configs, causing definitions/imports to be skipped gracefully rather than raising errors. This allows the function to be called uniformly for any file type without upfront filtering.
- Import/usage analysis (`symbol_to_file_map`, `usage_list`, `caller_usages`) is only performed when both `language` and `import_query_str` are available, avoiding wasted work and query errors for languages without import query definitions.
- `project_file_set` is rebuilt from `project_dep_list` on every call (rather than passed in directly) to keep the function's dependency on the caller's internal data structure minimal and to guarantee the set matches exactly the files already analyzed in the current project pass.
- Source code context for each definition is extracted directly from `content_lines` using the definition's line range, avoiding a second file read/parse for this purpose.
- Relative path normalization to forward slashes (`"/"`) is applied to `target_file_rel` to ensure consistent path keys across platforms, since downstream matching (e.g., against `project_dep_list`, symbol-to-file maps) relies on string equality of relative paths.

**Edge cases and constraints:**
- `target_file` must be a file whose extension is registered in `TREE_SITTER_LANGUAGES` (a precondition of `parse_file`); otherwise parsing fails with an unhandled exception (`KeyError`).
- If `file_ext` has no entry in `DEFINITION_DICTS`, `definition_dict` is `None`, and `extract_definitions` behavior depends on receiving `None`—no definitions are filtered out because the language is unsupported at the config level (this function does not special-case that; it simply passes the (possibly `None`) dict through).
- If `get_import_params` returns `(None, None)`, `usage_list` and `caller_usages` remain empty lists, and definitions are still returned—so files without import-analysis support still contribute definition data to the overall output.
- Assumes `target_file` is UTF-8 encoded (`content.decode("utf-8")`); non-UTF-8 files will raise a decoding error.
- Assumes each entry in `project_dep_list` contains a `"file"` key; malformed entries will raise a `KeyError`.

# Dependency Description

## Dependencies (what this file uses)

- **`codetwine/config/settings.py` – `DEFINITION_DICTS`**: Used to look up the per-language definition-extraction configuration (AST node type mappings) based on the target file's extension, enabling `extract_definitions` to work generically across languages.

- **`codetwine/parsers/ts_parser.py` – `parse_file`**: Used to read and parse the target file into a tree-sitter AST (`root_node`) plus its raw byte content, which serves as the basis for both definition extraction and import/usage analysis.

- **`codetwine/extractors/definitions.py` – `extract_definitions`**: Used to extract function/class/variable definitions (name, type, line range) from the parsed AST, which are then combined with source line ranges to build the `definitions` output.

- **`codetwine/import_to_path.py` – `get_import_params`**: Used to obtain the tree-sitter `Language` object and import query string for the target file's extension, determining whether import/usage analysis should proceed at all.

- **`codetwine/import_to_path.py` – `detect_source_roots`**: Used to detect known source root prefixes present in the project's file set, needed for correctly resolving import paths.

- **`codetwine/import_to_path.py` – `build_symbol_to_file_map`**: Used to convert extracted import statements into a mapping from imported symbol names to the project files that define them (plus alias mappings), which is required before usage tracking can be performed.

- **`codetwine/extractors/imports.py` – `extract_imports`**: Used to parse import/include statements out of the AST, providing the raw import information consumed by `build_symbol_to_file_map`.

- **`codetwine/extractors/usage_analysis.py` – `build_usage_info_list`**: Used to locate where imported symbols are used within the target file and attach their definition source code, producing the `callee_usages` output.

- **`codetwine/extractors/usage_analysis.py` – `build_caller_usages`**: Used to find where symbols defined in the target file are used by other project files, producing the `caller_usages` output.

## Dependents (what uses this file)

- **`codetwine/pipeline.py` – `get_file_dependencies`**: Calls this file's `get_file_dependencies` function for each analyzed source file, passing the absolute file path, project directory, and the accumulated project dependency list, and uses the returned dict (definitions, callee_usages, caller_usages) to produce per-file dependency output (`file_dependencies.json`).

The dependency direction is unidirectional: `codetwine/pipeline.py` depends on `codetwine/file_analyzer.py`, while `file_analyzer.py` has no dependency back on `pipeline.py`.

# Data Flow

## Input

| Source | Data |
|---|---|
| `target_file` (str) | Absolute path of the file to analyze |
| `project_dir` (str) | Absolute path of the project root |
| `project_dep_list` (list[dict]) | Project-wide dependency info, each entry containing at least `{"file": <rel path>, "callers": [...]}` |

## Main Transformation Flow

```
target_file ──► parse_file ──► (root_node, content bytes)
                                     │
                                     ├─► extract_definitions(root_node, definition_dict)
                                     │        │
                                     │        ▼
                                     │   list[DefinitionInfo] ──► enrich with source
                                     │        text (via content_lines slicing) ──► definition_list
                                     │
                                     └─► get_import_params(file_ext) ──► (language, import_query_str)
                                              │
                                              ├─ if unsupported language: skip import/usage analysis
                                              │
                                              └─ if supported:
                                                   project_dep_list ──► project_file_set (set of rel paths)
                                                   project_file_set ──► detect_source_roots ──► source_root_set

                                                   extract_imports(root_node, language, import_query_str)
                                                        ──► list[ImportInfo]
                                                        ──► build_symbol_to_file_map(..., project_file_set,
                                                                                     source_root_set)
                                                        ──► (symbol_to_file_map, alias_to_original)

                                                   build_usage_info_list(root_node, symbol_to_file_map,
                                                                         project_dir, file_ext, alias_to_original)
                                                        ──► usage_list (callee_usages)

                                                   build_caller_usages(target_file_rel, project_dep_list,
                                                                       project_dir, project_file_set)
                                                        ──► caller_usages
```

Key transformations:
1. **Path/extension normalization**: `target_file` → `target_file_rel` (relative, POSIX slashes) and `file_ext` used as lookup keys into per-language config (`DEFINITION_DICTS`, import params).
2. **AST → definitions**: `root_node` is scanned via `extract_definitions`, then each `DefinitionInfo` (name/type/line range) is enriched with its actual source snippet by slicing `content_lines`.
3. **AST → imports → symbol map**: `extract_imports` turns raw import syntax into `ImportInfo` objects; `build_symbol_to_file_map` resolves these to concrete project file paths, producing a name→file lookup table (`symbol_to_file_map`) plus alias resolution (`alias_to_original`).
4. **Symbol map → callee usages**: `build_usage_info_list` walks the AST again, matches identifier usages against `symbol_to_file_map` keys, and attaches the referenced definition's source code, producing `callee_usages`.
5. **Reverse lookup → caller usages**: `build_caller_usages` uses `project_dep_list`'s caller references to re-parse other files, extract their imports, and detect usages of this file's symbols, producing `caller_usages`.

## Output

Returned dict (destination: caller `get_file_dependencies` in `pipeline.py`, serialized into `file_dependencies.json`):

| Field | Type | Description |
|---|---|---|
| `file` | str | Relative path of the analyzed file |
| `definitions` | list[dict] | Definitions found in this file (`name`, `type`, `start_line`, `end_line`, `context`) |
| `callee_usages` | list[dict] | Usages of symbols imported into this file, each with `lines`, `name`, `from`, `target_context` |
| `caller_usages` | list[dict] | Usages of this file's symbols in other project files, each with `lines`, `name`, `file`, `usage_context` |

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `definition_list` (list[dict]) | `name`, `type`, `start_line`, `end_line`, `context` | Local representation of definitions with source snippets, built from `DefinitionInfo` + raw file lines |
| `project_file_set` (set[str]) | relative file paths | Fast membership checks for resolving imports/includes to project-internal files |
| `source_root_set` (set[str]) | source root prefixes (e.g. `"src/main/java/"`) | Helps normalize import paths that include a language-specific source root |
| `symbol_to_file_map` (dict[str, str]) | imported name → defining file path | Central lookup enabling usage-to-definition resolution |
| `alias_to_original` (dict[str, str]) | alias name → original name | Resolves `import X as Y` style aliasing when looking up definitions |
| `usage_list` / `caller_usages` (list[dict]) | `lines`, `name`, `from`/`file`, `target_context`/`usage_context` | Final usage records aggregated by symbol, forming the callee/caller usage outputs |

# Error Handling

`file_analyzer.py` follows a **fail-fast** strategy at its own level: it performs no local `try/except` handling and lets exceptions from parsing, decoding, or file I/O propagate to the caller (`pipeline.py`). This design assumes that upstream stages (file discovery, extension filtering) have already validated that the target file exists and is a supported, parseable source file, so error suppression here would mask genuine data or configuration problems.

At the same time, the module relies on **graceful degradation built into its dependencies** for cases that are not truly exceptional but simply "not applicable"—most notably unsupported languages. When a file extension has no entry in `DEFINITION_DICTS` or `IMPORT_QUERIES`, the corresponding lookups return `None`/`(None, None)` rather than raising, and this file explicitly branches on that condition (`if language and import_query_str`) to skip import/usage analysis while still returning a valid result with empty `callee_usages`/`caller_usages` lists.

| Error type | Handling | Impact |
|---|---|---|
| Unsupported file extension (no `DEFINITION_DICTS` entry) | `definition_dict` becomes `None`; passed through to `extract_definitions`, which degrades gracefully (BFS falls through to child traversal, empty result) | Definitions list is empty; no crash |
| Unsupported/unconfigured language for import analysis (`get_import_params` returns `(None, None)`) | Explicit `if language and import_query_str` check skips import/usage/caller analysis entirely | `callee_usages` and `caller_usages` remain empty lists; other fields still populated |
| File read / parse failure (`parse_file`, encoding errors) | Not caught locally; exception propagates unhandled | Whole-file analysis aborts; caller (`pipeline.py`) must decide how to handle the failure |
| Malformed or unreadable content during `content.decode("utf-8")` | Not caught locally; `UnicodeDecodeError` propagates | Function call fails entirely for that file |
| Missing/invalid entries in `project_dep_list` (e.g., no matching file record) | Not validated here; delegated to downstream functions (`build_caller_usages`), which handle absence internally (e.g., empty caller list) | No error surfaced; caller usages simply empty |

**Design considerations:**
- The module deliberately centralizes "is this language supported" checks into a single guarded branch, avoiding duplicated per-step error handling for import/usage logic.
- No defensive validation is performed on `target_file`, `project_dir`, or `project_dep_list` inputs; correctness of these arguments is assumed to be guaranteed by the calling pipeline.
- By not catching exceptions locally, the file keeps its logic focused on data transformation/aggregation, pushing retry, logging, or skip-on-error decisions to the orchestration layer (`pipeline.py`), even though a `logger` is configured but not actively used for error reporting in this file.

# Summary

`file_analyzer.py` orchestrates single-file dependency analysis, exposing `get_file_dependencies(target_file, project_dir, project_dep_list) -> dict` (`file`, `definitions`, `callee_usages`, `caller_usages`). It composes `ts_parser`, `extractors.definitions/imports/usage_analysis`, and `import_to_path` to parse a file, extract definitions with source context, resolve imports to a symbol-to-file map, and build bidirectional usage records. Config-driven per-extension dispatch enables graceful skipping for unsupported languages; no local error handling—failures propagate. Used exclusively by `pipeline.py`.
