# Design Document: main.py

# Overview & Purpose

`main.py` is the command-line entry point for CodeTwine. It exists as a thin, standalone orchestration layer that wires together configuration, logging, the LLM client, and the analysis pipeline without containing any business logic itself. Its sole responsibilities are: parsing CLI arguments, resolving effective project/output directories (combining CLI overrides with `.env`-provided defaults), initializing logging, conditionally constructing an `LLMClient`, and invoking the asynchronous `process_all_files` pipeline. By isolating these concerns in a dedicated script, the project keeps the actual analysis logic (in `codetwine/pipeline.py`) decoupled from process bootstrapping and CLI handling.

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_args()` | none | `argparse.Namespace` | Defines and parses the `--project-dir` / `--output-dir` CLI options. |
| `resolve_dirs(args)` | `args: argparse.Namespace` | `tuple[str, str]` (`project_dir`, `output_dir`) | Determines final project/output directories, applying the special rule that specifying only `--project-dir` forces the output dir to `{REPO_ROOT}/output` instead of `DEFAULT_OUTPUT_DIR`. |
| `main()` | none | `None` | Entry point: sets up logging, resolves directories, builds an `LLMClient` if enabled, and runs `process_all_files` via `asyncio.run`. |

### Design Decisions

- **Separation of concerns**: `main.py` only handles CLI/argument resolution and startup wiring; all actual dependency analysis and doc generation logic is delegated to `process_all_files` in `codetwine/pipeline.py`.
- **Conditional dependency injection**: `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`, otherwise `None` is passed to `process_all_files`, allowing the pipeline to skip LLM-based documentation generation without needing a valid LLM configuration.
- **Explicit override precedence for output directory**: `resolve_dirs` encodes a deliberate rule that CLI-specified `--project-dir` without `--output-dir` bypasses `.env`'s `DEFAULT_OUTPUT_DIR` in favor of a fixed `{REPO_ROOT}/output` path, preventing accidental mixing of results from ad-hoc project runs with the default output location.
- **Async execution boundary**: `main()` bridges the synchronous CLI entry point to the asynchronous pipeline via `asyncio.run`, keeping the async design confined to `process_all_files` and its internals.

# Definition Design Specifications

## `parse_args`

Parses command-line arguments for the `main.py` entry point using `argparse`.

- **Arguments**: None (reads from `sys.argv` implicitly via `argparse`).
- **Returns**: `argparse.Namespace` containing optional `project_dir` and `output_dir` attributes (both `None` if not supplied on the command line).
- **Design intent**: Isolates CLI parsing from the rest of `main()` so that argument definitions (help text, flags) are centralized and testable independently of directory-resolution logic.
- **Constraints**: Both `--project-dir` and `--output-dir` are optional; no validation of path existence is performed here—that responsibility is deferred to downstream consumers (`resolve_dirs`, `process_all_files`).

## `resolve_dirs`

Determines the effective `project_dir` and `output_dir` values by combining CLI arguments with `.env`-based defaults.

- **Arguments**: `args: argparse.Namespace` — the parsed CLI arguments from `parse_args()`, expected to expose `project_dir` and `output_dir` attributes.
- **Returns**: `tuple[str, str]` — `(project_dir, output_dir)`, the resolved absolute/relative directory paths to use for analysis and output.
- **Design intent**: Encapsulates the precedence rules between explicit CLI flags and configured defaults (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`), so `main()` stays a thin orchestrator.
- **Important design decision**: When `--project-dir` is given but `--output-dir` is not, `DEFAULT_OUTPUT_DIR` from `.env` is deliberately ignored in favor of `{REPO_ROOT}/output`. This prevents accidentally writing analysis results for a custom project into an output location configured for a different (default) project, avoiding cross-project output collisions.
- **Edge cases**: If neither CLI argument is provided, both defaults (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`) are used unchanged. If only `--output-dir` is provided, `project_dir` falls back to `DEFAULT_PROJECT_DIR` while `output_dir` uses the explicit CLI value.

## `main`

Entry point that wires together logging setup, argument resolution, LLM client construction, and the async analysis pipeline.

- **Arguments**: None (invoked as the script entry point).
- **Returns**: `None`.
- **Design intent**: Acts as the top-level orchestrator, keeping `main.py` free of business logic by delegating actual work to `process_all_files` and configuration/parsing to helper functions.
- **Important design decisions**:
  - Calls `setup_logging()` first, before any other operation, so that all subsequent code paths (including argument errors) are captured in logs.
  - Conditionally constructs `LLMClient()` only when `ENABLE_LLM_DOC` is true, otherwise passes `None`, allowing the pipeline to skip design-document generation without requiring a valid LLM configuration.
  - Uses `asyncio.run(...)` as the single point where the async pipeline is driven, keeping `main()` itself synchronous for compatibility with standard script execution (`uv run main.py`).
- **Constraints/edge cases**: If `ENABLE_LLM_DOC` is true but LLM configuration (e.g., model name) is invalid, `LLMClient()` construction raises `ValueError`, which propagates uncaught out of `main()`.

# Dependency Description

### Dependencies (what this file uses)

`main.py` serves as the CLI entry point for CodeTwine and relies on the following project-internal modules:

- **codetwine/config/settings.py** (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC`, `REPO_ROOT`): Used to resolve configuration defaults. `DEFAULT_PROJECT_DIR` and `DEFAULT_OUTPUT_DIR` provide fallback values when the corresponding CLI arguments are not supplied, `REPO_ROOT` is used to compute a default output path when only `--project-dir` is given, and `ENABLE_LLM_DOC` determines whether an `LLMClient` instance is created at all.
- **codetwine/config/logger.py** (`setup_logging`): Used to initialize console and file logging before any processing begins, ensuring consistent log output across the application.
- **codetwine/llm/client.py** (`LLMClient`): Instantiated conditionally (based on `ENABLE_LLM_DOC`) to provide the LLM access object that is passed into the processing pipeline for design document generation.
- **codetwine/pipeline.py** (`process_all_files`): The core orchestration function that performs the actual dependency analysis and document generation; `main.py` invokes it asynchronously with the resolved project directory, output directory, and LLM client.

