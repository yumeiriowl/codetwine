# Design Document: codetwine/config/settings.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

`settings.py` is the single source of truth for all runtime configuration in the codetwine project. It centralises every tunable parameter—LLM credentials, file-system paths, performance knobs, analysis switches, and the complete per-language tree-sitter grammar registry—so that the rest of the codebase can import named constants instead of scattering `os.getenv` calls or hard-coded values across modules.

It exists as a separate file to enforce a clean separation between *configuration* and *behaviour*: every other module (`main.py`, `pipeline.py`, `file_analyzer.py`, `import_to_path.py`, the extractor suite, and the LLM client) imports from this single location, which means changing any setting requires editing exactly one place.

---

## Public Interface

| Name | Arguments / Type | Return / Type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default=_REQUIRED`, `var_type: type = str` | converted value (`str` / `int` / `float` / `bool`) | Reads an environment variable and converts it to the requested type; raises `ValueError` if the variable is absent and no default is provided |
| `LangConfig` | dataclass fields: `language`, `definition_dict`, `import_query`, `usage_node_types`, `import_resolve`, `same_package_visible` | frozen dataclass instance | Bundles all tree-sitter and analysis settings for one language extension into a single immutable record |
| `_expand_ext_aliases` | `base_dict: dict` | `dict` | Returns a copy of a settings dict with alias extensions (e.g. `h`, `kts`, `jsx`) populated from `_EXT_ALIASES` |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension → tree-sitter `Language` object; consumed by the parser layer |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension → AST node-type-to-name-node-type dict; drives definition extraction |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension → tree-sitter S-expression query string; used to extract import statements |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension → node-type settings dict for usage/call-site tracking |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension → module-path resolution rules (`separator`, `try_init`, `index_ext_list`, etc.) |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension → flag indicating implicit same-package symbol visibility (Java, Kotlin) |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE` | — | `str` | LLM provider credentials consumed by `LLMClient` |
| `DOC_MAX_TOKENS`, `MAX_RETRIES`, `RETRY_WAIT` | — | `int` | LLM call behaviour limits consumed by `LLMClient` |
| `MAX_WORKERS` | — | `int` | Parallelism degree consumed by the pipeline and doc-creator |
| `ENABLE_LLM_DOC` | — | `bool` | Feature flag that controls whether LLM documentation generation runs |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for directories/files to skip during project traversal |
| `SUMMARY_MAX_CHARS` | — | `int` | Character budget for per-file summary generation |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language for LLM-generated documentation output |
| `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH` | — | `str` | Resolved filesystem paths used by the pipeline and doc-creator |

---

## Design Decisions

- **Registry-driven language support (`_LANG_REGISTRY`).** All per-language configuration is stored as `LangConfig` entries in a single `dict`. The four public mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) are derived from this registry at import time via dict comprehensions. Adding a new language requires only one new `_LANG_REGISTRY` entry; no other code needs to change.

- **Extension alias expansion.** Rather than duplicating identical `LangConfig` records for extension variants that share the same grammar (`.h`/`.cpp`, `.kts`/`.kt`, `.jsx`/`.js`), a lightweight alias table (`_EXT_ALIASES`) is applied once by `_expand_ext_aliases` when building each public dict.

- **Frozen dataclass for language config.** `LangConfig` is declared `@dataclass(frozen=True)`, making every language record immutable after construction and safe to share across threads.

- **Sentinel-based required-value detection.** `get_config_value` uses a private sentinel object (`_REQUIRED`) instead of `None` as the "no default provided" signal, allowing `None` itself to be a valid explicit default.

- **Single `.env` load point.** `load_dotenv()` is called once at module import time, so all subsequent `os.getenv` calls across the project reflect the `.env` file without each module needing to call it independently.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Sentinel and Configuration Loader

### `_REQUIRED` (sentinel object)
A private sentinel used as the default value for the `default` parameter of `get_config_value`. Its sole purpose is to distinguish "no default was provided" from `None` being explicitly passed as a default, since `None` is a legitimate default value in some cases.

---

### `get_config_value(key, default=_REQUIRED, var_type=str) -> Any`

**Arguments:**
- `key: str` — The environment variable name to look up.
- `default` — Fallback value when the variable is absent. Omitting this argument marks the variable as required. Passing `None` explicitly allows the function to return `None`.
- `var_type: type` — Target Python type for the returned value; supports `str`, `int`, `float`, and `bool`.

**Returns:** The environment variable's value converted to `var_type`, the converted default, or `None`.

**Raises:** `ValueError` if the variable is absent and no default was supplied.

