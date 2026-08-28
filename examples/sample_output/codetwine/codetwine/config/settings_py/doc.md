# Design Document: codetwine/config/settings.py

# Overview & Purpose

## 1. Module Summary
Centralizes all environment-driven configuration and per-language tree-sitter definitions (parsers, definition/import/usage node type mappings, and path-resolution rules) used across the CodeTwine analysis pipeline.

## 2. When to Use This Module
- **Reading runtime configuration**: Import constants like `LLM_MODEL`, `LLM_API_KEY`, `MAX_WORKERS`, `MAX_RETRIES`, or `KNOWLEDGE_FORMAT` instead of calling `os.getenv` directly, ensuring consistent defaults and type conversion.
- **Resolving project paths**: Use `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, or `DOC_TEMPLATE_PATH` to locate the project root, output folder, or the JSON template driving document generation.
- **Parsing source files with tree-sitter**: Look up `TREE_SITTER_LANGUAGES[ext]` to obtain the correct `Language` object for a given file extension before parsing.
- **Extracting definitions (classes/functions/variables)**: Look up `DEFINITION_DICTS[ext]` to know which AST node types represent named definitions in a given language, as done in `file_analyzer.py` and `import_to_path.py`.
- **Extracting import statements**: Look up `IMPORT_QUERIES[ext]` to get the tree-sitter S-expression query for finding import/include statements, and `IMPORT_RESOLVE_CONFIG[ext]` to know how to turn a module string into a file path (separator, index/alt extensions, bare-path fallback, etc.).
- **Tracking symbol usage**: Look up `USAGE_NODE_TYPES[ext]` to know which AST node types represent calls/attribute access and which parent node types should be skipped when scanning for usages of a symbol.
- **Handling implicit same-package visibility (Java/Kotlin)**: Check `SAME_PACKAGE_VISIBLE[ext]` to decide whether files in the same directory should be treated as implicitly importable.
- **Locating source roots in layered project structures**: Use `SOURCE_ROOT_PATTERNS` to strip Maven/Gradle/Python src-layout prefixes when resolving import paths.
- **Filtering scanned files/directories**: Use `EXCLUDE_PATTERNS` to skip directories/files (e.g., `.git`, `node_modules`) during project traversal.
- **Controlling LLM-based documentation behavior**: Use `ENABLE_LLM_DOC`, `ENABLE_CODE_SUMMARY`, `SUMMARY_MAX_CHARS`, `CODE_SUMMARY_MAX_CHARS`, `CODE_SUMMARY_TRIGGER_LINES`, `OUTPUT_LANGUAGE`, and `DOC_MAX_TOKENS` to configure whether/how the LLM is called and how output is trimmed and localized.
- **Managing parse cache size**: Use `PARSE_CACHE_MAX_FILES` to bound the number of cached parse results kept in memory.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key (str)`, `default (Any)`, `var_type (type)` | `str \| int \| float \| bool \| None` | Read and type-convert an environment variable, raising `ValueError` if required and missing |
| `LLM_API_KEY` | — | `str` | API key for the LLM provider |
| `LLM_MODEL` | — | `str` | Model identifier used by the LLM client |
| `LLM_API_BASE` | — | `str` | Base URL/endpoint for the LLM API |
| `OUTPUT_LANGUAGE` | — | `str` | Language in which generated documentation is written |
| `DOC_MAX_TOKENS` | — | `int` | Max tokens allowed per LLM document-generation call |
| `REPO_ROOT` | — | `str` | Absolute path to the repository root |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default directory to analyze when none is specified |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default directory for analysis output |
| `DOC_TEMPLATE_PATH` | — | `str` | Path to the JSON template driving design-document sections |
| `MAX_WORKERS` | — | `int` | Number of parallel workers for document generation/analysis |
| `MAX_RETRIES` | — | `int` | Max retry attempts for LLM calls |
| `RETRY_WAIT` | — | `int` | Wait time (seconds) between LLM retry attempts |
| `PARSE_CACHE_MAX_FILES` | — | `int` | Max number of parse results kept in the LRU parse cache (0 = unlimited) |
| `KNOWLEDGE_FORMAT` | — | `str` | Output format for consolidated knowledge (`json`/`sqlite`/`both`) |
| `ENABLE_LLM_DOC` | — | `bool` | Whether LLM-based document generation is enabled |
| `SUMMARY_MAX_CHARS` | — | `int` | Character limit for file-level summaries |
| `ENABLE_CODE_SUMMARY` | — | `bool` | Whether LLM summarization is used as a context-overflow fallback |
| `CODE_SUMMARY_TRIGGER_LINES` | — | `int` | Line-count threshold for candidate symbols to summarize |
| `CODE_SUMMARY_MAX_CHARS` | — | `int` | Character limit for a single code behavior summary |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns of files/dirs excluded from project traversal |
| `PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, `CPP_DEFINITION_DICT`, `C_DEFINITION_DICT`, `KOTLIN_DEFINITION_DICT`, `JS_DEFINITION_DICT`, `TS_DEFINITION_DICT` | — | `dict[str, str]` | Per-language AST node type → name-node type mapping for definition extraction |
| `LangConfig` | `language (Language)`, `definition_dict (dict[str, str])`, `import_query (str \| None)`, `usage_node_types (dict \| None)`, `import_resolve (dict \| None)`, `same_package_visible (bool)` | — (dataclass) | Bundles all per-language settings into a single frozen record |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Extension → tree-sitter `Language` object |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Extension → definition node mapping dictionary |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Extension → tree-sitter import extraction query string |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Extension → AST node type settings for usage tracking |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Extension → module/import path resolution settings |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Extension → whether implicit same-package references are supported |
| `SOURCE_ROOT_PATTERNS` | — | `list[str]` | Known source-root prefixes (Maven/Gradle/Python layouts) for import resolution |

## 4. Design Decisions
- **Registry pattern**: All per-language settings (parser, definition dict, import query, usage node types, import resolution, package visibility) are consolidated into a single `_LANG_REGISTRY: dict[str, LangConfig]`. Public per-concern dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) are auto-derived from this registry, so adding a new language requires only one new `LangConfig` entry.
- **Extension aliasing**: `_EXT_ALIASES` and `_expand_ext_aliases` let multiple extensions (e.g., `h`, `kts`, `jsx`) share the same `LangConfig` as their canonical extension, avoiding duplicated configuration.
- **Sentinel-based name extraction**: Definition dicts use a `"__sentinel__"`-style special value (e.g., `"__assignment__"`, `"__variable_declarator__"`) to signal that name extraction requires dedicated logic (handled elsewhere in `definitions.py`) rather than a simple direct-child lookup.
- **Required-vs-optional env vars**: `get_config_value` uses a sentinel object `_REQUIRED` (distinct from `None`) to distinguish "no default provided, value is mandatory" from "default is explicitly `None`."

# Definition Design Specifications

## `_REQUIRED`

- **Signature:** `_REQUIRED = object()`
- **Responsibility:** Acts as a unique sentinel to distinguish "no default supplied" from an explicit `None` default in `get_config_value`.
- **When to use:** Used internally as the default value of the `default` parameter of `get_config_value`.
- **Design decisions:** A plain `object()` instance is used instead of `None` because `None` is a valid, meaningful default value that callers may legitimately pass (to mean "return `None` if unset").
- **Constraints & edge cases:** Must remain a module-private singleton; identity comparison (`is`) is required for correct sentinel detection.

---

## `get_config_value(key: str, default=_REQUIRED, var_type: type = str)`

- **Signature:** `get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None`
  - `default`: any value, or the `_REQUIRED` sentinel, or `None`.
  - `var_type`: one of `str`, `int`, `float`, `bool`; determines the conversion applied to the raw string environment value.
- **Responsibility:** Centralizes reading and type-converting environment variables so every setting in the module follows the same override/default/type-casting logic.
- **When to use:** Called once per configuration constant at module import time to derive each public setting from the environment (or its default).
- **Design decisions:**
  - If the variable is missing and `default is _REQUIRED`, raises `ValueError` — this enforces "fail fast" for settings without a safe fallback.
  - If the variable is missing and `default is None`, returns `None` directly without type conversion.
  - If the variable is missing and a non-`None`, non-sentinel default is given, the default is first stringified (`str(default)`) and then passed through the same type-conversion path as an actual env value, ensuring consistent conversion behavior regardless of source.
  - Boolean conversion is truthy-string based (`"true"`, `"1"`, `"yes"`, `"on"`, case-insensitive) rather than using `bool()` casting, avoiding the common Python pitfall where `bool("false")` is `True`.
- **Constraints & edge cases:**
  - `var_type` values other than `bool`/`int`/`float` fall through to returning the raw string.
  - `int()`/`float()` conversion will raise `ValueError` if the environment string is not numeric — no exception is caught here.
  - Not decorated, not async.

---

## LLM Settings Constants

| Name | Type | Env Var | Default | Purpose |
|---|---|---|---|---|
| `LLM_API_KEY` | `str` | `LLM_API_KEY` | `""` | API key used by `LLMClient` for authentication. |
| `LLM_MODEL` | `str` | `LLM_MODEL` | `""` | Model identifier passed to `LLMClient`. |
| `LLM_API_BASE` | `str` | `LLM_API_BASE` | `""` | Base URL for the LLM API endpoint. |
| `OUTPUT_LANGUAGE` | `str` | `OUTPUT_LANGUAGE` | `"English"` | Language in which generated documentation/summaries are written. |
| `DOC_MAX_TOKENS` | `int` | `DOC_MAX_TOKENS` | `8192` | Max token budget for a single LLM generation call. |

**Responsibility:** Configure the LLM client (model, credentials, endpoint) and output language/length behavior for documentation generation.
**When to use:** Read once at import time; consumed by `LLMClient.__init__` and `doc_creator.py` prompt builders.

---

## Path Settings Constants

| Name | Type | Purpose |
|---|---|---|
| `REPO_ROOT` | `str` | Absolute, normalized path to the repository root, computed as two directories above this file. |
| `DEFAULT_PROJECT_DIR` | `str` | Default directory to analyze if `--project-dir` is not passed on the CLI; defaults to `REPO_ROOT`. |
| `DEFAULT_OUTPUT_DIR` | `str` | Default output directory (`REPO_ROOT/output`) for generated docs/knowledge files. |
| `DOC_TEMPLATE_PATH` | `str` | Path to the JSON file describing per-file documentation section templates. |

- **Design decisions:** `REPO_ROOT` is derived purely from `__file__` location rather than an environment variable, guaranteeing a stable anchor regardless of the current working directory; the other three paths are environment-overridable but fall back to paths relative to `REPO_ROOT`.
- **Constraints & edge cases:** No validation that these paths exist; existence checks are deferred to consumers (`main.py`, `doc_creator.py`).

---

## Performance Settings Constants

| Name | Type | Default | Purpose |
|---|---|---|---|
| `MAX_WORKERS` | `int` | `4` | Number of parallel workers used within each dependency-graph level when generating docs. |
| `MAX_RETRIES` | `int` | `3` | Max retry attempts for LLM calls on rate-limit errors. |
| `RETRY_WAIT` | `int` | `2` | Seconds to sleep between retries. |
| `PARSE_CACHE_MAX_FILES` | `int` | `200` | Max number of parsed-file entries kept in the LRU parse cache; `0` disables eviction (unbounded cache for the run). |

**Design decisions:** `PARSE_CACHE_MAX_FILES = 0` is a special sentinel value meaning "unlimited," handled by the consumer (`ts_parser.py`), not by this module.

---

## Output Settings: `KNOWLEDGE_FORMAT`

- **Signature:** `KNOWLEDGE_FORMAT: str` (module-level constant, normalized to lowercase, whitespace-stripped).
- **Responsibility:** Selects which consolidated output artifact(s) (`project_knowledge.json`, `.sqlite`, or both) the pipeline produces.
- **When to use:** Read by `pipeline.py` after per-file analysis to decide which writer functions to invoke.
- **Design decisions:** Validated eagerly at import time against the allowed set `{"json", "sqlite", "both"}`; an invalid value raises `ValueError` immediately rather than failing later during pipeline execution, surfacing misconfiguration early.
- **Constraints & edge cases:** Value is case- and whitespace-insensitive due to `.strip().lower()` normalization before validation.

---

## Analysis Settings Constants

| Name | Type | Default | Purpose |
|---|---|---|---|
| `ENABLE_LLM_DOC` | `bool` | `True` | Master switch for whether design-document generation via LLM runs at all. |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Character budget for a whole-file summary. |
| `ENABLE_CODE_SUMMARY` | `bool` | `True` | Enables LLM-based summarization of oversized code as a context-overflow fallback. |
| `CODE_SUMMARY_TRIGGER_LINES` | `int` | `40` | Line-count threshold above which a definition/dependency symbol becomes a summarization candidate. |
| `CODE_SUMMARY_MAX_CHARS` | `int` | `400` | Character budget for a single code-behavior summary. |

**Design decisions:** These are graduated fallback controls — `ENABLE_CODE_SUMMARY=False` disables the "summarize as fallback" behavior while leaving simpler context-trimming behavior (dropping caller/callee context) always active, per the module's own inline documentation.

---

## `EXCLUDE_PATTERNS: list[str]`

- **Signature:** `EXCLUDE_PATTERNS: list[str]`
- **Responsibility:** Defines glob-style filename/directory patterns excluded from project traversal (used with `fnmatch`).
- **When to use:** Consumed by `dependency_graph.py` during file-tree walking to skip directories/files.
- **Design decisions:** If `EXCLUDE_PATTERNS` env var is set, it is parsed as a comma-separated list (trimmed, empty entries dropped); otherwise a hardcoded default list (`__pycache__`, `.git`, `.github`, `.venv`, `node_modules`) is used instead of merging with it — i.e., setting the env var fully replaces the defaults rather than extending them.
- **Constraints & edge cases:** An env var containing only commas/whitespace resolves to an empty list rather than falling back to defaults, since the fallback check is on `_EXCLUDE_PATTERNS_ENV` truthiness before filtering.

---

## Per-Language Definition Dictionaries

`PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, `CPP_DEFINITION_DICT`, `C_DEFINITION_DICT`, `KOTLIN_DEFINITION_DICT`, `JS_DEFINITION_DICT`, `TS_DEFINITION_DICT`

