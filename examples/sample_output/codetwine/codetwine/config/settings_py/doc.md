# Design Document: codetwine/config/settings.py

# Overview & Purpose

## 1. Module Summary

Centralizes all configuration values, tree-sitter language bindings, and per-language analysis settings that the rest of the codetwine system reads at import time.

## 2. When to Use This Module

- **Retrieving LLM credentials and model settings** — import `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `DOC_MAX_TOKENS`, and `MAX_RETRIES` / `RETRY_WAIT` to initialize `LLMClient`.
- **Resolving project and output paths** — import `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, and `DOC_TEMPLATE_PATH` to locate files and directories without hard-coding paths.
- **Looking up a tree-sitter `Language` object for a file extension** — index `TREE_SITTER_LANGUAGES` with a bare extension string (e.g., `"py"`, `"ts"`, `"h"`) to obtain the parser language needed by `ts_parser`.
- **Extracting definitions from an AST** — call `DEFINITION_DICTS.get(ext)` to obtain the node-type → name-child-type mapping consumed by `file_analyzer` and `usage_analysis`.
- **Running import extraction queries** — call `IMPORT_QUERIES.get(ext)` to retrieve the tree-sitter S-expression query string used by `import_to_path`.
- **Configuring import path resolution** — call `IMPORT_RESOLVE_CONFIG.get(ext)` to get the separator, index file list, and other resolution parameters used by `import_to_path` and `usage_analysis`.
- **Enabling same-package visibility (Java/Kotlin)** — call `SAME_PACKAGE_VISIBLE.get(ext)` to determine whether definitions in the same directory are implicitly reachable without an import statement, as used by `import_to_path`, `usage_analysis`, and `dependency_graph`.
- **Filtering files during project traversal** — read `EXCLUDE_PATTERNS` to skip directories and files matching known noise patterns (e.g., `__pycache__`, `.git`, `node_modules`).
- **Controlling parallelism and analysis behavior** — read `MAX_WORKERS`, `ENABLE_LLM_DOC`, `SUMMARY_MAX_CHARS`, and `OUTPUT_LANGUAGE` to configure pipeline and document generation.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default: any`, `var_type: type` | `str \| int \| float \| bool \| None` | Reads an environment variable and converts it to the requested type; raises `ValueError` if a required variable is absent |
| `LangConfig` | `language: Language`, `definition_dict: dict[str, str]`, `import_query: str \| None`, `usage_node_types: dict \| None`, `import_resolve: dict \| None`, `same_package_visible: bool` | — | Immutable dataclass bundling all language-specific settings for one file extension |
| `LLM_API_KEY` | — | `str` | LLM provider API key |
| `LLM_MODEL` | — | `str` | LLM model identifier |
| `LLM_API_BASE` | — | `str` | LLM API base URL |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language for generated documentation output |
| `DOC_MAX_TOKENS` | — | `int` | Maximum token budget for a single LLM documentation request |
| `REPO_ROOT` | — | `str` | Absolute path to the repository root |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default source project directory to analyze |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default directory for analysis output files |
| `DOC_TEMPLATE_PATH` | — | `str` | Path to the JSON document template file |
| `MAX_WORKERS` | — | `int` | Maximum number of parallel workers for pipeline and doc generation |
| `MAX_RETRIES` | — | `int` | Maximum LLM request retry attempts |
| `RETRY_WAIT` | — | `int` | Seconds to wait between LLM retries |
| `ENABLE_LLM_DOC` | — | `bool` | Whether LLM-based document generation is enabled |
| `SUMMARY_MAX_CHARS` | — | `int` | Maximum character count for generated file summaries |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for directories and files to skip during project traversal |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension → tree-sitter `Language` object |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension → AST node type → name child node type |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension → tree-sitter import extraction query string |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension → AST node type settings for usage tracking |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension → module path resolution parameters |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension → whether same-package implicit visibility applies |

## 4. Design Decisions

- **`_LANG_REGISTRY` as the single source of truth** — all per-language settings are declared once in `_LANG_REGISTRY` as `LangConfig` entries, and the five public mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`) are derived from it automatically. Adding support for a new language requires only one new registry entry.
- **`_EXT_ALIASES` and `_expand_ext_aliases`** — extensions that share an identical configuration (e.g., `.h` reusing `cpp`, `.jsx` reusing `js`) are declared as aliases rather than duplicated registry entries, keeping the registry minimal while ensuring all public dictionaries cover the full extension set.
- **Sentinel values in `definition_dict`** — values beginning with `__` (e.g., `"__function_declarator__"`, `"__variable_declarator__"`) signal that the name node is nested more than one level deep and that the caller (`_extract_name` in `definitions.py`) must dispatch to a dedicated extraction function rather than performing a direct child lookup.
- **`_REQUIRED` sentinel for mandatory config** — a private module-level object is used as the default sentinel in `get_config_value` so that `None` can legitimately be passed as an explicit default without being mistaken for "no default provided."

