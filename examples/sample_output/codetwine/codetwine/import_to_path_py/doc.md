# Design Document: codetwine/import_to_path.py

# Overview & Purpose

## 1. Module Summary

Resolve import module names found in source files to concrete project-relative file paths, and build the symbol-to-file mappings that let downstream analysis determine which file each imported name originates from.

## 2. When to Use This Module

- **Checking whether an import targets a project-internal file**: Call `resolve_module_to_project_path(module, current_file_rel, project_file_set)` to convert a raw module string (e.g. `"..utils"`, `"./helper"`, `"os"`) into a project-relative path, or `None` if it resolves to a standard library or external package.
- **Building a name-to-file lookup for usage tracking**: Call `build_symbol_to_file_map(import_info_list, current_file_rel, project_file_set, file_ext, project_dir)` after parsing import statements; it returns a `(symbol_to_file_map, alias_to_original)` tuple mapping every imported name to the file that defines it.
- **Obtaining language/query parameters before import extraction**: Call `get_import_params(file_ext)` to retrieve the tree-sitter `Language` object and import query string needed to drive import parsing for a given file extension; it returns `(None, None)` for unsupported languages so the caller can skip analysis safely.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `resolve_relative_import` | `module: str`, `separator: str`, `current_dir_part_list: list[str]` | `list[str]` | Convert a relative or absolute import module string into a list of directory path components. |
| `generate_candidate_path_list` | `base_path: str`, `src_ext_with_dot: str`, `resolve_config: dict`, `current_dir_part_list: list[str]` | `list[str]` | Produce an ordered, deduplicated list of candidate file paths from a base path using language-specific resolution config. |
| `resolve_module_to_project_path` | `module: str`, `current_file_rel: str`, `project_file_set: set[str]` | `str \| None` | Resolve a module name from an import statement to a project-relative file path; returns `None` if no match exists in the project. |
| `build_symbol_to_file_map` | `import_info_list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str` | `tuple[dict[str, str], dict[str, str]]` | Build `(symbol_to_file_map, alias_to_original)` mapping imported names to their definition files, handling Python, Java, C/C++, and same-package visibility rules. |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Return the tree-sitter `Language` object and import query string for a given file extension, or `(None, None)` if unsupported. |

## 4. Design Decisions

- **Declarative, config-driven candidate generation**: `generate_candidate_path_list` contains no language-specific branches; all per-language rules (index files, alternative extensions, `__init__.py`, bare paths, current-directory fallback) are expressed via fields in `IMPORT_RESOLVE_CONFIG`. Adding support for a new language requires only a config entry, not code changes in this module.
- **Three-step resolution pipeline**: `resolve_module_to_project_path` decomposes resolution into three discrete, independently testable steps—relative-to-path-component conversion, candidate generation, and set membership check—each delegated to a dedicated function.
- **Wildcard and same-package visibility handled at the map-building layer**: Rather than encoding Java/Kotlin package semantics into the resolution step, `build_symbol_to_file_map` adds a separate post-processing pass for wildcard imports and same-package-visible definitions, keeping `resolve_module_to_project_path` language-agnostic.
- **Extension deduplication guard**: `generate_candidate_path_list` detects when `base_path` already carries a known extension (e.g. C/C++ `#include "stdio.h"`) and skips appending alternative extensions to prevent meaningless double-extension candidates.

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

**Responsibility:** Translates an import module string into a list of filesystem path components, handling both relative and absolute import syntaxes across Python (dot-based) and JS/TS (slash-based) conventions.

**When to use:** Called by `resolve_module_to_project_path` whenever a module name needs to be converted to a path before candidate file paths can be generated.

**Design decisions:**

| Input style | Condition | Behavior |
|---|---|---|
| Python relative | `separator == "."` and module starts with `.` | Counts leading dots; one dot = current dir, each additional dot = one level up |
| JS/TS relative | `separator == "/"` and module starts with `./` or `../` | Concatenates current dir with module, normalizes via `os.path.normpath`, splits on `/` |
| Absolute | Neither condition met | Splits module by `separator` directly |

**Constraints & edge cases:**
- For Python-style, a single leading dot keeps the full `current_dir_part_list`; each additional dot pops one element.
- If `current_dir_part_list` is empty and JS/TS relative import is given, `combined` equals the bare module string before normalization.
- `os.path.normpath` backslash output is normalized to forward slashes for cross-platform consistency.

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

