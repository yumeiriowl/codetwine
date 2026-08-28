# Design Document: codetwine/config/settings.py

# Overview & Purpose

## Role and Rationale

`codetwine/config/settings.py` is the single centralized configuration module for the entire Codetwine project. It is responsible for:

1. **Environment-driven configuration** — loading `.env` values (via `python-dotenv`) and exposing typed constants for LLM settings, path settings, performance tuning, output format, and analysis behavior, so that every other module (`main.py`, `pipeline.py`, `doc_creator.py`, `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`, `ts_parser.py`, `llm/client.py`) can import ready-to-use values instead of re-reading environment variables themselves.
2. **Per-language tree-sitter setup** — instantiating and registering `tree_sitter.Language` objects for each supported language (Python, Java, C, C++, Kotlin, JavaScript, TypeScript/TSX) and bundling, for each language, its AST definition-node mapping, import-extraction query, usage-tracking node types, and import-resolution rules.
3. **Single source of truth for language behavior** — by consolidating all language-specific AST knowledge (`_LANG_REGISTRY`) in one place, adding support for a new language or file extension requires only one new registry entry, and all derived public dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) are auto-generated consistently, avoiding duplication and drift across modules that need this information.

This module exists as a standalone file (rather than being embedded in the modules that use it) because configuration values and language-specific AST metadata are cross-cutting concerns consumed by parsing, dependency-analysis, usage-analysis, and document-generation modules alike; centralizing them avoids circular imports and duplicated environment-parsing logic.

## Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `get_config_value(key, default=_REQUIRED, var_type=str)` | `key: str`, `default`, `var_type: type` | Converted config value (str/int/float/bool) or raises `ValueError` | Reads an environment variable and converts it to the requested type, enforcing required-vs-optional semantics |
| `LangConfig` (dataclass, frozen) | `language`, `definition_dict`, `import_query=None`, `usage_node_types=None`, `import_resolve=None`, `same_package_visible=False` | `LangConfig` instance | Bundles all per-language settings (tree-sitter `Language`, definition mapping, import query, usage node types, import-resolution config, same-package visibility flag) into one immutable record |
| `_expand_ext_aliases(base_dict)` | `base_dict: dict` | New `dict` with alias extensions added | Duplicates a canonical-extension-keyed dict's entries under alias extensions (`h`, `kts`, `jsx`) defined in `_EXT_ALIASES` |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `OUTPUT_LANGUAGE`, `DOC_MAX_TOKENS` | — (module-level constants) | `str` / `int` | LLM connection and generation settings used by `llm/client.py` and `doc_creator.py` |
| `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH` | — | `str` (paths) | Path resolution defaults used by `main.py` and `doc_creator.py` |
| `MAX_WORKERS`, `MAX_RETRIES`, `RETRY_WAIT`, `PARSE_CACHE_MAX_FILES` | — | `int` | Performance/concurrency and parse-cache size tuning used by `pipeline.py`, `llm/client.py`, `ts_parser.py` |
| `KNOWLEDGE_FORMATS`, `KNOWLEDGE_FORMAT` | — | `tuple[str, ...]` / `str` | Defines and validates the allowed output formats for whole-project analysis results, used by `pipeline.py` |
| `ENABLE_LLM_DOC`, `SUMMARY_MAX_CHARS`, `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS`, `EXCLUDE_PATTERNS` | — | `bool` / `int` / `list[str]` | Controls whether/how LLM documentation and code summarization run, and which files/dirs to exclude from analysis (`main.py`, `doc_creator.py`, `dependency_graph.py`) |
| `PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, `CPP_DEFINITION_DICT`, `C_DEFINITION_DICT`, `KOTLIN_DEFINITION_DICT`, `JS_DEFINITION_DICT`, `TS_DEFINITION_DICT` | — | `dict[str, str]` | Per-language mapping of AST definition node type to the child node type holding its name |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Extension → tree-sitter `Language` object, used by `ts_parser.py` and `import_to_path.py` |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Extension → definition-node mapping, used by `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Extension → tree-sitter import-extraction query string, used by `import_to_path.py` |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Extension → AST node type settings for usage tracking, used by `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Extension → module-path resolution rules (separator, index/alt extensions, etc.), used by `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Extension → whether same-package symbols are visible without explicit imports (Java/Kotlin), used by `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |
| `SOURCE_ROOT_PATTERNS` | — | `list[str]` | Known Maven/Gradle/Python source-root prefixes for import resolution, used by `import_to_path.py` |

## Design Decisions

- **Registry pattern (`_LANG_REGISTRY` + `LangConfig`)**: All per-language settings are grouped into a single frozen dataclass per language and stored in one registry dict, from which every public per-extension mapping is derived via dict comprehensions. This centralizes language onboarding to one place and guarantees consistency across all derived dictionaries.
- **Alias expansion (`_EXT_ALIASES` / `_expand_ext_aliases`)**: Extensions that share identical language behavior (`h`→`cpp`, `kts`→`kt`, `jsx`→`js`) are expressed once and expanded automatically, avoiding duplicated registry entries.
- **Sentinel-based extension handling**: `_REQUIRED` acts as a sentinel object to distinguish "no default provided" (raise error) from "default is `None`" in `get_config_value`, and `"__sentinel__"`-style string markers (e.g., `"__assignment__"`, `"__variable_declarator__"`) in the definition dicts signal to the AST extraction logic (in `definitions.py`) that a nested/dedicated extraction routine is needed instead of a simple direct-child lookup.
- **Fail-fast validation for output format**: `KNOWLEDGE_FORMATS` is defined as the authoritative allow-list, checked by `pipeline.py` before any analysis work begins, ensuring invalid configuration is caught early.
- **Environment-first configuration with typed defaults**: `get_config_value` centralizes type coercion (bool/int/float/str) and required-value enforcement so downstream modules receive already-validated, correctly-typed constants rather than raw strings.

# Definition Design Specifications

## `get_config_value`

Retrieves an environment variable identified by `key` (str) and converts it to `var_type` (type, default `str`), returning the converted value. `default` (any type, sentinel `_REQUIRED` by default) supplies a fallback when the variable is absent; passing `None` explicitly as `default` short-circuits type conversion and returns `None`.

This function centralizes environment-variable parsing so that every configuration constant in the module goes through the same validation and type-coercion logic, avoiding duplicated `os.getenv` + cast code throughout the settings file.

Design decisions:
- Uses a private sentinel object (`_REQUIRED`) rather than `None` to distinguish "no default provided" from "default is explicitly `None`", since `None` is itself a valid, intentional default value in several call sites.
- Boolean conversion is done via an explicit set of accepted truthy string tokens (`"true"`, `"1"`, `"yes"`, `"on"`) rather than Python's built-in truthiness, because environment variable values are always strings and a naive `bool("false")` would incorrectly evaluate to `True`.
- Numeric conversion (`int`/`float`) is applied only after the default-substitution step, so a supplied non-`_REQUIRED`, non-`None` default is stringified and then subject to the same type conversion path as a real environment value, keeping behavior consistent regardless of whether the value came from the environment or the default.

Edge cases / constraints:
- Raises `ValueError` if the variable is unset and no `default` was supplied.
- If `var_type` is `int` or `float` and the resolved string value cannot be parsed, the underlying `ValueError` from the built-in conversion propagates uncaught.
- For any `var_type` other than `bool`, `int`, or `float`, the value is returned as-is (string), so unsupported types silently fall back to string behavior.

## `LangConfig`

Frozen dataclass bundling every per-language setting needed to analyze source files of one language/extension: `language` (`Language`, the tree-sitter grammar object), `definition_dict` (`dict[str, str]`, AST node type → name-bearing child node type used for definition extraction), `import_query` (`str | None`, tree-sitter S-expression query for import extraction), `usage_node_types` (`dict | None`, AST node type sets used for usage/dependency tracking), `import_resolve` (`dict | None`, module path resolution rules such as separator, index/alt extensions, and package-init lookup), and `same_package_visible` (`bool`, whether unimported same-package symbols should be treated as visible, defaulting to `False`).

Its purpose is to let `_LANG_REGISTRY` describe a full language configuration as a single cohesive record instead of parallel dictionaries keyed by extension, so that adding support for a new language requires only one new registry entry rather than edits scattered across several independent mappings.

Design decisions:
- Declared `frozen=True` because language configuration is fixed at import time and must not be mutated afterward; the object is shared and read from multiple modules across the analysis pipeline.
- `import_query`, `usage_node_types`, and `import_resolve` are optional (`None`-able) to accommodate languages or future extensions that may not need every capability, while `language` and `definition_dict` are mandatory since every supported language must at least support parsing and definition extraction.

Edge cases / constraints:
- Consumers of the derived public dictionaries (e.g., `IMPORT_QUERIES`, `USAGE_NODE_TYPES`) must handle `None` values, since the dataclass allows these fields to be absent for a given language.

## `_expand_ext_aliases`

Takes `base_dict` (`dict`, keyed by canonical file extension) and returns a new `dict` (same value types as the input) with alias extensions from `_EXT_ALIASES` added, each alias pointing to the same value as its canonical extension.

This function exists to avoid duplicating identical configuration entries for extensions that share a language implementation (e.g., `.h` reusing the C++ configuration, `.jsx` reusing JavaScript, `.kts` reusing Kotlin), letting `_LANG_REGISTRY` define each language only once under its canonical extension.

Design decisions:
- Returns a new dictionary rather than mutating `base_dict` in place, so the original per-registry mapping remains unmodified and can be reused to build multiple derived public dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`).
- Only adds an alias key when it is absent from `base_dict` and its canonical extension is present, preventing accidental overwrition of an extension that already has its own explicit entry and silently skipping aliases whose canonical target isn't present in a given derived dictionary (relevant since `IMPORT_RESOLVE_CONFIG` and `SAME_PACKAGE_VISIBLE` are built from filtered subsets of the registry).

