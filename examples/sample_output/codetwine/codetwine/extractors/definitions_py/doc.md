# Design Document: codetwine/extractors/definitions.py

# Design Specification

**Overview**

Extract function, class, variable, type and other named definitions from programming language AST nodes or COBOL/R source files, returning a list of definitions with their names, types, line ranges, and byte offsets.

A developer or tool would use this file to:
- Call `extract_definitions()` with a tree-sitter AST root node and language-specific definition dictionary to obtain all definitions in a file as `DefinitionInfo` objects sorted by line number.
- Call `select_top_level_definitions()` to filter a definition list to only outermost definitions, excluding nested members of classes, traits, and impl blocks while preserving definitions in open containers like C++ namespaces.
- Call `definition_name_list()` to retrieve the names defined by a single AST node, supporting both standard patterns (direct child lookup) and sentinel-value patterns (dedicated extraction functions).
- Call `pattern_name_list()` to extract variable names bound by destructuring patterns in assignments, parameters, and other binding contexts across multiple languages.
- Call helper functions like `commonjs_export_assignment()`, `require_module()`, and `declarator_name_node()` to analyze specific language constructs (JavaScript/TypeScript module exports, C/C++ declarators).

This file is a core extraction module that `extract_definitions()` is called by `definition_source.py` (caching and querying definitions), `file_analyzer.py` (building JSON output), `import_binding.py` (finding symbol definitions for import resolution), `import_reference.py` (identifying owned versus imported names), and other analysis modules. It relies on `cobol_source.py` and `r_source.py` for their specialized parsing of COBOL and R files respectively, and on `settings.py` for per-language AST node type mappings (`COBOL_DEFINITION_DICT`, `R_DEFINITION_DICT`, `PATTERN_FIELD_DICT`, `PATTERN_TYPE_SET`) that drive extraction logic.

The file handles multiple extraction patterns: standard (searching direct children for a named node type), sentinel (delegating to language-specific extractor functions), and special cases (decorated definitions, COBOL/R files with custom parsing). It uses breadth-first search of AST nodes to discover nested definitions, skipping into container types but not into function bodies written as values unless they are immediately invoked. Include-guard detection filters out `#define` directives in C/C++. Definition line ranges exclude trailing newlines when they fall at line boundaries.

**Definitions**

## `DefinitionInfo`

Data class holding the name, AST node type, and line/byte range of a single definition extracted from source code. For COBOL and BMS files, it additionally captures the line where the name appears, the level number (nesting depth in data hierarchies), and whether a data item is a group. For tree-sitter-parsed files, byte offsets mark the definition's span in UTF-8 text; for COBOL/R files, byte offsets are None. Used as the return type of `extract_definitions()` and as the unit of definition information throughout downstream analysis modules.

## `extract_definitions`

Extract all definitions (functions, classes, variables, types, etc.) from an AST root node or COBOL/R source file and return them as a list of `DefinitionInfo` sorted by start line. For tree-sitter ASTs, performs breadth-first search using a language-specific definition dictionary (e.g., from `settings.py`) to identify definition nodes, extracts names via `definition_name_list()`, determines line ranges via `_line_range()` and extent nodes, and traverses into container definitions and type nodes. For COBOL files (CobolSource), returns wrapped definitions from the source's definition_list. For R files (when definition_dict is R_DEFINITION_DICT), delegates to `r_definition_list()` from `r_source.py`. Filters out include-guard `#define` directives in C/C++. Handles decorated definitions (Python @property) and cases where no name is obtained by continuing BFS on child nodes.

## `select_top_level_definitions`

Filter a definition list to keep only outermost definitions, removing nested members of classes, impl blocks, and traits while preserving definitions inside open containers (C++ namespaces, C/C++ enums) and all COBOL definitions (which reference their names from anywhere). Tracks the end line of the most recent container to skip definitions that start within its range. Returns a sorted list of top-level definitions.

## `definition_name_list`

Return the names a single definition AST node defines, looking up the node type in a language-specific definition dictionary. If the dictionary value is a sentinel string (starts and ends with "__"), invoke the corresponding extractor function from `_SENTINEL_EXTRACTOR_DICT`. Otherwise, search direct children for a node matching the dictionary value and return its text. For decorated_definition nodes, recursively extract names from the inner function or class. Returns an empty list if the node is not a definition or no name is found.

## `pattern_name_list`

