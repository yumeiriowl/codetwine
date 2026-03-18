# Design Document: codetwine/import_to_path.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Resolves import statement module names to project-internal file paths and builds symbol-to-file mapping tables that enable downstream analysis modules to trace which file each imported name originates from.

## 2. When to Use This Module

- **Resolving a single import to a file path**: Call `resolve_module_to_project_path(module, current_file_rel, project_file_set)` to convert a raw module string (e.g. `"..utils"`, `"./helper"`, `"com.example.Bar"`) into a relative project file path, or `None` if the module is external (stdlib, third-party).
- **Building a symbol-to-file map for a file's imports**: Call `build_symbol_to_file_map(import_info_list, current_file_rel, project_file_set, file_ext, project_dir)` after extracting import statements to get a dict mapping each imported name to the project file that defines it, plus a dict of alias-to-original-name mappings. Used by `file_analyzer.py` to support usage tracking.
- **Retrieving parser parameters for import extraction**: Call `get_import_params(file_ext)` to obtain the tree-sitter `Language` object and import query string needed before parsing import statements in a file. Returns `(None, None)` for unsupported languages. Used by `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `resolve_relative_import` | `module: str`, `separator: str`, `current_dir_part_list: list[str]` | `list[str]` | Converts a relative or absolute import module name into a list of directory path components, handling Python-style (`..`) and JS/TS-style (`./`, `../`) relative imports. |
| `generate_candidate_path_list` | `base_path: str`, `src_ext_with_dot: str`, `resolve_config: dict`, `current_dir_part_list: list[str]` | `list[str]` | Generates an ordered, deduplicated list of candidate file paths from a base path using declarative per-language config rules (index files, alternative extensions, bare paths, current-directory lookup). |
| `resolve_module_to_project_path` | `module: str`, `current_file_rel: str`, `project_file_set: set[str]` | `str \| None` | Resolves an import module name to a project-internal file path by composing `resolve_relative_import` and `generate_candidate_path_list`, then matching against `project_file_set`. Returns `None` for external modules. |
| `build_symbol_to_file_map` | `import_info_list: list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str` | `tuple[dict[str, str], dict[str, str]]` | Builds a `symbol_to_file_map` (imported name → definition file path) and `alias_to_original` (alias → original name) by resolving all imports and applying language-specific registration rules (Python root/leaf, Java class name, C/C++ full-file inclusion, wildcard imports, same-package visibility). |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Returns the tree-sitter `Language` object and import query string for a given file extension, or `(None, None)` if the extension is unsupported. |

## 4. Design Decisions

- **Declarative, language-agnostic candidate generation**: `generate_candidate_path_list` contains no language-specific conditional branches. All language differences (index files, alternative extensions, `__init__.py`, bare paths) are expressed through the `IMPORT_RESOLVE_CONFIG` dict, making the function extensible to new languages without code changes.
- **Three-step resolution pipeline**: `resolve_module_to_project_path` explicitly separates the resolution concern into three stages—relative import parsing, candidate generation, and set membership check—each delegated to a dedicated function, allowing each stage to be tested and reused independently (as `usage_analysis.py` and `dependency_graph.py` each call `resolve_module_to_project_path` directly).
- **External module filtering by absence**: The function does not maintain any allowlist or denylist of stdlib/external package names. Instead, it relies on the fact that non-project modules produce no candidate matching `project_file_set`, naturally returning `None` for them.
- **Same-package visibility**: For languages where `SAME_PACKAGE_VISIBLE` is set (Java/Kotlin), `build_symbol_to_file_map` automatically registers definitions from all same-directory files of the same extension, reflecting the language's implicit same-package accessibility without requiring explicit import statements.

## Definition Design Specifications

# Definition Design Specifications

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

**Responsibility:** Converts an import statement's module string into a list of filesystem path components, handling both relative and absolute import syntaxes for Python (dot-prefixed) and JS/TS (slash-prefixed).

**When to use:** Called by `resolve_module_to_project_path` as the first step when transforming any module name into a base path for candidate file generation.

**Design decisions:**

| Scenario | Behavior |
|---|---|
| Python relative (`..utils`) | Counts leading dots; 1 dot = current dir, each additional dot pops one component from `current_dir_part_list` |
| JS/TS relative (`./x`, `../x`) | Joins `current_dir_part_list` with the module string and applies `os.path.normpath` to resolve `../` sequences, then splits on `/` |
| Absolute import (`os`, `com.example.Foo`) | Splits directly by `separator` with no directory manipulation |

**Constraints & edge cases:**
- Python: A module string of only dots (e.g. `"..."`) produces an empty `clean_module`, resulting in path components equal to the ancestor directory only.
- JS/TS: `os.path.normpath` on Windows may produce backslashes; these are explicitly replaced with `/` before splitting.
- If `current_dir_part_list` is empty during Python relative traversal, `pop()` calls are silently skipped via the `if path_part_list:` guard.
- Absolute imports with a separator not matching the module format produce semantically incorrect but syntactically valid splits; correctness depends on the caller providing an appropriate `separator`.

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

**Responsibility:** Produces an ordered, deduplicated list of candidate file paths from a base path by applying language-specific resolution rules declared in `resolve_config`, without containing any language-specific conditional branches.

**When to use:** Called by `resolve_module_to_project_path` after `resolve_relative_import` has produced `base_path`, to enumerate all plausible file locations for a given import.

**Design decisions:**

| `resolve_config` field | Type | Effect when truthy/non-empty |
|---|---|---|
| `try_init` | `bool` | Appends `base_path + "/__init__.py"` (Python packages) |
| `index_ext_list` | `list[str]` | Appends `base_path + "/index" + ext` for each ext (JS/TS index files) |
| `alt_ext_list` | `list[str]` | Appends `base_path + ext` for each ext not equal to `src_ext_with_dot` |
| `try_bare_path` | `bool` | Appends `base_path` as-is (C/C++ `#include "stdio.h"`) |
| `try_current_dir` | `bool` | For each root candidate, also appends a version prefixed with `current_dir` |

