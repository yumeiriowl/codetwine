# Design Document: codetwine/import_to_path.py

## Overview & Purpose

## 1. Module Summary

Resolve import statement module names to project-internal file paths, and build symbol-to-file mapping dictionaries that enable downstream usage tracking across a multi-language codebase.

## 2. When to Use This Module

- **Checking whether an import resolves to a project file**: Call `resolve_module_to_project_path(module, current_file_rel, project_file_set)` to convert a raw module string (e.g. `"..utils"`, `"./helper"`, `"com.example.Foo"`) into a relative project file path, or `None` if the module is a standard library or external package.
- **Building a symbol-to-file map before tracking usages**: Call `build_symbol_to_file_map(import_info_list, current_file_rel, project_file_set, file_ext, project_dir)` to produce a `{symbol_name: file_path}` dict and an `{alias: original_name}` dict, used downstream to identify which file each referenced name originates from.
- **Obtaining language and query parameters for import extraction**: Call `get_import_params(file_ext)` to retrieve the tree-sitter `Language` object and import query string for a given extension before parsing import statements. Returns `(None, None)` for unsupported extensions.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `resolve_relative_import` | `module: str`, `separator: str`, `current_dir_part_list: list[str]` | `list[str]` | Converts a module name (relative or absolute) into a list of path components, handling Python-style dot prefixes and JS/TS-style `./`/`../` prefixes. |
| `generate_candidate_path_list` | `base_path: str`, `src_ext_with_dot: str`, `resolve_config: dict`, `current_dir_part_list: list[str]` | `list[str]` | Generates an ordered, deduplicated list of candidate file paths from a base path using declarative per-language config (index files, alternative extensions, bare paths, current-directory relatives). |
| `resolve_module_to_project_path` | `module: str`, `current_file_rel: str`, `project_file_set: set[str]` | `str \| None` | Resolves an import module name to a project-internal file path by chaining relative-import parsing, candidate generation, and set membership lookup. Returns `None` if no match exists. |
| `build_symbol_to_file_map` | `import_info_list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str` | `tuple[dict[str, str], dict[str, str]]` | Builds `symbol_to_file_map` and `alias_to_original` dicts from a list of parsed import statements, applying language-specific rules for Python, Java/Kotlin, and C/C++. |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Returns the tree-sitter `Language` object and import query string for the given file extension, or `(None, None)` if the extension is unsupported. |

## 4. Design Decisions

- **Language-specific rules are configuration-driven, not branched**: `generate_candidate_path_list` reads declarative flags (`try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`) from `IMPORT_RESOLVE_CONFIG` rather than containing per-language `if` branches, keeping the core algorithm language-agnostic.
- **Three-step resolution pipeline**: `resolve_module_to_project_path` deliberately delegates each stage to a named function (`resolve_relative_import` → `generate_candidate_path_list` → set lookup), making each step independently testable and replaceable.
- **Standard library and external packages are implicitly excluded**: Because resolution is performed by matching candidates against `project_file_set`, modules that have no corresponding project file are naturally filtered out without any explicit allowlist or blocklist.
- **Same-package visibility for Java/Kotlin**: `build_symbol_to_file_map` automatically registers definitions from files in the same directory when `SAME_PACKAGE_VISIBLE` is set for the extension, reflecting the language rule that same-package classes are accessible without explicit imports.

## Definition Design Specifications

---

## `resolve_relative_import`

**Signature:**
```python
def resolve_relative_import(
    module: str,
    separator: str,
    current_dir_part_list: list[str],
) -> list[str]
```

**Responsibility:** Converts an import module string into a list of filesystem path components, handling both relative and absolute import syntax for all supported languages.

**When to use:** Called by `resolve_module_to_project_path` as the first step whenever a module name must be translated into a candidate file path.

