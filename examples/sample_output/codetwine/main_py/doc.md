# Design Document: main.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Serves as the command-line entry point for codetwine, parsing user arguments, resolving project and output directories, and launching the full dependency analysis and design document generation pipeline.

## 2. When to Use This Module

- **Running the analysis tool from the command line**: Invoke `main()` (or `uv run main.py`) to trigger the complete pipeline; it accepts `--project-dir` and `--output-dir` flags and delegates to `process_all_files`.
- **Resolving directory configuration**: Call `resolve_dirs(args)` when the directory resolution logic (CLI args vs. `.env` defaults vs. `REPO_ROOT`-based fallback) needs to be tested or reused independently of the CLI parsing step.
- **Parsing CLI arguments in isolation**: Call `parse_args()` to obtain a typed `argparse.Namespace` containing `project_dir` and `output_dir` values without executing any analysis logic.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_args` | none | `argparse.Namespace` | Parses `--project-dir` and `--output-dir` command-line arguments |
| `resolve_dirs` | `args: argparse.Namespace` | `tuple[str, str]` | Determines final `(project_dir, output_dir)` by combining CLI args with `.env` defaults and `REPO_ROOT` fallback |
| `main` | none | `None` | Initializes logging, resolves directories, constructs `LLMClient` when enabled, and runs `process_all_files` |

## 4. Design Decisions

- **Conditional `LLMClient` instantiation**: `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`, allowing the pipeline to skip LLM-dependent steps without raising errors.
- **`output_dir` fallback when only `--project-dir` is supplied**: When `--project-dir` is provided but `--output-dir` is not, `resolve_dirs` ignores `DEFAULT_OUTPUT_DIR` from `.env` and unconditionally falls back to `{REPO_ROOT}/output`. This prevents a project-specific `.env` value from polluting output when the user explicitly targets a different project directory.

## Definition Design Specifications

# Definition Design Specifications

---

## `parse_args() -> argparse.Namespace`

- **Signature:** `parse_args() -> argparse.Namespace`
- **Responsibility:** Declares and parses the two optional CLI flags (`--project-dir`, `--output-dir`) accepted by the tool, returning a namespace object carrying their values (or `None` when omitted).
- **When to use:** Called once at startup inside `main()` before any directory resolution occurs.
- **Constraints & edge cases:** Both arguments are optional; the returned namespace fields `args.project_dir` and `args.output_dir` will be `None` if not supplied by the caller.

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

- **Signature:** `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`
  - `args`: The namespace returned by `parse_args()`.
  - Return type: A 2-tuple `(project_dir, output_dir)` where both elements are absolute or relative path strings.
- **Responsibility:** Implements the precedence rules that determine `project_dir` and `output_dir` from the combination of CLI arguments and `.env`-sourced defaults.
- **When to use:** Called once in `main()` immediately after `parse_args()`, before any filesystem or pipeline operations.
- **Design decisions:**

  | Condition | `project_dir` | `output_dir` |
  |---|---|---|
  | Neither flag supplied | `DEFAULT_PROJECT_DIR` (.env) | `DEFAULT_OUTPUT_DIR` (.env) |
  | Only `--project-dir` supplied | CLI value | `{REPO_ROOT}/output` (hardcoded default, ignores `.env`) |
  | Only `--output-dir` supplied | `DEFAULT_PROJECT_DIR` (.env) | CLI value |
  | Both flags supplied | CLI value | CLI value |

  The notable non-obvious rule is that supplying `--project-dir` alone causes `DEFAULT_OUTPUT_DIR` from `.env` to be **ignored** in favour of `{REPO_ROOT}/output`. This prevents results from a custom project being mixed into a configured shared output location.

- **Constraints & edge cases:** Does not validate that the resolved paths exist; path existence errors surface later in the pipeline.

---

## `main() -> None`

- **Signature:** `main() -> None`
- **Responsibility:** Serves as the single entry point that wires together logging setup, argument parsing, optional `LLMClient` construction, and async pipeline execution.
- **When to use:** Invoked directly when the module is run as a script (`if __name__ == "__main__"`) or via the package's installed console-script entry point.
- **Design decisions:**
  - `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to the pipeline, which interprets `None` as "skip LLM document generation".
  - `asyncio.run()` is used to drive the top-level coroutine `process_all_files`, making the entire pipeline async without requiring the caller to manage an event loop.
