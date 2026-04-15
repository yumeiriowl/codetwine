# Design Document: codetwine/extractors/usage_analysis.py

# Overview & Purpose

## 1. Module Summary

Analyze how names imported from project-internal files are used across source files, producing structured usage-location records that link each referenced symbol to its definition source code and the lines where it appears.

## 2. When to Use This Module

- **Generating callee usage data for a single file**: Call `build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` when you have already parsed a file and built its `symbol_to_file_map`. It returns a list of records describing which project-internal names are used, on which lines, and what their definition source code looks like. Used by `codetwine/file_analyzer.py` to populate the `callee_usages` output.

- **Generating caller usage data for a target file**: Call `build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set)` to find all other project files that import names defined in `target_file_rel`, and collect the exact lines where those names are used together with surrounding source context. Used by `codetwine/file_analyzer.py` to populate the `caller_usages` output.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None` | `list[dict]` | Extract usage locations of project-internal imported names from a parsed file's AST, attach each name's definition source code, and merge multiple occurrences of the same name into a single record with an accumulated `lines` list. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | For each file that imports from `target_file_rel`, collect the lines where those imported names are used, resolve typed variable aliases, and attach surrounding source context snippets. |

## 4. Design Decisions

- **Typed alias expansion**: Both public functions call `extract_typed_aliases` to detect variables declared with an imported type (e.g., `genre: Genre`) and transparently remap those variable names back to the original type name before grouping results. This ensures that usages through typed local variables are attributed to the correct imported symbol rather than being silently dropped.

- **Language-specific name collection strategy**: `_collect_names_from_target` (internal helper driving `build_caller_usages`) applies three distinct resolution strategies based on the language's import separator: named imports for Python/JS/TS (`.`-separated with explicit name lists), trailing-component extraction for Java/Kotlin (`.`-separated without explicit names), and full-target-definition expansion for C/C++ (`/`-separated), as well as Java/Kotlin wildcard and same-package implicit visibility. This keeps the public function's interface uniform while handling language-specific import semantics internally.

- **Definition name caching across callers**: In `build_caller_usages`, `target_definition_names` is computed at most once per call and reused across all caller files, avoiding redundant parsing of the target file when multiple callers require its full definition list (e.g., C/C++ `#include` or Java wildcard imports).

- **Deduplication and merging by group key**: In `build_usage_info_list`, records are keyed by `(source_file, remapped_name)` so that multiple AST occurrences of the same symbol are merged into one output entry with a sorted, deduplicated `lines` list rather than emitting one entry per occurrence.

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

- `symbol_to_file_map`: Maps imported symbol names to their source file paths (relative to project root). **Mutated in place** when typed aliases are discovered.
- `alias_to_original`: Maps alias names (as they appear in the import statement) to their original definition names. Optional; pass `None` if no aliasing occurred.
- Returns a list of dicts, each with keys: `lines` (sorted list of line numbers), `name` (usage name), `from` (source file path), `target_context` (source code of the definition or `None`).

**Responsibility:** Extracts all in-file usages of project-internal imported symbols from an AST, retrieves the definition source for each, and merges multiple usage locations of the same symbol into a single record.

**When to use:** Called by `file_analyzer.py` after symbol-to-file mapping has been built for a file, to produce the `callee_usages` output.

**Design decisions:**
- Typed variable aliases (e.g., a variable `genre` declared with type `Genre`) are discovered and injected into `symbol_to_file_map` so they are tracked as usages of the original type.
- Usages are grouped by a `(source_file, remapped_name)` key, meaning different names that resolve to different files are always kept in separate records even if they appear on the same line.
- When an alias-to-original mapping is present, the `search_name` used to look up the definition source is rewritten to the original name, while the `name` field in the output retains the remapped (post-alias-resolution) name.
- Duplicate line numbers within a group are removed and the list is sorted before being returned.

**Constraints & edge cases:**
- `symbol_to_file_map` is mutated by this function when typed aliases extend the tracking set; callers must be aware of this side effect.
- If `extract_callee_source` returns `None` (definition not found), `target_context` in the output dict is `None`.
- `alias_to_original` may be `None`; the alias-rewriting branch is skipped entirely in that case.

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

