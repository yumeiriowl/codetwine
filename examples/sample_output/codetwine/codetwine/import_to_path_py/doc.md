# Design Document: codetwine/import_to_path.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Resolve import statement module names to project-internal file paths and build the symbol-to-file mapping used to trace which imported name originates from which file.

## 2. When to Use This Module

- **Resolving a single import to a file path**: Call `resolve_module_to_project_path(module, current_file_rel, project_file_set)` to convert a module name from an import statement into a relative project file path, or `None` if the module is a standard library or external package.
- **Building a full symbol map for a file**: Call `build_symbol_to_file_map(import_info_list, current_file_rel, project_file_set, file_ext, project_dir)` to produce a `{symbol_name: file_path}` dict and an alias mapping, used during usage tracking to determine which file each referenced name comes from.
- **Retrieving language-specific import analysis parameters**: Call `get_import_params(file_ext)` to obtain the tree-sitter `Language` object and the import query string needed to extract import statements from a source file, before calling `extract_imports`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `resolve_relative_import` | `module: str`, `separator: str`, `current_dir_part_list: list[str]` | `list[str]` | Converts a relative or absolute import module name into a list of directory path components, handling Python-style (`.`/`..`) and JS/TS-style (`./`, `../`) relative imports. |
| `generate_candidate_path_list` | `base_path: str`, `src_ext_with_dot: str`, `resolve_config: dict`, `current_dir_part_list: list[str]` | `list[str]` | Generates a deduplicated, priority-ordered list of candidate file paths from a base path using language-specific resolution rules declared in `IMPORT_RESOLVE_CONFIG`. |
| `resolve_module_to_project_path` | `module: str`, `current_file_rel: str`, `project_file_set: set[str]` | `str \| None` | Resolves an import module name to a matching project-internal file path by running candidate generation and matching against the project file set; returns `None` for non-project modules. |
| `build_symbol_to_file_map` | `import_info_list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str` | `tuple[dict[str, str], dict[str, str]]` | Builds a `{imported_name: file_path}` dict and an `{alias: original_name}` dict from a list of parsed import statements, applying language-specific registration logic for Python, Java/Kotlin, and C/C++. |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Returns the tree-sitter `Language` object and import query string for the given file extension; returns `(None, None)` for unsupported languages. |

## 4. Design Decisions

- **Declarative language configuration**: `generate_candidate_path_list` contains no language-specific branching. All per-language resolution rules (index files, alternative extensions, `__init__.py`, current-directory fallback) are expressed as config fields in `IMPORT_RESOLVE_CONFIG`, keeping the resolution logic language-agnostic.
- **Three-step resolution pipeline**: `resolve_module_to_project_path` is intentionally decomposed into three delegated steps—relative import parsing, candidate generation, and project-set matching—each isolated in its own function to allow independent testing and reuse by external modules such as `dependency_graph.py` and `usage_analysis.py`.
- **Exclusion by absence**: Standard library and external package modules are excluded implicitly because none of their candidate paths will match any entry in `project_file_set`, requiring no explicit allowlist or denylist.
- **Same-package visibility**: `build_symbol_to_file_map` automatically registers definitions from same-directory files for languages where `SAME_PACKAGE_VISIBLE` is set (Java/Kotlin), modeling the language's implicit intra-package visibility without requiring explicit import statements.

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

**Responsibility:** Converts an import module string into a list of filesystem path components, handling both relative and absolute import syntaxes. Acts as the canonical translation layer between language-level module names and directory-relative path fragments.

**When to use:** Called by `resolve_module_to_project_path` whenever a module name must be converted into path components before candidate file paths are generated.

**Design decisions:**
- Python-style relative imports (leading dots): dot count determines traversal depth. One dot = current directory (zero pops); two dots = one level up (one pop); and so on.
- JS/TS-style relative imports (`./` or `../`): delegated to `os.path.normpath` for correct multi-segment traversal, with backslash normalization afterward.
- Absolute imports (no leading dots or slashes): split mechanically by the separator character without any directory context.

