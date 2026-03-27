# Design Document: codetwine/config/settings.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Centralizes all application-wide configuration values—LLM credentials, file paths, performance tuning, analysis options, and per-language tree-sitter settings—so that every other module in the codebase has a single authoritative source from which to import constants and language registries.

---

## 2. When to Use This Module

- **Configuring the LLM client** (`LLMClient` in `codetwine/llm/client.py`): import `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, `MAX_RETRIES`, `RETRY_WAIT`, and `DOC_MAX_TOKENS` to supply default constructor arguments and retry logic.
- **Resolving default directories** (`main.py`): import `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` to determine where to read source files and write output when the user provides no CLI arguments.
- **Parsing source files by extension** (`codetwine/parsers/ts_parser.py`): import `TREE_SITTER_LANGUAGES` to obtain the correct tree-sitter `Language` object for a given file extension.
- **Extracting definitions from an AST** (`codetwine/file_analyzer.py`, `codetwine/extractors/usage_analysis.py`): import `DEFINITION_DICTS` and call `.get(file_ext)` to retrieve the AST-node-type-to-name-node-type mapping for the file's language.
- **Extracting import statements** (`codetwine/import_to_path.py`): import `IMPORT_QUERIES` and `TREE_SITTER_LANGUAGES` to obtain the query string and `Language` object needed to run a tree-sitter query against a file.
- **Resolving import paths to file paths** (`codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`): import `IMPORT_RESOLVE_CONFIG` and call `.get(ext)` to obtain separator, extension lists, and resolution strategy for the language.
- **Handling same-package visibility** (`codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py`): import `SAME_PACKAGE_VISIBLE` and call `.get(file_ext)` to determine whether Java/Kotlin files can reference sibling-package definitions without an explicit import.
- **Tracking symbol usages** (`codetwine/extractors/usage_analysis.py`): import `USAGE_NODE_TYPES` and call `.get(file_ext)` to obtain call types, attribute types, and skip-parent sets for the language.
- **Traversing a project directory** (`codetwine/extractors/dependency_graph.py`): import `DEFINITION_DICTS` (for its `.keys()` as the supported-extension set) and `EXCLUDE_PATTERNS` to filter directories and files during `os.walk`.
- **Generating documentation** (`codetwine/doc_creator.py`): import `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, `DOC_TEMPLATE_PATH`, and `MAX_WORKERS` to control prompt language, summary length, template location, and parallelism.
- **Controlling pipeline execution** (`codetwine/pipeline.py`, `main.py`): import `ENABLE_LLM_DOC` to decide whether the LLM documentation generation step runs, and `MAX_WORKERS` to set the default worker count.

---

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default: any` (optional), `var_type: type` (default `str`) | `str \| int \| float \| bool \| None` | Reads an environment variable, applies a type conversion, and raises `ValueError` if a required variable is absent |
| `LangConfig` | `language: Language`, `definition_dict: dict[str, str]`, `import_query: str \| None`, `usage_node_types: dict \| None`, `import_resolve: dict \| None`, `same_package_visible: bool` | — | Frozen dataclass that bundles every language-specific setting into a single immutable record |
| `LLM_API_KEY` | — | `str` | LLM provider API key, read from `LLM_API_KEY` env var |
| `LLM_MODEL` | — | `str` | LLM model identifier, read from `LLM_MODEL` env var |
| `LLM_API_BASE` | — | `str` | LLM API base URL, read from `LLM_API_BASE` env var |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language for generated documentation output |
| `DOC_MAX_TOKENS` | — | `int` | Maximum token count for a single LLM documentation request |
| `REPO_ROOT` | — | `str` | Absolute path to the repository root, derived from this file's location |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default source project directory (env override or `REPO_ROOT`) |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default output directory (env override or `REPO_ROOT/output`) |
| `DOC_TEMPLATE_PATH` | — | `str` | Path to the JSON documentation template file |
| `MAX_WORKERS` | — | `int` | Number of parallel workers for async processing |
| `MAX_RETRIES` | — | `int` | Maximum retry count for LLM API calls |
| `RETRY_WAIT` | — | `int` | Seconds to wait between retries on rate-limit errors |
| `ENABLE_LLM_DOC` | — | `bool` | Whether LLM-based documentation generation is enabled |
| `SUMMARY_MAX_CHARS` | — | `int` | Maximum character count for per-file summary text |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for directories and files to skip during project traversal |
| `PYTHON_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for Python |
| `JAVA_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for Java |
| `CPP_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for C++ |
| `C_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for C |
| `KOTLIN_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for Kotlin |
| `JS_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for JavaScript |
| `TS_DEFINITION_DICT` | — | `dict[str, str]` | AST node type → name node type mapping for TypeScript/TSX |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Extension → tree-sitter `Language` object (includes aliases) |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Extension → definition node mapping dictionary (includes aliases) |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Extension → import extraction query string (includes aliases) |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Extension → usage tracking node type settings (includes aliases) |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Extension → import path resolution settings (includes aliases) |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Extension → whether same-package implicit visibility applies (Java, Kotlin) |

---

## 4. Design Decisions

- **`_LANG_REGISTRY` as the single source of truth**: All per-language settings are declared once inside `_LANG_REGISTRY` as `LangConfig` entries. The five public dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`) are derived from it by comprehension, eliminating the possibility of inconsistency between mappings across languages.