`resolve_config` is a per-language dict from `IMPORT_RESOLVE_CONFIG`; `src_ext_with_dot` is a string like `".py"` or `".ts"`.

**Responsibility:** Produces an ordered, deduplicated list of candidate file paths to try for a given `base_path`, driven entirely by declarative configuration flags so no language-specific branching is needed here.

**When to use:** Called by `resolve_module_to_project_path` after the base path has been derived, to enumerate all plausible physical file locations for the import.

**Design decisions:**

| Config key | Type | Effect when enabled |
|---|---|---|
| `try_init` | `bool` | Appends `base_path + "/__init__.py"` for Python packages |
| `index_ext_list` | `list[str]` | Appends `base_path + "/index" + ext` for each entry (JS/TS index files) |
| `alt_ext_list` | `list[str]` | Appends `base_path + alt_ext` for each alternative extension |
| `try_bare_path` | `bool` | Appends `base_path` as-is (handles C/C++ `#include "stdio.h"`) |
| `try_current_dir` | `bool` | Prepends `current_dir/` to every previously generated candidate |

- **Extension guard:** If `base_path` already carries a known extension (its extension is in `alt_ext_list`), neither same-extension nor alternative-extension candidates are appended, preventing nonsense paths like `stdio.h.h`.
- **Same-extension deduplication:** When iterating `alt_ext_list`, the entry equal to `src_ext_with_dot` is skipped because that candidate is already added first.
- Final deduplication preserves insertion order via `dict.fromkeys`.

**Constraints & edge cases:**
- `try_current_dir` doubles the candidate list size; the root-based candidates appear before current-directory variants.
- An empty `current_dir_part_list` with `try_current_dir` enabled produces candidates with an empty prefix (`"/candidate"`), which will not match real paths.

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

`project_file_set` is a set of project-relative path strings (e.g. `"src/utils.py"`). Returns a matching project-relative path or `None`.

**Responsibility:** Determines whether a given import module name resolves to a file within the project, returning the project-relative path if found and `None` otherwise (standard library or third-party packages return `None`).

**When to use:** Called from `build_symbol_to_file_map`, `dependency_graph.py`, and `usage_analysis.py` to convert raw import strings into concrete project file paths.

**Design decisions:**
- Delegates to three sub-functions in sequence: `resolve_relative_import` → `generate_candidate_path_list` → linear scan of `project_file_set`.
- Returns the first candidate that exists in `project_file_set`; priority order is determined by `generate_candidate_path_list`.
- Returns `None` early if no `IMPORT_RESOLVE_CONFIG` entry exists for the file's extension, making unsupported languages safe to call with.

**Constraints & edge cases:**
- `current_file_rel` must use forward slashes or will be normalized internally via `.replace("\\", "/")`.
- All modules—internal and external—are passed in; external ones are filtered out by the absence of a match in `project_file_set`.

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

`symbol_map` maps symbol names to project-relative file paths; modified in place.

**Responsibility:** Inserts or updates a symbol-name entry in `symbol_map`, emitting a warning log when the same name was already mapped to a different file.

**When to use:** Used internally by `build_symbol_to_file_map`, `_register_definitions_from_file`, and `_register_definitions_from_package` whenever a symbol needs to be registered.

**Constraints & edge cases:**
- Silently overwrites if `existing == path` (idempotent for the same file).
- Warning is issued but the new path still wins when there is a conflict.

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

`import_info_list` is a list of `ImportInfo` objects from `extract_imports`. Returns `(symbol_to_file_map, alias_to_original)` where both are `dict[str, str]`.

**Responsibility:** Builds a complete mapping from every name that can be referenced in the current file to the project file where that name is defined, enabling downstream usage tracking to identify which file a usage refers to.

**When to use:** Called from `file_analyzer.py` after import statements have been parsed, to set up the name-resolution table used during usage analysis.

**Design decisions — language-specific registration logic:**

| Language / condition | Symbol(s) registered |
|---|---|
| `from X import a, b` | `"a"`, `"b"` individually |
| `from X import *` | All definitions extracted from the resolved file |
| `import X.Y.Z` (Python) | Root `"X"` + leaf `"Z"` |
| `import X as Y` | Alias `"Y"` only |
| Java `import com.foo.Bar` | Leaf `"Bar"` only (root skipped for `java`/`kt`) |
| Java/Kotlin wildcard `import pkg.*` | All definitions from all files under `pkg/` directory |
| C/C++ `#include` (no names, separator `/`) | All definitions from the included file |
| `from X import a, b` (also) | Module root set via `setdefault` for attribute access, except for `java`/`kt` |

