# Design Document: codetwine/config/settings.py

# Design Specification

**Overview**

Manage configuration settings and language registry for source code analysis across multiple programming languages using tree-sitter parsers.

This file is used by developers and other project modules to:
- Call `get_config_value()` to retrieve environment variables with type conversion and default values for LLM settings, paths, performance tuning, and analysis options.
- Access `language_ext()` to determine which language's grammar settings should analyze a given file path, accounting for file extensions, case sensitivity, and special overrides.
- Retrieve language-specific configuration via `EXT_TO_DEFINITION_DICT`, `EXT_TO_IMPORT_QUERY_DICT`, `EXT_TO_USAGE_NODE_TYPE_DICT`, and `EXT_TO_IMPORT_RESOLVE_DICT` to extract definitions, imports, and usage information from source code.
- Call `has_language()` to check whether a file participates in language-based analysis.
- Call `set_copy_target_ext()` and `set_no_language_file()` to register COBOL copybooks and R Markdown files without code chunks that require special handling.
- Query extension sets like `COBOL_EXT_SET`, `C_FAMILY_EXT_SET`, `CSHARP_EXT_SET`, `R_EXT_SET`, and `R_MARKDOWN_EXT_SET` to identify files of specific language families.

The file is the configuration hub of the codetwine project: it centralizes environment variable parsing, defines grammar and resolution rules for twelve programming languages, and provides lookup dictionaries that nearly every analysis module (dependency_graph.py, file_analyzer.py, import_binding.py, import_reference.py, ts_parser.py, definition_source.py, and others) consult to determine how to process a file. It imports tree-sitter language bindings and defines per-language AST node mapping dictionaries (like `PYTHON_DEFINITION_DICT`, `JAVA_DEFINITION_DICT`, etc.) that map AST node types to the fields holding definition names and usage context.

The design uses sentinel values like `"__assignment__"` and `"__default_export__"` in definition dictionaries to signal that a name-extraction function must dispatch to specialized logic rather than simply reading a direct child node. Extension alias expansion via `_expand_ext_aliases()` avoids repeating configuration for file extensions that share language settings (e.g., ".h" uses C++ rules). Language registry design centralizes all per-language configuration in the `LangConfig` dataclass and `_LANG_REGISTRY` dictionary, auto-generating public lookup dictionaries from it. Parse and definition caching is configured via `PARSE_CACHE_MAX_FILES` with LRU eviction. The `_copy_target_ext_dict` and `_no_language_path_set` module-level dictionaries track project-specific file overrides and are cleared per project to prevent cross-project pollution.

**Definitions**

## `get_config_value`

Retrieves an environment variable, converts it to a specified type (str, int, float, or bool), and returns a default value if the variable is unset; raises `ValueError` if a required variable is missing. Used throughout the module to populate LLM settings (`LLM_API_KEY`, `LLM_MODEL`), path settings (`DEFAULT_PROJECT_DIR`, `DOC_TEMPLATE_PATH`), performance settings (`MAX_WORKERS`, `MAX_RETRIES`), and analysis settings (`ENABLE_LLM_DOC`, `SUMMARY_MAX_CHARS`).

## `_REQUIRED`

Sentinel object used as the default value parameter in `get_config_value()` to indicate that an environment variable is mandatory; when a variable is not set and `_REQUIRED` is the default, a `ValueError` is raised.

## `LLM_API_KEY`

Environment variable or empty default string specifying the API key for the language model service; passed to `LLMClient` initialization.

## `LLM_MODEL`

Environment variable or empty default string specifying the name of the language model (e.g., "gpt-4"); validated in `LLMClient.__init__()` to be non-empty.

## `LLM_API_BASE`

Environment variable or empty default string specifying the API endpoint URL for the language model service; passed to `LLMClient` initialization.

## `OUTPUT_LANGUAGE`

Environment variable or "English" default specifying the language in which design documents and code summaries should be generated; used in doc_creator.py to format prompts.

## `DOC_MAX_TOKENS`

Environment variable or default of 16384 specifying the maximum token count for a single LLM generation request; passed to `LLMClient.generate()`.

## `REPO_ROOT`

Absolute path to the root of the codetwine repository, computed from the settings.py file location; used in main.py and as a base for relative path defaults.

## `DEFAULT_PROJECT_DIR`

Environment variable or default repository root specifying the project directory to analyze when none is provided via command-line arguments.

## `DEFAULT_OUTPUT_DIR`

