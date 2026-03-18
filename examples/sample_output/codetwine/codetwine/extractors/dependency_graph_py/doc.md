# Design Document: codetwine/extractors/dependency_graph.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Analyze inter-file dependencies across a multi-language project by walking the file system, resolving import statements to project-internal files, and extracting definition source code from dependency target files.

## 2. When to Use This Module

- **Building a project-wide dependency graph**: Call `build_project_dependencies(project_dir)` to obtain a list of dicts describing each file's callers and callees. Used by `pipeline.py` as the first step of the analysis pipeline to establish relationships among all supported-language files in the project.
- **Retrieving a specific definition's source code from a dependency file**: Call `extract_callee_source(callee_file_path, callee_name, project_dir)` to look up the AST of a dependency file and return the source text of the named definition (function, class, variable, etc.). Used by `usage_analysis.py` when resolving a referenced symbol back to its implementation.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parse the AST of the given file and return the source code of the node that defines `callee_name`. Falls back from the trailing part to the leading part of dotted names if the first search fails. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walk the project directory, resolve all import statements to project-internal files, add implicit same-package dependencies for Java/Kotlin, and return a list of dicts with `"file"`, `"callers"`, and `"callees"` keys using `project_name/copy_path` formatted paths. |

## 4. Design Decisions

- **BFS-based definition lookup**: `_find_definition_node` searches the AST breadth-first and targets only `identifier`, `type_identifier`, and `namespace_identifier` node types, skipping nodes that appear inside import statements. This avoids false matches on imported names that share the same identifier as a local definition.
- **Dotted-name fallback strategy**: `extract_callee_source` splits dotted callee names (e.g. `helper.process`) and searches first by the trailing component, then by the leading component. This handles both attribute access on objects and calls to built-in methods on named constants without requiring the caller to pre-classify the name.
- **Implicit same-package dependency injection (Java/Kotlin)**: Because Java and Kotlin allow referencing classes in the same directory without explicit imports, `build_project_dependencies` performs a regex word-boundary scan of each file's source text against class names derived from sibling filenames within the same directory and extension group, adding unidirectional edges only when a name match is found.
- **Reuse of parse cache**: Both functions rely on `parse_file` from `ts_parser.py`, which maintains a module-level cache keyed by absolute path, so each file is parsed at most once across the entire pipeline run.
- **`copy_path` output format**: All paths in the returned dependency list use the `project_name/{parent_dir}/{stem}_{ext}/{filename}` format via `rel_to_copy_path`, matching the physical output directory structure used when copying files, ensuring paths remain valid if the output folder is relocated.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-level Constant

### `_DEFINITION_NAME_NODE_TYPES`
**Type:** `set[str]`

The set of tree-sitter node type strings that represent definition names in the AST. Contains `"identifier"`, `"type_identifier"`, and `"namespace_identifier"`.

**Responsibility:** Centralizes the node types used during BFS traversal in `_find_definition_node` so that the matching logic is not scattered inline.

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
| `node` | tree-sitter `Node` | The AST node to check ancestry for |

**Returns:** `bool` — `True` if any ancestor node has a type containing `"import"` or is `"preproc_include"`.

**Responsibility:** Prevents definition-search from incorrectly matching identifiers that appear inside import/include statements rather than in actual definition sites.

**When to use:** Called internally by `_find_definition_node` on every candidate node before accepting it as a definition match.

**Design decisions:**
- Traverses the parent chain using tree-sitter's `Node.parent` field (upward traversal) rather than inspecting node children, because ancestry determines syntactic context.
- The `"import"` substring check covers multiple node type variants across languages (`import_statement`, `import_from_statement`, `import_declaration`) without enumerating each explicitly.

**Constraints & edge cases:**
- Relies on tree-sitter's `parent` attribute being populated; nodes without a parent chain terminate the loop cleanly.
- Only checks node type strings, not semantic meaning.

---

### `_find_definition_node`

