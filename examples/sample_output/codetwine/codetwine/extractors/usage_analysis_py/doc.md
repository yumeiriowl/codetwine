# Design Document: codetwine/extractors/usage_analysis.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyze usage relationships between project files by extracting the lines where imported symbols are used and pairing each usage with its definition source code, producing structured data for both callee-usage and caller-usage outputs.

## 2. When to Use This Module

- **Call `build_usage_info_list`** when you have the AST root of a file and a map of imported symbol names to their definition files, and you need a list of records describing where each imported project-internal symbol is used within that file, along with the corresponding definition source code (used by `codetwine/file_analyzer.py` to produce `callee_usages` output).

- **Call `build_caller_usages`** when you have the relative path of a target file and the project-wide dependency list, and you need to find all other project files that use symbols defined in that target file, along with the line numbers and surrounding source context of each usage (used by `codetwine/file_analyzer.py` to produce `caller_usages` output).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `build_usage_info_list` | `root_node`, `symbol_to_file_map: dict[str, str]`, `project_dir: str`, `file_ext: str`, `alias_to_original: dict[str, str] \| None` | `list[dict]` | Extract usage locations of project-internal imported symbols within a file's AST, merge multi-line occurrences of the same symbol into one record, and attach the definition source code for each symbol. |
| `build_caller_usages` | `target_file_rel: str`, `project_dep_list: list[dict]`, `project_dir: str`, `project_file_set: set[str]` | `list[dict]` | For each file that imports from the target file, collect the lines where target-defined symbols are used and attach surrounding source context, returning one record per symbol per caller file. |

## 4. Design Decisions

- **Typed alias expansion**: Both public functions extend the set of tracked symbol names with typed variable aliases (e.g., a variable `genre` declared with type `Genre`) via `extract_typed_aliases`, then remap those alias names back to the original type names before grouping, so that usages of local variables typed with an imported class are attributed to the original imported symbol rather than the local variable name.

- **Deferred definition loading with caching**: When collecting names imported from a target file in `build_caller_usages`, the target file's definitions are parsed at most once per invocation. The result is passed through the caller loop via `target_definition_names`, avoiding redundant parsing for languages (C/C++, Java/Kotlin wildcard imports, same-package visibility) where all target definitions are implicitly available without an explicit per-name import.

- **Usage grouping by `(source_file, name)` key**: In `build_usage_info_list`, usages of the same symbol are merged into a single record with a `lines` list rather than emitting one record per occurrence, reducing redundancy in the output and ensuring the definition source code is fetched only once per symbol.

- **Context radius for caller usages**: In `build_caller_usages`, usage context snippets are extracted with a fixed radius of 3 lines around each usage line, capped at 2 usage locations per symbol group (`_max_context_locations = 2`, `_context_radius = 3`), balancing context richness against output size.

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

- `symbol_to_file_map`: maps imported symbol names to their definition file paths (relative to project root)
- `alias_to_original`: maps alias names (as used in the caller file) to their original exported names; `None` if no aliasing occurred
- Returns a list of dicts, each representing a group of usages of one symbol, with keys: `lines` (list of ints), `name` (str), `from` (str), `target_context` (str or None)

**Responsibility:**
Identifies all locations in a single file's AST where project-internal imported names are used, retrieves the source code of each referenced definition, and merges multiple usage lines for the same symbol into a single record.

**When to use:**
Called by `file_analyzer.py` after parsing a source file to build the `callee_usages` output for that file.

**Design decisions:**
- Mutates `symbol_to_file_map` in-place to add typed alias variable names (e.g., a variable `genre` declared with type `Genre` is added so its usages are tracked alongside the type).
- Groups usages by `(source_file, remapped_name)` rather than by raw name, so aliased imports and typed variables are correctly consolidated with the canonical name.
- When looking up the definition source for an aliased name, translates the alias back to the original exported name before calling `extract_callee_source`, ensuring the correct definition is found even when the import was renamed at the call site.
- Duplicate line numbers within a group are eliminated and the list is sorted before returning.

**Constraints & edge cases:**
- `symbol_to_file_map` must already contain only project-internal symbols (external/stdlib imports excluded upstream).
- If `alias_to_original` is `None`, no alias remapping is performed for definition lookup.
- `target_context` in the returned dict may be `None` if `extract_callee_source` cannot locate the definition.
- `file_ext` must match a key in `USAGE_NODE_TYPES`; if it does not, `usage_node_types` will be `None` and `extract_usages` returns an empty list.

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

