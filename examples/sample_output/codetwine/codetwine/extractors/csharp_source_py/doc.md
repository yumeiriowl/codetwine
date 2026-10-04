# Design Document: codetwine/extractors/csharp_source.py

# Design Specification

**Overview**

Extract C# namespace declarations, using directives, type definitions, and name chain references from a source file's abstract syntax tree, producing structured metadata for namespace resolution and cross-file reference linking.

This file is used to:
- Call `read_csharp_declaration()` to parse a C# file's AST and obtain all namespace scopes, using directives, and type declarations with their members and arity information
- Call `csharp_reference_list()` to extract all name chain references (identifiers, member accesses, generic names) from a file's AST, categorized by context (type position, attribute, method call, etc.) and excluding declaration names
- Access `CsharpDeclaration`, `CsharpType`, `CsharpMember`, `CsharpUsing`, and `CsharpReference` dataclasses from `codetwine/csharp_namespace_index.py` to build and query a project-wide namespace index that resolves references to their definitions
- Call `join_name()` and `type_name()` utility functions when assembling fully qualified names and extracting type information from AST nodes

The file depends on `codetwine/config/settings.py` for `CSHARP_DEFINITION_DICT`, which maps C# AST node types to extraction patterns (name fields, variable declarations, or unnamed members), and on `codetwine/extractors/definitions.py` for `definition_name_list()` to extract member names from declaration nodes. It is the sole extractor that produces `CsharpDeclaration` and `CsharpReference` objects consumed by `codetwine/csharp_namespace_index.py` to construct a namespace index and resolve references to type and member definitions across a project.

The walker pattern used by `_ReferenceWalker` maintains a stack of (node, field name, local name set) tuples to track context while traversing the tree depth-first, allowing references to be distinguished by their syntactic position (type annotation, method call, attribute, etc.) and filtered against locally declared names (parameters, type parameters, loop variables) to eliminate spurious references to local identifiers.

**Definitions**

## `NAMESPACE_USING`

Constant string "namespace" marking a using directive that imports a namespace (e.g., `using System.Text;`), used to classify `CsharpUsing.kind`.

## `STATIC_USING`

Constant string "static" marking a using directive that imports static members of a type (e.g., `using static App.MathEx;`), used to classify `CsharpUsing.kind`.

## `ALIAS_USING`

Constant string "alias" marking a using directive that creates an alias for a namespace or type (e.g., `using Json = App.Text.Serializer;`), used to classify `CsharpUsing.kind`.

## `NAME_REFERENCE`

Constant string "name" marking a name chain appearing in an expression context where it refers to a value or method, not explicitly declared as a type annotation.

## `TYPE_REFERENCE`

Constant string "type" marking a name chain appearing in a type position (field declaration, cast target, base class, generic argument), where a type name is expected.

## `ATTRIBUTE_REFERENCE`

Constant string "attribute" marking the name of an attribute applied to a declaration (e.g., `[Route]`), extracted separately from the attribute's arguments.

## `THIS_REFERENCE`

Constant string "this" marking a name chain appearing after `this.` in a member access.

## `MEMBER_REFERENCE`

Constant string "member" marking the name of a method or property called on an expression value (e.g., `a.b().Run()` → `Run`), extracted only when the chain is a call.

## `CsharpUsing`

Dataclass representing one using directive: stores its kind (namespace, static, or alias), the namespace or type name as parts (e.g., `A.B.Type` → `[A, B, Type]`), optional alias name, global flag, and arity of the last part's generic arguments, enabling namespace resolution to track what names are imported into each scope.

## `CsharpScope`

Dataclass representing a scope where using directives are active: the file itself or one namespace declaration, storing the fully qualified namespace name, line range, nesting depth, and list of using directives, used to associate declarations and references with the scopes that govern their import availability.

## `CsharpMember`

Dataclass representing one declaration of a member within a type: stores its start line, static flag (for enum members and const/static members named by type), argument range tuple for methods (least and most arguments, with most being None for params), extension method flag, constructor flag, and parameter type names, enabling the namespace index to distinguish overloads and match references to method calls by argument count and type.

## `CsharpType`

Dataclass representing one type declaration: stores namespace, path (qualified name including outer types), type parameter arity, line range, set of member names, dictionary mapping member names to their `CsharpMember` declarations in order, and extension method dictionary mapping method names to argument ranges of their extension declarations, forming the index of accessible members and methods that references may target.

## `CsharpDeclaration`

Dataclass representing the complete extraction result from one C# file: contains a list of scopes (file and namespace declarations in order) and a list of type declarations, serving as the root object returned by `read_csharp_declaration()` and stored in the project index.

## `CsharpReference`

Frozen dataclass representing one name chain that is not a declaration name: stores the reference kind (name, type, attribute, this, or member), tuple of name parts, tuple of type argument arities, line number, call flag, absolute flag (starts with `global::`), invocation argument count, and argument type names (where determinable from literals and known types), enabling the namespace index to resolve the reference by matching its names, arities, and context against declarations.

