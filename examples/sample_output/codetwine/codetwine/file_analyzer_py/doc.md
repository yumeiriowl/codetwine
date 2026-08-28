# Design Document: codetwine/file_analyzer.py

# Overview & Purpose

`file_analyzer.py` acts as the per-file orchestration layer of Codetwine's analysis pipeline. Rather than embedding parsing, definition extraction, import resolution, and usage tracking logic directly into the pipeline driver, this module composes those independently-responsible components (from `ts_parser`, `extractors.definitions`, `extractors.imports`, `extractors.usage_analysis`, `import_to_path`, and `config.settings`) into a single cohesive result for one target file. This separation keeps `pipeline.py` free of analysis details—it only needs to precompute project-wide shared state (`project_file_set`, `source_root_set`, `caller_map`) once and delegate all per-file work to this module.

Its core responsibility is to answer, for a given file: "what does this file define, what external symbols does it use (callees), and where is it used by others (callers)?" — producing the structured data that ultimately feeds `file_dependencies.json`.

### Main Public Interface

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str, project_dir: str, project_file_set: set[str], source_root_set: set[str], caller_map: dict[str, list[str]]` | `dict` with keys `"file"`, `"definitions"`, `"callee_usages"`, `"caller_usages"` | Parses a target file, extracts its definitions, resolves its imports to project files, collects both outgoing (callee) and incoming (caller) symbol usages, and returns a unified per-file dependency record. |

### Design Decisions

- **Pipeline/facade pattern**: The function sequences four distinct concerns (parsing → definition extraction → import resolution → usage analysis) in a fixed order, each delegated to a specialized module, keeping this file itself free of low-level AST or resolution logic.
- **Shared state passed by reference**: `project_file_set`, `source_root_set`, and `caller_map` are computed once per project by the caller (`pipeline.py`) and passed into every invocation, avoiding redundant recomputation across files.
- **Graceful degradation for unsupported languages**: `definition_dict` and `get_import_params` return `None`/`(None, None)` for extensions without configured support; the function checks `language and import_query_str` before attempting import/usage analysis, falling back to empty `usage_list`/`caller_usages` rather than failing.
- **Path normalization**: The target file's path is converted to a project-relative, forward-slash-normalized form (`target_file_rel`) up front, ensuring consistent path representation across all downstream calls (import resolution, caller lookups) and in the returned result.
- **Source extraction via line ranges**: Definition source (`context`) is reconstructed by slicing decoded file content lines using each definition's `start_line`/`end_line`, avoiding a second parse or re-read of the file.

# Definition Design Specifications

## `get_file_dependencies`

**Signature**: `(target_file: str, project_dir: str, project_file_set: set[str], source_root_set: set[str], caller_map: dict[str, list[str]]) -> dict`

**Responsibility**: Acts as the per-file orchestrator of the analysis pipeline, coordinating parsing, definition extraction, and bidirectional usage analysis (callee and caller) into a single unified result consumed by `pipeline.py` to build `file_dependencies.json`. It is the single entry point that ties together all extractor/resolver modules for one file.

**Parameters**:
- `target_file`: Absolute path of the file being analyzed.
- `project_dir`: Absolute path to the project root, used to compute relative paths and to resolve other files during caller/callee analysis.
- `project_file_set`: Set of all relative file paths in the project; needed to resolve imports to actual project files (as opposed to external/stdlib modules).
- `source_root_set`: Set of detected source root prefixes (e.g. `"src/main/java/"`), used to correctly resolve absolute-style imports (mainly Java/Kotlin) to project-relative paths.
- `caller_map`: Precomputed `{file relative path: list of dependent files}` mapping, shared across all files in a project so it only needs to be built once by the caller (`pipeline.py`) rather than per file.

**Return value**: A dict with keys `"file"` (relative path), `"definitions"` (list of dicts with name/type/line range/source snippet), `"callee_usages"` (symbols this file imports and uses from elsewhere, with target source snippets), and `"caller_usages"` (locations in other files where this file's definitions are used). This shape is the direct schema for the JSON output artifact.

**Design decisions**:
- Language support is entirely config-driven: the file extension is looked up in `DEFINITION_DICTS` (for definition extraction) and via `get_import_params` (for import/usage analysis). If either lookup fails (unsupported language), the corresponding analysis is silently skipped rather than raising, so unsupported extensions degrade gracefully to just returning an empty/partial result instead of failing the whole pipeline run.
- Import/usage analysis (import resolution, callee usages, caller usages) is only performed when both `language` and `import_query_str` are available, avoiding wasted work and errors for languages without a defined import query.
- Definition source snippets are built by slicing decoded UTF-8 source lines using each definition's 1-based inclusive `start_line`/`end_line`, so the returned `"context"` field always contains the exact original source text for that definition rather than a re-serialized AST representation.
- `project_file_set`, `source_root_set`, and `caller_map` are accepted as parameters instead of being recomputed here, since they are identical for every file in a project; this keeps the function fast when called repeatedly across a large file set.

**Edge cases / constraints**:
- Assumes file content is valid UTF-8 (`content.decode("utf-8")` is called unconditionally); non-UTF-8 files would raise.
- If `caller_map` has no entry for the target file's relative path, `caller_usages` becomes an empty list via the `.get(..., [])` default.
- Relies on `parse_file` returning a cached AST/content pair, so repeated calls for the same file elsewhere in a run avoid re-parsing.

# Dependency Description

### Dependencies (what this file uses)

- **`codetwine/config/settings.py` (`DEFINITION_DICTS`)**: Used to look up the per-language definition extraction schema (node type → name-extraction rule) based on the target file's extension, enabling `extract_definitions` to work generically across languages.

- **`codetwine/parsers/ts_parser.py` (`parse_file`)**: Used to parse the target file into a tree-sitter AST (`root_node`) and obtain its raw byte content, which serves as the basis for both definition extraction and import/usage analysis.

- **`codetwine/extractors/definitions.py` (`extract_definitions`)**: Used to extract structural definitions (functions, classes, variables, etc.) from the parsed AST, which are then converted into the `definitions` output with source code snippets from the file content.

- **`codetwine/import_to_path.py` (`get_import_params`, `build_symbol_to_file_map`)**: 
  - `get_import_params` is used to retrieve the tree-sitter `Language` object and import query string needed to analyze import statements for the file's language, skipping unsupported languages.
  - `build_symbol_to_file_map` is used to resolve imported names (extracted from the AST) into their defining files within the project, producing the symbol-to-file and alias mappings needed for usage tracking.

- **`codetwine/extractors/imports.py` (`extract_imports`)**: Used to extract raw import statement information from the AST, which is then fed into `build_symbol_to_file_map` for resolution.

- **`codetwine/extractors/usage_analysis.py` (`build_usage_info_list`, `build_caller_usages`)**:
  - `build_usage_info_list` is used to find where the target file uses symbols imported from other project files, attaching their definition source code (producing `callee_usages`).
  - `build_caller_usages` is used to find where other project files use symbols defined in the target file, based on the precomputed `caller_map` (producing `caller_usages`).

### Dependents (what uses this file)

- **`codetwine/pipeline.py` (`get_file_dependencies`)**: The pipeline calls `get_file_dependencies` for each file in the project to obtain its definitions, callee usages, and caller usages, which it uses as the source data for generating the `file_dependencies.json` output.

The dependency direction between `file_analyzer.py` and `pipeline.py` is unidirectional: `pipeline.py` depends on `file_analyzer.py`, driving per-file analysis as part of the overall project processing flow, while `file_analyzer.py` has no dependency back on `pipeline.py`.

# Data Flow

## Input

| Input | Source | Description |
|---|---|---|
| `target_file` | Caller (`pipeline.py`) | Absolute path of the file to analyze |
| `project_dir` | Caller | Absolute path to project root |
| `project_file_set` | Caller (built once per project) | Set of all project files (relative paths) |
| `source_root_set` | Caller (built once per project) | Known source-root prefixes (e.g. `"src/main/java/"`) |
| `caller_map` | Caller (built once per project) | `{file_rel_path: [dependent_file_rel_paths]}` |

## Transformation Flow

```
target_file
  ├─► path/ext derivation ─► target_file_rel, file_ext
  │                             └─► definition_dict (via DEFINITION_DICTS)
  │
  ├─► parse_file(target_file) ─► (root_node, content)
  │       └─► content decoded into content_lines (for slicing source snippets)
  │
  ├─► extract_definitions(root_node, definition_dict)
  │       ─► list[DefinitionInfo]
  │       ─► mapped into definition_list (dicts), each enriched with
  │          "context" = source lines sliced by start_line/end_line
  │
  └─► get_import_params(file_ext) ─► (language, import_query_str)
          │
          ├─ if unsupported language: usage_list = [], caller_usages = []
          │
          └─ if supported:
                extract_imports(root_node, language, import_query_str)
                   ─► list[ImportInfo]
                       │
                       ▼
                build_symbol_to_file_map(imports, target_file_rel, project_file_set,
                                          file_ext, project_dir, source_root_set)
                   ─► (symbol_to_file_map, alias_to_original)
                       │
                       ├─► build_usage_info_list(root_node, symbol_to_file_map,
                       │        project_dir, file_ext, alias_to_original)
                       │        ─► usage_list (callee usages: this file's use of
                       │            external project symbols)
                       │
                       └─► build_caller_usages(target_file_rel,
                                caller_map.get(target_file_rel, []),
                                project_dir, project_file_set)
                              ─► caller_usages (other files' use of this file's symbols)
