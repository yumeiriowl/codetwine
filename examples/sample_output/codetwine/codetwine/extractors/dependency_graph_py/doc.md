# Design Document: codetwine/extractors/dependency_graph.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyze inter-file import dependencies across an entire project and extract definition source code from dependency target files, providing the foundational dependency graph used by downstream pipeline stages.

## 2. When to Use This Module

- **Building the project-wide dependency graph**: Call `build_project_dependencies(project_dir)` to walk all supported-language files under `project_dir`, resolve their import statements to project-internal files, and receive a list of dicts mapping each file to its callers and callees in `"project_name/copy_path"` format. This is the entry point used by `pipeline.py` to initialize the dependency graph before per-file analysis.

- **Retrieving a definition's source code from a dependency file**: Call `extract_callee_source(callee_file_path, callee_name, project_dir)` to parse a specific file and return the source text of the function, class, variable, or other definition matching `callee_name`. This is used by `usage_analysis.py` when resolving what a referenced symbol actually contains.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parse a dependency file and return the source text of the definition matching `callee_name`; tries the trailing then leading component of dotted names; returns `None` if not found. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walk all supported-language files under `project_dir`, resolve imports to project-internal paths, account for same-package implicit references (Java/Kotlin), build caller/callee maps, and return a list of `{"file", "callers", "callees"}` dicts with paths in `"project_name/copy_path"` format. |

## 4. Design Decisions

- **BFS-based definition lookup**: `_find_definition_node` searches the AST breadth-first rather than depth-first, ensuring the shallowest (most top-level) matching definition is returned first. Nodes inside import statements are explicitly skipped to avoid confusing import references with definitions.

- **Dotted-name fallback for `extract_callee_source`**: For a name like `helper.process`, the function first searches for `process` (the trailing component); if that fails, it retries with `helper` (the leading component). This handles both attribute access on project objects and built-in method calls on project-defined constants (e.g., `TEMPLATE.format`).

- **Same-package implicit dependency detection**: For languages where `SAME_PACKAGE_VISIBLE` is set (Java/Kotlin), files in the same directory and extension group are checked with regex word-boundary matching to detect references that occur without any import statement, and are added as unidirectional callees.

- **Reuse of `parse_file` cache**: All AST parsing goes through `parse_file`, which maintains a module-level cache keyed by absolute path. This avoids re-parsing files that are visited multiple times during dependency graph construction and callee source extraction.

- **`project_file_set` as an O(1) membership filter**: A set of project-relative paths is built once before import resolution, allowing `resolve_module_to_project_path` to quickly discard standard-library and third-party imports without filesystem probing.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constant

### `_DEFINITION_NAME_NODE_TYPES`

| Type | Value |
|------|-------|
| `set[str]` | `{"identifier", "type_identifier", "namespace_identifier"}` |

**Responsibility:** Acts as a filter set for the AST node types that can represent a definition's name. Centralizes the multi-language name-node taxonomy so that `_find_definition_node` does not hard-code the set inline.

---

## Functions

---

### `_is_inside_import`

**Signature:**
```python
def _is_inside_import(node) -> bool
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `node` | tree-sitter `Node` | The AST node to test |

**Returns:** `True` if any ancestor of `node` is an import/include statement node.

**Responsibility:** Guards against treating import-statement references as definitions by walking the parent chain and checking for import-related node type names.

**When to use:** Called internally by `_find_definition_node` before accepting a candidate name node as a definition site.

**Design decisions:**
- Uses `node.parent` traversal rather than re-querying the tree, making it O(depth) with no additional AST queries.
- The check is purely string-based: any node type containing the substring `"import"` matches, plus the literal string `"preproc_include"` for C/C++ `#include` directives.

**Constraints & edge cases:**
- Relies on tree-sitter's `parent` pointer being populated; behaviour is undefined if `node` is the root.

---

### `_find_definition_node`

