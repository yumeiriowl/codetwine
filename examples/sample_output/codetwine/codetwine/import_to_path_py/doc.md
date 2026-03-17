# Design Document: codetwine/import_to_path.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`import_to_path.py` is the **import-resolution layer** of the codetwine pipeline. Its sole responsibility is to translate raw import statement strings (as extracted from source files) into concrete file paths within the project. It exists as a dedicated module because import resolution is a non-trivial, language-specific concern that is consumed by multiple independent parts of the pipeline (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`), and centralising the logic avoids duplication while keeping each consumer clean.

The module handles the full resolution pipeline:
1. **Parse** a module string into path components (relative vs. absolute, Python-style dots vs. JS/TS-style slashes).
2. **Expand** those components into an ordered list of candidate file paths according to per-language rules (index files, package init files, alternative extensions, etc.).
3. **Match** candidates against the set of known project files and return the first hit.

Beyond single-module resolution, the module builds the **symbol-to-file map** that downstream usage trackers rely on to determine which file each imported name originates from. It also provides the utility function used to obtain the tree-sitter `Language` object and query string required before any import extraction can take place.

All language-specific branching is driven by the declarative configuration in `IMPORT_RESOLVE_CONFIG` (from `settings.py`); no hard-coded per-language `if`-chains exist in the resolution or candidate-generation logic.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `resolve_relative_import` | `module: str`, `separator: str`, `current_dir_part_list: list[str]` | `list[str]` | Converts a raw module string into an ordered list of path components, handling Python dot-relative, JS/TS slash-relative, and absolute import forms. |
| `generate_candidate_path_list` | `base_path: str`, `src_ext_with_dot: str`, `resolve_config: dict`, `current_dir_part_list: list[str]` | `list[str]` | Produces a deduplicated, priority-ordered list of candidate file paths from a base path, applying per-language rules (init files, index files, alt extensions, bare paths, current-dir fallback) declared in `resolve_config`. |
| `resolve_module_to_project_path` | `module: str`, `current_file_rel: str`, `project_file_set: set[str]` | `str \| None` | Orchestrates steps 1–3 (parse → expand → match) to resolve a module name to a project-internal file path; returns `None` for stdlib/external modules. |
| `build_symbol_to_file_map` | `import_info_list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str` | `tuple[dict[str, str], dict[str, str]]` | Builds `(symbol_to_file_map, alias_to_original)` by resolving every import and registering imported names (with language-specific rules for Python, Java, C/C++) against their source files, including wildcard and same-package visibility handling. |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Returns the tree-sitter `Language` object and import query string for a given extension; returns `(None, None)` for unsupported languages so callers can skip import analysis. |

---

## Design Decisions

- **Declarative, config-driven resolution**: `generate_candidate_path_list` contains no language-specific `if`-branches; all variation (Python `__init__.py`, JS `index.ts`, C bare paths, alternative extensions) is expressed through fields in `IMPORT_RESOLVE_CONFIG`. Adding support for a new language requires only a new config entry, not changes to this file.

- **Three-step pipeline with single-responsibility helpers**: `resolve_module_to_project_path` is intentionally thin—it delegates each step to a focused helper (`resolve_relative_import`, `generate_candidate_path_list`) and performs only the final set-membership check itself. This makes each step independently testable.