Edge cases / constraints:
- If an alias's canonical extension is missing from `base_dict` (e.g., because it was filtered out upstream), the alias is simply omitted rather than raising an error.

# Dependency Description

### Dependencies (what this file uses)

This file has no project-internal dependencies. It only relies on external/third-party packages (`dotenv` for environment variable loading and the various `tree_sitter` language bindings for constructing `Language` objects), which are outside the scope of this description. As a pure configuration module, it defines constants and registries without importing from any other module in this project.

### Dependents (what uses this file)

This file is depended on by nearly every other module in the project, acting as the central configuration hub. The dependency direction is strictly unidirectional: `settings.py` is consumed by other modules, and it never imports back from them.

- **main.py**: Uses `DEFAULT_PROJECT_DIR`, `REPO_ROOT`, and `DEFAULT_OUTPUT_DIR` to resolve the project and output directory paths for a run, and `ENABLE_LLM_DOC` to decide whether to instantiate an LLM client.
- **codetwine/file_analyzer.py**: Uses `DEFINITION_DICTS` to look up the language-specific AST definition-extraction rules for a given file extension.
- **codetwine/import_to_path.py**: Uses `SOURCE_ROOT_PATTERNS` to detect known source root prefixes, `IMPORT_RESOLVE_CONFIG` to determine how to resolve import statements into file paths, `SAME_PACKAGE_VISIBLE` to check whether same-package symbol visibility applies (Java/Kotlin), `DEFINITION_DICTS` to extract definition names for resolving import targets, `IMPORT_QUERIES` to obtain the tree-sitter query for import extraction, and `TREE_SITTER_LANGUAGES` to get the parser `Language` object for a file extension.
- **codetwine/doc_creator.py**: Uses `OUTPUT_LANGUAGE` to set the language of generated documentation, `CODE_SUMMARY_MAX_CHARS` and `CODE_SUMMARY_TRIGGER_LINES` to control when and how large code is summarized, `ENABLE_CODE_SUMMARY` to gate LLM-based summarization fallback, `SUMMARY_MAX_CHARS` to bound summary length, `MAX_WORKERS` to control parallelism when generating documents, and `DOC_TEMPLATE_PATH` to load the documentation template file.
- **codetwine/pipeline.py**: Uses `MAX_WORKERS` to control parallel processing, `KNOWLEDGE_FORMATS` and `KNOWLEDGE_FORMAT` to validate and determine the output format for consolidated project knowledge, and `ENABLE_LLM_DOC` to decide whether to run the document-generation step.
- **codetwine/llm/client.py**: Uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default constructor parameters for the LLM client, `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate-limit errors, and `DOC_MAX_TOKENS` as the default token limit for generation requests.
- **codetwine/extractors/usage_analysis.py**: Uses `USAGE_NODE_TYPES` to obtain language-specific AST node type rules for tracking symbol usages, `IMPORT_RESOLVE_CONFIG` to resolve import module separators, `SAME_PACKAGE_VISIBLE` to handle implicit same-package references, and `DEFINITION_DICTS` to extract target definition names.
- **codetwine/extractors/dependency_graph.py**: Uses `DEFINITION_DICTS` to identify which file extensions are supported for analysis, `EXCLUDE_PATTERNS` to filter out excluded directories/files during project traversal, and `SAME_PACKAGE_VISIBLE` to group same-package files for Java/Kotlin.
- **codetwine/parsers/ts_parser.py**: Uses `TREE_SITTER_LANGUAGES` to obtain the parser `Language` object per file extension, and `PARSE_CACHE_MAX_FILES` to bound the size of the in-memory parse result cache.

# Data Flow

## Input
| Source | Format | Consumed by |
|---|---|---|
| `.env` file / shell environment | Key-value strings | `load_dotenv()`, then `os.getenv()` inside `get_config_value()` |
| `tree_sitter_*` packages (c, cpp, java, javascript, kotlin, python, typescript) | Compiled grammar objects | Wrapped into `Language` instances for `_LANG_REGISTRY` |
| Static Python literals in this file (dicts, tuples, lists, query strings) | Hard-coded config data | Registered per-language, exported as public constants |

## Transformation Flow

```
os.getenv(key) ──► get_config_value() ──► type-converted scalar constants
                        │                    (LLM_*, DOC_MAX_TOKENS, MAX_WORKERS,
                        │                     KNOWLEDGE_FORMAT, ENABLE_*, SUMMARY_*, etc.)
                        │