**Signature:**
```python
def _find_definition_node(root_node, definition_name: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `root_node` | tree-sitter `Node` | Root of the AST to search |
| `definition_name` | `str` | Exact name of the symbol to locate |

**Returns:** The *parent* of the first matching name node (i.e., the enclosing definition node such as `function_definition` or `class_definition`), or `None` if not found.

**Responsibility:** Provides a language-agnostic BFS-based symbol lookup across the AST for all node types that can carry a definition name.

**When to use:** Called by `extract_callee_source` to locate the AST subtree whose source text constitutes the definition of a requested symbol.

**Design decisions:**
- BFS is chosen over DFS so that top-level definitions are encountered before nested ones, returning the outermost enclosing definition when multiple matches exist at different depths.
- The queue stores `(node, parent)` tuples so that the parent is immediately available when a match is found, without an additional upward traversal.
- Import nodes are excluded via `_is_inside_import` so that `from foo import Bar` does not produce a false match on `Bar`.

**Constraints & edge cases:**
- Returns the *first* BFS match; if the same name is used both at top-level and inside a nested scope, the outer one wins.
- Does not distinguish between a variable named `Foo` and a class named `Foo`; both would match.

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
| `callee_file_path` | `str` | Path to the dependency file, relative to the project root |
| `callee_name` | `str` | Symbol name to look up; may contain dot-access (e.g. `"helper.process"`, `"TEMPLATE.format"`) |
| `project_dir` | `str` | Absolute path to the project root |

**Returns:** The full source text of the enclosing definition node as a UTF-8 string, or `None` if the symbol is not found.

**Responsibility:** Retrieves the actual source code of a called symbol from its defining file, enabling downstream callers to surface dependency implementations.

**When to use:** Invoked by `usage_analysis.py` when a usage site references a symbol whose definition file has been resolved and the source text of that definition is needed.

**Design decisions:**
- Dot-notation names are split and searched in a two-attempt strategy: trailing part first (covers `helper.process` → look for `process`), then leading part as a fallback (covers `TEMPLATE.format` → look for `TEMPLATE`).
- Delegates parsing to `parse_file`, which maintains a module-level cache, so repeated calls for the same file incur no additional I/O.

**Constraints & edge cases:**
- `callee_file_path` must be relative to `project_dir`; an absolute path will produce an incorrect `os.path.join` result.
- Only the first match is returned; overloaded or shadowed names return the BFS-outermost occurrence.
- Returns `None` for built-in methods or standard library names that have no definition node inside the file.

---

### `build_project_dependencies`

**Signature:**
```python
def build_project_dependencies(project_dir: str) -> list[dict]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `project_dir` | `str` | Absolute path to the project root directory |

**Returns:** A list of dictionaries, each representing one source file and its dependency relationships. Each dict has the following shape:

| Key | Type | Description |
|-----|------|-------------|
| `"file"` | `str` | Copy-path for this file (`"<project_name>/<copy_path>"`) |
| `"callers"` | `list[str]` | Copy-paths of files that import this file |
| `"callees"` | `list[str]` | Copy-paths of files imported by this file |

**Responsibility:** Constructs the complete inter-file dependency graph for a project by combining explicit import resolution with implicit same-package visibility rules.

**When to use:** Called once at the start of the analysis pipeline (in `pipeline.py`) to produce the project-wide dependency map consumed by all subsequent processing steps.

**Design decisions:**

- **File discovery filtering:** Directories and files matching `EXCLUDE_PATTERNS` are pruned during `os.walk` traversal. Directory exclusion is applied in-place so entire subtrees are skipped.
- **Supported files only:** Only files whose extension appears in `DEFINITION_DICTS.keys()` are included, ensuring unsupported file types are silently ignored.
- **Implicit same-package dependencies (Java/Kotlin):** For languages where `SAME_PACKAGE_VISIBLE` is `True` for their extension, files within the same directory and extension group are checked for textual references to each other's class names (filename stem) using word-boundary regex matching. Matched pairs receive a unidirectional callee edge without requiring an explicit import statement.
- **Caller map construction:** The caller index is derived by inverting the callee map, avoiding any separate traversal.
- **Path format:** All output paths use the `"<project_name>/<copy_path>"` format produced by `rel_to_copy_path`, matching the physical output directory layout so paths remain valid across environments.