**Design decisions:**
- Python-style relative import depth is inferred by counting leading dots; one dot means current directory, each additional dot traverses one level up.
- JS/TS-style relative imports delegate normalization to `os.path.normpath`, which handles `../` sequences and collapses redundant separators; backslashes are then normalized to forward slashes.
- Absolute imports are handled by a simple `split(separator)`, requiring no directory context.

**Constraints & edge cases:**

| Condition | Behavior |
|---|---|
| Python import with only dots (e.g., `".."`), `clean_module` is empty | No path components are appended after traversing up |
| `current_dir_part_list` is empty and a relative import pops from it | `pop()` is guarded by `if path_part_list` so it silently stops |
| `separator` is neither `"."` nor `"/"` | Falls through to the absolute import branch |

---

## `generate_candidate_path_list`

**Signature:**
```python
def generate_candidate_path_list(
    base_path: str,
    src_ext_with_dot: str,
    resolve_config: dict,
    current_dir_part_list: list[str],
) -> list[str]
```

**Responsibility:** Produces an ordered, deduplicated list of possible file paths that a given base path could resolve to, driven entirely by the per-language `resolve_config` dictionary with no hard-coded language branches.

**When to use:** Called by `resolve_module_to_project_path` after `resolve_relative_import` has produced a `base_path`, to enumerate all plausible filesystem locations before matching against the project file set.

**Design decisions:**
- All language-specific behavior (index files, `__init__.py`, alternative extensions, current-directory fallback) is controlled declaratively through `resolve_config` fields, keeping the function language-agnostic.
- When `base_path` already carries a known extension (detected via `alt_ext_list`), extension-appending candidates are skipped to prevent nonsensical paths such as `"stdio.h.h"`.
- When `try_current_dir` is enabled, all root-relative candidates are duplicated with the current directory prepended, appended after the root-relative variants to maintain priority order.
- Deduplication uses `dict.fromkeys` to preserve insertion order.

**Constraints & edge cases:**

| Config field | Default | Effect when absent |
|---|---|---|
| `try_init` | `False` | `__init__.py` variant not generated |
| `index_ext_list` | `[]` | No `index.*` variants generated |
| `alt_ext_list` | `[]` | No alternative-extension variants; `has_known_ext` always `False` |
| `try_bare_path` | `False` | `base_path` itself not added |
| `try_current_dir` | `False` | No current-directory-relative candidates |

The same-extension candidate (`base_path + src_ext_with_dot`) is always the first entry unless `has_known_ext` is `True`.

---

## `resolve_module_to_project_path`

**Signature:**
```python
def resolve_module_to_project_path(
    module: str,
    current_file_rel: str,
    project_file_set: set[str],
) -> str | None
```
`project_file_set` is a set of strings, each a project-relative file path.

**Responsibility:** Determines whether an import's module name refers to a file within the project and returns its relative path, acting as the central orchestrator of the three-step resolution pipeline.

**When to use:** Called for every import statement encountered in dependency graph construction, usage analysis, and symbol-map building to filter out standard library and third-party modules.

**Design decisions:**
- Returns `None` immediately when no `IMPORT_RESOLVE_CONFIG` entry exists for the file extension, making unsupported languages safe to call.
- Returns the first matching candidate path from the ordered list, so priority is determined entirely by `generate_candidate_path_list`'s ordering.

**Constraints & edge cases:**
- `module` may be a standard library name, an external package name, or a project-internal name; all pass through, and only project files produce a non-`None` result.
- `current_file_rel` must use forward slashes or be normalized; backslashes are explicitly converted internally.
- Returns `None` if no candidate path matches any entry in `project_file_set`.

---

## `_put_symbol`

**Signature:**
```python
def _put_symbol(
    symbol_map: dict[str, str],
    name: str,
    path: str,
) -> None
```

**Responsibility:** Inserts or updates a single symbol→file-path entry in a shared map and emits a warning when an existing entry would be overwritten with a different path.

**When to use:** Used internally as the single point of insertion into `symbol_to_file_map` to ensure consistent overwrite-warning behavior across all callers within this module.

