# Design Document: codetwine/config/settings.py

# Overview & Purpose

## Role in the Project

`codetwine/config/settings.py` is the **centralized configuration module** for the entire CodeTwine project. It serves as the single source of truth for:

1. **Environment-driven configuration** — LLM connection settings, path defaults, performance tuning, and analysis toggles, all read from environment variables (via `.env` and `os.getenv`) with typed defaults.
2. **Multi-language static analysis configuration** — a per-language registry (`_LANG_REGISTRY`) that bundles everything needed to parse and analyze source code in Python, Java, C, C++, Kotlin, JavaScript, and TypeScript/TSX using `tree-sitter`: the `Language` object, definition-node extraction rules, import-extraction queries, usage-tracking node types, and import-path resolution rules.

This file exists as a separate module so that:
- All environment/config access is funneled through one validated helper (`get_config_value`), avoiding scattered `os.getenv` calls and inconsistent type coercion across the codebase.
- Language-specific tree-sitter setup (grammars, queries, node-type mappings) is defined once and consumed uniformly by parsing, extraction, dependency-graph, and usage-analysis modules, so adding a new language requires touching only this file (one registry entry) rather than every consumer module.
- Downstream modules (`main.py`, `doc_creator.py`, `pipeline.py`, `llm/client.py`, `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`, `ts_parser.py`) can import ready-made, pre-validated constants and dictionaries instead of duplicating configuration logic.

## Main Public Interfaces

