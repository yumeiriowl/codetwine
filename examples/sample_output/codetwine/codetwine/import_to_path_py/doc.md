# Design Document: codetwine/import_to_path.py

# Overview & Purpose

## Role and Responsibilities

`codetwine/import_to_path.py` is the module responsible for resolving import/include statements (as extracted by tree-sitter-based parsers) into concrete project file paths and building symbol-to-file lookup tables. It bridges raw syntactic import information (module names such as `"..utils"`, `"com.example.Foo"`, `"stdio.h"`) and the project's actual file layout (`project_file_set`), enabling downstream consumers (`file_analyzer.py`, `pipeline.py`, `extractors/usage_analysis.py`, `extractors/dependency_graph.py`) to determine which project files a given file depends on and which imported names map to which definition files.

It exists as a separate file because import resolution is a distinct, language-agnostic concern that is orthogonal to AST parsing (`ts_parser.py`) and definition extraction (`extractors/definitions.py`): it consumes their output but focuses purely on path/module-name algebra and cross-language config-driven resolution rules (`IMPORT_RESOLVE_CONFIG`, `SOURCE_ROOT_PATTERNS`, `SAME_PACKAGE_VISIBLE`). Keeping this logic isolated avoids duplicating relative-path resolution and symbol registration logic across every language-specific analysis path.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `detect_source_roots` | `project_file_set: set[str]` | `set[str]` | Determine which known source root prefixes (e.g. `"src/main/java/"`) actually appear in the project's file paths. |
| `resolve_relative_import` | `module: str, separator: str, current_dir_part_list: list[str]` | `list[str]` | Convert a relative or absolute module name into a list of directory path components. |
| `generate_candidate_path_list` | `base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]` | `list[str]` | Generate ordered, deduplicated candidate file paths from a base path per language-specific resolve config (index files, alt extensions, bare path, current-dir variants). |
| `resolve_module_to_project_path` | `module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str] \| None = None` | `str \| None` | Resolve an import's module name to an actual project-internal file path, or `None` if it's external/unresolvable; falls back to source-root-prefixed retries. |
| `build_symbol_to_file_map` | `import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set: set[str] \| None = None` | `tuple[dict[str, str], dict[str, str]]` | Build a mapping of imported/usable symbol names to their defining file path (`symbol_to_file_map`) and an alias-to-original-name map (`alias_to_original`), applying language-specific rules (Python module roots, Java class names, C/C++ full-file inclusion, wildcard imports, same-package visibility). |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Retrieve the tree-sitter `Language` object and import query string for a given file extension, or `(None, None)` if unsupported. |

### Internal (non-public) helpers
`_put_symbol`, `_register_definitions_from_file`, `_select_top_level_definitions`, and `_register_definitions_from_package` support `build_symbol_to_file_map` by registering symbols with overwrite-warning semantics, extracting definitions from included/wildcard-imported files, and filtering out nested (non-top-level) definitions such as class members.

## Design Decisions

- **Declarative, config-driven resolution**: Language-specific behavior (relative import syntax, index files, alternate extensions, bare-path support, same-package visibility) is expressed entirely through `IMPORT_RESOLVE_CONFIG` and `SAME_PACKAGE_VISIBLE` dictionaries from `settings.py`, so `generate_candidate_path_list` and related functions avoid per-language `if` branches and instead branch on config flags (`try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`).
- **Staged resolution pipeline**: `resolve_module_to_project_path` explicitly decomposes resolution into three steps (relative/absolute parsing → candidate generation → matching against the project file set, with a source-root-prefixed fallback), each delegated to a dedicated function for testability and clarity.
- **Best-effort symbol registration with conflict warning**: `_put_symbol` centralizes writes into `symbol_to_file_map`, logging a warning (not raising) when a symbol name is redefined by a different file, favoring the module's overall best-effort nature over strict correctness.
- **Order-preserving deduplication**: Candidate paths use `dict.fromkeys` to remove duplicates while preserving priority order, ensuring the most specific/likely candidate is checked first.
- **Graceful degradation for unsupported languages/files**: Both `resolve_module_to_project_path` (missing resolve config) and `get_import_params` (missing query or `Language`) return `None`/`(None, None)` rather than raising, allowing callers to skip unsupported files transparently.

# Definition Design Specifications

## `detect_source_roots`

Takes `project_file_set: set[str]` (relative file paths in the project) and returns `set[str]` containing only the source root prefixes (from `SOURCE_ROOT_PATTERNS`) that actually prefix at least one file in the project.

