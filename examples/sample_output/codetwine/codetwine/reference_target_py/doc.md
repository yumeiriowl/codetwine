# Design Document: codetwine/reference_target.py

# Design Specification

**Overview**

Resolve references in source files to their definitions by dispatching to language-specific resolution strategies based on file extension.

This file is used by developers and tools to:
- Call `reference_target_list()` to get all resolved references in a file, returning targets with their definition locations across the project.
- Call `reference_kind()` to determine which resolution strategy applies to a file before processing its references.
- Use the `ReferenceTarget` type alias when working with resolved references across multiple languages.

The file acts as a dispatcher hub for the project's reference resolution system. It depends on four language-specific modules (`cobol_file_index`, `csharp_namespace_index`, `r_name_index`, `import_reference`) to resolve references according to each language's scoping and binding rules, and on `config.settings` to map file extensions to language types. Files like `dependency_graph.py`, `usage_analysis.py`, and `file_analyzer.py` consume the public functions here to build call graphs and analyze usage patterns.

The resolution strategy is selected by file extension through `language_ext()`, falling back to `import_reference_target_list()` for languages without dedicated resolution modules. Results are delegated entirely to the language-specific function, which may implement its own caching; callers receive fresh resolution results for the given project file set.

**Definitions**

## `ReferenceTarget`

A union type representing a resolved reference to a definition, which may be a COBOL name or procedure, a C# type or member, an R definition, or an imported symbol. This type is used by callers to work with resolved references regardless of the source language.

## `reference_kind`

Returns the resolution strategy identifier ("cobol", "csharp", "r", or "import") for a file based on its extension, or None if the file has no recognized language. Callers use this to determine whether and how a file's references can be resolved.

## `reference_target_list`

Resolves all references in a file to their definitions by selecting the appropriate language-specific resolver via `reference_kind()` and calling the corresponding function from `_TARGET_LIST_FUNCTION_DICT`. Returns an empty list for files without a language. Each target includes the reference name, line number, definition file path, and language-specific definition metadata.

## `_TARGET_LIST_FUNCTION_DICT`

A mapping from language identifiers ("cobol", "csharp", "r", "import") to their respective reference resolution functions. This dict enables `reference_target_list()` to dispatch to the correct language-specific resolver without conditional logic.

# Summary

# Summary: codetwine/reference_target.py

**Single Responsibility:** Dispatch reference resolution to language-specific strategies based on file extension, serving as the central hub for resolving references to their definitions across multiple programming languages.

**Main Public Definitions:**
- `ReferenceTarget`: Type alias for resolved references (COBOL names/procedures, C# types/members, R definitions, or imported symbols)
- `reference_kind()`: Returns resolution strategy identifier for a file
- `reference_target_list()`: Resolves all references in a file to their definitions

**Key Concepts:** Language-specific resolution strategies, file extension mapping, reference resolution dispatch, definition locations, cross-language reference handling, cached resolution results.
