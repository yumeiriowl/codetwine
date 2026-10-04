# Design Document: codetwine/csharp_namespace_index.py

# Design Specification

**Overview**

Resolve C# name references to their type and member definitions by indexing project namespaces, types, and using directives, then matching reference chains through scope-aware namespace lookup and type member traversal.

- Call `csharp_reference_target_list()` to resolve all references in a C# file to their definitions across the project, receiving one target per reference-definition pair with file location and declaration line.
- Call `_get_namespace_index()` internally to build and cache the namespace, type, and extension method registry of a project's C# files for reuse across multiple files.
- Use `CsharpNamespaceIndex` and `CsharpReferenceTarget` dataclasses as the cached index structure and resolution output respectively in other modules.
- Build the index by calling `_build_namespace_index()` once per project when the project file set changes, parsing all C# files and extracting their declarations.

This file depends on `codetwine/extractors/csharp_source.py` to parse C# file ASTs and extract namespace scopes, using directives, type declarations, and reference chains; on `codetwine/parsers/ts_parser.py` to obtain cached AST root nodes and file content; and on `codetwine/config/settings.py` to identify C# files by extension. The file `codetwine/reference_target.py` calls `csharp_reference_target_list()` as its primary entry point to resolve references, and `codetwine/extractors/usage_analysis.py` and `codetwine/extractors/dependency_graph.py` use the `CsharpReferenceTarget` dataclass and cache-clearing functions.

The module implements two-level caching: `namespace_index_cache` stores the per-project namespace index indexed by project directory and validated by project file set, and `csharp_target_cache` stores resolved reference targets per file and revalidates against project file set changes. Lookup chains are cached within each `_ReferenceResolver` instance to avoid redundant resolution of the same name in the same scope. References that cannot be resolved to a project definition return an empty target list.

**Definitions**

## `_ANY_TYPE_SET`

Constant set containing the type names `"object"` and `"dynamic"` that match any parameter type during method overload resolution, allowing arguments of unknown type to fit any parameter.

## `_WIDER_TYPE_DICT`

Constant mapping from numeric type names to the wider numeric types they implicitly convert to when passed as arguments, used by `_is_type_fit()` to determine whether an argument type satisfies a parameter type through numeric widening.

## `_PROJECT_FILE_EXT`

Constant string `".csproj"` (lowercase with dot) used to identify C# project files when determining which directory above a C# file contains its project configuration.

## `_ATTRIBUTE_SUFFIX`

Constant string `"Attribute"` appended to attribute reference names during lookup to resolve short forms like `[Route]` to their full declarations like `RouteAttribute`.

## `namespace_index_cache`

Module-level cache dictionary mapping project directory to a tuple of (project file set, CsharpNamespaceIndex), used to avoid re-parsing and re-indexing C# files when the project file set has not changed. Cache entries are validated by `project_cache_value()` and cleared by dependent modules when the project state changes.

## `csharp_target_cache`

Module-level cache dictionary mapping absolute file path to a tuple of (project file set, resolved target list), used to avoid re-resolving references in a file when the project file set has not changed. Cache entries are validated by `project_cache_value()` and cleared by dependent modules.

## `CsharpReferenceTarget`

Dataclass representing one resolved reference in a C# file, containing the name as written, the line number, the relative file path containing the definition, the qualified definition name (type.member), and the declaration line in that file; used as the output of reference resolution for downstream usage analysis.

## `_TypeEntry`

Dataclass pairing a type declaration with its containing file's relative path, used as the value type in the namespace index's type and extension method dictionaries to track all declarations of each type and method name across the project.

## `CsharpNamespaceIndex`

Dataclass storing the indexed namespace, type, and using directive metadata of a project's C# files, containing dictionaries mapping (namespace, type path) to type entries, extension method names to declaring types, and files to their nearest .csproj directory, plus the set of all project namespaces and global using directives per .csproj directory; passed to `_ReferenceResolver` to resolve references.

## `_LookupStep`

Dataclass representing one namespace and its associated using directives to search when resolving a name chain, used internally by `_ReferenceResolver` to structure the hierarchical namespace lookup process from innermost to global scope.

## `_Match`

Dataclass representing a partial or complete match of a reference name to a type or type member, containing the matched type entries, the name parts written, the definition name with member if applicable, the count of reference parts consumed, and metadata about whether the first part names the type; used internally to pass resolution state between lookup methods.

## `_project_dir_dict()`

Builds a mapping from each C# file to its nearest ancestor .csproj file directory by walking up the directory tree, used by `_build_namespace_index()` to associate each file with its project configuration for later lookup of global using directives.

## `_add_declaration()`

Indexes one C# file's namespace scopes, types, and extension methods into the namespace index, registering all namespace prefixes, global using directives, type entries keyed by (namespace, path), and extension method names, called by `_build_namespace_index()` for each parsed file.

## `_build_namespace_index()`

Parses all C# files in a project, extracts their declarations using `read_csharp_declaration()`, and indexes them into a CsharpNamespaceIndex by calling `_add_declaration()` for each file; returns the complete index for caching, with files that fail to parse logged as warnings and omitted from the index.

## `_get_namespace_index()`

Returns the cached namespace index for a project, building it via `_build_namespace_index()` on first call or when the project file set changes, storing the result in `namespace_index_cache` with project file set validation.

## `_find_type()`

Traces a tuple of name parts through a namespace to locate a type, advancing through nested types once the first type is found, returning the namespace, type path, and part indices on success or None when the chain leads to no type in the project.

## `_is_constructor_match()`