- `caller_import_list`: List of `ImportInfo` objects from the caller file's import statements.
- `target_file_rel`: Relative path of the file whose symbols are being sought.
- `target_definition_names`: A cache of all definition names from the target file. Pass `None` on first call; the function will populate it lazily and return it so the caller can pass it back on subsequent calls.
- Returns `(names_from_target, target_definition_names)`. `names_from_target` is a flat list of symbol name strings (may contain duplicates before the caller deduplicates). `target_definition_names` is either the previously passed cache value or a newly loaded one.

**Responsibility:** Determines which names from a target file are visible to a given caller file by inspecting the caller's import statements and applying language-specific resolution rules.

**When to use:** Called once per caller file inside `build_caller_usages` to identify which symbols need usage tracking.

**Design decisions:**

| Scenario | Rule applied |
|---|---|
| `from X import a, b` (Python/JS/TS) | Named symbols are added directly |
| `from X import *` | All definitions from the target file are added |
| `import com.foo.Bar` (Java/Kotlin, separator `.`) | Only the trailing leaf `Bar` is added |
| `#include <header.h>` (C/C++, separator `/`) | All definitions from the target file are added |
| Java/Kotlin wildcard `import pkg.*` (unresolved module) | Target file checked against package directory; all definitions added if match |
| Same-directory, `SAME_PACKAGE_VISIBLE` set for language | All definitions added without any import match required |

- `target_definition_names` is loaded lazily via `_load_target_definitions` and cached across iterations via the return value, avoiding redundant parses for C/C++ and wildcard imports.

**Constraints & edge cases:**
- The `caller_separator` determines language family behavior; if `IMPORT_RESOLVE_CONFIG` has no entry for `caller_ext`, `separator` defaults to `"."`.
- The returned `names_from_target` list may contain duplicates; callers are responsible for deduplication before further processing.
- The same-package fallback only activates when `names_from_target` is still empty after processing all import statements.

---

## `_load_target_definitions`

**Signature:**
```python
def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```

- Returns a flat list of definition name strings found in the target file. Returns an empty list if the file cannot be parsed, does not exist, or has no configured `DEFINITION_DICTS` entry.

**Responsibility:** Parses a target source file and returns all top-level definition names within it, to be used when a caller needs the complete exported surface of a file (wildcard imports, C/C++ includes, same-package visibility).

**When to use:** Called by `_collect_names_from_target` whenever the complete definition name list of the target file is required and has not yet been cached.

**Design decisions:**
- Parsing uses the module-level cache in `ts_parser.parse_file`, so repeated calls for the same file incur no additional I/O.
- Only definitions whose `name` field is non-empty (truthy) are included in the result.
- The file extension is derived from `target_file_rel` rather than passed as a parameter, ensuring the correct `DEFINITION_DICTS` entry is selected.

**Constraints & edge cases:**
- Returns an empty list (not `None`) if the file extension has no entry in `DEFINITION_DICTS` or if the file does not exist on disk.
- No exception is raised on missing files; the `os.path.isfile` guard silently produces an empty result.

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

- `project_dep_list`: The full project dependency list produced by `build_project_dependencies`; each element is a dict with `"file"` and `"callers"` keys.
- Returns a list of dicts, each with keys: `lines` (sorted, deduplicated list of ints), `name` (symbol name), `file` (relative path of the caller file), `usage_context` (a multi-snippet string of surrounding source lines, present only when source was readable).

**Responsibility:** For a given target file, finds all other project files that import from it and records the exact line numbers where each imported symbol is used, together with surrounding source context.

**When to use:** Called by `file_analyzer.py` to produce the `caller_usages` output for a file being analyzed.

**Design decisions:**
- `target_definition_names` is initialized once outside the caller loop and passed into `_collect_names_from_target` on each iteration, so the target file is parsed at most once regardless of how many callers reference it.
- Typed variable aliases in the caller are discovered and appended to `names_from_target` before usage extraction, applying the same alias-remapping logic as `build_usage_info_list`.
- Usage grouping key is the post-alias-remapped `name` string alone (not a tuple with the file), because all usages in a single caller loop iteration belong to the same caller file.
- `usage_context` is built from up to `_max_context_locations = 2` usage locations, each providing `_context_radius = 3` lines of surrounding context; snippets are joined with `"\n...\n"`.
- Caller source lines are read from disk only when at least one usage was found, and only once per caller file.
- If the caller file cannot be read (`OSError`, `UnicodeDecodeError`), `usage_context` is simply absent from the group dicts; no exception is propagated.
- Callers for which `get_import_params` returns `(None, None)` are silently skipped.

