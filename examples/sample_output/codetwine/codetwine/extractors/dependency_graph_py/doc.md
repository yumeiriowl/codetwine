# Design Document: codetwine/extractors/dependency_graph.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyze inter-file dependencies across an entire project by parsing import statements and same-package references, and extract individual definition source code from dependency target files.

## 2. When to Use This Module

- **Building a project-wide dependency graph**: Call `build_project_dependencies(project_dir)` to obtain a list of dicts describing, for every supported source file, which files it imports (`callees`) and which files import it (`callers`). This is the entry point used by `pipeline.py` to initialize the dependency graph before per-file processing.
- **Retrieving a specific definition's source code from a dependency file**: Call `extract_callee_source(callee_file_path, callee_name, project_dir)` to get the raw source text of a named definition (function, class, variable, etc.) from a given file. This is used by `usage_analysis.py` when resolving what an imported symbol's implementation looks like.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parse the target file's AST and return the source text of the node whose name matches `callee_name`; tries the trailing part of dotted names first, then the leading part. Returns `None` if not found. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walk all supported source files under `project_dir`, resolve their imports to project-internal paths, add implicit same-package dependencies for Java/Kotlin, and return a list of `{"file", "callers", "callees"}` dicts using `project_name/copy_path` formatted paths. |

## 4. Design Decisions

- **Dotted-name fallback in `extract_callee_source`**: For an attribute access like `helper.process`, the trailing component (`process`) is tried first as the definition name. If not found, the leading component (`helper`) is tried. This handles the case where the leading part is a module-level constant whose built-in method (e.g., `TEMPLATE.format`) is what is actually called.
- **BFS over import-aware AST traversal**: Definition lookup uses BFS rather than a targeted tree-sitter query so that the same search logic works uniformly across all supported languages. Nodes inside import statements are explicitly skipped to avoid matching import references as definitions.
- **Same-package implicit dependency injection (Step 3.5)**: For languages where `SAME_PACKAGE_VISIBLE` is set (Java/Kotlin), files in the same directory are checked for textual references to sibling class names via regex, and matching pairs are added as directed dependencies without requiring an explicit import statement.
- **`parse_file` cache reuse**: Both `extract_callee_source` and `build_project_dependencies` call `parse_file`, which maintains a module-level cache keyed by absolute path, so each file is parsed at most once across the entire pipeline run.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constant

### `_DEFINITION_NAME_NODE_TYPES`

| Property | Value |
|---|---|
| Type | `set[str]` |
| Contents | `{"identifier", "type_identifier", "namespace_identifier"}` |

**Responsibility:** Centralizes the set of tree-sitter node types that may carry a definition name, avoiding repetition across BFS logic.

---

## Functions

---

### `_is_inside_import`

**Signature:** `_is_inside_import(node) -> bool`

- `node`: A tree-sitter AST node (any node type).
- Returns `bool`: `True` if the node is a descendant of an import/include statement.

**Responsibility:** Guards definition searches from false-positive matches on names that appear inside import statements rather than as actual definitions.

**When to use:** Called internally by `_find_definition_node` before accepting a candidate identifier node as a real definition.

**Design decisions:**
- Traverses the ancestor chain via `node.parent` up to the root rather than inspecting children; this is efficient because import statement depth is shallow.
- Detects import-related node types by checking whether the string `"import"` is a substring of the node type name, plus an explicit check for `"preproc_include"` (C/C++ `#include`). This substring check makes the guard language-agnostic across Python, Java, JS, and C/C++ without enumerating every exact node type name.

**Constraints & edge cases:**
- Relies on tree-sitter's `node.parent` API; a node detached from a tree would cause the loop to terminate immediately, returning `False`.
- The substring match on `"import"` could theoretically match hypothetical future node type names that contain the word "import" but are not import statements.

---

### `_find_definition_node`

**Signature:** `_find_definition_node(root_node, definition_name: str) -> node | None`

