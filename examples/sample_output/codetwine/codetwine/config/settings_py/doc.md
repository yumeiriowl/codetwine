# Design Document: codetwine/config/settings.py

## Overview & Purpose

## 1. Module Summary

Centralizes all configuration constants, language-specific AST settings, and public mapping dictionaries required to parse, analyze, and document source files across every supported programming language.

## 2. When to Use This Module

- **Retrieving LLM credentials and model settings**: Import `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `DOC_MAX_TOKENS`, and `MAX_RETRIES`/`RETRY_WAIT` when constructing an `LLMClient` instance.
- **Resolving project and output paths**: Import `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` when determining where to read source files or write results (e.g., in `main.py`).
- **Looking up a tree-sitter `Language` object by file extension**: Access `TREE_SITTER_LANGUAGES[ext]` to obtain the pre-built `Language` object needed to create a tree-sitter parser (e.g., in `ts_parser.py`).
- **Extracting definitions from an AST**: Access `DEFINITION_DICTS.get(ext)` to obtain the node-type-to-name-type mapping used by the definition extractor (e.g., in `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`).
- **Running import extraction queries**: Access `IMPORT_QUERIES.get(ext)` to obtain the S-expression query string passed to tree-sitter (e.g., in `import_to_path.py`).
- **Configuring import path resolution**: Access `IMPORT_RESOLVE_CONFIG.get(ext)` to obtain the separator, extension lists, and resolution flags used when mapping module names to file paths (e.g., in `import_to_path.py`, `usage_analysis.py`).
- **Tracking identifier usages in AST nodes**: Access `USAGE_NODE_TYPES.get(ext)` to obtain the call, attribute, and skip-parent-type sets used during usage analysis (e.g., in `usage_analysis.py`).
- **Enabling same-package implicit visibility**: Check `SAME_PACKAGE_VISIBLE.get(ext)` to determine whether definitions from files in the same directory are referenceable without explicit imports (e.g., Java/Kotlin handling in `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py`).
- **Controlling pipeline behavior**: Import `ENABLE_LLM_DOC`, `MAX_WORKERS`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `DOC_TEMPLATE_PATH`, and `EXCLUDE_PATTERNS` to configure document generation, parallelism, and file traversal (e.g., in `pipeline.py`, `doc_creator.py`, `dependency_graph.py`).

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default: any`, `var_type: type` | `str \| int \| float \| bool \| None` | Reads an environment variable and returns it converted to the specified type; raises `ValueError` if the variable is missing and no default is provided |
| `LangConfig` | `language: Language`, `definition_dict: dict[str, str]`, `import_query: str \| None`, `usage_node_types: dict \| None`, `import_resolve: dict \| None`, `same_package_visible: bool` | — | Frozen dataclass bundling all AST and resolution settings for a single language extension |
| `LLM_API_KEY` | — | `str` | LLM provider API key loaded from the environment |
| `LLM_MODEL` | — | `str` | LLM model identifier loaded from the environment |
| `LLM_API_BASE` | — | `str` | LLM API base URL loaded from the environment |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language in which generated documentation is written |
| `DOC_MAX_TOKENS` | — | `int` | Maximum token count for a single LLM documentation generation request |
| `REPO_ROOT` | — | `str` | Absolute path to the repository root directory |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default source project directory used when no CLI argument is provided |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default output directory for generated artifacts |
| `DOC_TEMPLATE_PATH` | — | `str` | File path to the JSON documentation template |
| `MAX_WORKERS` | — | `int` | Maximum number of parallel workers for concurrent processing |
| `MAX_RETRIES` | — | `int` | Maximum number of retry attempts for LLM API calls |
| `RETRY_WAIT` | — | `int` | Seconds to wait between LLM API retry attempts |
| `ENABLE_LLM_DOC` | — | `bool` | Whether LLM-based documentation generation is enabled |
| `SUMMARY_MAX_CHARS` | — | `int` | Maximum character count for generated file summaries |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for files and directories to skip during project traversal |
| `PYTHON_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for Python definitions |
| `JAVA_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for Java definitions |
| `CPP_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for C++ definitions |
| `C_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for C definitions |
| `KOTLIN_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for Kotlin definitions |
| `JS_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for JavaScript definitions |
| `TS_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for TypeScript definitions |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension to the pre-built tree-sitter `Language` object |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension to its definition node type mapping |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension to its tree-sitter import extraction query string |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension to its AST node type settings for usage tracking |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension to its module path resolution settings |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension to whether same-package implicit visibility is enabled |

## 4. Design Decisions

- **Registry-driven public dictionaries**: All per-language settings are defined once in `_LANG_REGISTRY` (a `dict[str, LangConfig]`), and the five public mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`) are derived from it automatically. Adding support for a new language requires only a single new entry in `_LANG_REGISTRY`.
- **Extension alias expansion**: `_EXT_ALIASES` declares extensions that share an existing language's configuration (e.g., `.h` → `cpp`, `.jsx` → `js`, `.kts` → `kt`). The `_expand_ext_aliases` function applies these aliases uniformly to every generated public dictionary, eliminating duplication of settings for closely related extensions.
- **Sentinel values in definition dictionaries**: Some AST node types require multi-level name extraction that cannot be expressed as a simple child node type string. These entries use sentinel strings (e.g., `"__function_declarator__"`, `"__variable_declarator__"`, `"__assignment__"`) as values, signaling that the caller (`_extract_name` in `definitions.py`) must dispatch to a dedicated extraction function rather than performing a direct child lookup.
- **Required vs. optional environment variables**: `get_config_value` uses a private sentinel object (`_REQUIRED`) as the default marker, allowing a clean distinction between "caller provided no default" (raises `ValueError`) and "caller explicitly passed `None`" (returns `None`).