**Constraints & edge cases:**
- `separator` is expected to be either `"."` (Python, Java, Kotlin) or `"/"` (JS/TS, C/C++).
- For Python relative imports, if `current_dir_part_list` is shorter than the traversal depth, `pop()` calls stop at an empty list rather than raising an error.
- A module string consisting entirely of dots (e.g. `".."`) produces a `clean_module` of `""`, and no extension is appended.

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

**Responsibility:** Expands a single `base_path` string into an ordered list of all plausible file paths that the import might resolve to, according to declarative per-language rules in `resolve_config`. Eliminates language-specific branching by reading behavior from configuration.

**When to use:** Called by `resolve_module_to_project_path` after `resolve_relative_import` has produced a `base_path`, to enumerate every file the import could reference.

**Design decisions:**
- **Extension collision guard:** If `base_path` already ends with one of the `alt_ext_list` extensions (detected via `os.path.splitext`), neither the same-extension candidate nor alternative-extension candidates are appended, preventing nonsense paths like `stdio.h.h`.
- **Priority order:** Same-extension file → `__init__.py` (Python) → index files (JS/TS) → alternative extensions → bare path → current-directory-relative variants. This ordering encodes language-conventional resolution priority.
- **Deduplication:** A `dict.fromkeys` pass removes duplicates while preserving insertion order.
- All behavior is controlled by config fields (`try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`); no language name is referenced inside the function.

**Constraints & edge cases:**
- `src_ext_with_dot` must include the leading dot (e.g. `".py"`).
- If `current_dir_part_list` is empty and `try_current_dir` is `True`, the current-directory prefix is omitted.

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
`project_file_set` is a set of strings representing relative paths of every file in the project. Returns a single relative path string, or `None`.

**Responsibility:** Determines whether an import module name refers to a file within the project and, if so, returns its relative path. Serves as the public resolution entry point for both dependency-graph construction and import-to-symbol mapping.

**When to use:** Called from `build_symbol_to_file_map`, `codetwine/extractors/usage_analysis.py`, and `codetwine/extractors/dependency_graph.py` whenever a raw module string from an import statement must be matched to a project file.

**Design decisions:**
- Returns `None` for modules with no matching `IMPORT_RESOLVE_CONFIG` entry, effectively making the function a no-op for unsupported file types.
- Delegates the three logical stages to `resolve_relative_import` → `generate_candidate_path_list` → set membership check, keeping this function thin and orchestration-only.
- First match in candidate list wins; priority is inherited from `generate_candidate_path_list`.

**Constraints & edge cases:**
- `current_file_rel` must use forward slashes or be normalizable (backslashes are replaced).
- Standard library and external package modules produce no candidates in `project_file_set` and correctly return `None`.

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

**Responsibility:** Inserts a symbol name → file path entry into `symbol_map`, emitting a warning log if the same name is already mapped to a different path. Centralizes conflict detection for symbol registration.

**When to use:** Called internally by `build_symbol_to_file_map` and `_register_definitions_from_file` wherever a symbol name must be written into the shared map.

**Constraints & edge cases:**
- If `name` maps to the same `path` already present, the write is a no-op with no warning.
- The warning is advisory only; the map is overwritten with the new path regardless.

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

Returns a 2-tuple:
| Element | Type | Content |
|---|---|---|
| `symbol_to_file_map` | `dict[str, str]` | Maps each imported name to the relative path of the file defining it |
| `alias_to_original` | `dict[str, str]` | Maps each alias name to the original name it was aliased from |

**Responsibility:** Translates all import statements in a file into a lookup table of "which project file does this name come from", filtering out standard library and third-party imports automatically. Supports usage tracking during analysis.

**When to use:** Called from `codetwine/file_analyzer.py` once per analyzed file, after import statements have been extracted from the AST.

**Design decisions:**

- **Wildcard import (`*`) resolution branches:**
  - `from X import *` with a resolvable path → all definitions extracted from the resolved file.
  - `import com.example.model.*` (Java/Kotlin, unresolvable as single file) → delegated to `_register_definitions_from_package`.
- **`names`-empty path (bare `import X` / `import X as Y`):**
  - Python: registers both the module root (`X`) and the module leaf (`X.Y.Z` → `Z`) to cover attribute-access patterns (`X.Y.func()`) and direct reference.
  - Java/Kotlin: skips root registration (package roots like `com`, `org` are never referenced directly); registers only the leaf class name.
  - `import X as Y`: registers only the alias `Y`.
  - C/C++ (separator `"/"`): registers all definitions from the resolved file via `_register_definitions_from_file`.
