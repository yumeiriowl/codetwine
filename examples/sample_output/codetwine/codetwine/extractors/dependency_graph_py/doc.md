# Design Document: codetwine/extractors/dependency_graph.py

# Overview & Purpose

## 1. Module Summary

Analyze inter-file dependencies across an entire project by parsing import statements and same-package references, then expose utilities to extract the source code of a named definition from any dependency file.

## 2. When to Use This Module

- **Building a project-wide dependency graph**: Call `build_project_dependencies(project_dir)` to obtain a list of dicts describing every supported-language file in the project together with its callers and callees, expressed as `project_name/copy_path`-formatted paths. This is the entry point used by `codetwine/pipeline.py` at the start of the analysis pipeline.
- **Retrieving a definition's source code from a dependency file**: Call `extract_callee_source(callee_file_path, callee_name, project_dir)` to look up a named symbol (function, class, variable, etc.) in a specific file and return its full source text. This is used by `codetwine/extractors/usage_analysis.py` when it needs to inline the implementation of a callee symbol during usage analysis.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parse the target file's AST and return the full source text of the node that defines `callee_name`; returns `None` if the definition is not found. Handles dotted names (e.g. `helper.process`) by trying the trailing part first, then the leading part. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walk the project directory, resolve all import-based and same-package dependencies for every supported-language file, and return a list of `{"file": str, "callers": list[str], "callees": list[str]}` dicts with paths in `project_name/copy_path` format. |

## 4. Design Decisions

- **Same-package implicit dependency detection (Java/Kotlin)**: Because Java and Kotlin allow classes in the same directory to be referenced without an explicit import, `build_project_dependencies` supplements the import-based callee map with a regex-based scan: for each group of files sharing a directory and extension where `SAME_PACKAGE_VISIBLE` is true, it checks whether any file's source text contains another file's class name (bare filename stem) as a whole word, and if so adds a unidirectional dependency edge.
- **BFS AST traversal for definition lookup**: `extract_callee_source` uses breadth-first search over the tree-sitter AST (via `_find_definition_node`) rather than a targeted query, so it can locate a definition node regardless of the nesting depth or the specific grammar construct used to declare it.
- **Import nodes are explicitly excluded from definition search**: During BFS, any identifier node whose ancestors include an import-related node type is skipped, preventing import statements from being mistaken for definitions.
- **Parse result reuse via ts_parser cache**: Both `extract_callee_source` and `build_project_dependencies` call `parse_file`, which maintains a module-level cache keyed by absolute path, so each file is parsed at most once even when accessed from multiple call sites.
- **`copy_path` path format throughout**: All paths in the returned dependency graph use the `project_name/{parent}/{stem}_{ext}/{filename}` format produced by `rel_to_copy_path`, matching the on-disk layout of the output directory so paths remain valid if the output folder is moved.

# Definition Design Specifications

---

## Module-Level Constants

### `_DEFINITION_NAME_NODE_TYPES`

| Value | Type |
|---|---|
| `{"identifier", "type_identifier", "namespace_identifier"}` | `set[str]` |

The set of tree-sitter node type strings considered as definition name nodes during AST search. Used as a fast membership check inside `_find_definition_node`.

---

## Functions

---

### `_is_inside_import`

**Signature:** `_is_inside_import(node) -> bool`

- **`node`**: A tree-sitter `Node` object at any depth in an AST.
- **Returns:** `True` if any ancestor of `node` is an import or include statement node; `False` otherwise.

**Responsibility:** Guards against misidentifying import-statement references as definition sites by walking the ancestor chain of a candidate node.

**When to use:** Called internally by `_find_definition_node` before accepting a matched identifier as a definition.

**Design decisions:**
- Traverses upward via `node.parent` rather than inspecting the subtree, because the relationship between an identifier and its enclosing import context is strictly vertical in the AST.
- Detection is string-based: any ancestor node type containing the substring `"import"`, or exactly matching `"preproc_include"`, is treated as an import context. This covers Python (`import_statement`, `import_from_statement`), JavaScript/TypeScript (`import_declaration`), Java/Kotlin, and C/C++ preprocessor includes in a single check.

**Constraints & edge cases:**
- Relies on tree-sitter's `parent` pointer being populated, which is guaranteed when parsing with tree-sitter but may not hold for synthetic nodes.

---

### `_find_definition_node`

**Signature:** `_find_definition_node(root_node, definition_name: str) -> Node | None`

- **`root_node`**: The tree-sitter AST root node covering an entire source file.
- **`definition_name`**: Plain string name of the definition to locate (e.g. `"parse_file"`, `"Point"`).
- **Returns:** The *parent* node of the matched name node (i.e., the containing definition construct such as `function_definition` or `class_definition`). `None` if no matching definition is found.

