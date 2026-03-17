# Design Document: main.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

`main.py` is the command-line entry point for the codetwine application. It exists as a separate file to isolate all CLI concerns — argument parsing, directory resolution, and top-level initialization — from the core analysis pipeline. Its sole responsibilities are:

1. Parsing command-line arguments (`--project-dir`, `--output-dir`)
2. Resolving effective directory paths by combining CLI arguments with `.env`-sourced defaults
3. Initializing logging and an optional `LLMClient`
4. Delegating all analysis work to `process_all_files` via `asyncio.run`

It contains no analysis logic itself; every substantive operation is performed by imported modules.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_args` | — | `argparse.Namespace` | Defines and parses the `--project-dir` and `--output-dir` CLI flags |
| `resolve_dirs` | `args: argparse.Namespace` | `tuple[str, str]` | Determines effective `(project_dir, output_dir)` by applying precedence rules over CLI args and `.env` defaults |
| `main` | — | `None` | Top-level entry point: initializes logging, resolves config, constructs `LLMClient` if enabled, and runs the async pipeline |

---

## Design Decisions

**Directory resolution precedence in `resolve_dirs`:**
An explicit rule governs `output_dir` selection: if `--project-dir` is provided without `--output-dir`, the `DEFAULT_OUTPUT_DIR` from `.env` is ignored and `{REPO_ROOT}/output` is used as the fallback instead. This prevents `.env`-configured output paths intended for a different project from receiving results when the user overrides the project directory. The three cases are:

| `--output-dir` | `--project-dir` | Effective `output_dir` |
|---|---|---|
| provided | any | CLI value |
| omitted | provided | `{REPO_ROOT}/output` |
| omitted | omitted | `DEFAULT_OUTPUT_DIR` from `.env` |

**Conditional LLM client construction:** `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`. This keeps the entry point responsible for the feature-flag check rather than scattering it across downstream modules.

**Async delegation via `asyncio.run`:** `main` is synchronous, and the pipeline is async. `asyncio.run` is called once at the top level, keeping the event loop lifecycle entirely within `main.py` and allowing all downstream code to be written as plain coroutines.

## Definition Design Specifications

# Definition Design Specifications

---

## `parse_args() -> argparse.Namespace`

Defines and parses the two CLI flags `--project-dir` and `--output-dir`. Both arguments are optional strings with no defaults at the parser level; defaults are resolved later in `resolve_dirs` so that the distinction between "user omitted the flag" and "user provided a value" is preserved. Returns the raw `Namespace` object for downstream consumption.

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

**Arguments**
- `args`: The `Namespace` returned by `parse_args()`, expected to carry `.project_dir` and `.output_dir` attributes (either a string or `None`).

**Returns**
- A `(project_dir, output_dir)` tuple of resolved absolute or relative path strings.

**Responsibility**
Centralizes the precedence logic for determining which directories to use, decoupling it from both argument parsing and the main execution path.

**Design decisions**

| Scenario | `project_dir` result | `output_dir` result |
|---|---|---|
| Neither flag given | `DEFAULT_PROJECT_DIR` (.env) | `DEFAULT_OUTPUT_DIR` (.env) |
| `--project-dir` only | supplied value | `{REPO_ROOT}/output` (hardcoded fallback) |
| `--output-dir` only | `DEFAULT_PROJECT_DIR` (.env) | supplied value |
| Both flags given | supplied value | supplied value |

The middle row is the critical design choice: when the user explicitly names a project directory but omits an output directory, the `.env` `DEFAULT_OUTPUT_DIR` is intentionally bypassed in favour of `{REPO_ROOT}/output`. This prevents ad-hoc project analyses from polluting a potentially project-specific output path that was configured in `.env` for the default project.

**Constraints**
- No path existence validation is performed; callers downstream are responsible for creating directories.

---

## `main() -> None`

**Responsibility**
Serves as the application entry point, wiring together logging setup, argument resolution, optional `LLMClient` construction, and the async pipeline invocation in the correct order.

**Design decisions**
- `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`, which handles the absent client gracefully. This keeps the feature flag's effect contained to a single conditional expression at the top level rather than scattered through lower layers.
- `asyncio.run` is called here rather than inside `process_all_files`, keeping the async boundary explicit at the entry point and leaving `process_all_files` as a plain coroutine that can be driven by any event loop in tests or alternative entry points.
- `setup_logging()` is called before any other work so that all downstream log calls (including those inside argument resolution and client construction) are captured.

**Constraints**
- Must be called from a synchronous context; it creates its own event loop via `asyncio.run` and will raise if an event loop is already running.
- Has no return value; all results are side-effects written to the output directory by `process_all_files`.

## Dependency Description

# Dependency Description

## Dependencies (what this file uses)

- **codetwine/config/settings.py** (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC`): Used to supply default values for the project and output directory paths when CLI arguments are omitted, and to determine whether LLM-based document generation should be enabled at runtime.

- **codetwine/config/logging.py** (`setup_logging`): Called once at application startup to initialize the root logger with console and rotating file handlers before any other processing begins.

