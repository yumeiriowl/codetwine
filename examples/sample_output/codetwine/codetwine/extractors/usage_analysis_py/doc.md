# Design Document: codetwine/extractors/usage_analysis.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyze AST nodes of a source file to extract two complementary usage datasets: the locations within a file where project-internal symbols are used (with their definition source attached), and the locations in other project files where symbols defined in a given file are referenced.

## 2. When to Use This Module

- **Call `build_usage_info_list`** when you have parsed a source file and need a list of records describing where each project-internal imported symbol is used within that file, merged by symbol name, with the callee's definition source code attached. Used by `codetwine/file_analyzer.py` to produce `callee_usages` output.

- **Call `build_caller_usages`** when you need to find all other project files that import and use symbols defined in a given target file, and want per-symbol usage line numbers and surrounding context snippets from each caller. Used by `codetwine/file_analyzer.py` to produce `caller_usages` output.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None` | `list[dict]` | Extracts usage locations of project-internal imported names from the file's AST, merges entries with duplicate names into a single record with accumulated line numbers, and attaches the callee's definition source code to each record. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | Iterates over all caller files that import the target file, resolves which names they import from it, extracts usage lines for those names, and returns grouped records with usage line numbers and surrounding source context. |

## 4. Design Decisions

- **Typed alias expansion in both directions**: Both functions call `extract_typed_aliases` to detect variables declared with an imported type (e.g., `genre: Genre`). These alias variable names are added to the tracked symbol set so that usages through the alias are captured, and the alias names are remapped back to the original type name before grouping, ensuring results are keyed by the canonical imported name rather than the local variable name.

- **Deferred and cached target definition loading**: `build_caller_usages` lazily loads the target file's definition names (via `_load_target_definitions`) only when needed—for wildcard imports, C/C++ `#include`, Java/Kotlin same-package visibility, and Java/Kotlin wildcard package imports—and passes the result as a cache across caller iterations to avoid redundant parses of the same target file.

- **Language-driven name collection strategy**: `_collect_names_from_target` adapts its name collection strategy based on the import separator character from `IMPORT_RESOLVE_CONFIG`: `.`-separated languages (Python, Java, Kotlin) extract individual named imports or the trailing component of a qualified import, while `/`-separated languages (C/C++) treat `#include` as incorporating all definitions from the included file.

- **Usage grouping by `(source_file, name)` key in `build_usage_info_list`**: Multiple occurrences of the same symbol name across different lines are merged into a single entry with a sorted, deduplicated `lines` list, preventing redundant definition source lookups for the same symbol.

## Definition Design Specifications

# Definition Design Specifications

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

- `symbol_to_file_map`: maps imported symbol names to the project-relative file paths where they are defined. **Mutated in place** when typed aliases are discovered.
- `alias_to_original`: maps alias names (as used in the caller) to their original names in the definition file; `None` if no aliases exist.
- Return: a list of dicts, each with keys `lines` (sorted list of 1-based line numbers), `name` (usage name), `from` (source definition file path), `target_context` (definition source code or `None`).

**Responsibility:**  
Finds every location in a single file where project-internal imported symbols are referenced and attaches the corresponding definition source code, producing the data structure for the `callee_usages` JSON output.

**When to use:**  
Called by `file_analyzer.py` after building `symbol_to_file_map` for a specific source file, to enumerate all usages of project-internal imports in that file.

**Design decisions:**
- Typed variable aliases (e.g., `genre: Genre`) are resolved via `extract_typed_aliases` and injected into `symbol_to_file_map` so that references through alias variables are tracked alongside direct references.
- Usages of the same `(source_file, remapped_name)` pair are merged into a single record with accumulated line numbers, avoiding duplicate entries.
- When `alias_to_original` is present, the `search_name` passed to `extract_callee_source` is rewritten to the original name, ensuring definition lookup succeeds even when the import was aliased.
- Attribute access names (e.g., `helper.process`) are split at `.` to identify the root symbol for file mapping, then kept whole as the usage name.

**Constraints & edge cases:**
- `symbol_to_file_map` is mutated when typed aliases are added; callers must tolerate this side effect.
- Duplicate line numbers within a group are removed and the list is sorted before returning.
- `target_context` will be `None` if `extract_callee_source` cannot locate the definition.

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