- `caller_import_list`: list of `ImportInfo` objects from the caller file
- `target_file_rel`: project-relative path of the file whose usage is being tracked
- `caller_ext`: file extension of the caller (without leading `.`), used to select language-specific resolution behavior
- `project_file_set`: set of all known project file paths for module resolution
- `target_definition_names`: cached result of parsing the target file's definitions; `None` signals that it has not been computed yet
- Returns `(names_from_target, target_definition_names)` where `names_from_target` is a list of symbol name strings and `target_definition_names` is either the newly computed cache or the passed-in value

**Responsibility:**
Determines which names the caller file brings in from the target file by inspecting the caller's import statements, applying language-specific rules for how names are derived from import syntax.

**When to use:**
Called once per caller file inside `build_caller_usages` to populate the set of names to search for in the caller's AST.

**Design decisions:**
- Language behavior is driven by the `separator` field from `IMPORT_RESOLVE_CONFIG`:
  | Separator | Language family | Name derivation |
  |-----------|----------------|-----------------|
  | `.` (with names) | Python / JS / TS | Individual names from `from X import a, b` |
  | `.` (no names, leaf) | Java / Kotlin | Trailing segment of dotted module path |
  | `/` | C / C++ | All definition names from the target file |
- `target_definition_names` is passed in and returned as a cache to avoid re-parsing the target file for every caller that includes it.
- Java/Kotlin wildcard imports (`import com.foo.*`) are handled separately: if the resolved path is absent but the import module maps to a package directory containing the target file, all target definitions are added.
- Same-package visibility (Java/Kotlin) is handled as a final fallback: if `SAME_PACKAGE_VISIBLE` is set for the caller's extension and both files share the same directory, target definitions are added even with no matching import statement.

**Constraints & edge cases:**
- `resolve_module_to_project_path` may return `None` for external/stdlib modules; those import entries are skipped.
- The wildcard `*` name in `import_info.names` triggers a full target-definition load; individual names alongside `*` in the same import are also added.
- Same-package fallback only fires when `names_from_target` is still empty after processing all imports.

---

## `_load_target_definitions`

**Signature:**
```python
def _load_target_definitions(
    target_file_rel: str,
    project_dir: str,
) -> list[str]
```

- `target_file_rel`: project-relative path of the target file
- Returns a list of definition name strings found in the target file; empty list if the file cannot be parsed or has no supported definition dict

**Responsibility:**
Parses the target file and extracts all top-level definition names from it, serving as the data source for wildcard imports and same-package visibility scenarios.

**When to use:**
Called lazily from `_collect_names_from_target` the first time a full definition name list is needed for a given target file, with the result cached by the caller.

**Design decisions:**
- Relies on `DEFINITION_DICTS` to select the correct definition extraction strategy for the target file's extension; if no dict is registered, returns an empty list without attempting to parse.
- Only names that are non-empty strings (truthy `defn.name`) are included.
- File existence is checked before parsing to avoid exceptions on missing files.

**Constraints & edge cases:**
- If the target file's extension has no entry in `DEFINITION_DICTS`, the function returns `[]`.
- If the file does not exist on disk, returns `[]`.
- Uses `parse_file`'s module-level cache, so repeated calls for the same file path incur no additional I/O.

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

