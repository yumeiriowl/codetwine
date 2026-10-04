# Design Document: codetwine/extractors/definitions.py

# Design Specification

**Overview**

Extract definitions (functions, classes, variables, types, etc.) from source code abstract syntax trees and return them as structured metadata ordered by line number.

- Call `extract_definitions()` with a tree-sitter AST root node and a language-specific definition dictionary to obtain a list of `DefinitionInfo` objects representing all definitions in a file, including their names, types, line ranges, and byte offsets.
- Call `select_top_level_definitions()` to filter a definition list, removing nested members of classes, impl blocks, and traits while preserving definitions in open containers (namespaces, enums) and those in `BARE_NAME_DEFINITION_TYPE_SET` (COBOL definitions).
- Call `definition_name_list()` with an AST node and definition dictionary to extract the one or more names that a single definition node declares.
- Call `pattern_name_list()` with a binding pattern node to extract all names bound by destructuring patterns in JavaScript, TypeScript, Python, and similar languages.
- Call utility functions like `commonjs_export_assignment()`, `require_module()`, `declarator_name_node()`, and language-specific extractors to retrieve specific definition properties (exported names, module strings, declarator chains) from expression statements and declarations.

The file depends on configuration settings (`COBOL_DEFINITION_DICT`, `R_DEFINITION_DICT`, `PATTERN_TYPE_SET`, `PATTERN_FIELD_DICT`) and source representation classes (`CobolSource`, `r_definition_list`) to handle multiple language grammars. It serves as the foundation for definition extraction across the codebase, with dependents in definition source caching (`definition_source.py`), import binding (`import_binding.py`), import reference resolution (`import_reference.py`), file analysis output (`file_analyzer.py`), usage analysis (`usage_analysis.py`), usage discovery (`usages.py`), import path mapping (`import_to_path.py`), Rust module tree traversal (`rust_module_tree.py`), and C# type member extraction (`csharp_source.py`).

The implementation uses breadth-first search traversal with a deque to visit AST nodes, skipping into bodies of locally-scoped functions (callbacks, lambdas, closures) only when they are immediately invoked. It continues searching inside container definitions and type nodes after recording them as definitions. For COBOL and R files, definitions are obtained from dedicated source modules rather than tree-sitter parsing. Include-guard `#define` directives are filtered from C/C++ preprocessor definitions by regex pattern matching.

**Definitions**

## `DefinitionInfo`

Dataclass holding metadata for a single definition: its name, AST node type, line range (1-based), and optional COBOL/BMS metadata (name line, level number, group item flag) and byte offsets in UTF-8 source text. Returned by `extract_definitions()` and used by dependents to identify, locate, and classify definitions in source files.

## `extract_definitions`

Main entry point extracting all definitions from a file's AST or COBOL/R source representation, returning them sorted by start line. Handles tree-sitter nodes via breadth-first search with special cases for COBOL and R files, decorated definitions (Python), include guards (C/C++), and container types that permit nested definitions. Continues traversal into type nodes of declarations and into object export entries when applicable.

## `select_top_level_definitions`

Filter a definition list to keep only outermost definitions, removing class/trait/impl members while preserving definitions inside open containers (namespaces, enums) and COBOL-style definitions accessible by bare name from anywhere in the file. Used by dependents to identify the public definition surface of a file.

## `definition_name_list`

Extract the names that an AST definition node declares by looking up the node type in a language-specific definition dictionary. Dispatches to sentinel extractor functions for special patterns (assignments, variable declarators, exports) or searches direct children for a matching name node type. Returns empty when the node is not a definition or no name is found, allowing callers to descend into children.

## `_node_text`

Decode and return the UTF-8 source text of a tree-sitter node.

## `_extent_node`

Return the declaration or field declaration surrounding a definition node when that outer declaration defines no name itself, allowing the definition to be listed with the full extent of its containing declaration (C/C++). Otherwise return the node itself.

## `_line_range`

Convert tree-sitter node positions to 1-based line numbers, handling the edge case where a node ending at the start of a line (e.g., `#define` with trailing newline) ends on the previous line.

## `_is_call_in_place`

Determine whether a function expression or arrow function is immediately called at its location (e.g., `(function() { ... })()` or `(() => { ... })()`), indicating its body's declarations are file-level rather than local.