- `root_node`: The tree-sitter AST root node for an entire file.
- `definition_name: str`: The bare name to search for (e.g., `"parse_file"`, `"Point"`).
- Returns: The **parent** AST node of the matched identifier node, or `None` if not found.

**Responsibility:** Locates the AST subtree that constitutes a definition (function, class, variable assignment, etc.) for a given name, so its source text can be extracted.

**When to use:** Called by `extract_callee_source` to find which top-level (or nested) construct declares `definition_name` inside a dependency file.

**Design decisions:**
- Uses breadth-first search (BFS) so shallower/top-level definitions are found before deeper nested ones.
- The BFS queue carries `(node, parent)` pairs, meaning the return value is always the **parent** of the matched name node (e.g., the `function_definition` containing the `identifier`), not the identifier itself—this is what provides the full source text.
- Nodes inside import statements are filtered via `_is_inside_import` to avoid treating imported names as definitions.
- Restricts matches to `_DEFINITION_NAME_NODE_TYPES` to skip unrelated text nodes.

**Constraints & edge cases:**
- Returns the first BFS match; if the same name is defined multiple times, only the shallowest occurrence is returned.
- Returns `None` when the name is not present in the file at all.

---

### `extract_callee_source`

**Signature:**
```
extract_callee_source(
    callee_file_path: str,
    callee_name: str,
    project_dir: str,
) -> str | None
```

- `callee_file_path: str`: Path of the dependency file **relative to the project root** (e.g., `"src/foo.py"`).
- `callee_name: str`: The name of the definition to retrieve; may be a dotted attribute expression (e.g., `"helper.process"`, `"TEMPLATE.format"`).
- `project_dir: str`: Absolute path to the project root directory.
- Returns `str | None`: The full source text of the matched definition node, or `None` if not found.

**Responsibility:** Retrieves the source code of a specific named definition from a project-internal dependency file, enabling callers to embed callee implementation text in analysis output.

**When to use:** Invoked by `usage_analysis.py` when the first occurrence of a callee name is encountered and its source code needs to be recorded.

**Design decisions:**
- Constructs the absolute path by joining `project_dir` and `callee_file_path`; relies on `parse_file`'s module-level cache so the file is not re-parsed on repeated calls.
- Dotted names (e.g., `"helper.process"`) are handled by a two-attempt fallback strategy: first search for the trailing part (`"process"`), then if not found, search for the leading part (`"helper"`). This covers both method calls on objects and built-in method calls on module-level constants.
- `search_names` always contains at least one element; the second element is added only when the name contains a dot.

**Constraints & edge cases:**
- `callee_file_path` must be relative to `project_dir`; an incorrect path causes `parse_file` to fail.
- Returns the source text of the **parent** node of the matched identifier; for an `assignment` node this includes the entire right-hand side.
- Only the first matched definition (BFS order) is returned if the name appears multiple times.
- Returns `None` for names with more than two dotted components where neither the last nor the first part matches any definition.

---

### `build_project_dependencies`

**Signature:**
```
build_project_dependencies(project_dir: str) -> list[dict]
```

- `project_dir: str`: Absolute path to the root directory of the project to analyze.
- Returns `list[dict]`: A list of dependency-info dictionaries. Each dict has the shape:

| Key | Type | Description |
|---|---|---|
| `"file"` | `str` | Copy-path of this file, prefixed with `project_name/` |
| `"callers"` | `list[str]` | Copy-paths of files that import this file |
| `"callees"` | `list[str]` | Copy-paths of files that this file imports |

All paths follow the `project_name/{parent_dir}/{stem}_{ext}/{filename}` copy-path convention produced by `rel_to_copy_path`.

**Responsibility:** Constructs the complete inter-file dependency graph for an entire project by combining explicit import analysis with same-package implicit reference detection, and expresses the result using stable copy-destination paths.

**When to use:** Called once at pipeline startup by `pipeline.py` to produce the project-wide dependency graph consumed by all downstream processing steps.

**Design decisions:**

