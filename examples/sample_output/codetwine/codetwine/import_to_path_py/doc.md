# Design Document: codetwine/import_to_path.py

# Overview & Purpose

## 1. Module Summary
Resolves import/include statement module names into concrete project-internal file paths and builds a symbol-to-file lookup map used to trace where imported names are actually defined.

## 2. When to Use This Module
- When you have a project's file set and need to know which multi-language source root conventions (e.g. `src/main/java/`) are actually in use: call `detect_source_roots(project_file_set)` to get the applicable prefix set.
- When you have an import statement's module string (e.g. `"..utils"`, `"com.example.Foo"`, `"./helper"`) and need to know which project file it refers to: call `resolve_module_to_project_path(module, current_file_rel, project_file_set, source_root_set)`, which returns the matching relative path or `None` if it's an external/stdlib module.
- When analyzing usages of imported symbols and you need a mapping from imported names (or aliases) to the file that defines them: call `build_symbol_to_file_map(import_info_list, current_file_rel, project_file_set, file_ext, project_dir, source_root_set)`, which returns `(symbol_to_file_map, alias_to_original)` for use by usage-tracking logic.
- When you need the tree-sitter `Language` object and import query string required to parse import statements for a given file extension: call `get_import_params(file_ext)`.
- Typical callers: `file_analyzer.py` (per-file import/usage analysis), `pipeline.py` (project-wide source root detection), `extractors/usage_analysis.py` and `extractors/dependency_graph.py` (resolving caller/callee import relationships).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `detect_source_roots` | `project_file_set: set[str]` | `set[str]` | Determine which known source root prefixes (from `SOURCE_ROOT_PATTERNS`) actually occur among project file paths. |
| `resolve_relative_import` | `module: str, separator: str, current_dir_part_list: list[str]` | `list[str]` | Convert a relative or absolute import module name into path components relative to the current file's directory. |
| `generate_candidate_path_list` | `base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]` | `list[str]` | Generate ordered, deduplicated candidate file paths (index files, alt extensions, bare path, current-dir variants) from a base path per language config. |
| `resolve_module_to_project_path` | `module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str] \| None` | `str \| None` | Resolve an import's module name to an actual file path in the project, or `None` if it's external/unresolvable. |
| `build_symbol_to_file_map` | `import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set: set[str] \| None` | `tuple[dict[str, str], dict[str, str]]` | Build a name→defining-file map (and alias→original map) from a file's import statements, including wildcard imports, same-package visibility, and language-specific naming rules. |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Retrieve the tree-sitter `Language` and import query string for a file extension, or `(None, None)` if unsupported. |