- **Constraints & edge cases:** `LLMClient()` construction raises `ValueError` if `LLM_MODEL` is unset in the environment; this propagates uncaught and terminates the process with a traceback.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

- `codetwine/main_py/main.py` → `codetwine/config/settings.py` : retrieves `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, and `REPO_ROOT` to resolve the project and output directory paths, and reads `ENABLE_LLM_DOC` to conditionally instantiate the LLM client

- `codetwine/main_py/main.py` → `codetwine/config/logger.py` : calls `setup_logging()` to initialize application-wide logging (console and rotating file handlers) at the start of `main()`

- `codetwine/main_py/main.py` → `codetwine/llm/client.py` : instantiates `LLMClient` when `ENABLE_LLM_DOC` is `True`, producing the client object passed to the pipeline for LLM-based design document generation

- `codetwine/main_py/main.py` → `codetwine/pipeline.py` : invokes `process_all_files(project_dir, output_dir, llm_client)` via `asyncio.run()` to execute the full project analysis and artifact generation pipeline

## Dependents (modules that import this file)

No dependent information available.

## Dependency Direction

All relationships are **unidirectional**:

- `codetwine/main_py/main.py` → `codetwine/config/settings.py` (unidirectional)
- `codetwine/main_py/main.py` → `codetwine/config/logger.py` (unidirectional)
- `codetwine/main_py/main.py` → `codetwine/llm/client.py` (unidirectional)
- `codetwine/main_py/main.py` → `codetwine/pipeline.py` (unidirectional)

`main.py` serves as the application entry point and is a pure consumer of all imported modules; none of the imported modules import back from `main.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `--project-dir` | CLI argument | String (filesystem path), optional |
| `--output-dir` | CLI argument | String (filesystem path), optional |
| `DEFAULT_PROJECT_DIR` | `codetwine/config/settings.py` (env-backed config) | String (filesystem path) |
| `DEFAULT_OUTPUT_DIR` | `codetwine/config/settings.py` (env-backed config) | String (filesystem path) |
| `REPO_ROOT` | `codetwine/config/settings.py` | String (filesystem path, normalized absolute path) |
| `ENABLE_LLM_DOC` | `codetwine/config/settings.py` (env-backed config) | `bool` |

## 2. Transformation Overview

```
CLI args (--project-dir, --output-dir)
        │
        ▼
   parse_args()
   → argparse.Namespace{project_dir, output_dir}
        │
        ▼
   resolve_dirs(args)
   Priority resolution:
     project_dir: args.project_dir → DEFAULT_PROJECT_DIR
     output_dir:  args.output_dir
                  → (if args.project_dir set) REPO_ROOT/output
                  → DEFAULT_OUTPUT_DIR
   → (project_dir: str, output_dir: str)
        │
        ├──── ENABLE_LLM_DOC ──────────────────────┐
        │                                           │
        │  True → LLMClient()                       │  False → None
        │         (model, api_key, api_base          │
        │          resolved internally)             │
        └───────────────────┬───────────────────────┘
                            │ llm_client: LLMClient | None
                            ▼
              asyncio.run(process_all_files(
                  project_dir, output_dir, llm_client
              ))
              → Full pipeline execution (see pipeline.py)
```

**Key branching rule in `resolve_dirs`:** when `--project-dir` is given without `--output-dir`, `DEFAULT_OUTPUT_DIR` from `.env` is bypassed and `{REPO_ROOT}/output` is used instead. Both `DEFAULT_OUTPUT_DIR` and `{REPO_ROOT}/output` default to the same path, but `DEFAULT_OUTPUT_DIR` can be overridden in `.env` while this fallback cannot.

## 3. Outputs