**Design intent:** Centralizes all environment variable access so that type coercion, missing-value enforcement, and default handling are handled in one place rather than scattered across the codebase.

**Edge cases and constraints:**
- When `var_type=bool`, the string is lowercased and matched against `("true", "1", "yes", "on")`; any other string evaluates to `False`.
- If `default` is not `None` and the env var is absent, the default is stringified before type conversion so the same conversion path is always exercised.
- If `default is None` and the variable is absent, the function returns `None` immediately without attempting conversion.

---

## Data Class

### `LangConfig` (frozen dataclass)

Bundles all language-specific settings required by the analysis pipeline into a single immutable record per file extension.

**Fields:**
| Field | Type | Meaning |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object used for parsing. |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type that holds the definition name. Special sentinel values (`__assignment__`, `__function_declarator__`, etc.) signal that name extraction requires a dedicated code path. |
| `import_query` | `str \| None` | tree-sitter S-expression query string for extracting import statements. `None` for languages without query support. |
| `usage_node_types` | `dict \| None` | AST node type configuration controlling which nodes are counted as usages versus definitions or syntax keywords. |
| `import_resolve` | `dict \| None` | Module-path resolution strategy (separator character, index file conventions, alternative extensions, bare-path attempts, same-directory lookup). |
| `same_package_visible` | `bool` | When `True`, definitions in the same directory are considered reachable without an explicit import statement (Java/Kotlin package semantics). Defaults to `False`. |

**Design intent:** Freezing the dataclass prevents accidental mutation of the central registry at runtime. Grouping all per-language knobs into one record makes adding a new language a single-location change in `_LANG_REGISTRY`.

---

## Helper Function

### `_expand_ext_aliases(base_dict) -> dict`

**Arguments:**
- `base_dict: dict` — A dictionary keyed by canonical extension strings (e.g., `"cpp"`, `"js"`).

**Returns:** A new dictionary containing all entries from `base_dict` plus additional entries for alias extensions defined in `_EXT_ALIASES`, where each alias maps to the same value as its canonical counterpart.

**Design intent:** Keeps `_LANG_REGISTRY` free of duplicate entries for extensions that share identical language settings (e.g., `.h` sharing C++ settings, `.jsx` sharing JS settings). Each public mapping dictionary is generated by passing the registry-derived dict through this function.

**Edge cases and constraints:**
- An alias is only added when the alias key is not already present in `base_dict` and the canonical key is present; existing entries are never overwritten.
- The original `base_dict` is not mutated; a new dict is returned.

---

## Module-Level Configuration Values

The following are module-level constants produced by calling `get_config_value`. They are not functions but are the primary interface consumed by dependent modules.

| Name | Type | Source env var | Default | Role |
|---|---|---|---|---|
| `LLM_API_KEY` | `str` | `LLM_API_KEY` | `""` | Authentication credential passed to the LLM client. |
| `LLM_MODEL` | `str` | `LLM_MODEL` | `""` | Model identifier forwarded to litellm. |
| `LLM_API_BASE` | `str` | `LLM_API_BASE` | `""` | Custom endpoint URL for the LLM provider. |
| `OUTPUT_LANGUAGE` | `str` | `OUTPUT_LANGUAGE` | `"English"` | Natural language for generated documentation. |
| `DOC_MAX_TOKENS` | `int` | `DOC_MAX_TOKENS` | `8192` | Token budget for each LLM generation call. |
| `REPO_ROOT` | `str` | — | Computed from `__file__` | Absolute path to the repository root, used as the base for relative default paths. |
| `DEFAULT_PROJECT_DIR` | `str` | `DEFAULT_PROJECT_DIR` | `REPO_ROOT` | Project directory used when none is supplied on the command line. |
| `DEFAULT_OUTPUT_DIR` | `str` | `DEFAULT_OUTPUT_DIR` | `REPO_ROOT/output` | Output directory used when none is supplied and no explicit project dir was given. |
| `DOC_TEMPLATE_PATH` | `str` | `DOC_TEMPLATE_PATH` | `REPO_ROOT/doc_template.json` | Path to the JSON document template loaded by the doc creator. |
| `MAX_WORKERS` | `int` | `MAX_WORKERS` | `4` | Maximum parallel worker count for async pipeline stages. |
| `MAX_RETRIES` | `int` | `MAX_RETRIES` | `3` | Number of LLM call retry attempts before giving up. |
| `RETRY_WAIT` | `int` | `RETRY_WAIT` | `2` | Seconds to wait between retry attempts on rate-limit errors. |
| `ENABLE_LLM_DOC` | `bool` | `ENABLE_LLM_DOC` | `True` | When `False`, the LLM client is not instantiated and document generation is skipped entirely. |
| `SUMMARY_MAX_CHARS` | `int` | `SUMMARY_MAX_CHARS` | `600` | Character limit hint included in summary generation prompts. |
| `EXCLUDE_PATTERNS` | `list[str]` | `EXCLUDE_PATTERNS` | See below | Glob patterns for directories and files to skip during project traversal. |