- **Extension deduplication guard:** If `base_path` already ends with a known extension from `alt_ext_list` (detected via `os.path.splitext`), all extension-appending steps are skipped to prevent nonsensical candidates like `stdio.h.h`.
- Final deduplication uses `dict.fromkeys` to preserve insertion order.

**Constraints & edge cases:**
- The candidate with the same extension as the current file is always added first (if `has_known_ext` is false), giving it highest priority.
- `try_current_dir` doubles the candidate count; if `current_dir` is empty, the prefix `"" + "/"` creates a leading slash, so the `if current_dir:` guard prevents this.

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
*Returns either a project-relative file path string or `None`.*

**Responsibility:** Determines whether an import module name refers to a file within the project by generating candidate paths and checking them against `project_file_set`; returns the first matching path or `None` for standard library/external modules.

**When to use:** Called whenever a module name from an import statement needs to be resolved to a concrete project file — used by `build_symbol_to_file_map`, and also called directly by `usage_analysis.py` and `dependency_graph.py`.

**Design decisions:**
- Delegates all logic to `resolve_relative_import` and `generate_candidate_path_list`, keeping this function as a thin three-step coordinator.
- Returns `None` immediately if no `resolve_config` exists for the file extension, allowing unsupported languages to be handled gracefully.
- Candidate list order is respected: the first matching candidate in `project_file_set` wins.

**Constraints & edge cases:**
- `project_file_set` must use `/`-separated paths (not OS-native separators) consistent with the candidates generated internally.
- Non-project modules (stdlib, third-party) produce candidates that never match `project_file_set`, yielding `None` without error.

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

**Responsibility:** Provides a single, consistent insertion point for writing entries into a symbol map, emitting a `logger.warning` when an existing entry for the same name points to a different file.

**When to use:** Called by `build_symbol_to_file_map` and `_register_definitions_from_file` whenever a symbol name and its source file need to be recorded, rather than writing to the dict directly.

**Design decisions:**
- Warns only on conflicting overwrites (different `path`); re-registering the same path for the same name is silently accepted.
- Uses module-level `logger`, so warning visibility is controlled by the calling application's logging configuration.

**Constraints & edge cases:**
- Does not raise exceptions on conflict; the last write wins.
- `name` is not validated; empty strings or `None` are accepted without error.

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
*Returns a 2-tuple: `(symbol_to_file_map, alias_to_original)` where both values are `dict[str, str]`.*

**Responsibility:** Builds a complete mapping of imported symbol names to their definition files for a single source file, enabling downstream usage tracking to know which file each referenced name originates from.

**When to use:** Called once per analyzed file in `file_analyzer.py` after import extraction, providing the symbol resolution context needed for usage analysis.

