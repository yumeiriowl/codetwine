# Design Document: codetwine/extractors/usage_analysis.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyze symbol usage relationships between project files by collecting where imported names are used and producing structured usage records enriched with definition source code.

## 2. When to Use This Module

- **Call `build_usage_info_list`** when you have a parsed AST for a caller file and a `symbol_to_file_map` (imported name → definition file path), and you need a list of records describing where each imported project-internal symbol is used, together with the corresponding definition source code. This is used by `file_analyzer.py` to populate the `callee_usages` output.

- **Call `build_caller_usages`** when you have a target file and the project-wide dependency list, and you need to find every other file in the project that uses names defined in the target file, along with the specific lines and surrounding context where those names appear. This is used by `file_analyzer.py` to populate the `caller_usages` output.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None` | `list[dict]` | Extracts usage locations of project-internal imported names from a file's AST, resolves their definition source code, and returns merged records keyed by `(source_file, name)`. Each record includes `lines`, `name`, `from`, and `target_context`. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | For each file that imports from the target file, collects the lines where target-defined names are used and returns records with `lines`, `name`, `file`, and `usage_context` (surrounding source snippet). |

## 4. Design Decisions

- **Typed alias tracking**: Both public functions expand the set of tracked names by detecting typed variable declarations (e.g., `genre: Genre`) via `extract_typed_aliases`. Alias variable names are remapped back to their original type names before grouping, so usage records are always keyed on the canonical imported name rather than the local variable name.

- **Deduplication and merging by group key**: Rather than emitting one record per usage occurrence, both functions group entries by `(source_file, name)` or `(name, caller_file)` and accumulate all line numbers into a `lines` list, removing duplicates via `sorted(set(...))`. This produces one consolidated record per symbol per file pair.

- **Definition name caching across callers**: In `build_caller_usages`, the target file's definition names (needed for wildcard imports, C/C++ includes, and same-package visibility) are computed once via `_load_target_definitions` and reused across all caller iterations through the `target_definition_names` cache variable, avoiding redundant file parses.

- **Usage context extraction**: `build_caller_usages` attaches a `usage_context` snippet (±3 lines around each usage, up to 2 locations) sourced directly from the caller file's raw text, giving consumers human-readable context without re-parsing.

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

- `root_node`: Tree-sitter AST root node of the file being analyzed.
- `symbol_to_file_map`: Maps imported symbol names to the project-relative path of their definition file. **Mutated in place** when typed aliases are discovered.
- `project_dir`: Absolute filesystem path to the project root.
- `file_ext`: Language identifier without the leading dot (e.g., `"py"`, `"java"`).
- `alias_to_original`: Optional mapping from alias names (as they appear in the import statement) to their original definition names. Used to look up the correct definition when a name was imported under an alias.
- Returns: A list of dicts, each with keys `"lines"` (sorted list of int line numbers), `"name"` (usage name string), `"from"` (definition file path), and `"target_context"` (definition source code string or `None`).

**Responsibility:**
Identifies all locations in one file where project-internal symbols are used, resolves each usage back to the definition file, retrieves the definition source code, and merges multiple occurrences of the same symbol into a single record.

**When to use:**
Called by `file_analyzer.py` after an import map has been built for a file, to produce the `callee_usages` output for that file.

**Design decisions:**
- **Typed alias expansion:** Before extracting usages, the function discovers typed variable declarations (e.g., `Genre genre`) via `extract_typed_aliases` and transparently adds those variable names to the tracking set. This ensures usage of a typed variable is attributed back to the original imported type. `symbol_to_file_map` is mutated to include the new variable-name entries pointing to the same file as the type.
- **Grouping key:** `(source_file, remapped_name)` is used as the grouping key so that the same symbol used in multiple places produces exactly one output record with all line numbers merged.
- **Alias remapping for definition lookup:** When `alias_to_original` is provided, the `search_name` passed to `extract_callee_source` is rewritten to the original definition name, while the `"name"` field in the output retains the remapped (post-alias) name.
- **Deduplication:** Duplicate line numbers within a group are removed and the list is sorted before the function returns.

**Constraints & edge cases:**
- `symbol_to_file_map` is mutated; callers should be aware that new keys for typed alias variables will be added.
- If `alias_to_original` is `None`, no alias remapping for definition lookup is performed.
- `"target_context"` will be `None` if `extract_callee_source` cannot locate the definition.
- If `USAGE_NODE_TYPES` has no entry for `file_ext`, usage extraction returns an empty list.

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

- `caller_import_list`: List of `ImportInfo` objects extracted from the caller file.
- `target_file_rel`: Project-relative path of the target (definition) file.
- `caller_ext`: File extension of the caller without the leading dot.
- `caller_rel`: Project-relative path of the caller file; used for module resolution.
- `project_file_set`: Complete set of project-relative file paths.
- `project_dir`: Absolute path to the project root.
- `target_definition_names`: Cached list of definition names from the target file, or `None` if not yet loaded. Acts as an in/out cache across successive calls for the same target.
- Returns: A tuple `(names_from_target, target_definition_names)` where `names_from_target` is the list of symbol names the caller imports from the target, and `target_definition_names` is the (possibly newly populated) cache value.

**Responsibility:**
Determines which symbol names a caller file imports from a specific target file, applying language-specific resolution strategies so that downstream usage extraction knows what names to track.

**When to use:**
Called inside the caller loop in `build_caller_usages` for each caller file, once per caller, to derive the set of trackable names before calling `extract_usages`.

**Design decisions:**

| Condition | Behavior |
|---|---|
| `import_info.names` is non-empty and does not contain `"*"` | Add named symbols directly |
| `import_info.names` contains `"*"` | Load all target definitions and add them |
| `caller_separator == "."` and no `names` | Java/Kotlin: extract trailing component of the dotted module path |
| `caller_separator == "/"` and no `names` | C/C++: `#include` pulls in the whole file; load all target definitions |
| Unresolved module + `"*"` in names + `caller_separator == "."` | Java/Kotlin wildcard package import: add all target definitions if target lives in the package directory |
| No names found + `SAME_PACKAGE_VISIBLE` is true for `caller_ext` + same directory | Java/Kotlin same-package visibility: add all target definitions |