**Constraints & edge cases:**
- If `target_file_rel` is not found in `project_dep_list`, `caller_file_list` remains empty and the function returns `[]`.
- The `lines` list in each output dict is deduplicated and sorted before `usage_context` extraction occurs.
- `usage_context` is only added to group dicts when `caller_source_lines` is not `None`; groups from unreadable files will lack this key.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse caller and target source files into tree-sitter ASTs for subsequent analysis.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to retrieve import statement metadata (`ImportInfo`) from a caller file's AST, enabling identification of which names originate from the target file.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : Uses `extract_usages` to locate all usage positions of tracked symbol names within an AST, and `extract_typed_aliases` to detect typed variable declarations (e.g. `Genre genre`) that introduce additional aliases requiring tracking.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to enumerate all named definitions within a target file, required when resolving wildcard imports or C/C++ `#include` directives where individual names are not explicitly listed.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a dependency file, populating the `target_context` field of usage records.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to map an import statement's module string to a project-internal file path, and `get_import_params` to obtain the tree-sitter `Language` object and query string needed to run import extraction on a caller file.

- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` to obtain the per-language definition node configuration for parsing target files, `USAGE_NODE_TYPES` to obtain per-language AST node type settings for usage extraction, `IMPORT_RESOLVE_CONFIG` to determine the module path separator per language (driving Java/Kotlin vs. C/C++ import handling logic), and `SAME_PACKAGE_VISIBLE` to identify languages where same-package symbols are accessible without explicit imports.

---

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : Uses `build_usage_info_list` to produce the list of locations where project-internal imported symbols are used within the currently analyzed file, along with the corresponding definition source code. Also uses `build_caller_usages` to collect the locations across other project files where symbols defined in the current file are referenced.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/extractors/usage_analysis.py` imports from `ts_parser.py`, `imports.py`, `usages.py`, `definitions.py`, `dependency_graph.py`, `import_to_path.py`, and `settings.py`; none of those modules import back from `usage_analysis.py`.
- `codetwine/file_analyzer.py` imports from `usage_analysis.py`; `usage_analysis.py` does not import from `file_analyzer.py`.

# Data Flow

## 1. Inputs

### `build_usage_info_list`
| Input | Format | Source |
|-------|--------|--------|
| `root_node` | Tree-sitter `Node` | Pre-parsed AST of the caller file |
| `symbol_to_file_map` | `dict[str, str]` (symbol name → relative file path) | Passed by caller (`file_analyzer.py`) |
| `project_dir` | `str` (absolute path) | Caller argument |
| `file_ext` | `str` (e.g. `"py"`, `"java"`) | Caller argument |
| `alias_to_original` | `dict[str, str] \| None` | Caller argument; maps import aliases to original names |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Config (`settings.py`) |

### `build_caller_usages`
| Input | Format | Source |
|-------|--------|--------|
| `target_file_rel` | `str` (relative path) | Caller argument |
| `project_dep_list` | `list[dict]` | Pre-built dependency graph from `build_project_dependencies` |
| `project_dir` | `str` (absolute path) | Caller argument |
| `project_file_set` | `set[str]` | Set of all project-relative file paths |
| `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `USAGE_NODE_TYPES` | Various dicts | Config (`settings.py`) |
| Source files on disk | Raw file bytes | Read via `parse_file` and direct `open()` |

---

## 2. Transformation Overview

### `build_usage_info_list` Pipeline

```
USAGE_NODE_TYPES[file_ext]
        │
        ▼
extract_typed_aliases(root_node, symbol_to_file_map.keys(), typed_alias_parent_types)
  → typed_aliases: dict[var_name → type_name]
        │
        ▼ augment symbol_to_file_map with alias variable names
        │
        ▼
extract_usages(root_node, symbol_to_file_map.keys(), usage_node_types)
  → usage_info_list: list[UsageInfo]
        │
        ▼ for each UsageInfo:
          1. split name on "." to get root_symbol
          2. remap typed alias variable → original type name
          3. look up source_file from symbol_to_file_map
          4. form group_key = (source_file, remapped_name)
          5. if alias_to_original mapping exists, derive search_name from original
          6. on first occurrence: call extract_callee_source → source_code str
        │
        ▼
