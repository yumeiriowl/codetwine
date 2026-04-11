# Design Document: codetwine/extractors/dependency_graph.py

# Overview & Purpose

## 1. Module Summary

Analyzes all source files within a project directory to build an inter-file dependency graph, and retrieves definition source code for named symbols from dependency target files.

## 2. When to Use This Module

- **Building a project-wide dependency graph**: Call `build_project_dependencies(project_dir)` to obtain a list of dicts describing each file's callers and callees across the entire project. This is the entry point used by `codetwine/pipeline.py` to initialize the dependency graph before further analysis.
- **Retrieving a symbol's definition source from a dependency file**: Call `extract_callee_source(callee_file_path, callee_name, project_dir)` to get the source code string of a named function, class, variable, or other definition from a specific file. This is used by `codetwine/extractors/usage_analysis.py` when resolving callee definitions during usage analysis.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parses the specified file's AST and returns the full source text of the parent node containing the definition matching `callee_name`. Tries the trailing part of a dotted name first, then falls back to the leading part. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walks the project directory, resolves inter-file imports for all supported language files, adds implicit same-package dependencies for Java/Kotlin, and returns a list of dicts with `"file"`, `"callers"`, and `"callees"` keys in `project_name/copy_path` format. |

## 4. Design Decisions

- **Same-package implicit dependency detection**: For languages where `SAME_PACKAGE_VISIBLE` is enabled (e.g. Java, Kotlin), files in the same directory are added as implicit callees when the class name (derived from the filename stem) appears as a word boundary match in the source text. This compensates for the absence of explicit import statements between same-package classes.
- **Dotted name fallback in `extract_callee_source`**: When `callee_name` contains a dot (e.g. `"helper.process"`), the function first searches by the trailing component (`"process"`), then by the leading component (`"helper"`). This handles both attribute access on imported objects and method calls on module-level constants.
- **BFS-based AST definition search**: `_find_definition_node` uses breadth-first search over the AST and skips nodes that appear inside import statements, ensuring only actual definition sites are matched rather than import references.
- **Path format for output**: All paths in the returned dependency list use the `project_name/copy_path` format produced by `rel_to_copy_path`, matching the physical output directory structure used by the rest of the pipeline.

# Definition Design Specifications

---

## Module-level Constants

### `_DEFINITION_NAME_NODE_TYPES`
**Type:** `set[str]`

The set of tree-sitter node type strings that represent definition name positions in an AST. Contains `"identifier"`, `"type_identifier"`, and `"namespace_identifier"`.

- **Responsibility:** Centralizes the node types used to locate named definitions so that `_find_definition_node` does not embed raw strings inline.
- **Design decisions:** Kept as a module-level set for O(1) membership tests during BFS traversal.

---

## Functions

### `_is_inside_import`