- **Lazy definition loading:** Target definitions are loaded at most once per `build_caller_usages` call because the result is passed back as `target_definition_names` and reused. The `_load_target_definitions` helper is invoked only when the cache is `None`.
- **`"*"` filtering:** Wildcard entries are never included directly in `names_from_target`; they trigger a full definition load instead.

**Constraints & edge cases:**
- `"*"` entries in `import_info.names` are consumed to trigger full-file loading and are not propagated into the returned list.
- If `IMPORT_RESOLVE_CONFIG` has no entry for `caller_ext`, the separator defaults to `"."`.
- Returns an empty `names_from_target` list (not `None`) when nothing matches.

---

## `_load_target_definitions`

**Signature:**
```python
def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```

- `target_file_rel`: Project-relative path of the file to inspect.
- `project_dir`: Absolute path to the project root.
- Returns: A list of definition name strings found in the target file. Returns an empty list if the file does not exist, if its extension is unsupported, or if no definitions are found.

**Responsibility:**
Parses a target file and extracts all top-level and nested definition names so callers that import the entire file (C/C++ `#include`, Java/Kotlin wildcard imports, same-package visibility) can enumerate trackable symbols.

**When to use:**
Called by `_collect_names_from_target` when a full-file symbol enumeration is required; not called directly by external code.

**Design decisions:**
- Guards against unsupported extensions by checking `DEFINITION_DICTS` before attempting to parse, returning an empty list immediately if the extension is unknown.
- Uses `parse_file`, which provides module-level caching, so repeated calls for the same file do not re-read disk.

**Constraints & edge cases:**
- Returns `[]` (not `None`) when the file is absent, unreadable, or has no recognized definitions.
- Only names for which `defn.name` is truthy are included; anonymous definitions are silently skipped.

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

