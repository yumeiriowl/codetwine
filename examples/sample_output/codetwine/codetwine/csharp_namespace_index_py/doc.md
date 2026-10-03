# Design Document: codetwine/csharp_namespace_index.py

# Design Specification

**Overview**

Resolve C# references to their type and member definitions within a project by building a namespace index and matching reference names against declarations using C# lookup semantics.

- Call `csharp_reference_target_list()` with a file path, project file set, and project directory to resolve all references in a C# file and receive a list of targets mapping each reference to its definition location and line number.
- Call `_get_namespace_index()` or use the module-level `namespace_index_cache` to obtain a cached namespace index for a project, which maps type paths and extension methods to their declarations for efficient repeated lookups.
- Call `_build_namespace_index()` to parse all C# files in a project and construct an index of namespaces, types, members, and extension methods in declaration order.
- Use `CsharpNamespaceIndex` to inspect a project's type declarations, namespaces, extension methods, and global using directives organized by relative path and type path.
- Use `CsharpReferenceTarget` to access the resolved definition information: the name written, line number, file path, definition name qualified by type and member, and declaration line in the definition file.

This file depends on `codetwine/extractors/csharp_source.py` to parse C# declarations, scopes, types, members, using directives, and references from AST nodes, and on `codetwine/parsers/ts_parser.py` to read and parse source files. It depends on `codetwine/config/settings.py` to determine which files are C# and `codetwine/utils/project_cache.py` to cache indexes and resolved references by project file set. The file is used by `codetwine/reference_target.py` to resolve C# references through the public `csharp_reference_target_list()` function, by `codetwine/extractors/usage_analysis.py` to convert reference targets to definition locations, and by `codetwine/extractors/dependency_graph.py` and `codetwine/pipeline.py` to clear cached indexes and targets when analysis resets.

Namespace resolution follows C# semantics: looking up names among type members first (including partial types), then in enclosing scopes from innermost to outermost, applying alias, namespace, and static using directives, and handling global using directives from the project directory. Type lookups support generic type parameters (arity) with fallback to any arity when exact matches fail. Method overload resolution considers argument count and parameter types, with automatic widening for numeric types. Both the `namespace_index_cache` and `csharp_target_cache` are invalidated when the project file set changes, ensuring stale definitions are not returned.

**Definitions**

## `CsharpReferenceTarget`

A resolved reference mapping a name written in one file to the declaration it refers to in another, recording the name used, reference line, definition file, qualified definition name (type.member when applicable), and the line number of the declaration for jump-to-definition support.

## `_TypeEntry`

Stores one declaration of a type paired with its relative file path for efficient grouping of multiple declarations of the same type across files (partial types).

## `CsharpNamespaceIndex`

The complete index of a C# project containing mappings of relative file paths to declarations, type paths to type declarations, all project namespaces and their prefixes, extension method names to declaring types, project directory locations for each file, and global using directives per .csproj directory; used as the primary lookup structure for resolving references.

## `_LookupStep`

Represents one namespace level in the scope chain where a name is looked up, paired with the using directives written in that scope, used to simulate C# namespace resolution by traversing from innermost to outermost scope.

## `_Match`

Captures a successful name lookup result: the type declarations matched, the name written by the reference, the fully qualified definition name (including member if present), the number of reference parts consumed, the member name if applicable, and whether the first part named the type; returned by name matching functions to guide reference target construction.

## `_project_dir_dict()`

Maps each C# file to its nearest ancestor .csproj directory (or empty string if none) by walking upward from the file location, enabling grouping of files under project configuration directories for global using directive scoping.

## `_add_declaration()`

Indexes one parsed C# file into the namespace index by recording its declaration, extracting and adding all unique namespaces and namespace prefixes to the namespace set, collecting global using directives by project directory, and cataloging all type declarations and extension methods for later lookup.

## `_build_namespace_index()`

Parses all C# files from a project file set, filters them by C# extension, constructs a project directory mapping, and returns a complete CsharpNamespaceIndex; logs and skips files that cannot be parsed.

## `_get_namespace_index()`

Returns the namespace index for a project from the module-level `namespace_index_cache`, building and caching it on first access; the cache is invalidated when the project file set changes, ensuring stale indexes are not reused.

## `_find_type()`

Follows a sequence of name parts through namespace and type nesting to locate a type declaration, starting from a given namespace and consuming parts as namespaces until one names a type, then consuming remaining parts as nested type paths; returns the namespace, type path, start position, and end position of the type match, or None if no type is found.

## `_is_constructor_match()`

Returns true when a match refers to the constructors of a type (all members with the matched name are constructor declarations), used to disambiguate between constructors and static members when resolving names.

## `_is_static_name()`

Returns true when a match refers to a name written through a type (the first part names the type and the match is either the type itself or a static/const/enum member), distinguishing static access patterns for overload resolution.

## `_is_type_fit()`

Determines whether the argument types of a method call fit the parameter types of a method declaration by checking each argument against its corresponding parameter, allowing None values (unknown types), parameters of type `object` or `dynamic`, numeric type widening, and extra arguments to params parameters.

## `_declaration_line()`

Returns the line number of a type declaration or, when a member is specified, the line of its declaration; when a member is called with a known argument count, selects the overload matching that count; when multiple overloads match, uses argument types to select the best fit, or returns the first overload if ambiguous.

## `_ReferenceResolver`

Resolves all references of one C# file by matching reference names against the project namespace index using C# lookup semantics, handling namespaces, using directives (alias, namespace, static), type members, extension methods, and attribute name suffixes; maintains caches of lookup steps and matched names to avoid redundant computation.