- **Extension aliasing via `_EXT_ALIASES`**: Rather than duplicating `LangConfig` entries for extensions that share a grammar (e.g., `.h` → `cpp`, `.jsx` → `js`, `.kts` → `kt`), `_expand_ext_aliases()` post-processes every public dictionary to inject alias keys automatically. Adding a new alias requires only one entry in `_EXT_ALIASES`.

- **Sentinel value `__sentinel__` in definition dictionaries**: When an AST node's name is nested more than one level deep, the definition dictionary stores a sentinel string (e.g., `"__function_declarator__"`, `"__variable_declarator__"`, `"__assignment__"`, `"__init_declarator__"`) rather than a direct child node type. The extraction logic in `definitions.py` dispatches to a dedicated function when it encounters a sentinel, keeping the registry data-driven while still handling non-uniform AST shapes.

- **`_REQUIRED` sentinel for mandatory environment variables**: `get_config_value` uses a private module-level object as a default sentinel to distinguish "no default provided" from `None`, allowing callers to declare optional variables with an explicit `None` default while still raising `ValueError` for truly required variables.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Configuration Variables

These constants are resolved at import time from environment variables (via `get_config_value`) or computed from other constants. They are consumed by dependent modules as read-only values.

### LLM Settings

| Name | Type | Default | Purpose |
|---|---|---|---|
| `LLM_API_KEY` | `str` | `""` | API key passed to the LLM client |
| `LLM_MODEL` | `str` | `""` | Model identifier string passed to litellm |
| `LLM_API_BASE` | `str` | `""` | Base URL of the LLM API endpoint |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Language in which generated documentation is written |
| `DOC_MAX_TOKENS` | `int` | `8192` | Maximum token budget for a single LLM generation call |

### Path Settings

| Name | Type | Description |
|---|---|---|
| `REPO_ROOT` | `str` | Absolute, normalized path to the repository root (two levels up from this file) |
| `DEFAULT_PROJECT_DIR` | `str` | Default source project directory to analyze |
| `DEFAULT_OUTPUT_DIR` | `str` | Default directory for analysis output files |
| `DOC_TEMPLATE_PATH` | `str` | Path to the JSON file defining documentation section templates |

### Performance Settings

| Name | Type | Default | Purpose |
|---|---|---|---|
| `MAX_WORKERS` | `int` | `4` | Thread/process pool size for parallel file processing |
| `MAX_RETRIES` | `int` | `3` | Maximum retry attempts on LLM rate-limit errors |
| `RETRY_WAIT` | `int` | `2` | Seconds to wait between retries |

### Analysis Settings

| Name | Type | Default | Purpose |
|---|---|---|---|
| `ENABLE_LLM_DOC` | `bool` | `True` | Gates whether LLM-based documentation generation runs at all |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Character limit for per-file summary text |
| `EXCLUDE_PATTERNS` | `list[str]` | See below | Glob patterns for directories/files to skip during traversal |

**`EXCLUDE_PATTERNS` default list:** `__pycache__`, `.git`, `.github`, `.venv`, `node_modules`

