# Design Document: codetwine/extractors/dependency_graph.py

## Overview & Purpose

## 1. Module Summary

Builds a project-wide inter-file dependency graph by resolving import statements and same-package references across all supported source files, and extracts the source code of named definitions from dependency target files.

## 2. When to Use This Module

- **Building the full project dependency graph**: Call `build_project_dependencies(project_dir)` to obtain a list of dicts describing each file's callers and callees across the entire project. Used by `codetwine/pipeline.py` as the first step of the analysis pipeline.
- **Retrieving a specific definition's source code from a dependency file**: Call `extract_callee_source(callee_file_path, callee_name, project_dir)` to get the source text of a named function, class, variable, or type from a given file. Used by `codetwine/extractors/usage_analysis.py` when resolving cross-file symbol references during usage analysis.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parses the target file's AST and returns the source text of the parent node containing the named definition; tries the trailing then the leading part of dotted names. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walks all supported-extension files in the project, resolves their imports to project-internal paths, adds implicit same-package dependencies for Java/Kotlin, builds a reverse caller index, and returns a list of `{"file", "callers", "callees"}` dicts using `project_name/copy_path` formatted paths. |

## 4. Design Decisions

- **Same-package implicit dependency detection**: For languages where `SAME_PACKAGE_VISIBLE` is enabled (e.g. Java/Kotlin), files in the same directory with the same extension are scanned with a word-boundary regex to detect class name references that exist without explicit imports. These are added as unidirectional callees, supplementing the import-based graph.
- **Dotted name fallback in `extract_callee_source`**: When resolving a dotted name such as `helper.process`, the function first searches for the trailing part (`process`) and, if not found, falls back to the leading part (`helper`). This handles both attribute access on imported objects and built-in method calls on project-defined constants.
- **AST node parent traversal for definition vs. import discrimination**: The BFS-based definition search skips any identifier node that appears inside an import statement (detected by walking `node.parent` ancestors), ensuring that import references are not mistaken for definitions.
- **Parse result reuse**: `extract_callee_source` relies on the module-level cache in `ts_parser.py` (`parse_cache`), so files parsed during dependency graph construction are not re-parsed when definition source extraction is performed later.

## Definition Design Specifications

---

## Module-Level Constants

### `_DEFINITION_NAME_NODE_TYPES`
- **Type:** `set[str]`
- **Value members:** `"identifier"`, `"type_identifier"`, `"namespace_identifier"`
- **Responsibility:** Centralizes the set of AST node types that represent a named definition across supported languages, used as a filter in BFS traversal.

---

## Functions

### `_is_inside_import`

| Item | Detail |
|---|---|
| **Signature** | `_is_inside_import(node) -> bool` |
| **Argument** | `node` — a tree-sitter `Node` whose ancestry is to be examined |
| **Return** | `True` if any ancestor node type contains the substring `"import"` or equals `"preproc_include"` |

**Responsibility:** Guards against treating imported names as definition sites by walking the parent chain of any AST node upward to the root.

**When to use:** Called internally by `_find_definition_node` before accepting a candidate node as a definition match.

