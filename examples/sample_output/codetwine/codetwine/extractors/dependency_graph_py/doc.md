# Design Document: codetwine/extractors/dependency_graph.py

# Overview & Purpose

## Role and Responsibilities

`dependency_graph.py` is CodeTwine's project-level dependency analysis module. It exists as a separate file because it operates at a different granularity than the per-file parsing/import-extraction utilities it depends on (`ts_parser.py`, `imports.py`, `import_to_path.py`): rather than analyzing a single file's AST, it orchestrates those primitives across an entire project to build a bidirectional caller/callee graph, and separately supports extracting individual definition source snippets for downstream documentation/usage-analysis features.

Its two responsibilities are distinct but complementary:

1. **Project-wide dependency graph construction** (`build_project_dependencies`): walks the project directory, collects all files with supported extensions, resolves each file's imports to in-project file paths, adds implicit same-package (Java/Kotlin) dependencies via regex-based class-name detection, and produces a caller/callee index expressed in copy-path format for use by the output pipeline.
2. **Definition source extraction** (`extract_callee_source` and its helpers): given a target file and a symbol name, locates the AST node defining that symbol (skipping references inside import statements) and returns its full source text, for use by `usage_analysis.py` when embedding dependency source snippets into documentation.

These two concerns are grouped in one module because both rely on the same underlying primitive (BFS traversal of tree-sitter ASTs to locate definitions/imports) and both feed into the same downstream dependency-graph/documentation pipeline.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parses the target file and returns the full source text of the AST node defining `callee_name` (trying the trailing then leading part of dotted names), or `None` if not found. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Scans the project, resolves imports and same-package references into a file-level dependency graph, and returns a list of `{"file", "callers", "callees"}` dicts using `"project_name/copy_path"` formatted paths. |

## Internal (module-private) Helpers

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `_is_inside_import` | `node` (tree-sitter Node) | `bool` | Walks up the AST from a node via `.parent` to determine if it sits inside an import/include statement, so such nodes are excluded from definition search. |
| `_find_definition_node` | `root_node`, `definition_name: str` | Node \| `None` | Performs a BFS over the AST for an identifier-like node matching `definition_name` (excluding import-context matches) and returns its parent (the enclosing definition construct). |
| `_DEFINITION_NAME_NODE_TYPES` | — | `set[str]` | Constant set (`identifier`, `type_identifier`, `namespace_identifier`) defining which node types can represent a definition name across supported languages. |

## Design Decisions and Patterns

- **Breadth-first search over AST**: Both `_find_definition_node` (definition lookup) and `build_project_dependencies`'s reliance on `extract_imports` favor BFS via an explicit `deque`, ensuring shallower/earlier definitions are matched first rather than deep-first traversal artifacts.
- **Graceful degradation**: Functions return `None` / skip entries rather than raising when definitions, imports, or files cannot be resolved (e.g., unreadable same-package files are silently skipped via `try/except (OSError, UnicodeDecodeError)`).
- **Reuse of cached parsing**: Delegates all AST parsing to `parse_file`, relying on its module-level cache (`parse_cache`) to avoid redundant re-parsing of files already analyzed elsewhere in the pipeline.
- **Path normalization strategy**: Internally works with absolute paths (`os.path.abspath`) as dictionary keys during graph construction (`file_callee_map`, `file_caller_map`) to avoid path-equality ambiguity, then converts to project-relative, copy-path-formatted (`rel_to_copy_path`) strings only at the final output stage.
- **Two-pass graph construction**: Callees are computed first (via imports and same-package heuristics), then callers are derived as a reverse index (Step 4) rather than tracked bidirectionally during the initial pass, keeping the resolution logic one-directional and simpler.
- **Heuristic same-package visibility**: For languages configured via `SAME_PACKAGE_VISIBLE` (Java/Kotlin), dependency detection falls back to regex word-boundary matching of class names (derived from filenames) within same-directory, same-extension file groups, complementing import-based resolution where such languages allow import-free same-package references.