**Constraints & edge cases:**
- `project_dir` is expected to be an absolute path; relative paths may produce inconsistent `os.path.relpath` results.
- The same-package visibility check is purely textual (regex) and may produce false positives if a class name appears in a comment or string literal.
- Files that fail to parse (e.g., syntax errors) will silently produce empty callee sets for that file, not a raised exception.
- Callee paths that do not correspond to any discovered project file are excluded from the caller index but remain in the callee list.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : uses `parse_file(file_path: str) -> tuple[Node, bytes]` to parse source files into tree-sitter ASTs for both callee definition extraction (`extract_callee_source`) and import analysis within `build_project_dependencies`.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/extractors/imports.py` : uses `extract_imports(root_node, language, import_query_str) -> list[ImportInfo]` to extract structured import information from parsed ASTs, enabling resolution of inter-file dependencies.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/import_to_path.py` : uses `resolve_module_to_project_path(module, current_file_rel, project_file_set) -> str|None` to resolve import module names to project-internal file paths, and `get_import_params(file_ext) -> tuple[Language, str]|tuple[None, None]` to obtain the tree-sitter Language object and query string required for import analysis per file extension.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/utils/file_utils.py` : uses `rel_to_copy_path(rel_path: str) -> str` to convert project-relative file paths into the copy-destination path format (`{parent_dir}/{stem}_{ext}/{filename}`) when constructing the final dependency graph output.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/config/settings.py` : uses `DEFINITION_DICTS` to determine the set of supported file extensions for project file collection, `EXCLUDE_PATTERNS` to skip directories and files matching exclusion rules during filesystem traversal, and `SAME_PACKAGE_VISIBLE` to identify language extensions (Java/Kotlin) that require same-package implicit dependency detection.

---

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : uses `build_project_dependencies(project_dir: str) -> list[dict]` as the first step of the analysis pipeline to construct the project-wide inter-file dependency graph, from which the full list of project files is also derived.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : uses `extract_callee_source(callee_file_path, callee_name, project_dir) -> str|None` to retrieve the source code of a specific definition from a dependency file during usage analysis, enabling inline inclusion of callee definitions.

---

### Dependency Direction

All relationships are **unidirectional**:

- This module depends on `ts_parser.py`, `imports.py`, `import_to_path.py`, `file_utils.py`, and `settings.py` — none of those modules import from this file.
- `pipeline.py` and `usage_analysis.py` depend on this module — this module does not import from either of them.

## Data Flow

# Data Flow

## 1. Inputs

### `build_project_dependencies(project_dir: str)`
- **`project_dir`**: Absolute path to the project root directory (string).
- **`DEFINITION_DICTS`** (from `settings.py`): Dict whose keys are supported file extensions (e.g., `"py"`, `"java"`), used to determine which files to collect.
- **`EXCLUDE_PATTERNS`** (from `settings.py`): List of glob patterns identifying directories and files to skip during traversal.
- **`SAME_PACKAGE_VISIBLE`** (from `settings.py`): Dict mapping file extensions to a boolean, indicating whether same-directory files are implicitly visible (Java/Kotlin).
- **File system**: Directory tree rooted at `project_dir`, read via `os.walk`.
- **Source file contents**: Raw UTF-8 text, read for same-package reference detection.
- **AST data**: Tree-sitter parse results (`root_node`) returned by `parse_file`, used to extract import statements.

### `extract_callee_source(callee_file_path, callee_name, project_dir)`
- **`callee_file_path`**: Project-relative path to the file containing the target definition (string).
- **`callee_name`**: The name of the definition to retrieve, possibly dot-qualified (e.g., `"helper.process"`, `"TEMPLATE.format"`).
- **`project_dir`**: Absolute path to the project root (string).
- **AST data**: Tree-sitter parse result (`root_node`) of the target file, returned by `parse_file`.

---

## 2. Transformation Overview

### `build_project_dependencies`

