# Design Document: codetwine/extractors/dependency_graph.py

# Overview & Purpose

`codetwine/extractors/dependency_graph.py` is responsible for statically analyzing a project's source tree and constructing an in-memory, file-level dependency graph (callers/callees) that downstream stages (e.g. `pipeline.py`) use to understand cross-file relationships, and for retrieving the actual source code of a specific definition referenced from another file (used by `usage_analysis.py` when resolving symbol usages to their defining source).

It exists as a separate file to isolate two closely related but distinct concerns from the rest of the extraction pipeline:
1. **Whole-project dependency graph construction** — walking the project, resolving imports to project-internal files via `import_to_path.py`, and additionally inferring implicit same-package dependencies (Java/Kotlin) that are not expressed through explicit imports.
2. **Definition source retrieval** — given a target file and a symbol name, locating and extracting the exact source snippet of that symbol's definition via AST traversal, decoupled from the import-resolution logic itself.

By centralizing these two operations here, other modules (`pipeline.py`, `usage_analysis.py`) can consume a clean, higher-level API without needing to know about tree-sitter traversal details, import resolution mechanics, or path-normalization conventions (delegated to `import_to_path.py`, `ts_parser.py`, and `file_utils.py`).

### Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str, callee_name: str, project_dir: str` | `str \| None` | Parses the target file and returns the full source text of the definition matching `callee_name` (handling `obj.method`-style names by falling back from the trailing to the leading part), or `None` if not found. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Scans all supported files in the project, resolves imports and same-package implicit references into a caller/callee map, and returns a list of `{"file", "callers", "callees"}` entries using `"project_name/copy_path"`-formatted paths. |

Internal (non-public) helpers `_is_inside_import` and `_find_definition_node` support `extract_callee_source` by performing BFS-based AST traversal to locate identifier definitions while excluding references inside import/include statements; they are not part of the file's external contract.

### Design Decisions

- **Delegation over duplication**: Actual import parsing (`extract_imports`), module-to-path resolution (`resolve_module_to_project_path`, `detect_source_roots`, `get_import_params`), and path formatting (`rel_to_copy_path`) are all delegated to dedicated modules; this file only orchestrates them and adds project-graph-specific logic (same-package visibility, caller/callee indexing).
- **BFS over recursive DFS** for AST search (`_find_definition_node`), using a `deque`-based queue, chosen to explicitly control traversal order and avoid Python recursion depth concerns when scanning large ASTs.
- **Reference filtering via ancestor walk**: `_is_inside_import` walks `node.parent` chains rather than inspecting query captures, keeping definition detection independent of language-specific import query definitions and applicable uniformly across languages.
- **Language-agnostic, config-driven behavior**: Which extensions get import analysis (`get_import_params`) and which get same-package implicit dependency detection (`SAME_PACKAGE_VISIBLE`) are both driven by `codetwine/config/settings.py`, keeping this file free of per-language branching.
- **Caching reuse**: Relies on `parse_file`'s module-level cache (in `ts_parser.py`) so files parsed during import extraction are not re-parsed when later used for same-package regex scanning or definition search elsewhere in the pipeline.
- **Two-pass graph construction**: Callees are computed first per file (Step 3, plus Step 3.5 for implicit same-package callees), then callers are derived as a reverse index (Step 4) over the completed callee map, ensuring consistency between the two directions before final path conversion (Step 5).

# Definition Design Specifications

## `_is_inside_import(node) -> bool`

**Arguments:**
- `node`: A tree-sitter AST node whose ancestry needs to be checked.

**Returns:** `bool` — `True` if any ancestor node's type contains `"import"` or equals `"preproc_include"`.

**Responsibility:** Distinguishes identifier occurrences that are mere references inside import/include statements from actual definitions, so that definition search does not mistakenly match a name used in an import line.

