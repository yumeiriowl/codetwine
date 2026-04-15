# Design Document: codetwine/extractors/imports.py

# Overview & Purpose

## 1. Module Summary

Extracts import statements from a parsed AST and returns structured metadata describing each import's source module, imported names, aliases, and line number.

## 2. When to Use This Module

- **Analyzing imports in a target file** (`file_analyzer.py`): Call `extract_imports(root_node, language, import_query_str)` to retrieve a list of `ImportInfo` objects, which are then passed to `build_symbol_to_file_map` to resolve imported names to their dependency files.
- **Resolving imports across caller files** (`usage_analysis.py`): Call `extract_imports(caller_root, language, import_query_str)` to enumerate all imports declared in a caller file, enabling cross-file usage analysis.
- **Building a dependency graph** (`dependency_graph.py`): Call `extract_imports(root_node, language, import_query_str)` to enumerate each file's imports and resolve them to other project files, forming the edges of the dependency graph.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | dataclass | Holds structured metadata for a single import statement, including the source module, individually imported names, line number, and any aliasing information. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST to locate all import statements and returns one `ImportInfo` per unique (module, line) combination, consolidating multiple imported names from the same statement into a single entry. |

## 4. Design Decisions

- **Query-driven extraction**: Import syntax varies significantly across languages, so extraction is delegated entirely to caller-supplied tree-sitter query strings (`import_query_str`) rather than hardcoded AST traversal logic. This keeps the extractor language-agnostic.
- **Grouping by `(module, line)` key**: Multiple `@name` captures from a single import statement (e.g., `from X import A, B`) are merged into one `ImportInfo` entry using a `(module, line)` tuple as the dictionary key, preventing duplicate entries.
- **Graceful no-op for undefined languages**: Passing `None` as `import_query_str` immediately returns an empty list, allowing callers to handle languages without a defined import query without special-casing outside this module.
- **CommonJS `require()` filtering**: Matches where a `@_require_func` capture exists but its text is not `"require"` are skipped inside the function, distinguishing `require()` calls from other call expressions that may match the same query pattern.

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

**Signature:** `@dataclass class ImportInfo`

**Responsibility:** Holds all structured data extracted from a single import statement in a source file, providing a language-agnostic representation of import semantics.

**When to use:** Instantiated internally by `extract_imports` and consumed by callers in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to resolve imported symbols to project files.

**Fields:**

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source — module name, file path, or header name with quotes/brackets stripped |
| `names` | `list[str]` | Names selectively imported from the module (e.g., `from X import Y`); empty list for whole-module imports or languages without named imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias `Y` in `import X as Y`; `None` when no alias exists |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for aliased named imports (e.g., `from X import a as b` → `{"b": "a"}`); `None` when no aliased named imports exist |

**Design decisions:**
- `names` uses an empty list (not `None`) as the default to allow uniform iteration without null checks.
- `alias_map` is `None` by default rather than an empty dict to signal the absence of aliased imports explicitly.

---

## `extract_imports`

**Signature:**
```
extract_imports(
    root_node: Node,
    language: Language,
    import_query_str: str | None,
) -> list[ImportInfo]
```

**Responsibility:** Queries the AST of a source file for import statements and consolidates all captures into a list of `ImportInfo` objects, normalizing differences in import syntax across languages.

**When to use:** Called after parsing a source file into an AST when the caller needs to enumerate all imports for dependency resolution or symbol mapping.

**Design decisions:**
- Uses `(module, line)` as a composite grouping key so that a multi-name import statement (`from X import A, B, C`) is collapsed into a single `ImportInfo` rather than producing one entry per name.
- The `_require_func` capture name acts as a filter guard for CommonJS `require()` patterns; matches where the captured function name is not `"require"` are silently skipped rather than raising an error.
- Falls back to the `@module` node's line number when no `@import_node` capture is present, ensuring a line is always recorded.
- Wildcard import detection (`*`) is performed via child node type inspection rather than through a dedicated query capture, covering Java `asterisk` node types and Kotlin `*` node types.
- Duplicate names within a single import entry are excluded before appending.

**Constraints & edge cases:**
- Returns an empty list immediately when `import_query_str` is `None` or empty — no AST traversal occurs.
- Assumes `root_node` covers the full file; partial subtrees may produce incomplete results.
- The `@module` capture is required for any match to produce output; matches lacking it are skipped.

---

## `_detect_module_alias`

**Signature:**
```
_detect_module_alias(
    module_node: Node,
    import_nodes: list[Node],
) -> str | None
```

**Responsibility:** Extracts the alias name from a whole-module alias import (`import X as Y`), handling the differing AST structures of Python and Kotlin.

**When to use:** Called internally during import processing for each match to populate `ImportInfo.module_alias`.

**Design decisions:**
- Python aliasing is detected by inspecting the parent node type of the `@module` capture rather than requiring a separate query capture.
- Kotlin aliasing is detected by inspecting a named field `alias` directly on the `@import_node`, then walking to a child with type `simple_identifier` or `identifier`.

**Constraints & edge cases:**
- Returns `None` when neither language-specific pattern matches.
- `import_nodes` may be an empty list; the Kotlin path is skipped entirely in that case.