| Name | Arguments | Return | Responsibility |
|---|---|---|---|
| `get_config_value(key, default=_REQUIRED, var_type=str)` | `key: str`, `default`, `var_type: type` | Converted config value (`str`/`int`/`float`/`bool`/`None`) | Reads an env var and converts it to the requested type; raises `ValueError` if required and missing |
| `LangConfig` (dataclass, frozen) | `language`, `definition_dict`, `import_query`, `usage_node_types`, `import_resolve`, `same_package_visible` | — | Bundles all per-language settings (tree-sitter language, definition rules, import query, usage rules, import resolution config) into one immutable record |
| `_expand_ext_aliases(base_dict)` | `base_dict: dict` | `dict` | Adds alias-extension entries (e.g. `h`→`cpp`, `jsx`→`js`) to a canonical-extension-keyed dict |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `OUTPUT_LANGUAGE`, `DOC_MAX_TOKENS` | — | `str`/`int` | LLM client configuration used by `llm/client.py` (model, key, base URL, output language, max tokens) |
| `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH` | — | `str` | Path defaults for project root, project/output directories, and doc template location, used by `main.py` and `doc_creator.py` |
| `MAX_WORKERS`, `MAX_RETRIES`, `RETRY_WAIT` | — | `int` | Concurrency and retry tuning used by `doc_creator.py`, `pipeline.py`, `llm/client.py` |
| `ENABLE_LLM_DOC`, `SUMMARY_MAX_CHARS`, `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS` | — | `bool`/`int` | Toggles and thresholds controlling LLM-based documentation and code-summarization fallback behavior in `doc_creator.py` and `pipeline.py` |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns of files/directories to skip during project traversal (`dependency_graph.py`) |
| `PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, `CPP_DEFINITION_DICT`, `C_DEFINITION_DICT`, `KOTLIN_DEFINITION_DICT`, `JS_DEFINITION_DICT`, `TS_DEFINITION_DICT` | — | `dict[str, str]` | Per-language mapping of AST node type → name-holding child node type (or sentinel) for definition extraction |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Extension → tree-sitter `Language` object, used by `ts_parser.py` for parsing |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Extension → definition-node dict, used by `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Extension → tree-sitter import-extraction query string, used by `import_to_path.py` |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Extension → AST node-type settings for usage tracking, used by `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Extension → module-path resolution settings (separator, index/alt extensions, etc.), used by `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Extension → whether same-package (directory-based) implicit visibility applies (Java/Kotlin), used by `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |
| `SOURCE_ROOT_PATTERNS` | — | `list[str]` | Known Maven/Gradle/Python source-root prefixes for resolving imports, used by `import_to_path.py` |

## Design Patterns & Decisions

- **Registry pattern**: `_LANG_REGISTRY` (a `dict[str, LangConfig]`) centralizes all per-language settings behind a single dataclass, so adding a new language requires only one new registry entry rather than changes across multiple public dictionaries.
- **Derived/generated public API**: The individually exported dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) are auto-generated from `_LANG_REGISTRY` via dict comprehensions and `_expand_ext_aliases`, ensuring consistency and avoiding manual duplication.
- **Alias expansion**: `_EXT_ALIASES` plus `_expand_ext_aliases()` implements a lightweight mapping-inheritance mechanism so file extensions sharing a grammar/config (e.g., `.h`→cpp, `.kts`→kotlin, `.jsx`→js) don't need duplicate registry entries.
- **Sentinel value convention**: The `"__sentinel__"`-style values (e.g., `"__assignment__"`, `"__declarator_name__"`) in definition dicts signal that name extraction requires special nested-node handling, deferring that logic to `definitions.py` rather than embedding it in configuration.
- **Frozen dataclass**: `LangConfig` is declared `frozen=True`, enforcing immutability of language configuration once constructed.
- **Fail-fast validation**: `get_config_value` raises `ValueError` for missing required environment variables (no silent defaults) unless a default is explicitly provided, surfacing misconfiguration early.

# Definition Design Specifications

## `get_config_value`

Reads an environment variable identified by `key` and converts it to `var_type` (`str`, `int`, `float`, or `bool`). `default` is the fallback value used when the variable is unset; if left at the internal `_REQUIRED` sentinel, a missing variable raises `ValueError`. Returns the converted value, or `None` when `default` is explicitly `None` and the variable is unset.

This function centralizes environment-variable loading so every setting in the module goes through one consistent type-conversion and error-reporting path, avoiding repeated `os.getenv` boilerplate scattered across the config file.

Design decisions:
- A private sentinel object (`_REQUIRED`) is used instead of a placeholder like `None` or a string, so that `None` can still be passed as a legitimate "default to empty" value without being confused with "no default provided."
- Boolean conversion uses a whitelist of string tokens (`"true"`, `"1"`, `"yes"`, `"on"`, case-insensitive) rather than Python's truthiness, since all environment variables arrive as strings and a naive `bool(value)` would treat any non-empty string (e.g., `"false"`) as `True`.
- When a non-`None` `default` is used because the variable is unset, it is coerced via `str(default)` before type conversion, so the same conversion logic path applies uniformly whether the value came from the environment or from `default`.

Constraints/edge cases:
- `var_type` must be one of `str`, `int`, `float`, or `bool`; any other type falls through and the raw string is returned unconverted.
- Invalid numeric strings for `int`/`float` propagate the underlying `ValueError`/exception from the conversion call.
- If `default` is `None` and the variable is unset, the function returns `None` regardless of `var_type`, bypassing type conversion entirely.

## `LangConfig`

A frozen dataclass that bundles all per-language settings required to analyze a single source file extension: the tree-sitter `Language` object, the definition-node mapping (`definition_dict`), the import-extraction query string (`import_query`), the usage-tracking node-type configuration (`usage_node_types`), the module-resolution configuration (`import_resolve`), and a flag (`same_package_visible`) indicating whether symbols in the same directory/package are implicitly visible without an import statement (used for Java/Kotlin).

It exists to keep all language-specific configuration for a given extension co-located and immutable, so that adding support for a new language means constructing one `LangConfig` instance rather than updating several independent parallel dictionaries by hand.

Design decisions:
- Declared `frozen=True` so language configuration cannot be mutated at runtime after the registry is built, protecting shared, process-wide analysis settings.
- `import_query`, `usage_node_types`, and `import_resolve` are optional (default `None`/absent) because not every language necessarily needs every kind of configuration; downstream mapping generation filters out `None` values (e.g., for `IMPORT_RESOLVE_CONFIG`).
- `same_package_visible` defaults to `False` since implicit same-package resolution is only meaningful for languages with package/namespace semantics (Java, Kotlin) and would be incorrect to assume by default.

Constraints:
- Instances are meant to be constructed once (in `_LANG_REGISTRY`) and read many times; no methods are defined for mutation.

## `_expand_ext_aliases`

Takes a settings dictionary keyed by canonical file extension (`base_dict`) and returns a new dictionary that additionally includes entries for alias extensions defined in `_EXT_ALIASES`, pointing each alias to the same value as its canonical extension.

This function exists so that extensions sharing identical language behavior (e.g., `.h` files using the C++ config, `.kts` using Kotlin config, `.jsx` using JS config) don't require duplicate entries in every one of the module's per-language dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`); the alias mapping is defined once and applied uniformly to all of them.