If the `EXCLUDE_PATTERNS` environment variable is set, it is parsed as a comma-separated list; otherwise the default list above is used.

---

## Definition Dictionaries

Each `*_DEFINITION_DICT` constant maps an **AST node type** (string) to either a **child node type** (string) or a **sentinel value** (string prefixed with `__`).

- Standard value: names the direct child node type that holds the symbol name.
- Sentinel value: signals that the name is nested more than one level deep; the extractor dispatches to a dedicated function.

| Constant | Language | Sentinel values used |
|---|---|---|
| `PYTHON_DEFINITION_DICT` | Python | `__assignment__` |
| `JAVA_DEFINITION_DICT` | Java | None |
| `CPP_DEFINITION_DICT` | C++ | `__function_declarator__`, `__init_declarator__` |
| `C_DEFINITION_DICT` | C | `__function_declarator__`, `__init_declarator__` |
| `KOTLIN_DEFINITION_DICT` | Kotlin | None |
| `JS_DEFINITION_DICT` | JavaScript | `__variable_declarator__` |
| `TS_DEFINITION_DICT` | TypeScript | `__variable_declarator__` |

---

## Import Query Strings

Each `_*_IMPORT_QUERY` constant is a tree-sitter S-expression query string. All queries use these standardized capture names:

| Capture name | Meaning |
|---|---|
| `@module` | The imported module path or source string |
| `@name` | An individual imported name (e.g., the `Y` in `from X import Y`) |
| `@import_node` | The entire import statement node (used for line number retrieval) |

| Constant | Language | Notable patterns covered |
|---|---|---|
| `_PYTHON_IMPORT_QUERY` | Python | `import X`, `import X as Y`, `from X import Y` |
| `_JS_IMPORT_QUERY` | JavaScript/TypeScript | ES module imports, re-exports, CommonJS `require()`, destructured `require()` |
| `_JAVA_IMPORT_QUERY` | Java | `import com.example.Foo` |
| `_C_IMPORT_QUERY` | C/C++ | `#include <...>` and `#include "..."` |
| `_KOTLIN_IMPORT_QUERY` | Kotlin | `import com.example.Foo` |

---

## Usage Node Type Dictionaries

Each `_*_USAGE_NODE_TYPES` constant configures how the usage extractor identifies symbol references in a parsed AST. All dictionaries share a common schema:

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types that represent function/method calls |
| `attribute_types` | `set[str]` | AST node types that represent attribute/member access |
| `skip_parent_types` | `set[str]` | Parent node types whose child identifiers must NOT be counted as usages (definitions, imports, parameters, etc.) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types that suppress counting of type-identifier or namespace-identifier nodes specifically |
| `typed_alias_parent_types` | `set[str]` | *(Java, C, Kotlin only)* Parent types that carry type annotations, used to build variable-name → type-name alias maps |
| `skip_name_field_types` | `set[str]` | *(Python only)* Node types whose `name` field should not be treated as a usage |

| Constant | Language |
|---|---|
| `_PYTHON_USAGE_NODE_TYPES` | Python |
| `_JAVA_USAGE_NODE_TYPES` | Java |
| `_JS_USAGE_NODE_TYPES` | JavaScript/TypeScript |
| `_C_USAGE_NODE_TYPES` | C/C++ |
| `_KOTLIN_USAGE_NODE_TYPES` | Kotlin |

---

## Extension List Constants

| Name | Type | Value |
|---|---|---|
| `_JS_TS_EXT_LIST` | `list[str]` | `[".ts", ".tsx", ".js", ".jsx"]` |
| `_C_CPP_EXT_LIST` | `list[str]` | `[".h", ".c", ".cpp"]` |

Used as shared values for `index_ext_list` and `alt_ext_list` fields in `import_resolve` configurations.

---

## `LangConfig` (dataclass)

```
@dataclass(frozen=True)
class LangConfig:
    language: Language
    definition_dict: dict[str, str]
    import_query: str | None = None
    usage_node_types: dict | None = None
    import_resolve: dict | None = None
    same_package_visible: bool = False
```