- **Same-package visibility (Java/Kotlin):** When `SAME_PACKAGE_VISIBLE` is true for the extension, all files in the same directory with the same extension are also scanned and their definitions registered, reflecting Java's same-package accessibility without explicit imports.
- `alias_to_original` is populated directly from `import_info.alias_map` for all resolved imports.

**Constraints & edge cases:**
- `import_info_list` elements are expected to have `.module`, `.names`, `.alias_map`, and `.module_alias` attributes (duck-typed, no formal type annotation).
- Wildcard Java/Kotlin imports that resolve to a single file are handled by the normal per-name path (the `"*"` name triggers `_register_definitions_from_file`); the package-directory fallback only activates when `resolve_module_to_project_path` returns `None`.

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

`symbol_to_file_map` is modified in place.

**Responsibility:** Parses a project file and registers all its extracted definition names into `symbol_to_file_map`, used when an import incorporates an entire file's namespace (C/C++ `#include`, `from X import *`, etc.).

**When to use:** Called wherever all publicly visible names from a file must be made available to a caller file without enumerating them individually.

**Design decisions:**
- Silently returns if the absolute path does not exist as a file or if no `DEFINITION_DICTS` entry exists for the extension, making it safe to call with any path.
- Delegates parsing to `parse_file` (which is cached) and definition extraction to `extract_definitions`.

**Constraints & edge cases:**
- Only names with a non-empty `defn.name` are registered.
- The extension used for `DEFINITION_DICTS` lookup is derived from `file_rel`, not from any caller context.

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

`symbol_to_file_map` is modified in place.

**Responsibility:** Handles Java/Kotlin wildcard package imports by iterating all files directly under `package_dir` with the matching extension and registering their definitions.

**When to use:** Called from `build_symbol_to_file_map` when a wildcard import (`import pkg.*`) cannot be resolved to a single file.

**Design decisions:**
- Only files *directly* under `package_dir` are included; a `/` in the remainder path after stripping the prefix indicates a sub-package and is skipped, matching Java's single-level wildcard import semantics.
- Delegates actual definition registration to `_register_definitions_from_file` for each qualifying file.

**Constraints & edge cases:**
- `package_dir` must use forward-slash separators (already guaranteed by the `.replace(".", "/")` call in the caller).
- Files with a different extension than `file_ext` are excluded.

---

## `get_import_params`

**Signature:**
```python
def get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]
```

`Language` is a tree-sitter `Language` object. Returns either a valid `(Language, query_string)` pair or `(None, None)`.

**Responsibility:** Provides callers with the tree-sitter `Language` object and the import query string required to extract import statements from a file, as a single convenient lookup.

**When to use:** Called at the start of any analysis phase (dependency graph building, usage analysis, file analysis) to determine whether import extraction is supported for a given file extension before proceeding.

**Design decisions:**
- Returns `(None, None)` for both missing query and missing language cases, giving callers a single falsy check (`if language and import_query_str`) instead of two separate error conditions.
- `TREE_SITTER_LANGUAGES` lookup uses `[]` (raising `KeyError`) rather than `.get()`, and the `KeyError` is caught to return `(None, None)`, keeping the same sentinel return for both failure modes.