Environment variable or default of `REPO_ROOT/output` specifying the directory where analysis results (JSON, SQLite, and design documents) are written.

## `DOC_TEMPLATE_PATH`

Environment variable or default path specifying the location of the JSON template file that defines the sections and prompts for design document generation; read by doc_creator.py.

## `MAX_WORKERS`

Environment variable or default of 4 specifying the maximum number of worker threads for parallel file analysis; passed to `process_all_files()` in doc_creator.py and pipeline.py.

## `MAX_RETRIES`

Environment variable or default of 3 specifying the number of retry attempts after an LLM rate-limit error; validated in `LLMClient.__init__()` to be non-negative.

## `RETRY_WAIT`

Environment variable or default of 2 specifying the number of seconds to wait between LLM retry attempts after a rate-limit error.

## `PARSE_CACHE_MAX_FILES`

Environment variable or default of 200 specifying the maximum number of parsed files cached in memory; when exceeded, the least recently used entry is discarded. A value of 0 disables eviction and caches every parse result.

## `KNOWLEDGE_FORMAT_TUPLE`

Tuple of allowed values for `KNOWLEDGE_FORMAT`: `("json", "sqlite", "both")`; validated before analysis begins.

## `KNOWLEDGE_FORMAT`

Environment variable or "json" default specifying the output format for whole-project analysis results; must be a member of `KNOWLEDGE_FORMAT_TUPLE`. Controls whether `project_knowledge.json`, `project_knowledge.sqlite`, or both are written.

## `ENABLE_LLM_DOC`

Environment variable or default of `True` specifying whether to generate design documents using LLM; when `False`, analysis runs but design documents are skipped; passed to `process_all_files()` in pipeline.py.

## `SUMMARY_MAX_CHARS`

Environment variable or default of 600 specifying the maximum character limit for a single file summary prompt used in design document generation.

## `ENABLE_CODE_SUMMARY`

Environment variable or default of `True` specifying whether to enable LLM summarization of large code blocks as a fallback when the design document prompt exceeds the model context window.

## `CODE_SUMMARY_TRIGGER_LINES`

Environment variable or default of 40 specifying the minimum line count for a definition or dependency symbol to be considered a candidate for LLM summarization during context-overflow fallback.

## `CODE_SUMMARY_MAX_CHARS`

Environment variable or default of 400 specifying the maximum character limit for a single code behavior summary generated by LLM; used in doc_creator.py.

## `SOURCE_ENCODING`

List of Python codec names tried in order on a source file that has no BOM and is not valid UTF-8; populated by splitting the `SOURCE_ENCODING` environment variable by commas. Empty by default; a file that none of them decode is decoded with the encoding charset-normalizer detects.

## `_SOURCE_ENCODING_ENV`

Raw environment variable string for `SOURCE_ENCODING` before splitting; used to populate the `SOURCE_ENCODING` list.

## `EXCLUDE_PATTERNS`

List of glob patterns specifying files and directories to skip during project traversal; defaults to `["__pycache__", ".git", ".github", ".venv", "node_modules"]` if the environment variable is empty. Used by dependency_graph.py to filter out non-source directories.

## `_EXCLUDE_PATTERNS_ENV`

Raw environment variable string for `EXCLUDE_PATTERNS` before splitting; used to populate the `EXCLUDE_PATTERNS` list.

## `PYTHON_DEFINITION_DICT`

Mapping of Python AST node types to the child node types holding their names (e.g., `"function_definition": "identifier"`); supports sentinel values like `"__assignment__"` for complex extraction logic. Extracted and analyzed by definition_source.py and definitions.py.

## `JAVA_DEFINITION_DICT`

Mapping of Java AST node types to name-holding child node types, including class, method, interface, enum, and field declarations. Used to extract definitions from Java source files.

## `CPP_DEFINITION_DICT`

Mapping of C++ AST node types to name-holding child node types, supporting complex patterns like struct, class, namespace, function, and preprocessor definitions. Includes sentinel values for nested or multi-level name extraction.

## `C_DEFINITION_DICT`

Mapping of C AST node types to name-holding child node types for function, struct, union, enum, and preprocessor definitions; similar to `CPP_DEFINITION_DICT` but without C++-specific constructs.

## `KOTLIN_DEFINITION_DICT`

Mapping of Kotlin AST node types to name-holding child node types for classes, functions, objects, enums, type aliases, and properties; includes the sentinel `"__kotlin_property__"` for property extraction.

