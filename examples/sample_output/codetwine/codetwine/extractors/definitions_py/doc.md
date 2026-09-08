# Design Document: codetwine/extractors/definitions.py

# Design Specification

**Overview**

Extracts named definitions (functions, classes, variables, types, etc.) from a tree-sitter AST via breadth-first traversal and returns them sorted by line number.

- Call `extract_definitions` to obtain a `list[DefinitionInfo]` for a parsed source file, used to build per-definition context snippets (e.g. combined with source line ranges).
- Call `extract_definitions` to get definition names to populate a symbol-to-file map for cross-file import resolution.
- Call `extract_definitions` to collect exported names of a target file when analyzing whether a given usage in one file corresponds to a definition in another file.
- Use `DefinitionInfo` as the shared data structure (name, type, start_line, end_line) passed between the extractor and any caller that needs to filter, group, or display definitions.

This file has no dependencies on other project-internal files; it only takes a `tree_sitter.Node` root and a `definition_dict` supplied by the caller. It is depended upon by `codetwine/file_analyzer.py` (to build definition context blocks for output), `codetwine/import_to_path.py` (to register symbol names per file and to filter top-level definitions via `DefinitionInfo`), and `codetwine/extractors/usage_analysis.py` (to gather candidate definition names from a target file when resolving usages).

Design decisions: name extraction failures are treated as a signal to keep descending into child nodes (BFS fallback) rather than as errors, so forward declarations and unrecognized substructures are not lost but simply searched deeper. Container-type definitions (namespaces, classes, structs, interfaces, enums) are recorded as definitions but their children are still enqueued, allowing nested methods/fields to also be captured. `#define` nodes matching an include-guard naming pattern are deliberately excluded from results while still traversing their children.

**Definitions**

## `DefinitionInfo`
A dataclass holding one extracted definition's `name`, AST `type`, `start_line`, and `end_line` (both 1-based). It is the return-element type of `extract_definitions` and is consumed by dependents such as `file_analyzer.py` (to slice source lines for context) and `import_to_path.py` (to filter and register top-level symbols).

## `extract_definitions`
Performs BFS over the AST starting at `root_node`, using `definition_dict` to decide which node types represent definitions and how to extract their names (`_parse_definition_node`, `_parse_decorated_definition`, or `_extract_destructured_names`). It skips include-guard `preproc_def` nodes (matched via `_INCLUDE_GUARD_RE`) while still queuing their children, continues descending into `_CONTAINER_DEFINITION_TYPES` (namespaces, classes, structs, interfaces, enums, object declarations) after recording them, and falls back to enqueuing child nodes whenever name extraction fails (e.g. forward declarations). Returns the final list sorted ascending by `start_line`; this is the single entry point used by all external callers.

## `_parse_decorated_definition`
Handles `decorated_definition` nodes (e.g. Python `@property`) by locating the inner definition child that appears in `definition_dict`, delegating name extraction to `_parse_definition_node`, and then widening the resulting `DefinitionInfo`'s line range to cover the decorator lines. Returns `None` if no recognized inner definition is found among the children.

## `_parse_definition_node`
Converts a definition node into a `DefinitionInfo` by calling `_extract_name` with the given `name_node_type` and computing 1-based `start_line`/`end_line` from the node's `start_point`/`end_point`. Returns `None` when the name cannot be extracted, signaling the caller to try destructuring extraction or BFS fallback.

## `_extract_name`
Dispatches name extraction based on `name_node_type`: sentinel values (`__assignment__`, `__variable_declarator__`, `__init_declarator__`, `__function_declarator__`, `__declarator_name__`, `__kotlin_property__`) route to their dedicated extractor functions for names nested two or more levels deep; any other value is treated as a standard direct-child node type name and searched for among `node.children`. Returns `None` if no matching name is found.

## `_extract_assignment_name`
Extracts the left-hand identifier name from a Python `expression_statement` wrapping a simple `assignment` (e.g. `X = 1`), used for top-level variable/constant detection. Returns `None` for non-assignment expression statements or when the left-hand side is not a plain `identifier` (e.g. attribute or destructuring targets), leaving those cases to `_extract_destructured_names` or BFS fallback.

## `_extract_variable_declarator_name`
Retrieves the `name` field identifier from the first `variable_declarator` child of a JS/TS `lexical_declaration`/`variable_declaration` or a Java `field_declaration`, used when multiple declarators may exist but only the first name is needed. Returns `None` if no `variable_declarator` is present, e.g. for destructuring patterns handled separately.

## `_extract_function_declarator_name`
Extracts a C/C++ function name from a `function_definition` by reading the `declarator` field's nested `function_declarator` and then its own `declarator` field, handling plain `identifier`/`field_identifier` names as well as C++ `qualified_identifier` method implementations (e.g. `Shape::get_name`) by taking the last `identifier` segment. Returns `None` when the declarator structure doesn't match a function definition.

## `_extract_declarator_name`
Extracts the name directly from a `function_declarator` node's `declarator` field, distinguishing free-function/constructor `identifier` names from class-member `field_identifier` names; used for C/C++ function prototypes reached via BFS fallback after a `function_definition`/`declaration` failed standard extraction. Returns `None` for other declarator kinds such as pointers or operator overloads.

## `_extract_kotlin_property_name`
Extracts the identifier name from the `variable_declaration` child of a Kotlin `property_declaration` (`val`/`var`), applicable to both top-level constants and class-body properties. Returns `None` for destructuring declarations like `val (a, b) = pair`, which lack a plain `identifier`.

## `_extract_init_declarator_name`
Extracts a C/C++ variable/constant name from a `declaration` node's `declarator` field when it is an `init_declarator`, reading the nested `declarator` field's `identifier`. Returns `None` for forward declarations lacking an `init_declarator`, relying on the caller's BFS fallback to instead find a nested `function_declarator`.

## `_extract_destructured_names`
Handles multi-name extraction for destructuring assignments when standard single-name extraction fails: for `__assignment__`, collects identifiers from a Python `pattern_list` (`X, Y = 1, 2`); for `__variable_declarator__`, delegates to `_collect_identifiers_from_pattern` on JS/TS `object_pattern`/`array_pattern` (`const { a, b } = obj`, `const [a, b] = arr`). Returns an empty list for any other sentinel or non-destructuring case, signaling the main BFS loop to descend into children instead.

## `_collect_identifiers_from_pattern`
Recursively walks an `object_pattern`/`array_pattern` node collecting bound variable names, including plain `identifier`s, `shorthand_property_identifier_pattern`s, nested patterns, and the local name from `pair_pattern` (`{ key: localName }`) including nested destructured values. Used by `_extract_destructured_names` to fully resolve nested JS/TS destructuring targets.

# Summary

This file provides tree-sitter based extraction of named definitions (functions, classes, variables, types) from a source AST via breadth-first traversal, returning them sorted by line. Main public definitions: DefinitionInfo (name/type/start_line/end_line dataclass) and extract_definitions (entry point). Handles decorated definitions, container types (namespaces/classes/structs), destructured/multi-name assignments, include-guard exclusion, and language-specific declarators (C/C++, JS/TS, Kotlin, Python). Used by file_analyzer, import_to_path, and usage_analysis for context building, symbol resolution, and cross-file usage matching.
