# Design Document: codetwine/config/settings.py

# Overview & Purpose

## 1. Module Summary

Centralizes all configuration values, language-specific parser settings, and per-language analysis dictionaries for the CodeTwine project, exposing them as named constants that other modules import directly.

## 2. When to Use This Module

- **Reading LLM credentials and model settings**: Import `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `DOC_MAX_TOKENS`, `MAX_RETRIES`, and `RETRY_WAIT` to initialize and operate `LLMClient` in `codetwine/llm/client.py`.
- **Resolving project and output directories**: Import `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` to determine file system paths in `main.py`.
- **Controlling document generation behavior**: Import `ENABLE_LLM_DOC`, `OUTPUT_LANGUAGE`, `SUMMARY_MAX_CHARS`, and `DOC_TEMPLATE_PATH` to configure prompt construction and output in `codetwine/doc_creator.py`.
- **Looking up tree-sitter language objects**: Access `TREE_SITTER_LANGUAGES[ext]` to obtain the `Language` instance needed to parse a source file in `codetwine/parsers/ts_parser.py`.
- **Extracting definitions from an AST**: Access `DEFINITION_DICTS.get(ext)` to retrieve the AST-node-type-to-name-node mapping for a given file extension in `codetwine/file_analyzer.py`, `codetwine/extractors/usage_analysis.py`, and `codetwine/extractors/dependency_graph.py`.
- **Extracting import statements**: Access `IMPORT_QUERIES.get(ext)` for the tree-sitter S-expression query string used in `codetwine/import_to_path.py`.
- **Resolving import paths to file paths**: Access `IMPORT_RESOLVE_CONFIG.get(ext)` for the separator, extension list, and resolution strategy for a language in `codetwine/import_to_path.py` and `codetwine/extractors/usage_analysis.py`.
- **Tracking symbol usages in ASTs**: Access `USAGE_NODE_TYPES.get(ext)` for the call, attribute, and skip-node-type sets used in `codetwine/extractors/usage_analysis.py`.
- **Enabling same-package visibility (Java/Kotlin)**: Check `SAME_PACKAGE_VISIBLE.get(ext)` to determine whether definitions in the same directory are implicitly visible without an import statement, used in `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, and `codetwine/extractors/dependency_graph.py`.
- **Normalizing source root prefixes**: Use `SOURCE_ROOT_PATTERNS` to strip Maven/Gradle/src-layout prefixes when resolving import paths in `codetwine/import_to_path.py`.
- **Filtering project directory traversal**: Use `EXCLUDE_PATTERNS` to skip directories and files (e.g., `.git`, `node_modules`) during file collection in `codetwine/extractors/dependency_graph.py`.
- **Controlling parallel execution**: Import `MAX_WORKERS` to set the default worker count in `codetwine/pipeline.py` and `codetwine/doc_creator.py`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `get_config_value` | `key: str`, `default: any` (optional), `var_type: type` (default `str`) | `str \| int \| float \| bool \| None` | Reads an environment variable, applies a type conversion, and returns the result; raises `ValueError` if the variable is absent and no default is provided. |
| `LangConfig` | `language: Language`, `definition_dict: dict[str, str]`, `import_query: str \| None`, `usage_node_types: dict \| None`, `import_resolve: dict \| None`, `same_package_visible: bool` | — | Immutable dataclass bundling all parser and analysis settings for one language extension. |
| `LLM_API_KEY` | — | `str` | API key for the LLM provider, read from `LLM_API_KEY` env var. |
| `LLM_MODEL` | — | `str` | Model identifier for the LLM provider, read from `LLM_MODEL` env var. |
| `LLM_API_BASE` | — | `str` | Base URL for the LLM API endpoint, read from `LLM_API_BASE` env var. |
| `OUTPUT_LANGUAGE` | — | `str` | Natural language for generated documentation output (default: `"English"`). |
| `DOC_MAX_TOKENS` | — | `int` | Maximum token count per LLM generation call (default: `8192`). |
| `REPO_ROOT` | — | `str` | Absolute normalized path to the repository root directory. |
| `DEFAULT_PROJECT_DIR` | — | `str` | Default source project directory to analyze. |
| `DEFAULT_OUTPUT_DIR` | — | `str` | Default directory for analysis output files. |
| `DOC_TEMPLATE_PATH` | — | `str` | Path to the JSON document template file. |
| `MAX_WORKERS` | — | `int` | Default number of parallel workers (default: `4`). |
| `MAX_RETRIES` | — | `int` | Maximum retry attempts for LLM requests (default: `3`). |
| `RETRY_WAIT` | — | `int` | Seconds to wait between retries on rate-limit errors (default: `2`). |
| `ENABLE_LLM_DOC` | — | `bool` | Whether LLM-based document generation is enabled (default: `True`). |
| `SUMMARY_MAX_CHARS` | — | `int` | Maximum character count for file summary text (default: `600`). |
| `EXCLUDE_PATTERNS` | — | `list[str]` | Glob patterns for directories and files to skip during project traversal. |
| `TREE_SITTER_LANGUAGES` | — | `dict[str, Language]` | Maps file extension to its tree-sitter `Language` object; includes alias extensions. |
| `DEFINITION_DICTS` | — | `dict[str, dict[str, str]]` | Maps file extension to the AST-node-type → name-node-type dictionary for definition extraction; includes alias extensions. |
| `IMPORT_QUERIES` | — | `dict[str, str \| None]` | Maps file extension to the tree-sitter S-expression query string for import extraction; includes alias extensions. |
| `USAGE_NODE_TYPES` | — | `dict[str, dict \| None]` | Maps file extension to the AST node type configuration for usage tracking; includes alias extensions. |
| `IMPORT_RESOLVE_CONFIG` | — | `dict[str, dict]` | Maps file extension to the import path resolution settings dictionary; includes alias extensions. |
| `SAME_PACKAGE_VISIBLE` | — | `dict[str, bool]` | Maps file extension to `True` for languages where same-directory definitions are implicitly visible (Java, Kotlin); includes alias extensions. |
| `SOURCE_ROOT_PATTERNS` | — | `list[str]` | Ordered list of source root path prefixes used to normalize import resolution for Maven/Gradle/src layouts. |

