# Design Document: codetwine/extractors/imports.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Extracts import statement information from a parsed AST and returns structured `ImportInfo` objects that callers use to resolve inter-file dependencies across supported languages.

## 2. When to Use This Module

- **Resolving imports in a single file**: Call `extract_imports(root_node, language, import_query_str)` to obtain a list of `ImportInfo` objects representing every import statement in a parsed file. Used by `file_analyzer.py` to build a symbol-to-file mapping for dependency resolution.
- **Analyzing caller imports during usage analysis**: Call `extract_imports(caller_root, language, import_query_str)` to retrieve the imports of a caller file, enabling `usage_analysis.py` to determine which external names a file depends on.
- **Building a project-wide dependency graph**: Call `extract_imports(root_node, language, import_query_str)` for each file in the project to enumerate its dependencies, as done in `dependency_graph.py` to identify callee relationships.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None`, `alias_map: dict[str, str] \| None` | — | Data class holding all parsed details of a single import statement, including aliasing information. |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs tree-sitter queries against the AST to extract and group all import statements into `ImportInfo` objects; returns an empty list when no query string is provided. |

## 4. Design Decisions

- **Language-agnostic via tree-sitter queries**: Import extraction logic is not hard-coded per language. Instead, the caller supplies a query string (`import_query_str`), making the core extraction function reusable across all supported languages without branching on language identity.
- **Grouping by `(module, line)` key**: Multiple `@name` captures from the same import statement (e.g., `from X import A, B`) are consolidated into a single `ImportInfo` entry using a `(module, line)` composite key, avoiding duplicate entries.
- **Alias tracking separated into two fields**: `module_alias` captures the alias of the entire module (`import X as Y`), while `alias_map` records per-name aliases (`from X import a as b`) as `{alias → original}`, allowing callers to resolve either form of aliasing independently.
- **`@_require_func` guard for CommonJS patterns**: Matches that include a `_require_func` capture are validated to ensure the function name is literally `"require"`, filtering out false positives from query matches on non-require call expressions.

## Definition Design Specifications

# Definition Design Specifications

---

## `ImportInfo` (dataclass)

**Signature:** `@dataclass class ImportInfo`

**Responsibility:** A plain data container representing a single parsed import statement, carrying all structured information extracted from the AST node.

**When to use:** Instantiated exclusively by `extract_imports` and consumed by callers in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to resolve import relationships.

### Fields

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source — module name, file path, or header name, with quotes/brackets already stripped |
| `names` | `list[str]` | Names explicitly brought into scope (e.g., `from X import Y, Z`); empty list for plain module imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The local alias assigned to the whole module (`Y` in `import X as Y`); `None` if absent |
| `alias_map` | `dict[str, str] \| None` | Maps each aliased name to its original name (`{"path_join": "join"}`); `None` when no per-name aliasing is present |

**Design decisions:**
- `names` uses an empty list (not `None`) so callers can always iterate without null checks.
- `alias_map` and `module_alias` are optional (`None` default) to avoid allocating mappings for the common no-alias case.

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

**Responsibility:** Runs a tree-sitter query against an AST and consolidates all matched import nodes into a deduplicated list of `ImportInfo` objects.

**When to use:** Called once per source file whenever a caller needs the complete set of resolved import statements for that file.

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `root_node` | `Node` | Root of the tree-sitter AST for the entire file |
| `language` | `Language` | tree-sitter `Language` instance needed to compile the query |
| `import_query_str` | `str \| None` | S-expression query string; passing `None` produces an empty result without error |

**Returns:** `list[ImportInfo]` — one entry per unique `(module, line)` pair; multiple `@name` captures from the same statement are merged into a single entry.

**Design decisions:**
- Grouping key `(module, line)` ensures that `from X import A, B` produces one `ImportInfo` with `names=["A", "B"]` rather than two separate entries.
- CommonJS `require()` filtering is enforced via a `@_require_func` capture: matches where the captured function identifier is not literally `"require"` are skipped entirely.
- Java/Kotlin wildcard imports (`*` or `asterisk` child node types) are detected directly from the import node's children rather than through a dedicated query capture, appending `"*"` to `names`.
- Duplicate name entries within a single import are suppressed at insertion time.

**Constraints & edge cases:**
- Returns `[]` immediately when `import_query_str` is `None` or empty without creating a query object.
- Relies on the query string to use the capture names `@module`, `@name`, `@import_node`, and optionally `@_require_func`; unrecognised capture names are silently ignored.
- Line numbers fall back to the `@module` node's position when `@import_node` is not captured.

---

## `_detect_module_alias`

**Signature:**
```
_detect_module_alias(
    module_node: Node,
    import_nodes: list[Node],
) -> str | None
```

**Responsibility:** Resolves the whole-module alias name from the AST, covering the distinct structural patterns used by Python and Kotlin.

**When to use:** Called internally by `extract_imports` for every matched import to populate `ImportInfo.module_alias`.

**Design decisions:**
- Python aliasing is detected by inspecting `module_node`'s **parent** node type (`aliased_import`) rather than the import-level node, because the alias field sits one level above the module name.
- Kotlin aliasing is detected by inspecting a named `alias` child of the import node and then searching its children for `simple_identifier` or `identifier` types, reflecting Kotlin's `import_alias` grammar structure.

**Constraints & edge cases:**
- Returns `None` when neither language pattern matches; callers must handle `None`.
- `import_nodes` may be an empty list; the Kotlin branch is only entered when at least one element is present.

---

## `_resolve_imported_name`

**Signature:**
```
_resolve_imported_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the name as it will actually be referenced in downstream code — the alias if one is present, otherwise the declared name.

