# Design Document: codetwine/config/settings.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Centralizes all configuration values, language-specific AST settings, and per-extension lookup tables required to parse, analyze, and document source code across multiple programming languages.

## 2. When to Use This Module

- **Retrieving LLM credentials and model settings**: Import `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `DOC_MAX_TOKENS`, `MAX_RETRIES`, and `RETRY_WAIT` to initialize and operate `LLMClient`.
- **Resolving project and output directories**: Import `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` to determine where to read source files and write results (used in `main.py`).
- **Looking up the tree-sitter `Language` object for a file extension**: Access `TREE_SITTER_LANGUAGES[ext]` to obtain the parser object needed before calling `parse_file` (used in `ts_parser.py`).
- **Extracting definitions from an AST**: Access `DEFINITION_DICTS.get(ext)` to get the node-type mapping passed to `extract_definitions` (used in `file_analyzer.py`, `usage_analysis.py`, and `dependency_graph.py`).
- **Running import extraction queries**: Access `IMPORT_QUERIES.get(ext)` to get the tree-sitter S-expression query string for a given language (used in `import_to_path.py`).
- **Resolving import paths to file paths**: Access `IMPORT_RESOLVE_CONFIG.get(ext)` to get separator, extension lists, and path-resolution flags for a language (used in `import_to_path.py` and `usage_analysis.py`).
- **Tracking symbol usages in AST nodes**: Access `USAGE_NODE_TYPES.get(ext)` to get call types, attribute types, and skip-parent sets for a language (used in `usage_analysis.py`).
- **Enabling same-package implicit visibility (Java/Kotlin)**: Check `SAME_PACKAGE_VISIBLE.get(ext)` to decide whether to include same-directory definitions without an explicit import (used in `import_to_path.py`, `usage_analysis.py`, and `dependency_graph.py`).
- **Filtering excluded directories and files during traversal**: Access `EXCLUDE_PATTERNS` to skip paths matching glob patterns like `__pycache__` or `.git` (used in `dependency_graph.py`).
- **Controlling parallelism and document generation**: Import `MAX_WORKERS` and `ENABLE_LLM_DOC` to configure worker counts and conditionally enable LLM-based documentation (used in `pipeline.py`, `doc_creator.py`, and `main.py`).
- **Building LLM prompts with language and length constraints**: Import `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, and `DOC_TEMPLATE_PATH` to parameterize prompt construction (used in `doc_creator.py`).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default: any`, `var_type: type` | `str \| int \| float \| bool \| None` | Reads an environment variable and returns it converted to the specified type; raises `ValueError` if a required variable is missing |
| `LangConfig` | `language: Language`, `definition_dict: dict[str, str]`, `import_query: str \| None`, `usage_node_types: dict \| None`, `import_resolve: dict \| None`, `same_package_visible: bool` | — | Immutable dataclass bundling all AST and resolution settings for a single language extension |
| `LLM_API_KEY` | — | `str` | LLM API key loaded from the environment |
| `LLM_MODEL` | — | `str` | LLM model identifier loaded from the environment |
| `LLM_API_BASE` | — | `str` | LLM API base URL loaded from the environment |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language for generated documentation output |
| `DOC_MAX_TOKENS` | — | `int` | Maximum token count for LLM document generation |
| `REPO_ROOT` | — | `str` | Absolute normalized path to the repository root |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default source project directory |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default output directory for generated artifacts |
| `DOC_TEMPLATE_PATH` | — | `str` | Path to the JSON documentation template file |
| `MAX_WORKERS` | — | `int` | Maximum number of parallel workers |
| `MAX_RETRIES` | — | `int` | Maximum number of LLM request retry attempts |
| `RETRY_WAIT` | — | `int` | Seconds to wait between LLM retries |
| `ENABLE_LLM_DOC` | — | `bool` | Whether LLM-based document generation is enabled |
| `SUMMARY_MAX_CHARS` | — | `int` | Maximum character count for file summary text |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for directories and files to skip during project traversal |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension to its tree-sitter `Language` object |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension to AST node-type → name-node-type mapping for definition extraction |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension to its tree-sitter import extraction query string |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension to AST node type settings for usage tracking |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension to import path resolution settings (separator, extension lists, flags) |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension to whether same-package implicit visibility applies (Java/Kotlin only) |

## 4. Design Decisions

- **`_LANG_REGISTRY` as the single source of truth**: All per-language settings are defined once in `_LANG_REGISTRY` as `LangConfig` entries. The five public mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`) are derived from the registry automatically, eliminating the need to maintain parallel structures when adding a new language.
- **Extension alias expansion via `_EXT_ALIASES`**: Extensions that share language settings (e.g., `.h` → `cpp`, `.jsx` → `js`, `.kts` → `kt`) are declared separately in `_EXT_ALIASES` and injected into every public dictionary by `_expand_ext_aliases`, keeping the registry entries deduplicated.
- **Sentinel strings in `definition_dict` values**: Values prefixed with `__` (e.g., `__function_declarator__`, `__variable_declarator__`, `__assignment__`) are sentinel strings rather than literal AST node types. They signal to the definition extractor that the name is nested more than one level deep and requires a dedicated extraction path.
- **`_REQUIRED` sentinel for mandatory environment variables**: A private module-level object (`_REQUIRED`) is used as the default sentinel instead of `None`, allowing `None` itself to be a valid explicit default while still distinguishing the "no default provided" case.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