## `JS_DEFINITION_DICT`

Mapping of JavaScript AST node types to name-holding child node types for functions, methods, classes, fields, and variable declarations; supports CommonJS patterns and export statements with sentinel values.

## `TS_DEFINITION_DICT`

Mapping of TypeScript AST node types to name-holding child node types, extending JavaScript definitions with TypeScript-specific constructs like interfaces, abstract classes, method signatures, type aliases, and enums.

## `RUST_DEFINITION_DICT`

Mapping of Rust AST node types to name-holding child node types for functions, structs, enums, traits, impl blocks, type aliases, constants, modules, and macros; includes sentinel values for impl blocks and modules.

## `CSHARP_DEFINITION_DICT`

Mapping of C# AST node types to name-holding child node types for classes, structs, interfaces, enums, records, methods, properties, events, and fields; uses the sentinel `"__name_field__"` for most type members and `"__variable_declaration__"` for field extraction.

## `COBOL_DEFINITION_DICT`

Mapping of COBOL AST node types to name-holding node types for program definitions, entry statements, section headers, paragraph headers, data descriptions, and file descriptions; read by cobol_source.read_cobol_source() and the values are not directly consulted.

## `BMS_DEFINITION_DICT`

Mapping containing a single COBOL data description node type for BMS symbolic map extraction; read by bms_source.read_bms_source().

## `R_DEFINITION_DICT`

Mapping of R AST node types for function definitions, binary operators (custom operators), and call/argument usages; read by r_source.r_definition_list() and the values are not directly consulted.

## `SQL_DEFINITION_DICT`

Mapping of SQL AST node types to name-holding child node types for CREATE statements (tables, views, functions, procedures, types, sequences, triggers) and schema/index declarations; extracted by definition_source.py.

## `_PYTHON_IMPORT_QUERY`

Tree-sitter query string (S-expression) describing patterns for Python import statements: import-as, from-import, wildcard imports, and dotted names; captures `@module` and `@name` and `@import_node`.

## `_JS_IMPORT_QUERY`

Tree-sitter query string describing patterns for JavaScript and TypeScript import statements, including ES6 imports, CommonJS require, dynamic imports, and re-exports; captures multiple variants for default imports, namespace imports, named imports, and member access.

## `_JAVA_IMPORT_QUERY`

Tree-sitter query string for Java import declarations; captures scoped identifiers as `@module`.

## `_C_IMPORT_QUERY`

Tree-sitter query string for C and C++ preprocessor include directives; captures the include path as `@module`.

## `_KOTLIN_IMPORT_QUERY`

Tree-sitter query string for Kotlin import statements; captures qualified identifiers as `@module`.

## `_RUST_IMPORT_QUERY`

Tree-sitter query string for Rust use declarations, mod declarations without bodies, extern crate declarations, and scoped identifiers (paths); captures `@path_item` for each pattern and is further processed by rust_import_list in rust_path.py.

## `_PYTHON_USAGE_NODE_TYPE_DICT`

Dictionary mapping Python AST node types to usage tracking settings: call types, attribute types, names that skip usage tracking, scope types for local binding, pattern types, and self-reference names; used by import_reference.py to extract usages and dependencies.

## `_JAVA_USAGE_NODE_TYPE_DICT`

Dictionary of Java AST node types for usage tracking: method invocations, field access, scope types (methods, lambdas), local binding patterns, and type references; distinguishes typed aliases for variables and formal parameters.

## `_JS_USAGE_NODE_TYPE_DICT`

Dictionary of JavaScript AST node types for usage tracking: call expressions, member expressions, scope types (functions, lambdas, arrow functions), variable declarators, and pattern types; includes self-reference types for `this`.

## `_C_USAGE_NODE_TYPE_DICT`

Dictionary of C and C++ AST node types for usage tracking: call expressions, field expressions, scope types (function definitions, lambdas), local binding patterns, and pattern types; skips include directives.

## `_KOTLIN_USAGE_NODE_TYPE_DICT`

Dictionary of Kotlin AST node types for usage tracking: call expressions, navigation expressions (member access), scope types (functions, lambdas, secondary constructors), local binding patterns, and self-reference types.

## `_RUST_USAGE_NODE_TYPE_DICT`

Dictionary of Rust AST node types for usage tracking: call expressions, field expressions, scoped identifiers, use declarations, pattern types, local binding, scope types, and pattern reference/variant types; includes macro argument handling and module-open types.

