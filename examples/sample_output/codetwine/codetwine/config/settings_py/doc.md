# Design Document: codetwine/config/settings.py

# Design Specification

**Overview**

This file centralizes all runtime configuration for the codetwine project, loading environment variables, defining tree-sitter language wiring, and exposing per-language static settings used throughout the analysis pipeline.

- A developer needs LLM connection settings (`LLM_API_KEY`, `LLM_MODEL`, `LLM_API_BASE`, `DOC_MAX_TOKENS`) when constructing an `LLMClient`.
- A module needs to resolve a file's AST definition rules by looking up its extension in `DEFINITION_DICTS`, or its import query in `IMPORT_QUERIES`, or its usage-tracking rules in `USAGE_NODE_TYPES`.
- The parser needs `TREE_SITTER_LANGUAGES` to obtain the compiled `Language` object for a given file extension, and `PARSE_CACHE_MAX_FILES` to bound its LRU parse cache.
- The import resolver needs `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, and `SOURCE_ROOT_PATTERNS` to turn an import statement into a project-relative file path.
- The pipeline needs `KNOWLEDGE_FORMAT`/`KNOWLEDGE_FORMATS` to validate output mode and `EXCLUDE_PATTERNS` to skip directories/files during project traversal.

This file has no internal project dependencies (it only consumes `.env` values and third-party tree-sitter grammar packages). It is imported by nearly every other module in the project: `main.py` uses the path/LLM toggles, `codetwine/file_analyzer.py` and `codetwine/import_to_path.py` use the definition/import/usage/registry dictionaries to drive language-agnostic AST analysis, `codetwine/doc_creator.py` and `codetwine/llm/client.py` use the LLM and summarization settings, `codetwine/pipeline.py` uses the output-format and worker settings, and `codetwine/parsers/ts_parser.py` uses the language map and cache size.

Configuration values are read once at import time via `get_config_value`, so environment changes after startup are not picked up; per-language behavior is designed to be extended by adding a single entry to `_LANG_REGISTRY` rather than editing multiple dictionaries, with `_expand_ext_aliases` propagating settings to alias extensions (e.g. `.h`, `.kts`, `.jsx`) automatically.

**Definitions**

## `get_config_value`
Reads an environment variable via `os.getenv` and converts it to the requested type (`bool`, `int`, `float`, or `str`), raising `ValueError` when the variable is required (no default given) but unset, and returning `None` when the default is explicitly `None`. It is the single entry point used by every setting in this file to keep environment parsing and type coercion consistent, including boolean parsing of strings like `"true"`/`"1"`/`"yes"`/`"on"`.

## `LLM_API_KEY`
Holds the API key used by `LLMClient` to authenticate LLM requests; read from the `LLM_API_KEY` environment variable, defaulting to an empty string.

## `LLM_MODEL`
Holds the model identifier passed to `LLMClient`, sourced from `LLM_MODEL`, defaulting to an empty string; the model name prefix is later used by the client to auto-detect the provider.

## `LLM_API_BASE`
Holds the custom API base URL for `LLMClient`, sourced from `LLM_API_BASE`, defaulting to an empty string (i.e. use the provider's default endpoint).

## `OUTPUT_LANGUAGE`
Specifies the natural language the generated design documents should be written in; consumed by `doc_creator.py` when building LLM prompts (`OUTPUT_LANGUAGE_INSTRUCTION`), defaulting to `"English"`.

## `DOC_MAX_TOKENS`
Sets the default `max_tokens` for `LLMClient.generate` calls when generating design-document text; read from `DOC_MAX_TOKENS` as an integer, defaulting to 16384.

## `REPO_ROOT`
Computes the repository root as two directories above this settings file; used as the base path for other path defaults (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `DOC_TEMPLATE_PATH`) and directly by `main.py` when deriving the output directory relative to an explicit `--project-dir`.

## `DEFAULT_PROJECT_DIR`
Provides the fallback project directory to analyze when no `--project-dir` CLI argument is given in `main.py`; read from `DEFAULT_PROJECT_DIR`, defaulting to `REPO_ROOT`.

## `DEFAULT_OUTPUT_DIR`
Provides the fallback output directory for generated documents/knowledge files when neither `--output-dir` nor `--project-dir` is specified; read from `DEFAULT_OUTPUT_DIR`, defaulting to `REPO_ROOT/output`.

## `DOC_TEMPLATE_PATH`
Points to the JSON file defining the design-document section prompts, loaded by `doc_creator.py` via `json.load`; read from `DOC_TEMPLATE_PATH`, defaulting to `REPO_ROOT/doc_template.json`.

## `MAX_WORKERS`
Sets the default number of parallel workers used both by `doc_creator.py`'s per-level document generation and by `pipeline.py`'s overall processing; read from `MAX_WORKERS` as an integer, defaulting to 4.

## `MAX_RETRIES`
Sets how many times `LLMClient` retries a request after hitting a rate limit before giving up and logging an error; read from `MAX_RETRIES` as an integer, defaulting to 3.

## `RETRY_WAIT`
Sets the number of seconds `LLMClient` sleeps between rate-limit retries; read from `RETRY_WAIT` as an integer, defaulting to 2.

## `PARSE_CACHE_MAX_FILES`
Bounds the number of parsed-file entries kept in `ts_parser.py`'s in-memory LRU cache; a value of 0 disables eviction and keeps every parsed result for the run, otherwise the least-recently-used entry is discarded once the count is exceeded. Read from `PARSE_CACHE_MAX_FILES` as an integer, defaulting to 200.

## `KNOWLEDGE_FORMATS`
Defines the fixed tuple of valid values (`"json"`, `"sqlite"`, `"both"`) accepted for `KNOWLEDGE_FORMAT`; used by `pipeline.py` to validate the configured format before any file analysis begins.

## `KNOWLEDGE_FORMAT`
Determines whether the whole-project knowledge base is written as `project_knowledge.json`, `project_knowledge.sqlite`, or both; read from `KNOWLEDGE_FORMAT`, normalized to lowercase and stripped, defaulting to `"json"`. Note the value checked by `pipeline.py` is whatever this module-level variable holds at call time, not necessarily the raw environment value at import time.

## `ENABLE_LLM_DOC`
Toggles whether `main.py`/`pipeline.py` invoke the LLM at all to generate design documents; when false, `main.py` skips constructing an `LLMClient` entirely. Read from `ENABLE_LLM_DOC` as a boolean, defaulting to `True`.

## `SUMMARY_MAX_CHARS`
Caps the character length of the per-file summary text generated by `doc_creator.py`'s summary prompt; read from `SUMMARY_MAX_CHARS` as an integer, defaulting to 600.

## `ENABLE_CODE_SUMMARY`
Enables the fallback LLM-based summarization of oversized code definitions/dependencies when a design-document prompt would exceed the model's context window; when false, `doc_creator.py` only trims context by dropping caller/callee usages without making extra LLM calls. Read from `ENABLE_CODE_SUMMARY` as a boolean, defaulting to `True`.

## `CODE_SUMMARY_TRIGGER_LINES`
Sets the line-count threshold above which a definition or dependency symbol becomes a candidate for LLM summarization during context-overflow handling in `doc_creator.py`; read from `CODE_SUMMARY_TRIGGER_LINES` as an integer, defaulting to 40.

## `CODE_SUMMARY_MAX_CHARS`
Sets the target character limit for a single generated code-behavior summary produced by `doc_creator.py`; read from `CODE_SUMMARY_MAX_CHARS` as an integer, defaulting to 400.

## `_EXCLUDE_PATTERNS_ENV`
Holds the raw comma-separated exclusion patterns string read from the `EXCLUDE_PATTERNS` environment variable, used only as an intermediate value to build `EXCLUDE_PATTERNS`.

## `EXCLUDE_PATTERNS`
Lists filename/glob patterns (matched with `fnmatch`) that `dependency_graph.py` uses to skip directories and files while walking the project tree; parsed from `_EXCLUDE_PATTERNS_ENV` if set, otherwise defaults to a built-in list (`__pycache__`, `.git`, `.github`, `.venv`, `node_modules`).

## `PYTHON_DEFINITION_DICT`
Maps Python AST node types (e.g. `function_definition`, `class_definition`, `expression_statement`) to the child node type or sentinel (`__assignment__`) used to extract the definition's name; consumed via `DEFINITION_DICTS` by `definitions.py`'s name-extraction logic and by `usage_analysis.py`/`dependency_graph.py` to recognize Python source files.

## `JAVA_DEFINITION_DICT`
Maps Java AST node types (classes, methods, interfaces, constructors, enums, fields) to their name-bearing child node type or the `__variable_declarator__` sentinel for field declarations, used for definition extraction on `.java` files.

## `CPP_DEFINITION_DICT`
Maps C++ AST node types (classes, structs, functions, namespaces, declarations, fields, aliases, enums, preprocessor defines) to their name node type or sentinel extractors (`__declarator_name__`, `__function_declarator__`, `__init_declarator__`), used for definition extraction on `.cpp`/`.h` files.

## `C_DEFINITION_DICT`
Maps C AST node types (functions, structs, declarations, fields, typedefs, enums, preprocessor defines) to their name node type or sentinel extractors, used for definition extraction on `.c` files.

## `KOTLIN_DEFINITION_DICT`
Maps Kotlin AST node types (classes, functions, objects, properties) to their name node type, including the `__kotlin_property__` sentinel for property declarations, used for definition extraction on `.kt`/`.kts` files.

## `JS_DEFINITION_DICT`
Maps JavaScript AST node types (function/class/method declarations, field definitions, `let`/`const`/`var` declarations) to their name node type, including the `__variable_declarator__` sentinel for lexical and variable declarations, used for definition extraction on `.js`/`.jsx` files.

## `TS_DEFINITION_DICT`
Maps TypeScript AST node types (functions, methods, classes, interfaces, public fields, variable declarations, type aliases, enums) to their name node type, used for definition extraction on `.ts`/`.tsx` files; shared with plain JavaScript's import query and usage rules via the language registry.

## `_PYTHON_IMPORT_QUERY`
Defines the tree-sitter S-expression query used to extract `@module`/`@name`/`@import_node` captures from Python `import`/`from...import` statements, consumed by `import_to_path.py` to build the import list for dependency resolution.

## `_JS_IMPORT_QUERY`
Defines the tree-sitter query covering JavaScript/TypeScript ES module imports/exports (default, named, namespace, re-export) as well as CommonJS `require()` calls including destructured requires; shared by JS, TS, and TSX language configs for import extraction.

## `_JAVA_IMPORT_QUERY`
Defines the tree-sitter query that captures the `@module`/`@import_node` for Java `import` declarations using `scoped_identifier`.

## `_C_IMPORT_QUERY`
Defines the tree-sitter query that captures `@module`/`@import_node` for C/C++ `#include` directives, matching both angle-bracket and quoted include paths; shared by the `c` and `cpp` language configs.

