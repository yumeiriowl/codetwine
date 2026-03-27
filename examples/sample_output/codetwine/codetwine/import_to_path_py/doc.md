# Design Document: codetwine/import_to_path.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Resolve import statement module names to project-internal file paths, and build symbol-to-file mapping dictionaries that enable downstream analysis to trace which file each imported name originates from.

## 2. When to Use This Module

- **Resolving a single import module to a file path**: Call `resolve_module_to_project_path(module, current_file_rel, project_file_set)` to determine whether an import statement refers to a file within the project, returning that file's relative path or `None` for standard library and external packages.
- **Building a complete symbol-to-file map for a source file**: Call `build_symbol_to_file_map(import_info_list, current_file_rel, project_file_set, file_ext, project_dir)` after extracting imports from a file, to obtain a dict mapping every imported name to its definition file path and a secondary dict of alias-to-original-name mappings.
- **Retrieving language and query parameters before import extraction**: Call `get_import_params(file_ext)` to obtain the tree-sitter `Language` object and import query string needed to run import extraction, or `(None, None)` if the language is unsupported.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `resolve_relative_import` | `module: str`, `separator: str`, `current_dir_part_list: list[str]` | `list[str]` | Convert a relative or absolute import module name into a list of directory path components, handling Python-style (`.`/`..`) and JS/TS-style (`./`, `../`) relative imports. |
| `generate_candidate_path_list` | `base_path: str`, `src_ext_with_dot: str`, `resolve_config: dict`, `current_dir_part_list: list[str]` | `list[str]` | Generate a deduplicated, priority-ordered list of candidate file paths from a base path using declarative per-language resolution config (index files, alternative extensions, etc.). |
| `resolve_module_to_project_path` | `module: str`, `current_file_rel: str`, `project_file_set: set[str]` | `str \| None` | Resolve an import module name to a project-internal file path by converting it to path candidates and matching against `project_file_set`; returns `None` for non-project modules. |
| `build_symbol_to_file_map` | `import_info_list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str` | `tuple[dict[str, str], dict[str, str]]` | Build a `symbol_to_file_map` (imported name → definition file path) and `alias_to_original` (alias → original name) from an import list, applying language-specific registration rules for Python, Java/Kotlin, C/C++, and same-package visibility. |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Return the tree-sitter `Language` object and import query string for the given file extension, or `(None, None)` if the language is unsupported. |

## 4. Design Decisions

- **Declarative language configuration**: `generate_candidate_path_list` and `resolve_module_to_project_path` are entirely driven by `IMPORT_RESOLVE_CONFIG` entries and contain no language-specific branching. All language-specific resolution behavior (index file fallbacks, alternative extensions, current-directory candidates) is expressed through config fields.
- **Three-step resolution pipeline**: `resolve_module_to_project_path` delegates to `resolve_relative_import` (parse module name → path components), `generate_candidate_path_list` (path components → candidate paths), and a set-membership check against `project_file_set`, keeping each concern isolated.
- **Automatic exclusion of non-project modules**: Because resolution is based solely on matching against `project_file_set`, standard library and external package imports are silently excluded without any allowlist or denylist.
- **Same-package visibility for Java/Kotlin**: `build_symbol_to_file_map` additionally registers definitions from all files in the same directory when `SAME_PACKAGE_VISIBLE` is set for the language, reflecting the language's implicit package-scope accessibility without requiring explicit imports.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-level

**`logger`**
- Module-level `logging.Logger` instance obtained via `logging.getLogger(__name__)`.
- Used internally by `_put_symbol` to emit warnings when a symbol mapping is overwritten.

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

**Responsibility:** Converts an import module string into a list of file-system path components, handling both relative and absolute import styles for all supported languages.

**When to use:** Called by `resolve_module_to_project_path` to translate a raw module name into a path component list before candidate file path generation.

**Design decisions:**
- Three distinct dispatch branches based on `separator` value and module prefix:
  - Python-style (separator `"."`, module starts with `"."`): dot-counting determines how many directory levels to ascend.
  - JS/TS-style (separator `"/"`, module starts with `"./"` or `"../"`): delegates normalization to `os.path.normpath` to handle arbitrary `../` sequences.
  - Absolute (all other cases): simple split on `separator`.