**Design decisions:**

Language-specific symbol registration strategy (no language-specific branches in the loop; behavior driven by `separator` and `file_ext`):

| Condition | Action |
|---|---|
| `names` is non-empty, name is `"*"` | Registers all definitions from the resolved file via `_register_definitions_from_file` |
| `names` is non-empty, specific names | Registers each name individually via `_put_symbol` |
| `names` empty, `separator == "."`, `module_alias` set | Registers the alias |
| `names` empty, `separator == "."`, no alias, not Java/Kotlin | Registers module root (first part) |
| `names` empty, `separator == "."`, any language | Registers module leaf (last part) if different from root |
| `names` empty, `separator == "/"` | Registers all definitions from file (C/C++ `#include` semantics) |
| Wildcard `"*"` in names, `separator == "."`, unresolved module | Treats module as a package dir and delegates to `_register_definitions_from_package` |
| `SAME_PACKAGE_VISIBLE[file_ext]` is true | After the loop, registers definitions from all same-directory, same-extension files |

- When `names` is non-empty, the module root is also registered with `setdefault` to support attribute-style access (`mymodule.func()`), without overwriting a directly imported entry.
- `alias_to_original` is accumulated directly from `import_info.alias_map`.

**Constraints & edge cases:**
- `import_info_list` is typed as an unparameterized list; callers are expected to pass `list[ImportInfo]` objects returned by `extract_imports`.
- Java/Kotlin (`file_ext in ("java", "kt")`) skip module-root registration to avoid registering meaningless package prefixes like `com` or `org`.
- Same-package visibility registration only occurs if `SAME_PACKAGE_VISIBLE.get(file_ext)` is truthy; for all other languages the post-loop block is skipped.

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

**Responsibility:** Parses a project file and registers every named definition it contains into `symbol_to_file_map`, enabling bulk symbol registration for `#include`, `from X import *`, and same-package scenarios.

**When to use:** Called by `build_symbol_to_file_map` and `_register_definitions_from_package` whenever all symbols from a file must be made available without enumerating them individually.

**Design decisions:**
- Silently returns early if the file does not exist on disk or if no `definition_dict` is configured for its extension, making it safe to call on any path without precondition checks.
- Uses `parse_file` (which is cached) and `extract_definitions` from the dependency layer, keeping parsing and AST traversal logic outside this module.

**Constraints & edge cases:**
- `file_rel` must be a project-root-relative path; combined with `project_dir` to form the absolute path.
- Only definitions with a non-empty `defn.name` are registered.

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

**Responsibility:** Handles Java/Kotlin wildcard imports (`import com.example.model.*`) by registering definitions from every file of the matching extension directly under the specified package directory.

**When to use:** Called by `build_symbol_to_file_map` specifically when a wildcard import cannot be resolved to a single file and the separator is `"."`.

**Design decisions:**
- Limits registration to files directly under `package_dir` (no recursive descent into sub-packages) by checking that the remainder after the prefix contains no `/`.
- Delegates the actual definition extraction to `_register_definitions_from_file` for each matching file.

**Constraints & edge cases:**
- `package_dir` must use `/` separators (converted from `.`-separated Java package names by the caller).
- Files in sub-packages are explicitly excluded; this matches Java's per-package import semantics.
- Extension matching uses `os.path.splitext` with `lstrip(".")`, so it is robust against leading-dot artifacts.

---

## `get_import_params`

**Signature:**
```python
def get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]
```
*Returns either a 2-tuple of `(tree-sitter Language object, import query string)` or `(None, None)`.*

**Responsibility:** Provides a single lookup point that retrieves both the tree-sitter `Language` object and the import query string required to perform import extraction for a given file extension.

**When to use:** Called at the start of any import analysis pipeline in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to determine whether a file's language is supported before attempting to parse imports.

**Design decisions:**
- Returns `(None, None)` rather than raising an exception for unsupported extensions, allowing callers to use a simple `if language and import_query_str:` guard.
- The two lookups (`IMPORT_QUERIES` and `TREE_SITTER_LANGUAGES`) are performed separately; missing `import_query_str` short-circuits before attempting the `Language` lookup.

