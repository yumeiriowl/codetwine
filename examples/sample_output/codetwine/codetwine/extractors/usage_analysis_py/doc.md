# Design Document: codetwine/extractors/usage_analysis.py

# Overview & Purpose

## 1. Module Summary

Analyzes symbol usage relationships between project files by extracting where imported symbols are used within a file (`build_usage_info_list`) and where symbols defined in a target file are referenced across the rest of the project (`build_caller_usages`).

## 2. When to Use This Module

- **To produce callee usage data for a file being analyzed**: Call `build_usage_info_list(root_node, symbol_to_file_map, project_dir, file_ext, alias_to_original)` to obtain a list of records describing which project-internal symbols are used, on which lines, and with their definition source code attached. Used by `codetwine/file_analyzer.py` to populate the `callee_usages` JSON output.

- **To produce caller usage data for a target file**: Call `build_caller_usages(target_file_rel, project_dep_list, project_dir, project_file_set)` to obtain a list of records describing which other project files reference symbols defined in the target file, on which lines, and with surrounding usage context. Used by `codetwine/file_analyzer.py` to populate the `caller_usages` JSON output.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None` | `list[dict]` | Extracts usage locations of project-internally imported symbols from an AST, retrieves definition source code for each symbol, merges entries by `(definition_file, name)`, and returns deduplicated records with accumulated line numbers. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | Iterates over all files that import from the target file, determines which names they import, extracts usage lines within each caller, and returns records with usage context snippets extracted from the surrounding source lines. |

## 4. Design Decisions

- **Typed alias expansion**: Both public functions invoke `extract_typed_aliases` to detect variables declared with an imported type (e.g., `genre: Genre`) and transparently remap those variable names back to their original type names before grouping. This ensures that usages through typed local variables are attributed to the correct imported symbol rather than being missed.

- **Alias-to-original remapping in `build_usage_info_list`**: When `alias_to_original` is provided, definition lookups in the source file use the original exported name rather than the local alias name, so `extract_callee_source` receives the name as it appears in the definition file.

- **Language-aware name collection in `_collect_names_from_target`**: The strategy for determining which names a caller imports from the target differs by language separator: Python/JS/TS use explicit named imports, Java/Kotlin derive the leaf identifier from the dotted module path, and C/C++ (separator `/`) incorporate all definition names from the included file. Same-package visibility (Java/Kotlin) is handled as an additional fallback when no import statement matches.

- **Target definition caching**: In `build_caller_usages`, `target_definition_names` is initialized to `None` and populated at most once across the entire caller loop, avoiding redundant parses of the target file when multiple callers trigger full-definition loading (e.g., C/C++ includes or wildcard imports).

- **Usage context extraction**: For each caller usage group, up to two usage locations are selected and a configurable line radius of surrounding source lines is included as `usage_context`, providing human-readable snippets without embedding the entire caller file.

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

- `root_node`: AST root node of the file being analyzed.
- `symbol_to_file_map`: Maps imported symbol names (strings) to their definition file paths (project-relative strings). **Mutated in place** when typed aliases are discovered.
- `alias_to_original`: Maps alias names (as imported) to their original names in the source module. Optional.
- Returns: A list of dicts, each with keys `lines` (sorted list of int line numbers), `name` (str), `from` (str file path), and `target_context` (str source code or None).

**Responsibility:**
Locates every usage of project-internal imported symbols within a single file's AST and assembles enriched records that include the definition source code, merging multiple occurrences of the same symbol into one record.

**When to use:**
Called by `file_analyzer.py` after the symbol-to-file map for a file has been built, to produce the `callee_usages` output for that file.

**Design decisions:**

| Decision | Rationale |
|---|---|
| Typed alias expansion | Variables declared with an imported type (e.g., `Genre genre`) are added to `symbol_to_file_map` so their usages are tracked as if the type itself were referenced. |
| Grouping key `(source_file, remapped_name)` | Merges all occurrences of the same logical name (after alias remapping) into one record with accumulated line numbers. |
| Alias remapping before definition lookup | When `alias_to_original` is provided, the original name is reconstructed for `extract_callee_source` so the definition can be found under its canonical name. |
| Attribute access handling | Only the root symbol (left of the first `.`) is used for file mapping; the full dotted name is preserved in the record. |

**Constraints & edge cases:**
- `symbol_to_file_map` is mutated; callers should be aware that typed alias entries are added.
- If `alias_to_original` is `None`, no alias remapping is performed for definition lookup.
- Duplicate line numbers within a group are removed and sorted before returning.
- If `extract_callee_source` returns `None`, `target_context` is `None`.

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

- `caller_import_list`: List of `ImportInfo` objects from the caller file.
- `target_file_rel`: Project-relative path of the file whose names are being sought.
- `caller_ext`: File extension of the caller (without leading `.`), used to select language-specific resolution strategy.
- `target_definition_names`: Previously computed list of all definition names in the target file, or `None` if not yet loaded. Acts as a pass-through cache.
- Returns: A `(names_from_target, target_definition_names)` tuple — the first is the collected name list, the second is the (possibly newly populated) cache.

**Responsibility:**
Determines which names from the target file are visible to the caller, applying language-appropriate rules for named imports, wildcard imports, Java/Kotlin trailing-leaf imports, C/C++ full-header inclusion, and same-package visibility.

**When to use:**
Called inside the caller loop in `build_caller_usages`, once per caller file, to decide which symbol names to search for in that caller's AST.

**Design decisions:**

| Language family | Strategy |
|---|---|
| Python / JS / TS (`separator="."` with `names`) | Uses explicitly listed names; expands `*` to all target definitions. |
| Java / Kotlin (`separator="."`, no `names`) | Extracts the trailing segment of the dotted module path as the single name. |
| Java / Kotlin wildcard (`*` with unresolved module) | Checks if the target file lives within the package directory and adds all target definitions. |
| C / C++ (`separator="/"`) | Always expands to all target definitions because `#include` incorporates the entire file. |
| Same-package (Java/Kotlin) | If `SAME_PACKAGE_VISIBLE` is set for the caller's language and both files share a directory, all target definitions are added without any import match. |

