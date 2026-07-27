# Design Document: codetwine/extractors/usage_analysis.py

# Overview & Purpose

## Purpose and Responsibilities

`usage_analysis.py` implements the "usage-analysis" layer of the codetwine dependency-extraction pipeline. It sits between the low-level, language-agnostic building blocks (`extract_imports`, `extract_usages`, `extract_typed_aliases`, `extract_definitions`, `parse_file`) and `file_analyzer.py`, which orchestrates per-file analysis. Its job is to answer two complementary questions for a single project file:

1. **Callee direction (`build_usage_info_list`)** — Given a file's AST and a mapping of imported symbol names to the files that define them, find every location where those imported symbols are actually used, merge duplicate usages of the same symbol into a single record with an accumulated, deduplicated list of line numbers, resolve typed-variable aliases (e.g. a local variable `genre` typed as `Genre`) back to the original imported name, and attach the actual definition source code fetched from the target file. This produces the data behind the `callee_usages` JSON output.

2. **Caller direction (`build_caller_usages`)** — Given a target file and the project-wide dependency graph, find every *other* file that imports from the target file, determine (per language) which names that caller actually pulled in from the target (explicit `from X import a, b`, Java/Kotlin `import com.foo.Bar`, wildcard imports, C/C++ `#include` whole-file inclusion, or same-package implicit visibility), locate the usage lines of those names inside each caller, group them, and attach a short source-code snippet (`usage_context`) around each usage. This produces the data behind the `caller_usages` JSON output.