## Definition Design Specifications

---

## Module-Level Constants and Configuration Values

### Sentinel Object

| Name | Type | Purpose |
|------|------|---------|
| `_REQUIRED` | `object` | Unique sentinel used as a default marker to distinguish "no default provided" from `None`. |

---

### LLM Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `LLM_API_KEY` | `str` | `""` | API key for the LLM provider. |
| `LLM_MODEL` | `str` | `""` | Model identifier string passed to the LLM client. |
| `LLM_API_BASE` | `str` | `""` | Base URL for the LLM API endpoint. |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Natural language used in generated documentation. |
| `DOC_MAX_TOKENS` | `int` | `8192` | Maximum token count for a single LLM generation call. |

---

### Path Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `REPO_ROOT` | `str` | Computed from `__file__` | Absolute normalized path to the repository root. Used as the base for all relative default paths. |
| `DEFAULT_PROJECT_DIR` | `str` | `REPO_ROOT` | Default directory scanned for source files. |
| `DEFAULT_OUTPUT_DIR` | `str` | `REPO_ROOT/output` | Default directory where analysis results are written. |
| `DOC_TEMPLATE_PATH` | `str` | `REPO_ROOT/doc_template.json` | Path to the JSON file defining documentation section prompts. |

---

### Performance Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `MAX_WORKERS` | `int` | `4` | Degree of parallelism for concurrent processing tasks. |
| `MAX_RETRIES` | `int` | `3` | Number of retry attempts on transient LLM errors. |
| `RETRY_WAIT` | `int` | `2` | Seconds to wait between retry attempts. |

---

### Analysis Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `ENABLE_LLM_DOC` | `bool` | `True` | Whether to invoke the LLM for documentation generation. |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Character limit enforced on file-level summary text. |
| `_EXCLUDE_PATTERNS_ENV` | `str` | `""` | Raw comma-separated string read from the environment variable `EXCLUDE_PATTERNS`. |
| `EXCLUDE_PATTERNS` | `list[str]` | See below | List of glob patterns for directories and files to skip during traversal. |

**`EXCLUDE_PATTERNS` default list** (used when the environment variable is empty):

| Pattern |
|---------|
| `__pycache__` |
| `.git` |
| `.github` |
| `.venv` |
| `node_modules` |

**Constraint:** When the environment variable is non-empty, every comma-separated token is stripped of whitespace; empty tokens are discarded.

---

### Per-Language Definition Dictionaries

Each dictionary maps an AST node type to the child node type (or a sentinel string) that holds the symbol's name. These are consumed by the definition extractor to identify named symbols in parsed source trees.

**Sentinel values:**

