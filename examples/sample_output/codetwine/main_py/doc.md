# Design Document: main.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Serves as the command-line entry point for codetwine, parsing user arguments, resolving directory paths, and launching the full project analysis pipeline.

## 2. When to Use This Module

- **Running the tool from the command line**: Invoke `main()` (via `uv run main.py`) to trigger dependency analysis and design document generation for a target project. It accepts `--project-dir` and `--output-dir` flags to override `.env` defaults.
- **Resolving effective project and output directories**: Call `resolve_dirs(args)` when you need to apply the directory-resolution logic (CLI args → `.env` defaults → fallback to `{REPO_ROOT}/output`) without running the full pipeline.
- **Parsing CLI arguments in isolation**: Call `parse_args()` to obtain the `argparse.Namespace` object representing `--project-dir` and `--output-dir` values.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_args` | none | `argparse.Namespace` | Parses `--project-dir` and `--output-dir` CLI arguments and returns the result. |
| `resolve_dirs` | `args: argparse.Namespace` | `tuple[str, str]` | Determines the effective `(project_dir, output_dir)` by combining CLI arguments with `.env` defaults; falls back to `{REPO_ROOT}/output` when only `--project-dir` is supplied. |
| `main` | none | `None` | Initializes logging, resolves directories, optionally constructs `LLMClient`, and runs `process_all_files` via `asyncio.run`. |

## 4. Design Decisions

- **Conditional LLM client instantiation**: `LLMClient` is only constructed when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`. This allows the pipeline to run in a dependency-analysis-only mode without requiring any LLM credentials.
- **Output directory fallback when `--project-dir` is specified alone**: When a user supplies `--project-dir` but omits `--output-dir`, the `DEFAULT_OUTPUT_DIR` from `.env` is intentionally ignored in favor of `{REPO_ROOT}/output`. This prevents results from a custom project from being written into a potentially unrelated `.env`-configured directory.

## Definition Design Specifications

# Definition Design Specifications

---

## `parse_args() -> argparse.Namespace`

| Item | Detail |
|---|---|
| **Return type** | `argparse.Namespace` — an object whose attributes correspond to declared CLI flags |
| **Responsibility** | Declares and parses the two optional CLI flags (`--project-dir`, `--output-dir`) that control which directories the tool reads from and writes to. |
| **When to use** | Called once at program startup inside `main()` before any directory resolution or analysis begins. |
| **Design decisions** | Both flags are intentionally optional at the parser level; defaulting logic is delegated entirely to `resolve_dirs()` rather than encoded as `argparse` defaults, keeping the two concerns separate. |
| **Constraints & edge cases** | Unrecognized flags cause `argparse` to exit with an error. Neither flag is validated for existence on disk at this stage. |

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

| Item | Detail |
|---|---|
| **Parameter** | `args` — the parsed namespace returned by `parse_args()` |
| **Return type** | `tuple[str, str]` — a two-element tuple of `(project_dir, output_dir)`, both as plain string paths |
| **Responsibility** | Implements a three-way priority rule that maps combinations of provided/omitted CLI flags to concrete directory paths, encoding one non-obvious default-override behavior. |
| **When to use** | Called once after `parse_args()` to obtain the final, resolved directory paths before pipeline execution. |

### Priority logic for `output_dir`

| Condition | Resolved `output_dir` |
|---|---|
| `--output-dir` is provided | The explicitly supplied value |
| `--project-dir` is provided, `--output-dir` is omitted | `{REPO_ROOT}/output` (ignores `DEFAULT_OUTPUT_DIR` from `.env`) |
| Neither flag is provided | `DEFAULT_OUTPUT_DIR` from `.env` |

**Design decision:** When a caller supplies a custom project directory but omits an output directory, the function deliberately ignores `DEFAULT_OUTPUT_DIR` from `.env` and falls back to the repository-relative default. This prevents results from a foreign project being written into a `.env`-configured location that was intended for the default project.

**Constraints & edge cases:**
- `project_dir` always resolves to `DEFAULT_PROJECT_DIR` from `.env` when `--project-dir` is omitted; no further validation is performed.
- The returned paths are not checked for existence or write permissions.

---

## `main() -> None`

| Item | Detail |
|---|---|
| **Return type** | `None` |
| **Responsibility** | Serves as the sole entry point: initializes logging, resolves configuration, conditionally constructs an `LLMClient`, and drives the async pipeline to completion. |
| **When to use** | Invoked directly by the Python runtime when the script is executed, or registered as a console-script entry point. |

### Execution sequence

