# Design Document: codetwine/import_to_path.py

# Overview & Purpose

## Role and Responsibility

`import_to_path.py` is CodeTwine's language-agnostic **import resolution engine**. Its central responsibility is to bridge the gap between raw import/include statements extracted from source code (e.g. `"..utils"`, `"com.example.Foo"`, `"./helper"`, `"stdio.h"`) and actual file paths within a project's file set. It also builds a higher-level mapping from *symbol names* (imported identifiers) to the *files that define them*, which downstream usage-tracking logic relies on to answer "which file does this name come from?".

This logic is isolated in its own module because import resolution is a distinct, reusable concern shared across multiple analysis stages (`file_analyzer.py`, `extractors/usage_analysis.py`, `extractors/dependency_graph.py`). Rather than duplicating relative-path math, extension-based candidate generation, and source-root fallback logic in each consumer, this file centralizes it behind a small set of pure functions driven by declarative configuration (`IMPORT_RESOLVE_CONFIG`), keeping per-language quirks (Python packages, Java classpath roots, C/C++ headers) out of business logic and out of consumers.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `detect_source_roots` | `project_file_set: set[str]` | `set[str]` | Finds which known source-root prefixes (e.g. `"src/main/java/"`) actually occur in the project's file paths. |
| `resolve_relative_import` | `module: str, separator: str, current_dir_part_list: list[str]` | `list[str]` | Converts a relative or absolute module name into a list of path components, handling Python-style dots and JS/TS-style `./`/`../`. |
| `generate_candidate_path_list` | `base_path: str, src_ext_with_dot: str, resolve_config: dict, current_dir_part_list: list[str]` | `list[str]` | Produces a deduplicated, priority-ordered list of candidate file paths (index files, alt extensions, bare path, current-dir variants) from config rules, with no language-specific branching. |
| `resolve_module_to_project_path` | `module: str, current_file_rel: str, project_file_set: set[str], source_root_set: set[str] \| None = None` | `str \| None` | Full pipeline: resolves an import's module name to a concrete project-internal file path (or `None` if it's external/unresolvable), retrying with source-root prefixes as a fallback. |
| `build_symbol_to_file_map` | `import_info_list, current_file_rel: str, project_file_set: set[str], file_ext: str, project_dir: str, source_root_set: set[str] \| None = None` | `tuple[dict[str, str], dict[str, str]]` | Builds `(symbol_to_file_map, alias_to_original)` by resolving every import and registering symbol names per language-specific conventions (Python module roots, Java class names, C/C++ whole-file inclusion, wildcard imports, same-package visibility). |
| `get_import_params` | `file_ext: str` | `tuple[Language, str] \| tuple[None, None]` | Looks up the tree-sitter `Language` and import query string for a given extension, signaling unsupported languages via `(None, None)`. |

### Internal helper functions (not part of the public contract but structurally important)
- `_put_symbol`: registers a symbol into the map with a warning on conflicting overwrite.
- `_register_definitions_from_file` / `_register_definitions_from_package`: pull all definition names from a resolved file or package directory (used for `import *`, C/C++ `#include`, and Java wildcard imports).
- `_select_top_level_definitions`: filters out nested (member-level) definitions so only outer-level names are registered as importable symbols.

## Design Decisions

- **Declarative, config-driven candidate generation**: `generate_candidate_path_list` contains no `if language == "python"` style branches; all language-specific behavior (index files, `__init__.py`, alternate extensions, bare paths, current-dir relative lookups) is expressed through `IMPORT_RESOLVE_CONFIG` entries, making it straightforward to add new languages without touching this function's logic.
- **Staged pipeline with single-responsibility functions**: `resolve_module_to_project_path` explicitly delegates to three steps (`resolve_relative_import` → `generate_candidate_path_list` → set-membership matching with source-root fallback), each independently testable and documented.
- **Graceful degradation over exceptions**: unresolvable modules (standard library, external packages, unsupported extensions) return `None`/`(None, None)` rather than raising, since most imports encountered are expected to be non-project modules.
- **Non-destructive conflict handling**: `_put_symbol` overwrites conflicting symbol definitions but logs a warning instead of failing, favoring best-effort mapping over strict correctness.
- **Whole-file inclusion semantics for C/C++**: because `#include` incorporates an entire file, `_register_definitions_from_file` registers *all* top-level definitions rather than only explicitly named imports, reflecting C/C++ semantics distinctly from Python/Java's name-based imports.