### Sentinel Object

| Name | Value | Purpose |
|------|-------|---------|
| `_REQUIRED` | `object()` | Unique sentinel used as the default for `get_config_value` to distinguish "no default provided" from `None`. |

---

### LLM Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `LLM_API_KEY` | `str` | `""` | API key for the LLM provider. |
| `LLM_MODEL` | `str` | `""` | Model identifier string passed to the LLM client. |
| `LLM_API_BASE` | `str` | `""` | Base URL for the LLM API endpoint. |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Natural language for generated documentation output. |
| `DOC_MAX_TOKENS` | `int` | `8192` | Maximum token limit per LLM generation call. |

---

### Path Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `REPO_ROOT` | `str` | Computed from `__file__` | Absolute normalized path to the repository root, used as a base for all relative paths. |
| `DEFAULT_PROJECT_DIR` | `str` | `REPO_ROOT` | Default source project directory when none is specified on the command line. |
| `DEFAULT_OUTPUT_DIR` | `str` | `REPO_ROOT/output` | Default directory for generated output files. |
| `DOC_TEMPLATE_PATH` | `str` | `REPO_ROOT/doc_template.json` | Path to the JSON file defining documentation section templates and prompts. |

---

### Performance Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `MAX_WORKERS` | `int` | `4` | Maximum number of parallel worker threads/coroutines for concurrent processing. |
| `MAX_RETRIES` | `int` | `3` | Number of retry attempts for LLM API calls on transient failures. |
| `RETRY_WAIT` | `int` | `2` | Seconds to wait between retry attempts after a rate-limit error. |

---

### Analysis Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `ENABLE_LLM_DOC` | `bool` | `True` | Controls whether LLM-based documentation generation is enabled. |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Maximum character length for per-file summary text. |
| `_EXCLUDE_PATTERNS_ENV` | `str` | `""` | Raw comma-separated exclude pattern string read from the environment. |
| `EXCLUDE_PATTERNS` | `list[str]` | See below | List of glob patterns for files and directories to skip during traversal. |

**`EXCLUDE_PATTERNS` default value** (when `_EXCLUDE_PATTERNS_ENV` is empty):
```
["__pycache__", ".git", ".github", ".venv", "node_modules"]
```
When the environment variable is set, the list is built by splitting on commas and stripping whitespace from each element, discarding empty strings.

---

### Per-Language Definition Dictionaries

Each dictionary maps **AST node type → child node type** that holds the definition name, used by definition extraction logic.

#### Special sentinel values