**Signature:**
```python
def _find_definition_node(root_node, definition_name: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `root_node` | tree-sitter `Node` | Root node of the parsed AST |
| `definition_name` | `str` | The bare name to find (e.g. `"parse_file"`, `"Point"`) |

**Returns:** tree-sitter `Node` (the parent of the matched identifier node), or `None` if not found.

**Responsibility:** Locates the enclosing definition node (function, class, assignment, etc.) for a given symbol name in the AST via BFS.

**When to use:** Called by `extract_callee_source` to locate the source span of a named definition within a dependency file.

**Design decisions:**
- Uses BFS (via `collections.deque`) rather than DFS to find the shallowest occurrence, which is more likely to be a top-level definition.
- Tracks the parent alongside each node in the queue so that the containing definition node can be returned directly without a second traversal.
- Only nodes with types in `_DEFINITION_NAME_NODE_TYPES` are matched, filtering out structural nodes.
- Import-context nodes are excluded via `_is_inside_import` to avoid false matches on aliased or re-exported names.

**Constraints & edge cases:**
- Returns the first (shallowest, leftmost) match found; if a name is defined multiple times, only the first occurrence is returned.
- Returns the *parent* of the identifier node, not the identifier node itself; the parent is assumed to be the definition container.
- Returns `None` if the name does not appear outside an import context.

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
| `callee_file_path` | `str` | Project-relative path to the file containing the definition (e.g. `"src/foo.py"`) |
| `callee_name` | `str` | The name to look up, possibly dotted (e.g. `"helper.process"`, `"TEMPLATE.format"`) |
| `project_dir` | `str` | Absolute path to the project root directory |

**Returns:** `str | None` — The source text of the matched definition node, or `None` if not found.

**Responsibility:** Retrieves the full source text of a named definition from a dependency file, supporting both direct names and dotted attribute-access names.

**When to use:** Called by `usage_analysis.py` when it needs to embed the source code of a callee definition into analysis output.

**Design decisions:**
- For a dotted name such as `"helper.process"`, the function tries the trailing component (`"process"`) first, under the assumption it is the defined symbol. If that fails, it retries with the leading component (`"helper"`), to handle cases like `"TEMPLATE.format"` where the leading name is the project-defined variable.
- Parsing is delegated to `parse_file`, which caches results at module level, so repeated calls on the same file are free.
- Returns the `.text` of the parent node decoded from UTF-8, giving the complete source span of the definition.

**Constraints & edge cases:**
- `callee_file_path` must be relative to `project_dir`; it is joined with `os.path.join` to form the absolute path passed to `parse_file`.
- Only the first matching definition is returned (inherits `_find_definition_node`'s first-match behavior).
- Dotted names with more than two parts use only `parts[-1]` and `parts[0]`; middle components are ignored.
- Returns `None` if neither search name produces a match.

---

### `build_project_dependencies`

**Signature:**
```python
def build_project_dependencies(project_dir: str) -> list[dict]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `project_dir` | `str` | Absolute path to the root directory of the project to analyze |

**Returns:** `list[dict]` — A list of dependency-info dictionaries. Each dictionary has three keys:

| Key | Type | Description |
|-----|------|-------------|
| `"file"` | `str` | Copy-path of the file, prefixed with project name (`"{project_name}/{copy_path}"`) |
| `"callers"` | `list[str]` | Copy-paths of files that import this file |
| `"callees"` | `list[str]` | Copy-paths of files imported by this file |

**Responsibility:** Performs a full static analysis of all supported-language source files in the project to construct a bidirectional file-level dependency graph.

**When to use:** Called once per pipeline run (from `pipeline.py`) at the start of processing to establish the dependency graph used by all downstream steps.

**Design decisions:**

- **File collection:** Uses `os.walk` with in-place `dir_names` filtering against `EXCLUDE_PATTERNS` to prune entire subtrees efficiently.
- **Callee map:** For each file, import statements are extracted via `extract_imports` and each module string is resolved to a project file via `resolve_module_to_project_path`. Only modules that resolve to a project file are recorded.
- **Same-package implicit dependencies (Java/Kotlin):** Files in the same directory with the same extension are grouped. For each file in a group, the source text is scanned with a word-boundary regex to detect references to class names (bare filenames without extension) from sibling files. Matching adds a unidirectional callee edge without requiring an explicit import statement. This behavior is gated by `SAME_PACKAGE_VISIBLE` and applies only to extensions where it is enabled.
- **Caller map:** Built as a reverse index of the callee map; no additional file I/O is performed.
- **Path format:** All output paths use the `"{project_name}/{rel_to_copy_path(rel)}"` format to match the copy-destination directory structure used elsewhere in the pipeline, ensuring paths remain valid when the output folder is relocated.
- All file paths are converted to absolute paths internally before being stored in maps to avoid relative-path ambiguity.

