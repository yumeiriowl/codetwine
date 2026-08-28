# Design Document: codetwine/extractors/usage_analysis.py

# Overview & Purpose

## Role and Responsibilities

`usage_analysis.py` is the component of Codetwine responsible for computing **cross-file symbol usage relationships** in two complementary directions:

1. **Callee-side usage** (`build_usage_info_list`): given a file's AST and a mapping of imported symbol names to the files that define them, it finds every location in the current file where those imported symbols are actually used, and attaches the corresponding definition source code. This produces the data behind the `callee_usages` JSON output.
2. **Caller-side usage** (`build_caller_usages`): given a target file and the list of other project files known to depend on it, it works backward through each caller's import statements to figure out which names originate from the target file, then locates the lines in the caller where those names are used, along with surrounding source context. This produces the data behind the `caller_usages` JSON output.

This logic is isolated into its own module (rather than living in `file_analyzer.py`, which invokes it) because it combines several distinct concerns — import resolution, AST usage extraction, typed-alias tracking, and definition-source retrieval — into a higher-level, language-agnostic analysis step. Keeping it separate allows `file_analyzer.py` to orchestrate per-file analysis while delegating the more intricate cross-referencing algorithm (grouping/deduplication, alias remapping, per-language import semantics) to a dedicated module. It relies on `config.settings` dictionaries to stay language-agnostic, supporting Python/JS/TS-style `from X import a, b`, Java/Kotlin `import com.foo.Bar` (including wildcard and same-package visibility), and C/C++ `#include` semantics uniformly through a shared code path.

## Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None = None` | `list[dict]` | Finds usages of imported symbols in a file's AST, resolves typed aliases, merges duplicate usages by line, and attaches the definition source code for each unique symbol/definition-file pair. |
| `build_caller_usages` | `target_file_rel: str`, `caller_file_list: list[str]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | For each caller file, determines which names it imports from the target file, extracts and groups usage lines of those names, and builds a snippet-based `usage_context` for each group. |
| `_collect_names_from_target` (internal) | `caller_import_list: list`, `target_file_rel: str`, `caller_ext: str`, `caller_rel: str`, `project_file_set: set[str]`, `project_dir: str`, `target_definition_names: list[str] \| None` | `tuple[list[str], list[str] \| None]` | Derives the list of target-originating names visible in a caller, handling per-language import styles (`from X import a,b`; `import com.foo.Bar`; wildcard imports; `#include`; same-package visibility), with caching of target definition names. |
| `_load_target_definitions` (internal) | `target_file_rel: str`, `project_dir: str` | `list[str]` | Parses the target file and extracts all definition names from it, used when a caller pulls in the whole file (wildcard import, `#include`, or same-package access). |

## Design Decisions

- **Grouping/deduplication by key**: Both public functions aggregate raw per-occurrence usage events into merged entries keyed by `(source_file, name)` or `name`, accumulating and de-duplicating line numbers (`sorted(set(...))`), so downstream JSON output is compact rather than one entry per occurrence.
- **Alias resolution as a preprocessing step**: Both functions detect *typed variable aliases* (e.g. `genre: Genre`) via `extract_typed_aliases` and remap alias usages back to their original imported/defined type name before grouping, ensuring usages of a locally-typed variable are correctly attributed to the imported type.
- **Import-alias vs. typed-alias separation**: `build_usage_info_list` additionally resolves *import aliases* (`alias_to_original`) independently from typed variable aliases, reconstructing the original definition name only when performing the (expensive) source lookup via `extract_callee_source`, avoiding redundant work for repeated occurrences.
- **Caching of target definitions across the caller loop**: `build_caller_usages` computes `target_definition_names` lazily and passes it through `_collect_names_from_target` as an in/out parameter so that whole-file inclusion scenarios (wildcard imports, `#include`, same-package visibility) only parse the target file once, regardless of how many callers reference it.
- **Language-agnostic dispatch via config-driven strategy tables**: `_collect_names_from_target` branches on `IMPORT_RESOLVE_CONFIG`'s `separator` value (`.` vs `/`) rather than hardcoding language names, delegating language differences to configuration rather than conditionals scattered through the analysis logic — consistent with the rest of the codebase's config-driven language abstraction (`USAGE_NODE_TYPES`, `DEFINITION_DICTS`, etc.).
- **Bounded context extraction**: `build_caller_usages` limits `usage_context` snippets to the first `_max_context_locations` (2) usage lines, each with a fixed `_context_radius` (3) lines of surrounding source, joined by an `"..."` separator — a deliberate cap to keep output size manageable rather than including every occurrence's context.

