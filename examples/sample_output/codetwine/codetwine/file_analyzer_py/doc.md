# Design Document: codetwine/file_analyzer.py

# Overview & Purpose

`file_analyzer.py` is the per-file orchestration layer of CodeTwine's dependency analysis pipeline. It coordinates parsing, definition extraction, import resolution, and usage analysis for a single source file, assembling their outputs into the unified data structure that ultimately becomes an entry in `file_dependencies.json`. This file exists as a separate module to isolate the "single-file analysis" concern from the project-wide orchestration performed by `pipeline.py` (which calls `get_file_dependencies` once per file) and from the lower-level, language-agnostic mechanics implemented in `parsers/`, `extractors/`, and `import_to_path.py`. By centralizing this coordination logic here, each dependency module can remain focused on one specific concern (parsing, definitions, imports, usage), while `file_analyzer.py` handles the sequencing and data-shaping needed to produce a consistent per-file result.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_dep_list: list[dict]` | `dict` with keys `{"file", "definitions", "callee_usages", "caller_usages"}` | Analyzes a single target file: parses it, extracts its definitions with source context, resolves imports and symbol usages (callee side), and collects usages of this file's definitions in other project files (caller side), returning a combined result dict for downstream JSON output. |

## Design Notes

- **Graceful degradation for unsupported languages**: `definition_dict` lookup via `DEFINITION_DICTS.get(file_ext)` and the `get_import_params` check (`if language and import_query_str`) allow the function to skip import/usage analysis entirely for languages without configured support, while still returning definitions where possible, rather than raising errors.
- **Single-responsibility delegation**: Each analysis step (parsing, definition extraction, import extraction, symbol-to-file mapping, usage list building, caller usage building) is delegated to a dedicated extractor/resolver module; `file_analyzer.py` itself contains no parsing or AST-traversal logic, only sequencing and data assembly (e.g., converting file content to lines and slicing `start_line`/`end_line` ranges to build `context` strings for each definition).
- **Config-driven behavior**: Language-specific behavior (which node types constitute definitions, which import query to use) is driven entirely by external configuration (`DEFINITION_DICTS`, and configuration consumed inside `get_import_params`), keeping this file language-agnostic.
- **Relative-path normalization**: The target file's path is normalized to a project-relative, forward-slash form (`target_file_rel`) early on, ensuring consistent path keys are used throughout the returned dict and when interacting with `project_dep_list`/`project_file_set`.
- **Shared caching awareness**: By calling `parse_file`, this module benefits from the module-level parse cache in `ts_parser.py`, avoiding redundant re-parsing when the same file is referenced during caller-usage analysis elsewhere in the pipeline.

# Definition Design Specifications

## `get_file_dependencies`

**Arguments:**
- `target_file: str` — Absolute path of the file being analyzed.
- `project_dir: str` — Absolute path to the project root, used to compute relative paths and resolve imports.
- `project_dep_list: list[dict]` — Dependency metadata for all project files (each containing at least `"file"` and `"callers"` keys), used to build the project-wide file set and to locate caller files for reverse-usage analysis.

**Returns:** `dict` with keys `"file"` (project-relative path of the target file), `"definitions"` (list of definition dicts with `name`, `type`, `start_line`, `end_line`, `context`), `"callee_usages"` (list of usage entries describing where this file references symbols from other project files), and `"caller_usages"` (list of usage entries describing where other project files reference symbols defined in this file).

**Responsibility:** Acts as the per-file orchestration entry point that ties together parsing, definition extraction, import resolution, and bidirectional usage analysis into the unified data structure consumed downstream for `file_dependencies.json`. It isolates `pipeline.py` from the details of how language-specific extraction and cross-file symbol resolution are composed.

**Design decisions:**
- Language support is looked up per file extension (`DEFINITION_DICTS`, `get_import_params`); when a language has no import query/grammar configured, `language`/`import_query_str` come back as `(None, None)` and the function silently skips all import/usage analysis instead of failing, leaving `usage_list` and `caller_usages` as empty lists. This makes the function tolerant of unsupported languages.
- `context` for each definition is derived by slicing decoded UTF-8 source lines using each definition's `start_line`/`end_line` (1-indexed, inclusive), giving callers ready-to-use source snippets without re-reading the file elsewhere.
- Builds `project_file_set` once from `project_dep_list` and reuses it both for import resolution (`build_symbol_to_file_map`) and for caller-usage collection (`build_caller_usages`), ensuring both directions of analysis operate on a consistent view of which files belong to the project.
- Relies on `parse_file`'s module-level caching, so repeated parsing across multiple analysis phases (definitions vs. imports) incurs no extra I/O/parsing cost for the same file.

