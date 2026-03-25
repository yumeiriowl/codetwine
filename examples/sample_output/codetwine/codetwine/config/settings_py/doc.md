# Design Document: codetwine/config/settings.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Centralizes all application-wide configuration by reading environment variables and defining per-language static mappings (tree-sitter grammars, definition dictionaries, import queries, usage node types, and import resolution settings) that every other module in the codetwine package consumes.

## 2. When to Use This Module

- **Accessing LLM credentials and behavior** — import `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, and `DOC_MAX_TOKENS` to initialize and operate `LLMClient`.
- **Resolving project and output directories** — import `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` to determine where source files are read from and where results are written.
- **Selecting the doc template and language** — import `DOC_TEMPLATE_PATH` to locate the JSON template file, and `OUTPUT_LANGUAGE` and `SUMMARY_MAX_CHARS` to control generated documentation content.
- **Looking up a tree-sitter parser for a file extension** — index `TREE_SITTER_LANGUAGES[ext]` to obtain the `Language` object required to parse a source file.
- **Extracting definitions from an AST** — call `DEFINITION_DICTS.get(ext)` to retrieve the AST-node-type → name-node-type mapping for a given extension.
- **Running import extraction queries** — call `IMPORT_QUERIES.get(ext)` to get the S-expression query string for a given extension.
- **Tracking symbol usages** — call `USAGE_NODE_TYPES.get(ext)` to get call types, attribute types, and skip-parent rules for usage analysis.
- **Resolving import paths to file paths** — call `IMPORT_RESOLVE_CONFIG.get(ext)` to get the separator, extension lists, and path-resolution flags for a given language.
- **Enabling same-package visibility** — call `SAME_PACKAGE_VISIBLE.get(ext)` to determine whether implicit same-directory references apply (Java, Kotlin).
- **Filtering files during directory traversal** — read `EXCLUDE_PATTERNS` to skip directories and files that match glob patterns.
- **Controlling parallelism** — read `MAX_WORKERS` to set the thread/task concurrency limit for pipeline and doc-generation functions.
- **Checking whether LLM documentation is active** — read `ENABLE_LLM_DOC` to decide whether to instantiate an `LLMClient` and run the documentation generation step.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default: any` (optional), `var_type: type` (default `str`) | `str \| int \| float \| bool \| None` | Reads an environment variable and converts it to the requested type; raises `ValueError` if the variable is missing and no default is provided. |
| `LangConfig` | `language: Language`, `definition_dict: dict[str, str]`, `import_query: str \| None`, `usage_node_types: dict \| None`, `import_resolve: dict \| None`, `same_package_visible: bool` | dataclass instance | Immutable bundle of all language-specific settings associated with one file extension. |
| `LLM_API_KEY` | — | `str` | API key for the LLM provider, read from the environment. |
| `LLM_MODEL` | — | `str` | Model identifier forwarded to the LLM client. |
| `LLM_API_BASE` | — | `str` | Base URL for the LLM API endpoint. |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language in which generated documentation is written. |
| `DOC_MAX_TOKENS` | — | `int` | Maximum token budget for a single LLM generation call. |
| `REPO_ROOT` | — | `str` | Absolute, normalized path to the repository root. |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default source directory to analyze when none is specified on the command line. |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default directory where analysis output is written. |
| `DOC_TEMPLATE_PATH` | — | `str` | Path to the JSON file that defines documentation section templates. |
| `MAX_WORKERS` | — | `int` | Maximum number of concurrent workers for parallel processing. |
| `MAX_RETRIES` | — | `int` | Maximum number of retry attempts for LLM calls. |
| `RETRY_WAIT` | — | `int` | Seconds to wait between retry attempts. |
| `ENABLE_LLM_DOC` | — | `bool` | Feature flag controlling whether LLM-based document generation runs. |
| `SUMMARY_MAX_CHARS` | — | `int` | Maximum character length for generated file summaries. |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for directories and files to skip during project traversal. |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension (including aliases) to the corresponding tree-sitter `Language` object. |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension to AST-node-type → name-node-type dictionary for definition extraction. |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension to the tree-sitter S-expression query string for import extraction. |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension to node-type settings (call types, attribute types, skip rules) for usage tracking. |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension to import-path resolution parameters (separator, extension lists, path-resolution flags). |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension to whether same-package implicit references are enabled (Java, Kotlin only). |