**Responsibility:** Locates the AST node that represents a named definition within a file, skipping any occurrences that appear inside import statements.

**When to use:** Called by `extract_callee_source` to find the AST subtree for a specific symbol before extracting its source text.

**Design decisions:**
- Uses BFS (breadth-first search) rather than DFS so that top-level definitions are encountered before nested ones, reducing the chance of returning an inner shadowed definition.
- The queue stores `(node, parent)` pairs so the parent node is immediately available when a match is found, avoiding a second upward traversal.
- Only node types in `_DEFINITION_NAME_NODE_TYPES` (`identifier`, `type_identifier`, `namespace_identifier`) are inspected for name matching, which limits unnecessary string comparisons to relevant nodes.
- Import-context filtering is delegated to `_is_inside_import`.

**Constraints & edge cases:**
- Returns the *first* BFS match; if the same name is defined more than once in a file, only the earliest breadth-level occurrence is returned.
- Name matching is exact and case-sensitive.
- If the definition name appears only inside imports and nowhere else, returns `None`.

---

### `extract_callee_source`

**Signature:** `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) -> str | None`

- **`callee_file_path`**: Project-relative path to the file containing the definition (e.g. `"src/foo.py"`).
- **`callee_name`**: Possibly dotted name of the definition to retrieve (e.g. `"parse_file"` or `"helper.process"` or `"TEMPLATE.format"`).
- **`project_dir`**: Absolute path to the project root directory.
- **Returns:** The source code string of the matched definition node. `None` if no definition is found.

**Responsibility:** Extracts the full source text of a named definition from a dependency file, enabling callers to embed that definition's code in analysis output.

**When to use:** Called by `usage_analysis.py` when the first use of a project-internal symbol is encountered and its source code needs to be retrieved.

**Design decisions:**
- For dotted names, two search strategies are attempted in order:
  1. The trailing component (e.g. `"process"` from `"helper.process"`) — covers the common case of attribute access on a module object where the trailing part names a function or class.
  2. The leading component (e.g. `"helper"` or `"TEMPLATE"`) — covers cases where the trailing part is a built-in method called on a project-defined object.
- Reuses `parse_file`'s module-level cache, so repeated calls for the same file do not re-read or re-parse it.
- Absolute path is constructed by joining `project_dir` and `callee_file_path`, assuming `callee_file_path` is always relative to `project_dir`.

**Constraints & edge cases:**
- Only the first BFS match for each search name is returned (inherited from `_find_definition_node`).
- If the dotted name has more than two components, only the last and first parts are tried; intermediate components are ignored.
- Non-dotted names produce a single-element search list; no fallback is attempted.
- Returns `None` if neither search name is found in the AST.

---

### `build_project_dependencies`

**Signature:** `build_project_dependencies(project_dir: str) -> list[dict]`

- **`project_dir`**: Absolute path to the root directory of the project to analyze.
- **Returns:** A list of dicts, each with the following shape:

| Key | Type | Description |
|---|---|---|
| `"file"` | `str` | `"project_name/copy_path"` identifier for this file |
| `"callers"` | `list[str]` | Files that import or reference this file |
| `"callees"` | `list[str]` | Files imported or referenced by this file |

All path strings use the `"project_name/copy_path"` format produced by `rel_to_copy_path`.

**Responsibility:** Performs whole-project static dependency analysis and produces an in-memory graph capturing which files import which other files, to be consumed downstream by the pipeline.

**When to use:** Called once per pipeline run by `pipeline.py` as the first step of project analysis.

**Design decisions:**

- **File discovery** respects `EXCLUDE_PATTERNS` for both directory names and file names using `fnmatch`, pruning `os.walk` in-place for directories to avoid descending into excluded subtrees.
- **Import-based edges** are built by parsing each file's import statements with `extract_imports` and resolving module names to project-relative paths via `resolve_module_to_project_path`, which handles relative imports, source-root prefixes (e.g. `src/main/java/`), and extension inference.
- **Same-package implicit edges** (Java/Kotlin): files in the same directory with the same extension are grouped. For each file, a plain-text regex search checks whether another file's class name (basename without extension) appears in the source. A unidirectional edge is added only when such a reference is found. This is controlled by the `SAME_PACKAGE_VISIBLE` configuration flag per extension.
- **Caller map** is derived as a reverse index of the callee map, avoiding a second traversal of the file system.
- Absolute paths are used as internal dict keys throughout to avoid platform-specific path normalization issues; conversion to relative and then copy-path format is deferred to the final output step.
- `project_name` is derived from `os.path.basename(project_dir)` and prepended to all output paths.

