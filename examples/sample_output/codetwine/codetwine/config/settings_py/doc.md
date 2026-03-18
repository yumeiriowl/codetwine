# Design Document: codetwine/config/settings.py

## Overview & Purpose

# Overview & Purpose

## Role in the Project

`settings.py` is the **single source of truth for all runtime configuration** in the CodeTwine project. It exists as a separate file to centralise every tunable value—LLM credentials, path defaults, performance limits, and per-language static data—so that no other module needs to read environment variables or hard-code language-specific AST constants directly. All other modules import from this file rather than calling `os.getenv` themselves.

The file performs three distinct responsibilities at import time:

1. **Environment variable loading** – reads `.env` via `python-dotenv` and exposes typed configuration values.
2. **Per-language static data tables** – declares AST node-type mappings, tree-sitter query strings, and usage-tracking settings for every supported language (Python, Java, C, C++, Kotlin, JavaScript, TypeScript/TSX).
3. **Public dictionary generation** – assembles `TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, and `SAME_PACKAGE_VISIBLE` by iterating `_LANG_REGISTRY` and expanding extension aliases, giving dependents a single keyed lookup interface.

---

## Main Public Interfaces

| Name | Arguments / Type | Return / Type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default=_REQUIRED`, `var_type: type = str` | converted value (`str \| int \| float \| bool`) | Reads an environment variable, applies type conversion, and raises `ValueError` if a required variable is missing |
| `LangConfig` | dataclass fields: `language`, `definition_dict`, `import_query`, `usage_node_types`, `import_resolve`, `same_package_visible` | frozen dataclass instance | Bundles all settings for one language extension into a single immutable record |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension → tree-sitter `Language` object for parser construction |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension → AST-node-type-to-name-node-type dict used by definition extractors |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension → tree-sitter S-expression query string for import extraction |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension → node-type settings dict controlling usage/call tracking |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension → module-path resolution options (separator, index files, alt extensions, etc.) |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension → flag indicating implicit same-package visibility (Java, Kotlin) |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Directory/file glob patterns to skip during project traversal |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE` | — | `str` | LLM authentication and endpoint configuration passed to `LLMClient` |
| `DOC_MAX_TOKENS`, `MAX_RETRIES`, `RETRY_WAIT` | — | `int` | LLM generation and retry behaviour |
| `MAX_WORKERS` | — | `int` | Parallelism limit for async pipeline and document generation |
| `ENABLE_LLM_DOC` | — | `bool` | Feature flag controlling whether LLM document generation runs at all |
| `SUMMARY_MAX_CHARS` | — | `int` | Character budget for per-file summary prompts |
| `OUTPUT_LANGUAGE` | — | `str` | Human language in which LLM output should be written |
| `REPO_ROOT`, `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH` | — | `str` | Filesystem path defaults derived from the package layout or environment overrides |
| `_expand_ext_aliases` | `base_dict: dict` | `dict` | Internal helper that adds alias-extension entries (e.g. `h` → `cpp`) to any registry-derived dictionary |

---

## Design Decisions

- **`_REQUIRED` sentinel pattern** — `get_config_value` uses a private `object()` sentinel rather than `None` as the "no default supplied" signal, allowing `None` itself to be a valid explicit default without ambiguity.
- **Centralised `_LANG_REGISTRY`** — All per-language data lives in a single `dict[str, LangConfig]`. The six public mapping dictionaries are derived from it mechanically, so adding a new language requires only one new `LangConfig` entry.
- **Frozen dataclass for `LangConfig`** — `@dataclass(frozen=True)` prevents accidental mutation of language settings after module load.
- **Extension alias expansion** — `_EXT_ALIASES` (`h→cpp`, `kts→kt`, `jsx→js`) is applied uniformly to every generated dictionary via `_expand_ext_aliases`, keeping alias handling out of every consumer module.
- **Special sentinel values in definition dicts** — String values beginning with `__` (e.g. `__assignment__`, `__function_declarator__`) act as sentinels indicating that name extraction requires a dedicated code path rather than a simple child-node lookup; this contract is documented inline and consumed by `definitions.py`.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants and Configuration Values

### LLM Settings
| Name | Type | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | `str` | `""` | API key for the LLM provider |
| `LLM_MODEL` | `str` | `""` | Model identifier passed to litellm |
| `LLM_API_BASE` | `str` | `""` | Custom endpoint URL for the LLM API |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Natural language for generated documentation |
| `DOC_MAX_TOKENS` | `int` | `8192` | Maximum token budget per LLM generation call |

### Path Settings
| Name | Type | Description |
|---|---|---|
| `REPO_ROOT` | `str` | Absolute, normalized path to the repository root, computed at import time relative to this file's location |
| `DEFAULT_PROJECT_DIR` | `str` | Project directory used when none is specified on the command line |
| `DEFAULT_OUTPUT_DIR` | `str` | Output directory used when neither `--output-dir` nor a custom `--project-dir` is provided |
| `DOC_TEMPLATE_PATH` | `str` | Path to the JSON file that defines the documentation section structure and prompts |

### Performance Settings
| Name | Type | Default | Description |
|---|---|---|---|
| `MAX_WORKERS` | `int` | `4` | Thread-pool concurrency limit used in both the pipeline and doc generator |
| `MAX_RETRIES` | `int` | `3` | Number of LLM call attempts before giving up |
| `RETRY_WAIT` | `int` | `2` | Seconds to wait between retry attempts on rate-limit errors |

### Analysis Settings
| Name | Type | Default | Description |
|---|---|---|---|
| `ENABLE_LLM_DOC` | `bool` | `True` | Feature flag; when `False`, the LLM client is never instantiated and doc generation is skipped entirely |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Character budget hint embedded in summary prompts |
| `EXCLUDE_PATTERNS` | `list[str]` | See below | Glob patterns for directories and files to skip during project traversal. Defaults to `__pycache__`, `.git`, `.github`, `.venv`, `node_modules` when the env variable is not set |

`EXCLUDE_PATTERNS` is derived from the comma-separated `EXCLUDE_PATTERNS` environment variable; if the variable is empty or absent, the built-in default list is used instead of an empty list.

---

## `get_config_value`

```
get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> Any
```

**Responsibility:** Central accessor for all environment-based configuration. It provides a single, consistent mechanism for reading, defaulting, and type-converting configuration values so that the rest of the codebase never calls `os.getenv` directly.

**Arguments:**
- `key`: Name of the environment variable to look up.
- `default`: Value to use when the variable is absent. If omitted (sentinel `_REQUIRED`), the function raises rather than silently returning a falsy value. Passing `None` explicitly returns `None` without conversion.
- `var_type`: Target Python type. Supports `str`, `int`, `float`, and `bool`.

**Return value:** The environment variable's value converted to `var_type`, the converted default, or `None`.

**Design decisions:**
- The private `_REQUIRED` sentinel object (rather than `None`) allows callers to distinguish between "no default supplied" and "default is explicitly `None`", enabling meaningful error messages for mandatory variables.
- Boolean conversion accepts `"true"`, `"1"`, `"yes"`, and `"on"` (case-insensitive) to align with common shell conventions.
- When `default` is not `None` and not `_REQUIRED`, it is stringified before type conversion so that the same conversion path is always exercised.

**Edge cases:**
- Raises `ValueError` only when `default` is `_REQUIRED` and the variable is missing.
- If `default` is `None`, returns `None` regardless of `var_type`.

---

## Per-Language Definition Dictionaries

`PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, `CPP_DEFINITION_DICT`, `C_DEFINITION_DICT`, `KOTLIN_DEFINITION_DICT`, `JS_DEFINITION_DICT`, `TS_DEFINITION_DICT`