## 4. Design Decisions

- **Single `_LANG_REGISTRY` as the source of truth** — all per-language settings are defined once inside `_LANG_REGISTRY` as `LangConfig` instances, and every public mapping dictionary (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, etc.) is derived from it automatically. Adding a new language requires only one registry entry.
- **Extension aliases via `_EXT_ALIASES`** — extensions that share an existing language configuration (`.h` → `cpp`, `.kts` → `kt`, `.jsx` → `js`) are registered separately from the primary registry and expanded into all public dictionaries by `_expand_ext_aliases`, avoiding duplication of `LangConfig` objects.
- **Sentinel values in definition dictionaries** — values prefixed with `__` (e.g., `"__function_declarator__"`, `"__variable_declarator__"`) are sentinel strings signaling that name extraction requires a dedicated code path rather than a direct child lookup, keeping the dictionary-driven dispatch extensible without adding special-case logic here.
- **`_REQUIRED` sentinel for mandatory environment variables** — a private module-level object is used as the default argument sentinel instead of `None`, allowing `None` itself to be a valid explicit default while still detecting truly missing required variables.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

### Sentinel Object: `_REQUIRED`

| Property | Detail |
|---|---|
| Type | `object` |
| Purpose | Acts as a unique sentinel distinguishing "no default provided" from `None` as a default |

**Design decision:** Uses a private module-level object rather than `None` so that `None` can itself be a valid explicit default.

---

### LLM Settings Constants

| Name | Type | Source Env Var | Default |
|---|---|---|---|
| `LLM_API_KEY` | `str` | `LLM_API_KEY` | `""` |
| `LLM_MODEL` | `str` | `LLM_MODEL` | `""` |
| `LLM_API_BASE` | `str` | `LLM_API_BASE` | `""` |
| `OUTPUT_LANGUAGE` | `str` | `OUTPUT_LANGUAGE` | `"English"` |
| `DOC_MAX_TOKENS` | `int` | `DOC_MAX_TOKENS` | `8192` |

---

### Path Settings Constants

| Name | Type | Description |
|---|---|---|
| `REPO_ROOT` | `str` | Absolute normalized path to the repository root, derived from this file's location |
| `DEFAULT_PROJECT_DIR` | `str` | Default project directory; overridable via env var |
| `DEFAULT_OUTPUT_DIR` | `str` | Default output directory; defaults to `<REPO_ROOT>/output` |
| `DOC_TEMPLATE_PATH` | `str` | Path to `doc_template.json`; defaults to `<REPO_ROOT>/doc_template.json` |

---

### Performance Settings Constants

| Name | Type | Source Env Var | Default |
|---|---|---|---|
| `MAX_WORKERS` | `int` | `MAX_WORKERS` | `4` |
| `MAX_RETRIES` | `int` | `MAX_RETRIES` | `3` |
| `RETRY_WAIT` | `int` | `RETRY_WAIT` | `2` |

---

### Analysis Settings Constants

| Name | Type | Description |
|---|---|---|
| `ENABLE_LLM_DOC` | `bool` | Controls whether LLM-based document generation is active |
| `SUMMARY_MAX_CHARS` | `int` | Maximum character count for generated summary text |
| `_EXCLUDE_PATTERNS_ENV` | `str` | Raw comma-separated exclude patterns string from env |
| `EXCLUDE_PATTERNS` | `list[str]` | List of glob patterns for directories/files to skip during traversal |

**Edge case for `EXCLUDE_PATTERNS`:** When `_EXCLUDE_PATTERNS_ENV` is empty or unset, a hardcoded default list (`__pycache__`, `.git`, `.github`, `.venv`, `node_modules`) is used instead.

---

### Per-Language Definition Dictionaries

Each dictionary maps AST node type strings to the child node type string that contains the name of the definition. Used by tree-sitter-based definition extraction.

**Special sentinel values:**
- `"__assignment__"` — name is nested in an assignment expression (Python)
- `"__function_declarator__"` — name is nested inside a function declarator (C/C++)
- `"__init_declarator__"` — name is nested inside an init declarator (C/C++)
- `"__variable_declarator__"` — name is nested inside a variable declarator (JS/TS)