- `target_definition_names` is lazily loaded and then passed back to the caller as a cache, preventing repeated parsing of the target file across multiple language branches.

**Constraints & edge cases:**
- Returns an empty `names_from_target` list if no import resolves to `target_file_rel` and same-package rules do not apply.
- The `*` wildcard in `import_info.names` triggers full target definition expansion only for languages that use named import lists.
- Same-package logic fires only when `names_from_target` is still empty after the import loop.

---

## `_load_target_definitions`

**Signature:**
```python
def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```

- `target_file_rel`: Project-relative path of the target file.
- Returns: A list of definition name strings found in the target file. Returns an empty list if the file cannot be read or has no recognized definition type configuration.

**Responsibility:**
Parses the target file with tree-sitter and extracts all top-level (and nested) definition names using the language-appropriate `DEFINITION_DICTS` configuration, providing a name list for wildcard/include-based import resolution.

**When to use:**
Called by `_collect_names_from_target` whenever full enumeration of a target file's definitions is needed (wildcard imports, `#include`, same-package visibility, Java/Kotlin wildcard packages).

**Design decisions:**
- Relies on `parse_file`'s module-level cache, so repeated calls for the same file do not re-read disk.
- Returns an empty list rather than raising an exception when the file is absent or the extension is unrecognized, allowing callers to proceed safely.

**Constraints & edge cases:**
- Only definitions with a non-empty `name` field (as returned by `extract_definitions`) are included.
- If `DEFINITION_DICTS` has no entry for the target file's extension, returns `[]`.
- If the file does not exist on disk, returns `[]`.

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

- `project_dep_list`: List of dependency info dicts (each with `"file"` and `"callers"` keys) produced by the project dependency builder.
- Returns: A list of dicts, each with keys `lines` (sorted list of int line numbers), `name` (str), `file` (str caller-relative path), and `usage_context` (str code snippet, present when source lines are readable).

**Responsibility:**
Across all files that import from `target_file_rel`, finds every line where names defined in that target are used and returns enriched usage records with surrounding code context.

**When to use:**
Called by `file_analyzer.py` after dependency data is available, to produce the `caller_usages` output for a given file.

**Design decisions:**

| Decision | Rationale |
|---|---|
| `target_definition_names` cache outside the caller loop | The target file's definition list is language-agnostic; computing it once and reusing across all callers avoids redundant parsing. |
| Typed alias expansion within caller | Variables in the caller declared with a target-defined type are added to the tracking set so indirect usages are captured. |
| Usage context extraction | For each grouped name, up to `_max_context_locations` (2) usage sites contribute a window of `_context_radius` (3) lines above and below; multiple snippets are joined with `\n...\n`. |
| Grouping key is `name` string alone | Within a single caller file, the same name string from the same target file is collapsed into one record, accumulating all line numbers. |
| Caller source lines loaded once per caller | File I/O for context extraction happens at most once per caller file, guarded by a check that `usage_list` is non-empty. |