- Python dot semantics: one dot = current directory (zero `pop` operations); each additional dot = one additional level up.
- `os.path.normpath` result has backslashes replaced with `/` for cross-platform consistency.

**Constraints & edge cases:**
| Condition | Behavior |
|---|---|
| Python module is only dots (e.g. `"..."`) | `clean_module` is empty; no path components appended |
| `current_dir_part_list` is empty during Python ascent | `pop` is guarded; no error but result may be `[]` |
| Absolute import with separator `"."` | Split by `"."` directly (e.g. `"os.path"` → `["os", "path"]`) |

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
- `src_ext_with_dot`: file extension including the leading dot (e.g. `".py"`, `".ts"`).
- `resolve_config`: a single entry from `IMPORT_RESOLVE_CONFIG` (a dict with keys `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`).
- Returns: ordered list of candidate path strings with duplicates removed.

**Responsibility:** Produces all plausible file paths that an import statement could resolve to, driven entirely by declarative config, with no language-specific branching inside the function body.

**When to use:** Called by `resolve_module_to_project_path` after `resolve_relative_import` to enumerate paths that will be checked against `project_file_set`.

**Design decisions:**

| Config key | Effect when true/non-empty |
|---|---|
| `try_init` | Appends `base_path + "/__init__.py"` (Python packages) |
| `index_ext_list` | Appends `base_path + "/index" + ext` for each ext (JS/TS index files) |
| `alt_ext_list` | Appends `base_path + ext` for each alt extension not equal to `src_ext_with_dot` |
| `try_bare_path` | Appends `base_path` as-is (C/C++ `#include "stdio.h"`) |
| `try_current_dir` | Mirrors all root candidates prefixed with the current directory path |

- **Extension deduplication guard:** if `base_path` already carries a known extension (its `os.path.splitext` suffix is in `alt_ext_list`), neither the same-extension candidate nor alternative-extension candidates are appended, preventing nonsensical paths like `"stdio.h.h"`.
- Duplicate removal preserves insertion order via `dict.fromkeys`.

**Constraints & edge cases:**
- `src_ext_with_dot` extension candidate is generated first (highest priority).
- When `try_current_dir` is enabled and `current_dir_part_list` is empty, `current_dir` becomes `""` and those candidates are silently skipped (falsy check).

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
- `current_file_rel`: project-root-relative path of the file containing the import (e.g. `"src/app/main.py"`).
- `project_file_set`: set of all project-relative file paths.
- Returns: the first matching project-relative file path, or `None`.

**Responsibility:** Acts as the single entry point that converts an import module name into a confirmed project-internal file path by orchestrating the three-step resolution pipeline.

**When to use:** Called both from `build_symbol_to_file_map` (within this module) and from external callers (`usage_analysis.py`, `dependency_graph.py`) when determining whether an import resolves to a project file.

**Design decisions:**
- Returns `None` immediately if no `IMPORT_RESOLVE_CONFIG` entry exists for the file's extension, cleanly excluding unsupported languages.
- Candidates are checked in priority order; the first hit wins.
- Standard library and third-party modules naturally return `None` because their paths don't match any project file.

**Constraints & edge cases:**
- Both `module` values representing project-internal and external modules are accepted; callers need not filter in advance.
- `current_file_rel` must use forward slashes or the `replace("\\", "/")` normalization handles Windows paths.

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
- `symbol_map`: mutable dict mapping symbol name → file path; modified in place.

**Responsibility:** Centralizes symbol registration with a warning when the same name is registered to a different file, preventing silent overwrite bugs.

**When to use:** Called internally whenever a single symbol name needs to be written into `symbol_to_file_map`; not part of the public API.

**Constraints & edge cases:**
- Registering the same name to the same path twice is silent (idempotent).
- Only logs a warning on conflicting paths; the new path wins.

---

## `build_symbol_to_file_map`