**Responsibility:** Bundles all per-language configuration needed for parsing, definition extraction, import extraction, usage tracking, and module resolution into a single immutable unit.

**When to use:** Instantiated once per language entry in `_LANG_REGISTRY`; never instantiated by callers directly.

**Decorator:** `frozen=True` makes all instances immutable and hashable after construction.

### Fields

| Field | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object used to create parsers and queries |
| `definition_dict` | `dict[str, str]` | Maps AST node types to name-child node types for definition extraction |
| `import_query` | `str \| None` | tree-sitter S-expression query string for import extraction; `None` means no import analysis |
| `usage_node_types` | `dict \| None` | Node-type configuration for usage tracking; `None` disables usage analysis |
| `import_resolve` | `dict \| None` | Module path resolution settings (keys described below); `None` disables path resolution |
| `same_package_visible` | `bool` | When `True`, definitions in the same directory are treated as implicitly visible without explicit imports (Java/Kotlin behavior) |

### `import_resolve` dictionary keys

| Key | Type | Applicable languages | Meaning |
|---|---|---|---|
| `separator` | `str` | All | Delimiter used to convert a module name to a file path (`"."` or `"/"`) |
| `try_init` | `bool` | Python | Whether to resolve a package import by looking for `__init__.py` |
| `index_ext_list` | `list[str]` | JS/TS | Extensions to try when resolving a directory import (e.g., `index.ts`) |
| `alt_ext_list` | `list[str]` | JS/TS, C/C++ | Alternative extensions to try when the original extension does not resolve |
| `try_bare_path` | `bool` | C/C++ | Whether to attempt resolution of the path without any extension |
| `try_current_dir` | `bool` | Python, C/C++ | Whether relative resolution from the importing file's directory is attempted |

---

## `_LANG_REGISTRY`

**Type:** `dict[str, LangConfig]`

**Responsibility:** The single source of truth mapping canonical file extensions to their complete `LangConfig`. All public mapping dictionaries are derived from this registry.

**Keys (canonical extensions):** `py`, `java`, `cpp`, `c`, `kt`, `js`, `ts`, `tsx`

**Design decision:** Centralizing all per-language settings here means adding a new language requires only one new entry; all public dictionaries update automatically.

---

## `_EXT_ALIASES`

**Type:** `dict[str, str]`

**Responsibility:** Maps non-canonical extensions to their canonical counterpart so alias extensions can share the same `LangConfig` without duplicating entries in `_LANG_REGISTRY`.

| Alias | Canonical |
|---|---|
| `h` | `cpp` |
| `kts` | `kt` |
| `jsx` | `js` |

---

## Functions

### `get_config_value`

**Signature:**
```python
def get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None
```

**Responsibility:** Retrieves a single environment variable and converts it to the specified Python type, providing a uniform interface for all configuration reads in this module.

**When to use:** Called at module import time for every configuration constant; not intended for use by external callers.

**Parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `key` | `str` | Environment variable name |
| `default` | any | Fallback value when the variable is absent; omitting it marks the variable as required |
| `var_type` | `type` | Target Python type (`str`, `int`, `float`, or `bool`) |

**Return type:** The converted value, or `None` if `default=None` and the variable is unset.

**Design decisions:**
- The sentinel `_REQUIRED = object()` is a private module-level object, making it impossible to accidentally pass a value that triggers the required-variable error path.
- Boolean conversion accepts `"true"`, `"1"`, `"yes"`, and `"on"` as truthy; all other strings are falsy.
- When the variable is absent and a non-`None` default is provided, the default is coerced through `str()` before type conversion, ensuring consistent conversion logic for all input sources.

**Constraints & edge cases:**
- Raises `ValueError` if the variable is absent and no default is supplied.
- `var_type=bool` does not raise on unrecognized strings; they evaluate to `False`.
- `var_type=int` or `var_type=float` will raise `ValueError` if the environment string is not a valid number.

---

### `_expand_ext_aliases`

**Signature:**
```python
def _expand_ext_aliases(base_dict: dict) -> dict
```

- `base_dict`: A dictionary keyed by canonical extension strings (e.g., `"cpp"`, `"kt"`).
- Returns a **new** `dict` that includes all original entries plus entries for alias extensions whose canonical counterpart is present in `base_dict`.