- **codetwine/llm/client.py** (`LLMClient`): Instantiated conditionally when `ENABLE_LLM_DOC` is `True` and passed to the pipeline as the LLM API wrapper responsible for generating design documents.

- **codetwine/pipeline.py** (`process_all_files`): The core orchestration function invoked via `asyncio.run`; receives the resolved project directory, output directory, and LLM client to execute the full analysis and output pipeline.

## Dependents (what uses this file)

No dependent information available.

### Direction of Dependency

All dependencies flow unidirectionally into `main.py`. It consumes configuration constants, logging setup, the LLM client, and the pipeline orchestrator, but none of those modules import from or reference `main.py` in return.

## Data Flow

# Data Flow

## Input Sources

| Source | Type | Description |
|--------|------|-------------|
| CLI arguments (`--project-dir`, `--output-dir`) | `argparse.Namespace` | Optional overrides for project and output directories |
| Environment / `.env` via `settings.py` | scalar constants | `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC`, `REPO_ROOT` |

## Transformation Flow

```
CLI args (--project-dir, --output-dir)
        │
        ▼
   parse_args()
   → argparse.Namespace { project_dir, output_dir }
        │
        ▼
   resolve_dirs(args)
   → (project_dir: str, output_dir: str)
        │  Priority rules applied:
        │  • args.project_dir  || DEFAULT_PROJECT_DIR
        │  • args.output_dir   → use as-is
        │  • args.project_dir only (no --output-dir) → REPO_ROOT/output
        │  • neither flag      → DEFAULT_OUTPUT_DIR
        │
        ▼
   ENABLE_LLM_DOC ──→ LLMClient() or None
        │
        ▼
   process_all_files(project_dir, output_dir, llm_client)
   (async, delegated entirely to pipeline.py)
```

## Key Data Structures in This File

| Variable | Type | Purpose |
|----------|------|---------|
| `args` | `argparse.Namespace` | Holds raw CLI-supplied strings for `project_dir` and `output_dir`; either field may be `None` if not provided |
| `project_dir` | `str` | Resolved absolute-or-relative path to the project root to analyze |
| `output_dir` | `str` | Resolved path where analysis results will be written |
| `llm_client` | `LLMClient \| None` | Instantiated only when `ENABLE_LLM_DOC` is truthy; passed as-is to the pipeline |

## Output

`main()` produces no return value. All outputs are side effects delegated to `process_all_files` in `pipeline.py`, which writes dependency JSON, design documents, consolidated JSON, and Mermaid graphs under `output_dir/<project_name>/`.

## `resolve_dirs` Priority Logic

```
args.output_dir set?  ──Yes──▶ use args.output_dir
        │ No
        ▼
args.project_dir set? ──Yes──▶ use REPO_ROOT/output
        │ No
        ▼
        use DEFAULT_OUTPUT_DIR   (from .env)
```

## Error Handling

# Error Handling

## Overall Strategy

`main.py` adopts a **fail-fast** strategy at the entry-point level. It performs no local exception handling; all errors propagate naturally to the Python runtime. The file delegates substantive work entirely to imported modules (`process_all_files`, `LLMClient`, `setup_logging`, `resolve_dirs`), relying on those layers to enforce their own error boundaries. The entry point itself acts as a thin coordinator with no defensive wrapping.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Missing or invalid `LLM_MODEL` configuration | Raised as `ValueError` inside `LLMClient.__init__()` upon instantiation; not caught in `main.py` | Process terminates immediately with an unhandled exception |
| LLM API failures (rate limits, API errors) | Handled within `LLMClient._call_with_retry()`; returns `None` to callers | LLM document generation may be skipped per file, but does not surface to `main.py` |
| `ENABLE_LLM_DOC=False` | `llm_client` is set to `None`; `process_all_files` handles the absent client | LLM generation is bypassed entirely; pipeline continues normally |
| Errors during pipeline execution | Not caught in `main.py`; propagate from `process_all_files` | Process terminates; partial output may exist in the output directory |
| Argument parsing errors | Handled by `argparse` (prints usage and exits) | Process terminates with a usage message before any analysis begins |

## Design Considerations

The entry point intentionally contains no `try/except` blocks, reflecting a design choice to keep `main.py` as a minimal coordinator. Error resilience is the responsibility of the pipeline and client layers, not the entry point. The conditional instantiation of `LLMClient` (`LLMClient() if ENABLE_LLM_DOC else None`) is the only explicit error-avoidance decision made at this layer, preventing unnecessary initialization failures when LLM functionality is disabled by configuration.

## Summary

**main.py** is the CLI entry point for codetwine. It defines three public functions: `parse_args` (parses `--project-dir`/`--output-dir` flags), `resolve_dirs` (applies precedence rules to determine effective directory paths), and `main` (top-level coordinator). Key data structures are `args` (raw `argparse.Namespace`), `project_dir`/`output_dir` (resolved path strings), and `llm_client` (`LLMClient` or `None`). It initializes logging, resolves config, conditionally instantiates `LLMClient` based on `ENABLE_LLM_DOC`, then delegates the full async pipeline to `process_all_files` via `asyncio.run`. Contains no analysis logic or exception handling.