| Sentinel | Meaning |
|----------|---------|
| `"__assignment__"` | Name is nested inside an assignment expression; a dedicated extraction function is dispatched. |
| `"__function_declarator__"` | Name is inside a nested `function_declarator` child node. |
| `"__init_declarator__"` | Name is inside a nested `init_declarator` child node. |
| `"__variable_declarator__"` | Name is inside a nested `variable_declarator` child node. |

| Constant | Language |
|----------|----------|
| `PYTHON_DEFINITION_DICT` | Python |
| `JAVA_DEFINITION_DICT` | Java |
| `CPP_DEFINITION_DICT` | C++ |
| `C_DEFINITION_DICT` | C |
| `KOTLIN_DEFINITION_DICT` | Kotlin |
| `JS_DEFINITION_DICT` | JavaScript |
| `TS_DEFINITION_DICT` | TypeScript / TSX |

---

### Per-Language Import Query Strings

Each constant is a tree-sitter S-expression query string used to extract import information from parsed ASTs.

| Constant | Language | Capture Names Used |
|----------|----------|--------------------|
| `_PYTHON_IMPORT_QUERY` | Python | `@module`, `@name`, `@import_node` |
| `_JS_IMPORT_QUERY` | JavaScript / TypeScript | `@module`, `@name`, `@import_node`, `@_require_func` |
| `_JAVA_IMPORT_QUERY` | Java | `@module`, `@import_node` |
| `_C_IMPORT_QUERY` | C / C++ | `@module`, `@import_node` |
| `_KOTLIN_IMPORT_QUERY` | Kotlin | `@module`, `@import_node` |

**Capture semantics:**
- `@module` — the import source module or path
- `@name` — an individually imported symbol (e.g., the `Y` in `from X import Y`)
- `@import_node` — the entire import statement node, used for line number retrieval
- `@_require_func` — internal capture used to match `require` calls; not surfaced to callers

---

### Per-Language Usage Node Type Dictionaries

Each dictionary configures which AST node types participate in usage tracking.

| Constant | Language |
|----------|----------|
| `_PYTHON_USAGE_NODE_TYPES` | Python |
| `_JAVA_USAGE_NODE_TYPES` | Java |
| `_JS_USAGE_NODE_TYPES` | JavaScript / TypeScript |
| `_C_USAGE_NODE_TYPES` | C / C++ |
| `_KOTLIN_USAGE_NODE_TYPES` | Kotlin |

**Common keys across all usage node type dicts:**

| Key | Type | Purpose |
|-----|------|---------|
| `call_types` | `set[str]` | AST node types representing function/method calls. |
| `attribute_types` | `set[str]` | AST node types representing attribute or member access. |
| `skip_parent_types` | `set[str]` | When an identifier's parent is one of these types, the identifier is not recorded as a usage (it is part of syntax, not a reference). |
| `skip_parent_types_for_type_ref` | `set[str]` | Same as `skip_parent_types` but applied only to type identifier and namespace identifier nodes. |

**Optional keys (language-specific):**

| Key | Languages | Purpose |
|-----|-----------|---------|
| `typed_alias_parent_types` | Java, C, Kotlin | Parent node types in which a typed variable alias may appear, enabling type-name tracking through local variable declarations. |
| `skip_name_field_types` | Python | Parent node types where the `name` field of an identifier should be skipped (e.g., keyword arguments). |

---

### Registry and Extension Lists

| Constant | Type | Purpose |
|----------|------|---------|
| `_JS_TS_EXT_LIST` | `list[str]` | Shared list of JS/TS extensions (`[".ts", ".tsx", ".js", ".jsx"]`) used as `index_ext_list` and `alt_ext_list` for module resolution. |
| `_C_CPP_EXT_LIST` | `list[str]` | Shared list of C/C++ extensions (`[".h", ".c", ".cpp"]`) used as `alt_ext_list` for header resolution. |
| `_LANG_REGISTRY` | `dict[str, LangConfig]` | Central registry mapping canonical extension strings to their complete `LangConfig` instances. |
| `_EXT_ALIASES` | `dict[str, str]` | Maps alias extensions to their canonical registry key (e.g., `"h" → "cpp"`, `"kts" → "kt"`, `"jsx" → "js"`). |

