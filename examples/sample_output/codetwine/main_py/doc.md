# Design Document: main.py

# Overview & Purpose

## 1. Module Summary

Serves as the command-line entry point for codetwine, parsing user arguments, resolving directory paths, and launching the full project analysis pipeline.

## 2. When to Use This Module

- **Running the tool from the command line**: Execute `uv run main.py` (with optional `--project-dir` and `--output-dir` flags) to trigger dependency analysis and design document generation for a target project.
- **Customizing the target project directory at runtime**: Pass `--project-dir DIR` to override `DEFAULT_PROJECT_DIR` from `.env`; `parse_args()` and `resolve_dirs()` handle the resolution.
- **Customizing the output directory at runtime**: Pass `--output-dir DIR` to override the default output path; when only `--project-dir` is given without `--output-dir`, `resolve_dirs()` falls back to `{REPO_ROOT}/output` rather than `DEFAULT_OUTPUT_DIR` from `.env`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_args` | none | `argparse.Namespace` | Parses `--project-dir` and `--output-dir` CLI arguments and returns the result |
| `resolve_dirs` | `args: argparse.Namespace` | `tuple[str, str]` | Determines the effective `project_dir` and `output_dir` by combining CLI arguments with `.env` defaults, applying the fallback rule when only `--project-dir` is provided |
| `main` | none | `None` | Initializes logging, resolves configuration, conditionally constructs `LLMClient`, and runs `process_all_files` via `asyncio.run` |

## 4. Design Decisions

- **Conditional LLM client construction**: `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`, allowing the pipeline to skip LLM-dependent steps without requiring changes in the pipeline layer.
- **Output directory fallback rule**: When `--project-dir` is specified without `--output-dir`, `resolve_dirs` explicitly ignores `DEFAULT_OUTPUT_DIR` from `.env` and uses `{REPO_ROOT}/output` instead. This prevents analysis results for an ad-hoc project from being written to an unrelated configured output path.

# Definition Design Specifications

---

## `parse_args() -> argparse.Namespace`

- **Signature:** `parse_args() -> argparse.Namespace`
- **Responsibility:** Declares and parses the two optional CLI arguments (`--project-dir`, `--output-dir`), returning them as a namespace object.
- **When to use:** Called once at program startup inside `main()` to capture user-supplied overrides before directory resolution.
- **Constraints & edge cases:**
  - Both arguments are optional; `args.project_dir` and `args.output_dir` will be `None` if not supplied.
  - No type coercion or path validation is performed here; raw strings are returned as-is.

---

## `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`

- **Signature:** `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]`
  - Return type is a two-element tuple of strings: `(project_dir, output_dir)`.
- **Responsibility:** Implements the three-way priority logic for determining the effective project and output directories, encapsulating the rule that `DEFAULT_OUTPUT_DIR` from `.env` is bypassed when `--project-dir` is provided without `--output-dir`.
- **When to use:** Called once in `main()` after `parse_args()` to obtain the final resolved directory pair before invoking the pipeline.
- **Design decisions:**

  | Condition | `project_dir` source | `output_dir` source |
  |-----------|----------------------|---------------------|
  | Neither flag supplied | `DEFAULT_PROJECT_DIR` | `DEFAULT_OUTPUT_DIR` |
  | `--project-dir` only | CLI value | `{REPO_ROOT}/output` (hardcoded fallback, ignores `DEFAULT_OUTPUT_DIR`) |
  | `--output-dir` only | `DEFAULT_PROJECT_DIR` | CLI value |
  | Both flags supplied | CLI value | CLI value |

  The decision to ignore `DEFAULT_OUTPUT_DIR` when only `--project-dir` is given prevents unintentional writes to a project-specific output path configured in `.env` that may be unrelated to the newly specified project.

- **Constraints & edge cases:**
  - Does not validate that the returned paths exist on disk; existence checks are the caller's responsibility.
  - `REPO_ROOT` is resolved at import time by `settings.py` and is the normalized absolute path two levels above `settings.py`.

---

## `main() -> None`

- **Signature:** `main() -> None`
- **Responsibility:** Top-level entry point that wires together logging setup, argument parsing, directory resolution, optional `LLMClient` instantiation, and async pipeline execution.
- **When to use:** Invoked when the script is run directly (`if __name__ == "__main__"`) or via the `uv run main.py` invocation described in the module docstring.
- **Design decisions:**
  - `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`, which handles the absent client gracefully.
  - `asyncio.run(...)` is used to execute the async pipeline from this synchronous entry point, creating and tearing down a new event loop for the duration of the run.
- **Constraints & edge cases:**
  - `LLMClient()` construction will raise `ValueError` if `LLM_MODEL` is not configured in the environment, halting execution before the pipeline starts.
  - `setup_logging()` must be called before any other operation so that all downstream log records are captured.

# Dependency Description

## Dependencies (modules this file imports)

- **main.py → codetwine/config/settings.py** : Retrieves configuration constants (`DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `REPO_ROOT`, `ENABLE_LLM_DOC`) needed to resolve project and output directories and to determine whether LLM document generation is enabled.