## `_SQL_USAGE_NODE_TYPE_DICT`

Dictionary of SQL AST node types for usage tracking: empty call and attribute types, with `identifier_parent_types` restricting identifiers to object references; minimal usage tracking since SQL dependencies are usually explicit.

## `LangConfig`

Dataclass bundling all language-specific analysis settings: tree-sitter Language object, definition node mapping, import extraction query, usage node type dictionary, module resolution settings, implicit visibility scope, extension case sensitivity, and reference resolution kind. Used as the value type in `_LANG_REGISTRY`.

## `_COBOL_LANG_CONFIG`

Shared `LangConfig` instance for COBOL files with `reference_kind="cobol"`, `name_index=True`, and case-insensitive extension matching; reused by multiple COBOL file extensions.

## `_BMS_LANG_CONFIG`

`LangConfig` instance for BMS (Basic Mapping Support) sources parsed with the COBOL grammar; uses `BMS_DEFINITION_DICT`, case-insensitive matching, and `reference_kind="cobol"` but no import query.

## `_R_LANG_CONFIG`

`LangConfig` instance for R scripts and R Markdown/Quarto files; uses `R_DEFINITION_DICT`, case-insensitive matching, and `reference_kind="r"`.

## `_LANG_REGISTRY`

Central dictionary mapping file extension strings (e.g., "py", "java", "cpp") to `LangConfig` objects defining grammar, definition extraction, import queries, usage tracking, and module resolution for each language; used to auto-generate public lookup dictionaries.

## `_JS_TS_EXT_LIST`

List of JavaScript and TypeScript file extensions: `[".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"]`; used as a base for index and alternative extension lists in `_JS_TS_RESOLVE_DICT`.

## `_C_CPP_EXT_LIST`

List of C and C++ file extensions: `[".h", ".c", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"]`; used in `_C_CPP_RESOLVE_DICT`.

## `_JS_SOURCE_EXT_DICT`

Dictionary mapping JavaScript/TypeScript import extensions to the source file extensions they may resolve to; for example, ".js" can stand for ".ts", ".tsx", or ".jsx" files. Used by import_to_path.py during module resolution.

## `_JS_TS_RESOLVE_DICT`

Dictionary of module resolution settings for JavaScript/TypeScript: path separator "/", bind type "export", index file extensions, alternative extensions, tsconfig.json / jsconfig.json lookup, package.json parsing, and bundler config aliases (Vite, Webpack).

## `_C_CPP_RESOLVE_DICT`

Dictionary of module resolution settings for C/C++: path separator "/", bind type "include", bare path attempt, current directory resolution, and minimum path-end-part matching for header file discovery.

## `EXT_TO_LANGUAGE_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to tree-sitter Language objects; used by ts_parser.py and other modules to get the parser for a given file.

## `EXT_TO_DEFINITION_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to definition node mapping dictionaries; used by definition_source.py, file_analyzer.py, import_binding.py, and others to extract definitions.

## `COBOL_EXT_SET`

Set of all COBOL file extensions (lower case); used by cobol_file_index.py and ts_parser.py to identify COBOL files requiring statement-level parsing instead of whole-file tree-sitter parsing.

## `C_FAMILY_EXT_SET`

Set of C and C++ file extensions (including aliases) identified by the "include" bind type in import resolution settings; used by ts_parser.py to apply C-family-specific parsing (macro blanking).

## `CSHARP_EXT_SET`

Set of all C# file extensions; used by csharp_namespace_index.py to identify C# files for namespace-based reference resolution.

## `R_EXT_SET`

Set of all R file extensions (scripts, R Markdown, Quarto in lower case); used by dependency_graph.py, definition_source.py, import_reference.py, and r_name_index.py to identify R files requiring R-specific import and definition handling.

## `R_MARKDOWN_EXT_SET`

Set of R Markdown and Quarto file extensions (lower case): `{"rmd", "qmd"}`; used by ts_parser.py and dependency_graph.py to identify files needing R code chunk extraction.

## `BMS_EXT_SET`

Set of all BMS file extensions; used by cobol_file_index.py and ts_parser.py to identify BMS sources requiring symbolic map parsing.

## `_IGNORE_CASE_EXT_SET`

Set of file extensions whose language settings match extension strings without regard to case; populated from languages with `ignore_ext_case=True` (COBOL, BMS, R).

## `_copy_target_ext_dict`