Extract variable names bound by a destructuring or binding pattern (array destructuring, object destructuring, parameter patterns, etc.) in JavaScript/TypeScript, Python, Kotlin, and other languages. Recursively traverses pattern structures using language-specific pattern_type_set and pattern_field_dict from settings. Returns names in the order they appear in source, excluding attribute accesses and other non-binding nodes. Called by variable declaration and assignment extractors.

## `_node_text`

Return the source text of an AST node by decoding its bytes as UTF-8. Helper function used throughout extraction functions to obtain identifier, operator, and keyword text.

## `_extent_node`

Determine the node whose text and line range should be reported for a definition. When a declarator (C/C++) is a definition node but its ancestor declaration gives no name itself, returns the declaration to include the full statement in the definition's extent. Otherwise returns the node itself. Used by `extract_definitions()` to establish the span of definitions.

## `_line_range`

Return the first and last line number (1-based) of an AST node's range. Adjusts the end line down by one if the node ends at the start of a line (column 0), such as when a preprocessor directive includes its trailing newline. Used by `extract_definitions()` to populate start_line and end_line of `DefinitionInfo` objects.

## `_is_call_in_place`

Determine whether a function written as a value (function expression, arrow function, etc.) is invoked immediately where it is written, such as `(function() { ... })()` or `(() => { ... })()`. Returns true when the function node (possibly wrapped in parentheses) is the callee of a call_expression. Used to decide whether to traverse into the function body's declarations; if called in place, declarations are treated as file-level.

## `_decorated_inner_node`

Return the function or class definition node nested inside a decorated_definition (Python @decorator syntax). Searches children for a definition node other than another decorated_definition. Used by `extract_definitions()` and `definition_name_list()` to handle decorated definitions as transparent wrappers.

## `_extract_assignment_name_list`

Extract variable names from a Python variable assignment (expression_statement > assignment). Handles assignment chains (a = b = value) and destructuring assignments (x, y = values) by recursively walking the right side and collecting names from the left side using `pattern_name_list()`. Returns an empty list if the statement is not an assignment.

## `_extract_type_alias_name_list`

Extract the name from a Python type alias statement (type Alias = int). Searches the left node for identifier children and returns the first one.

## `_extract_variable_declarator_name_list`

Extract variable names from JavaScript/TypeScript lexical declarations (const, let, var) or Java field declarations. Iterates over variable_declarator children, retrieves names from the name field using `pattern_name_list()` (to handle destructuring), and returns all names. Used as a sentinel extractor.

## `commonjs_export_assignment`

Parse a CommonJS module export assignment (expression_statement > assignment_expression) and return a tuple of the export name and value node, or None if the statement is not such an assignment. Recognizes exports.name, module.exports.name (returning the property name), and module.exports itself (returning DEFAULT_EXPORT_NAME). Handles assignment chains by walking the right side. Used by import and export extraction.

## `require_module`

Return the module string from a require("module") call, or None for any other node. Validates that the node is a call_expression with function "require" and exactly one string argument, then returns the string content without quotes. Used to identify dynamic module dependencies.

## `_member_function_name`

Return the name a top-level statement assigns to a function or class value on a member expression (app.init = function() {}, Foo.prototype.run = () => {}), or None otherwise. Validates that the statement is a top-level expression_statement with an assignment_expression, the left side is a member_expression, and the right side is a function or class. Returns the property identifier name.

## `_member_assignment_name_list`

Extract names from an assignment to a module export member written as a top-level statement (JavaScript/TypeScript). For exports.name and module.exports.name, returns the property name; for module.exports = unnamed value, returns DEFAULT_EXPORT_NAME; for module.exports = named value or require, returns empty (the named definition or module stands for the export). For function assignments to object members, returns the member name via `_member_function_name()`. Used as a sentinel extractor.

## `_extract_export_member_name_list`

Extract the key of an entry in an object that is exported via module.exports or export default. Returns the property_identifier key only when the entry's value is not a bare identifier (where the named definition stands for it) and the object is confirmed to be an export value. Returns empty otherwise. Used as a sentinel extractor for pair nodes.

## `_extract_enum_member_name_list`

Extract the name of a TypeScript enum member when written without an explicit value (enum E { A, B }). Returns the identifier name only if its parent is an enum_body. Used as a sentinel extractor.

## `_default_export_name_list`

Return [DEFAULT_EXPORT_NAME] for an export default statement of an unnamed value (function, class, or object literal), or empty when the exported value is a named identifier (where the named definition stands for the export) or a named function/class. Used as a sentinel extractor.