## 4. Design Decisions

- **Sentinel-based required values**: A private `_REQUIRED` sentinel object distinguishes "no default provided" from `None`, allowing `get_config_value` to accept `None` as a legitimate explicit default while still raising `ValueError` for truly missing required variables.
- **Registry-driven public dictionaries**: All per-language settings are defined once in `_LANG_REGISTRY` keyed by canonical extension (e.g., `"ts"`, `"cpp"`). The five public mapping dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, etc.) are auto-generated from the registry via dictionary comprehensions, eliminating duplication and ensuring consistency across all language maps.
- **Alias extension expansion**: Rather than duplicating entries for related extensions (`.h` → `cpp`, `.kts` → `kt`, `.jsx` → `js`), `_expand_ext_aliases` applies `_EXT_ALIASES` uniformly to every generated public dictionary, so alias extensions transparently share canonical language settings.
- **Sentinel values in definition dictionaries**: Values such as `"__assignment__"`, `"__function_declarator__"`, and `"__variable_declarator__"` in the definition dictionaries act as sentinels signaling that name extraction requires a dedicated code path in the extractor, rather than a simple direct child lookup.

# Definition Design Specifications

---

## Module-Level Constants and Configuration Values

### Sentinel Object

| Name | Type | Purpose |
|------|------|---------|
| `_REQUIRED` | `object` | Unique sentinel used to distinguish "no default provided" from `None` as a default. Compared by identity (`is`), not equality. |

---

### Environment-Derived Configuration Variables

All values are loaded at module import time via `get_config_value`.

#### LLM Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `LLM_API_KEY` | `str` | `""` | API key for the LLM provider |
| `LLM_MODEL` | `str` | `""` | Model identifier passed to the LLM client |
| `LLM_API_BASE` | `str` | `""` | Base URL for the LLM API endpoint |
| `OUTPUT_LANGUAGE` | `str` | `"English"` | Natural language for generated documentation |
| `DOC_MAX_TOKENS` | `int` | `8192` | Token budget for a single LLM generation call |

#### Path Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `REPO_ROOT` | `str` | Two directories above this file | Absolute, normalized path to the repository root |
| `DEFAULT_PROJECT_DIR` | `str` | `REPO_ROOT` | Project directory used when none is specified on the CLI |
| `DEFAULT_OUTPUT_DIR` | `str` | `<REPO_ROOT>/output` | Output directory used when none is specified on the CLI |
| `DOC_TEMPLATE_PATH` | `str` | `<REPO_ROOT>/doc_template.json` | Path to the JSON template that defines documentation sections |

#### Performance Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `MAX_WORKERS` | `int` | `4` | Thread-pool concurrency limit for parallel file processing and document generation |
| `MAX_RETRIES` | `int` | `3` | Maximum number of LLM retry attempts on rate-limit errors |
| `RETRY_WAIT` | `int` | `2` | Seconds to wait between retry attempts |