**Constraints & edge cases:**
- Overwriting a symbol with the same path it already maps to is silently allowed (not a warning condition).
- The function mutates `symbol_map` in place; it has no return value.

---

## `build_symbol_to_file_map`

**Signature:**
```python
def build_symbol_to_file_map(
    import_info_list,           # list[ImportInfo]
    current_file_rel: str,
    project_file_set: set[str],
    file_ext: str,
    project_dir: str,
) -> tuple[dict[str, str], dict[str, str]]
```

Returns a tuple of two dicts:
- `symbol_to_file_map`: maps each imported symbol name to the project-relative path of its definition file.
- `alias_to_original`: maps alias names to the original names they alias (e.g., `import X as Y` → `{"Y": "X"}`).

**Responsibility:** Builds the complete symbol-name→definition-file mapping for a single source file by resolving all its imports against the project file set, with language-specific registration logic applied per import form.

**When to use:** Called once per file during usage analysis in `file_analyzer.py` to establish the lookup table for identifying which file each referenced name originates from.

**Design decisions:**

| Import form | Registration behavior |
|---|---|
| `from X import a, b` | Each name in `names` registered individually |
| `from X import *` | All definitions from the resolved file registered via `_register_definitions_from_file` |
| `import X` (Python, dot-separator, no names) | Module root (`X`) registered; leaf part registered only when different from root |
| `import X as Y` | Alias `Y` registered; original tracked in `alias_to_original` |
| Java/Kotlin `import com.foo.Bar` (no names) | Leaf part (`Bar`) registered; root skipped for Java/Kotlin |
| C/C++ `#include` (slash-separator, no names) | All definitions from the included file registered |
| Java/Kotlin wildcard `import pkg.*` unresolvable | Falls back to `_register_definitions_from_package` |
| Same-package files (Java/Kotlin) | All definitions from sibling files in the same directory registered |

- `symbol_to_file_map.setdefault` is used when registering module roots for `from X import a, b` forms to avoid overwriting entries from a direct `import X` statement.
- Java/Kotlin are excluded from root-part registration because package roots (`com`, `org`, etc.) are never used as standalone identifiers.
- The same-package visibility pass runs only when `SAME_PACKAGE_VISIBLE[file_ext]` is truthy, limiting it to languages where this is meaningful (Java, Kotlin).

**Constraints & edge cases:**
- `import_info_list` elements are `ImportInfo` objects; the function accesses `.module`, `.names`, `.alias_map`, and `.module_alias` attributes.
- Standard library and external packages are silently excluded because `resolve_module_to_project_path` returns `None` for them.
- `project_dir` is only used when parsing files for definition extraction; it is not used for path membership checks.

---

## `_register_definitions_from_file`

**Signature:**
```python
def _register_definitions_from_file(
    file_rel: str,
    project_dir: str,
    symbol_to_file_map: dict[str, str],
) -> None
```

**Responsibility:** Parses a project file and registers all of its named definitions into the shared symbol map, supporting C/C++ `#include` semantics and wildcard import resolution.

**When to use:** Called when an import form requires registering every symbol from a target file rather than individually named imports.

**Design decisions:**
- Returns silently if the file does not exist on disk or if no `DEFINITION_DICTS` entry exists for its extension, making it safe to call speculatively.
- Delegates parsing to `parse_file` (which caches results) and definition extraction to `extract_definitions`.

**Constraints & edge cases:**
- `file_rel` is converted to an absolute path by joining with `project_dir`; the file must be accessible on the filesystem.
- Only definitions with a non-empty `.name` are registered.

---

## `_register_definitions_from_package`

**Signature:**
```python
def _register_definitions_from_package(
    package_dir: str,
    file_ext: str,
    project_dir: str,
    project_file_set: set[str],
    symbol_to_file_map: dict[str, str],
) -> None
```