**Signature:**
```python
def build_symbol_to_file_map(
    import_info_list,
    current_file_rel: str,
    project_file_set: set[str],
    file_ext: str,
    project_dir: str,
) -> tuple[dict[str, str], dict[str, str]]
```
- `import_info_list`: list of `ImportInfo` objects (as returned by `extract_imports`).
- `file_ext`: extension without leading dot (e.g. `"py"`, `"java"`).
- `project_dir`: absolute path to project root, used to resolve files for definition extraction.
- Returns: `(symbol_to_file_map, alias_to_original)` — both are `dict[str, str]`.

**Responsibility:** Builds the mapping of imported names to their source files so that downstream usage tracking can determine which file a given identifier comes from.

**When to use:** Called from `file_analyzer.py` after import extraction, once per file being analyzed.

**Design decisions:**

| Scenario | Registration strategy |
|---|---|
| `from X import a, b` | Register each name individually |
| `from X import *` | Extract and register all definitions from resolved file |
| `import X as Y` (dot-separator) | Register alias `Y` only |
| `import os.path` (Python, no names) | Register root `os` and leaf `path` |
| `import com.foo.Bar` (Java/Kotlin, no names) | Register leaf `Bar` only (root skipped) |
| `#include "header.h"` (C/C++, separator `/`) | Register all definitions from included file |
| `import com.example.*` (wildcard, unresolvable) | Register all definitions from all files in the package directory |
| Names present (non-wildcard) | Additionally register module root via `setdefault` (non-Java/Kotlin only) |

- **Same-package visibility (Java/Kotlin):** When `SAME_PACKAGE_VISIBLE[file_ext]` is true, definitions from all same-directory, same-extension files are registered without any explicit import statement, mirroring Java/Kotlin language semantics.
- `alias_to_original` is populated directly from `import_info.alias_map` for all resolved imports.
- `setdefault` (not `_put_symbol`) is used for module-root registration when names are present, to avoid overwriting an existing direct-import entry.

**Constraints & edge cases:**
- `import_info_list` type is untyped (`list`) in the signature; elements are expected to have `.module`, `.names`, `.alias_map`, `.module_alias` attributes.
- Java/Kotlin root-part registration is explicitly suppressed to avoid registering meaningless package-root identifiers (`com`, `org`, etc.).

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
- `file_rel`: project-root-relative path of the file to extract definitions from.
- Modifies `symbol_to_file_map` in place; returns `None`.

**Responsibility:** Parses a project file, extracts all named definitions, and registers them into `symbol_to_file_map`, enabling symbol resolution for languages where an import incorporates an entire file (e.g. C/C++ `#include`).

**When to use:** Called internally when a wildcard import or a whole-file-incorporation import (C/C++) is encountered, and when same-package visibility registration is needed.

**Design decisions:**
- Silently returns early if the file does not exist on disk or if no `DEFINITION_DICTS` entry exists for its extension, making it safe to call speculatively.
- Uses `parse_file` (with its module-level cache) to avoid redundant parsing.

**Constraints & edge cases:**
- Only definitions with a non-empty `defn.name` are registered.
- `file_rel` must resolve to an actual file; symlinks or missing files are handled by the `os.path.isfile` guard.

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
- `package_dir`: slash-separated directory path derived from a Java/Kotlin package name (e.g. `"com/example/model"`).
- `file_ext`: extension without dot, used to filter files by language.

**Responsibility:** Handles Java/Kotlin wildcard imports (`import com.example.model.*`) by registering all class definitions from the matching package directory.

**When to use:** Called from `build_symbol_to_file_map` when a wildcard import cannot be resolved to a single file via the normal path.

**Design decisions:**
- Only files **directly** under `package_dir` are processed; sub-package files (those with a `/` in the remainder after the prefix) are explicitly excluded to respect Java package semantics.
- Delegates per-file processing to `_register_definitions_from_file`.

**Constraints & edge cases:**
- `package_dir` must not have a trailing slash; the `prefix = package_dir + "/"` construction depends on this.

---

## `get_import_params`

**Signature:**
```python
def get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]
```
- `file_ext`: file extension without leading dot (e.g. `"py"`, `"java"`).
- Returns: `(Language, import_query_str)` on success, `(None, None)` if the extension is unsupported.
- `Language` is a tree-sitter `Language` object.

