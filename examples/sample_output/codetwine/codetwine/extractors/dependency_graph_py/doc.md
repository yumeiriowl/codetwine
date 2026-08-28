# Design Document: codetwine/extractors/dependency_graph.py

# Overview & Purpose

`dependency_graph.py` is responsible for statically analyzing a project's source tree and producing a file-level dependency graph (caller/callee relationships). It exists as a dedicated module because this analysis spans multiple concerns—file discovery, AST parsing, import resolution, and same-package visibility rules—that are otherwise handled by separate specialized modules (`ts_parser`, `imports`, `import_to_path`, `settings`). By centralizing the orchestration logic here, the rest of the pipeline (`pipeline.py`) and downstream consumers (`usage_analysis.py`) can rely on a single, well-defined graph-building entry point and a companion source-lookup helper, without needing to know the details of AST traversal or import resolution.

The module also provides a low-level AST search utility (`extract_callee_source`) that lets other parts of the codebase (notably usage analysis) fetch the actual source code of a definition once the dependency graph has identified which file it lives in.

### Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str, callee_name: str, project_dir: str` | `str \| None` | Locates and returns the source code of a named definition (function/class/variable) inside a given project file, using BFS over its AST; falls back from attribute-access trailing name to leading name if needed. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walks the project, collects supported source files, extracts and resolves imports (plus same-package implicit references for Java/Kotlin), and returns a list of `{"file", "callers", "callees"}` entries using `project_name/copy_path`-formatted paths. |

Internal (non-exported but structurally important) helpers:

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `_is_inside_import` | `node` | `bool` | Walks up AST ancestors to determine if a node sits inside an import/include statement, so such occurrences are excluded from definition search. |
| `_find_definition_node` | `root_node, definition_name: str` | node or `None` | BFS search over the AST for an identifier/type/namespace node matching a name, skipping import references, returning its parent (the definition-bearing node). |

### Design Decisions & Patterns

- **BFS over AST for definition lookup**: Both `_find_definition_node` and the overall dependency scan favor breadth-first traversal via `collections.deque`, systematically searching shallow-to-deep rather than recursing, and skipping import-only occurrences via `_is_inside_import`.
- **Staged pipeline within `build_project_dependencies`**: The function is explicitly organized into sequential, commented steps (file collection → project file set/source roots → per-file callee extraction via imports → same-package implicit dependency augmentation → reverse caller index construction → path normalization/JSON-ready output), making the multi-phase graph-building process easy to follow and maintain.
- **Delegation to specialized modules**: Parsing (`parse_file`), import extraction (`extract_imports`), and module-to-path resolution (`resolve_module_to_project_path`, `detect_source_roots`, `get_import_params`) are all delegated to dedicated modules, keeping this file focused purely on graph assembly logic rather than parsing/resolution mechanics.
- **Same-package visibility as a distinct, language-gated step**: For languages where `SAME_PACKAGE_VISIBLE` is true (e.g., Java/Kotlin), files in the same directory are grouped and checked via regex word-boundary matching against class names, adding implicit unidirectional dependencies not captured by explicit imports—kept as a separate step from import-based resolution to reflect the differing detection mechanism (source-text scanning vs. AST-based import parsing).
- **Path normalization for portability**: Output paths are converted to a `project_name/copy_path` structure via `rel_to_copy_path`, ensuring the dependency graph remains valid even if the analyzed project/output folder is relocated.
- **Robustness**: File reads for same-package detection are wrapped in try/except (`OSError`, `UnicodeDecodeError`) to skip unreadable files without aborting the whole analysis.

# Definition Design Specifications

## `_is_inside_import`

Takes a tree-sitter `node` and returns a `bool` indicating whether the node lies inside an import/include statement.

Exists to let AST traversal distinguish between a name being *defined* and a name merely being *referenced* in an import (e.g. `import Foo` should not count as a definition of `Foo`).

Design decision: walks up via `node.parent` and checks each ancestor's `type` string for the substring `"import"` or an exact match to `"preproc_include"`, rather than maintaining an explicit list of all possible import node type names across languages — this keeps the check language-agnostic across Python/Java/Kotlin/JS/TS/C/C++ grammars at the cost of relying on naming conventions in tree-sitter grammars.

Edge case: returns `False` for a root node (no parent) or any node whose ancestor chain never includes an import-like type.

## `_find_definition_node`

Takes `root_node` (AST root) and `definition_name` (str, the identifier to locate), and returns the parent AST node of the matching definition, or `None` if not found.

Exists to locate where a given name is actually defined in a file's source (function, class, variable, type, namespace) as opposed to where it's merely referenced, so callers can extract the full definition source text.

