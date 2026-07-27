# Design Document: main.py

# Overview & Purpose

`main.py` is the CLI entry point of the codetwine tool. It exists as a separate, minimal file to decouple process bootstrapping (argument parsing, logging setup, directory resolution) from the actual analysis/documentation-generation logic, which is delegated entirely to `codetwine.pipeline.process_all_files`. This keeps orchestration logic testable and reusable while `main.py` remains a thin, script-like wrapper responsible only for wiring CLI input to the pipeline.

Its responsibilities are:
- Parse command-line arguments (`--project-dir`, `--output-dir`).
- Resolve the effective project/output directories by combining CLI arguments with `.env`-driven defaults (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`).
- Initialize logging via `setup_logging()`.
- Conditionally construct an `LLMClient` based on the `ENABLE_LLM_DOC` feature flag.
- Invoke the async pipeline (`process_all_files`) via `asyncio.run`.

### Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `parse_args()` | none | `argparse.Namespace` | Defines and parses the `--project-dir` / `--output-dir` CLI options. |
| `resolve_dirs(args)` | `args: argparse.Namespace` | `tuple[str, str]` (`project_dir`, `output_dir`) | Determines effective project and output directories from CLI args and `.env` defaults, with special-case fallback when only `--project-dir` is given. |
| `main()` | none | `None` | Entry point: sets up logging, resolves args/dirs, builds `LLMClient` if enabled, and runs the async analysis pipeline. |

### Design Decisions

- **Thin orchestrator / delegation pattern**: `main.py` contains no analysis logic itself; all substantive work is delegated to `process_all_files` in `pipeline.py`, keeping the entry point simple and focused on setup/wiring.
- **Explicit directory resolution rule**: `resolve_dirs` encodes a deliberate precedence rule — if `--project-dir` is specified without `--output-dir`, the `.env`-based `DEFAULT_OUTPUT_DIR` is intentionally bypassed in favor of `{REPO_ROOT}/output`, avoiding unintended reuse of a default output path tied to a different default project.
- **Feature toggle for optional dependency**: `LLMClient` instantiation is guarded by `ENABLE_LLM_DOC`, allowing the pipeline to run without requiring valid LLM credentials/configuration when documentation generation is disabled.
- **Sync-to-async bridging**: `main()` remains a synchronous function (standard `if __name__ == "__main__":` script pattern) and uses `asyncio.run()` to invoke the asynchronous pipeline, providing a conventional synchronous CLI entry point over async internals.

# Definition Design Specifications

## `parse_args() -> argparse.Namespace`

**Arguments:** None.

**Returns:** `argparse.Namespace` containing the parsed CLI options `project_dir` (`str | None`) and `output_dir` (`str | None`), both unset (`None`) if not provided on the command line.

**Responsibility / Design intent:** Provides a single, isolated entry point for CLI argument parsing, decoupling argument definition from the resolution logic that determines actual runtime paths.

**Design decisions:** Both `--project-dir` and `--output-dir` are optional with no hardcoded defaults at the `argparse` level; defaulting is deferred to `resolve_dirs`, allowing the resolution logic to apply conditional fallback rules (see below) rather than static defaults.

**Edge cases / constraints:** No validation of path existence or format is performed here; any string is accepted as-is.

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

**Arguments:** `args` — the `argparse.Namespace` returned by `parse_args()`, expected to expose `project_dir` and `output_dir` attributes (both optional strings).

**Returns:** `tuple[str, str]` of `(project_dir, output_dir)` — fully resolved absolute/relative path strings to be used for the rest of the pipeline.

**Responsibility / Design intent:** Centralizes the precedence rules for determining the effective project and output directories from CLI input versus `.env`-based configuration (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`), so `main()` itself stays simple.

**Design decisions:**
- `project_dir` falls back to `DEFAULT_PROJECT_DIR` when `--project-dir` is not given.
- `output_dir` resolution is intentionally asymmetric: if `--output-dir` is explicitly given, it is always used; if `--output-dir` is omitted but `--project-dir` *is* given, `DEFAULT_OUTPUT_DIR` (from `.env`) is deliberately **ignored** in favor of `{REPO_ROOT}/output`, preventing an unrelated `.env`-configured output path from being applied to a manually specified project; only when neither CLI argument is given does it fall back to `DEFAULT_OUTPUT_DIR`.

**Edge cases / constraints:** Assumes `REPO_ROOT` is a valid absolute path already resolved in `settings.py`; does not create or validate the returned directories (that responsibility belongs to `process_all_files`).

---

## `main() -> None`

**Arguments:** None (reads from `sys.argv` via `parse_args()`).

**Returns:** `None`.

**Responsibility / Design intent:** Serves as the sole executable entry point of the application, wiring together logging setup, CLI parsing/resolution, conditional LLM client construction, and invocation of the async pipeline.

**Design decisions:**
- Logging is initialized first (`setup_logging()`) before any other operation, ensuring all subsequent output (including argument resolution issues) is captured.
- `LLMClient` is instantiated conditionally based on `ENABLE_LLM_DOC`; when disabled, `None` is passed to `process_all_files`, allowing the pipeline to skip LLM-dependent steps without special-casing client construction in `main`.
- The async pipeline (`process_all_files`) is driven via `asyncio.run`, keeping `main()` itself synchronous as the process entry point.

**Edge cases / constraints:** If `ENABLE_LLM_DOC` is `True` but required LLM configuration (e.g., model name) is missing, `LLMClient()` construction will raise (per its documented fail-fast behavior); `main()` does not catch this, so the process will terminate with an error rather than proceeding without an LLM client.

# Dependency Description