# Definition Design Specifications

---

## Module-Level Sentinel Object

### `_REQUIRED`

| Item | Detail |
|------|--------|
| Type | `object` |
| Purpose | Acts as a unique sentinel distinguishing "no default provided" from `None` as an explicit default. |

---

## Functions

### `get_config_value`

**Signature:**
```python
def get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | `str` | Name of the environment variable to retrieve. |
| `default` | any | Value used when the variable is absent. Omitting this argument makes the variable required. |
| `var_type` | `type` | Target Python type for the returned value (`str`, `int`, `float`, or `bool`). |

**Responsibility:** Provides a single, centralized point for reading and type-converting environment variables, with optional enforcement of required variables.

**When to use:** Called at module import time to populate every configuration constant in this file from `.env` or the shell environment.

**Design decisions:**
- The `_REQUIRED` sentinel (not `None`) is used as the default-absence marker, allowing `None` to be a valid explicit default.
- `bool` conversion uses a string-matching approach (`"true"`, `"1"`, `"yes"`, `"on"`) rather than Python truthiness, so the string `"false"` correctly evaluates to `False`.
- When `default` is not `None` and not `_REQUIRED`, it is converted to `str` before type conversion, ensuring uniform handling regardless of the default's original type.

**Constraints & edge cases:**
- Raises `ValueError` if the variable is absent and no default was supplied.
- `var_type` must be one of `str`, `int`, `float`, or `bool`; other types fall through to the `str` return branch without error.
- Returns `None` immediately (skipping type conversion) when `default` is `None` and the variable is unset.

---

### `_expand_ext_aliases`

**Signature:**
```python
def _expand_ext_aliases(base_dict: dict) -> dict
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `base_dict` | `dict` | A settings dictionary keyed by canonical language extension strings. |

**Returns:** A new `dict` containing all original entries plus additional entries for alias extensions defined in `_EXT_ALIASES`.

**Responsibility:** Avoids duplicating configuration entries by automatically deriving alias extensions (e.g., `"h"` → `"cpp"`, `"jsx"` → `"js"`) from canonical ones.

**When to use:** Called once per public mapping dictionary (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, etc.) during module initialization.

**Design decisions:**
- Alias entries are only added when the alias is not already present in `base_dict` and the canonical key exists, preventing accidental overwrites.
- Returns a new dict rather than mutating the input, keeping intermediate dictionaries immutable.

**Constraints & edge cases:**
- Aliases not present in `base_dict` (because their canonical key was filtered out, as with `IMPORT_RESOLVE_CONFIG`) are silently skipped.

---

## Dataclass

### `LangConfig`

**Decorator:** `@dataclass(frozen=True)` — instances are immutable after creation; all fields are read-only.

**Responsibility:** Bundles every per-language setting into a single, hashable, immutable record so the language registry (`_LANG_REGISTRY`) can be defined declaratively.