# Definition Design Specifications

## `_is_inside_import`

Takes a tree-sitter `Node` and returns a `bool` indicating whether that node lies within an import/include statement, by walking `.parent` links up to the root and checking `node.type` for the substring `"import"` or exact match `"preproc_include"`.

Exists to distinguish "reference" occurrences of an identifier (e.g. `import foo`) from actual "definition" occurrences, so that definition search does not mistakenly match names inside import statements.

Design decision: uses a generic substring check (`"import" in node_type`) rather than an exhaustive list of node type names, so it works across multiple tree-sitter grammars (Python, JS, Java, Kotlin, etc.) that name their import nodes differently (`import_statement`, `import_from_statement`, `import_declaration`, etc.) without needing per-language configuration. `preproc_include` is handled as a special case for C/C++ since it doesn't contain "import" in its name.

Edge case: a node with no ancestors (root node itself passed as `node.parent is None`) returns `False`.

## `_find_definition_node`

Takes `root_node` (AST root) and `definition_name` (`str`), and returns the parent `Node` of the first matching definition-name identifier found via BFS, or `None` if no match exists.

Exists to locate the syntactic container (function/class/assignment/etc.) that defines a given name, which is the actual unit of source code to extract for cross-file usage/dependency analysis.

Design decisions:
- Uses BFS (via a `deque` of `(node, parent)` pairs) rather than DFS so that the shallowest/earliest matching definition in the tree is found first, favoring top-level definitions over deeply nested ones with the same name.
- Restricts matching to `_DEFINITION_NAME_NODE_TYPES` (`identifier`, `type_identifier`, `namespace_identifier`) since these are the node types tree-sitter grammars use for name tokens across supported languages.
- Skips nodes for which `_is_inside_import` is `True`, avoiding false positives where the name merely appears in an import statement.
- Returns the immediate parent node (not the identifier node itself), since the parent typically represents the enclosing definition construct (e.g. `function_definition`, `class_definition`, `assignment`).

Constraint: only the first BFS match is returned; if multiple definitions with the same name exist in the file, later/deeper ones are not considered.

## `extract_callee_source`

Takes `callee_file_path` (`str`, project-relative path to the dependency target file), `callee_name` (`str`, the name of the definition to retrieve, possibly dotted like `"helper.process"`), and `project_dir` (`str`, absolute project root path). Returns the source code (`str`) of the matching definition, or `None` if not found.

Exists as the primary lookup used by usage analysis to pull in the actual source of a symbol referenced across files, given only its file path and name.

Design decisions:
- Relies on `parse_file`'s module-level cache so repeated lookups into the same callee file do not re-parse it.
- Handles dotted names (attribute access) by first searching for the trailing segment (e.g. `"process"` in `"helper.process"`), since that is typically the actual member/function definition; if not found, falls back to searching for the leading segment (e.g. `"helper"`), covering cases like `"TEMPLATE.format"` where the trailing part is a built-in method and the real definition is the leading identifier (`TEMPLATE`).
- Returns the full source text of the parent node found by `_find_definition_node`, giving callers a ready-to-use code snippet rather than requiring further AST traversal.

Edge case: if `callee_name` has no `.`, only the single search name is tried. If neither search name yields a definition, returns `None`.

## `build_project_dependencies`

Takes `project_dir` (`str`, root directory of the project to analyze) and returns `list[dict]`, where each dict has keys `"file"`, `"callers"`, and `"callees"`, all path strings in `"project_name/copy_path"` format.

Exists as the top-level entry point that scans an entire project, resolves cross-file import relationships (and same-package implicit relationships for languages like Java/Kotlin), and produces an in-memory dependency graph consumed by the rest of the pipeline (e.g. for building documentation with caller/callee context).