- `caller_import_list`: list of `ImportInfo` objects extracted from the caller file.
- `target_definition_names`: cached list of all definition names from the target file, or `None` on first call. Acts as an in/out cache to avoid redundant parsing.
- Return: `(names_from_target, target_definition_names)` — the names the caller imports from the target, and the (possibly newly populated) definition name cache.

**Responsibility:**  
Determines which names from the target file are visible to a specific caller file, accounting for language-specific import semantics (named imports, Java/Kotlin trailing-class imports, C/C++ whole-file inclusion, wildcard imports, and same-package visibility).

**When to use:**  
Called once per caller file inside `build_caller_usages`, to establish the set of names to search for in the caller's AST.

**Design decisions:**

| Condition | Behavior |
|---|---|
| `import_info.names` present (Python/JS/TS style) | Individual names added directly; `*` triggers full target definition loading |
| `caller_separator == "."` and no `names` (Java/Kotlin) | Trailing component of dotted module path is used as the name |
| `caller_separator == "/"` (C/C++) | All definition names from the target file are added |
| Wildcard Java/Kotlin import with unresolved module | Target names added if target resides within the package directory |
| `SAME_PACKAGE_VISIBLE` true and same directory | All target definition names added even with no matching import |

- `target_definition_names` is passed in and returned to allow the caller loop in `build_caller_usages` to cache the result across iterations over multiple callers of the same target.

**Constraints & edge cases:**
- Returns an empty `names_from_target` list if no import matches the target and same-package visibility does not apply.
- `target_definition_names` is only populated lazily via `_load_target_definitions` when actually needed.

---

## `_load_target_definitions`

**Signature:**
```python
def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```

- Return: list of definition name strings found in the target file; empty list if the file cannot be parsed or has no supported definition dict.

**Responsibility:**  
Parses the target file on demand and extracts all named definition identifiers from its AST, serving as the data source for wildcard imports, C/C++ includes, and same-package visibility resolution.

**When to use:**  
Called from `_collect_names_from_target` whenever the full set of names defined in the target file is required and has not yet been cached.

**Design decisions:**
- Only files with a known extension in `DEFINITION_DICTS` and that physically exist on disk are parsed; all other cases return an empty list silently.
- Relies on `parse_file`'s module-level cache, so repeated calls for the same file do not re-read disk.

**Constraints & edge cases:**
- Returns an empty list (not `None`) when the target file is absent or its extension is unsupported.
- Definition names with an empty/falsy `name` field are excluded.

---

## `build_caller_usages`

**Signature:**
```python
def build_caller_usages(
    target_file_rel: str,
    project_dep_list: list[dict],
    project_dir: str,
    project_file_set: set[str],
) -> list[dict]
```