### Dependents (what uses this file)

No dependent information available.

### Direction of Dependency

The dependency relationship is unidirectional: `main.py` depends on `codetwine/config/settings.py`, `codetwine/config/logger.py`, `codetwine/llm/client.py`, and `codetwine/pipeline.py` to perform its setup and orchestration duties, while none of these modules depend back on `main.py`. As the entry point of the application, `main.py` sits at the top of the dependency chain, consuming lower-level modules but not being consumed by any other project file.

# Data Flow

**Input**
| Source | Data | Format |
|---|---|---|
| CLI | `--project-dir`, `--output-dir` | Optional string args parsed by `argparse` into `argparse.Namespace` |
| `.env` / settings | `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC` | Config values loaded via `codetwine.config.settings` |

**Transformation Flow**

```
CLI argv
   │  parse_args()
   ▼
argparse.Namespace(project_dir, output_dir)
   │  resolve_dirs()
   │    - fills missing project_dir from DEFAULT_PROJECT_DIR
   │    - fills missing output_dir using precedence:
   │      explicit output_dir > (REPO_ROOT/output if project_dir given) > DEFAULT_OUTPUT_DIR
   ▼
(project_dir: str, output_dir: str)
   │
   │  ENABLE_LLM_DOC flag decides:
   ▼
llm_client = LLMClient() | None
   │
   ▼
asyncio.run(process_all_files(project_dir, output_dir, llm_client))
```