| Sentinel | Meaning |
|----------|---------|
| `"__assignment__"` | Name is nested inside an assignment expression; requires dedicated extraction logic. |
| `"__function_declarator__"` | Name is nested inside a function declarator subtree. |
| `"__init_declarator__"` | Name is nested inside an init declarator subtree. |
| `"__variable_declarator__"` | Name is nested inside a variable declarator subtree. |

#### `PYTHON_DEFINITION_DICT`
| AST Node Type | Name Node / Sentinel |
|---------------|---------------------|
| `function_definition` | `identifier` |
| `class_definition` | `identifier` |
| `decorated_definition` | `identifier` |
| `expression_statement` | `__assignment__` |

#### `JAVA_DEFINITION_DICT`
| AST Node Type | Name Node |
|---------------|-----------|
| `class_declaration` | `identifier` |
| `method_declaration` | `identifier` |
| `interface_declaration` | `identifier` |
| `constructor_declaration` | `identifier` |
| `enum_declaration` | `identifier` |

#### `CPP_DEFINITION_DICT`
| AST Node Type | Name Node / Sentinel |
|---------------|---------------------|
| `class_specifier` | `type_identifier` |
| `struct_specifier` | `type_identifier` |
| `function_declarator` | `identifier` |
| `function_definition` | `__function_declarator__` |
| `namespace_definition` | `namespace_identifier` |
| `declaration` | `__init_declarator__` |
| `alias_declaration` | `type_identifier` |
| `enum_specifier` | `type_identifier` |
| `preproc_def` | `identifier` |

#### `C_DEFINITION_DICT`
| AST Node Type | Name Node / Sentinel |
|---------------|---------------------|
| `function_declarator` | `identifier` |
| `function_definition` | `__function_declarator__` |
| `struct_specifier` | `type_identifier` |
| `declaration` | `__init_declarator__` |
| `preproc_def` | `identifier` |
| `type_definition` | `type_identifier` |
| `enum_specifier` | `type_identifier` |

#### `KOTLIN_DEFINITION_DICT`
| AST Node Type | Name Node |
|---------------|-----------|
| `class_declaration` | `identifier` |
| `function_declaration` | `identifier` |
| `object_declaration` | `identifier` |

#### `JS_DEFINITION_DICT`
| AST Node Type | Name Node / Sentinel |
|---------------|---------------------|
| `function_declaration` | `identifier` |
| `method_definition` | `identifier` |
| `class_declaration` | `identifier` |
| `lexical_declaration` | `__variable_declarator__` |
| `variable_declaration` | `__variable_declarator__` |

#### `TS_DEFINITION_DICT`
| AST Node Type | Name Node / Sentinel |
|---------------|---------------------|
| `function_declaration` | `identifier` |
| `method_definition` | `identifier` |
| `class_declaration` | `type_identifier` |
| `interface_declaration` | `type_identifier` |
| `lexical_declaration` | `__variable_declarator__` |
| `variable_declaration` | `__variable_declarator__` |
| `type_alias_declaration` | `type_identifier` |
| `enum_declaration` | `identifier` |

---

### Per-Language Import Query Strings

These are private constants holding tree-sitter S-expression query strings. Each query uses the following capture names:

| Capture Name | Meaning |
|--------------|---------|
| `@module` | The import source (module path or package name). |
| `@name` | An individual imported symbol (e.g., `Y` in `from X import Y`). |
| `@import_node` | The entire import statement node, used for line number retrieval. |

| Constant | Languages |
|----------|-----------|
| `_PYTHON_IMPORT_QUERY` | Python |
| `_JS_IMPORT_QUERY` | JavaScript, TypeScript, TSX |
| `_JAVA_IMPORT_QUERY` | Java |
| `_C_IMPORT_QUERY` | C, C++ |
| `_KOTLIN_IMPORT_QUERY` | Kotlin |

---

### Per-Language Usage Node Type Dictionaries

These private dictionaries configure which AST node types are relevant when tracking symbol usages. Each dictionary may contain the following keys:

| Key | Type | Purpose |
|-----|------|---------|
| `call_types` | `set[str]` | AST node types that represent function/method call expressions. |
| `attribute_types` | `set[str]` | AST node types that represent attribute or member access. |
| `skip_parent_types` | `set[str]` | Identifier occurrences whose parent is one of these types are not counted as usages (they are definitions, imports, or syntax). |
| `skip_name_field_types` | `set[str]` | (Python only) Skip identifiers that appear as the `name` field of these parent node types. |
| `skip_parent_types_for_type_ref` | `set[str]` | For type-reference identifiers specifically, skip when the parent is one of these types. |
| `typed_alias_parent_types` | `set[str]` | (Java, C/C++, Kotlin) Parent node types from which typed variable alias mappings are built. |

| Constant | Language |
|----------|----------|
| `_PYTHON_USAGE_NODE_TYPES` | Python |
| `_JAVA_USAGE_NODE_TYPES` | Java |
| `_JS_USAGE_NODE_TYPES` | JavaScript / TypeScript |
| `_C_USAGE_NODE_TYPES` | C / C++ |
| `_KOTLIN_USAGE_NODE_TYPES` | Kotlin |

---

### Extension List Constants

| Name | Value | Purpose |
|------|-------|---------|
| `_JS_TS_EXT_LIST` | `[".ts", ".tsx", ".js", ".jsx"]` | Shared list of JS/TS extensions used in `index_ext_list` and `alt_ext_list` resolution config. |
| `_C_CPP_EXT_LIST` | `[".h", ".c", ".cpp"]` | Shared list of C/C++ extensions used in `alt_ext_list` resolution config. |

---

### Extension Alias Mapping

| Name | Type | Purpose |
|------|------|---------|
| `_EXT_ALIASES` | `dict[str, str]` | Maps alias extensions to their canonical registry key, so that `.h`, `.kts`, and `.jsx` automatically inherit their canonical language's configuration. |

| Alias | Canonical |
|-------|-----------|
| `h` | `cpp` |
| `kts` | `kt` |
| `jsx` | `js` |

---

### Public Mapping Dictionaries

These are module-level exports consumed by other modules. All are generated from `_LANG_REGISTRY` and expanded with aliases via `_expand_ext_aliases`.

| Name | Type | Purpose |
|------|------|---------|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | Maps file extension to the tree-sitter `Language` object for parsing. |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | Maps file extension to its definition node type dictionary. |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | Maps file extension to its import extraction query string. |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Maps file extension to its usage-tracking node type configuration. |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | Maps file extension to its module resolution configuration. Only extensions with a non-`None` `import_resolve` are included. |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | Maps file extension to `True` for languages (Java, Kotlin) where same-package symbols are visible without explicit imports. Only `True` entries are included. |

---

## Functions

---

### `get_config_value`

**Signature:**
```python
def get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None
```

**Responsibility:** Reads a named environment variable and returns it converted to the requested Python type. Centralizes all environment-variable access for the configuration module.

**When to use:** Called at module load time to populate every configuration constant from the environment or a `.env` file.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | `str` | Name of the environment variable to read. |
| `default` | any | Value to use when the variable is absent. Omitting this argument makes the variable required. |
| `var_type` | `type` | Target Python type: `str`, `int`, `float`, or `bool`. |

**Returns:** The environment variable's value converted to `var_type`, or the default if the variable is not set.

**Design decisions:**
- The sentinel object `_REQUIRED` is used as the default for `default`, making `None` a valid explicit default distinct from "no default".
- Boolean conversion treats `"true"`, `"1"`, `"yes"`, and `"on"` (case-insensitive) as `True`; all other strings as `False`.
- When a non-`None` default is used as a fallback, it is converted through `str()` before type conversion, ensuring uniform processing regardless of whether the value came from the environment or the default.

**Constraints & edge cases:**
- Raises `ValueError` when the variable is absent and no default is provided.
- Returns `None` directly (without type conversion) when the variable is absent and `default=None`.
- No validation is performed on whether the string can actually be converted to `var_type`; conversion errors propagate as standard Python exceptions.

---

### `_expand_ext_aliases`

**Signature:**
```python
def _expand_ext_aliases(base_dict: dict) -> dict
```

**Responsibility:** Produces a new dictionary that contains all entries from `base_dict` plus additional entries for alias extensions defined in `_EXT_ALIASES`, so callers never need to look up aliases manually.