Design decisions:
- Returns a new dictionary (via `dict(base_dict)`) rather than mutating `base_dict` in place, avoiding side effects on the caller's original mapping.
- An alias is only added if it is not already present in `base_dict` and its canonical extension does exist in `base_dict`; this makes the function safe to reuse across dictionaries where the canonical key may be absent (e.g., `IMPORT_RESOLVE_CONFIG`, which excludes languages with no resolve config), simply skipping alias creation in that case rather than raising an error.

Constraints:
- Alias resolution is single-level: if `canonical` itself were an alias key in `_EXT_ALIASES`, it would not be further resolved (not applicable with current alias definitions, but not handled generically).

# Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. Its imports consist solely of standard library modules (`os`, `dataclasses`) and third-party packages (`dotenv`, `tree_sitter`, and the various `tree_sitter_*` language grammar packages) used to load environment variables and build `Language` objects for each supported programming language. No internal project modules are imported here, as this file serves as the root configuration module for the entire project.

### Dependents (what uses this file)

This file is a foundational configuration module that many other project files depend on; the dependency direction is unidirectional (other modules depend on `settings.py`, not vice versa).

- **main.py** uses `DEFAULT_PROJECT_DIR`, `REPO_ROOT`, and `DEFAULT_OUTPUT_DIR` to resolve the project and output directories, and `ENABLE_LLM_DOC` to decide whether to instantiate an LLM client before running the document generation pipeline.
- **codetwine/file_analyzer.py** uses `DEFINITION_DICTS` to obtain the per-language AST node mapping needed for extracting code definitions from a target file.
- **codetwine/import_to_path.py** uses `SOURCE_ROOT_PATTERNS` to detect source root prefixes, `IMPORT_RESOLVE_CONFIG` to determine module resolution rules (separator, extension handling) for each language, `SAME_PACKAGE_VISIBLE` to check whether a language allows implicit same-package references (Java/Kotlin), `DEFINITION_DICTS` to extract definition names from resolved files, `IMPORT_QUERIES` to retrieve the tree-sitter query string for import extraction, and `TREE_SITTER_LANGUAGES` to obtain the parser `Language` object for a given file extension.
- **codetwine/doc_creator.py** uses `OUTPUT_LANGUAGE` to specify the language of generated documentation, `CODE_SUMMARY_MAX_CHARS` and `CODE_SUMMARY_TRIGGER_LINES` to control when and how large code definitions are summarized, `ENABLE_CODE_SUMMARY` to toggle LLM-based summarization fallback, `SUMMARY_MAX_CHARS` to bound summary length, `MAX_WORKERS` to control parallelism during document generation, and `DOC_TEMPLATE_PATH` to load the documentation template file.
- **codetwine/pipeline.py** uses `MAX_WORKERS` to control parallel processing during project analysis and `ENABLE_LLM_DOC` to determine whether the design document generation step should run.
- **codetwine/llm/client.py** uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default parameters for initializing the LLM client, `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate limit errors, and `DOC_MAX_TOKENS` as the default token limit for generation requests.
- **codetwine/extractors/usage_analysis.py** uses `USAGE_NODE_TYPES` to obtain per-language AST node type settings for tracking symbol usage, `IMPORT_RESOLVE_CONFIG` to resolve import module names, `SAME_PACKAGE_VISIBLE` to check implicit visibility rules, and `DEFINITION_DICTS` to extract definitions from target files.
- **codetwine/extractors/dependency_graph.py** uses `DEFINITION_DICTS` to determine the set of supported file extensions for dependency analysis, `EXCLUDE_PATTERNS` to filter out directories/files that should not be scanned, and `SAME_PACKAGE_VISIBLE` to group files by same-package visibility for Java/Kotlin.
- **codetwine/parsers/ts_parser.py** uses `TREE_SITTER_LANGUAGES` to map file extensions to their corresponding tree-sitter `Language` objects for parsing.

# Data Flow

## 1. Input Data

| Source | Format | Consumed By |
|---|---|---|
| `.env` file / shell environment | Environment variables (strings) | `load_dotenv()` + `os.getenv()` via `get_config_value()` |
| `tree_sitter_*` packages (c, cpp, java, javascript, kotlin, python, typescript) | Compiled grammar objects (`PyCapsule`/binding objects) | `Language(...)` constructor calls |
| Hard-coded Python literals in this file | dicts / strings / lists | `_LANG_REGISTRY`, definition dicts, query strings, usage-node-type dicts |

## 2. Main Transformation Flow

```
Env Vars (.env / shell)
   │  os.getenv()
   ▼