**Constraints & edge cases:**
- Only files whose extension (without leading `.`) appears in `DEFINITION_DICTS.keys()` are included.
- Files that fail to parse are silently skipped at the import-extraction stage (handled within `extract_imports` / `parse_file`).
- Same-package files that cannot be read (`OSError`, `UnicodeDecodeError`) are silently skipped during the regex-search phase.
- The same-package edge is unidirectional: it is added only from the referencing file to the referenced file, not vice versa, based on presence of the class name in source text.
- `callee_set` values are absolute paths; files not present in `file_caller_map` (i.e., external dependencies) are silently excluded from the caller index.
- Path separators are normalized to `/` throughout to ensure consistent behavior on Windows.

# Dependency Description

### Dependencies (modules this file imports)

- `codetwine/extractors/dependency_graph.py` → `codetwine/parsers/ts_parser.py` : Uses `parse_file` to parse source files into tree-sitter ASTs. Required both for scanning import statements during dependency graph construction and for searching AST nodes when extracting callee source code definitions.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to parse import statements from an AST root node into structured `ImportInfo` objects, enabling resolution of which project files each file depends on.

- `codetwine/extractors/dependency_graph.py` → `codetwine/import_to_path.py` : Uses three symbols:
  - `detect_source_roots` to identify source root prefixes (e.g. `src/main/java/`) present in the project file set.
  - `resolve_module_to_project_path` to convert a raw module string from an import statement into a concrete project-relative file path.
  - `get_import_params` to retrieve the tree-sitter `Language` object and import query string appropriate for a given file extension.

- `codetwine/extractors/dependency_graph.py` → `codetwine/utils/file_utils.py` : Uses `rel_to_copy_path` to convert project-relative file paths into the `{parent_dir}/{stem}_{ext}/{filename}` copy-destination path format used in the final output.

- `codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py` : Uses three configuration constants:
  - `DEFINITION_DICTS` (specifically its `.keys()`) to determine the set of supported file extensions for file collection.
  - `EXCLUDE_PATTERNS` to filter out directories and files that should be skipped during filesystem traversal.
  - `SAME_PACKAGE_VISIBLE` to determine which file extensions (Java/Kotlin) require same-package implicit dependency detection.

---

### Dependents (modules that import this file)