## `_KOTLIN_IMPORT_QUERY`
Defines the tree-sitter query that captures `@module`/`@import_node` for Kotlin `import` statements using `qualified_identifier`.

## `_PYTHON_USAGE_NODE_TYPES`
Defines the AST node type sets (`call_types`, `attribute_types`, `skip_parent_types`, `skip_name_field_types`) that `usage_analysis.py` uses to identify genuine usages of imported/target symbols in Python source versus syntactic occurrences (definitions, parameters, imports) that should be skipped.

## `_JAVA_USAGE_NODE_TYPES`
Defines the usage-tracking node types for Java, including `typed_alias_parent_types` (field/local-variable/parameter declarations) used to associate a variable name with its declared type for later usage matching, and `skip_parent_types_for_type_ref` to avoid treating import/scope-resolution identifiers as type references.

## `_JS_USAGE_NODE_TYPES`
Defines the usage-tracking node types for JavaScript (and shared with TypeScript), identifying call expressions and member expressions as usages while skipping syntactic contexts like import specifiers and declaration names.

## `_C_USAGE_NODE_TYPES`
Defines the usage-tracking node types for C/C++, including `typed_alias_parent_types` (declarations and parameter declarations) for associating variable names with declared types, and skip rules for `#include` directives and qualified identifiers.