#### Analysis Settings

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `ENABLE_LLM_DOC` | `bool` | `True` | Whether LLM-based document generation is executed |
| `SUMMARY_MAX_CHARS` | `int` | `600` | Character budget for per-file summary text |
| `_EXCLUDE_PATTERNS_ENV` | `str` | `""` | Raw comma-separated exclude patterns from the environment |
| `EXCLUDE_PATTERNS` | `list[str]` | See below | Glob patterns for files/directories to skip during traversal |

**`EXCLUDE_PATTERNS` default list** (used when `_EXCLUDE_PATTERNS_ENV` is empty):
- `__pycache__`, `.git`, `.github`, `.venv`, `node_modules`

---

### Language-Specific Definition Dictionaries

Each dictionary maps **AST node type → child node type** that holds the definition name. The special sentinel value `"__assignment__"`, `"__function_declarator__"`, `"__init_declarator__"`, or `"__variable_declarator__"` signals that name extraction requires language-specific nested logic in `definitions.py`.

| Constant | Language | Covered constructs |
|----------|----------|--------------------|
| `PYTHON_DEFINITION_DICT` | Python | functions, classes, decorated definitions, expression-level assignments |
| `JAVA_DEFINITION_DICT` | Java | classes, methods, interfaces, constructors, enums |
| `CPP_DEFINITION_DICT` | C++ | classes, structs, functions, namespaces, declarations, aliases, enums, macros |
| `C_DEFINITION_DICT` | C | functions, structs, declarations, macros, typedefs, enums |
| `KOTLIN_DEFINITION_DICT` | Kotlin | classes, functions, object declarations |
| `JS_DEFINITION_DICT` | JavaScript | functions, methods, classes, lexical and var declarations |
| `TS_DEFINITION_DICT` | TypeScript | functions, methods, classes, interfaces, declarations, type aliases, enums |

---

### Import Query Strings

Each `_*_IMPORT_QUERY` constant is a tree-sitter S-expression query string. Capture names follow a fixed convention:

| Capture | Meaning |
|---------|---------|
| `@module` | The imported module or path |
| `@name` | An individually imported symbol (e.g., `Y` in `from X import Y`) |
| `@import_node` | The entire import statement node (used for line number retrieval) |

| Constant | Language | Patterns covered |
|----------|----------|-----------------|
| `_PYTHON_IMPORT_QUERY` | Python | `import X`, `import X as Y`, `from X import Y, Z` |
| `_JS_IMPORT_QUERY` | JavaScript/TypeScript | ES module imports, named imports, export re-exports, CommonJS `require`, destructured `require` |
| `_JAVA_IMPORT_QUERY` | Java | `import com.example.Foo` |
| `_C_IMPORT_QUERY` | C/C++ | `#include <...>` and `#include "..."` |
| `_KOTLIN_IMPORT_QUERY` | Kotlin | `import com.example.Foo` |

---

### Usage Node Type Dictionaries

Each `_*_USAGE_NODE_TYPES` constant is a `dict` with the following keys:

| Key | Type | Purpose |
|-----|------|---------|
| `call_types` | `set[str]` | AST node types representing function/method calls |
| `attribute_types` | `set[str]` | AST node types representing attribute/member access |
| `skip_parent_types` | `set[str]` | Parent node types that disqualify an identifier from being counted as a usage (covers definition sites, import statements, parameter lists, etc.) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types that disqualify a *type* identifier from being a usage; typically narrower than `skip_parent_types` |
| `skip_name_field_types` | `set[str]` | *(Python only)* Parent types whose `name` field child should not be treated as a usage |
| `typed_alias_parent_types` | `set[str]` | *(Java, C, Kotlin)* Parent types used to identify typed variable declarations for alias tracking |

| Constant | Language |
|----------|----------|
| `_PYTHON_USAGE_NODE_TYPES` | Python |
| `_JAVA_USAGE_NODE_TYPES` | Java |
| `_JS_USAGE_NODE_TYPES` | JavaScript/TypeScript |
| `_C_USAGE_NODE_TYPES` | C/C++ |
| `_KOTLIN_USAGE_NODE_TYPES` | Kotlin |

---

### Extension List Constants

| Name | Value | Purpose |
|------|-------|---------|
| `_JS_TS_EXT_LIST` | `[".ts", ".tsx", ".js", ".jsx"]` | Shared list of JS/TS extensions used for index file and alternative extension resolution |
| `_C_CPP_EXT_LIST` | `[".h", ".c", ".cpp"]` | Shared list of C/C++ extensions used for alternative extension resolution |

