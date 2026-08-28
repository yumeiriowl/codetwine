# Design Document: codetwine/extractors/imports.py

# Overview & Purpose

## 1. Module Summary
Extract import/dependency statements from a language-agnostic tree-sitter AST into a normalized list of `ImportInfo` records using per-language tree-sitter queries.

## 2. When to Use This Module
- When you need to identify what modules/files a source file depends on: call `extract_imports(root_node, language, import_query_str)` to get a list of `ImportInfo`, each describing one import statement (module, imported names, line number, aliasing info).
- When building a symbol-to-file resolution map (e.g., `file_analyzer.py` uses this to map imported names to project files) and you need both the imported module path and the names/aliases brought into scope.
- When performing cross-file usage analysis (e.g., `usage_analysis.py`) and you need to know, for a caller file, which modules and symbols it imports in order to trace call sites back to their definitions.
- When constructing a project-wide dependency graph (e.g., `dependency_graph.py`) and you need each file's list of imported modules to resolve them into edges between project files.
- When you need to distinguish an aliased import name from its original name (e.g., `from X import join as path_join`), use the `alias_map` field on `ImportInfo` rather than re-parsing the AST yourself.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `ImportInfo` | `module` (str), `names` (list[str]), `line` (int), `module_alias` (str \| None), `alias_map` (dict[str, str] \| None) | — (dataclass) | Holds normalized data for a single import statement: source module, imported names, statement line number, module-level alias (e.g. `import X as Y`), and a mapping of imported alias names to their original names. |
| `extract_imports` | `root_node` (Node), `language` (Language), `import_query_str` (str \| None) | `list[ImportInfo]` | Runs a tree-sitter query against the AST to find import statements, groups multi-name imports from the same statement into a single `ImportInfo`, resolves aliases (module-level and name-level), and detects wildcard imports (Java/Kotlin `*`). Returns an empty list if no query string is provided. |

## 4. Design Decisions
- Uses tree-sitter S-expression queries (via `IMPORT_QUERIES` supplied externally) as a uniform mechanism to handle syntactically different import forms across languages, relying on standardized capture names (`@module`, `@name`, `@import_node`) rather than per-language parsing logic.
- Groups query matches by `(module, line)` so that multiple `@name` captures belonging to the same import statement (e.g., `from X import Y, Z`) are consolidated into one `ImportInfo` instead of duplicated entries.
- Filters CommonJS-style `require()` calls generically via an optional `@_require_func` capture, checking the captured function name equals `"require"` to avoid false positives from similarly-shaped call expressions.
- Alias resolution and original-name lookup are implemented as separate internal helpers per language construct (Python `aliased_import`, JS/TS `import_specifier`/`export_specifier`, Kotlin `import_alias`), keeping the public `extract_imports` function language-agnostic while delegating syntax-specific detail to private helpers.

# Definition Design Specifications

## `ImportInfo` (dataclass)

**Signature:** `@dataclass class ImportInfo`

**Responsibility:** Represents a single normalized import statement extracted from source code, providing a language-agnostic structure for downstream consumers (dependency graph builders, symbol resolvers).

**When to use:** Instantiated internally by `extract_imports` for each distinct import statement found in an AST; consumed by callers as return values, not typically constructed directly by external code.

**Fields:**

| Field | Type | Purpose |
|---|---|---|
| `module` | `str` | The import source module name or path (quotes/brackets stripped) |
| `names` | `list[str]` | Names imported from the module (e.g., `from X import a, b`); empty for languages/statements without named imports |
| `line` | `int` | 1-based line number of the import statement in the source file |
| `module_alias` | `str \| None` | The alias assigned to the whole module (the `Y` in `import X as Y`); `None` if no alias |
| `alias_map` | `dict[str, str] \| None` | Maps each aliased imported name to its original name (e.g., `{"path_join": "join"}`); `None` if no aliases present among `names` |

**Design decisions:** Uses a single unified schema across multiple languages (Python, JS/TS, Java, Kotlin, C/C++) rather than per-language subclasses, pushing language-specific parsing logic into helper functions and tree-sitter queries instead of the data model.