- `project_dep_list`: list of dependency info dicts (each with `"file"` and `"callers"` keys) as produced by `build_project_dependencies`
- Returns a list of dicts, each with keys: `lines` (sorted list of ints), `name` (str), `file` (str, the caller's relative path), and `usage_context` (str, source snippets surrounding usage lines, present when caller source was readable)

**Responsibility:**
Collects all lines across every file in the project that references names defined in `target_file_rel`, producing the `caller_usages` output for the target file.

**When to use:**
Called by `file_analyzer.py` after building the project dependency graph to populate the `caller_usages` field for a given file.

**Design decisions:**
- `target_definition_names` is cached across all callers so the target file is parsed at most once regardless of how many callers exist.
- Typed variable aliases in each caller file are resolved back to their declared type names before grouping, keeping group keys consistent with the canonical imported name.
- `usage_context` is constructed by extracting a ±`_context_radius` (3) line window around each usage line, capped at `_max_context_locations` (2) locations per name, and joined with `"\n...\n"` between snippets.
- Callers for which `get_import_params` returns `(None, None)` are silently skipped, limiting analysis to languages with registered import queries.
- Duplicate line numbers within a group are deduplicated and sorted before context extraction.

**Constraints & edge cases:**
- If `target_file_rel` is not present as a `"file"` key in `project_dep_list`, `caller_file_list` remains empty and the function returns `[]`.
- `usage_context` is only populated when the caller file is readable as UTF-8; on `OSError` or `UnicodeDecodeError`, the key is absent from the group dict.
- The function does not deduplicate across callers; the same name from different caller files produces separate entries (differentiated by the `"file"` field).
- `caller_ext` must have a registered entry in `USAGE_NODE_TYPES` for usage extraction to produce results; otherwise `extract_usages` returns `[]`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/usage_analysis.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to parse caller and target source files into tree-sitter ASTs for subsequent analysis

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : uses `extract_imports` to retrieve the list of import statements from a caller file's AST

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/usages.py` : uses `extract_usages` to locate all usage lines of tracked symbol names within an AST, and `extract_typed_aliases` to discover variables declared with an imported type so they can be remapped to the original type name

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` (via `_load_target_definitions`) to enumerate all named definitions in a target file, required for wildcard imports and same-package visibility resolution

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : uses `extract_callee_source` to retrieve the definition source code of a named symbol from its defining file, attached as `target_context` in the usage info output

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` : uses `resolve_module_to_project_path` to determine whether an import statement in a caller file resolves to the target file, and `get_import_params` to obtain the tree-sitter `Language` object and query string needed for import extraction

- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py` : uses `DEFINITION_DICTS` to obtain per-language definition node configuration for target file parsing, `USAGE_NODE_TYPES` to obtain per-language usage node type settings for usage extraction, `IMPORT_RESOLVE_CONFIG` to determine the module path separator (`.` or `/`) controlling Java/Kotlin vs. C/C++ import resolution logic, and `SAME_PACKAGE_VISIBLE` to determine whether same-directory files are implicitly visible without explicit imports

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/usage_analysis.py` : uses `build_usage_info_list` to produce the callee usages output for a file being analyzed (mapping imported symbol names to their definition source and usage lines), and `build_caller_usages` to collect the lines in other project files where definitions from the current file are referenced

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

## Data Flow

# Data Flow

## 1. Inputs

**`build_usage_info_list`**
- `root_node`: Tree-sitter AST root of the caller file being analyzed
- `symbol_to_file_map`: `dict[str, str]` mapping imported symbol names to their definition file paths (relative to project root)
- `project_dir`: Absolute path to the project root directory
- `file_ext`: File extension string (e.g. `"py"`, `"java"`) without leading dot
- `alias_to_original`: Optional `dict[str, str]` mapping alias names to original names
- Config values: `USAGE_NODE_TYPES` (per-language AST node type settings)

**`build_caller_usages`**
- `target_file_rel`: Relative path of the file whose usages in other files are being sought
- `project_dep_list`: List of dependency dicts produced by `build_project_dependencies`, each containing `"file"`, `"callers"`, and `"callees"` keys
- `project_dir`: Absolute path to the project root
- `project_file_set`: Set of all relative file paths in the project
- Config values: `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`
- File reads: Each caller file is read and parsed on demand via `parse_file`; the target file may be parsed to extract definition names

---

## 2. Transformation Overview

### `build_usage_info_list`

**Stage 1 — Typed alias discovery**  
`USAGE_NODE_TYPES` is consulted for the `typed_alias_parent_types` node type set. `extract_typed_aliases` traverses the AST to find variables declared with an imported type (e.g. `genre: Genre`) and returns a `dict[str, str]` mapping variable names to type names. The variable names are injected into `symbol_to_file_map` so subsequent stages track them as importable symbols.

**Stage 2 — Raw usage extraction**  
`extract_usages` performs a DFS traversal of the AST and returns a list of `UsageInfo` objects (name + line number) for every identifier matching any key in the expanded `symbol_to_file_map`.

**Stage 3 — Alias remapping and grouping**  
Each `UsageInfo` entry is processed: the root symbol (before any `.`) is checked against `typed_aliases`; if found, the name is rewritten to the original type name. The `(source_file, remapped_name)` pair forms a group key. Entries sharing the same key accumulate their line numbers into a single record.

**Stage 4 — Definition source retrieval**  
For the first occurrence of each group key, `extract_callee_source` is called with the definition file path and the search name (substituting the `alias_to_original` original name when applicable) to retrieve the definition's source text.

**Stage 5 — Deduplication and output assembly**  
Duplicate line numbers within each group are removed and sorted. The final list of group dicts is returned.

---

### `build_caller_usages`

**Stage 1 — Caller file discovery**  
`project_dep_list` is scanned to find the entry where `"file" == target_file_rel`. Its `"callers"` list drives the rest of the pipeline.

**Stage 2 — Per-caller import extraction**  
For each caller file, the file is parsed with `parse_file`. `get_import_params` provides the language object and query string. `extract_imports` returns `list[ImportInfo]` for the caller.

**Stage 3 — Name collection from target**  
`_collect_names_from_target` inspects each `ImportInfo`: for Python/JS/TS-style imports (`from X import a, b`), individual names are taken directly; for Java/Kotlin (`import com.foo.Bar`), the trailing segment is used; for C/C++ (`#include`), all definition names from the target file are loaded. Wildcard imports and same-package visibility rules can also trigger loading of target definition names via `_load_target_definitions`.

**Stage 4 — Typed alias expansion (per caller)**  
`extract_typed_aliases` is called on the caller AST with the collected `names_from_target` set, and any newly discovered alias variable names are appended to the tracking set.

**Stage 5 — Usage extraction and grouping**  
`extract_usages` finds all usage locations within the caller. Alias variable names are remapped back to original type names. Usages are grouped by `name`, accumulating line numbers.

**Stage 6 — Usage context extraction**  
The caller's source text is read line-by-line. For each group, up to `_max_context_locations` (2) usage lines are selected; a window of `_context_radius` (3) lines around each is extracted and joined with `"\n...\n"` to form `usage_context`.

**Stage 7 — Output assembly**  
All group dicts across all callers are concatenated into a flat list and returned.

---

### `_load_target_definitions` (internal helper)

The target file is parsed and `extract_definitions` is called with the language-specific `DEFINITION_DICTS` entry. The resulting `DefinitionInfo` objects are mapped to their `name` strings and returned as a flat `list[str]`.

---

## 3. Outputs

**`build_usage_info_list`** returns `list[dict]` where each dict describes one symbol used within the analyzed file:

```
[
  {
    "lines":          [int, ...],
    "name":           str,
    "from":           str,
    "target_context": str | None,
  },
  ...
]
```

**`build_caller_usages`** returns `list[dict]` where each dict describes one symbol from the target file as used in a caller file:

```
[
  {
    "lines":         [int, ...],
    "name":          str,
    "file":          str,
    "usage_context": str,
  },
  ...
]
```

No files are written; no side effects beyond the `parse_cache` maintained by `ts_parser.py`.

---

## 4. Key Data Structures

### `symbol_to_file_map` (input / mutated in `build_usage_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| `<symbol_name>` | `str` | Relative file path where that symbol is defined |

---

### `alias_to_original` (input to `build_usage_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| `<alias_name>` | `str` | Original name that the alias was imported as |

---

### `typed_aliases` (intermediate)
| Field / Key | Type | Purpose |
|---|---|---|
| `<variable_name>` | `str` | The imported type name the variable was declared with (e.g. `"genre"` → `"Genre"`) |

---

### `UsageInfo` (produced by `extract_usages`, consumed internally)
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Symbol name as it appears at the usage site (may include `.` for attribute access) |
| `line` | `int` | 1-based line number of the usage |

---

### `usage_group_map` (intermediate in `build_usage_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| key: `(source_file, remapped_name)` | `tuple[str, str]` | Unique group identifier combining definition file and canonical name |
| `"lines"` | `list[int]` | All line numbers where this name was used |
| `"name"` | `str` | Canonical (remapped) symbol name |
| `"from"` | `str` | Relative path of the file where the symbol is defined |
| `"target_context"` | `str \| None` | Source text of the symbol's definition |

---

### `groups` (intermediate in `build_caller_usages`)
| Field / Key | Type | Purpose |
|---|---|---|
| key: `<name>` | `str` | Canonical symbol name used as the group key |
| `"lines"` | `list[int]` | All line numbers in the caller where the symbol appears |
| `"name"` | `str` | Canonical symbol name |
| `"file"` | `str` | Relative path of the caller file |
| `"usage_context"` | `str` | Source snippets surrounding usage locations (added in Stage 6) |

---

### `ImportInfo` (produced by `extract_imports`, consumed in `_collect_names_from_target`)
| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | Module/path string from the import statement |
| `names` | `list[str]` | Individually imported names; `"*"` for wildcard imports |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | Alias given to the module itself |
| `alias_map` | `dict[str, str] \| None` | Maps alias names to their original names |

---

### `project_dep_list` entry (input to `build_caller_usages`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Relative path of a project file |
| `"callers"` | `list[str]` | Relative paths of files that import this file |
| `"callees"` | `list[str]` | Relative paths of files this file imports |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** approach. The primary goal is to keep the overall analysis pipeline running even when individual operations fail. Missing files, unreadable sources, and unresolvable symbols are handled by returning partial results or skipping the failing unit rather than terminating the process. Only file I/O with genuinely unrecoverable issues (e.g., encoding errors when reading a caller file) is caught and silently bypassed, allowing remaining callers to be processed normally.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Opening a caller source file to extract usage context fails | Caught; `caller_source_lines` is left as `None`, `usage_context` field is simply omitted from affected group entries | Yes | That caller's usage entries lack `usage_context`; all other callers continue processing |
| Definition source not found (`None` return from `extract_callee_source`) | The named definition cannot be located in the callee AST | `target_context` is stored as `None` in the usage group entry; no exception is raised | Yes | The entry is included in results with a `None` context value |
| Unsupported file extension (`get_import_params` returns `(None, None)`) | A caller file has an extension with no import query configured | The caller is skipped via `continue` | Yes | That caller contributes no entries to `caller_usages` |
| Target definition file absent or unreadable | `os.path.isfile` check fails inside `_load_target_definitions` | Returns an empty list silently; no exception is raised | Yes | No definition names are extracted from that target; downstream name collection is empty |
| `USAGE_NODE_TYPES` returns `None` for an extension | Caller or target file extension has no usage node types registered | `extract_usages` returns an empty list; typed alias extraction is skipped | Yes | No usages are detected for that file; results are empty but no error propagates |
| Target file cannot be parsed by `parse_file` | `parse_file` raises an exception (e.g., missing language mapping) | Not caught within this file; exception propagates to the caller | No | The entire `build_caller_usages` or `_load_target_definitions` call fails |

---

## 3. Design Notes

- **Partial-result tolerance**: The design deliberately accepts incomplete output (missing `usage_context`, empty name lists, `None` source code) in preference to aborting the pipeline. This reflects the expectation that analysis over a large project will encounter occasional unreadable or unsupported files.
- **Guard-before-call pattern**: Many potential errors are prevented rather than caught. Checks such as `os.path.isfile`, `USAGE_NODE_TYPES.get`, and inspecting the return value of `get_import_params` before proceeding mean that the most common failure modes never reach an exception-raising code path at all.
- **No retry logic**: There is no retry or fallback for failed I/O operations; each failure is handled once and the pipeline moves on.
- **Unguarded parse failures**: Calls to `parse_file` inside `build_caller_usages` and `_load_target_definitions` are not wrapped in exception handlers. Failures here are considered exceptional and are allowed to propagate, contrasting with the otherwise defensive style of the file.
- **No centralized logging at the site of errors**: None of the error-handling paths in this file emit log messages; errors are absorbed silently, placing the burden of diagnosing missing output on the caller or the downstream consumer of the results.

## Summary

**usage_analysis.py**: Analyzes cross-file symbol usage relationships.

- `build_usage_info_list(root_node, symbol_to_file_map: dict[str,str], project_dir: str, file_ext: str, alias_to_original: dict|None) → list[dict]`: returns `{lines, name, from, target_context}` records for imported symbols used in a file.
- `build_caller_usages(target_file_rel: str, project_dep_list: list[dict], project_dir: str, project_file_set: set[str]) → list[dict]`: returns `{lines, name, file, usage_context}` records for all callers of a target file.

Consumes `ImportInfo`, `UsageInfo`; produces grouped usage dicts; expands typed variable aliases to canonical imported names.