---

### Public Mapping Dictionaries (auto-generated)

All are produced by `_expand_ext_aliases` applied to values extracted from `_LANG_REGISTRY`. Adding a new language requires only a new entry in `_LANG_REGISTRY`; all public dictionaries update automatically.

| Constant | Key | Value Type | Purpose |
|----------|-----|-----------|---------|
| `TREE_SITTER_LANGUAGES` | extension `str` | `Language` | Maps file extension to the tree-sitter `Language` object for parsing. |
| `DEFINITION_DICTS` | extension `str` | `dict[str, str]` | Maps file extension to the definition node type dictionary. |
| `IMPORT_QUERIES` | extension `str` | `str \| None` | Maps file extension to the import extraction query string. |
| `USAGE_NODE_TYPES` | extension `str` | `dict \| None` | Maps file extension to the usage tracking configuration. |
| `IMPORT_RESOLVE_CONFIG` | extension `str` | `dict` | Maps file extension to the module path resolution configuration. Excludes languages where `import_resolve` is `None`. |
| `SAME_PACKAGE_VISIBLE` | extension `str` | `bool` | Maps file extension to whether implicit same-package visibility is enabled. Excludes languages where `same_package_visible` is `False`. |

---

## Function: `get_config_value`

```python
def get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None
```

**Responsibility:** Reads a named environment variable and returns it converted to the requested type. Centralizes all environment variable access and type coercion for configuration values.

**When to use:** Called at module load time for every configuration constant that may be overridden via environment variable or `.env` file.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | `str` | Environment variable name to look up. |
| `default` | Any | Value to use when the variable is absent. Omitting this argument marks the variable as required. |
| `var_type` | `type` | Target Python type for the return value (`str`, `int`, `float`, or `bool`). |

**Return type:** The converted value, whose concrete type matches `var_type`.

**Design decisions:**
- The `_REQUIRED` sentinel (a unique `object()` instance) distinguishes "no default given" from `None` as an explicit default, since `None` is a valid intentional default.
- Boolean conversion accepts `"true"`, `"1"`, `"yes"`, and `"on"` as truthy (case-insensitive); all other strings are falsy.
- When `default` is not `None` and not `_REQUIRED`, it is converted to `str` before type coercion, so non-string defaults are handled uniformly.

**Constraints & edge cases:**
- Raises `ValueError` when the variable is absent and no default was supplied.
- Returns `None` immediately when `default is None` and the variable is unset, without attempting type conversion.
- `var_type` must be one of `str`, `int`, `float`, or `bool`; other types fall through to the `str` path.

---

## Dataclass: `LangConfig`

```python
@dataclass(frozen=True)
class LangConfig:
    ...
```

**Responsibility:** Bundles all language-specific settings needed to parse, analyze, and resolve imports for a single file extension into one immutable unit. Enables `_LANG_REGISTRY` to be the single source of truth for language configuration.

**When to use:** Instantiated once per language inside `_LANG_REGISTRY`; never constructed by callers outside this module.

**Frozen:** Yes — all fields are immutable after construction.

**Fields:**

| Field | Type | Required | Purpose |
|-------|------|----------|---------|
| `language` | `Language` | Yes | The tree-sitter `Language` object used to parse source files of this type. |
| `definition_dict` | `dict[str, str]` | Yes | Maps AST node types to the child node type that holds the definition name. |
| `import_query` | `str \| None` | No (default `None`) | S-expression query string for extracting import statements from the AST. |
| `usage_node_types` | `dict \| None` | No (default `None`) | Configuration dict for usage tracking (call types, skip types, etc.). |
| `import_resolve` | `dict \| None` | No (default `None`) | Module path resolution settings. Keys vary by language (see table below). |
| `same_package_visible` | `bool` | No (default `False`) | When `True`, definitions in the same directory are reachable without an explicit import (Java / Kotlin semantics). |