**Responsibility:** Automatically populates alias extension keys in any per-language mapping dictionary so that callers using `.h`, `.kts`, or `.jsx` extensions receive the same configuration as their canonical counterparts.

**When to use:** Called once per public mapping dictionary during module initialization; not called by external code.

**Design decisions:**
- Returns a new dictionary rather than mutating the input, keeping each public mapping independent.
- Alias keys are only added if the canonical key already exists in `base_dict`, so partial registries (e.g., `IMPORT_RESOLVE_CONFIG`, which omits languages without `import_resolve`) do not gain spurious alias entries.

**Constraints & edge cases:**
- If an alias key already exists in `base_dict`, it is not overwritten.
- Aliases not represented in `base_dict`'s canonical set are silently skipped.

---

## Public Mapping Dictionaries

All five dictionaries are auto-generated by applying `_expand_ext_aliases` to a comprehension over `_LANG_REGISTRY`. Alias extensions (`h`, `kts`, `jsx`) are included in each.

| Name | Type | Keys | Values | Consumers |
|---|---|---|---|---|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | File extensions | tree-sitter `Language` objects | `ts_parser.py`, `import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | File extensions | Definition node-type maps | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | File extensions | Import query strings or `None` | `import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | File extensions | Usage node-type config dicts or `None` | `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | File extensions | Import resolution config dicts | `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | File extensions (Java/Kotlin only) | `True` for same-package implicit visibility | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |

**Constraint:** `IMPORT_RESOLVE_CONFIG` and `SAME_PACKAGE_VISIBLE` are generated with filtering (`if cfg.import_resolve is not None` / `if cfg.same_package_visible`), so extensions without those features are absent from these dictionaries rather than mapped to `None`/`False`.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file (`codetwine/config/settings.py`) does not import any project-internal modules. All of its imports are from the standard library (`os`, `dataclasses`) or third-party packages (`dotenv`, `tree_sitter`, `tree_sitter_c`, `tree_sitter_cpp`, `tree_sitter_java`, `tree_sitter_javascript`, `tree_sitter_kotlin`, `tree_sitter_python`, `tree_sitter_typescript`). There are no intra-project dependencies.

---

## Dependents (modules that import this file)

The following project-internal modules depend on `codetwine/config/settings.py`:

- **`main.py` → `codetwine/config/settings.py`** : Uses `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, and `ENABLE_LLM_DOC` to resolve project/output directory paths and to decide whether to instantiate an LLM client.

- **`codetwine/import_to_path.py` → `codetwine/config/settings.py`** : Uses `IMPORT_RESOLVE_CONFIG.get`, `SAME_PACKAGE_VISIBLE.get`, `DEFINITION_DICTS.get`, `IMPORT_QUERIES.get`, and `TREE_SITTER_LANGUAGES` to drive module-path resolution, same-package visibility checks, definition extraction, import query lookup, and tree-sitter language selection.

- **`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS.get` to retrieve per-language definition node mapping settings for a target file.

- **`codetwine/pipeline.py` → `codetwine/config/settings.py`** : Uses `MAX_WORKERS` as the default parallelism level and `ENABLE_LLM_DOC` to conditionally trigger design document generation.

- **`codetwine/doc_creator.py` → `codetwine/config/settings.py`** : Uses `OUTPUT_LANGUAGE` for language-specific prompt formatting, `SUMMARY_MAX_CHARS` to cap summary length, `MAX_WORKERS` as the default worker count, and `DOC_TEMPLATE_PATH` to locate the documentation template file.

- **`codetwine/llm/client.py` → `codetwine/config/settings.py`** : Uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default constructor arguments, `MAX_RETRIES` and `RETRY_WAIT` to control retry logic on rate-limit errors, and `DOC_MAX_TOKENS` as the default token limit for generation calls.

- **`codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`** : Uses `USAGE_NODE_TYPES.get` for per-language AST node type settings, `IMPORT_RESOLVE_CONFIG.get` for module separator configuration, `SAME_PACKAGE_VISIBLE.get` for same-package reference handling, and `DEFINITION_DICTS.get` to load target file definitions.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS.keys` to build the set of supported extensions, `EXCLUDE_PATTERNS` to filter directories and files during project traversal, and `SAME_PACKAGE_VISIBLE.get` to group files by directory for same-package dependency analysis.

