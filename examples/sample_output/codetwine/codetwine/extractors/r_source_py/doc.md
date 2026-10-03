# Design Document: codetwine/extractors/r_source.py

# Design Specification

**Overview**

Extract definitions, imports, and references from R source files by traversing their tree-sitter AST, classifying constructs by their syntactic patterns and assignment operators.

- Call `r_definition_list()` to obtain all top-level definitions in an R file, which identifies names bound by assignments, function definitions, and calls to S3/S4/R6 class-creation functions; this is used by `codetwine/extractors/definitions.py` to report definitions and by `codetwine/extractors/usage_analysis.py` to build definition ranges.
- Call `_import_list()` (via `read_r_source()`) to extract source file paths from `source()` calls, package names from `library()`/`require()` calls, and box module paths from `box::use()` calls; this is used by `codetwine/r_name_index.py` to resolve file dependencies and populate import metadata.
- Call `_reference_list()` (via `read_r_source()`) to collect all name references at the statement level, including namespace-qualified names (`pkg::name`), member accesses (`owner$name`), string-based class/function references, and replacement function calls; this is used by `codetwine/r_name_index.py` to match references to definitions.
- Call `r_definition_text()` to retrieve the source lines of a named top-level definition; this is used by `codetwine/extractors/definition_source.py` for cross-file definition lookup.

This file depends on `codetwine/utils/file_utils.py` only for `line_list_of()`, which splits file text into lines using normalized line break handling. Files in `codetwine/r_name_index.py` consume `RDefinition`, `RImport`, `RReference`, and `RSource` dataclasses along with the `read_r_source()` function to populate a project-wide name and import index; `codetwine/extractors/definitions.py` and `codetwine/extractors/usage_analysis.py` call `r_definition_list()` directly to extract definition metadata.

Assignment operators are classified into left-assign (`<-`, `=`, `<<-`), right-assign (`->`, `->>`), local-scope (`<-`, `=`, `->`), and outer-scope (`<<-`, `->>`) sets to determine whether a binding occurs at the statement level or outside its enclosing function. Top-level statements are extracted by unwinding block-level constructs (braced/parenthesized expressions, if statements) and loops (for/while/repeat), with loop variables tracked separately. References are collected by a tree-walking visitor that maintains scope lists of bound names from function parameters, loop variables, and assignments, marking a reference as unbound only when it appears in no active scope; the visitor does not traverse into quoted expressions (`quote()`, `bquote()`, `substitute()`, `expression()`) or function bodies for reference extraction outside those bodies.

**Definitions**

## `RDefinition`

A dataclass representing one definition of a name at the top level of an R file, recording its name, syntactic type (function_definition, binary_operator, call, or argument), start and end lines, and metadata flags indicating whether the value is a function, whether the function calls `UseMethod` (S3 generic), whether it is a class member, or whether it is written for an existing name via `setMethod()` or similar.

## `RImport`

A dataclass representing one file or package imported by an R file, distinguishing between source file imports (kind `SOURCE_IMPORT` from `source()`/`sys.source()`), box module imports (kind `BOX_IMPORT` from `box::use(...)`), and library/package imports (kind `LIBRARY_IMPORT` from `library()`/`require()`), with fields for the path or package name as written, the import line number, and for box imports, the alias binding the module and a name dictionary mapping imported names to module member names.

## `RReference`

A frozen dataclass representing one place in an R file where a name is referred to without being bound by an enclosing function or assignment, recording the name, line number, and kind of reference (NAME_REFERENCE for bare identifiers and string-named definitions, NAMESPACE_REFERENCE for `pkg::name`, MEMBER_REFERENCE for `owner$name`), with optional owner and member_tuple fields for namespace and member chain accesses.

## `RSource`

A dataclass aggregating the three lists (definition_list, import_list, reference_list) extracted from one R file, used as the container returned by `read_r_source()`.

## `SOURCE_IMPORT`, `BOX_IMPORT`, `LIBRARY_IMPORT`

String constants identifying the three kinds of imports: `SOURCE_IMPORT` for file-based source includes, `BOX_IMPORT` for box module references, and `LIBRARY_IMPORT` for package attachments.

## `NAME_REFERENCE`, `NAMESPACE_REFERENCE`, `MEMBER_REFERENCE`

String constants identifying the three kinds of name references: `NAME_REFERENCE` for identifiers and string-named definitions, `NAMESPACE_REFERENCE` for `pkg::name` or `pkg:::name`, and `MEMBER_REFERENCE` for `owner$name` or `owner@name` member accesses.

## `_LEFT_ASSIGN_SET`, `_RIGHT_ASSIGN_SET`, `_LOCAL_ASSIGN_SET`, `_OUTER_ASSIGN_SET`