The file exists separately from `usages.py`, `imports.py`, and `dependency_graph.py` because those modules provide generic, reusable AST-traversal primitives, whereas this module owns the *policy* of combining them: resolving aliases, grouping/deduplicating usage records, deciding per-language how imported names propagate from an import statement to a set of trackable symbol names, and shaping the final dict structures consumed by the JSON output/pipeline layer. It also owns a small private helper (`_load_target_definitions`) to enumerate all definitions in a target file, needed for wildcard imports, whole-file includes, and same-package visibility.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None = None` | `list[dict]` | Finds usages in this file of imported symbols, merges duplicates by (definition file, name), remaps typed-alias variables to their original imported type, and attaches the resolved definition source (`target_context`) for each group. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | For each known caller of `target_file_rel`, derives which of the target's names the caller imports, locates their usage lines in the caller, groups by name, and attaches a nearby source-code snippet (`usage_context`) per group. |

Internal (module-private, not part of the external contract but essential to behavior):

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `_collect_names_from_target` | `caller_import_list`, `target_file_rel`, `caller_ext`, `caller_rel`, `project_file_set`, `project_dir`, `target_definition_names` | `tuple[list[str], list[str] \| None]` | Determines, per language import style (named imports, wildcard, Java/Kotlin dotted import, C/C++ include, same-package visibility), which names from the target file are visible/importable in the caller. |
| `_load_target_definitions` | `target_file_rel`, `project_dir` | `list[str]` | Parses the target file and lists all definition names in it (used for wildcard/whole-file/same-package cases). |

## Design Decisions

- **Grouping/deduplication via dict keyed by (source_file, name) or by name**: both public functions accumulate multiple line hits for the same logical symbol into a single record (`lines` list), sorted and deduplicated with `sorted(set(...))`, avoiding redundant entries in the JSON output.
- **Alias resolution as a two-stage remap**: typed-variable aliases (`genre` → `Genre`) are resolved first via `extract_typed_aliases`, then import aliases (`alias_to_original`) are applied on top when searching for the actual definition, keeping usage-line tracking and source lookup correctly aligned with the original symbol name even through renaming layers.
- **Config-driven, per-language branching isolated in one helper**: rather than scattering language-specific logic throughout, all per-language rules for "which names does a caller inherit from an import of the target file" are centralized in `_collect_names_from_target`, driven by `IMPORT_RESOLVE_CONFIG`'s `separator` field (`.` for Java/Kotlin, `/` for C/C++) and `SAME_PACKAGE_VISIBLE`.
- **Lazy, cached computation of target definitions**: `target_definition_names` is computed at most once per `build_caller_usages` call (and passed through/cached across `_collect_names_from_target` invocations) to avoid repeatedly re-parsing and re-extracting definitions from the same target file for multiple callers.
- **Graceful degradation over exceptions**: missing usage-node-type configuration, absent alias mappings, or unreadable caller source files (caught via `except (OSError, UnicodeDecodeError)`) all result in reduced output (empty lists/omitted context) rather than raised errors, consistent with the fail-soft philosophy of the extractor modules it depends on.

# Definition Design Specifications

## `build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original=None) -> list[dict]`

**Arguments:**
- `root_node`: AST root node of the file being analyzed.
- `symbol_to_file_map`: Maps imported symbol names to the file path where they are defined. Mutated in place to include typed-alias variable names.
- `project_dir`: Absolute path to the project root, used to locate definition source files.
- `file_ext`: File extension (no leading dot), used to select language-specific usage node type configuration.
- `alias_to_original`: Optional mapping from import alias to original name, used to resolve the correct definition when the imported symbol was renamed at import time (e.g. `import X as Y`).

**Returns:** A list of dicts, each with `lines` (sorted, deduplicated line numbers), `name` (resolved/remapped symbol name), `from` (definition file path), and `target_context` (source code of the definition, or `None` if not found). This becomes the `callee_usages` JSON output.

**Responsibility:** Produces, for a single file, a consolidated record of every project-internal imported symbol used in that file, together with the actual source code of its definition, so downstream consumers don't need to re-resolve definitions per usage line.

**Design decisions:**
- Typed variable aliases (e.g. a variable `genre` declared with type `Genre`) are first extracted and folded into `symbol_to_file_map` so that usages of the variable are attributable to the same definition file as the type. This is necessary because languages like Java/Kotlin/C++ reference imported types indirectly through variable names rather than the type name itself.
- Usages are grouped by `(definition_file, remapped_name)` rather than by raw usage name, so that attribute accesses (`helper.process`) and simple identifiers referring to the same underlying symbol are merged into one entry, and lines from multiple occurrences accumulate into a single `lines` list instead of duplicate entries.
- Alias remapping happens twice: first from typed-alias variable name back to the original type name (for grouping), then—independently—from import alias to original name (for definition lookup via `search_name`), since these two alias mechanisms serve different purposes (variable-to-type vs. import-renaming).
- Source code retrieval (`extract_callee_source`) is only performed on first encounter of a group key, deferring the potentially expensive parse/search operation.

**Edge cases:** If `usage_node_types` is not configured for `file_ext` (`USAGE_NODE_TYPES.get` returns `None`), `typed_alias_parent_types` falls back to an empty set and `extract_usages` will return an empty list, so the function returns `[]`. Assumes every root symbol in `usage_info_list` exists as a key in `symbol_to_file_map` (via typed alias remap or original), since it is indexed without a guard.

---

## `_collect_names_from_target(caller_import_list, target_file_rel, caller_ext, caller_rel, project_file_set, project_dir, target_definition_names) -> tuple[list[str], list[str] | None]`

**Arguments:**
- `caller_import_list`: List of `ImportInfo` extracted from the caller file's import statements.
- `target_file_rel`: Relative path of the file whose definitions are being searched for usage.
- `caller_ext`: Caller's file extension, used to look up its import-resolution separator convention.
- `caller_rel`: Caller's relative path, needed to resolve relative/module imports to project paths.
- `project_file_set`: Set of all project file paths, used for import resolution.
- `project_dir`: Absolute project root path, forwarded to definition loading.
- `target_definition_names`: Cache of the target file's definition names (used for `import *`, Java/Kotlin wildcard, C/C++ `#include`, and same-package visibility cases); `None` triggers a lazy load on first need.

**Returns:** A tuple of `(names_from_target, target_definition_names)`, where `names_from_target` is the list of symbol names the caller could reference from the target file, and the second element is the (possibly newly populated) cache to be reused by subsequent calls.

