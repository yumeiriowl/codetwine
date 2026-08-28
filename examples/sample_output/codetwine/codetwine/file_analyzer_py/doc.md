# Design Document: codetwine/file_analyzer.py

# Overview & Purpose

## 1. Module Summary
Analyzes a single source file to produce a unified dependency record combining its structural definitions, outgoing (callee) usages of imported symbols, and incoming (caller) usages by other project files.

## 2. When to Use This Module
- **Building per-file dependency data for a whole project**: Call `get_file_dependencies(target_file, project_dir, project_file_set, source_root_set, caller_map)` once per file (as done in `pipeline.py`) to obtain the definitions, callee usages, and caller usages needed to write `file_dependencies.json`.
- **Extracting a file's own definitions with source snippets**: Use the `definitions` list in the returned dict to get each function/class/variable's name, type, line range, and corresponding source text (already resolved from `extract_definitions` output).
- **Tracing what a file depends on**: Use the `callee_usages` list in the returned dict to see which imported names are used in the file, where they are defined, and their source code.
- **Tracing who depends on a file**: Use the `caller_usages` list in the returned dict to see which other project files use symbols defined in the target file and where.
- **Skipping unsupported languages gracefully**: Rely on this module's behavior of returning empty `callee_usages`/`caller_usages` lists (via `get_import_params` returning `(None, None)`) when the file's extension has no import query configured, without needing extra handling by the caller.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_file_dependencies` | `target_file: str`, `project_dir: str`, `project_file_set: set[str]`, `source_root_set: set[str]`, `caller_map: dict[str, list[str]]` | `dict` | Parses the target file, extracts its definitions, resolves its imports to build callee usage records, and builds caller usage records from files that depend on it; assembles all into a single result dict. |

## 4. Design Decisions
- **Language-agnostic orchestration**: This module contains no language-specific logic itself; it delegates parsing, definition extraction, import resolution, and usage analysis entirely to specialized modules, acting purely as a coordinator that assembles their outputs into one schema.
- **Graceful degradation for unsupported languages**: When `get_import_params` returns `(None, None)` for a file extension without import query configuration, the module skips all usage analysis and returns empty `callee_usages`/`caller_usages` lists rather than failing, while still returning `definitions` if a `definition_dict` exists.
- **Shared per-project state passed by reference**: `project_file_set`, `source_root_set`, and `caller_map` are computed once by the caller and passed unchanged into every call, avoiding redundant recomputation across files in the same project.

# Definition Design Specifications

## `get_file_dependencies`

**Signature:**
```python
def get_file_dependencies(
    target_file: str,
    project_dir: str,
    project_file_set: set[str],
    source_root_set: set[str],
    caller_map: dict[str, list[str]],
) -> dict
```

**Type explanations:**

| Parameter | Type | Meaning |
|---|---|---|
| `project_file_set` | `set[str]` | All project files as relative paths (e.g. `"src/main.py"`), used to determine whether a resolved import target is inside the project. |
| `source_root_set` | `set[str]` | Set of detected source-root prefixes (e.g. `{"src/main/java/"}`), used to strip prefixes when resolving imports to file paths. |
| `caller_map` | `dict[str, list[str]]` | Maps a file's relative path to the list of relative paths of files that depend on (import from) it. |

**Return type:** `dict` with fixed keys `"file"` (str), `"definitions"` (list of dicts), `"callee_usages"` (list of dicts), `"caller_usages"` (list of dicts).

**Responsibility:**
Acts as the single-file orchestration entry point of the analysis pipeline: it parses one target file, extracts its structural definitions, resolves its imports to project files, and gathers both outgoing (callee) and incoming (caller) cross-file symbol usage, packaging everything into the per-file record consumed downstream to build `file_dependencies.json`.

**When to use:**
Called once per project file by the pipeline driver (`process_all_files` / `pipeline.py`), which precomputes `project_file_set`, `source_root_set`, and `caller_map` once per project and reuses them across all calls.

**Design decisions:**
- Language support is data-driven: `file_ext` is looked up in `DEFINITION_DICTS` and `IMPORT_QUERIES`/`TREE_SITTER_LANGUAGES` (via `get_import_params`), so unsupported extensions silently degrade — `definition_dict` can be `None` (passed through to `extract_definitions`), and import/usage analysis is skipped entirely when `get_import_params` returns `(None, None)`.
- Import/usage analysis (symbol map building, callee usage extraction, caller usage extraction) is only performed when both `language` and `import_query_str` are truthy, avoiding wasted work for files/languages without import query definitions.
- Source context for each definition is reconstructed by slicing `content_lines` using 1-based `start_line`/`end_line` from `DefinitionInfo`, converting them to a 0-based Python slice (`start_line - 1 : end_line`).
- Paths are normalized to forward slashes (`.replace("\\", "/")`) to ensure consistent relative-path keys across platforms (important since `caller_map` and `project_file_set` are keyed by relative path strings).
- `caller_map.get(target_file_rel, [])` defensively defaults to an empty list when the target file has no known callers, so `build_caller_usages` is always called with a valid list (though only when import analysis is active for that language).

**Constraints & edge cases:**
- Assumes `target_file` is a valid, parseable source file with a `TREE_SITTER_LANGUAGES`-supported extension for language-based parsing (`parse_file` itself has no error handling and will raise if the extension is unsupported at the parser level).
- File content is decoded as UTF-8 via `content.decode("utf-8")`; non-UTF-8 files will raise a `UnicodeDecodeError`.
- `definition_dict` may be `None` for extensions absent from `DEFINITION_DICTS`; behavior in that case depends on `extract_definitions`'s handling of a `None` definition dict.
- If `get_import_params` returns `(None, None)` (unsupported/missing import query for the extension), `usage_list` and `caller_usages` remain empty lists, and the returned dict still contains all four keys with `callee_usages` and `caller_usages` as `[]`.
- Relies entirely on injected `project_file_set`, `source_root_set`, and `caller_map` being consistent for the whole project; this function performs no validation of those inputs.

## Module-level constant

**Name:** `logger`
**Type:** `logging.Logger`
**Definition:** `logger = logging.getLogger(__name__)`

**Responsibility:** Provides a module-scoped logger for this file, following standard Python logging conventions.

**When to use:** Available for any future logging calls within this module; not actively invoked in the current function body shown.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` (`DEFINITION_DICTS`): to look up the per-language definition-node mapping (keyed by file extension) needed to drive definition extraction; returns `None` for unsupported languages.

