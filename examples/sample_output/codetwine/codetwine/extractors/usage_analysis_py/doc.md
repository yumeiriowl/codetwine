# Design Document: codetwine/extractors/usage_analysis.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`usage_analysis.py` is the usage-tracking layer of the codetwine pipeline. It exists as a separate module to encapsulate two complementary but distinct analysis directions:

1. **Outward (callee) analysis** — given a file under analysis, find where names it imports from other project files are used, and attach the corresponding definition source code from those other files.
2. **Inward (caller) analysis** — given a file under analysis, find all other project files that import and use names defined in it, and record the precise lines and surrounding context of those usages.

These responsibilities require coordinating the AST parser (`ts_parser`), import extractor (`imports`), usage extractor (`usages`), definition extractor (`definitions`), definition source retriever (`dependency_graph`), and language configuration (`settings`, `import_to_path`). Isolating this coordination into its own module keeps `file_analyzer.py` (the sole consumer) free of this orchestration complexity.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None` | `list[dict]` | Extract lines where project-imported names are used in the current file and attach each name's definition source code; merges multiple occurrences of the same name into one record. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | For each file that imports the target file, collect the lines where target-defined names are used and include surrounding source context snippets. |

Two private helpers support these public functions:

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_collect_names_from_target` | `caller_import_list`, `target_file_rel`, `caller_ext`, `caller_rel`, `project_file_set`, `project_dir`, `target_definition_names` | `tuple[list[str], list[str] \| None]` | Derive the set of names a caller imports from the target file, using language-specific import resolution strategies (named imports, Java/Kotlin leaf names, C/C++ whole-file inclusion, wildcard imports, same-package visibility). |
| `_load_target_definitions` | `target_file_rel: str`, `project_dir: str` | `list[str]` | Parse the target file and return all its definition names, used as a cache-friendly helper for wildcard and include-style import scenarios. |

---

## Design Decisions

- **Grouping by `(source_file, name)` key** (`usage_group_map` in `build_usage_info_list`, `groups` by name in `build_caller_usages`): multiple usage lines for the same name are accumulated and deduplicated via `sorted(set(...))` rather than emitting one record per occurrence, keeping output compact.
- **Typed-alias expansion**: both public functions call `extract_typed_aliases` to detect variables declared with an imported type (e.g. `genre: Genre`) and transparently remap them back to the canonical type name before grouping. This is applied uniformly without special-casing individual languages.
- **`target_definition_names` cache**: in `build_caller_usages`, the potentially expensive parse-and-extract step for the target file's definitions is performed at most once across all callers by threading the result through `_collect_names_from_target` as an optional out-parameter, avoiding redundant re-parsing.
- **Language dispatch via config dicts** (`USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`): all language-specific branching is driven by configuration rather than explicit `if lang == "java"` conditionals, with the only structural exception being the `separator`-based dispatch (`"."` for Java/Kotlin, `"/"` for C/C++) inside `_collect_names_from_target`.
- **Context window extraction**: `build_caller_usages` extracts up to `_max_context_locations = 2` usage sites per name, each surrounded by `_context_radius = 3` lines, joined by `"\n...\n"`, providing human-readable snippets without loading large file regions.

## Definition Design Specifications

# Definition Design Specifications

## `build_usage_info_list`

**Signature:**
```
build_usage_info_list(
    root_node,
    symbol_to_file_map: dict[str, str],
    project_dir: str,
    file_ext: str,
    alias_to_original: dict[str, str] | None = None,
) -> list[dict]
```

**Arguments:**
- `root_node`: AST root node of the file being analyzed.
- `symbol_to_file_map`: Mutable mapping from imported symbol names to their definition file paths (relative to project root). This dict is modified in-place when typed aliases are discovered.
- `project_dir`: Absolute path to the project root; passed through to `extract_callee_source`.
- `file_ext`: File extension without leading dot, used to look up language-specific `USAGE_NODE_TYPES`.
- `alias_to_original`: Optional mapping from alias names to the original names they refer to, used when searching for definitions in the target file.