## `_KOTLIN_USAGE_NODE_TYPES`
Defines the usage-tracking node types for Kotlin, including `typed_alias_parent_types` (property and parameter declarations) and skip rules covering imports, qualified identifiers, and package headers.

## `_JS_TS_EXT_LIST`
Lists the JavaScript/TypeScript file extensions (`.ts`, `.tsx`, `.js`, `.jsx`) used as `index_ext_list`/`alt_ext_list` values in the `import_resolve` configs for JS/TS/TSX languages, enabling extensionless or index-file import resolution.

## `_C_CPP_EXT_LIST`
Lists the C/C++ file extensions (`.h`, `.c`, `.cpp`) used as `alt_ext_list` in the `import_resolve` configs for the `c` and `cpp` languages, enabling cross-extension include resolution (e.g. resolving a `.h` include to a `.cpp` implementation).

## `LangConfig`
A frozen dataclass bundling every per-language setting (tree-sitter `language`, `definition_dict`, `import_query`, `usage_node_types`, `import_resolve`, `same_package_visible`) into a single record; instances populate `_LANG_REGISTRY`, and adding a new supported language requires only creating one new `LangConfig` entry rather than editing multiple separate dictionaries.

## `_LANG_REGISTRY`
The central mapping from canonical file extension (e.g. `"py"`, `"java"`, `"cpp"`) to its `LangConfig`; it is the single source of truth from which all public per-extension dictionaries (`TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`) are auto-generated.