- **main.py → codetwine/config/logger.py** : Calls `setup_logging()` to initialize the application-wide logging configuration at program startup.

- **main.py → codetwine/llm/client.py** : Instantiates `LLMClient` when `ENABLE_LLM_DOC` is `True`, producing the async LLM client passed into the pipeline.

- **main.py → codetwine/pipeline.py** : Calls `process_all_files(project_dir, output_dir, llm_client)` to execute the full project analysis and output generation pipeline.

## Dependents (modules that import this file)

No dependent information available.

## Dependency Direction

All relationships are **unidirectional**: `main.py` imports from each of the four modules listed above, and none of those modules import from `main.py`. `main.py` acts as the top-level entry point that consumes functionality from lower-level modules without exposing any symbols of its own to the rest of the codebase.

# Data Flow

## 1. Inputs

| Source | Format | Description |
|--------|--------|-------------|
| CLI arguments (`--project-dir`, `--output-dir`) | `argparse.Namespace` | Optional strings specifying the project root and output directory |
| `.env` / shell environment | `str`, `bool` | `DEFAULT_PROJECT_DIR`, `DEFAULT_OUTPUT_DIR`, `ENABLE_LLM_DOC` read via `get_config_value` in `settings.py` |
| `REPO_ROOT` | `str` (normalized filesystem path) | Derived from the location of `settings.py`; used as the fallback base for `output_dir` |

## 2. Transformation Overview

```
CLI args (--project-dir, --output-dir)
        │
        ▼
  parse_args()
  → argparse.Namespace {project_dir: str|None, output_dir: str|None}
        │
        ▼
  resolve_dirs(args)
  → Applies three-way precedence logic:
      project_dir  = args.project_dir  OR  DEFAULT_PROJECT_DIR
      output_dir   = args.output_dir   OR  (REPO_ROOT/output if --project-dir given)
                                        OR  DEFAULT_OUTPUT_DIR
  → (project_dir: str, output_dir: str)
        │
        ├── ENABLE_LLM_DOC ──► LLMClient() instantiated  OR  None
        │
        ▼
  asyncio.run(process_all_files(project_dir, output_dir, llm_client))
  → Delegates all analysis, document generation, and file I/O
    to the pipeline module
```

**Precedence rule in `resolve_dirs`:** If `--project-dir` is provided but `--output-dir` is omitted, `DEFAULT_OUTPUT_DIR` from the environment is intentionally bypassed in favor of `{REPO_ROOT}/output`. Only when neither CLI argument is given does `DEFAULT_OUTPUT_DIR` take effect.

## 3. Outputs

| Output | Format | Description |
|--------|--------|-------------|
| `(project_dir, output_dir)` | `tuple[str, str]` | Resolved directory paths returned by `resolve_dirs`; consumed internally by `main` |
| `llm_client` | `LLMClient \| None` | Passed to `process_all_files`; `None` when `ENABLE_LLM_DOC` is `False` |
| Side effects via `process_all_files` | Files on disk, console output | All file writes (per-file dependency JSON, design documents, consolidated JSON, Mermaid graph) are produced inside the pipeline; `main.py` itself writes nothing directly |
| Logging configuration | Root logger state | `setup_logging()` attaches console (WARNING+) and rotating file (INFO+) handlers as a side effect |