**Signature:**
```python
def _is_inside_import(node) -> bool
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `node` | tree-sitter `Node` | The AST node to check for import ancestry |

**Return type:** `bool` — `True` if any ancestor node is an import or preprocessor include statement.

- **Responsibility:** Guards `_find_definition_node` from treating imported names as locally defined symbols by detecting import-related ancestors.
- **When to use:** Called internally by `_find_definition_node` before accepting a candidate identifier node as a definition site.
- **Design decisions:** Walks the parent chain using `node.parent` rather than querying by position, which works for any supported language without language-specific logic. The detection condition checks whether `"import"` is a substring of the node type string or whether the type is `"preproc_include"`, covering Python, Java, Kotlin, JavaScript/TypeScript, and C/C++ in a single predicate.
- **Constraints & edge cases:** Relies on tree-sitter's `Node.parent` being populated; behavior is undefined if a detached node is passed.

---

### `_find_definition_node`

**Signature:**
```python
def _find_definition_node(root_node, definition_name: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `root_node` | tree-sitter `Node` | AST root node for the entire file |
| `definition_name` | `str` | The bare name to find (e.g. `"parse_file"`, `"Point"`) |

**Return type:** tree-sitter `Node` (the parent of the matched name node), or `None` if not found.

- **Responsibility:** Locates the AST node that contains a named definition within a file, enabling source extraction by the caller.
- **When to use:** Called by `extract_callee_source` to find where a specific definition lives in a parsed file's AST.
- **Design decisions:**
  - Uses BFS (via `deque`) rather than DFS so that top-level definitions are encountered before deeply nested ones, preferring the most prominent match.
  - The BFS queue stores `(node, parent)` pairs so that the enclosing definition node (parent) can be returned directly without an additional lookup.
  - Nodes inside import statements are filtered via `_is_inside_import` to avoid returning import-site references.
  - Only node types in `_DEFINITION_NAME_NODE_TYPES` are tested against `definition_name`, limiting false positives from non-name nodes.
- **Constraints & edge cases:** Returns the first BFS match; if the same name is defined multiple times, only the first encountered node is returned. Returns `None` when the name does not exist in the file or is only referenced in imports.

---

### `extract_callee_source`

**Signature:**
```python
def extract_callee_source(
    callee_file_path: str,
    callee_name: str,
    project_dir: str,
) -> str | None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `callee_file_path` | `str` | Path to the dependency file, relative to the project root (e.g. `"src/foo.py"`) |
| `callee_name` | `str` | Name of the definition to retrieve; may be dotted (e.g. `"helper.process"`, `"TEMPLATE.format"`) |
| `project_dir` | `str` | Absolute path to the project root directory |

**Return type:** `str | None` — The UTF-8 source text of the matched definition node, or `None` if no match is found.

- **Responsibility:** Retrieves the full source code of a named definition from a dependency file for use in usage analysis.
- **When to use:** Invoked by `codetwine/extractors/usage_analysis.py` when it needs to inline or display the definition of a called symbol found in a project file.
- **Design decisions:**
  - For dotted names, two search attempts are made in order: the trailing component (handles attribute access like `helper.process` → `process`), then the leading component (handles built-in method calls like `TEMPLATE.format` → `TEMPLATE`). This two-pass strategy avoids requiring callers to pre-process the name.
  - Delegates parsing to `parse_file`, which is module-level cached in `ts_parser.py`, so repeated calls for the same file do not incur re-parse overhead.
  - Constructs the absolute path from `project_dir` + `callee_file_path` before calling `parse_file`.
- **Constraints & edge cases:** Returns `None` if the name is not found after both search attempts. Non-dotted names result in a single search with the name itself. Behavior for names with more than one dot component is determined by `parts[-1]` and `parts[0]` only; intermediate components are ignored.

---

### `build_project_dependencies`

**Signature:**
```python
def build_project_dependencies(project_dir: str) -> list[dict]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `project_dir` | `str` | Absolute path to the root directory of the project to analyze |

**Return type:** `list[dict]` — A list of dictionaries, where each dictionary describes one file's dependency relationships. Each `dict` has the following structure:

| Key | Value type | Description |
|-----|-----------|-------------|
| `"file"` | `str` | Copy-path of the file in `"{project_name}/{copy_path}"` format |
| `"callers"` | `list[str]` | Copy-paths of files that import this file |
| `"callees"` | `list[str]` | Copy-paths of files that this file imports |

- **Responsibility:** Builds a complete, project-wide inter-file dependency graph by analyzing import statements and same-package class references, forming the foundation for downstream dependency-aware processing.
- **When to use:** Called once at pipeline startup (in `codetwine/pipeline.py`) to produce the dependency graph before per-file processing begins.
- **Design decisions:**
  - **File discovery** uses `os.walk` with in-place modification of `dir_names` to prune directories matching `EXCLUDE_PATTERNS`, which prevents descent into excluded subtrees entirely.
  - **Explicit import resolution** is delegated to `extract_imports` + `resolve_module_to_project_path`, which handle language-specific import syntax and path resolution uniformly across all supported languages.
  - **Same-package implicit dependencies** (Java/Kotlin): for languages where `SAME_PACKAGE_VISIBLE` is true, files in the same directory and extension group are checked for class-name references via regex, and unidirectional edges are added when found. This handles the Java/Kotlin convention of referencing same-package classes without imports.
  - **Caller index** is built as a reverse lookup from the callee map rather than being computed independently, ensuring consistency.
  - **Path format** in the output uses `rel_to_copy_path` to produce paths that match the actual output directory structure, making paths valid across environments.
  - Directory exclusion patterns and same-package visibility rules are read from `EXCLUDE_PATTERNS` and `SAME_PACKAGE_VISIBLE` in `settings.py`, keeping policy separate from logic.
- **Constraints & edge cases:**
  - Only files whose extensions appear in `DEFINITION_DICTS.keys()` are collected; unsupported file types are silently ignored.
  - Files whose language has no import query (i.e., `get_import_params` returns `(None, None)`) produce an empty callee set from explicit imports but may still gain edges from the same-package check.
  - Same-package implicit dependency detection reads source files as UTF-8 text; files that fail to open or decode are silently skipped.
  - Callee paths that do not correspond to a known project file are excluded from the caller index (no entry is created for them in `file_caller_map`).
  - The returned paths use forward slashes regardless of the host operating system.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into tree-sitter AST root nodes, both for import extraction during graph construction and for definition lookup in `extract_callee_source`.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to parse import statements from an AST root node and obtain a list of `ImportInfo` records, which are then resolved to project-internal file paths.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to determine whether an import module string resolves to a file within the project, and `get_import_params` to retrieve the tree-sitter `Language` object and query string appropriate for a given file extension.

- `codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to convert project-relative file paths into the copy-destination path format (`project_name/{parent}/{stem}_{ext}/{filename}`) used in the output dependency records.

- `codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` to determine the set of supported file extensions for file collection, `EXCLUDE_PATTERNS` to filter out directories and files during filesystem traversal, and `SAME_PACKAGE_VISIBLE` to identify language extensions (Java/Kotlin) where same-package implicit dependencies must be detected.

---

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` : Uses `build_project_dependencies` to construct the project-wide inter-file dependency graph as the first step of the pipeline, producing the list of file dependency records that drives all subsequent processing.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a dependency target file, enabling usage analysis to inline the relevant callee source when examining cross-file symbol references.

---

### Dependency Direction

All relationships are **unidirectional**:

- `dependency_graph.py` → `ts_parser.py`: one-way; `ts_parser.py` has no knowledge of this module.
- `dependency_graph.py` → `imports.py`: one-way; `imports.py` has no knowledge of this module.
- `dependency_graph.py` → `import_to_path.py`: one-way; `import_to_path.py` has no knowledge of this module.
- `dependency_graph.py` → `file_utils.py`: one-way; `file_utils.py` has no knowledge of this module.
- `dependency_graph.py` → `settings.py`: one-way; `settings.py` has no knowledge of this module.
- `pipeline.py` → `dependency_graph.py`: one-way; `dependency_graph.py` does not import `pipeline.py`.
- `usage_analysis.py` → `dependency_graph.py`: one-way; `dependency_graph.py` does not import `usage_analysis.py`.

# Data Flow

## 1. Inputs

### `build_project_dependencies(project_dir: str)`
- **`project_dir`**: Absolute path string to the project root directory. Used as the traversal root for file discovery.
- **Config values**: `DEFINITION_DICTS` (keys determine supported file extensions), `EXCLUDE_PATTERNS` (glob patterns for directory/file exclusion), `SAME_PACKAGE_VISIBLE` (per-extension flag controlling implicit same-package dependency detection).
- **File system**: All files under `project_dir` are discovered via `os.walk`. Source file contents are read (as text) for same-package class-name reference scanning.
- **Parsed ASTs**: `parse_file(absolute_path)` returns `(root_node, bytes)` for each source file; `root_node` is passed to `extract_imports`.
- **Import analysis results**: `extract_imports(root_node, language, import_query_str)` returns `list[ImportInfo]`; each `ImportInfo.module` string is passed to `resolve_module_to_project_path`.

### `extract_callee_source(callee_file_path, callee_name, project_dir)`
- **`callee_file_path`**: Project-relative path string to the dependency target file.
- **`callee_name`**: Name string of the definition to locate (may contain dots, e.g. `"helper.process"`).
- **`project_dir`**: Absolute path to the project root, used to construct the absolute path for parsing.
- **Parsed AST**: `parse_file(absolute_path)` returns `(root_node, bytes)`; `root_node` is the search target.

---

## 2. Transformation Overview

### `build_project_dependencies`

**Stage 1 — File discovery**  
`os.walk` traverses `project_dir`, pruning directories matching `EXCLUDE_PATTERNS`. Files whose extensions (stripped of `.`) appear in `DEFINITION_DICTS.keys()` are collected into `all_file_list: list[str]` of absolute paths.

**Stage 2 — Project file set construction**  
Each absolute path in `all_file_list` is converted to a `project_dir`-relative POSIX string and inserted into `project_file_set: set[str]`. This set is later used as a membership lookup during import resolution.

**Stage 3 — Explicit callee extraction (import-based)**  
For each file in `all_file_list`, `get_import_params` resolves the file extension to a `(Language, import_query_str)` pair. If both are non-None, the file is parsed into an AST, `extract_imports` extracts `list[ImportInfo]`, and `resolve_module_to_project_path` maps each `ImportInfo.module` against `project_file_set`. Resolved absolute paths accumulate into a `callee_set: set[str]` per file, stored in `file_callee_map: dict[str, set[str]]`.

**Stage 4 — Implicit callee injection (same-package, Java/Kotlin)**  
Files whose extensions are listed in `SAME_PACKAGE_VISIBLE` are grouped by `(directory, extension)` into `dir_ext_groups`. For each group, the class name (filename stem) of every peer file is matched as a word-boundary regex against the source text of every other file in the group. Any match causes the peer's absolute path to be added into the caller's entry in `file_callee_map`.

**Stage 5 — Caller index construction (reverse map)**  
`file_caller_map: dict[str, list[str]]` is initialized with an empty list for every file. The `file_callee_map` is iterated: for each `(caller, callee_set)` pair, the caller's absolute path is appended to `file_caller_map[callee]` for each callee that exists in the map.

**Stage 6 — Path conversion and output assembly**  
Each absolute path is converted to a `project_dir`-relative POSIX string, then transformed via `rel_to_copy_path` into the `{parent_dir}/{stem}_{ext}/{filename}` copy-destination format and prefixed with `project_name/`. The final `file_info_list: list[dict]` is assembled with `"file"`, `"callers"`, and `"callees"` keys.

---

### `extract_callee_source`

**Stage 1 — Path construction and parsing**  
`callee_file_path` is joined with `project_dir` to form an absolute path. `parse_file` returns the cached or freshly parsed `(root_node, bytes)`.

**Stage 2 — Search name resolution**  
`callee_name` is split on `.`. The trailing part (e.g. `"process"` from `"helper.process"`) is tried first; the leading part (e.g. `"helper"`) is appended as a fallback candidate.

**Stage 3 — AST BFS definition search**  
`_find_definition_node` performs a breadth-first search over the AST. At each node, if the node type is one of `{"identifier", "type_identifier", "namespace_identifier"}` and its decoded text matches the search name, and the node is not inside an import statement (checked by `_is_inside_import`), the node's **parent** is returned.

**Stage 4 — Source extraction**  
The returned parent node's `.text` bytes are decoded to a UTF-8 string and returned. Returns `None` if no candidate search name yields a match.

---

## 3. Outputs

### `build_project_dependencies`
- **Return value**: `list[dict]` — one entry per supported source file discovered under `project_dir`. Each dict contains `"file"`, `"callers"`, and `"callees"` as copy-destination-format path strings. No files are written; no side effects beyond in-process AST cache population via `parse_file`.

### `extract_callee_source`
- **Return value**: `str | None` — the UTF-8 decoded source text of the matched AST parent node (a function definition, class definition, assignment, etc.), or `None` if no definition is found.

---

## 4. Key Data Structures

### `file_info_list` entry (output of `build_project_dependencies`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Copy-destination path of this file, prefixed with `project_name/` |
| `"callers"` | `list[str]` | Copy-destination paths of files that import this file |
| `"callees"` | `list[str]` | Copy-destination paths of files that this file imports |

### `file_callee_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | The importing (caller) file |
| value | `set[str]` (absolute paths) | All files imported by the caller, both explicit (import-based) and implicit (same-package) |

### `file_caller_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | A file that may be imported by others |
| value | `list[str]` (absolute paths) | All files that import this file |

### `project_file_set`
| Field / Key | Type | Purpose |
|---|---|---|
| elements | `str` (POSIX relative paths) | Project-relative paths of all discovered source files; used for membership checks during import resolution |

### `dir_ext_groups`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `tuple[str, str]` — `(directory, extension)` | Groups files sharing the same directory and extension |
| value | `list[str]` (absolute paths) | All files in that directory+extension group |

### `class_patterns` (within same-package stage)
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | Source file path |
| value | `re.Pattern[str]` | Word-boundary regex for the file's class name (filename stem) |

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** strategy. Failures in individual file operations are silently absorbed so that the broader dependency graph construction can continue. Critical path operations (AST parsing, graph traversal) do not employ explicit exception handling and are permitted to propagate exceptions to the caller. The result is a two-tier approach: infrastructure-level I/O failures are caught and skipped, while logic-level failures (missing definitions, unresolvable imports) are handled by returning `None` or an empty collection rather than raising exceptions.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Opening or reading a source file during same-package visibility analysis (Step 3.5) | `except (OSError, UnicodeDecodeError): continue` — the file is skipped entirely | Yes | The affected file contributes no same-package implicit dependencies; all other files proceed normally |
| Definition not found | `_find_definition_node` traverses the full AST and finds no identifier matching `definition_name` | Returns `None` from `_find_definition_node`; `extract_callee_source` tries fallback search names before returning `None` | Yes | The caller receives `None`; no source snippet is returned for that callee name |
| Multi-part callee name with wrong trailing part | `callee_name` like `"TEMPLATE.format"` where the trailing part is a built-in method, not a project definition | `extract_callee_source` retries the search using the leading part (`parts[0]`) as a fallback | Yes | If the leading part resolves to a definition, source is returned; otherwise `None` |
| Unresolvable import | `resolve_module_to_project_path` returns `None` (module is a stdlib or external package) | The resolved value is checked with `if resolved:` and simply not added to `callee_set` | Yes | That import is excluded from the dependency graph; no error is raised |
| Unsupported file extension for import analysis | `get_import_params` returns `(None, None)` for an extension with no import query configured | Guarded by `if language and import_query_str:` — the import analysis block is skipped | Yes | The file has an empty callee set; it still appears in the graph |
| Callee path not tracked in `file_caller_map` | A resolved callee path does not exist as a key in `file_caller_map` (e.g. resolved outside the walked tree) | `if callee_path in file_caller_map:` guard prevents the append | Yes | The reverse-caller link is silently omitted for that dependency |
| AST parse failure (propagated) | `parse_file` encounters an unreadable or malformed file | No `try/except` wraps `parse_file` calls; exceptions propagate to the caller | No | The entire `build_project_dependencies` or `extract_callee_source` call fails |

---

## 3. Design Notes

- **Selective containment**: Error handling is applied only where the data source is external and inherently unreliable (filesystem I/O of arbitrary project files). Logic that operates on already-parsed, in-memory data (AST traversal, graph construction) is not wrapped, reflecting an implicit assumption that well-formed inputs produce no exceptions.
- **Silent skipping over logging**: The same-package file read errors are caught and skipped with no log emission at the catch site, meaning failures in that phase are invisible at runtime. No `logger.warning` or similar call accompanies the `continue` statement.
- **`None`-as-sentinel convention**: Both `_find_definition_node` and `extract_callee_source` use `None` as a typed absence signal rather than raising an exception, delegating the decision of how to handle a missing definition entirely to the caller.
- **Graph completeness vs. correctness trade-off**: By continuing past per-file errors, the function guarantees a complete graph structure (all discovered files are represented as nodes) at the cost of potentially missing some edges, favoring a partial result over a hard failure.

# Summary

**dependency_graph.py**: Builds a project-wide inter-file dependency graph and retrieves symbol definitions from dependency files.

**Public functions:**
- `build_project_dependencies(project_dir: str) → list[dict]`: returns dicts with `"file"`, `"callers"`, `"callees"` keys (copy-path format)
- `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) → str | None`: returns source text of a named definition via BFS AST search

**Key structures:** `file_callee_map: dict[str, set[str]]`, `file_caller_map: dict[str, list[str]]`, `project_file_set: set[str]`; supports dotted callee names and implicit same-package dependencies (Java/Kotlin).