**`import_resolve` dict keys:**

| Key | Type | Applies to | Meaning |
|-----|------|-----------|---------|
| `separator` | `str` | All | Delimiter used in module names (`"."` or `"/"`). |
| `try_init` | `bool` | Python | When `True`, also checks for `__init__.py` when resolving a package path. |
| `index_ext_list` | `list[str]` | JS / TS | Extensions to probe as index files (e.g., `index.ts`) when resolving a directory import. |
| `alt_ext_list` | `list[str]` | JS / TS, C / C++ | Alternative extensions to try when the exact extension does not match. |
| `try_bare_path` | `bool` | C / C++ | When `True`, attempts resolution without appending an extension. |
| `try_current_dir` | `bool` | Python, C / C++ | When `True`, also probes paths relative to the importing file's directory. |

---

## Function: `_expand_ext_aliases`

```python
def _expand_ext_aliases(base_dict: dict) -> dict
```

**Responsibility:** Adds alias extension entries to a settings dictionary so that variant extensions (e.g., `.h`, `.kts`, `.jsx`) automatically inherit the same configuration as their canonical counterpart without duplicating data.

**When to use:** Applied once to each per-extension mapping during module initialization to produce the public dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, etc.).

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `base_dict` | `dict` | A dictionary keyed by canonical extension strings. |

**Returns:** `dict` — a new dictionary containing all entries from `base_dict` plus any alias entries whose canonical key exists in `base_dict` and whose alias key is not already present.

**Design decisions:**
- Returns a new dict rather than mutating the input, keeping intermediate dicts used to construct public mappings stable.
- An alias is only added when the alias key is absent from the expanded dict, preventing accidental overwrites if an alias is explicitly registered in `_LANG_REGISTRY`.

**Constraints & edge cases:**
- Alias entries not found in `base_dict` (because their canonical key is absent, e.g., in `IMPORT_RESOLVE_CONFIG` where some languages have `None`) are silently skipped.
- The alias and its canonical entry share the exact same value object (no deep copy).

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/config/settings.py`) does not import any project-internal modules. All imports are from the standard library (`os`, `dataclasses`) or third-party packages (`dotenv`, `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_python`, `tree_sitter_typescript`).

**No project-internal dependencies exist for this file.**

---

## Dependents (modules that import this file)

The following project-internal modules depend on `codetwine/config/settings.py`:

- **`main.py` → `codetwine/config/settings.py`** : Uses `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, and `ENABLE_LLM_DOC` to resolve project and output directories and to conditionally instantiate the LLM client.

- **`codetwine/import_to_path.py` → `codetwine/config/settings.py`** : Uses `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, and `TREE_SITTER_LANGUAGES` to resolve import paths, determine same-package visibility, extract definition names, retrieve import query strings, and obtain tree-sitter language objects per file extension.

- **`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to retrieve the per-language definition extraction configuration for a given file extension.

- **`codetwine/pipeline.py` → `codetwine/config/settings.py`** : Uses `MAX_WORKERS` as the default parallelism level and `ENABLE_LLM_DOC` to conditionally trigger design document generation.

- **`codetwine/doc_creator.py` → `codetwine/config/settings.py`** : Uses `OUTPUT_LANGUAGE` to append language instructions to prompts, `SUMMARY_MAX_CHARS` to cap summary length, `MAX_WORKERS` as the default worker count, and `DOC_TEMPLATE_PATH` to load the documentation template file.