Exists to avoid hardcoding assumptions about where a project's source root lives (e.g. Maven/Gradle-style `src/main/java/`), since not every project uses every convention. By checking actual files rather than assuming a layout, downstream import resolution can safely try source-root-relative fallbacks without producing false positives for projects that don't use that layout.

Uses a simple prefix match per known pattern; stops scanning a pattern's project files as soon as one match is found (only existence matters, not count). Returns an empty set if none of the known patterns match, which callers must handle as "no source-root fallback available."

## `resolve_relative_import`

Converts a module reference string (`module: str`) plus its import `separator: str` ("." or "/") and the importing file's directory components (`current_dir_part_list: list[str]`) into a `list[str]` of path components representing the target's directory/base path (before extension resolution).

Exists to normalize the very different relative-import syntaxes of Python (dot-counting, e.g. `..utils`) and JS/TS (`./`, `../`) into a single directory-component representation that the rest of the pipeline can treat uniformly, while leaving absolute imports (Java-style dotted packages, plain module names) to a trivial split.

Design decisions:
- Python-style resolution counts leading dots to determine how many directory levels to go up (dot_count - 1 pops), matching Python's relative import semantics where a single dot means "current package."
- JS/TS-style resolution defers to `os.path.normpath` after concatenating with the current directory, so `../` traversal and `.` segments are resolved correctly by a battle-tested implementation, then results are re-normalized to forward slashes for cross-platform consistency.
- Absolute imports are not resolved against `current_dir_part_list` at all — they are assumed to be rooted at the project (or source root) level and are just split by separator.

Constraints: the function only inspects `module`'s prefix for a given `separator`; it does not attempt to guess the separator, so callers must pass one consistent with the module string's language convention. `current_dir_part_list` may be empty (file at project root) and both branches handle that case explicitly.

## `generate_candidate_path_list`

Given a resolved `base_path: str`, the current file's extension `src_ext_with_dot: str`, a per-language `resolve_config: dict` (from `IMPORT_RESOLVE_CONFIG`), and `current_dir_part_list: list[str]`, produces an ordered, de-duplicated `list[str]` of candidate project-relative file paths that `base_path` might correspond to.

Exists to encapsulate all language-specific "what file could this import actually point to" heuristics (extension inference, index files, package `__init__.py`, bare paths for C-style includes, current-directory-relative retries) behind a single declarative, config-driven function, so `resolve_module_to_project_path` itself stays language-agnostic.