**When to use:** Instantiated once per entry in `_LANG_REGISTRY` at module load; never instantiated directly by callers outside this file.

#### Fields

| Field | Type | Purpose |
|-------|------|---------|
| `language` | `Language` | The tree-sitter `Language` object for parsing source files of this language. |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type (or sentinel string) used to extract definition names. |
| `import_query` | `str \| None` | tree-sitter S-expression query for extracting import statements; `None` if unsupported. |
| `usage_node_types` | `dict \| None` | AST node type configuration for usage tracking (call types, attribute types, skip sets); `None` if unsupported. |
| `import_resolve` | `dict \| None` | Module path resolution settings (separator, extension lists, flags); `None` if unsupported. |
| `same_package_visible` | `bool` | When `True`, definitions from same-directory files are visible without explicit imports (used for Java and Kotlin). Defaults to `False`. |

#### `import_resolve` Dictionary Keys

| Key | Type | Meaning |
|-----|------|---------|
| `separator` | `str` | Delimiter used in module names (`"."` or `"/"`). |
| `try_init` | `bool` | Whether to attempt resolving packages via `__init__.py` (Python only). |
| `index_ext_list` | `list[str]` | Extensions to try as index files when resolving bare directory imports (JS/TS). |
| `alt_ext_list` | `list[str]` | Alternative extensions to attempt during path resolution. |
| `try_bare_path` | `bool` | Whether to attempt paths without any extension (C/C++). |
| `try_current_dir` | `bool` | Whether to attempt resolution relative to the current file's directory. |

---

## Module-Level Constants

### LLM Settings

| Constant | Type | Source Env Var | Default | Purpose |
|----------|------|----------------|---------|---------|
| `LLM_API_KEY` | `str` | `LLM_API_KEY` | `""` | Authentication key for the LLM provider. |
| `LLM_MODEL` | `str` | `LLM_MODEL` | `""` | Model name passed to litellm (prefix determines provider). |
| `LLM_API_BASE` | `str` | `LLM_API_BASE` | `""` | Base URL for the LLM API endpoint. |
| `OUTPUT_LANGUAGE` | `str` | `OUTPUT_LANGUAGE` | `"English"` | Language in which generated documentation is written. |
| `DOC_MAX_TOKENS` | `int` | `DOC_MAX_TOKENS` | `8192` | Maximum token budget for a single LLM generation call. |

### Path Settings

| Constant | Type | Source | Default | Purpose |
|----------|------|--------|---------|---------|
| `REPO_ROOT` | `str` | Computed | Two directories above `settings.py` | Absolute, normalized path to the repository root. |
| `DEFAULT_PROJECT_DIR` | `str` | `DEFAULT_PROJECT_DIR` env | `REPO_ROOT` | Default directory scanned when no project dir is specified. |
| `DEFAULT_OUTPUT_DIR` | `str` | `DEFAULT_OUTPUT_DIR` env | `<REPO_ROOT>/output` | Default directory for generated output files. |
| `DOC_TEMPLATE_PATH` | `str` | `DOC_TEMPLATE_PATH` env | `<REPO_ROOT>/doc_template.json` | Path to the JSON file defining documentation section prompts. |

### Performance Settings

| Constant | Type | Source Env Var | Default | Purpose |
|----------|------|----------------|---------|---------|
| `MAX_WORKERS` | `int` | `MAX_WORKERS` | `4` | Thread/coroutine concurrency limit for parallel processing. |
| `MAX_RETRIES` | `int` | `MAX_RETRIES` | `3` | Number of LLM call attempts before giving up on rate-limit errors. |
| `RETRY_WAIT` | `int` | `RETRY_WAIT` | `2` | Seconds to wait between LLM retry attempts. |

### Analysis Settings