**Constraints & edge cases:**
- If `IMPORT_QUERIES` has an entry for an extension but `TREE_SITTER_LANGUAGES` does not, the `KeyError` is caught and `(None, None)` is returned.
- An extension whose `import_query` is explicitly `None` in the registry returns `(None, None)` because `IMPORT_QUERIES.get` returns `None` and the early-return triggers.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/config/settings.py` : Retrieves per-language import resolution configuration (`IMPORT_RESOLVE_CONFIG`), definition dictionaries (`DEFINITION_DICTS`), import query strings (`IMPORT_QUERIES`), tree-sitter language objects (`TREE_SITTER_LANGUAGES`), and same-package visibility flags (`SAME_PACKAGE_VISIBLE`) needed to resolve module names to file paths and register imported symbols.

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into tree-sitter AST root nodes when extracting definition names from resolved dependency files (e.g., for C/C++ `#include` targets and Java/Kotlin wildcard imports).

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/extractors/definitions.py` : Uses `extract_definitions` to enumerate all named definitions from a parsed AST, enabling registration of those definition names into the symbol-to-file map when an entire file's contents are incorporated (e.g., via `#include` or `import *`).

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/import_to_path_py/import_to_path.py` : Uses `get_import_params` to obtain the tree-sitter `Language` object and import query string for a given file extension, and uses `build_symbol_to_file_map` to produce the `symbol_to_file_map` and `alias_to_original` dictionaries that map imported names to their definition file paths during per-file analysis.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path_py/import_to_path.py` : Uses `get_import_params` to retrieve import extraction parameters for caller files, and uses `resolve_module_to_project_path` to check whether a caller's import statement resolves to the target file being analyzed.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path_py/import_to_path.py` : Uses `get_import_params` to obtain language and query parameters for each file in the project, and uses `resolve_module_to_project_path` to resolve each import statement's module name to a project-internal file path when building the project-wide dependency graph.

## Dependency Direction

All relationships are **unidirectional**:

- `import_to_path.py` → `codetwine/config/settings.py`: one-way; `settings.py` has no dependency on `import_to_path.py`.
- `import_to_path.py` → `codetwine/parsers/ts_parser.py`: one-way; `ts_parser.py` has no dependency on `import_to_path.py`.
- `import_to_path.py` → `codetwine/extractors/definitions.py`: one-way; `definitions.py` has no dependency on `import_to_path.py`.
- `codetwine/file_analyzer.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `file_analyzer.py`.
- `codetwine/extractors/usage_analysis.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `usage_analysis.py`.
- `codetwine/extractors/dependency_graph.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `dependency_graph.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `module` | Caller (import statement extracted from AST) | String (e.g. `"..utils"`, `"./helper"`, `"os"`, `"com.example.Foo"`) |
| `current_file_rel` | Caller | Relative path string from project root (e.g. `"src/app/main.py"`) |
| `project_file_set` | Caller | `set[str]` of project-relative file paths |
| `import_info_list` | Caller (output of `extract_imports`) | List of `ImportInfo` objects carrying `.module`, `.names`, `.alias_map`, `.module_alias` |
| `file_ext` | Caller | String extension without dot (e.g. `"py"`, `"java"`, `"c"`) |
| `project_dir` | Caller | Absolute path string to the project root |
| `IMPORT_RESOLVE_CONFIG` | `codetwine/config/settings.py` | `dict[str, dict]` keyed by extension; each value holds `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | `dict[str, dict[str, str]]` keyed by extension; maps AST node types to name-extraction strategies |
| `IMPORT_QUERIES` | `codetwine/config/settings.py` | `dict[str, str|None]` keyed by extension; tree-sitter query strings |
| `TREE_SITTER_LANGUAGES` | `codetwine/config/settings.py` | `dict[str, Language]` keyed by extension |
| `SAME_PACKAGE_VISIBLE` | `codetwine/config/settings.py` | `dict[str, bool]` keyed by extension |
| File bytes (on demand) | Filesystem via `parse_file` | Binary content of source files, parsed into tree-sitter AST nodes |

---

## 2. Transformation Overview

### Stage 1 — Module name → path component list (`resolve_relative_import`)

A raw module string (e.g. `"..utils"`, `"./helper"`, `"os.path"`) is examined against the language separator (`"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++`). For relative imports the current file's directory component list is used as a starting anchor, navigating up one level per extra dot (Python) or applying `os.path.normpath` (JS/TS). The output is a `list[str]` of path components ready to be joined.

### Stage 2 — Path components → candidate file paths (`generate_candidate_path_list`)