- **`names`-present path:** Individual names are registered; additionally, the module root is inserted via `setdefault` (lower priority) to support attribute-access style without overwriting direct-import registrations. Java/Kotlin skip this root registration.
- **Same-package visibility (Java/Kotlin):** If `SAME_PACKAGE_VISIBLE[file_ext]` is set, all files in the same directory with the same extension are scanned and their definitions registered, modeling the language's implicit same-package accessibility.

**Constraints & edge cases:**
- `import_info_list` elements are `ImportInfo` objects as returned by `extract_imports`; the type is untyped in the signature.
- `file_ext` must be without the leading dot.
- Same-package registration excludes the current file itself.

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

**Responsibility:** Parses a project file and registers all named definitions it contains into `symbol_to_file_map`. Models the semantics of C/C++ `#include` (wholesale incorporation) and `from X import *`.

**When to use:** Called internally when an import pulls in an entire file's namespace rather than individual names.

**Design decisions:**
- Silently returns if the absolute path does not exist or if no `DEFINITION_DICTS` entry exists for the extension, making it safe to call speculatively.
- Uses the module-level `parse_file` cache, so repeated calls for the same file do not re-parse.

**Constraints & edge cases:**
- `file_rel` must be a relative path; joined with `project_dir` to produce the absolute path.
- Definitions with an empty `name` are skipped.

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

**Responsibility:** Handles Java/Kotlin wildcard imports (`import com.example.model.*`) by registering definitions from every file directly inside the specified package directory, excluding sub-packages.

**When to use:** Called from `build_symbol_to_file_map` when a wildcard import cannot be resolved to a single file and the separator is `"."`.

**Design decisions:**
- Sub-package exclusion is enforced by checking that no `"/"` appears in the remainder after stripping the `package_dir` prefix, matching Java's wildcard import semantics (non-recursive).
- Only files matching `file_ext` are processed, avoiding accidental cross-language registration.

**Constraints & edge cases:**
- `package_dir` uses forward slashes (e.g. `"com/example/model"`).
- If `package_dir` does not appear in `project_file_set` at all, the function is a silent no-op.

---

## `get_import_params`

**Signature:**
```python
def get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]
```

Returns either a `(Language, str)` tuple — where `Language` is a tree-sitter `Language` object and `str` is a query string — or `(None, None)` for unsupported extensions.

**Responsibility:** Provides callers with the two objects required to run tree-sitter import queries on a file, acting as a single lookup point that guards against unsupported languages.

**When to use:** Called at the start of import analysis in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to decide whether import extraction should proceed.

**Design decisions:**
- Returns `(None, None)` as a sentinel pair rather than raising an exception, allowing callers to skip unsupported files with a simple truthiness check.
- Two separate lookups (`IMPORT_QUERIES`, `TREE_SITTER_LANGUAGES`) are performed sequentially; a missing query short-circuits before the language lookup.

**Constraints & edge cases:**
- `file_ext` must not include the leading dot.
- A `KeyError` from `TREE_SITTER_LANGUAGES` is caught and converted to `(None, None)`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/config/settings.py` : retrieves per-language configuration via `IMPORT_RESOLVE_CONFIG.get` (module separator and candidate-generation rules), `DEFINITION_DICTS.get` (AST node-type-to-name mappings for symbol extraction), `SAME_PACKAGE_VISIBLE.get` (flag controlling same-package implicit visibility for Java/Kotlin), `IMPORT_QUERIES.get` (tree-sitter query strings for import parsing), and `TREE_SITTER_LANGUAGES` (tree-sitter `Language` objects keyed by file extension).

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to read and parse source files into tree-sitter AST root nodes when registering definitions from resolved files (e.g., during `*`-import expansion or C/C++ `#include` handling).