Key design decisions:
- Candidate generation order encodes priority: same-extension file first, then `__init__.py` (Python packages), then index files, then alternative extensions, then bare path, expanded finally with current-directory-relative variants — first match found by the caller wins, so ordering matters.
- If `base_path` already ends with an extension found in `alt_ext_list` (e.g. `#include "stdio.h"` or `import "./helpers.js"`), it is treated as a literal file path candidate and no extension is appended, preventing malformed candidates like `stdio.h.h`.
- All behavior is driven purely by boolean/list fields read from `resolve_config`, keeping this function free of any `if language == ...` branching; new languages are supported purely through config changes.
- Deduplication preserves first-seen order (via `dict.fromkeys`) so priority ordering established above survives potential duplicate entries (e.g. when an alt extension coincides with the source extension, it's explicitly skipped rather than causing a duplicate).

## `resolve_module_to_project_path`

Attempts to resolve an import/include's `module` name (as written in source, which may reference standard library, third-party, or in-project code) to an actual project file path, given `current_file_rel: str`, `project_file_set: set[str]`, and optional `source_root_set: set[str] | None`. Returns the matching relative path `str`, or `None` if the module cannot be matched to any project file (the expected outcome for external/library imports).

Exists as the central per-import resolution entry point, orchestrating relative-path resolution, candidate generation, and matching against actual project files — used both when building symbol maps and when building the dependency graph, so it must be pure and side-effect free relative to its inputs.

Design decisions:
- Returns `None` early if the current file's extension has no entry in `IMPORT_RESOLVE_CONFIG`, since without a resolve config no candidate paths can be meaningfully generated (unsupported language for import resolution).
- Matching proceeds in two passes: first tries all generated candidates as-is, and only if none match, retries every candidate with each detected source-root prefix prepended (e.g. converting a Java package-derived path into one rooted under `src/main/java/`). This ordering avoids spurious source-root matches when a direct project-relative match already exists.
- The function deliberately treats "not found in project" as a valid, expected outcome (returns `None`) rather than an error, since most imports in real projects reference external code.

Constraints: `current_file_rel` must use a path format compatible with `os.path.splitext`/`os.path.normpath` expectations; backslashes are normalized to forward slashes before splitting into directory components.

## `_put_symbol`

Helper that inserts a `name: str` -> `path: str` mapping into `symbol_map: dict[str, str]` (mutated in place), logging a warning if `name` is already mapped to a *different* path before overwriting it.

Exists to centralize the "last writer wins, but warn on conflicting symbol origins" policy so that every call site registering a symbol (imports, wildcard imports, same-package visibility, file/package definition scans) behaves consistently and conflicts are observable via logs rather than silently causing incorrect "definition source" attribution later in usage tracking.

Edge case: re-registering the same name to the same path is a no-op with no warning (only genuine conflicts are logged).

## `build_symbol_to_file_map`

Builds the two lookup structures needed for usage tracking from a file's import statements: `symbol_to_file_map: dict[str, str]` (imported/visible name -> defining file path) and `alias_to_original: dict[str, str]` (alias -> original name), given `import_info_list`, `current_file_rel: str`, `project_file_set: set[str]`, `file_ext: str`, `project_dir: str`, and optional `source_root_set`.

Exists as the language-aware translation layer between raw parsed import statements and a flat symbol table that later usage-detection code can query without needing to know per-language import semantics itself.

Key design decisions and behavior:
- Delegates all path resolution to `resolve_module_to_project_path`; imports that don't resolve to a project file are simply skipped (external/stdlib), except for the Java/Kotlin wildcard-import special case.
- For unresolved wildcard imports (`*` in `import_info.names` and dot-separator languages), falls back to treating the module as a package directory and scans all matching-extension files directly under that directory (via `_register_definitions_from_package`), since a wildcard import has no single resolvable file.
- When `import_info.names` is present (`from X import a, b` style), each name is registered individually, with `*` triggering a full-file definition scan (`from X import *`); aliases collected across all imports are merged into `alias_to_original`.
- When `names` is empty, symbol derivation is genuinely language-specific: dot-separator languages (Python/Java/Kotlin) register the module's root segment for attribute-style access (e.g. `os.path.join`) unless the language is Java/Kotlin (which don't reference bare package roots) and/or the leaf segment (for direct class references, e.g. Java's `import com.foo.Bar`); an explicit `module_alias` (Python's `import X as Y`) takes precedence and registers only the alias. Slash-separator languages (C/C++) instead register *all* definitions in the resolved file, since `#include` incorporates the entire file's contents rather than named symbols.
- Even when `names` is non-empty, the module root is additionally registered via `setdefault` (not overwriting an existing mapping) so that both attribute-style access and named imports work simultaneously; Java/Kotlin are excluded from this since bare package roots aren't referenced directly in those languages.
- After processing all imports, if `SAME_PACKAGE_VISIBLE` is enabled for `file_ext` (Java/Kotlin), the function also scans sibling files in the same directory (matching extension, excluding the current file) and registers their top-level definitions, modeling the language rule that same-package classes are visible without an explicit import.

Constraints: `import_info_list` entries are expected to expose `.module`, `.names`, `.alias_map`, and `.module_alias` attributes (produced by the import extractor, not defined in this file). The function assumes `IMPORT_RESOLVE_CONFIG` may not have an entry for `file_ext` (defaults to `{}`/separator `"."` in that case).

## `_register_definitions_from_file`

Parses the file at `file_rel: str` (relative to `project_dir: str`) and registers every top-level definition name it contains into `symbol_to_file_map: dict[str, str]` (mutated in place). No return value.

Exists to support "whole-file inclusion" semantics (C/C++ `#include`, Python `from X import *`) where an entire file's public surface becomes usable without individually-named imports, by reusing the shared AST-based definition extractor rather than re-implementing name discovery.

Design decisions:
- Silently returns if the target file doesn't exist on disk or if there's no `DEFINITION_DICTS` entry for its extension, treating both as "nothing to register" rather than raising, since callers may pass paths derived from best-effort resolution.
- Filters extracted definitions through `_select_top_level_definitions` before registering, deliberately excluding nested members (methods, fields) so that member names don't get incorrectly mapped to their containing class's file as if they were independently importable symbols.

## `_select_top_level_definitions`

Given `definition_list: list[DefinitionInfo]` (assumed sorted by `start_line`), returns a `list[DefinitionInfo]` containing only the outermost (non-nested) definitions.