**Responsibility:** Handles Java/Kotlin wildcard package imports by registering all definitions from every same-extension file directly under the specified package directory.

**When to use:** Called from `build_symbol_to_file_map` when a wildcard import (`import pkg.*`) cannot be resolved to a single file.

**Design decisions:**
- Restricts registration to files directly under `package_dir` (no recursive descent into sub-packages) by checking that the path remainder after the prefix contains no `/`.
- Delegates per-file registration to `_register_definitions_from_file`.

**Constraints & edge cases:**
- `package_dir` must use forward-slash notation matching the format of entries in `project_file_set`.
- Files with a different extension than `file_ext` under the package directory are silently skipped.

---

## `get_import_params`

**Signature:**
```python
def get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]
```

`Language` is a tree-sitter `Language` object. The second element is the import query string for that language.

**Responsibility:** Provides callers with the tree-sitter language object and import query string required to parse import statements, or signals that the extension is unsupported.

**When to use:** Called at the start of any analysis pass (dependency graph, usage analysis, file analysis) before attempting to extract imports from a file, to determine whether import extraction is supported for that file type.

**Design decisions:**
- Returns `(None, None)` rather than raising an exception so callers can use a simple truthiness check to skip unsupported languages.
- Two separate failure modes are handled: missing `IMPORT_QUERIES` entry and missing `TREE_SITTER_LANGUAGES` entry, both mapping to the same `(None, None)` return.

**Constraints & edge cases:**
- `file_ext` must be provided without a leading dot.
- A `KeyError` on `TREE_SITTER_LANGUAGES` is caught and treated as unsupported; other exceptions propagate normally.

## Dependency Description

### Dependencies (modules this file imports)

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/config/settings.py` : Imports `IMPORT_RESOLVE_CONFIG` (to look up per-language import resolution settings such as separator and candidate generation flags), `IMPORT_QUERIES` (to retrieve the tree-sitter query string for import extraction per extension), `TREE_SITTER_LANGUAGES` (to retrieve the tree-sitter `Language` object for a given extension), `DEFINITION_DICTS` (to obtain the definition extraction configuration for a file's extension when registering symbols), and `SAME_PACKAGE_VISIBLE` (to determine whether same-package files should be automatically registered as visible symbols, e.g. for Java/Kotlin).

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/parsers/ts_parser.py` : Imports `parse_file` to parse source files into AST root nodes when extracting definitions from resolved import targets (used inside `_register_definitions_from_file`).

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/extractors/definitions.py` : Imports `extract_definitions` to extract named definition symbols from a parsed AST, enabling registration of all symbol names from an imported file into the symbol-to-file map (used inside `_register_definitions_from_file`).

---

### Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/import_to_path_py/import_to_path.py` : Uses `get_import_params` to retrieve the tree-sitter `Language` object and import query string needed to analyze imports in a given file, and uses `build_symbol_to_file_map` to construct the mapping from imported symbol names to their definition file paths for subsequent usage tracking.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path_py/import_to_path.py` : Uses `resolve_module_to_project_path` to check whether a caller file imports a specific target file (by resolving each import's module name against the project file set), and uses `get_import_params` to obtain the language and query string needed to extract import statements from caller files.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path_py/import_to_path.py` : Uses `get_import_params` to retrieve language and query parameters for parsing imports in each project file, and uses `resolve_module_to_project_path` to resolve each import's module name to a project-internal file path when building the dependency graph.

---

### Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/config/settings.py` (this file consumes configuration; `settings.py` has no dependency back)
- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/parsers/ts_parser.py` (this file calls `parse_file`; `ts_parser.py` has no dependency back)
- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/extractors/definitions.py` (this file calls `extract_definitions`; `definitions.py` has no dependency back)
- `codetwine/file_analyzer.py` → `codetwine/import_to_path_py/import_to_path.py` (consumer depends on this module; no reverse dependency)
- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path_py/import_to_path.py` (consumer depends on this module; no reverse dependency)
- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path_py/import_to_path.py` (consumer depends on this module; no reverse dependency)