## `_ReferenceResolver.__init__()`

Initializes the resolver with the project namespace index, file path, file declarations, and the .csproj directory of the file, and prepares internal caches for lookup steps and name matches.

## `_ReferenceResolver._scope_index()`

Returns the index in the file's scope list of the innermost scope (namespace or file) that encloses a given line, used to determine which using directives and namespace context apply to a reference.

## `_ReferenceResolver._step_list()`

Builds the sequence of lookup steps (namespaces with their using directives) for resolving a name written in a scope, starting from the innermost scope and walking outward to the file scope, appending global using directives of the project at the file scope, caching the result.

## `_ReferenceResolver._absolute_using()`

Resolves the name of a using directive from the namespace it is written in by looking up each name in successively broader namespaces (current, parent, global), returning the directive with the full qualified name of the first match, or the original directive if no match is found.

## `_ReferenceResolver._outer_type_list()`

Returns the type declarations of the file that enclose a given line, sorted from innermost to outermost, used to resolve references against member names of surrounding types.

## `_ReferenceResolver._select_entry_list()`

Filters a list of type declarations by priority: declarations in the same file if requested, else declarations under the same .csproj directory, else all declarations; used to disambiguate partial types and imported names.

## `_ReferenceResolver._match_name()`

Looks up a name from a namespace using a prefix (from a using directive alias or type member context) by finding the type it names, optionally taking the part after the type as a member name, filtering by type arity, and returning a match with the name written by the reference and qualified definition name.

## `_ReferenceResolver._match_outer_type()`

Matches the first part of a reference against the members of types enclosing the reference line (including partial types from other files), returning a match when one of the types defines the first part as a member.

## `_ReferenceResolver._match_step()`

Matches a reference in one namespace and its using directives by trying the namespace itself, alias using directives, namespace using directives (treating the first part as a type), and static using directives (treating the first part as a member); returns the first match found.

## `_ReferenceResolver._match_reference()`

Resolves a reference to a type or member by attempting exact generic arity matching, then fallback to any arity if exact matching fails; for attribute references, also tries appending "Attribute" suffix to the last part, returning the match or None.

## `_ReferenceResolver._match_written()`

Delegates name matching to either global namespace lookup (when the reference starts with "global::") or relative scope lookup, depending on the reference kind.

## `_ReferenceResolver._match_namespace()`

Returns the first match found when looking up a reference in each enclosing namespace from innermost to outermost, used for relative (non-global) name resolution.

## `_ReferenceResolver._match_chain()`

Matches a relative (non-global) reference by first attempting to match against members of surrounding types, then against enclosing namespaces; when a member match consumes only the first part of a multi-part chain, prefers a namespace type match if the namespace name is static or the member is a constructor.

## `_ReferenceResolver._extension_entry_list()`

Returns type declarations that define an extension method of a given name and accepting a given argument count, by collecting candidates from the extension method index and filtering to those visible from the reference line through namespace and using directive scoping.

## `_ReferenceResolver.resolve()`

Resolves a single reference to a list of targets by matching the leading parts of the reference name to a type or member, then optionally matching the last part to an extension method if the reference is a call on a value and the leading parts do not consume all parts; returns one target per declaration file of each matched definition.

## `csharp_reference_target_list()`

Resolves all references of a C# file to their definitions by reading the file's declarations and references, matching each reference name using the project namespace index with C# semantics, and returning targets in line order without duplicates; results are cached by absolute file path and project file set.

## `namespace_index_cache`

Module-level cache mapping project directory to (project file set, CsharpNamespaceIndex) pairs, enabling fast reuse of namespace indexes across repeated analyses of the same project.

## `csharp_target_cache`

Module-level cache mapping absolute file path to (project file set, list of CsharpReferenceTarget) pairs, enabling fast reuse of resolved reference targets across repeated analyses of the same file.

## `_ANY_TYPE_SET`

Set of type names (`object`, `dynamic`) that match any parameter type during method overload resolution, representing universally compatible types in C#.

## `_WIDER_TYPE_DICT`

Dictionary mapping numeric type names to the set of wider types they can be implicitly converted to (e.g., `char` → `int`, `uint`, etc.), used to determine whether an argument type fits a parameter type during overload resolution.

## `_PROJECT_FILE_EXT`

The file extension (`.csproj`) of C# project configuration files, used to locate project boundaries for scoping global using directives.

## `_ATTRIBUTE_SUFFIX`

The suffix (`Attribute`) appended to an attribute reference name when the exact name does not match, allowing `[Route]` to resolve to `RouteAttribute`.

# Summary

# Summary: csharp_namespace_index.py

**Responsibility**: Build and cache a namespace index for C# projects, then resolve references to their type and member definitions using C# lookup semantics.

**Main Public Definitions**:
- `csharp_reference_target_list()` — resolves all references in a C# file
- `CsharpNamespaceIndex` — project-wide index of types, namespaces, and members
- `CsharpReferenceTarget` — maps a reference to its definition location
- `namespace_index_cache`, `csharp_target_cache` — module-level caches

**Key Concepts**: Parses C# source files to extract declarations, namespaces, using directives (alias, namespace, static), and references. Indexes types and extension methods by qualified name. Resolves references by simulating C# name lookup: matching against type members and enclosing scopes, applying using directives, handling generic arity, performing method overload resolution with numeric type widening, and supporting attribute name suffixes. Caches results per project file set to avoid redundant parsing and lookup.