**Constraints & edge cases:**
- If `target_file_rel` is not found in `project_dep_list`, `caller_file_list` is empty and an empty list is returned.
- Callers whose extension is not recognized by `get_import_params` are skipped (`language` is `None`).
- `usage_context` is omitted from a group's dict if the caller file cannot be read (`OSError` or `UnicodeDecodeError`), because it is only set when `caller_source_lines` is truthy.
- Duplicate line numbers are removed and sorted before context extraction.
- Context snippets are clamped to the actual file length to prevent index errors.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into tree-sitter AST root nodes when loading target and caller files for definition and usage extraction.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to retrieve the list of import statements (`ImportInfo`) from a caller file's AST, which is needed to determine which names the caller imports from the target file.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : Uses `extract_usages` to find all usage locations of tracked symbol names within an AST, and `extract_typed_aliases` to discover typed variable declarations whose type is an imported name, enabling alias-based usage tracking.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` : Uses `extract_definitions` (via `_load_target_definitions`) to enumerate all named definitions in a target file, which is required for wildcard imports, C/C++ `#include` incorporation, and Java/Kotlin same-package visibility resolution.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from its defining file, attaching it as `target_context` in the usage info output.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to check whether a caller's import statement resolves to the target file, and `get_import_params` to obtain the tree-sitter `Language` object and query string needed for import extraction from a given file extension.

- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py` : Uses `USAGE_NODE_TYPES` to retrieve per-language AST node type settings for usage extraction, `IMPORT_RESOLVE_CONFIG` to determine the module path separator per language (distinguishing Python/JS, Java/Kotlin, and C/C++ import resolution strategies), `DEFINITION_DICTS` to obtain per-language definition node configurations when parsing target files, and `SAME_PACKAGE_VISIBLE` to determine whether same-directory files are implicitly visible without explicit imports (Java/Kotlin).

---

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : Uses `build_usage_info_list` to produce the callee usage records (with definition source code attached) for names imported from within the project into the currently analyzed file, and `build_caller_usages` to collect the lines in other project files where symbols defined in the current file are used.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py`: unidirectional
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py`: unidirectional
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py`: unidirectional
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py`: unidirectional
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py`: unidirectional
- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py`: unidirectional
- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`: unidirectional
- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py`: unidirectional

None of the dependencies import back from `codetwine/extractors/usage_analysis.py`, and `codetwine/file_analyzer.py` is consumed by this file's dependents only, so no bidirectional relationships exist.

# Data Flow

## 1. Inputs

### `build_usage_info_list`
| Input | Format | Source |
|---|---|---|
| `root_node` | Tree-sitter `Node` (AST root) | Caller (`file_analyzer.py`) |
| `symbol_to_file_map` | `dict[str, str]` — imported name → definition file path | Caller |
| `project_dir` | `str` — absolute path | Caller |
| `file_ext` | `str` — extension without leading dot | Caller |
| `alias_to_original` | `dict[str, str] \| None` — alias name → original name | Caller (optional) |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | `codetwine/config/settings.py` |

### `build_caller_usages`
| Input | Format | Source |
|---|---|---|
| `target_file_rel` | `str` — relative file path | Caller (`file_analyzer.py`) |
| `project_dep_list` | `list[dict]` — project-wide dependency info | Caller |
| `project_dir` | `str` — absolute path | Caller |
| `project_file_set` | `set[str]` — all project file paths | Caller |
| Config constants | `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS` | `codetwine/config/settings.py` |
| Caller source files | Raw bytes read via `parse_file` | Filesystem |

---

## 2. Transformation Overview

### `build_usage_info_list`

**Stage 1 — Typed alias discovery.**  
`extract_typed_aliases` scans the AST for typed variable declarations (e.g., `Genre genre`) whose declared type belongs to `symbol_to_file_map`. The resulting `var_name → type_name` mapping is merged into `symbol_to_file_map` so that alias variable names are treated as tracked symbols alongside the original type names.

**Stage 2 — Usage extraction.**  
`extract_usages` performs a DFS over the AST and returns a list of `UsageInfo` objects (name + line number) for every occurrence of any key in the now-expanded `symbol_to_file_map`.

**Stage 3 — Alias remapping.**  
For each `UsageInfo`, the root symbol (the part before the first `.`) is checked against `typed_aliases`. If it is an alias variable, the name is rewritten to use the original type name (e.g., `genre.play` → `Genre.play`), keeping the attribute suffix intact.

**Stage 4 — Grouping and definition retrieval.**  
Usages are grouped by a `(source_file, remapped_name)` key. On the first encounter of a key, `extract_callee_source` fetches the definition source code from the definition file. Subsequent encounters for the same key append only the new line number to the existing record.

**Stage 5 — Deduplication.**  
Each group's `lines` list is deduplicated and sorted before the final list is returned.

---

### `build_caller_usages`

**Stage 1 — Caller identification.**  
`project_dep_list` is scanned for the entry whose `"file"` matches `target_file_rel`. The associated `"callers"` list is extracted.

**Stage 2 — Per-caller import analysis.**  
For each caller file, the file is parsed with `parse_file` and its import statements are extracted with `extract_imports`. `_collect_names_from_target` resolves each import's module string to a project path via `resolve_module_to_project_path` and, when the resolved path matches `target_file_rel`, collects the specific imported names. Language-specific rules determine which names are collected:
- Python/JS/TS: named imports listed in the statement.
- Java/Kotlin: the trailing component of the dotted module path; wildcard imports and same-package visibility trigger full definition name loading via `_load_target_definitions`.
- C/C++: all definition names from the target file (result cached in `target_definition_names`).

**Stage 3 — Typed alias expansion.**  
`extract_typed_aliases` finds alias variables in the caller's AST whose declared type is among the collected names, and appends the alias variable names to `names_from_target`.

**Stage 4 — Usage extraction.**  
`extract_usages` scans the caller's AST for all occurrences of the tracked names, returning `UsageInfo` objects.

**Stage 5 — Grouping and context extraction.**  
Usages are grouped by the remapped name. After deduplication and sorting of each group's `lines`, up to two usage locations per group receive a surrounding code snippet (`usage_context`) extracted from the caller's source lines, using a radius of three lines around each usage line.

**Stage 6 — Accumulation.**  
Each caller's groups are appended to the shared `caller_usages` list, which is returned after all callers are processed.

---

### `_load_target_definitions` (internal helper)

Parses the target file with `parse_file`, runs `extract_definitions` against the language-appropriate `DEFINITION_DICTS` entry, and returns a flat `list[str]` of definition names. This result is cached by the caller to avoid redundant parses.

---

## 3. Outputs

| Function | Return Type | Description |
|---|---|---|
| `build_usage_info_list` | `list[dict]` | One dict per `(source_file, name)` group; contains `lines`, `name`, `from`, `target_context` |
| `build_caller_usages` | `list[dict]` | One dict per `(caller_file, name)` group; contains `lines`, `name`, `file`, `usage_context` |
| `_collect_names_from_target` | `tuple[list[str], list[str] \| None]` | Names imported from the target file; updated definition-name cache |
| `_load_target_definitions` | `list[str]` | All definition names found in the target file |

No file writes or other side effects occur in this module.

---

## 4. Key Data Structures

### `build_usage_info_list` — entry in returned list

| Field / Key | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where this name is used |
| `name` | `str` | Usage name as it appears after alias remapping (e.g., `Genre.play`) |
| `from` | `str` | Relative path of the file where the name is defined |
| `target_context` | `str \| None` | Source code of the definition, from `extract_callee_source` |

### `build_caller_usages` — entry in returned list

| Field / Key | Type | Purpose |
|---|---|---|
| `lines` | `list[int]` | Sorted, deduplicated line numbers where this name is used in the caller |
| `name` | `str` | Usage name after alias remapping |
| `file` | `str` | Relative path of the caller file |
| `usage_context` | `str` | Up to two code snippets (±3 lines) joined by `\n...\n` |

### `usage_group_map` (internal to `build_usage_info_list`)

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `tuple[str, str]` | `(source_file_path, remapped_name)` — identity of a usage group |
| Value | `dict` | Record with `lines`, `name`, `from`, `target_context` |

### `groups` (internal to `build_caller_usages`)

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | Remapped usage name |
| Value | `dict` | Record with `lines`, `name`, `file`; `usage_context` added in Stage 5 |

### `typed_aliases`

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | Alias variable name (e.g., `genre`) |
| Value | `str` | Original imported type name (e.g., `Genre`) |

### `symbol_to_file_map`

| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` | Imported symbol name (or alias variable name after expansion) |
| Value | `str` | Relative path of the file defining that symbol |