### Dependencies (what this file uses)

`main.py` serves as the CLI entry point for codetwine and relies on the following internal modules:

- **codetwine/config/settings.py** (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC`, `REPO_ROOT`): Used to resolve fallback values for the project directory and output directory when CLI arguments are not provided, to determine whether LLM-based documentation generation should be enabled, and to compute a repository-root-relative default output path.

- **codetwine/config/logger.py** (`setup_logging`): Used to initialize console and file logging before any processing begins, ensuring consistent logging behavior across the application.

- **codetwine/llm/client.py** (`LLMClient`): Used to instantiate an LLM client for generating design documents, but only when `ENABLE_LLM_DOC` is true; otherwise `None` is passed downstream.

- **codetwine/pipeline.py** (`process_all_files`): Used as the core orchestration function that performs the actual dependency analysis and design document generation; `main.py` delegates all substantive processing to this function after resolving directories and setting up the LLM client.

### Dependents (what uses this file)

No dependent information available. `main.py` acts as the top-level executable entry point, so the dependency direction is unidirectional: `main.py` depends on the modules listed above, and no other project file depends on `main.py`.

# Data Flow

**Input data format and source**

| Source | Data | Format |
|---|---|---|
| CLI arguments | `--project-dir`, `--output-dir` | Optional strings via `argparse.Namespace` |
| `.env` / settings | `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC` | Constants loaded from `codetwine/config/settings.py` |

**Main transformation flow**

1. `parse_args()` reads raw CLI input into an `argparse.Namespace` (`project_dir`, `output_dir`, both optionally `None`).
2. `resolve_dirs(args)` merges CLI values with `.env` defaults into a concrete `(project_dir, output_dir)` string tuple, applying this precedence:
   - `output_dir`: explicit `--output-dir` > (`--project-dir` given but no `--output-dir` → `REPO_ROOT/output`) > `DEFAULT_OUTPUT_DIR`.
   - `project_dir`: explicit `--project-dir` > `DEFAULT_PROJECT_DIR`.
3. `setup_logging()` is invoked first (side effect only, no data returned) to initialize console/file logging before any processing.
4. `ENABLE_LLM_DOC` (bool flag) gates conditional construction of an `LLMClient` instance; otherwise `llm_client` is `None`.
5. The resolved `(project_dir, output_dir, llm_client)` tuple is passed into `process_all_files(...)`, run synchronously via `asyncio.run`, which performs all actual analysis/document generation internally (opaque to `main.py`).

**Output data format and destination**

- `main.py` itself produces no direct return value or file output; it only triggers side effects:
  - Logging output to console (WARNING+) and `logs/codetwine.log` (via `setup_logging`).
  - Delegated file outputs (dependency JSON, design docs, consolidated JSON, Mermaid graphs) written under `output_dir` by `process_all_files`.

**Key data structures**

| Structure | Fields | Purpose |
|---|---|---|
| `argparse.Namespace` (from `parse_args`) | `project_dir: str | None`, `output_dir: str | None` | Raw CLI input container |
| `(project_dir, output_dir)` tuple (from `resolve_dirs`) | `project_dir: str`, `output_dir: str` | Fully resolved paths passed downstream |
| `llm_client` | `LLMClient` instance or `None` | Optional dependency injected into the pipeline for LLM-based doc generation |

# Error Handling

`main.py` follows a fail-fast strategy: it performs no local exception handling and delegates all error management to lower layers (`LLMClient`, `process_all_files`, and their internal components). Any unhandled exception propagates up and terminates the process, since this file is the top-level entry point with no caller to recover to.

| Error Type | Handling | Impact |
|---|---|---|
| Missing/invalid `LLM_MODEL` config | `LLMClient.__init__` raises `ValueError`; not caught here | Program aborts before analysis starts when `ENABLE_LLM_DOC` is true |
| Errors during dependency analysis / doc generation (inside `process_all_files`) | Not caught in `main.py`; relies on `process_all_files`'s own internal handling (per-file isolation) or propagation | Uncaught exceptions stop the entire run; `asyncio.run` surfaces them to the caller |
| CLI argument errors (`argparse`) | Handled by `argparse` itself (prints usage, exits) | Process exits immediately with an error message |
| Invalid/missing directories (project/output) | No explicit validation in `main.py`; any resulting error surfaces from downstream file I/O in `process_all_files` | Failure occurs later in the pipeline rather than at argument resolution time |

Design considerations:
- Since `main.py` only wires together configuration, argument parsing, and the async pipeline call, it intentionally avoids try/except blocks, keeping responsibility for granular error recovery (e.g., per-file isolation, retries) inside `pipeline.py` and `LLMClient`.
- Logging is initialized first (`setup_logging`) so that any downstream error, even if it later crashes the process, is recorded to the rotating log file before failure.
- The `llm_client` is conditionally created based on `ENABLE_LLM_DOC`, meaning LLM-related initialization errors (e.g., missing model) only occur when LLM-based documentation is enabled, avoiding unnecessary fail-fast behavior when the feature is disabled.

# Summary

main.py is codetwine's thin CLI entry point, delegating all analysis logic to codetwine.pipeline.process_all_files. Responsibilities: parse CLI args (--project-dir, --output-dir), resolve effective directories against .env defaults (with asymmetric fallback for output_dir), initialize logging, conditionally build LLMClient per ENABLE_LLM_DOC, and run the async pipeline via asyncio.run. Interfaces: parse_args()->Namespace, resolve_dirs(args)->(project_dir, output_dir), main()->None. No local error handling; fail-fast, delegating errors downstream. No dependents (top-level entry point).