## 4. Key Data Structures

### `argparse.Namespace` (produced by `parse_args`)

| Field / Key | Type | Purpose |
|-------------|------|---------|
| `project_dir` | `str \| None` | Value of `--project-dir`; `None` if not supplied |
| `output_dir` | `str \| None` | Value of `--output-dir`; `None` if not supplied |

### Resolved directory tuple (produced by `resolve_dirs`)

| Position | Type | Purpose |
|----------|------|---------|
| `[0]` `project_dir` | `str` | Absolute or relative path to the project root to be analyzed |
| `[1]` `output_dir` | `str` | Absolute or relative path where all analysis artifacts will be written |

### Directory resolution precedence (logic inside `resolve_dirs`)

| Condition | `project_dir` source | `output_dir` source |
|-----------|---------------------|---------------------|
| Both CLI args provided | `args.project_dir` | `args.output_dir` |
| Only `--project-dir` provided | `args.project_dir` | `{REPO_ROOT}/output` |
| Only `--output-dir` provided | `DEFAULT_PROJECT_DIR` | `args.output_dir` |
| Neither CLI arg provided | `DEFAULT_PROJECT_DIR` | `DEFAULT_OUTPUT_DIR` |

# Error Handling

## 1. Overall Strategy

`main.py` itself contains no explicit error handling (no try-except blocks). The file delegates all substantive work to imported modules and relies on **fail-fast propagation**: any unhandled exception raised by `parse_args()`, `resolve_dirs()`, `LLMClient()`, or `process_all_files()` will terminate the process with a Python traceback. The one deliberate error-avoidance decision made at this layer is the conditional instantiation of `LLMClient` — when `ENABLE_LLM_DOC` is `False`, the client is never constructed, eliminating the possibility of a `ValueError` from an unconfigured model.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| `ValueError` from `LLMClient.__init__` | `ENABLE_LLM_DOC=True` but `LLM_MODEL` is unset in the environment | Avoided entirely by not instantiating `LLMClient` when `ENABLE_LLM_DOC=False`; raised and propagated unhandled when `ENABLE_LLM_DOC=True` and model is missing | No | Process terminates |
| Missing or invalid CLI arguments | Unrecognized arguments passed to `argparse` | `argparse` prints usage and exits (standard `argparse` behavior) | No | Process terminates before any analysis begins |
| Invalid or missing `project_dir` / `output_dir` | Paths cannot be resolved from CLI args or `.env` defaults | `resolve_dirs` silently falls back to `REPO_ROOT/output` when `--project-dir` is supplied without `--output-dir`; no validation of path existence at this layer | Yes (fallback applied) | Analysis proceeds with fallback output directory |
| Any exception from `process_all_files` | I/O errors, dependency graph failures, LLM errors, etc. | Propagates unhandled through `asyncio.run()` | No | Process terminates |

---

## 3. Design Notes

- **Thin entry-point philosophy**: `main.py` is intentionally a minimal orchestrator. Error handling responsibility is pushed into the pipeline and client layers (`pipeline.py`, `LLMClient`), keeping this file free of defensive logic.
- **Feature-flag as error prevention**: The `ENABLE_LLM_DOC` guard is the only proactive error-avoidance measure in this file. It prevents a configuration error (`ValueError` for a missing model) from reaching runtime when the LLM feature is disabled, rather than catching the error after it occurs.
- **No logging of entry-point errors**: `setup_logging()` is called before any potentially failing operations, so any terminal exception would be visible via the configured logging infrastructure — but `main.py` itself emits no log messages and catches nothing, meaning terminal failures surface only as unhandled exception tracebacks.

# Summary

`main.py` is the CLI entry point that parses arguments, resolves directories, and launches the analysis pipeline. Public functions: `parse_args() -> argparse.Namespace` (fields: `project_dir: str|None`, `output_dir: str|None`); `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]` (applies three-way precedence: CLI args, `REPO_ROOT/output` fallback, or `.env` defaults); `main() -> None` (calls `setup_logging`, `resolve_dirs`, conditionally constructs `LLMClient`, runs `process_all_files(project_dir, output_dir, llm_client)` via `asyncio.run`).