**`EXCLUDE_PATTERNS` construction:** If `EXCLUDE_PATTERNS` env var is set to a non-empty string, it is split on commas and stripped of whitespace. If unset or empty, the list defaults to `["__pycache__", ".git", ".github", ".venv", "node_modules"]`.

---

## Per-Language Definition Dictionaries

Each `*_DEFINITION_DICT` constant maps an AST node type string to either a direct child node type string or a sentinel string beginning with `__`. The sentinel values (`__assignment__`, `__function_declarator__`, `__init_declarator__`, `__variable_declarator__`) indicate that the name cannot be found by inspecting a single level of children and that `definitions.py` must dispatch to a language-specific extraction routine.

| Constant | Language |
|---|---|
| `PYTHON_DEFINITION_DICT` | Python |
| `JAVA_DEFINITION_DICT` | Java |
| `CPP_DEFINITION_DICT` | C++ |
| `C_DEFINITION_DICT` | C |
| `KOTLIN_DEFINITION_DICT` | Kotlin |
| `JS_DEFINITION_DICT` | JavaScript |
| `TS_DEFINITION_DICT` | TypeScript / TSX |

---

## Per-Language Import Query Strings

Each `_*_IMPORT_QUERY` constant is a tree-sitter S-expression query string. All queries use three standard capture names: `@module` for the imported module or path, `@name` for individual imported names, and `@import_node` for the entire import statement (used for line-number retrieval). Multiple patterns may appear in a single query string.

| Constant | Language(s) |
|---|---|
| `_PYTHON_IMPORT_QUERY` | Python |
| `_JS_IMPORT_QUERY` | JavaScript and TypeScript (shared) |
| `_JAVA_IMPORT_QUERY` | Java |
| `_C_IMPORT_QUERY` | C and C++ (shared) |
| `_KOTLIN_IMPORT_QUERY` | Kotlin |

---

## Per-Language Usage Node Type Dictionaries

Each `_*_USAGE_NODE_TYPES` constant is a plain dict with the following keys:

| Key | Meaning |
|---|---|
| `call_types` | AST node type set representing function/method call sites. |
| `attribute_types` | AST node type set representing member/attribute access. |
| `skip_parent_types` | Identifier nodes whose parent is one of these types are not treated as usages (they are definition names, parameter names, import clause names, etc.). |
| `skip_parent_types_for_type_ref` | Narrower skip set applied only when the node is a type identifier or namespace identifier; import and scope-resolution contexts are excluded. |
| `skip_name_field_types` | *(Python only)* Parent types for which the `name` field child should be skipped. |
| `typed_alias_parent_types` | *(Java, C/C++, Kotlin)* Parent node types from which a `type → variable_name` alias mapping is extracted to track typed variables across files. |

---

## Registry and Public Mapping Dictionaries

### `_LANG_REGISTRY: dict[str, LangConfig]`
The single authoritative registry mapping canonical extension strings (`"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"`) to their `LangConfig` instances. All public mapping dictionaries are derived from this registry, so adding or modifying a language requires only one entry here.

### `_EXT_ALIASES: dict[str, str]`
Maps alias extensions to their canonical counterpart in `_LANG_REGISTRY`. Currently maps `"h" → "cpp"`, `"kts" → "kt"`, and `"jsx" → "js"`.

### Public Mapping Dictionaries (auto-generated)

All five public dictionaries are produced by iterating `_LANG_REGISTRY` and applying `_expand_ext_aliases`, so alias extensions are automatically included.

| Name | Value type | Purpose |
|---|---|---|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | Extension → tree-sitter `Language` object; consumed by the parser module. |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | Extension → definition node mapping dict; consumed by definition extractor and usage analyzer. |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | Extension → import query string; consumed by import extractor. |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Extension → usage tracking configuration; consumed by usage analyzer. |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | Extension → import path resolution config; consumed by import-to-path resolver and usage analyzer. Only extensions with a non-`None` `import_resolve` are included. |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | Extension → `True` for languages where same-directory files are implicitly accessible (Java, Kotlin). Only extensions where `same_package_visible=True` are included. |

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports are either standard library modules (`os`, `dataclasses`) or third-party packages (`dotenv`, `tree_sitter`, and the various `tree_sitter_*` language bindings). There are no imports from other files within the `codetwine` project.