---

## Functions

---

### `get_config_value`

**Signature:**
```python
def get_config_value(key: str, default=_REQUIRED, var_type: type = str) -> str | int | float | bool | None
```

- `key`: Name of the environment variable to read.
- `default`: Fallback value when the variable is absent. Omitting this argument makes the variable required. Passing `None` explicitly returns `None` when absent.
- `var_type`: Target Python type for the returned value (`str`, `int`, `float`, or `bool`).
- Returns the environment variable's value converted to `var_type`.

**Responsibility:** Centralizes all environment variable retrieval and type coercion, enforcing the required-vs-optional distinction through the `_REQUIRED` sentinel.

**When to use:** Called at module load time for every configuration variable derived from environment variables or `.env` file entries.

**Design decisions:**
- The `_REQUIRED` sentinel (compared by identity) cleanly separates "caller did not pass a default" from "caller explicitly wants `None` as default," which `None` itself cannot express.
- Boolean conversion accepts `"true"`, `"1"`, `"yes"`, and `"on"` (case-insensitive) as truthy, mirroring common shell conventions.
- When a default is supplied, it is stringified before type conversion so the same conversion path is always exercised.

**Constraints & edge cases:**
- `var_type` must be one of `str`, `int`, `float`, or `bool`; other types fall through to the `str` return path silently.
- Raises `ValueError` only when `default` is omitted and the variable is unset.

---

### `_expand_ext_aliases`

**Signature:**
```python
def _expand_ext_aliases(base_dict: dict) -> dict
```

- `base_dict`: A settings dictionary keyed by canonical extension strings (without leading dot).
- Returns a new `dict` containing all entries from `base_dict` plus entries for any alias extension defined in `_EXT_ALIASES` whose canonical extension is present in `base_dict`.

**Responsibility:** Avoids duplicate configuration entries for extensions that share identical settings (e.g., `.h` sharing C++ settings, `.jsx` sharing JS settings).

**When to use:** Called once at module load time to produce each of the five public mapping dictionaries.

**Design decisions:**
- Aliases are only added when the canonical extension is already present in `base_dict`, preventing silent failures if a canonical entry is missing.
- Alias entries are never overwritten if already present in `base_dict`, giving explicit entries precedence.
- Returns a new dictionary rather than mutating `base_dict`.

**Constraints & edge cases:**
- Aliases are defined statically in `_EXT_ALIASES`; the function has no knowledge of which alias maps to which canonical entry beyond that mapping.
- Extensions in `base_dict` without a matching canonical in `_EXT_ALIASES` are copied unchanged.

---

## Data Class

---

### `LangConfig`

**Decorator:** `@dataclass(frozen=True)` — instances are immutable after construction; fields cannot be reassigned.

**Responsibility:** Bundles every language-specific setting needed for parsing, definition extraction, import extraction, usage tracking, and import path resolution into a single immutable value object.

**When to use:** Instantiated once per canonical extension inside `_LANG_REGISTRY` at module load time. Consumers read fields directly; they do not instantiate `LangConfig` themselves.

#### Fields

| Field | Type | Required | Purpose |
|-------|------|----------|---------|
| `language` | `Language` | Yes | tree-sitter `Language` object used to parse source files |
| `definition_dict` | `dict[str, str]` | Yes | AST node type → name child type mapping for definition extraction |
| `import_query` | `str \| None` | No (default `None`) | tree-sitter S-expression query for import extraction |
| `usage_node_types` | `dict \| None` | No (default `None`) | AST node type configuration for usage tracking |
| `import_resolve` | `dict \| None` | No (default `None`) | Module path resolution configuration; see keys below |
| `same_package_visible` | `bool` | No (default `False`) | When `True`, definitions in the same directory are reachable without imports (Java/Kotlin) |

#### `import_resolve` Dictionary Keys

| Key | Type | Languages | Meaning |
|-----|------|-----------|---------|
| `separator` | `str` | All | Delimiter used to split module names into path segments (`"."` or `"/"`) |
| `try_init` | `bool` | Python | Whether to look for `__init__.py` when resolving package imports |
| `index_ext_list` | `list[str]` | JS/TS | Extensions to try as index files within a directory |
| `alt_ext_list` | `list[str]` | JS/TS, C/C++ | Alternative extensions to try when the import has no extension |
| `try_bare_path` | `bool` | C/C++ | Whether to attempt resolution without appending any extension |
| `try_current_dir` | `bool` | Python, C/C++ | Whether to also attempt relative resolution from the current file's directory |