**When to use:** Called once per public mapping dictionary at module load time to add alias extension entries (`.h`, `.kts`, `.jsx`) derived from their canonical counterparts.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `base_dict` | `dict` | A settings dictionary keyed by canonical extension strings (without leading dot). |

**Returns:** A new `dict` containing all entries from `base_dict` plus alias entries where the alias key is not already present and the canonical key exists.

**Design decisions:**
- Only adds an alias entry if the alias key is not already present in `base_dict`, preserving any explicit overrides.
- Does not mutate `base_dict`; always returns a new dictionary.

**Constraints & edge cases:**
- Aliases whose canonical key is absent from `base_dict` are silently skipped.
- The function has no awareness of nested values; it copies references, not deep copies.

---

## Classes

---

### `LangConfig`

**Signature:**
```python
@dataclass(frozen=True)
class LangConfig
```

**Responsibility:** Bundles all language-specific settings needed to parse, analyze, and resolve imports for one file extension into a single immutable record. Enables `_LANG_REGISTRY` to manage all languages uniformly.

**When to use:** Instantiated once per language entry in `_LANG_REGISTRY` at module load time; never instantiated by callers outside this file.

**Fields:**

| Field | Type | Purpose |
|-------|------|---------|
| `language` | `Language` | tree-sitter `Language` object used to build parsers and queries. |
| `definition_dict` | `dict[str, str]` | Maps AST node type to name node type for definition extraction. |
| `import_query` | `str \| None` | tree-sitter S-expression query string for extracting import statements. `None` for languages without import query support. |
| `usage_node_types` | `dict \| None` | Configuration dict controlling which AST node types are tracked during usage analysis. `None` for unsupported languages. |
| `import_resolve` | `dict \| None` | Module path resolution configuration. `None` for languages without path resolution. Keys documented below. |
| `same_package_visible` | `bool` | Whether symbols in the same package directory are implicitly visible without an explicit import (e.g., Java, Kotlin). Defaults to `False`. |

**`import_resolve` dictionary keys:**

| Key | Type | Applicable Languages | Purpose |
|-----|------|---------------------|---------|
| `separator` | `str` | All | Delimiter used to split module names into path segments (`"."` or `"/"`). |
| `try_init` | `bool` | Python | When `True`, also attempts to resolve a package by looking for `__init__.py`. |
| `index_ext_list` | `list[str]` | JS/TS | Extensions tried as index files when a path resolves to a directory. |
| `alt_ext_list` | `list[str]` | JS/TS, C/C++ | Alternative extensions tried when the exact extension does not match. |
| `try_bare_path` | `bool` | C/C++ | When `True`, attempts resolution without any file extension. |
| `try_current_dir` | `bool` | Python, C/C++ | When `True`, also attempts relative resolution from the current file's directory. |

**Design decisions:**
- Declared `frozen=True` so that configuration cannot be mutated after module initialization, preventing accidental runtime changes.
- Fields with `None` defaults (`import_query`, `usage_node_types`, `import_resolve`) allow languages to opt out of features they do not support without requiring separate registry structures.

**Constraints & edge cases:**
- `same_package_visible` defaults to `False`; it must be explicitly set to `True` for Java and Kotlin.
- The `import_resolve` dict is not validated at construction time; missing keys are handled by callers using `.get()` with fallback defaults.

---

### `_LANG_REGISTRY`

| Name | Type | Purpose |
|------|------|---------|
| `_LANG_REGISTRY` | `dict[str, LangConfig]` | Central registry mapping canonical extension strings (without leading dot) to their `LangConfig` instances. The single source of truth from which all public mapping dictionaries are derived. |

**Registered canonical extensions:**

| Key | Language | `same_package_visible` |
|-----|----------|----------------------|
| `py` | Python | `False` |
| `java` | Java | `True` |
| `cpp` | C++ | `False` |
| `c` | C | `False` |
| `kt` | Kotlin | `True` |
| `js` | JavaScript | `False` |
| `ts` | TypeScript | `False` |
| `tsx` | TypeScript JSX | `False` |