**Design decisions:**
- Uses `node.parent` traversal (tree-sitter's built-in parent pointer) rather than a second query, keeping the implementation independent of language-specific query strings.
- The substring check `"import" in node_type` covers multiple languages' import node type names (e.g., `import_statement`, `import_from_statement`, `import_declaration`) without enumerating each.

**Constraints & edge cases:**
- Relies on tree-sitter's parent pointers being populated; nodes without a connected parent chain will terminate the walk at `None` without error.

---

### `_find_definition_node`

| Item | Detail |
|---|---|
| **Signature** | `_find_definition_node(root_node, definition_name: str)` → parent node or `None` |
| **Argument `root_node`** | AST root node for the entire file |
| **Argument `definition_name`** | Plain identifier string to locate (e.g., `"parse_file"`, `"Point"`) |
| **Return** | The **parent** node of the matched identifier node; `None` if not found |

**Responsibility:** Locates the AST node that contains a named definition by performing a BFS over the entire file's syntax tree, skipping nodes that are inside import statements.

**When to use:** Called by `extract_callee_source` to find where a given name is defined within a dependency file.

**Design decisions:**
- BFS (breadth-first) traversal is used rather than DFS so that top-level definitions are encountered before nested ones, making it more likely the first match is the canonical definition.
- The queue stores `(node, parent)` pairs so the parent can be returned directly without a second lookup.
- Only node types in `_DEFINITION_NAME_NODE_TYPES` are compared against `definition_name`, avoiding false matches on irrelevant node types.

**Constraints & edge cases:**
- Returns the parent of the first matching non-import identifier; if multiple definitions share the same name (e.g., overloaded or shadowed), only the first BFS-encountered one is returned.
- Node text is decoded as UTF-8; files with non-UTF-8 identifiers are not handled.

---

### `extract_callee_source`

| Item | Detail |
|---|---|
| **Signature** | `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) -> str \| None` |
| **Argument `callee_file_path`** | Relative path from project root to the dependency file (e.g., `"src/foo.py"`) |
| **Argument `callee_name`** | Name of the definition to retrieve; may include a dotted accessor (e.g., `"helper.process"`, `"TEMPLATE.format"`) |
| **Argument `project_dir`** | Absolute path to the project root |
| **Return** | UTF-8 source text of the definition's parent AST node; `None` if not found |

**Responsibility:** Retrieves the full source text of a named definition from a dependency file, supporting both direct names and dotted attribute access patterns.

**When to use:** Called by `usage_analysis.py` when the first occurrence of a referenced name is encountered and its definition source needs to be fetched from the owning project file.

**Design decisions:**
- For a dotted name such as `"helper.process"`, the trailing component (`"process"`) is tried first because it is typically the function/method name. The leading component (`"helper"`) is tried as a fallback to handle cases where the trailing part is a built-in method called on a project-defined object (e.g., `"TEMPLATE.format"`).
- Parse results are not explicitly cached in this function; caching is delegated to `ts_parser.parse_cache`, so repeated calls for the same file incur no re-parsing cost.

**Constraints & edge cases:**
- `callee_file_path` must be relative to `project_dir`; the function constructs an absolute path internally.
- Returns the source of the **parent** node of the matched identifier, not just the identifier itself — the quality of the returned snippet depends on AST structure.
- Only the first matching definition is returned; name collisions within a file yield only the BFS-first result.

---

### `build_project_dependencies`

| Item | Detail |
|---|---|
| **Signature** | `build_project_dependencies(project_dir: str) -> list[dict]` |
| **Argument `project_dir`** | Absolute path to the project root directory to analyze |
| **Return** | List of dicts, each with keys `"file"` (`str`), `"callers"` (`list[str]`), `"callees"` (`list[str]`); all paths in `"{project_name}/{copy_path}"` format |

**Responsibility:** Performs a full static analysis of the project directory to produce an in-memory dependency graph by resolving import statements and same-package class references to concrete project files.

**When to use:** Called once per pipeline run by `pipeline.py` as the first step before per-file processing begins.

**Design decisions:**

- **File collection** uses `os.walk` with in-place modification of `dir_names` to prune excluded directories (e.g., `.git`, `node_modules`) from traversal entirely, not just filtering results.
- **Same-package visibility** (Step 3.5) handles Java/Kotlin's implicit class accessibility within the same directory. For each group of files sharing a directory and extension where `SAME_PACKAGE_VISIBLE` is `True`, source text is scanned with a word-boundary regex per candidate class name. This is a unidirectional check: a reference in file A to the class name of file B adds B as a callee of A, but not vice versa unless B also references A's class name.
- **Caller map** (Step 4) is derived by inverting the callee map rather than being built independently, ensuring consistency.
- **Output paths** use `rel_to_copy_path` to transform relative paths into the `{parent}/{stem}_{ext}/{filename}` format that matches the actual on-disk output structure.

**Return value structure:**

| Key | Type | Description |
|---|---|---|
| `"file"` | `str` | Copy-path of the file, prefixed with project name |
| `"callers"` | `list[str]` | Copy-paths of files that import this file |
| `"callees"` | `list[str]` | Copy-paths of files this file imports |

**Constraints & edge cases:**
- Files matching `EXCLUDE_PATTERNS` (via `fnmatch`) at either the directory or file level are silently skipped.
- Only files whose extension (without `.`) appears in `DEFINITION_DICTS.keys()` are included.
- `SAME_PACKAGE_VISIBLE` grouping is keyed on `(directory, extension)`, so files of different supported extensions in the same directory are treated as separate groups.
- Files that cannot be read during same-package scanning (e.g., permission errors, encoding errors) are skipped silently via `OSError`/`UnicodeDecodeError` handling.
- Import resolution relies on `resolve_module_to_project_path`, which only returns project-internal paths; standard library and third-party imports are always ignored in the graph.
- Callee paths that are not in `file_caller_map` (i.e., outside the collected file set) are excluded from the callers list in Step 4.

## Dependency Description

### Dependencies (modules this file imports)

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into AST root nodes, both when searching for definition nodes in `extract_callee_source` and when extracting import statements in `build_project_dependencies`.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to extract structured import information (`ImportInfo` objects) from a parsed AST, enabling resolution of inter-file dependencies in `build_project_dependencies`.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to map an import module string to a project-relative file path, and `get_import_params` to retrieve the tree-sitter `Language` object and import query string for a given file extension.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to convert project-relative file paths into the copy-destination path format (`{parent}/{stem}_{ext}/{filename}`) when constructing the final dependency graph output.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` (via `.keys()`) to determine the set of supported file extensions, `EXCLUDE_PATTERNS` to skip excluded directories and files during file traversal, and `SAME_PACKAGE_VISIBLE` to identify languages (Java/Kotlin) where same-package implicit dependencies must be detected.

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : Uses `build_project_dependencies` to construct the project-wide file dependency graph (callers/callees per file) as the first step of the analysis pipeline.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a dependency file, enabling cross-file usage analysis.

### Dependency Direction

All relationships are unidirectional:

- This module depends on `ts_parser.py`, `imports.py`, `import_to_path.py`, `file_utils.py`, and `settings.py`; none of those modules import from this file.
- `pipeline.py` and `usage_analysis.py` each depend on this module; this module does not import from either of them.

## Data Flow

## 1. Inputs

### `build_project_dependencies(project_dir: str)`
| Input | Format | Source |
|---|---|---|
| `project_dir` | Absolute directory path string | Caller argument |
| File system traversal | Directory/file names via `os.walk` | Project directory tree |
| `DEFINITION_DICTS` | `dict[str, dict]` | `codetwine/config/settings.py` |
| `EXCLUDE_PATTERNS` | `list[str]` | `codetwine/config/settings.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | `codetwine/config/settings.py` |
| AST root nodes | `Node` (tree-sitter) | `parse_file(absolute_path)` |
| Import info records | `list[ImportInfo]` | `extract_imports(root_node, language, query)` |
| Resolved module paths | `str | None` | `resolve_module_to_project_path(...)` |
| File source text | `str` | Direct file reads (for same-package analysis) |

### `extract_callee_source(callee_file_path, callee_name, project_dir)`
| Input | Format | Source |
|---|---|---|
| `callee_file_path` | Project-relative path string | Caller argument |
| `callee_name` | Dotted name string (e.g., `"helper.process"`) | Caller argument |
| `project_dir` | Absolute directory path string | Caller argument |
| AST root node | `Node` (tree-sitter) | `parse_file(absolute_path)` |

---

## 2. Transformation Overview

### `build_project_dependencies`

**Stage 1 — File discovery:**  
`os.walk` traverses `project_dir`, filtering out directory and file names matching `EXCLUDE_PATTERNS`. Files whose extensions appear in `DEFINITION_DICTS.keys()` are collected into `all_file_list` as absolute paths.

**Stage 2 — Project file set construction:**  
Each absolute path in `all_file_list` is converted to a project-relative POSIX path and inserted into `project_file_set` (a `set[str]`). This set acts as a membership lookup for later import resolution.

**Stage 3 — Import-based callee resolution:**  
For each file in `all_file_list`:
- `get_import_params` maps the file extension to a `(Language, import_query_str)` pair.
- `parse_file` produces the AST root node.
- `extract_imports` queries the AST and returns `list[ImportInfo]`, each holding a module string.
- `resolve_module_to_project_path` checks each module string against `project_file_set` and, when a match exists, returns the project-relative path, which is converted to an absolute path and added to the file's callee set.
- The result is stored in `file_callee_map` as `abs_path → set[abs_path]`.

**Stage 3.5 — Same-package implicit dependency injection:**  
Files whose extension appears in `SAME_PACKAGE_VISIBLE` are grouped by `(directory, extension)` into `dir_ext_groups`. Within each group, the raw text of every file is read and searched (using a word-boundary regex) for the class name (filename stem) of every other file in the same group. Matches cause the matched file's absolute path to be added to the reading file's callee set in `file_callee_map`.

**Stage 4 — Caller (reverse) index construction:**  
`file_caller_map` is initialized with one empty list per absolute file path. Each `(caller, callee)` pair from `file_callee_map` is inverted: the caller's absolute path is appended to `file_caller_map[callee]`.

**Stage 5 — Path formatting and output assembly:**  
For each file in `all_file_list`, all absolute paths (file, callers, callees) are converted to project-relative POSIX strings. Each relative path is then passed through `rel_to_copy_path` and prefixed with `project_name/` to produce the final `project_name/copy_path` format. The results are assembled into a list of dicts.

---

### `extract_callee_source`

**Stage 1 — Name splitting:**  
`callee_name` is split on `"."`. The search list is `[last_part]`; if multiple parts exist, `first_part` is appended as a fallback.

**Stage 2 — AST parse:**  
`parse_file` is called with the joined absolute path to obtain the cached or freshly parsed AST root node.

**Stage 3 — BFS definition search (via `_find_definition_node`):**  
A breadth-first traversal of the AST looks for nodes whose type is in `{"identifier", "type_identifier", "namespace_identifier"}` and whose text matches the search name. Nodes whose ancestors include import-related node types (detected by `_is_inside_import`) are skipped. The first non-import parent node found is returned.

**Stage 4 — Source extraction:**  
The matched parent node's `.text` bytes are decoded to UTF-8 and returned. If neither search name produces a match, `None` is returned.

---

## 3. Outputs

### `build_project_dependencies`
Returns `list[dict]` — one entry per supported file in the project.

### `extract_callee_source`
Returns `str | None` — the UTF-8 source text of the matched definition's parent AST node, or `None` if not found.

### Side effects
- `parse_file` populates the module-level `parse_cache` in `ts_parser.py` as a side effect of both functions.

---

## 4. Key Data Structures

### `file_callee_map`
| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` (absolute path) | The file doing the importing (caller) |
| Value | `set[str]` (absolute paths) | All project-internal files imported or referenced by the key file |

### `file_caller_map`
| Field / Key | Type | Purpose |
|---|---|---|
| Key | `str` (absolute path) | A file that is depended upon (callee) |
| Value | `list[str]` (absolute paths) | All files that import or reference the key file |

### `dir_ext_groups`
| Field / Key | Type | Purpose |
|---|---|---|
| Key | `tuple[str, str]` — `(directory, extension)` | Groups files sharing the same directory and language extension |
| Value | `list[str]` (absolute paths) | Files in that directory/extension group |

### `project_file_set`
| Field / Key | Type | Purpose |
|---|---|---|
| Elements | `str` (project-relative POSIX paths) | Membership lookup: determines whether a resolved import corresponds to a file within the project |

### Output dict (one entry in returned `list[dict]`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | `project_name/copy_path` of this file |
| `"callers"` | `list[str]` | `project_name/copy_path` strings for all files that depend on this file |
| `"callees"` | `list[str]` | `project_name/copy_path` strings for all files this file depends on |

### `search_names` (inside `extract_callee_source`)
| Index | Type | Purpose |
|---|---|---|
| `0` | `str` | Trailing part of dotted name — primary search target (e.g., `"process"` from `"helper.process"`) |
| `1` (optional) | `str` | Leading part of dotted name — fallback search target (e.g., `"helper"` from `"helper.process"`) |

## Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** strategy throughout. Individual file failures during dependency graph construction are isolated so that the overall analysis proceeds without termination. In the definition-extraction path (`extract_callee_source`), the design falls back through multiple candidate search names before returning `None`. Errors that cannot be meaningfully recovered from (e.g., unresolvable imports, missing definitions) silently yield `None` or are simply omitted from results, keeping the caller unaffected.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Reading a source file for same-package class-name pattern matching fails | Caught explicitly; the file is skipped with `continue` | Yes | That file is excluded from same-package dependency detection only; all other files proceed normally |
| Definition not found (no match) | `_find_definition_node` traverses the entire AST and finds no identifier matching `callee_name` | Returns `None`; `extract_callee_source` falls back to an alternative search name (leading part of dotted name), then returns `None` if still unresolved | Yes | The callee source is absent from the result; caller receives `None` |
| Unresolvable import | `resolve_module_to_project_path` returns `None` for a module name (standard library, external package, or unknown path) | The resolved value is checked with `if resolved:`; non-project imports are silently skipped | Yes | That import is not added as a callee edge; dependency graph is incomplete only for that import |
| Unsupported file extension | `get_import_params` returns `(None, None)` for a file whose extension has no import query | Guarded by `if language and import_query_str:`; import analysis is skipped entirely for that file | Yes | No callee edges are built for that file; it still appears as a node in the graph |
| Callee path not in `file_caller_map` | A resolved callee absolute path does not correspond to any collected project file | Guarded by `if callee_path in file_caller_map:`; the reverse edge is not added | Yes | The caller-side back-reference for that callee is absent; the callee node itself is unaffected |
| Node inside import statement | An AST identifier matching the target name is found but resides within an import statement | `_is_inside_import` returns `True`; the node is skipped and BFS continues | Yes | The import-site reference is not mistaken for a definition; BFS continues searching |

---

## 3. Design Notes

- **Isolation by file**: The single explicit `try/except` block in the same-package analysis loop is scoped tightly to one file read. This prevents a single unreadable or non-UTF-8 file from aborting the entire group or project scan.
- **Silent omission over exception propagation**: Unresolvable imports and missing definitions do not raise exceptions; they produce `None` or are skipped. This reflects an intentional trade-off: a partial dependency graph is more useful to callers than a hard failure.
- **Fallback search in definition extraction**: The two-candidate search strategy in `extract_callee_source` (trailing part first, then leading part of a dotted name) is a semantic fallback rather than an error handler—it compensates for ambiguity in attribute-access expressions without raising errors.
- **No logging at call sites**: Neither `logger.warning` nor `logger.error` is invoked for the handled failure cases in this file; failures are absorbed silently. The module-level `logger` is defined but unused in the current implementation, indicating that the current policy prioritizes non-interrupting behavior over diagnostic verbosity.

## Summary

Builds the project-wide inter-file dependency graph and extracts definition source from dependency files.

**Public functions:**
- `build_project_dependencies(project_dir: str) -> list[dict]` — returns `{"file", "callers", "callees"}` dicts with `project_name/copy_path` formatted paths
- `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) -> str | None` — returns source text of a named definition's parent AST node

**Key structures:** `file_callee_map` (`dict[str, set[str]]`), `file_caller_map` (`dict[str, list[str]]`), `project_file_set` (`set[str]` of relative POSIX paths)
