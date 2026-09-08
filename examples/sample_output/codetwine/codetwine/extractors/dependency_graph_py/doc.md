# Design Document: codetwine/extractors/dependency_graph.py

# Design Specification

**Overview**

Builds a project-wide, file-level dependency graph by analyzing import statements (and same-package visibility for Java/Kotlin) across all supported source files, and provides lookup of definition source code for cross-file symbol references.

- A caller needing the full inter-file dependency graph of a project (for documentation generation, knowledge extraction, or pipeline orchestration) calls `build_project_dependencies` to get a list of `{file, callers, callees}` dicts keyed by copy-path.
- A caller that has already identified a cross-file reference (e.g. `helper.process()`) and needs the actual definition source calls `extract_callee_source` with the target file path, symbol name, and project root to get the definition's source text.
- Internal helper `_find_definition_node` is used when a caller needs to locate the AST node that defines a given identifier name, skipping references inside import statements.
- Internal helper `_is_inside_import` is used whenever code needs to distinguish a real definition occurrence of a name from a mere import reference to that name.

This file relies on `ts_parser.parse_file` for cached AST parsing, `extractors.imports.extract_imports` and `import_to_path` (`detect_source_roots`, `resolve_module_to_project_path`, `get_import_params`) to turn raw import statements into resolved project-relative file paths, `config.settings` (`DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, `SAME_PACKAGE_VISIBLE`) to drive which files are analyzed and which languages get implicit same-package linking, and `utils.file_utils.rel_to_copy_path` to format output paths consistently with the copied source layout. It is used by `pipeline.py`, which calls `build_project_dependencies` as the first step of dependency analysis before converting paths to internal representations, and by `extractors/usage_analysis.py`, which calls `extract_callee_source` to fetch definition bodies for cross-file symbol usages during code understanding/knowledge extraction.

Design decisions: definition lookup uses breadth-first search over the AST (not depth-first) to prefer shallower/outer matches; file reads for same-package regex scanning silently skip files on `OSError`/`UnicodeDecodeError` rather than failing the whole build; AST parsing benefits transparently from the LRU cache in `ts_parser`, so repeated lookups of the same file (e.g. multiple callee extractions from one file) avoid re-parsing; excluded directories are pruned in-place during `os.walk` traversal for efficiency rather than filtered after collection.

**Definitions**

## `_is_inside_import`

Walks up the AST from a given node via `.parent` links to determine whether that node sits within an import/include construct (checking node types containing "import" or equal to `preproc_include`), so that identifier occurrences inside import statements are treated as references rather than definitions. Used internally by `_find_definition_node` to filter out false-positive definition matches.

## `_DEFINITION_NAME_NODE_TYPES`

A module-level constant set of AST node type names (`identifier`, `type_identifier`, `namespace_identifier`) representing the tree-sitter node kinds that can carry a definition name across the supported languages (Python/Java/Kotlin/JS identifiers, C/C++/TS type names, C++ namespaces). Used by `_find_definition_node` to filter which nodes are candidates during the BFS scan.

## `_find_definition_node`

Performs a breadth-first search over the AST rooted at `root_node`, looking for a name-bearing node (per `_DEFINITION_NAME_NODE_TYPES`) whose decoded text matches `definition_name`, skipping any match found via `_is_inside_import`, and returns the matching node's parent (expected to be the enclosing definition construct such as a function/class/assignment node). It exists to locate the syntactic container of a named definition without language-specific AST traversal logic, and is used by `extract_callee_source` to resolve a symbol name to its defining construct.

## `extract_callee_source`

Given a target file path (relative to the project), a callee name possibly containing attribute access (e.g. `"helper.process"` or `"TEMPLATE.format"`), and the project root, parses the target file via `parse_file` and returns the full source text of the AST node defining that name, or `None` if not found. It first searches using the trailing part of a dotted name (the actual member being called) and falls back to the leading part (the object/module name) when the trailing part isn't found as a definition, handling cases where the trailing segment is a built-in method rather than a project-defined symbol. Used by usage analysis to retrieve the concrete source of cross-file symbol references for downstream documentation/knowledge building.

## `build_project_dependencies`

Scans the entire project directory tree for files with extensions listed in `DEFINITION_DICTS`, excluding paths matching `EXCLUDE_PATTERNS`, then for each file extracts and resolves import statements (via `get_import_params`, `extract_imports`, `resolve_module_to_project_path`, and detected `source_root_set` from `detect_source_roots`) to build a per-file set of callee files; it additionally adds implicit same-directory callee edges for languages marked `SAME_PACKAGE_VISIBLE` when another file's class name (derived from its filename) appears as a whole-word match in the source text. It then inverts the callee map into a caller map and emits a list of dicts with `file`, `callers`, and `callees` fields, all expressed as `"project_name/copy_path"` strings via `rel_to_copy_path`, giving a serializable, environment-portable dependency graph. This is the primary entry point of the module, intended to run once per project analysis (e.g. from `pipeline.py`) before any per-symbol usage resolution occurs; note that same-package edges are unidirectional string-match heuristics rather than verified symbol resolution.

# Summary

Builds a project-wide, file-level dependency graph from source imports (plus same-package visibility heuristics for Java/Kotlin) and resolves cross-file symbol references to their definition source. Main definitions: `build_project_dependencies` (entry point producing file/callers/callees list), `extract_callee_source` (fetches definition text for a dotted callee name), plus internal helpers `_find_definition_node` (BFS AST search) and `_is_inside_import`. Key terms: import resolution, source roots, AST parsing, definition lookup, dependency graph, copy-path formatting, cross-file symbol usage, documentation/knowledge extraction pipeline support.