**Responsibility:** Provides callers with the two objects required to perform import extraction (a tree-sitter `Language` and the corresponding query string) without exposing direct access to `IMPORT_QUERIES` or `TREE_SITTER_LANGUAGES`.

**When to use:** Called as the first step by `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to determine whether import analysis is feasible for a given file before proceeding.

**Design decisions:**
- Returns `(None, None)` rather than raising an exception for unsupported extensions, allowing callers to use a simple truthiness check to skip processing.
- `TREE_SITTER_LANGUAGES` access uses a `try/except KeyError` rather than `.get()`, consistent with treating a missing language as an unsupported-extension signal.

**Constraints & edge cases:**
- An extension present in `IMPORT_QUERIES` but absent from `TREE_SITTER_LANGUAGES` returns `(None, None)`.
- An extension with a `None` value in `IMPORT_QUERIES` (explicitly unsupported) also returns `(None, None)`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

**`codetwine/import_to_path_py/import_to_path.py` → `codetwine/config/settings.py`**
Multiple configuration dictionaries are imported to drive language-aware logic:
- `IMPORT_RESOLVE_CONFIG` (via `.get`): supplies per-language import resolution settings (separator, extension lists, path resolution flags) used in `resolve_module_to_project_path` and `build_symbol_to_file_map`.
- `SAME_PACKAGE_VISIBLE` (via `.get`): consulted in `build_symbol_to_file_map` to determine whether same-package files should be registered without explicit imports (Java/Kotlin behavior).
- `DEFINITION_DICTS` (via `.get`): provides per-language AST node-type-to-name-strategy mappings used in `_register_definitions_from_file` to extract definition names from included/imported files.
- `IMPORT_QUERIES` (via `.get`): supplies the tree-sitter query string for import extraction, used in `get_import_params`.
- `TREE_SITTER_LANGUAGES`: provides the tree-sitter `Language` object keyed by file extension, used in `get_import_params`.

**`codetwine/import_to_path_py/import_to_path.py` → `codetwine/parsers/ts_parser.py`**
- `parse_file`: called in `_register_definitions_from_file` to parse a source file into a tree-sitter AST (`root_node`) so that definition names can be extracted from it.

**`codetwine/import_to_path_py/import_to_path.py` → `codetwine/extractors/definitions.py`**
- `extract_definitions`: called in `_register_definitions_from_file` to walk the AST returned by `parse_file` and yield `DefinitionInfo` objects, whose `.name` fields are then registered into `symbol_to_file_map`.

---

## Dependents (modules that import this file)

**`codetwine/file_analyzer.py` → `codetwine/import_to_path_py/import_to_path.py`**
Uses this module for two distinct purposes:
- `get_import_params`: retrieves the `Language` object and import query string needed to drive import extraction for a given file extension.
- `build_symbol_to_file_map`: constructs the mapping from imported names to their definition file paths, which is subsequently used during usage tracking within the file analyzer.

**`codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path_py/import_to_path.py`**
Uses this module for:
- `get_import_params`: obtains language and query string parameters before parsing import statements from caller files.
- `resolve_module_to_project_path`: resolves each import's module name to a project-internal file path to determine whether a caller file imports the target file.

**`codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path_py/import_to_path.py`**
Uses this module for:
- `get_import_params`: retrieves language and query string parameters when iterating over all project files to build the dependency graph.
- `resolve_module_to_project_path`: resolves each import statement to a project-internal file path, thereby establishing callee edges in the dependency graph.

---

## Dependency Direction

All relationships are **unidirectional**:

- `import_to_path.py` → `settings.py`: one-way; `settings.py` has no knowledge of `import_to_path.py`.
- `import_to_path.py` → `ts_parser.py`: one-way; `ts_parser.py` has no knowledge of `import_to_path.py`.
- `import_to_path.py` → `definitions.py`: one-way; `definitions.py` has no knowledge of `import_to_path.py`.
- `file_analyzer.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `file_analyzer.py`.
- `usage_analysis.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `usage_analysis.py`.
- `dependency_graph.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `dependency_graph.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `module` | Caller (import statement text) | String, e.g. `"..utils"`, `"./helper"`, `"os"`, `"com.example.Foo"` |
| `current_file_rel` | Caller | Relative file path string from project root, e.g. `"src/app/main.py"` |
| `project_file_set` | Caller | `set[str]` of relative file paths in the project |
| `import_info_list` | Caller (result of `extract_imports`) | List of `ImportInfo` objects with `.module`, `.names`, `.alias_map`, `.module_alias` fields |
| `file_ext` | Caller | Extension string without leading dot, e.g. `"py"`, `"java"`, `"c"` |
| `project_dir` | Caller | Absolute path string to the project root |
| Config dicts | `codetwine/config/settings.py` | `IMPORT_RESOLVE_CONFIG`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `SAME_PACKAGE_VISIBLE`, `TREE_SITTER_LANGUAGES` — all `dict[str, ...]` keyed by file extension |
| File contents | Disk (via `parse_file`) | Raw bytes parsed into tree-sitter AST nodes |

---

## 2. Transformation Overview

### Pipeline A: `resolve_relative_import`

**Input:** raw module string + separator character + current directory path components  
**Stage 1 — Detect import style:** Examines the module string prefix (leading `.` for Python, `./` or `../` for JS/TS) to determine whether the import is relative or absolute.  
**Stage 2 — Build path components:** For Python-style, counts dots to compute directory traversal depth, pops from the current directory list accordingly, then appends the remaining module segments. For JS/TS-style, concatenates current directory and module string, normalizes the path, then splits. For absolute imports, splits directly by the separator.  
**Output:** `list[str]` of path components, e.g. `["src", "utils"]`

---

### Pipeline B: `generate_candidate_path_list`

**Input:** `base_path` string + current file extension + per-language `resolve_config` dict + current directory components  
**Stage 1 — Check existing extension:** Determines whether `base_path` already carries a known extension to prevent double-extension candidates.  
**Stage 2 — Assemble root candidates:** Applies config-driven rules in priority order: same-extension file, Python `__init__.py`, JS/TS index files, alternative extensions, bare path. Each rule is gated by boolean or list fields in `resolve_config`.  
**Stage 3 — Expand with current-directory prefix:** If `try_current_dir` is set, duplicates all root candidates prefixed with the current directory path.  
**Stage 4 — Deduplicate:** Removes duplicates while preserving insertion order.  
**Output:** `list[str]` of candidate relative file paths in priority order

---

### Pipeline C: `resolve_module_to_project_path`

**Input:** module string + current file path + project file set  
**Stage 1 — Config lookup:** Derives file extension, fetches the corresponding `IMPORT_RESOLVE_CONFIG` entry, and extracts the separator.  
**Stage 2 — Delegate to Pipeline A:** Produces path components via `resolve_relative_import`.  
**Stage 3 — Delegate to Pipeline B:** Produces candidate paths via `generate_candidate_path_list`.  
**Stage 4 — Membership check:** Iterates candidates in order and returns the first one present in `project_file_set`.  
**Output:** A single matching relative file path string, or `None`

---

### Pipeline D: `build_symbol_to_file_map`

**Input:** `import_info_list` + current file path + project file set + file extension + project directory  
**Stage 1 — Per-import resolution:** For each `ImportInfo`, calls Pipeline C to resolve the module to a project file path.  
**Stage 2 — Wildcard package expansion (Java/Kotlin):** If the module is unresolvable and contains `*` with a `.` separator, treats the module as a package directory and calls `_register_definitions_from_package`, which scans all matching files in that directory.  
**Stage 3 — Name registration by import form:**  
- `from X import a, b` (names list non-empty): registers each name directly; `*` triggers full-file definition extraction.  
- `import X as Y` (no names, alias present): registers the alias.  
- `import X.Y.Z` (no names, no alias): registers the root segment for Python; registers the leaf segment for Java/Kotlin.  
- `#include` (separator `/`, no names): registers all definitions from the resolved file.  
- When names exist, also registers the module root via `setdefault` for attribute-style access.  
**Stage 4 — Alias map transfer:** Copies `import_info.alias_map` entries into `alias_to_original`.  
**Stage 5 — Same-package visibility (Java/Kotlin):** If `SAME_PACKAGE_VISIBLE` is set for the extension, iterates all project files in the same directory and registers their definitions via `_register_definitions_from_file`.  
**Output:** `(symbol_to_file_map, alias_to_original)` tuple