## `_Chain`

Dataclass used internally during chain reading: holds the accumulated parts and arities of a name chain being parsed, the head context (nothing, this, base, global, builtin type keyword, or value expression), and list of inner nodes (type argument lists and subexpressions) to be walked separately, assembled by `_read_chain()` and consumed by `_chain_reference()` to classify references.

## `_node_text`

Extract the text of a name node, removing the verbatim prefix (`@`) used in C# to escape keywords as identifiers, applied to all name nodes to normalize their representation.

## `_root_node`

Traverse parent pointers from a given node to find the tree root, used to determine the end line of file-scoped namespace declarations that extend to the end of the file.

## `_has_modifier`

Check whether a declaration or parameter node has a specified modifier (e.g., "static", "const", "this") by searching its direct children for a `modifier` node with matching text.

## `_type_parameter_name_list`

Extract the names of type parameters declared by a type or method declaration node, returning them as a list in order, used to recognize type parameter names when filtering local scopes.

## `_local_name_list`

Extract all names declared inside a method, accessor, local function, or lambda: parameters, local variables, loop and catch variables, pattern and out variables, local functions, and query range variables, traversed depth-first to handle nested scopes, enabling the reference walker to exclude these names from reference matching.

## `_type_argument_count`

Count the number of type arguments in a `type_argument_list` node by counting commas and adding one, handling cases like `<Order>` (1) and `<,>` (2).

## `_read_chain`

Recursively read a name chain node (identifier, generic name, qualified name, alias qualified name, or member access) into a `_Chain` with parts, arities, and head context, handling cases like `Helper.Make<Order>`, `global::App.Util`, `this.list`, and `repo.Find(id)`, and collecting inner nodes (type arguments, subexpressions) for separate traversal.

## `_append_chain`

Merge a partial chain into an accumulating chain, extending parts and arities while preserving the head of the first part, used during recursive chain reading to assemble qualified and member access expressions.

## `_using_directive`

Parse a `using_directive` node into a `CsharpUsing`, extracting the kind (namespace, static, or alias), name parts, alias name, global flag, and arity of the last name, returning None if the directive targets a non-name-chain type (e.g., tuple or array type).

## `join_name`

Join parts of a dotted name with dots, omitting empty parts, used to construct fully qualified namespace and type names from part lists and to combine namespace and type path segments.

## `type_name`

Extract the type name from a type node by unwinding nullable, generic, and qualified name wrappers to reach the base type identifier, returning the name without type arguments, or None for array, tuple, pointer types and the keyword `var`.

## `_type_parameter_name_set`

Collect the names of type parameters declared by a declaration node and all type declarations around it (walking up the parent chain), returning a set used to identify which names in a scope are type parameters rather than local variables or members.

## `_parameter_type_tuple`

Extract the type name of each parameter in a method declaration, returning None for parameters whose type is a type parameter of the method or enclosing type, used to populate `CsharpMember.parameter_type_tuple` for method overload resolution.

## `_declared_type`

Look up the type a name is declared with in the context of a given node by searching parameters and local variables (innermost scope first), then fields and properties of enclosing types, handling `var` declarations by finding the type of the initializer expression, returning the type name or None if not found or not determinable.

## `argument_type`

Extract the type name of an argument node from literals (string, char, bool), numeric literals with suffixes, object creation expressions, casts, or declared identifier types, returning the type name for use in method call resolution, or None for expressions without a statically determinable type.

## `_parameter_node_list`

Extract the `parameter` child nodes from a method declaration's parameter list, excluding `params` parameters (which are represented separately in the list).

## `_argument_range`

Compute the (minimum, maximum) argument count for a method declaration by counting mandatory parameters (those without defaults) and checking for `params`, returning (least, most) where most is None if `params` is present, or None if the method has no parameter list.

## `_is_extension`

Check whether the first parameter of a method declaration is marked with the `this` modifier, indicating an extension method.

## `_extension_argument_range`

Compute the (minimum, maximum) argument count for calling an extension method without the implicit first argument, subtracting one from the range returned by `_argument_range()`, or returning None if the method is not an extension.

## `_read_type`

Parse a type declaration node (class, struct, interface, enum, record, delegate) and add it to the `CsharpDeclaration`, recording its namespace, path, arity, line range, and body members (by recursively reading the body), and recording the type name in its owner type's member set if nested.

## `_read_declaration_list`

Recursively traverse the children of a node (typically a namespace or type body) and extract using directives, namespace declarations, type declarations, and members, handling file-scoped namespace declarations specially by extending their scope to the end of the file, and returning the final scope state after processing all children.

## `_member_name_list`

Extract the names declared by a member declaration node (e.g., multiple variable names from a field declaration, the method name, etc.) by delegating to `definition_name_list()` with the language-specific `CSHARP_DEFINITION_DICT`.

## `_add_member`