- **`codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py`** : Uses `TREE_SITTER_LANGUAGES` as the module-level extension-to-Language mapping for the tree-sitter parser.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/config/settings.py` has **no dependencies on any other project-internal module**; it is a pure configuration provider.
- Every dependent module listed above imports from `settings.py` but `settings.py` does not import from any of them.

`settings.py` acts as a **leaf dependency node** — a single source of truth for configuration that flows outward to all other modules in the project.

## Data Flow

# Data Flow

## 1. Inputs

| Input Source | Format | Description |
|---|---|---|
| Environment variables / `.env` file | Shell environment / key-value text file | Loaded via `python-dotenv` (`load_dotenv()`); provides LLM credentials, path overrides, and tuning parameters |
| `tree_sitter_*` language packages | Native compiled binaries | Each language grammar package exposes a `language()` function that returns a capsule object wrapped into a `tree_sitter.Language` |
| Module-level literal constants | Python dicts / strings / lists | Per-language definition dictionaries, import query strings, and usage node type dicts are defined inline as source-code constants |

### Environment Variables Consumed

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `LLM_API_KEY` | `str` | `""` | API key for the LLM provider |
| `LLM_MODEL` | `str` | `""` | Model identifier string |
| `LLM_API_BASE` | `str` | `""` | Endpoint URL for the LLM API |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Language used in generated documentation |
| `DOC_MAX_TOKENS` | `int` | `8192` | Token budget per LLM call |
| `DEFAULT_PROJECT_DIR` | `str` | `REPO_ROOT` | Root directory of the project to analyze |
| `DEFAULT_OUTPUT_DIR` | `str` | `<REPO_ROOT>/output` | Directory for generated output files |
| `DOC_TEMPLATE_PATH` | `str` | `<REPO_ROOT>/doc_template.json` | Path to the JSON documentation template |
| `MAX_WORKERS` | `int` | `4` | Parallelism level for async pipelines |
| `MAX_RETRIES` | `int` | `3` | LLM call retry limit |
| `RETRY_WAIT` | `int` | `2` | Seconds to wait between LLM retries |
| `ENABLE_LLM_DOC` | `bool` | `True` | Whether to invoke the LLM for documentation |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Character limit for file summaries |
| `EXCLUDE_PATTERNS` | `str` | `""` | Comma-separated glob patterns to skip during traversal |

---

## 2. Transformation Overview

```
Stage 1: Environment Ingestion
  load_dotenv()
  └─ get_config_value(key, default, var_type)
       ├─ os.getenv(key) → raw string or None
       ├─ fallback to default if missing
       └─ cast to bool / int / float / str
       → scalar config constants (LLM_API_KEY, MAX_WORKERS, EXCLUDE_PATTERNS, ...)

Stage 2: Path Resolution
  os.path.dirname(__file__) + normpath
       → REPO_ROOT (absolute path string)
  REPO_ROOT fed into get_config_value defaults
       → DEFAULT_PROJECT_DIR, DEFAULT_OUTPUT_DIR, DOC_TEMPLATE_PATH

Stage 3: EXCLUDE_PATTERNS Derivation
  _EXCLUDE_PATTERNS_ENV (raw comma-separated string)
       ├─ non-empty → split + strip → list[str]
       └─ empty     → hardcoded default list
       → EXCLUDE_PATTERNS : list[str]

Stage 4: Language Object Construction
  tree_sitter_<lang>.language() capsule
       └─ Language(capsule) → tree_sitter.Language object
  Paired with inline definition dicts, query strings, usage dicts
       → one LangConfig dataclass per canonical extension

Stage 5: Registry Assembly
  LangConfig instances keyed by canonical extension
       → _LANG_REGISTRY : dict[str, LangConfig]

Stage 6: Public Dictionary Generation
  _LANG_REGISTRY
       └─ dict comprehension per attribute
            └─ _expand_ext_aliases()   (adds h, kts, jsx)
       → TREE_SITTER_LANGUAGES
          DEFINITION_DICTS
          IMPORT_QUERIES
          USAGE_NODE_TYPES
          IMPORT_RESOLVE_CONFIG
          SAME_PACKAGE_VISIBLE