- **Signature:** Each is a `dict[str, str]` mapping an AST node type (tree-sitter grammar node name) to either a direct child node type holding the definition's name, or a `"__sentinel__"`-style string (e.g. `"__assignment__"`, `"__variable_declarator__"`, `"__declarator_name__"`, `"__function_declarator__"`, `"__init_declarator__"`, `"__kotlin_property__"`) signaling that name extraction requires special multi-level logic.
- **Responsibility:** Provide language-specific configuration for how the definition extractor (in `definitions.py`) locates the "name" identifier for each kind of syntactic definition (function, class, field, etc.).
- **When to use:** Looked up via `DEFINITION_DICTS.get(file_ext)` whenever a file of that language extension needs its top-level definitions (functions/classes/fields) extracted.
- **Design decisions:** Two-tier pattern — "standard" (direct child node type) vs. "sentinel" (dispatches to a dedicated extraction function) — keeps the dict declarative for simple cases while still supporting deeply nested name nodes without complicating the schema.
- **Constraints & edge cases:** These dicts must stay in sync with the actual tree-sitter grammar node names of each corresponding parser library version; a grammar upgrade that renames node types silently breaks extraction for that entry.

---

## Per-Language Import Queries

`_PYTHON_IMPORT_QUERY`, `_JS_IMPORT_QUERY`, `_JAVA_IMPORT_QUERY`, `_C_IMPORT_QUERY`, `_KOTLIN_IMPORT_QUERY`