| Constant | Language | Notable Entries |
|---|---|---|
| `PYTHON_DEFINITION_DICT` | Python | `function_definition`, `class_definition`, `decorated_definition`, `expression_statement` |
| `JAVA_DEFINITION_DICT` | Java | `class_declaration`, `method_declaration`, `interface_declaration`, `constructor_declaration`, `enum_declaration` |
| `CPP_DEFINITION_DICT` | C++ | `class_specifier`, `function_definition` (sentinel), `namespace_definition`, `declaration` (sentinel) |
| `C_DEFINITION_DICT` | C | `function_definition` (sentinel), `declaration` (sentinel), `type_definition`, `enum_specifier` |
| `KOTLIN_DEFINITION_DICT` | Kotlin | `class_declaration`, `function_declaration`, `object_declaration` |
| `JS_DEFINITION_DICT` | JavaScript | `function_declaration`, `class_declaration`, `lexical_declaration` (sentinel), `variable_declaration` (sentinel) |
| `TS_DEFINITION_DICT` | TypeScript | Superset of JS; adds `interface_declaration`, `type_alias_declaration`, `enum_declaration` |

---

### Per-Language Import Query Strings

Private string constants containing tree-sitter S-expression queries. Each query uses capture names `@module`, `@name`, and `@import_node`.

| Constant | Language | Patterns Covered |
|---|---|---|
| `_PYTHON_IMPORT_QUERY` | Python | `import X`, `import X as Y`, `from X import Y` |
| `_JS_IMPORT_QUERY` | JavaScript/TypeScript | ES module imports, named imports, re-exports, CommonJS `require`, destructured `require` |
| `_JAVA_IMPORT_QUERY` | Java | `import com.example.Foo` |
| `_C_IMPORT_QUERY` | C/C++ | `#include <...>` and `#include "..."` |
| `_KOTLIN_IMPORT_QUERY` | Kotlin | `import com.example.Foo` |

---

### Per-Language Usage Node Type Dictionaries

Each dictionary configures usage-tracking behavior for one language.

**Keys present in each dict:**

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types representing function/method calls |
| `attribute_types` | `set[str]` | AST node types representing attribute/member access |
| `skip_parent_types` | `set[str]` | Parent node types under which an identifier is not a usage (definitions, parameters, imports) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types under which a type identifier or namespace identifier is not a usage |
| `skip_name_field_types` *(Python only)* | `set[str]` | Parent types where the `name` field is not a usage |
| `typed_alias_parent_types` *(Java, C, Kotlin)* | `set[str]` | Parent node types that introduce a typed variable alias |

| Constant | Language |
|---|---|
| `_PYTHON_USAGE_NODE_TYPES` | Python |
| `_JAVA_USAGE_NODE_TYPES` | Java |
| `_JS_USAGE_NODE_TYPES` | JavaScript/TypeScript |
| `_C_USAGE_NODE_TYPES` | C/C++ |
| `_KOTLIN_USAGE_NODE_TYPES` | Kotlin |

---

### Extension Lists

| Constant | Type | Value |
|---|---|---|
| `_JS_TS_EXT_LIST` | `list[str]` | `[".ts", ".tsx", ".js", ".jsx"]` |
| `_C_CPP_EXT_LIST` | `list[str]` | `[".h", ".c", ".cpp"]` |

Used as shared references within `import_resolve` configurations for related language families.

---

### Extension Alias Map: `_EXT_ALIASES`

| Type | Value |
|---|---|
| `dict[str, str]` | `{"h": "cpp", "kts": "kt", "jsx": "js"}` |

Maps non-canonical extensions to the canonical extension whose settings they inherit. Used exclusively by `_expand_ext_aliases`.

---

### Registry: `_LANG_REGISTRY`

- **Type:** `dict[str, LangConfig]`
- **Keys:** Canonical file extensions without dot: `"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"`
- **Responsibility:** Single authoritative source for all per-language settings. Adding a new language requires only one new entry here.
- **Design decision:** Centralizing settings in one registry and auto-generating all public mapping dictionaries from it eliminates duplication and ensures consistency across all consumers.

