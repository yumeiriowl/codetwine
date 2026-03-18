# Design Document: main.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

`main.py` is the command-line entry point for the CodeTwine tool. It exists as a separate file to isolate application startup concerns—argument parsing, directory resolution, logging initialization, and LLM client construction—from the core pipeline logic in `pipeline.py`. Its sole responsibility is to wire together configuration, CLI inputs, and top-level dependencies before delegating all substantive work to `process_all_files`.

## Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_args` | *(none)* | `argparse.Namespace` | Defines and parses `--project-dir` and `--output-dir` CLI arguments |
| `resolve_dirs` | `args: argparse.Namespace` | `tuple[str, str]` | Resolves effective `(project_dir, output_dir)` by combining CLI args with `.env` defaults, applying the rule that `--project-dir` alone overrides `DEFAULT_OUTPUT_DIR` with `{REPO_ROOT}/output` |
| `main` | *(none)* | `None` | Initializes logging, parses arguments, resolves directories, conditionally constructs `LLMClient`, and launches the async pipeline via `asyncio.run` |

## Design Decisions

- **`resolve_dirs` directory-override rule**: When `--project-dir` is specified without `--output-dir`, `DEFAULT_OUTPUT_DIR` from `.env` is intentionally ignored in favor of `{REPO_ROOT}/output`. This prevents outputs from a custom project analysis being silently written to a potentially unrelated `.env`-configured path. This logic is explicit in `resolve_dirs` rather than embedded in `main` to make it independently testable and readable.
- **Conditional `LLMClient` instantiation**: `LLMClient` is constructed only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`. This keeps the LLM dependency opt-out at the entry point rather than buried in the pipeline.
- **`asyncio.run` as the async boundary**: The entire pipeline is async, but `main` is synchronous. `asyncio.run` is the single point where the sync entry point hands off to the async world, keeping the CLI surface straightforward.

## Definition Design Specifications

# Definition Design Specifications

---

## `parse_args() -> argparse.Namespace`

Declares two optional CLI arguments (`--project-dir` and `--output-dir`) and returns the parsed namespace. Exists to isolate argument declaration from resolution logic, keeping `main()` free of argparse boilerplate.

**Constraints:** Both arguments are optional strings; neither has a default value at the parser level—defaults are handled downstream in `resolve_dirs`.

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

**Arguments:**
- `args`: The namespace returned by `parse_args()`, expected to carry `.project_dir` and `.output_dir` attributes (either a string or `None`).

**Returns:** A `(project_dir, output_dir)` tuple of resolved absolute-ish path strings.

Centralizes the three-way precedence logic for determining the working directories, keeping that logic out of `main()`. The design decision here is that `--output-dir` and `--project-dir` are not symmetric: when `--project-dir` is supplied without `--output-dir`, the function deliberately ignores `DEFAULT_OUTPUT_DIR` from `.env` and falls back to `{REPO_ROOT}/output`. This prevents a user-specified project from accidentally polluting an env-configured output path. Only when neither CLI argument is given does `DEFAULT_OUTPUT_DIR` apply.

**Edge cases:**
- If both `args.project_dir` and `args.output_dir` are `None`, both defaults come from the `.env`-derived settings constants.
- If `args.project_dir` is provided but `args.output_dir` is `None`, `DEFAULT_OUTPUT_DIR` is bypassed entirely in favor of `os.path.join(REPO_ROOT, "output")`.

---

## `main() -> None`

Entry point that wires together logging, argument parsing, directory resolution, optional `LLMClient` construction, and the async pipeline invocation. Exists as the single coordinating function that composes all top-level dependencies before handing control to `process_all_files`.

**Design decisions:**
- `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`, letting the pipeline skip doc generation without requiring `main()` to know the details of that skip logic.
- `asyncio.run` is called here rather than inside the pipeline, keeping `process_all_files` a pure coroutine and `main()` the sole sync/async boundary.

**Constraints:** `setup_logging()` must be called before any other operation so that all downstream loggers inherit the configured handlers from startup.

## Dependency Description

# Dependency Description

## Dependencies (what this file uses)

- **codetwine/config/settings.py** — Provides four configuration constants required at startup: `DEFAULT_PROJECT_DIR` and `DEFAULT_OUTPUT_DIR` supply fallback path values when CLI arguments are omitted, `REPO_ROOT` is used to construct a default output path when only `--project-dir` is explicitly supplied, and `ENABLE_LLM_DOC` controls whether an `LLMClient` instance is created or skipped entirely.

- **codetwine/config/logging.py** — Provides `setup_logging()`, which is called once at the very start of `main()` to initialize the package-wide logging infrastructure (console and rotating file handlers) before any other processing begins.

- **codetwine/llm/client.py** — Provides `LLMClient`, which is instantiated conditionally (only when `ENABLE_LLM_DOC` is `True`) and passed into the pipeline to enable LLM-based design document generation.

- **codetwine/pipeline.py** — Provides `process_all_files()`, the top-level async orchestrator. `main()` delegates all analysis work—dependency extraction, document generation, and consolidated output writing—to this function, supplying the resolved project directory, output directory, and optional LLM client.

## Dependents (what uses this file)

No dependent information available.

## Data Flow

# Data Flow

## Input Data

| Source | Format | Description |
|--------|--------|-------------|
| CLI (`sys.argv`) | `--project-dir DIR`, `--output-dir DIR` | Optional flags parsed by `argparse` |
| `.env` / environment variables | Scalar values | `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC` read from `settings.py` |

## Transformation Flow

```
CLI args (--project-dir, --output-dir)
        │
        ▼
   parse_args()
   → argparse.Namespace {project_dir, output_dir}
        │
        ▼
   resolve_dirs(args)
   → (project_dir: str, output_dir: str)
        │
        │  Resolution priority:
        │  project_dir = args.project_dir OR DEFAULT_PROJECT_DIR
        │  output_dir  = args.output_dir  (explicit)
        │              OR {REPO_ROOT}/output (only --project-dir given)
        │              OR DEFAULT_OUTPUT_DIR (neither flag given)
        │
        ▼
   LLMClient()  ← constructed only if ENABLE_LLM_DOC is True
   else None
        │
        ▼
   process_all_files(project_dir, output_dir, llm_client)
   [async, delegates all further processing to pipeline.py]