**Constraints & edge cases:**
- `frozen=True` means the object cannot be modified after creation; any attempt to set a field raises `FrozenInstanceError`.
- No validation is performed on field values; callers must supply a valid `Language` object and a non-empty `definition_dict`.

---

## Module-Level Registry and Public Mapping Dictionaries

### `_LANG_REGISTRY`

**Type:** `dict[str, LangConfig]`

Maps canonical extension strings (without dot) to their `LangConfig` instances. Contains entries for: `py`, `java`, `cpp`, `c`, `kt`, `js`, `ts`, `tsx`.

**Responsibility:** Single authoritative source for all per-language configuration. Adding support for a new language requires only a new entry here.

---

### `_EXT_ALIASES`

**Type:** `dict[str, str]`

| Alias | Canonical |
|-------|-----------|
| `h` | `cpp` |
| `kts` | `kt` |
| `jsx` | `js` |

**Responsibility:** Declares extensions that reuse an existing language's full configuration without requiring duplicate registry entries.

---

### Public Mapping Dictionaries

All five are generated by applying `_expand_ext_aliases` to a dictionary comprehension over `_LANG_REGISTRY`. They cover all canonical extensions plus the three aliases.

| Name | Type | Maps to | Primary consumers |
|------|------|---------|-------------------|
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | Extension → tree-sitter `Language` object | `ts_parser.py`, `import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | Extension → definition node mapping | `file_analyzer.py`, `usage_analysis.py`, `dependency_graph.py`, `import_to_path.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | Extension → import query string | `import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | Extension → usage node type configuration | `usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | Extension → import resolution configuration | `import_to_path.py`, `usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | Extension → same-package visibility flag | `import_to_path.py`, `usage_analysis.py`, `dependency_graph.py` |

**Design decision:** `IMPORT_RESOLVE_CONFIG` and `SAME_PACKAGE_VISIBLE` are generated with a filter (`if cfg.import_resolve is not None` and `if cfg.same_package_visible`, respectively), so languages without these features are absent from the maps rather than present with null/false values. Callers use `.get()` to safely handle missing keys.

---

### `SOURCE_ROOT_PATTERNS`

**Type:** `list[str]`

| Value | Purpose |
|-------|---------|
| `src/main/java/` | Maven/Gradle Java source root |
| `src/test/java/` | Maven/Gradle Java test root |
| `src/main/kotlin/` | Maven/Gradle Kotlin source root |
| `src/test/kotlin/` | Maven/Gradle Kotlin test root |
| `src/main/scala/` | Maven/Gradle Scala source root |
| `src/test/scala/` | Maven/Gradle Scala test root |
| `src/` | Python src-layout and generic source root |

**Responsibility:** Provides the known directory prefixes that are stripped when converting import paths to relative file paths, enabling correct resolution in Maven/Gradle and src-layout projects.

**Constraints:** Patterns are matched as prefix strings against project-relative file paths; they are not glob patterns.

# Dependency Description

## Dependencies (modules this file imports)

`codetwine/config/settings.py` has **no project-internal module dependencies**. It serves as the configuration root of the project and does not import from any other internal modules. All its imports are from the standard library (`os`, `dataclasses`) and third-party packages (`python-dotenv`, `tree-sitter`, and the various `tree-sitter-*` language bindings).

---

## Dependents (modules that import this file)

The following project-internal modules depend on `codetwine/config/settings.py`:

- **`main.py` → `codetwine/config/settings.py`** : Uses `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, and `ENABLE_LLM_DOC` to resolve project/output directories from CLI arguments and decide whether to instantiate the LLM client.

- **`codetwine/import_to_path.py` → `codetwine/config/settings.py`** : Uses `SOURCE_ROOT_PATTERNS` to detect source root directories, `IMPORT_RESOLVE_CONFIG` to obtain per-language module resolution settings (separator, extension lists, etc.), `SAME_PACKAGE_VISIBLE` to enable implicit same-package references for Java/Kotlin, `DEFINITION_DICTS` to extract definition names from same-package files, `IMPORT_QUERIES` to retrieve import extraction query strings, and `TREE_SITTER_LANGUAGES` to obtain the tree-sitter `Language` object for parsing.

- **`codetwine/file_analyzer.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS` to obtain the per-language definition node mapping for extracting definitions from a target source file.

- **`codetwine/pipeline.py` → `codetwine/config/settings.py`** : Uses `MAX_WORKERS` as the default concurrency limit for the pipeline and `ENABLE_LLM_DOC` to conditionally execute the document generation step.