- `codetwine/import_to_path_py/import_to_path.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to enumerate all named definitions from parsed AST nodes, enabling registration of every symbol from an included or wildcard-imported file into the symbol-to-file map.

---

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/import_to_path_py/import_to_path.py` : uses `get_import_params` to obtain the `Language` object and import query string needed to parse import statements in a target file, and uses `build_symbol_to_file_map` to construct the mapping from imported symbol names to their defining project files, which drives subsequent usage tracking.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path_py/import_to_path.py` : uses `resolve_module_to_project_path` to check whether a caller file's import statements reference the target file (confirming a caller–callee relationship), and uses `get_import_params` to obtain the language and query string required to extract import statements from caller files.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path_py/import_to_path.py` : uses `get_import_params` to retrieve the language and query string for each file in the project, and uses `resolve_module_to_project_path` to resolve each parsed import to a project-internal file path, thereby building the project-wide dependency graph edges.

---

## Dependency Direction

All relationships are **unidirectional**:

- `import_to_path.py` → `settings.py`: one-way; `settings.py` does not import from `import_to_path.py`.
- `import_to_path.py` → `ts_parser.py`: one-way; `ts_parser.py` does not import from `import_to_path.py`.
- `import_to_path.py` → `definitions.py`: one-way; `definitions.py` does not import from `import_to_path.py`.
- `file_analyzer.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `file_analyzer.py`.
- `usage_analysis.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `usage_analysis.py`.
- `dependency_graph.py` → `import_to_path.py`: one-way; `import_to_path.py` does not import from `dependency_graph.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `module` | Caller (import statement text) | String (e.g. `"..utils"`, `"./helper"`, `"com.example.Foo"`, `"stdio.h"`) |
| `current_file_rel` | Caller | Relative file path string from project root (e.g. `"src/app/main.py"`) |
| `project_file_set` | Caller | `set[str]` of all relative file paths in the project |
| `import_info_list` | Caller (result of `extract_imports`) | List of `ImportInfo` objects with `.module`, `.names`, `.alias_map`, `.module_alias` fields |
| `file_ext` | Caller | String without leading dot (e.g. `"py"`, `"java"`, `"c"`) |
| `project_dir` | Caller | Absolute path string to project root |
| `IMPORT_RESOLVE_CONFIG` | `codetwine/config/settings.py` | `dict[str, dict]` keyed by extension; each value contains `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | `dict[str, dict[str, str]]` keyed by extension |
| `IMPORT_QUERIES` | `codetwine/config/settings.py` | `dict[str, str|None]` keyed by extension |
| `TREE_SITTER_LANGUAGES` | `codetwine/config/settings.py` | `dict[str, Language]` keyed by extension |
| `SAME_PACKAGE_VISIBLE` | `codetwine/config/settings.py` | `dict[str, bool]` keyed by extension |
| Source file bytes | Filesystem (via `parse_file`) | Binary file content parsed into Tree-sitter AST |

---

## 2. Transformation Overview

### Pipeline A: `resolve_relative_import` → path components

A raw module string (e.g. `"..utils"`, `"./helper"`, `"os"`) is classified by separator type and prefix pattern. Python-style relative imports (`"."` separator, starts with `"."`) are resolved by counting leading dots to determine how many directory levels to ascend from `current_dir_part_list`, then appending remaining module parts. JS/TS-style relative imports (`"/"` separator, starts with `"./"` or `"../"`) are resolved by concatenating the current directory with the module path and normalizing with `os.path.normpath`. Absolute imports are split directly by the separator. The output is a `list[str]` of path components, which is joined with `"/"` to produce `base_path`.

### Pipeline B: `generate_candidate_path_list` → ordered candidate paths

`base_path` and the current file's extension are fed into the candidate generator along with the per-language `resolve_config`. The config flags drive what candidates are appended in order:
1. `base_path + src_ext_with_dot` (same extension, unless `base_path` already has a known extension)
2. `base_path + "/__init__.py"` (if `try_init` is set)
3. `base_path + "/index" + idx_ext` for each `index_ext_list` entry
4. `base_path + alt_ext` for each `alt_ext_list` entry (skipping duplicates and already-known extensions)
5. `base_path` bare (if `try_bare_path`)
6. All of the above prefixed with the current directory (if `try_current_dir`)

Deduplication preserves insertion order via `dict.fromkeys`. The output is a `list[str]` of candidate paths.

### Pipeline C: `resolve_module_to_project_path` → resolved file path