## 4. Design Decisions
- Module resolution is split into three explicit, independently testable steps (relative-path resolution → candidate generation → project-set matching), each delegated to its own function rather than inlined.
- Language-specific behavior (index files, alt extensions, `__init__.py`, current-dir fallback, bare-path includes) is driven entirely by declarative `IMPORT_RESOLVE_CONFIG` entries, avoiding per-language conditional branches in `generate_candidate_path_list`.
- Source-root prefixing (e.g. Java's `src/main/java/`) is applied only as a fallback after direct candidate matching fails, keeping the common case simple while still supporting layered build directory conventions.
- Symbol registration intentionally differs by import style: Python registers module roots (and leaves) for attribute-style access, Java/Kotlin registers only the trailing class name (skipping package roots), and C/C++ `#include` registers all top-level definitions from the included file, reflecting each language's actual reference semantics.
- `_put_symbol` centralizes overwrite detection with a warning log, so conflicting symbol definitions across files are surfaced rather than silently and inconsistently overwritten.

# Definition Design Specifications

## Module-level constant

### `logger`
- **Signature:** `logger: logging.Logger = logging.getLogger(__name__)`
- **Responsibility:** Provides a module-scoped logger for warnings emitted during symbol map construction (e.g., symbol overwrite conflicts).
- **When to use:** Used internally by `_put_symbol`; not intended for external use.

---

## `detect_source_roots(project_file_set: set[str]) -> set[str]`

- **Responsibility:** Determines which of the known source-root prefixes (e.g. `"src/main/java/"`) are actually present in a given project, so downstream import resolution can try these prefixes without assuming a fixed project layout.
- **When to use:** Called once per project (e.g. in a pipeline/dependency-graph build step) before resolving any imports, so the resulting set can be reused across all files.
- **Design decisions:** Iterates `SOURCE_ROOT_PATTERNS` in the config-defined order and stops scanning a pattern as soon as one matching file is found (early `break`), trading a full match count for early exit performance.
- **Constraints & edge cases:** Returns an empty set if no pattern matches any file. Patterns are matched purely as string prefixes on `project_file_set` entries (paths assumed to use `/` separators).

---

## `resolve_relative_import(module: str, separator: str, current_dir_part_list: list[str]) -> list[str]`

- **Responsibility:** Normalizes an import/include module name (Python dotted-relative, JS/TS `./`/`../`-relative, or absolute) into a flat list of directory/path components relative to the project root.
- **When to use:** Called as the first step of `resolve_module_to_project_path` whenever a raw import statement's module string needs to be turned into a candidate file path.
- **Design decisions:**
  - Python-style handling: counts leading dots to determine "how many directories to go up" (1 dot = current dir, additional dots pop one path component each), then appends the remaining dotted segments as new components.
  - JS/TS-style handling: rather than manually popping components, builds a combined string and delegates normalization to `os.path.normpath`, then converts backslashes to forward slashes for cross-platform consistency.
  - Absolute imports (neither Python relative dot-prefix nor JS/TS relative slash-prefix) are handled by a simple split, with no directory-context adjustment.
- **Constraints & edge cases:**
  - If `current_dir_part_list` is shorter than the number of "up" traversals requested, the pop loop stops silently once the list is empty (no error raised, no negative index).
  - Assumes `separator` is either `"."` or `"/"`; only these two conditional branches exist, otherwise falls through to the absolute-import case.
  - Windows-style backslashes are normalized only in the JS/TS branch.

---

## `generate_candidate_path_list(base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]) -> list[str]`

- **Responsibility:** Expands a single resolved base path into an ordered list of plausible on-disk file paths, according to declarative per-language rules in `resolve_config` (index files, alternate extensions, bare paths, current-directory-relative variants), keeping the function itself language-agnostic.
- **When to use:** Called as the second step of `resolve_module_to_project_path`, immediately after `resolve_relative_import` produces `base_path`.
- **Design decisions:**
  - Detects whether `base_path` already carries one of the language's recognized extensions (`alt_ext_list`) via `os.path.splitext`; if so, treats `base_path` as a complete file reference and skips extension-appending logic (avoids invalid double extensions like `stdio.h.h`).
  - Candidate generation order is fixed and priority-encoded: (1) same-extension-as-current-file variant or as-is known-extension path, (2) `__init__.py` package variant, (3) directory index files, (4) alternate extensions (skipping duplicate of current file's extension), (5) bare path, (6) current-directory-relative duplicates of all the above.
  - `try_current_dir` candidates are generated by re-iterating a *snapshot* (`list(root_candidate_list)`) of the already-built root-relative list, then prefixing each with the current directory — ensuring root-relative candidates are still tried first and current-dir-relative ones don't recursively include each other.
  - De-duplicates the final list using `dict.fromkeys` to preserve first-seen order while removing duplicates cheaply.
- **Constraints & edge cases:** All config keys (`try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`) default to falsy/empty values if absent from `resolve_config`, so a minimal config produces only the single same-extension candidate.

---

## `resolve_module_to_project_path(module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str] | None = None) -> str | None`

- **Responsibility:** Central entry point that determines whether an imported module name refers to a file inside the project, and if so, returns that file's project-relative path; returns `None` for standard-library/external modules or anything unresolvable.
- **When to use:** Called once per import statement whenever the caller needs to know if that import is project-internal (used by `build_symbol_to_file_map`, dependency-graph building, and usage analysis across the codebase).
- **Design decisions:**
  - Extension of `current_file_rel` selects the `IMPORT_RESOLVE_CONFIG` entry, so unsupported/unknown extensions cause an immediate `None` return without further processing.
  - Delegates the three-stage pipeline (relative-import parsing → candidate generation → matching) to the two dedicated helper functions, keeping this function focused on orchestration and fallback logic.
  - Fallback: if no direct candidate exists in `project_file_set`, retries each candidate with every prefix in `source_root_set` prepended — this specifically supports Java/Kotlin-style projects where source files live under nested root directories (e.g. `src/main/java/`) not reflected in the package name itself.
- **Constraints & edge cases:**
  - Returns `None` immediately if the current file's extension has no entry in `IMPORT_RESOLVE_CONFIG` — regardless of whether the module itself could theoretically resolve.
  - Source-root fallback is only attempted if `source_root_set` is truthy (non-`None` and non-empty); otherwise it's skipped entirely.
  - First-matching-candidate wins; no attempt is made to disambiguate multiple valid matches.

---

## `_put_symbol(symbol_map: dict[str, str], name: str, path: str) -> None`

- **Responsibility:** Centralizes writes into a symbol→file map with a side-effect of warning when an existing symbol mapping to a different file is silently overwritten, aiding debuggability of ambiguous symbol resolution.
- **When to use:** Called anywhere a name-to-definition-file mapping needs to be registered or updated (import registration, wildcard-import expansion, same-package visibility registration).
- **Design decisions:** Overwrite is always allowed (last write wins); the function only logs a warning rather than raising or preventing the overwrite, favoring resilience over strict correctness.
- **Constraints & edge cases:** No warning is logged if `existing == path` (idempotent re-registration) or if there was no prior entry.

---

## `build_symbol_to_file_map(import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set: set[str] | None = None) -> tuple[dict[str, str], dict[str, str]]`

- **Responsibility:** Converts a file's parsed import statements into two lookup dicts — imported/aliased symbol name → defining project file, and alias → original name — used later for usage tracking (matching identifier usages in code back to the file that defines them).
- **When to use:** Called once per analyzed source file, after imports have been extracted via `extract_imports`, before scanning the file body for symbol usages.
- **Design decisions:**
  - Uses `resolve_config.get("separator", ".")` from `IMPORT_RESOLVE_CONFIG` to branch behavior by import syntax family (dot-separated languages like Python/Java vs. slash-separated C/C++-style `#include`), rather than checking `file_ext` directly, keeping the language-specific logic table-driven.
  - Java/Kotlin wildcard imports (`import com.foo.*` where `"*"` appears in `import_info.names`) that fail direct file resolution are treated as package-directory imports and delegated to `_register_definitions_from_package`; this is only attempted when `separator == "."`, avoiding accidental slash-based wildcard handling.
  - `from X import *`-style entries (`"*"` in explicit `names`, not a whole-module wildcard) trigger `_register_definitions_from_file` to pull in every top-level definition from the resolved module file.
  - When `import_info.names` is empty (bare `import X` form), behavior differs by separator:
    - Dot-separated: registers `module_alias` if present; otherwise registers both the root package segment (skipped for Java/Kotlin, since bare package roots aren't referenceable there) and the trailing segment (useful for Java's direct class-name references), only registering the trailing part if it differs from the root to avoid redundant duplicate registration.
    - Slash-separated (C/C++): treats the include as pulling in the entire file's definitions via `_register_definitions_from_file`, since `#include` has no selective-name import concept.
  - When names *are* present, additionally registers the module root via `setdefault` (not `_put_symbol`) specifically to avoid clobbering an existing higher-priority registration from a separate bare `import mylib` statement for the same root — Java/Kotlin are excluded from this since package roots aren't standalone-referenceable there.
  - After processing all imports, if `SAME_PACKAGE_VISIBLE[file_ext]` is true (Java/Kotlin), scans `project_file_set` for other files in the *same directory* with the same extension and registers all of their top-level definitions — this models same-package class visibility that requires no explicit import statement.
- **Constraints & edge cases:**
  - Unresolvable modules (standard library/external packages) are silently skipped, except for the Java/Kotlin wildcard fallback path.
  - `resolve_config` defaults to `{}` and `separator` defaults to `"."` if `file_ext` is missing from `IMPORT_RESOLVE_CONFIG`, meaning unconfigured extensions are treated as dot-separated languages by default.
  - Same-package visibility scan explicitly excludes the current file itself (`project_file == current_file_rel`) and matches directories via `os.path.dirname` equality (exact match, not prefix).

---

## `_register_definitions_from_file(file_rel: str, project_dir: str, symbol_to_file_map: dict[str, str]) -> None`

- **Responsibility:** Parses a specific project file and registers every top-level definition name it contains into the symbol map, modeling "whole-file import" semantics (C/C++ `#include`, Python `from X import *`, Java same-package visibility, wildcard package imports).
- **When to use:** Called whenever an import construct pulls in an entire file's namespace rather than individual named symbols.
- **Design decisions:** Looks up `DEFINITION_DICTS` by the *target* file's own extension (`file_rel`), not the current file's extension, since the included/imported file may theoretically differ in language handling; delegates nested/member filtering to `_select_top_level_definitions` so class methods/fields aren't wrongly registered as standalone importable symbols.
- **Constraints & edge cases:**
  - No-ops silently if the resolved absolute path doesn't exist on disk (`os.path.isfile` check) or if there is no `DEFINITION_DICTS` entry for the file's extension.
  - Relies on `parse_file`'s module-level caching, so repeated calls for the same file across multiple imports/callers are cheap after the first parse.
  - Definitions with an empty/falsy `name` are skipped.

---

## `_select_top_level_definitions(definition_list: list[DefinitionInfo]) -> list[DefinitionInfo]`

- **Responsibility:** Filters a line-ordered list of `DefinitionInfo` down to only the outermost (non-nested) definitions, preventing class members from being mistakenly treated as independently importable top-level symbols.
- **When to use:** Called internally by `_register_definitions_from_file` right after `extract_definitions` produces a flat, nested-inclusive list.
- **Design decisions:** Uses a single forward pass tracking `covered_end` (the end line of the most recently selected outer definition) rather than building a tree structure; any definition whose `start_line` falls within `[previous_start, covered_end]` is treated as nested and skipped. This relies on the precondition that `definition_list` is sorted ascending by `start_line` (as guaranteed by `extract_definitions`'s return contract).
- **Constraints & edge cases:** Assumes non-overlapping sibling ranges once a top-level definition is selected; does not handle out-of-order input correctly since it does not re-sort.

---

## `_register_definitions_from_package(package_dir: str, file_ext: str, project_dir: str, project_file_set: set[str], symbol_to_file_map: dict[str, str], source_root_set: set[str] | None = None) -> None`

- **Responsibility:** Implements Java/Kotlin wildcard-import (`import com.example.model.*`) resolution by locating all same-extension files directly inside a package directory (trying both the bare path and each detected source-root-prefixed variant) and registering their definitions.
- **When to use:** Called from `build_symbol_to_file_map` only when a wildcard import's module name could not be resolved to a single file directly.
- **Design decisions:**
  - Builds a `prefix_list` combining the bare package path and, if available, each `source_root_set` entry prepended to it — since the actual file layout may not literally match the package's dotted path (e.g. Maven/Gradle's `src/main/java/` convention).
  - Restricts matches to files *directly* under the package directory by checking that the remainder (path after the prefix) contains no further `/`, explicitly excluding sub-package files to correctly model Java's non-recursive wildcard import semantics.
  - Iterates all `prefix_list` entries even after finding matches under one prefix (no early exit across prefixes), so files matched by multiple prefix variants could be processed more than once (idempotent since `_put_symbol` handles re-registration).
- **Constraints & edge cases:** If `source_root_set` is `None` or empty, only the bare `package_dir + "/"` prefix is tried. Files whose extension does not equal `file_ext` are ignored even if they otherwise match the directory.

---

## `get_import_params(file_ext: str) -> tuple[Language, str] | tuple[None, None]`

- **Responsibility:** Provides the tree-sitter `Language` object and import-extraction query string needed to run import analysis for a given file extension, acting as a single guarded lookup point across two config dicts.
- **When to use:** Called at the start of per-file import analysis (in `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) to determine whether import parsing is supported for that file type before proceeding.
- **Design decisions:** Checks `IMPORT_QUERIES` first and short-circuits with `(None, None)` if there's no query for the extension, avoiding an unnecessary lookup into `TREE_SITTER_LANGUAGES`; the `TREE_SITTER_LANGUAGES` lookup uses a `try/except KeyError` (rather than `.get`) to explicitly signal that a missing language entry despite a defined query is treated as an unsupported/misconfigured case, also yielding `(None, None)`.
- **Constraints & edge cases:** Returns `(None, None)` uniformly for both "extension not configured for import analysis" and "extension configured for queries but missing from the language map," so callers cannot distinguish between these two failure causes from the return value alone.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/import_to_path.py` → `codetwine/config/settings.py` (`SOURCE_ROOT_PATTERNS`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `TREE_SITTER_LANGUAGES`) : obtains per-language configuration needed to detect source root prefixes, resolve module names to file paths based on language-specific import rules, determine whether unimported same-package symbols are visible, look up the definition-node dictionary for a given extension, retrieve the tree-sitter import query string, and get the tree-sitter `Language` object for parsing.

- `codetwine/import_to_path.py` → `codetwine/parsers/ts_parser.py` (`parse_file`) : parses a project file into its AST root node (and byte content) so that definitions can be extracted from files referenced by `#include`, `import *`, or same-package visibility rules.

- `codetwine/import_to_path.py` → `codetwine/extractors/definitions.py` (`extract_definitions`, `DefinitionInfo`) : extracts the list of definitions (functions, classes, structs, etc.) from a parsed file's AST, using `DefinitionInfo` as the typed representation of each extracted definition, in order to register all symbol names defined in an included/wildcard-imported/same-package file.

### Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/import_to_path.py` (`get_import_params`, `build_symbol_to_file_map`) : obtains the tree-sitter `Language` and import query for a file extension, then builds the symbol-to-file mapping (and alias mapping) used to trace which file each used name was imported from.

- `codetwine/pipeline.py` → `codetwine/import_to_path.py` (`detect_source_roots`) : detects the set of source root prefixes present in the project once, to be passed into per-file import resolution.

- `codetwine/extractors/usage_analysis.py` → `codetwine/import_to_path.py` (`resolve_module_to_project_path`, `get_import_params`) : resolves a caller file's import module names to project file paths to determine whether a caller actually imports the target file, and retrieves import query parameters needed to parse a caller's import statements.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path.py` (`detect_source_roots`, `get_import_params`, `resolve_module_to_project_path`) : detects source root prefixes for the whole project, retrieves import query parameters per file, and resolves each file's import statements to project-internal file paths in order to build the callee/dependency graph between files.

### Dependency Direction

All relationships are **unidirectional**. `codetwine/import_to_path.py` depends on `codetwine/config/settings.py`, `codetwine/parsers/ts_parser.py`, and `codetwine/extractors/definitions.py` for configuration, parsing, and definition-extraction utilities, without any reverse dependency. Conversely, `codetwine/file_analyzer.py`, `codetwine/pipeline.py`, `codetwine/extractors/usage_analysis.py`, and `codetwine/extractors/dependency_graph.py` depend on `codetwine/import_to_path.py` for import resolution and symbol-mapping functionality, with no dependency flowing back from `import_to_path.py` to any of these dependents.

# Data Flow

## 1. Inputs

This module receives data from three sources:

- **Caller-provided arguments** (from `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, `dependency_graph.py`):
  - `project_file_set: set[str]` — all relative file paths ("path/to/file.ext" format) within the project, typically built once per pipeline run.
  - `module: str` — a raw module/import name extracted from source code (e.g. `"..utils"`, `"os"`, `"com.example.Foo"`, `"./helper"`, `"stdio.h"`).
  - `current_file_rel: str` — relative path of the file currently being analyzed.
  - `import_info_list` — a list of `ImportInfo`-like objects (external type, produced by `extract_imports`), each carrying `.module`, `.names`, `.alias_map`, `.module_alias`.
  - `file_ext: str` — extension (no dot) of the file under analysis.
  - `project_dir: str` — absolute path to the project root, used to resolve files on disk.
  - `source_root_set: set[str] | None` — optional set of detected source-root prefixes.

- **Config values** (from `codetwine/config/settings.py`, loaded at import time):
  - `SOURCE_ROOT_PATTERNS: list[str]` — candidate source-root prefixes to test.
  - `IMPORT_RESOLVE_CONFIG: dict[str, dict]` — per-extension resolution rules (separator, try_init, index_ext_list, alt_ext_list, try_bare_path, try_current_dir).
  - `SAME_PACKAGE_VISIBLE: dict[str, bool]` — per-extension flag for same-directory implicit visibility (Java/Kotlin).
  - `DEFINITION_DICTS: dict[str, dict]` — per-extension AST node → name-extraction mapping.
  - `IMPORT_QUERIES: dict[str, str|None]` — per-extension tree-sitter query strings for import statements.
  - `TREE_SITTER_LANGUAGES: dict[str, Language]` — per-extension tree-sitter `Language` objects.

- **File reads (indirect, via dependencies)**:
  - `parse_file(abs_path)` reads and parses on-disk source files (called when registering definitions from a resolved file, e.g. for `#include`, `import *`, or same-package visibility).

## 2. Transformation Overview

The module implements a layered pipeline that converts raw import statement text into structured "symbol → defining file" mappings, used downstream for usage/dependency tracking.

**Stage 1 — Source root detection** (`detect_source_roots`)
Input: `project_file_set`. Each pattern in `SOURCE_ROOT_PATTERNS` is tested as a prefix against every file path. Patterns matching at least one file are collected into `source_root_set`. This is a one-time, per-project computation feeding later resolution steps.

**Stage 2 — Module name → path components** (`resolve_relative_import`)
Input: `module` string, `separator` ("." or "/"), `current_dir_part_list` (directory of the current file, split into parts).
- If Python-style relative (`separator == "."` and leading dots): dot count determines how many directory levels to pop from `current_dir_part_list`; remaining dotted name is appended as path parts.
- If JS/TS-style relative (`separator == "/"` and `./` or `../` prefix): the module path is concatenated onto the current directory and normalized (`..` segments collapsed) via `os.path.normpath`.
- Otherwise (absolute import): the module string is simply split by `separator`.
Output: `list[str]` of path components, later joined with `/` into `base_path`.

**Stage 3 — Candidate path generation** (`generate_candidate_path_list`)
Input: `base_path`, current file's extension, `resolve_config` dict, `current_dir_part_list`.
Produces an ordered, deduplicated list of file path candidates by combining:
- base_path + current file's extension (unless base_path already has a known alt extension),
- `__init__.py` variant if `try_init`,
- `/index<ext>` variants for each `index_ext_list` entry,
- alternate extensions from `alt_ext_list` (skipping duplicates of current ext or already-known ext),
- bare `base_path` if `try_bare_path`,
- each of the above candidates also re-prefixed with the current directory if `try_current_dir`.
Output: ordered `list[str]` of unique candidate relative paths.

**Stage 4 — Matching against project files** (`resolve_module_to_project_path`)
Orchestrates Stages 2–3 using `IMPORT_RESOLVE_CONFIG[src_ext]`, then:
- checks each candidate against `project_file_set` directly;
- if none match and `source_root_set` is supplied, retries each candidate prefixed with each source root.
Output: a single matched project-relative path, or `None` if the module is external/unresolved.

**Stage 5 — Symbol table construction** (`build_symbol_to_file_map`)
Iterates `import_info_list`; for each `ImportInfo`:
- Resolves `.module` via Stage 4.
- If unresolved but it's a Java/Kotlin wildcard import (`"*"` in `.names`, separator `.`), delegates to `_register_definitions_from_package` to scan a package directory.
- If resolved:
  - For each name in `.names`: if `"*"`, delegates to `_register_definitions_from_file` (parses file, extracts all top-level definitions); otherwise registers `name → resolved_path` via `_put_symbol`.
  - Merges `.alias_map` into `alias_to_original`.
  - If `.names` is empty, derives symbol(s) from the module string itself: root/leaf parts for dotted imports (Python/Java rules differ), or all file definitions for C/C++ (`separator == "/"`).
  - If `.names` is non-empty, also registers the module root as a fallback (`setdefault`), skipped for Java/Kotlin.
- After processing all imports, if `SAME_PACKAGE_VISIBLE[file_ext]` is true, scans `project_file_set` for same-directory, same-extension files (excluding the current file) and registers their definitions too, enabling implicit same-package symbol resolution.

**Sub-stage — Definition extraction from a file** (`_register_definitions_from_file`)
Given a resolved file path: verifies it exists on disk, looks up `DEFINITION_DICTS[ext]`, calls `parse_file` (tree-sitter parse) then `extract_definitions`, filters to top-level (non-nested) definitions via `_select_top_level_definitions`, and registers each definition's name into `symbol_to_file_map`.

**Sub-stage — Package-wide definition extraction** (`_register_definitions_from_package`)
For wildcard imports: builds candidate directory prefixes (bare + source-root-prefixed), scans `project_file_set` for files directly under that directory (not sub-packages) matching `file_ext`, and calls `_register_definitions_from_file` for each.

**Stage 6 — Language/query lookup** (`get_import_params`)
Independent utility: looks up `IMPORT_QUERIES[file_ext]` and `TREE_SITTER_LANGUAGES[file_ext]`; returns `(None, None)` if either is missing, otherwise `(Language, query_str)`.

There is no async/parallel processing in this module; all operations are sequential, iterating over sets/lists in-process.

## 3. Outputs

- `detect_source_roots` → `set[str]`: source-root prefixes present in the project (e.g. `{"src/main/java/"}`), consumed by later resolution calls.
- `resolve_relative_import` → `list[str]`: directory/path components (intermediate, feeds `generate_candidate_path_list`).
- `generate_candidate_path_list` → `list[str]`: ordered, deduplicated candidate relative file paths (intermediate).
- `resolve_module_to_project_path` → `str | None`: a single resolved project-relative file path, or `None`.
- `build_symbol_to_file_map` → `tuple[dict[str, str], dict[str, str]]`:
  - `symbol_to_file_map`: imported/defined symbol name → file path where it is defined.
  - `alias_to_original`: alias name → original imported name.
  This is the module's primary externally-consumed output, used by `file_analyzer.py` and `usage_analysis.py` to trace symbol usage back to defining files.
- `get_import_params` → `tuple[Language, str] | tuple[None, None]`: tree-sitter `Language` object and import query string for a given extension, or `(None, None)` if unsupported. Used by callers to drive tree-sitter parsing/queries for import extraction.
- **Side effects**: `logger.warning(...)` calls in `_put_symbol` when a symbol name is overwritten with a different source file (no exceptions raised, no file writes).

## 4. Key Data Structures

### `IMPORT_RESOLVE_CONFIG` entry (external, `dict`)
| Field / Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Module name delimiter (`"."` or `"/"`), determines relative-import parsing style |
| `try_init` | `bool` | Whether to try `base_path + "/__init__.py"` (Python packages) |
| `index_ext_list` | `list[str]` | Extensions to try as `base_path + "/index" + ext` (JS/TS directory imports) |
| `alt_ext_list` | `list[str]` | Alternative extensions to try appending to `base_path` |
| `try_bare_path` | `bool` | Whether to try `base_path` unmodified (C/C++ `#include`) |
| `try_current_dir` | `bool` | Whether to also try candidates relative to the current file's directory |

### `ImportInfo` (external, produced by `extract_imports`, consumed here)
| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | Raw module name from the import statement |
| `names` | `list[str]` | Individually imported names (`"*"` denotes wildcard) |
| `alias_map` | `dict[str, str]` | alias → original name mapping for `from X import a as b` |
| `module_alias` | `str \| None` | Alias for `import X as Y` form |

### `symbol_to_file_map` (returned by `build_symbol_to_file_map`)
| Field / Key | Type | Purpose |
|---|---|---|
| key: symbol name | `str` | Name usable in code (imported name, class name, module root/leaf, or `*`-expanded definition name) |
| value: file path | `str` | Project-relative file path where that symbol is defined |

### `alias_to_original` (returned by `build_symbol_to_file_map`)
| Field / Key | Type | Purpose |
|---|---|---|
| key: alias name | `str` | Local alias used in code (`from X import a as b` → `"b"`) |
| value: original name | `str` | Original symbol name (`"a"`) |

### `DefinitionInfo` (external, consumed via `extract_definitions`)
| Field / Key | Type | Purpose |
|---|---|---|
| `name` | `str` | Definition name (function/class/variable/type) |
| `type` | `str` | AST node type of the definition |
| `start_line` | `int` | 1-based start line, used to determine nesting/top-level status |
| `end_line` | `int` | End line, used to compute `covered_end` for filtering nested definitions |

### `candidate_path_list` (intermediate, local to resolution)
| Field / Key | Type | Purpose |
|---|---|---|
| list elements | `str` | Ordered, de-duplicated candidate project-relative file paths generated from `base_path` per language rules |

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation / skip-and-continue** strategy rather than fail-fast. Since import resolution deals with inherently uncertain input (module names may refer to standard libraries, external packages, or non-existent paths), the guiding principle is: *when resolution is uncertain or impossible, return `None` / empty structures and let the caller proceed with reduced information*, rather than raising exceptions. The one exception is symbol overwrite conflicts, which are handled via **logging-and-continue**: the condition is not treated as fatal, but is surfaced to the operator via a warning so that silent data loss (a symbol being remapped to a different file) is visible without interrupting processing. There are no explicit `try/except` blocks in this file; all "error handling" is implemented as conditional guards that short-circuit into a safe fallback value.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Unsupported/unknown file extension | `IMPORT_RESOLVE_CONFIG.get(src_ext)` or `IMPORT_RESOLVE_CONFIG.get(file_ext, {})` returns nothing for the current file's extension | `resolve_module_to_project_path` returns `None` immediately; `build_symbol_to_file_map` falls back to default separator `"."` | Yes | Import resolution for that file/extension is skipped; no symbols registered for it |
| Unresolvable module (stdlib/external package/non-existent path) | None of the generated candidate paths match `project_file_set`, even after source-root prefixing | `resolve_module_to_project_path` returns `None`; caller (`build_symbol_to_file_map`) uses `continue` to skip that import | Yes | That import contributes no entries to `symbol_to_file_map`; overall analysis continues for other imports |
| Unsupported language for import query/AST | `IMPORT_QUERIES.get(file_ext)` is empty/None, or `TREE_SITTER_LANGUAGES[file_ext]` raises `KeyError` | `get_import_params` returns `(None, None)` | Yes | Caller is expected to skip import analysis entirely for that file (per docstring contract) |
| Referenced definition file missing on disk | `os.path.isfile(abs_path)` is `False` for a resolved file path (e.g. stale/deleted file) | `_register_definitions_from_file` returns early without parsing | Yes | No symbols registered from that file; does not affect processing of other files |
| No definition dict for target extension | `DEFINITION_DICTS.get(resolved_ext)` returns nothing | `_register_definitions_from_file` returns early | Yes | Definitions from that file are simply not extracted |
| Symbol name collision (overwrite) | `_put_symbol` is called with a name already present in `symbol_map` but mapped to a different file path | Logs a warning (`logger.warning`) identifying old/new file, then overwrites the mapping | Yes (processing continues) | Potential loss of a prior symbol-to-file association; visible only via log, not exception |
| Java/Kotlin wildcard import resolution failure | `resolve_module_to_project_path` fails for an import with `"*"` in `names` | Falls back to `_register_definitions_from_package`, scanning the package directory instead of a single file | Yes | If the package directory also yields no matches, silently registers nothing (no error raised) |
| Malformed/empty relative import components | `current_dir_part_list` is empty or `clean_module` is empty during relative import resolution | Loop/extend operations are guarded by conditionals (`if path_part_list`, `if clean_module`) so no exception is raised on empty input | Yes | Resulting path list may simply be shorter/empty; downstream candidate generation proceeds with whatever is available |

## 3. Design Notes

- **No exception propagation by design**: All failure conditions in this file are anticipated (unknown extensions, non-project modules, missing files) rather than exceptional, so they are modeled as normal control flow (`None` returns, early `continue`/`return`) instead of exceptions. This keeps the batch-oriented callers (pipeline, dependency graph, usage analysis) running across many files without per-file crash handling.
- **`None` as a first-class "not applicable" signal**: Functions like `resolve_module_to_project_path` and `get_import_params` use `None` (or `(None, None)`) consistently as the "this doesn't apply / can't be resolved" signal, which callers are documented to check before proceeding — pushing the responsibility of handling non-resolution to the call sites.
- **Warning-only for data-integrity conflicts**: The only place where a "problem" is explicitly logged rather than just silently handled is `_put_symbol`'s overwrite detection. This reflects that overwriting a symbol mapping is a *correctness* concern (could cause usage-tracking to point at the wrong file) worth surfacing, but not severe enough to halt analysis.
- **Defensive existence checks over exception handling**: Rather than wrapping filesystem/parsing calls in `try/except`, `_register_definitions_from_file` proactively checks `os.path.isfile` and dict membership before attempting to parse, avoiding exceptions in the first place for the common "file doesn't exist" or "unsupported extension" cases.
- **Layered fallback for resolution**: Multiple fallback layers exist before giving up entirely (plain candidate match → source-root-prefixed match → wildcard package-directory scan), reflecting that real-world project layouts (especially Java/Kotlin with source roots) often require several resolution strategies before concluding a module is genuinely external/unresolvable.

# Summary

Resolves import/include module names to project file paths and builds symbol→file lookup maps for usage tracing. Key functions: `detect_source_roots(project_file_set: set[str])->set[str]`, `resolve_module_to_project_path(module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str]|None)->str|None`, `build_symbol_to_file_map(import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set)->tuple[dict,dict]`, `get_import_params(file_ext: str)->tuple[Language,str]|tuple[None,None]`. Uses `ImportInfo`, `DefinitionInfo`, `symbol_to_file_map`, `alias_to_original`.