**Type:** `dict[str, str]`

Each dictionary maps an AST node type (as produced by the corresponding tree-sitter grammar) to the child node type that contains the definition's name. Two sentinel string values carry special semantics:

| Sentinel | Meaning |
|---|---|
| `"__assignment__"` | The name is nested inside an assignment expression (Python `expression_statement`) |
| `"__function_declarator__"` | The name is buried inside a `function_declarator` child (C/C++ `function_definition`) |
| `"__init_declarator__"` | The name is inside an `init_declarator` child (C/C++ `declaration`) |
| `"__variable_declarator__"` | The name is inside a `variable_declarator` child (JS/TS `lexical_declaration` / `variable_declaration`) |

Sentinel values signal to `_extract_name` in `definitions.py` that standard single-level child lookup is insufficient and a dedicated extraction function must be invoked. Non-sentinel values are plain tree-sitter node type strings used for direct child lookup.

---

## Per-Language Import Query Strings

`_PYTHON_IMPORT_QUERY`, `_JS_IMPORT_QUERY`, `_JAVA_IMPORT_QUERY`, `_C_IMPORT_QUERY`, `_KOTLIN_IMPORT_QUERY`

**Type:** `str` (tree-sitter S-expression query)

Each string contains one or more tree-sitter query patterns that capture:
- `@module` — the import source (module path or file path)
- `@name` — an individual imported name when the import is destructured (e.g., `from X import Y`)
- `@import_node` — the entire import statement node, used to retrieve line numbers