Pipelines A and B are chained: the module string becomes path components (A), then candidate paths (B), then each candidate is looked up in `project_file_set`. The first hit is returned as a relative project path string, or `None` if no candidate matches.

### Pipeline D: `build_symbol_to_file_map` → symbol mapping

Each `ImportInfo` in `import_info_list` passes through Pipeline C. Based on the resolved path and language-specific rules, symbol names are registered into `symbol_to_file_map`:

- **`from X import a, b`** (`names` non-empty): each name is registered individually; `"*"` triggers `_register_definitions_from_file`.
- **`import X.Y.Z`** (`names` empty, dot separator): the module root (`X`) is registered for Python; the module leaf (`Z`) is registered for Java/Kotlin class-name references; a `module_alias` overrides both.
- **`#include "header.h"`** (`names` empty, slash separator): all definitions extracted from the resolved file via `_register_definitions_from_file` are registered.
- **Wildcard `import pkg.*`** (unresolvable to a single file): `_register_definitions_from_package` scans `project_file_set` for files directly under the package directory and calls `_register_definitions_from_file` for each.
- **Same-package visibility** (Java/Kotlin): files in the same directory as `current_file_rel` are scanned and their definitions registered automatically.

`_register_definitions_from_file` reads the target file via `parse_file`, calls `extract_definitions` on the AST, and registers each `DefinitionInfo.name` into `symbol_to_file_map`.

Alias entries from `import_info.alias_map` are collected separately into `alias_to_original`.

### Pipeline E: `get_import_params` → language + query string

A file extension is looked up in `IMPORT_QUERIES` and `TREE_SITTER_LANGUAGES`. Both must be present to return a usable `(Language, str)` pair; either missing yields `(None, None)`.

---

## 3. Outputs

| Output | Produced by | Format |
|---|---|---|
| Path components | `resolve_relative_import` | `list[str]` |
| Candidate paths | `generate_candidate_path_list` | `list[str]`, ordered by priority, deduplicated |
| Resolved project path | `resolve_module_to_project_path` | `str` (relative project path) or `None` |
| Symbol-to-file map | `build_symbol_to_file_map` | `dict[str, str]` mapping imported name → definition file path |
| Alias-to-original map | `build_symbol_to_file_map` | `dict[str, str]` mapping alias name → original name |
| Language + query | `get_import_params` | `tuple[Language, str]` or `tuple[None, None]` |
| Side effect: warnings | `_put_symbol` | Log warnings when a symbol's file mapping is overwritten |

---

## 4. Key Data Structures

### `resolve_config` (from `IMPORT_RESOLVE_CONFIG`, per-language entry)