- **`codetwine/doc_creator.py` → `codetwine/config/settings.py`** : Uses `OUTPUT_LANGUAGE` to append a language instruction to LLM prompts, `SUMMARY_MAX_CHARS` as the character limit for summary generation, `MAX_WORKERS` as the default parallel worker count, and `DOC_TEMPLATE_PATH` to load the documentation template JSON file.

- **`codetwine/llm/client.py` → `codetwine/config/settings.py`** : Uses `LLM_MODEL`, `LLM_API_KEY`, and `LLM_API_BASE` as default constructor arguments for the LLM client, `MAX_RETRIES` and `RETRY_WAIT` to control retry behavior on rate limit errors, and `DOC_MAX_TOKENS` as the default token limit for generation requests.

- **`codetwine/extractors/usage_analysis.py` → `codetwine/config/settings.py`** : Uses `USAGE_NODE_TYPES` to retrieve per-language AST node type settings for usage tracking, `IMPORT_RESOLVE_CONFIG` to obtain separator and resolution settings for matching import statements, `SAME_PACKAGE_VISIBLE` to enable same-directory implicit references (Java/Kotlin), and `DEFINITION_DICTS` to load definition names from target files.

- **`codetwine/extractors/dependency_graph.py` → `codetwine/config/settings.py`** : Uses `DEFINITION_DICTS.keys()` to determine the set of supported file extensions, `EXCLUDE_PATTERNS` to filter out directories and files during project traversal, and `SAME_PACKAGE_VISIBLE` to group files by directory for same-package dependency analysis.

- **`codetwine/parsers/ts_parser.py` → `codetwine/config/settings.py`** : Uses `TREE_SITTER_LANGUAGES` as the module-level extension-to-`Language`-object mapping for the tree-sitter parser.

---

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/config/settings.py` → *(no internal dependencies)*
- All dependent modules → `codetwine/config/settings.py`

`codetwine/config/settings.py` is a **pure configuration leaf** in the dependency graph. It imports no other internal module, and all other modules reference it for settings and language registry data. There are no bidirectional relationships.

# Data Flow

## 1. Inputs

| Input Source | Format | Description |
|---|---|---|
| Environment variables / `.env` file | Strings (via `os.getenv`) | LLM credentials, paths, performance tuning, and analysis flags |
| `tree_sitter_*` language packages | Binary grammar objects | Native grammar binaries wrapped into `Language` objects |
| Module-level constants | Python literals | Hardcoded AST node type mappings, query strings, and extension lists |

The `.env` file is loaded once at module import time via `load_dotenv()`. All environment variable reads flow through `get_config_value()`, which applies type coercion and default values.

---

## 2. Transformation Overview

```
Stage 1: Environment Resolution
  .env file + shell environment
        │
        ▼
  get_config_value() ──► typed Python values
  (str / int / float / bool, with defaults and required-key enforcement)
        │
        ▼
  Flat configuration constants
  (LLM_API_KEY, MAX_WORKERS, EXCLUDE_PATTERNS, etc.)

Stage 2: Language Grammar Initialization
  tree_sitter_* packages (binary grammars)
        │
        ▼
  Language(tspython.language()), Language(tsjava.language()), ...
        │
        ▼
  tree-sitter Language objects (one per canonical language)

Stage 3: Per-Language Configuration Assembly
  Language objects + definition dicts + query strings
  + usage node type dicts + import resolve configs
        │
        ▼
  LangConfig dataclass instances (one per canonical extension)
        │
        ▼
  _LANG_REGISTRY: dict[str, LangConfig]
  (canonical extension → LangConfig)

Stage 4: Alias Expansion and Public Dictionary Generation
  _LANG_REGISTRY + _EXT_ALIASES
        │
        ▼
  _expand_ext_aliases() applied to each projected slice
        │
        ├──► TREE_SITTER_LANGUAGES   (ext → Language)
        ├──► DEFINITION_DICTS        (ext → definition dict)
        ├──► IMPORT_QUERIES          (ext → query string | None)
        ├──► USAGE_NODE_TYPES        (ext → usage config dict | None)
        ├──► IMPORT_RESOLVE_CONFIG   (ext → resolve config dict)
        └──► SAME_PACKAGE_VISIBLE    (ext → bool)