```

Core transformation: raw AST + source bytes → structured definition records (with source snippets) + two directional usage lists (outgoing "callee" usages resolved via import graph, incoming "caller" usages resolved via the precomputed caller_map), all keyed by project-relative file paths.

## Output

Single dict returned to caller (`pipeline.py`), consumed as source data for `file_dependencies.json`:

```json
{
  "file": "relative/path.ext",
  "definitions": [
    {
      "name": "...", "type": "...",
      "start_line": int, "end_line": int,
      "context": "source snippet"
    }
  ],
  "callee_usages": [
    {
      "lines": [int, ...],
      "name": "symbol.name",
      "from": "definition/file/path",
      "target_context": "definition source snippet"
    }
  ],
  "caller_usages": [
    {
      "lines": [int, ...],
      "name": "symbol.name",
      "file": "caller/file/path",
      "usage_context": "usage snippet(s)"
    }
  ]
}
```

## Key Data Structures

| Structure | Fields | Purpose |
|---|---|---|
| `definition_dict` | `{ast_node_type: name_extraction_hint}` | Per-language rules driving `extract_definitions` |
| `definition_list` entry | `name, type, start_line, end_line, context` | Local representation of a code definition plus its extracted source text |
| `symbol_to_file_map` | `{imported_name: defining_file_rel_path}` | Resolves which project file a used name comes from |
| `alias_to_original` | `{alias_name: original_name}` | Resolves renamed imports back to original symbol names for lookup |
| `usage_list` entry (callee) | `lines, name, from, target_context` | One outgoing dependency: where used, what/where it's defined |
| `caller_usages` entry | `lines, name, file, usage_context` | One incoming dependency: where an external file uses this file's symbol |

# Error Handling

**Overall strategy:** `get_file_dependencies` follows a fail-fast policy with no internal try/except blocks. It performs no error suppression or recovery itself; instead it relies on called modules (parsing, definition/import extraction, usage analysis) to either succeed, raise, or degrade gracefully on their own. Any unhandled exception propagates directly to the caller (`pipeline.py`), which is responsible for per-file error isolation across the batch.

Graceful degradation is achieved only at the structural level: when a file's extension has no entry in `DEFINITION_DICTS` or no import query/language is available via `get_import_params`, the corresponding analysis step is simply skipped (empty definition dict, empty `usage_list`/`caller_usages`) rather than raising, allowing unsupported or partially supported languages to still produce a minimal, valid result dict.

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported/unknown file extension (`DEFINITION_DICTS.get` returns `None`) | Not treated as an error; `definition_dict` is `None`, and `extract_definitions` is called with it (delegated downstream) | Definitions extraction may yield no results for unsupported languages, but processing continues |
| No import query/language for extension (`get_import_params` returns `(None, None)`) | Explicit `if language and import_query_str` guard skips the entire import/usage analysis block | `usage_list` and `caller_usages` remain empty lists; no crash, partial result returned |
| File read/parse failures in `parse_file` (missing file, bad extension, decode issues) | Not caught here; propagates from `ts_parser.py` | Whole file analysis aborts for this file; exception surfaces to `pipeline.py` |
| Decoding errors on `content.decode("utf-8")` | Not caught; `UnicodeDecodeError` would propagate | Analysis of this file fails entirely |
| Errors inside dependency extraction functions (`extract_definitions`, `build_symbol_to_file_map`, `build_usage_info_list`, `build_caller_usages`) | Not caught locally; assumed to either raise or internally degrade gracefully (per their own documented behavior) | Failures propagate up; graceful-degradation behaviors (e.g., unresolved imports skipped) are inherited from those modules |

**Design considerations:** The module intentionally centralizes no defensive error handling, keeping the function focused on orchestration. This reflects a design where robustness against missing/partial language support is handled via configuration-driven checks (`DEFINITION_DICTS`, `get_import_params`) rather than exception handling, while genuine I/O or parsing failures are treated as fatal for that file and left to the caller (`pipeline.py`) to catch and isolate, preventing one file's failure from being silently masked.

# Summary

`file_analyzer.py` orchestrates per-file analysis: given a target file plus project-wide shared state (project file set, source roots, caller map), it parses the file, extracts definitions with source snippets, resolves imports, and computes bidirectional symbol usages (callee: outgoing dependencies; caller: incoming usages). Exposes single function `get_file_dependencies` returning `{file, definitions, callee_usages, caller_usages}`. Delegates to ts_parser, extractors (definitions/imports/usage_analysis), import_to_path, and settings. Unsupported languages degrade gracefully; other errors propagate to caller `pipeline.py`, which builds `file_dependencies.json`.
