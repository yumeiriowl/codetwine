# Design Document: main.py

# Overview & Purpose

## 1. Module Summary
Serve as the CLI entry point that parses command-line arguments, resolves project/output directory settings, and triggers the full project dependency-analysis and design-document-generation pipeline.

## 2. When to Use This Module
- **Running codetwine from the command line**: Execute `main.py` directly (e.g., via `uv run main.py`) to analyze a project's source code and generate dependency data and design documents.
- **Analyzing a specific project directory**: Pass `--project-dir DIR` to override `DEFAULT_PROJECT_DIR`, causing `resolve_dirs` to compute an output directory of `{REPO_ROOT}/output` unless `--output-dir` is also specified.
- **Customizing output location**: Pass `--output-dir DIR` to explicitly control where `process_all_files` writes dependency JSON, design docs, consolidated JSON, and Mermaid graphs.
- **Understanding argument resolution logic**: Call `resolve_dirs(args)` (e.g., in tests) to determine what `project_dir`/`output_dir` combination will be used given a parsed `argparse.Namespace`.
- **Toggling LLM-based documentation**: Rely on the `ENABLE_LLM_DOC` setting (via `.env`) to decide whether `main()` constructs an `LLMClient` instance and passes it into `process_all_files`, or passes `None` to skip design-document generation.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_args` | — | `argparse.Namespace` | Define and parse the `--project-dir` and `--output-dir` CLI options. |
| `resolve_dirs` | `args (argparse.Namespace)` | `tuple[str, str]` | Determine the effective `(project_dir, output_dir)` pair based on CLI args and `.env`-derived defaults. |
| `main` | — | `None` | Set up logging, parse args, resolve directories, optionally instantiate `LLMClient`, and run `process_all_files` via `asyncio.run`. |

## 4. Design Decisions
- **Asymmetric default resolution**: When only `--project-dir` is given (without `--output-dir`), the module intentionally ignores `DEFAULT_OUTPUT_DIR` from `.env` and falls back to `{REPO_ROOT}/output`, preventing accidental writes to a default output path meant for the default project. This behavior is explicitly documented in `resolve_dirs`'s docstring and is the only non-obvious branching logic in the module.
- **Optional LLM dependency**: `LLMClient` is only constructed when `ENABLE_LLM_DOC` is true; otherwise `None` is passed to `process_all_files`, delegating the decision of whether to skip documentation generation entirely to the pipeline layer.

# Definition Design Specifications

## `parse_args() -> argparse.Namespace`

**Responsibility:** Defines and parses the CLI interface for the tool, exposing `--project-dir` and `--output-dir` as optional overrides.

**When to use:** Called once at the start of `main()` to obtain user-supplied CLI arguments before resolving effective directories.

**Design decisions:**
- Both arguments are optional (no `default=` set), leaving them as `None` when omitted so that `resolve_dirs` can distinguish "not provided" from "explicitly provided" and apply fallback logic based on `.env` settings.

**Constraints & edge cases:**
- No validation is performed on the provided paths (existence, permissions, etc.); this is deferred to downstream consumers (`process_all_files`).

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

**Signature detail:** Returns `tuple[str, str]` — a `(project_dir, output_dir)` pair of absolute or relative filesystem paths to be used for the run.

**Responsibility:** Determines the effective project and output directories by combining CLI arguments with `.env`-derived defaults, applying a specific precedence rule.

**When to use:** Called immediately after `parse_args()` in `main()`, once per program invocation, to compute the final directories passed to `process_all_files`.

**Design decisions:**
- `project_dir` simply falls back to `DEFAULT_PROJECT_DIR` if `--project-dir` is not given.
- `output_dir` resolution has three branches instead of a simple fallback:
  1. If `--output-dir` is explicitly given, use it as-is.
  2. Else, if `--project-dir` was given (but not `--output-dir`), ignore `DEFAULT_OUTPUT_DIR` from `.env` entirely and use `{REPO_ROOT}/output` instead — this prevents accidentally writing output configured for a different default project into the wrong location when the user only overrides the project.
  3. Else (neither CLI arg given), use `DEFAULT_OUTPUT_DIR` from `.env`.
- This asymmetric precedence (CLI project-dir alone triggers a different output default than "no CLI args at all") is the key non-obvious behavior of this function.

**Constraints & edge cases:**
- Passing `--project-dir` without `--output-dir` always yields `{REPO_ROOT}/output`, even if `DEFAULT_OUTPUT_DIR` is customized in `.env` — callers relying on `.env`'s `DEFAULT_OUTPUT_DIR` must also pass `--output-dir` explicitly or omit `--project-dir`.
- Does not check that the resulting paths exist or are writable.

---

## `main() -> None`

**Responsibility:** Serves as the single entry point that wires together logging setup, argument resolution, optional LLM client construction, and the async pipeline execution.

**When to use:** Invoked when the script is run directly (`python main.py` / `uv run main.py`), guarded by `if __name__ == "__main__":`.

**Design decisions:**
- Logging is initialized first (`setup_logging()`) before any other logic, ensuring all subsequent operations (including argument parsing side effects and pipeline logging) are captured.
- The `LLMClient` is conditionally instantiated based on the `ENABLE_LLM_DOC` feature flag rather than always being created; when disabled, `None` is passed downstream so `process_all_files` can skip document generation without needing a valid LLM configuration (e.g., avoiding the `ValueError` raised by `LLMClient.__init__` when `LLM_MODEL` is unset).
- The async pipeline (`process_all_files`) is run via `asyncio.run`, making this the single top-level event loop entry for the whole program; `main()` itself is synchronous.

**Constraints & edge cases:**
- If `ENABLE_LLM_DOC` is `True` but `LLM_MODEL` is not configured, `LLMClient()` construction will raise `ValueError`, aborting execution before the pipeline starts.
- Since `asyncio.run` is used, `main()` cannot be called from within an already-running event loop.

# Dependency Description

### Dependencies (modules this file imports)

- main.py → codetwine/config/settings.py (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC`) : obtains default project/output directory paths, the repository root path (used to construct the fallback output directory when only `--project-dir` is specified), and the flag controlling whether LLM-based design document generation is enabled.