get_config_value(key, default, var_type)
   │  - missing & required -> raise ValueError
   │  - missing & optional -> use default (stringified)
   │  - type coercion: bool/int/float/str
   ▼
Typed scalar config constants
   (LLM_API_KEY, LLM_MODEL, DOC_MAX_TOKENS, MAX_WORKERS, ENABLE_LLM_DOC, ...)

Grammar packages (tsc, tscpp, tsjava, ...)
   │  Language(pkg.language())
   ▼
LangConfig instances (per extension: py/java/cpp/c/kt/js/ts/tsx)
   │  bundled with: definition_dict, import_query, usage_node_types, import_resolve, same_package_visible
   ▼
_LANG_REGISTRY: dict[ext -> LangConfig]
   │  comprehension extraction per field
   ▼
Raw per-field dicts (ext -> language / definition_dict / import_query / usage_node_types / import_resolve / same_package_visible)
   │  _expand_ext_aliases() adds alias extensions (h->cpp, kts->kt, jsx->js)
   ▼
Public mapping dictionaries:
   TREE_SITTER_LANGUAGES, DEFINITION_DICTS, IMPORT_QUERIES,
   USAGE_NODE_TYPES, IMPORT_RESOLVE_CONFIG, SAME_PACKAGE_VISIBLE
```

Path-related values (`REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH`) are derived by combining `os.path` operations on `__file__` with `get_config_value()` overrides.

## 3. Output Data (module-level constants exposed to importers)

| Constant | Structure | Consumed By (external) |
|---|---|---|
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `OUTPUT_LANGUAGE`, `DOC_MAX_TOKENS` | scalars (str/int) | `llm/client.py`, `doc_creator.py` |
| `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH` | path strings | `main.py`, `doc_creator.py` |
| `MAX_WORKERS`, `MAX_RETRIES`, `RETRY_WAIT` | int | `doc_creator.py`, `pipeline.py`, `llm/client.py` |
| `ENABLE_LLM_DOC`, `ENABLE_CODE_SUMMARY` | bool | `main.py`, `pipeline.py`, `doc_creator.py` |
| `SUMMARY_MAX_CHARS`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS` | int | `doc_creator.py` |
| `EXCLUDE_PATTERNS` | `list[str]` (glob patterns) | `extractors/dependency_graph.py` (via `fnmatch`) |
| `TREE_SITTER_LANGUAGES` | `dict[ext -> Language]` | `parsers/ts_parser.py`, `import_to_path.py` |
| `DEFINITION_DICTS` | `dict[ext -> dict[node_type -> name_node_type]]` | `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` (`.keys()`) |
| `IMPORT_QUERIES` | `dict[ext -> query string \| None]` | `import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[ext -> usage-config dict \| None]` | `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[ext -> resolve-config dict]` | `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[ext -> bool]` (only truthy entries kept) | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |
| `SOURCE_ROOT_PATTERNS` | `list[str]` | `import_to_path.py` |

## 4. Key Data Structure Field Reference

