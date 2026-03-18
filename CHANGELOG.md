# Changelog

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