- **Signature:** Each is a `str` containing one or more tree-sitter S-expression query patterns.
- **Responsibility:** Declaratively express, per language, how to locate import/include statements and capture the imported module path (`@module`), imported names (`@name`), and the enclosing statement node (`@import_node`) for line-number reporting.
- **When to use:** Compiled into a tree-sitter `Query` and executed against a file's AST whenever import information must be extracted for dependency resolution (`import_to_path.py`).
- **Design decisions:**
  - JS/TS query additionally covers CommonJS `require()` calls and destructured `require` results, unifying ES module and CommonJS import styles under one query.
  - Multiple independent patterns are concatenated in a single string (newline-separated) rather than split into separate query objects, so one query execution yields all match types for a language.
- **Constraints & edge cases:** Capture names must exactly match what `import_to_path.py` expects (`@module`, `@name`, `@import_node`); a capture-name typo would silently produce no matches rather than raising an error at this layer.

---

## Per-Language Usage Node Type Dictionaries

`_PYTHON_USAGE_NODE_TYPES`, `_JAVA_USAGE_NODE_TYPES`, `_JS_USAGE_NODE_TYPES`, `_C_USAGE_NODE_TYPES`, `_KOTLIN_USAGE_NODE_TYPES`

- **Signature:** Each is a `dict` with keys among: `call_types: set[str]`, `attribute_types: set[str]`, `skip_parent_types: set[str]`, `skip_parent_types_for_type_ref: set[str]`, and (where present) `skip_name_field_types: set[str]` or `typed_alias_parent_types: set[str]`.