**Design decisions:** Uses a substring check (`"import" in node_type`) rather than an exact match against a fixed list of node types, since import-related node type names vary across languages (`import_statement`, `import_from_statement`, `import_declaration`, etc.) but consistently contain the word "import"; `preproc_include` (C/C++) is handled as a special case since it doesn't follow that naming pattern.

**Edge cases:** Returns `False` for a root node or any node with no import ancestor; relies on `node.parent` chain terminating at `None`.

---

## `_DEFINITION_NAME_NODE_TYPES` (module-level set)

A constant set of AST node type strings (`identifier`, `type_identifier`, `namespace_identifier`) considered candidates for holding a definition's name across the supported languages (Python/Java/Kotlin/JS identifiers, C/C++/TS type names, C++ namespaces). Centralizes the language-agnostic notion of "a name node" used by `_find_definition_node`.

---

## `_find_definition_node(root_node, definition_name: str)`

**Arguments:**
- `root_node`: AST root node of the file to search.
- `definition_name`: The exact name string to look for (e.g., a function, class, or variable name).

**Returns:** The parent node of the matching name node (the node presumably representing the actual definition, e.g. a function/class/assignment node), or `None` if no match is found.

**Responsibility:** Locates the definition node for a given name in a language-agnostic way by scanning identifier-like nodes rather than relying on per-language grammar-specific definition node types.

**Design decisions:**
- Uses BFS (via a `deque`) instead of DFS, returning the shallowest/first-encountered match in level order — this is an explicit design choice to prefer top-level or earlier definitions when multiple candidates share the same name.
- Skips any candidate found inside an import statement (via `_is_inside_import`), ensuring import references are not mistaken for definitions.
- Returns the **parent** of the matched name node, not the name node itself, since the parent is expected to be the syntactic construct that constitutes the full definition (function/class/assignment).

**Edge cases:** If `definition_name` matches multiple nodes, only the first BFS-order match not inside an import is returned; no disambiguation by node type (e.g., preferring `function_definition` over `assignment`) is performed.

---

## `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) -> str | None`

**Arguments:**
- `callee_file_path`: File path relative to the project root pointing to the file expected to contain the definition.
- `callee_name`: Name of the definition to retrieve; may be a simple identifier or a dotted/attribute-style name (e.g. `"helper.process"`).
- `project_dir`: Absolute path to the project root, used to resolve `callee_file_path` into an absolute path for parsing.

**Returns:** The full source code text (`str`) of the matched definition's parent node, or `None` if no definition is found under any candidate name.

**Responsibility:** Given a cross-file reference name, retrieves the actual source code of its definition, enabling downstream consumers (e.g. `usage_analysis.py`) to inline or display dependency source code.

**Design decisions:**
- Handles dotted names by trying the trailing segment first (e.g. `process` in `helper.process`), then falling back to the leading segment (e.g. `TEMPLATE` in `TEMPLATE.format`) if the first search fails — this order reflects the intuition that the last segment is usually the actual member/function name, except when it's a built-in method call on a project-defined object/constant.
- Delegates actual parsing to `parse_file`, benefiting from its module-level cache to avoid repeated I/O and re-parsing across multiple `extract_callee_source` calls targeting the same file.

**Edge cases:** Returns `None` if the file has no matching identifier for any of the candidate search names; does not validate that `callee_file_path` exists or is parseable (errors from `parse_file` propagate).

---

## `build_project_dependencies(project_dir: str) -> list[dict]`

**Arguments:**
- `project_dir`: Absolute or relative root directory of the project to analyze.

**Returns:** A list of dictionaries, one per supported source file, each with:
- `"file"`: string in `"{project_name}/{copy_path}"` format identifying the file.
- `"callers"`: list of files (same path format) that depend on this file.
- `"callees"`: list of files (same path format) that this file depends on.

**Responsibility:** Serves as the single entry point for constructing a project-wide, language-agnostic file dependency graph by combining static import resolution and (for certain languages) same-package implicit visibility, then converting the result into the copy-destination path format used throughout the pipeline.