**Returns:** A list of dicts, each containing:
- `"lines"`: Sorted, deduplicated list of 1-based line numbers where the symbol is used.
- `"name"`: The canonical (post-remap) usage name, possibly dotted.
- `"from"`: Relative path of the file where the symbol is defined.
- `"target_context"`: Source code of the definition retrieved from the target file, or `None` if not found.

**Responsibility:** Produces the `callee_usages` output by locating every use of project-internal imported symbols in one file's AST and attaching the corresponding definition source. Multiple uses of the same symbol are merged into a single record.

**Design decisions:**
- Uses `(source_file, remapped_name)` as the grouping key so that the same logical entity used at multiple lines collapses into one record. `extract_callee_source` is called only on the first occurrence per group, avoiding redundant parsing.
- Typed alias resolution (e.g., a variable `genre` declared as `Genre`) is handled before grouping: aliases are added to `symbol_to_file_map` and remapped back to their type name in the output, keeping the output name consistent with the definition lookup.
- When `alias_to_original` is provided, the search name for `extract_callee_source` is derived from the original imported name rather than the alias, ensuring correct definition retrieval.

**Edge cases:**
- `symbol_to_file_map` is mutated in-place when typed aliases are found; callers must be aware of this side effect.
- If `USAGE_NODE_TYPES` has no entry for `file_ext`, `typed_alias_parent_types` defaults to an empty set and typed alias extraction is skipped.

---

## `_collect_names_from_target`

**Signature:**
```
_collect_names_from_target(
    caller_import_list: list,
    target_file_rel: str,
    caller_ext: str,
    caller_rel: str,
    project_file_set: set[str],
    project_dir: str,
    target_definition_names: list[str] | None,
) -> tuple[list[str], list[str] | None]
```

**Arguments:**
- `caller_import_list`: Import statements extracted from the caller file.
- `target_file_rel`: Relative path of the file whose usages are being searched for.
- `caller_ext`: File extension of the caller, used to determine separator style and same-package visibility.
- `caller_rel`: Relative path of the caller file, used for import resolution.
- `project_file_set`: Full set of project-relative file paths, passed to `resolve_module_to_project_path`.
- `project_dir`: Absolute project root, passed to `_load_target_definitions` when needed.
- `target_definition_names`: Cache of already-loaded definition names from the target file; pass `None` on first call.

**Returns:** A `(names_from_target, target_definition_names)` tuple. `names_from_target` is the list of names from the target file that the caller references. `target_definition_names` is returned as a lazily populated cache to avoid re-parsing the target file across multiple callers.

**Responsibility:** Determines which names defined in the target file are visible to a given caller, using language-specific import semantics (explicit named import, Java/Kotlin trailing-part import, C/C++ full-file inclusion, wildcard import, same-package visibility).

**Design decisions:**
- The separator character (`"."` vs `"/"`) from `IMPORT_RESOLVE_CONFIG` is the primary discriminator for Java/Kotlin versus C/C++ import semantics, avoiding a language-name enum.
- `target_definition_names` is passed in and out rather than computed once externally so that the expensive `_load_target_definitions` call is deferred until actually needed and shared across all callers of the same target within one `build_caller_usages` invocation.
- The same-package fallback (for Java/Kotlin) is applied only when no names were found via explicit import matching, preventing duplication.

**Edge cases:**
- Wildcard imports (`"*"`) trigger full target definition loading for both Python-style (`from X import *`) and Java/Kotlin package-level wildcards.
- If `caller_ext` is not in `IMPORT_RESOLVE_CONFIG`, `resolve_module_to_project_path` returns `None` for all imports, and names will only be collected via the same-package fallback if `SAME_PACKAGE_VISIBLE` is set.

---

## `_load_target_definitions`

**Signature:**
```
_load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```

**Arguments:**
- `target_file_rel`: Project-relative path of the file to parse.
- `project_dir`: Absolute path to the project root.

**Returns:** A list of definition name strings found in the target file. Returns an empty list if the file does not exist, has no `DEFINITION_DICTS` entry for its extension, or yields no named definitions.

**Responsibility:** Parses a target file and enumerates all top-level definition names, used when the entire file's public surface must be treated as imported (wildcard imports, C/C++ `#include`, same-package visibility).