---

## `_resolve_imported_name`

**Signature:**
```
_resolve_imported_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the name as it will appear in code after import — the alias if one is present, otherwise the original name — normalizing Python and JavaScript/TypeScript aliased named imports.

**When to use:** Called internally for each `@name` capture to determine the key to store in `ImportInfo.names`.

**Design decisions:**
- For Python `aliased_import` nodes, field-name access (`name`, `alias`) is preferred over child index traversal to remain robust to AST shape variations.
- For JS/TS, the alias is retrieved from the parent `import_specifier` or `export_specifier` node rather than from the `@name` node itself, because the `@name` capture points to the original identifier.

**Constraints & edge cases:**
- Returns the raw node text as a fallback when no alias is detected; does not return `None` in the common case.

---

## `_get_original_name`

**Signature:**
```
_get_original_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the pre-alias (definition-site) name of an aliased named import, exclusively to populate `ImportInfo.alias_map` values.

**When to use:** Called internally alongside `_resolve_imported_name` for each `@name` capture to build the original-name side of the alias mapping.

**Design decisions:**
- Intentionally returns `None` when no alias exists (as opposed to returning the name), so callers can use the return value as a conditional signal to decide whether to write into `alias_map`.
- Mirrors the two-language structure of `_resolve_imported_name`: Python via `aliased_import` field access, JS/TS via parent specifier node inspection.

**Constraints & edge cases:**
- For JS/TS aliased specifiers, returns `name_node.text` (the original identifier) only when an `alias` field exists on the parent; returns `None` otherwise.

---

## `_strip_quotes`

**Signature:**
```
_strip_quotes(text: str) -> str
```

**Responsibility:** Normalizes a raw module string captured from the AST by removing surrounding quote characters or angle brackets, producing a bare module name or path.

**When to use:** Called internally on every `@module` capture before storing it in `ImportInfo.module`.

**Design decisions:**
- Handles three delimiters: double quotes, single quotes, and `<>` angle brackets, covering JavaScript/TypeScript and C/C++ import syntaxes.
- Languages whose module paths are unquoted in the AST (Python, Java, Kotlin) pass through unmodified.

**Constraints & edge cases:**
- Requires the string to be at least 2 characters long before attempting delimiter removal; single-character strings are returned as-is.
- Only removes outermost delimiters; nested or mismatched delimiters are not handled.

# Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/extractors/imports.py`) has **no project-internal dependencies**. All imports in the source code are from the standard library (`dataclasses`) and the third-party package `tree_sitter` (`Language`, `Query`, `QueryCursor`, `Node`). No project-internal modules are imported.

## Dependents (modules that import this file)

Three project-internal modules depend on this file, each consuming `extract_imports` to drive their respective analysis workflows:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : uses `extract_imports` to parse import statements from a file's AST root node, feeding the results into `build_symbol_to_file_map` to construct an imported-name-to-dependency-file mapping for a target file.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : uses `extract_imports` to retrieve the import list of a caller file's AST, enabling subsequent analysis of which symbols the caller brings into scope.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports.py` : uses `extract_imports` to enumerate import statements from each file's AST and resolve them to project-internal paths via `resolve_module_to_project_path`, building the project's dependency graph edges.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` (one-way)
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` (one-way)
- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports.py` (one-way)

`codetwine/extractors/imports.py` itself imports nothing from within the project, making it a leaf dependency node that other modules consume without any back-references.

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `Node` | The root node of a tree-sitter AST covering an entire source file |
| `language` | `Language` | A tree-sitter `Language` object used to compile the query |
| `import_query_str` | `str \| None` | An S-expression query string that encodes language-specific import syntax patterns; `None` signals that no import extraction should be performed |

The query string is sourced externally (from `IMPORT_QUERIES` in `config.py` as noted in the docstring) and uses tree-sitter capture names `@module`, `@name`, `@import_node`, and `@_require_func` as a shared contract between the query and this module.

---

## 2. Transformation Overview

```
root_node + language + import_query_str
        │
        ▼
[Guard] if import_query_str is None → return []
        │
        ▼
[Query Compilation] Query(language, import_query_str)
        │
        ▼
[AST Scanning] QueryCursor.matches(root_node)
  → stream of (pattern_index, captures) dicts
        │
        ▼
[Per-Match Filtering & Extraction]
  ├─ Filter: @_require_func present but not "require" → skip match
  ├─ Filter: no @module capture → skip match
  ├─ Extract raw module string → strip surrounding quotes/angle brackets
  └─ Determine line number from @import_node or @module node
        │
        ▼
[Grouping] keyed by (module_str, line_number)
  ├─ First occurrence of a key → create ImportInfo with empty names list
  ├─ Module alias detection (_detect_module_alias) → populate module_alias
  ├─ Per @name capture:
  │    ├─ _resolve_imported_name → alias name as used in code
  │    ├─ _get_original_name → pre-alias name (if aliased)
  │    ├─ Append alias name to names (deduplication enforced)
  │    └─ Populate alias_map when original ≠ resolved name
  └─ Wildcard detection on @import_node children → append "*" to names
        │
        ▼
[Output] list(grouped.values()) → list[ImportInfo]
```