**Constraints & edge cases:** `alias_map` is only populated (and defaults to `None` otherwise) when at least one name in `names` differs from its original name.

---

## `extract_imports(root_node, language, import_query_str)`

**Signature:** `extract_imports(root_node: Node, language: Language, import_query_str: str | None) -> list[ImportInfo]`

- `root_node`: tree-sitter AST root node of the file to analyze.
- `language`: tree-sitter `Language` object matching `root_node`'s grammar (needed to compile the query).
- `import_query_str`: A tree-sitter S-expression query string defining capture patterns (`@module`, `@name`, `@import_node`, `@_require_func`), or `None` if the language has no import query defined.
- Returns: list of `ImportInfo`, one per distinct import statement (grouped by module + line).

**Responsibility:** Serves as the single entry point for extracting import statement metadata from any supported language's AST, using tree-sitter queries to abstract over syntax differences.

**When to use:** Called whenever a caller needs the list of imports from a parsed file to resolve dependencies, build a dependency graph, or map imported symbols to their defining files (as done by `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`).

**Design decisions:**
- Groups query matches by `(module, line)` key rather than treating every capture independently, so that a single `from X import a, b` statement produces one `ImportInfo` with multiple `names` instead of duplicate entries.
- Uses a dedicated `@_require_func` capture as a filter mechanism to distinguish genuine CommonJS `require()` calls from other function calls with the same call shape; entries where the captured function name isn't literally `"require"` are discarded.
- Falls back to the `@module` node's line number when no `@import_node` capture is present, tolerating simpler queries that don't capture the whole statement.
- Deduplicates names within a group (`if alias_name not in grouped[group_key].names`) to guard against queries that might match the same name node more than once.
- Detects Java/Kotlin wildcard imports (`*`) by scanning the `@import_node`'s direct children for `asterisk`/`*` node types, appending a literal `"*"` sentinel into `names`.

**Constraints & edge cases:**
- Returns an empty list immediately if `import_query_str` is `None` (i.e., unsupported language), without attempting to build a `Query`.
- Skips any match lacking a `@module` capture entirely (an `ImportInfo` cannot be constructed without a module name).
- Order of returned list depends on dict insertion order (first-seen module+line pairs), not necessarily source order across non-grouped statements from different queries.
- Relies on `language` and `import_query_str` being mutually compatible; passing a mismatched grammar/query will raise at `Query` construction time (not handled internally).

---

## `_detect_module_alias(module_node, import_nodes)`

**Signature:** `_detect_module_alias(module_node: Node, import_nodes: list[Node]) -> str | None`

**Responsibility:** Extracts the alias name assigned to an entire imported module (the `Y` in `import X as Y`), handling two distinct AST shapes (Python's `aliased_import` parent node, Kotlin's `import_alias` child field).

**When to use:** Called once per matched import statement inside `extract_imports` to populate `ImportInfo.module_alias`.

**Design decisions:** Checks the Python-specific parent-node pattern first, then falls back to inspecting `import_nodes[0]`'s `alias` field for the Kotlin-style child structure; iterates the alias field's children looking for `simple_identifier`/`identifier` node types since the alias field itself may wrap the identifier in a container node.

**Constraints & edge cases:**
- Returns `None` if neither pattern matches, or if `import_nodes` is empty and the Python parent pattern doesn't apply.
- Assumes at most one alias per statement; only checks `import_nodes[0]`, ignoring any additional import nodes in the list.

---

## `_resolve_imported_name(name_node)`

**Signature:** `_resolve_imported_name(name_node: Node) -> str | None`

**Responsibility:** Determines the name actually usable/referenced in code for a given `@name` capture, preferring the alias over the original name when one exists.

**When to use:** Called for every `@name` capture within a matched import statement to build the `names` list on `ImportInfo`.

**Design decisions:** Branches on `name_node.type` (`aliased_import` for Python) versus checking the parent node type (`import_specifier`/`export_specifier` for JS/TS) since different grammars attach alias information at different tree levels; falls back to raw node text when no special structure is found, and within the Python branch falls back further to the `name` field or full node text if `alias` is absent.