| Constant | Type | Source Env Var | Default | Purpose |
|----------|------|----------------|---------|---------|
| `ENABLE_LLM_DOC` | `bool` | `ENABLE_LLM_DOC` | `True` | Feature flag; disabling skips all LLM documentation generation. |
| `SUMMARY_MAX_CHARS` | `int` | `SUMMARY_MAX_CHARS` | `600` | Maximum character length for per-file summary text. |
| `EXCLUDE_PATTERNS` | `list[str]` | `EXCLUDE_PATTERNS` (comma-separated) | See below | Glob patterns for directories and files to skip during traversal. |

**Default `EXCLUDE_PATTERNS`** (when env var is empty):
`__pycache__`, `.git`, `.github`, `.venv`, `node_modules`

**Design decision for `EXCLUDE_PATTERNS`:** When the environment variable is set, it is split on commas with whitespace stripped from each element. When the variable is absent or empty, the hardcoded default list is used in its entirety; no merging of the two occurs.

---

## Per-Language Definition Dictionaries

Each constant maps **AST node type** → **child node type or sentinel string**.

| Constant | Languages |
|----------|-----------|
| `PYTHON_DEFINITION_DICT` | Python |
| `JAVA_DEFINITION_DICT` | Java |
| `CPP_DEFINITION_DICT` | C++ |
| `C_DEFINITION_DICT` | C |
| `KOTLIN_DEFINITION_DICT` | Kotlin |
| `JS_DEFINITION_DICT` | JavaScript |
| `TS_DEFINITION_DICT` | TypeScript / TSX |

**Sentinel value convention:**

| Sentinel | Meaning |
|----------|---------|
| `"__assignment__"` | Name is embedded inside an assignment expression (Python). |
| `"__function_declarator__"` | Name is nested inside a `function_declarator` child node (C/C++). |
| `"__init_declarator__"` | Name is nested inside an `init_declarator` child node (C/C++). |
| `"__variable_declarator__"` | Name is nested inside a `variable_declarator` child node (JS/TS). |

Sentinel values trigger dedicated extraction functions in `codetwine/extractors/definitions.py` rather than direct child-node lookup.

---

## Per-Language Import Query Strings

| Constant | Language(s) |
|----------|-------------|
| `_PYTHON_IMPORT_QUERY` | Python |
| `_JS_IMPORT_QUERY` | JavaScript, TypeScript, TSX |
| `_JAVA_IMPORT_QUERY` | Java |
| `_C_IMPORT_QUERY` | C, C++ |
| `_KOTLIN_IMPORT_QUERY` | Kotlin |

All query strings use tree-sitter S-expression syntax with three capture name conventions:

| Capture | Meaning |
|---------|---------|
| `@module` | The imported module or path string. |
| `@name` | An individual imported name (e.g., the `Y` in `from X import Y`). |
| `@import_node` | The entire import statement node (used to retrieve line numbers). |

---

## Per-Language Usage Node Type Dictionaries

| Constant | Language(s) |
|----------|-------------|
| `_PYTHON_USAGE_NODE_TYPES` | Python |
| `_JAVA_USAGE_NODE_TYPES` | Java |
| `_JS_USAGE_NODE_TYPES` | JavaScript, TypeScript, TSX |
| `_C_USAGE_NODE_TYPES` | C, C++ |
| `_KOTLIN_USAGE_NODE_TYPES` | Kotlin |

Each dictionary shares a common key schema:

| Key | Type | Purpose |
|-----|------|---------|
| `call_types` | `set[str]` | AST node types representing function/method calls. |
| `attribute_types` | `set[str]` | AST node types representing attribute or member access. |
| `skip_parent_types` | `set[str]` | Parent node types under which an identifier is part of syntax (definition name, parameter, import path) rather than a usage. |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types under which type identifier or namespace identifier occurrences are skipped. |
| `skip_name_field_types` | `set[str]` *(Python only)* | Parent types where the `name` field of a node is not a usage. |
| `typed_alias_parent_types` | `set[str]` *(Java, C, Kotlin)* | Parent types used to build variable-name → type-name alias mappings from typed declarations. |

---

## Language Registry and Alias Constants