## `_EXT_ALIASES`
Maps alias extensions (`h`, `kts`, `jsx`) to their canonical registry key (`cpp`, `kt`, `js` respectively), used by `_expand_ext_aliases` to make alias extensions resolve to the same settings as their canonical language without duplicating registry entries.

## `_expand_ext_aliases`
Takes a canonical-extension-keyed settings dictionary and returns a copy augmented with entries for each alias in `_EXT_ALIASES` that isn't already present, pointing to the same value as its canonical extension; used when building every public per-extension dictionary from `_LANG_REGISTRY`.

## `TREE_SITTER_LANGUAGES`
Maps file extension to its compiled tree-sitter `Language` object; looked up by `ts_parser.py` to parse a given source file and by `import_to_path.py` when resolving import queries, raising a lookup failure (returning `None, None`) for unsupported extensions.

## `DEFINITION_DICTS`
Maps file extension to its language-specific definition-node dictionary; looked up by `file_analyzer.py`, `import_to_path.py`, and `usage_analysis.py` to drive `extract_definitions`/name-resolution logic and to determine which extensions are supported at all (via `.keys()` in `dependency_graph.py`).

## `IMPORT_QUERIES`
Maps file extension to its tree-sitter import-extraction query string; looked up by `import_to_path.py` to fetch the appropriate query before running it against a parsed file's AST to collect import statements.

## `USAGE_NODE_TYPES`
Maps file extension to its usage-tracking node-type configuration; looked up by `usage_analysis.py` for both the target file's extension and the caller file's extension when determining whether an identifier constitutes a real usage of an imported symbol.

## `IMPORT_RESOLVE_CONFIG`
Maps file extension to its module/import path resolution settings (separator, `try_init`, `index_ext_list`, `alt_ext_list`, `try_bare_path`, `try_current_dir`); looked up by `import_to_path.py` and `usage_analysis.py` to convert an import module string into a project-relative file path, only for extensions that declare a non-`None` `import_resolve`.

## `SAME_PACKAGE_VISIBLE`
Maps file extension to whether same-directory files are implicitly visible without an explicit import (true for Java and Kotlin); used by `import_to_path.py` and `usage_analysis.py` to register same-package definition names as reachable symbols and by `dependency_graph.py` to group same-package files for cross-file dependency detection.

## `SOURCE_ROOT_PATTERNS`
Lists standard Maven/Gradle and Python src-layout source-root prefixes (e.g. `src/main/java/`, `src/`); used by `import_to_path.py` to strip these prefixes when matching an import's fully qualified path against actual project file paths.

# Summary

Central configuration module for codetwine: loads environment-based settings (LLM credentials/model, paths, worker counts, retry/timeout, knowledge output format, exclusion patterns) and builds a per-language registry (`_LANG_REGISTRY`) driving tree-sitter parsing, AST definition extraction, import queries, usage-node tracking, and import-path resolution for Python, Java, C/C++, Kotlin, JS/TS. Exposes `TREE_SITTER_LANGUAGES`, `DEFINITION_DICTS`, `IMPORT_QUERIES`, `USAGE_NODE_TYPES`, `IMPORT_RESOLVE_CONFIG`, `SAME_PACKAGE_VISIBLE`, `get_config_value`, `LangConfig`. No internal dependencies; imported nearly everywhere.