Sets of assignment operators classified by direction and scope: `_LEFT_ASSIGN_SET` (`<-`, `=`, `<<-`) and `_RIGHT_ASSIGN_SET` (`->`, `->>`) split assignments into target and value positions; `_LOCAL_ASSIGN_SET` (`<-`, `=`, `->`) binds within the current scope, while `_OUTER_ASSIGN_SET` (`<<-`, `->>`) bind outside the enclosing function.

## `_FORMAL_DICT`, `_NAME_CALL_DICT`, `_ATTACH_CALL_DICT`, `_NAME_SUFFIX_CALL_DICT`, `_MEMBER_ARGUMENT_DICT`, `_STRING_NAME_CALL_DICT`, `_CLASS_ARGUMENT_DICT`, `_SLOT_ARGUMENT_DICT`

Mappings from call names (e.g., `setClass`, `setRefClass`, `R6Class`, `new`) to formal parameter lists or argument names, enabling matching of unnamed arguments to parameters and extraction of definition names from string arguments in S3/S4/R6 class and method definition calls.

## `_STRING_HOLDER_CALL_SET`, `_CLASS_STRING_CALL_SET`, `_SOURCE_CALL_SET`, `_LIBRARY_CALL_SET`, `_PATH_CALL_SET`, `_QUOTE_CALL_SET`

Sets of call names marking functions that hold strings naming classes or definitions (`c()`, `list()`, `signature()`, `representation()`), source imports (`source()`, `sys.source()`), library imports (`library()`, `require()`), file paths that compose into strings (`file.path()`, `here()`), or quoted expressions whose contents are not evaluated at their written location.

## `_TOP_LEVEL_BLOCK_TYPE_SET`, `_LOOP_FIELD_DICT`

A set of node types (`braced_expression`, `parenthesized_expression`, `if_statement`) and a dict mapping loop node types to their field names, used to identify which children of these constructs should be treated as top-level statements when the construct itself is top-level.

## `_node_text()`

Decodes and returns the UTF-8 source text of a tree-sitter node.

## `_line()`

Returns the 1-based line number where a node starts in the source file.

## `_string_text()`

Extracts the text between quotes of a string node, returning the content string, empty string for empty strings, or None if the node is not a string or is a raw string literal.

## `_name_text()`

Extracts a name from an identifier node (stripped of backticks), a string node (via `_string_text()`), or returns None, used to normalize various syntactic forms of names.

## `_operator()`

Returns the operator text from a binary_operator, extract_operator, or namespace_operator node.

## `_assign_part()`

Splits an assignment node into its target and value nodes based on the operator direction, returning None if the node is not an assignment; handles both left-assign (`<-`, `=`, `<<-`) and right-assign (`->`, `->>`) operators.

## `_is_target_pair()`

Tests whether a node represents a bare `=` assignment (without parentheses).

## `_target_part_list()`

Flattens a nested assignment target (e.g., from `a = b <- value`) into a list of individual target nodes in source order, used to identify all names being assigned in an assignment chain.

## `_call_name_part()`

Returns the (package, name) pair of a function being called, extracting from an identifier or namespace_operator node; returns None for member calls or non-call nodes.

## `_call_name()`

Returns the unqualified function name from a call node (via `_call_name_part()`), or None if the call's function is not a simple name or namespace-qualified name.

## `_argument_list()`

Extracts the arguments of a call or subset node as (name or None, value node or None, argument node) tuples in source order, enabling access to both named and positional arguments.

## `_formal_name()`

Maps an argument name to its formal parameter, handling prefix matching when the argument name is an abbreviation of a parameter.

## `_argument_value()`

Returns the value node assigned to a named parameter in a call, using `_FORMAL_DICT` to match abbreviated argument names to formals and filling unnamed positional arguments in order.

## `_assign_chain()`

Unwinds nested assignments (e.g., `a <- b <- 1`) into a list of target nodes and the final value node, used to identify all names defined by an assignment statement.

## `_read_top_level()`

Traverses the AST to extract all top-level statements, descending into block-level constructs (braces, parentheses, if) and loop constructs (for, while, repeat), and collects the names of for loop variables.

## `_has_call()`

Checks whether a function body contains a call to a named function outside any nested function definitions, used to detect S3 generic functions that call `UseMethod()`.

## `_member_definition_list()`

Extracts named members (public, private, active fields in R6Class or methods, fields in setRefClass) from list/c arguments of class-creation calls, returning RDefinition records with is_member set.

## `_string_name()`

Extracts the name from a string argument of a call, applying a suffix (e.g., `<-` for `setReplaceMethod()`) when specified in `_NAME_SUFFIX_CALL_DICT`.

## `_define_call_list()`