- **`codetwine/llm/client.py` → `codetwine/config/settings.py`** : Uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default constructor arguments for the LLM client, `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate limit errors, and `DOC_MAX_TOKENS` as the default token limit for generation requests.

- **`codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`** : Uses `USAGE_NODE_TYPES` to retrieve AST node type settings per language, `IMPORT_RESOLVE_CONFIG` and `SAME_PACKAGE_VISIBLE` to resolve import-based and same-package symbol references, and `DEFINITION_DICTS` to load target file definition names.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to determine the set of supported file extensions, `EXCLUDE_PATTERNS` to filter directories and files during project traversal, and `SAME_PACKAGE_VISIBLE` to identify languages that support implicit same-package references.

- **`codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py`** : Uses `TREE_SITTER_LANGUAGES` as the module-level mapping from file extension to tree-sitter `Language` object, assigned directly to the internal `_language_map` variable.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/config/settings.py` has **no dependencies** on other project-internal modules; it is a pure configuration leaf node.
- All dependent modules import **from** `codetwine/config/settings.py`; `settings.py` does not import from any of them.

This makes `codetwine/config/settings.py` a **shared configuration root** in the dependency graph — depended upon widely across the project but itself depending on nothing internal.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| Environment variables / `.env` file | Strings read via `os.getenv` | Runtime configuration values (API keys, paths, feature flags, tuning parameters) |
| `dotenv` `.env` file | Key-value text file | Loaded at module import time by `load_dotenv()`, populating environment variables |
| Hardcoded defaults | Python literals | Fallback values embedded in `get_config_value` call sites when environment variables are absent |
| Third-party grammar packages | Compiled grammar objects | `tree_sitter_python`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_typescript` — each exposes a `.language()` callable that produces a grammar handle wrapped into `Language` objects |

---

## 2. Transformation Overview

```
Stage 1: Environment resolution
    load_dotenv()
        → environment variables available to os.getenv

Stage 2: Scalar configuration extraction
    get_config_value(key, default, var_type)
        → raw string from os.getenv
        → type-cast to str / int / float / bool
        → scalar config constants (LLM_API_KEY, LLM_MODEL, MAX_WORKERS, …)

Stage 3: Derived path construction
    REPO_ROOT ← os.path.normpath(__file__ relative path)
    DEFAULT_PROJECT_DIR, DEFAULT_OUTPUT_DIR, DOC_TEMPLATE_PATH
        ← REPO_ROOT + get_config_value (path strings)

Stage 4: List construction
    _EXCLUDE_PATTERNS_ENV (str) → split + strip → EXCLUDE_PATTERNS (list[str])
    (falls back to a hardcoded list if the env var is empty)

Stage 5: Per-language static data assembly
    - Per-language dicts (PYTHON_DEFINITION_DICT, …)    plain dict literals
    - Per-language query strings (_PYTHON_IMPORT_QUERY, …)  plain string literals
    - Per-language usage-type dicts (_PYTHON_USAGE_NODE_TYPES, …) plain dict literals
    → All remain as module-level constants at this stage

Stage 6: LangConfig construction
    For each canonical extension ("py", "java", "cpp", "c", "kt", "js", "ts", "tsx"):
        Language(grammar.language())          tree-sitter Language object
        + definition_dict                     per-language dict
        + import_query                        per-language query string
        + usage_node_types                    per-language usage dict
        + import_resolve                      plain dict of resolver parameters
        + same_package_visible                bool
        → LangConfig frozen dataclass
    → _LANG_REGISTRY: dict[str, LangConfig]

Stage 7: Public mapping dictionary generation
    _LANG_REGISTRY (dict[str, LangConfig])
        → extract one field per entry          dict[str, <field type>]
        → _expand_ext_aliases()                adds alias keys ("h"→"cpp", "kts"→"kt", "jsx"→"js")
        → TREE_SITTER_LANGUAGES                dict[str, Language]
        → DEFINITION_DICTS                     dict[str, dict[str, str]]
        → IMPORT_QUERIES                       dict[str, str | None]
        → USAGE_NODE_TYPES                     dict[str, dict | None]
        → IMPORT_RESOLVE_CONFIG                dict[str, dict]
        → SAME_PACKAGE_VISIBLE                 dict[str, bool]