## `_decorated_inner_node`

Extract the inner function or class definition from a Python decorated definition node, skipping decorator syntax to find the actual definition type.

## `pattern_name_list`

Recursively extract all names bound by a binding pattern (destructuring, parameter lists) in languages like JavaScript, TypeScript, and Python. Handles pattern node types from `PATTERN_NAME_TYPE_SET`, field extraction via `pattern_field_dict`, and recursion into child patterns.

## `_extract_assignment_name_list`

Extract target variable names from a Python assignment statement, including all targets in a chain (a = b = 1) and names from destructuring patterns, while ignoring attribute assignments.

## `_extract_type_alias_name_list`

Extract the name from a Python type alias statement (type Alias = int).

## `_extract_variable_declarator_name_list`

Extract variable names from JavaScript/TypeScript `lexical_declaration`, `variable_declaration` or Java `field_declaration` nodes, handling multiple declarators and destructuring patterns.

## `commonjs_export_assignment`

Parse a CommonJS export assignment statement (module.exports.name = value, exports.name = value, or module.exports = value) and return the exported name and value node, or None if not an export assignment. Handles assignment chains by returning the first matching target.

## `require_module`

Extract the module string from a require("module") call expression, returning the string without quotes, or None for non-require nodes.

## `_member_function_name`

Extract the member name from a top-level statement assigning a function or class to a member (app.init = function () {}), returning None if not a member assignment or not at file scope.

## `_member_assignment_name_list`

Extract definition names from member assignments in CommonJS modules, distinguishing between named exports (exports.name, module.exports.name), default exports (module.exports with unnamed values), and function assignments to object members.

## `_extract_export_member_name_list`

Extract the key name from an object entry being exported via module.exports or export default, returning empty when the value is a named identifier (the identifier itself is the definition).

## `_extract_enum_member_name_list`

Extract the name of a TypeScript enum member written without a value (enum E { A, B }).

## `_default_export_name_list`

Return the default export name for unnamed function, class, or object literals in export default statements, returning empty when the export is a named identifier or function.

## `declarator_name_node`

Unwrap C/C++ declarators (pointers, arrays, references, parentheses, initializers) to find the innermost name-holding node (identifier, function_declarator, etc.), or None if no such node exists.

## `_declarator_name_list`

Extract variable or member names from C/C++ declaration or type_definition nodes by unwrapping their declarators and filtering for specified name node types.

## `_function_name`

Extract the name from a C/C++ function_declarator, handling free functions, constructors, methods, operators, destructors, and qualified identifiers (scope::name), returning None for function pointers.

## `_extract_function_declarator_name_list`

Extract the function name from a C/C++ function_definition by locating its function_declarator and unwrapping declarator chains.

## `_extract_declarator_name_list`

Extract the function name from a C/C++ function_declarator prototype (a forward declaration not yet seen by `_extract_function_declarator_name_list`).

## `_extract_init_declarator_name_list`

Extract variable names from C/C++ variable declarations, unwrapping declarator chains to find identifiers.

## `_extract_field_declarator_name_list`

Extract member names from C/C++ field declarations, unwrapping declarator chains to find field identifiers.

## `_extract_type_declarator_name_list`

Extract type names from C/C++ typedef declarations, including those of function pointer typedefs.

## `_extract_body_name_list`

Extract the name of a C/C++ struct, union, enum, or class specifier when it has a body (struct node { ... }), returning empty for forward declarations or uses without a body.

## `_extract_kotlin_property_name_list`

Extract property names from Kotlin val/var declarations, handling single and destructuring (multi_variable_declaration) cases.

## `_extract_object_reference_name_list`

Extract the object name from SQL CREATE statements (create_table, create_view, create_function, etc.) via the object_reference child node.

## `_extract_impl_type_name_list`

Extract the type name that a Rust impl block is written for, stripping generic arguments, path prefixes, and references to isolate the core type identifier.

## `_extract_inline_module_name_list`

Extract the module name from a Rust inline module (mod name { ... }), returning empty for declaration-only modules without a body.

## `_extract_name_field_name_list`

Extract the name from the name field of C# declarations (methods, types, constructors, properties, events, enum members), removing verbatim identifier prefixes (@class -> class).

## `_extract_variable_declaration_name_list`

Extract variable names from C# field and event field declarations, removing verbatim prefixes.