**Constraints & edge cases:**
- An extension present in `IMPORT_QUERIES` but absent from `TREE_SITTER_LANGUAGES` returns `(None, None)`.
- An extension with `import_query` set to `None` in the registry also returns `(None, None)` because `.get()` returns `None` for that key.

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/import_to_path.py` → `codetwine/config/settings.py` : retrieves `IMPORT_RESOLVE_CONFIG.get` to obtain per-language import resolution configuration (separator, extension candidates, etc.), `SAME_PACKAGE_VISIBLE.get` to determine whether same-package files are implicitly visible (Java/Kotlin), `DEFINITION_DICTS.get` to obtain per-language definition node type mappings for parsing included/imported files, `IMPORT_QUERIES.get` to retrieve the tree-sitter import query string for a given extension, and `TREE_SITTER_LANGUAGES` to retrieve the tree-sitter `Language` object for a given extension.

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file` to parse source files into tree-sitter AST root nodes when registering definitions from imported or included files (e.g., for C/C++ `#include` resolution and wildcard imports).

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions.py` : uses `extract_definitions` to extract named definitions (functions, classes, types, etc.) from parsed AST nodes, enabling registration of all symbols from an imported or included file into the symbol-to-file map.

## Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` : uses `get_import_params` to retrieve the tree-sitter `Language` object and import query string needed to drive import extraction for a given file, and uses `build_symbol_to_file_map` to construct the mapping from imported symbol names to their defining project files, which underlies usage tracking within the file analyzer.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` : uses `resolve_module_to_project_path` to check whether a caller file's import resolves to the target file (determining whether the caller imports the target), and uses `get_import_params` to obtain language and query parameters needed to extract import statements from caller files.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path.py` : uses `get_import_params` to obtain language and query parameters for each file when building the project-wide dependency graph, and uses `resolve_module_to_project_path` to resolve each import statement to a project-internal file path, establishing directed edges in the dependency graph.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/import_to_path.py` → `codetwine/config/settings.py`: one-way; `settings.py` has no dependency on `import_to_path.py`.
- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser.py`: one-way; `ts_parser.py` has no dependency on `import_to_path.py`.
- `codetwine/import_to_path.py` → `codetwine/extractors/definitions.py`: one-way; `definitions.py` has no dependency on `import_to_path.py`.
- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py`: one-way; `import_to_path.py` does not import from `file_analyzer.py`.
- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py`: one-way; `import_to_path.py` does not import from `usage_analysis.py`.
- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path.py`: one-way; `import_to_path.py` does not import from `dependency_graph.py`.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `module` | Caller (import statement text) | String, e.g. `"..utils"`, `"./helper"`, `"os"`, `"com.example.Foo"` |
| `current_file_rel` | Caller | Relative file path string from project root, e.g. `"src/app/main.py"` |
| `project_file_set` | Caller | `set[str]` of all project-relative file paths |
| `import_info_list` | Caller (result of `extract_imports`) | List of `ImportInfo` objects |
| `file_ext` | Caller | Extension string without leading dot, e.g. `"py"`, `"java"`, `"c"` |
| `project_dir` | Caller | Absolute path string to project root |
| `IMPORT_RESOLVE_CONFIG` | `codetwine/config/settings.py` | `dict[str, dict]` keyed by file extension; each value has `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` |
| `DEFINITION_DICTS` | `codetwine/config/settings.py` | `dict[str, dict[str, str]]` keyed by file extension |
| `IMPORT_QUERIES` | `codetwine/config/settings.py` | `dict[str, str | None]` keyed by file extension |
| `TREE_SITTER_LANGUAGES` | `codetwine/config/settings.py` | `dict[str, Language]` keyed by file extension |
| `SAME_PACKAGE_VISIBLE` | `codetwine/config/settings.py` | `dict[str, bool]` keyed by file extension |
| File contents (on demand) | `parse_file` + filesystem | Binary file content parsed into tree-sitter AST `Node` |

---

## 2. Transformation Overview

### Pipeline A: `resolve_relative_import` → `generate_candidate_path_list` → project file matching

This is the core resolution pipeline, executed inside `resolve_module_to_project_path`.

**Stage 1 — Extension/config lookup:** The current file's extension is extracted from `current_file_rel`. The corresponding entry in `IMPORT_RESOLVE_CONFIG` is retrieved, providing `separator` and other resolution settings. The current file's directory is split into a `list[str]` of path components.

**Stage 2 — Module → path components (`resolve_relative_import`):** The raw module string is interpreted according to its `separator`. Python-style dot-relative imports (`.`, `..`) are resolved by counting leading dots and trimming the directory component list. JS/TS-style slash-relative imports (`./`, `../`) are resolved using `os.path.normpath`. Absolute imports are simply split on the separator. The result is `path_part_list: list[str]`, joined with `/` into `base_path: str`.

**Stage 3 — Candidate path generation (`generate_candidate_path_list`):** From `base_path`, a prioritized list of candidate file paths is generated by applying extension-appending rules drawn from the resolve config: same-extension variant, `__init__.py`, index files, alternative extensions, bare path, and current-directory-relative variants. Duplicates are removed while preserving priority order.

**Stage 4 — Project file matching:** Each candidate path is checked against `project_file_set`. The first match is returned as the resolved project-internal path; if none match, `None` is returned.

---

### Pipeline B: `build_symbol_to_file_map` — import list → symbol map

This pipeline iterates over all `ImportInfo` entries and builds two output dicts.

**Stage 1 — Per-import module resolution:** For each `ImportInfo`, Pipeline A is invoked to resolve `import_info.module` to a project file path.

**Stage 2 — Java/Kotlin wildcard fallback:** When resolution fails and the import contains `"*"` with a `.` separator, the module is treated as a package directory path. All same-extension files directly under that directory are enumerated from `project_file_set`, and their definitions are registered via `_register_definitions_from_file`.

**Stage 3 — Symbol registration (name-specific):** For resolved imports with explicit `names`:
- `"*"` triggers `_register_definitions_from_file` to register all definitions from the resolved file.
- Individual names are registered directly into `symbol_to_file_map`.
- Alias mappings (`alias_map`) are merged into `alias_to_original`.

**Stage 4 — Symbol registration (name-absent):** When `names` is empty, the registration strategy depends on `separator` and `file_ext`:
- `.` separator with alias: the alias is registered.
- `.` separator without alias: the module root component (for Python package access) and module leaf component (for Java class reference) are both registered, skipping the root for `java`/`kt`.
- `/` separator (C/C++): all definitions from the resolved file are registered via `_register_definitions_from_file`.

**Stage 5 — Module root setdefault (names non-empty):** For non-Java/Kotlin files with explicit names, the module root is also registered using `setdefault` so it does not overwrite an existing direct-import entry.

**Stage 6 — Same-package visibility (Java/Kotlin):** If `SAME_PACKAGE_VISIBLE` is set for the extension, all project files in the same directory with the same extension (excluding the current file) are iterated, and their definitions are registered into `symbol_to_file_map`.

---

### Pipeline C: `_register_definitions_from_file`

**Stage 1:** Absolute path is constructed from `project_dir + file_rel` and verified to exist.

**Stage 2:** `DEFINITION_DICTS` is consulted for the file's extension to get the definition extraction config.

**Stage 3:** `parse_file` reads and parses the file into a tree-sitter AST `Node`.

**Stage 4:** `extract_definitions` traverses the AST and returns `DefinitionInfo` objects. Each definition's `name` is registered into `symbol_to_file_map` via `_put_symbol`.

---

### Pipeline D: `get_import_params`

A simple two-step lookup: retrieve the import query string from `IMPORT_QUERIES`, then the `Language` object from `TREE_SITTER_LANGUAGES`. Returns both as a tuple, or `(None, None)` if either is absent.

---

## 3. Outputs

| Output | Function | Format | Description |
|---|---|---|---|
| Resolved project path | `resolve_module_to_project_path` | `str \| None` | Project-relative file path matching the import, or `None` |
| Path components | `resolve_relative_import` | `list[str]` | Directory path components derived from a module string |
| Candidate paths | `generate_candidate_path_list` | `list[str]` | Priority-ordered candidate file paths, deduplicated |
| Symbol-to-file map | `build_symbol_to_file_map` | `dict[str, str]` | Maps imported name → definition file path |
| Alias-to-original map | `build_symbol_to_file_map` | `dict[str, str]` | Maps alias name → original name |
| Language + query | `get_import_params` | `tuple[Language, str] \| tuple[None, None]` | Tree-sitter Language object and import query string |
| Side effect | `_put_symbol` | Mutation of `dict[str, str]` | Registers or overwrites a symbol entry; logs a warning on overwrite |

---

## 4. Key Data Structures

### `IMPORT_RESOLVE_CONFIG` entry (per-language resolve config dict)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Module name delimiter: `"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++ |
| `try_init` | `bool` | Whether to try `base_path/__init__.py` as a candidate (Python packages) |
| `index_ext_list` | `list[str]` | Extensions to try as index files (e.g. `[".ts", ".js"]` for `base_path/index.ts`) |
| `alt_ext_list` | `list[str]` | Alternative extensions to append to `base_path` |
| `try_bare_path` | `bool` | Whether to include `base_path` as-is without appending any extension |
| `try_current_dir` | `bool` | Whether to also generate candidates relative to the current file's directory |

