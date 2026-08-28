# Design Document: codetwine/extractors/usage_analysis.py

# Overview & Purpose

## 1. Module Summary
Builds bidirectional cross-file usage records — both "callee usages" (how this file uses symbols imported from elsewhere) and "caller usages" (how other project files use symbols defined in this file) — by combining AST usage extraction with import resolution and definition lookup.

## 2. When to Use This Module
- When generating the `callee_usages` JSON output for a file: call `build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` to get a list of merged usage entries (name, source lines, defining file, and the definition's source code) for every imported symbol referenced in that file.
- When generating the `caller_usages` JSON output for a file: call `build_caller_usages(target_file_rel, caller_file_list, project_dir, project_file_set)` to find, across all files that depend on `target_file_rel`, where and how they use names defined in it, complete with surrounding source-code context.
- When you need to determine, given a caller file's import statements, which names it actually imports from a specific target file (accounting for Python/JS/TS named imports, Java/Kotlin `import` and wildcard/package-visibility rules, and C/C++ `#include` whole-file inclusion) — use `_collect_names_from_target` internally within `build_caller_usages` (not intended for direct external use, but documents the module's import-resolution logic).
- When you need the full set of definition names in an arbitrary target file (e.g., for wildcard imports or C/C++ includes) — this is handled internally via `_load_target_definitions`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node` (Node), `symbol_to_file_map` (dict[str, str]), `project_dir` (str), `file_ext` (str), `alias_to_original` (dict[str, str] \| None) | `list[dict]` | Finds usages of names imported into a file from project-internal sources, merges duplicate usages by (definition file, name), and attaches the corresponding definition source code for each unique symbol. |
| `build_caller_usages` | `target_file_rel` (str), `caller_file_list` (list[str]), `project_dir` (str), `project_file_set` (set[str]) | `list[dict]` | For each file that depends on `target_file_rel`, resolves which names it imports from that target, locates their usage lines in the caller, groups/deduplicates them, and attaches short source-code context snippets. |

## 4. Design Decisions
- **Typed-alias remapping**: Both entry points detect variables whose declared type is an imported/target symbol (e.g., `Genre genre = ...`) via `extract_typed_aliases`, and remap usages of the alias variable back to the original type name so that usage grouping and definition lookup operate on the canonical symbol name rather than the local variable name.
- **Grouping/deduplication strategy**: Usages are keyed by `(source_file, name)` (in `build_usage_info_list`) or by `name` per caller file (in `build_caller_usages`), with line numbers accumulated and deduplicated via `sorted(set(...))`, ensuring one consolidated record per symbol rather than one record per occurrence.
- **Lazy, cached definition loading**: `_load_target_definitions` is only invoked on demand (wildcard imports, C/C++ `#include`, Java/Kotlin package-wildcard or same-package visibility) and its result (`target_definition_names`) is threaded through `build_caller_usages` as a cache parameter to avoid re-parsing the target file for every caller in the loop.
- **Language-agnostic import handling via separator convention**: Import-to-name resolution branches on `caller_separator` (`.` for Java/Kotlin-style dotted imports, `/` for C/C++-style includes) rather than hardcoding language names, delegating language-specific behavior to configuration (`IMPORT_RESOLVE_CONFIG`) rather than conditionals on file extension.
- **Bounded context extraction**: In `build_caller_usages`, usage context snippets are capped to the first two usage locations per group (`_max_context_locations = 2`) with a fixed radius of 3 lines (`_context_radius = 3`), trading completeness for output size control.

# Definition Design Specifications

## Module-level constants

| Name | Value/type | Purpose |
|---|---|---|
| `logger` | `logging.Logger` | Module-level logger obtained via `logging.getLogger(__name__)`; used for diagnostic logging within this module (not directly invoked in the shown code, but available to called functions/future use). |

---

## `build_usage_info_list`

**Signature:**
```python
def build_usage_info_list(
    root_node,
    symbol_to_file_map: dict[str, str],
    project_dir: str,
    file_ext: str,
    alias_to_original: dict[str, str] | None = None,
) -> list[dict]
```
- `root_node`: tree-sitter AST root node of the file being analyzed.
- `symbol_to_file_map`: maps an imported symbol name to the absolute/relative project path of the file that defines it. **This dict is mutated in place** (typed-alias entries are added).
- `alias_to_original`: maps a locally aliased import name to the original name in the source module (used to resolve renamed imports back to the real definition name for lookup).
- Returns: a list of dicts, each with keys `lines` (`list[int]`), `name` (`str`), `from` (`str`, file path), `target_context` (`str | None`, source code of the definition).