# Definition Design Specifications

## `detect_source_roots`

Takes `project_file_set` (a `set[str]` of relative file paths within the project) and returns a `set[str]` of source root prefix strings that actually occur in the project.

This function exists to narrow the fixed, language-agnostic list of candidate source root patterns (`SOURCE_ROOT_PATTERNS`, e.g. Java/Kotlin/Scala Maven-style layouts) down to only those prefixes that are actually present in a given project, avoiding wasted fallback attempts and false positives later in module resolution.

Design decision: it performs a simple prefix-match scan rather than inspecting build files (e.g. `pom.xml`), keeping detection purely file-path based and independent of build tooling.

Edge case: if none of the known patterns match any file, an empty set is returned, meaning source-root fallback resolution will simply be skipped by callers.

## `resolve_relative_import`

Takes `module` (the raw module/import string, e.g. `"..utils"`, `"./helper"`, `"os"`), `separator` (`"."` for Python-style languages or `"/"` for path-style languages), and `current_dir_part_list` (path components of the directory containing the current file). Returns a `list[str]` of path components representing the resolved base path (before extension/candidate generation).

Its responsibility is to normalize the two distinct relative-import syntaxes (Python dot-prefix and JS/TS `./`/`../`) plus plain absolute module strings into a single directory-component representation, so downstream code doesn't need per-language branching for relative-path semantics.

Design decisions:
- For Python-style imports, dot count directly encodes directory nesting to walk up (one dot = current directory, additional dots pop path components), matching Python's relative import semantics.
- For JS/TS-style imports, resolution is delegated to `os.path.normpath` to correctly collapse `..` segments in arbitrarily nested relative paths, rather than reimplementing path normalization manually.
- For anything not matching a recognized relative pattern, the module is treated as absolute and merely split by `separator`.

Constraints: the relative-import branches are only triggered when `separator` matches the expected style (`"."` with a leading dot, or `"/"` with a leading `./`/`../`); mismatched combinations (e.g. a dot-relative module with `separator == "/"`) fall through to the absolute-import case.

## `generate_candidate_path_list`