| Action | Details |
|---|---|
| Logging setup | `setup_logging()` is called first so all subsequent code has a configured logger |
| Argument parsing | `parse_args()` then `resolve_dirs()` yield the two directory paths |
| LLM client construction | `LLMClient()` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed |
| Pipeline execution | `asyncio.run(process_all_files(...))` blocks until the entire async pipeline completes |

**Design decisions:**
- `LLMClient` construction is guarded by `ENABLE_LLM_DOC` so that the tool can run without valid LLM credentials when document generation is disabled.
- `asyncio.run()` is used rather than managing an event loop manually, restricting the entry point to a synchronous context and ensuring the loop is cleanly closed on exit.

**Constraints & edge cases:**
- `LLMClient()` will raise `ValueError` if `LLM_MODEL` is unset and `ENABLE_LLM_DOC` is `True`.
- Only one event loop is created; the function is not designed to be called more than once per process.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/main_py/main.py` → `codetwine/config/settings.py` : imports `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` to resolve the project and output directory paths, and `ENABLE_LLM_DOC` to conditionally instantiate the LLM client.

- `codetwine/main_py/main.py` → `codetwine/config/logger.py` : imports `setup_logging` to initialize application-wide logging (console + rotating file handler) at the start of `main()`.

- `codetwine/main_py/main.py` → `codetwine/llm/client.py` : imports `LLMClient` to instantiate the async LLM API wrapper when `ENABLE_LLM_DOC` is `True`, passing the client into the pipeline for design document generation.

- `codetwine/main_py/main.py` → `codetwine/pipeline.py` : imports `process_all_files` to execute the full analysis pipeline — dependency extraction, design document generation, and consolidated output — driven via `asyncio.run`.

## Dependents (modules that import this file)

No dependent information available.

## Dependency Direction

All relationships are **unidirectional**: `codetwine/main_py/main.py` depends on each of the four modules listed above, and none of those modules import back from `main.py`. This file serves strictly as the top-level entry point, consuming configuration, logging, LLM client, and pipeline functionality without exposing any symbols for other modules to import.

## Data Flow

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| CLI arguments (`--project-dir`, `--output-dir`) | `argparse.Namespace` | Optional strings specifying the project root and output destination |
| `DEFAULT_PROJECT_DIR` | `str` (path) | Fallback project directory read from `.env` via `settings.py` |
| `DEFAULT_OUTPUT_DIR` | `str` (path) | Fallback output directory read from `.env` via `settings.py` |
| `REPO_ROOT` | `str` (path) | Absolute path to the repository root, derived from `settings.py` file location |
| `ENABLE_LLM_DOC` | `bool` | Feature flag read from `.env` controlling whether an `LLMClient` is instantiated |

---

## 2. Transformation Overview

```
CLI args (--project-dir, --output-dir)
          │
          ▼
    parse_args()
    → argparse.Namespace
          │
          ▼
    resolve_dirs(args)
    applies precedence rules:
      - args.project_dir  → overrides DEFAULT_PROJECT_DIR
      - args.output_dir   → overrides DEFAULT_OUTPUT_DIR
      - args.project_dir set but not args.output_dir
                          → REPO_ROOT/output (ignores DEFAULT_OUTPUT_DIR)
      - neither set       → DEFAULT_PROJECT_DIR, DEFAULT_OUTPUT_DIR
    → (project_dir: str, output_dir: str)
          │
          ▼
    ENABLE_LLM_DOC ?
      True  → LLMClient()   → llm_client: LLMClient
      False → None          → llm_client: None
          │
          ▼
    asyncio.run(
      process_all_files(project_dir, output_dir, llm_client)
    )
    → all analysis artifacts written to output_dir