- `project_dep_list`: list of dependency dicts produced by `save_project_dependencies`, each with keys `"file"`, `"callers"`, `"callees"`.
- Return: a list of dicts, each with keys `lines` (sorted, deduplicated 1-based line numbers), `name` (symbol name), `file` (caller's relative path), and `usage_context` (source snippet around each usage location, if the caller file is readable).

**Responsibility:**  
Collects all locations across the project where names defined in the target file are referenced by other files, producing the data structure for the `caller_usages` JSON output.

**When to use:**  
Called by `file_analyzer.py` for the target file being analyzed, to enumerate all inbound usages from other project files.

**Design decisions:**
- `target_definition_names` is initialized once before the caller loop and passed into `_collect_names_from_target` on each iteration, so the target file is parsed at most once regardless of how many callers exist.
- Typed variable aliases in each caller file are detected and their alias names appended to `names_from_target` before usage extraction, so references through typed variables are captured.
- Alias variable names are remapped to original type names before grouping, ensuring consistent `name` keys in the output.
- `usage_context` extracts up to `_max_context_locations = 2` usage sites, each surrounded by `_context_radius = 3` lines, joined by `"\n...\n"`.
- Callers whose file extension is not supported by `get_import_params` are skipped via `continue`.

**Constraints & edge cases:**
- If the target file has no entry in `project_dep_list`, `caller_file_list` remains empty and the function returns `[]`.
- `usage_context` is omitted from a group's dict if the caller file cannot be read (`OSError` or `UnicodeDecodeError`), since `caller_source_lines` stays `None` and the context-extraction block is skipped.
- Duplicate line numbers within a group are removed and sorted before context extraction and before returning.
- The output list may contain entries from multiple caller files interleaved, as `caller_usages.extend(groups.values())` appends without per-file separation.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to parse source files into tree-sitter AST root nodes for both caller files and target definition files.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : uses `extract_imports` to retrieve the list of import statements from a caller file's AST, enabling identification of which names originate from the target file.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : uses `extract_usages` to locate all usage positions of tracked symbol names within an AST, and `extract_typed_aliases` to discover typed variable declarations that introduce additional aliases for imported type names.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to enumerate all named definitions from a target file's AST, required when resolving wildcard imports, same-package visibility, or C/C++ `#include` incorporation.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : uses `extract_callee_source` to retrieve the definition source code of a named symbol from its owning file, attached as `target_context` in usage records.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` : uses `resolve_module_to_project_path` to map an import module string to a project-internal file path (determining whether a caller imports from the target file), and `get_import_params` to obtain the tree-sitter `Language` object and query string needed to extract imports from a caller file.

- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py` : uses `USAGE_NODE_TYPES` to obtain per-language AST node type configuration for usage extraction, `IMPORT_RESOLVE_CONFIG` to determine the language-specific module path separator, `DEFINITION_DICTS` to obtain per-language definition node mappings when loading target definitions, and `SAME_PACKAGE_VISIBLE` to determine whether same-directory files are implicitly visible without an explicit import (Java/Kotlin).

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : uses `build_usage_info_list` to produce usage-location records (with definition source context) for names that the current file imports from within the project, and `build_caller_usages` to collect the lines in other project files where symbols defined in the current file are referenced.

## Dependency Direction

All relationships are **unidirectional**:

- This module depends on `ts_parser`, `imports`, `usages`, `definitions`, `dependency_graph`, `import_to_path`, and `settings` — none of those modules import from `usage_analysis.py`.
- `file_analyzer.py` depends on this module — this module does not import from `file_analyzer.py`.

## Data Flow

# Data Flow

## 1. Inputs

### `build_usage_info_list`
| Input | Type | Source |
|---|---|---|
| `root_node` | AST Node | Parsed AST of the caller file |
| `symbol_to_file_map` | `dict[str, str]` | Maps imported symbol names → definition file paths |
| `project_dir` | `str` | Absolute path to project root |
| `file_ext` | `str` | File extension without leading dot |
| `alias_to_original` | `dict[str, str] \| None` | Maps alias names → original names |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Per-language usage node type config from `settings.py` |

### `build_caller_usages`
| Input | Type | Source |
|---|---|---|
| `target_file_rel` | `str` | Relative path of the target file |
| `project_dep_list` | `list[dict]` | Project-wide dependency info (callers/callees per file) |
| `project_dir` | `str` | Absolute path to project root |
| `project_file_set` | `set[str]` | All file paths in the project |
| Config values | `dict` | `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS` from `settings.py` |
| File contents | `bytes` / `str` | Read from disk via `parse_file` and direct file open |

---

## 2. Transformation Overview

### `build_usage_info_list` Pipeline

**Stage 1 — Typed alias discovery:**  
`USAGE_NODE_TYPES[file_ext]` is consulted to obtain `typed_alias_parent_types`. `extract_typed_aliases` traverses the AST to find variable declarations typed with imported names (e.g., `genre: Genre`), returning a `var_name → type_name` dict. New variable names discovered this way are injected into `symbol_to_file_map`, pointing to the same file as their type.

**Stage 2 — Usage extraction:**  
`extract_usages` performs a DFS over the AST to collect all `UsageInfo` records (name + line number) for any identifier in the expanded `symbol_to_file_map` key set.

**Stage 3 — Alias remapping:**  
For each `UsageInfo`, the root symbol (the part before the first `.`) is checked against `typed_aliases`. If it matches, the usage name is rewritten to substitute the variable name with the original type name (e.g., `genre.play()` → `Genre.play()`).

**Stage 4 — Grouping and deduplication:**  
Usages are grouped by `(source_file, remapped_name)`. The first occurrence triggers `extract_callee_source` to fetch the definition's source text from its file. Subsequent occurrences for the same key simply append line numbers. After grouping, each entry's `lines` list is deduplicated and sorted.

---

### `build_caller_usages` Pipeline

**Stage 1 — Caller list retrieval:**  
`project_dep_list` is scanned to find the entry whose `file` matches `target_file_rel`, yielding a `caller_file_list`.

**Stage 2 — Per-caller import resolution:**  
For each caller file, the file is parsed with `parse_file`. `get_import_params` retrieves the tree-sitter language and query string; `extract_imports` extracts structured `ImportInfo` objects from the AST. `_collect_names_from_target` then resolves each import against `target_file_rel` to build a list of names that the caller imports from the target. Language-specific resolution strategies handle Python/JS/TS named imports, Java/Kotlin trailing-segment imports, C/C++ full-file inclusion, wildcard imports, and same-package visibility.

**Stage 3 — Typed alias expansion (per caller):**  
`extract_typed_aliases` finds local variable declarations in the caller that are typed with any of the collected target names, expanding `names_from_target` with those variable names.

**Stage 4 — Usage extraction:**  
`extract_usages` collects all `UsageInfo` records for the expanded name set within the caller AST.

**Stage 5 — Context extraction:**  
The caller's source file is read line by line. For each group, up to 2 usage locations are selected, and a ±3-line code snippet is extracted around each location; snippets are joined with `\n...\n` to form `usage_context`.

**Stage 6 — Grouping and accumulation:**  
Usages are grouped by the remapped name. Each group records `lines` (deduplicated and sorted), `name`, `file`, and `usage_context`. Results from all callers are concatenated into a single list.

---

### `_collect_names_from_target` Pipeline

For each `ImportInfo` in the caller's import list, `resolve_module_to_project_path` checks whether the import resolves to `target_file_rel`. Depending on the import form and language separator:
- Named imports → individual names added directly.
- Wildcard imports → `_load_target_definitions` parses the target file and returns all definition names.
- Java/Kotlin trailing-segment → the final `.`-separated component is added.
- C/C++ `#include` → all target definition names are added.
- Same-package (Java/Kotlin, no import match) → all target definition names are added.

`target_definition_names` is passed back as a cache to avoid re-parsing the target file across multiple import entries or caller iterations.

---

### `_load_target_definitions` Pipeline

The target file is located using `project_dir + target_file_rel`. Its extension is used to look up `DEFINITION_DICTS`. The file is parsed by `parse_file`, then `extract_definitions` traverses the AST and returns `DefinitionInfo` objects; their `.name` fields are collected into a flat string list.

---

## 3. Outputs

### `build_usage_info_list`
Returns `list[dict]`, where each dict represents one uniquely named usage of a project-internal symbol:

```
[
  {
    "lines":          [int, ...],       # sorted, deduplicated line numbers in the caller file
    "name":           str,              # remapped usage name (alias-resolved)
    "from":           str,              # definition file path (relative to project root)
    "target_context": str | None,       # source text of the definition
  },
  ...
]
```

### `build_caller_usages`
Returns `list[dict]`, where each dict represents one named symbol from the target file used in a caller file:

```
[
  {
    "lines":         [int, ...],        # sorted, deduplicated line numbers in the caller
    "name":          str,               # remapped usage name
    "file":          str,               # relative path of the caller file
    "usage_context": str,               # ±3-line code snippets around usage locations
  },
  ...
]
```

No file writes or side effects are produced; all output is via return values.

---

## 4. Key Data Structures

### `build_usage_info_list` — `usage_group_map` (internal grouping dict)
| Field / Key | Type | Purpose |
|---|---|---|
| key `(source_file, remapped_name)` | `tuple[str, str]` | Unique identifier grouping all usages of the same symbol from the same file |
| `"lines"` | `list[int]` | Line numbers where the usage appears |
| `"name"` | `str` | Usage name after alias remapping |
| `"from"` | `str` | Relative path of the file containing the definition |
| `"target_context"` | `str \| None` | Source text of the definition retrieved by `extract_callee_source` |

### `build_caller_usages` — `groups` (internal per-caller grouping dict)
| Field / Key | Type | Purpose |
|---|---|---|
| key `name` | `str` | Remapped usage name used as grouping key within a single caller |
| `"lines"` | `list[int]` | Line numbers of usages in the caller file |
| `"name"` | `str` | Remapped usage name |
| `"file"` | `str` | Relative path of the caller file |
| `"usage_context"` | `str` | Code snippet context added after lines deduplication |

### `project_dep_list` entries (consumed input)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Relative path of the file described |
| `"callers"` | `list[str]` | Relative paths of files that import this file |
| `"callees"` | `list[str]` | Relative paths of files imported by this file |

### `typed_aliases` (intermediate, both functions)
| Field / Key | Type | Purpose |
|---|---|---|
| key `var_name` | `str` | Local variable name declared with an imported type |
| value `type_name` | `str` | The imported type name that the variable's type refers to |

### `symbol_to_file_map` (consumed and mutated in `build_usage_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| key `symbol_name` | `str` | Imported or alias-derived name |
| value `file_path` | `str` | Relative path of the file where that symbol is defined |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file follows a **graceful degradation / logging-and-continue** strategy. The dominant pattern is to skip operations that cannot be completed rather than raising exceptions to the caller. File I/O failures during context extraction are silently absorbed so that the rest of the analysis proceeds. Upstream failures in parsing or definition lookup are tolerated by returning `None` or empty collections, which the calling code treats as absent data and skips accordingly.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Reading a caller source file to extract `usage_context` snippets | Caught silently; `caller_source_lines` remains `None` | Yes | `usage_context` field is omitted from all groups in that caller |
| Missing file / unparseable file (via `parse_file`) | Target or caller file does not exist or cannot be parsed by tree-sitter | Propagates from `parse_file` (no local catch); `_load_target_definitions` guards with `os.path.isfile` before calling `parse_file` | Partial — `_load_target_definitions` skips gracefully; direct `parse_file` calls in `build_caller_usages` are unguarded | `_load_target_definitions` returns empty list; unguarded paths may propagate exceptions |
| Definition not found (`extract_callee_source` returns `None`) | The named symbol is not locatable in the dependency file's AST | `None` is stored as `target_context` in the usage group entry | Yes | The usage entry is still produced but `target_context` is `None` |
| Unsupported language / missing import params (`get_import_params` returns `(None, None)`) | Caller file has an extension with no import query configured | `continue` skips the caller entirely | Yes | That caller file contributes no entries to `caller_usages` |
| No `DEFINITION_DICTS` entry for target extension | Target file extension is not registered in settings | `_load_target_definitions` returns an empty list without calling `parse_file` | Yes | No definition names are collected; wildcard/C++ include expansion produces no names |
| Empty or absent `usage_node_types` | File extension not registered in `USAGE_NODE_TYPES` | `extract_usages` returns `[]`; `extract_typed_aliases` returns `{}` | Yes | No usages detected for that file; contributes empty result |
| Module not resolvable to a project path (`resolve_module_to_project_path` returns `None`) | Import refers to a standard library or external package | Import is skipped in `_collect_names_from_target`; wildcard Java/Kotlin path checked as secondary fallback | Yes | Names from that import are not tracked |

---

## 3. Design Notes

- **Selective guarding**: The `os.path.isfile` check in `_load_target_definitions` before invoking `parse_file` is a deliberate guard at the boundary where a missing file is considered a normal project condition (e.g., generated or excluded files). In contrast, `parse_file` calls inside `build_caller_usages` are unguarded, implying callers are expected to exist (their paths come from a pre-validated dependency list).
- **None-as-absent convention**: Functions like `extract_callee_source` returning `None` are treated as "no data available" rather than errors. The calling code stores `None` directly in the output dict without special-casing, delegating interpretation to downstream consumers.
- **Silent I/O absorption**: The `try/except (OSError, UnicodeDecodeError)` around context extraction is intentionally broad and silent, prioritising completeness of the structural output (names and lines) over the supplementary `usage_context` field.
- **Cache-driven resilience**: `parse_file` uses a module-level cache, so repeated access to the same file (e.g., the target file parsed both for definitions and for callee source) does not multiply I/O failure opportunities.

## Summary

**usage_analysis.py**: Extracts project-internal symbol usage locations from ASTs and collects inbound usages from other project files.

- `build_usage_info_list(root_node, symbol_to_file_map: dict[str,str], project_dir: str, file_ext: str, alias_to_original: dict|None) → list[dict]`: returns records with `lines`, `name`, `from`, `target_context`.
- `build_caller_usages(target_file_rel: str, project_dep_list: list[dict], project_dir: str, project_file_set: set[str]) → list[dict]`: returns records with `lines`, `name`, `file`, `usage_context`.

Consumes `symbol_to_file_map` (mutated) and `project_dep_list`; produces grouped usage dicts.