```

---

## 3. Outputs

All outputs are module-level names exported at import time. No file writes or side effects occur beyond `load_dotenv()`.

| Exported Name | Type | Consumers |
|---|---|---|
| `LLM_API_KEY` | `str` | `codetwine/llm/client.py` |
| `LLM_MODEL` | `str` | `codetwine/llm/client.py` |
| `LLM_API_BASE` | `str` | `codetwine/llm/client.py` |
| `DOC_MAX_TOKENS` | `int` | `codetwine/llm/client.py` |
| `MAX_RETRIES` | `int` | `codetwine/llm/client.py` |
| `RETRY_WAIT` | `int` | `codetwine/llm/client.py` |
| `OUTPUT_LANGUAGE` | `str` | `codetwine/doc_creator.py` |
| `SUMMARY_MAX_CHARS` | `int` | `codetwine/doc_creator.py` |
| `DOC_TEMPLATE_PATH` | `str` | `codetwine/doc_creator.py` |
| `MAX_WORKERS` | `int` | `codetwine/pipeline.py`, `codetwine/doc_creator.py` |
| `ENABLE_LLM_DOC` | `bool` | `main.py`, `codetwine/pipeline.py` |
| `REPO_ROOT` | `str` | `main.py` |
| `DEFAULT_PROJECT_DIR` | `str` | `main.py` |
| `DEFAULT_OUTPUT_DIR` | `str` | `main.py` |
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

The central per-language configuration bundle stored in `_LANG_REGISTRY`.

| Field | Type | Purpose |
|---|---|---|
| `language` | `tree_sitter.Language` | Compiled grammar object passed to the tree-sitter `Parser` |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type that holds the definition name; sentinel values (`__...__`) signal special extraction logic |
| `import_query` | `str \| None` | S-expression tree-sitter query string for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type settings controlling usage tracking (see below) |
| `import_resolve` | `dict \| None` | Module resolution parameters (see below) |
| `same_package_visible` | `bool` | Whether definitions in the same package directory are implicitly visible without an import (Java / Kotlin) |

---

### `definition_dict` values (per-language dicts)

Each dict maps an AST node type string to either a plain child node type or a sentinel string.

| Key (AST node type) | Value (child node type or sentinel) | Sentinel meaning |
|---|---|---|
| e.g. `"function_definition"` | `"identifier"` | Direct child lookup |
| e.g. `"function_definition"` (C/C++) | `"__function_declarator__"` | Name is nested; dispatch to dedicated extractor |
| e.g. `"declaration"` | `"__init_declarator__"` | Name is nested inside an `init_declarator` node |
| e.g. `"expression_statement"` (Python) | `"__assignment__"` | Name is the left-hand side of an assignment |
| e.g. `"lexical_declaration"` (JS/TS) | `"__variable_declarator__"` | Name is inside a `variable_declarator` node |

---

### `usage_node_types` dict

Each language's usage configuration dict shares the following key schema.

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types that represent a function/method call |
| `attribute_types` | `set[str]` | AST node types that represent attribute / member access |
| `skip_parent_types` | `set[str]` | Parent node types whose identifier children are not counted as usages (definitions, imports, parameters) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types that suppress type-identifier / namespace-identifier usage recording |
| `skip_name_field_types` | `set[str]` *(optional)* | Parent types where the `name` field child is not a usage (Python-only) |
| `typed_alias_parent_types` | `set[str]` *(optional)* | Parent types where a variable is declared with a type annotation, enabling alias tracking (Java, C/C++, Kotlin) |

---

### `import_resolve` dict

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Delimiter used in module paths (`"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++) |
| `try_init` | `bool` *(optional)* | Try resolving a package as `<path>/__init__.py` (Python) |
| `index_ext_list` | `list[str]` *(optional)* | Extensions to try as index files (e.g., `index.ts`) for directory imports (JS/TS) |
| `alt_ext_list` | `list[str]` *(optional)* | Alternative extensions to try when the exact extension does not match (JS/TS, C/C++) |
| `try_bare_path` | `bool` *(optional)* | Try the path without adding any extension (C/C++) |
| `try_current_dir` | `bool` *(optional)* | Also try resolving relative to the source file's directory (Python, C/C++) |