# Definition Design Specifications

## `build_usage_info_list`

Extracts and aggregates usage locations of names imported into a file from other project files, and attaches the source code of the definition being used, producing the `callee_usages` output data.

Arguments:
- `root_node`: AST root node of the file being analyzed.
- `symbol_to_file_map`: dict mapping imported symbol names to the file path where each is defined; this dict is mutated in place to add discovered typed-alias variable names.
- `project_dir`: absolute path to the project root, used to locate/parse definition files.
- `file_ext`: file extension (no dot), used to look up language-specific usage node type config.
- `alias_to_original`: optional dict mapping locally-aliased import names back to their original names, used to find the correct definition when the imported name was renamed on import.

Returns a list of dicts, each with `lines` (sorted unique line numbers), `name` (remapped/original symbol name, possibly with attribute suffix), `from` (definition file path), and `target_context` (source code of the definition, or `None` if not found).

Design intent: consolidates two independent remapping concerns — typed-variable aliasing (e.g., a variable declared with an imported type) and import aliasing (e.g., `import X as Y`) — before grouping usages, so that the final output always keys usages by their true originating name and file rather than by local/aliased names. Grouping is done via a `(source_file, remapped_name)` key so that multiple usages of the same symbol across different lines merge into a single entry with only one lookup of the definition source (`extract_callee_source` is called only on first occurrence per group, since it is more expensive than pure AST traversal).

Edge cases: if a variable is a typed alias, its root symbol is rewritten to the original type name for lookup purposes but for grouping purposes, both typed-alias remapping and import-alias remapping are applied so the correct file and search name are used. Assumes every key in `symbol_to_file_map` (including newly added typed aliases) has a valid file path.

## `_collect_names_from_target`

Determines which symbol names, as referenced by a given caller file, actually originate from a specific target file, based on the caller's import statements and language-specific import semantics.

Arguments:
- `caller_import_list`: list of `ImportInfo` extracted from the caller file.
- `target_file_rel`: relative path of the target (definition) file.
- `caller_ext`: caller's file extension, used to select import resolution rules.
- `caller_rel`: caller's relative path, needed to resolve relative/module imports.
- `project_file_set`: set of all project file paths, used for import resolution.
- `project_dir`: absolute path to project root, passed through for definition loading.
- `target_definition_names`: cache of the target file's definition names (`None` if not yet computed), used to avoid re-parsing the target file multiple times across languages/branches that need "all names" (wildcard imports, C/C++ includes, Java/Kotlin wildcard imports, same-package visibility).

Returns a tuple `(names_from_target, target_definition_names)`: the list of names attributable to the target file for this caller, and the (possibly newly populated) cache to be reused by later calls.

Design intent: encapsulates per-language differences in how an import statement expresses "this name comes from that file" (explicit `from X import a,b`, Java/Kotlin dotted imports where only the last segment is a name, C/C++ whole-file includes, wildcard imports, and implicit same-package visibility) into one unified output shape usable by the rest of the pipeline. The `target_definition_names` cache pattern lets a single parse of the target file be shared across multiple callers/branches within a run.