```

## Key Data Structures

| Name | Type | Purpose |
|------|------|---------|
| `args` | `argparse.Namespace` | Holds raw CLI flag values (`project_dir`, `output_dir`); `None` when a flag is omitted |
| `project_dir` | `str` | Resolved absolute/relative path to the project root to analyze |
| `output_dir` | `str` | Resolved path where all analysis artifacts are written |
| `llm_client` | `LLMClient \| None` | Passed to the pipeline; `None` disables LLM doc generation |

## Output

All outputs from this file are passed as arguments into `process_all_files`; no files or data structures are written directly by `main.py` itself. Artifact generation is entirely delegated to `codetwine/pipeline.py`.

## `resolve_dirs` Logic Summary

```
┌─────────────────────┬──────────────────────────────────────────────┐
│ CLI flags provided  │ output_dir resolution                        │
├─────────────────────┼──────────────────────────────────────────────┤
│ --output-dir only   │ args.output_dir                              │
│ --project-dir only  │ {REPO_ROOT}/output  (DEFAULT_OUTPUT_DIR ignored) │
│ both flags          │ args.output_dir                              │
│ neither flag        │ DEFAULT_OUTPUT_DIR (from .env)               │
└─────────────────────┴──────────────────────────────────────────────┘
```

## Error Handling

# Error Handling

## Overall Strategy

`main.py` adopts a **fail-fast** strategy at the entry-point level. The file contains no explicit exception handling constructs; errors that arise during argument parsing, directory resolution, LLM client initialization, or pipeline execution propagate unhandled to the Python runtime, terminating the process immediately. Error resilience is intentionally delegated to the dependency layers (`LLMClient`, `process_all_files`) rather than absorbed here.

## Main Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Invalid or missing CLI arguments | Delegated to `argparse`; invalid arguments cause automatic usage output and process exit | Process terminates before any analysis begins |
| Missing or invalid LLM configuration (e.g., unset `LLM_MODEL`) | `LLMClient.__init__` raises `ValueError`; not caught here, propagates to runtime | Process terminates at startup if `ENABLE_LLM_DOC` is `True` |
| `ENABLE_LLM_DOC` is `False` | Conditional instantiation: `LLMClient()` is never called; `None` is passed to the pipeline | LLM-related errors are entirely avoided; pipeline runs without doc generation |
| Pipeline execution errors | Not caught; any exception from `asyncio.run(process_all_files(...))` propagates to the runtime | Process terminates; partial outputs may exist in the output directory |
| Directory resolution errors (e.g., inaccessible paths) | Not caught; OS-level errors surface through the pipeline when paths are first used | Process terminates when the pipeline attempts to access the invalid path |

## Design Considerations

The absence of top-level exception handling in `main.py` is a deliberate boundary: this file's responsibility is limited to wiring together configuration, the LLM client, and the pipeline. Each dependency layer carries its own error policy—`LLMClient` handles rate limits and API errors internally, and `process_all_files` manages file-level resilience—so catching exceptions at this layer would risk masking meaningful failure signals. The `ENABLE_LLM_DOC` flag serves as the sole preventive guard, avoiding an entire class of LLM-related failures when the feature is disabled.

## Summary

**main.py** is the CLI entry point for CodeTwine, responsible for startup wiring: argument parsing, directory resolution, logging initialization, and optional LLM client construction, before delegating all pipeline work to `process_all_files`.

**Public interfaces:** `parse_args()` returns an `argparse.Namespace` with optional `--project-dir` and `--output-dir`; `resolve_dirs(args)` returns a `(project_dir, output_dir)` tuple using a three-way precedence rule; `main()` composes all dependencies and invokes the async pipeline.

**Key data structures:** `argparse.Namespace` (raw CLI values), `str` paths for project/output dirs, and `LLMClient | None` (conditionally instantiated based on `ENABLE_LLM_DOC`).