- `codetwine/file_analyzer.py` → `codetwine/parsers/ts_parser.py` (`parse_file`): to read the target file and obtain its tree-sitter AST root node plus raw byte content, which serves as the input for all subsequent extraction steps.

- `codetwine/file_analyzer.py` → `codetwine/extractors/definitions.py` (`extract_definitions`): to walk the AST and produce the list of definitions (functions, classes, variables, etc.) with name, type, and line range, which is then enriched with source-code context.

- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` (`get_import_params`, `build_symbol_to_file_map`): `get_import_params` supplies the tree-sitter `Language` object and import query string for the file's extension (or `(None, None)` if unsupported); `build_symbol_to_file_map` resolves the extracted imports into a symbol-to-file map and an alias-to-original map used to trace which project files define the imported names.

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` (`extract_imports`): to parse import/include statements out of the AST into `ImportInfo` records, which are fed into `build_symbol_to_file_map`.

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` (`build_usage_info_list`, `build_caller_usages`): `build_usage_info_list` finds where imported symbols are used within the target file and attaches the corresponding definition source code (callee usages); `build_caller_usages` finds where symbols defined in the target file are used by other project files that depend on it (caller usages), using the precomputed `caller_map`.

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/file_analyzer.py` (`get_file_dependencies`): calls this function once per project file, passing the target file path, project directory, project file set, source root set, and caller map, to obtain the definitions/callee_usages/caller_usages data used to build `file_dependencies.json`.

### Dependency Direction

All relationships are unidirectional. `codetwine/file_analyzer.py` depends on `settings.py`, `ts_parser.py`, `extractors/definitions.py`, `import_to_path.py`, `extractors/imports.py`, and `extractors/usage_analysis.py` to perform parsing, definition extraction, and import/usage analysis, but none of these modules import back from `file_analyzer.py`. Similarly, `codetwine/pipeline.py` depends on `file_analyzer.py` to orchestrate per-file analysis, while `file_analyzer.py` has no dependency on `pipeline.py`.

# Data Flow

## 1. Inputs

`get_file_dependencies` receives the following inputs from its caller (`pipeline.py`):

- `target_file: str` — absolute path of the file to analyze.
- `project_dir: str` — absolute path to the project root.
- `project_file_set: set[str]` — relative paths of all files in the project (shared/reused across all files of a project).
- `source_root_set: set[str]` — recognized source root prefixes (e.g. `"src/main/java/"`) (shared/reused).
- `caller_map: dict[str, list[str]]` — maps a file's relative path to the list of files depending on it (shared/reused).

Additional data pulled in during execution:

- `DEFINITION_DICTS` (module-level config dict, keyed by file extension) — supplies the per-language `definition_dict` used for definition extraction.
- File content and AST, obtained via `parse_file(target_file)` — returns `(root_node, content)` where `content` is raw bytes read from disk.

## 2. Transformation Overview

1. **Path/extension normalization** — `target_file` is converted to a project-relative path (`target_file_rel`) and its extension (`file_ext`) is extracted; `file_ext` is used to look up the language-specific `definition_dict` from `DEFINITION_DICTS`.

2. **Parsing** — `parse_file` produces `(root_node, content)`. The raw `content` bytes are decoded to UTF-8 and split into `content_lines` (a list of source lines) for later slicing.

3. **Definition extraction** — `extract_definitions(root_node, definition_dict)` walks the AST and returns a list of `DefinitionInfo` objects (name, type, start/end line). Each is transformed into a plain dict by joining the corresponding `content_lines` slice into a `context` string, producing `definition_list`.

4. **Import parameter resolution** — `get_import_params(file_ext)` looks up the tree-sitter `Language` object and import query string for the file's language. If either is missing (unsupported language), the import/usage pipeline is skipped and `usage_list`/`caller_usages` remain empty lists.

5. **Import extraction** (if language supported) — `extract_imports(root_node, language, import_query_str)` scans the AST and returns a list of `ImportInfo` records describing each import statement.

6. **Symbol resolution** — `build_symbol_to_file_map` consumes the `ImportInfo` list plus `target_file_rel`, `project_file_set`, `file_ext`, `project_dir`, `source_root_set`, and resolves imported symbol names to project-internal file paths, producing `symbol_to_file_map` (name → defining file) and `alias_to_original` (alias → original name).

7. **Callee usage analysis** — `build_usage_info_list` combines `root_node`, `symbol_to_file_map`, `project_dir`, `file_ext`, and `alias_to_original` to locate usages of imported symbols within the file and attach each usage's target source code, producing `usage_list` (a list of usage-group dicts).

8. **Caller usage analysis** — `build_caller_usages` uses `target_file_rel`, the list of caller files (looked up from `caller_map` via `target_file_rel`), `project_dir`, and `project_file_set` to re-parse each calling file, find where this file's symbols are used, and produce `caller_usages` (a list of usage-group dicts with surrounding context).

9. **Aggregation** — the four intermediate results (`target_file_rel`, `definition_list`, `usage_list`, `caller_usages`) are merged into a single output dict, with no further transformation.

There is no async/parallel fan-out inside this file; `build_caller_usages` internally iterates over caller files sequentially but that is encapsulated in the dependency module.

## 3. Outputs

The function returns a single dict (no file writes or other side effects occur directly in this module — persistence to `file_dependencies.json` happens in the caller):

| Key | Type | Content |
|---|---|---|
| `file` | `str` | Project-relative path of the analyzed file |
| `definitions` | `list[dict]` | Structural definitions found in the file, each with source context |
| `callee_usages` | `list[dict]` | Usages of externally-imported symbols within this file, with target definition source |
| `caller_usages` | `list[dict]` | Usages of this file's symbols found in other project files, with surrounding usage context |

## 4. Key Data Structures

**Definition entry (dict, element of `definitions`)**

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Name of the defined symbol (function, class, variable, etc.) |
| `type` | `str` | AST node type of the definition |
| `start_line` | `int` | 1-based start line of the definition |
| `end_line` | `int` | 1-based end line of the definition |
| `context` | `str` | Source code text spanning `start_line`–`end_line` |

**Callee usage entry (dict, element of `callee_usages`, produced by `build_usage_info_list`)**

| Field / Key | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where the symbol is used |
| `name` | `str` | Used symbol name (possibly remapped via typed-alias/original-name resolution) |
| `from` | `str` | Relative path of the file where the symbol is defined |
| `target_context` | `str` | Source code of the referenced definition |

**Caller usage entry (dict, element of `caller_usages`, produced by `build_caller_usages`)**

| Field / Key | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers of usage in the caller file |
| `name` | `str` | Symbol name used (remapped for typed aliases where applicable) |
| `file` | `str` | Relative path of the caller file |
| `usage_context` | `str` | Snippet(s) of source code surrounding the usage location(s) |

**Intermediate structures consumed from dependencies**

