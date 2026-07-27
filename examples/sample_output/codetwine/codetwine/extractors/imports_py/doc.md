# Design Document: codetwine/extractors/imports.py

# Overview & Purpose

## Role and Responsibility

`codetwine/extractors/imports.py` provides a **language-agnostic import extraction layer** built on top of `tree-sitter` query matching. Instead of writing custom AST-walking logic for every supported language (Python, JavaScript/TypeScript, Java, Kotlin, C/C++, etc.), this module relies on tree-sitter query strings (defined externally, e.g. in `config.py` via `IMPORT_QUERIES`) that uniformly capture import-related nodes using standardized capture names (`@module`, `@name`, `@import_node`, `@_require_func`). The module then normalizes these captures into a single, language-independent data structure (`ImportInfo`).

This file exists as a separate module because import extraction is a distinct, reusable concern needed by multiple downstream components (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) that all need to resolve module dependencies and imported symbol names without duplicating language-specific parsing logic.

## Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `ImportInfo` (dataclass) | `module: str`, `names: list[str]`, `line: int`, `module_alias: str \| None = None`, `alias_map: dict[str, str] \| None = None` | — | Holds normalized data for a single import statement (source module, imported names, line number, module alias, and alias→original name mapping). |
| `extract_imports` | `root_node: Node`, `language: Language`, `import_query_str: str \| None` | `list[ImportInfo]` | Runs a tree-sitter query against the AST, groups matches by (module, line), resolves aliases and wildcard imports, and returns a consolidated list of `ImportInfo` objects (empty list if no query is provided). |

### Internal Helper Functions (not part of the public API, but support the above)

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `_detect_module_alias` | `module_node: Node`, `import_nodes: list[Node]` | `str \| None` | Detects the alias in `import X as Y` for Python (`aliased_import`) and Kotlin (`import_alias`). |
| `_resolve_imported_name` | `name_node: Node` | `str \| None` | Returns the name actually used in code (alias if present) from a `@name` capture, for Python and JS/TS import specifiers. |
| `_get_original_name` | `name_node: Node` | `str \| None` | Returns the pre-alias original name, or `None` if the import has no alias. |
| `_strip_quotes` | `text: str` | `str` | Strips surrounding quotes (`"`, `'`) or angle brackets (`<`, `>`) from module path strings, leaving unquoted names (Python, Java) unchanged. |

## Design Decisions

- **Query-driven, uniform extraction**: Rather than implementing per-language AST traversal, the module delegates syntax differences to externally supplied tree-sitter query strings and only interprets a small, fixed set of capture names (`@module`, `@name`, `@import_node`, `@_require_func`), keeping the extraction logic language-agnostic.
- **Grouping by (module, line)**: Multiple `@name` captures belonging to the same import statement (e.g., `from X import Y, Z`) are consolidated into a single `ImportInfo` using a dictionary keyed by `(module, line)`, avoiding duplicate entries.
- **Special-case filtering for CommonJS `require()`**: An optional `@_require_func` capture is used to filter out matches where the called function is not literally `require`, preventing false positives from similarly shaped call expressions.
- **Explicit alias tracking**: Aliased imports are represented by storing the alias as the effective name in `names` while preserving the original name via `alias_map`, allowing downstream consumers to resolve renamed imports back to their source symbols.
- **Wildcard import support**: Java (`asterisk`) and Kotlin (`*`) wildcard imports are detected structurally by inspecting `import_node` children and represented uniformly as the literal string `"*"` in `names`.
- **Graceful degradation**: If no import query is defined for a language (`import_query_str is None`), the function safely returns an empty list rather than raising an error, allowing callers to handle unsupported languages uniformly.

# Definition Design Specifications

## `ImportInfo` (dataclass)

**Fields:** `module: str`, `names: list[str]`, `line: int`, `module_alias: str | None = None`, `alias_map: dict[str, str] | None = None`

**Responsibility:** Serves as a language-agnostic, normalized representation of a single import statement, decoupling downstream consumers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) from the syntactic differences between languages.

**Design decisions:**
- `names` is a list rather than a single value because a single import statement can import multiple names (e.g., `from X import a, b`); languages without named imports simply leave it empty.
- `module_alias` and `alias_map` are optional/nullable because aliasing is not universal across languages (e.g., Python/Kotlin support `import X as Y`, but many languages do not); using `None` as the default avoids forcing empty containers on every instance.
- `alias_map` maps alias → original name (not the reverse) because downstream symbol resolution needs to translate a locally-used name back to its source-defined name.