Design decisions:
- Uses breadth-first search (via `deque`) rather than depth-first, seeking the shallowest/first matching definition in traversal order.
- Restricts matching to a fixed set of node types (`identifier`, `type_identifier`, `namespace_identifier`) covering common definition-name node kinds across supported languages.
- Skips any matching identifier node found inside an import statement (via `_is_inside_import`) since those are references, not definitions.
- Returns the *parent* node of the matched identifier (not the identifier itself), since the parent is expected to be the actual definition construct (function_definition, class_definition, assignment, etc.) whose source text is meaningful to extract.

Edge cases: if multiple nodes share the same name, the first one encountered in BFS order (excluding import references) is returned; no disambiguation by node kind (e.g. preferring a function definition over a variable) is performed beyond import-exclusion.

## `extract_callee_source`

Takes `callee_file_path` (str, project-relative path to the file containing the dependency's definition), `callee_name` (str, the name of the definition to retrieve, possibly dotted like `helper.process`), and `project_dir` (absolute path to project root). Returns the definition's full source code as `str`, or `None` if not found.

Exists to resolve a call/usage reference in one file to the actual source code of its definition in another file, enabling downstream consumers (e.g. usage analysis) to inline or display dependency implementations.

Design decisions:
- Relies on `parse_file`'s module-level cache to avoid redundant re-parsing when the same callee file is queried multiple times.
- Handles dotted names by first searching for the definition using the trailing segment (e.g. `process`), since that is the common case for member access; if not found, falls back to searching by the leading segment (e.g. `helper`), covering cases where the trailing segment refers to a built-in/stdlib method (e.g. `TEMPLATE.format`) and the real project-local definition is the leading name.
- Returns the raw decoded source text of the found node's parent, giving callers the complete original definition rather than a synthesized excerpt.

Edge cases: if the name has more than two dot-separated parts, only the first and last segments are tried, not intermediate ones. If neither search name yields a match, returns `None` rather than raising.

## `build_project_dependencies`

Takes `project_dir` (str, root directory of the project to analyze) and returns `list[dict]`, where each dict describes one file's `"file"`, `"callers"`, and `"callees"` paths in `"project_name/copy_path"` format.

Exists as the top-level entry point that scans an entire project, resolves import statements (and same-package implicit references for certain languages) into a bidirectional file-level dependency graph, and serializes the result into a stable, portable path format for downstream consumption (pipeline output, usage analysis).

Design decisions:
- Restricts the file scan to extensions present in `DEFINITION_DICTS`, and prunes excluded directories/files in-place during `os.walk` (mutating `dir_names`) so excluded subtrees are never descended into, which is both a performance optimization and correctness measure (avoids resolving imports into ignored code like `node_modules`).
- Builds a `project_file_set` of all project-relative paths first, since import resolution (`resolve_module_to_project_path`) needs the full set of valid project files to determine whether a resolved module path actually exists in the project.
- Detects source root prefixes (`detect_source_roots`) once per project up front, since multiple files may need the same prefix set for import resolution (e.g. Java's `src/main/java/`).
- Uses absolute paths as internal dictionary keys throughout construction (`file_callee_map`, `file_caller_map`) to avoid ambiguity from relative-path collisions, only converting to project-relative and then to copy-path format (`rel_to_copy_path`) at the final serialization step.
- Adds a same-package visibility pass (Step 3.5) specifically for languages where `SAME_PACKAGE_VISIBLE` is true (e.g. Java/Kotlin), because such languages allow referencing sibling classes in the same directory without an explicit import; detection is done heuristically via word-boundary regex matching of the other file's class name (derived from filename) against the source text, and is intentionally unidirectional (added only from the referencing file to the referenced file, not inferred both ways) and only within files sharing the same directory and extension.
- Builds the callers map as a reverse index of the callees map in a separate pass, since callees are naturally computed per-file first and callers require aggregating across all files.
- The dependency detection for same-package visibility is a heuristic text search rather than true reference resolution, so it may produce false positives (e.g. name appearing in a comment or string) or miss references obscured by aliasing.

Edge cases and constraints:
- Files whose extension has no configured `import_query_str`/`language` (via `get_import_params`) are still included in the file list and output, but contribute no callees from import analysis (only possibly from the same-package pass).
- Files that fail to open/read (`OSError`, `UnicodeDecodeError`) during the same-package pass are silently skipped, so their potential same-package callees are not detected, but they still appear in the final output as they were already collected in `all_file_list`.
- A callee is only registered if it resolves to a path within `file_caller_map`'s keys (i.e., an actual project file), preventing external/unresolved modules from appearing as callees.
- Output ordering of `callers`/`callees` lists is not guaranteed to be stable/sorted since they originate from set iteration.

# Dependency Description

### Dependencies (what this file uses)

- **`codetwine/parsers/ts_parser.py` (`parse_file`)**: Used to parse project source files into tree-sitter ASTs (with byte content). Both `extract_callee_source` and `build_project_dependencies` rely on this to obtain the root AST node needed for definition lookup and import extraction, benefiting from its module-level parse caching to avoid redundant parsing.

- **`codetwine/extractors/imports.py` (`extract_imports`)**: Used to extract structured import information (`ImportInfo` list) from a parsed AST, which is then resolved into project file paths to build the callee relationships between files.

- **`codetwine/import_to_path.py` (`detect_source_roots`, `resolve_module_to_project_path`, `get_import_params`)**: Used to translate raw import module names into concrete project-relative file paths. `get_import_params` supplies the language and query needed for import extraction per file extension, `detect_source_roots` identifies source root prefixes (e.g., Java-style `src/main/java/`) to improve path resolution, and `resolve_module_to_project_path` performs the actual mapping from a module reference to an in-project file.

- **`codetwine/utils/file_utils.py` (`rel_to_copy_path`)**: Used to convert project-relative file paths into the copy-destination directory structure format, ensuring the dependency graph's `file`, `callers`, and `callees` fields match the actual output folder layout.

- **`codetwine/config/settings.py` (`DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE`)**: Used as configuration sources — `DEFINITION_DICTS.keys()` determines which file extensions are supported for analysis, `EXCLUDE_PATTERNS` filters out directories/files that should be skipped during traversal, and `SAME_PACKAGE_VISIBLE` determines which languages (e.g., Java/Kotlin) require implicit same-package dependency detection.

### Dependents (what uses this file)

- **`codetwine/pipeline.py`**: Calls `build_project_dependencies` to construct the project-wide dependency graph as an initial step in the analysis pipeline, then converts the resulting paths into internal path formats for downstream processing.

- **`codetwine/extractors/usage_analysis.py`**: Calls `extract_callee_source` to retrieve the source code of a specific definition (function, class, variable, etc.) from a resolved dependency target file, based on a symbol name.

The dependency direction is unidirectional: `pipeline.py` and `usage_analysis.py` depend on `dependency_graph.py`'s public functions, while `dependency_graph.py` has no dependency on either of these files.

# Data Flow

## Input

| Source | Format |
|---|---|
| `project_dir` (function argument) | Absolute path string to the project root directory |
| Filesystem (`os.walk`) | Directory tree containing source files of arbitrary languages |
| `DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE` (config) | Static lookup tables/lists controlling which files are analyzed/excluded |

## Main Transformation Flow

```
project_dir
   │
   ▼
[1] os.walk + EXCLUDE_PATTERNS filter
   → all_file_list (absolute file paths, supported extensions only)
   │
   ▼
[2] relpath() normalization
   → project_file_set (set of "path/to/file.ext" strings)
   → detect_source_roots() → source_root_set
   │
   ▼
[3] per file: parse_file() → AST → extract_imports() → ImportInfo list
      each ImportInfo.module → resolve_module_to_project_path()
   → file_callee_map: { abs_path : set(abs_path of imported project files) }
   │
   ▼
[3.5] group files by (dir, ext) where SAME_PACKAGE_VISIBLE
      regex-scan source text for sibling class/type names
   → file_callee_map extended with same-package implicit callees
   │
   ▼
[4] invert file_callee_map
   → file_caller_map: { abs_path : list(abs_path of files that import it) }
   │
   ▼
[5] convert abs paths → project-relative → rel_to_copy_path() → prefixed with project_name
   → file_info_list (final output)
```

`extract_callee_source` follows a separate, independent flow:
```
(callee_file_path, callee_name, project_dir)
   → parse_file() → AST root
   → callee_name split on "." → candidate search names (trailing part first, then leading part)
   → _find_definition_node() BFS over AST, skipping nodes inside import statements (_is_inside_import)
   → matching parent node's source text
```

## Output

**`build_project_dependencies`** returns `list[dict]`:

| Field | Type | Meaning |
|---|---|---|
| `file` | str | `"{project_name}/{copy_path}"` of the analyzed file |
| `callers` | list[str] | `"{project_name}/{copy_path}"` of files that import/reference this file |
| `callees` | list[str] | `"{project_name}/{copy_path}"` of files this file imports/references |

Destination: consumed by `pipeline.py` (`build_project_dependencies` call → `_convert_dep_list_to_internal_paths` → drives further pipeline steps such as file traversal ordering and dependency-aware processing).

**`extract_callee_source`** returns `str | None` — raw source code text of the located definition node (or `None` if not found). Destination: `usage_analysis.py`, which uses it to attach definition source code to detected symbol usages.

## Key Data Structures

| Structure | Shape | Purpose |
|---|---|---|
| `all_file_list` | `list[str]` (absolute paths) | Master list of files to analyze |
| `project_file_set` | `set[str]` (relative paths) | Fast membership check during import resolution |
| `source_root_set` | `set[str]` | Known source-root prefixes (e.g. `"src/main/java/"`) for fallback resolution |
| `file_callee_map` | `dict[str, set[str]]` (abs path → abs paths) | Forward dependency edges (imports + same-package refs) |
| `file_caller_map` | `dict[str, list[str]]` (abs path → abs paths) | Reverse dependency edges, built by inverting `file_callee_map` |
| `dir_ext_groups` | `dict[(dir, ext), list[str]]` | Groups same-directory/same-extension files for same-package visibility checks |
| `class_names` / `class_patterns` | `dict[str, str]` / `dict[str, re.Pattern]` | Per-file class/type name and compiled regex used for sibling-reference detection |
| `file_info_list` | `list[dict]` | Final serializable output (see Output table) |
| `_DEFINITION_NAME_NODE_TYPES` | `set[str]` | AST node types considered valid definition-name candidates (`identifier`, `type_identifier`, `namespace_identifier`) |
| BFS queue in `_find_definition_node` | `deque[(node, parent)]` | Traversal state for locating a definition node by name while tracking its parent |

# Error Handling

**Overall strategy: graceful degradation with narrow, targeted suppression.**
This module favors continuing the dependency graph build over failing the whole analysis. Most steps operate on a best-effort basis: unresolvable imports, unreadable files, or missing definitions are silently skipped or recorded as absent (`None` / empty set), rather than raising. Only one explicit exception-handling block exists (file read for same-package reference detection); all other error resilience is achieved through conditional checks (e.g., `if resolved:`, `if parent_node is not None:`) rather than try/except, meaning parsing or lookup failures from dependencies (`parse_file`, `resolve_module_to_project_path`, etc.) are expected to already degrade gracefully upstream (returning `None`/empty results) instead of raising.

| Error type | Handling | Impact |
|---|---|---|
| File read failure or invalid UTF-8 when scanning same-package files (`OSError`, `UnicodeDecodeError`) | Caught explicitly per file; the file is skipped in the same-package reference scan | That file contributes no implicit same-package callee edges, but the rest of the graph build continues unaffected |
| Import module not resolvable to a project file (`resolve_module_to_project_path` returns `None`) | Simply not added to `callee_set`; no error raised | External/stdlib/unresolvable imports are excluded from the graph; no crash |
| No import query/language available for a file extension (`get_import_params` returns `(None, None)`) | Import extraction step is skipped entirely for that file | File still appears as a node in the graph but contributes no import-based callee edges |
| Definition node not found for a callee name (`_find_definition_node` returns `None`) | `extract_callee_source` tries the secondary (leading) name part, then returns `None` if still not found | Caller (`usage_analysis.py`) receives `None` and presumably treats it as "no source available"; no exception propagates |
| Definition search encountering identifiers inside import statements | `_is_inside_import` filters these out during BFS traversal so they aren't mistaken for definitions | Prevents false-positive matches on re-exported/imported names; does not affect control flow or error state |
| Excluded directories/files (matching `EXCLUDE_PATTERNS`) | Proactively pruned from `os.walk` traversal (directories) or skipped (files) before any parsing is attempted | Avoids wasted parsing effort and potential parse errors on irrelevant files (e.g., `.git`, `node_modules`) |
| Missing entries in `file_caller_map` for a callee path (e.g., callee outside `all_file_list`) | Guarded by `if callee_path in file_caller_map` before appending | Prevents `KeyError`; such callees simply do not get a caller edge recorded |

**Design considerations:**
- The function intentionally treats absence of a resolution (import target, definition, source root) as a normal, expected outcome rather than an error condition, since real-world codebases always contain external dependencies, dynamic references, and ambiguous names that cannot be statically resolved.
- Error suppression is scoped as narrowly as possible (only around file I/O in the same-package scan) so that genuine bugs in earlier pipeline stages (e.g., malformed AST from `parse_file`, missing config entries) are not accidentally masked.
- The module relies on its dependencies (`parse_file`, `resolve_module_to_project_path`, `get_import_params`, `extract_imports`) to already return safe sentinel values (`None`, empty lists) on failure rather than raising, allowing this file to remain free of broad try/except wrapping around parsing and resolution logic.
- No logging calls are made within this file despite a module-level `logger` being configured, indicating that silent degradation (rather than warning/error logging) is the chosen behavior for unresolved imports or missing definitions.

# Summary

`dependency_graph.py` builds a file-level dependency graph by scanning a project's source tree, resolving imports (via `imports.py`/`import_to_path.py`) and same-package visibility rules, into `build_project_dependencies(project_dir) -> list[dict]` (`file`/`callers`/`callees`, project-relative paths). It also exposes `extract_callee_source(callee_file_path, callee_name, project_dir) -> str|None`, doing BFS AST search for a definition's source. Uses `parse_file`, config settings, and `rel_to_copy_path`. Consumed by `pipeline.py` and `usage_analysis.py`. Degrades gracefully on unresolved imports/files.