**Responsibility:** Determines, per caller file, which specific names originating from the target file are potentially in scope, using language-specific import semantics rather than a single universal rule, since different languages expose "what is imported" very differently (explicit name lists vs. leaf class name vs. whole-file inclusion vs. implicit same-package visibility).

**Design decisions:**
- Distinguishes import styles by inspecting `import_info.names` (Python/JS/TS style explicit names) versus `caller_separator` (`.` for Java/Kotlin dotted imports, `/` for C/C++ `#include`), rather than branching on language name directly, keeping the logic driven by configuration (`IMPORT_RESOLVE_CONFIG`).
- Wildcard imports (`from X import *`, Java/Kotlin `import pkg.*`) and whole-file includes (C/C++) all fall back to loading *all* definition names from the target file via `_load_target_definitions`, since precise symbol-level import information isn't available in these forms.
- Same-package implicit visibility (Java/Kotlin) is only applied as a fallback when no import-based names were found, avoiding unnecessary definition loading when explicit imports already resolved the names.
- `target_definition_names` is threaded through as an explicit cache parameter (rather than a closure or module-level cache) so the expensive full-file parse/definition-extraction only happens once per target file across all callers in the caller loop.

**Edge cases:** Java/Kotlin wildcard-import package matching is done via string prefix comparison against `target_file_rel`, assuming forward-slash-normalized paths. If `caller_separator` is neither `.` nor `/` and `import_info.names` is empty, no names are collected from that import statement.

---

## `_load_target_definitions(target_file_rel, project_dir) -> list[str]`

**Arguments:**
- `target_file_rel`: Relative path of the target file from the project root.
- `project_dir`: Absolute project root path.

**Returns:** List of all definition names (functions, classes, variables, etc.) found in the target file; empty list if the extension is unsupported, the file doesn't exist, or no definitions have a name.

**Responsibility:** Centralizes the logic for exhaustively enumerating a file's definitions, needed whenever precise imported-symbol information is unavailable (wildcard imports, `#include`, same-package visibility), so `_collect_names_from_target` doesn't duplicate parsing/definition-extraction logic across its several fallback branches.

**Design decisions:** Silently returns an empty list rather than raising when `DEFINITION_DICTS` has no entry for the extension or the file is missing, consistent with the module's general degrade-gracefully approach to unsupported languages or unresolved paths.

---

## `build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set) -> list[dict]`