## `_extract_unnamed_member_name_list`

Generate synthetic names for C# members that have no explicit name: this[] for indexers, ~Name for destructors, operator <type> for conversion operators, and operator <symbol> for operator overloads.

## `_SENTINEL_EXTRACTOR_DICT`

Dictionary mapping sentinel values (special patterns like "__assignment__", "__variable_declarator__") to dedicated name extraction functions, allowing definition_dict to reference complex language-specific extraction logic by name rather than by standard child type.

## `_INCLUDE_GUARD_RE`

Regex pattern matching C/C++ include guard `#define` directives (e.g., _HEADER_H_, HEADER_HPP_INCLUDED_) for filtering from the definition list.

## `CONTAINER_DEFINITION_TYPE_SET`

Set of AST node types that permit nested definitions (namespaces, classes, structs, enums, interfaces, impl blocks, traits) and should continue breadth-first traversal into their children after being recorded as definitions.

## `CLASS_DEFINITION_TYPE_SET`

Set of AST node types representing type definitions (classes, interfaces, records, enums, unions, structs) used to identify when a definition is a type rather than a value or routine.

## `OPEN_DEFINITION_TYPE_SET`

Set of container definition types whose inner definitions belong at the same nesting level as the container itself rather than as members (C/C++ namespaces and enums).

## `TRANSPARENT_DEFINITION_TYPE_SET`

Set of definition types that group other definitions but do not themselves contribute a name to the file, used to exclude them from own-name sets in analysis.

## `_TYPE_FIELD_DEFINITION_TYPE_SET`

Set of declaration node types (declaration, type_definition, field_declaration) for which the file continues breadth-first search into the type node to find nested type definitions.

## `BARE_NAME_DEFINITION_TYPE_SET`

Set of COBOL definition types (from COBOL_DEFINITION_DICT) representing definitions that are referenced by name from anywhere in the file, even when nested, and should not be filtered by `select_top_level_definitions()`.

## `ATTACHED_DEFINITION_TYPE_SET`

Set of definition types (Rust impl blocks) that carry a name but do not define it, instead attaching to and providing members for a type defined elsewhere.

## `DEFAULT_EXPORT_NAME`

Constant string name ("default") used to represent unnamed default exports in JavaScript and TypeScript.

## `_LOCAL_BODY_TYPE_SET`

Set of AST node types representing functions and blocks written as values (function expressions, arrow functions, lambdas, closures, class static blocks, init blocks) whose internal declarations are local rather than file-level, traversal into which is skipped unless the function is immediately called.

## `_DECLARATION_TYPE_SET`

Set of C/C++ node types (declaration, field_declaration) for which extent_node looks upward to find a containing declaration when the current node defines no name itself.

## `_WRAP_DECLARATOR_TYPE_SET`

Set of C/C++ declarator node types (pointer, array, reference, parenthesized) that wrap another declarator and are unwrapped during name extraction.

## `_FUNCTION_VALUE_TYPE_SET`

Set of JavaScript/TypeScript value node types (function_expression, arrow_function, class) that can represent function or class definitions assigned to members or exported.

## `PATTERN_NAME_TYPE_SET`

Set of AST node types representing bare names within binding patterns, treated as leaf nodes during pattern name extraction.

# Summary

# Summary: codetwine/extractors/definitions.py

**Single Responsibility**
Extract definitions (functions, classes, variables, types) from source code ASTs and return them as structured metadata sorted by line number, supporting multiple languages and handling special cases like decorated definitions, include guards, destructuring patterns, and nested definitions in containers.

**Main Public Definitions**
- `DefinitionInfo`: Dataclass holding definition metadata (name, node type, line range, byte offsets)
- `extract_definitions()`: Main entry point returning all definitions from file AST
- `select_top_level_definitions()`: Filter to keep only outermost definitions
- `definition_name_list()`: Extract names declared by an AST node
- `pattern_name_list()`: Extract names from destructuring patterns
- Utility functions: `commonjs_export_assignment()`, `require_module()`, `declarator_name_node()`

**Key Terms**
Breadth-first AST traversal, tree-sitter parsing, COBOL/R source modules, container definitions, decorated definitions, include guards, binding patterns, destructuring, CommonJS exports, member assignments, C/C++ declarators, language-specific extraction functions.