---

### Public Mapping Dictionaries

Auto-generated from `_LANG_REGISTRY` via `_expand_ext_aliases`. All include alias extensions (`h`, `kts`, `jsx`).

| Constant | Type | Maps Extension To |
|---|---|---|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | tree-sitter `Language` object |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | Definition node type mapping dictionary |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | Import extraction query string |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Usage-tracking node type settings |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | Import path resolution settings (only extensions with non-`None` config) |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | Whether same-package implicit visibility is enabled (only `True` entries: `java`, `kt`, and aliases) |

---

## Functions

### `get_config_value`

```
get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None
```

- **Responsibility:** Retrieves a configuration value from environment variables and converts it to the requested type. Provides a single, uniform entry point for all configuration reads.
- **When to use:** Called at module load time to populate every configuration constant in this file.
- **Parameters:**

  | Parameter | Type | Description |
  |---|---|---|
  | `key` | `str` | Environment variable name to look up |
  | `default` | any | Fallback value; omit to make the variable required |
  | `var_type` | `type` | Target Python type: `str`, `int`, `float`, or `bool` |

- **Returns:** The converted value, or `None` if `default=None` and the variable is unset.
- **Raises:** `ValueError` when a required variable (no default provided) is absent from the environment.
- **Design decisions:**
  - `bool` conversion treats `"true"`, `"1"`, `"yes"`, `"on"` (case-insensitive) as `True`; all other non-empty strings are `False`.
  - When a default is provided and the env var is absent, the default is stringified before type conversion, ensuring consistent conversion behavior regardless of the default's original type.
- **Constraints:** `var_type` must be one of `str`, `int`, `float`, or `bool`; other types fall through to the `str` path without conversion.

---

### `_expand_ext_aliases`

```
_expand_ext_aliases(base_dict: dict) -> dict
```

- **Responsibility:** Produces a new dictionary that includes alias extension entries copied from their canonical counterparts, so callers do not need to enumerate aliases explicitly.
- **When to use:** Called once per public mapping dictionary to add alias entries (`h`, `kts`, `jsx`) before exposing the dictionary to consumers.
- **Parameters:**

  | Parameter | Type | Description |
  |---|---|---|
  | `base_dict` | `dict` | A dictionary keyed by canonical extensions |

- **Returns:** A shallow copy of `base_dict` with alias extensions added where the canonical key exists and the alias key is not already present.
- **Constraints:** Does not modify `base_dict` in place. Alias keys that already exist in `base_dict` are left unchanged.

---

## Classes

### `LangConfig`

```python
@dataclass(frozen=True)
class LangConfig:
    ...
```

- **Responsibility:** Immutable value object that bundles all language-specific settings needed by the analysis pipeline under a single, named, type-safe structure.
- **When to use:** Instantiated once per supported language within `_LANG_REGISTRY`; never instantiated outside this module.
- **Decorator:** `@dataclass(frozen=True)` — instances are immutable after creation; field assignment raises `FrozenInstanceError`.

**Fields:**

| Field | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object used for parsing source files |
| `definition_dict` | `dict[str, str]` | Maps AST node types to the child node type holding the definition name |
| `import_query` | `str \| None` | tree-sitter S-expression query for import extraction; `None` for unsupported languages |
| `usage_node_types` | `dict \| None` | Node type configuration for usage tracking; `None` for unsupported languages |
| `import_resolve` | `dict \| None` | Module path resolution settings (see keys below); `None` if resolution is unsupported |
| `same_package_visible` | `bool` | `True` for Java/Kotlin where symbols in the same package are visible without explicit import |

**`import_resolve` dictionary keys:**