```

`_expand_ext_aliases()` takes each projected dictionary (keyed by canonical extensions such as `"cpp"`, `"kt"`, `"js"`) and adds alias entries (`"h"` → same value as `"cpp"`, `"kts"` → `"kt"`, `"jsx"` → `"js"`) without mutating the originals.

---

## 3. Outputs

All outputs are module-level names exported for consumption by dependent modules. No files are written and no network calls are made.

| Exported Name | Type | Consumed By |
|---|---|---|
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE` | `str` | `codetwine/llm/client.py` |
| `DOC_MAX_TOKENS`, `MAX_RETRIES`, `RETRY_WAIT` | `int` | `codetwine/llm/client.py` |
| `MAX_WORKERS` | `int` | `codetwine/pipeline.py`, `codetwine/doc_creator.py` |
| `ENABLE_LLM_DOC` | `bool` | `main.py`, `codetwine/pipeline.py` |
| `OUTPUT_LANGUAGE` | `str` | `codetwine/doc_creator.py` |
| `SUMMARY_MAX_CHARS` | `int` | `codetwine/doc_creator.py` |
| `DOC_TEMPLATE_PATH` | `str` | `codetwine/doc_creator.py` |
| `EXCLUDE_PATTERNS` | `list[str]` | `codetwine/extractors/dependency_graph.py` |
| `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT` | `str` | `main.py` |
| `TREE_SITTER_LANGUAGES` | `dict[str, Language]` | `codetwine/parsers/ts_parser.py`, `codetwine/import_to_path.py` |
| `DEFINITION_DICTS` | `dict[str, dict[str, str]]` | `codetwine/file_analyzer.py`, `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py` |
| `IMPORT_QUERIES` | `dict[str, str \| None]` | `codetwine/import_to_path.py` |
| `USAGE_NODE_TYPES` | `dict[str, dict \| None]` | `codetwine/extractors/usage_analysis.py` |
| `IMPORT_RESOLVE_CONFIG` | `dict[str, dict]` | `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py` |
| `SAME_PACKAGE_VISIBLE` | `dict[str, bool]` | `codetwine/import_to_path.py`, `codetwine/extractors/usage_analysis.py`, `codetwine/extractors/dependency_graph.py` |
| `SOURCE_ROOT_PATTERNS` | `list[str]` | `codetwine/import_to_path.py` |

---

## 4. Key Data Structures

### `LangConfig` (frozen dataclass)

The central bundle holding all settings for one language extension. One instance exists per canonical extension in `_LANG_REGISTRY`.

| Field | Type | Purpose |
|---|---|---|
| `language` | `Language` | tree-sitter `Language` object used for parsing source files |
| `definition_dict` | `dict[str, str]` | Maps AST node type → child node type that holds the symbol name |
| `import_query` | `str \| None` | tree-sitter S-expression query for extracting import statements |
| `usage_node_types` | `dict \| None` | AST node type categories for usage tracking (see below) |
| `import_resolve` | `dict \| None` | Module path resolution rules (see below) |
| `same_package_visible` | `bool` | Whether same-package symbols are visible without explicit imports (Java/Kotlin) |

---

### Definition dict (e.g., `PYTHON_DEFINITION_DICT`)

Keyed by canonical extension; values are plain `dict[str, str]`.

| Key | Value | Purpose |
|---|---|---|
| AST node type string (e.g., `"function_definition"`) | Child node type string or sentinel (e.g., `"identifier"`, `"__assignment__"`) | Tells the extractor which child node carries the definition's name; sentinels (`__…__`) trigger dedicated extraction logic |

---

### `import_resolve` dict (inside `LangConfig`)

| Key | Type | Purpose |
|---|---|---|
| `separator` | `str` | Delimiter used in module paths (`"."` for Python/Java/Kotlin, `"/"` for JS/TS/C/C++) |
| `try_init` | `bool` | Look for `__init__.py` when resolving a package directory (Python only) |
| `index_ext_list` | `list[str]` | Extensions to probe as index files inside a directory (JS/TS) |
| `alt_ext_list` | `list[str]` | Alternative extensions to try when the exact file is not found |
| `try_bare_path` | `bool` | Attempt path resolution without any extension (C/C++) |
| `try_current_dir` | `bool` | Also try relative resolution from the current file's directory (Python, C/C++) |

---

### `usage_node_types` dict (e.g., `_PYTHON_USAGE_NODE_TYPES`)

| Key | Type | Purpose |
|---|---|---|
| `call_types` | `set[str]` | AST node types representing function/method call expressions |
| `attribute_types` | `set[str]` | AST node types representing attribute or member access |
| `skip_parent_types` | `set[str]` | Parent node types whose `identifier` children are not counted as usages (definitions, imports, parameters) |
| `skip_name_field_types` | `set[str]` | Parent types where the `name` field child is skipped (Python-specific) |
| `skip_parent_types_for_type_ref` | `set[str]` | Parent types where type-identifier / namespace-identifier children are skipped |
| `typed_alias_parent_types` | `set[str]` | Parent types whose children declare typed variable aliases (Java, C/C++, Kotlin) |