---

### Dependents (what uses this file)

This file acts as the central configuration hub for the project. Many modules depend on it unidirectionally (they import from this file; this file does not import from them).

- **`main.py`**: Imports `DEFAULT_PROJECT_DIR`, `REPO_ROOT`, `DEFAULT_OUTPUT_DIR`, and `ENABLE_LLM_DOC` to resolve working directories and decide whether to instantiate the LLM client at application startup.

- **`codetwine/import_to_path.py`**: Imports `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, and `TREE_SITTER_LANGUAGES` to perform language-aware module path resolution, import statement parsing, and same-package symbol visibility checks.

- **`codetwine/file_analyzer.py`**: Imports `DEFINITION_DICTS` to obtain per-language AST node type mappings for extracting symbol definitions from a parsed source file.

- **`codetwine/pipeline.py`**: Imports `MAX_WORKERS` and `ENABLE_LLM_DOC` to control parallel execution and conditionally trigger design document generation.

- **`codetwine/doc_creator.py`**: Imports `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `MAX_WORKERS`, and `DOC_TEMPLATE_PATH` to configure LLM-generated documentation output language, summary length limits, worker concurrency, and the template file location.

- **`codetwine/llm/client.py`**: Imports `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, and `DOC_MAX_TOKENS` to initialize the LLM API client and govern retry behavior and token limits during text generation.

- **`codetwine/extractors/usage_analysis.py`**: Imports `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, and `DEFINITION_DICTS` to drive AST-based usage tracking, import path resolution, and same-package reference handling across all supported languages.

- **`codetwine/extractors/dependency_graph.py`**: Imports `DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, and `SAME_PACKAGE_VISIBLE` to determine which file extensions are supported, which directories and files to skip during project traversal, and which languages allow implicit same-package dependencies.

- **`codetwine/parsers/ts_parser.py`**: Imports `TREE_SITTER_LANGUAGES` to obtain the `Language` object for each file extension when parsing source files with tree-sitter.

**Direction**: All dependencies are unidirectional — every dependent imports from this settings file, and this file imports nothing from any other project-internal module.

## Data Flow

# Data Flow — `codetwine/config/settings.py`

## Input Data Sources

| Source | How It Is Read | Examples |
|---|---|---|
| Environment variables / `.env` file | `get_config_value()` via `os.getenv()` + `load_dotenv()` | `LLM_API_KEY`, `MAX_WORKERS`, `EXCLUDE_PATTERNS` |
| tree-sitter grammar packages | Imported directly at module level | `tspython.language()`, `tstypescript.language_typescript()` |
| Hardcoded literals in module | Python dict/list literals | `PYTHON_DEFINITION_DICT`, `_PYTHON_IMPORT_QUERY`, etc. |

---

## Main Transformation Flow

```
.env file / shell environment
         │
         ▼
   load_dotenv()   ──▶  os.getenv(key)
         │
         ▼
   get_config_value(key, default, var_type)
     ├─ missing + no default  ──▶  raise ValueError
     ├─ missing + has default ──▶  use str(default)
     └─ type conversion: bool / int / float / str
         │
         ▼
   Flat configuration constants
   (LLM_MODEL, MAX_WORKERS, EXCLUDE_PATTERNS, …)

Hardcoded grammar dicts + query strings + usage type dicts
         │
         ▼
   _LANG_REGISTRY: dict[str, LangConfig]   (one entry per canonical extension)
         │
         ├─ _expand_ext_aliases()           (adds h→cpp, kts→kt, jsx→js)
         │
         ▼
   Public mapping dictionaries
   (TREE_SITTER_LANGUAGES, DEFINITION_DICTS,
    IMPORT_QUERIES, USAGE_NODE_TYPES,
    IMPORT_RESOLVE_CONFIG, SAME_PACKAGE_VISIBLE)
