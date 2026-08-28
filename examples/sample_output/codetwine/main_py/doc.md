# Design Document: main.py

# Overview & Purpose

`main.py` is the command-line entry point for codetwine. It exists as a thin, standalone orchestration layer that wires together configuration, logging, the optional LLM client, and the analysis pipeline (`process_all_files`), without itself containing any analysis logic. Its responsibilities are:

- Parsing CLI arguments (`--project-dir`, `--output-dir`).
- Resolving the effective project/output directories by combining CLI input with `.env`-derived defaults from `codetwine.config.settings`.
- Initializing application-wide logging via `setup_logging`.
- Conditionally constructing an `LLMClient` based on the `ENABLE_LLM_DOC` flag.
- Invoking the async pipeline `process_all_files` via `asyncio.run`, keeping the rest of the codebase (`pipeline.py`, `llm/client.py`) free of CLI/argument-parsing concerns.

Keeping this in a separate top-level file follows the standard "thin entry point" pattern: it isolates process bootstrapping (arg parsing, logging setup, async loop management) from the reusable library logic in `codetwine/`, so `pipeline.py` and other modules can be imported/tested independently of CLI concerns.

## Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_args()` | none | `argparse.Namespace` | Defines and parses the `--project-dir`/`--output-dir` CLI options. |
| `resolve_dirs(args)` | `args: argparse.Namespace` | `tuple[str, str]` (`project_dir`, `output_dir`) | Resolves final project/output directories, applying `.env` defaults and the special rule that an explicit `--project-dir` without `--output-dir` forces output to `{REPO_ROOT}/output` instead of `DEFAULT_OUTPUT_DIR`. |
| `main()` | none | `None` | Entry point: sets up logging, parses/resolves args, optionally builds `LLMClient`, and runs `process_all_files` via `asyncio.run`. |

## Design Decisions

- **Separation of CLI bootstrapping from core logic**: `main.py` only orchestrates; all real work (dependency graph construction, doc generation, output writing) is delegated to `process_all_files` in `pipeline.py`.
- **Explicit directory-resolution precedence rule**: `resolve_dirs` encodes a deliberate override policy — when `--project-dir` is given without `--output-dir`, `DEFAULT_OUTPUT_DIR` from `.env` is intentionally bypassed in favor of `{REPO_ROOT}/output`, preventing accidental writes to a default output location configured for a different project.
- **Conditional dependency construction**: `LLMClient` is only instantiated when `ENABLE_LLM_DOC` is true, avoiding the `ValueError` raised by `LLMClient.__init__` when no LLM model is configured, and allowing the pipeline to run in a documentation-free mode by passing `None`.
- **Single async execution boundary**: `asyncio.run` is called only once, at the top level in `main()`, keeping the async pipeline (`process_all_files`) as the sole coroutine entry, consistent with `main.py` being a synchronous CLI wrapper around an async core.

# Definition Design Specifications

## `parse_args`

Parses command-line arguments for the CLI entry point using `argparse`.

- Arguments: none (reads from `sys.argv` implicitly via `argparse`).
- Returns: `argparse.Namespace` containing optional `project_dir` and `output_dir` string attributes (both default to `None` when not supplied by the user).
- Exists to isolate CLI argument definitions from the rest of `main()`, keeping the entry point focused on orchestration.
- Both options are optional strings; absence of a flag is represented as `None`, which downstream code (`resolve_dirs`) must handle explicitly rather than relying on argparse defaults, since the actual defaults come from `.env`-derived settings.

## `resolve_dirs`

Determines the effective `project_dir` and `output_dir` by combining CLI arguments with `.env`-derived defaults.