Returns true if a `_Match` represents constructors of a type and nothing else, determined by checking that all declarations of the matched member name are constructors.

## `_is_static_name()`

Returns true if a `_Match` represents a name accessed through a type (not through an instance), determined by checking that the first part names the type and the match is either the type itself or a static, const, or enum member.

## `_is_type_fit()`

Determines whether the arguments of a method call can satisfy the parameters of a method declaration by checking argument count (with params support), type matching (exact or numeric widening), and handling unknown argument types and any-type parameters like `object` and `dynamic`.

## `_exact_type_count()`

Counts how many arguments of a method call have exact type matches with their corresponding parameters, used to select the best overload when multiple methods fit an argument count.

## `_declaration_line()`

Locates the declaration line of a type or member within a type by returning the type's start line if the member is not found, the first member declaration if not called, or (when called) the unique method declaration matching the argument count and types, with preference for exact type matches over implicit conversions.

## `_ReferenceResolver`

Class that resolves all references in a single C# file by storing the namespace index and file's declarations, computing scope-based lookup steps, caching matches to avoid redundant lookups, and implementing reference resolution via namespace traversal, using directive application, and member lookup; instantiated once per file by `csharp_reference_target_list()`.

## `_ReferenceResolver.__init__()`

Initializes a resolver for one C# file by storing the namespace index, file path, and declarations, preparing internal caches for scope lookup steps and name matches.

## `_ReferenceResolver._scope_index()`

Returns the index in the file's scope list of the innermost namespace scope containing a given line, used to locate the immediate namespace context of a reference.

## `_ReferenceResolver._step_list()`

Computes the sequence of namespaces a name is looked up in starting from a scope, proceeding from the innermost scope outward through parent scopes and ending at the global namespace, with each step including the using directives (and global using directives at file scope) that apply there; cached by scope index.

## `_ReferenceResolver._absolute_using()`

Resolves a using directive's namespace or type name to its full qualified name by checking whether each part built from the directive is a project namespace or leads to a project type, starting from the namespace the directive is written in and working upward to the global namespace.

## `_ReferenceResolver._outer_type_list()`

Returns the type declarations that contain a given line, sorted from outermost to innermost, used to find the enclosing types where member references are resolved.

## `_ReferenceResolver._select_entry_list()`

Filters type entry candidates according to priority: own file first if requested, then entries under the same .csproj directory, then all entries; used to choose which file's declaration to resolve a reference to when a type is declared in multiple files.

## `_ReferenceResolver._match_name()`

Looks up a reference name from a namespace, optionally prefixed by using directive or enclosing type names, traversing through namespaces until a type is found then continuing into the type for members, with support for type arity checking and any-arity fallback; returns a `_Match` on success or None if the chain leads nowhere.

## `_ReferenceResolver._match_outer_type()`

Resolves a reference's first part as a member of the types enclosing the reference line, searching from innermost to outermost type and collecting member declarations across partial type definitions.

## `_ReferenceResolver._match_step()`

Looks up a reference within a single namespace step by trying the namespace itself, alias using directives, namespace using directives (combining matches from multiple), and static using directives in that order; returns the first successful `_Match`.

## `_ReferenceResolver._match_reference()`

Resolves a reference by attempting lookup first with exact type arity, then with any-arity fallback; for attribute references, also attempts the name with `"Attribute"` suffix appended; used for non-member references (type, attribute, name, etc.).

## `_ReferenceResolver._match_written()`

Routes reference lookup to either `_match_name()` for absolute references (prefixed with "global::") or `_match_chain()` for relative ones.

## `_ReferenceResolver._match_namespace()`

Finds the first namespace in the lookup step list (from innermost scope outward) that provides a match for a reference via `_match_step()`.

## `_ReferenceResolver._match_chain()`

Resolves a relative reference by checking outer type members first, then namespaces, with special handling for `this.` references (members only), constructors, and static member access; disambiguates between member and namespace matches when a short name could refer to either.

## `_ReferenceResolver._extension_entry_list()`

Locates extension method declarations visible at a call site by filtering project extension methods by name and argument count, then checking which declaring types are visible through the namespace lookup steps at that line (direct namespace, using namespace directives, or using static directives).

## `_ReferenceResolver.resolve()`

Resolves a single reference by matching its leading parts to a type or member, then resolving the last part as an extension method if the leading parts do not consume it and the reference is a call on a value; returns a list of `CsharpReferenceTarget` objects, one per definition file, or empty if no definition is found.

## `csharp_reference_target_list()`

Resolves all references in a C# file to their project definitions by retrieving the cached namespace index, creating a `_ReferenceResolver` for the file, iterating through references extracted by `csharp_reference_list()`, calling `resolve()` on each, deduplicating by target signature, and storing the sorted results in the target cache; the entry point called by `reference_target.py`.

# Summary

# Summary: csharp_namespace_index.py

**Single Responsibility**

Resolve C# name references to their type and member definitions across a project by indexing namespaces, types, and using directives, then matching reference chains through scope-aware lookup and type member traversal.

**Main Public Definitions**

- `csharp_reference_target_list()` — resolves all references in a C# file to definitions, returning targets with file locations and declaration lines
- `CsharpNamespaceIndex` — cached index structure storing namespace, type, and using directive metadata per project
- `CsharpReferenceTarget` — resolved reference output containing name, line, definition file path, qualified definition name, and declaration line

**Key Terms**

Namespace indexing, using directives, type declarations, extension methods, reference resolution, scope-based lookup, type member traversal, method overload resolution, type compatibility, nested types, global using directives, .csproj association, attribute name resolution, type arity, two-level caching.
