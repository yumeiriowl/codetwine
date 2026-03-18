# Design Document: codetwine/extractors/dependency_graph.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibilities

`dependency_graph.py` implements the **inter-file dependency analysis** subsystem for the CodeTwine pipeline. It exists as a dedicated module to encapsulate the two distinct but related responsibilities of (1) building a project-wide directed dependency graph from import statements and same-package visibility rules, and (2) retrieving the source code of a specific named definition from a dependency target file.

Within the pipeline, `build_project_dependencies` is called first to establish which files depend on which others (callers/callees), and `extract_callee_source` is called later during usage analysis to fetch the actual definition text for a named symbol. Separating these concerns into this module keeps the AST traversal and dependency resolution logic out of the orchestration layer (`pipeline.py`) and the usage analysis layer (`usage_analysis.py`).

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `extract_callee_source` | `callee_file_path: str`, `callee_name: str`, `project_dir: str` | `str \| None` | Parse the target file's AST, locate the definition node matching `callee_name` (with fallback from trailing to leading dot-separated part), and return its full source text. |
| `build_project_dependencies` | `project_dir: str` | `list[dict]` | Walk all supported-extension files in the project, resolve their imports to project-internal file paths, add same-package implicit dependencies for Java/Kotlin, build a caller/callee graph, and return it as a list of dicts with `"file"`, `"callers"`, and `"callees"` keys using `project_name/copy_path` format. |

---