The queries are kept as private module-level strings and referenced through `LangConfig`; consumers access them via the public `IMPORT_QUERIES` dictionary.

---

## Per-Language Usage Node Type Dictionaries

`_PYTHON_USAGE_NODE_TYPES`, `_JAVA_USAGE_NODE_TYPES`, `_JS_USAGE_NODE_TYPES`, `_C_USAGE_NODE_TYPES`, `_KOTLIN_USAGE_NODE_TYPES`

**Type:** `dict[str, set[str] | dict]`

Each dictionary configures the usage-tracking logic in `usage_analysis.py` for one language. Recognized keys:

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types that represent function/method calls |
| `attribute_types` | `set[str]` | AST node types that represent attribute or member access |
| `skip_parent_types` | `set[str]` | Parent node types under which an `identifier` should not be treated as a usage (definition names, parameter names, import targets, etc.) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent node types under which a `type_identifier` or `namespace_identifier` should be skipped; kept minimal because type references nearly always represent dependencies |
| `skip_name_field_types` | `set[str]` | (Python only) Parent types where the `name` field of a node is a keyword argument key rather than a usage |
| `typed_alias_parent_types` | `set[str]` | (Java, C/C++, Kotlin) Parent types whose children declare a variable with a type annotation, enabling variable-name → type-name alias tracking |

---

## `LangConfig`

```python
@dataclass(frozen=True)
class LangConfig:
    language: Language
    definition_dict: dict[str, str]
    import_query: str | None = None
    usage_node_types: dict | None = None
    import_resolve: dict | None = None
    same_package_visible: bool = False
```

**Responsibility:** An immutable value object that co-locates every language-specific setting required by the analysis pipeline. Centralizing settings per language means that adding support for a new language requires only one new registry entry.

**Fields:**
- `language`: The tree-sitter `Language` object used for parsing source files of this type.
- `definition_dict`: Maps AST node types to name-child types (see definition dictionaries above).
- `import_query`: tree-sitter S-expression query for extracting import statements; `None` for languages without import support.
- `usage_node_types`: Configuration dict for the usage-tracking pass; `None` if usage tracking is not supported for this language.
- `import_resolve`: Module-path resolution strategy. Recognized keys: `separator` (`.` or `/`), `try_init` (Python package detection), `index_ext_list` (JS/TS index file resolution), `alt_ext_list` (alternative extensions to probe), `try_bare_path` (C/C++ extensionless path lookup), `try_current_dir` (C/C++ relative-from-current resolution). `None` if import resolution is not supported.
- `same_package_visible`: When `True`, definitions in the same directory (package) are assumed reachable without an explicit import statement. Currently `True` for Java and Kotlin.

**Constraint:** `frozen=True` prevents accidental mutation of shared configuration instances at runtime.

---

## `_LANG_REGISTRY`

**Type:** `dict[str, LangConfig]`

Maps canonical file extensions (`"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"`) to their `LangConfig` instances. This is the single authoritative source from which all public mapping dictionaries are derived. Extensions not listed here (aliases such as `"h"`, `"kts"`, `"jsx"`) are added automatically by `_expand_ext_aliases`.

---

## `_EXT_ALIASES`

**Type:** `dict[str, str]`

Maps alias extensions to the canonical extension whose `LangConfig` they should inherit:
- `"h"` → `"cpp"` (C/C++ headers share the C++ grammar and settings)
- `"kts"` → `"kt"` (Kotlin Script shares the Kotlin grammar)
- `"jsx"` → `"js"` (JSX shares the JavaScript grammar)

Keeping aliases separate from the registry avoids duplicating `LangConfig` objects while still exposing all extensions through the public dictionaries.

---

## `_expand_ext_aliases`

```
_expand_ext_aliases(base_dict: dict) -> dict
```

**Responsibility:** Produces a new dictionary that includes alias extensions, given a base dictionary keyed by canonical extensions. This allows the public mapping dictionaries to be generated from `_LANG_REGISTRY` without manually repeating alias entries.

**Arguments:**
- `base_dict`: Any dictionary whose keys are canonical extension strings drawn from `_LANG_REGISTRY`.