Multiple `@name` captures from the same import statement (e.g., `from X import A, B`) are consolidated into a single `ImportInfo` entry via the `(module, line)` grouping key rather than producing one entry per name.

---

## 3. Outputs

The sole output of this module is the return value of `extract_imports`:

| Output | Type | Description |
|---|---|---|
| Import list | `list[ImportInfo]` | One `ImportInfo` per distinct `(module, line)` pair found in the file |

This list is consumed by:
- `build_symbol_to_file_map` in `codetwine/file_analyzer.py` to build a symbol-to-file resolution map.
- `extract_imports` call sites in `codetwine/extractors/usage_analysis.py` and `codetwine/extractors/dependency_graph.py` to resolve caller imports and build dependency graphs respectively.

There are no file writes or other side effects.

---

## 4. Key Data Structures

### `ImportInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source module name or path, with surrounding quotes/angle brackets stripped |
| `names` | `list[str]` | Names brought into scope by the import (the `Y` in `from X import Y`); empty for whole-module imports; `"*"` for wildcard imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias name when the module itself is aliased (the `Y` in `import X as Y`); `None` when absent |
| `alias_map` | `dict[str, str] \| None` | Maps each alias name to its original name for aliased name imports (e.g., `{"path_join": "join"}` for `from X import join as path_join`); `None` when no aliased names exist |

### `grouped` (internal accumulation dict)

| Key | Type | Purpose |
|---|---|---|
| `(module, line)` | `tuple[str, int]` | Composite key that identifies a unique import statement; prevents duplicate entries and merges multiple `@name` captures from the same statement |
| value | `ImportInfo` | The partially or fully populated `ImportInfo` being assembled for that import statement |

### captures (per query match, internal)

| Key | Type | Purpose |
|---|---|---|
| `"module"` | `list[Node]` | Nodes representing the import source; first element used |
| `"name"` | `list[Node]` | Nodes for individually imported names; may be empty or contain multiple entries |
| `"import_node"` | `list[Node]` | Nodes for the entire import statement; used for line number and wildcard/alias detection |
| `"_require_func"` | `list[Node]` | Nodes for the called function name in CommonJS patterns; used for filtering non-`require` calls |

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation / skip-and-continue** policy. No exceptions are raised explicitly, and no try-except blocks are present. Instead, the code relies on defensive conditional checks (`if not`, `if ... is None`, `.get(...)` with defaults) to silently skip invalid, missing, or unexpected data and continue processing. The caller receives a partial or empty result rather than an exception propagating upward.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| No query string provided | `import_query_str` is `None` or empty | Returns an empty list immediately | Yes | No imports extracted; callers receive `[]` |
| Missing `@module` capture in a match | A query match yields no `module` node | `continue` skips the entire match | Yes | That match is silently dropped; other matches proceed |
| Non-`require` function in CommonJS pattern | `_require_func` capture exists but text ≠ `"require"` | `continue` skips the match | Yes | Non-require call-sites are silently excluded |
| Missing `@name` or `@import_node` captures | Captures not present in a match | `.get(...)` returns `[]`; logic proceeds with empty lists | Yes | Line number falls back to module node; names list remains empty |
| No alias field on a node | `child_by_field_name(...)` returns `None` | Returns `None` from helper; caller ignores the result | Yes | No alias recorded; original name is used |
| Duplicate name in the same import group | Same name already present in `names` list | Membership check prevents re-insertion | Yes | Name appears once; no data corruption |
| Wildcard import already recorded | `"*"` already present in `names` | Guard check prevents duplicate insertion | Yes | Wildcard recorded only once |
| Unquoted or unusually formatted module string | `_strip_quotes` receives a string without surrounding quotes or brackets | String returned as-is | Yes | Module name used verbatim; no error raised |

---

## 3. Design Notes

- **No exception propagation**: The module is designed as a best-effort extractor. Because it operates on parsed AST nodes from potentially incomplete or multi-language source files, strict failure would make the entire analysis pipeline fragile. Silent skipping is preferred to halting.
- **Fallback chaining for line numbers**: When the `@import_node` capture is absent, the line number falls back to the `@module` node's position, ensuring `ImportInfo.line` is always populated when a module is found.
- **Caller-side impact**: All three dependents (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) consume the returned list directly without additional error handling, meaning they implicitly rely on this module never raising and always returning a list. The graceful degradation policy is therefore a contract with the callers.
- **No logging**: Skipped or degraded cases produce no diagnostic output, meaning data loss during extraction is silent. This is a deliberate simplicity trade-off within the current implementation.

# Summary

**codetwine/extractors/imports.py** extracts import statements from tree-sitter ASTs into structured metadata. Public interface: `ImportInfo` dataclass (`module:str`, `names:list[str]`, `line:int`, `module_alias:str|None`, `alias_map:dict[str,str]|None`); `extract_imports(root_node:Node, language:Language, import_query_str:str|None) -> list[ImportInfo]`. Groups captures by `(module, line)` key, merging multi-name imports into one entry. Consumed by `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.