The component list is joined into `base_path`. Language-specific rules from `IMPORT_RESOLVE_CONFIG` are applied declaratively: the current file's extension is tried first, then `__init__.py` (Python packages), then directory index files (JS/TS), then alternative extensions, and optionally the bare path (C/C++). If `try_current_dir` is set, each root candidate is also prefixed with the current directory. Duplicates are removed while preserving priority order, yielding `list[str]` of candidate paths.

### Stage 3 — Candidate paths → resolved project path (`resolve_module_to_project_path`)

Each candidate path is checked for membership in `project_file_set`. The first match is returned as the resolved path. If no candidate matches, `None` is returned, which causes the module to be treated as external (standard library or third-party package) and silently skipped.

### Stage 4 — Resolved paths → symbol-to-file map (`build_symbol_to_file_map`)

Each `ImportInfo` in `import_info_list` is fed through Stage 1–3. Depending on the result and the import form, different registration strategies apply:

- **Resolved + named imports** (`from X import a, b`): each name is registered directly into `symbol_to_file_map`.
- **Resolved + wildcard** (`from X import *`): `_register_definitions_from_file` is called to parse the resolved file's AST and register all its definition names.
- **Resolved + no names** (bare `import X` or `#include`): the module root and/or leaf are registered by separator type; for `/`-separator languages the entire included file's definitions are registered.
- **Unresolved + wildcard + dot separator** (Java/Kotlin `import pkg.*`): `_register_definitions_from_package` scans `project_file_set` for files directly under the package directory and calls `_register_definitions_from_file` for each.
- **Alias mappings**: `import_info.alias_map` entries are copied into `alias_to_original`.
- **Same-package visibility** (Java/Kotlin): after all imports are processed, files sharing the same directory and extension as the current file are scanned and their definitions added to `symbol_to_file_map`.

### Stage 5 — Definition extraction sub-pipeline (on demand)

When `_register_definitions_from_file` is called, the target file is parsed via `parse_file` (cached), and `extract_definitions` traverses the AST using the `DEFINITION_DICTS` entry for that extension. Each `DefinitionInfo.name` produced is written into `symbol_to_file_map` via `_put_symbol`, which logs a warning if a name is being mapped to a different file than previously recorded.

---

## 3. Outputs

| Output | Returned By | Format |
|---|---|---|
| Resolved project-relative path | `resolve_module_to_project_path` | `str` (e.g. `"src/utils.py"`) or `None` |
| Symbol-to-file map | `build_symbol_to_file_map` | `dict[str, str]` — imported name → project-relative file path |
| Alias-to-original map | `build_symbol_to_file_map` | `dict[str, str]` — alias name → original name |
| Language + query pair | `get_import_params` | `tuple[Language, str]` or `tuple[None, None]` |
| Path component list | `resolve_relative_import` | `list[str]` |
| Candidate path list | `generate_candidate_path_list` | `list[str]`, deduplicated, priority-ordered |
| Warning log entries | `_put_symbol` (side effect) | Log messages via `logger.warning` when a symbol's source file is overwritten |

No files are written by this module.

---

## 4. Key Data Structures

### `IMPORT_RESOLVE_CONFIG` entry (per-extension resolve config dict)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Module name delimiter (`"."` or `"/"`) |
| `try_init` | `bool` | Whether to try `base_path/__init__.py` as a candidate |
| `index_ext_list` | `list[str]` | Extensions to try as directory index files (e.g. `[".ts", ".js"]`) |
| `alt_ext_list` | `list[str]` | Alternative extensions to append to `base_path` |
| `try_bare_path` | `bool` | Whether to try `base_path` without any appended extension |
| `try_current_dir` | `bool` | Whether to also generate candidates relative to the current file's directory |

### `symbol_to_file_map`

| Key | Value Type | Purpose |
|---|---|---|
| Imported or defined symbol name (`str`) | `str` | Project-relative path of the file where the symbol is defined |

### `alias_to_original`

| Key | Value Type | Purpose |
|---|---|---|
| Alias name as used in the importing file (`str`) | `str` | Original name before aliasing (from `import_info.alias_map`) |

### `ImportInfo` fields consumed by this module

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The raw module string from the import statement |
| `names` | `list[str]` | Explicitly imported names (empty for bare imports; `["*"]` for wildcard) |
| `alias_map` | `dict[str, str]` | Maps alias → original for `from X import a as b` forms |
| `module_alias` | `str \| None` | Alias for the whole module (`import X as Y`) |