Exists because the underlying AST-based extractor (`extract_definitions`) intentionally includes nested definitions (e.g. class methods) for other use cases, but symbol-map registration must exclude them — a method name should not be treated as a directly importable/usable symbol at the file level.

Design decision: uses a single linear scan tracking `covered_end`, the end line of the most recently selected outer definition; any subsequent definition starting at or before that line is considered nested and skipped. This relies on the precondition that the input is sorted by `start_line` and that nested definitions' line ranges are fully contained within their parent's range — no explicit parent/child structure is used, only line-range containment.

## `_register_definitions_from_package`

For Java/Kotlin wildcard imports, scans `project_file_set: set[str]` for files directly inside `package_dir: str` (optionally also under each prefix in `source_root_set`) and registers their top-level definitions into `symbol_to_file_map: dict[str, str]` via `_register_definitions_from_file`. No return value.

Exists to handle `import com.example.model.*`-style imports, which reference an entire package's classes rather than a single resolvable file, requiring directory-level scanning instead of single-file resolution.

Design decisions:
- Builds a list of candidate directory prefixes: the bare `package_dir` plus one variant per `source_root_set` entry, mirroring the same source-root fallback strategy used in `resolve_module_to_project_path`, since the actual project layout may nest packages under `src/main/java/` etc.
- Restricts matches to files directly under the prefix (no additional `/` in the remainder) to avoid pulling in definitions from sub-packages, matching Java's wildcard-import semantics which do not descend into sub-packages.
- Only files whose extension matches `file_ext` are processed, consistent with only wanting same-language definitions.

## `get_import_params`

Given a file extension `file_ext: str` (without leading dot), returns a `tuple[Language, str]` pair of the tree-sitter `Language` object and the import-detection query string for that language, or `(None, None)` if the extension is unsupported for import analysis.

Exists as a single lookup point that lets callers (file analyzer, dependency graph builder, usage analyzer) uniformly decide whether and how to run import extraction for a given file type, without needing to know about `IMPORT_QUERIES`/`TREE_SITTER_LANGUAGES` internals directly.

Design decision: treats "no import query defined" and "no tree-sitter language registered" as equally valid reasons to skip import analysis, returning the same `(None, None)` sentinel in both cases so callers can use a single truthiness check rather than distinguishing failure modes.

# Dependency Description

### Dependencies (what this file uses)

`import_to_path.py` relies on several project-internal modules to resolve import statements to concrete file paths and to register the symbols they expose:

- **codetwine/config/settings.py** — Supplies all language-specific configuration this module needs to remain free of hardcoded, language-specific branching:
  - `SOURCE_ROOT_PATTERNS`: known source-root prefixes (e.g. `"src/main/java/"`) used by `detect_source_roots` to figure out which prefixes are actually present in a given project.
  - `IMPORT_RESOLVE_CONFIG`: per-extension settings (separator, whether to try `__init__.py`, index files, alternate extensions, bare paths, current-directory candidates) driving `generate_candidate_path_list` and `resolve_module_to_project_path`.
  - `SAME_PACKAGE_VISIBLE`: flags whether a language (e.g. Java/Kotlin) allows same-package files to be referenced without explicit imports, used in `build_symbol_to_file_map`.
  - `DEFINITION_DICTS`: per-extension AST node-type mappings needed to extract definitions when registering symbols from resolved files.
  - `IMPORT_QUERIES` and `TREE_SITTER_LANGUAGES`: used by `get_import_params` to hand back the tree-sitter `Language` object and query string required for import extraction for a given file extension.

- **codetwine/parsers/ts_parser.py** (`parse_file`) — Used in `_register_definitions_from_file` to parse an already-resolved project file (e.g. a header included via `#include`, or an imported module) into an AST so its definitions can be extracted.

- **codetwine/extractors/definitions.py** (`extract_definitions`, `DefinitionInfo`) — Used to pull definition names (functions, classes, etc.) out of the AST of a resolved file, so those names can be registered in `symbol_to_file_map`. `DefinitionInfo` is the data type consumed when filtering to top-level definitions.

### Dependents (what uses this file)

This file is consumed by several other project-internal modules that need to map import/include statements to actual project files and their symbols:

- **codetwine/file_analyzer.py** — Uses `get_import_params` to obtain the language and import query for a file, and `build_symbol_to_file_map` to build the mapping of imported names to their originating files, which supports downstream usage tracking within that file.