**`LangConfig` (dataclass, per extension)**
| Field | Purpose |
|---|---|
| `language` | tree-sitter `Language` object for parsing |
| `definition_dict` | maps AST node type → child node type holding the definition name (or `__sentinel__` marker for special extraction) |
| `import_query` | tree-sitter S-expression query for extracting import/include statements (`@module`, `@name`, `@import_node` captures) |
| `usage_node_types` | dict describing call/attribute node types and which parent node types to skip when tracking symbol usage |
| `import_resolve` | dict controlling module path resolution (`separator`, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`) |
| `same_package_visible` | bool flag (Java/Kotlin) indicating implicit same-package symbol visibility |

**`_EXT_ALIASES`**: `dict[alias_ext -> canonical_ext]` — used by `_expand_ext_aliases()` to duplicate a canonical language's config entries under alias extensions without re-declaring them.

**`usage_node_types` sub-dict (per language)**
| Key | Purpose |
|---|---|
| `call_types` | AST node types representing function/method calls |
| `attribute_types` | AST node types representing attribute/field access |
| `skip_parent_types` | parent node types where identifier occurrences are not counted as "usage" |
| `skip_name_field_types` | (Python) additional field-based skip rule |
| `skip_parent_types_for_type_ref` | parent types to skip specifically for type-identifier references |
| `typed_alias_parent_types` | parent node types used to build variable-name → type-name alias maps |

This file performs no runtime data mutation beyond initialization: it reads environment/config sources once at import time and produces static, read-only mapping dictionaries and scalar constants consumed by other modules throughout the pipeline (parsing, dependency extraction, usage analysis, documentation generation).

# Error Handling

## Overall Strategy

This file adopts a **fail-fast** strategy for required configuration values, combined with **graceful degradation via defaults** for optional settings. There is no exception handling around module-level initialization (e.g., `Language(...)` construction, `load_dotenv()`), meaning any failure during import of this module (missing tree-sitter binding, corrupted grammar, etc.) propagates immediately and halts application startup. Configuration value retrieval (`get_config_value`) is the only place with explicit conditional error logic; all other "error handling" in this file is implicit, relying on the type system and downstream consumers (e.g., `.get()` lookups returning `None`/`{}`) to degrade gracefully rather than raising.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Missing required environment variable (no default provided) | `get_config_value` raises `ValueError` with a descriptive message instructing the user to set it via `.env` or shell | Application fails to start (import-time failure) since this occurs at module load |
| Missing optional environment variable (default provided) | Falls back to the provided default (converted to `str` first, or returns `None` if `default is None`) | No interruption; configuration silently uses default behavior |
| Type conversion errors (e.g., non-numeric string for `int`/`float` fields) | Not caught; `int()`/`float()` conversion errors propagate as unhandled `ValueError` | Application fails to start if an env var value is incompatible with its declared `var_type` |
| Boolean parsing of unrecognized string values | No error raised; any value not in `("true", "1", "yes", "on")` is treated as `False` | Silent fallback to `False` rather than an explicit error; may mask user misconfiguration |
| Unsupported file extension lookups downstream (`DEFINITION_DICTS.get`, `IMPORT_QUERIES.get`, `USAGE_NODE_TYPES.get`, `IMPORT_RESOLVE_CONFIG.get`, `SAME_PACKAGE_VISIBLE.get`) | This file does not raise; it simply omits unsupported extensions from the generated dictionaries, so callers receive `None`/falsy values via `.get()` | Dependents (e.g., `file_analyzer.py`, `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`) are responsible for checking falsy returns and skipping unsupported languages; no error is surfaced by this file itself |
| Direct extension lookup via `TREE_SITTER_LANGUAGES[file_ext]` | No error handling in this file; a `KeyError` will be raised for unregistered extensions | Callers (e.g., `ts_parser.py`, `import_to_path.py`) must catch `KeyError` themselves, as observed in dependent usage |
| Malformed `EXCLUDE_PATTERNS` environment variable (e.g., empty entries) | Empty/whitespace-only entries are filtered out via a list comprehension before assignment | No error; degrades to an effectively empty or reduced pattern list without raising |

## Design Considerations

- Configuration errors are intentionally surfaced early and loudly (via `ValueError` at import time) for values with no safe default, ensuring misconfiguration is caught before any file processing begins rather than failing deep in the pipeline.
- Language/extension-specific settings are structured so that missing or unsupported entries resolve to `None`/absence rather than exceptions, pushing the responsibility of graceful handling (skip file, skip language) onto downstream modules that consume these mappings.
- The alias-expansion mechanism (`_expand_ext_aliases`) is a pure data transformation with no error handling, relying on the correctness of `_EXT_ALIASES` and `_LANG_REGISTRY` being kept in sync; a mismatch would silently omit an alias rather than raise.

# Summary

`settings.py` is CodeTwine's central config module: loads env vars (via `get_config_value`, with typed coercion and fail-fast on missing required keys) for LLM/client, path, concurrency, and doc-summarization settings; and defines a per-language tree-sitter registry (`_LANG_REGISTRY`/`LangConfig`) covering Python, Java, C/C++, Kotlin, JS/TS. Auto-generates public dicts (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) with extension-alias expansion. No internal deps; consumed project-wide as pure config.