- `codetwine/pipeline.py` → `codetwine/extractors/dependency_graph.py` : Uses `build_project_dependencies` as the first step of the pipeline to construct the project-wide dependency graph. The returned list of file dependency dicts drives the enumeration of all project files and their caller/callee relationships for subsequent processing.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/dependency_graph.py` : Uses `extract_callee_source` to retrieve the source code of a named definition from a dependency target file, used during usage analysis to fetch the definition text for symbols referenced across file boundaries.

---

### Dependency Direction

All relationships are **unidirectional**:

- `dependency_graph.py` → `ts_parser.py`: one-way; `ts_parser.py` has no knowledge of `dependency_graph.py`.
- `dependency_graph.py` → `imports.py`: one-way; `imports.py` has no knowledge of `dependency_graph.py`.
- `dependency_graph.py` → `import_to_path.py`: one-way; `import_to_path.py` has no knowledge of `dependency_graph.py`.
- `dependency_graph.py` → `file_utils.py`: one-way; `file_utils.py` has no knowledge of `dependency_graph.py`.
- `dependency_graph.py` → `settings.py`: one-way; `settings.py` has no knowledge of `dependency_graph.py`.
- `pipeline.py` → `dependency_graph.py`: one-way; `dependency_graph.py` has no knowledge of `pipeline.py`.
- `usage_analysis.py` → `dependency_graph.py`: one-way; `dependency_graph.py` has no knowledge of `usage_analysis.py`.

# Data Flow

## 1. Inputs

### `build_project_dependencies(project_dir: str)`
- **`project_dir`**: Absolute path string to the project root directory, used as the base for all file discovery and path resolution.
- **Config values**: `DEFINITION_DICTS` (supplies the set of supported file extensions), `EXCLUDE_PATTERNS` (glob patterns for directories and files to skip), `SAME_PACKAGE_VISIBLE` (per-extension flag indicating whether same-directory files are implicitly visible).
- **File system**: All files under `project_dir` encountered via `os.walk`.
- **File contents**: Raw source bytes read by `parse_file` for AST construction, and raw UTF-8 text read directly for same-package class-name pattern matching.

### `extract_callee_source(callee_file_path, callee_name, project_dir)`
- **`callee_file_path`**: Project-relative path to the file containing the target definition.
- **`callee_name`**: A dotted name string (e.g., `"helper.process"` or `"TEMPLATE.format"`) identifying the definition to locate.
- **`project_dir`**: Absolute project root, combined with `callee_file_path` to form the absolute path passed to `parse_file`.
- **File contents**: Raw source bytes read by `parse_file` (cached at the `ts_parser` module level).

---

## 2. Transformation Overview

### `build_project_dependencies`

**Stage 1 — File Discovery**
`os.walk` traverses `project_dir`, pruning subtrees whose directory names match any `EXCLUDE_PATTERNS` glob. Individual files matching exclude patterns or lacking a supported extension (from `DEFINITION_DICTS.keys()`) are also discarded. The surviving absolute paths are collected into `all_file_list`.

**Stage 2 — Project File Set and Source Root Detection**
Each absolute path is converted to a project-relative POSIX string and inserted into `project_file_set`. This set is passed to `detect_source_roots`, which matches it against known patterns (e.g., `"src/main/java/"`) to produce `source_root_set`. Both structures are used downstream for import resolution.

**Stage 3 — Explicit Callee Resolution (Import Analysis)**
For each file in `all_file_list`, `get_import_params` maps the file extension to a `(Language, import_query_str)` pair. If the extension is supported, `parse_file` produces an AST, and `extract_imports` extracts `ImportInfo` objects. Each `ImportInfo.module` string is fed to `resolve_module_to_project_path`, which attempts to match it against `project_file_set` (with `source_root_set` fallback). Successfully resolved paths are collected into `callee_set` and stored in `file_callee_map` keyed by absolute path.

**Stage 4 — Implicit Callee Resolution (Same-Package Visibility)**
Files whose extension appears in `SAME_PACKAGE_VISIBLE` are grouped by `(directory, extension)` into `dir_ext_groups`. For each group, every file's base name (without extension) is compiled into a word-boundary regex. Each file's raw UTF-8 source is then searched for the class names of its sibling files; a match causes the sibling's absolute path to be added to that file's `callee_set` in `file_callee_map`.

**Stage 5 — Caller Index Construction (Reverse Map)**
`file_caller_map` is initialized with an empty list for every file in `all_file_list`. The `file_callee_map` is iterated: for each `(caller, callees)` pair, the caller's absolute path is appended to `file_caller_map[callee]` (only for callees that are themselves project files).

**Stage 6 — Path Formatting and Output Assembly**
For each file, absolute paths in both maps are converted to project-relative POSIX strings. Those relative strings are passed through `rel_to_copy_path` and prefixed with `project_name/` to produce the final `"project_name/copy_path"` format. The result is assembled into a list of dicts.

---

### `extract_callee_source`

**Stage 1 — AST Acquisition**
The absolute file path is constructed and passed to `parse_file`, returning the cached or freshly parsed AST root node.

**Stage 2 — Search Name Derivation**
`callee_name` is split on `"."`. The trailing component becomes the primary search name; if there are multiple components, the leading component becomes a fallback search name.

**Stage 3 — BFS Definition Search**
`_find_definition_node` performs a breadth-first traversal of the AST, visiting each node and comparing its text against the search name when its type is one of `identifier`, `type_identifier`, or `namespace_identifier`. Nodes inside import statements (detected by ancestor-type inspection in `_is_inside_import`) are skipped. The first matching node's parent is returned.

**Stage 4 — Source Extraction**
The matched parent node's `text` bytes are decoded to UTF-8 and returned as the definition's source string. If neither search name yields a match, `None` is returned.

---

## 3. Outputs

### `build_project_dependencies`
Returns `list[dict]`. Each dict has three keys: `"file"`, `"callers"`, and `"callees"`, all carrying paths in `"project_name/copy_path"` format. No files are written and no side effects occur beyond the `parse_file` cache being populated.

### `extract_callee_source`
Returns `str | None`. The string is the raw UTF-8 source text of the matched definition's parent AST node. Returns `None` when no definition is found for either search name.

---

## 4. Key Data Structures

### `all_file_list`
| Field / Key | Type | Purpose |
|---|---|---|
| *(elements)* | `str` | Absolute filesystem paths of all discovered project files with supported extensions |

---

### `project_file_set`
| Field / Key | Type | Purpose |
|---|---|---|
| *(elements)* | `str` | Project-relative POSIX paths (e.g., `"src/foo.py"`) used for import resolution lookups |

---

### `file_callee_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | The importing/referencing file |
| value | `set[str]` (absolute paths) | All project-internal files that this file depends on, populated by both import analysis and same-package matching |

---

### `file_caller_map`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `str` (absolute path) | A project file that may be depended upon |
| value | `list[str]` (absolute paths) | All project files that import or reference this file |