## Data Flow

## 1. Inputs

| Input | Format | Source |
|---|---|---|
| `module` | `str` — import module name (e.g. `"..utils"`, `"./helper"`, `"com.example.Foo"`, `"stdio.h"`) | Caller argument |
| `current_file_rel` | `str` — relative path from project root (e.g. `"src/app/main.py"`) | Caller argument |
| `project_file_set` | `set[str]` — all relative file paths in the project | Caller argument |
| `import_info_list` | `list[ImportInfo]` — structured import records from `extract_imports` | Caller argument |
| `file_ext` | `str` — extension without dot (e.g. `"py"`, `"java"`, `"c"`) | Caller argument |
| `project_dir` | `str` — absolute path to project root | Caller argument |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` — per-extension resolution settings | `codetwine/config/settings.py` |
| `IMPORT_QUERIES` | `dict[str, str | None]` — per-extension tree-sitter query strings | `codetwine/config/settings.py` |
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` — per-extension tree-sitter Language objects | `codetwine/config/settings.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` — per-extension AST definition patterns | `codetwine/config/settings.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` — per-extension same-package visibility flag | `codetwine/config/settings.py` |
| Source files on disk | Binary file content, read via `parse_file` | Filesystem |

---

## 2. Transformation Overview

### Pipeline A: `resolve_module_to_project_path`

```
module string
    │
    ▼
[Stage 1: resolve_relative_import]
    Current file's directory path components extracted from current_file_rel.
    Relative prefix (".", "..", "./", "../") is consumed to compute
    an absolute path_part_list. Absolute imports are split by separator.
    │
    ▼
path_part_list (list[str])  →  base_path = "/".join(path_part_list)
    │
    ▼
[Stage 2: generate_candidate_path_list]
    IMPORT_RESOLVE_CONFIG provides: try_init, index_ext_list, alt_ext_list,
    try_bare_path, try_current_dir.
    Candidates are produced by appending extensions, index filenames, and
    optionally current-directory-prefixed variants to base_path.
    Deduplication preserves order.
    │
    ▼
candidate_path_list (list[str])
    │
    ▼
[Stage 3: match against project_file_set]
    First candidate found in project_file_set is returned.
    Unmatched → None (standard library / external package).
```

### Pipeline B: `build_symbol_to_file_map`

```
import_info_list
    │
    ▼  (for each ImportInfo)
[Stage 1: resolve_module_to_project_path]  ← Pipeline A
    │
    ├── resolved_path == None, wildcard import, separator == "."
    │       │
    │       ▼
    │   [_register_definitions_from_package]
    │       Scans project_file_set for files directly under package_dir.
    │       For each file → parse_file + extract_definitions → _put_symbol
    │
    └── resolved_path != None
            │
            ▼
        [Name registration branch]
        ┌── names contains specific names (from X import a, b)
        │       Each name → _put_symbol(symbol_to_file_map, name, resolved_path)
        │       "*" → _register_definitions_from_file (all defs from file)
        │
        ├── names is empty, separator == "."
        │       module_alias present → register alias
        │       else → register module root (non-Java/Kotlin) + module leaf
        │
        ├── names is empty, separator == "/"
        │       → _register_definitions_from_file (C/C++ #include style)
        │
        └── names non-empty, non-Java/Kotlin
                → symbol_to_file_map.setdefault(module_root, resolved_path)

        alias_map entries → alias_to_original dict
    │
    ▼
[Stage 2: same-package visibility (Java/Kotlin)]
    If SAME_PACKAGE_VISIBLE[file_ext] is true:
    For every project file in the same directory with the same extension
    → _register_definitions_from_file
    │
    ▼
(symbol_to_file_map, alias_to_original)
```

### Pipeline C: `_register_definitions_from_file`