Collects all calls that define names by string or class arguments (from `_NAME_CALL_DICT`, `_ATTACH_CALL_DICT`, `_MEMBER_ARGUMENT_DICT`) within a value node, descending into wrapper calls (e.g., `invisible()`) but not into function bodies or quoted expressions.

## `_call_definition_list()`

Extracts one or zero RDefinition records from a call that defines a name via a string argument, marking it as an "attach" definition when the call is written for a pre-existing name (setMethod, setReplaceMethod, setValidity).

## `r_definition_list()`

Returns all top-level definitions in an R file sorted by start line, including assignments to names, function definitions, members of class-creation calls, and calls that define names by string arguments, with reference to loop variables and inner assignments promoted to top-level status when later referred to.

## `_is_inside_local_call()`

Tests whether a node is nested inside a `local()` call within a statement, used to exclude bindings inside local() from being treated as top-level definitions.

## `_inner_definition_list()`

Extracts definitions written inside top-level statements (assignments outside nested functions and local() calls) that are later referred to in subsequent statements, recording them as function or non-function definitions based on their value type.

## `r_definition_text()`

Returns the source lines of a named top-level definition, preferring non-member, non-attach definitions when multiple matches exist, or None if no definition is found.

## `_literal_path()`

Extracts a file path from a string node or from calls to path-composing functions (`file.path()`, `here::here()`) when only string literals are used, returning None if variables are present.

## `_box_path_part_list()`

Parses a box module path (e.g., `./mod/calc`, `app/logic/calc[add]`, `dplyr`) into component parts, handling identifiers, `/` operator chains, and subset syntax.

## `_box_import()`

Constructs an RImport record from one argument of `box::use()`, extracting the module path, alias binding, and attach list (named imports or `[...]` for all), returning None if the value is not a module path.

## `_import_list()`

Collects all imports in an R file (source files via `source()`/`sys.source()`, packages via `library()`/`require()`, box modules via `box::use()`), sorted by line number, extracting paths and package names with validation for string-literal composition.

## `_chain_node_id_set()`

Returns the tree-sitter node IDs of all assignment nodes in an assignment chain, used to skip re-processing the outermost target nodes when extracting inner bindings.

## `_local_bind_list()`

Collects all name bindings (assignment targets and loop variables) within a node outside nested functions, with optional exclusion of specified assignment node IDs, returning (name, binding node) pairs.

## `_local_name_set()`

Returns the set of names bound by assignments and loop variables within a node outside nested functions (via `_local_bind_list()`).

## `_function_local_name_set()`

Returns the names a function binds: its formal parameters and any names assigned in its body (via `_local_name_set()`).

## `_string_node_list()`

Collects string nodes from an argument, either a single string or strings within `c()`/`list()` calls, with an option to include only named arguments.

## `_string_reference_list()`

Extracts references from strings in a call that name definitions or classes, including function/class names from `_STRING_NAME_CALL_DICT` arguments, class names from `_CLASS_ARGUMENT_DICT` and `_SLOT_ARGUMENT_DICT` arguments, and classes from `_CLASS_STRING_CALL_SET` calls.

## `_ReferenceWalker`

A visitor class that traverses an R AST, collecting NAME_REFERENCE, NAMESPACE_REFERENCE, and MEMBER_REFERENCE records while maintaining scope lists of bound names from function parameters, assignments, loop variables, and top-level for loops, excluding references that fall within any active scope and excluding names in certain positions (argument names, parameter names, assignment targets except for outer-scope assignment references).

## `_reference_list()`

Returns all unbound name references in an R file by walking top-level statements via `_ReferenceWalker`, excluding names bound by function parameters, assignments, loops, and top-level for loop variables, in the order statements are encountered.

## `read_r_source()`

Parses an R file's tree-sitter AST into an RSource record containing its definitions, imports, and references, serving as the main entry point for extracting all metadata from an R source file.

# Summary

# Summary: codetwine/extractors/r_source.py

**Responsibility:** Extract definitions, imports, and references from R source files by parsing tree-sitter ASTs and classifying constructs by syntactic patterns and assignment operators.

**Main Public Functions:**
- `r_definition_list()` – returns top-level definitions (assignments, functions, class members, string-named calls)
- `r_definition_text()` – retrieves source lines of a named definition
- `read_r_source()` – main entry point returning RSource with definitions, imports, and references

**Key Dataclasses:**
- `RDefinition` – name, type, line range, and metadata (function, S3 generic, class member)
- `RImport` – source files, packages, box modules with paths and aliases
- `RReference` – unbound name references (bare identifiers, namespace-qualified, member access)
- `RSource` – aggregated definitions, imports, and references

**Key Concepts:** Assignment operators (left/right, local/outer scope); top-level statement unwinding from blocks and loops; scope-aware reference collection excluding function parameters and bindings; string-named definitions from S3/S4/R6 class calls; literal path extraction from source/library/box imports.