**Design decisions / algorithm rationale:**
- **File discovery**: Walks the directory tree once, pruning excluded directories in-place (`dir_names[:] = ...`) so `os.walk` does not descend into them at all (an efficiency measure, not just a filter after the fact); files are also filtered by `EXCLUDE_PATTERNS` and restricted to extensions present in `DEFINITION_DICTS`.
- **Project file set as a relative-path lookup**: Built once and reused for all import resolution calls, since resolution needs to test many candidate paths per import without hitting the filesystem.
- **Source root detection**: Performed once globally (`detect_source_roots`) rather than per file, since source root prefixes (e.g. Java's `src/main/java/`) are a project-wide property, not file-specific.
- **Callee collection via imports**: For each file, only executes import parsing when `get_import_params` returns a valid `(language, import_query_str)` pair — this lets unsupported languages' files still appear in the graph (as nodes with no import-based callees) without failing.
- **Same-package visibility (Step 3.5)**: Addresses languages (per `SAME_PACKAGE_VISIBLE`) where a class can be referenced without an explicit import if in the same directory/package. Files are grouped by `(directory, extension)`, and for each file, a word-boundary regex (`\bClassName\b`) tests whether other class names in the group appear as substrings in its source text. This heuristic favors recall (any textual mention counts as a dependency) over precision (no distinction between a genuine reference and, e.g., a comment or string mentioning the name), and is deliberately restricted to same-directory groups sharing the same extension to limit scope and cost.
- **Directionality of same-package edges**: Explicitly unidirectional — edges are added only from the file whose source *mentions* another class's name to that other file, not automatically bidirectionally, since only one file may actually reference the other's name in code.
- **Callers as reverse index**: Built as a separate pass over the callee map rather than computed inline during callee discovery, since callers cannot be known until all files' callees have been resolved.
- **Path format conversion**: All internal computation uses absolute paths (for correctness while merging results from different steps); conversion to the public relative `"{project_name}/{copy_path}"` format happens only at the end, via `rel_to_copy_path`, to match the physical output directory layout used elsewhere in the pipeline.

**Edge cases / constraints:**
- Files with unresolvable imports (e.g., external packages, stdlib modules) simply contribute no callee edge for that import — `resolve_module_to_project_path` returning `None` is silently skipped.
- Files that fail to read as UTF-8 text during the same-package check (`OSError`, `UnicodeDecodeError`) are skipped for that check without raising.
- A file with no callers or callees still appears in the output with empty `callers`/`callees` lists.
- Assumes all files in `all_file_list` are uniquely resolvable via `os.path.abspath`; relies on consistent path handling (`\\` to `/` normalization) for cross-platform relative path keys.

# Dependency Description

## Dependencies (what this file uses)

- **`codetwine.parsers.ts_parser.parse_file`**: Used to parse source files into tree-sitter ASTs (root node and byte content), both for extracting import statements per file and for locating definition nodes when retrieving callee source code. Leverages the module-level parse cache to avoid redundant parsing across the pipeline.
- **`codetwine.extractors.imports.extract_imports`**: Used to extract structured import information (module name, imported names, aliases) from a file's AST, which is the basis for resolving cross-file dependencies.
- **`codetwine.import_to_path.detect_source_roots`**: Used to detect source root prefixes (e.g. `src/main/java/`) present in the project, enabling correct resolution of imports in projects with nested source layouts (notably Java/Kotlin).
- **`codetwine.import_to_path.resolve_module_to_project_path`**: Used to convert an extracted import's module name into a concrete project-relative file path, determining whether an import target is an internal project file (a callee).
- **`codetwine.import_to_path.get_import_params`**: Used to obtain the tree-sitter `Language` object and import query string for a given file extension, needed to drive import extraction per language.
- **`codetwine.utils.file_utils.rel_to_copy_path`**: Used to convert project-relative file paths into the copy-destination directory structure format used in the final dependency graph output.
- **`codetwine.config.settings.DEFINITION_DICTS`**: Used to determine the set of supported file extensions when collecting project files to analyze.
- **`codetwine.config.settings.EXCLUDE_PATTERNS`**: Used to filter out excluded directories and files (e.g. `.git`, `node_modules`) while walking the project tree.
- **`codetwine.config.settings.SAME_PACKAGE_VISIBLE`**: Used to determine, per file extension, whether same-directory files should be treated as implicitly visible dependencies without explicit imports (e.g. Java/Kotlin same-package classes).

## Dependents (what uses this file)

- **`codetwine/pipeline.py`**: Calls `build_project_dependencies` to construct the project-wide dependency graph as the first step of the analysis pipeline, then converts the resulting paths for further processing.
- **`codetwine/extractors/usage_analysis.py`**: Calls `extract_callee_source` to retrieve the source code of a definition (function, class, variable, etc.) from a resolved dependency target file, in order to analyze usage of that symbol.

The dependency direction is unidirectional: `pipeline.py` and `usage_analysis.py` depend on `dependency_graph.py`'s public functions, while `dependency_graph.py` itself has no dependency back on either file.

# Data Flow

## Input

| Source | Format |
|---|---|
| `project_dir` (function argument) | Absolute path to the project root directory |
| Filesystem (via `os.walk`) | Directory tree of source files |
| File contents (via `parse_file`, `open`) | Raw bytes/text for AST parsing and regex scanning |
| `DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE` (config) | Static lookup tables controlling which extensions/paths are processed |

## Main Transformation Flow

```
project_dir
   │
   ▼
[Step1] os.walk + extension/exclude filtering
   → all_file_list (absolute file paths)
   │
   ▼
[Step2] relative-path conversion
   → project_file_set (set of "path/to/file.ext")
   → source_root_set (via detect_source_roots)
   │
   ▼
[Step3] per-file AST parse + import extraction
   parse_file → root_node
   extract_imports → ImportInfo list
   resolve_module_to_project_path → resolved project file path (or None)
   → file_callee_map: { abs_file_path: set(abs_callee_path) }
   │
   ▼
[Step3.5] same-package implicit dependency detection (Java/Kotlin)
   group files by (directory, ext) → dir_ext_groups
   build class-name regex per file → class_patterns
   scan file source for other classes' names → add to file_callee_map
   │
   ▼
[Step4] reverse-index construction
   file_callee_map → file_caller_map: { abs_file_path: [abs_caller_path, ...] }
   │
   ▼
[Step5] path normalization + copy-path formatting
   rel_to_copy_path + project_name prefix
   → file_info_list (final output)
```

Also, `extract_callee_source` performs an independent, on-demand flow:
```
callee_file_path + callee_name + project_dir
   → parse_file → AST root
   → _find_definition_node (BFS over identifier/type_identifier/namespace_identifier nodes,
      skipping nodes inside import statements via _is_inside_import)
   → matched definition node's source text (or None)
```

## Output

| Function | Output format | Destination |
|---|---|---|
| `build_project_dependencies` | `list[dict]` with keys `file`, `callers`, `callees` (each a `"project_name/copy_path"` string or list thereof) | Returned to caller (`pipeline.py`, which builds `project_dep_list`) |
| `extract_callee_source` | `str \| None` (full source text of the matched definition node) | Returned to caller (`usage_analysis.py`, for source-code enrichment) |

## Key Data Structures

| Structure | Fields / Elements | Purpose |
|---|---|---|
| `all_file_list` | `list[str]` (absolute paths) | Master list of supported-extension files to analyze |
| `project_file_set` | `set[str]` (relative paths) | Fast membership check for resolving imports to in-project files |
| `source_root_set` | `set[str]` (prefixes, e.g. `"src/main/java/"`) | Fallback prefixing when resolving module paths that assume a source root |
| `file_callee_map` | `{abs_path: set[abs_path]}` | Forward dependency map: file → files it imports/references |
| `file_caller_map` | `{abs_path: list[abs_path]}` | Reverse dependency map: file → files that depend on it |
| `dir_ext_groups` | `{(dir, ext): list[abs_path]}` | Groups same-directory, same-extension files for implicit same-package visibility checks (Java/Kotlin) |
| `class_names` / `class_patterns` | `{abs_path: str}` / `{abs_path: re.Pattern}` | Filename-derived class name and its word-boundary regex, used to detect implicit references |
| `file_info_list` (final output) | `list[{"file": str, "callers": list[str], "callees": list[str]}]` | Serialized dependency graph, paths in `"project_name/copy_path"` form |
| `_DEFINITION_NAME_NODE_TYPES` | `{"identifier", "type_identifier", "namespace_identifier"}` | Node types considered as candidate definition names during BFS search |

# Error Handling

This module follows a **graceful degradation** strategy overall: since it performs best-effort static analysis across an entire project's files, unresolvable or unreadable individual files/imports are silently skipped rather than aborting the whole build process. The only explicit error handling in this file is a narrow `try/except` around file reads for same-package reference detection; all other error propagation is implicit (unhandled exceptions from dependencies, such as tree-sitter parsing failures via `parse_file`, are allowed to bubble up and fail the entire `build_project_dependencies` call).

| Error type | Handling | Impact |
|---|---|---|
| File read failure during same-package scan (`OSError`, `UnicodeDecodeError`) | Caught explicitly; the file is skipped (`continue`), no callee edges added for it | That file is excluded only from same-package implicit dependency detection; other steps (import-based callees, callers) are unaffected |
| Unresolvable import (`resolve_module_to_project_path` returns `None`) | Silently ignored; no callee edge added | Reduces graph completeness but does not raise; external/stdlib imports are naturally filtered out this way |
| Unsupported file extension for import analysis (`get_import_params` returns `(None, None)`) | Import parsing step is skipped for that file | File still appears in the graph as a node but contributes no import-derived callees |
| Parsing failure in `parse_file` (e.g. unsupported extension, unreadable file) | Not caught; exception propagates | Aborts `build_project_dependencies` entirely (fail-fast at the parsing layer) |
| Definition lookup failure in `extract_callee_source` (`_find_definition_node` finds nothing for any candidate name) | Function returns `None` instead of raising | Caller (`usage_analysis.py`) must handle the `None` case; no source snippet is produced for that callee |
| Excluded directories/files (`EXCLUDE_PATTERNS`) | Proactively filtered out during `os.walk` traversal before any parsing is attempted | Prevents unnecessary errors from non-source directories (e.g. `.git`, `node_modules`) rather than reacting to failures |

**Design considerations:**
- The narrow catch scope (only `OSError`/`UnicodeDecodeError` during raw file reads) reflects that this is the sole place in the file doing manual I/O outside the cached `parse_file` abstraction; all other file reads are delegated to `parse_file`, which is trusted to succeed for files already confirmed to have supported extensions.
- Returning `None`/empty collections (rather than raising) is the consistent convention for "not found" or "not resolvable" conditions throughout the dependency-resolution pipeline (`resolve_module_to_project_path`, `_find_definition_node`, `extract_callee_source`), letting callers decide how to handle absence without exception-driven control flow.
- No retry, logging of skipped/failed items, or partial-result reporting is performed for the same-package file-read failures or unresolved imports—these are treated as expected, benign cases rather than exceptional conditions requiring diagnostics.

# Summary

`dependency_graph.py` builds a project-wide, language-agnostic file dependency graph and retrieves definition source code for cross-file references. Public API: `build_project_dependencies(project_dir)` → list of `{file, callers, callees}` dicts (paths as "project_name/copy_path"), combining import resolution and same-package implicit visibility (Java/Kotlin); `extract_callee_source(callee_file_path, callee_name, project_dir)` → definition source text or None, via BFS AST search. Delegates parsing/import-resolution/path-formatting to helper modules. Used by `pipeline.py` and `usage_analysis.py`.