- **File collection:** `os.walk` with in-place `dir_names` pruning against `EXCLUDE_PATTERNS` prevents descent into excluded directories (e.g., `.git`, `node_modules`, `__pycache__`).
- **Import-based edges:** For each file, import statements are extracted via `extract_imports` and each module string is resolved to a project file path via `resolve_module_to_project_path`; only modules that resolve within the project become callee edges.
- **Same-package implicit edges (Java/Kotlin):** Files in the same directory with the same extension are grouped when `SAME_PACKAGE_VISIBLE` is true for that extension. A plain-text regex word-boundary search for each peer's class name (basename without extension) determines whether an implicit dependency exists, adding a unidirectional edge only when the name appears in source.
- **Caller index:** Built as the reverse of the callee map; a file appears in another file's `callers` list if and only if it references that other file.
- **Path format:** All returned paths use the `rel_to_copy_path` convention rather than raw relative paths, ensuring consistency with the rest of the pipeline's file references.
- All internal maps are keyed by **absolute** paths to avoid cross-platform `os.path.relpath` inconsistencies; conversion to relative/copy paths occurs only in the final output step.

**Constraints & edge cases:**
- Only files whose extension (without leading `.`) appears as a key in `DEFINITION_DICTS` are included; unsupported file types are silently ignored.
- Files matching `EXCLUDE_PATTERNS` at the file level (not only directory level) are also excluded.
- If `language` or `import_query_str` is `None` for a given extension (returned by `get_import_params`), import analysis is skipped for that file and its callee set remains empty.
- Same-package implicit edge detection reads file content via `open`; files that raise `OSError` or `UnicodeDecodeError` are silently skipped.
- The `callers` lists in the returned dicts are **not** deduplicated at the construction level; a caller that imports the same file via multiple aliases would be added multiple times.
- The function does not persist results; serialization is the caller's responsibility.

## Dependency Description

## Dependency Description