**Return value:** A shallow copy of `base_dict` with additional entries for each alias in `_EXT_ALIASES` whose canonical extension is present. Existing keys are never overwritten.

**Edge cases:** If a canonical extension referenced by `_EXT_ALIASES` is absent from `base_dict` (e.g., `IMPORT_RESOLVE_CONFIG` omits languages with `None` resolve config), the alias is silently skipped.

---

## Public Mapping Dictionaries

All five dictionaries are auto-generated by applying `_expand_ext_aliases` to the corresponding field extracted from `_LANG_REGISTRY`, and are the only symbols imported by dependent modules.

| Name | Type | Purpose |
|---|---|---|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | Extension → tree-sitter `Language` object used to construct parsers |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | Extension → definition node mapping used by the definition extractor and usage analyzer |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | Extension → import query string passed to tree-sitter's query engine |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Extension → usage tracking configuration consumed by `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | Extension → import path resolution strategy consumed by `import_to_path.py`. Only languages with a non-`None` `import_resolve` are included |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | Extension → `True` only for languages where same-directory files are reachable without import statements. Only languages with `same_package_visible=True` are included |

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports are from external packages (`os`, `dataclasses`, `dotenv`, `tree_sitter`, and the various `tree_sitter_*` language grammar packages), which are excluded from this description.

---

### Dependents (what uses this file)

This file serves as the central configuration provider for the entire project. All dependents import from it in a unidirectional manner (dependents rely on this file; this file does not import from any of them).

- **`main.py`**: Uses `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, and `ENABLE_LLM_DOC` to resolve project and output directory paths from CLI arguments, and to decide whether to instantiate an LLM client before launching the analysis pipeline.

- **`codetwine/import_to_path.py`**: Uses `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, and `TREE_SITTER_LANGUAGES` to drive module path resolution—determining how import strings map to file paths, which languages support implicit same-package visibility, and how to parse and extract import statements.

- **`codetwine/file_analyzer.py`**: Uses `DEFINITION_DICTS` to retrieve the per-language AST node type mapping required for extracting definitions from a target source file.

- **`codetwine/pipeline.py`**: Uses `MAX_WORKERS` and `ENABLE_LLM_DOC` to configure the degree of parallelism in the analysis pipeline and to conditionally execute the document generation step.

- **`codetwine/doc_creator.py`**: Uses `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `MAX_WORKERS`, and `DOC_TEMPLATE_PATH` to control the language of generated documentation, the character limit for summaries, worker concurrency, and the path to the document template file.

- **`codetwine/llm/client.py`**: Uses `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, and `DOC_MAX_TOKENS` as default parameter values for LLM client initialization, retry logic on rate-limit errors, and token limits for generation requests.

- **`codetwine/extractors/usage_analysis.py`**: Uses `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, and `DEFINITION_DICTS` to configure AST node type recognition for usage tracking, import path resolution, same-package reference handling, and loading of target file definitions.

- **`codetwine/extractors/dependency_graph.py`**: Uses `DEFINITION_DICTS`, `EXCLUDE_PATTERNS`, and `SAME_PACKAGE_VISIBLE` to determine which file extensions are supported, which directories and files to skip during project traversal, and which languages require same-package grouping.

- **`codetwine/parsers/ts_parser.py`**: Uses `TREE_SITTER_LANGUAGES` to obtain the extension-to-`Language` object mapping needed to initialize tree-sitter parsers for each supported language.

## Data Flow

# Data Flow: `settings.py`

## Overview

This file serves as the **central configuration hub**: it reads raw environment variables and static definitions, transforms them into typed settings and structured language configs, then exports named dictionaries consumed by the rest of the codebase.

---

## Input → Transformation → Output

```
┌─────────────────────────────────┐
│  Environment / .env file        │
│  (os.getenv via python-dotenv)  │
└────────────────┬────────────────┘
                 │ raw strings
                 ▼
        get_config_value()
        ┌───────────────────┐
        │ type conversion   │  str / int / float / bool
        │ default injection │
        │ required check    │
        └───────┬───────────┘
                │ typed scalars
                ▼
   Named scalar settings
   (LLM_API_KEY, LLM_MODEL, LLM_API_BASE,
    OUTPUT_LANGUAGE, DOC_MAX_TOKENS,
    REPO_ROOT, DEFAULT_PROJECT_DIR,
    DEFAULT_OUTPUT_DIR, DOC_TEMPLATE_PATH,
    MAX_WORKERS, MAX_RETRIES, RETRY_WAIT,
    ENABLE_LLM_DOC, SUMMARY_MAX_CHARS,
    EXCLUDE_PATTERNS)