**Responsibility:** Produces the "callee_usages" data — for every project-internal symbol used in a file, the set of line numbers where it's used and the source code of its definition.

**When to use:** Called once per analyzed source file by `file_analyzer.py`, after imports have been resolved into `symbol_to_file_map`, to generate the usage/definition-linking report for that file.

**Design decisions:**
- Typed variable aliases (e.g., a variable `genre` declared with type `Genre`) are resolved first via `extract_typed_aliases` and merged into `symbol_to_file_map` so that usages of the alias variable are tracked as if they were usages of the type name — but only when the alias variable name doesn't already collide with an existing tracked symbol.
- Attribute-style usages (`helper.process`) are grouped by their root symbol (`helper`), and the root is what's used to look up the source file and possible alias/typed-alias remapping; the suffix (`.process`) is preserved when reconstructing the final `name`.
- Deduplication/merging is done via a `(source_file, remapped_name)` composite key so the same symbol used in multiple files/contexts is not confused, and repeated usages just accumulate line numbers instead of creating duplicate entries.
- Definition source code (`target_context`) is fetched only once per group (on first occurrence), not per usage line, to avoid redundant parsing/lookups.
- Alias resolution for source lookup (`alias_to_original`) is applied only to the search name passed into `extract_callee_source`, not to the `name` field stored in the output — so the displayed name reflects the "remapped" (typed-alias-resolved) name, while the actual source search may use the original pre-alias name.
- Final line lists are deduplicated and sorted for deterministic, clean output.

**Constraints & edge cases:**
- Assumes every `root_symbol` produced by `extract_usages` exists as a key in `symbol_to_file_map` (or was added via typed aliases) — a `KeyError` would occur otherwise.
- If `USAGE_NODE_TYPES.get(file_ext)` returns `None` (unsupported language), `usage_node_types` is `None`, `typed_alias_parent_types` becomes an empty set, and `extract_usages`/`extract_typed_aliases` degrade to returning empty results.
- `target_context` may be `None` if `extract_callee_source` cannot find a matching definition.

---

## `_collect_names_from_target`

**Signature:**
```python
def _collect_names_from_target(
    caller_import_list: list,
    target_file_rel: str,
    caller_ext: str,
    caller_rel: str,
    project_file_set: set[str],
    project_dir: str,
    target_definition_names: list[str] | None,
) -> tuple[list[str], list[str] | None]
```
- `caller_import_list`: list of `ImportInfo` objects extracted from the caller file's import statements.
- `target_definition_names`: cache of all definition names in the target file, or `None` if not yet computed; passed through so callers can reuse it across multiple invocations (avoids re-parsing the target file for every caller).
- Returns: `(names_from_target, target_definition_names)` — the list of symbol names from the target that this caller might reference, and the (possibly newly populated) cache.

**Responsibility:** Determines, per programming-language import style, which specific names a caller file could be referencing from a given target file, based on that caller's import statements (or same-package visibility rules).

**When to use:** Invoked once per caller file inside `build_caller_usages`, before attempting to locate actual usage lines, to narrow down the search set of symbol names.

**Design decisions:**
- Language-specific behavior is driven entirely by config (`IMPORT_RESOLVE_CONFIG[caller_ext]["separator"]`) rather than hardcoded per-language branches:
  - `names` present on `ImportInfo` (Python/JS/TS "from X import a, b") → names added directly; `"*"` wildcard triggers a full target-definition-name lookup.
  - `separator == "."` with no explicit names (Java/Kotlin `import com.foo.Bar`) → only the trailing module segment is treated as the imported name.
  - `separator == "/"` (C/C++ `#include`) → the entire target file's definitions are considered visible, since `#include` textually incorporates the whole file.