---

### `dir_ext_groups`
| Field / Key | Type | Purpose |
|---|---|---|
| key | `tuple[str, str]` | `(directory_path, extension)` identifying a same-package group |
| value | `list[str]` | Absolute paths of all files in that directory sharing the extension |

---

### Output dict (element of `file_info_list`)
| Field / Key | Type | Purpose |
|---|---|---|
| `"file"` | `str` | `"project_name/copy_path"` formatted path of this file |
| `"callers"` | `list[str]` | `"project_name/copy_path"` paths of all files that depend on this file |
| `"callees"` | `list[str]` | `"project_name/copy_path"` paths of all files this file depends on |

---

### `search_names` (inside `extract_callee_source`)
| Field / Key | Type | Purpose |
|---|---|---|
| `[0]` | `str` | Trailing component of `callee_name` (primary search target, e.g., `"process"`) |
| `[1]` (optional) | `str` | Leading component of `callee_name` (fallback when trailing part not found, e.g., `"TEMPLATE"`) |

# Error Handling

## 1. Overall Strategy

The file adopts a **graceful degradation / logging-and-continue** approach. The primary concern is maintaining forward progress across an entire project scan: individual failures (unreadable files, unresolvable imports, unrecognised definitions) result in the affected item being silently skipped rather than aborting the overall operation. There is no retry mechanism; fallback paths are used where multiple search strategies exist (e.g., searching by trailing then leading name component in `extract_callee_source`).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `OSError` / `UnicodeDecodeError` | Opening a same-package file for text reading (Step 3.5) fails | Caught; the file is silently skipped via `continue` | Yes | That file's same-package callee relationships are not added; all other files processed normally |
| Unresolvable import | `resolve_module_to_project_path` returns `None` for an import | Result is ignored; the import is not added to `callee_set` | Yes | The dependency edge for that import is omitted from the graph |
| Definition not found (trailing part) | `_find_definition_node` returns `None` for the last name component in `extract_callee_source` | Fallback: re-search using the leading name component (e.g., `TEMPLATE` instead of `format`) | Yes (one retry) | If neither search succeeds, `None` is returned to the caller |
| Definition not found (both parts) | Neither the trailing nor leading part of `callee_name` matches any AST node | Returns `None` | Yes (caller decides) | Caller receives `None`; source code for that callee is unavailable |
| Unsupported file extension for import analysis | `get_import_params` returns `(None, None)` for a file's extension | Import analysis block is skipped entirely (`if language and import_query_str:` guard) | Yes | No callee edges derived from imports for that file; file still appears in the graph with empty callees |
| Node inside import statement | An AST identifier matching the definition name is found but is inside an import/include node | `_is_inside_import` returns `True`; BFS continues searching for a non-import occurrence | Yes | Definition search continues; first non-import match is returned |
| File not in `file_caller_map` | A callee path resolved from imports does not correspond to a file in `all_file_list` | Guard `if callee_path in file_caller_map` prevents the reverse-edge insertion | Yes | Caller entry is not added for that path; no crash |

---

## 3. Design Notes

- **No explicit logging at error sites**: The file imports `logging` and defines a module-level `logger`, but none of the error handling paths in the current implementation emit log messages. Errors are absorbed silently through control flow (`continue`, guard clauses, `None` returns) rather than through log output.
- **`None`-as-sentinel contract**: Functions such as `extract_callee_source` and `resolve_module_to_project_path` communicate "not found" or "not applicable" via `None` return values, placing the responsibility for deciding impact on the caller.
- **BFS fallback in definition search**: The two-step name search (trailing part first, then leading part) in `extract_callee_source` is a deliberate fallback strategy to handle both attribute-access patterns (`helper.process`) and built-in-method patterns (`TEMPLATE.format`), rather than a general error recovery mechanism.
- **Defensive file-existence check**: Before appending a reverse (caller) edge, the code verifies the callee path exists in `file_caller_map`, preventing `KeyError` from files that were resolved via imports but were not collected during the initial directory walk (e.g., files outside supported extensions or matching exclude patterns).

# Summary

**dependency_graph.py** builds a project-wide import dependency graph and extracts named definitions from source files.

**Public functions:**
- `build_project_dependencies(project_dir: str) → list[dict]` — returns `{"file": str, "callers": list[str], "callees": list[str]}` dicts with `project_name/copy_path`-formatted paths
- `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) → str | None` — returns source text of a named definition via BFS AST search

**Key structures:** `file_callee_map` (`dict[str, set[str]]`), `file_caller_map` (`dict[str, list[str]]`), both keyed by absolute path internally.