**Stage 1 — File collection**
`os.walk` traverses the project directory tree. Directories matching `EXCLUDE_PATTERNS` are pruned in-place. Files are retained only if their extension (without the leading dot) is a key in `DEFINITION_DICTS` and their filename does not match any `EXCLUDE_PATTERNS`. Result: `all_file_list` (list of absolute file paths).

**Stage 2 — Project file set construction**
Each path in `all_file_list` is converted to a project-relative, forward-slash-normalized string and added to `project_file_set`. This set is the lookup table used during import resolution to distinguish project-internal modules from external dependencies.

**Stage 3 — Import-based callee resolution**
For each file in `all_file_list`, `get_import_params` maps the file extension to a `(Language, import_query_str)` pair. If the pair is valid, `parse_file` produces the AST, and `extract_imports` returns a list of `ImportInfo` objects. Each `ImportInfo.module` is passed to `resolve_module_to_project_path` together with the current file's relative path and `project_file_set`. Resolved paths (project-internal only) are converted to absolute paths and accumulated into `callee_set`. The mapping `abs_file_path → callee_set` is stored in `file_callee_map`.

**Stage 3.5 — Same-package implicit dependency injection (Java/Kotlin)**
Files whose extension appears in `SAME_PACKAGE_VISIBLE` are grouped by `(directory, extension)`. For each group, the class name (filename stem) of every member is compiled into a word-boundary regex pattern. Each file's source text is read and scanned against every other member's pattern. When a match is found, that other file's absolute path is added to the caller's callee set in `file_callee_map`.

**Stage 4 — Reverse index (caller map) construction**
`file_caller_map` is initialized as `abs_path → []` for every file in `all_file_list`. The callee sets from `file_callee_map` are iterated in reverse: for each `(caller, callee)` pair where `callee` is a known project file, `caller` is appended to `file_caller_map[callee]`.

**Stage 5 — Path normalization and result assembly**
Each file's absolute path is converted to a project-relative path, then passed through `rel_to_copy_path` to produce the `{parent_dir}/{stem}_{ext}/{filename}` copy-destination format. The project name (basename of `project_dir`) is prepended. Caller and callee lists undergo the same transformation. Each file is represented as a dict with `"file"`, `"callers"`, and `"callees"` keys, collected into `file_info_list`.

### `extract_callee_source`

**Stage 1 — AST acquisition**
The `callee_file_path` is joined with `project_dir` to form an absolute path. `parse_file` returns the cached or freshly parsed `(root_node, content)` tuple.

**Stage 2 — Search name derivation**
`callee_name` is split on `"."`. The primary search name is the trailing component (the actual attribute); if there are multiple parts, the leading component is added as a fallback (for cases where the trailing part is a built-in method and the leading part is the real definition).

**Stage 3 — BFS definition lookup**
For each candidate name, `_find_definition_node` performs a breadth-first traversal of the AST. At each node, if the node's type is `identifier`, `type_identifier`, or `namespace_identifier`, its decoded text is compared to the search name. Nodes whose ancestors contain import-related node types are skipped via `_is_inside_import`. The first matching node's **parent** is returned as the definition node.

**Stage 4 — Source extraction**
The definition node's `.text` bytes are decoded to UTF-8 and returned. If no match is found across all candidate names, `None` is returned.

---

## 3. Outputs

### `build_project_dependencies`
Returns `list[dict]` — one dict per analyzed file:
```
[
  {
    "file":    "project_name/parent/stem_ext/filename",
    "callers": ["project_name/...", ...],
    "callees": ["project_name/...", ...],
  },
  ...
]
```
All paths use the `project_name/{copy_path}` format produced by `rel_to_copy_path`. No files are written; the list is the sole output.

### `extract_callee_source`
Returns `str | None` — the full source text of the matched definition node (function definition, class definition, assignment, etc.) decoded from UTF-8. Returns `None` when no matching definition is found.

---

## 4. Key Data Structures

### `all_file_list`
| Field / Key | Type | Purpose |
|---|---|---|
| *(element)* | `str` | Absolute path to a supported source file within the project |