| Key | Type | Description |
|---|---|---|
| `separator` | `str` | Delimiter used in module names (`"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++) |
| `try_init` | `bool` | When `True`, attempts to resolve a module as a package via `__init__.py` (Python only) |
| `index_ext_list` | `list[str]` | Extensions to try as index files when resolving directory imports (JS/TS only) |
| `alt_ext_list` | `list[str]` | Alternative file extensions to try during resolution (JS/TS and C/C++) |
| `try_bare_path` | `bool` | When `True`, attempts path lookup without any extension (C/C++ only) |
| `try_current_dir` | `bool` | When `True`, also tries relative paths from the current file's directory (Python, C/C++) |

**Design decision:** Using a frozen dataclass rather than a plain dict enforces that language configurations are never mutated after registry initialization and enables attribute-access syntax (`cfg.language`) over key lookup.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/config/settings.py`) does not import any project-internal modules. All imports are from the standard library (`os`, `dataclasses`) or third-party packages (`dotenv`, `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_python`, `tree_sitter_typescript`).

**No project-internal dependencies exist.**

---

## Dependents (modules that import this file)

The following project-internal modules import symbols from this file:

- **`main.py` → `codetwine/config/settings.py`** : Uses `DEFAULT_PROJECT_DIR`, `REPO_ROOT`, `DEFAULT_OUTPUT_DIR`, and `ENABLE_LLM_DOC` to resolve project/output directory paths and to decide whether to instantiate an LLM client.

- **`codetwine/import_to_path.py` → `codetwine/config/settings.py`** : Uses `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, and `TREE_SITTER_LANGUAGES` to perform language-specific module path resolution, same-package visibility checks, definition extraction, and import query execution during import-to-path mapping.

- **`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to retrieve per-language definition extraction settings when analyzing a target source file.

- **`codetwine/pipeline.py` → `codetwine/config/settings.py`** : Uses `MAX_WORKERS` as the default parallelism level for file processing, and `ENABLE_LLM_DOC` to conditionally trigger design document generation.

- **`codetwine/doc_creator.py` → `codetwine/config/settings.py`** : Uses `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `MAX_WORKERS`, and `DOC_TEMPLATE_PATH` to control documentation generation language, summary length limits, worker concurrency, and template file location.

- **`codetwine/llm/client.py` → `codetwine/config/settings.py`** : Uses `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, and `DOC_MAX_TOKENS` as default constructor arguments and runtime parameters controlling LLM API access, retry behavior, and token limits.

- **`codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`** : Uses `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, and `DEFINITION_DICTS` to drive language-specific AST node type selection, import path resolution, same-package reference detection, and target definition loading during usage analysis.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to determine the set of supported file extensions, `EXCLUDE_PATTERNS` to filter directories and files during project traversal, and `SAME_PACKAGE_VISIBLE` to group files by directory for same-package dependency inference.

- **`codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py`** : Uses `TREE_SITTER_LANGUAGES` to obtain the extension-to-Language-object mapping used for tree-sitter parsing.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/config/settings.py` has **no project-internal dependencies** — it is a pure configuration leaf that depends only on the standard library and third-party packages.
- All nine dependent modules listed above import from `settings.py`; `settings.py` does not import from any of them.

The dependency direction is strictly: **dependent modules → `codetwine/config/settings.py`**, making this file a shared configuration root consumed by the rest of the project.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|---|---|---|
| `.env` file / shell environment | Key-value string pairs | Loaded via `load_dotenv()` at module import time; all `LLM_*`, `OUTPUT_*`, `DOC_*`, `MAX_*`, `RETRY_*`, `ENABLE_*`, `SUMMARY_*`, `EXCLUDE_*`, `DEFAULT_*` variables |
| `tree_sitter_*` native modules | C extension objects | Each language module exposes a `language()` (or `language_typescript()` / `language_tsx()`) factory that produces a raw language pointer wrapped by `tree_sitter.Language` |
| Hardcoded literals in module body | Python dicts, lists, strings | Definition dicts, import query strings, and usage node type dicts are defined as module-level constants |

---

## 2. Transformation Overview