## Internal Helpers (non-public)

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_is_inside_import` | `node` | `bool` | Walk a node's ancestor chain to detect whether it is nested inside an import/include statement. |
| `_find_definition_node` | `root_node`, `definition_name: str` | node or `None` | BFS over the AST to find the parent node of the first non-import identifier, type identifier, or namespace identifier matching `definition_name`. |

---

## Design Decisions

- **BFS for AST traversal**: Both `_find_definition_node` (called from `extract_callee_source`) and the description in the docstring explicitly use breadth-first search with a `deque`, ensuring the shallowest (outermost) definition is found first rather than a deeply nested one.

- **Dot-separated name fallback**: `extract_callee_source` splits `callee_name` on `"."` and tries the trailing part first (for attribute access like `helper.process`), then falls back to the leading part (for cases like `TEMPLATE.format` where the leading identifier is the actual project-defined symbol).

- **Import exclusion during definition search**: `_is_inside_import` prevents `_find_definition_node` from matching identifiers that appear only in import statements, ensuring only true definition sites are returned.

- **Same-package visibility (Java/Kotlin)**: Beyond explicit imports, `build_project_dependencies` adds implicit callee edges when a file's source text contains a word-boundary match for another file's class name (filename stem) within the same directory and extension group, controlled by the `SAME_PACKAGE_VISIBLE` configuration flag per extension.

- **Parse cache reuse**: `parse_file` (from `ts_parser.py`) maintains a module-level cache keyed by absolute path; `dependency_graph.py` calls it without any additional caching, relying entirely on that shared cache to avoid redundant disk reads and re-parses across the analysis steps.

- **`copy_path` output format**: All file paths in the returned dependency graph use the `project_name/copy_path` encoding (via `rel_to_copy_path`), matching the physical directory structure of the output folder so that paths remain valid when the output is moved across environments.

## Definition Design Specifications

# Definition Design Specifications

---

## `_is_inside_import(node) -> bool`

**Arguments:**
- `node`: A tree-sitter AST node whose ancestor chain is to be inspected.

**Returns:** `True` if the node resides within an import or include statement; `False` otherwise.

**Responsibility:** Guards definition searches from false-positive matches on identifiers that appear in import statements rather than actual definitions. Without this filter, a `from foo import bar` would incorrectly report `bar` as a definition site.

**Design decision:** Traversal follows `node.parent` links upward rather than querying the tree downward, because any ancestor containing "import" in its type name (or being a `preproc_include`) unambiguously indicates an import context. The substring check on `node_type` handles the variety of import node type names across languages (`import_statement`, `import_from_statement`, `import_declaration`, etc.) without requiring per-language branching.

---

## `_find_definition_node(root_node, definition_name: str)`

**Arguments:**
- `root_node`: The AST root node for an entire file.
- `definition_name` (`str`): The bare identifier to locate (e.g. `"parse_file"`, `"Point"`).

**Returns:** The parent node of the matched identifier node, or `None` if no definition is found.

**Responsibility:** Locates the AST subtree (function definition, class definition, assignment, etc.) that declares a given name, so its full source text can be extracted.

**Design decision:** BFS is used rather than DFS so that top-level definitions—which are the most likely targets—are reached before nested ones. The search restricts candidate node types to `identifier`, `type_identifier`, and `namespace_identifier` to avoid matching string literals or comments. The parent node (not the identifier node itself) is returned because the parent represents the full declaration whose `.text` yields the complete source.

**Edge cases:** Nodes inside import statements are skipped via `_is_inside_import`. If the same name appears in multiple non-import positions, the first one encountered in BFS order is returned.

---

## `extract_callee_source(callee_file_path: str, callee_name: str, project_dir: str) -> str | None`

**Arguments:**
- `callee_file_path` (`str`): Path of the file to search, relative to the project root (e.g. `"src/foo.py"`).
- `callee_name` (`str`): The name to look up; may be a dotted attribute reference (e.g. `"helper.process"` or `"TEMPLATE.format"`).
- `project_dir` (`str`): Absolute path to the project root.

**Returns:** The full source text of the matched definition node, or `None` if no definition is found.

**Responsibility:** Retrieves the verbatim source code of a named definition from a dependency file, enabling downstream consumers to embed dependency source in analysis output.

**Design decision:** For dotted names, the function tries the trailing component first (handles `helper.process` → look up `process`), then falls back to the leading component (handles `TEMPLATE.format` where `format` is a built-in and `TEMPLATE` is the real definition). This two-attempt heuristic avoids requiring callers to pre-classify dotted names. Parsing is delegated to `parse_file`, whose module-level cache prevents redundant disk reads across repeated calls.

**Edge cases:** Returns `None` when neither the trailing nor the leading component resolves to a definition. Requires `callee_file_path` to be a supported language file parseable by `parse_file`.

---

## `build_project_dependencies(project_dir: str) -> list[dict]`

**Arguments:**
- `project_dir` (`str`): Absolute path to the root directory of the project to analyze.

**Returns:** A list of dicts, each with keys `"file"`, `"callers"`, and `"callees"`. All paths use the `"{project_name}/{copy_path}"` format where `copy_path` follows the `rel_to_copy_path` encoding convention.

**Responsibility:** Produces the complete inter-file dependency graph for a project by combining explicit import resolution with implicit same-package visibility rules, yielding both forward (callee) and reverse (caller) edges for every supported source file.

**Design decision:** Directory traversal respects `EXCLUDE_PATTERNS` by pruning `dir_names` in-place so `os.walk` skips excluded subtrees entirely. Import resolution is language-agnostic: `get_import_params` returns the appropriate tree-sitter language and query string per file extension, and `resolve_module_to_project_path` matches resolved modules against `project_file_set` to filter out standard library and third-party imports. The same-package implicit dependency step (Java/Kotlin) adds unidirectional edges only when a class name from another file in the same directory appears as a word boundary match in the source, using pre-compiled regex patterns per file to minimize repeated compilation overhead. Caller edges are derived by inverting the callee map rather than being tracked independently, ensuring consistency. Output paths use `rel_to_copy_path` to match the copy-destination directory structure used elsewhere in the pipeline.

**Edge cases:** Files that cannot be opened or decoded during the same-package scan are silently skipped. Callee paths that do not correspond to a known project file (e.g. resolved to a path outside the collected set) are excluded from caller index construction. Only files with extensions present in `DEFINITION_DICTS` are included in the graph.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

- **codetwine/parsers/ts_parser.py** (`parse_file`): Used to parse source files into tree-sitter AST root nodes. Called both when extracting callee source code (`extract_callee_source`) and when scanning each project file for import statements during dependency graph construction.

- **codetwine/extractors/imports.py** (`extract_imports`): Used to extract structured import information from a parsed AST. Provides the list of `ImportInfo` objects whose `module` fields are subsequently resolved to project-internal file paths.

- **codetwine/import_to_path.py** (`resolve_module_to_project_path`, `get_import_params`): `get_import_params` supplies the tree-sitter `Language` object and query string required to run import extraction for a given file extension. `resolve_module_to_project_path` converts raw module strings from import statements into matching project-relative file paths, enabling the construction of caller–callee relationships.

- **codetwine/config/settings.py** (`DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE`): `DEFINITION_DICTS` provides the set of supported file extensions used to filter which files are collected during project traversal. `EXCLUDE_PATTERNS` specifies directory and file name patterns to skip during traversal. `SAME_PACKAGE_VISIBLE` identifies languages (Java/Kotlin) where files in the same directory are implicitly visible to each other without explicit imports, driving the same-package dependency inference step.

- **codetwine/utils/file_utils.py** (`rel_to_copy_path`): Used when serializing the final dependency list to convert project-relative paths into the copy-destination directory structure format used throughout the output.

---

### Dependents (what uses this file)

- **codetwine/pipeline.py** (`build_project_dependencies`): Uses this file as the entry point for project-wide static dependency analysis. `pipeline.py` calls `build_project_dependencies` to obtain the full list of file dependency records, then further processes the result by converting paths to internal format and extracting the full file list for downstream pipeline steps. The dependency is unidirectional: `pipeline.py` depends on this file; this file has no knowledge of `pipeline.py`.

- **codetwine/extractors/usage_analysis.py** (`extract_callee_source`): Uses `extract_callee_source` to retrieve the source code of a specific named definition from a resolved dependency file. This is called during usage analysis when the definition of a referenced symbol needs to be located and returned as a string. The dependency is unidirectional: `usage_analysis.py` depends on this file; this file has no knowledge of `usage_analysis.py`.

## Data Flow

# Data Flow

## Inputs

| Source | Type | Description |
|---|---|---|
| `project_dir` (arg to `build_project_dependencies`) | `str` | Absolute path to the project root; drives all file discovery and resolution |
| `callee_file_path`, `callee_name`, `project_dir` (args to `extract_callee_source`) | `str` | Identifies a specific definition to retrieve from a single file |
| Filesystem | files | Source files read by `parse_file` and `open()` for same-package text scan |
| `DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE` | config dicts | Control which file extensions are supported, which paths are excluded, and which languages need same-package analysis |

---

## Main Data Structures

### `file_callee_map: dict[str, set[str]]`
Maps each file's **absolute path** → set of absolute paths of files it imports (callees).  
Built from parsed import statements, then augmented with same-package references.

### `file_caller_map: dict[str, list[str]]`
Reverse index: absolute path → list of absolute paths of files that import it (callers).  
Derived by inverting `file_callee_map`.

### `dir_ext_groups: dict[tuple[str, str], list[str]]`
Groups files by `(directory, extension)` for same-package visibility analysis (Java/Kotlin).

### Output element (one per file)
```
{
  "file":    "<project_name>/<copy_path>",
  "callers": ["<project_name>/<copy_path>", ...],
  "callees": ["<project_name>/<copy_path>", ...],
}
```

---

## Transformation Flow: `build_project_dependencies`

```
Filesystem (os.walk)
        │
        ▼