### `_JS_TS_EXT_LIST`
`list[str]`: `[".ts", ".tsx", ".js", ".jsx"]` — shared extension list used for JS/TS index file and alternative extension resolution.

### `_C_CPP_EXT_LIST`
`list[str]`: `[".h", ".c", ".cpp"]` — shared extension list used for C/C++ alternative extension resolution.

### `_LANG_REGISTRY`
`dict[str, LangConfig]` — the authoritative registry mapping canonical extension strings to their `LangConfig`. Keys: `"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"`.

### `_EXT_ALIASES`
`dict[str, str]` — maps alias extensions to canonical ones: `"h"` → `"cpp"`, `"kts"` → `"kt"`, `"jsx"` → `"js"`.

---

## Public Mapping Dictionaries

All five are generated by calling `_expand_ext_aliases` over the corresponding field extracted from `_LANG_REGISTRY`, so alias extensions (`h`, `kts`, `jsx`) are automatically included.

| Constant | Type | Keyed by | Value | Consumers |
|----------|------|----------|-------|-----------|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | File extension | tree-sitter `Language` object | `ts_parser.py`, `import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | File extension | Definition node-type mapping | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | File extension | Import S-expression query string | `import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | File extension | Usage tracking node-type config | `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | File extension | Import path resolution config | `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | File extension | Whether same-package implicit visibility applies | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |

**Constraint for `IMPORT_RESOLVE_CONFIG` and `SAME_PACKAGE_VISIBLE`:** Only registry entries where the respective field is non-`None` / `True` are included before alias expansion, so languages without resolution config or same-package semantics are absent from these dictionaries.

# Dependency Description

## Dependencies (modules this file imports)

`codetwine/config/settings.py` has **no project-internal module dependencies**. It does not import from any other file within the `codetwine` package. All imports in this file are from the standard library (`os`, `dataclasses`) or third-party packages (`dotenv`, `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_python`, `tree_sitter_typescript`), which are excluded from this description.

---

## Dependents (modules that import this file)

The following project-internal modules import symbols from `codetwine/config/settings.py`:

- **`main.py` → `codetwine/config/settings.py`** : Uses `DEFAULT_PROJECT_DIR` and `DEFAULT_OUTPUT_DIR` to resolve the project and output directory paths when CLI arguments are not provided; uses `REPO_ROOT` as a fallback base path for the output directory; uses `ENABLE_LLM_DOC` to decide whether to instantiate an `LLMClient`.

- **`codetwine/import_to_path.py` → `codetwine/config/settings.py`** : Uses `IMPORT_RESOLVE_CONFIG` to obtain per-language module resolution settings (separator, index extensions, etc.); uses `SAME_PACKAGE_VISIBLE` to enable implicit same-package symbol resolution for Java/Kotlin; uses `DEFINITION_DICTS` to extract definition names from same-package files; uses `IMPORT_QUERIES` to retrieve the import extraction query string for a given extension; uses `TREE_SITTER_LANGUAGES` to obtain the tree-sitter `Language` object for parsing.

- **`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to obtain the per-language AST node type mapping for definition extraction from a target file.

- **`codetwine/pipeline.py` → `codetwine/config/settings.py`** : Uses `MAX_WORKERS` as the default parallelism level for file processing; uses `ENABLE_LLM_DOC` to conditionally trigger design document generation.

- **`codetwine/doc_creator.py` → `codetwine/config/settings.py`** : Uses `OUTPUT_LANGUAGE` to append the output language instruction to LLM prompts; uses `SUMMARY_MAX_CHARS` as the character limit for summary generation; uses `MAX_WORKERS` as the default number of parallel workers; uses `DOC_TEMPLATE_PATH` to load the documentation template JSON file.

- **`codetwine/llm/client.py` → `codetwine/config/settings.py`** : Uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default constructor arguments for the LLM client; uses `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate-limit errors; uses `DOC_MAX_TOKENS` as the default token limit for generation requests.