---

### `_LANG_REGISTRY`

| Key | Type | Purpose |
|---|---|---|
| `"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"` | `LangConfig` | One complete language configuration per canonical file extension |

---

### Public flat dictionaries (all share the same key schema)

All five public dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) and the alias-expanded variants use the following key space:

| Key | Source |
|---|---|
| Canonical extensions: `py`, `java`, `cpp`, `c`, `kt`, `js`, `ts`, `tsx` | `_LANG_REGISTRY` |
| Alias extensions: `h` → `cpp`, `kts` → `kt`, `jsx` → `js` | `_EXT_ALIASES` via `_expand_ext_aliases()` |

## Error Handling

# Error Handling

## 1. Overall Strategy

`settings.py` adopts a **fail-fast** strategy for required configuration and a **silent default substitution** strategy for optional configuration. The file executes at module import time, meaning any unrecoverable error surfaces immediately when the application starts—before any business logic runs. Required environment variables that are absent cause the process to terminate with a descriptive `ValueError`. Optional variables silently fall back to hardcoded defaults, allowing the application to proceed without any operator intervention.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | A required environment variable (marked with `_REQUIRED` sentinel) is not set in the environment or `.env` file | Raises `ValueError` with a descriptive message identifying the missing key | No | Process terminates at import time |
| Missing optional environment variable | An optional environment variable is not set, but a default value is provided in the `get_config_value` call | Returns the default value after converting it to the target type | Yes | No impact; default is used transparently |
| Type conversion failure (`int`, `float`) | An environment variable is set but its value cannot be converted to the declared `var_type` (e.g., a non-numeric string passed for an `int` setting) | Propagates the built-in `ValueError` or `TypeError` from `int()`/`float()` | No | Process terminates at import time |
| `None` default with missing variable | `default=None` is explicitly passed and the environment variable is absent | Returns `None` directly without type conversion | Yes | Downstream code receives `None` and must handle it |
| Unsupported `var_type` for `bool` | Environment variable value does not match any of `"true"`, `"1"`, `"yes"`, `"on"` (case-insensitive) | Evaluates to `False` silently | Yes | Configuration silently treated as disabled |

---

## 3. Design Notes

- **Sentinel object pattern**: The `_REQUIRED` sentinel (a plain `object()`) is used instead of a special string or `None` to unambiguously distinguish between "no default provided" and "default is explicitly `None`". This prevents accidental silent failures when `None` is a meaningful intended default.
- **Import-time validation**: All `get_config_value` calls execute at module load, not lazily. This ensures configuration errors are caught at startup rather than at runtime when a downstream function is first invoked, which aligns with the fail-fast principle.
- **Bool coercion is one-directional**: The boolean parsing logic treats any value not in the truthy set as `False` without raising an error. This means invalid boolean strings (e.g., `"yes_please"`) degrade silently to `False` rather than terminating the process—a deliberate trade-off favoring availability over strictness for toggle-style settings.
- **No retry or logging within `settings.py`**: Error handling in this file is intentionally minimal. Retry and logging policies are the responsibility of the consumers of these settings (e.g., `client.py` implements `MAX_RETRIES`/`RETRY_WAIT`-based retry logic for LLM calls). The settings layer itself does not obscure configuration problems.

## Summary

**settings.py** centralizes all application-wide configuration as a single authoritative source. Exports `get_config_value(key:str, default, var_type:type)` for env-var retrieval and frozen dataclass `LangConfig(language, definition_dict, import_query, usage_node_types, import_resolve, same_package_visible)` bundling per-language settings. Produces `TREE_SITTER_LANGUAGES:dict[str,Language]`, `DEFINITION_DICTS:dict[str,dict]`, `IMPORT_QUERIES:dict[str,str|None]`, `USAGE_NODE_TYPES:dict[str,dict]`, `IMPORT_RESOLVE_CONFIG:dict[str,dict]`, `SAME_PACKAGE_VISIBLE:dict[str,bool]` via `_LANG_REGISTRY` and alias expansion, plus scalar constants for LLM, path, and performance settings.
