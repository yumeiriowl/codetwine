# Design Document: codetwine/extractors/r_source.py

# Design Specification

**Overview**

Extract the definitions, imports, and references of an R source file by parsing its abstract syntax tree to enable dependency analysis and name resolution.

This file is used to:
- Call `read_r_source()` to parse an R file's AST and return an `RSource` object containing all definitions, imports, and references for further analysis
- Call `r_definition_list()` to extract top-level definitions from an R file, including assignments, function definitions, and S3/S4/R6 class definitions
- Call `r_definition_text()` to retrieve the source text of a specific definition by name, prioritizing top-level definitions over members and attached definitions
- Call import-related functions like `_import_list()` indirectly through `read_r_source()` to identify `source()` calls, `library()` / `require()` calls, and `box::use()` module imports

The file depends on `codetwine/utils/file_utils.py` only for `line_list_of()`, which splits file text into lines using multiple line-break conventions. It is used by `codetwine/r_name_index.py` to build a searchable index of R definitions, imports, and references across a project; by `codetwine/extractors/definitions.py` to list definitions in a file; by `codetwine/extractors/usage_analysis.py` to locate definitions by line range; and by `codetwine/extractors/definition_source.py` to retrieve definition source text.

The file processes R source code as a tree-sitter AST, making no external I/O calls and performing no caching or concurrency logic. Names assigned inside top-level statements (outside functions and `local()` calls) are treated as definitions only if a later statement refers to them, preventing false positives from temporary variables. The `_ReferenceWalker` class applies scoping rules during a single tree traversal: names bound by function parameters, assignments within function bodies, or for-loop variables do not generate references.

**Definitions**

## `RDefinition`

A definition of a name at the top level of an R file or as a member of a class, with its kind (function, assignment, call like `setClass()`, or class member), line range, and attributes indicating whether it is a function, a generic (calls `UseMethod`), a class member, or an attachment (`setMethod` for an existing name).

## `RImport`

A file or package that an R file reads via `source()`, `library()` / `require()`, or `box::use()`, with its kind, path or package name, line number, and for box imports, the alias and name mappings for selective imports.

## `RReference`

A place in an R file where a name is referred to without being defined by an enclosing function or assignment, with the name, line number, and kind (simple name, namespace `pkg::name`, or member `owner$name`). Includes the owner and member chain for `owner$a$b` references.

## `RSource`

The complete extraction result of an R file, holding lists of definitions, imports, and references in the order they appear in the source.

## `SOURCE_IMPORT`, `BOX_IMPORT`, `LIBRARY_IMPORT`

Constants identifying the three kinds of imports: file loading via `source()` / `sys.source()`, module imports via `box::use()`, and package loading via `library()` / `require()`.

## `NAME_REFERENCE`, `NAMESPACE_REFERENCE`, `MEMBER_REFERENCE`

Constants identifying the three kinds of references: a bare name, a namespaced name with `pkg::name`, and a chained member access with `$` or `@`.

## `read_r_source()`

Parse an R file's AST and return an `RSource` with all definitions, imports, and references; this is the primary entry point for extracting structured metadata from an R file.

## `r_definition_list()`

Return all top-level definitions of an R file in source order, including assignments to names, function definitions, S3/S4/R6 class definitions made by calls like `setClass()`, and names assigned inside top-level statements (such as within `tryCatch()` blocks) that are later referenced. A name assigned inside a top-level statement is included as a definition only if a subsequent top-level statement refers to it, excluding names bound in function bodies and `local()` calls.

## `r_definition_text()`

Retrieve the source lines of a definition by name, preferring a top-level definition over class members and attached definitions like `setMethod()`. Returns the joined lines or None if the definition does not exist in the file.

## `_read_top_level()`

Extract the statements that are logically at the file's top level, including those nested inside blocks (`{ }`, `( )`, `if`), loop bodies, and loop sequences. Also return the names bound by for-loop variables at the top level, which are considered in scope for reference resolution.

## `_assign_part()`

Split a binary operator node into target and value if it is an assignment, handling left-assign (`<-`, `=`, `<<-`) and right-assign (`->`, `->>`) operators. Return None for non-assignment operators.

## `_assign_chain()`

Decompose a chained assignment statement (e.g., `a <- b <- 1`) into a list of target nodes from outermost to innermost and the final value node, handling mixed operators and nested target pairs like `a = b <- 1`.

## `_call_name()`, `_call_name_part()`

Extract the function name a call invokes; `_call_name()` returns just the name, while `_call_name_part()` returns both the owner (for `pkg::name`) and the name.

## `_argument_list()`

Return the arguments of a call or subset node as `(name or None, value node or None, argument node)` tuples in source order, where the name is present only for named arguments.

## `_argument_value()`

Find the value a call passes to a parameter by name, supporting both explicit named arguments and positional arguments mapped to parameters via `_FORMAL_DICT`. Handles partial name matching and unnamed arguments filling positional slots.

## `_string_text()`

Extract the literal text between quotes of a string node, or None if the node is not a string or is a raw string (prefixed with `r`). Returns an empty string for empty string nodes.

## `_string_name()`