- **Extension-already-present guard**: `generate_candidate_path_list` checks whether `base_path` already carries a known extension (e.g. C's `#include "stdio.h"`) before appending alternative extensions, preventing nonsensical candidates like `stdio.h.h`.

- **`_put_symbol` with overwrite warning**: All symbol registrations funnel through `_put_symbol`, which emits a `logger.warning` when a symbol name would be remapped to a different file. This surfaces ambiguous import shadowing during analysis without raising exceptions.

- **Lazy definition extraction for `*` and `#include`**: Rather than pre-indexing all definitions up front, `_register_definitions_from_file` parses and extracts definitions on demand (with results cached at the `parse_file` level in `ts_parser.py`) only when a wildcard import or `#include` is encountered.

## Definition Design Specifications

# Definition Design Specifications

---

## `resolve_relative_import`

**Signature:** `resolve_relative_import(module: str, separator: str, current_dir_part_list: list[str]) -> list[str]`

**Responsibility:** Converts an import module string into a list of path components, handling both relative and absolute import syntax for Python (`.`-separated) and JS/TS (`/`-separated).

**Arguments:**
- `module`: The raw module string from an import statement (e.g. `"..utils"`, `"./helper"`, `"os"`).
- `separator`: The delimiter used to split module names; either `"."` for Python/Java/Kotlin or `"/"` for JS/TS/C/C++.
- `current_dir_part_list`: Path components of the directory containing the current file, used as the starting point for resolving relative paths.

**Return value:** A list of path components that, when joined with `"/"`, yields the `base_path` for candidate generation.

**Design decisions:**
- Python dot-counting semantics: one dot means current directory (zero pops), each additional dot pops one more level from `current_dir_part_list`. This matches Python's `importlib` resolution rules.
- JS/TS paths delegate normalization to `os.path.normpath` (with backslash normalization) rather than implementing manual `..` traversal, leveraging the OS path library for correctness.
- Absolute imports (no leading dots or slashes) are handled uniformly by splitting on the separator, making the function a single entry point regardless of import style.

**Edge cases:**
- A Python single dot (`"."`) with an empty `clean_module` returns the current directory unchanged.
- An empty `current_dir_part_list` with a JS/TS relative import results in normpath operating on the module string alone.

---

## `generate_candidate_path_list`

**Signature:** `generate_candidate_path_list(base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]) -> list[str]`

**Responsibility:** Produces an ordered, deduplicated list of file path candidates that a given import might resolve to, driven entirely by declarative per-language configuration rather than language-specific branching.

**Arguments:**
- `base_path`: The path string derived from the module name (e.g. `"src/utils"`, `"stdio.h"`).
- `src_ext_with_dot`: File extension of the importing file including the leading dot (e.g. `".py"`, `".ts"`).
- `resolve_config`: An entry from `IMPORT_RESOLVE_CONFIG` containing keys such as `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, and `try_current_dir`.
- `current_dir_part_list`: Path components of the current file's directory, used when `try_current_dir` is enabled to generate relative candidates.

**Return value:** A list of candidate paths in priority order with no duplicates, preserving insertion order via `dict.fromkeys`.

**Design decisions:**
- The deduplication guard (`has_known_ext`) prevents nonsensical candidates like `"stdio.h.h"` when `base_path` already carries a recognized extension; in that case both the same-extension and alt-extension candidates are skipped.
- When `try_current_dir` is enabled, current-directory-relative variants of every root candidate are appended after all root candidates, ensuring project-root resolution always takes priority.
- The same-extension candidate is always placed first in the candidate list to give it highest priority.

**Edge cases:**
- If `current_dir_part_list` is empty and `try_current_dir` is `True`, the current-directory prefix is an empty string and is omitted from the combined path.
- The alt extension list skips the entry equal to `src_ext_with_dot` since that candidate was already added first.

---

## `resolve_module_to_project_path`

**Signature:** `resolve_module_to_project_path(module: str, current_file_rel: str, project_file_set: set[str]) -> str | None`

**Responsibility:** Determines whether an import module string refers to a file within the project by generating path candidates and testing membership in `project_file_set`, acting as the primary resolution entry point used across the codebase.

**Arguments:**
- `module`: Raw module string from an import statement; may refer to project files, standard library modules, or third-party packages.
- `current_file_rel`: Relative path of the importing file from the project root (e.g. `"src/app/main.py"`).
- `project_file_set`: The complete set of project-relative file paths used for membership testing.

**Return value:** The project-relative path of the resolved file (e.g. `"src/utils.py"`), or `None` if the module cannot be matched to any project file.

**Design decisions:**
- Returns `None` for standard library and external package imports naturally: they produce no matching candidate in `project_file_set` without any explicit allowlist/denylist.
- Returns `None` immediately if no `IMPORT_RESOLVE_CONFIG` entry exists for the file's extension, avoiding any processing for unsupported languages.
- The first matching candidate is returned, preserving the priority order established by `generate_candidate_path_list`.

**Edge cases:**
- If `resolve_config` is absent for the extension, the function returns `None` without proceeding.

---

## `_put_symbol`

**Signature:** `_put_symbol(symbol_map: dict[str, str], name: str, path: str) -> None`

**Responsibility:** Centralizes symbol registration into `symbol_to_file_map` with a warning when a name is being reassigned to a different file, preventing silent overwrites from going unnoticed.

**Arguments:**
- `symbol_map`: The mutable dict mapping symbol names to file paths; modified in place.
- `name`: The symbol name to register.
- `path`: The file path where the symbol is defined.

**Design decisions:**
- Overwriting to the same file is silently allowed (idempotent re-registration); only cross-file overwriting emits a warning, since same-file re-registration is harmless and expected in wildcard import scenarios.

---

## `build_symbol_to_file_map`

**Signature:** `build_symbol_to_file_map(import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str) -> tuple[dict[str, str], dict[str, str]]`

**Responsibility:** Constructs the complete mapping of imported names to their defining project files for a single source file, enabling downstream usage tracking to identify which file any referenced name originates from.

**Arguments:**
- `import_info_list`: Parsed import records (as returned by `extract_imports`) for the current file.
- `current_file_rel`: Relative path of the current file from the project root.
- `project_file_set`: Set of all project-relative file paths.
- `file_ext`: Extension of the current file without the leading dot (e.g. `"py"`, `"java"`).
- `project_dir`: Absolute path to the project root, needed for reading definition files.

**Return value:** A two-element tuple:
- `symbol_to_file_map`: `{ imported_name: definition_file_path }` for all resolvable names.
- `alias_to_original`: `{ alias: original_name }` for all aliased imports (e.g. `import a as b` yields `{"b": "a"}`).

**Design decisions:**
- Standard library and third-party modules are excluded implicitly: `resolve_module_to_project_path` returns `None` for them and the loop skips them with `continue`.
- Java/Kotlin wildcard imports (`import com.example.*`) that cannot resolve to a single file fall back to scanning the package directory, since the module name represents a package rather than a file.
- The module root is registered via `setdefault` (not `_put_symbol`) when names are present, to avoid overwriting an already-registered direct import with a `from X import y` reference.
- Java/Kotlin root-part registration is explicitly skipped because those languages reference classes by their unqualified trailing name, not by package root prefixes like `com` or `org`.
- Same-package visibility (Java/Kotlin): files in the same directory with the same extension are automatically scanned for definitions, replicating the language's implicit same-package accessibility without requiring explicit imports.

**Edge cases:**
- `import *` within resolved names triggers full definition registration from the resolved file.
- When `import_info.names` is empty and no alias is present, both the root and leaf parts of the module name are registered (with Java/Kotlin root registration skipped).
- The `SAME_PACKAGE_VISIBLE` check skips the current file itself to avoid self-registration.

---

## `_register_definitions_from_file`

**Signature:** `_register_definitions_from_file(file_rel: str, project_dir: str, symbol_to_file_map: dict[str, str]) -> None`

**Responsibility:** Extracts all definition names from a project file and registers them in `symbol_to_file_map`, supporting C/C++ `#include` semantics and wildcard import resolution where an entire file's namespace is incorporated.

**Arguments:**
- `file_rel`: Project-relative path of the file to scan.
- `project_dir`: Absolute path to the project root.
- `symbol_to_file_map`: Target dict modified in place.

**Design decisions:**
- The function silently returns if the file does not exist on disk or if no `DEFINITION_DICTS` entry exists for its extension, making it safe to call speculatively.
- Uses `parse_file` (which caches results) to avoid redundant re-parsing across multiple calls.

**Edge cases:**
- Definitions with a falsy `name` attribute are skipped.

---

## `_register_definitions_from_package`

**Signature:** `_register_definitions_from_package(package_dir: str, file_ext: str, project_dir: str, project_file_set: set[str], symbol_to_file_map: dict[str, str]) -> None`

**Responsibility:** Handles Java/Kotlin wildcard package imports by scanning all files of the matching extension directly under `package_dir` and registering their definitions, without descending into sub-packages.

**Arguments:**
- `package_dir`: The directory path corresponding to the imported package (e.g. `"com/example/model"`).
- `file_ext`: Extension of the current file without the leading dot, used to filter files to scan.
- `project_dir`: Absolute path to the project root.
- `project_file_set`: Set of project-relative file paths to iterate over.
- `symbol_to_file_map`: Target dict modified in place.

**Design decisions:**
- Sub-package files are excluded by checking for `"/"` in the remainder after stripping the `package_dir` prefix, matching Java's semantics where `import com.example.*` does not import from sub-packages.

**Edge cases:**
- Files in nested sub-directories of `package_dir` are explicitly excluded.

---

## `get_import_params`

**Signature:** `get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]`

**Responsibility:** Provides the tree-sitter `Language` object and import query string for a given file extension, acting as a gating function that lets callers cleanly skip import analysis for unsupported languages.

**Arguments:**
- `file_ext`: File extension without the leading dot (e.g. `"py"`, `"java"`, `"c"`).

**Return value:** A `(Language, import_query_str)` tuple for supported extensions, or `(None, None)` if either the import query or the language is absent.

**Design decisions:**
- Returns `(None, None)` rather than raising an exception so callers can use a simple truthiness check (`if language and import_query_str`) without try/except blocks, keeping call sites clean.
- The `KeyError` from `TREE_SITTER_LANGUAGES` is caught and converted to `(None, None)` for consistency with the missing-query case.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

**`codetwine/config/settings.py`**
Five configuration dictionaries are imported from this module:
- `IMPORT_RESOLVE_CONFIG` — provides per-language import resolution settings (separator character, index file extensions, alternative extensions, etc.) that drive the logic in `resolve_relative_import`, `generate_candidate_path_list`, and `resolve_module_to_project_path`.
- `IMPORT_QUERIES` — supplies the tree-sitter query strings per file extension, used by `get_import_params` to retrieve the query needed for import statement analysis.
- `TREE_SITTER_LANGUAGES` — supplies the tree-sitter `Language` objects per file extension, also used by `get_import_params` to construct the return value for import analysis.
- `DEFINITION_DICTS` — provides per-language definition node configurations, used by `_register_definitions_from_file` to know which AST node types represent definitions when scanning included/imported files.
- `SAME_PACKAGE_VISIBLE` — indicates which languages (Java, Kotlin) allow referencing same-package symbols without explicit imports, used in `build_symbol_to_file_map` to auto-register definitions from files in the same directory.

**`codetwine/parsers/ts_parser.py`**
`parse_file` is used by `_register_definitions_from_file` to parse a project file into an AST root node. This is necessary when the full set of definitions must be extracted from an included or wildcard-imported file.

**`codetwine/extractors/definitions.py`**
`extract_definitions` is used by `_register_definitions_from_file` to walk the AST produced by `parse_file` and yield all named definitions. The resulting names are then registered into `symbol_to_file_map`, enabling symbol-to-file resolution for `#include`-style or wildcard imports.

---

### Dependents (what uses this file)

**`codetwine/file_analyzer.py`**
Uses `get_import_params` to obtain the tree-sitter `Language` object and import query string required before parsing import statements in a file. Uses `build_symbol_to_file_map` to produce the `symbol_to_file_map` and `alias_to_original` dictionaries that map imported names to their source files, which are consumed downstream for usage tracking. The dependency is unidirectional: `file_analyzer.py` depends on this file; this file does not reference `file_analyzer.py`.

**`codetwine/extractors/usage_analysis.py`**
Uses `get_import_params` to retrieve parsing parameters when analyzing caller files for usage detection. Uses `resolve_module_to_project_path` to check whether a caller's import statement resolves to a specific target file, determining whether that caller file is a dependent of the target. The dependency is unidirectional.

**`codetwine/extractors/dependency_graph.py`**
Uses `get_import_params` to obtain import parsing parameters when building the project-wide dependency graph. Uses `resolve_module_to_project_path` to resolve each import statement in a file to a project-internal path, adding the resolved path as a callee edge in the graph. The dependency is unidirectional.

## Data Flow

# Data Flow

## Input Data

| Source | Data | Format |
|--------|------|--------|
| Caller (import statement parser) | `module` string from import statement | `str` (e.g. `"..utils"`, `"./helper"`, `"os"`) |
| Caller | `current_file_rel` | Relative path string from project root |
| Caller | `project_file_set` | `set[str]` of all project-relative file paths |
| Caller | `import_info_list` | List of `ImportInfo` objects (module, names, alias_map, module_alias) |
| `settings.py` | `IMPORT_RESOLVE_CONFIG` | `dict[ext → config_dict]` with keys: `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` |
| `settings.py` | `IMPORT_QUERIES`, `TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `SAME_PACKAGE_VISIBLE` | Per-extension lookup dicts |
| Filesystem (via `parse_file`) | Source files for definition extraction | Binary file content → AST root node |

---

## Main Transformation Pipeline

```
module string
    │
    ▼
resolve_relative_import()
    │  Converts module name + current_dir_part_list
    │  into path_part_list (list[str])
    │  using separator ("." or "/")
    ▼
base_path  ("/".join(path_part_list))
    │
    ▼
generate_candidate_path_list()
    │  Applies resolve_config rules to base_path:
    │  append src_ext, try __init__.py,
    │  index files, alt_ext_list, bare path,
    │  current-dir prefixed copies
    │  → deduplication via dict.fromkeys()
    ▼
candidate_path_list (list[str], priority-ordered)
    │
    ▼
Match against project_file_set (first hit wins)
    │
    ▼
resolved_path (str | None)
```

`build_symbol_to_file_map` applies the above pipeline for each `ImportInfo`, then performs a second transformation:

```
resolved_path + ImportInfo.names
    │
    ├─ names is empty, separator="."  → register module_root and/or module_leaf
    ├─ names is empty, separator="/"  → _register_definitions_from_file()
    ├─ name == "*"                    → _register_definitions_from_file()
    ├─ names present                  → register each name directly
    └─ wildcard + unresolved + "."    → _register_definitions_from_package()
    │
    ▼
symbol_to_file_map { name → file_rel }
alias_to_original  { alias → original_name }
```

`_register_definitions_from_file` adds a further sub-flow:

```
file_rel + project_dir
    │
    ▼
parse_file(abs_path) → AST root_node
    │
    ▼
extract_definitions(root_node, definition_dict)
    │
    ▼
Each DefinitionInfo.name → _put_symbol() → symbol_to_file_map
```

---

## Output Data

| Function | Output | Destination |
|----------|--------|-------------|
| `resolve_relative_import` | `list[str]` path components | consumed by `resolve_module_to_project_path` |
| `generate_candidate_path_list` | `list[str]` candidate paths (priority-ordered, deduplicated) | consumed by `resolve_module_to_project_path` |
| `resolve_module_to_project_path` | `str \| None` project-relative file path | `dependency_graph.py`, `usage_analysis.py`, `build_symbol_to_file_map` |
| `build_symbol_to_file_map` | `(symbol_to_file_map, alias_to_original)` tuple | `file_analyzer.py` |
| `get_import_params` | `(Language, import_query_str) \| (None, None)` | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py` |

---

## Key Data Structures

### `IMPORT_RESOLVE_CONFIG` entry (per extension)

| Field | Type | Purpose |
|-------|------|---------|
| `separator` | `str` | `"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++ |
| `try_init` | `bool` | Try `base_path/__init__.py` (Python packages) |
| `index_ext_list` | `list[str]` | Try `base_path/index<ext>` (JS/TS barrel files) |
| `alt_ext_list` | `list[str]` | Try `base_path<ext>` for alternative extensions |
| `try_bare_path` | `bool` | Try `base_path` as-is (C/C++ `#include "stdio.h"`) |
| `try_current_dir` | `bool` | Also prepend current directory to all root candidates |

### `symbol_to_file_map`

```
{ symbol_name: str → project_relative_file_path: str }
```
Maps every imported (or same-package) name to the file where it is defined. Used by `file_analyzer.py` to trace "which file does this identifier come from" during usage tracking.

### `alias_to_original`

```
{ alias_name: str → original_name: str }
```
Captures `from X import a as b` → `{"b": "a"}`. Built directly from `ImportInfo.alias_map` entries.

## Error Handling

# Error Handling

## Overall Strategy

This file follows a **graceful degradation** strategy. No exceptions are raised to callers; instead, unresolvable or unsupported inputs cause the affected operation to be silently skipped or to return a sentinel value (`None`, empty dict, or empty list). The caller retains full control over whether to treat the absence of a result as an error.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Extension not present in `IMPORT_RESOLVE_CONFIG` | Return `None` immediately | The import is treated as non-project-internal and ignored for the current file |
| Extension not present in `IMPORT_QUERIES` or `TREE_SITTER_LANGUAGES` | Return `(None, None)` | Caller skips import analysis entirely for the file |
| Extension not present in `DEFINITION_DICTS` | Return early without registering any symbols | No definitions from that file are added to the symbol map |
| Referenced file does not exist on disk (`os.path.isfile` check) | Return early without parsing | No definitions from that file are added to the symbol map |
| Module name resolves to no candidate matching `project_file_set` | Return `None` | Import is classified as external/stdlib and excluded from tracking |
| `KeyError` on `TREE_SITTER_LANGUAGES` lookup | Caught explicitly, return `(None, None)` | Caller skips import analysis for the unsupported extension |
| Symbol name collision (same name resolves to a different file) | Log a `WARNING` via `logger`, overwrite with the new path | Later-seen definition wins; the discrepancy is surfaced in logs but processing continues |

## Design Considerations

- **No exception propagation to callers.** All boundary checks (missing config entries, missing files, unresolvable modules) produce a clean falsy return rather than raising, which keeps the calling pipeline resilient against gaps in language configuration or project structure.
- **Explicit `KeyError` guard vs. `dict.get`.** The majority of config lookups use `.get()` (returning `None` on miss). The single `KeyError`-based `try/except` in `get_import_params` reflects a deliberate distinction: `TREE_SITTER_LANGUAGES` is expected to be fully populated if an import query exists, so an unexpected miss is treated as an error condition worth catching explicitly rather than quietly returning `None` through `.get()`.
- **Warning-only on symbol collision.** Overwriting a symbol's source file is a data-integrity concern (e.g., two files defining the same name), but it does not halt analysis. The warning log provides observability without interrupting the pipeline.
- **No input validation exceptions.** Functions assume well-formed inputs (valid path strings, properly structured config dicts). Malformed inputs are not detected proactively; they cause silent misses rather than explicit errors.

## Summary

`import_to_path.py` resolves raw import strings to project file paths. It parses module strings into path components (`resolve_relative_import`), generates prioritized candidate paths via declarative config (`generate_candidate_path_list`), and matches against the project file set (`resolve_module_to_project_path`). `build_symbol_to_file_map` constructs `{symbol→file}` and `{alias→original}` mappings for downstream usage tracking, handling wildcards, same-package visibility, and `#include` semantics. `get_import_params` returns tree-sitter Language/query pairs for supported extensions. All language-specific behavior is driven by `IMPORT_RESOLVE_CONFIG`; unresolvable imports return `None` without exceptions.
