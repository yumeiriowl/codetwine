# Design Document: main.py

# Design Specification

**Overview**

Parse command-line arguments, resolve configuration directories, and orchestrate the complete source code analysis pipeline from dependency extraction through design document generation and consolidated output production.

The file serves as the application entry point in these situations:

- A developer runs `uv run main.py` with optional `--project-dir` and `--output-dir` flags to analyze a project and invoke `parse_args()` and `resolve_dirs()` for argument resolution before calling `process_all_files()`.
- The pipeline calls `process_all_files()` from `codetwine/pipeline.py` to execute the full analysis workflow, which depends on `main.py` establishing the correct project and output directories.
- The LLM-based design document generation feature checks `ENABLE_LLM_DOC` and conditionally creates an `LLMClient` instance to pass to the pipeline.
- Logging infrastructure is initialized by `setup_logging()` before any analysis runs to configure console and file output across the entire execution.

This file depends on `codetwine/config/settings.py` for configuration values (DEFAULT_PROJECT_DIR, DEFAULT_OUTPUT_DIR, REPO_ROOT, ENABLE_LLM_DOC) and environment-driven LLM settings; `codetwine/config/logger.py` for centralized logging setup; `codetwine/llm/client.py` for the LLMClient class used in document generation; and `codetwine/pipeline.py` for the core async analysis orchestration. No files are documented as depending on main.py.

Directory resolution implements a specific policy: when `--project-dir` is specified without `--output-dir`, the hardcoded default of `{REPO_ROOT}/output` is used instead of DEFAULT_OUTPUT_DIR from `.env`, supporting a use case where custom projects should write to a predictable location rather than inheriting environment configuration.

**Definitions**

## `parse_args`

Parse `--project-dir` and `--output-dir` command-line flags and return an argparse.Namespace object. Called once at startup to capture user-supplied directory overrides before passing results to `resolve_dirs()`.

## `resolve_dirs`

Determine the project_dir and output_dir tuple by applying precedence rules: CLI arguments take priority, then DEFAULT_PROJECT_DIR and DEFAULT_OUTPUT_DIR from settings.py, with special handling that defaults output_dir to `{REPO_ROOT}/output` when only `--project-dir` is supplied. Called during main initialization to unify configuration from CLI arguments and `.env` file into canonical paths for pipeline execution.

## `main`

Initialize logging via `setup_logging()`, parse command-line arguments, resolve directory paths, conditionally instantiate LLMClient if ENABLE_LLM_DOC is true, and invoke the async pipeline via `process_all_files()`. Serves as the single entry point for the application when run as a script.

# Summary

# main.py Summary

**Single Responsibility:** Entry point that orchestrates the source code analysis pipeline by parsing CLI arguments, resolving configuration directories, initializing logging, and conditionally enabling LLM-based document generation before invoking the core async analysis workflow.

**Main Public Definitions:** `parse_args()` captures `--project-dir` and `--output-dir` command-line flags; `resolve_dirs()` applies precedence rules favoring CLI arguments over environment configuration, with special handling to default output to `{REPO_ROOT}/output` when only project directory is specified; `main()` coordinates logging setup, argument parsing, directory resolution, LLM client instantiation, and pipeline invocation.

**Key Concepts:** Command-line argument parsing, configuration directory resolution, logging initialization, LLM integration, async pipeline orchestration, environment-driven settings, hardcoded fallback behavior.