**When to use:** Called internally by `extract_imports` for each `@name` capture node to determine what string to store in `ImportInfo.names`.

**Design decisions:**
- Python's `aliased_import` node type is handled by field-name introspection (`alias`, then `name`), with a final fallback to the full node text.
- JS/TS aliasing is resolved via the **parent** node (`import_specifier` / `export_specifier`) rather than the capture node itself, because the alias field lives at the specifier level.

**Constraints & edge cases:**
- Never returns `None` in practice (the final fallback decodes the raw node text), but the return type is declared `str | None` to match the optional-result contract.

---

## `_get_original_name`

**Signature:**
```
_get_original_name(name_node: Node) -> str | None
```

**Responsibility:** Returns the pre-alias definition name only when aliasing is present, so callers can distinguish "aliased" from "not aliased" without comparing two strings.

**When to use:** Called internally by `extract_imports` alongside `_resolve_imported_name` to populate `ImportInfo.alias_map` when a per-name alias exists.

**Design decisions:**
- Returns `None` (not the name itself) when no alias exists, making the presence of a non-`None` return value an unambiguous signal that aliasing occurred.
- For JS/TS specifiers, the original name is taken from `name_node.text` (the captured identifier) while the alias comes from the parent specifier's `alias` field — the inverse of what `_resolve_imported_name` returns for the same node.

**Constraints & edge cases:**
- Returns `None` for plain imports without aliases; callers must guard before inserting into `alias_map`.

---

## `_strip_quotes`

**Signature:**
```
_strip_quotes(text: str) -> str
```

**Responsibility:** Normalises a raw module string captured from the AST by removing surrounding quote characters or angle brackets introduced by different language syntaxes.

**When to use:** Called internally by `extract_imports` on every `@module` capture before storing the module name in `ImportInfo`.

**Design decisions:**
- Handles three delimeter pairs: double quotes, single quotes, and `<>` (C/C++ system headers).
- Languages that express module paths without any delimiters (Python, Java, Kotlin) pass through unchanged.

**Constraints & edge cases:**
- Only strips a **single** matched pair; strings shorter than two characters are returned as-is.
- Does not handle nested or mismatched delimiters.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal dependencies**. All imports in the source code are from external packages (`dataclasses` from the standard library, `tree_sitter` as a third-party package). There are no imports from other modules within the `codetwine` project.

