# Changelog

## Unreleased

### Added
- SQL (`sql`, `tree-sitter-sql`): tables, views, materialized views, functions, procedures, types, sequences, triggers, indexes and schemas are extracted as definitions, and a reference to an object created in another `.sql` file of the project is a dependency
- `LangConfig.implicit_visibility` (`package` / `project`): the files whose definitions can be referenced without an import statement (Java / Kotlin: same directory, SQL: whole project), replacing `same_package_visible`
- Every non-empty text file is analysed: a file whose extension has no tree-sitter language is listed with empty `definitions`, `callee_usages` and `caller_usages`, a `null` summary and no design document, and copied to the output directory (`settings.has_language()`, `file_utils.is_text_file()`)
- `KNOWLEDGE_FORMAT` setting (`json` / `sqlite` / `both`) selecting the form of the whole-project result
- `codetwine/knowledge_db.py`: SQLite output (`project_knowledge.sqlite`) built from the per-file JSON files, with a read API (`open_knowledge`, `iter_files`, `get_file`, `callers_of`, `callees_of`, `find_definitions`)
- `PARSE_CACHE_MAX_FILES` setting capping how many files' parse results are kept in memory
- `knowledge_db.iter_dependencies()`: read each file's summary and dependency lists back from the database
- `examples/rlm_qa`: accepts a `project_knowledge.sqlite` as well as a `project_knowledge.json` (`TARGET_JSON_PATH` renamed to `TARGET_KNOWLEDGE_PATH`)
- `examples/rlm_qa/knowledge_store.py`: per-file read access to either knowledge file form
- `examples/rlm_qa`: `get_file_detail()` and `search_text()` tools
- `knowledge_db.find_definitions()`: `partial` argument for a case-insensitive contains match
- `doc_template.json`: heading format instruction for the `definitions` section (`` ## `<name>` ``)
- `doc.json`: `source_hash`, the SHA256 of the source file the document was generated from
- `doc_creator.load_doc()`: read one file's `doc.json`
- `LLMClient`: a warning when the LLM output was cut at `DOC_MAX_TOKENS`
- `examples/doc_template_search.json`: search-oriented design document template for any language (one section: overview and one prose entry per definition). The sample output is generated with it

### Changed
- Public settings renamed after their type: `TREE_SITTER_LANGUAGES` -> `EXT_TO_LANGUAGE_DICT`, `DEFINITION_DICTS` -> `EXT_TO_DEFINITION_DICT`, `IMPORT_QUERIES` -> `EXT_TO_IMPORT_QUERY_DICT`, `USAGE_NODE_TYPES` -> `EXT_TO_USAGE_NODE_TYPE_DICT`, `IMPORT_RESOLVE_CONFIG` -> `EXT_TO_IMPORT_RESOLVE_DICT`, `KNOWLEDGE_FORMATS` -> `KNOWLEDGE_FORMAT_TUPLE`, `SOURCE_ROOT_PATTERNS` -> `SOURCE_ROOT_PATTERN_LIST`
- `build_project_dependencies()`: an implicit dependency (Java / Kotlin same package, SQL) is added when a top-level definition name of the other file is used in the syntax tree, instead of when the file name appears in the source text
- `build_project_dependencies()`: collects every text file instead of only the supported extensions, and skips empty and binary files there; import resolution, dependency edges, change detection and design documents cover the files with a language only
- `examples/rlm_qa`: the agent now receives only the file graph and per-file summaries; definitions, source code and design documents are fetched per file through the tools instead of being sent into the sandbox
- `parse_file()`: the parse cache is now a bounded LRU, so the syntax trees of a whole project are no longer held at once
- `save_consolidated_json()` / `save_dependency_summary()`: entries are written one at a time instead of being assembled in a list first
- `generate_all_docs()`: only each design document's summary is carried forward between levels, not its full section text
- `get_file_dependencies()`: takes the project file set, source roots and caller map from the caller instead of rebuilding them per file
- `examples/doc_template_python.json`: the five sections are merged into one `design` section, so a design document takes one LLM call plus the summary
- `process_all_files()`: change detection runs only when design documents are generated
- `DOC_MAX_TOKENS` default raised from `8192` to `16384`

### Removed
- `is_file_unchanged()`

### Fixed
- `extract_callee_source()`: returns the definition node the name belongs to instead of the parent of the name node. A C/C++ function definition now comes with its body, and a SQL object with its whole `CREATE` statement
- Change detection compares the source with the `source_hash` recorded in `doc.json` instead of with the source copy in the output directory. The copy is refreshed by every run, so a change made between runs with `ENABLE_LLM_DOC=False` was never regenerated. A design document without `source_hash` is regenerated once
- `KNOWLEDGE_FORMAT`: an unusable value no longer stops `import codetwine`. It is checked at the start of `process_all_files()` instead, before anything is analysed, so a caller that replaces the setting in the pipeline's namespace is not stopped by what the environment holds
- `generate_candidate_path_list()`: resolve an import whose specifier already carries a known extension (JS/TS `import "./helpers.js"`). Such a path is now tried as it is instead of only as a directory index
- `extract_definitions()`: extract methods, constructors and fields declared inside a class, struct, interface, enum or object
- `extract_definitions()`: extract Kotlin `val` / `var` / `const val`
- Updated sample output

## 0.3.0 - 2026-07-25

### Added
- LLM code summarization as a context-overflow fallback in design-document generation: large dependency symbols (`callee_usages[].target_context`) and large source definitions are replaced by concise behavior summaries, cached per symbol via SHA256 (`_summarize_code`, `_summarize_callee_usages`, `_splice_large_definitions`)
- `ENABLE_CODE_SUMMARY`, `CODE_SUMMARY_TRIGGER_LINES`, `CODE_SUMMARY_MAX_CHARS` settings

### Changed
- `_generate_section_with_fallback()`: restructured the context-overflow fallback into cumulative reduction stages (drop caller bodies → drop callee context → summarize callee symbols → summarize source definitions)
- `CALLEE_USAGES_SCHEMA_NOTE`: corrected wording to describe the dependency symbol (not file), noting large ones may be summarized

### Removed
- The 100-character compaction stage of the callee-summary fallback (low yield; superseded by symbol-level code summarization). `_build_callee_context_summary()` no longer takes a `compact` argument

## 0.2.1 - 2026-04-15

### Added
- `detect_source_roots()`: Detect source root prefixes (e.g. `src/main/java/`) present in the project
- `resolve_module_to_project_path()`: Fallback resolution with source root prefixes for Maven/Gradle/Scala standard layouts
- `SOURCE_ROOT_PATTERNS`: Configuration for known source root directory patterns
- Updated sample output

## 0.2.0 - 2026-04-11

### Changed
- `_save_doc()`: Changed Markdown section headings from `##` to `#`
- `_parse_md_sections()`: Updated section delimiter from `## {title}` to `# {title}`
- `_build_section_prompt()`: Use `output_path_to_rel()` for relative file path in prompt
- Updated sample output

## 0.1.9 - 2026-03-31

### Fixed
- `_save_doc()`: Strip duplicate section title headers that the LLM may include in its response
- Updated sample output

## 0.1.8 - 2026-03-30

### Changed
- README: Rewrote High-Level Processing Flow for clarity (step 1: file collection, step 3: extraction details, step 4: topological sort and summary propagation, step 5: output description)
- README: Moved Output Files section to directly follow Processing Flow

## 0.1.7 - 2026-03-27

### Changed
- README: Added emoji icons to section headings
- RLM QA agent: Added `LLM_API_BASE` configuration for custom API endpoints (e.g. Ollama, Azure)
- RLM QA agent: Replaced `dspy.configure(lm=lm)` with `rlm.set_lm(lm)` for module-level LM setting

### Removed
- RLM QA agent: Removed `max_iterations` parameter from RLM

## 0.1.6 - 2026-03-26

### Changed
- `rlm_qa_agent.py`: Renamed private functions to public (`_build_doc_schema` → `build_doc_schema`, `_load_project` → `load_project`, `_create_interpreter` → `create_interpreter`)

## 0.1.5 - 2026-03-25

### Changed
- `pyproject.toml`: Pinned all dependencies to exact versions (`>=` → `==`)
- `graph_search()`: Renamed return key `results` → `nodes`
- `graph_search()`: Renamed internal variables for clarity (`starts` → `candidates`, `file_deps` → `deps`, `caller` → `usage`)
- Updated sample output

## 0.1.4 - 2026-03-24

### Changed
- `doc_template.json`: Removed character limit (400-600 chars) from `summary_prompt`
- RLM QA agent: Strengthened Investigation rules to require verifying answers against actual source code before responding
- RLM QA agent: Increased `max_iterations` from 10 to 12

### Added
- RLM QA agent: Added `SUB_LLM_MODEL` to separate sub-LLM for `llm_query` / `llm_query_batched` within RLM sandbox

## 0.1.3 - 2026-03-19

### Fixed
- README: `--output-dir` default description did not match actual behavior when only `--project-dir` is specified
- README: `examples/rlm_qa/qa_tools.py` was missing from Project Structure

### Changed
- README: Clarified `file` / `callers` / `callees` field descriptions in JSON Schema tables to indicate they are paths within the output directory
- README: Added `OUTPUT_LANGUAGE` to Quick Start `.env` example
- README: Added Note in RLM QA section explaining that `file` field paths differ from original source tree paths
- RLM QA agent: Removed usage guidance from `context` field description in JSON Schema, keeping only data structure info

### Added
- RLM QA agent: Added Investigation rules with concrete methods for code investigation (`definitions[].context` / `read_source_file()`)

## 0.1.2 - 2026-03-19

### Fixed
- Python same-directory imports (e.g. `import module_name`) not detected as dependencies

### Changed
- Renamed `config/logging.py` to `config/logger.py` to avoid standard library name collision

### Added
- Python-optimized design document template (`examples/doc_template_python.json`)

## 0.1.1 - 2026-03-18

### Fixed
- Incomplete `doc.json` (missing sections or empty summary) being reused instead of regenerated
- `InternalServerError` and `ServiceUnavailableError` not being caught in LLM API error handling

## 0.1.0 - 2026-03-17

### Added
- Dependency analysis via tree-sitter (supports 7 languages: Python / Java / JavaScript / TypeScript / C / C++ / Kotlin)
- Automated design document generation via LLM (supports multiple providers through litellm)
- Symbol-level (functions, classes) dependency extraction
- Dependency-order-aware document generation via topological sort
- Incremental processing (regenerates only changed files and their affected scope)
- Dependency graph output in Mermaid format
- Customizable design document template (`doc_template.json`)
- Manual editing of `doc.md` with automatic reflection to `doc.json`
- Dependency-only output with `ENABLE_LLM_DOC=False`
- RLM QA agent sample (`examples/rlm_qa/`)