REPO_ROOT (derived from __file__) ──► path constants
                        │             (DEFAULT_PROJECT_DIR, DEFAULT_OUTPUT_DIR, DOC_TEMPLATE_PATH)

Per-language literals (definition dicts, import queries, usage-node dicts,
import-resolve dicts) ──► bundled into LangConfig ──► _LANG_REGISTRY[ext]
                                                             │
                                _expand_ext_aliases() adds alias extensions (h→cpp, kts→kt, jsx→js)
                                                             │
                        ┌────────────────────────────────────┴─────────────────────────────┐
                        ▼               ▼                 ▼                 ▼               ▼
          TREE_SITTER_LANGUAGES  DEFINITION_DICTS   IMPORT_QUERIES   USAGE_NODE_TYPES   IMPORT_RESOLVE_CONFIG
                                                                                          SAME_PACKAGE_VISIBLE
```

1. **Env-driven values**: `get_config_value()` reads a raw string from the environment (or falls back to a default), then converts it to `bool`/`int`/`float`/`str` based on `var_type`. Missing required values (no default) raise `ValueError`.
2. **Path values**: `REPO_ROOT` is computed once from the module's own location; other path constants (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH`) default to paths relative to it unless overridden by env vars.
3. **Language registry build**: Each supported extension's `Language` object, definition-node mapping, import query, usage-node config, and import-resolution rules are packed into a `LangConfig` dataclass instance stored in `_LANG_REGISTRY`.
4. **Public dict generation**: `_expand_ext_aliases()` derives five/six public extension-keyed dictionaries from `_LANG_REGISTRY` by extracting one field per entry and adding alias extensions that share settings with a canonical extension.