---

### Sub-pipeline: `_register_definitions_from_file`

**Input:** relative file path + project directory + target `symbol_to_file_map`  
**Stage 1 — File validation:** Builds the absolute path and confirms the file exists.  
**Stage 2 — Parse:** Calls `parse_file` to obtain the AST root node.  
**Stage 3 — Extract definitions:** Calls `extract_definitions` with the language-specific `DEFINITION_DICTS` entry.  
**Stage 4 — Register:** Calls `_put_symbol` for each extracted definition name, writing into `symbol_to_file_map`.  
**Output:** Side-effect mutation of `symbol_to_file_map`

---

### `get_import_params`

**Input:** file extension string  
**Stage 1 — Query lookup:** Fetches the import query string from `IMPORT_QUERIES`.  
**Stage 2 — Language lookup:** Fetches the `Language` object from `TREE_SITTER_LANGUAGES`.  
**Output:** `(Language, import_query_str)` tuple, or `(None, None)` if the extension is unsupported

---

## 3. Outputs

| Output | Function | Format | Consumer |
|---|---|---|---|
| Path components | `resolve_relative_import` | `list[str]` | `resolve_module_to_project_path` |
| Candidate file paths | `generate_candidate_path_list` | `list[str]` (ordered, deduplicated) | `resolve_module_to_project_path` |
| Resolved project file path | `resolve_module_to_project_path` | `str \| None` | `build_symbol_to_file_map`, `usage_analysis.py`, `dependency_graph.py` |
| Symbol-to-file and alias maps | `build_symbol_to_file_map` | `tuple[dict[str, str], dict[str, str]]` | `file_analyzer.py` |
| Mutated `symbol_to_file_map` | `_register_definitions_from_file`, `_register_definitions_from_package` | Side effect on `dict[str, str]` | `build_symbol_to_file_map` |
| Language and query string | `get_import_params` | `tuple[Language, str] \| tuple[None, None]` | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py` |
| Warning log messages | `_put_symbol` | Log output via `logger.warning` | Logging system |

---

## 4. Key Data Structures

### `resolve_config` (entry from `IMPORT_RESOLVE_CONFIG`)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Module path delimiter: `"."` for Python/Java/Kotlin, `"/"` for C/C++/JS/TS |
| `try_init` | `bool` | Whether to try `base_path/__init__.py` as a candidate (Python packages) |
| `index_ext_list` | `list[str]` | Extensions to try as index files (e.g. JS/TS: `[".ts", ".js"]`) |
| `alt_ext_list` | `list[str]` | Alternative file extensions to try beyond the current file's extension |
| `try_bare_path` | `bool` | Whether to include `base_path` as-is without any extension appended |
| `try_current_dir` | `bool` | Whether to also generate candidates relative to the current file's directory |

---

### `symbol_to_file_map`

| Key | Type | Purpose |
|---|---|---|
| Imported or defined name | `str` | Symbol name as it appears in source code (e.g. `"User"`, `"os"`, `"helper"`) |
| Value | `str` | Relative file path where the symbol is defined, e.g. `"src/models/user.py"` |

---

### `alias_to_original`

| Key | Type | Purpose |
|---|---|---|
| Alias name | `str` | The local alias used in the importing file (e.g. `"b"` from `import a as b`) |
| Value | `str` | The original exported name before aliasing (e.g. `"a"`) |

---

### `import_info_list` (elements are `ImportInfo` objects)

| Field | Type | Purpose |
|---|---|---|
| `.module` | `str` | The module path as written in the import statement |
| `.names` | `list[str]` | Individual names imported (`from X import a, b`); empty for bare imports; `["*"]` for wildcard |
| `.alias_map` | `dict[str, str] \| None` | Mapping of `alias -> original` for named aliases in the import |
| `.module_alias` | `str \| None` | Alias for the whole module (`import X as Y`) |

---

### `candidate_path_list` (output of `generate_candidate_path_list`)

| Position | Type | Purpose |
|---|---|---|
| Earlier entries | `str` | Higher-priority candidates (same extension, `__init__.py`, index files) |
| Later entries | `str` | Lower-priority candidates (alternative extensions, bare path, current-dir variants) |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** strategy throughout. No operation raises an exception to the caller; instead, unresolvable inputs return `None` or empty collections, and non-critical anomalies are logged as warnings. The pipeline is designed to process as many files and symbols as possible, silently skipping anything that cannot be resolved rather than halting execution.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension | `IMPORT_RESOLVE_CONFIG` has no entry for the current file's extension | Return `None` (in `resolve_module_to_project_path`) or `(None, None)` (in `get_import_params`) | Yes | The caller skips import analysis for that file |
| Module not resolvable to a project file | No generated candidate path matches any entry in `project_file_set` | Return `None`; the symbol is silently omitted from `symbol_to_file_map` | Yes | External/stdlib modules are excluded; no project-internal reference is registered |
| Symbol name collision (duplicate symbol) | A symbol name already in `symbol_to_file_map` is being overwritten by a different file path | `logger.warning(...)` emitted; the new path overwrites the old one | Yes | Last writer wins; a warning is emitted but processing continues |
| Referenced file does not exist on disk | `os.path.isfile(abs_path)` returns `False` in `_register_definitions_from_file` | Early return; nothing is registered | Yes | Definitions from that file are not added to the symbol map |
| No definition dict for a file's extension | `DEFINITION_DICTS.get(resolved_ext)` returns `None` in `_register_definitions_from_file` | Early return; nothing is registered | Yes | Definitions from that file are not added to the symbol map |
| Unsupported language for import queries | `IMPORT_QUERIES` or `TREE_SITTER_LANGUAGES` has no entry for `file_ext` in `get_import_params` | Return `(None, None)` | Yes | Caller skips import extraction for that file |
| Missing `TREE_SITTER_LANGUAGES` key | `KeyError` on `TREE_SITTER_LANGUAGES[file_ext]` in `get_import_params` | `except KeyError` catches it; return `(None, None)` | Yes | Import analysis is skipped for that extension |
| Java/Kotlin wildcard import unresolvable to single file | `resolve_module_to_project_path` returns `None` and `*` is in `import_info.names` | Falls back to `_register_definitions_from_package` using the module name as a directory path | Yes | Definitions from matching package files are registered via the fallback path |

---

## 3. Design Notes

- **`None`-as-sentinel pattern**: Functions that cannot produce a meaningful result return `None` (or a `(None, None)` tuple) rather than raising exceptions. This places the responsibility of checking results on the caller and allows each step of the pipeline to be skipped independently.

- **Warning-only on symbol collision**: Overwriting a symbol in `symbol_to_file_map` is considered non-fatal because it may arise from legitimate cases such as re-exports or aliasing. A `logger.warning` preserves visibility without interrupting the build of the map.

- **Filesystem checks before parsing**: `_register_definitions_from_file` explicitly verifies file existence before invoking `parse_file`, preventing parse errors from propagating and keeping the parse cache clean.

- **No exception propagation**: The single explicit `try/except` block in `get_import_params` catches only `KeyError` from a dictionary lookup. All other potential failures (missing config keys, unresolvable paths) are guarded by `.get()` with safe defaults rather than exception handling, consistent with the file's overall defensive style.

## Summary

**import_to_path.py** resolves import statements to project-internal file paths and builds symbol-to-file mappings for downstream analysis.

**Public functions:**
- `resolve_relative_import(module:str, separator:str, current_dir_part_list:list[str]) → list[str]`
- `generate_candidate_path_list(base_path:str, src_ext_with_dot:str, resolve_config:dict, current_dir_part_list:list[str]) → list[str]`
- `resolve_module_to_project_path(module:str, current_file_rel:str, project_file_set:set[str]) → str|None`
- `build_symbol_to_file_map(import_info_list, current_file_rel:str, project_file_set:set[str], file_ext:str, project_dir:str) → tuple[dict[str,str], dict[str,str]]`
- `get_import_params(file_ext:str) → tuple[Language,str]|tuple[None,None]`

**Key structures:** `symbol_to_file_map` (name→file path), `alias_to_original` (alias→original name), `project_file_set:set[str]`.