### Dependencies (modules this file imports)

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into tree-sitter ASTs. Called both during callee source extraction (`extract_callee_source`) and during the import analysis phase of `build_project_dependencies`.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to extract structured import information from parsed ASTs, enabling resolution of inter-file dependencies within `build_project_dependencies`.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to resolve import module strings to project-internal file paths, and `get_import_params` to retrieve the tree-sitter `Language` object and query string required for import analysis for a given file extension.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` (specifically `.keys()`) to determine the set of supported file extensions when collecting project files, `EXCLUDE_PATTERNS` to filter out directories and files during the file walk, and `SAME_PACKAGE_VISIBLE` to identify languages (Java/Kotlin) where same-package class references are implicit without imports.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to convert project-relative file paths into the copy-destination directory structure format used in the output dependency graph.

---

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : Uses `build_project_dependencies` to construct the project-wide dependency graph as the first step of the pipeline, producing the list of file dependency records (callers/callees) used throughout subsequent processing.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a dependency file, supporting usage analysis of cross-file symbol references.

---

### Dependency Direction

All relationships are **unidirectional**:

- This module depends on `ts_parser.py`, `imports.py`, `import_to_path.py`, `settings.py`, and `file_utils.py` — none of those modules import from this file.
- `pipeline.py` and `usage_analysis.py` depend on this module — this module does not import from either of them.

## Data Flow

# Data Flow

## 1. Inputs

**`build_project_dependencies(project_dir: str)`**
- `project_dir`: Absolute path to the project root directory (string).
- Configuration values from `settings.py`: `DEFINITION_DICTS` (supported file extensions), `EXCLUDE_PATTERNS` (directory/file glob patterns to skip), `SAME_PACKAGE_VISIBLE` (per-extension flag for same-package implicit visibility).
- File system: all files reachable under `project_dir` via `os.walk`.
- Source file contents: read as UTF-8 text for same-package class-name matching; read as binary by `parse_file` for AST construction.
- Import query parameters from `get_import_params`: `(Language, import_query_str)` per file extension.
- AST root nodes returned by `parse_file(file_path)`.
- `ImportInfo` objects returned by `extract_imports(root_node, language, import_query_str)`.

**`extract_callee_source(callee_file_path, callee_name, project_dir)`**
- `callee_file_path`: Project-relative path to the target file (string).
- `callee_name`: Dot-separated name of the definition to retrieve (string, e.g. `"helper.process"` or `"TEMPLATE.format"`).
- `project_dir`: Absolute path to the project root (string).
- AST root node from `parse_file` (cached).

---

## 2. Transformation Overview

### `build_project_dependencies`

**Stage 1 — File collection:**  
`os.walk` traverses `project_dir`, pruning directories and files matching `EXCLUDE_PATTERNS`. Files whose extensions appear in `DEFINITION_DICTS` are collected into `all_file_list` (list of absolute paths).

**Stage 2 — Project file set construction:**  
Each absolute path in `all_file_list` is converted to a forward-slash relative path (relative to `project_dir`) and inserted into `project_file_set` (a `set[str]`). This set is used downstream as the universe of known project files for import resolution.

**Stage 3 — Callee map construction:**  
For each file in `all_file_list`, `get_import_params` supplies the language and query string. `parse_file` produces the AST, and `extract_imports` extracts `ImportInfo` objects. Each `ImportInfo.module` is passed to `resolve_module_to_project_path` along with the file's relative path and `project_file_set`; resolved results (absolute paths) are accumulated into `callee_set`. The result is stored in `file_callee_map: dict[str, set[str]]` keyed by absolute caller path.

**Stage 3.5 — Same-package implicit dependency injection:**  
Files whose extension is flagged in `SAME_PACKAGE_VISIBLE` are grouped by `(directory, extension)` into `dir_ext_groups`. Within each group, source text is read and scanned with pre-compiled regex patterns (`\b<ClassName>\b`) for every peer file's stem. When a match is found, the peer's absolute path is added to the caller's entry in `file_callee_map`.

**Stage 4 — Caller (reverse) map construction:**  
`file_caller_map: dict[str, list[str]]` is initialized with one entry per file (empty list). `file_callee_map` is iterated: for each `(caller, callee)` pair, `caller` is appended to `file_caller_map[callee]`.

**Stage 5 — Path conversion and output assembly:**  
Absolute paths are converted to project-relative strings, then transformed through `rel_to_copy_path` into copy-destination path format. Each file's entry is assembled as a dict with `"file"`, `"callers"`, and `"callees"` keys, all prefixed with `project_name/`. The complete list is returned.

### `extract_callee_source`

**Stage 1 — AST retrieval:**  
`callee_file_path` is joined with `project_dir` to form an absolute path. `parse_file` returns the cached or freshly parsed AST root node.

**Stage 2 — Name decomposition:**  
`callee_name` is split on `"."`. A search list is built: the trailing part is tried first (handles attribute access like `helper.process`), then the leading part (handles cases like `TEMPLATE.format` where the trailing part is a built-in method).

**Stage 3 — BFS definition search:**  
`_find_definition_node` performs a breadth-first traversal of the AST. For each node whose type is in `{identifier, type_identifier, namespace_identifier}` and whose text matches the search name, `_is_inside_import` checks ancestor chain to exclude import-statement nodes. The first qualifying node's **parent** is returned as the definition node.

**Stage 4 — Source extraction:**  
The definition node's `.text` is decoded as UTF-8 and returned. If no match is found across all search names, `None` is returned.

---

## 3. Outputs

**`build_project_dependencies`** returns `list[dict]`:
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
One entry per supported source file; paths use the `rel_to_copy_path` copy-destination format prefixed with the project name.

**`extract_callee_source`** returns `str | None`:  
The full source text of the matched definition node (function, class, assignment, etc.), or `None` if no definition is found.

Both functions have no file-write side effects; all output is via return values.

---

## 4. Key Data Structures

### `all_file_list`
| Field / Key | Type | Purpose |
|---|---|---|
| elements | `str` | Absolute filesystem paths of all supported source files found under `project_dir` |

### `project_file_set`
| Field / Key | Type | Purpose |
|---|---|---|
| elements | `str` | Forward-slash project-relative paths (`"src/foo.py"`); used as membership lookup for import resolution |

### `file_callee_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute path of the caller file |
| value | `set[str]` | Absolute paths of all files this caller imports (explicit via import statements + implicit same-package references) |