- **`codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`** : Uses `USAGE_NODE_TYPES` to obtain the per-language AST node type configuration for usage tracking; uses `IMPORT_RESOLVE_CONFIG` to determine the module path separator when matching imports to target files; uses `SAME_PACKAGE_VISIBLE` to allow same-directory references without explicit imports (Java/Kotlin); uses `DEFINITION_DICTS` to load definition names from a target file.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS.keys()` to build the set of supported file extensions for project-wide file collection; uses `EXCLUDE_PATTERNS` to filter out directories and files during `os.walk` traversal; uses `SAME_PACKAGE_VISIBLE` to identify language extensions that require same-package grouping.

- **`codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py`** : Uses `TREE_SITTER_LANGUAGES` as the module-level extension-to-`Language` mapping for file parsing.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/config/settings.py` does not import from any other project-internal module.
- All dependent modules import from `codetwine/config/settings.py` without `settings.py` importing back from them.

`codetwine/config/settings.py` acts as a **pure configuration source** at the base of the dependency graph; data flows outward from it to all other modules.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| `.env` file / shell environment | Key-value string pairs | Loaded via `load_dotenv()` and `os.getenv()`. Provides all runtime configuration values (API keys, paths, toggles, limits). |
| `tree_sitter_*` language packages | Native language bindings | Each package exposes a `language()` (or `language_typescript()` / `language_tsx()`) function whose return value is wrapped into a `tree_sitter.Language` object. |
| Hardcoded defaults | Python literals | Used as fallbacks when environment variables are absent (e.g., `MAX_WORKERS=4`, `OUTPUT_LANGUAGE="English"`). |

---

## 2. Transformation Overview

### Stage 1 — Environment variable resolution
`get_config_value()` reads each environment variable by name. If the variable is absent, it falls back to the provided default or raises `ValueError` for required variables. The raw string value is then cast to the declared `var_type` (`str`, `int`, `float`, or `bool`). This produces all scalar configuration constants (`LLM_API_KEY`, `MAX_WORKERS`, `ENABLE_LLM_DOC`, etc.).

### Stage 2 — Path derivation
`REPO_ROOT` is computed from `__file__` using `os.path` operations. `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `DOC_TEMPLATE_PATH` are derived either from environment variables or by joining `REPO_ROOT` with relative path segments.

### Stage 3 — Per-language static data assembly
Each language's configuration data (tree-sitter `Language` object, definition node mapping dict, import query string, usage node type dict, import resolution dict) is assembled into individual named constants (`PYTHON_DEFINITION_DICT`, `_PYTHON_IMPORT_QUERY`, `_PYTHON_USAGE_NODE_TYPES`, etc.).

### Stage 4 — `LangConfig` instantiation
Each language's static data is packaged into a frozen `LangConfig` dataclass instance and registered in `_LANG_REGISTRY`, a dict keyed by canonical extension string (e.g., `"py"`, `"java"`, `"ts"`).

### Stage 5 — Public mapping generation
Five public dicts (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`) and one bool dict (`SAME_PACKAGE_VISIBLE`) are generated by iterating `_LANG_REGISTRY` and extracting the relevant field from each `LangConfig`.

### Stage 6 — Alias expansion
`_expand_ext_aliases()` takes each of the six public dicts and adds entries for alias extensions defined in `_EXT_ALIASES` (`"h" → "cpp"`, `"kts" → "kt"`, `"jsx" → "js"`), producing the final exported dicts that consumers use.

---

## 3. Outputs

All outputs are module-level constants exposed for import by other modules. No files are written and no side effects occur beyond loading the `.env` file.