- `target_file_rel`: Project-relative path of the file whose definitions are being tracked as used elsewhere.
- `project_dep_list`: The project-wide dependency list produced by `save_project_dependencies`; each entry is a dict with at least `"file"` and `"callers"` keys.
- `project_dir`: Absolute path to the project root.
- `project_file_set`: Complete set of project-relative file paths.
- Returns: A list of dicts. Each dict represents one symbol used in one caller file and contains: `"lines"` (sorted, deduplicated list of int line numbers), `"name"` (symbol name string), `"file"` (caller's project-relative path), and `"usage_context"` (code snippet string, present only when the caller source was readable).

**Responsibility:**
Finds every other file in the project that references symbols defined in `target_file_rel`, identifies the exact lines of those references, and attaches surrounding source context snippets, producing the `caller_usages` output.

**When to use:**
Called by `file_analyzer.py` after the project dependency list is available, to populate the `caller_usages` field for a given file.

**Design decisions:**
- **Target definition caching across callers:** `target_definition_names` is initialized to `None` before the caller loop and passed into `_collect_names_from_target` each iteration. The first call that needs full-file definitions populates it; subsequent callers reuse it, avoiding redundant parses of the same target file.
- **Typed alias expansion within callers:** As in `build_usage_info_list`, typed aliases discovered in each caller file are added to `names_from_target` before usage extraction, with remapping applied during grouping.
- **Usage context extraction:** For each group, up to `_max_context_locations = 2` line numbers are used; each contributes a ±`_context_radius = 3` line window. Multiple snippets are joined with `"\n...\n"`. Context extraction is skipped silently if the caller file cannot be read.
- **Grouping key is `name`:** Within a single caller file, usages are grouped by the (remapped) symbol name alone. Each group maps to one output dict with the caller's file path.
- **File-level fallback:** If `get_import_params` returns `(None, None)` for a caller's extension, that caller is skipped entirely.

**Constraints & edge cases:**
- If `target_file_rel` is not found in `project_dep_list`, `caller_file_list` remains empty and the function returns `[]`.
- `"usage_context"` is only added to a group dict when `caller_source_lines` is not `None`; groups from unreadable files will lack this key.
- `OSError` and `UnicodeDecodeError` on reading the caller source are silently swallowed; the group is still emitted without context.
- Duplicate line numbers within a group are removed and sorted before context extraction runs.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse caller and target source files into tree-sitter ASTs for downstream symbol extraction.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to retrieve the list of import statements from a caller file's AST, enabling resolution of which names originate from which module.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : Uses `extract_usages` to locate all usage sites of tracked symbol names within an AST, and `extract_typed_aliases` to discover variable names declared with imported types so they can be tracked as additional usage targets.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to enumerate all named definitions in a target file, required when resolving wildcard imports or same-package visibility to determine which names should be tracked.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a dependency file, attaching it as `target_context` in usage records.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to map an import statement's module string to a project-internal file path, and `get_import_params` to obtain the tree-sitter `Language` object and query string needed for import extraction on a given file extension.

- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` to select the per-language definition node configuration for target file parsing, `USAGE_NODE_TYPES` to obtain per-language AST node type settings for usage extraction, `IMPORT_RESOLVE_CONFIG` to determine the module path separator for each language (governing Java/Kotlin vs. C/C++ import resolution logic), and `SAME_PACKAGE_VISIBLE` to identify languages where same-directory files are accessible without an explicit import statement.

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : Uses `build_usage_info_list` to produce the `callee_usages` output — a list of records describing where project-internal imported names are used in the current file along with their definition source code. Also uses `build_caller_usages` to produce the `caller_usages` output — a list of records describing the locations in other project files where names defined in the current file are referenced.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py`
- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`

None of the dependency modules import back from `usage_analysis.py`, and `usage_analysis.py` does not import from `file_analyzer.py`.

## Data Flow

# Data Flow

## 1. Inputs

**`build_usage_info_list`**
- `root_node`: Tree-sitter AST root of the caller file, produced by `parse_file`
- `symbol_to_file_map`: `dict[str, str]` mapping imported symbol names to their definition file paths (relative to project root)
- `project_dir`: Absolute path string to the project root
- `file_ext`: File extension string without leading dot (e.g. `"py"`, `"java"`)
- `alias_to_original`: Optional `dict[str, str]` mapping alias names to their original names
- Config: `USAGE_NODE_TYPES` (per-extension AST node type settings)

**`build_caller_usages`**
- `target_file_rel`: Relative path string of the file whose callers are being analyzed
- `project_dep_list`: `list[dict]` from the project dependency graph, each entry having `"file"` and `"callers"` keys
- `project_dir`: Absolute path string to the project root
- `project_file_set`: `set[str]` of all project-relative file paths
- Config: `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`
- File reads: Each caller file is read from disk via `parse_file` (AST) and `open` (raw lines for context extraction)

**`_collect_names_from_target`** (internal)
- `caller_import_list`: `list[ImportInfo]` from `extract_imports`
- `target_file_rel`, `caller_ext`, `caller_rel`: path and extension strings
- `project_file_set`, `project_dir`: project scope data
- `target_definition_names`: Optional cached `list[str]` of definition names from the target file

**`_load_target_definitions`** (internal)
- `target_file_rel`: relative path string
- `project_dir`: absolute path string
- Config: `DEFINITION_DICTS`
- File read: target file parsed via `parse_file`

---

## 2. Transformation Overview

### `build_usage_info_list`

**Stage 1 — Typed alias discovery:**  
`USAGE_NODE_TYPES` is queried for `typed_alias_parent_types`. `extract_typed_aliases` traverses the AST to find variables declared with an imported type (e.g. `genre: Genre`), returning a `dict[str, str]` of variable name → type name. New variable names are injected into `symbol_to_file_map`, mapping them to the same file as the original type.

**Stage 2 — Usage extraction:**  
`extract_usages` performs a DFS over the AST and returns a `list[UsageInfo]`, each carrying a name and line number, covering all detected uses of symbols in `symbol_to_file_map`.

**Stage 3 — Grouping and deduplication:**  
Each `UsageInfo` entry is processed: the root symbol is extracted (left of `.`), alias variables are remapped to their original type names, and the source file is looked up from `symbol_to_file_map`. Entries are grouped into `usage_group_map` keyed by `(source_file, remapped_name)`. Line numbers are accumulated per group; on first occurrence, `extract_callee_source` retrieves the definition source text from the target file.

**Stage 4 — Line deduplication and output:**  
Each group's `lines` list is deduplicated and sorted, then all groups are collected into a flat `list[dict]`.

---

### `build_caller_usages`

**Stage 1 — Caller identification:**  
`project_dep_list` is scanned to find the entry matching `target_file_rel`, extracting its `"callers"` list.

**Stage 2 — Per-caller import analysis (loop):**  
For each caller file: the AST is parsed via `parse_file`; imports are extracted via `extract_imports` using parameters from `get_import_params`. `_collect_names_from_target` resolves which imports point to the target file and collects the specific symbol names imported from it, using `resolve_module_to_project_path` for each import.

**Stage 2a — Target definition loading (lazy, cached):**  
When a wildcard import, C/C++ include, same-package visibility, or Java/Kotlin wildcard package import is encountered, `_load_target_definitions` parses the target file once and returns all definition names. The result is cached in `target_definition_names` across the caller loop.

**Stage 3 — Typed alias expansion:**  
`extract_typed_aliases` finds additional variable names within the caller that are typed with the collected symbols. These are appended to `names_from_target`.

**Stage 4 — Usage extraction:**  
`extract_usages` scans the caller AST for all uses of the collected names, returning `list[UsageInfo]`.

**Stage 5 — Grouping by name:**  
Usage entries are grouped into a `dict[str, dict]` keyed by the (remapped) symbol name, accumulating line numbers. Alias variable names are remapped to their original type names.

**Stage 6 — Context extraction:**  
The caller's raw source is read line-by-line. For each group, up to 2 usage locations are selected and a ±3-line snippet is extracted around each. Snippets are joined with `"\n...\n"` and stored as `"usage_context"`.

**Stage 7 — Output accumulation:**  
Each caller's groups are extended into the final `caller_usages` list.

---

## 3. Outputs

**`build_usage_info_list`** returns `list[dict]`:  
Each dict represents a unique `(definition file, symbol name)` pair and contains the merged set of lines where that symbol is used in the analyzed file, plus the definition's source text.

**`build_caller_usages`** returns `list[dict]`:  
Each dict represents a unique symbol name used in one caller file, with the accumulated usage line numbers and a surrounding code context snippet.

Both functions return data; neither writes files nor produces side effects.

---

## 4. Key Data Structures

### `usage_group_map` entry (produced by `build_usage_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers where the symbol is used |
| `"name"` | `str` | Remapped symbol name (alias resolved to original type if applicable) |
| `"from"` | `str` | Project-relative path of the file where the symbol is defined |
| `"target_context"` | `str \| None` | Full source text of the definition, from `extract_callee_source` |

### `groups` entry (produced by `build_caller_usages`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"lines"` | `list[int]` | Sorted, deduplicated line numbers of usages within the caller file |
| `"name"` | `str` | Symbol name (alias-remapped to original type where applicable) |
| `"file"` | `str` | Project-relative path of the caller file |
| `"usage_context"` | `str` | Concatenated ±3-line source snippets around up to 2 usage sites |

### `symbol_to_file_map` (input/mutated in `build_usage_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| symbol name | `str` (key) | Imported name or typed alias variable name |
| file path | `str` (value) | Project-relative path of the definition file |

### `typed_aliases` (intermediate, both functions)
| Field / Key | Type | Purpose |
|---|---|---|
| variable name | `str` (key) | Local variable typed with an imported type (e.g. `"genre"`) |
| type name | `str` (value) | The imported type name used in the declaration (e.g. `"Genre"`) |

### `project_dep_list` entry (input to `build_caller_usages`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Project-relative path of a file in the dependency graph |
| `"callers"` | `list[str]` | Project-relative paths of files that import this file |

### `ImportInfo` (consumed from `extract_imports`)
| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | Import source string (module path, header name, etc.) |
| `names` | `list[str]` | Individually imported names; `"*"` for wildcard imports |
| `line` | `int` | Line number of the import statement |
| `module_alias` | `str \| None` | Alias for the module itself (`import X as Y`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias names to their original names |

### `UsageInfo` (consumed from `extract_usages`)
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name as it appears at the usage site (may include `.` for attribute access) |
| `line` | `int` | 1-based line number of the usage |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** strategy. Most operations that may fail are allowed to silently return a reduced or empty result rather than propagating exceptions to the caller. The one explicit exception is file I/O when reading caller source lines, which is caught and suppressed so that context extraction is skipped while the rest of the usage aggregation proceeds normally. No retry logic is present anywhere in the file.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Reading a caller source file with `open()` in `build_caller_usages` | Caught; `caller_source_lines` remains `None` | Yes | `usage_context` fields are omitted from all groups for that caller file; usage line numbers are still recorded |
| Missing key in `symbol_to_file_map` | A `root_symbol` derived from a usage is not present in the map | Not explicitly guarded; would raise `KeyError` at runtime | No | Process terminates with unhandled exception |
| `parse_file` failure (unsupported extension or missing file) | `parse_file` called on a file whose extension is not in `_language_map`, or the file does not exist | Not caught here; exception propagates from `ts_parser.py` | No | Process terminates with unhandled exception |
| `get_import_params` returns `(None, None)` | Caller file has an extension not registered in `IMPORT_QUERIES` | Checked explicitly; the caller file is skipped via `continue` | Yes | That caller file produces no usage entries |
| `extract_callee_source` returns `None` | Definition not found in the target file | Return value stored as `None` in `target_context`; no exception raised | Yes | `target_context` is `None` for that usage group entry |
| `_load_target_definitions` called on a non-existent or unsupported target file | `target_abs` does not exist or `target_def_dict` is `None` | Guarded by `if target_def_dict and os.path.isfile(target_abs)`; returns empty list | Yes | `names_from_target` receives no names from that file; usage detection for that target is silently skipped |
| `USAGE_NODE_TYPES.get` returns `None` | File extension not registered in `USAGE_NODE_TYPES` | `extract_usages` returns `[]` when `usage_node_types` is falsy | Yes | No usages are detected for that file extension |

---

## 3. Design Notes

- **Silent `None` propagation for missing definitions:** `extract_callee_source` may return `None`, and this value is stored directly in the output dict without raising an error. Consumers of the output must tolerate a `None` `target_context` value.
- **Caller file I/O is the only explicitly caught exception:** All other potential failure points (key lookup, parsing, resolution) are either guarded by conditional checks before the operation or left unguarded, meaning they would surface as unhandled exceptions. The asymmetry reflects that file I/O failure during context extraction is considered a non-critical, expected edge case, while logic failures elsewhere indicate programming or configuration errors that should not be silently swallowed.
- **Guard-before-call rather than try-except:** Most resilience is achieved through `if` checks (e.g., `os.path.isfile`, `if not language`, `if names_from_target`) rather than exception handling, keeping the normal code path free of exception overhead.
- **No logging in this file:** Despite `logger` being instantiated at module level, no log calls appear in the source. Silent degradation is the sole fallback mechanism for most error conditions.

## Summary

**usage_analysis.py** analyzes symbol usage relationships between project files.

**Public functions:**
- `build_usage_info_list(root_node, symbol_to_file_map: dict[str,str], project_dir: str, file_ext: str, alias_to_original: dict|None) → list[dict]` — returns callee usage records with `lines`, `name`, `from`, `target_context`
- `build_caller_usages(target_file_rel: str, project_dep_list: list[dict], project_dir: str, project_file_set: set[str]) → list[dict]` — returns caller usage records with `lines`, `name`, `file`, `usage_context`

Consumes `ImportInfo`, `UsageInfo`, and `symbol_to_file_map`; groups results by `(source_file, name)` with deduplication.