```

---

## Key Data Structures

### `LangConfig` (frozen dataclass)

| Field | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object used for parsing |
| `definition_dict` | `dict[str, str]` | AST node type → name-child type for definition extraction |
| `import_query` | `str \| None` | S-expression query used to extract import statements |
| `usage_node_types` | `dict \| None` | Sets of AST node types governing call/attribute/skip logic |
| `import_resolve` | `dict \| None` | Module path resolution parameters (see below) |
| `same_package_visible` | `bool` | Whether same-directory files are implicitly visible (Java/Kotlin) |

### `import_resolve` dict keys

| Key | Purpose |
|---|---|
| `separator` | Delimiter used in module names (`"."` or `"/"`) |
| `try_init` | Look for `__init__.py` when resolving Python packages |
| `index_ext_list` | Extensions to try as index files (JS/TS) |
| `alt_ext_list` | Alternative file extensions to probe |
| `try_bare_path` | Try path without extension (C/C++) |
| `try_current_dir` | Also resolve relative to current directory (C/C++) |

### `usage_node_types` dict keys

| Key | Purpose |
|---|---|
| `call_types` | AST node types representing function calls |
| `attribute_types` | AST node types representing attribute/member access |
| `skip_parent_types` | Parent node types under which an identifier is not a usage |
| `skip_parent_types_for_type_ref` | Parent types to skip specifically for type references |
| `skip_name_field_types` | Parent types where the `name` field is not a usage (Python) |
| `typed_alias_parent_types` | Parent types that introduce typed variable aliases (Java/C/Kotlin) |

---

## Output Data (Public Exports)

Each public dictionary is keyed by **file extension string** (without leading `.`) and is produced by filtering/projecting `_LANG_REGISTRY` through `_expand_ext_aliases()`.

| Exported Name | Value Type | Consumed By |
|---|---|---|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | `ts_parser.py`, `import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | `file_analyzer.py`, `usage_analysis.py`, `import_to_path.py`, `dependency_graph.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | `import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |
| Flat constants (`LLM_*`, `MAX_*`, etc.) | scalar values | `client.py`, `pipeline.py`, `doc_creator.py`, `main.py` |

## Error Handling

# Error Handling

## Overall Error Handling Strategy

This file adopts a **mixed strategy** that combines fail-fast for strictly required values with graceful degradation for optional configuration. The central design principle is to front-load configuration failures at import time, so that downstream modules never receive an undefined or invalid configuration state.

---

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Required environment variable missing (`_REQUIRED` sentinel) | Raises `ValueError` immediately with a descriptive message indicating the variable name and remediation hint | Halts the entire application at startup; no partially-initialized state is propagated to dependents |
| Optional environment variable missing (explicit `default` provided) | Returns the default value; no exception is raised | Zero impact on runtime; callers receive a predictable fallback value |
| `None` passed as `default` | Returns `None` directly, bypassing type conversion | Caller receives `None`; caller is responsible for handling it |
| Type conversion failure (`int`, `float`, `bool`) | Delegates to Python's built-in `int()` / `float()` constructors; a `ValueError` or `TypeError` from these will propagate uncaught | Application startup fails if an environment variable contains a malformed value for the declared type |
| `bool` type conversion | Handled by explicit string comparison (`"true"`, `"1"`, `"yes"`, `"on"`); no exception is raised for unrecognized values — they evaluate to `False` | Silently treats any unrecognized value as `False`; no error is surfaced |
| Missing extension key in generated mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, etc.) | Not handled in this file; callers use `.get()` or `[]` access directly | Dependent modules handle `KeyError` or a `None` return individually at their own call sites |

---

## Design Considerations

**Sentinel object for "required" detection**: Rather than relying on `None` as the absence marker (which would collide with an intentional `default=None`), a private `_REQUIRED = object()` sentinel is used. This allows the function signature to cleanly distinguish "no default provided" from "default is explicitly `None`", avoiding ambiguity at the cost of a small non-obvious idiom.

**Import-time evaluation**: All `get_config_value` calls execute at module import time. Any `ValueError` from a missing required variable, or a propagated conversion error, surfaces immediately when any dependent module imports `settings`. This is an intentional fail-fast boundary ensuring that configuration errors are never silently deferred to the moment a configuration value is first used at runtime.

**No error handling for tree-sitter language instantiation**: The `Language(...)` calls within `_LANG_REGISTRY` also execute at import time with no exception handling. If a tree-sitter grammar library is missing or incompatible, the resulting exception propagates unhandled, which is consistent with the file's overall fail-fast posture for non-optional infrastructure.

**Responsibility boundary**: This file is solely responsible for loading and validating configuration values. It does not handle errors arising from how downstream modules *use* the exported mappings (e.g., an unsupported file extension producing a `None` from `DEFINITION_DICTS.get()`). That responsibility is explicitly delegated to the calling modules.

## Summary

`settings.py` is the single configuration source for codetwine. It loads environment variables via `get_config_value` (with sentinel-based required-value detection) and exposes scalar constants for LLM credentials, performance limits, paths, and feature flags. It defines a frozen `LangConfig` dataclass bundling tree-sitter grammar, definition dicts, import queries, usage node types, and import resolution rules per language. A central `_LANG_REGISTRY` maps canonical extensions to `LangConfig` instances; `_expand_ext_aliases` adds variant extensions. Five public dicts (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) are derived from this registry and consumed by all other modules.
