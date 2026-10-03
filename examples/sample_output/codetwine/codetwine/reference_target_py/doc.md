# Design Document: codetwine/reference_target.py

# Design Specification

**Overview**

Resolve source code references to their definitions across multiple programming languages by dispatching to language-specific resolution strategies.

This file serves as the main entry point for reference resolution in multi-language projects:
- Call `reference_kind()` to determine which language-specific resolver handles a file's references based on its file extension.
- Call `reference_target_list()` to resolve all references in a source file to their definitions, receiving targets with name, line number, and definition location.
- Use `ReferenceTarget` as a type alias to work with resolved references from any supported language (COBOL, C#, R, or import-based languages).

The file acts as a façade that unifies reference resolution across language-specific indexing modules: it imports language-specific target types and resolver functions from `cobol_file_index`, `csharp_namespace_index`, `r_name_index`, and `import_reference`, and uses `EXT_TO_REFERENCE_KIND_DICT` and `language_ext()` from `config/settings` to map file extensions to their resolvers. It is called by `dependency_graph.py`, `usage_analysis.py`, and `file_analyzer.py` to extract references and their targets for dependency and usage analysis.

The file caches resolution results per file within each language-specific module; `reference_target_list()` returns an empty list for files without a language rather than raising an error.

**Definitions**

## `ReferenceTarget`

A union type representing a resolved reference from any of the four supported language resolution strategies: `CobolReferenceTarget`, `CsharpReferenceTarget`, `RReferenceTarget`, or `ImportReferenceTarget`. Each variant holds the reference name, line number, target file path, and definition metadata appropriate to its language's lookup semantics.

## `_TARGET_LIST_FUNCTION_DICT`

A dispatch table mapping language kinds ("cobol", "csharp", "r", "import") to their corresponding reference resolution functions. Each function takes file path, project file set, and project directory and returns a list of resolved targets for that language.

## `reference_kind`

Returns the language kind ("cobol", "csharp", "r", "import", or None) that determines how a file's references are resolved, by looking up the file's extension in `EXT_TO_REFERENCE_KIND_DICT` after obtaining the language-specific extension via `language_ext()`. Returns None for files without a recognized language.

## `reference_target_list`

Resolves all references in a source file to their definitions by dispatching to the appropriate language-specific resolver function based on the file's language kind. For each reference, yields a target with name, line number, file location, and definition metadata specific to that language's lookup rules. Returns an empty list for files without a language.

# Summary

# Summary: codetwine/reference_target.py

**Single Responsibility**: Serve as the main entry point for resolving source code references to their definitions across multiple programming languages (COBOL, C#, R, and import-based languages) by dispatching to language-specific resolution strategies.

**Main Public Definitions**: `ReferenceTarget` (union type for resolved references), `reference_kind()` (determines language-specific resolver by file extension), and `reference_target_list()` (resolves all references in a file to their definitions).

**Key Terms**: Reference resolution, language-agnostic façade, multi-language support, definition lookup, dependency extraction, language-specific indexing modules, file extension mapping, caching per file, target metadata (name, line number, file path).