---

### `ImportInfo` (consumed from `extract_imports`)

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | Raw module string from the import statement |
| `names` | `list[str]` | Individually imported names (empty list for bare imports; `"*"` for wildcard) |
| `alias_map` | `dict[str, str] \| None` | Maps alias → original name for `import X as Y` / `from X import a as b` |
| `module_alias` | `str \| None` | Alias for the entire module in `import X as Y` |

---

### `symbol_to_file_map`

| Key | Type | Purpose |
|---|---|---|
| imported name or definition name | `str` | The symbol as it appears in source code (e.g. `"User"`, `"os"`, `"helper"`) |
| (value) | `str` | Project-relative file path where the symbol is defined |

---

### `alias_to_original`

| Key | Type | Purpose |
|---|---|---|
| alias name | `str` | The name used in the importing file (e.g. `"b"` in `import a as b`) |
| (value) | `str` | The original name in the exporting module (e.g. `"a"`) |

---

### `candidate_path_list` (output of `generate_candidate_path_list`)

| Element | Type | Purpose |
|---|---|---|
| Each entry | `str` | A project-relative file path candidate to check against `project_file_set`, ordered by priority |

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation with logging-and-continue** strategy. No exceptions are raised to callers; instead, functions return sentinel values (`None`, empty collections, or empty strings) when they cannot fulfill their primary purpose. The guiding principle is that a failure to resolve a single import or symbol should never abort the broader analysis pipeline — unresolvable items are silently skipped, while only unexpected data conflicts are surfaced via warnings.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported file extension | `IMPORT_RESOLVE_CONFIG.get(src_ext)` returns `None` (no resolve config registered for the extension) | Return `None` immediately | Yes — caller skips this file | Import resolution is skipped entirely for the file |
| Unsupported language for import params | `IMPORT_QUERIES.get(file_ext)` returns `None` or `file_ext` is absent from `TREE_SITTER_LANGUAGES` | Return `(None, None)` | Yes — caller checks for `None` and skips import analysis | Import analysis is not performed for that file |
| Module not resolvable to a project file | Generated candidate paths do not match any entry in `project_file_set` (external/stdlib modules, typos, etc.) | Return `None` from `resolve_module_to_project_path` | Yes — import entry is skipped | That import contributes no entries to `symbol_to_file_map` |
| Referenced file does not exist on disk | `os.path.isfile(abs_path)` is `False` in `_register_definitions_from_file` | Return early without registering any symbols | Yes — file is skipped silently | Definitions from the missing file are absent from `symbol_to_file_map` |
| No definition dict for extension | `DEFINITION_DICTS.get(resolved_ext)` returns `None` inside `_register_definitions_from_file` | Return early without registering any symbols | Yes — file is skipped silently | Definitions from that file are absent from `symbol_to_file_map` |
| Symbol name collision (duplicate symbol across files) | `_put_symbol` is called with a `name` already mapped to a **different** file path | Log a `WARNING` and overwrite with the new path | Yes — processing continues with the latest mapping | The earlier file's association for that symbol is lost; a warning is emitted |
| Wildcard import unresolvable to single file | `resolve_module_to_project_path` returns `None` and `"*"` is in `import_info.names` with dot separator | Fall back to treating the module as a package directory and scanning it via `_register_definitions_from_package` | Yes — package-level fallback is attempted | If the package directory also contains no matching files, no symbols are registered |