```
Stage 1: Environment ingestion
  os.getenv() → get_config_value() → typed scalar values
      (str, int, float, bool with default/required semantics)

Stage 2: Path materialisation
  REPO_ROOT derived from __file__
  → DEFAULT_PROJECT_DIR, DEFAULT_OUTPUT_DIR, DOC_TEMPLATE_PATH
  resolved relative to REPO_ROOT if not overridden by env

Stage 3: Pattern list normalisation
  EXCLUDE_PATTERNS env string (comma-separated)
  → split + strip → list[str]
  (fallback: hardcoded default list)

Stage 4: Language object construction
  tree_sitter_* native factory calls
  → Language(...) wrappers
  → stored in per-language LangConfig instances inside _LANG_REGISTRY

Stage 5: Registry assembly
  Per-language constant dicts + Language objects
  → frozen LangConfig dataclass per canonical extension key
  → _LANG_REGISTRY: dict[str, LangConfig]

Stage 6: Alias expansion
  _LANG_REGISTRY + _EXT_ALIASES
  → _expand_ext_aliases() copies canonical entries under alias keys
  → applied independently to each public mapping

Stage 7: Public mapping generation
  _LANG_REGISTRY (expanded)
  → TREE_SITTER_LANGUAGES   (ext → Language)
  → DEFINITION_DICTS        (ext → definition dict)
  → IMPORT_QUERIES          (ext → query string | None)
  → USAGE_NODE_TYPES        (ext → usage dict | None)
  → IMPORT_RESOLVE_CONFIG   (ext → resolve dict)  [only non-None entries]
  → SAME_PACKAGE_VISIBLE    (ext → bool)           [only True entries]
```

---

## 3. Outputs

All outputs are module-level names that consumer modules import directly. No files are written and no network calls are made by this module.

| Name | Type | Consumed by |
|---|---|---|
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
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | `codetwine/parsers/ts_parser.py`, `codetwine/import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict]` | `codetwine/file_analyzer.py`, `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | `codetwine/import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | `codetwine/extractors/usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py` |

---

## 4. Key Data Structures

### `LangConfig` (frozen dataclass)

| Field | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object used to parse and query source files of this extension |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type (or sentinel string) for extracting definition names |
| `import_query` | `str \| None` | S-expression tree-sitter query string for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type sets controlling which nodes are treated as calls, attributes, or skipped during usage tracking |
| `import_resolve` | `dict \| None` | Module path resolution parameters (keys documented below) |
| `same_package_visible` | `bool` | Whether definitions in the same directory/package are referenceable without explicit imports |

---

### `import_resolve` dict (nested inside `LangConfig`)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Delimiter used in module names (`"."` for Python/Java/Kotlin, `"/"` for C/C++/JS/TS) |
| `try_init` | `bool` | When `True`, attempts to resolve a module as a package by looking for `__init__.py` (Python only) |
| `try_current_dir` | `bool` | When `True`, also attempts resolution relative to the current file's directory (Python, C/C++) |
| `index_ext_list` | `list[str]` | Extensions to try as directory index files (JS/TS: `.ts`, `.tsx`, `.js`, `.jsx`) |
| `alt_ext_list` | `list[str]` | Alternative extensions to try when the resolved path has no extension (C/C++/JS/TS) |
| `try_bare_path` | `bool` | When `True`, attempts the path without any extension appended (C/C++) |

---

### `usage_node_types` dict (e.g. `_PYTHON_USAGE_NODE_TYPES`)

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types representing function/method call expressions |
| `attribute_types` | `set[str]` | AST node types representing attribute or member access |
| `skip_parent_types` | `set[str]` | Parent node types under which an `identifier` node should not be recorded as a usage |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent node types under which a type identifier or namespace identifier should not be recorded as a usage |
| `skip_name_field_types` *(Python only)* | `set[str]` | Parent node types whose `name` field child should be skipped |
| `typed_alias_parent_types` *(Java, C, Kotlin only)* | `set[str]` | Parent node types used to build a variable-name → type-name alias map |

---

### Definition dicts (e.g. `PYTHON_DEFINITION_DICT`)

| Key | Type | Purpose |
|---|---|---|
| AST node type string (e.g. `"function_definition"`) | `str` | Maps to either a direct child node type name (e.g. `"identifier"`) or a sentinel string (e.g. `"__assignment__"`, `"__function_declarator__"`, `"__init_declarator__"`, `"__variable_declarator__"`) triggering dedicated extraction logic in the definition extractor |

---

### `_LANG_REGISTRY`

| Key | Type | Purpose |
|---|---|---|
| Canonical file extension string (e.g. `"py"`, `"java"`, `"ts"`) | `LangConfig` | Central registry mapping each supported extension to its complete language configuration bundle |

---