```

---

## 3. Outputs

All outputs are module-level names exported implicitly when the module is imported. There are no file writes or network calls; all outputs are in-process Python objects.

| Name | Type | Consumed By |
|------|------|-------------|
| `LLM_API_KEY` | `str` | `codetwine/llm/client.py` |
| `LLM_MODEL` | `str` | `codetwine/llm/client.py` |
| `LLM_API_BASE` | `str` | `codetwine/llm/client.py` |
| `OUTPUT_LANGUAGE` | `str` | `codetwine/doc_creator.py` |
| `DOC_MAX_TOKENS` | `int` | `codetwine/llm/client.py` |
| `REPO_ROOT` | `str` | `main.py` |
| `DEFAULT_PROJECT_DIR` | `str` | `main.py` |
| `DEFAULT_OUTPUT_DIR` | `str` | `main.py` |
| `DOC_TEMPLATE_PATH` | `str` | `codetwine/doc_creator.py` |
| `MAX_WORKERS` | `int` | `codetwine/pipeline.py`, `codetwine/doc_creator.py` |
| `MAX_RETRIES` | `int` | `codetwine/llm/client.py` |
| `RETRY_WAIT` | `int` | `codetwine/llm/client.py` |
| `ENABLE_LLM_DOC` | `bool` | `main.py`, `codetwine/pipeline.py` |
| `SUMMARY_MAX_CHARS` | `int` | `codetwine/doc_creator.py` |
| `EXCLUDE_PATTERNS` | `list[str]` | `codetwine/extractors/dependency_graph.py` |
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | `codetwine/import_to_path.py`, `codetwine/parsers/ts_parser.py` |
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
| `language` | `Language` | tree-sitter `Language` object used to parse source files of this type |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type (or sentinel) identifying the definition's name |
| `import_query` | `str \| None` | tree-sitter S-expression query string for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type groups controlling usage tracking (see below) |
| `import_resolve` | `dict \| None` | Module path resolution parameters (see below) |
| `same_package_visible` | `bool` | Whether same-directory files are implicitly visible without imports (Java / Kotlin) |

---

### `definition_dict` values (e.g., `PYTHON_DEFINITION_DICT`)

| Key (AST node type) | Value | Meaning |
|---------------------|-------|---------|
| `"function_definition"` | `"identifier"` | Direct child node type holding the name |
| `"class_definition"` | `"identifier"` | Direct child node type holding the name |
| `"decorated_definition"` | `"identifier"` | Direct child node type holding the name |
| `"expression_statement"` | `"__assignment__"` | Sentinel: name is extracted by a dedicated handler |

The same two-value schema applies to all per-language definition dicts. Sentinel strings (prefixed and suffixed with `__`) signal that normal child-node lookup is bypassed.

---

### `usage_node_types` dict (e.g., `_PYTHON_USAGE_NODE_TYPES`)

| Key | Type | Purpose |
|-----|------|---------|
| `call_types` | `set[str]` | AST node types that represent function/method calls |
| `attribute_types` | `set[str]` | AST node types that represent attribute or member access |
| `skip_parent_types` | `set[str]` | Parent node types under which an identifier is not treated as a usage |
| `skip_name_field_types` | `set[str]` | Parent node types under which the `name` field is skipped (Python only) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types under which type identifiers / namespace identifiers are not treated as usages |
| `typed_alias_parent_types` | `set[str]` | Parent node types from which typed variable → type name aliases are extracted (Java, C, Kotlin) |

---

### `import_resolve` dict

| Key | Type | Purpose |
|-----|------|---------|
| `separator` | `str` | Delimiter used in module names (`"."` for Python/Java/Kotlin, `"/"` for C/C++/JS/TS) |
| `try_init` | `bool` | When `True`, look for `__init__.py` to resolve package paths (Python) |
| `index_ext_list` | `list[str]` | Extensions to try as index files when resolving directory imports (JS/TS) |
| `alt_ext_list` | `list[str]` | Alternative extensions to try when the exact extension is not found |
| `try_bare_path` | `bool` | When `True`, attempt path resolution without any extension appended (C/C++) |
| `try_current_dir` | `bool` | When `True`, also resolve relative to the current file's directory (Python, C/C++) |

---

### `_LANG_REGISTRY`

| Key | Type | Purpose |
|-----|------|---------|
| `"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"` | `LangConfig` | Complete configuration for each canonical file extension |

---

### `_EXT_ALIASES`

| Key (alias ext) | Value (canonical ext) | Purpose |
|-----------------|-----------------------|---------|
| `"h"` | `"cpp"` | Header files share C++ settings |
| `"kts"` | `"kt"` | Kotlin script files share Kotlin settings |
| `"jsx"` | `"js"` | JSX files share JavaScript settings |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file applies a **fail-fast** strategy for required configuration and a **silent default fallback** strategy for optional configuration. Missing required environment variables cause an immediate `ValueError` at module load time, preventing any downstream module from operating with an undefined configuration. Optional variables silently fall back to hardcoded defaults, allowing the application to proceed without user intervention. No retry logic or logging exists within this file itself; error propagation is left entirely to the caller.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | A required environment variable (declared without a `default`) is not set in the environment or `.env` file | Raised immediately with a descriptive message identifying the missing variable | No | Module import fails; entire application startup aborts |
| Missing optional env var | An optional environment variable is absent but has a `default` value supplied | Silently replaced with the provided default value | Yes | No impact; default is used transparently |
| `int` / `float` conversion failure | An environment variable is set but contains a non-numeric string when `var_type=int` or `var_type=float` | `int()` / `float()` raises `ValueError`; no internal catch | No | Module import fails; application startup aborts |
| `None` default passthrough | `default=None` is explicitly passed and the variable is not set | Returns `None` without attempting type conversion | Yes | Caller receives `None` and must handle it |
| Missing language extension in registry | A file extension is requested from a public mapping dict (e.g., `TREE_SITTER_LANGUAGES`) that has no entry | `KeyError` (dict access) or `None` (`.get()`) raised or returned in dependent modules; not caught here | Yes (in dependents) | Dependent module skips processing for that extension |

---

## 3. Design Notes

- **Module-load-time validation**: `get_config_value` is called at the top level of the module, not lazily. This means all configuration errors surface at import time, making misconfiguration immediately visible rather than discovered mid-execution.
- **Sentinel object for required values**: The `_REQUIRED` sentinel object (a plain `object()`) distinguishes "no default provided" from `default=None`, allowing `None` to be a legitimate explicit default without being confused with "not set."
- **Type conversion is unchecked beyond booleans**: Boolean conversion is handled defensively via a string membership test. Integer and float conversions delegate directly to built-in constructors with no internal guard, meaning malformed values propagate as unhandled exceptions—consistent with the fail-fast intent.
- **Alias expansion is non-validating**: `_expand_ext_aliases` silently skips an alias if its canonical extension is absent from the base dictionary, producing no error. This is a graceful degradation choice limited to the registry expansion step.

## Summary

**`codetwine/config/settings.py`** — Centralizes all configuration constants, language-specific AST settings, and per-extension lookup tables for parsing and analyzing multi-language source code.

**Public interface:** `get_config_value(key:str, default, var_type:type)→scalar`; frozen dataclass `LangConfig(language, definition_dict:dict, import_query:str|None, usage_node_types:dict|None, import_resolve:dict|None, same_package_visible:bool)`.

**Key outputs:** `TREE_SITTER_LANGUAGES:dict[str,Language]`, `DEFINITION_DICTS:dict[str,dict]`, `IMPORT_QUERIES:dict[str,str|None]`, `USAGE_NODE_TYPES:dict[str,dict|None]`, `IMPORT_RESOLVE_CONFIG:dict[str,dict]`, `SAME_PACKAGE_VISIBLE:dict[str,bool]`, scalar constants (`LLM_API_KEY`, `MAX_WORKERS`, `EXCLUDE_PATTERNS:list[str]`, etc.). All public dicts are auto-derived from `_LANG_REGISTRY:dict[str,LangConfig]` via `_expand_ext_aliases(base_dict:dict)→dict`.