all_file_list: list[str]          # absolute paths of all supported-extension files
        │
        ▼
project_file_set: set[str]        # relative paths ("dir/file.ext") for import resolution
        │
        ├──► per file: parse_file() → AST
        │              extract_imports() → list[ImportInfo]
        │              resolve_module_to_project_path() → relative path or None
        │
        ▼
file_callee_map: dict[str, set[str]]   # absolute_path → {absolute callee paths}
        │
        ├──► same-package augmentation (SAME_PACKAGE_VISIBLE langs only):
        │    read source text, regex-search for class names of sibling files
        │    → add matched sibling abs_paths to file_callee_map[abs_path]
        │
        ▼
file_caller_map: dict[str, list[str]]  # invert file_callee_map
        │
        ▼
file_info_list: list[dict]
  abs_path → relpath → rel_to_copy_path() → "<project_name>/copy_path" strings
```

---

## Transformation Flow: `extract_callee_source`

```
callee_file_path + project_dir
        │
        ▼
parse_file(absolute_path) → AST root node
        │
        ▼
_find_definition_node(root, name) via BFS
  └─ skips nodes inside import statements (_is_inside_import)
  └─ tries parts[-1] first, then parts[0] for dotted names (e.g. "helper.process")
        │
        ▼
parent_node.text.decode("utf-8")   # source code string of the definition, or None
```

---

## Output

| Function | Return type | Destination |
|---|---|---|
| `build_project_dependencies` | `list[dict]` with `file`, `callers`, `callees` string fields (copy-path format) | `pipeline.py` → `_convert_dep_list_to_internal_paths` |
| `extract_callee_source` | `str` (definition source) or `None` | `usage_analysis.py` for inline source enrichment |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **mixed strategy**: fail-fast for core parsing and AST operations, and graceful degradation for file I/O during the same-package visibility analysis. The primary design intent is to keep the dependency graph construction as complete as possible even when individual files are unreadable, while allowing unrecoverable errors (e.g., missing or unparseable source files) to propagate naturally to callers.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| File read/decode failure during same-package visibility scan (`OSError`, `UnicodeDecodeError`) | Caught explicitly; the offending file is silently skipped via `continue` | Only the affected file's same-package callee edges are omitted; the rest of the graph is unaffected |
| Module not resolvable to a project path | `resolve_module_to_project_path` returns `None`; the result is silently skipped | The unresolvable import is omitted from the callee set; no error is raised |
| Definition name not found in the target file's AST | `extract_callee_source` returns `None` | The caller receives `None` and is responsible for handling the absence |
| File parsing failure inside `parse_file` | Not caught here; exceptions propagate to the caller (fail-fast) | Processing of the affected file or the entire `build_project_dependencies` call may abort |
| Callee path not present in `file_caller_map` | Guarded by `if callee_path in file_caller_map` before appending | Callees outside the collected file set are silently ignored in the reverse index |

---

## Design Considerations

The explicit exception catch around file reading in the same-package analysis is intentional and isolated: that code path reads raw text outside the normal tree-sitter parsing pipeline, making encoding errors realistically possible for source files in mixed-encoding projects. All other processing relies on `parse_file`, whose fail-fast behavior is a deliberate contract documented in `ts_parser.py`—exceptions there are expected to propagate to the pipeline caller rather than be suppressed locally. This separation keeps error visibility high for structural failures while tolerating peripheral I/O issues that would otherwise discard large portions of a valid dependency graph.

## Summary

`dependency_graph.py` handles inter-file dependency analysis. It exposes two public functions: `build_project_dependencies(project_dir)` walks all supported source files, resolves imports to project-internal paths, adds implicit same-package edges for Java/Kotlin, and returns a caller/callee graph as `list[dict]` with `file`, `callers`, and `callees` fields in `project_name/copy_path` format. `extract_callee_source(callee_file_path, callee_name, project_dir)` parses a target file and returns the source text of a named definition (with dotted-name fallback), or `None`. Key internal structures are `file_callee_map` (absolute path → callee paths) and `file_caller_map` (its inverse).