**Design decisions:**
- Returns only non-empty names, filtering out anonymous or unnamed definitions that `extract_definitions` may emit with a falsy `name`.
- Guards against missing files and unsupported extensions before attempting to parse, returning an empty list rather than raising.

---

## `build_caller_usages`

**Signature:**
```
build_caller_usages(
    target_file_rel: str,
    project_dep_list: list[dict],
    project_dir: str,
    project_file_set: set[str],
) -> list[dict]
```

**Arguments:**
- `target_file_rel`: Project-relative path of the file whose definitions are being tracked.
- `project_dep_list`: The project dependency list produced by `save_project_dependencies`, each entry having `"file"` and `"callers"` keys.
- `project_dir`: Absolute path to the project root.
- `project_file_set`: Full set of project-relative file paths.

**Returns:** A list of dicts, each containing:
- `"lines"`: Sorted, deduplicated 1-based line numbers of usage occurrences within the caller.
- `"name"`: The canonical usage name (alias-remapped if applicable).
- `"file"`: Relative path of the caller file.
- `"usage_context"` (present when the caller source was readable): A code snippet of up to 2 usage locations, each surrounded by ±3 lines, joined by `"\n...\n"`.

**Responsibility:** Produces the `caller_usages` output by iterating every file that imports the target, extracting which of the target's names each caller uses, and collecting usage lines together with surrounding context.

**Design decisions:**
- `target_definition_names` is initialized once before the caller loop and reused across all callers to avoid repeatedly parsing the target file.
- Typed alias tracking is applied per caller, so a variable `genre: Genre` in a caller file is correctly attributed to `Genre` from the target.
- Usage context extraction is limited to the first `_max_context_locations = 2` line numbers per group with a radius of `_context_radius = 3` lines, bounding the output size while providing meaningful context.
- Line deduplication and sorting happen before context extraction so that the context snippets correspond to the final canonical line set.
- If the caller file cannot be opened (e.g., permission error, encoding issue), usage groups are still emitted without `"usage_context"`, ensuring a partial result rather than a failure.

**Edge cases:**
- If `target_file_rel` is not found in `project_dep_list`, `caller_file_list` remains empty and the function returns `[]`.
- Callers whose extension is not supported by `get_import_params` are silently skipped via the `continue` guard.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **`codetwine/parsers/ts_parser.py`** (`parse_file`): Used to parse source files into tree-sitter AST root nodes. Called when loading caller files during `build_caller_usages` and when loading the target file to extract its definitions in `_load_target_definitions`.

- **`codetwine/extractors/imports.py`** (`extract_imports`): Used to extract import statements from a caller file's AST. The resulting `ImportInfo` list is used to determine which names a caller file imports from the target file.

- **`codetwine/extractors/usages.py`** (`extract_usages`, `extract_typed_aliases`): `extract_usages` is used to find the lines where imported names appear in a given AST. `extract_typed_aliases` is used to discover variables declared with an imported type (e.g., `Genre genre`) so that those variable names can also be tracked as usages.

- **`codetwine/extractors/definitions.py`** (`extract_definitions`): Used in `_load_target_definitions` to enumerate all definition names in a target file. This is needed for wildcard imports, C/C++ `#include` handling, and same-package visibility resolution in Java/Kotlin.

- **`codetwine/extractors/dependency_graph.py`** (`extract_callee_source`): Used to retrieve the source code of a named definition from a dependency file. The result is stored as `target_context` in the usage info records produced by `build_usage_info_list`.

- **`codetwine/import_to_path.py`** (`resolve_module_to_project_path`, `get_import_params`): `resolve_module_to_project_path` is used to match each import statement in a caller file to a project-internal file path, identifying which imports reference the target file. `get_import_params` provides the tree-sitter `Language` object and query string needed to run import extraction on caller files.

