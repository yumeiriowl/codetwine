# Design Document: codetwine/extractors/rust_path.py

# Design Specification

**Overview**

Parse and extract Rust module paths, imports, and usages from a syntax tree by analyzing path nodes, use declarations, and macro arguments.

This file is used to:
- Extract all imports from a Rust file by calling `rust_import_list` on captured nodes, returning ImportEntry tuples with module paths, bound names, and scope information for use in `codetwine/extractors/imports.py`.
- Identify paths written in macro arguments by calling `macro_path_list`, which splits loose tokens into qualified path segments for usage tracking in `codetwine/extractors/usages.py`.
- Resolve path segments into their semantic names by calling `path_segment_list`, which normalizes generic arguments and raw identifiers for both imports and usages.
- Determine module scope boundaries and inheritance by calling `inline_module_scope_list`, which maps inline module lines and detects `use super::*` declarations for binding logic in `codetwine/import_binding.py`.
- Locate module declarations without bodies by calling `mod_declaration`, which extracts module names and optional `#[path]` attributes for the module tree in `codetwine/rust_module_tree.py`.

The file depends only on tree-sitter Node types and has no internal project dependencies. It is used by four dependent files in the extractors and module tree layers: `imports.py` for use declarations and extern crates, `usages.py` for path resolution in code, `import_binding.py` for scope analysis, and `rust_module_tree.py` for module declarations and imports.

Paths are cached during processing via the `UseCacheDict` parameter to avoid recomputing use declarations across multiple path resolutions in the same file. The `_MAX_SCOPE_USE_HOP` constant limits scope hops to 8 to prevent excessive traversal when following use bindings through nested scopes.

**Definitions**

## `PATH_NODE_TYPE_SET`

Identifies AST node types representing Rust paths with explicit `::` separators; used by `path_segment_list` and `_read_path` to recognize qualified identifiers as paths.

## `_SEGMENT_NODE_TYPE_SET`

Defines AST node types that constitute individual path segments (identifiers, type identifiers, and scope keywords); used to split paths into components and identify names in macro arguments.

## `_ROOT_SEGMENT`

Represents the empty first segment of paths with a leading `::` (absolute paths from crate root); used by `_node_segment_list`, `macro_path_list`, and `file_module_path` to mark crate-rooted imports.

## `_SELF_TYPE_SEGMENT`

The literal string "Self" that marks a path starting with the implicit impl type or trait name; replaced by the actual type or trait name in `_impl_type_segment_list`.

## `_SELF_OWNER_FIELD_DICT`

Maps impl and trait AST node types to their field names ("type" for impl_item, "name" for trait_item); used to locate the actual type or trait name when replacing `_SELF_TYPE_SEGMENT`.

## `_GENERIC_END_TYPE_SET`

Identifies tokens (`>` and `>>`) that terminate generic arguments; used by `macro_path_list` to detect when a `::` after a generic closes the generic rather than starting a new path segment.

## `_ANCHOR_SEGMENT_SET`

Contains path prefixes that cannot be bound by use declarations ("crate", "self", "super", and empty for absolute paths); used by `_module_path` to stop following use declaration chains.

## `_MAX_SCOPE_USE_HOP`

Maximum number of use declarations followed when resolving a path through nested scopes; limits scope traversal in `_module_path` to prevent excessive lookup chains.

## `ImportEntry`

Type alias for a tuple of (module path as "::" string, name bound in file or None for globs, original name before renaming or None, line range tuple or None); represents one name imported by a use, mod, or extern crate declaration.

## `UseCacheDict`

Type alias for a dictionary mapping scope node IDs to cached dicts of bound names and their use declaration segments; avoids recomputing use declarations when multiple paths are resolved in the same scope.

## `_segment_text`

Extracts the text of a path segment from a node and removes the raw identifier prefix `r#`; used internally to normalize segment names across all path-parsing functions.

## `_node_segment_list`

Recursively splits a path node into segment strings, preserving `Self` as a literal first segment and dropping generic arguments; returns None for non-path node types, used by `path_segment_list` and `expand_use_tree`.

## `_impl_type_segment_list`

Replaces a leading `Self` segment with the actual impl type or trait name by walking up to the enclosing impl or trait block; used to resolve `Self::method` paths inside impl blocks.

## `path_segment_list`

Splits a Rust path node into segments, normalizing generics, raw identifiers, leading `::`, and `Self` references; the primary entry point for extracting path components used by `usages.py` and `expand_use_tree`.

## `macro_path_list`

Extracts qualified paths from macro argument tokens by reading runs of names separated by `::`; handles leading `::`, `Self` replacement, and skips single names and paths immediately after `>` tokens, returning tuples of node and segment list.

## `_drop_trailing_self`

Removes a trailing `self` segment from a path, used to normalize `use a::b::{self}` into `use a::b`; called by `expand_use_tree` and `_read_use_declaration`.

## `expand_use_tree`

