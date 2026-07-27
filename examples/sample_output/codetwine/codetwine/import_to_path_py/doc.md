# Design Document: codetwine/import_to_path.py

# Overview & Purpose

`import_to_path.py` is the module responsible for **resolving import/include statements to concrete project-internal file paths and mapping imported symbol names to those files**. It bridges the gap between raw import syntax (Python `import`, Java `import`, C/C++ `#include`, JS/TS `import`) and the project's actual file set, so that downstream consumers (`file_analyzer.py`, `extractors/usage_analysis.py`, `extractors/dependency_graph.py`) can determine "which file does this imported name actually come from" without needing to know language-specific import semantics themselves.

It exists as a separate file because import resolution is a distinct, reusable, and non-trivial concern: it must handle relative vs. absolute imports, per-language candidate-path generation (index files, alternate extensions, `__init__.py`, bare includes), source-root detection (e.g. Maven/Gradle-style `src/main/java/`), and symbol-to-file mapping (including wildcard imports and same-package visibility for Java/Kotlin). Centralizing this logic avoids duplicating import-resolution rules across the three dependent modules and keeps them focused on their own responsibilities (usage tracking, dependency graph building, file analysis).

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `detect_source_roots` | `project_file_set: set[str]` | `set[str]` | Detect which known source-root prefixes (e.g. `src/main/java/`) actually appear in the project's file paths. |
| `resolve_relative_import` | `module: str, separator: str, current_dir_part_list: list[str]` | `list[str]` | Convert an import module name (relative or absolute) into a list of path components relative to the current file's directory. |
| `generate_candidate_path_list` | `base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]` | `list[str]` | Generate an ordered, deduplicated list of candidate file paths from a base path, driven declaratively by `IMPORT_RESOLVE_CONFIG` (index files, alt extensions, bare path, current-dir variants). |
| `resolve_module_to_project_path` | `module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str] \| None = None` | `str \| None` | Resolve a module/import name to an actual project file path (or `None` if it's external/stdlib), orchestrating the relative-import parsing, candidate generation, and matching (with source-root fallback). |
| `build_symbol_to_file_map` | `import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set: set[str] \| None = None` | `tuple[dict[str, str], dict[str, str]]` | Build a `(symbol_to_file_map, alias_to_original)` pair mapping imported/usable names to their defining project file, applying language-specific rules (Python module roots, Java class names, C/C++ full-file inclusion, wildcard imports, same-package visibility). |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Retrieve the tree-sitter `Language` object and import query string for a given file extension, or `(None, None)` if unsupported. |

Internal helpers `_put_symbol`, `_register_definitions_from_file`, and `_register_definitions_from_package` are not part of the public interface but support `build_symbol_to_file_map` by handling symbol-map warnings on overwrite, extracting all definitions from an included/wildcard-imported file, and resolving Java/Kotlin wildcard package imports across directory contents, respectively.

## Design Decisions

- **Pipeline decomposition**: `resolve_module_to_project_path` is explicitly split into three delegated steps (relative-import parsing → candidate generation → matching with source-root fallback), each implemented as its own function, making the resolution logic testable and traceable independently.
- **Configuration-driven, branch-free candidate generation**: `generate_candidate_path_list` avoids language-specific `if` branches by reading declarative flags/lists (`try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`) from `IMPORT_RESOLVE_CONFIG`, keeping the function language-agnostic while still supporting Python packages, JS/TS index resolution, and C/C++ bare includes.
- **Graceful degradation over exceptions**: unresolved modules (stdlib/external packages), missing config entries, or unsupported extensions all return `None`/`(None, None)` rather than raising, consistent with the module's role of best-effort resolution within an unbounded space of import statements.
- **Order-preserving deduplication**: candidate lists use `dict.fromkeys(...)` to remove duplicates while preserving priority order, ensuring the most likely candidate is checked first.
- **Language-specific post-processing isolated in `build_symbol_to_file_map`**: rather than pushing per-language quirks into the path-resolution functions, all naming/symbol semantics (Python root modules, Java trailing class names, C/C++ whole-file inclusion, Java/Kotlin wildcard imports and same-package visibility) are handled in one place, keeping `resolve_module_to_project_path` purely about path resolution.
- **Non-destructive overwrite warnings**: `_put_symbol` logs a warning instead of silently overwriting when a symbol name maps to a different file, surfacing potential ambiguity without halting analysis.

# Definition Design Specifications

## `detect_source_roots(project_file_set: set[str]) -> set[str]`

**Arguments:**
- `project_file_set`: Set of relative file paths within the project.

**Returns:** Set of source root prefixes (from `SOURCE_ROOT_PATTERNS`) that actually appear as a prefix of at least one project file. Empty set if none match.

**Responsibility:** Determines which language-specific source root conventions (e.g. Maven/Gradle-style `src/main/java/`) are actually in use in a given project, so that import resolution can later strip/prepend these prefixes correctly.

**Design decisions:** Uses a simple prefix-match against a fixed, ordered pattern list rather than filesystem heuristics, keeping detection declarative and dependent only on the known file set. Since patterns are checked independently (not mutually exclusive), multiple overlapping roots (e.g. both `src/` and `src/main/java/`) can be returned simultaneously.

**Edge cases:** If no file matches any pattern, returns an empty set, which callers treat as "skip source-root fallback."

---

## `resolve_relative_import(module: str, separator: str, current_dir_part_list: list[str]) -> list[str]`

**Arguments:**
- `module`: Module name as written in an import statement (e.g. `"..utils"`, `"./helper"`, `"os"`).
- `separator`: `"."` for Python-style, `"/"` for JS/TS-style languages.
- `current_dir_part_list`: Directory components of the file containing the import.

**Returns:** List of path components representing the resolved base path (to be joined with `"/"` by the caller).

**Responsibility:** Normalizes three distinct import styles (Python dot-relative, JS/TS slash-relative, and plain absolute/dotted) into a single directory-component representation, decoupling this language-specific parsing logic from downstream candidate generation.

**Design decisions:**
- For Python-style relative imports, dot count directly maps to directory traversal: one dot means "current directory" (no pop), and each additional dot pops one component from `current_dir_part_list`. This encodes Python's `from . import x` / `from .. import x` semantics without importing `importlib` machinery.
- For JS/TS-style relative imports, resolution is delegated to `os.path.normpath` after concatenating with the current directory, allowing correct handling of `..` traversal and redundant `./` segments in one step, then re-splitting into components.
- Absolute imports (i.e. inputs that don't match either relative-style prefix condition) are simply split by `separator`, treating the module name as a literal path.

**Edge cases:**
- If `current_dir_part_list` becomes empty during dot-popping, further pops are no-ops (guarded by `if path_part_list`), preventing errors on out-of-root relative imports.
- A module string with only dots and no trailing name (e.g. `".."`) yields a `clean_module` of `""`, so no extra components are appended beyond the popped path.
- Behavior is entirely driven by the `separator` and prefix checks; a `separator="."` input beginning with `./` (JS-style) or a `separator="/"` input beginning with `..` (Python-style) will not match relative-import branches and falls through to the absolute-import case.

---

## `generate_candidate_path_list(base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]) -> list[str]`

**Arguments:**
- `base_path`: Path derived from the module name (e.g. `"src/utils"`, `"stdio.h"`).
- `src_ext_with_dot`: Extension of the importing file, including the dot (e.g. `".py"`).
- `resolve_config`: Per-language config dict from `IMPORT_RESOLVE_CONFIG`, with flags `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`.
- `current_dir_part_list`: Directory components of the importing file.

**Returns:** Ordered, deduplicated list of candidate file paths to check against the project's file set.

**Responsibility:** Encapsulates all language-specific rules for turning a bare module path into concrete file candidates (index files, alternate extensions, bare paths, current-directory-relative variants) purely through configuration, so that no per-language conditional logic is needed elsewhere in the module.

**Design decisions:**
- Detects whether `base_path` already carries a known extension (present in `alt_ext_list`) via `os.path.splitext`, and if so, skips appending the source extension or alternate extensions — this avoids nonsensical candidates like `"stdio.h.h"` for C/C++ `#include "stdio.h"`.
- Candidate generation order is priority-based: same-extension match first, then `__init__.py` (Python packages), then index files (JS/TS directory imports), then alternate extensions, then bare path (C/C++ headers) — reflecting the likelihood of each resolution style.
- `try_current_dir` appends a second pass of all previously generated candidates prefixed with the current directory, supporting languages/resolution styles where both project-root-relative and same-directory-relative resolution must be tried.
- Final deduplication via `dict.fromkeys` preserves first-seen order, ensuring priority ordering is not disturbed by duplicate removal.

**Edge cases:** If `alt_ext_list` contains an extension equal to `src_ext_with_dot`, it is skipped when generating alt-extension candidates to avoid duplicating the same-extension candidate already added earlier.

---

## `resolve_module_to_project_path(module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str] | None = None) -> str | None`

**Arguments:**
- `module`: Module/import name, which may refer to a project file, a standard library, or an external package.
- `current_file_rel`: Relative path (from project root) of the file containing the import.
- `project_file_set`: Set of all relative file paths in the project.
- `source_root_set`: Optional set of detected source root prefixes for fallback resolution.

**Returns:** The matched project-relative file path, or `None` if the module cannot be resolved to any project file (including standard library/external package modules, which are expected to fail resolution).

**Responsibility:** Acts as the single orchestration point that determines whether an arbitrary import statement refers to an actual file within the project, combining relative-import parsing, candidate generation, and matching (with source-root fallback) into one coherent resolution pipeline.

**Design decisions:**
- Resolution config is looked up per-extension via `IMPORT_RESOLVE_CONFIG`; if the current file's extension has no configured resolver, resolution is abandoned immediately (returns `None`), since candidate generation rules are undefined for that language.
- Matching is attempted first without any source-root prefix (fast path for most languages), and only if that fails and `source_root_set` is provided, each candidate is retried with every detected source root prepended — this two-phase approach avoids unnecessary string concatenation for languages/imports that don't need root-prefix resolution (e.g. Python, JS/TS), while still supporting Java/Kotlin-style package-to-path mapping.
- Returns the *first* matching candidate found (candidates are ordered by priority from `generate_candidate_path_list`), meaning resolution is deterministic and biased toward the most "canonical" match.

**Edge cases:** External/standard-library modules naturally produce candidate paths absent from `project_file_set`, so the function correctly and silently returns `None` for them without special-casing known library names.

---

## `_put_symbol(symbol_map: dict[str, str], name: str, path: str) -> None`

**Arguments:**
- `symbol_map`: Mutable name-to-file-path map, modified in place.
- `name`: Symbol name to register.
- `path`: File path where the symbol is defined.

**Returns:** `None` (mutates `symbol_map` directly).

**Responsibility:** Centralizes symbol registration with conflict detection, ensuring that silent, hard-to-debug overwrites of a symbol's source file (e.g. due to two different imports defining the same name) are surfaced via a warning log rather than passing unnoticed.

**Design decisions:** Overwriting is still allowed (last write wins) rather than raising an error or keeping the first definition, since some legitimate cases (e.g. re-imports, shadowing) require this; the warning is purely diagnostic.

**Edge cases:** If `existing == path` (i.e. re-registering the same symbol from the same file), no warning is emitted, avoiding noise for redundant registrations.

---

## `build_symbol_to_file_map(import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set: set[str] | None = None) -> tuple[dict[str, str], dict[str, str]]`

**Arguments:**
- `import_info_list`: List of parsed import statements (`ImportInfo` objects with `module`, `names`, `alias_map`, `module_alias` attributes).
- `current_file_rel`: Relative path of the file being analyzed.
- `project_file_set`: Set of all project file paths.
- `file_ext`: Extension of the current file (without dot).
- `project_dir`: Absolute path to the project root (needed to read files for definition extraction).
- `source_root_set`: Optional detected source root prefixes.

**Returns:** A `(symbol_to_file_map, alias_to_original)` tuple — the former maps referenceable symbol names to their defining file, the latter maps import aliases to their original names.

**Responsibility:** Builds the core lookup table used by usage-tracking logic to answer "which file defines this identifier," reconciling differing symbol-exposure semantics across Python (`import X.Y.Z`, `from X import a as b`), Java/Kotlin (class-name-only references, wildcard imports, same-package visibility), and C/C++ (`#include` pulling in an entire file's definitions).

**Design decisions:**
- Per-import resolution delegates to `resolve_module_to_project_path`; unresolved modules are skipped rather than raising, since most imports (stdlib/external) are expected to be unresolvable and this isn't an error condition.
- Java/Kotlin wildcard imports (`import com.foo.*`) that fail to resolve as a single file (since `*` isn't a real filename) are specially detected (via `"*" in import_info.names` and `separator == "."`) and routed to `_register_definitions_from_package`, which treats the module string as a directory rather than a file.
- When `import_info.names` is empty (plain `import X` form), the language dispatch differs: dot-separated languages (Python/Java) register the module's root and/or leaf name (skipping root registration for Java/Kotlin, since those languages never reference bare package roots), while slash-separated languages (C/C++) register *all* definitions from the included file, reflecting that `#include` has no selective import syntax.
- When names *are* specified (`from X import a, b`), the module root is still registered via `setdefault` (not `_put_symbol`) specifically to avoid overwriting a potentially more specific/prior registration from a separate bare `import X` statement for the same root — this is an intentional asymmetry from the empty-names case.
- After processing all imports, if `SAME_PACKAGE_VISIBLE` is enabled for the language (Java/Kotlin), files in the same directory as the current file are also scanned for definitions and registered, modeling the language rule that same-package classes need no explicit import.

**Edge cases:**
- `alias_map` from each `ImportInfo` is merged into `alias_to_original` unconditionally per import, even when the module doesn't resolve to a project file — meaning aliases are tracked regardless of resolution success. *(Note: this happens only within the `if not resolved_path: continue` guard, so unresolved modules actually skip alias merging too — alias merging only occurs for resolved imports.)*
- Java/Kotlin root-package names (e.g. `com`, `org`) are deliberately never registered as standalone symbols, since these languages don't support referencing a package root without a full path.

---

## `_register_definitions_from_file(file_rel: str, project_dir: str, symbol_to_file_map: dict[str, str]) -> None`

**Arguments:**
- `file_rel`: Relative path (from project root) of the file whose definitions should be registered.
- `project_dir`: Absolute path to the project root.
- `symbol_to_file_map`: Mutable map to populate, keyed by symbol name.

**Returns:** `None` (mutates `symbol_to_file_map`).

**Responsibility:** Provides a reusable primitive for "import the whole file's public surface," used both for `#include`-style whole-file imports and for `from X import *` wildcard imports, avoiding duplication of the parse-and-extract logic.

**Design decisions:** Relies on `DEFINITION_DICTS` (extension-keyed) to determine how to extract definitions, so it transparently supports any language configured in settings without additional branching here.

**Edge cases:**
- Silently returns without registering anything if the target file doesn't exist on disk (`os.path.isfile` check) or if no `DEFINITION_DICTS` entry exists for its extension — both treated as non-fatal, expected conditions (e.g. generated/virtual files, unsupported extensions).
- Definitions without a `name` (per `DefinitionInfo.name` being falsy) are skipped, since they can't be meaningfully registered as symbols.

---

## `_register_definitions_from_package(package_dir: str, file_ext: str, project_dir: str, project_file_set: set[str], symbol_to_file_map: dict[str, str], source_root_set: set[str] | None = None) -> None`

**Arguments:**
- `package_dir`: Directory path corresponding to the wildcard-imported package (e.g. `"com/example/model"`).
- `file_ext`: Extension of the current file, used to filter which files in the directory to process.
- `project_dir`: Absolute path to the project root.
- `project_file_set`: Set of all project file paths.
- `symbol_to_file_map`: Mutable map to populate.
- `source_root_set`: Optional detected source root prefixes, used to also try root-prefixed variants of `package_dir`.

**Returns:** `None` (mutates `symbol_to_file_map`).

**Responsibility:** Handles Java/Kotlin-style wildcard package imports (`import com.example.model.*`) by locating all same-extension files directly within the corresponding directory (trying both a bare and source-root-prefixed path) and registering their definitions, since such imports have no single resolvable file target.

**Design decisions:**
- Builds a list of candidate directory prefixes (bare `package_dir/` plus each `source_root + package_dir/`) and tries all of them, since the actual on-disk location of the package may or may not include a source root like `src/main/java/`.
- Filters strictly to files *directly* under the prefix (rejects any remainder containing `/`) to avoid pulling in definitions from sub-packages, which Java wildcard imports do not include.

**Edge cases:** If multiple prefix candidates match overlapping files (e.g. both bare and source-root-prefixed prefixes happen to match the same file under unusual project layouts), the file's definitions could be processed more than once; `_put_symbol` (used internally) tolerates re-registration of identical name→path pairs without warning.

---

## `get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]`

**Arguments:**
- `file_ext`: File extension without the leading dot (e.g. `"py"`, `"java"`).

**Returns:** A `(Language, import_query_str)` tuple for supported languages, or `(None, None)` if the extension is unsupported (no import query defined, or no tree-sitter grammar registered).

**Responsibility:** Serves as the single gatekeeper for whether import analysis is possible for a given file extension, letting callers uniformly skip unsupported languages with a simple truthiness/unpacking check rather than duplicating lookup-and-fallback logic at each call site.

**Design decisions:** Checks `IMPORT_QUERIES` first (returning early if absent) before attempting the `TREE_SITTER_LANGUAGES` lookup, treating "no import query configured" and "no language registered" as equally valid reasons to report unsupported, both collapsed to the same `(None, None)` sentinel for caller simplicity.

**Edge cases:** A `KeyError` from `TREE_SITTER_LANGUAGES[file_ext]` (extension has an import query but no registered language — an inconsistent config state) is caught and also converted to `(None, None)` rather than propagating.

# Dependency Description

## Dependencies (what this file uses)

- **`codetwine.config.settings` (SOURCE_ROOT_PATTERNS, IMPORT_RESOLVE_CONFIG, IMPORT_QUERIES, SAME_PACKAGE_VISIBLE, DEFINITION_DICTS, TREE_SITTER_LANGUAGES)**: Provides the declarative, per-language configuration that drives nearly all logic in this file. `SOURCE_ROOT_PATTERNS` is used by `detect_source_roots` to identify known source-root prefixes (e.g. Java/Kotlin/Scala source layouts). `IMPORT_RESOLVE_CONFIG` supplies per-extension resolution rules (separator, index files, alternate extensions, bare-path/current-dir behavior) consumed by `resolve_relative_import`, `generate_candidate_path_list`, and `resolve_module_to_project_path`. `IMPORT_QUERIES` and `TREE_SITTER_LANGUAGES` are used by `get_import_params` to obtain the tree-sitter `Language` and import query for a given extension. `SAME_PACKAGE_VISIBLE` tells `build_symbol_to_file_map` whether same-directory files should be treated as implicitly visible symbols (Java/Kotlin-style). `DEFINITION_DICTS` is used by `_register_definitions_from_file` to select the correct node-type-to-name mapping for extracting definitions from a given file's extension.

- **`codetwine.parsers.ts_parser.parse_file`**: Used by `_register_definitions_from_file` to parse a resolved project file (e.g. an imported header or wildcard-imported package file) into an AST root node, so its definitions can be extracted and registered.

- **`codetwine.extractors.definitions.extract_definitions`**: Used together with `parse_file` inside `_register_definitions_from_file` to walk the parsed AST and obtain the list of definition names (functions, classes, etc.) defined in a resolved file, which are then registered into the symbol-to-file map.

## Dependents (what uses this file)

- **`codetwine/file_analyzer.py`**: Uses `get_import_params` to obtain the tree-sitter language and import query needed to extract imports from a target file, `detect_source_roots` to compute source-root prefixes for the project before resolving imports, and `build_symbol_to_file_map` to convert extracted import statements into a symbol-to-file mapping used for downstream usage tracking.

- **`codetwine/extractors/usage_analysis.py`**: Uses `get_import_params` to retrieve import-extraction parameters for caller files, and `resolve_module_to_project_path` to determine whether a caller's import statement resolves to a specific target file, in order to confirm usage relationships between files.

- **`codetwine/extractors/dependency_graph.py`**: Uses `detect_source_roots` to compute project source roots once for the whole project, `get_import_params` to obtain per-file import-extraction parameters, and `resolve_module_to_project_path` to resolve each file's imports into project-internal file paths for constructing the dependency graph's callee relationships.

The dependency direction is unidirectional: `import_to_path.py` depends on configuration and parsing/extraction utilities in `codetwine.config.settings`, `codetwine.parsers.ts_parser`, and `codetwine.extractors.definitions`, while `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` depend on `import_to_path.py` for import resolution and symbol mapping; none of these dependents are themselves used by `import_to_path.py`.

# Data Flow

## Input Data

| Source | Format | Description |
|---|---|---|
| `project_file_set` | `set[str]` | Relative file paths within the project (e.g. `"src/app/utils.py"`) |
| `module` (import statement) | `str` | Module name as written in source code (e.g. `"..utils"`, `"os"`, `"com.example.Foo"`, `"./helper"`, `"stdio.h"`) |
| `current_file_rel` | `str` | Relative path of the file currently being analyzed |
| `import_info_list` | `list[ImportInfo]` (external, from `extractors`) | Parsed import statements, each with `module`, `names`, `alias_map`, `module_alias` |
| `file_ext` | `str` | Extension of current file (no dot) |
| `project_dir` | `str` | Absolute path to project root |
| Config dicts | `dict` (from `settings.py`) | `IMPORT_RESOLVE_CONFIG`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `SAME_PACKAGE_VISIBLE`, `SOURCE_ROOT_PATTERNS`, `TREE_SITTER_LANGUAGES` — static, per-language rules |

## Main Transformation Flow

```
project_file_set ──► detect_source_roots ──► source_root_set (e.g. {"src/main/java/"})

module + current_dir_part_list
        │
        ▼
resolve_relative_import  ──►  path_part_list (list of directory components)
        │
        ▼
"/".join(path_part_list) ──► base_path (string)
        │
        ▼
generate_candidate_path_list ──► candidate_path_list (list[str], ordered, deduped)
        │
        ▼
match against project_file_set (optionally retried with source_root_set prefixes)
        │
        ▼
resolved_path (str | None)  ──►  resolve_module_to_project_path (top-level entry)
```

This resolved path is then consumed by `build_symbol_to_file_map`, which iterates all imports of a file and, per language convention (Python/Java/C-C++), decides which symbol names to bind to that resolved file:

```
for import_info in import_info_list:
    resolved_path = resolve_module_to_project_path(...)
    ├─ if None and wildcard import (java/kt) → scan package dir → register all defs
    ├─ if resolved_path:
    │     ├─ names present → register each name (or all defs if "*")
    │     ├─ names absent  → derive symbol from module root/leaf (python/java) 
    │                         or register all defs from file (C/C++ #include)
    │     └─ record alias_map into alias_to_original
    └─ (after loop) same-package visible files (java/kt) → register their defs too
```

Definition extraction (`_register_definitions_from_file`, `_register_definitions_from_package`) delegates to external pipeline:

```
file_rel ──► parse_file(abs_path) ──► root_node
root_node + DEFINITION_DICTS[ext] ──► extract_definitions ──► list[DefinitionInfo]
DefinitionInfo.name ──► _put_symbol(symbol_to_file_map, name, file_rel)
```

## Output Data

| Function | Output | Format | Destination |
|---|---|---|---|
| `detect_source_roots` | `source_root_set` | `set[str]` | passed into resolution functions, `file_analyzer.py`, `dependency_graph.py` |
| `resolve_relative_import` | path components | `list[str]` | internal, feeds `generate_candidate_path_list` |
| `generate_candidate_path_list` | ordered candidates | `list[str]` | internal, feeds matching step |
| `resolve_module_to_project_path` | resolved project file path or `None` | `str \| None` | `build_symbol_to_file_map`, `usage_analysis.py`, `dependency_graph.py` |
| `build_symbol_to_file_map` | `(symbol_to_file_map, alias_to_original)` | `tuple[dict[str,str], dict[str,str]]` | `file_analyzer.py` (usage tracking) |
| `get_import_params` | `(Language, query_str)` or `(None, None)` | `tuple` | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py` (drives tree-sitter parsing/import extraction) |

## Key Data Structures

| Structure | Fields / Shape | Purpose |
|---|---|---|
| `source_root_set` | `set[str]` of prefixes like `"src/main/java/"` | Fallback prefixes tried when a candidate path doesn't directly match project files (e.g. Java package roots) |
| `path_part_list` | `list[str]` of directory/file name components | Intermediate representation of a resolved module path before joining |
| `candidate_path_list` | `list[str]`, order = priority (same-ext file → `__init__.py` → index files → alt extensions → bare path → current-dir-relative variants), deduplicated | All plausible file locations for a module, checked in order against `project_file_set` |
| `resolve_config` (`IMPORT_RESOLVE_CONFIG[ext]`) | `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` | Declarative per-language rules driving candidate generation, avoiding language-specific branching |
| `symbol_to_file_map` | `dict[str, str]` — symbol name → defining file path | Central lookup used later to trace "which file defines this used name" during usage analysis |
| `alias_to_original` | `dict[str, str]` — alias name → original name | Maps renamed imports (`import X as Y`, `from X import a as b`) back to their original identifiers |
| `import_info` (external `ImportInfo`) | `module`, `names`, `alias_map`, `module_alias` | Parsed import statement passed in from `extractors`; drives which symbols get registered and how |
| `DefinitionInfo` (external) | `name`, `type`, `start_line`, `end_line` | Used to populate `symbol_to_file_map` when an entire file's definitions must be registered (wildcard/`#include`/same-package visibility) |

# Error Handling

This module follows a **graceful degradation** strategy: import resolution and symbol mapping are inherently best-effort operations (since imports may reference standard libraries, external packages, or unresolvable paths), so the module favors returning `None`/empty results over raising exceptions, allowing callers to continue processing other files. The only exception is a single non-fatal warning log for symbol overwrites, and one place where a missing external config silently disables processing for that extension.

| Error Pattern | Handling | Impact |
|---|---|---|
| Unresolvable module (not a project-internal file) | `resolve_module_to_project_path` returns `None` after exhausting all candidate paths and source-root fallbacks | Caller (`build_symbol_to_file_map`) simply skips registering that import; no exception raised |
| Missing/unsupported language config (`IMPORT_RESOLVE_CONFIG.get(src_ext)` returns falsy) | Early return `None` from `resolve_module_to_project_path` | Import resolution for that file extension is silently skipped |
| Unsupported extension in `get_import_params` (`IMPORT_QUERIES.get` returns falsy, or `TREE_SITTER_LANGUAGES[file_ext]` raises `KeyError`) | Returns `(None, None)`; `KeyError` is explicitly caught | Caller detects `(None, None)` and skips import analysis entirely for that file/language |
| Symbol name collision (same name mapped to a different file) in `_put_symbol` | Logs a `logger.warning` and overwrites the existing mapping with the new path | Non-fatal; processing continues, but earlier mapping is lost (last-write-wins) |
| Non-existent file passed to `_register_definitions_from_file` (`os.path.isfile` check fails) | Function returns early without registering anything | Silent no-op; no symbols registered for that file, no error surfaced |
| Missing definition dict for a resolved file's extension (`DEFINITION_DICTS.get` returns falsy) | Function returns early | Silent no-op; definitions from that file type are not extracted |
| Errors from downstream parsing (`parse_file`) or extraction (`extract_definitions`) | Not caught; propagate naturally (e.g., file I/O errors, `KeyError` for unsupported languages in the parser's internal language map) | Fail-fast for unexpected structural/environment issues (e.g., corrupted file, unregistered extension), since these indicate a misconfiguration outside the scope of normal "unresolvable import" cases |

**Design considerations:**
- The distinction between "expected non-matches" (unresolvable imports, missing files, unsupported extensions) and "unexpected failures" (parser/config errors) is intentional: the former are handled defensively with early returns/`None`, since they are a normal and frequent occurrence given that most imported module names are standard library or external packages; the latter are allowed to propagate, since they typically indicate a configuration or environment problem that should not be silently masked.
- The single `logger.warning` call in `_put_symbol` reflects a conscious tradeoff to surface potentially meaningful ambiguity (symbol name collisions across files) without halting analysis, since best-effort static analysis tools are expected to encounter such conflicts routinely.

# Summary

`import_to_path.py` resolves language-agnostic import/include statements (Python, Java, C/C++, JS/TS) to concrete project file paths and maps imported symbols to their defining files. Key APIs: `detect_source_roots`, `resolve_relative_import`, `generate_candidate_path_list`, `resolve_module_to_project_path`, `build_symbol_to_file_map`, `get_import_params`. Uses config-driven (non-branching) resolution via `IMPORT_RESOLVE_CONFIG`/`SOURCE_ROOT_PATTERNS`, returns `None`/empty results for unresolved imports instead of raising. Outputs: `symbol_to_file_map`, `alias_to_original`, resolved paths—consumed by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`.