```

**Stage 1 — Argument parsing:** Raw CLI strings are parsed into a structured `argparse.Namespace`.

**Stage 2 — Directory resolution:** `resolve_dirs` applies a three-branch precedence rule to produce concrete filesystem paths for both the project root and the output destination.

**Stage 3 — LLM client construction:** The `ENABLE_LLM_DOC` flag gates instantiation of `LLMClient`; the result is either a live client or `None`, passed directly into the pipeline.

**Stage 4 — Pipeline execution:** The resolved paths and optional client are handed off to `process_all_files`, which drives all subsequent analysis, document generation, and file I/O asynchronously. This module does not process the pipeline's results further; control does not return with any value.

---

## 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| Logging configuration | Side effect | `setup_logging()` attaches handlers to the root logger for the lifetime of the process |
| All analysis artifacts | Files on disk | Produced entirely inside `process_all_files`; this module has no direct file writes of its own |

This file has no return values. Its sole observable output beyond logging setup is the side effects triggered by `process_all_files`.

---

## 4. Key Data Structures

### `argparse.Namespace` — produced by `parse_args()`

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `project_dir` | `str \| None` | Value of `--project-dir`; `None` if not supplied |
| `output_dir` | `str \| None` | Value of `--output-dir`; `None` if not supplied |

### resolved directory tuple — produced by `resolve_dirs()`

| Position | Type | Purpose |
|----------|------|---------|
| `[0]` `project_dir` | `str` | Absolute or relative path to the project root to analyze |
| `[1]` `output_dir` | `str` | Absolute or relative path where all output artifacts will be written |

### Precedence table inside `resolve_dirs`

| `args.project_dir` | `args.output_dir` | Resulting `output_dir` |
|--------------------|-------------------|------------------------|
| set | set | `args.output_dir` |
| set | not set | `REPO_ROOT/output` |
| not set | set | `args.output_dir` |
| not set | not set | `DEFAULT_OUTPUT_DIR` |

## Error Handling

# Error Handling

## 1. Overall Strategy

`main.py` adopts a **minimal-intervention, delegate-and-trust** strategy. The entry-point layer performs no explicit error catching of its own; instead, it delegates all substantive processing to downstream modules (`process_all_files`, `LLMClient`, `setup_logging`) and allows unhandled exceptions to propagate naturally, terminating the process. The only conditional logic at this layer is the optional instantiation of `LLMClient` based on the `ENABLE_LLM_DOC` flag, which provides graceful degradation by passing `None` as the client when LLM functionality is disabled.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing or invalid `LLM_MODEL` | `LLMClient()` is instantiated (`ENABLE_LLM_DOC=True`) but `LLM_MODEL` is not configured | `ValueError` raised inside `LLMClient.__init__`; propagates uncaught through `main()` | No | Process terminates immediately |
| LLM rate limit exceeded | LLM API returns a 429 response during `process_all_files` | Handled inside `LLMClient._call_with_retry` with retry logic; `main.py` itself does not intervene | Yes (within retry limits) | Retried up to `MAX_RETRIES`; returns `None` after exhaustion |
| LLM API error | LLM API returns a non-429 error during `process_all_files` | Handled inside `LLMClient`; `main.py` does not intervene | No (for that call) | `None` returned for that generation; pipeline continues |
| LLM disabled (`ENABLE_LLM_DOC=False`) | `ENABLE_LLM_DOC` is falsy at startup | `LLMClient` is not instantiated; `None` is passed to `process_all_files` | Yes (by design) | LLM doc generation is skipped; all other steps proceed |
| Invalid or missing CLI arguments | `--project-dir` or `--output-dir` not provided | Falls back to `DEFAULT_PROJECT_DIR` / `DEFAULT_OUTPUT_DIR` from settings, or `{REPO_ROOT}/output` per `resolve_dirs` logic | Yes | Processing continues with resolved defaults |
| Unhandled exception in pipeline | Any unexpected error raised by `process_all_files` or `setup_logging` | Not caught in `main.py`; propagates to the Python runtime | No | Process terminates with a traceback |

---

## 3. Design Notes

- **Thin entry-point principle:** `main.py` is intentionally kept free of try-except blocks. Error handling responsibility is pushed entirely into the pipeline and client layers, keeping the orchestration layer simple and readable.
- **Conditional instantiation as degradation:** The `LLMClient() if ENABLE_LLM_DOC else None` pattern is the sole resilience mechanism at this layer. It prevents a configuration error from causing a crash when the LLM feature is deliberately turned off, without requiring any exception handling code.
- **Argument resolution as a soft fallback:** `resolve_dirs` encodes a deliberate priority order (CLI argument → project-specific default → `.env` default) to avoid hard failures from missing arguments, treating absent CLI input as a normal operating condition rather than an error.
- **No logging of startup errors:** Because `setup_logging()` is called before any error-prone operations, any failure within `setup_logging` itself would also go uncaught, consistent with the fail-fast posture of the entry point.

## Summary

**main.py** is the CLI entry point that parses arguments, resolves directories, and launches the async analysis pipeline.

**Public functions:**
- `parse_args()` → `argparse.Namespace` (`project_dir: str|None`, `output_dir: str|None`)
- `resolve_dirs(args: argparse.Namespace)` → `tuple[str, str]` (project_dir, output_dir)
- `main()` → `None`

**Key data:** Consumes `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC` from settings; passes resolved `(project_dir, output_dir)` and optional `LLMClient` instance to `process_all_files`.