**Constraints & edge cases:**
- Return type is annotated `str | None`, but in practice always returns a string in the final fallback (`name_node.text.decode("utf-8")`); `None` is not actually returned by any code path shown, only reachable if `name_node.text` were absent, which does not occur for a valid `Node`.
- Depends on parent node type checks, so a `@name` capture matched to an unexpected/unhandled grammar shape simply falls through to the last-resort raw text return.

---

## `_get_original_name(name_node)`

**Signature:** `_get_original_name(name_node: Node) -> str | None`

**Responsibility:** Retrieves the original (pre-alias) name for a `@name` capture, returning `None` when no aliasing occurred, so that `ImportInfo.alias_map` is only populated for genuinely aliased names.

**When to use:** Called alongside `_resolve_imported_name` for every `@name` capture to determine whether an alias mapping entry should be recorded.

**Design decisions:** Mirrors the type/parent-based branching of `_resolve_imported_name` but inverts the logic — it explicitly returns `None` when no alias field is present, rather than falling back to raw text, since the purpose is to signal "no alias" distinctly from "same name."

**Constraints & edge cases:**
- For the Python `aliased_import` case, if the `alias` field exists but `name` field does not, returns `None` even though an alias exists (asymmetric with `_resolve_imported_name`'s more permissive fallback).
- For JS/TS, only returns a value when the parent is `import_specifier`/`export_specifier` **and** has an `alias` field; any other node shape returns `None`.

---

## `_strip_quotes(text)`

**Signature:** `_strip_quotes(text: str) -> str`

**Responsibility:** Normalizes raw module-name text captured from the AST by removing surrounding quote characters (`"`, `'`) or angle brackets (`<`, `>`) used in different languages' import syntax (JS/TS string literals, C/C++ header includes).

**When to use:** Called once per matched import statement in `extract_imports` immediately after extracting the raw `@module` capture text, before using it as the `module` field or grouping key.

**Design decisions:** Checks for matching quote/bracket pairs specifically at the first and last character positions rather than using generic strip, ensuring only a single well-formed enclosing pair is removed; falls through to returning the original text unmodified for languages without quoting (Python, Java) or malformed/short input.

**Constraints & edge cases:**
- Requires `len(text) >= 2` to attempt any stripping; strings shorter than 2 characters are returned unchanged.
- Only strips one layer of matching delimiters (first char/last char); does not handle mismatched pairs (e.g., `"foo>`) or nested/multiple layers.

# Dependency Description

### Dependencies (modules this file imports)

This file has no dependencies on other project-internal modules. Its only imports (`dataclasses`, `tree_sitter`) are standard library and third-party packages, which are excluded per instructions.

### Dependents (modules that import this file)

- `codetwine/file_analyzer.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to parse import statements from a file's AST and build a mapping from imported names to the project files that define them (via `build_symbol_to_file_map`), enabling symbol-to-file resolution for downstream analysis.

- `codetwine/extractors/usage_analysis.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to obtain a caller file's list of import statements, which is then used to analyze how imported symbols are used within that caller.

- `codetwine/extractors/dependency_graph.py` → `codetwine/extractors/imports.py` : Uses `extract_imports` to retrieve the imports declared in a file, then resolves each import's module path against the project's file set to add resolvable modules as callees in the dependency graph.

### Dependency Direction

All relationships are **unidirectional**: `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py` each depend on `imports.py` for import-extraction functionality, while `imports.py` itself does not depend on any of these modules or any other project-internal module.

# Data Flow

## 1. Inputs

- **`root_node` (`tree_sitter.Node`)**: The root node of an already-parsed AST for a source file. Supplied by callers who have run the tree-sitter parser beforehand (e.g. `file_analyzer.py`, `dependency_graph.py`, `usage_analysis.py`).
- **`language` (`tree_sitter.Language`)**: The tree-sitter `Language` object matching the AST, required to compile the query string into a `Query`.
- **`import_query_str` (`str | None`)**: A language-specific S-expression query string (sourced from `IMPORT_QUERIES` in `config.py` by callers) describing how to capture import-related nodes (`@module`, `@name`, `@import_node`, `@_require_func`). If `None`, no import query exists for the language.

## 2. Transformation Overview

1. **Guard stage**: If `import_query_str` is falsy, the function short-circuits and returns an empty list (no further processing).
2. **Query compilation**: The query string and `language` are combined into a `Query` object, and a `QueryCursor` is created to run pattern matching against `root_node`.
3. **Match iteration**: The cursor yields matches over the AST; each match provides a `captures` dict mapping capture names (`module`, `name`, `import_node`, `_require_func`) to lists of `Node`s.
4. **CommonJS filtering**: For each match, if a `_require_func` capture is present, its text is checked against the literal `"require"`; non-matching matches are discarded before further processing.
5. **Module/line extraction**: The raw `@module` node text is quote/bracket-stripped (`_strip_quotes`) into a canonical module string. The statement's line number is derived from `@import_node` (preferred) or falls back to the `@module` node's start line.
6. **Grouping**: A `(module, line)` tuple is used as a dictionary key to consolidate multiple captures belonging to the same logical import statement into a single `ImportInfo` entry (created lazily on first encounter).
7. **Module alias resolution**: `_detect_module_alias` inspects the module node's parent (Python `aliased_import`) or the import node's `alias` child (Kotlin `import_alias`) to populate `ImportInfo.module_alias`.
8. **Name/alias resolution (fan-in per group)**: For every `@name` node in the match, `_resolve_imported_name` computes the effectively-used name (alias if present) and `_get_original_name` computes the pre-alias name. Resolved names are appended to `ImportInfo.names` (deduplicated), and when an alias differs from the original, the mapping is recorded into `ImportInfo.alias_map`.
9. **Wildcard detection**: If the import node has a child of type `asterisk` or `*` (Java/Kotlin wildcard imports), the literal string `"*"` is added to `names` (once).
10. **Aggregation to output**: After all matches are processed, the `grouped` dict's values are converted into a `list[ImportInfo]`, one entry per distinct `(module, line)` combination.

There is no async or parallel processing; all matches are processed sequentially and merged into the single `grouped` dict, which acts as the fan-in point for multiple captures/matches referring to the same import statement.

## 3. Outputs

- **Return value**: `list[ImportInfo]` — one `ImportInfo` per distinct import statement (keyed by module + line), each populated with module name, imported names, optional module alias, and optional alias-to-original name mapping.
- **No side effects**: The function performs no file writes, no mutation of `root_node`, and no external state changes; it is a pure transformation from AST + query string to a structured list.
- **Downstream consumption**: The returned list is passed by dependents (`file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`) into further resolution logic (e.g. `build_symbol_to_file_map`, `resolve_module_to_project_path`) to map imports to project files and symbols.

## 4. Key Data Structures

### `ImportInfo` (dataclass) — primary output unit

| Field / Key | Type | Purpose |
|---|---|---|
| `module` | `str` | Import source module name/path, with quotes/brackets stripped |
| `names` | `list[str]` | Names imported from the module (empty for whole-module imports); may include `"*"` for wildcard imports |
| `line` | `int` | 1-based line number of the import statement |
| `module_alias` | `str \| None` | Alias assigned to the whole module (e.g. `Y` in `import X as Y`) |
| `alias_map` | `dict[str, str] \| None` | Maps alias name → original name for individually aliased imports (e.g. `{"b": "a"}` for `from X import a as b`) |

### `grouped` (internal dict) — intermediate aggregation structure

| Field / Key | Type | Purpose |
|---|---|---|
| key: `(module, line)` | `tuple[str, int]` | Uniquely identifies a single import statement to consolidate multiple `@name`/`@module` captures |
| value | `ImportInfo` | The accumulating import record for that statement |

### `captures` (per-match dict, from `QueryCursor.matches`)

| Key | Type | Purpose |
|---|---|---|
| `module` | `list[Node]` | Node(s) captured as the import source/module |
| `name` | `list[Node]` | Node(s) captured as individually imported names |
| `import_node` | `list[Node]` | Node(s) representing the whole import statement (used for line number and wildcard/alias detection) |
| `_require_func` | `list[Node]` | Optional node(s) capturing the function name in CommonJS `require(...)` calls, used only for filtering |

# Error Handling

## 1. Overall Strategy

This module follows a **graceful degradation** strategy rather than raising exceptions or performing explicit validation. There are no `try/except` blocks anywhere in the file. Instead, error handling is achieved through:

- **Early return of empty results** when no usable query is provided (`import_query_str is None`), allowing callers to treat "no imports found" the same as "language not supported."
- **Defensive conditional checks** (`if not module_nodes`, `if alias:`, `if name:`) that skip or short-circuit processing when expected AST structure is absent, rather than assuming tree-sitter capture data is always well-formed.
- **Silent skipping** of malformed or irrelevant matches (e.g., CommonJS `require()` filtering, missing `@module` captures) instead of logging warnings or raising errors.
- **No exception propagation**: any malformed input either produces a partial/empty `ImportInfo` list or is filtered out silently. The function never terminates the calling process.

This design assumes tree-sitter queries and language grammars are trusted inputs (defined internally in `config.py`), so runtime validation focuses on defending against absent or optional AST nodes (e.g., missing alias fields) rather than against invalid queries or corrupted trees.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing import query | `import_query_str` is `None` or empty string | Immediately return `[]` | Yes | Caller receives empty import list; no imports extracted for that language/file |
| Non-`require` function call match | `@_require_func` capture exists but text is not `"require"` | `continue` to skip the match | Yes | That match is excluded from results; other matches still processed |
| Missing `@module` capture | A query match has no `module` capture | `continue` to skip the match | Yes | That match is excluded; grouping/aggregation unaffected for other matches |
| Missing `@import_node` capture | No `import_node` capture present for a match | Falls back to `module_nodes[0].start_point[0] + 1` for line number | Yes | Line number is derived from module node instead of full statement node; no functional failure |
| Missing alias field (Python `aliased_import`) | `child_by_field_name("alias")` returns `None` | Falls back to `name` field, then to raw node text | Yes | Alias detection degrades to plain name resolution |
| Missing `name` field (Python `aliased_import`, no alias) | Both `alias` and `name` fields absent | Falls back to `name_node.text.decode("utf-8")` | Yes | Returns raw text instead of a semantically resolved name |
| Missing Kotlin `import_alias` children | `alias_child` exists but has no matching identifier child type | Loop completes without returning a value; falls through to `None` | Yes | No module alias registered for that import |
| Duplicate/redundant name captures | Same `alias_name` already present in `names` list | Skipped via `if ... not in grouped[group_key].names` check | Yes | Prevents duplicate entries; no error surfaced |
| Malformed/unquoted module string | `_strip_quotes` receives text shorter than 2 chars or without matching quote/bracket pairs | Returns text unchanged | Yes | Module name may retain unexpected characters, but no crash |

## 3. Design Notes

- The module treats **absence of expected structure as a normal, expected case** (e.g., a language without aliasing, no wildcard imports, no `@import_node` capture) rather than as an error condition, reflecting the fact that a single set of query-capture conventions must uniformly support several different language grammars (Python, JS/TS, Java, Kotlin, C/C++).
- Because tree-sitter capture lists (`captures.get(...)`) may legitimately be empty for a given language/pattern, all downstream logic is written defensively using `if` guards and `.get(..., [])` defaults instead of assuming key presence.
- No logging is performed on skipped/malformed matches, keeping the extraction logic silent and side-effect-free; callers (e.g., `file_analyzer.py`, `dependency_graph.py`, `usage_analysis.py`) receive only a well-formed (possibly empty) list and are responsible for any higher-level reporting.
- The grouping mechanism (`grouped` dict keyed by `(module, line)`) inherently tolerates partial or repeated matches from the same import statement, consolidating them rather than treating repeated captures as conflicting or erroneous.

# Summary

Extracts import/dependency statements from a tree-sitter AST into normalized `ImportInfo` records. Public API: `ImportInfo(module: str, names: list[str], line: int, module_alias: str|None, alias_map: dict[str,str]|None)` dataclass; `extract_imports(root_node: Node, language: Language, import_query_str: str|None) -> list[ImportInfo]`, which runs per-language tree-sitter queries, groups matches by (module, line), and resolves aliases/wildcards.