## `declarator_name_node`

Unwrap C/C++ declarators (pointer, array, reference, parentheses, init) and return the innermost node holding the name (an identifier, function_declarator, etc.), or None if none is found. Used throughout C/C++ extraction functions to isolate name-bearing nodes.

## `_declarator_name_list`

Return the names of declarators in a C/C++ declaration or type_definition that are of given types (e.g., identifier for variables, field_identifier for class members). Unwraps each declarator via `declarator_name_node()` and collects names from those matching the type tuple. Used by variable and field declarator extractors.

## `_function_name`

Extract the name from a C/C++ function_declarator, handling free functions, constructors, methods, operators, destructors, and qualified identifiers (members defined outside their class). Qualified names are constructed without template arguments, e.g., "Box::get". Returns None for function pointer declarators. Used by function_definition and declarator extractors.

## `_extract_function_declarator_name_list`

Extract the function name from a C/C++ function_definition by finding the declarator field, unwrapping it via `declarator_name_node()`, and calling `_function_name()`. Returns empty if no function_declarator is found or the name cannot be extracted. Used as a sentinel extractor.

## `_extract_declarator_name_list`

Extract the function name from a C/C++ function_declarator prototype by calling `_function_name()`. Returns the name or empty if it is not a named declarator (e.g., a function pointer). Used as a sentinel extractor.

## `_extract_init_declarator_name_list`

Extract variable names from a C/C++ declaration by finding all declarators and unwrapping them to collect identifiers. Returns empty for function prototypes (handled by traversing the function_declarator separately). Used as a sentinel extractor.

## `_extract_field_declarator_name_list`

Extract member names from a C/C++ field_declaration by finding all declarators and collecting field_identifier nodes. Used as a sentinel extractor.

## `_extract_type_declarator_name_list`

Extract type names from a C/C++ typedef by iterating over declarators, unwrapping them, and returning those with type_identifier or identifier node types. Handles function pointer typedefs by unwrapping function_declarators. Used as a sentinel extractor.

## `_extract_body_name_list`

Extract the name from a C/C++ struct, union, enum, or class specifier that has a body. Returns the name node's text only when the specifier has both a name field and a body (distinguishing definitions from forward declarations and type references). Used as a sentinel extractor.

## `_extract_kotlin_property_name_list`

Extract property names from a Kotlin val/var declaration by iterating over variable_declaration children, finding identifier children, and collecting them. Handles multi_variable_declaration for destructuring. Used as a sentinel extractor.

## `_extract_object_reference_name_list`

Extract the object name from a SQL CREATE statement (create_table, create_view, etc.) by finding the object_reference child and returning the name field. Used as a sentinel extractor.

## `_extract_impl_type_name_list`

Extract the type name that a Rust impl block is written for, removing generic arguments, path prefixes, and references. Traverses generic_type, scoped_type_identifier, and reference_type wrappers to find the innermost type_identifier. For non-named types, returns the full text with whitespace normalized. Used as a sentinel extractor.

## `_extract_inline_module_name_list`

Extract the module name from a Rust inline module (mod name { ... }). Returns the name field only if the module has a body (distinguishing from declarations). Used as a sentinel extractor.

## `_extract_name_field_name_list`

Extract the name from the name field of a C# declaration (method, type, constructor, property, event, enum member). Removes the verbatim identifier prefix "@" if present. Used as a sentinel extractor.

## `_extract_variable_declaration_name_list`

Extract variable names from a C# field_declaration or event_field_declaration by finding the variable_declaration child and collecting declarator names, removing "@" prefixes. Used as a sentinel extractor.

## `_extract_unnamed_member_name_list`

Name a C# member declaration that has no syntactic name of its own: indexers (this[]), operators (operator +, operator int), and destructors (~Vec). Returns the constructed name or empty if the declaration cannot be read. Used as a sentinel extractor.

## `_INCLUDE_GUARD_RE`

Regular expression pattern matching C/C++ include guard `#define` directives (e.g., _HEADER_H_INCLUDED_). Used by `extract_definitions()` to filter out these preprocessor directives when extracting macro names.

## `CONTAINER_DEFINITION_TYPE_SET`

Set of AST node types that define containers (namespaces, classes, structs, enums, interfaces, impl blocks, trait blocks, modules, etc.). Child nodes of these types are traversed during extraction to find nested definitions. Used by `extract_definitions()` to decide when to continue BFS.

## `CLASS_DEFINITION_TYPE_SET`