usage_group_map: dict[(source_file, name) → entry dict]
        │
        ▼ deduplicate + sort each entry's lines list
        │
        ▼
list[dict]  (returned)
```

**Key merge rule:** Multiple `UsageInfo` records with the same `(source_file, remapped_name)` are merged into a single output entry; their line numbers are accumulated and then deduplicated.

---

### `build_caller_usages` Pipeline

```
project_dep_list
        │
        ▼ find dep_info where dep_info["file"] == target_file_rel
          → caller_file_list: list[str]
        │
        ▼ for each caller_rel:
          │
          ├─ parse_file(caller_abs) → caller_root AST
          │
          ├─ get_import_params(caller_ext) → (language, import_query_str)
          │
          ├─ extract_imports(caller_root, language, import_query_str)
          │    → caller_import_list: list[ImportInfo]
          │
          ├─ _collect_names_from_target(...)
          │    │  for each ImportInfo:
          │    │    resolve_module_to_project_path → resolved path
          │    │    if resolved == target_file_rel:
          │    │      • import_info.names → add individual names
          │    │      • "*" in names → _load_target_definitions → all def names
          │    │      • separator=="." (Java/Kotlin) → add trailing leaf name
          │    │      • separator=="/" (C/C++) → _load_target_definitions → all def names
          │    │    Java/Kotlin wildcard + package match → _load_target_definitions
          │    │    SAME_PACKAGE_VISIBLE + same dir → _load_target_definitions
          │    └─ → names_from_target: list[str], (cached) target_definition_names
          │
          ├─ extract_typed_aliases(caller_root, names_from_target, ...) → typed_aliases
          │    augment names_from_target with alias variable names
          │
          ├─ extract_usages(caller_root, names_from_target, usage_node_types)
          │    → usage_list: list[UsageInfo]
          │
          ├─ open(caller_abs) → caller_source_lines: list[str]
          │
          ├─ group usage_list by remapped name
          │    → groups: dict[name → entry dict]
          │
          ├─ deduplicate + sort each group's lines
          │
          └─ extract usage_context snippets (±3 lines around each usage, up to 2 locations)
               → groups[name]["usage_context"] = str

caller_usages.extend(groups.values())
        │
        ▼
list[dict]  (returned)
```

### `_load_target_definitions` (internal helper)

```
target_file_rel + project_dir
        │
        ▼
parse_file(target_abs) → target_root AST
        │
        ▼
extract_definitions(target_root, DEFINITION_DICTS[target_ext])
        │
        ▼