Module-level dictionary mapping absolute file paths to extension strings representing the language settings to apply; used for COBOL copybooks named by COPY statements. Cleared per project by `set_copy_target_ext()`.

## `set_copy_target_ext`

Records files whose language comes from the files that name them rather than from their own extension (COBOL copybooks); clears previous entries for the same project directory and populates `_copy_target_ext_dict` with absolute path to extension mappings.

## `_no_language_path_set`

Module-level set of absolute file paths representing files analyzed without a language despite having an extension (R Markdown / Quarto without R code chunks). Cleared per project by `set_no_language_file()`.

## `set_no_language_file`

Records files of a project that are analyzed without a language whatever their extension; clears previous entries for the same project directory and populates `_no_language_path_set` with absolute file paths.

## `language_ext`

Returns the extension whose language settings should analyze a given file path: checks `_copy_target_ext_dict` overrides, `_no_language_path_set` (returns empty string), then the file's actual extension (case-sensitive or case-insensitive depending on `_IGNORE_CASE_EXT_SET`). Returns empty string for files without a supported language.

## `has_language`

Returns whether a file participates in language-based analysis (definitions, dependencies, design documents); returns `True` when `language_ext()` gives a non-empty extension. Used by pipeline.py and dependency_graph.py to filter files.

## `EXT_TO_IMPORT_QUERY_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to import extraction query strings; used by import_binding.py and import_to_path.py to extract imports from source code.

## `EXT_TO_USAGE_NODE_TYPE_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to usage node type dictionaries; used by import_binding.py and import_reference.py for scope handling, local binding, and pattern extraction during usage tracking.

## `PATTERN_TYPE_SET`

Set of all pattern AST node types across all languages; used by definitions.py to extract variable names from patterns (assignments, destructuring, for loops) during definition binding.

## `PATTERN_FIELD_DICT`

Dictionary mapping pattern node types to the field names that hold nested patterns; used by definitions.py to recursively extract variable names during definition binding.

## `EXT_TO_IMPORT_RESOLVE_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to module resolution configuration dictionaries (containing keys like "separator", "bind", "index_ext_list", etc.); used by import_to_path.py during import path resolution.

## `EXT_TO_REFERENCE_KIND_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to reference resolution kinds ("import", "cobol", "csharp", "r"); used by reference_target.py to determine how a file's dependencies are resolved.

## `EXT_TO_IMPLICIT_VISIBILITY_DICT`

Expansion of `_LANG_REGISTRY` mapping file extensions (including aliases) to implicit visibility scopes ("package" for Java/Kotlin, "project" for SQL, or absent); used by import_binding.py to find definitions that can be referenced without explicit imports.

## `_expand_ext_aliases`

Helper function that expands a settings dictionary keyed by canonical extensions (from `_LANG_REGISTRY.keys()`) to include alias extensions defined in `_EXT_ALIAS_DICT`; used to auto-generate all public `EXT_TO_*_DICT` dictionaries.

## `_EXT_ALIAS_DICT`

Dictionary mapping file extension aliases to their canonical extensions; for example, ".h" → "cpp", ".jsx" → "js", ".mts" → "ts". Used by `_expand_ext_aliases()` to auto-populate public dictionaries with alias entries.

## `SOURCE_ROOT_PATTERN_LIST`

List of directory path patterns (e.g., "src/main/java/", "src/") specifying standard Maven/Gradle and Python source layouts; used by import_to_path.py to locate source roots for resolving relative imports.

# Summary

# codetwine/config/settings.py Summary

This configuration hub centralizes environment variable parsing and language registry for source code analysis across twelve programming languages using tree-sitter parsers. It manages LLM settings, file paths, performance tuning, and analysis options through `get_config_value()`. The module defines per-language AST node mappings and import extraction queries via a `LangConfig` dataclass and `_LANG_REGISTRY` dictionary, auto-generating public lookup dictionaries (`EXT_TO_DEFINITION_DICT`, `EXT_TO_IMPORT_QUERY_DICT`, `EXT_TO_USAGE_NODE_TYPE_DICT`, `EXT_TO_IMPORT_RESOLVE_DICT`) that nearly every analysis module consults. Key functions include `language_ext()` for determining applicable language settings by file extension or override, `has_language()` for checking language participation, and `set_copy_target_ext()`/`set_no_language_file()` for registering special files like COBOL copybooks and R Markdown without code chunks. Extension sets identify language families for targeted processing.