## Dependents (modules that import this file)

Three project-internal modules depend on this file, each importing and using `extract_imports`:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to parse import statements from a file's AST, feeding the results into `build_symbol_to_file_map` to construct a mapping from imported symbol names to their resolved dependency files.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to retrieve the import list from a caller file's AST, enabling subsequent analysis of which external symbols are referenced in that file.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports_py/imports.py` : Uses `extract_imports` to enumerate all import statements in a source file, then resolves each `ImportInfo.module` to a project-internal file path in order to build callee relationships in the dependency graph.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports_py/imports.py` (one-way)
- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports_py/imports.py` (one-way)
- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports_py/imports.py` (one-way)

This file (`imports.py`) does not import from any of these dependents. It acts purely as a provider of import-extraction functionality, with no reverse dependencies on the modules that consume it.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Type | Description |
|---|---|---|
| `root_node` | `Node` | The root AST node of a parsed source file, produced by tree-sitter |
| `language` | `Language` | A tree-sitter `Language` object used to compile the query |
| `import_query_str` | `str \| None` | An S-expression query string that defines how to match import statements in the AST; sourced from an external config (e.g., `IMPORT_QUERIES`) |

When `import_query_str` is `None`, no processing occurs and an empty list is returned immediately.

---

## 2. Transformation Overview

```
AST root node + language + query string
        │
        ▼
┌─────────────────────────────────────┐
│  Stage 1: Query Compilation         │
│  Query(language, import_query_str)  │
│  → compiled Query object            │
│  → QueryCursor for AST traversal    │
└──────────────────┬──────────────────┘
                   │ cursor.matches(root_node)
                   ▼
┌─────────────────────────────────────┐
│  Stage 2: Match Filtering           │
│  Per match:                         │
│  - Skip if @_require_func present   │
│    and text ≠ "require"             │
│  - Skip if no @module capture       │
└──────────────────┬──────────────────┘
                   │ valid captures dict
                   ▼
┌─────────────────────────────────────┐
│  Stage 3: Raw Data Extraction       │
│  - module text → _strip_quotes()    │
│  - line number from @import_node    │
│    or fallback to @module node      │
│  - group_key = (module, line)       │
└──────────────────┬──────────────────┘
                   │ (module, line) keyed grouping
                   ▼
┌─────────────────────────────────────┐
│  Stage 4: ImportInfo Construction   │
│  - Create or retrieve grouped entry │
│  - _detect_module_alias() →         │
│    module_alias field               │
│  - Per @name node:                  │
│    _resolve_imported_name() →       │
│      alias_name for names list      │
│    _get_original_name() →           │
│      original_name for alias_map    │
│  - Wildcard (*) child detection     │
│    → appends "*" to names           │
└──────────────────┬──────────────────┘
                   │
                   ▼
        list[ImportInfo]
```

**Grouping mechanism:** Multiple `@name` captures from the same import statement (e.g., `from X import Y, Z`) are merged into a single `ImportInfo` by keying on `(module_string, line_number)`. The `grouped` dict accumulates names incrementally across matches that share the same key.

---

## 3. Outputs

| Output | Type | Description |
|---|---|---|
| Return value of `extract_imports` | `list[ImportInfo]` | One `ImportInfo` per distinct import statement, with all imported names consolidated |