### Candidate path list (output of `generate_candidate_path_list`)

| Position | Type | Purpose |
|---|---|---|
| First entries | `str` | Same-extension candidates, highest priority |
| Middle entries | `str` | `__init__.py`, index files, alternative extensions |
| Last entries | `str` | Bare path and/or current-directory-relative variants |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file follows a **graceful degradation / logging-and-continue** strategy throughout. No exceptions are raised to callers; instead, unresolvable states are signaled via `None` return values or silent skips. The single explicit error-reporting mechanism is `logger.warning(...)` for symbol-map conflicts. The design prioritizes partial results over hard failures: if one import cannot be resolved, the remainder of the analysis proceeds unaffected.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension (resolve config missing) | `IMPORT_RESOLVE_CONFIG.get(src_ext)` returns `None` in `resolve_module_to_project_path` | Returns `None` immediately | Yes – caller skips this import | The module is not resolved; treated as external/stdlib |
| Unsupported file extension (import query missing) | `IMPORT_QUERIES.get(file_ext)` returns falsy in `get_import_params` | Returns `(None, None)` | Yes – caller skips import analysis | No import analysis performed for the file |
| Unsupported file extension (Language object missing) | `TREE_SITTER_LANGUAGES[file_ext]` raises `KeyError` in `get_import_params` | `KeyError` caught; returns `(None, None)` | Yes – caller skips import analysis | No import analysis performed for the file |
| Module not resolvable to a project file | No candidate path from `generate_candidate_path_list` matches `project_file_set` | Returns `None` from `resolve_module_to_project_path` | Yes – treated as external dependency | Import is silently ignored; standard library and third-party packages are excluded this way |
| Target file does not exist on disk | `os.path.isfile(abs_path)` is `False` in `_register_definitions_from_file` | Returns immediately with no action | Yes – function exits early | No definitions are registered from the missing file |
| No definition dict for a file extension | `DEFINITION_DICTS.get(resolved_ext)` returns `None` in `_register_definitions_from_file` | Returns immediately with no action | Yes – function exits early | No definitions are registered from that file |
| Symbol name collision (different source file) | `_put_symbol` detects an existing entry with a different path | Logs a `WARNING` and overwrites with the new path | Yes – last writer wins | Earlier mapping is lost; a warning is emitted to aid debugging |
| Wildcard import not resolvable to a single file | `resolve_module_to_project_path` returns `None` and `"*"` is in `import_info.names` with `.`-separated imports | Falls back to `_register_definitions_from_package` over the package directory | Yes – package-level scan attempted | Definitions from all matching files in the directory are registered instead |

---

## 3. Design Notes

- **`None` as the universal "not found" signal.** Both `resolve_module_to_project_path` and `get_import_params` use `None` / `(None, None)` as their failure sentinel, keeping callers responsible for deciding whether to skip or substitute. This avoids exception propagation across module boundaries.

- **Logging instead of raising for data conflicts.** Symbol-map overwrites in `_put_symbol` emit a `WARNING` rather than raising an exception because overwrites can legitimately arise from wildcard imports, same-package visibility, or re-exports. The warning provides observability without interrupting analysis.

- **Early-return guards in helpers.** `_register_definitions_from_file` uses sequential guard clauses (file existence, extension support) to exit cleanly, ensuring that infrastructure gaps (missing parsers, unsupported extensions) do not surface as exceptions in callers that iterate over many files.

- **`KeyError` is the only explicitly caught exception** (in `get_import_params`), reflecting that dictionary key absence is the only anticipated runtime fault in this module. All other failure modes are handled by checking return values before proceeding.

## Summary

Resolves import module strings to project file paths and builds symbol-to-file mapping tables.

**Public functions:**
- `resolve_module_to_project_path(module:str, current_file_rel:str, project_file_set:set[str]) → str|None`
- `build_symbol_to_file_map(import_info_list:list, current_file_rel:str, project_file_set:set[str], file_ext:str, project_dir:str) → tuple[dict[str,str], dict[str,str]]`
- `get_import_params(file_ext:str) → tuple[Language,str]|tuple[None,None]`

**Key structures:** `symbol_to_file_map` (name→file path), `alias_to_original` (alias→original name), `IMPORT_RESOLVE_CONFIG` (per-extension resolution rules).