| Key | Meaning |
|---|---|
| `call_types` | AST node types that represent a function/method call expression. |
| `attribute_types` | AST node types that represent attribute/member/field access. |
| `skip_parent_types` | Parent node types for which an identifier should NOT be treated as a "usage" (e.g., it's part of a definition name, import clause, or parameter list). |
| `skip_parent_types_for_type_ref` | Parent node types under which a type-reference identifier (`type_identifier`/`namespace_identifier`) should be skipped — narrower than `skip_parent_types` since almost all type references are treated as real dependencies. |
| `skip_name_field_types` (Python only) | Node types whose `name` field should not count as a usage (e.g., default/keyword argument names). |
| `typed_alias_parent_types` (Java/C/Kotlin) | Node types under which a typed variable declaration's type should be recorded as a name→type alias for later resolution. |

- **Responsibility:** Configure `extract_usages` (in `usage_analysis.py`) to correctly identify genuine symbol usages versus syntactic occurrences that are not real dependencies (definition names, parameter names, import identifiers).
- **When to use:** Looked up via `USAGE_NODE_TYPES.get(file_ext)` when computing which imported/target symbols are actually referenced within a caller file.
- **Design decisions:** Not every language dict defines every optional key (e.g., Python lacks `typed_alias_parent_types`; JS lacks it too) — consumers must use `.get(...)` with defaults rather than assuming key presence.
- **Constraints & edge cases:** These sets encode grammar-specific knowledge; incompleteness (a missing node type) causes false-positive "usage" detections rather than an error.

---

## `_JS_TS_EXT_LIST` / `_C_CPP_EXT_LIST`

- **Signature:** `_JS_TS_EXT_LIST: list[str] = [".ts", ".tsx", ".js", ".jsx"]`, `_C_CPP_EXT_LIST: list[str] = [".h", ".c", ".cpp"]`
- **Responsibility:** Shared extension lists reused across multiple `import_resolve` configs (as `index_ext_list`/`alt_ext_list`) to avoid duplicating the same list literal in each `LangConfig` entry.
- **When to use:** Referenced only within this module when constructing `_LANG_REGISTRY` entries for JS/TS and C/C++ languages.

---

## `LangConfig` (dataclass)

- **Signature:** `@dataclass(frozen=True) class LangConfig`

| Field | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object for parsing files of this extension. |
| `definition_dict` | `dict[str, str]` | Node-type → name-node-type mapping for definition extraction (see above). |
| `import_query` | `str \| None` | tree-sitter S-expression query for import extraction; `None` if unsupported. |
| `usage_node_types` | `dict \| None` | Usage-tracking node type configuration (see above); `None` if unsupported. |
| `import_resolve` | `dict \| None` | Module-resolution settings: `separator` (module name delimiter), `try_init` (look for `__init__.py`, Python), `index_ext_list`/`alt_ext_list` (extensions to try, JS/TS/C/C++), `try_bare_path` and `try_current_dir` (C/C++ path resolution behavior). |
| `same_package_visible` | `bool` (default `False`) | Whether symbols in the same directory/package are implicitly visible without an explicit import (Java/Kotlin semantics). |

- **Responsibility:** Bundles every per-language configuration facet into a single immutable record so that adding a new supported language requires only one new registry entry instead of touching five parallel dictionaries.
- **When to use:** Instantiated once per supported language/extension inside `_LANG_REGISTRY`; not intended for external instantiation.
- **Design decisions:** `frozen=True` makes instances immutable/hashable-safe, appropriate since these are fixed, load-time configuration objects shared across the whole process; optional fields default to `None`/`False` so languages that don't need import resolution or same-package visibility can omit them.
- **Constraints & edge cases:** No field-level validation is performed (e.g., no check that `import_query` is valid S-expression syntax); errors surface later when the query is compiled/executed.

---

## `_LANG_REGISTRY: dict[str, LangConfig]`

- **Signature:** `dict[str, LangConfig]`, keyed by canonical extension string (`"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"`).
- **Responsibility:** Single source of truth binding each supported language extension to its full `LangConfig`; all public per-extension mapping dictionaries are derived from this registry.
- **When to use:** Consulted only inside this module to generate the public dictionaries below; external modules use the derived public dicts instead.
- **Design decisions:** TypeScript and TSX share the same grammar package (`tstypescript`) but use different entry points (`language_typescript()` vs `language_tsx()`), so they are registered as two distinct keys rather than one, since TSX requires a separate grammar variant for JSX syntax support.
- **Constraints & edge cases:** Keys here are the "canonical" extensions; alias extensions (`h`, `kts`, `jsx`) are intentionally absent and injected later via `_expand_ext_aliases`.

---

## `_EXT_ALIASES: dict[str, str]`

- **Signature:** `dict[str, str] = {"h": "cpp", "kts": "kt", "jsx": "js"}`
- **Responsibility:** Maps non-canonical extensions to the canonical extension whose configuration they should reuse.
- **When to use:** Consumed exclusively by `_expand_ext_aliases`.
- **Design decisions:** `.h` is aliased to C++ (not C) configuration, reflecting a deliberate choice to treat header files with C++-oriented parsing/definition rules by default.
- **Constraints & edge cases:** Aliasing is one-directional and flat (no alias chains); an alias pointing to a nonexistent canonical key is silently ignored by `_expand_ext_aliases`.

---

## `_expand_ext_aliases(base_dict: dict) -> dict`

- **Signature:** `_expand_ext_aliases(base_dict: dict) -> dict`
- **Responsibility:** Given any of the per-extension settings dictionaries, produces a copy that additionally contains entries for the alias extensions defined in `_EXT_ALIASES`, pointing to the same value object as their canonical counterpart.
- **When to use:** Called once per public mapping dictionary at module load time (six times total, once for each of `TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`).
- **Design decisions:** Skips an alias if it is already present in `base_dict` or if its canonical target is absent (e.g., `IMPORT_RESOLVE_CONFIG`/`SAME_PACKAGE_VISIBLE` are pre-filtered to only include entries with truthy/non-`None` values, so aliases for excluded canonical keys are correctly omitted too). Aliases share object identity with the canonical value rather than being deep-copied, since these configs are treated as read-only.
- **Constraints & edge cases:** Not recursive/generic beyond one level — only aliases defined in `_EXT_ALIASES` are expanded; it performs a shallow copy of `base_dict`, so mutating a returned dict's nested values would affect the original `base_dict`'s values too (not exercised elsewhere in this module).

---

##

# Dependency Description

### Dependencies (modules this file imports)

`codetwine/config/settings.py` has no project-internal module dependencies. All imports in this file (`os`, `dataclasses`, `dotenv`, `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_python`, `tree_sitter_typescript`) are standard library or third-party packages, which are excluded per the scope of this description. This file acts as a root configuration module with no upstream project-internal dependencies.

### Dependents (modules that import this file)

- `main.py` → `codetwine/config/settings.py` : uses `DEFAULT_PROJECT_DIR`, `REPO_ROOT`, `DEFAULT_OUTPUT_DIR` to resolve project/output directory paths, and `ENABLE_LLM_DOC` to decide whether to instantiate the LLM client.

- `codetwine/file_analyzer.py` → `codetwine/config/settings.py` : uses `DEFINITION_DICTS.get` to obtain per-language AST node/name mappings for definition extraction.

- `codetwine/import_to_path.py` → `codetwine/config/settings.py` : uses `SOURCE_ROOT_PATTERNS` to detect source root prefixes, `IMPORT_RESOLVE_CONFIG.get` to resolve module import paths, `SAME_PACKAGE_VISIBLE.get` to determine implicit same-package visibility (Java/Kotlin), `DEFINITION_DICTS.get` to extract definition names, `IMPORT_QUERIES.get` to obtain tree-sitter import queries, and `TREE_SITTER_LANGUAGES` to obtain the parser Language object per extension.

- `codetwine/doc_creator.py` → `codetwine/config/settings.py` : uses `OUTPUT_LANGUAGE` for prompt localization, `CODE_SUMMARY_MAX_CHARS` and `CODE_SUMMARY_TRIGGER_LINES` for controlling code summarization thresholds, `ENABLE_CODE_SUMMARY` to gate summarization fallback, `SUMMARY_MAX_CHARS` for summary prompt limits, `MAX_WORKERS` for parallel document generation, and `DOC_TEMPLATE_PATH` to load the documentation template file.

- `codetwine/pipeline.py` → `codetwine/config/settings.py` : uses `MAX_WORKERS` for parallel processing, `ENABLE_LLM_DOC` to control document generation, and `KNOWLEDGE_FORMAT` to decide output format (json/sqlite/both) for the consolidated project knowledge output.

- `codetwine/llm/client.py` → `codetwine/config/settings.py` : uses `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE` as default constructor parameters for the LLM client, `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate limiting, and `DOC_MAX_TOKENS` as the default max token limit for generation calls.

- `codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py` : uses `USAGE_NODE_TYPES.get` to obtain per-language AST node type settings for usage tracking, `IMPORT_RESOLVE_CONFIG.get` for module separator resolution, `SAME_PACKAGE_VISIBLE.get` for implicit same-package reference detection, and `DEFINITION_DICTS.get` for definition name extraction.

- `codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py` : uses `DEFINITION_DICTS.keys` to determine the set of supported file extensions, `EXCLUDE_PATTERNS` to filter out excluded directories/files during project traversal, and `SAME_PACKAGE_VISIBLE.get` to group files by same-package visibility.

- `codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py` : uses `TREE_SITTER_LANGUAGES` to map file extensions to tree-sitter Language objects for parsing, and `PARSE_CACHE_MAX_FILES` to limit the size of the in-memory parse result cache.

### Dependency Direction

All relationships are **unidirectional**. `codetwine/config/settings.py` is a foundational configuration module that defines constants and per-language settings consumed by numerous other project-internal modules (`main.py`, `file_analyzer.py`, `import_to_path.py`, `doc_creator.py`, `pipeline.py`, `llm/client.py`, `extractors/usage_analysis.py`, `extractors/dependency_graph.py`, `parsers/ts_parser.py`). This file does not import or reference any of these dependent modules, so there is no reverse dependency.

# Data Flow

## 1. Inputs

- **Environment variables**: Read via `os.getenv` inside `get_config_value`, sourced from the process environment or a `.env` file loaded by `load_dotenv()` at import time. Each variable is a raw string (or `None` if unset).
- **Defaults**: Hardcoded fallback values (`str`, `int`, `bool`) passed as the `default` argument to `get_config_value` when the corresponding environment variable is absent.
- **`var_type` hints**: `type` objects (`str`, `int`, `float`, `bool`) passed by call sites to control how the raw string value is converted.
- **Filesystem path derivation input**: `__file__` (this module's own path), used to compute `REPO_ROOT`.
- **Tree-sitter language grammars**: Compiled grammar objects obtained from the `tree_sitter_*` packages (`tsc`, `tscpp`, `tsjava`, `tsjavascript`, `tskotlin`, `tspython`, `tstypescript`), each exposing a `.language()` (or `.language_typescript()` / `.language_tsx()`) function returning a native grammar pointer.
- **Static literals defined in-module**: dictionaries mapping AST node types to name-node types, tree-sitter S-expression query strings, and usage-tracking configuration dicts — these are not "inputs" from outside but constant data structures assembled directly in source.

## 2. Transformation Overview

The module performs a linear, import-time initialization pipeline (no runtime functions are exposed for repeated calls except `get_config_value`, which is invoked many times during module load):

1. **Environment loading**: `load_dotenv()` populates `os.environ` from any `.env` file before any config values are read.

2. **Config value resolution (`get_config_value`)**: For each named setting,
   - fetch the raw string from `os.environ`,
   - if missing, either raise (`_REQUIRED`), return `None`, or stringify the `default`,
   - convert the resulting string to the target type (`bool` via truthy-string matching, `int`/`float` via casting, else left as `str`).
   This produces the module-level scalar constants (`LLM_API_KEY`, `LLM_MODEL`, `DOC_MAX_TOKENS`, `MAX_WORKERS`, etc.).

3. **Path derivation**: `REPO_ROOT` is computed by normalizing a path relative to `__file__`; it then seeds defaults for `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `DOC_TEMPLATE_PATH`, each resolved through `get_config_value`.

4. **Validation**: `KNOWLEDGE_FORMAT` is lower-cased/stripped and checked against an allowed set (`json`/`sqlite`/`both`); an invalid value raises `ValueError` immediately, halting module import.

5. **Exclude pattern parsing**: `EXCLUDE_PATTERNS` env value (comma-separated string) is split, trimmed, and filtered into a `list[str]`; if empty, a hardcoded default list is used instead.

6. **Static per-language dictionaries construction**: Node-type-to-name-type mappings (`PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, etc.) and tree-sitter query strings (`_PYTHON_IMPORT_QUERY`, `_JS_IMPORT_QUERY`, etc.) and usage-node-type dicts (`_PYTHON_USAGE_NODE_TYPES`, etc.) are defined as literal constants — no transformation, just declaration.

7. **Language registry assembly (`_LANG_REGISTRY`)**: For each supported extension, a `LangConfig` instance is built by combining:
   - a `Language` object wrapping the grammar returned by the corresponding `tree_sitter_*` package,
   - the language's definition dict, import query, usage node types, and import-resolve settings.
   This fans multiple independent per-language literal groups into a single keyed registry (`dict[str, LangConfig]`).

8. **Public dictionary generation with alias expansion**: `_expand_ext_aliases` takes a base `{ext: value}` dict and, for each alias in `_EXT_ALIASES` (e.g., `"h" -> "cpp"`), copies the canonical extension's value under the alias key if not already present. This is applied to five projections of `_LANG_REGISTRY`:
   - `{ext: cfg.language}` → `TREE_SITTER_LANGUAGES`
   - `{ext: cfg.definition_dict}` → `DEFINITION_DICTS`
   - `{ext: cfg.import_query}` → `IMPORT_QUERIES`
   - `{ext: cfg.usage_node_types}` → `USAGE_NODE_TYPES`
   - `{ext: cfg.import_resolve}` (filtered to non-`None`) → `IMPORT_RESOLVE_CONFIG`
   - `{ext: cfg.same_package_visible}` (filtered to truthy) → `SAME_PACKAGE_VISIBLE`
   Each expansion fans one registry (7 canonical entries: py, java, cpp, c, kt, js, ts, tsx) out into a broader keyed dict including aliases (h, kts, jsx), merging language-specific config into extension-indexed lookup tables consumed elsewhere in the codebase.

9. **Static list literal**: `SOURCE_ROOT_PATTERNS` is declared directly as a `list[str]` with no derivation from environment or other structures.

No asynchronous or parallel processing occurs in this module; all transformations happen synchronously at import time, and downstream consumers (`main.py`, `pipeline.py`, `doc_creator.py`, parsers, extractors) read the resulting module-level constants directly.

## 3. Outputs

This module produces **module-level constants** consumed by other modules via `import`. No functions return computed pipeline results at runtime except `get_config_value`, which returns a converted scalar (`str | int | float | bool | None`) to its caller.

- **Scalar settings** (str/int/bool): `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `OUTPUT_LANGUAGE`, `DOC_MAX_TOKENS`, `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH`, `MAX_WORKERS`, `MAX_RETRIES`, `RETRY_WAIT`, `PARSE_CACHE_MAX_FILES`, `KNOWLEDGE_FORMAT`, `ENABLE_LLM_DOC`, `SUMMARY_MAX_CHARS`, `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS`.
- **List output**: `EXCLUDE_PATTERNS` (`list[str]`), `SOURCE_ROOT_PATTERNS` (`list[str]`).
- **Per-language keyed dictionaries** (extension string → value), all extension-alias-expanded:
  - `TREE_SITTER_LANGUAGES: dict[str, Language]`
  - `DEFINITION_DICTS: dict[str, dict[str, str]]`
  - `IMPORT_QUERIES: dict[str, str | None]`
  - `USAGE_NODE_TYPES: dict[str, dict | None]`
  - `IMPORT_RESOLVE_CONFIG: dict[str, dict]`
  - `SAME_PACKAGE_VISIBLE: dict[str, bool]`
- **Side effects**: `load_dotenv()` mutates `os.environ` as a process-wide side effect; an invalid `KNOWLEDGE_FORMAT` raises `ValueError` at import time, aborting startup for the whole application.

## 4. Key Data Structures

### `LangConfig` (frozen dataclass)

| Field / Key | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter grammar object for parsing this language's source files |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type (or `__sentinel__` marker) holding the definition's name |
| `import_query` | `str \| None` | tree-sitter S-expression query for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type configuration used for usage/dependency tracking |
| `import_resolve` | `dict \| None` | Module path resolution configuration (see below) |
| `same_package_visible` | `bool` | Whether symbols in the same package/directory are visible without explicit imports (Java/Kotlin) |

### `import_resolve` dict (value of `LangConfig.import_resolve` / entries of `IMPORT_RESOLVE_CONFIG`)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Delimiter used in module names (`"."` or `"/"`) |
| `try_init` | `bool` (optional) | Whether to resolve packages via `__init__.py` (Python) |
| `index_ext_list` | `list[str]` (optional) | Extensions tried for index files (JS/TS) |
| `alt_ext_list` | `list[str]` (optional) | Alternative extensions to try when resolving a module path |
| `try_bare_path` | `bool` (optional) | Whether to try the path without any extension (C/C++) |
| `try_current_dir` | `bool` (optional) | Whether to also try resolving relative to the current file's directory |

### `usage_node_types` dict (e.g. `_PYTHON_USAGE_NODE_TYPES`, entries of `USAGE_NODE_TYPES`)

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types representing function/method calls |
| `attribute_types` | `set[str]` | AST node types representing attribute/member access |
| `skip_parent_types` | `set[str]` | Parent node types under which an identifier should not be treated as a usage |
| `skip_name_field_types` | `set[str]` (optional) | Node types whose `name` field should be excluded from usage tracking |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent node types under which a type reference (`type_identifier`/`namespace_identifier`) should be skipped |
| `typed_alias_parent_types` | `set[str]` (optional) | Parent node types used to associate a variable name with its declared type |

### `definition_dict` (e.g. `PYTHON_DEFINITION_DICT`, entries of `DEFINITION_DICTS`)

| Key (AST node type) | Value Type | Purpose |
|---|---|---|
| e.g. `"function_definition"`, `"class_declaration"` | `str` | Direct child node type containing the definition's name |
| e.g. `"expression_statement"` | `str` (sentinel, e.g. `"__assignment__"`, `"__variable_declarator__"`, `"__declarator_name__"`, `"__function_declarator__"`, `"__init_declarator__"`, `"__kotlin_property__"`) | Marker indicating that name extraction requires special nested-node handling, dispatched externally by `_extract_name` |

### `_EXT_ALIASES` dict

| Key (alias extension) | Type | Purpose |
|---|---|---|
| `"h"` | `str` (canonical extension, `"cpp"`) | Maps header files to C++ language config |
| `"kts" ` | `str` (`"kt"`) | Maps Kotlin script files to Kotlin config |
| `"jsx"` | `str` (`"js"`) | Maps JSX files to JavaScript config |

# Error Handling

## 1. Overall Strategy

This file adopts a **fail-fast strategy at module load time**. Since `settings.py` is imported once at application startup and its values are consumed throughout the codebase (LLM client, parsers, extractors, pipeline), any invalid or missing critical configuration is surfaced immediately as an exception during import, rather than being deferred to runtime. There is no retry, fallback, or logging-and-continue logic in this file — configuration errors are treated as unrecoverable conditions that must be fixed before the application can run. For non-critical settings, the file instead applies **graceful defaulting**: sensible default values are substituted when environment variables are absent, allowing the application to proceed without requiring every variable to be explicitly set.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing required environment variable | `get_config_value(key)` called without a `default` argument and the variable is not set via `os.getenv` | Raises `ValueError` with a message instructing the user to set it in `.env` or the shell | No | Module import fails; application cannot start |
| Invalid `KNOWLEDGE_FORMAT` value | `KNOWLEDGE_FORMAT` resolves (after lowercasing/stripping) to a value not in `("json", "sqlite", "both")` | Explicit validation check raises `ValueError` with the offending value included in the message | No | Module import fails; application cannot start |
| Type conversion failure (int/float) | `get_config_value` called with `var_type=int` or `var_type=float` but the string value (from env or default) is not numerically parseable | No explicit handling; `int(value)` / `float(value)` raises `ValueError` (or `TypeError`) uncaught | No | Module import fails; application cannot start |
| Boolean parsing ambiguity | `var_type=bool` and the environment value is not one of `"true"`, `"1"`, `"yes"`, `"on"` (case-insensitive) | Value is silently treated as `False`; no error raised | Yes (implicitly) | Setting may be interpreted as disabled without warning |
| Missing/absent optional variable | Environment variable not set, but caller supplied a `default` (including `default=None`) | Returns `None` directly (if `default is None`) or converts `str(default)` to `var_type` | Yes | Application proceeds with the provided default |
| `.env` file not found | `load_dotenv()` cannot locate a `.env` file | `load_dotenv()` silently does nothing (no exception raised, per its own behavior) | Yes | Application relies on variables already present in the shell environment or on defaults |

## 3. Design Notes

- **Centralized validation point**: All environment-derived configuration passes through the single `get_config_value` helper, concentrating type-conversion and required-value logic in one place rather than scattering validation across the codebase.
- **Explicit vs. implicit failure modes**: Required values (`_REQUIRED` sentinel) and the `KNOWLEDGE_FORMAT` enum check produce explicit, descriptive `ValueError`s aimed at guiding the user to the `.env` file. Type-conversion errors for `int`/`float`, by contrast, are not explicitly caught, so they surface as generic Python exceptions without a custom message.
- **Defaults minimize required configuration**: Most settings (LLM parameters, paths, performance tuning, analysis toggles) are given defaults, so the application can run in a reasonable default mode without any `.env` file, while still allowing strict validation for settings where an invalid value would be ambiguous (`KNOWLEDGE_FORMAT`).
- **No runtime recovery within this file**: Because this module only defines configuration constants and does not perform I/O beyond reading environment variables, there is no retry or fallback behavior here — retry/fallback logic (e.g., `MAX_RETRIES`, `RETRY_WAIT`) is defined as *values* consumed by other modules (such as `llm/client.py`), not implemented within `settings.py` itself.
- **Load-once semantics**: Since configuration is computed at import time, any error encountered here blocks all downstream modules that depend on these constants (dependents such as `main.py`, `pipeline.py`, `doc_creator.py`, `usage_analysis.py`, etc.), consistent with treating configuration correctness as a precondition for the entire pipeline rather than a per-operation concern.

# Summary

Centralizes env-driven config and per-language tree-sitter settings for CodeTwine. Main function: `get_config_value(key: str, default=Any, var_type: type) -> str|int|float|bool|None`. Key structures: `LangConfig` dataclass (language, definition_dict, import_query, usage_node_types, import_resolve, same_package_visible); dicts `TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE` (all keyed by extension str); plus scalar constants (paths, LLM/model settings, `EXCLUDE_PATTERNS: list[str]`, `SOURCE_ROOT_PATTERNS: list[str]`).