| Exported Name | Type | Consumed By |
|---------------|------|-------------|
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE` | `str` | `codetwine/llm/client.py` |
| `DOC_MAX_TOKENS`, `MAX_RETRIES`, `RETRY_WAIT` | `int` | `codetwine/llm/client.py` |
| `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS` | `str` / `int` | `codetwine/doc_creator.py` |
| `DOC_TEMPLATE_PATH` | `str` | `codetwine/doc_creator.py` |
| `MAX_WORKERS` | `int` | `codetwine/pipeline.py`, `codetwine/doc_creator.py` |
| `ENABLE_LLM_DOC` | `bool` | `main.py`, `codetwine/pipeline.py` |
| `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT` | `str` | `main.py` |
| `EXCLUDE_PATTERNS` | `list[str]` | `codetwine/extractors/dependency_graph.py` |
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | `codetwine/parsers/ts_parser.py`, `codetwine/import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | `codetwine/file_analyzer.py`, `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | `codetwine/import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | `codetwine/extractors/usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py` |

---

## 4. Key Data Structures

### `LangConfig` (frozen dataclass)

| Field | Type | Purpose |
|-------|------|---------|
| `language` | `Language` | tree-sitter `Language` object used for parsing source files of this language |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type that holds the definition name; drives definition extraction |
| `import_query` | `str \| None` | tree-sitter S-expression query for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type settings controlling usage/call tracking (see below) |
| `import_resolve` | `dict \| None` | Module path resolution settings (see below) |
| `same_package_visible` | `bool` | Whether definitions in the same package/directory are implicitly visible (Java, Kotlin) |

---

### `_LANG_REGISTRY`

| Key | Type | Purpose |
|-----|------|---------|
| `"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"` | `str` | Canonical file extension used as the lookup key |
| *(value)* | `LangConfig` | Complete language configuration bundle for that extension |

---

### `definition_dict` (per-language, e.g. `PYTHON_DEFINITION_DICT`)

| Key | Type | Purpose |
|-----|------|---------|
| AST node type (e.g. `"function_definition"`) | `str` | The parent node type to match in the AST |
| *(value)* | `str` | Child node type holding the name, or a sentinel string such as `"__assignment__"`, `"__function_declarator__"`, `"__init_declarator__"`, `"__variable_declarator__"` signalling a dedicated extraction path |

---

### `usage_node_types` dict (e.g. `_PYTHON_USAGE_NODE_TYPES`)

| Key | Type | Purpose |
|-----|------|---------|
| `"call_types"` | `set[str]` | AST node types representing function/method call expressions |
| `"attribute_types"` | `set[str]` | AST node types representing attribute/member access |
| `"skip_parent_types"` | `set[str]` | Parent node types under which an identifier is not treated as a usage (definitions, imports, parameters) |
| `"skip_parent_types_for_type_ref"` | `set[str]` | Parent node types under which a type identifier is not treated as a type reference usage |
| `"skip_name_field_types"` *(Python only)* | `set[str]` | Parent types whose `name` field child should be skipped |
| `"typed_alias_parent_types"` *(Java, C, Kotlin only)* | `set[str]` | Parent types whose children declare typed variable aliases for usage tracking |

---

### `import_resolve` dict

| Key | Type | Purpose |
|-----|------|---------|
| `"separator"` | `str` | Delimiter used in module paths (`"."` for Python/Java/Kotlin, `"/"` for C/C++/JS/TS) |
| `"try_init"` | `bool` *(optional)* | When `True`, attempts to resolve a package path via `__init__.py` (Python) |
| `"index_ext_list"` | `list[str]` *(optional)* | Extensions to try as index files when resolving a directory import (JS/TS) |
| `"alt_ext_list"` | `list[str]` *(optional)* | Alternative extensions to try when the exact extension is not found |
| `"try_bare_path"` | `bool` *(optional)* | When `True`, attempts resolution without appending an extension (C/C++) |
| `"try_current_dir"` | `bool` *(optional)* | When `True`, also attempts relative resolution from the current file's directory (Python, C/C++) |

---

### `_EXT_ALIASES`

| Key | Type | Purpose |
|-----|------|---------|
| `"h"` | `str` (`"cpp"`) | `.h` header files share the C++ language configuration |
| `"kts"` | `str` (`"kt"`) | Kotlin Script files share the Kotlin language configuration |
| `"jsx"` | `str` (`"js"`) | JSX files share the JavaScript language configuration |