---

### `_LANG_REGISTRY`

`dict[str, LangConfig]` — maps canonical file extension strings (`"py"`, `"java"`, `"cpp"`, `"c"`, `"kt"`, `"js"`, `"ts"`, `"tsx"`) to their fully assembled `LangConfig` instances.

---

### `_EXT_ALIASES`

`dict[str, str]` — maps non-canonical extension aliases to their canonical counterpart.

| Key (alias) | Value (canonical) |
|---|---|
| `"h"` | `"cpp"` |
| `"kts"` | `"kt"` |
| `"jsx"` | `"js"` |

# Error Handling

## 1. Overall Strategy

`settings.py` employs a **fail-fast on required configuration, graceful degradation on optional configuration** strategy. Environment variables are validated at module import time, meaning configuration errors are surfaced immediately before any application logic executes. For optional settings, safe defaults are substituted silently, allowing the application to continue with predictable behavior. There are no retry mechanisms within this file; error handling is limited to validation and default substitution.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` | A required environment variable (no default provided) is absent from the environment | Raises `ValueError` with a descriptive message identifying the missing key | No | Module import fails; entire application startup is aborted |
| Missing optional environment variable | An optional environment variable is absent but a default value was supplied | Silently substitutes the default value after converting it to a string | Yes | Operation continues with the default; no error is raised or logged |
| `None` default for optional variable | `default=None` is passed and the environment variable is not set | Returns `None` immediately without type conversion | Yes | Caller receives `None`; downstream behavior depends on the caller |
| Bool type conversion | `var_type=bool` is requested for any environment variable value | Value is matched case-insensitively against `("true", "1", "yes", "on")`; any other string silently resolves to `False` | Yes | No exception; unrecognized truthy strings silently become `False` |
| Int/Float type conversion failure | `var_type=int` or `var_type=float` is requested and the value cannot be parsed | Python built-in `int()`/`float()` raises `ValueError` or `TypeError`; no internal catch | No | Module import fails at the point of the malformed setting |
| Empty `EXCLUDE_PATTERNS` | `EXCLUDE_PATTERNS` environment variable is set to an empty string or whitespace | Falls back to the hardcoded default list of patterns (`__pycache__`, `.git`, etc.) | Yes | File traversal uses the built-in exclusion list |

---

## 3. Design Notes

- **Import-time validation** is a deliberate design choice: by executing all `get_config_value` calls at module load, any misconfiguration is detected at startup rather than at the moment a specific feature is first used, preventing partially initialized application states.
- The distinction between **required** and **optional** variables is encoded through the `_REQUIRED` sentinel object rather than a boolean flag, allowing `None` itself to be a valid explicit default without being confused with "no default provided."
- Bool conversion is intentionally **permissive in the false direction**: any value not in the recognized truthy set is treated as `False` without raising an error, prioritizing availability over strict validation for boolean flags.
- Type conversion for `int` and `float` is delegated entirely to Python built-ins with **no internal guard**, meaning malformed numeric settings produce an uncaught exception at import time, consistent with the fail-fast strategy for settings that would cause undefined behavior if silently defaulted.
- The `EXCLUDE_PATTERNS` parsing applies a **two-level fallback**: first the environment variable is checked, then individual entries are stripped and filtered, and only if the result is empty is the hardcoded default list used.

# Summary

**codetwine/config/settings.py**: Central configuration root supplying all constants, LLM credentials, paths, and per-language parser/analysis settings to the entire project.

**Public API:** `get_config_value(key:str, default, var_type:type)→scalar`; `LangConfig(language, definition_dict:dict, import_query:str|None, usage_node_types:dict|None, import_resolve:dict|None, same_package_visible:bool)` frozen dataclass.

**Key structures produced:** `TREE_SITTER_LANGUAGES:dict[str,Language]`, `DEFINITION_DICTS:dict[str,dict]`, `IMPORT_QUERIES:dict[str,str|None]`, `USAGE_NODE_TYPES:dict[str,dict|None]`, `IMPORT_RESOLVE_CONFIG:dict[str,dict]`, `SAME_PACKAGE_VISIBLE:dict[str,bool]`, `_LANG_REGISTRY:dict[str,LangConfig]`.