Add a member declaration to its owner type by computing its static flag, constructor flag, method argument range and parameter types, extension method flag, and creating a `CsharpMember` entry for each name the declaration defines, recording the member in the type's member name set and member list dictionary, and in the extension dictionary if applicable.

## `read_csharp_declaration`

Parse the AST root node of a C# file and return a `CsharpDeclaration` containing all scopes (file and namespace declarations) with their using directives, and all type declarations with their members and nested types, serving as the primary public entry point for extracting declaration metadata.

## `_is_type_position`

Determine whether a name chain node appears in a position where a type name is syntactically expected: as a qualified/alias-qualified name, in a field named "type" or "returns", as a child of type-context parent nodes (base list, type arguments, explicit interface specifier), or on the right side of `as`/`is` operators.

## `_chain_reference`

Construct a `CsharpReference` from a `_Chain`, the reference kind, line number, and optional invocation context, returning None for chains without parts, chains after `base.` or builtin type keywords, or non-call chains after value expressions, and special-casing member calls on value expressions to extract only the final member name.

## `_local_value_reference`

Transform a reference whose first name is a local variable into a `MEMBER_REFERENCE` of the called member if the reference is a call with two or more names (e.g., `items.Where(...)` where `items` is local), returning None otherwise to suppress the reference to the local name itself.

## `_declare_name_id`

Return the node id of a name declared or set by a non-chain node: the loop variable of a `foreach_statement` or the member name on the left side of an assignment in an initializer (e.g., `new { Name = value }`), used to exclude such names from reference extraction.

## `_is_member_name`

Check whether a child at a given index is the name of a member being set or matched in an initializer or pattern (e.g., `Name` in `new { Name = value }`), by verifying the next child is the expected token (=, :, etc.) for that node type.

## `_call_member_chain`

Extract the member name from a conditional access expression call (e.g., `a?.Run()`), returning a `_Chain` with head "value" and the member name, or None if the node is not a conditional access ending in a member binding.

## `_ReferenceWalker`

Class implementing a depth-first tree walk that collects all name chain references from a C# file's AST, maintaining a stack of (node, field name, local name set) tuples to track syntactic context and local scope, and using visitor methods for different node types (using directives, chains, attributes, invocations, preprocessor directives, and other nodes) to classify and filter references, with a public `walk()` method returning references in find order without duplicates.

## `_ReferenceWalker.walk`

Initialize the walker stack with the root node and process all nodes, classifying each reference by its syntactic position and filtering against local scopes, returning the accumulated references in find order without duplicates.

## `_ReferenceWalker._push`

Add a list of nodes to the walker stack without field names, used to queue child nodes for processing.

## `_ReferenceWalker._add_chain`

Process a name chain by creating its reference (if not a local name), recording it, and queueing its inner nodes (type arguments, subexpressions) for traversal, filtering local name chains by `_local_value_reference()` to capture only the member call on the local value.

## `_ReferenceWalker._visit_using`

Walk the type nodes inside a using directive (type arguments and non-name-chain types), excluding the directive's namespace/type name and alias.

## `_ReferenceWalker._visit_attribute`

Extract the attribute name as an `ATTRIBUTE_REFERENCE` and queue the attribute's argument nodes for traversal.

## `_ReferenceWalker._visit_invocation`

Extract the invoked function as a `NAME_REFERENCE` with call context (marking `is_call` and recording argument count and types), handling both direct chains and conditional member accesses (e.g., `a?.Run()`), and queue remaining child nodes.

## `_ReferenceWalker._visit_preproc`

Walk the code children of preprocessor directives (`#if`, `#elif`, `#else`), excluding the condition.

## `_ReferenceWalker._visit_other`

Walk the children of other nodes, collecting declared type parameters and local names, excluding declaration names (field "name"), loop/foreach/pattern variables, labels, and member names in initializers and patterns, and passing accumulated local names to child contexts.

## `csharp_reference_list`

Walk the AST of a C# file and return all name chain references, excluding declaration names, namespace names, using directive names (but not types inside them), named arguments, member names in initializers, chains starting with type parameters or local names, and non-called members of value expressions, processed in find order without duplicates.

# Summary

# Summary: csharp_source.py

**Single Responsibility**
Extract C# namespace declarations, using directives, type definitions with members, and name chain references from source files' abstract syntax trees, producing structured metadata for namespace resolution and cross-file reference linking.

**Main Public Definitions**
- `read_csharp_declaration()`: Parse C# file AST to extract all scopes, using directives, and type declarations
- `csharp_reference_list()`: Extract all name chain references categorized by context
- `CsharpDeclaration`, `CsharpType`, `CsharpMember`, `CsharpUsing`, `CsharpReference`: Dataclasses representing extracted metadata
- `join_name()`, `type_name()`: Utility functions for qualified name assembly

**Key Terms**
Namespace scopes, using directives (namespace/static/alias), type declarations, member definitions with arity and overload information, name chain references classified by syntactic context (type position, attribute, method call), local scope tracking to filter spurious references, generic type arguments, extension methods, invocation argument context.