Set of AST node types that represent classes, structs, interfaces, enums, or equivalent type definitions. Used by downstream modules (e.g., `import_reference.py`) to determine whether a definition is a type that can be the target of a reference or inheritance.

## `OPEN_DEFINITION_TYPE_SET`

Set of AST node types for containers whose inner definitions are not members but are defined at the same level (C++ namespaces, C/C++ enums). Used by `select_top_level_definitions()` to determine which nested definitions should be kept in the output.

## `TRANSPARENT_DEFINITION_TYPE_SET`

Set of AST node types (currently namespace_definition) that group other definitions without contributing a name themselves (a namespace can be split across files). Used by downstream modules to identify definitions that do not export a unique name.

## `_TYPE_FIELD_DEFINITION_TYPE_SET`

Set of AST node types (declaration, type_definition, field_declaration) whose type field is searched for further definitions during extraction. Used by `extract_definitions()` to traverse into type specifiers that contain nested class or enum definitions.

## `BARE_NAME_DEFINITION_TYPE_SET`

Set of AST node types from COBOL_DEFINITION_DICT representing COBOL definitions whose names can be referenced from anywhere in the file, even when nested. Used by `select_top_level_definitions()` to keep COBOL definitions in top-level output.

## `ATTACHED_DEFINITION_TYPE_SET`

Set of AST node types (impl_item) representing definitions that carry a name but do not define it; they are attached to a type defined elsewhere. Used by downstream modules to filter out definitions that do not create new names.

## `DEFAULT_EXPORT_NAME`

String constant "default" used as the name for anonymous default exports in JavaScript/TypeScript (export default value). Used by export extraction and downstream import resolution.

## `_LOCAL_BODY_TYPE_SET`

Set of AST node types representing functions written as values and blocks that are executed where written (function expressions, arrow functions, class static blocks, lambdas, closures). Declarations inside these are local and not file-level definitions. Used by `extract_definitions()` to avoid traversing into their bodies unless they are immediately invoked.

## `_DECLARATION_TYPE_SET`

Set of AST node types (declaration, field_declaration) that hold declarators in C/C++. Used by `_extent_node()` to identify declaration nodes that should be returned as the extent of declarator definitions.

## `_WRAP_DECLARATOR_TYPE_SET`

Set of AST node types (pointer, array, reference, parenthesized declarators) that wrap another declarator in C/C++. Used by `declarator_name_node()` and `_extent_node()` to unwrap declarators and find the innermost name-bearing node.

## `_FUNCTION_VALUE_TYPE_SET`

Set of AST node types representing functions or classes as values in JavaScript/TypeScript (function_expression, arrow_function, generator_function, class). Used by `_member_function_name()` to identify function/class values assigned to members.

## `PATTERN_NAME_TYPE_SET`

Set of AST node types that directly represent bound names in destructuring patterns (identifier, shorthand_property_identifier_pattern, shorthand_field_identifier). Used by `pattern_name_list()` as base cases for recursion.

## `_SENTINEL_EXTRACTOR_DICT`

Dictionary mapping sentinel string values in language-specific definition dictionaries to extraction functions. Keyed by values like "__assignment__", "__variable_declarator__", "__function_declarator__", and maps to the corresponding extractor function (e.g., `_extract_assignment_name_list`). Used by `definition_name_list()` to dispatch specialized extraction logic.

# Summary

# Summary: codetwine/extractors/definitions.py

**Single Responsibility:**
Extract named definitions (functions, classes, variables, types) from source code ASTs and COBOL/R files, returning structured information about their names, types, and locations for downstream analysis.

**Main Public Definitions:**
- `DefinitionInfo`: Data class for a single definition with name, type, line range, and byte offsets
- `extract_definitions()`: Primary function extracting all definitions from AST nodes or language-specific source files
- `select_top_level_definitions()`: Filter definitions to outermost level only
- `definition_name_list()`: Get names defined by a single AST node
- `pattern_name_list()`: Extract names from destructuring patterns
- Helper functions: `commonjs_export_assignment()`, `require_module()`, `declarator_name_node()`

**Key Handling:**
Breadth-first AST traversal using language-specific definition dictionaries; standard patterns (direct child lookup) and sentinel patterns (delegated extraction functions); container types and nested definitions; decorated definitions; C/C++ declarators and function names; JavaScript/TypeScript exports and module bindings; COBOL/R specialized parsing; include-guard filtering; destructuring patterns across multiple languages.