- Arguments: `args` (`argparse.Namespace`) — the parsed CLI arguments from `parse_args()`, expected to expose `project_dir` and `output_dir` attributes (each `str | None`).
- Returns: `tuple[str, str]` — the resolved `(project_dir, output_dir)` pair.
- Exists to encapsulate the precedence rules between CLI flags and configuration defaults, so `main()` does not need to know about `.env` semantics.
- Key design decision: when `--project-dir` is given but `--output-dir` is not, the function deliberately ignores `DEFAULT_OUTPUT_DIR` and falls back to `{REPO_ROOT}/output`. This avoids accidentally writing analysis results for a custom project into an output directory configured for a different (default) project.
- Edge cases: if neither CLI argument is given, both values come entirely from `.env` settings (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`). If `--output-dir` is explicitly provided, it always takes precedence regardless of `--project-dir`.

## `main`

Entry point that wires together logging setup, argument resolution, optional LLM client creation, and the async analysis pipeline.

- Arguments: none.
- Returns: `None`.
- Responsible for initializing global logging first (via `setup_logging`), then resolving directories, then conditionally constructing an `LLMClient` based on the `ENABLE_LLM_DOC` flag, and finally driving the asynchronous `process_all_files` pipeline via `asyncio.run`.
- Design decision: the `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is true, passing `None` otherwise, so that `process_all_files` can run in a documentation-free mode without requiring LLM credentials.
- Precondition: if `ENABLE_LLM_DOC` is true, `LLMClient()` requires a valid model configuration; otherwise it raises `ValueError` during construction, which will propagate uncaught from `main()`.

# Dependency Description

### Dependencies (what this file uses)

`main.py` serves as the CLI entry point for codetwine and depends on the following project-internal modules:

- **`codetwine.config.settings`** (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC`, `REPO_ROOT`): Used to resolve default values for the project and output directories when CLI arguments are omitted, to compute a fallback output path relative to the repository root, and to decide whether LLM-based document generation should be enabled.
- **`codetwine.config.logger`** (`setup_logging`): Used to initialize application-wide logging (console and file handlers) before any processing begins.
- **`codetwine.llm.client`** (`LLMClient`): Used to instantiate the LLM client that will be passed into the pipeline for generating design documents, conditionally created only when `ENABLE_LLM_DOC` is true.
- **`codetwine.pipeline`** (`process_all_files`): Used as the core orchestration function that performs the actual dependency analysis and document generation; `main.py` invokes it asynchronously with the resolved project directory, output directory, and LLM client.

### Dependents (what uses this file)

No dependent information available.

**Direction of dependency:** Unidirectional — `main.py` depends on `codetwine.config.settings`, `codetwine.config.logger`, `codetwine.llm.client`, and `codetwine.pipeline`, acting purely as the top-level entry point that coordinates these modules without being depended upon by them.

# Data Flow

## Input Data Format and Source

| Source | Data | Format |
|---|---|---|
| CLI arguments | `--project-dir`, `--output-dir` | `argparse.Namespace` (optional strings, default `None`) |
| `.env` / environment (via `codetwine.config.settings`) | `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC`, `REPO_ROOT` | typed constants (`str`, `bool`) resolved at import time |

## Main Transformation Flow

```
[CLI args] ──▶ parse_args() ──▶ argparse.Namespace
                                     │
                                     ▼
                        resolve_dirs(args) ──▶ (project_dir, output_dir)
                                     │
                                     ▼
                    ENABLE_LLM_DOC ? LLMClient() : None ──▶ llm_client
                                     │
                                     ▼
              asyncio.run(process_all_files(project_dir, output_dir, llm_client))
```

1. `setup_logging()` initializes logging (no data returned; side effect only).
2. `parse_args()` converts raw CLI input into a `Namespace` with `project_dir` / `output_dir` (possibly `None`).
3. `resolve_dirs()` merges CLI values with settings-derived defaults using precedence rules:
   - `project_dir`: CLI value → else `DEFAULT_PROJECT_DIR`.
   - `output_dir`: CLI value → else (if `project_dir` was explicitly given) `{REPO_ROOT}/output` → else `DEFAULT_OUTPUT_DIR`.
4. The resolved `(project_dir, output_dir)` strings and an `LLMClient | None` instance are passed as arguments into `process_all_files`, which performs the actual analysis and file I/O (this file does not process its output itself).

## Output Data Format and Destination

- This file produces no direct return value or file output itself.
- It delegates all output generation (dependency JSON, design documents, consolidated JSON/SQLite, Mermaid graphs) to `process_all_files`, writing under the resolved `output_dir`.
- `main()` returns `None`; the process terminates after the awaited pipeline completes.

## Main Data Structures

| Structure | Fields / Type | Purpose |
|---|---|---|
| `argparse.Namespace` (from `parse_args`) | `project_dir: str \| None`, `output_dir: str \| None` | Holds raw CLI input before resolution |
| `(project_dir, output_dir)` tuple (from `resolve_dirs`) | `project_dir: str`, `output_dir: str` | Final resolved directories passed to the pipeline |
| `llm_client` | `LLMClient` instance or `None` | Encapsulates LLM configuration (model, API key, base URL); `None` disables doc generation downstream |

# Error Handling

`main.py` itself contains no explicit try/except blocks; it acts purely as a thin entry point that wires together configuration, argument parsing, and the async pipeline. Its error handling policy is therefore **fail-fast by delegation**: any exception raised during argument resolution, LLM client initialization, or pipeline execution propagates uncaught up to the Python runtime, terminating the process with a traceback. No error is swallowed or logged at this layer beyond what `setup_logging()` enables for downstream modules.

| Error Type | Handling | Impact |
|---|---|---|
| Invalid/missing `LLM_MODEL` config (raised by `LLMClient.__init__` as `ValueError`) | Not caught in `main.py`; propagates immediately when `ENABLE_LLM_DOC` is true | Program aborts before any analysis starts |
| Invalid `KNOWLEDGE_FORMAT` setting (raised by `process_all_files` as `ValueError`) | Not caught; propagates from within `asyncio.run(...)` | Program aborts after logging/setup has occurred but before/during processing |
| Errors during dependency analysis or doc generation inside `process_all_files` (e.g., I/O errors, LLM call failures) | Fully delegated to `codetwine.pipeline`; `main.py` does not intercept them | Behavior depends entirely on `pipeline.py`'s internal handling; if unhandled there, the whole run fails |
| Argument parsing errors (`argparse`) | Handled by `argparse` itself (prints usage and exits) | Process exits with a non-zero status before any pipeline logic runs |

**Design considerations:**
- Error handling responsibility is intentionally pushed down to the modules that own the relevant logic (`LLMClient`, `process_all_files`), keeping `main.py` minimal and declarative.
- `setup_logging()` is called first, ensuring that any warnings/errors emitted by downstream modules (e.g., LLM retry warnings, API errors logged via `logger.error`) are captured to both console and rotating log file even though `main.py` does not itself log anything.
- Because `main.py` performs no recovery or fallback, the overall strategy is fail-fast at the top level: any unrecoverable condition in configuration or pipeline processing results in immediate termination rather than partial/degraded output being silently produced by this file.

# Summary

main.py is codetwine's thin CLI entry point: it parses --project-dir/--output-dir, resolves final directories against .env defaults (with special precedence rules), sets up logging, conditionally builds an LLMClient based on ENABLE_LLM_DOC, and runs process_all_files via asyncio.run. Public interfaces: parse_args()→Namespace, resolve_dirs(args)→(project_dir, output_dir), main()→None. It contains no analysis logic, no error handling (fail-fast), and produces no direct output—everything is delegated to pipeline.py.