- `setup_logging()` is invoked first (side-effect only, no data returned) to configure root logger handlers before any processing occurs.
- `resolve_dirs` is a pure function: it takes the parsed CLI namespace and applies conditional fallback logic (no external I/O) to produce a resolved `(project_dir, output_dir)` tuple.
- `LLMClient` instantiation is conditional on `ENABLE_LLM_DOC`; if disabled, `None` is passed downstream, and `process_all_files` (per its own design) will skip design-document generation accordingly.
- The actual heavy-lifting data transformation (dependency graph extraction, doc generation, JSON/Mermaid output) happens entirely inside `process_all_files`, which this file only invokes with resolved parameters.

**Output**
| Destination | Data | Notes |
|---|---|---|
| Filesystem (`output_dir`) | Dependency JSON, design docs, consolidated JSON, Mermaid graphs | Produced internally by `process_all_files`; this file does not directly write files |
| Log file / console | Log messages | Via handlers configured by `setup_logging()` |
| Process exit | None (side-effect only) | `main()` returns `None`; execution completes after `asyncio.run` finishes |

**Key Data Structures**

| Structure | Fields | Purpose |
|---|---|---|
| `argparse.Namespace` | `project_dir: str \| None`, `output_dir: str \| None` | Raw CLI input container |
| `(project_dir, output_dir)` tuple | two `str` values | Fully resolved directories passed to the pipeline |
| `llm_client` | `LLMClient` instance or `None` | Encapsulates model/API config; passed by reference into `process_all_files` for optional LLM-based doc generation |

This file itself holds no persistent state or complex data structures — it acts purely as a CLI-to-pipeline adapter, resolving configuration inputs and delegating all data transformation to `process_all_files`.

# Error Handling

`main.py` follows a **fail-fast** strategy: it performs no explicit error handling of its own and relies entirely on unhandled exceptions propagating up from `parse_args`, `resolve_dirs`, `LLMClient` construction, and `process_all_files`. Any failure immediately terminates the process with a traceback. This is consistent with its role as a thin CLI entry point — configuration resolution, LLM setup, and analysis logic (including retries and per-file fail-soft handling) are delegated to `settings.py`, `LLMClient`, and `pipeline.py`, so `main.py` itself does not need to intercept or recover from errors.

| Error Type | Handling | Impact |
|---|---|---|
| Invalid/missing CLI arguments | Delegated to `argparse` (`parse_args`); no custom validation in `main.py` | Program exits via `argparse`'s built-in error/usage handling |
| Missing `LLM_MODEL` when `ENABLE_LLM_DOC` is true | `LLMClient.__init__` raises `ValueError`; not caught in `main.py` | Program crashes at startup before any analysis runs |
| Errors during dependency analysis / doc generation (`process_all_files`) | Not caught in `main.py`; any exception raised inside propagates through `asyncio.run` | Entire run aborts; no partial-result handling at this layer |
| Configuration issues in `settings.py` (e.g., missing required env vars) | Handled by `get_config_value` at import time, before `main()` executes | Import-time failure prevents `main.py` from running at all |
| Logging setup failure (`setup_logging`) | No error handling; exceptions (e.g., filesystem issues creating log directory) propagate | Program exits before argument parsing or analysis begins |

**Design considerations:** `main.py` intentionally centralizes no error-recovery logic, keeping the entry point simple and predictable. Graceful degradation and retry logic (e.g., LLM rate-limit retries, per-file fail-soft processing) are pushed down into `LLMClient` and `pipeline.py`, so failures that reach `main.py` are treated as unrecoverable at the CLI level and are allowed to fail fast with a full stack trace for diagnostics.

# Summary

main.py is CodeTwine's CLI entry point, orchestrating startup without business logic. It provides parse_args() (CLI parsing), resolve_dirs(args) (merges CLI args with .env defaults, with special rule: --project-dir alone forces output to REPO_ROOT/output), and main() (sets up logging, resolves dirs, conditionally builds LLMClient based on ENABLE_LLM_DOC, then runs process_all_files via asyncio.run). Key data: argparse.Namespace, resolved (project_dir, output_dir) tuple, optional LLMClient. Depends on settings.py, logger.py, LLMClient, and pipeline.py; fails fast with no internal error handling.