- **codetwine/pipeline.py** — Uses `detect_source_roots` once per project run to precompute the set of applicable source-root prefixes, which it then passes along to per-file processing steps.

- **codetwine/extractors/usage_analysis.py** — Uses `resolve_module_to_project_path` to check whether a caller file's imports resolve to a given target file, and uses `get_import_params` to retrieve the language/query needed to extract a caller's import statements.

- **codetwine/extractors/dependency_graph.py** — Uses `detect_source_roots` to compute source roots for the whole project, `get_import_params` to obtain per-file import parsing parameters, and `resolve_module_to_project_path` to convert each file's imports into resolvable project-internal file paths for constructing the dependency graph.

The dependency direction is unidirectional: `import_to_path.py` depends on `settings.py`, `ts_parser.py`, and `definitions.py`, while `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, and `dependency_graph.py` depend on `import_to_path.py`. There is no reverse dependency from `import_to_path.py` back to any of its dependents.

# Data Flow

### Inputs

| Source | Data | Format |
|---|---|---|
| `pipeline.py` / `dependency_graph.py` | `project_file_set` | `set[str]` of project-relative file paths |
| `file_analyzer.py` / `usage_analysis.py` / `dependency_graph.py` | `import_info_list` (from `extract_imports`, external) | list of `ImportInfo`-like objects with `.module`, `.names`, `.alias_map`, `.module_alias` |
| Caller context | `current_file_rel`, `file_ext`, `project_dir` | strings (relative path, extension, absolute root path) |
| `settings.py` (config) | `SOURCE_ROOT_PATTERNS`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `TREE_SITTER_LANGUAGES` | dicts/lists keyed by file extension |

### Main Transformation Flow

```
project_file_set ──► detect_source_roots() ──► source_root_set
                                                   │
module name (string) ──► resolve_relative_import() ──► path_part_list (list[str])
                                                   │
                              base_path = "/".join(path_part_list)
                                                   │
base_path + resolve_config ──► generate_candidate_path_list() ──► candidate_path_list (ordered, deduped)
                                                   │
candidate_path_list × project_file_set (+ source_root_set fallback)
                                                   │
                                         resolve_module_to_project_path()
                                                   │
                                    resolved_path (str | None)
                                                   │
        ┌──────────────────────────────────────────┴───────────────────────────────┐
        ▼                                                                          ▼
build_symbol_to_file_map()                                          (used standalone by dependency_graph /
  - iterates import_info_list                                        usage_analysis to test module→file resolution)
  - registers names/aliases/module roots into symbol_to_file_map
  - for wildcard/whole-file includes, delegates to
    _register_definitions_from_file() / _register_definitions_from_package()
        │
        ▼
_register_definitions_from_file()
  parse_file() ──► root_node ──► extract_definitions() ──► definition_list ──► _select_top_level_definitions()
        │
        ▼
symbol_to_file_map[name] = file_path  (via _put_symbol, with overwrite warning)
```

Additionally, `get_import_params(file_ext)` is a pure lookup: `IMPORT_QUERIES` + `TREE_SITTER_LANGUAGES` → `(Language, query_str)` or `(None, None)`, used by callers to decide whether/how to run import extraction.

### Outputs

| Function | Output | Destination |
|---|---|---|
| `detect_source_roots` | `set[str]` of matched root prefixes | passed into `resolve_module_to_project_path` / `build_symbol_to_file_map` |
| `resolve_relative_import` | `list[str]` path components | consumed internally by `resolve_module_to_project_path` |
| `generate_candidate_path_list` | `list[str]` deduplicated candidate paths | consumed internally by `resolve_module_to_project_path` |
| `resolve_module_to_project_path` | `str | None` project-relative file path | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, and internally by `build_symbol_to_file_map` |
| `build_symbol_to_file_map` | `(symbol_to_file_map, alias_to_original)` tuple of dicts | `file_analyzer.py` (used for usage tracking) |
| `get_import_params` | `(Language, str) | (None, None)` | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py` |

### Key Data Structures