```
file_rel
    │
    ▼
Absolute path constructed → existence check
    │
    ▼
DEFINITION_DICTS[ext]  →  parse_file(abs_path) → root_node
    │
    ▼
extract_definitions(root_node, definition_dict) → list[DefinitionInfo]
    │
    ▼
Each defn.name → _put_symbol(symbol_to_file_map, defn.name, file_rel)
```

### Pipeline D: `get_import_params`

```
file_ext
    │
    ▼
IMPORT_QUERIES[file_ext]  →  import_query_str (or None → return (None, None))
    │
    ▼
TREE_SITTER_LANGUAGES[file_ext]  →  language object
    │
    ▼
(language, import_query_str)
```

---

## 3. Outputs

| Output | Format | Produced By |
|---|---|---|
| Resolved project file path | `str \| None` — relative path (e.g. `"src/utils.py"`) or `None` | `resolve_module_to_project_path` |
| `(symbol_to_file_map, alias_to_original)` | `tuple[dict[str, str], dict[str, str]]` | `build_symbol_to_file_map` |
| `(language, import_query_str)` | `tuple[Language, str] \| tuple[None, None]` | `get_import_params` |
| `path_part_list` | `list[str]` — path components | `resolve_relative_import` (internal) |
| `candidate_path_list` | `list[str]` — deduplicated ordered candidates | `generate_candidate_path_list` (internal) |
| Side effect: `symbol_to_file_map` populated | `dict[str, str]` mutated in place | `_put_symbol`, `_register_definitions_from_file`, `_register_definitions_from_package` |
| Warning log | Logger output when a symbol's source file is overwritten | `_put_symbol` |

---

## 4. Key Data Structures

### `resolve_config` (one entry from `IMPORT_RESOLVE_CONFIG`)

| Field / Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Module path delimiter: `"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++ |
| `try_init` | `bool` | Whether to try `base_path + "/__init__.py"` as a candidate |
| `index_ext_list` | `list[str]` | Extensions for index file candidates (e.g. `[".ts", ".js"]` → `base_path/index.ts`) |
| `alt_ext_list` | `list[str]` | Alternative extensions to try (e.g. `[".ts", ".tsx", ".js"]`) |
| `try_bare_path` | `bool` | Whether to try `base_path` with no extension appended (e.g. C/C++ `#include "stdio.h"`) |
| `try_current_dir` | `bool` | Whether to also generate candidates prefixed with the current file's directory |

### `symbol_to_file_map`

| Field / Key | Type | Purpose |
|---|---|---|
| `name` (key) | `str` | Imported symbol name, module root, module leaf, alias, or definition name |
| value | `str` | Relative project file path where the symbol is defined |

### `alias_to_original`

| Field / Key | Type | Purpose |
|---|---|---|
| alias (key) | `str` | The local alias name used in the importing file (e.g. `"b"` from `import a as b`) |
| value | `str` | The original name before aliasing (e.g. `"a"`) |

### `candidate_path_list`

| Field / Key | Type | Purpose |
|---|---|---|
| elements | `str` | Relative file path candidates in priority order, deduplicated, ready for lookup in `project_file_set` |

### `ImportInfo` (consumed, defined externally)

| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | The module being imported |
| `names` | `list[str]` | Specific names imported from the module (empty for bare `import X`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias → original name for `import a as b` style |
| `module_alias` | `str \| None` | Alias for the whole module (`import X as Y`) |

## Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** strategy throughout. No exceptions are raised to callers; instead, unresolvable states return sentinel values (`None`, empty collections) or are silently skipped, allowing the broader analysis pipeline to continue processing remaining files and symbols. The only active signaling mechanism is `logger.warning` for data-integrity anomalies (symbol overwrite conflicts). There are no `try/except` blocks in this file except in `get_import_params`, where a `KeyError` on the language map lookup causes a clean `(None, None)` fallback rather than a crash.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (resolve config missing) | `IMPORT_RESOLVE_CONFIG.get(src_ext)` returns `None` in `resolve_module_to_project_path` | Return `None` immediately | Yes | The import is treated as non-project-internal; no path resolution attempted |
| Unsupported file extension (import query missing) | `IMPORT_QUERIES.get(file_ext)` returns `None` in `get_import_params` | Return `(None, None)` | Yes | Caller skips import analysis for this file entirely |
| Unknown language key in `TREE_SITTER_LANGUAGES` | `TREE_SITTER_LANGUAGES[file_ext]` raises `KeyError` in `get_import_params` | Caught; return `(None, None)` | Yes | Caller skips import analysis for this file entirely |
| Module not resolvable to a project file | No candidate from `generate_candidate_path_list` matches `project_file_set` | Return `None` from `resolve_module_to_project_path` | Yes | Import treated as external (stdlib/third-party); excluded from symbol map |
| Target file for definition extraction does not exist on disk | `os.path.isfile(abs_path)` is `False` in `_register_definitions_from_file` | Return early, no registration | Yes | Symbols from that file are not registered; no error propagated |
| No definition dict for a file extension | `DEFINITION_DICTS.get(resolved_ext)` returns `None` in `_register_definitions_from_file` | Return early, no registration | Yes | Symbols from that file are not registered; no error propagated |
| Symbol name collision (overwrite conflict) | `_put_symbol` detects an existing entry mapping the same name to a different file | Log `WARNING`; overwrite with new path | Yes | Symbol map entry is updated to the newer file; earlier mapping is lost with a warning |
| Java/Kotlin wildcard import unresolvable to single file | `resolved_path` is `None` and `"*" in import_info.names` and `separator == "."` | Attempt package-directory fallback via `_register_definitions_from_package` | Yes | If no matching package dir files exist, no symbols registered; no error raised |
| Empty/missing module root after stripping dots | `module_root` evaluates to empty string after `lstrip(".")` | Guarded by `if module_root:` check; registration skipped | Yes | No registration for that symbol; no error propagated |

---

## 3. Design Notes

**Sentinel-value returns over exceptions:** Functions operating on potentially external or unresolvable modules (`resolve_module_to_project_path`, `get_import_params`) are explicitly designed to return `None` or `(None, None)` rather than raise, because the input space naturally includes standard library and third-party modules that will never resolve to project files. Treating non-resolution as a normal, expected outcome avoids exception-based control flow for the common case.

**Fail-safe file existence checks before parsing:** `_register_definitions_from_file` guards `parse_file` invocation with an `os.path.isfile` check and a config-presence check, preventing errors from propagating out of the parser layer for stale or misconfigured references.

**Warning-only for data integrity issues:** Symbol overwrite conflicts in `_put_symbol` are surfaced as warnings rather than errors because they are non-fatal to analysis correctness (the later-seen definition wins) and may legitimately occur during re-exports or aliased imports. The warning preserves observability without halting the pipeline.

**Single `try/except` scope:** The only exception-catching construct is the `KeyError` guard in `get_import_params`, isolated to the language-object lookup. All other error conditions are handled through control-flow checks (`if not x`, early `return`), reflecting a deliberate preference for explicit state checking over exception handling.

## Summary

Resolves import module strings to project-relative file paths and builds symbol-to-file mappings for cross-file usage tracking.

**Public functions:**
- `resolve_module_to_project_path(module:str, current_file_rel:str, project_file_set:set[str]) → str|None`
- `build_symbol_to_file_map(import_info_list, current_file_rel:str, project_file_set:set[str], file_ext:str, project_dir:str) → tuple[dict[str,str], dict[str,str]]`
- `get_import_params(file_ext:str) → tuple[Language,str]|tuple[None,None]`

**Key structures:** `symbol_to_file_map` (`dict[str,str]`), `alias_to_original` (`dict[str,str]`), `IMPORT_RESOLVE_CONFIG` (per-language resolution settings).