| Field / Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Module path delimiter (`"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++) |
| `try_init` | `bool` | Whether to try `base_path/__init__.py` as a candidate |
| `index_ext_list` | `list[str]` | Extensions to try as directory index files (e.g. `[".ts", ".js"]`) |
| `alt_ext_list` | `list[str]` | Alternative extensions to append to `base_path` (e.g. `[".py", ".pyi"]`) |
| `try_bare_path` | `bool` | Whether to try `base_path` without any extension appended |
| `try_current_dir` | `bool` | Whether to also generate candidates relative to the current file's directory |

### `symbol_to_file_map`

| Field / Key | Type | Purpose |
|---|---|---|
| `<imported name>` | `str` (key) | The name as it appears in source code (e.g. `"User"`, `"os"`, `"Bar"`) |
| `<file path>` | `str` (value) | Relative project path of the file where that name is defined (e.g. `"src/models/user.py"`) |

### `alias_to_original`

| Field / Key | Type | Purpose |
|---|---|---|
| `<alias name>` | `str` (key) | The local alias used in the importing file (e.g. `"np"`) |
| `<original name>` | `str` (value) | The original exported name before aliasing (e.g. `"numpy"`) |

### `ImportInfo` (consumed, defined externally)

| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | The module path from the import statement |
| `names` | `list[str]` | Specific names imported (`[]` for bare imports, `["*"]` for wildcard) |
| `alias_map` | `dict[str, str]` | Maps alias → original name for named imports with `as` |
| `module_alias` | `str \| None` | Alias for the whole module (`import X as Y`) |

### `DefinitionInfo` (consumed from `extract_definitions`)

| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | The defined symbol name |
| `type` | `str` | AST node type of the definition |
| `start_line` | `int` | Line number where the definition begins |
| `end_line` | `int` | Line number where the definition ends |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation / logging-and-continue** strategy throughout. No exceptions are raised to callers; instead, functions return sentinel values (`None`, empty collections, or skip iterations) when inputs cannot be resolved or files cannot be processed. The only active notification mechanism is `logger.warning` for data integrity concerns (symbol overwrites). There are no `try-except` blocks in this file except within `get_import_params`, which wraps a single dictionary lookup.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension | `IMPORT_RESOLVE_CONFIG.get(src_ext)` returns `None` | Return `None` immediately | Yes | The calling module receives `None` and skips import resolution for that file |
| Unsupported language in `get_import_params` | `IMPORT_QUERIES` has no entry for `file_ext`, or `TREE_SITTER_LANGUAGES[file_ext]` raises `KeyError` | Return `(None, None)` | Yes | Callers skip import analysis for that file entirely |
| Module not resolvable to a project file | No candidate path from `generate_candidate_path_list` matches `project_file_set` | Return `None` from `resolve_module_to_project_path` | Yes | The import is silently treated as a stdlib or external package dependency; no symbol registration occurs |
| Target file does not exist on disk | `os.path.isfile(abs_path)` is `False` in `_register_definitions_from_file` | Return immediately (early exit) | Yes | No symbols are registered from that file; processing continues normally |
| No definition dict for an extension | `DEFINITION_DICTS.get(resolved_ext)` returns `None` in `_register_definitions_from_file` | Return immediately (early exit) | Yes | No symbols are registered from that file; processing continues normally |
| Symbol name collision (overwrite) | `_put_symbol` detects an existing entry mapping the same name to a different file | Log a `WARNING` and overwrite with the new path | Yes (with data loss of prior mapping) | The symbol map retains only the latest registered file path; a warning is emitted |
| Wildcard import unresolvable to a single file | `resolve_module_to_project_path` returns `None` and `"*"` is in `import_info.names` with a `.`-separated language | Fall back to `_register_definitions_from_package` using the module as a package directory path | Yes | Definitions from all files directly under the inferred package directory are registered instead |
| Empty or missing module root after splitting | `module_parts[0].lstrip(".")` yields an empty string | Guard check `if module_root:` prevents registration | Yes | No symbol is registered; processing continues silently |

---

## 3. Design Notes

**Boundary between project and external code:** The central design intent is that `resolve_module_to_project_path` acts as a natural filter — standard library and third-party modules simply fail to match any entry in `project_file_set` and return `None`, requiring no special error classification.

**No exceptions propagated:** The file never raises exceptions to its callers. The `KeyError` in `get_import_params` is the only explicitly caught exception, converted to a `(None, None)` sentinel. All other failure modes are handled by early `return` or `continue` statements.

**Warning as the sole active signal:** Symbol overwrites are the only situation deemed worth surfacing actively via `logger.warning`. All other degradations (unresolved imports, missing files, unsupported extensions) are silent by design, reflecting the assumption that unresolvable imports are expected and frequent (external dependencies), not exceptional.

**Deduplication prevents silent compounding errors:** `generate_candidate_path_list` removes duplicates via `dict.fromkeys` before returning, preventing redundant filesystem lookups that could mask or amplify resolution failures.

## Summary

**import_to_path.py** resolves import module strings to project-internal file paths and builds symbol-to-file mappings.

**Public functions:**
- `resolve_module_to_project_path(module:str, current_file_rel:str, project_file_set:set[str]) → str|None`
- `build_symbol_to_file_map(import_info_list, current_file_rel:str, project_file_set:set[str], file_ext:str, project_dir:str) → tuple[dict[str,str], dict[str,str]]`
- `get_import_params(file_ext:str) → tuple[Language,str]|tuple[None,None]`

**Key structures:** `symbol_to_file_map` (`dict[str,str]`: name→file path), `alias_to_original` (`dict[str,str]`: alias→original name), `ImportInfo` (consumed: `.module`, `.names`, `.alias_map`, `.module_alias`).