**Constraints:** `line` is 1-based to match typical editor/tooling conventions.

---

## `extract_imports(root_node, language, import_query_str) -> list[ImportInfo]`

**Arguments:**
- `root_node: Node` — AST root covering the entire source file.
- `language: Language` — tree-sitter `Language` object required to compile the query.
- `import_query_str: str | None` — tree-sitter query string defining how to capture imports for the target language; `None` signals that no import query is defined for this language.

**Return value:** `list[ImportInfo]` — one entry per distinct import statement (module + line), with multi-name imports consolidated.

**Responsibility:** Acts as the single, language-agnostic entry point for import extraction, translating heterogeneous per-language import syntax into a uniform list of `ImportInfo` via tree-sitter queries, so callers never need language-specific parsing logic.

**Design decisions:**
- Uses `(module, line)` as the grouping key rather than just `module`, because the same module could theoretically be imported at multiple points, and grouping by line ensures each distinct statement produces its own `ImportInfo` while still merging multiple `@name` captures belonging to the same statement.
- Relies entirely on tree-sitter query capture names (`@module`, `@name`, `@import_node`) as the contract with the query definitions in `config.py`, keeping this function decoupled from any single grammar's node types (aside from the alias-resolution helpers).
- Special-cased handling of a `@_require_func` capture filters out matches where the invoked function is not literally named `require`, to avoid false positives from the CommonJS `require()` pattern.
- Wildcard imports (Java `asterisk`, Kotlin `*`) are normalized into a literal `"*"` entry in `names`, giving callers a consistent way to detect "import everything" regardless of language.
- Deduplicates resolved names within a group to avoid inflating `names` when a query fires multiple overlapping matches for the same name.

**Edge cases / constraints:**
- Returns an empty list immediately if `import_query_str` is `None`, allowing languages with no import concept/query to be handled uniformly by callers.
- If a match has no `@module` capture, it is skipped entirely — a module reference is treated as mandatory for a valid import entry.
- Line number falls back to the `@module` node's position when no `@import_node` capture is present.

---

## `_detect_module_alias(module_node, import_nodes) -> str | None`

**Arguments:**
- `module_node: Node` — the node captured by `@module`.
- `import_nodes: list[Node]` — nodes captured by `@import_node` (may be empty).

**Return value:** `str | None` — the alias name (the `Y` in `import X as Y`), or `None` if no alias is present.

**Responsibility:** Encapsulates the language-specific structural knowledge needed to detect a module-level alias, since Python and Kotlin express this via different AST shapes (a parent `aliased_import` node vs. a sibling `import_alias` field).