Takes `base_path` (module name already converted to a path string), `src_ext_with_dot` (current file's extension including the dot), `resolve_config` (a per-language `IMPORT_RESOLVE_CONFIG` entry), and `current_dir_part_list` (directory components of the current file). Returns an ordered, duplicate-free `list[str]` of candidate file paths to check against the project.

This function centralizes all language-specific candidate-generation rules (index files, `__init__.py`, alternate extensions, bare paths, current-directory-relative variants) as declarative config-driven behavior, so `resolve_module_to_project_path` remains free of per-language conditionals.

Design decisions:
- Candidates are generated in a fixed priority order (same-extension file, `__init__.py`, index files, alternate extensions, bare path, then current-directory-relative duplicates of all the above), reflecting the order a compiler/runtime would typically probe them.
- If `base_path` already ends in a known extension from `alt_ext_list` (e.g. C header `.h`), same/alternate-extension appending is skipped entirely to avoid nonsensical candidates like `"stdio.h.h"`.
- `try_current_dir` candidates are appended as duplicates of the whole existing list rather than replacing it, since both project-root-relative and current-dir-relative resolution may be valid depending on the language's include semantics.
- Final deduplication preserves first-seen order via `dict.fromkeys`, since candidate priority matters for correctness (first match wins later).

Edge case: if `current_dir_part_list` is empty, `try_current_dir` produces no additional candidates (avoiding a leading `"/"`).

## `resolve_module_to_project_path`

Takes `module` (raw import string, may reference stdlib/external packages), `current_file_rel` (relative path of the importing file), `project_file_set` (set of all project-relative file paths), and optional `source_root_set` (detected source-root prefixes). Returns the resolved project-relative file path as `str`, or `None` if the module isn't project-internal.

This is the central orchestration function that determines whether an arbitrary import/include string refers to a file inside the project, by composing relative-import resolution, candidate generation, and set membership checks; it is the single point other modules (dependency graph, usage analysis) rely on for import-to-file resolution.

Design decisions:
- Returns `None` early for extensions with no `IMPORT_RESOLVE_CONFIG` entry, allowing unsupported languages to be silently skipped by callers.
- Checks plain candidates against `project_file_set` first, and only afterward retries with `source_root_set` prefixes prepended — this ordering favors exact/simple matches and treats source-root prefixing strictly as a fallback (relevant to Java/Kotlin/Scala style layouts where import paths don't include the `src/main/java/`-style root).
- Iterates candidates in priority order and returns on first match, so candidate ordering from `generate_candidate_path_list` determines which file wins when multiple candidates coincidentally exist.

Constraint: correctness depends on `current_file_rel` using forward-slash-normalizable path separators; backslashes are normalized before splitting.

## `_put_symbol`

Takes `symbol_map` (mutable `dict[str, str]`, symbol name → file path), `name` (symbol name), and `path` (file path to associate). Returns `None`; mutates `symbol_map` in place.

Exists as a single choke point for writing into a symbol map so that conflicting redefinitions (the same imported/defined name resolving to two different files) can be logged consistently rather than silently overwritten across the many call sites in this file.

Design decision: overwriting is still allowed (last write wins) rather than raising or keeping the first definition, since a warning is considered sufficient given that symbol collisions are expected to be rare and not necessarily fatal to analysis.

## `build_symbol_to_file_map`

Takes `import_info_list` (parsed import statements, `ImportInfo`-like objects with `.module`, `.names`, `.alias_map`, `.module_alias`), `current_file_rel` (relative path of the file being analyzed), `project_file_set` (all project file paths), `file_ext` (extension without dot), `project_dir` (absolute project root), and optional `source_root_set`. Returns a tuple `(symbol_to_file_map, alias_to_original)`: the first maps imported/referenceable names to the file path where they are defined, the second maps import aliases to their original names.

This function is the core translation layer between "what a file imports" and "which project file defines each name it can reference," feeding downstream usage-tracking logic that needs to answer "does this identifier come from file X."

Design decisions/responsibilities:
- Delegates the module-name-to-file resolution to `resolve_module_to_project_path`; only imports resolvable to project-internal files are registered, so stdlib/external package imports are naturally excluded without special-casing.
- Handles the Java/Kotlin wildcard-import case (`import pkg.*`) specially: since it can't resolve to a single file, it treats the module string as a package directory and registers definitions from every same-extension file directly in that directory via `_register_definitions_from_package`.
- For `from X import name` style imports, each named symbol is registered individually; `*` triggers full-file definition registration via `_register_definitions_from_file` (used e.g. for Python `from X import *`).
- When an import has no explicit names (`import X` style), symbol registration is language-dependent: Python registers both the root and leaf of the dotted module path (supporting both `X.Y.func()` and bare submodule references) unless overridden by an alias; Java/Kotlin registers only the leaf class name (package roots aren't referenced alone in those languages); C/C++ (separator `"/"`) registers all definitions from the included file, mirroring `#include`'s "pull in everything" semantics.
- Even when explicit names are imported, the module root is still registered via `setdefault` (not overwritten) to support attribute-style access on the imported module object, while avoiding clobbering a more specific existing registration.
- For languages marked `SAME_PACKAGE_VISIBLE` (Java/Kotlin), it additionally scans same-directory, same-extension files (excluding the current file itself) and registers their definitions, since such languages allow referencing classes in the same package without an explicit import statement.

Edge cases: Java/Kotlin explicitly skip package-root registration (`module_parts[0]`) since bare package names like `com` or `org` are never referenced directly in code. The wildcard-import branch requires `separator == "."` (dot-style languages) since it targets Java/Kotlin package syntax specifically.

## `_register_definitions_from_file`

Takes `file_rel` (project-relative path of a file to pull definitions from), `project_dir` (absolute project root), and `symbol_to_file_map` (mutable dict to populate). Returns `None`; mutates `symbol_to_file_map`.

Exists to encapsulate the "parse a file and register every top-level definition name it contains" operation, used both for `#include`-style whole-file imports (C/C++) and for `from X import *` wildcards, where an import brings in an unbounded/unspecified set of names.

Design decisions:
- Silently returns if the file doesn't exist on disk or if no `DEFINITION_DICTS` entry exists for its extension, since callers pass paths that are only resolved logically and may not always correspond to real files or supported languages.
- Only registers top-level (non-nested) definitions via `_select_top_level_definitions`, since class members aren't independently importable/referenceable by their bare name from outside the class.

Constraint: relies on `parse_file` caching, so repeated calls for the same file are cheap.

## `_select_top_level_definitions`

Takes `definition_list` (a `list[DefinitionInfo]` sorted by `start_line`) and returns a filtered `list[DefinitionInfo]` containing only definitions not nested inside a previously selected definition's line range.

Exists to prevent registering class members (methods, fields, constructors) as standalone importable symbols, since such members are only meaningfully referenced through their containing class and mapping their bare name directly to a file would be misleading (a method name unrelated to any actual top-level symbol could be mapped to the wrong class's file).

Design decision: uses a simple running "covered end line" watermark rather than a full nested-tree structure, since the input is a flat line-sorted list and nesting can be inferred purely from line-range containment — this assumes definitions do not partially overlap (a definition either is fully nested within a prior one's range or starts strictly after it), which holds for well-formed AST-derived definitions.

Precondition: `definition_list` must be sorted ascending by `start_line` for the watermark logic to correctly detect nesting.

## `_register_definitions_from_package`

Takes `package_dir` (directory path derived from a dotted package/module name, e.g. `"com/example/model"`), `file_ext` (extension without dot), `project_dir` (absolute project root), `project_file_set` (all project file paths), `symbol_to_file_map` (mutable dict to populate), and optional `source_root_set`. Returns `None`; mutates `symbol_to_file_map`.

Exists specifically to support Java/Kotlin wildcard imports (`import pkg.*`), where the import statement names a package rather than a single class, requiring all classes defined directly within that package directory to become resolvable symbols.

Design decisions:
- Builds a list of candidate directory prefixes—the bare `package_dir` plus each `source_root_set` entry prepended—to handle both projects where the package path directly mirrors the filesystem and Maven/Gradle-style layouts where a `src/main/java/`-type root precedes it.
- Restricts matches to files directly under the prefix (rejecting any remainder containing `/`) to exclude sub-package files, since a wildcard import on `pkg.*` in Java does not recursively import sub-packages.
- Delegates actual definition extraction per matching file to `_register_definitions_from_file`, keeping this function focused solely on directory/prefix matching.

## `get_import_params`

Takes `file_ext` (file extension without the leading dot). Returns a tuple `(Language, str)` — the tree-sitter `Language` object and the import-extraction query string for that language — or `(None, None)` if the extension is unsupported.

Exists as a single lookup entry point so callers (file analyzer, usage analysis, dependency graph) can uniformly check language support and obtain both pieces of information needed to run import extraction, without duplicating the two-dict lookup and unsupported-language handling logic at each call site.

Design decision: checks `IMPORT_QUERIES` first and short-circuits to `(None, None)` if no query is defined; only then looks up `TREE_SITTER_LANGUAGES`, also catching a `KeyError` there to guard against configuration inconsistencies (a query defined without a corresponding language registered).

# Dependency Description

## Dependencies (what this file uses)

`import_to_path.py` relies on the following project-internal modules:

- **`codetwine/config/settings.py`** — Supplies all language-specific configuration needed to resolve imports without hardcoding per-language logic:
  - `SOURCE_ROOT_PATTERNS`: known source root prefixes (e.g. `"src/main/java/"`) used by `detect_source_roots` to identify which root layouts are actually present in a project.
  - `IMPORT_RESOLVE_CONFIG`: per-extension settings (separator, index/alt extensions, `try_init`, `try_bare_path`, `try_current_dir`) driving `resolve_module_to_project_path` and `generate_candidate_path_list` to build candidate file paths from module names.
  - `SAME_PACKAGE_VISIBLE`: flags which languages (Java/Kotlin) allow same-package symbol visibility without explicit imports, used in `build_symbol_to_file_map`.
  - `DEFINITION_DICTS`: per-language node-type-to-name-extraction mapping, needed by `_register_definitions_from_file` to extract definitions from a resolved file.
  - `IMPORT_QUERIES` and `TREE_SITTER_LANGUAGES`: used by `get_import_params` to supply the tree-sitter query and `Language` object required for import statement extraction.

- **`codetwine/parsers/ts_parser.py`** — `parse_file` is used in `_register_definitions_from_file` to parse a resolved dependency file into an AST before extracting its definitions.

- **`codetwine/extractors/definitions.py`** — `extract_definitions` and `DefinitionInfo` are used to pull definition names (functions, classes, etc.) out of a file's AST, enabling registration of symbols coming from wildcard imports, `#include`s, or same-package files into the symbol map.

## Dependents (what uses this file)

- **`codetwine/file_analyzer.py`** — Uses `get_import_params` to obtain the tree-sitter language/query needed to extract imports, `detect_source_roots` to determine applicable source root prefixes for the project, and `build_symbol_to_file_map` to build the imported-name-to-file mapping used for downstream usage tracking.

- **`codetwine/extractors/usage_analysis.py`** — Uses `get_import_params` to retrieve import extraction parameters for caller files, and `resolve_module_to_project_path` to check whether a caller's import resolves to a specific target file.

- **`codetwine/extractors/dependency_graph.py`** — Uses `detect_source_roots` to compute project source roots, `get_import_params` to obtain per-file import extraction parameters, and `resolve_module_to_project_path` to resolve each file's imports into callee file paths for building the dependency graph.

The dependency direction is unidirectional: `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` depend on `import_to_path.py` for import resolution and symbol mapping, while `import_to_path.py` has no dependency back on these files.

# Data Flow

## Input Data

| Source | Format | Description |
|---|---|---|
| `project_file_set` | `set[str]` | All project-relative file paths (e.g. `"src/utils/foo.py"`), provided by callers (`file_analyzer.py`, `dependency_graph.py`) |
| `ImportInfo` list | list of objects with `module`, `names`, `alias_map`, `module_alias` | Produced upstream by `extract_imports` (not in this file), passed into `build_symbol_to_file_map` |
| `current_file_rel` / `caller_rel` | `str` | Relative path of the file currently being analyzed |
| `file_ext` | `str` | Extension without dot (e.g. `"py"`, `"java"`) used to key into config dicts (`IMPORT_RESOLVE_CONFIG`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `TREE_SITTER_LANGUAGES`, `SAME_PACKAGE_VISIBLE`) |
| `project_dir` | `str` | Absolute path to project root, used to build absolute paths for parsing |

## Main Transformation Flow

```
project_file_set ──► detect_source_roots() ──► source_root_set (prefixes actually used)

module name (string) ──► resolve_relative_import()
        │  (splits "." / "/" or resolves "..", "./", "../")
        ▼
  path_part_list (list of path components)
        │  "/".join()
        ▼
     base_path (string)
        │
        ▼
generate_candidate_path_list()  (adds extensions, __init__.py, index files, bare path,
                                  current-dir-relative variants per IMPORT_RESOLVE_CONFIG)
        ▼
  candidate_path_list (ordered, deduped list of candidate relative paths)
        │  match against project_file_set (optionally retried with source_root_set prefixes)
        ▼
  resolved_path (str | None)   ──► resolve_module_to_project_path() output
```

This `resolved_path` feeds `build_symbol_to_file_map`, which iterates all `ImportInfo` entries per file and, depending on `import_info.names` / `module_alias` / language separator, decides how to populate the symbol map:

```
For each ImportInfo:
  resolve_module_to_project_path() ──► resolved_path
        │
   ┌────┴─────────────────────────────────────────────┐
   │ names contains "*" and unresolved (Java wildcard) │──► _register_definitions_from_package()
   │ names non-empty                                   │──► register each name -> resolved_path
   │                                                    │    ("*" name -> _register_definitions_from_file())
   │ names empty, separator="."                        │──► register module root / leaf / alias
   │ names empty, separator="/"                         │──► _register_definitions_from_file() (whole file)
   └────────────────────────────────────────────────────┘
        │
        ▼
symbol_to_file_map (dict: symbol name -> file path)
alias_to_original  (dict: alias -> original name)
```

`_register_definitions_from_file` / `_register_definitions_from_package` call `parse_file()` (tree-sitter AST) → `extract_definitions()` (list of `DefinitionInfo`) → `_select_top_level_definitions()` (filters nested/member definitions) → each definition's `name` is inserted into `symbol_to_file_map` via `_put_symbol()` (which also logs overwrite warnings on conflicting sources).

Finally, if the language marks `SAME_PACKAGE_VISIBLE`, sibling files in the same directory are also scanned via `_register_definitions_from_file` and merged into the same map (allows Java/Kotlin same-package references without explicit imports).

`get_import_params` is a separate, independent lookup: `file_ext` → `IMPORT_QUERIES` + `TREE_SITTER_LANGUAGES` → `(Language, query_str)` tuple, used by callers to drive tree-sitter import extraction before this file's resolution logic runs.

## Output Data

| Function | Output | Destination |
|---|---|---|
| `detect_source_roots` | `set[str]` of source-root prefixes | Passed into `resolve_module_to_project_path` / `build_symbol_to_file_map` |
| `resolve_relative_import` | `list[str]` path components | Consumed internally by `resolve_module_to_project_path` |
| `generate_candidate_path_list` | `list[str]` ordered candidate paths | Consumed internally by `resolve_module_to_project_path` |
| `resolve_module_to_project_path` | `str \| None` resolved project file path | Used by `build_symbol_to_file_map`, and directly by `usage_analysis.py`/`dependency_graph.py` |
| `build_symbol_to_file_map` | `(symbol_to_file_map, alias_to_original)` tuple of dicts | Consumed by `file_analyzer.py` for usage tracking |
| `get_import_params` | `(Language, str) \| (None, None)` | Consumed by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py` to drive import parsing |

## Key Data Structures

| Structure | Fields / Shape | Purpose |
|---|---|---|
| `source_root_set` | `set[str]` of prefix strings (e.g. `"src/main/java/"`) | Fallback prefixes tried when a bare candidate path doesn't match project files |
| `current_dir_part_list` | `list[str]` | Directory components of the current file, used as the base for relative import resolution |
| `path_part_list` / `candidate_path_list` | `list[str]` | Intermediate path components / final ordered candidate file paths before matching against `project_file_set` |
| `resolve_config` (from `IMPORT_RESOLVE_CONFIG`) | dict with keys: `separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir` | Declarative per-language rules driving candidate generation, avoiding language-specific branching |
| `symbol_to_file_map` | `dict[str, str]` (symbol name → file path) | Central output mapping used later to identify which file a used name originates from |
| `alias_to_original` | `dict[str, str]` (alias → original name) | Tracks `import X as Y` style aliasing separately from the symbol map |
| `DefinitionInfo` (external) | `name`, `type`, `start_line`, `end_line` | Represents a single extracted definition, filtered by `_select_top_level_definitions` before being merged into `symbol_to_file_map` |

# Error Handling

This module follows a **graceful degradation** strategy throughout: unresolved modules, missing configuration, unsupported languages, and missing files are treated as expected, non-fatal conditions and consistently resolved to `None`, empty collections, or silent skips rather than raising exceptions. The only explicit signal emitted is a warning log for symbol-map overwrites, which is informational rather than error handling. No exceptions are caught with `try/except` inside this file except a narrow `KeyError` guard in `get_import_params`; all other "failure" paths are handled through conditional checks (`if not ...`) that short-circuit to a safe default.

| Error Pattern | Handling | Impact |
|---|---|---|
| Module/import cannot be resolved to a project file (`resolve_module_to_project_path` finds no match) | Returns `None`; caller (`build_symbol_to_file_map`) skips registering the symbol (falls through to wildcard-package handling or `continue`) | Symbol is simply not tracked; treated as external/stdlib dependency, not an error |
| Unsupported file extension in `IMPORT_RESOLVE_CONFIG` (`resolve_module_to_project_path`, `build_symbol_to_file_map`) | Returns `None` early / falls back to empty dict `{}` via `.get(file_ext, {})` | Import resolution/symbol mapping silently disabled for that language |
| Unsupported extension in `IMPORT_QUERIES` or missing entry in `TREE_SITTER_LANGUAGES` (`get_import_params`) | Returns `(None, None)`; `KeyError` from missing `TREE_SITTER_LANGUAGES` entry is explicitly caught | Caller (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) skips import analysis for that file entirely |
| Referenced file does not exist on disk (`_register_definitions_from_file`) | Checks `os.path.isfile` and returns immediately if false | No definitions registered from that file; no crash on stale/incorrect resolved paths |
| No `DEFINITION_DICTS` entry for a resolved file's extension (`_register_definitions_from_file`) | Returns early if `definition_dict` is falsy | Definitions from that file are skipped; downstream symbol map unaffected |
| No source root patterns match / `source_root_set` empty or `None` | Fallback prefixing loop in `resolve_module_to_project_path` / `_register_definitions_from_package` is simply skipped (`if source_root_set:`) | Reduces resolution accuracy for prefixed layouts (e.g. Maven-style Java) but does not error |
| Wildcard import (`*`) with no directly resolvable module (Java/Kotlin) | Falls back to treating the module as a package directory via `_register_definitions_from_package`, which itself only registers matches found in `project_file_set` | Best-effort symbol registration; produces nothing if no matching files exist, without raising |
| Symbol name already mapped to a different file (`_put_symbol`) | Logs a `logger.warning` and overwrites the existing mapping | Non-fatal; last writer wins, potential loss of earlier mapping is only surfaced via log |

### Design Considerations
- The module deliberately avoids raising exceptions for "not found" conditions because unresolved modules (standard library, external packages, or files outside the project) are an expected, common case rather than a bug.
- Failure is propagated as data (`None` / empty list / empty dict) rather than control flow, keeping the resolution pipeline (`resolve_relative_import` → `generate_candidate_path_list` → project-set matching) composable and side-effect-free until the final registration step.
- The single logged warning (symbol overwrite) exists purely for diagnostic visibility into ambiguous/conflicting imports; it does not alter control flow or abort processing.
- File-system and parsing errors from deeper dependencies (`parse_file`, `extract_definitions`) are not caught here—this file only guards against the *precondition* of a missing file (`os.path.isfile`) before invoking them, implicitly relying on callers/config to supply valid, previously-validated project paths.

# Summary

`import_to_path.py` is CodeTwine's language-agnostic import resolution engine, bridging raw import strings to project file paths and symbol→file maps. Key functions: `detect_source_roots`, `resolve_relative_import`, `generate_candidate_path_list`, `resolve_module_to_project_path`, `build_symbol_to_file_map`, `get_import_params`. Driven declaratively by `IMPORT_RESOLVE_CONFIG`/`SOURCE_ROOT_PATTERNS`/`DEFINITION_DICTS`. Used by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`. Favors graceful degradation (None/empty) over exceptions; logs warnings on symbol conflicts.