┌─────────────────────────────────────────┐
│  Static definitions (literals in file)  │
│  *_DEFINITION_DICT, *_IMPORT_QUERY,     │
│  *_USAGE_NODE_TYPES                     │
└────────────────┬────────────────────────┘
                 │
                 ▼
        _LANG_REGISTRY  (dict[str, LangConfig])
        ┌───────────────────────────────────┐
        │ one LangConfig entry per          │
        │ canonical extension (py, java,    │
        │ cpp, c, kt, js, ts, tsx)          │
        └───────────────┬───────────────────┘
                        │
                        ▼
              _expand_ext_aliases()
              adds h→cpp, kts→kt, jsx→js
                        │
                        ▼
   Public export dictionaries
   (TREE_SITTER_LANGUAGES, DEFINITION_DICTS,
    IMPORT_QUERIES, USAGE_NODE_TYPES,
    IMPORT_RESOLVE_CONFIG, SAME_PACKAGE_VISIBLE)
```

---

## Data Structures

### `get_config_value()` — Environment Variable Resolver

| Parameter   | Type     | Purpose |
|-------------|----------|---------|
| `key`       | `str`    | Environment variable name |
| `default`   | any / `_REQUIRED` sentinel | Value when unset; raises `ValueError` if `_REQUIRED` and missing |
| `var_type`  | `type`   | Target type: `str`, `int`, `float`, or `bool` |
| **returns** | typed    | Converted configuration value |

For `bool`, the string is lowercased and matched against `("true", "1", "yes", "on")`.

---

### `LangConfig` — Per-Language Settings Bundle

```
LangConfig (frozen dataclass)
├── language            : tree_sitter.Language   – parser for this language
├── definition_dict     : dict[str, str]         – AST node type → name-child node type
├── import_query        : str | None             – S-expression query for import extraction
├── usage_node_types    : dict | None            – node type sets for usage tracking
├── import_resolve      : dict | None            – path resolution settings (see below)
└── same_package_visible: bool                   – implicit same-package visibility (Java/Kotlin)
```

**`import_resolve` dict keys:**

| Key              | Used by     | Purpose |
|------------------|-------------|---------|
| `separator`      | all         | Module path delimiter (`.` or `/`) |
| `try_init`       | Python      | Look for `__init__.py` when resolving packages |
| `index_ext_list` | JS/TS       | Extensions tried as index files |
| `alt_ext_list`   | JS/TS, C/C++| Alternative extensions to try |
| `try_bare_path`  | C/C++       | Try path without extension |
| `try_current_dir`| C/C++       | Also try relative paths from current directory |

---

### `_LANG_REGISTRY` → Public Export Dictionaries

`_LANG_REGISTRY` is the single source of truth. All public dictionaries are derived from it:

| Exported Name           | Key type | Value type          | Consumer |
|-------------------------|----------|---------------------|----------|
| `TREE_SITTER_LANGUAGES` | ext str  | `Language`          | `ts_parser.py`, `import_to_path.py` |
| `DEFINITION_DICTS`      | ext str  | `dict[str,str]`     | `file_analyzer.py`, `usage_analysis.py`, `import_to_path.py`, `dependency_graph.py` |
| `IMPORT_QUERIES`        | ext str  | `str \| None`       | `import_to_path.py` |
| `USAGE_NODE_TYPES`      | ext str  | `dict \| None`      | `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | ext str  | `dict \| None`      | `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE`  | ext str  | `bool`              | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |

All six dictionaries are expanded from the canonical-extension keys in `_LANG_REGISTRY` by `_expand_ext_aliases()`, which adds `h`, `kts`, and `jsx` by copying the values from `cpp`, `kt`, and `js` respectively.

---

### `*_DEFINITION_DICT` — AST Node Type Mapping

Each definition dict maps:
```
"AST node type"  →  "child node type holding the name"
                    OR "__sentinel__"  (nested name; handled by calling module)
```

Example values: `"identifier"`, `"type_identifier"`, `"__function_declarator__"`, `"__variable_declarator__"`, `"__assignment__"`.

---

### `*_USAGE_NODE_TYPES` — Usage Tracking Configuration

Each usage dict contains:

| Key                          | Type        | Purpose |
|------------------------------|-------------|---------|
| `call_types`                 | `set[str]`  | AST node types for function calls |
| `attribute_types`            | `set[str]`  | AST node types for attribute/member access |
| `skip_parent_types`          | `set[str]`  | Parent node types that disqualify an identifier as a usage |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types that disqualify type/namespace identifiers |
| `skip_name_field_types`      | `set[str]`  | (Python only) parent types where the `name` field is skipped |
| `typed_alias_parent_types`   | `set[str]`  | (Java/C/Kotlin) parent types for typed variable alias extraction |

---

## Scalar Settings and Their Consumers

| Setting              | Type    | Consumer module(s) |
|----------------------|---------|--------------------|
| `LLM_API_KEY`        | `str`   | `llm/client.py` |
| `LLM_MODEL`          | `str`   | `llm/client.py` |
| `LLM_API_BASE`       | `str`   | `llm/client.py` |
| `DOC_MAX_TOKENS`     | `int`   | `llm/client.py` |
| `MAX_RETRIES`        | `int`   | `llm/client.py` |
| `RETRY_WAIT`         | `int`   | `llm/client.py` |
| `OUTPUT_LANGUAGE`    | `str`   | `doc_creator.py` |
| `SUMMARY_MAX_CHARS`  | `int`   | `doc_creator.py` |
| `DOC_TEMPLATE_PATH`  | `str`   | `doc_creator.py` |
| `MAX_WORKERS`        | `int`   | `pipeline.py`, `doc_creator.py` |
| `ENABLE_LLM_DOC`     | `bool`  | `main.py`, `pipeline.py` |
| `DEFAULT_PROJECT_DIR`| `str`   | `main.py` |
| `DEFAULT_OUTPUT_DIR` | `str`   | `main.py` |
| `REPO_ROOT`          | `str`   | `main.py` |
| `EXCLUDE_PATTERNS`   | `list[str]` | `dependency_graph.py` |

`EXCLUDE_PATTERNS` is derived by splitting the comma-separated `EXCLUDE_PATTERNS` env var, falling back to a hardcoded default list (`__pycache__`, `.git`, `.github`, `.venv`, `node_modules`).

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **mixed policy** that combines fail-fast behavior for critical configuration with silent graceful degradation for optional settings.

- **Required environment variables** (those called without a `default` argument) raise `ValueError` immediately at module load time, halting the application before any work begins.
- **Optional environment variables** (those called with an explicit `default`) silently fall back to their default values, allowing the application to proceed without operator intervention.
- All other constructs in this file (language registry setup, dictionary generation) have no explicit error handling; failures there propagate as unhandled exceptions at import time.

---

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Required environment variable missing (no `default` provided) | Raises `ValueError` with a descriptive message including the variable name and a hint to check `.env` | Application startup is aborted; no downstream code runs |
| Optional environment variable missing (`default` provided) | Silently uses the specified default value | No impact; application continues with the default |
| Environment variable set but type conversion fails (e.g., non-numeric value for `var_type=int`) | Unhandled; propagates as `ValueError` or `TypeError` from the built-in type constructor | Application startup is aborted |
| `EXCLUDE_PATTERNS` environment variable empty or unset | Falls back to a hardcoded default list of patterns | No impact; standard exclusions remain in effect |
| Missing tree-sitter language binding at import time | Unhandled; propagates as an `ImportError` or equivalent | Application startup is aborted |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE` unset | Silently defaults to an empty string `""` | No startup failure; runtime errors may occur later when the LLM client is used |

---

## Design Considerations

The sentinel object `_REQUIRED` is used internally to distinguish between "no default was given" and "the default value is `None`", allowing `None` to be a valid explicit default without conflicting with the absence-of-default signal. This makes the required/optional distinction unambiguous at the call site without relying on exception catching.

Because all configuration is resolved at **module import time**, any misconfiguration (type errors, missing required values) surfaces immediately when the settings module is first imported, rather than at the point where a specific setting is first accessed. This front-loads detection of configuration errors, consistent with a fail-fast philosophy for the required subset of settings.

## Summary

`settings.py` is the single configuration source for CodeTwine. It loads environment variables via `get_config_value` (with typed conversion and a `_REQUIRED` sentinel for mandatory values), declares per-language static data (definition dicts, import queries, usage node type configs), and assembles six public dictionaries—`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`—from the central `_LANG_REGISTRY` of frozen `LangConfig` dataclasses. Extension aliases (`h`, `jsx`, `kts`) are expanded uniformly via `_expand_ext_aliases`. Scalar settings cover LLM credentials, paths, performance limits, and feature flags.