**Design decision:** `ts` and `tsx` share `JS_DEFINITION_DICT` and `_JS_IMPORT_QUERY` for import analysis but use distinct tree-sitter `Language` objects (`language_typescript()` vs `language_tsx()`), reflecting that their AST grammars differ while their import syntax is identical. The TypeScript definition dict (`TS_DEFINITION_DICT`) is used for both, as opposed to `JS_DEFINITION_DICT`, because TypeScript introduces additional node types (`interface_declaration`, `type_alias_declaration`, etc.).

## Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/config/settings.py`) contains only standard library imports (`os`, `dataclasses`) and third-party package imports (`dotenv`, `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_python`, `tree_sitter_typescript`). There are **no project-internal module dependencies**.

---

## Dependents (modules that import this file)

The following project-internal modules import symbols from this file:

- **`main.py` → `codetwine/config/settings.py`** : Uses `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT` to resolve project and output directories at startup, and `ENABLE_LLM_DOC` to conditionally instantiate the LLM client.

- **`codetwine/import_to_path.py` → `codetwine/config/settings.py`** : Uses `IMPORT_RESOLVE_CONFIG` to determine module path resolution strategy per language extension, `SAME_PACKAGE_VISIBLE` to enable implicit same-package references (Java/Kotlin), `DEFINITION_DICTS` to extract definition names from resolved files, `IMPORT_QUERIES` to obtain per-language import extraction query strings, and `TREE_SITTER_LANGUAGES` to obtain the tree-sitter `Language` object for parsing.

- **`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to retrieve per-language definition node mappings for extracting definitions from a target file.

- **`codetwine/pipeline.py` → `codetwine/config/settings.py`** : Uses `MAX_WORKERS` as the default parallelism level for file processing, and `ENABLE_LLM_DOC` to conditionally execute the design document generation step.

- **`codetwine/doc_creator.py` → `codetwine/config/settings.py`** : Uses `OUTPUT_LANGUAGE` to append language instructions to LLM prompts, `SUMMARY_MAX_CHARS` to cap summary length, `MAX_WORKERS` as the default worker count for parallel document generation, and `DOC_TEMPLATE_PATH` to load the documentation template JSON file.

- **`codetwine/llm/client.py` → `codetwine/config/settings.py`** : Uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default constructor arguments for the LLM client, `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate limit errors, and `DOC_MAX_TOKENS` as the default token limit for generation calls.

- **`codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`** : Uses `USAGE_NODE_TYPES` to retrieve per-language AST node type settings for usage extraction, `IMPORT_RESOLVE_CONFIG` to determine import path separators when matching imports to target files, `SAME_PACKAGE_VISIBLE` to allow same-directory references without explicit imports (Java/Kotlin), and `DEFINITION_DICTS` to load definition names from target files.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS.keys()` to build the set of supported file extensions for project-wide file collection, `EXCLUDE_PATTERNS` to filter out directories and files during traversal, and `SAME_PACKAGE_VISIBLE` to identify language extensions that support implicit same-package visibility.

- **`codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py`** : Uses `TREE_SITTER_LANGUAGES` as the module-level mapping from file extension to tree-sitter `Language` object for parsing source files.

---

## Dependency Direction

All relationships are **unidirectional**: each dependent module imports from `codetwine/config/settings.py`, and `settings.py` does not import from any of them. `settings.py` acts as a pure configuration provider — it is a leaf node in the project-internal dependency graph with no outgoing edges to other project modules.

## Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `.env` file / shell environment | Key-value string pairs | Loaded via `load_dotenv()` at module import time; provides all runtime configuration overrides |
| `os.environ` | String values | Environment variables read by `get_config_value()` for LLM credentials, path overrides, performance tuning, and analysis options |
| `tree_sitter_*` native modules | C extension objects | Each `language()` / `language_typescript()` / `language_tsx()` call returns a raw language object that is wrapped into a `tree_sitter.Language` instance |
| Hardcoded defaults | Python literals | Default values embedded in `get_config_value()` calls serve as fallbacks when environment variables are absent |

---

## 2. Transformation Overview

### Stage 1 — Environment Resolution

`get_config_value()` is called for every configuration key. It reads a string from the environment (or falls back to a default), then casts the value to the requested type (`str`, `int`, `float`, or `bool`). The result is assigned to a module-level constant (`LLM_API_KEY`, `MAX_WORKERS`, `ENABLE_LLM_DOC`, etc.).

### Stage 2 — Path Materialization

`REPO_ROOT` is derived from `__file__` using `os.path` operations and becomes the base for `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `DOC_TEMPLATE_PATH`. These three paths may be overridden by environment variables resolved in Stage 1.