## Output

| Constant | Type | Destination (consumers) |
|---|---|---|
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `DOC_MAX_TOKENS` | str/int | `codetwine/llm/client.py` (`LLMClient.__init__`, `generate`) |
| `OUTPUT_LANGUAGE` | str | `codetwine/doc_creator.py` prompt building |
| `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `DOC_TEMPLATE_PATH` | str (path) | `main.py` (dir resolution), `doc_creator.py` (template load) |
| `MAX_WORKERS`, `MAX_RETRIES`, `RETRY_WAIT` | int | `doc_creator.py`, `pipeline.py`, `llm/client.py` |
| `PARSE_CACHE_MAX_FILES` | int | `parsers/ts_parser.py` (LRU cache size) |
| `KNOWLEDGE_FORMATS`, `KNOWLEDGE_FORMAT` | tuple/str | `pipeline.py` (validation + output-format branching) |
| `ENABLE_LLM_DOC`, `ENABLE_CODE_SUMMARY`, `SUMMARY_MAX_CHARS`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS` | bool/int | `pipeline.py`, `doc_creator.py` (doc generation & summarization gating) |
| `EXCLUDE_PATTERNS` | list[str] | `extractors/dependency_graph.py` (file/dir filtering via `fnmatch`) |
| `TREE_SITTER_LANGUAGES` | dict[str, Language] | `parsers/ts_parser.py` (parser selection), `import_to_path.py` |
| `DEFINITION_DICTS` | dict[str, dict[str,str]] | `file_analyzer.py`, `import_to_path.py`, `extractors/*` (AST node → name-node lookup for definition extraction) |
| `IMPORT_QUERIES` | dict[str, str] | `import_to_path.py` (tree-sitter Query construction) |
| `USAGE_NODE_TYPES` | dict[str, dict] | `extractors/usage_analysis.py` (call/attribute/skip node classification) |
| `IMPORT_RESOLVE_CONFIG` | dict[str, dict] | `import_to_path.py`, `usage_analysis.py` (module path resolution: separator, extensions, index/bare-path rules) |
| `SAME_PACKAGE_VISIBLE` | dict[str, bool] | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` (Java/Kotlin implicit same-package symbol visibility) |
| `SOURCE_ROOT_PATTERNS` | list[str] | `import_to_path.py` (stripping src-root prefixes when resolving imports) |

## Key Data Structures

**`LangConfig` (per-language bundle)**
| Field | Purpose |
|---|---|
| `language` | tree-sitter `Language` object used to build a parser |
| `definition_dict` | AST node type → name-child node type (or `__sentinel__` marker) for definition extraction |
| `import_query` | S-expression query string to extract import statements (`@module`, `@name`, `@import_node` captures) |
| `usage_node_types` | Node-type sets (`call_types`, `attribute_types`, `skip_parent_types`, etc.) driving usage/reference tracking |
| `import_resolve` | Rules for turning an import string into a file path (`separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`) |
| `same_package_visible` | Whether same-directory files are implicitly visible without imports (Java/Kotlin) |

**`_LANG_REGISTRY`**: `dict[extension → LangConfig]` — single source of truth; all public dictionaries are derived views of it.

**`_EXT_ALIASES`**: `dict[alias_ext → canonical_ext]` (e.g., `h→cpp`, `kts→kt`, `jsx→js`) used by `_expand_ext_aliases()` to duplicate canonical settings under alias keys without re-registering a language.

**Per-language `*_DEFINITION_DICT`**: maps AST node type (e.g., `function_definition`) to the name-bearing child node type, or a `__sentinel__` string signaling a dedicated extraction function is needed.

**Per-language `*_USAGE_NODE_TYPES`**: sets categorizing AST node types for call/attribute detection and for suppressing false-positive usage matches on definition/import/parameter nodes.

# Error Handling

## Overall Strategy

This module follows a **fail-fast policy for required configuration** combined with **silent fallback (graceful degradation) for optional configuration**. Since this file executes at import time, any unrecoverable error here halts application startup immediately, preventing the system from running with an inconsistent or unusable configuration. Values with sensible defaults are never allowed to raise; instead, they degrade to a default value or `None` so that unrelated features remain usable even when a specific environment variable is misconfigured. Validation of some settings (e.g., `KNOWLEDGE_FORMAT` against `KNOWLEDGE_FORMATS`) is intentionally deferred to consuming modules rather than enforced here, so that downstream code can override settings without being blocked by this module.

## Error Patterns and Handling Policy

| Error Type | Handling | Impact |
|---|---|---|
| Required environment variable missing (no default provided) | `get_config_value` raises `ValueError` with a message pointing to `.env` / shell configuration | Import of `settings.py` fails, halting application startup (fail-fast) |
| Optional environment variable missing (default provided) | Falls back to the provided default value (or `None` if `default=None`) without raising | Application continues with default behavior; no visible error |
| Invalid type conversion (e.g., non-numeric string for `int`/`float` fields) | No explicit handling; conversion is delegated directly to Python's `int()`/`float()` builtins | Raises an unhandled `ValueError`/`TypeError` at import time, causing startup failure |
| Boolean-like values with unexpected text | Treated as falsy by default (`False`) since only a fixed set of strings map to `True` | Silent behavior change rather than an error; no exception is raised |
| Missing/unsupported language configuration (`DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `TREE_SITTER_LANGUAGES`) for a given file extension | Not handled in this file; consumers use `.get()` with `None`/`{}` fallback or catch `KeyError` themselves | Unsupported languages are skipped gracefully by dependents rather than causing this module to fail |
| Invalid `KNOWLEDGE_FORMAT` value | Not validated in this file; the raw (stripped/lowercased) value is exported as-is | Validation and resulting `ValueError` are deferred entirely to the pipeline module that consumes `KNOWLEDGE_FORMAT` |
| Tree-sitter `Language` construction failures (e.g., incompatible grammar binary) | No error handling; any exception from `Language(...)` propagates directly | Import of `settings.py` fails outright, since language registry construction happens at module load time |