This module produces no return values or file writes directly. Its sole output is the side effect of invoking `process_all_files`, which drives all downstream artifact generation. The resolved `project_dir` and `output_dir` strings are passed as arguments into that pipeline.

| Output | Destination | Format |
|---|---|---|
| `project_dir` | `process_all_files` | `str` (filesystem path) |
| `output_dir` | `process_all_files` | `str` (filesystem path) |
| `llm_client` | `process_all_files` | `LLMClient` instance or `None` |
| Logging configuration | Root logger (global side effect) | Handlers attached by `setup_logging()` |

## 4. Key Data Structures

### `argparse.Namespace` (produced by `parse_args`)

| Field | Type | Purpose |
|---|---|---|
| `project_dir` | `str \| None` | Value of `--project-dir` CLI argument; `None` if omitted |
| `output_dir` | `str \| None` | Value of `--output-dir` CLI argument; `None` if omitted |

### Resolved directory tuple (produced by `resolve_dirs`)

| Position | Type | Purpose |
|---|---|---|
| `[0]` (`project_dir`) | `str` | Root directory of the project to analyze |
| `[1]` (`output_dir`) | `str` | Root directory where all output artifacts are written |

## Error Handling

# Error Handling

## 1. Overall Strategy

`main.py` adopts a **delegating, minimal-intervention** strategy at the entry-point level. The file itself contains no explicit error handling constructs; instead, it trusts dependency modules to enforce their own policies. The overall behavior is **fail-fast by default**: unhandled exceptions from `parse_args()`, `resolve_dirs()`, `LLMClient()`, or `process_all_files()` propagate directly to the Python runtime, terminating the process. The sole conditional guard is the `ENABLE_LLM_DOC` flag, which provides **graceful degradation** by bypassing `LLMClient` instantiation entirely when LLM features are disabled.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` from `LLMClient.__init__` | `LLM_MODEL` is not set in the environment when `ENABLE_LLM_DOC=True` | Propagates unhandled to the runtime | No | Process terminates with an exception |
| Missing or invalid CLI arguments | Unrecognized flags or malformed argument values passed to `parse_args()` | `argparse` prints usage and exits via `sys.exit` | No | Process terminates before execution begins |
| LLM disabled via configuration | `ENABLE_LLM_DOC=False` in environment/`.env` | `LLMClient` is not instantiated; `None` is passed to `process_all_files()` | Yes | LLM document generation is skipped; dependency analysis proceeds normally |
| Errors within `process_all_files()` | Any failure during dependency analysis, file I/O, or LLM calls | Delegated entirely to `pipeline.py` and its sub-components | Depends on pipeline policy | Determined by `pipeline.py` error handling |
| Errors within `setup_logging()` | Failure during log directory creation or handler initialization | Delegated to `logger.py` | Depends on logger policy | Logging may be unavailable for subsequent steps |

---

## 3. Design Notes

`main.py` is intentionally thin as an entry point, and this is reflected in its error handling posture. The file makes no attempt to catch, wrap, or recover from exceptions itself. This is consistent with the single-responsibility principle: `main.py` is responsible only for argument parsing, directory resolution, and wiring together top-level components. All substantive error handling—such as LLM retry logic and file-level exception management—is encapsulated within the respective dependency modules (`LLMClient`, `process_all_files`).

The `ENABLE_LLM_DOC` conditional is the only deliberate fault-tolerance mechanism at this layer, ensuring that a misconfigured or unavailable LLM does not prevent the core dependency analysis pipeline from running. This represents a conscious design boundary: LLM functionality is treated as optional, while pipeline execution is treated as mandatory.

## Summary

**main.py** is the CLI entry point that parses arguments, resolves directories, and launches the analysis pipeline.

**Public functions:**
- `parse_args()` → `argparse.Namespace` (`project_dir`, `output_dir`)
- `resolve_dirs(args: argparse.Namespace)` → `tuple[str, str]`
- `main()` → `None`

**Key data:** CLI flags `--project-dir`/`--output-dir`; settings `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC`; conditionally constructs `LLMClient` (or `None`); passes `(project_dir, output_dir, llm_client)` to `process_all_files`.