Important design decisions:
- Restricts analysis to files whose extensions appear in `DEFINITION_DICTS`, ensuring only languages with defined tree-sitter support are processed.
- Excludes directories/files matching `EXCLUDE_PATTERNS` by pruning `os.walk`'s `dir_names` in place, which lets `os.walk` skip entire excluded subtrees efficiently rather than filtering results after the fact.
- Builds `project_file_set` (relative paths) up front so that import resolution (`resolve_module_to_project_path`) can distinguish project-internal modules from external/stdlib modules purely via set membership.
- Calls `detect_source_roots` once for the whole project (not per-file) since source root prefixes like `src/main/java/` are project-wide conventions, not file-specific.
- Uses absolute paths as internal dictionary keys (`file_callee_map`, `file_caller_map`) throughout the graph-building steps to avoid path ambiguity, only converting to project-relative and then `project_name/copy_path` format at the final output step.
- Adds an extra pass (Step 3.5) for languages flagged in `SAME_PACKAGE_VISIBLE` (e.g. Java/Kotlin) to capture implicit same-directory class references that don't require an explicit import statement. This is done by grouping files by `(directory, extension)`, deriving each file's "class name" from its filename stem, and regex-searching (`\bClassName\b`) other files' source text for that name — a heuristic textual check rather than full AST-based resolution, since such references may not appear as parseable import statements.
- Builds the reverse "callers" index (Step 4) by inverting the callee map, so each file's callers list is derived data rather than independently computed, guaranteeing consistency between callers and callees.
- Converts final paths through `rel_to_copy_path` to align with the actual on-disk output folder structure used elsewhere in the pipeline, ensuring returned paths remain valid identifiers even if the project is relocated.

Edge cases and constraints:
- Files that fail to open/decode during the same-package check (`OSError`, `UnicodeDecodeError`) are silently skipped, so a corrupt/binary-like file only loses its same-package callee detection, not the whole build.
- If a language has no import query or language object (`get_import_params` returns `(None, None)`), import-based callee detection is skipped for that file entirely, though it is still included in `all_file_list` and can still be a target of same-package detection or of other files' callees.
- `callee_set`/`file_caller_map` use sets/lists of absolute paths internally; only files present in `file_caller_map` (i.e., part of `all_file_list`) are recorded as callers, so resolved paths outside the collected file set are effectively dropped at that stage (though `resolve_module_to_project_path` should already only return in-project paths).

# Dependency Description

## Dependencies (what this file uses)

- **`codetwine/parsers/ts_parser.py` (`parse_file`)**: Used to obtain the tree-sitter AST root node and byte content for each project file. This underlies both the definition-search logic in `extract_callee_source` and the import-statement extraction in `build_project_dependencies`. Parse results are cached at the module level, so this file benefits from avoiding redundant parsing when the same file is analyzed multiple times.

- **`codetwine/extractors/imports.py` (`extract_imports`)**: Used to extract structured import information (module name, imported names, aliases) from a file's AST, forming the basis for resolving which files a given file depends on (callees).

- **`codetwine/import_to_path.py` (`detect_source_roots`, `resolve_module_to_project_path`, `get_import_params`)**: Used together to translate raw import statements into concrete project file paths. `get_import_params` supplies the language and query needed to extract imports for a given file extension; `detect_source_roots` identifies source-root prefixes (e.g. `src/main/java/`) present in the project to improve resolution accuracy; `resolve_module_to_project_path` performs the actual mapping from a module reference to an in-project file path, filtering out external/stdlib modules.

- **`codetwine/utils/file_utils.py` (`rel_to_copy_path`)**: Used to convert project-relative file paths into the copy-destination directory structure format, ensuring the paths returned in the dependency graph match the actual output folder layout.