### `project_dep_list` entry

| Field / Key | Type | Purpose |
|---|---|---|
| `file` | `str` | Relative path of a project file |
| `callers` | `list[str]` | Relative paths of files that import/depend on this file |
| `callees` | `list[str]` | Relative paths of files this file imports (not consumed here) |

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** approach. Most operations are designed to skip or return partial results when inputs are missing or files are inaccessible, rather than raising exceptions to the caller. The only explicit exception handling present is a narrow `try-except` guard around file I/O for reading caller source lines; all other error conditions are handled through defensive conditional checks and safe fallback returns (empty lists, `None`, skipped iterations).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` on file read | Opening a caller source file fails (file missing, permission denied, encoding error) in `build_caller_usages` | Caught silently; `caller_source_lines` remains `None` and `usage_context` fields are simply omitted from all groups in that caller | Yes | Affected caller's usage entries lack `usage_context`; all other data is still produced |
| Unsupported file extension (no language registered) | `get_import_params` returns `(None, None)` for a caller's extension in `build_caller_usages` | `continue` skips the entire caller iteration | Yes | That caller file is excluded from results; other callers are still processed |
| Missing `USAGE_NODE_TYPES` entry | `USAGE_NODE_TYPES.get(file_ext)` returns `None` | `extract_usages` returns an empty list; `extract_typed_aliases` receives an empty set and returns `{}` | Yes | No usages are extracted for that file extension; result is an empty list |
| Target file absent or unreadable | `os.path.isfile(target_abs)` fails in `_load_target_definitions` | Guard check prevents parsing; returns an empty `names` list | Yes | No definition names are collected; callers using wildcard/same-package logic see no names and produce no usages |
| No `DEFINITION_DICTS` entry for target extension | `DEFINITION_DICTS.get(target_ext)` returns `None` in `_load_target_definitions` | Combined guard with `isfile` check; returns empty `names` list immediately | Yes | Same as above — no definition names extracted |
| Symbol not found in dependency file | `extract_callee_source` finds no matching definition node | Returns `None`; stored as `"target_context": None` in the usage group entry | Yes | Individual usage entry has no source context; entry is still included in output |
| No callers found for the target file | `target_file_rel` not present in `project_dep_list` | `caller_file_list` stays as empty list `[]`; outer loop body never executes | Yes | `build_caller_usages` returns an empty list |
| `resolve_module_to_project_path` returns `None` | Import module cannot be resolved to any project-internal file | Import is silently skipped in `_collect_names_from_target` | Yes | That import contributes no names; does not affect other imports |

---

## 3. Design Notes

- **No exception propagation to callers.** Neither `build_usage_info_list` nor `build_caller_usages` raise exceptions under any documented error condition. Callers in `file_analyzer.py` receive either a complete or partially populated list without needing their own error handling for these scenarios.
- **Partial output preference over failure.** When a single caller file or a single usage entry cannot be fully resolved, the system produces the remaining valid entries rather than aborting the entire analysis pass. This is consistent with the broader codetwine design of producing best-effort static analysis output.
- **File I/O is the sole explicit exception boundary.** Only the file read for `usage_context` extraction is wrapped in a try-except. All other potential failure points (missing config entries, unresolvable imports, absent definition files) are guarded by conditional checks on return values, relying on the contracts of dependency functions (`parse_file`, `extract_callee_source`, etc.) to handle their own internal errors.
- **Cache reuse reduces blast radius.** `parse_file` (via `ts_parser.py`) caches results at the module level, so a file that fails to parse on one call will not trigger repeated I/O failures; however, the error handling for parse failures themselves is delegated entirely to `ts_parser.py` and is not addressed within this file.

# Summary

**usage_analysis.py**: Analyzes symbol usage relationships between project files.

**Public functions:**
- `build_usage_info_list(root_node, symbol_to_file_map: dict[str,str], project_dir: str, file_ext: str, alias_to_original: dict[str,str]|None) → list[dict]` — returns callee usage records with `lines`, `name`, `from`, `target_context`
- `build_caller_usages(target_file_rel: str, project_dep_list: list[dict], project_dir: str, project_file_set: set[str]) → list[dict]` — returns caller usage records with `lines`, `name`, `file`, `usage_context`

**Key structures consumed:** `project_dep_list` (dicts with `file`, `callers` keys); `symbol_to_file_map` (mutated in place).