# Error Handling

## 1. Overall Strategy

`settings.py` applies a **fail-fast** strategy for configuration errors at module load time, combined with **safe defaults** for optional settings. All configuration is resolved once at import time via `get_config_value()`; any missing required variable raises immediately, preventing the application from starting in a misconfigured state. Optional variables are silently substituted with their declared defaults, allowing the module to load successfully without external configuration.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | A required environment variable (declared without a `default`) is not set in the environment or `.env` file | Raises `ValueError` with a descriptive message identifying the missing key and instructing the user to set it | No | Module import fails; entire application terminates at startup |
| Missing optional env var | An optional environment variable is not set (has a `default` value provided) | The default value is used; no exception is raised | Yes (default substituted) | No impact; module loads normally with the fallback value |
| `None` default env var | An optional variable is not set and `default=None` is explicitly passed | Returns `None` directly without type conversion | Yes | Downstream consumers receive `None` and must handle it |
| Type conversion failure | An environment variable is set but its string value cannot be converted to the declared `var_type` (e.g., a non-numeric string for `var_type=int`) | Built-in conversion (`int()`, `float()`) raises an uncaught exception | No | Module import fails; application terminates at startup |
| Empty `EXCLUDE_PATTERNS` | The `EXCLUDE_PATTERNS` environment variable is set to an empty string or not set | Falls back to a hardcoded default list (`__pycache__`, `.git`, `.github`, `.venv`, `node_modules`) | Yes (default list used) | No impact; standard exclusions remain active |
| `bool` conversion edge case | A `var_type=bool` variable is set to any string not in `("true", "1", "yes", "on")` | Evaluates to `False`; no exception is raised | Yes | The setting silently evaluates to `False` regardless of intent |

---

## 3. Design Notes

- **Load-time enforcement**: Because all `get_config_value()` calls execute at module import (not lazily), configuration errors surface immediately when the application starts rather than at the point of first use. This makes the failure boundary predictable and explicit.
- **Sentinel object for required values**: The `_REQUIRED` sentinel object (distinct from `None`) is used as the default marker so that `None` can itself be a valid explicit default, avoiding ambiguity between "no default given" and "default is `None`".
- **Type conversion is unconditional**: Once a value is resolved (either from the environment or the default), type conversion is applied uniformly. If a non-`None` default is provided, it is stringified and then converted, ensuring consistent behavior regardless of the value source.
- **No error handling within the registry**: The `_LANG_REGISTRY` construction and all tree-sitter `Language()` instantiations are performed at module level with no surrounding error handling. Failures in language binding initialization propagate directly as uncaught exceptions, consistent with the fail-fast posture of the module.
- **Downstream responsibility**: For settings such as `LLM_API_KEY` and `LLM_MODEL`, the module defaults to empty strings rather than requiring them. Error handling for these semantically invalid-but-syntactically-valid values is delegated to the consuming modules (e.g., `codetwine/llm/client.py`).

# Summary

**settings.py** centralizes all configuration constants, tree-sitter language bindings, and per-language analysis settings for the codetwine system.

**Functions:** `get_config_value(key:str, default, var_type:type)`, `_expand_ext_aliases(base_dict:dict)`

**Dataclass:** `LangConfig(frozen)` — bundles `language:Language`, `definition_dict:dict`, `import_query:str|None`, `usage_node_types:dict|None`, `import_resolve:dict|None`, `same_package_visible:bool`

**Key outputs:** `TREE_SITTER_LANGUAGES:dict[str,Language]`, `DEFINITION_DICTS:dict[str,dict]`, `IMPORT_QUERIES:dict[str,str]`, `USAGE_NODE_TYPES:dict[str,dict]`, `IMPORT_RESOLVE_CONFIG:dict[str,dict]`, `SAME_PACKAGE_VISIBLE:dict[str,bool]`, scalar constants for LLM, paths, and performance.