## Design Considerations

- Centralizing type coercion and default/required logic in a single helper (`get_config_value`) ensures consistent error messaging and behavior across all environment-derived settings, rather than duplicating validation logic per variable.
- Distinguishing `_REQUIRED` (sentinel) from `default=None` allows the same function to express three distinct outcomes—mandatory failure, explicit `None`, and a substituted default—without extra parameters.
- Deferring format/value validation (e.g., `KNOWLEDGE_FORMAT`) to the point of use rather than at import time is a deliberate choice noted in the comments, so that programmatic overrides of settings in a caller's namespace are not blocked by validation performed during this module's import.
- Errors originating from `Language(...)` construction or dictionary lookups on the language registry are not caught here; the registry is treated as static, trusted configuration, and any failure to build it is expected to surface immediately as a startup-time error rather than be masked.

# Summary

Central config module for Codetwine, loaded via python-dotenv and consumed by nearly all other modules. Responsibilities: (1) parse/validate env vars into typed constants (LLM settings, paths, performance, output/analysis flags) via `get_config_value`; (2) build per-language tree-sitter setup through a `LangConfig` dataclass registry (`_LANG_REGISTRY`), expanded with extension aliases; (3) derive public extension-keyed dicts (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) as single source of truth for language behavior, with no internal dependencies.