Expands a use declaration argument into one (segments, bound name) tuple per imported item; handles `use_as_clause`, `use_wildcard`, `scoped_use_list`, and nested `use_list` nodes, returning glob imports with `*` as the last segment and no bound name.

## `inline_module_name_list`

Collects the names of inline modules (mod name { ... }) surrounding a node, from outermost to innermost; used by `_module_path` and `_impl_type_segment_list` to rewrite paths written inside nested modules.

## `_is_super_glob`

Checks whether a use declaration argument is `use super::*;` or `use super::{*, ...};`; used to mark scopes that inherit all names from their parent module in `inline_module_scope_list`.

## `inline_module_scope_list`

Traverses the AST to collect line ranges and `use super::*` flags for every inline module; returns tuples of (first line, last line, has_super_glob) used by `import_binding.py` to manage scope inheritance.

## `file_module_path`

Rewrites a path containing `self` or `super` keywords into a path relative to the file's module by adjusting segments based on inline module nesting depth; handles `super` counts exceeding module depth by emitting leading `super` tokens.

## `_use_scope_node`

Walks up from a node to find the innermost block or inline module body; returns the scope node for use declaration lookup in `_scope_use_dict`, or None if the node is at file top level.

## `_line_tuple`

Converts a node's start and end points into a (first line, last line) tuple with 1-based indexing; used to track the scope lines where a use declaration or path binding applies.

## `_scope_use_dict`

Caches the use declarations in a scope by expanding all use_declaration nodes and storing bound names with their import paths; called by `_scope_use` and memoized in `use_cache_dict` to avoid recomputing use declarations.

## `_scope_use`

Searches up the scope chain for a use declaration that binds a name in the innermost containing block or module; used by `_module_path` to follow use bindings when rewriting the first segment of a path.

## `_module_path`

Rewrites a path into a path from the file's module by following use declarations through at most `_MAX_SCOPE_USE_HOP` scopes, then applying `file_module_path` to handle `self` and `super` keywords; returns the rewritten segments and a flag indicating whether a scope use was followed.

## `mod_declaration`

Extracts the module name and optional `#[path]` attribute value from a `mod name;` declaration (without a body); returns None for inline modules or declarations that cannot be parsed, used by `rust_module_tree.py` to build the module hierarchy.

## `_is_inside_use_declaration`

Checks whether a node is a descendant of a use_declaration by walking up ancestors; used to skip path parsing inside use statements since those are handled by `_read_use_declaration`.

## `_read_use_declaration`

Expands a use_declaration into ImportEntry tuples by calling `expand_use_tree`, applying `_module_path` to rewrite segments, and attaching scope line information for declarations inside blocks or modules; glob imports bind the name `*`.

## `_read_mod_declaration`

Converts a `mod name;` declaration into an ImportEntry with module `"self::<name>"` or the `#[path]` value; returns empty list if the declaration is inside inline modules or cannot be parsed.

## `_read_extern_crate`

Reads an `extern crate name;` or `extern crate name as alias;` declaration into an ImportEntry with the crate name or its alias; returns empty list if the name node is invalid.

## `_path_entry`

Wraps a path's segments into an ImportEntry by applying `_module_path` to rewrite it from the file's module and optionally attaching the node's line range if a scope use was applied; returns empty list for empty paths.

## `_read_path`

Extracts a path node outside use declarations and visibility modifiers into an ImportEntry; skips paths nested inside other paths or visibility modifiers to capture only the outermost paths in expressions and type signatures.

## `_read_macro_argument`

Extracts paths from macro argument tokens by calling `macro_path_list` and wrapping each in `_path_entry`; used to capture qualified names passed as arguments to macros.

## `_IMPORT_READER_DICT`

Maps AST node types to their corresponding import extraction functions; used by `rust_import_list` to dispatch to the appropriate reader (use_declaration, mod_item, extern_crate_declaration, path nodes, or token_tree).

## `rust_import_list`

The main entry point that extracts all imports from a node by looking up its type in `_IMPORT_READER_DICT` and calling the appropriate reader; caches use declarations in `use_cache_dict` to avoid recomputation across multiple paths in the same file.

# Summary

# Summary: rust_path.py

**Single Responsibility:** Extract and normalize Rust module paths, imports, and usages from syntax trees by parsing use declarations, mod declarations, extern crates, and paths in code and macros, with scope-aware path resolution through use bindings.

**Main Public Functions:**
- `rust_import_list`: Entry point extracting all imports from a node
- `path_segment_list`: Normalizes path nodes into segment strings
- `macro_path_list`: Extracts qualified paths from macro arguments
- `expand_use_tree`: Expands use declarations into imported items
- `inline_module_scope_list`: Maps inline module boundaries and scope inheritance
- `file_module_path`: Resolves self/super keywords relative to file module
- `mod_declaration`: Extracts module names and path attributes

**Key Concepts:** Import entries (module path, bound name, original name, line range); use cache for avoiding recomputation; scope-aware path resolution following use binding chains; distinction between absolute paths (leading `::`), self/super relative paths, and paths bound by use declarations; inline module nesting; generic argument normalization; glob imports.