### `file_caller_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` | Absolute path of the callee file |
| value | `list[str]` | Absolute paths of all files that import this callee |

### `dir_ext_groups`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `tuple[str, str]` | `(directory_path, file_extension)` grouping key for same-package detection |
| value | `list[str]` | Absolute paths of all files sharing the same directory and extension |

### Output dict (per file entry in `build_project_dependencies` result)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Copy-destination path of this file, prefixed with project name |
| `"callers"` | `list[str]` | Copy-destination paths of files that import this file |
| `"callees"` | `list[str]` | Copy-destination paths of files this file imports |

### `search_names` (inside `extract_callee_source`)
| Field / Key | Type | Purpose |
|---|---|---|
| element 0 | `str` | Trailing part of dot-split `callee_name`; tried first as definition name |
| element 1 (optional) | `str` | Leading part of dot-split `callee_name`; tried as fallback |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file follows a **graceful degradation / logging-and-continue** strategy. Individual file failures are isolated so that the overall dependency graph construction completes even when specific files or definitions cannot be processed. The only explicit exception handling is a targeted `try/except` block in the same-package visibility scan; all other operations rely on the caller or dependency layer to propagate unexpected failures naturally (no catch-all suppression at the top level).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | A same-package source file cannot be opened or decoded during the raw-text scan (Step 3.5) | Silently caught; the file is skipped via `continue` | Yes | That file is excluded from same-package callee detection; all other files in the group are still processed |
| Definition not found | `_find_definition_node` returns `None` for both the trailing and leading name parts in `extract_callee_source` | Returns `None` to the caller | Yes | The caller receives `None` and must handle the missing source; no exception is raised |
| Unresolvable import | `resolve_module_to_project_path` returns `None` for a given import | The resolved result is simply not added to `callee_set` | Yes | The import is treated as an external (non-project) dependency and omitted from the graph |
| Unsupported file extension | `get_import_params` returns `(None, None)` for the file's extension | The import-extraction block is skipped via the `if language and import_query_str:` guard | Yes | No callees are recorded for that file; the file still appears in the graph with empty callee/caller lists |
| File not in `file_caller_map` | A resolved callee path is not among the collected project files | The `if callee_path in file_caller_map:` guard prevents the append | Yes | The orphaned reference is silently dropped; no caller entry is created for that path |

---

## 3. Design Notes

- **Scope of explicit exception handling is narrow.** Only file I/O during the same-package text scan is wrapped in `try/except`. All other error paths are handled through return-value checks (`None` guards, truthiness checks) rather than exception catching, keeping control flow predictable.
- **Silent skipping is preferred over logging at this layer.** Neither the `OSError`/`UnicodeDecodeError` path nor the unresolvable-import path emits a log message in this file; the module-level `logger` is declared but not invoked, indicating that surfacing these events is delegated to dependency layers (e.g., `parse_file` caching, `resolve_module_to_project_path`) or left to the caller.
- **Partial results are always returned.** `build_project_dependencies` never raises an exception due to a single bad file; it always returns the list accumulated so far, ensuring the pipeline can proceed with whatever dependency information was successfully extracted.

## Summary

**dependency_graph.py**: Builds a project-wide inter-file dependency graph and extracts named definition source code from dependency files.

**Public functions:**
- `build_project_dependencies(project_dir: str) → list[dict]`: returns `{"file", "callers", "callees"}` dicts with copy-path formatted strings
- `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) → str | None`: returns source text of a named definition via BFS AST search

**Key structures:** `file_callee_map: dict[str, set[str]]`, `file_caller_map: dict[str, list[str]]`, output list of per-file dependency dicts.