- **`codetwine/config/settings.py` (`DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE`)**: `DEFINITION_DICTS.keys()` determines which file extensions are supported for analysis; `EXCLUDE_PATTERNS` is used to skip irrelevant directories/files (e.g. build artifacts, VCS folders) during project traversal; `SAME_PACKAGE_VISIBLE` identifies languages (e.g. Java/Kotlin) where files in the same directory/package can implicitly reference each other without explicit imports, enabling detection of same-package dependencies.

## Dependents (what uses this file)

- **`codetwine/pipeline.py`**: Calls `build_project_dependencies` to construct the project-wide dependency graph as an initial step in the analysis pipeline, using its output (file/caller/callee lists) for subsequent processing steps.

- **`codetwine/extractors/usage_analysis.py`**: Calls `extract_callee_source` to retrieve the definition source code of a given symbol name from its resolved source file, using it to obtain the actual code body of dependencies referenced during usage analysis.

The dependency direction between this file and its dependents is unidirectional: `pipeline.py` and `usage_analysis.py` depend on functionality provided by `dependency_graph.py`, while this file has no reverse dependency on either.

# Data Flow

## Input

| Source | Format |
|---|---|
| `project_dir` (function argument) | Absolute path string to the project root directory |
| Filesystem (via `os.walk`) | Directory tree containing source files of various languages |
| `DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE` (config) | Static config dicts/lists used as filters |

## Transformation Flow

```
project_dir
   │
   ▼
[Step 1] os.walk + extension/exclude filtering
   → all_file_list (absolute paths, supported languages only)
   │
   ▼
[Step 2] relpath conversion
   → project_file_set (relative path strings)
   → source_root_set (via detect_source_roots)
   │
   ▼
[Step 3] per-file: parse_file → extract_imports → resolve_module_to_project_path
   → file_callee_map: { abs_path: set(abs_resolved_callee_paths) }
   │
   ▼
[Step 3.5] group same-directory/same-ext files (Java/Kotlin) →
   regex-search class names in source text →
   add implicit same-package callees into file_callee_map
   │
   ▼
[Step 4] invert file_callee_map
   → file_caller_map: { abs_path: [caller_abs_paths] }
   │
   ▼
[Step 5] relpath + rel_to_copy_path + project_name prefix
   → file_info_list (final output)
```

Additionally, `extract_callee_source` performs an independent, on-demand flow:
```
(callee_file_path, callee_name, project_dir)
   → absolute_path → parse_file → root_node
   → split callee_name on "." → search_names (trailing part, then leading part)
   → _find_definition_node (BFS over AST, skipping nodes inside import statements)
   → matched node's source text (str) or None
```

## Output

**`build_project_dependencies`** returns `list[dict]`, each dict:

| Field | Type | Meaning |
|---|---|---|
| `file` | str | `"{project_name}/{copy_path}"` of the analyzed file |
| `callers` | list[str] | Files that depend on (import/reference) this file, same path format |
| `callees` | list[str] | Files this file depends on (imports or same-package references), same path format |

Consumed by `pipeline.py` (`build_project_dependencies` → converted to internal paths, used to construct the full dependency graph for the pipeline).

**`extract_callee_source`** returns `str | None` (raw definition source code text, or `None` if not found). Consumed by `usage_analysis.py` to attach definition source code to identified usages.

## Key Data Structures

| Structure | Shape | Purpose |
|---|---|---|
| `all_file_list` | `list[str]` (absolute paths) | Master list of files to analyze |
| `project_file_set` | `set[str]` (relative paths) | Fast membership check for "is this module inside the project" during import resolution |
| `source_root_set` | `set[str]` | Known source-root prefixes (e.g. `src/main/java/`) to retry unresolved import paths |
| `file_callee_map` | `dict[str(abs_path) → set[str(abs_path)]]` | Intermediate forward dependency graph (file → its dependencies) |
| `dir_ext_groups` | `dict[(dirname, ext) → list[abs_path]]` | Groups files eligible for same-package implicit visibility (Java/Kotlin), keyed by directory+extension |
| `class_names` / `class_patterns` | `dict[abs_path → str]` / `dict[abs_path → re.Pattern]` | Per-file class name and compiled regex used to detect same-package references in source text |
| `file_caller_map` | `dict[str(abs_path) → list[str(abs_path)]]` | Intermediate reverse dependency graph (file → files that depend on it) |
| `file_info_list` | `list[dict]` | Final serializable output combining file/callers/callees in copy-path format |
| `search_names` (in `extract_callee_source`) | `list[str]` | Ordered candidate identifier names (trailing then leading part of dotted `callee_name`) to search for a matching definition node |