- main.py → codetwine/config/logger.py (`setup_logging`) : configures application-wide console and file logging before running the analysis pipeline.

- main.py → codetwine/llm/client.py (`LLMClient`) : instantiates the LLM client used to generate design documents, conditionally created only when `ENABLE_LLM_DOC` is true.

- main.py → codetwine/pipeline.py (`process_all_files`) : delegates the entire project analysis workflow (dependency extraction, design document generation, and output file creation) by invoking it asynchronously with the resolved project directory, output directory, and LLM client.

### Dependents (modules that import this file)

No dependent information available.

### Dependency Direction

All described relationships are unidirectional: main.py depends on `codetwine/config/settings.py`, `codetwine/config/logger.py`, `codetwine/llm/client.py`, and `codetwine/pipeline.py`, while none of these modules depend back on main.py. As the application entry point, main.py only consumes functionality from these modules and is not imported by any other project module.

# Data Flow

## 1. Inputs

- **Command-line arguments** (via `argparse`): `--project-dir` (str, optional) and `--output-dir` (str, optional), parsed into an `argparse.Namespace` object.
- **Configuration values** loaded at import time from `codetwine.config.settings`:
  - `DEFAULT_PROJECT_DIR` (str) — fallback project directory.
  - `DEFAULT_OUTPUT_DIR` (str) — fallback output directory.
  - `ENABLE_LLM_DOC` (bool) — flag controlling whether an `LLMClient` is instantiated.
  - `REPO_ROOT` (str) — repository root path, used to build a default `output` directory when only `--project-dir` is given.
- No direct file reads occur in this module; file I/O is delegated to `process_all_files`.

## 2. Transformation Overview

1. **Logging setup**: `setup_logging()` is called first, configuring the root logger (console + rotating file handlers) as a side effect. No data is passed in or returned.
2. **Argument parsing**: `parse_args()` converts raw CLI input into a structured `argparse.Namespace` with `project_dir` and `output_dir` attributes (each `str | None`).
3. **Directory resolution**: `resolve_dirs(args)` transforms the `Namespace` plus config constants into a concrete `(project_dir, output_dir)` tuple of strings, applying this precedence logic:
   - `project_dir` = `args.project_dir` if provided, else `DEFAULT_PROJECT_DIR`.
   - `output_dir` = `args.output_dir` if provided; else, if `args.project_dir` was provided (but not `--output-dir`), `os.path.join(REPO_ROOT, "output")`; else `DEFAULT_OUTPUT_DIR`.
4. **LLM client construction**: Based on the `ENABLE_LLM_DOC` flag, either an `LLMClient` instance (constructed with default model/API settings) or `None` is produced.
5. **Pipeline dispatch**: `main()` invokes `asyncio.run(process_all_files(project_dir, output_dir, llm_client))`, handing off the resolved paths and client to the async pipeline, which fans out internally (dependency analysis, doc generation, JSON/Mermaid output) and merges back into a single completed coroutine. `main.py` itself does not observe or transform any return value from this call (it returns `None`).

## 3. Outputs

- **Return value**: `main()` returns `None`; it is a pure entry-point/side-effect driver.
- **Side effects**:
  - Log output written to console (WARNING+) and to a rotating log file (via `setup_logging`).
  - All analysis artifacts (dependency JSON, design documents, consolidated JSON, Mermaid graphs) are written to disk under `output_dir`, but this file-writing behavior is performed entirely inside `process_all_files`, not directly by `main.py`.
- **Process exit**: When run as `__main__`, `main()` executes synchronously to completion (or raises an exception) as the program's entry point.

## 4. Key Data Structures