- Handles Java/Kotlin wildcard package imports (`import com.foo.*`) separately from direct-target wildcard imports: checks whether `target_file_rel` lives inside the imported package directory.
- Falls back to "same package visibility" (`SAME_PACKAGE_VISIBLE[caller_ext]`) when no import statement resolves to the target but the caller and target reside in the same directory — models languages (Java/Kotlin) where same-package classes are visible without imports. This fallback only triggers if `names_from_target` is still empty after processing all imports.
- `target_definition_names` is lazily computed (`_load_target_definitions`) only when actually needed (wildcard, C/C++, or same-package cases), and the computed value is threaded back to the caller for reuse — an explicit memoization pattern implemented via parameter passing rather than a class attribute or global cache.

**Constraints & edge cases:**
- If `caller_ext` has no entry in `IMPORT_RESOLVE_CONFIG`, `caller_resolve_config` defaults to `{}` and `caller_separator` defaults to `"."`, causing Java/Kotlin-style trailing-name handling to be attempted even for unconfigured languages.
- Names equal to `"*"` are explicitly filtered out of `import_info.names` when adding individual names, since `"*"` is a sentinel, not a real name.
- Relies on `resolve_module_to_project_path` correctly resolving relative/absolute imports; if resolution fails for all imports and same-package visibility is disabled/inapplicable, `names_from_target` remains empty and the caller file is effectively skipped for this target in `build_caller_usages`.

---

## `_load_target_definitions`

**Signature:**
```python
def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```
- Returns: flat list of all definition names (functions, classes, variables, etc., per language config) found in the target file; empty list if the extension is unsupported or the file doesn't exist.

**Responsibility:** Provides a single reusable routine to parse a target file and enumerate every named definition it contains, for use in wildcard-import, `#include`, and same-package resolution scenarios.

**When to use:** Called from `_collect_names_from_target` whenever a caller's reference to the target file cannot be resolved to specific named imports and instead requires "all definitions in the file" (wildcard `*`, C/C++ `#include`, Java/Kotlin same-package or wildcard package imports).

**Design decisions:**
- Extension is derived from `target_file_rel` via `os.path.splitext`, and definition extraction is skipped entirely (returns `[]`) if either there's no `DEFINITION_DICTS` entry for that extension or the resolved absolute path is not an existing file — avoids exceptions from parsing unsupported/missing files.
- Only definitions with a non-empty/truthy `name` are included, filtering out anonymous or unnamed definitions returned by `extract_definitions`.

**Constraints & edge cases:**
- Performs a fresh `parse_file` call each time it's invoked (benefiting from `parse_file`'s internal module-level cache), so repeated calls for the same file across different callers don't re-read/re-parse from disk unnecessarily, but do still incur one `extract_definitions` traversal per call — mitigated by the memoization performed by the caller (`_collect_names_from_target`/`build_caller_usages`).

---

## `build_caller_usages`