**Design decisions:** Checks Python's structure first (via `module_node.parent`) and falls back to Kotlin's structure (via `import_nodes[0]`'s `alias` field) — the two checks are independent and non-conflicting since they inspect different node relationships, allowing the function to serve both languages without an explicit language parameter.

**Edge cases:** Returns `None` silently if neither pattern matches (i.e., languages without module aliasing, or import statements without an alias).

---

## `_resolve_imported_name(name_node) -> str | None`

**Arguments:** `name_node: Node` — the node captured by `@name`.

**Return value:** `str | None` — the name as it is actually referenced in code (post-alias if aliased); effectively never `None` in practice since it falls back to the raw node text, but typed as optional for consistency with related helper `_get_original_name`.

**Responsibility:** Normalizes "what name should be used when tracking usages" across Python's `aliased_import` construct and JS/TS's `import_specifier`/`export_specifier` alias fields, so callers get the effective (aliased) name uniformly.

**Design decisions:** Checks node type (`aliased_import`) before checking parent type (`import_specifier`/`export_specifier`) since these are mutually exclusive grammar shapes across the two language families this function supports; defaults to the raw node text when no alias structure is found, which correctly handles non-aliased imports.

---

## `_get_original_name(name_node) -> str | None`

**Arguments:** `name_node: Node` — the node captured by `@name`.

**Return value:** `str | None` — the original (pre-alias) name, or `None` when there is no alias (signaling to the caller that alias tracking is unnecessary for this name).

**Responsibility:** Complements `_resolve_imported_name` by extracting the source-defined name when aliasing occurs, enabling `extract_imports` to build the `alias_map` for later symbol resolution back to original definitions.

**Design decisions:** Returning `None` in the no-alias case (rather than duplicating the resolved name) is intentional — it lets `extract_imports` distinguish "no alias" from "aliased to the same name," avoiding redundant entries in `alias_map`.

---

## `_strip_quotes(text) -> str`

**Arguments:** `text: str` — the raw module string captured by the query (may include surrounding quote or angle-bracket characters).

**Return value:** `str` — the module string with surrounding quotes (`"`, `'`) or angle brackets (`<`, `>`) removed; returned unchanged if no such wrapping is present.

**Responsibility:** Normalizes module path literals across languages that quote/bracket import paths (JS/TS string literals, C/C++ `<...>`/`"..."` includes) versus those that don't (Python, Java), so `ImportInfo.module` is always a clean, comparable path/name string.

**Design decisions:** Checks matching quote character pairs (`"..."` or `'...'`) and angle-bracket pairs (`<...>`) explicitly rather than generic trimming, ensuring only well-formed wrapped strings are stripped.

**Edge cases:** Strings shorter than length 2 are returned as-is, avoiding index errors on empty or single-character input.

# Dependency Description

## Dependencies (what this file uses)

This file relies solely on the `tree_sitter` library (`Language`, `Query`, `QueryCursor`, `Node`) to build and execute S-expression queries against a language-specific AST, and to traverse/inspect syntax nodes. No project-internal file dependencies exist; the module is self-contained within `codetwine/extractors/imports.py`.

## Dependents (what uses this file)

No project-internal files are used by this module, but the following files depend on it:

- **`codetwine/file_analyzer.py`**: Calls `extract_imports` to obtain the list of import statements from a parsed source file's AST, which is then used to build a mapping from imported symbol names to their originating dependency files (`build_symbol_to_file_map`).
- **`codetwine/extractors/usage_analysis.py`**: Calls `extract_imports` on a caller file's root AST node to retrieve the caller's import list, which is used as part of usage/reference analysis across files.
- **`codetwine/extractors/dependency_graph.py`**: Calls `extract_imports` on a file's parsed AST to obtain its imports, then resolves each import's module path to a project file path in order to construct callee relationships in the dependency graph.

The dependency direction is unidirectional: `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` all depend on `imports.py` for import extraction functionality, while `imports.py` has no dependency on any of these files.

# Data Flow

## Input

| Source | Type | Description |
|---|---|---|
| `root_node` | `tree_sitter.Node` | Root of the parsed AST for a source file, produced upstream by the file parser (e.g. `parse_file`) |
| `language` | `tree_sitter.Language` | Language grammar object used to compile the query |
| `import_query_str` | `str \| None` | Language-specific tree-sitter query string (from `IMPORT_QUERIES` config), defining `@module`, `@name`, `@import_node`, `@_require_func` capture points |

If `import_query_str` is `None`, processing short-circuits and returns an empty list immediately.

## Transformation Flow

```
root_node + language + query_str
        │
        ▼
Query/QueryCursor compiled from grammar
        │
        ▼
cursor.matches(root_node) → iterate (match, captures) pairs
        │
        ▼
For each match:
  1. Filter out CommonJS false positives (@_require_func text != "require")
  2. Extract @module, @name, @import_node capture lists
  3. Skip match if no @module captured
  4. Decode module text, strip quotes/angle-brackets → module string
  5. Compute line number (from @import_node, fallback to @module)
  6. Build group_key = (module, line) → get-or-create ImportInfo in `grouped` dict
  7. Detect "import X as Y" module alias → set ImportInfo.module_alias
  8. For each @name capture:
       - resolve alias-used name (_resolve_imported_name)
       - resolve original pre-alias name (_get_original_name)
       - append alias name to ImportInfo.names (dedup)
       - if aliased, record alias→original mapping in ImportInfo.alias_map
  8. Detect wildcard import (asterisk/* child under @import_node) → append "*" to names
        │
        ▼
grouped dict values collected
        │
        ▼
return list[ImportInfo]
```

Multiple `@name` captures belonging to the same statement (same `module` + `line`) are merged into one `ImportInfo` via the `grouped` dict, so the transformation is essentially a **grouping/reduction** of raw AST capture tuples into consolidated import records.

## Output

`list[ImportInfo]` — returned to callers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`), which use it to:
- build a symbol-to-source-file map (`file_analyzer.py`)
- analyze caller-side imports for usage resolution (`usage_analysis.py`)
- resolve import modules to project file paths for dependency graph edges (`dependency_graph.py`)

## Key Data Structures

### `ImportInfo` (dataclass) — final output unit
| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | Import source (module name/path/header), quotes/brackets stripped |
| `names` | `list[str]` | Names imported from the module (as used in code); `"*"` denotes wildcard import |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | Alias for the whole module (`import X as Y`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for individually aliased imports |

### `grouped` (internal working structure)
- Type: `dict[tuple[str, int], ImportInfo]`
- Key: `(module, line)` — uniquely identifies one import statement
- Value: the in-progress/final `ImportInfo` being accumulated across multiple `@name` captures within the same match set
- Purpose: consolidation buffer that is discarded after conversion to `list[ImportInfo]` at return time

### `captures` (per-match, from tree-sitter)
- Type: `dict[str, list[Node]]`
- Keys used: `"module"`, `"name"`, `"import_node"`, `"_require_func"`
- Purpose: raw AST node references per query capture label, consumed to derive text/position values before being discarded

# Error Handling

## Overall Strategy

This module follows a **graceful degradation** policy rather than fail-fast. It is designed to tolerate missing, malformed, or language-specific syntax variations without raising exceptions, returning empty results or partial data instead. There are no `try`/`except` blocks in this file; instead, error handling is implemented through defensive conditional checks (existence checks, membership checks, type checks) before accessing node data. This reflects the module's role as a best-effort parser layer feeding downstream consumers (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) that expect a plain list of `ImportInfo`, even if incomplete.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| No import query defined for a language (`import_query_str is None`) | Immediately returns an empty list (`[]`) | Callers receive no import data for that file/language; downstream logic treats it as "no imports" rather than an error |
| Query match missing `@module` capture | The match is skipped via `continue` | Partial/invalid matches do not corrupt the grouped results; only well-formed matches contribute entries |
| Query match missing `@import_node` capture | Falls back to using the `@module` node's position for line number calculation | Line numbers remain populated; slight reliance on module node position instead of the full statement |
| CommonJS `require()` filtering: `@_require_func` capture present but text is not `"require"` | The match is skipped via `continue` | Prevents non-`require` function calls from being misidentified as imports |
| Missing alias/name fields when resolving aliases (`_detect_module_alias`, `_resolve_imported_name`, `_get_original_name`) | Each helper checks for `None` at every `child_by_field_name` step and falls back to returning `None` or the raw node text | Alias information degrades gracefully (e.g., `alias_map` stays `None` or omits an entry) instead of raising `AttributeError` |
| Duplicate `@name` captures within the same import statement | Names are deduplicated via `if alias_name not in grouped[group_key].names` check before appending | Prevents duplicate entries in `ImportInfo.names`; does not raise or halt processing |
| Module string without surrounding quotes/brackets (`_strip_quotes`) | Length and character checks guard the stripping logic; if conditions don't match, the original text is returned unchanged | Languages without quoted import syntax (e.g., Python, Java) pass through unaffected |
| Malformed or unexpected tree-sitter query string | Not handled locally; `Query(language, import_query_str)` would propagate any exception from the tree-sitter binding | Would surface as an unhandled exception to the caller, since no validation is performed prior to query construction |

## Design Considerations

- The consolidation strategy using `(module, line)` as a grouping key implicitly handles multiple `@name` captures for a single import statement without requiring dedicated error handling for "duplicate module" scenarios.
- Defensive `None`/existence checks are used pervasively instead of exception handling, reflecting an assumption that tree-sitter grammars may produce optional or absent child nodes across different language grammars (Python, JS/TS, Kotlin, Java, C/C++).
- The module does not perform validation on the syntactic correctness of the query string itself or on the `language` object; it relies on the caller to supply valid tree-sitter query definitions (from `IMPORT_QUERIES` in `config.py`), consistent with the module's stated design of trusting pre-validated inputs from the calling layer.

# Summary

`imports.py` provides language-agnostic import extraction using tree-sitter queries. Main API: `extract_imports(root_node, language, import_query_str) -> list[ImportInfo]`, which runs a query, groups matches by (module, line), resolves aliases/wildcards, and returns empty list if no query exists. `ImportInfo` dataclass holds module, names, line, module_alias, alias_map. Internal helpers handle alias/name resolution and quote stripping. No internal dependencies; used by file_analyzer.py, usage_analysis.py, dependency_graph.py for symbol mapping, usage analysis, and dependency graph construction.