### Stage 3 — Per-Language Static Definitions

All per-language dictionaries (`PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, `CPP_DEFINITION_DICT`, etc.) and query strings (`_PYTHON_IMPORT_QUERY`, `_JS_IMPORT_QUERY`, etc.) and usage-type sets (`_PYTHON_USAGE_NODE_TYPES`, `_JAVA_USAGE_NODE_TYPES`, etc.) are assembled as pure Python literals with no runtime computation.

### Stage 4 — Language Registry Assembly

Each entry in `_LANG_REGISTRY` is constructed as a frozen `LangConfig` dataclass. During this step, each `tree_sitter_*` module's raw language object is wrapped into a `tree_sitter.Language` instance. The resulting registry maps a canonical extension string (e.g., `"py"`, `"ts"`) to the full bundle of language settings.

### Stage 5 — Public Dictionary Generation

Five public mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`) and one boolean mapping (`SAME_PACKAGE_VISIBLE`) are derived from `_LANG_REGISTRY` via dictionary comprehensions. Each comprehension extracts exactly one field from every `LangConfig`.

### Stage 6 — Alias Expansion

`_expand_ext_aliases()` is applied to each of the six public dictionaries produced in Stage 5. It reads `_EXT_ALIASES` (`h → cpp`, `kts → kt`, `jsx → js`) and copies the referenced canonical entry under the alias key, producing final dictionaries that cover both canonical and alias extensions.

---

## 3. Outputs

All outputs are module-level names exported for consumption by dependent modules. No files are written and no side effects occur beyond the initial `load_dotenv()` call.