list[str]  (definition names only)
```

The result is cached in the `target_definition_names` variable across all caller iterations in `build_caller_usages`.

---

## 3. Outputs

| Function | Return Type | Description |
|----------|-------------|-------------|
| `build_usage_info_list` | `list[dict]` | One entry per unique `(definition_file, name)` pair used in the analyzed file |
| `build_caller_usages` | `list[dict]` | One entry per unique name, per caller file, where a target-defined symbol is used |
| `_collect_names_from_target` | `tuple[list[str], list[str] \| None]` | Names imported from target + (possibly populated) definition-name cache |
| `_load_target_definitions` | `list[str]` | All definition names extracted from a target file |

No file writes or other side effects occur in this module.

---

## 4. Key Data Structures

### Output entry dict from `build_usage_info_list`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where the name is used |
| `name` | `str` | The (potentially remapped) name as it appears in usage |
| `from` | `str` | Relative path of the file where the name is defined |
| `target_context` | `str \| None` | Source code of the definition, from `extract_callee_source` |

### Output entry dict from `build_caller_usages`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where the name is used in the caller |
| `name` | `str` | The (potentially remapped) name being used |
| `file` | `str` | Relative path of the caller file |
| `usage_context` | `str` | Source lines surrounding each usage location, joined by `\n...\n` |

### `usage_group_map` (internal to `build_usage_info_list`)
| Field / Key | Type | Purpose |
|-------------|------|---------|
| key | `tuple[str, str]` | `(source_file_path, remapped_name)` — grouping key |
| value | `dict` | Accumulated entry (same schema as output entry above) |

### `typed_aliases`
| Field / Key | Type | Purpose |
|-------------|------|---------|
| key | `str` | Variable name declared with an imported type (e.g. `"genre"`) |
| value | `str` | The imported type name the variable was declared as (e.g. `"Genre"`) |

### `ImportInfo` (consumed, defined in `imports.py`)
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `module` | `str` | Import source module/path string |
| `names` | `list[str]` | Individually imported names; `"*"` for wildcard |
| `line` | `int` | Line number of the import statement |
| `module_alias` | `str \| None` | Alias for the whole module (`import X as Y`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias names to their original names |

### `UsageInfo` (consumed, defined in `usages.py`)
| Field / Key | Type | Purpose |
|-------------|------|---------|
| `name` | `str` | Symbol name as it appears at the usage site (may include `.` for attribute access) |
| `line` | `int` | 1-based line number of the usage |

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** strategy. The core processing loops are designed to keep running even when individual items fail. Missing or unresolvable data is represented as `None` or an empty collection, allowing callers to receive partial results rather than experiencing a hard failure. The single explicit exception catch (`OSError`, `UnicodeDecodeError` when reading caller source lines) silently absorbs I/O failures and proceeds with reduced output. No retry logic is present; each failed operation is simply skipped or left as absent data.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Opening a caller source file to extract `usage_context` snippets fails (e.g., permission denied, encoding error) | Caught; `caller_source_lines` remains `None`; the `usage_context` field is simply not populated | Yes | Affected groups in `caller_usages` lack `usage_context`; all other fields are still emitted |
| Unsupported file extension (no import params) | `get_import_params` returns `(None, None)` for a caller file's extension | `continue` skips that caller entirely | Yes | That caller file contributes no entries to `caller_usages` |
| Missing or empty `usage_node_types` | `USAGE_NODE_TYPES.get(file_ext)` returns `None` for an unsupported extension | `extract_usages` returns `[]`; `extract_typed_aliases` returns `{}` when the parent-types set is empty | Yes | No usages are detected for that file; processing continues |
| Target file absent or unparseable | `_load_target_definitions` calls `os.path.isfile` and only proceeds if the file exists and has a registered definition dict | File is silently skipped; returns an empty list | Yes | No definition names are collected for that target; wildcard/same-package name resolution yields nothing |
| `extract_callee_source` returns `None` | The named definition is not found in the dependency target file's AST | `None` is stored as `target_context` in the usage group entry | Yes | The emitted record has `"target_context": None`; the entry itself is still included |
| Symbol not in `symbol_to_file_map` | A usage name resolved via `typed_aliases` remapping is not present as a key | No explicit guard; relies on the remapping logic always inserting the alias key before lookup | N/A | Would raise `KeyError` if the remapping invariant is violated (no defensive catch) |
| No callers found for target file | `target_file_rel` is not present in `project_dep_list` | `caller_file_list` stays `[]`; the outer loop body never executes | Yes | Returns an empty `caller_usages` list |

---

## 3. Design Notes

- **Partial output preference**: The design consistently favours returning incomplete-but-valid data over raising exceptions. Missing source code (`target_context: None`), absent context snippets, or unresolvable imports all result in reduced output fields rather than aborting the analysis.
- **Guard-then-proceed pattern**: Precondition checks such as `if not language`, `if names_from_target`, `if caller_source_lines`, and `if target_def_dict and os.path.isfile(target_abs)` act as lightweight guards that naturally skip failed branches without requiring exception handling.
- **No explicit logging on most failures**: The module sets up a `logger` but none of the error paths in this file actually invoke it. Silent degradation is the chosen policy rather than warning-level logging.
- **One unguarded invariant**: The assumption that every remapped alias key is pre-inserted into `symbol_to_file_map` before it is accessed is relied upon implicitly and is not protected by a try-except or conditional check.

# Summary

**usage_analysis.py** links imported project symbols to their usage locations across source files.

**Public functions:**
- `build_usage_info_list(root_node, symbol_to_file_map: dict[str,str], project_dir: str, file_ext: str, alias_to_original: dict|None) → list[dict]` — returns callee usage records with `lines`, `name`, `from`, `target_context`
- `build_caller_usages(target_file_rel: str, project_dep_list: list[dict], project_dir: str, project_file_set: set[str]) → list[dict]` — returns caller usage records with `lines`, `name`, `file`, `usage_context`

Consumes `ImportInfo` and `UsageInfo` objects; expands typed variable aliases via `extract_typed_aliases`.