### `_EXT_ALIASES`

| Key | Type | Purpose |
|---|---|---|
| Alias extension string (e.g. `"h"`, `"kts"`, `"jsx"`) | `str` (canonical extension) | Declares that an alias extension reuses the `LangConfig` of its canonical counterpart; used by `_expand_ext_aliases()` to populate the public mappings |

## Error Handling

# Error Handling

## 1. Overall Strategy

The settings module applies a **fail-fast** strategy for required configuration values and **graceful degradation with defaults** for optional ones. Errors are surfaced at startup (module import time), ensuring that misconfiguration is caught immediately before any downstream processing begins. Once configuration is loaded successfully, the module itself performs no further runtime error handling — all values are treated as validated constants by dependents.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | A required environment variable (marked with `_REQUIRED` sentinel) is not set in the environment or `.env` file | Raises `ValueError` with a descriptive message indicating the missing key and how to resolve it | No | Process terminates at import time |
| Missing optional env var | An optional environment variable is not set but has a declared default | Silently substitutes the default value; no exception raised | Yes (default used) | None — processing continues with the default |
| `None` default explicitly passed | `default=None` is passed to `get_config_value` and the env var is absent | Returns `None` directly without conversion | Yes | Caller receives `None`; caller is responsible for handling |
| Type conversion failure | A non-`str` `var_type` (e.g., `int`, `float`, `bool`) is specified and the raw string value cannot be converted | Native Python conversion exceptions (e.g., `ValueError` from `int()`) propagate uncaught | No | Process terminates at import time |
| Unsupported extension lookup | A dependent calls `.get()` on `TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, or `IMPORT_RESOLVE_CONFIG` with an unregistered extension | Returns `None` or raises `KeyError` (for direct index access on `TREE_SITTER_LANGUAGES`); dependents are responsible for checking the result | Yes (handled by callers) | The dependent skips processing for that file/extension |

---

## 3. Design Notes

- **Sentinel object for required values**: The module uses a private `_REQUIRED = object()` sentinel rather than a special string or `None` to distinguish "no default provided" from an explicit `None` default. This prevents ambiguity when `None` is a valid and intentional default.
- **Fail-fast at import time**: By evaluating all `get_config_value()` calls at module load time (not lazily), the module ensures that any missing required configuration or type conversion failure is reported immediately when the application starts, rather than surfacing as a runtime error deep in processing.
- **Type conversion errors are intentionally uncaught**: No try-except wraps the `int()`, `float()`, or boolean coercion logic. A malformed value (e.g., `"abc"` for an `int` variable) is considered a configuration defect that should halt startup, consistent with the fail-fast strategy.
- **Boolean coercion is explicit and permissive**: Boolean conversion accepts `"true"`, `"1"`, `"yes"`, and `"on"` (case-insensitive) as truthy, avoiding the pitfall of Python's `bool("false") == True` behavior on raw strings.
- **Delegation to callers for lookup failures**: The public mapping dictionaries (`TREE_SITTER_LANGUAGES`, etc.) do not embed any error handling. The responsibility for handling missing keys is delegated entirely to dependent modules, which consistently use `.get()` and guard against `None` returns before proceeding.

## Summary

**`codetwine/config/settings.py`** centralizes all application-wide configuration by reading environment variables and defining per-language static mappings consumed by every other module.

**Functions:** `get_config_value(key:str, default, var_type:type)→scalar`; `_expand_ext_aliases(base_dict:dict)→dict`

**Classes:** `LangConfig(frozen dataclass)` — fields: `language:Language`, `definition_dict:dict[str,str]`, `import_query:str|None`, `usage_node_types:dict|None`, `import_resolve:dict|None`, `same_package_visible:bool`

**Key outputs:** `TREE_SITTER_LANGUAGES:dict[str,Language]`, `DEFINITION_DICTS:dict[str,dict]`, `IMPORT_QUERIES:dict[str,str|None]`, `USAGE_NODE_TYPES:dict[str,dict|None]`, `IMPORT_RESOLVE_CONFIG:dict[str,dict]`, `SAME_PACKAGE_VISIBLE:dict[str,bool]`, `_LANG_REGISTRY:dict[str,LangConfig]`