| Structure | Fields / Shape | Purpose |
|---|---|---|
| `source_root_set` | `set[str]` (e.g. `{"src/main/java/"}`) | Prefixes to retry when a plain candidate path doesn't match any project file |
| `path_part_list` / `current_dir_part_list` | `list[str]` of path segments | Intermediate representation of a module/directory path before joining into `base_path` |
| `candidate_path_list` | ordered `list[str]`, deduplicated | Ranked guesses for the real project file path corresponding to an import |
| `resolve_config` (entry of `IMPORT_RESOLVE_CONFIG`) | dict with `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` | Declarative, per-language rules driving candidate generation without language-specific branching |
| `symbol_to_file_map` | `dict[str, str]`: symbol name → defining file path | Central lookup used later to trace "which file does this used name come from" |
| `alias_to_original` | `dict[str, str]`: alias name → original name | Tracks `import X as Y` / `from X import a as b` renamings |
| `DefinitionInfo` (external) | `name`, `type`, `start_line`, `end_line` | Represents a single extracted definition; used to populate `symbol_to_file_map` when a whole file/package is registered (wildcard imports, `#include`, same-package visibility) |

# Error Handling

**Overall strategy:** This module follows a graceful-degradation policy throughout. Since its core purpose is best-effort resolution of import statements to project files—where many imports legitimately refer to standard library modules or external packages that cannot be resolved—the module never raises exceptions for unresolved or ambiguous input. Instead, functions return `None`, empty collections, or silently skip processing when a lookup fails or a precondition is not met. Only one non-fatal warning is logged (for symbol overwrite conflicts), and no exceptions are caught or suppressed anywhere in the file; any unexpected errors from dependencies (e.g., file I/O or parsing failures in `parse_file`) are allowed to propagate to the caller rather than being handled locally.

**Main error patterns and handling policies:**

| Error Type | Handling | Impact |
|---|---|---|
| Unsupported/unknown file extension (`IMPORT_RESOLVE_CONFIG`, `IMPORT_QUERIES`, `TREE_SITTER_LANGUAGES` lookup miss) | Return `None` / `(None, None)` early instead of raising | Caller (e.g. `file_analyzer.py`) skips import analysis for that file/extension |
| Module name not resolvable to a project file (stdlib, external package, or genuinely missing file) | `resolve_module_to_project_path` returns `None` after exhausting all candidates and source-root fallbacks | Symbol/import is simply not registered in `symbol_to_file_map`; treated as external dependency |
| No matching definition dict for a file extension in `_register_definitions_from_file` | Return immediately without registering any symbols | Definitions from that file are silently omitted from the symbol map |
| Target file for definition extraction does not exist on disk (`os.path.isfile` check) | Return immediately without attempting to parse | Avoids propagating file-not-found errors from `parse_file`; symbols simply not registered |
| Java/Kotlin wildcard import (`*`) not resolvable to a single file | Falls back to treating the module as a package directory and scans `project_file_set` for matching files | Enables partial resolution instead of failing outright when a wildcard doesn't map to one file |
| Symbol name collision (same name mapped to two different files) | Logged via `logger.warning` in `_put_symbol`, then the mapping is overwritten with the newer value | Analysis continues; the older association is lost, which is surfaced only through the log message |
| Errors raised by dependencies (`parse_file`, `extract_definitions`) such as bad file content or parser issues | Not caught; propagate to the caller | Failures in file analysis bubble up rather than being masked, consistent with the module's approach of not hiding structural/parsing errors |

**Design considerations:**
- The pervasive use of `None`/empty-return patterns reflects the expectation that unresolved imports (external libraries, stdlib) are a normal, frequent outcome rather than an error condition—so no exception-based signaling is used for them.
- The single `logger.warning` call is intentionally the only diagnostic surfaced to users; it exists to make silent symbol overwrites visible without interrupting the analysis pipeline.
- The module deliberately avoids wrapping calls to `parse_file`/`extract_definitions` in try/except, keeping error responsibility (e.g., malformed files, unreadable paths) with those lower-level modules and their callers, and preserving fail-fast behavior for structural/parsing problems while keeping resolution-related "misses" non-fatal.

# Summary

`import_to_path.py` resolves parsed import/include module names into concrete project file paths and builds symbol→file lookup tables, using config-driven rules (`IMPORT_RESOLVE_CONFIG`, `SOURCE_ROOT_PATTERNS`, `SAME_PACKAGE_VISIBLE`). Key APIs: `detect_source_roots`, `resolve_relative_import`, `generate_candidate_path_list`, `resolve_module_to_project_path`, `build_symbol_to_file_map`, `get_import_params`. Outputs `symbol_to_file_map`/`alias_to_original` dicts and resolved paths, feeding `file_analyzer.py`, `pipeline.py`, `usage_analysis.py`, `dependency_graph.py`. Failures degrade gracefully (return None/skip), never raise.