**Signature:**
```python
def build_caller_usages(
    target_file_rel: str,
    caller_file_list: list[str],
    project_dir: str,
    project_file_set: set[str],
) -> list[dict]
```
- Returns: a list of dicts, each with keys `lines` (`list[int]`), `name` (`str`), `file` (`str`, the caller's relative path), `usage_context` (`str`, snippet(s) of surrounding source lines).

**Responsibility:** Produces the "caller_usages" data — for a given target file, finds every other project file that references its definitions, and for each, the specific lines and source-code context of those references.

**When to use:** Called once per analyzed file by `file_analyzer.py`, using a precomputed reverse-dependency map (`caller_map`) that lists which files import/depend on the target file, to generate the reverse-usage report.

**Design decisions:**
- Caches `target_definition_names` **once outside the per-caller loop** and threads it through `_collect_names_from_target` calls, so the target file's definitions are parsed/extracted at most once across all callers (important for C/C++ `#include`-style whole-file visibility, which is common across many callers).
- Extends the tracked name set with typed-alias variable names (same pattern as `build_usage_info_list`), but only adds them if not already present in `names_from_target` (avoids duplicate entries in the list, unlike the dict-based dedup used elsewhere).
- Reads the caller's raw source file lazily (`caller_source_lines`) and only if there is at least one usage found (`usage_list` non-empty), avoiding unnecessary file I/O for callers with no actual matches.
- File read errors (`OSError`, `UnicodeDecodeError`) are caught and silently ignored, leaving `caller_source_lines = None`; this results in groups being produced without a `usage_context` key (since the context-building block is skipped when `caller_source_lines` is falsy).
- Usage grouping key is the (possibly typed-alias-remapped) `name` string only — **not** combined with file, since all usages in this loop iteration belong to the same `caller_rel` file by construction.
- Context snippets are limited to the first `_max_context_locations = 2` usage line numbers per group (to bound output size for symbols used many times), each expanded by `_context_radius = 3` lines above/below, and multiple snippets are joined with a `"\n...\n"` separator to visually indicate a gap.
- Line/column bounds for snippets are clamped via `max(0, ...)` / `min(total_lines, ...)` to prevent out-of-range slicing at file boundaries.

**Constraints & edge cases:**
- If `get_import_params(caller_ext)` returns `(None, None)` (unsupported language), that caller file is skipped entirely (`continue`), contributing nothing to `caller_usages`.
- If `names_from_target` is empty after `_collect_names_from_target`, the entire usage-extraction block (Step 2 onward) is skipped for that caller — no groups are added.
- Groups without any source lines successfully read (`caller_source_lines is None`) will lack the `"usage_context"` key in their output dict, meaning consumers must handle its potential absence.
- Assumes `caller_map.get(target_file_rel, [])` (constructed by the caller in `file_analyzer.py`) provides valid, existing relative file paths; no existence check is performed before calling `parse_file(caller_abs)`, so a missing/unreadable caller file would raise an exception (unlike the guarded read in the context-extraction step).

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/usage_analysis.py → codetwine/parsers/ts_parser.py (parse_file)` : Parses caller/target source files into tree-sitter AST root nodes so that imports, definitions, and usages can be extracted for cross-file usage analysis.

- `codetwine/extractors/usage_analysis.py → codetwine/extractors/imports.py (extract_imports)` : Extracts import statements from a caller file's AST in order to determine which names the caller imports and whether they originate from the target file being analyzed.

- `codetwine/extractors/usage_analysis.py → codetwine/extractors/usages.py (extract_usages, extract_typed_aliases)` : Extracts AST usage locations (calls, attribute accesses, identifiers, type references) of tracked symbol names, and builds variable-name → type-name mappings from typed variable declarations so aliased variables can be traced back to their original imported/defined type.

- `codetwine/extractors/usage_analysis.py → codetwine/extractors/definitions.py (extract_definitions)` : Extracts all definition names from a target file's AST, used when a caller imports via wildcard (`*`), package-level include, or same-package visibility, requiring the full set of names defined in the target file.

- `codetwine/extractors/usage_analysis.py → codetwine/extractors/dependency_graph.py (extract_callee_source)` : Retrieves the definition source code of a used symbol from its defining file, to attach as `target_context` in the usage output.

- `codetwine/extractors/usage_analysis.py → codetwine/import_to_path.py (resolve_module_to_project_path, get_import_params)` : Resolves an import's module string to a concrete project-internal file path, and retrieves the tree-sitter `Language` object plus import query string needed to run `extract_imports` for a given file extension.

- `codetwine/extractors/usage_analysis.py → codetwine/config/settings.py (DEFINITION_DICTS, USAGE_NODE_TYPES, IMPORT_RESOLVE_CONFIG, SAME_PACKAGE_VISIBLE)` : Reads per-language configuration dicts to determine definition node/name mappings for `extract_definitions`, usage-node-type settings for `extract_usages`/`extract_typed_aliases`, import separator/resolution rules for module-to-path matching, and whether a language allows same-package (no-import) visibility of symbols.

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py → codetwine/extractors/usage_analysis.py (build_usage_info_list)` : Calls this function with a file's AST root node, its symbol-to-file map, project directory, and file extension to obtain the list of usage locations (with attached definition source code) for the callee_usages JSON output.

- `codetwine/file_analyzer.py → codetwine/extractors/usage_analysis.py (build_caller_usages)` : Calls this function with the target file's relative path, the list of caller files, project directory, and project file set to obtain usage locations of this file's definitions found in other project files, for the caller_usages JSON output.

## Dependency Direction

All relationships described above are **unidirectional**:
- `usage_analysis.py` unidirectionally depends on `ts_parser.py`, `imports.py`, `usages.py`, `definitions.py`, `dependency_graph.py`, `import_to_path.py`, and `settings.py` — these modules do not import or call back into `usage_analysis.py`.
- `file_analyzer.py` unidirectionally depends on `usage_analysis.py` — `usage_analysis.py` does not import or call back into `file_analyzer.py`.

# Data Flow

## 1. Inputs

This module exposes two independent entry points, each consuming different shaped inputs:

**`build_usage_info_list` (callee-direction analysis)**
- `root_node`: tree-sitter AST root node of the file currently being analyzed.
- `symbol_to_file_map: dict[str, str]`: maps imported symbol names to the relative file path where they are defined. This dict is mutated in place (new entries added for typed aliases).
- `project_dir: str`: absolute path to the project root, used to resolve relative definition file paths.
- `file_ext: str`: file extension (no dot), used to look up `USAGE_NODE_TYPES`.
- `alias_to_original: dict[str, str] | None`: maps import aliases to their original names, used to correct definition lookups.
- Config input: `USAGE_NODE_TYPES` dict (per-language node type sets).

**`build_caller_usages` (caller-direction analysis)**
- `target_file_rel: str`: relative path of the file whose definitions are being traced for external usage.
- `caller_file_list: list[str]`: relative paths of files that potentially depend on the target file.
- `project_dir: str`: absolute project root path.
- `project_file_set: set[str]`: set of all relative file paths in the project, used for import resolution.
- Config inputs: `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `USAGE_NODE_TYPES`.
- File reads: each caller file is read from disk twice logically — once parsed into an AST via `parse_file`, and once (if usages are found) read as raw text lines via `open(...).read().splitlines()` for context snippet extraction.

## 2. Transformation Overview

### Pipeline A: `build_usage_info_list`
1. **Config lookup**: Retrieve `usage_node_types` for `file_ext`; extract `typed_alias_parent_types`.
2. **Typed alias detection**: Call `extract_typed_aliases` on `root_node` against the known symbol names to build a `var_name -> type_name` map (e.g., `genre -> Genre`). Extend `symbol_to_file_map` so aliased variables resolve to the same definition file as their type.
3. **Usage extraction**: Call `extract_usages` over the AST using the full set of tracked names (including aliases) to produce a flat `list[UsageInfo]` (name + line).
4. **Name normalization & alias remapping** (per usage):
   - Split `usage.name` on `.` to get `root_symbol` (handles attribute access like `helper.process`).
   - If `root_symbol` is a typed alias, remap it back to its original type name, adjusting the full name string accordingly.
   - Resolve `root_symbol` to its `source_file` via `symbol_to_file_map`.
5. **Grouping**: Build a `group_key = (source_file, remapped_name)`. If seen before, append the new line number; otherwise create a new group entry.
6. **Definition source lookup (first occurrence only)**: If an import alias mapping exists (`alias_to_original`), rewrite `search_name` to the original name. Call `extract_callee_source(source_file, search_name, project_dir)` to fetch the actual definition source code text (or `None`).
7. **Deduplication**: For every group, sort and dedupe the accumulated `lines` list.
8. **Fan-in**: All usages collapse into `usage_group_map`, keyed by `(file, name)`, then flattened to a list for output.

### Pipeline B: `build_caller_usages`
1. **Per-caller loop** (sequential, one caller file at a time), with a cross-iteration cache `target_definition_names` to avoid re-parsing the target file repeatedly for C/C++/wildcard cases.
2. **Parse caller**: `parse_file(caller_abs)` → AST root.
3. **Import extraction**: Determine `(language, import_query_str)` via `get_import_params(caller_ext)`; skip file if unsupported. Extract `caller_import_list` via `extract_imports`.
4. **Name collection from imports** (`_collect_names_from_target`, sub-pipeline):
   - For each import, resolve its module string to a project file path via `resolve_module_to_project_path`.
   - If resolved path equals `target_file_rel`:
     - Named imports (`from X import a, b`) → add names directly.
     - Wildcard imports (`*`) → lazily load all definitions from the target file via `_load_target_definitions` (parses target file + `extract_definitions`), cache result.
     - Dot-separator languages (Java/Kotlin) with no explicit names → take the last module path segment as the imported name.
     - Slash-separator languages (C/C++) → treat `#include` as importing the entire file; load all target definitions.
   - If unresolved but wildcard + dot-separator (Java/Kotlin package wildcard) → check if target lives inside the imported package directory; if so, load all target definitions.
   - Fallback: if no names collected and `SAME_PACKAGE_VISIBLE` is true for this extension, and caller/target share the same directory, load all target definitions.
   - Returns `(names_from_target, target_definition_names)` — the cache flows back to the outer loop for reuse across callers.
5. **Usage extraction within caller** (only if `names_from_target` is non-empty):
   - Look up `usage_node_types` for `caller_ext`.
   - Detect typed aliases (`extract_typed_aliases`) within the caller against `names_from_target`; extend the tracked name set.
   - Run `extract_usages` over the caller AST → `list[UsageInfo]`.
6. **Source line loading**: If any usages found, read the caller file's raw text into `caller_source_lines` for later context snippet slicing (best-effort, errors swallowed).
7. **Grouping by name**: For each usage, remap alias root symbols back to original type names (like Pipeline A), then group by `name` into a dict of `{lines, name, file}`, accumulating line numbers.
8. **Dedup + context extraction**: Sort/dedupe each group's `lines`. For up to 2 usage locations (`_max_context_locations`) per group, slice `±3 lines` (`_context_radius`) of surrounding source text from `caller_source_lines`, join multiple snippets with `"\n...\n"` into `usage_context`.
9. **Fan-out/fan-in across callers**: Each caller's `groups.values()` are extended into the shared `caller_usages` list, accumulating results across the full `caller_file_list` loop.

## 3. Outputs

**`build_usage_info_list`** returns `list[dict]`, each dict representing one merged usage group (a "callee_usages" record):
- Side effect: mutates the input `symbol_to_file_map` by adding typed-alias-derived entries.

**`build_caller_usages`** returns `list[dict]`, each dict representing one usage group per caller file (a "caller_usages" record):
- Side effect: reads caller files from disk (parse + raw text read); no mutation of inputs.

**`_load_target_definitions`** (internal helper) returns `list[str]` of definition names found in a target file; used by both flows via caching.

**`_collect_names_from_target`** (internal helper) returns `tuple[list[str], list[str] | None]`: the collected import-derived names plus the (possibly newly populated) cache of target definition names.

## 4. Key Data Structures

### `usage_group_map` entry (dict) — output of `build_usage_info_list`
| Field / Key | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where the symbol is used in the analyzed file |
| `name` | `str` | Remapped usage name (original type/name after alias resolution) |
| `from` | `str` | Relative file path where the symbol is defined |
| `target_context` | `str \| None` | Source code text of the definition, or `None` if not found |

### `groups` entry (dict) — output of `build_caller_usages`
| Field / Key | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where the name is used in the caller file |
| `name` | `str` | Usage name (remapped from typed alias if applicable) |
| `file` | `str` | Relative path of the caller file containing the usage |
| `usage_context` | `str` | Concatenated source snippets (±3 lines) around up to 2 usage locations, joined by `"\n...\n"` |

### `typed_aliases` (dict) — intermediate, from `extract_typed_aliases`
| Field / Key | Type | Purpose |
|---|---|---|
| key: variable name | `str` | Local variable declared with an imported/tracked type |
| value: type name | `str` | The imported type name the variable is declared as |

### `UsageInfo` (dataclass, from `usages.py`) — intermediate
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name (possibly dotted, e.g. `helper.process`) found in a usage location |
| `line` | `int` | Line number (1-indexed) where the usage occurs |

### `ImportInfo` (dataclass, from `imports.py`) — intermediate input to `_collect_names_from_target`
| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | Import source module/path/header string |
| `names` | `list[str]` | Explicitly imported names (may include `"*"` for wildcard) |
| `line` | `int` | Line number of the import statement |
| `module_alias` | `str \| None` | Alias assigned to the whole module (`import X as Y`) |
| `alias_map` | `dict[str,str] \| None` | Maps aliased imported names to original names |

### `symbol_to_file_map` (dict) — input/mutated in `build_usage_info_list`
| Field / Key | Type | Purpose |
|---|---|---|
| key: symbol name | `str` | Imported symbol (function, class, variable, alias) |
| value: file path | `str` | Relative path to the file where the symbol is defined |

# Error Handling

## 1. Overall Strategy

This file follows a **graceful degradation / logging-and-continue** policy rather than fail-fast. Since it operates over a large project-wide corpus of source files with heterogeneous language support, the design assumes that individual lookups (missing definitions, unresolved imports, unreadable files, unsupported languages) are normal and expected occurrences rather than fatal errors. Missing or unresolved data is represented with `None`, empty collections, or simply skipped entries, allowing the analysis to continue producing partial results for the rest of the project. The only explicit exception handling in this file guards file I/O when reading caller source for context extraction; all other potential failure points (unsupported extensions, unresolved symbols, missing definitions) are handled through conditional checks and early continuation rather than exception handling, delegating true exception propagation to lower-level dependencies (e.g., `parse_file`, tree-sitter query execution).

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported language for import analysis | `get_import_params(caller_ext)` returns `(None, None)` for a caller file extension not present in `IMPORT_QUERIES`/`TREE_SITTER_LANGUAGES` | `continue` to skip this caller file in the loop | Yes | That caller file is excluded from `caller_usages`; other callers are still processed |
| No usage node type config for extension | `USAGE_NODE_TYPES.get(file_ext)` returns `None` | `usage_node_types` set to `None`/empty defaults; `extract_usages`/`extract_typed_aliases` return empty results | Yes | No usages detected for that language; empty list returned instead of error |
| Missing definition dict for target extension | `DEFINITION_DICTS.get(target_ext)` returns `None`, or target file does not exist (`os.path.isfile` check fails) | `_load_target_definitions` skips parsing and returns an empty `names` list | Yes | No definition names collected from target file; downstream usage collection yields nothing for that target |
| Symbol/type not found for a usage | `root_symbol` absent from `symbol_to_file_map`, or alias/type lookup misses | Implicit reliance on prior filtering (`extract_usages` only returns names already in `imported_names`); no explicit fallback exists beyond this guarantee | N/A (prevented by upstream filtering) | Usage list only contains names already known to be resolvable, avoiding `KeyError` in practice |
| Definition source not found in target file | `extract_callee_source` searches for both dot-suffix and dot-prefix names and finds neither | Returns `None`; stored as `target_context: None` in the usage entry | Yes | Usage entry still emitted with lines/name/file, but without source snippet |
| Caller source file unreadable for context extraction | `open(caller_abs, ...)` raises `OSError` or `UnicodeDecodeError` (e.g., binary file, permission issue, encoding mismatch) | Caught explicitly; `caller_source_lines` remains `None` | Yes | `usage_context` is omitted (never added) for that caller's usage groups; usage line/name data is still preserved |
| Import resolution miss | `resolve_module_to_project_path` returns `None` for a given import (external library, stdlib, or unresolved path) | The import is simply not matched against `target_file_rel`; no names are added for it | Yes | That import contributes nothing to `names_from_target`; other imports are still evaluated |
| No names resolved from target imports at all | `names_from_target` remains empty for a caller after import matching and same-package fallback | Usage extraction step (Step 2) is skipped entirely for that caller | Yes | No caller_usages entries generated for that caller file |

## 3. Design Notes

- The file relies heavily on **upstream guarantees** from its dependencies: `extract_usages` only returns names already present in the tracked `imported_names` set, which is why lookups into `symbol_to_file_map` are not defensively guarded against `KeyError`. This shifts data-integrity responsibility to the extraction/config layer rather than duplicating validation locally.
- Absence of data is uniformly modeled as `None` or empty containers (`[]`, `{}`) rather than raising exceptions, which keeps the aggregation logic (grouping, merging lines, deduplication) simple and allows partial results to propagate cleanly through the returned list of dicts.
- The single explicit `try/except` (around reading caller source for context) is scoped narrowly to an operation with genuinely unpredictable environmental failure modes (encoding, filesystem access), consistent with the broader policy of only intercepting errors where recovery behavior (proceeding without context) is meaningful and where the same guarantee cannot be established by construction elsewhere in the code.
- Parsing failures (`parse_file`) and query construction failures are not caught in this file; error propagation for those cases is deferred to the caller (`file_analyzer.py`) or the underlying parser module, reflecting a layered responsibility split rather than centralized error handling in this module.

# Summary

Builds bidirectional cross-file usage records linking symbol usages to their definitions. Public API: `build_usage_info_list(root_node, symbol_to_file_map: dict[str,str], project_dir: str, file_ext: str, alias_to_original: dict[str,str]|None) -> list[dict]` and `build_caller_usages(target_file_rel: str, caller_file_list: list[str], project_dir: str, project_file_set: set[str]) -> list[dict]`. Key structures: usage group dicts (`lines`, `name`, `from`/`file`, `target_context`/`usage_context`), `UsageInfo`, `ImportInfo`, `symbol_to_file_map`.