**Constraints & edge cases:**
- Only files with extensions present in `DEFINITION_DICTS.keys()` are included.
- Files matching any pattern in `EXCLUDE_PATTERNS` (checked against both filenames and directory names) are skipped.
- If a callee path resolved from an import does not appear in `file_caller_map` (i.e., it is outside the collected file set), it is silently omitted from caller registration.
- Same-package detection reads files as UTF-8 text; files that raise `OSError` or `UnicodeDecodeError` are skipped without aborting the analysis.
- The function does not perform recursive or transitive closure; only direct import relationships are recorded.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into tree-sitter ASTs. Called both when searching for callee definitions (`extract_callee_source`) and when scanning all project files for import statements (`build_project_dependencies`).

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to extract structured import information from a parsed AST node, supplying the language and query string required for the tree-sitter query engine.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/import_to_path.py` : Uses `resolve_module_to_project_path` to determine whether an imported module resolves to a file within the project, and `get_import_params` to retrieve the tree-sitter `Language` object and import query string for a given file extension.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to convert project-relative file paths into the copy-destination path format (`{parent}/{stem}_{ext}/{filename}`) when constructing the final dependency graph output.

- `codetwine/extractors/dependency_graph_py/dependency_graph.py` → `codetwine/config/settings.py` : Uses `DEFINITION_DICTS` to determine the set of supported file extensions for project-wide file collection, `EXCLUDE_PATTERNS` to filter out directories and files during traversal, and `SAME_PACKAGE_VISIBLE` to identify language extensions (e.g. Java, Kotlin) that require same-package implicit dependency resolution.

## Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : Uses `build_project_dependencies` as the first step of the pipeline to construct the project-wide dependency graph. The returned list of file dependency dicts is subsequently converted to internal paths and used as the master file list for all downstream processing.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph_py/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a resolved dependency file, providing the definition source text during usage/call analysis.

## Dependency Direction

All relationships are unidirectional:

- `dependency_graph.py` → `ts_parser.py` : unidirectional (dependency_graph.py consumes parse_file; ts_parser.py has no knowledge of this module)
- `dependency_graph.py` → `imports.py` : unidirectional (dependency_graph.py consumes extract_imports; imports.py has no knowledge of this module)
- `dependency_graph.py` → `import_to_path.py` : unidirectional (dependency_graph.py consumes resolution utilities; import_to_path.py has no knowledge of this module)
- `dependency_graph.py` → `file_utils.py` : unidirectional (dependency_graph.py consumes path conversion; file_utils.py has no knowledge of this module)
- `dependency_graph.py` → `settings.py` : unidirectional (dependency_graph.py reads configuration constants; settings.py has no knowledge of this module)
- `pipeline.py` → `dependency_graph.py` : unidirectional (pipeline.py calls into this module; dependency_graph.py has no knowledge of pipeline.py)
- `usage_analysis.py` → `dependency_graph.py` : unidirectional (usage_analysis.py calls into this module; dependency_graph.py has no knowledge of usage_analysis.py)

## Data Flow

# Data Flow

## 1. Inputs

**`build_project_dependencies(project_dir: str)`**
- `project_dir`: absolute path to the project root directory (string)
- File system: all files reachable under `project_dir` via `os.walk`
- Config values: `DEFINITION_DICTS` (supported extensions), `EXCLUDE_PATTERNS` (directories/files to skip), `SAME_PACKAGE_VISIBLE` (extensions that allow implicit same-package references)
- AST data: tree-sitter parse results via `parse_file` (returns `(root_node, bytes)`)
- Import metadata: `ImportInfo` records from `extract_imports`

**`extract_callee_source(callee_file_path, callee_name, project_dir)`**
- `callee_file_path`: project-relative path to the file containing the definition (string)
- `callee_name`: name of the symbol to find, possibly dotted (e.g. `"helper.process"`) (string)
- `project_dir`: absolute path to the project root (string)
- AST data: tree-sitter parse result for `callee_file_path` via `parse_file`

---

## 2. Transformation Overview

### `build_project_dependencies`

**Stage 1 — File discovery**
`os.walk` traverses `project_dir`, pruning directories matching `EXCLUDE_PATTERNS` in-place. Individual files are also filtered against `EXCLUDE_PATTERNS`. Files whose extension (without leading `.`) appears in `DEFINITION_DICTS.keys()` are collected into `all_file_list` as absolute paths.

**Stage 2 — Project file set construction**
Each absolute path in `all_file_list` is converted to a project-relative POSIX string and accumulated into `project_file_set` (a `set[str]`). This set is the membership oracle used during import resolution.

**Stage 3 — Callee map construction (explicit imports)**
For each file in `all_file_list`, `get_import_params` fetches the tree-sitter `Language` and query string for that file's extension. If both are available, `parse_file` produces the AST, and `extract_imports` yields `ImportInfo` records. Each record's `module` field is passed to `resolve_module_to_project_path` together with the file's relative path and `project_file_set`. Resolved paths are converted to absolute form and accumulated into a per-file `callee_set`. The result is stored in `file_callee_map` keyed by absolute path.

**Stage 4 — Callee map augmentation (same-package implicit references)**
Files whose extension appears in `SAME_PACKAGE_VISIBLE` are grouped by `(directory, extension)` into `dir_ext_groups`. Within each group, the raw source of each file is read and searched for the class name (filename stem) of every other file in the group using a pre-compiled word-boundary regex. When a match is found, the other file's absolute path is added to the caller file's entry in `file_callee_map`.

**Stage 5 — Caller map construction (reverse index)**
`file_caller_map` is initialized with an empty list for every file. The `file_callee_map` is iterated: for each `(caller, callee)` pair, the caller is appended to `file_caller_map[callee]` if the callee is a known project file.

**Stage 6 — Serialization to relative copy-destination paths**
Each entry in `all_file_list` is converted to a project-relative path. Caller and callee sets are similarly converted. All paths are then passed through `rel_to_copy_path` and prefixed with `project_name/` to produce the final `"project_name/{parent}/{stem}_{ext}/{filename}"` format. Each file's record is assembled as a dict and appended to `file_info_list`.

---

### `extract_callee_source`

**Stage 1 — AST retrieval**
The absolute path is formed by joining `project_dir` and `callee_file_path`. `parse_file` returns the cached or freshly parsed `(root_node, bytes)` tuple; only `root_node` is used.

**Stage 2 — Search name derivation**
`callee_name` is split on `.`. The search list is `[last_part]`; if there are multiple parts, `first_part` is appended as a fallback.

**Stage 3 — BFS definition search**
For each candidate name, `_find_definition_node` performs a breadth-first traversal of the AST. At each node, if the node's type is one of `{"identifier", "type_identifier", "namespace_identifier"}` and its decoded text matches the candidate name, and the node is not inside an import statement (checked by `_is_inside_import`), the node's **parent** is returned immediately.

**Stage 4 — Source extraction**
The parent node's `.text` bytes are decoded to UTF-8 and returned as the definition source string. If no candidate name yields a match, `None` is returned.

---

## 3. Outputs

**`build_project_dependencies`**
Returns `list[dict]` — one dict per supported source file in the project. Paths are in `"project_name/{copy_path}"` format. No files are written; no side effects beyond file reads and parse caching inside `parse_file`.

**`extract_callee_source`**
Returns `str | None` — the UTF-8 source text of the matched definition node (function, class, assignment, etc.), or `None` if no definition is found.

---

## 4. Key Data Structures

### `file_info_list` entry (output of `build_project_dependencies`)

| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | Copy-destination path of this file, prefixed with `project_name/` |
| `"callers"` | `list[str]` | Copy-destination paths of files that import this file |
| `"callees"` | `list[str]` | Copy-destination paths of files imported by this file |

---

### `file_callee_map`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | The importing file |
| value | `set[str]` (absolute paths) | All project files imported (directly or implicitly) by the key file |

---

### `file_caller_map`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | A project file |
| value | `list[str]` (absolute paths) | All project files that import the key file |

---

### `dir_ext_groups`

| Field / Key | Type | Purpose |
|---|---|---|
| key | `tuple[str, str]` | `(directory_path, file_extension)` grouping key |
| value | `list[str]` | Absolute paths of all files in that directory with that extension |

---

### `project_file_set`

| Field / Key | Type | Purpose |
|---|---|---|
| elements | `str` | Project-relative POSIX paths (`"src/foo.py"` format) of all supported files; used as membership oracle during import resolution |

---

### `class_patterns` (within same-package augmentation)

| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | File whose class name is being searched for |
| value | `re.Pattern[str]` | Word-boundary regex matching that file's class name (filename stem) |

## Error Handling

# Error Handling

## 1. Overall Strategy

The file follows a **graceful degradation / logging-and-continue** approach. The primary goal is to produce a complete dependency graph even when individual files or import resolutions fail. Unresolvable imports and unreadable files are silently skipped, allowing the overall analysis to proceed with the remaining files. There is no retry logic; failures at the file or symbol level result in that item being omitted from the output rather than aborting the process.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | A same-package file cannot be opened or decoded when reading source text for intra-package reference detection (Step 3.5) | Caught silently via `except (OSError, UnicodeDecodeError): continue` | Yes | That file is skipped for same-package dependency detection; all other files in the group are still processed |
| Unresolvable import module | `resolve_module_to_project_path` returns `None` (module is external, standard library, or not found in `project_file_set`) | The resolved value is checked with `if resolved:` and the import is silently skipped | Yes | That import produces no callee entry; all other imports for the file are still processed |
| Unsupported file extension for import analysis | `get_import_params` returns `(None, None)` for a file extension not covered by `IMPORT_QUERIES` | Guarded by `if language and import_query_str:`, the import extraction block is bypassed entirely | Yes | No callee entries are added for that file; the file still appears in the dependency graph with empty callee list |
| Definition not found in callee file | `_find_definition_node` returns `None` (the searched name is absent from the AST), including the fallback search on the leading name part | Returns `None` from `extract_callee_source` | Yes | The caller receives `None`; no source code snippet is returned for that symbol |
| Import-context false positive | An identifier matching `definition_name` is found inside an import statement rather than a definition | `_is_inside_import` returns `True` and the node is skipped; BFS continues | Yes | The import-context node is ignored; the search continues for a genuine definition node |
| Callee path not in `file_caller_map` | An absolute resolved callee path does not correspond to any tracked project file (e.g., resolved to a path outside the collected file list) | Guarded by `if callee_path in file_caller_map:` before appending | Yes | That callee is not registered as a caller of any file; no crash occurs |

---

## 3. Design Notes

- **No explicit logging on skip events.** When imports fail to resolve or files cannot be read, the module silently continues without emitting log messages. The `logger` object is defined at module level but is not invoked within the error-handling paths present in this file, meaning failures are invisible in logs unless surfaced by a caller.
- **Two-pass name search in `extract_callee_source`.** The split-name fallback (trailing part → leading part) is a design choice to handle both attribute-access patterns (e.g., `helper.process`) and cases where the trailing component is a built-in method (e.g., `TEMPLATE.format`). This avoids a hard failure when the first search attempt yields nothing.
- **In-place `dir_names` mutation for `os.walk`.** Excluding directories matching `EXCLUDE_PATTERNS` by mutating `dir_names[:]` is the only structural guard applied during file collection; any directory not matching a pattern is traversed unconditionally, with no error handling for permission or access failures at the directory level.
- **Dependency on external modules for error propagation.** Errors that could arise inside `parse_file`, `extract_imports`, or `resolve_module_to_project_path` are not caught here; they propagate to the caller. Only errors specific to file I/O during same-package detection are caught locally.

## Summary

**dependency_graph.py**: Builds a project-wide file dependency graph and retrieves callee source code from dependency files.

**Public functions:**
- `build_project_dependencies(project_dir: str) → list[dict]` — returns dicts with `"file"`, `"callers"`, `"callees"` keys (copy-destination paths)
- `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) → str | None` — returns source text of a named definition

**Key data structures:**
- `file_callee_map`: `dict[str, set[str]]` (abs path → callee abs paths)
- `file_caller_map`: `dict[str, list[str]]` (abs path → caller abs paths)
- Output entries use `"project_name/{copy_path}"` formatted strings