- **`codetwine/config/settings.py`** (`DEFINITION_DICTS`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`): These configuration dicts drive all language-specific behavior. `USAGE_NODE_TYPES` controls how usages are detected per language; `IMPORT_RESOLVE_CONFIG` provides the import separator to distinguish Python/JS/TS, Java/Kotlin, and C/C++ resolution strategies; `DEFINITION_DICTS` provides the node-type mapping for definition extraction; `SAME_PACKAGE_VISIBLE` determines whether same-directory files in Java/Kotlin are treated as implicitly visible.

---

### Dependents (what uses this file)

- **`codetwine/file_analyzer.py`**: Uses both public functions exported by this file. `build_usage_info_list` is called to produce the callee usage records for a file being analyzed — identifying where project-internal imported names are used and attaching their definition source code. `build_caller_usages` is called to collect the locations in other project files where names defined in the current file are referenced, producing the caller usage records. The dependency is unidirectional: `file_analyzer.py` depends on this file, but this file has no knowledge of `file_analyzer.py`.

## Data Flow

# Data Flow

## Overview

This file contains two independent pipelines that both analyse how symbols defined in one file are used in others, but from opposite perspectives.

---

## `build_usage_info_list` — Callee-side view

### Input
| Source | Type | Description |
|---|---|---|
| `root_node` | AST Node | Pre-parsed AST of the file being analysed |
| `symbol_to_file_map` | `dict[str, str]` | Imported symbol name → definition file path |
| `project_dir` | `str` | Absolute project root |
| `file_ext` | `str` | Extension of the file being analysed |
| `alias_to_original` | `dict[str, str] \| None` | Alias name → original name |

### Transformation Flow

```
symbol_to_file_map
       │
       ▼
extract_typed_aliases()          ← adds var-name → type-name pairs
       │  (augments symbol_to_file_map in-place)
       ▼
extract_usages()                 → list[UsageInfo(name, line)]
       │
       ▼
 Group by (source_file, remapped_name)
       │  ─ remap typed aliases back to original type names
       │  ─ resolve alias_to_original for definition lookup
       ▼
extract_callee_source()          → source code string of definition
       │
       ▼
usage_group_map: (source_file, name) → entry dict
       │
       ▼
Deduplicate & sort lines in each entry
```

### Output
`list[dict]` — each dict is one **usage group**:

| Field | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where the name is used |
| `name` | `str` | Symbol name as it appears in the caller (possibly dotted) |
| `from` | `str` | Relative path of the file where the symbol is defined |
| `target_context` | `str \| None` | Source code of the definition in that file |

Consumed by `build_usage_info_list` caller in `codetwine/file_analyzer.py`.

---

## `build_caller_usages` — Caller-side view

### Input
| Source | Type | Description |
|---|---|---|
| `target_file_rel` | `str` | The file whose definitions are being tracked |
| `project_dep_list` | `list[dict]` | Project-wide dependency graph (`file`, `callers`, `callees`) |
| `project_dir` | `str` | Absolute project root |
| `project_file_set` | `set[str]` | All project file paths |

### Transformation Flow

```
project_dep_list
       │
       ▼
Locate callers of target_file_rel   → caller_file_list: list[str]
       │
       ▼  (for each caller file)
parse_file()  +  extract_imports()  → caller_import_list: list[ImportInfo]
       │
       ▼
_collect_names_from_target()        → names_from_target: list[str]
       │  (language-specific: direct names / trailing leaf / all definitions)
       ▼
extract_typed_aliases()             ← adds var-name aliases to names_from_target
       │
       ▼
extract_usages()                    → list[UsageInfo(name, line)]
       │
       ▼
Group by name, remap typed aliases  → groups: dict[str, entry dict]
       │
       ▼
Extract usage_context snippets      (±3 lines around each usage, up to 2 locations)
       │
       ▼
caller_usages.extend(groups.values())
```

### Output
`list[dict]` — each dict is one **caller usage group**:

| Field | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers of usages in the caller |
| `name` | `str` | Symbol name (possibly remapped from typed alias) |
| `file` | `str` | Relative path of the caller file |
| `usage_context` | `str` | Source snippet(s) around usage sites, joined by `\n...\n` |

Consumed by `build_caller_usages` caller in `codetwine/file_analyzer.py`.

---

## Key Internal Data Structures

### `usage_group_map` (in `build_usage_info_list`)
```
dict[
  (source_file: str, remapped_name: str),   # group key
  {lines, name, from, target_context}       # accumulated entry
]
```
Groups all occurrences of the same symbol in the same source file into one record; prevents duplicate definition lookups.

### `groups` (in `build_caller_usages`)
```
dict[
  name: str,                                # symbol name as key
  {lines, name, file}  → +usage_context    # entry extended after dedup
]
```
Per-caller grouping of usages; `usage_context` is appended after line deduplication.

### `target_definition_names` cache
A `list[str] | None` shared across all callers inside `build_caller_usages` and passed into `_collect_names_from_target`. Populated lazily on the first call to `_load_target_definitions` and reused for subsequent callers to avoid re-parsing the target file.

---

## `_collect_names_from_target` — Name resolution sub-flow

```
caller_import_list
       │
       ▼  resolve each import module → project path
       │
       ├─ match == target_file_rel?
       │      ├─ import has names → use them directly (or wildcard → load all definitions)
       │      ├─ separator "." (Java/Kotlin) → use trailing leaf of module path
       │      └─ separator "/" (C/C++) → load all definitions from target
       │
       ├─ wildcard + separator "." → Java/Kotlin package wildcard → load all definitions
       │
       └─ SAME_PACKAGE_VISIBLE + same directory → load all definitions
               │
               ▼
       names_from_target: list[str]
```

## Error Handling

# Error Handling

## Overall Strategy

`usage_analysis.py` adopts a **mixed strategy**: strict fail-fast for the core analysis path combined with localized graceful degradation for optional enrichment steps (such as reading caller source for context snippets). The dominant pattern is implicit error propagation — most failure paths raise exceptions naturally rather than catching and recovering, ensuring that upstream callers receive clear signals when fundamental preconditions are violated.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| File I/O failure when reading caller source lines (`OSError`, `UnicodeDecodeError`) | Caught and silenced; `caller_source_lines` remains `None` | `usage_context` fields are omitted from affected caller usage groups; all other data is preserved |
| Missing key in `symbol_to_file_map` during usage iteration | No guard; raises `KeyError` implicitly | Propagates to caller (fail-fast) |
| `parse_file` failure for caller or target files | Not caught; exceptions propagate | Entire analysis for that file aborts |
| `extract_callee_source` returning `None` | Stored as `None` in `target_context`; no exception | Definition source code is absent in the output entry; structural data is unaffected |
| `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, or `DEFINITION_DICTS` returning `None`/missing key | Handled by conditional checks (`if usage_node_types`, `if target_def_dict`) before use | Analysis for that language is skipped or produces empty results gracefully |
| `_load_target_definitions` on a non-existent or unreadable file | Guarded by `os.path.isfile` before parsing | Returns an empty list; caller receives no definition names for that file |
| `get_import_params` returning `(None, None)` for unsupported extension | Checked with `if not language: continue` | That caller file is silently skipped in `build_caller_usages` |

---

## Design Considerations

The only explicit `try/except` block is intentionally narrow, covering solely the optional step of loading caller source lines for context extraction. This isolation ensures that the inability to produce enrichment data (context snippets) cannot abort the primary task of collecting usage line numbers, which is treated as non-negotiable.

All other error conditions — including missing symbols, parse failures, and resolution mismatches — are left to propagate as unhandled exceptions. This reflects a deliberate fail-fast posture for inputs that represent a broken or inconsistent project model: if the dependency graph or file map is malformed, silent continuation would produce silently incorrect output rather than a detectable failure.

The `None`-return contracts of dependencies such as `extract_callee_source` and `_load_target_definitions` are accommodated without exception handling, instead relying on `None`-tolerant assignment and `if` guards, keeping the normal path uncluttered while safely handling absent optional data.

## Summary

`usage_analysis.py` coordinates usage tracking from two perspectives: callee-side (where imported names are used in the current file, with definition source attached) and caller-side (which other files use names defined in the current file, with context snippets). Public functions: `build_usage_info_list` returns grouped usage records with `lines`, `name`, `from`, and `target_context`; `build_caller_usages` returns records with `lines`, `name`, `file`, and `usage_context`. Both apply typed-alias resolution. Internal helpers handle language-specific import resolution and lazy-cached target definition loading.