The return value is consumed by callers in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` to resolve module dependencies, build symbol-to-file maps, and construct dependency graphs. There are no file writes or other side effects.

---

## 4. Key Data Structures

### `ImportInfo` (dataclass)

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source path or module name, with surrounding quotes/angle brackets stripped |
| `names` | `list[str]` | Names imported from the module (the `Y` in `from X import Y`); empty list for bare `import X` style; contains `"*"` for wildcard imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias assigned to the module itself (the `Y` in `import X as Y`); `None` when no alias |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for aliased named imports (e.g., `from X import a as b` → `{"b": "a"}`); `None` when no aliased names exist |

### `grouped` (internal dict)

| Key | Type | Purpose |
|---|---|---|
| `(module, line)` | `tuple[str, int]` | Composite key ensuring that multiple `@name` captures from the same import statement are accumulated into one `ImportInfo` rather than producing duplicate entries |

### `captures` (per-match dict from tree-sitter)

| Key | Type | Purpose |
|---|---|---|
| `"module"` | `list[Node]` | Nodes matching `@module`; first element holds the module path text |
| `"name"` | `list[Node]` | Nodes matching `@name`; each represents one imported name or aliased import |
| `"import_node"` | `list[Node]` | Nodes matching `@import_node`; the full import statement, used for line number and wildcard/alias detection |
| `"_require_func"` | `list[Node]` | Nodes matching `@_require_func`; used exclusively to filter out non-`require` CommonJS call expressions |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **graceful degradation** approach. Rather than raising exceptions on unexpected or missing data, the functions return empty collections or `None` values, allowing callers to continue processing unaffected. There are no try-except blocks; the defensive logic is expressed entirely through guard conditions and optional return types.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing query string | `import_query_str` is `None` or empty | Returns an empty list immediately | Yes | No imports extracted for that file; callers receive `[]` |
| Missing `@module` capture | A query match contains no `module` captures | `continue` skips the match entirely | Yes | That match is discarded; other matches are processed normally |
| Unrecognized `require()` call | `@_require_func` capture exists but its text is not `"require"` | `continue` skips the match entirely | Yes | Non-require calls are silently ignored; no imports lost |
| Missing parent node | `module_node.parent` is `None` during alias detection | Guard check (`if parent and ...`) returns `None` | Yes | Alias is not recorded; import is still extracted without alias |
| No alias field on aliased node | `child_by_field_name("alias")` returns `None` | Falls through to next resolution strategy or returns `None` | Yes | Name or alias is resolved via fallback; `alias_map` is not populated |
| Missing `name` field on `aliased_import` | `child_by_field_name("name")` returns `None` | Falls back to returning the full node text | Yes | Raw node text used as the name; extraction continues |
| Quote-free module string | Module text has no surrounding quotes or angle brackets | `_strip_quotes` returns the string unchanged | Yes | No data loss; module name used as-is |
| Short module string | Module text is fewer than 2 characters | `_strip_quotes` skips stripping | Yes | String returned as-is; no error |

---

## 3. Design Notes

- **No exceptions are raised internally.** All error conditions are expressed as early returns of empty values (`[]`, `None`) or conditional skips (`continue`), making the extractor safe to call from all three dependent files (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) without requiring any error handling on the caller side.
- **Optional fields use `None` as the absent sentinel.** `module_alias` and `alias_map` on `ImportInfo` default to `None`, so callers can distinguish between "no alias recorded" and "alias recorded but empty" without risking `KeyError` or `AttributeError`.
- **Deduplication is a correctness guard, not an error handler.** The `if alias_name not in grouped[group_key].names` check prevents duplicate name entries when the same name appears in multiple query matches, treating redundancy as a normal condition rather than an error.
- **The design assumes well-formed AST input.** Because tree-sitter produces a complete AST even for syntactically broken source files, the absence of try-except blocks is consistent with the expectation that node access operations will not raise exceptions under normal use.

## Summary

**codetwine/extractors/imports.py** — Extracts import statements from tree-sitter ASTs into structured objects for dependency resolution.

**Public interface:**
- `ImportInfo` (dataclass): `module: str`, `names: list[str]`, `line: int`, `module_alias: str|None`, `alias_map: dict[str,str]|None`
- `extract_imports(root_node: Node, language: Language, import_query_str: str|None) → list[ImportInfo]`

Groups matches by `(module, line)` key; merges multiple `@name` captures per statement. Consumed by `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`.