### `project_file_set`
| Field / Key | Type | Purpose |
|---|---|---|
| *(element)* | `str` | Project-relative, forward-slash-normalized file path (e.g., `"src/foo.py"`) used for O(1) membership checks during import resolution |

### `file_callee_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (abs path) | Absolute path of the caller file |
| value | `set[str]` | Set of absolute paths of files that the caller imports or implicitly references |

### `file_caller_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (abs path) | Absolute path of a project file |
| value | `list[str]` | List of absolute paths of files that import or reference this file |

### `dir_ext_groups`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `tuple[str, str]` | `(directory_path, extension)` grouping key for same-package visibility |
| value | `list[str]` | Absolute paths of all files in that directory with that extension |

### Output dict (element of `file_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Copy-destination path of the analyzed file, prefixed with project name |
| `"callers"` | `list[str]` | Copy-destination paths of files that depend on this file |
| `"callees"` | `list[str]` | Copy-destination paths of files that this file depends on |

### `search_names` (inside `extract_callee_source`)
| Field / Key | Type | Purpose |
|---|---|---|
| `[0]` | `str` | Trailing component of `callee_name` (primary search target, e.g., `"process"`) |
| `[1]` (optional) | `str` | Leading component of `callee_name` (fallback for built-in trailing parts, e.g., `"TEMPLATE"`) |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** policy. No operation is designed to terminate the entire pipeline on failure. Individual file processing errors are silently skipped or produce `None` returns, allowing the dependency graph construction to proceed with partial results. There is no retry logic; unrecoverable per-file errors simply result in that file being excluded from the output.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` | A same-package file cannot be opened for source reading (Step 3.5) | Caught; the file is skipped via `continue` | Yes | That file's same-package dependencies are not recorded; all other files are unaffected |
| `UnicodeDecodeError` | A same-package file contains non-UTF-8 bytes when read as text | Caught together with `OSError`; file is skipped via `continue` | Yes | Same as above |
| Definition not found | `_find_definition_node` returns `None` for every search name in `extract_callee_source` | Function returns `None` | Yes | Caller receives `None`; no source code snippet is available for that callee |
| Unsupported file extension | `get_import_params` returns `(None, None)` for a file extension | Import analysis block is skipped (`if language and import_query_str`) | Yes | No callees are recorded for that file; file still appears in the graph |
| Unresolvable import | `resolve_module_to_project_path` returns `None` for a given import | The import is silently ignored; no callee entry is added | Yes | External or standard-library imports produce no dependency edges |
| `callee_path` not in `file_caller_map` | A resolved callee path points outside the collected file list | Guard check `if callee_path in file_caller_map` prevents insertion | Yes | Caller–callee edge is dropped for that specific pairing |

---

## 3. Design Notes

- **Isolation of file-level failures**: The `OSError`/`UnicodeDecodeError` catch in the same-package visibility step is scoped narrowly to individual file reads, ensuring a single unreadable file does not abort group-level or project-level processing.
- **`None`-return as soft failure**: `extract_callee_source` and `_find_definition_node` communicate absence via `None` rather than raising exceptions, delegating the decision of how to handle a missing definition entirely to the caller.
- **Silent omission over assertion**: Unresolvable imports and unsupported extensions are treated as expected conditions (external dependencies, mixed-language projects) rather than errors, so no logging or exception is emitted for them.
- **No explicit parse error handling**: Errors from `parse_file` (e.g., I/O failure on an AST parse) are not caught within this file; such failures would propagate upward, representing the only path to uncontrolled termination.

## Summary

**dependency_graph.py**: Builds the project-wide inter-file dependency graph and extracts definition source from dependency files.

**Public functions:**
- `build_project_dependencies(project_dir: str) -> list[dict]` — returns `{"file", "callers", "callees"}` dicts with paths in `project_name/copy_path` format
- `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) -> str | None` — returns source text of a named definition via BFS AST search

**Key structures:** `file_callee_map` (`dict[str, set[str]]`), `file_caller_map` (`dict[str, list[str]]`), `project_file_set` (`set[str]`)