- `symbol_to_file_map: dict[str, str]` — imported/usable symbol name → relative path of the file defining it.
- `alias_to_original: dict[str, str]` — alias name → original symbol name (for `from X import a as b` style imports).
- `DefinitionInfo` (from `extractors.definitions`) — dataclass with `name`, `type`, `start_line`, `end_line`; consumed and converted into the `definitions` dict entries.
- `ImportInfo` (from `extractors.imports`) — dataclass with `module`, `names`, `line`, `module_alias`, `alias_map`; consumed internally by `build_symbol_to_file_map` and `build_caller_usages`.

# Error Handling

## 1. Overall Strategy

`get_file_dependencies` itself contains no `try/except` blocks and performs no explicit error handling. It follows a **fail-fast** strategy: it assumes the target file exists, is parseable, and is valid UTF-8 text, and it lets any exception raised by its dependencies (`parse_file`, `extract_definitions`, `extract_imports`, `build_symbol_to_file_map`, `build_usage_info_list`, `build_caller_usages`) propagate unmodified to the caller (`pipeline.py`). No logging of errors occurs in this file despite a module-level `logger` being defined and imported. Robustness against unsupported languages is handled through **graceful degradation via conditional branching** rather than exception handling: when a file extension is unsupported, `DEFINITION_DICTS.get` and `get_import_params` return `None`, and the function simply skips the corresponding analysis step (definitions or import/usage analysis) instead of raising.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension for definitions | `DEFINITION_DICTS.get(file_ext)` returns `None` | `definition_dict` is `None`; passed through to `extract_definitions`, which relies on `None` behaving as an empty/falsy mapping to yield no definitions | Yes (skipped, not an exception) | `definitions` list is empty for that file; rest of analysis proceeds |
| Unsupported file extension for import analysis | `get_import_params(file_ext)` returns `(None, None)` | The `if language and import_query_str` guard causes the entire import/usage block to be skipped | Yes (skipped, not an exception) | `callee_usages` and `caller_usages` remain empty lists; only `file` and `definitions` are populated |
| File read / parse failure | `parse_file` cannot open or parse `target_file` (e.g. missing file, unsupported/unregistered tree-sitter language, I/O error) | No handling; exception propagates unchanged | No | Entire `get_file_dependencies` call fails; caller (`pipeline.py`) must decide how to react per file |
| Non-UTF-8 file content | `content.decode("utf-8")` raises `UnicodeDecodeError` on binary or non-UTF-8 encoded source files | No handling; exception propagates unchanged | No | Processing of this file aborts before definitions/usages are computed |
| Malformed/missing caller entry | `caller_map.get(target_file_rel, [])` returns an unexpected type or the map is incomplete | Default value `[]` used via `.get`, avoiding a `KeyError` | Yes (defaults to empty caller list) | `caller_usages` computed as empty for files with no recorded callers |
| Downstream extractor/resolver errors | Errors raised inside `extract_definitions`, `extract_imports`, `build_symbol_to_file_map`, `build_usage_info_list`, or `build_caller_usages` (e.g. malformed AST assumptions, missing file during caller analysis) | No handling; exceptions propagate unchanged | No | Entire call for the target file fails; no partial dict is returned |

## 3. Design Notes

- The function is designed as a pure orchestration/aggregation layer: it composes calls to specialized extractor/resolver modules and trusts their contracts, deferring all error-handling responsibility either to those modules or to the top-level caller (`pipeline.py`).
- The only defensive coding present is the use of `dict.get` with defaults (`DEFINITION_DICTS.get`, `caller_map.get(target_file_rel, [])`) and conditional branching (`if language and import_query_str`) to treat "unsupported/absent configuration" as a normal, expected case rather than an error — this is the sole form of graceful degradation implemented here.
- Because `parse_file` results are cached at the module level (in `ts_parser.py`), a parse failure for a given file is not retried differently on subsequent calls within the same process; the fail-fast propagation means a single problematic file does not get a special recovery path within this file.
- The presence of an unused `logger` suggests logging-based error visibility was anticipated architecturally, but no log statements are actually invoked in this file — all error visibility currently depends on unhandled exceptions surfacing to the caller.

# Summary

Orchestrates single-file dependency analysis by parsing a file, extracting definitions, and resolving callee/caller symbol usages. Public function: `get_file_dependencies(target_file: str, project_dir: str, project_file_set: set[str], source_root_set: set[str], caller_map: dict[str, list[str]]) -> dict`. Produces/consumes `DefinitionInfo`, `ImportInfo`, `symbol_to_file_map: dict[str,str]`, `alias_to_original: dict[str,str]`; returns dict with `file: str`, `definitions: list[dict]`, `callee_usages: list[dict]`, `caller_usages: list[dict]`.