Return the name a call is given as a string argument, applying any suffix from `_NAME_SUFFIX_CALL_DICT` (e.g., `setReplaceMethod("f", ...)` becomes `"f<-"`). Return None if the parameter is not given a string or receives an empty string.

## `_name_text()`

Extract a name from a node: strip backticks from an identifier, or extract the string content from a string node. Return None for other node types.

## `_call_definition_list()`

Return definitions made by a single call that defines a name via a string argument (like `setClass()`, `setGeneric()`, `setMethod()`), filtering out names already assigned by the statement. Distinguish between definitions (the call defines the name) and attachments (the call is written for an existing name).

## `_member_definition_list()`

Return class members (named arguments in the `public`, `private`, `active` lists of `R6Class()` or the `methods`, `fields` lists of `setRefClass()`) as definitions with the `is_member` flag set.

## `_define_call_list()`

Recursively find calls that define names by string or class definitions within a value node, skipping those inside function definitions and quote-like calls (`quote()`, `bquote()`, `substitute()`, `expression()`).

## `_inner_definition_list()`

Return names assigned inside top-level statements (outside functions and `local()` calls) that are referenced by a later top-level statement, treating them as definitions with the line range of their first assignment.

## `_is_inside_local_call()`

Check whether a node is nested inside a `local()` call within its enclosing statement, using parent traversal.

## `_literal_path()`

Extract a file path from a node as a string, supporting string literals, `file.path()`, and `here::here()` with all-literal arguments. Return None if variables are present or the node is not a path.

## `_box_path_part_list()`, `_box_import()`

Parse a module path expression in `box::use()` (e.g., `app/logic/calc[add, minus = sub]`) and return an `RImport` with the path, alias, selective imports (name mappings), and attach-all flag.

## `_import_list()`

Extract all imports from an R file in source order: `source()` and `sys.source()` calls with literal paths, `library()` and `require()` calls with package names, and `box::use()` arguments, found anywhere in the file.

## `_chain_node_id_set()`

Collect the node IDs of all assignment nodes in an assignment chain, including nested target pairs, to identify which nodes represent top-level definitions.

## `_local_bind_list()`, `_local_name_set()`

Return names bound (assigned or used as loop variables) inside a node outside of function bodies, with `_local_bind_list()` pairing each name with its binding node and `_local_name_set()` returning just the set of names.

## `_function_local_name_set()`

Return all names a function binds: its parameter names and all names assigned in its body (excluding nested function definitions).

## `_string_node_list()`

Collect string nodes from a value, supporting single strings, and strings in `c()` or `list()` calls. Optionally filter to only named arguments (for slot definitions like `c(x = "A")`).

## `_string_reference_list()`

Extract references made by a call via string arguments that name definitions or classes: function names in `new()`, `setValidity()`, `setMethod()`, `setReplaceMethod()`, `do.call()`, `match.fun()`; class names in signature, contains, members, slots arguments; and classes in `signature()` and `representation()` calls.

## `_ReferenceWalker`

A class that traverses an R AST and collects all references (names used without binding), respecting scoping rules from function parameters, assignments, for-loop variables, and top-level statements. Each node type has a reader method that extracts references and returns child nodes; names are not collected as references if they are bound by an enclosing scope.

## `_reference_list()`

Extract all references in an R file, applying scoping rules: names are not references where bound by function parameters, assignments, for loops in function bodies or top-level statements, or top-level for loops. Special handling includes operators between `%`, namespace-qualified names, member access, string-named definitions, and replacement function calls.

## `_has_call()`

Check whether a function body calls a named function (as the outermost call in any expression), without entering nested function definitions.

## `_is_target_pair()`, `_target_part_list()`

Utility for assignment parsing: `_is_target_pair()` detects `a = b` without parentheses, and `_target_part_list()` expands nested target pairs into a flat list.

## `_read_assign()`, `_read_replace_target()`

Methods of `_ReferenceWalker` for processing assignments: `_read_assign()` handles simple targets, outer-scope assignments (<<-, ->>) inside functions, and chained targets; `_read_replace_target()` handles complex targets like `f(x) <- value`, extracting replacement function names and recursing into the first argument.

## `_node_text()`, `_line()`, `_operator()`

Utility functions: `_node_text()` decodes a node's source bytes to UTF-8, `_line()` returns its 1-based starting line, and `_operator()` extracts the operator text of a binary or namespace operator node.

# Summary

# Summary: codetwine/extractors/r_source.py

**Single Responsibility**
Extract structured metadata (definitions, imports, references) from R source files by parsing their abstract syntax trees, enabling dependency analysis and name resolution across R projects.

**Main Public Definitions**
- `read_r_source()`: Primary entry point parsing R files into `RSource` objects
- `r_definition_list()`: Extract top-level definitions including assignments and class definitions
- `r_definition_text()`: Retrieve source text of definitions by name
- `RSource`, `RDefinition`, `RImport`, `RReference`: Data classes holding extraction results

**Key Terms**
Handles R language constructs: assignments, function definitions, S3/S4/R6 classes, namespace-qualified names, member access via `$` and `@`. Processes imports from `source()`, `library()`/`require()`, and `box::use()`. Applies scoping rules to distinguish definitions from references, excluding names bound in function bodies and `local()` calls. Treats names assigned in top-level statements as definitions only if later referenced.
