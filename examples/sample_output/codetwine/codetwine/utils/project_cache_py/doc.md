# Design Document: codetwine/utils/project_cache.py

# Design Specification

**Overview**

Provide a cache lookup utility that validates cached values against the project file set they were built for.

- Call `project_cache_value()` from language-specific index builders like `cobol_file_index`, `csharp_namespace_index`, and `r_name_index` to retrieve cached index objects if they were built for the current project file set.
- Call `project_cache_value()` from target reference resolvers in `import_reference`, `cobol_file_index`, `csharp_namespace_index`, and `r_name_index` to retrieve cached target lists if they remain valid for the current project file set.
- Call `project_cache_value()` from `import_binding` to retrieve a cached ImportBinder if it matches the current project file set.
- Call `project_cache_value()` from `rust_module_tree` to retrieve a cached module tree if it was built for the current project file set.

This file has no dependencies on other project-internal files and serves as a utility module used across six language-specific modules (`cobol_file_index`, `csharp_namespace_index`, `import_binding`, `import_reference`, `r_name_index`, `rust_module_tree`) to manage cache validity based on project file set changes.

The caching strategy assumes that cached values remain valid only when associated with the exact same project file set object (identity check) or an equal file set (equality check); when an equal but different object is found, the cache is updated to reference the new file set object, optimizing for subsequent lookups.

**Definitions**

## `CacheValue`

A generic type variable representing any value type that can be stored in the project cache, enabling type-safe caching across different language-specific indexing and binding modules.

## `project_cache_value()`

Retrieve a cached value if it was built for the current project file set, performing both identity and equality checks to validate cache freshness. Updates the cache entry to reference the new project file set object when an equal but distinct file set is found, ensuring the cache tracks the canonical file set instance.

# Summary

# Summary: project_cache.py

**Single Responsibility**
Provides cache validation utility ensuring cached values remain valid only for their original project file set, supporting six language-specific indexing and binding modules.

**Main Public Definitions**
- `CacheValue`: Generic type variable for type-safe caching across modules
- `project_cache_value()`: Retrieves cached values if built for current project file set, using identity and equality checks; updates cache references when equal but distinct file sets are found

**Key Terms**
Cache lookup, project file set validation, identity and equality checking, cache freshness, language-specific index builders, target reference resolvers, import binding, module trees, cache optimization through file set object tracking