### `argparse.Namespace` (returned by `parse_args`)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_dir` | `str \| None` | User-specified project root; `None` if not passed on CLI. |
| `output_dir` | `str \| None` | User-specified output root; `None` if not passed on CLI. |

### `(project_dir, output_dir)` tuple (returned by `resolve_dirs`)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_dir` (index 0) | `str` | Final resolved root directory of the project to analyze. |
| `output_dir` (index 1) | `str` | Final resolved directory where analysis results will be saved. |

### Config inputs consumed

| Field / Key | Type | Purpose |
|---|---|---|
| `DEFAULT_PROJECT_DIR` | `str` | Fallback project directory when `--project-dir` is omitted. |
| `DEFAULT_OUTPUT_DIR` | `str` | Fallback output directory when neither `--output-dir` nor `--project-dir` is given. |
| `REPO_ROOT` | `str` | Base path used to derive `{REPO_ROOT}/output` when only `--project-dir` is specified. |
| `ENABLE_LLM_DOC` | `bool` | Determines whether `LLMClient()` is instantiated (`LLMClient` instance) or `None`. |

# Error Handling

## 1. Overall Strategy

`main.py` itself contains no explicit `try/except` blocks; it acts purely as a thin orchestration layer (argument parsing → directory resolution → client instantiation → pipeline invocation). Its error handling policy is therefore **fail-fast at the entry point, with all recoverable error handling delegated downstream**. Any exception raised in `LLMClient()` construction or in `process_all_files` propagates unhandled up through `main()`, terminating the process with a traceback. Retry logic, rate-limit handling, and graceful degradation (e.g., logging-and-continue on API errors) are implemented inside `LLMClient` and `process_all_files`, not in `main.py`. `setup_logging()` is called first so that any downstream failure is captured in both console (WARNING+) and rotating file logs before/while the process exits.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing/invalid CLI arguments | Malformed `--project-dir`/`--output-dir` usage passed to `argparse` | Handled internally by `argparse` (prints usage, calls `SystemExit`) | No | Process exits immediately before any analysis starts |
| Invalid `LLM_MODEL` config | `ENABLE_LLM_DOC` is True but `LLM_MODEL` is unset when `LLMClient()` is constructed | `LLMClient.__init__` raises `ValueError`; not caught in `main.py` | No | `main()` terminates before `process_all_files` runs |
| LLM API rate limiting | `litellm.RateLimitError` raised during generation inside `LLMClient` | Retried internally by `LLMClient` (wait `RETRY_WAIT`, up to `MAX_RETRIES`), logged; not visible to `main.py` | Yes (internal retry) / falls back to `None` on exhaustion | No propagation to `main.py`; design doc generation for that item degrades gracefully upstream |
| LLM API failure (non-rate-limit) | `openai.APIError` during generation | Logged and returns `None` inside `LLMClient`; not raised to `main.py` | Yes (skipped, not fatal) | Design document for the affected item is skipped; overall run continues |
| Context window exceeded | `ContextWindowExceededError` during generation | Re-raised by `LLMClient`, propagates through `process_all_files` up to `main.py` if unhandled there | No (unless caught downstream) | Could terminate `asyncio.run(process_all_files(...))` call in `main()` |
| Pipeline-level failures (e.g., file I/O, parsing errors in `process_all_files`) | Any unhandled exception inside `process_all_files` or its sub-steps | Not caught in `main.py`; propagates to `asyncio.run` and terminates the program | No | Entire analysis run aborts; no output guaranteed for that invocation |
| Directory resolution edge cases | `--project-dir` given without `--output-dir`, or neither given | Deterministic fallback logic in `resolve_dirs()` (not exception-based) | N/A (not an error) | Ensures consistent output location without needing error handling |

## 3. Design Notes

- `main.py` deliberately avoids wrapping the pipeline call in `try/except`, keeping the entry point simple and relying on the called modules (`LLMClient`, `process_all_files`) to handle recoverable errors (e.g., rate limits, per-file failures) internally.
- `setup_logging()` is invoked as the very first action in `main()` to guarantee that any exception occurring afterward—whether inside argument resolution, LLM client creation, or the async pipeline—is captured by the configured handlers (console WARNING+, rotating file at INFO+).
- The conditional construction of `LLMClient` (`if ENABLE_LLM_DOC else None`) reflects a graceful-degradation design at the configuration level: when LLM documentation generation is disabled, no client is created and no LLM-related errors can occur at all, since `process_all_files` is expected to skip doc generation when `llm_client` is `None`.
- Because `main.py` performs no error translation or wrapping, any failure surfaces with its original exception type and message, preserving full diagnostic context for developers inspecting logs or stack traces.

# Summary

CLI entry point orchestrating project analysis: parses args, resolves dirs, optionally builds LLMClient, runs pipeline. Functions: `parse_args() -> argparse.Namespace`; `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`; `main() -> None`. Consumes settings (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC`: str/bool) and produces `(project_dir, output_dir): tuple[str, str]` passed with an `LLMClient|None` to `process_all_files` via `asyncio.run`.