**Edge cases / constraints:**
- Assumes `target_file` is UTF-8 decodable; a decoding failure will raise rather than degrade gracefully.
- Assumes `file_ext` (extension without the leading dot) is used consistently as the key into `DEFINITION_DICTS` and import-related configs; unknown extensions yield `definition_dict=None`, which is passed through to `extract_definitions` (behavior in that case is dictated by that function's handling of a falsy `definition_dict`).
- Depends on `project_dep_list` already containing an entry for `target_file` (matched via its project-relative path) for `build_caller_usages` to find its callers; if absent, `caller_file_list` inside that function stays empty and no caller usages are found.

# Dependency Description

## Dependencies (what this file uses)

`file_analyzer.py` acts as an orchestrator that composes several project-internal modules to build the per-file dependency analysis result:

- **`codetwine/config/settings.py` (`DEFINITION_DICTS`)**: Used to look up the per-language definition extraction configuration based on the target file's extension, enabling language-agnostic definition parsing.
- **`codetwine/parsers/ts_parser.py` (`parse_file`)**: Used to read the target file and obtain its tree-sitter AST root node plus raw byte content, which serves as the basis for all subsequent extraction steps.
- **`codetwine/extractors/definitions.py` (`extract_definitions`)**: Used to extract function/class/variable definitions from the AST, which are then combined with source line ranges to build the `definitions` output field.
- **`codetwine/import_to_path.py` (`get_import_params`, `detect_source_roots`, `build_symbol_to_file_map`)**: Used to determine whether import analysis is supported for the file's language, detect source root prefixes within the project, and resolve imported symbol names to their defining project files (needed before usage tracking can occur).
- **`codetwine/extractors/imports.py` (`extract_imports`)**: Used to parse import statements out of the AST, providing the raw import information consumed by `build_symbol_to_file_map`.
- **`codetwine/extractors/usage_analysis.py` (`build_usage_info_list`, `build_caller_usages`)**: Used to locate where imported symbols are used within the target file (producing `callee_usages`) and where symbols defined in the target file are used by other project files (producing `caller_usages`).

## Dependents (what uses this file)

- **`codetwine/pipeline.py` (`get_file_dependencies`)**: Calls this file's `get_file_dependencies` function for each target file to obtain its definitions, callee usages, and caller usages, which are then written out as part of the `file_dependencies.json` output.

The dependency direction is unidirectional: `pipeline.py` depends on `file_analyzer.py`, while `file_analyzer.py` does not depend on `pipeline.py`.

# Data Flow

### Input

| Parameter | Type | Description |
|---|---|---|
| `target_file` | `str` | Absolute path of the file to analyze |
| `project_dir` | `str` | Absolute path of the project root |
| `project_dep_list` | `list[dict]` | Per-file dependency records (each containing at least `"file"` and `"callers"`), produced upstream by `save_project_dependencies` |

### Transformation Flow

```
target_file, project_dir
        │
        ├─ os.path.relpath / splitext ──► target_file_rel, file_ext
        │
        ├─ DEFINITION_DICTS.get(file_ext) ──► definition_dict (None if unsupported)
        │
        ├─ parse_file(target_file) ──► (root_node, content)  [AST + raw bytes, cached]
        │       │
        │       ├─ content.decode/splitlines ──► content_lines
        │       │
        │       └─ extract_definitions(root_node, definition_dict)
        │               ──► list[DefinitionInfo] (name/type/start_line/end_line)
        │               ──► mapped + enriched with "context" (source slice from content_lines)
        │               ──► definition_list
        │
        └─ get_import_params(file_ext) ──► (language, import_query_str)
                │
                ▼ (only if language & query available)
        project_dep_list ──► project_file_set (set of relative paths)
        project_file_set ──► detect_source_roots ──► source_root_set

        extract_imports(root_node, language, import_query_str)
                ──► list[ImportInfo]
                        │
                        ▼
        build_symbol_to_file_map(imports, target_file_rel, project_file_set,
                                  file_ext, project_dir, source_root_set)
                ──► (symbol_to_file_map, alias_to_original)
                        │
                        ├─► build_usage_info_list(root_node, symbol_to_file_map,
                        │        project_dir, file_ext, alias_to_original)
                        │        ──► usage_list  (this file's calls into other files)
                        │
                        └─► build_caller_usages(target_file_rel, project_dep_list,
                                 project_dir, project_file_set)
                                 ──► caller_usages (other files calling this file's symbols)
```

### Output

Returned to the caller (`pipeline.py`'s `get_file_dependencies` call site), which uses it as the per-file record eventually persisted as part of `file_dependencies.json`.

```python
{
    "file": str,                  # target_file_rel
    "definitions": list[dict],    # see structure below
    "callee_usages": list[dict],  # from build_usage_info_list
    "caller_usages": list[dict],  # from build_caller_usages
}
```

### Key Data Structures

**`definition_list` item** (derived from `DefinitionInfo` + source text):
| Field | Purpose |
|---|---|
| `name` | Symbol name (function/class/variable/etc.) |
| `type` | AST node type of the definition |
| `start_line` / `end_line` | 1-based line range in the file |
| `context` | Actual source code text for that line range |

**`usage_list` item** (produced by `build_usage_info_list`, i.e. `callee_usages`):
| Field | Purpose |
|---|---|
| `lines` | Sorted, deduplicated line numbers where the symbol is used |
| `name` | Used symbol name (possibly remapped via alias/typed-alias resolution) |
| `from` | Relative path of the file where the symbol is defined |
| `target_context` | Source code of the referenced definition |

**`caller_usages` item** (produced by `build_caller_usages`):
| Field | Purpose |
|---|---|
| `lines` | Sorted, deduplicated line numbers in the caller file |
| `name` | Symbol (defined in `target_file`) used by the caller |
| `file` | Relative path of the caller file |
| `usage_context` | Snippet(s) of source around the usage line(s) |

**Intermediate maps:**
- `symbol_to_file_map`: `{imported/local name -> defining file relative path}` — used to resolve which file a used name comes from.
- `alias_to_original`: `{alias name -> original name}` — used to resolve aliased imports back to their real definition name before looking up source code.
- `project_file_set`: set of all relative file paths in the project, used for import resolution and source-root detection.

# Error Handling

`file_analyzer.py` itself contains no explicit `try/except` blocks; it follows a **fail-fast** strategy at its own level, relying on unhandled exceptions to propagate upward from lower-level components (`parse_file`, `extract_definitions`, import/usage extraction) when unexpected conditions occur (e.g., unreadable files, unsupported encodings, malformed ASTs). Meanwhile, it delegates **graceful degradation for unsupported/ambiguous cases** to its dependencies, which are designed to return empty results (`None`, `[]`, `{}`) rather than raise, allowing the overall pipeline to continue processing other files even when a particular language or import pattern isn't fully supported.

The one explicit control-flow branch in this file—checking whether `language` and `import_query_str` are both truthy—is not exception handling but a conditional skip: if the file extension has no import-analysis configuration, import/usage analysis is bypassed entirely and empty lists (`usage_list`, `caller_usages`) are returned for those fields, while definition extraction still proceeds normally.

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported file extension for definitions (`DEFINITION_DICTS.get` returns `None`) | No explicit check in this file; `None` is passed through to `extract_definitions`, which is designed to handle it (per dependency doc, degrades gracefully) | Definitions may be empty/absent for unsupported languages, but processing continues |
| Unsupported file extension for import analysis (`get_import_params` returns `(None, None)`) | Explicit conditional skip of the entire import/usage block | `callee_usages` and `caller_usages` default to empty lists; no exception raised |
| File read/parse failures (`parse_file`) | Not caught in this file; propagates from `ts_parser.py` | Exception bubbles up to caller (`pipeline.py`), likely halting analysis for that file |
| Content decoding failures (`content.decode("utf-8")`) | Not caught; will raise `UnicodeDecodeError` if content is not valid UTF-8 | Exception propagates uncaught to the caller |
| Malformed/unresolvable imports or symbol mapping issues | Delegated to `build_symbol_to_file_map`, `extract_imports`, etc., which are documented to favor returning empty/partial results over raising | Missing or incomplete usage data, but no crash |

**Design considerations:**
- This module acts as an orchestrator: it deliberately avoids embedding defensive error handling itself, trusting that each specialized dependency (parser, extractor, import resolver) already implements appropriate degradation or validation internally.
- The single explicit guard (`if language and import_query_str`) reflects a deliberate design choice to treat "unsupported language for import analysis" as a normal, expected case rather than an error condition, distinguishing it from true failures (e.g., I/O or parse errors) which are allowed to propagate.
- Consistent with the "graceful degradation over exceptions" philosophy noted in the design summaries of its dependencies (e.g., `import_to_path.py`, `usage_analysis.py`), this file assumes that empty return values are safe defaults it can pass through to its output dict without additional validation.

# Summary

`file_analyzer.py` orchestrates per-file dependency analysis, exposing `get_file_dependencies(target_file, project_dir, project_dep_list) -> dict`. It sequences parsing, definition extraction, import resolution, and callee/caller usage analysis by delegating to specialized modules (parsers, extractors, import_to_path), without performing parsing/AST logic itself. Returns `{"file", "definitions", "callee_usages", "caller_usages"}`. Gracefully skips import/usage analysis for unsupported languages while still returning definitions. Used by `pipeline.py`; has no reverse dependency. Fails fast on I/O/decoding errors, otherwise relies on dependencies' graceful degradation.