**Arguments:**
- `target_file_rel`: Relative path of the file whose usages by other project files are being collected (the "callee" from this file's perspective).
- `project_dep_list`: Precomputed dependency graph entries (`{file, callers, callees}`) used to find which files call `target_file_rel`.
- `project_dir`: Absolute project root path.
- `project_file_set`: Set of all project file paths, needed for import resolution during name collection.

**Returns:** A list of dicts, each with `lines` (sorted, deduplicated), `name`, `file` (the caller file), and `usage_context` (source snippet(s) around the usage). This produces the `caller_usages` JSON output.

**Responsibility:** For a given file, finds every other project file that calls into it and records exactly where (with surrounding source context) those calls occur, enabling reverse-dependency usage reporting.

**Design decisions:**
- The target's definition-name cache (`target_definition_names`) is declared once outside the per-caller loop and threaded through `_collect_names_from_target` calls, avoiding redundant re-parsing of the target file across multiple callers (important for wildcard/`#include`/same-package cases where the whole file must be parsed).
- Callers with no `language`/`import_query_str` support (`get_import_params` returns `(None, None)`) are skipped entirely, since import extraction—and therefore usage attribution—cannot proceed without a language-specific query.
- Typed alias variables discovered in the caller are added to `names_from_target` so that variables typed with an imported/target type are tracked as usages, mirroring the same alias-resolution approach used in `build_usage_info_list`.
- Usage context extraction is capped to the first 2 usage locations per group (`_max_context_locations = 2`) and a ±3-line radius (`_context_radius = 3`) around each usage line, keeping context snippets bounded in size regardless of how many times a symbol is used in one file; multiple snippets are joined with a `"\n...\n"` separator to visually indicate non-contiguous excerpts.
- Reading the caller's source file for context is skipped entirely if there are no usages, and read failures (`OSError`, `UnicodeDecodeError`) are tolerated by leaving `caller_source_lines` as `None`, in which case no `usage_context` key is added to any group for that caller.
- Grouping is keyed by resolved `name` only (not also by file, since all usages in a single loop iteration share the same `caller_rel`), with typed-alias root symbols remapped to their original type name before grouping, matching the alias-merging behavior in `build_usage_info_list`.

**Edge cases:** If `target_file_rel` has no matching entry in `project_dep_list` (or has no callers), `caller_file_list` is empty and the function returns `[]`. If `names_from_target` ends up empty for a given caller (no relevant imports and no same-package visibility), that caller is skipped for usage extraction and contributes nothing to `caller_usages`. Line-number-based context slicing clamps `start`/`end` to file boundaries via `max`/`min`, so lines near the start/end of a file simply get a shorter snippet rather than raising an index error.

# Dependency Description

## Dependencies (what this file uses)

This file relies on a combination of configuration lookups, AST extraction utilities, and import/path resolution helpers to build usage analysis data:

- **`codetwine/config/settings.py`** (`USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`): Used to retrieve per-language configuration needed to drive usage/definition extraction and import resolution logic in a language-agnostic way. `USAGE_NODE_TYPES` supplies node-type sets for detecting usages and typed aliases; `IMPORT_RESOLVE_CONFIG` supplies the module-name separator used to distinguish import styles (e.g. dotted vs. slash-based); `SAME_PACKAGE_VISIBLE` determines whether same-directory references are allowed without explicit imports (Java/Kotlin); `DEFINITION_DICTS` supplies node-type mappings for extracting definitions from a target file.

- **`codetwine/extractors/usages.py`** (`extract_usages`, `extract_typed_aliases`): Used to locate where imported/tracked symbol names are referenced within an AST (`extract_usages`), and to detect typed variable declarations that alias an imported type name to a local variable (`extract_typed_aliases`), enabling alias-to-original-type remapping during usage grouping.

- **`codetwine/extractors/definitions.py`** (`extract_definitions`): Used by `_load_target_definitions` to enumerate all definition names in a target file, needed for wildcard imports, C/C++ `#include` (whole-file inclusion), Java/Kotlin wildcard imports, and same-package visibility cases.

- **`codetwine/extractors/dependency_graph.py`** (`extract_callee_source`): Used to retrieve the source code of a definition in another file once a usage's originating file and name are known, populating the `target_context` field in usage records.

- **`codetwine/import_to_path.py`** (`resolve_module_to_project_path`, `get_import_params`): Used to resolve a caller file's import module strings to concrete project file paths (to determine if a caller imports from the target file), and to obtain the tree-sitter language/query needed to extract imports for a given file extension.

- **`codetwine/parsers/ts_parser.py`** (`parse_file`): Used to parse caller and target source files into ASTs (with built-in caching) for both import extraction and definition extraction.

- **`codetwine/extractors/imports.py`** (`extract_imports`): Used to extract import statements from a caller file's AST so that names imported from the target file can be identified.

## Dependents (what uses this file)

- **`codetwine/file_analyzer.py`**: Uses `build_usage_info_list` to obtain usage locations and attached definition source code for names imported into a file, and uses `build_caller_usages` to obtain usage locations in other project files for names defined in the current file. Both functions are consumed as part of the file-level analysis pipeline that produces the JSON output data (`callee_usages` and `caller_usages`).

The dependency direction is unidirectional: `file_analyzer.py` depends on `usage_analysis.py` to perform usage extraction, while `usage_analysis.py` has no dependency back on `file_analyzer.py`.

# Data Flow

This file implements two independent pipelines that both derive from the same project dependency-analysis process but flow in opposite directions: **callee usage extraction** (what this file's symbols call into) and **caller usage extraction** (who calls into this file's symbols).

## 1. `build_usage_info_list` — Callee-side usage flow

**Input**
- `root_node`: tree-sitter AST of the file being analyzed.
- `symbol_to_file_map`: `{imported_name: definition_file_path}` (built upstream via `import_to_path.py`).
- `project_dir`, `file_ext`, optional `alias_to_original`: `{alias_name: original_name}`.

**Transformation flow**
```
root_node ──► extract_typed_aliases ──► typed_aliases {var_name: type_name}
                                              │
                                (merges var_name into symbol_to_file_map)
                                              │
root_node + symbol keys ──► extract_usages ──► usage_info_list [UsageInfo(name, line)]
                                              │
                     for each usage: resolve root_symbol → remap via typed_aliases
                                              │
                     group by (source_file, remapped_name) ──► usage_group_map
                                              │
                (first occurrence per group) ──► extract_callee_source ──► source_code text
                                              │
                              lines deduplicated/sorted
                                              ▼
                                     list[dict] output
```

**Output** — list of dicts, one per unique `(source_file, name)` pair:

| Field | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` (sorted, deduped) | All line numbers where the symbol is used |
| `name` | `str` | Resolved/remapped symbol name (post alias substitution) |
| `from` | `str` | Relative path of the file defining the symbol |
| `target_context` | `str \| None` | Source code of the definition, from `extract_callee_source` |

Destination: consumed by `file_analyzer.py` to produce the `callee_usages` JSON.

**Key internal structures**
- `typed_aliases`: `{var_name: type_name}` — lets a locally declared variable of an imported type be tracked as a usage of that type.
- `usage_group_map`: keyed by `(definition_file_path, canonical_name)`, merges repeated usages into a single record.

## 2. `build_caller_usages` — Caller-side usage flow

**Input**
- `target_file_rel`: the file whose definitions are being searched for external usage.
- `project_dep_list`: list of `{file, callers, callees}` dicts (from `dependency_graph.py`).
- `project_dir`, `project_file_set`.

**Transformation flow**
```
project_dep_list ──► lookup target_file_rel ──► caller_file_list [str]

for each caller_rel:
  parse_file ──► caller_root
  get_import_params + extract_imports ──► caller_import_list [ImportInfo]
                                              │
  _collect_names_from_target(imports, target_file_rel, ...) 
      ├─ direct "from X import a,b" names
      ├─ wildcard "*" → _load_target_definitions (parse target file defs)
      ├─ Java/Kotlin "import a.b.Bar" → leaf name
      ├─ C/C++ "#include" → all target definitions
      └─ same-package visibility fallback
                                              │
                                names_from_target [str] (cached target_definition_names)
                                              │
  extract_typed_aliases + extract_usages(caller_root, names_from_target)
                                              │
  group usages by (remapped) name ──► groups {name: {lines, name, file}}
                                              │
  for each group: slice caller_source_lines around each line (±context radius,
  capped to _max_context_locations) ──► usage_context string
                                              ▼
                              caller_usages.extend(groups.values())
```

**Output** — list of dicts, one per `(caller_file, name)` group across all callers:

| Field | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` (sorted, deduped) | Lines in the caller file referencing the target symbol |
| `name` | `str` | Symbol name (remapped through typed aliases if applicable) |
| `file` | `str` | Relative path of the caller file |
| `usage_context` | `str` | Concatenated source snippets (joined by `"\n...\n"`) around up to 2 usage locations |

Destination: consumed by `file_analyzer.py` to produce the `caller_usages` JSON.

**Key internal structures**
- `names_from_target`: `list[str]` — accumulated symbol names the current caller could reference from the target file, language-dependent derivation.
- `target_definition_names` (cache): `list[str] | None` — definitions of the target file, computed once via `_load_target_definitions` and reused across all callers within the same call (avoids re-parsing for wildcard/`#include`/same-package cases).
- `groups`: `{name: {lines, name, file}}` — per-caller aggregation before context extraction, later merged into the flat `caller_usages` list.

## Shared helper: `_load_target_definitions`

Input: `target_file_rel`, `project_dir` → parses the target file (`parse_file`) and runs `extract_definitions` using the extension-specific `DEFINITION_DICTS` entry, returning a flat `list[str]` of definition names. Used only as a cache-filling step for wildcard imports, `#include`, and same-package visibility scenarios in `_collect_names_from_target`.

# Error Handling

**Overall strategy:** This file follows a graceful degradation approach, favoring silent skips and empty-result fallbacks over raised exceptions. Missing configuration, unresolved lookups, and absent data are treated as expected conditions rather than errors, allowing analysis to continue across the rest of the project even when a particular file, language, or symbol cannot be processed. The only explicit exception handling is a narrow catch around file reading for usage-context extraction; all other error propagation is implicit, relying on upstream/dependency functions (e.g., `parse_file`, dict lookups) to raise naturally if inputs are invalid.

| Error Pattern | Handling | Impact |
|---|---|---|
| Unsupported/unknown file extension (`USAGE_NODE_TYPES.get`, `IMPORT_RESOLVE_CONFIG.get`, `DEFINITION_DICTS.get` return `None`/`{}`) | Treated as "no config available"; downstream functions (`extract_usages`, `extract_typed_aliases`) receive `None`/empty sets and return empty results | That file/language is skipped for usage or alias detection, but processing of other files continues |
| No import query/language available for caller extension (`get_import_params` returns `(None, None)`) | Caller loop uses `continue` to skip that caller file entirely | That single caller is excluded from `caller_usages`; other callers are still processed |
| Symbol/definition not found in target file (`extract_callee_source` returns `None`) | Stored as-is in `target_context` field of the usage entry | Resulting JSON entry has a null/missing source snippet, but the usage location record itself is still preserved |
| Import cannot be resolved to a project path (`resolve_module_to_project_path` returns `None`) | Import is simply not matched to the target file; no names are collected from it | External/unresolvable imports contribute nothing to `names_from_target`; no error raised |
| No definitions collected for a target file (`_load_target_definitions` yields empty list when file missing or no matching definition dict) | Empty list is used as-is; wildcard/whole-file import cases add nothing | Silent no-op — no usages detected for that target when definitions can't be loaded |
| Caller source file unreadable or has invalid encoding (`open(...).read()`) | Explicitly caught via `except (OSError, UnicodeDecodeError)`; `caller_source_lines` remains `None` | Usage lines/groups are still recorded, but `usage_context` snippets are omitted for that caller |
| Missing/invalid file paths, malformed AST, or other unexpected conditions (e.g., `parse_file` failures, `symbol_to_file_map` key errors) | Not handled locally; exceptions propagate to the caller (`file_analyzer.py`) | A single malformed or inaccessible file can raise an unhandled exception, potentially interrupting the analysis of that file (fail-fast at this boundary) |

**Design considerations:**
- The module distinguishes between "expected absence" (unsupported language, unresolved import, missing definition) — handled gracefully with empty/`None` fallbacks — and "unexpected failure" (I/O or parsing errors on a required file), where only the caller-source read is explicitly guarded; other I/O/parsing calls (e.g., `parse_file`) are left to propagate naturally.
- Deduplication of usage lines (`sorted(set(...))`) is applied defensively after every grouping step, ensuring that partial or duplicate detections don't produce inconsistent output even when upstream extraction is imperfect.
- Caching of `target_definition_names` across the caller loop avoids repeated parsing/definition extraction, and its `None` sentinel value doubles as an internal signal for "not yet computed" rather than an error state.

# Summary

usage_analysis.py builds callee/caller usage data for codetwine's dependency pipeline. `build_usage_info_list` finds a file's usages of imported symbols, resolves typed-alias/import-alias remapping, groups by (definition file, name), and attaches definition source (`target_context`) → feeds `callee_usages`. `build_caller_usages` finds files calling into a target, resolves per-language import semantics (explicit, wildcard, dotted, include, same-package) via helpers `_collect_names_from_target`/`_load_target_definitions`, groups usages, attaches snippets (`usage_context`) → feeds `caller_usages`. Consumed by file_analyzer.py; degrades gracefully on missing config/data.