Edge cases: falls back to same-package visibility only when no import-based names were found and the config flag is enabled; Java/Kotlin wildcard resolution requires the target file path to be nested inside the imported package directory; empty leaf names from module splitting are skipped.

## `_load_target_definitions`

Parses a target file and extracts all definition names within it, for use when an entire file's exported names must be considered (wildcard/whole-file includes, same-package visibility).

Arguments:
- `target_file_rel`: relative path of the target file from the project root.
- `project_dir`: absolute project root path.

Returns a list of definition name strings (empty if the extension has no configured definition dict or the file does not exist).

Design intent: centralizes the "parse + extract all names" operation so callers needing "all symbols visible from this file" don't duplicate parsing/extraction logic. Skips silently (returns empty list) rather than raising when the target extension is unsupported or the file is missing, to keep the broader analysis pipeline resilient to partial/incomplete project data.

## `build_caller_usages`

Collects, across a set of caller files, all the line locations where names defined in a given target file are used, producing the `caller_usages` output data.

Arguments:
- `target_file_rel`: relative path of the file whose definitions are being tracked.
- `caller_file_list`: relative paths of files known to depend on the target file.
- `project_dir`: absolute path to the project root.
- `project_file_set`: set of all project file paths, needed for import resolution.

Returns a list of dicts, one per (name, caller file) group, each containing `lines` (sorted unique line numbers), `name`, `file` (caller's relative path), and `usage_context` (source snippet(s) around the usage lines).

Design intent: for each caller, first narrows down to the specific names that are actually imported/visible from the target file (via `_collect_names_from_target`), then only scans for usages of those specific names, avoiding false positives from identically-named symbols defined elsewhere. Typed-alias variables are also tracked so that usages through an aliased variable are attributed back to the original imported type. The `target_definition_names` cache is computed once and threaded through all callers within the loop to avoid redundant re-parsing of the target file for languages/situations that require the full name list (C/C++, wildcard imports, same-package visibility).

Important design decisions: usage context extraction is capped at a fixed number of locations (`_max_context_locations = 2`) and each snippet spans a fixed radius (`_context_radius = 3`) around the usage line, to keep output size bounded regardless of how many times a name is used; multiple snippets for the same group are joined with a separator ("...") to indicate discontinuity. Caller source is only read from disk when there is at least one usage to avoid unnecessary I/O; read failures (`OSError`/`UnicodeDecodeError`) degrade gracefully by leaving `usage_context` unset for that caller rather than raising.

Edge cases and constraints: callers whose extension has no configured import parameters (`get_import_params` returns `(None, None)`) are skipped entirely; if no names are attributable to the target file for a caller, no usage extraction/context work is performed for that caller.

# Dependency Description

### Dependencies (what this file uses)

`usage_analysis.py` composes several extraction and configuration modules to build two kinds of usage reports (`build_usage_info_list` and `build_caller_usages`):

- **`codetwine/parsers/ts_parser.py` (`parse_file`)**: Used to parse caller/target source files into a tree-sitter AST when their source needs to be (re-)analyzed, e.g. when loading target definitions or parsing each caller file in `build_caller_usages`.
- **`codetwine/extractors/imports.py` (`extract_imports`)**: Used to extract the import statements of a caller file so that names imported from a specific target file can be identified.
- **`codetwine/extractors/usages.py` (`extract_usages`, `extract_typed_aliases`)**: Core usage-detection logic — `extract_usages` locates AST nodes where tracked symbol names are referenced (calls, attribute access, identifiers, type references), while `extract_typed_aliases` detects locally-declared variables whose type matches a tracked/imported name, so that variables holding an imported type can also be tracked as usages.
- **`codetwine/extractors/definitions.py` (`extract_definitions`)**: Used by `_load_target_definitions` to enumerate all definition names in a target file, needed for wildcard imports (`from X import *`), C/C++ `#include`, and same-package visibility cases where the exact imported names aren't explicitly listed.
- **`codetwine/extractors/dependency_graph.py` (`extract_callee_source`)**: Used in `build_usage_info_list` to fetch the actual source code of the definition being used, so the usage report can attach the referenced symbol's implementation.
- **`codetwine/import_to_path.py` (`resolve_module_to_project_path`, `get_import_params`)**: Used to resolve a caller's import module string to a project-relative file path (to check whether it matches the target file) and to obtain the tree-sitter language/query needed to run `extract_imports`.
- **`codetwine/config/settings.py` (`DEFINITION_DICTS`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`)**: Supplies per-language configuration driving all the above extraction steps — which AST node types represent definitions, which represent usages, how import module strings are resolved into paths per language, and whether same-package/directory visibility rules apply (e.g., Java/Kotlin).

### Dependents (what uses this file)

- **`codetwine/file_analyzer.py`**: Calls `build_usage_info_list` to obtain, for a given file, the usage locations of names imported from within the project along with their definition source code (feeding the `callee_usages` output). It also calls `build_caller_usages` to obtain, for a given file, the locations in other project files where its own definitions are used (feeding the `caller_usages` output).

The dependency direction is unidirectional: `file_analyzer.py` depends on `usage_analysis.py` to perform usage analysis, while `usage_analysis.py` has no dependency back on `file_analyzer.py`.

# Data Flow

## Overview

This file implements two independent analysis pipelines that both consume tree-sitter ASTs and import/definition metadata to produce usage-report JSON structures: `build_usage_info_list` (callee-side: "what does this file use from elsewhere") and `build_caller_usages`/`_collect_names_from_target` (caller-side: "who else uses what this file defines").

## 1. `build_usage_info_list`

**Input**
- `root_node`: AST of the file being analyzed.
- `symbol_to_file_map`: `{imported_name: definition_file_path}` — built upstream by `import_to_path.py`.
- `project_dir`, `file_ext`, optional `alias_to_original`: `{alias_name: original_name}`.

**Transformation flow**
1. Look up `USAGE_NODE_TYPES` for the file's language to get AST node categories relevant to usage detection.
2. Call `extract_typed_aliases` to detect variables typed as one of the imported names (e.g. `genre: Genre`), producing `typed_aliases: {var_name: type_name}`. Extend `symbol_to_file_map` so alias variables resolve to the same source file as their type.
3. Call `extract_usages` with the full symbol name set to get raw `UsageInfo(name, line)` occurrences.
4. For each usage:
   - Split `usage.name` on `.` to get the root symbol (handles attribute access like `helper.process`).
   - If the root symbol is a typed alias, remap it back to the original type name (`genre.save` → `Genre.save`).
   - Determine `source_file` via `symbol_to_file_map[root_symbol]`.
   - Group by `(source_file, remapped_name)` key, accumulating line numbers.
   - On first occurrence of a group, resolve the actual lookup name via `alias_to_original` (if the symbol was imported under an alias) and call `extract_callee_source` to fetch the definition's source code.
5. Deduplicate/sort each group's `lines`.

**Output**
- `list[dict]`, each shaped as:
```
{
  "lines": [int, ...],          # sorted, deduplicated line numbers
  "name": str,                  # resolved/original symbol name
  "from": str,                  # definition file path
  "target_context": str | None  # source code of the definition
}
```
- Consumed by `file_analyzer.py` for the "callee_usages" JSON output.

## 2. `build_caller_usages` (with helpers `_collect_names_from_target`, `_load_target_definitions`)

**Input**
- `target_file_rel`: the file whose definitions are being tracked as used elsewhere.
- `caller_file_list`: candidate files that may reference `target_file_rel`.
- `project_dir`, `project_file_set`.

**Transformation flow (per caller file)**
1. Parse caller file (`parse_file`) → AST.
2. Extract caller's imports (`extract_imports`) → `list[ImportInfo]`.
3. `_collect_names_from_target` determines which names from `target_file_rel` are visible to this caller, based on language-specific import resolution:
   - Python/JS/TS: explicit `from X import a, b` → names list directly; `import *` → falls back to `_load_target_definitions` (parses target file, collects all `DefinitionInfo.name`).
   - Java/Kotlin: `import com.foo.Bar` → trailing segment `Bar`; wildcard package import or same-directory visibility (`SAME_PACKAGE_VISIBLE`) → all target definitions.
   - C/C++ (`separator == "/"`): `#include` → all target definitions (whole file is imported).
   - `target_definition_names` is cached across the caller loop (an optimization since target file parsing is expensive and identical across callers).
4. If any `names_from_target` were found:
   - Detect typed aliases in the caller (`extract_typed_aliases`) and merge alias variable names into the tracked name set.
   - Extract usages (`extract_usages`) in the caller AST.
   - Read caller source lines (for later context snippet extraction).
   - Group usages by (possibly alias-remapped) `name` into `groups: {name: {lines, name, file}}`.
   - Deduplicate/sort each group's `lines`.
   - For up to 2 usage locations per group, build a surrounding-code snippet (± context radius) from `caller_source_lines`, joined with a separator, stored as `usage_context`.
5. Extend the overall `caller_usages` list with this caller's groups.

**Output**
- `list[dict]`, each shaped as:
```
{
  "lines": [int, ...],       # sorted, deduplicated line numbers within the caller file
  "name": str,               # symbol name (remapped through typed aliases if applicable)
  "file": str,               # caller's relative file path
  "usage_context": str       # concatenated code snippets around usage lines
}
```
- Consumed by `file_analyzer.py` for the "caller_usages" JSON output.

## Key Intermediate Data Structures

| Structure | Shape | Purpose |
|---|---|---|
| `symbol_to_file_map` | `{name: file_path}` | Maps imported/definable names to their defining file; extended with alias variable names |
| `typed_aliases` | `{var_name: type_name}` | Tracks variables declared with an imported type, so usages of the variable are attributed to the type's definition |
| `usage_group_map` / `groups` | `{key: {lines, name, ..., context/source}}` | Aggregates multiple line-level usages of the same symbol into one merged record |
| `names_from_target` | `list[str]` | Names visible to a caller that originate from the target file, driving which usages to search for |
| `target_definition_names` (cache) | `list[str] \| None` | Cached full definition-name list of the target file, reused across callers to avoid re-parsing |
| `UsageInfo` (from usages.py) | `(name, line)` | Raw usage occurrence before grouping |
| `ImportInfo` (from imports.py) | `(module, names, line, module_alias, alias_map)` | Raw import statement data used to resolve which project file a caller's import points to |
| `DefinitionInfo` (from definitions.py) | `(name, type, start_line, end_line)` | Raw definition record; only `.name` is consumed here when building `target_definition_names` |

## Data Flow Diagram

```
[build_usage_info_list]
root_node + symbol_to_file_map
   → extract_typed_aliases → typed_aliases → extend symbol_to_file_map
   → extract_usages → raw UsageInfo list
   → group by (source_file, name), remap aliases
   → extract_callee_source (first occurrence per group) → source_code
   → list[dict: lines/name/from/target_context]  →  file_analyzer.py (callee_usages)

[build_caller_usages]
target_file_rel + caller_file_list
   for each caller:
     parse_file → caller AST
     extract_imports → ImportInfo list
     _collect_names_from_target (uses resolve_module_to_project_path,
        _load_target_definitions[parse_file + extract_definitions]) → names_from_target
     extract_typed_aliases + extract_usages → usage_list
     group by name → groups
     build usage_context from caller source lines
   → list[dict: lines/name/file/usage_context] → file_analyzer.py (caller_usages)
```

# Error Handling

**Overall strategy:** This file follows a graceful degradation approach throughout. It does not raise or catch domain-specific exceptions; instead it relies on defensive lookups (`dict.get`, `None` checks, membership tests) to skip unresolvable or unsupported cases and continue processing the remaining data. The only explicit exception handling is a narrow try/except around file I/O when reading caller source lines for context extraction, reflecting the fact that most failure modes here are "missing/unsupported configuration" rather than runtime exceptions. Errors from deeper layers (parsing, AST traversal) are not caught here and are allowed to propagate to callers (e.g. `file_analyzer.py`), consistent with the fail-fast behavior documented for `ts_parser.py`.

| Error Pattern | Handling | Impact |
|---|---|---|
| Missing/unsupported language configuration (`USAGE_NODE_TYPES.get`, `IMPORT_RESOLVE_CONFIG.get` returning `None`/empty) | Falls back to empty sets/dicts (e.g. `typed_alias_parent_types = set()`) so downstream extraction functions receive safe defaults or return empty results | That file/extension is silently skipped for usage/alias tracking; no crash, but no usage data produced |
| Unsupported caller language for import extraction (`get_import_params` returns `(None, None)`) | Caller file is skipped via `continue` in the loop | That specific caller file contributes no entries to `caller_usages`; other callers are still processed |
| Unresolvable module import (`resolve_module_to_project_path` returns `None`) | Import is treated as external/non-project and excluded from `names_from_target` collection | No usage tracking for that import; does not stop processing of other imports/files |
| Missing definition source for a usage (`extract_callee_source` returns `None`) | `target_context` is stored as `None` in the resulting entry | Entry is still included in `usage_info_list` output but without source code context |
| Target file for definitions not found or unparsable extension (`_load_target_definitions`: `os.path.isfile` check, `DEFINITION_DICTS.get`) | Returns an empty `names` list instead of attempting to parse | Downstream logic treats it as if the target file has no definitions; no exception raised |
| File read failure when loading caller source for context snippets (`OSError`, `UnicodeDecodeError`) | Caught explicitly; `caller_source_lines` remains `None` | `usage_context` is omitted for that caller's usage groups, but line-number based grouping still succeeds |
| Missing alias/original name mapping (`alias_to_original` lookups) | Only applied if key present; otherwise original resolved name is used unchanged | No behavioral disruption; alias resolution is simply skipped when not applicable |
| Parsing failures at the AST/parser level (`parse_file`, tree-sitter errors) | Not caught in this file | Propagates upward, causing the overall analysis for that file/project to fail (fail-fast at a lower layer) |

**Design considerations:**
- The module favors defensive, data-driven checks (config lookups, set/dict membership) over try/except blocks, since most "errors" here are expected variations in project structure (e.g., a symbol not being project-internal, a language lacking usage-tracking config) rather than exceptional conditions.
- Deduplication and merging logic (e.g., `sorted(set(...))` on line numbers) is used to normalize potentially inconsistent or duplicate extraction results rather than treating them as errors.
- Caching (`target_definition_names`) is used to avoid repeated expensive parsing/definition extraction, and is only computed lazily on first need, which also limits the blast radius of a parsing issue in `_load_target_definitions` to the callers that actually require it.
- The narrow, explicit try/except for file reads is deliberately scoped only to the non-critical enrichment step (context snippet generation), ensuring that a failure there does not affect the correctness of the core usage/line aggregation results.

# Summary

`usage_analysis.py` computes cross-file symbol usage in two directions: `build_usage_info_list` finds where a file uses symbols imported from elsewhere, attaching definition source; `build_caller_usages` (with helpers `_collect_names_from_target`, `_load_target_definitions`) finds where other files use a target file's definitions, with context snippets. Both group/deduplicate usages by symbol/file keys and resolve typed-alias/import-alias remapping. Uses config-driven, language-agnostic dispatch (settings.py) for Python/JS/TS, Java/Kotlin, C/C++ import semantics. Degrades gracefully on missing config/data; used by `file_analyzer.py`.