| Exported Name | Type | Consumed By |
|---|---|---|
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE` | `str` | `codetwine/llm/client.py` |
| `DOC_MAX_TOKENS`, `MAX_RETRIES`, `RETRY_WAIT` | `int` | `codetwine/llm/client.py` |
| `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS` | `str` / `int` | `codetwine/doc_creator.py` |
| `DOC_TEMPLATE_PATH`, `MAX_WORKERS` | `str` / `int` | `codetwine/doc_creator.py` |
| `ENABLE_LLM_DOC` | `bool` | `main.py`, `codetwine/pipeline.py` |
| `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT` | `str` | `main.py` |
| `EXCLUDE_PATTERNS` | `list[str]` | `codetwine/extractors/dependency_graph.py` |
| `MAX_WORKERS` | `int` | `codetwine/pipeline.py` |
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
|---|---|---|
| `language` | `tree_sitter.Language` | Compiled tree-sitter grammar used by the parser for this language |
| `definition_dict` | `dict[str, str]` | Maps AST node type to the child node type holding the definition name |
| `import_query` | `str \| None` | S-expression tree-sitter query for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type sets controlling usage-tracking behavior |
| `import_resolve` | `dict \| None` | Module resolution strategy parameters (see below) |
| `same_package_visible` | `bool` | Whether same-directory files are implicitly accessible without imports (Java/Kotlin) |

### `import_resolve` dict (within `LangConfig`)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Delimiter used to split module path segments (`"."` or `"/"`) |
| `try_init` | `bool` | When `True`, attempt to resolve a package via its `__init__.py` (Python only) |
| `index_ext_list` | `list[str]` | Extensions to probe as index files when a directory import is detected (JS/TS) |
| `alt_ext_list` | `list[str]` | Alternative file extensions to try during resolution (JS/TS, C/C++) |
| `try_bare_path` | `bool` | When `True`, attempt resolution without any extension (C/C++) |
| `try_current_dir` | `bool` | When `True`, also probe paths relative to the current file's directory (Python, C/C++) |

### Definition dict (e.g., `PYTHON_DEFINITION_DICT`)

| Key | Type | Purpose |
|---|---|---|
| AST node type string (e.g., `"function_definition"`) | `str` | The node type to match during tree traversal |
| Value (e.g., `"identifier"`) | `str` | The child node type from which the definition name is extracted; `"__sentinel__"` values signal a language-specific extraction function |

### Usage node types dict (e.g., `_PYTHON_USAGE_NODE_TYPES`)

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types representing function/method call sites |
| `attribute_types` | `set[str]` | AST node types representing attribute or member access |
| `skip_parent_types` | `set[str]` | Parent node types under which an identifier should not be counted as a usage |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent node types under which a type identifier should not be counted as a usage |
| `skip_name_field_types` | `set[str]` | Parent node types where the `name` field child should be skipped (Python only) |
| `typed_alias_parent_types` | `set[str]` | Parent node types from which typed variable alias mappings are extracted (Java, C/C++, Kotlin) |

### `_EXT_ALIASES` dict

| Key | Type | Purpose |
|---|---|---|
| `"h"` | `str` (`"cpp"`) | Maps `.h` header files to C++ language settings |
| `"kts"` | `str` (`"kt"`) | Maps Kotlin script files to Kotlin language settings |
| `"jsx"` | `str` (`"js"`) | Maps JSX files to JavaScript language settings |

### `EXCLUDE_PATTERNS` list

| Element | Type | Purpose |
|---|---|---|
| Pattern string (e.g., `"__pycache__"`, `".git"`) | `str` | fnmatch-compatible glob pattern; directories and files matching any pattern are skipped during project traversal |

## Error Handling

## 1. Overall Strategy

`settings.py` employs a **fail-fast** strategy for mandatory configuration and **silent default substitution** for optional configuration. Required environment variables raise an exception immediately at module load time, preventing the application from starting in an invalid state. Optional variables fall back to hardcoded defaults without any warning or logging, allowing the application to proceed with safe baseline values.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | A required environment variable (one with no `default` argument, i.e., `default=_REQUIRED`) is absent from the environment and `.env` file | Raised immediately with a descriptive message naming the missing variable | No | Module import fails; entire application cannot start |
| Missing optional variable | An optional environment variable is absent (has an explicit `default` value supplied) | Silently substituted with the specified default value; no exception or log | Yes | Application continues with the default value |
| `None` default passthrough | `default=None` is explicitly supplied and the environment variable is absent | Returns `None` directly without type conversion | Yes | Caller receives `None`; downstream behavior depends on caller |
| Type conversion failure | A non-empty environment variable value cannot be converted by `int()` or `float()` to the requested `var_type` | Propagates the built-in `ValueError` or `TypeError` from the conversion call (no catch) | No | Module import fails at the point of the offending `get_config_value` call |

---

## 3. Design Notes

- **Sentinel object for required detection**: The `_REQUIRED = object()` sentinel distinguishes "no default supplied" from `default=None`, enabling `None` to be a valid explicit default without ambiguity.
- **No logging in the configuration layer**: All error handling in this file either raises or silently substitutes; there is no use of a logger. Error visibility for missing required variables comes solely from the raised `ValueError` message propagating to the runtime.
- **Module-load-time validation**: Because all `get_config_value` calls and the entire `_LANG_REGISTRY` construction execute at import time, any misconfiguration (missing required variable or type-conversion failure) surfaces before any application logic runs, consistent with a fail-fast philosophy.
- **No defensive handling for tree-sitter language instantiation**: `Language(...)` calls within `_LANG_REGISTRY` are not wrapped in error handling; failures in loading tree-sitter grammar bindings propagate directly as unhandled exceptions, also enforcing fail-fast at startup.

## Summary

**`codetwine/config/settings.py`** centralizes all configuration constants, language AST settings, and public mapping dictionaries for the project. Exports: `get_config_value(key:str, default, var_type:type)`, frozen dataclass `LangConfig(language, definition_dict:dict, import_query:str|None, usage_node_types:dict|None, import_resolve:dict|None, same_package_visible:bool)`. Key outputs: `TREE_SITTER_LANGUAGES:dict[str,Language]`, `DEFINITION_DICTS:dict[str,dict]`, `IMPORT_QUERIES:dict[str,str|None]`, `USAGE_NODE_TYPES:dict[str,dict|None]`, `IMPORT_RESOLVE_CONFIG:dict[str,dict]`, `SAME_PACKAGE_VISIBLE:dict[str,bool]`, plus LLM/path/pipeline constants.