---

## 3. Design Notes

**Sentinel returns over exceptions.** All public functions that may fail to produce a result return `None` or empty structures rather than raising exceptions. This design isolates resolution failures to individual imports and prevents a single bad import from disrupting analysis of the rest of a file or the broader project graph.

**Conflict visibility without termination.** Symbol name collisions are considered non-fatal (they can arise legitimately from wildcard imports or same-package visibility rules), so the policy is to warn and continue rather than raise. This preserves as much analysis data as possible while making conflicts observable in logs.

**Responsibility delegation for existence checks.** File existence is verified locally within `_register_definitions_from_file` rather than at call sites, so callers do not need to guard against missing files. This centralizes the check and ensures consistent silent-skip behavior regardless of which code path reaches the function.

**No retry logic.** The file performs no retries. Each resolution attempt is made exactly once; if it fails, the result is treated as absent. This is appropriate because failures here represent logical mismatches (module not in project, unsupported language) rather than transient I/O errors.

# Summary

**`codetwine/import_to_path.py`** resolves import module strings to project-relative file paths and builds symbol-to-file mappings for downstream analysis.

**Public functions:**
- `resolve_module_to_project_path(module: str, current_file_rel: str, project_file_set: set[str]) → str | None`
- `build_symbol_to_file_map(import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str) → tuple[dict[str, str], dict[str, str]]`
- `get_import_params(file_ext: str) → tuple[Language, str] | tuple[None, None]`
- `generate_candidate_path_list(base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]) → list[str]`

**Key structures:** `symbol_to_file_map` (`dict[str, str]`), `alias_to_original` (`dict[str, str]`), `IMPORT_RESOLVE_CONFIG` (`dict[str, dict]`).