# Error Handling

This module follows a **graceful degradation** strategy overall: rather than raising exceptions when a file, import, or definition cannot be resolved, functions return `None`, empty collections, or simply skip the problematic item so that the dependency graph build continues for the rest of the project. The only exception is file I/O during the same-package reference scan, where errors are explicitly caught and the file is skipped rather than aborting the whole analysis.

| Error Type | Handling | Impact |
|---|---|---|
| Unresolvable import module (not a project file) | `resolve_module_to_project_path` returns `None`; the module is simply not added to `callee_set` | That import edge is omitted from the graph; other imports/files are unaffected |
| Unsupported file extension for import analysis | `get_import_params` returns `(None, None)`; import parsing is skipped for that file (`if language and import_query_str` guard) | File is still included in the graph (as a node) but contributes no callee edges |
| Definition node not found in callee file (`_find_definition_node`) | Returns `None`; `extract_callee_source` tries the leading-part fallback name, and if still not found returns `None` | Caller (`usage_analysis.py`) receives `None` and handles the missing source separately; no crash |
| AST nodes inside import/include statements matching the search name | `_is_inside_import` filters these out during BFS so they are not mistaken for definitions | Prevents false-positive matches; does not raise or halt processing |
| Unreadable/undecodable source file during same-package scan (`OSError`, `UnicodeDecodeError`) | Caught explicitly with `try/except`, and the file is skipped via `continue` | That file is excluded from same-package implicit-dependency detection, but the rest of the group and the overall graph build proceed normally |
| Directories/files matching `EXCLUDE_PATTERNS` | Filtered out during `os.walk` traversal (`dir_names[:]` pruning and `continue` on files) before any parsing is attempted | Prevents unnecessary parsing errors on irrelevant directories (e.g. `.git`, `node_modules`); no explicit exception path needed |
| Parsing failures in `parse_file` (unsupported extension, unreadable file) | Not caught in this module; underlying errors would propagate | A malformed or unsupported file passed as a supported extension could cause the overall build to fail (no local safeguard) |

**Design considerations:**
- The module favors returning `None`/empty results at each resolution step (import resolution, definition lookup) so that a single unresolved reference does not interrupt graph construction for the entire project — consistent with the graceful-degradation style of its dependencies (`import_to_path.py`, `imports.py`).
- The one place where hard failures are anticipated (reading arbitrary source files for regex-based same-package name matching) is explicitly guarded, since file encoding/permission issues are common and unrelated to the correctness of the rest of the graph.
- Errors from `parse_file` (tree-sitter parsing) are not locally handled, implicitly relying on upstream extension filtering (`supported_ext_set`) and the assumption that files with supported extensions are parseable; this is a fail-fast boundary inherited from `ts_parser.py`.

# Summary

dependency_graph.py performs project-wide dependency analysis: `build_project_dependencies(project_dir)` walks the project, resolves imports/same-package refs into a file-level caller/callee graph (list of `{file, callers, callees}` dicts, paths as "project_name/copy_path"). `extract_callee_source(callee_file_path, callee_name, project_dir)` returns source text of a symbol's definition via BFS over tree-sitter ASTs. Uses ts_parser, imports.py, import_to_path.py; consumed by pipeline.py and usage_analysis.py. Emphasizes graceful degradation and absolute-path keyed intermediate maps.
