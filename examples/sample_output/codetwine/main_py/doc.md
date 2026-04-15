# Design Document: main.py

# Overview & Purpose

## 1. Module Summary

Serve as the command-line entry point for the codetwine tool, parsing user arguments, resolving input/output directories, and launching the full project analysis pipeline.

## 2. When to Use This Module

- **Running the tool from the command line**: Execute `uv run main.py` (with optional `--project-dir` and `--output-dir` flags) to trigger dependency analysis and design document generation for a target project.
- **Customizing the analysis target programmatically**: Call `parse_args()` to obtain a structured `argparse.Namespace` object representing the user's CLI input.
- **Resolving effective directories**: Call `resolve_dirs(args)` with a parsed `argparse.Namespace` to determine the final `project_dir` and `output_dir`, respecting the precedence rules between CLI arguments and `.env` defaults.
- **Running the full pipeline**: Call `main()` to initialize logging, resolve directories, conditionally construct an `LLMClient`, and execute `process_all_files` via `asyncio.run`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `parse_args` | none | `argparse.Namespace` | Parse `--project-dir` and `--output-dir` CLI arguments and return the result as a namespace object. |
| `resolve_dirs` | `args: argparse.Namespace` | `tuple[str, str]` | Determine the effective `(project_dir, output_dir)` pair by applying precedence rules among CLI arguments, `REPO_ROOT`-based default, and `.env` settings. |
| `main` | none | `None` | Initialize logging, resolve directories, optionally construct `LLMClient`, and run `process_all_files` as the top-level entry point. |

## 4. Design Decisions

- **Conditional `LLMClient` instantiation**: `LLMClient` is only constructed when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`. This keeps the entry point responsible for the instantiation decision while allowing the pipeline to remain agnostic about whether LLM generation is active.
- **Non-symmetric directory defaulting in `resolve_dirs`**: When `--project-dir` is supplied without `--output-dir`, the output directory defaults to `{REPO_ROOT}/output` rather than `DEFAULT_OUTPUT_DIR` from `.env`. This deliberate asymmetry prevents `.env`-configured output paths (which may be project-specific) from being silently applied to an unrelated CLI-specified project directory.

# Definition Design Specifications

---

## Module-level Overview

`main.py` is the CLI entry point for the `codetwine` tool. It owns argument parsing, directory resolution, logging initialization, and top-level orchestration of the async pipeline.

---

## `parse_args`

**Signature:**
```python
def parse_args() -> argparse.Namespace
```

| Item | Detail |
|---|---|
| Returns | `argparse.Namespace` — object with optional attributes `project_dir: str \| None` and `output_dir: str \| None` |

**Responsibility:** Declares and parses the two optional CLI flags (`--project-dir`, `--output-dir`), making raw user input available as a structured object.

**When to use:** Called once at the start of `main()` before any directory resolution or pipeline invocation.

**Constraints & edge cases:**
- Both flags are optional; neither has a hardcoded default at the parser level. Missing flags yield `None` on the returned namespace, which downstream resolution handles explicitly.

---

## `resolve_dirs`

**Signature:**
```python
def resolve_dirs(args: argparse.Namespace) -> tuple[str, str]
```

| Parameter | Type | Description |
|---|---|---|
| `args` | `argparse.Namespace` | Parsed CLI arguments from `parse_args()` |
| Returns | `tuple[str, str]` | `(project_dir, output_dir)` — both as absolute or resolvable path strings |

**Responsibility:** Implements the three-way precedence rule for `output_dir` and the two-way fallback for `project_dir`, insulating the rest of the program from argument/config ambiguity.

**When to use:** Called immediately after `parse_args()` in `main()`, before instantiating `LLMClient` or invoking the pipeline.

**Design decisions:**
- `output_dir` resolution follows a deliberate three-branch priority:
  1. Explicit `--output-dir` CLI value.
  2. If only `--project-dir` was provided (and `--output-dir` was not), `DEFAULT_OUTPUT_DIR` from `.env` is **ignored** in favor of `{REPO_ROOT}/output`.
  3. If neither CLI flag is set, `DEFAULT_OUTPUT_DIR` from `.env` is used.
- `project_dir` follows a simpler two-branch rule: CLI value takes precedence, falling back to `DEFAULT_PROJECT_DIR`.

**Constraints & edge cases:**
- The "ignore `DEFAULT_OUTPUT_DIR`" rule applies **only** when `--project-dir` is given without `--output-dir`; it does not apply when neither flag is specified.
- Does not validate that the resolved paths exist or are accessible.

---

## `main`

**Signature:**
```python
def main() -> None
```

**Responsibility:** Serves as the single top-level entry point that wires together logging setup, argument parsing, directory resolution, optional `LLMClient` construction, and async pipeline execution.

**When to use:** Invoked by the `if __name__ == "__main__"` guard or by a package entry-point script.

**Design decisions:**
- `LLMClient` is instantiated only when `ENABLE_LLM_DOC` is `True`; otherwise `None` is passed to `process_all_files`, which handles the absent client gracefully.
- The async pipeline (`process_all_files`) is driven synchronously via `asyncio.run`, keeping the entry point itself synchronous and avoiding any ambient event-loop dependency.

**Constraints & edge cases:**
- `LLMClient()` construction raises `ValueError` if `LLM_MODEL` is not configured; this propagates unhandled from `main`.
- `setup_logging()` must be called before any other operation so that log output from all subsequent components is captured correctly.

# Dependency Description

### Dependencies (modules this file imports)

- **main.py → codetwine/config/settings.py** : Retrieves runtime configuration constants — `DEFAULT_PROJECT_DIR` (fallback project root), `DEFAULT_OUTPUT_DIR` (fallback output directory), `REPO_ROOT` (repository root path used to construct the default output path when `--project-dir` is specified alone), and `ENABLE_LLM_DOC` (flag controlling whether LLM-based document generation is enabled).

- **main.py → codetwine/config/logger.py** : Invokes `setup_logging()` to initialize application-wide logging (console + rotating file handlers) once at startup before any other processing begins.

- **main.py → codetwine/llm/client.py** : Instantiates `LLMClient` when `ENABLE_LLM_DOC` is `True`, producing the async LLM API client that is passed into the pipeline for design document generation.

- **main.py → codetwine/pipeline.py** : Calls `process_all_files(project_dir, output_dir, llm_client)` to delegate the entire analysis pipeline — dependency extraction, design document generation, and output artifact production — to the pipeline module.

---

### Dependents (modules that import this file)

No dependent information available.

---

### Dependency Direction

All relationships are **unidirectional**:

- `main.py → codetwine/config/settings.py` : unidirectional (settings does not import main)
- `main.py → codetwine/config/logger.py` : unidirectional (logger does not import main)
- `main.py → codetwine/llm/client.py` : unidirectional (client does not import main)
- `main.py → codetwine/pipeline.py` : unidirectional (pipeline does not import main)

`main.py` acts as the top-level entry point of the application, consuming from all internal modules without being imported by any of them.

# Data Flow

## 1. Inputs

| Source | Data | Format |
|---|---|---|
| CLI arguments (`--project-dir`) | Root directory of the project to analyze | `str` (filesystem path) |
| CLI arguments (`--output-dir`) | Output directory for analysis results | `str` (filesystem path) |
| `DEFAULT_PROJECT_DIR` (settings.py) | Fallback project directory when `--project-dir` is omitted | `str` (filesystem path, derived from `.env` or `REPO_ROOT`) |
| `DEFAULT_OUTPUT_DIR` (settings.py) | Fallback output directory when neither CLI argument is provided | `str` (filesystem path, derived from `.env` or `REPO_ROOT/output`) |
| `REPO_ROOT` (settings.py) | Repository root path used as the base for the default output directory when only `--project-dir` is specified | `str` (normalized filesystem path) |
| `ENABLE_LLM_DOC` (settings.py) | Flag controlling whether an `LLMClient` instance is created | `bool` |

## 2. Transformation Overview

```
CLI argv
    │
    ▼
parse_args()
    │  argparse.Namespace {project_dir: str|None, output_dir: str|None}
    ▼
resolve_dirs()
    │  Applies three-way priority logic:
    │  - Both args present      → use both CLI values
    │  - Only --project-dir     → CLI project_dir + REPO_ROOT/output
    │  - Neither arg present    → DEFAULT_PROJECT_DIR + DEFAULT_OUTPUT_DIR
    │
    │  (project_dir: str, output_dir: str)
    ▼
LLMClient() or None
    │  Conditional on ENABLE_LLM_DOC bool:
    │  True  → instantiated LLMClient (model/key/base from settings)
    │  False → None
    │
    ▼
asyncio.run(process_all_files(project_dir, output_dir, llm_client))
    │  Delegates all analysis, document generation, and file I/O
    │  to the pipeline module
    ▼
  (all outputs produced inside pipeline.py)
```

## 3. Outputs

`main.py` itself produces no direct file writes or return values. Its outputs are entirely side effects routed through the called functions:

| Sink | Data | Nature |
|---|---|---|
| `process_all_files(...)` | `project_dir`, `output_dir`, `llm_client` | Passed as arguments; all file writing, JSON generation, Mermaid graphs, and design documents are produced inside `pipeline.py` |
| Root logger (via `setup_logging()`) | Rotating log file at `_LOG_DIR/codetwine.log`; WARNING-level messages to console | Side effect |

## 4. Key Data Structures

### `argparse.Namespace` (produced by `parse_args`)

| Field / Key | Type | Purpose |
|---|---|---|
| `project_dir` | `str \| None` | Value of `--project-dir` CLI argument; `None` when omitted |
| `output_dir` | `str \| None` | Value of `--output-dir` CLI argument; `None` when omitted |

### Return value of `resolve_dirs` — plain `tuple`

| Position | Type | Purpose |
|---|---|---|
| `[0]` `project_dir` | `str` | Resolved absolute or relative path to the project root to be analyzed |
| `[1]` `output_dir` | `str` | Resolved absolute or relative path where all output artifacts will be written |

### Priority matrix inside `resolve_dirs`

| `args.project_dir` | `args.output_dir` | Resulting `project_dir` | Resulting `output_dir` |
|---|---|---|---|
| provided | provided | `args.project_dir` | `args.output_dir` |
| provided | omitted | `args.project_dir` | `REPO_ROOT/output` |
| omitted | omitted | `DEFAULT_PROJECT_DIR` | `DEFAULT_OUTPUT_DIR` |

# Error Handling

## 1. Overall Strategy

`main.py` itself contains no explicit error handling constructs (no try/except blocks). Its strategy is **delegation with minimal defense**: each responsibility is pushed entirely to called subsystems (`setup_logging`, `resolve_dirs`, `LLMClient`, `process_all_files`), and errors that propagate back are left unhandled, causing the process to terminate with an unhandled exception. The one deliberate design choice at this layer is **conditional instantiation**: when `ENABLE_LLM_DOC` is `False`, `LLMClient` is never constructed, avoiding any initialization errors related to missing LLM credentials.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Missing or invalid `LLM_MODEL` | `ENABLE_LLM_DOC` is `True` and `LLM_MODEL` is unset in `.env` or environment | `LLMClient.__init__` raises `ValueError`; propagates uncaught to the caller | No | Process terminates immediately at startup |
| LLM rate limit exceeded | API returns HTTP 429 during `process_all_files` execution | Handled inside `LLMClient._call_with_retry` (retry with wait); not surfaced to `main.py` | Yes (within retry budget) | Transparent to `main.py`; handled in dependency |
| LLM API error | Provider API error during generation | Handled inside `LLMClient`; returns `None` | Yes (skips that document) | Transparent to `main.py` |
| Invalid or inaccessible `project_dir` / `output_dir` | Path does not exist or lacks permissions; detected during `process_all_files` | Propagates uncaught to `main.py` | No | Process terminates |
| Invalid CLI arguments | Unrecognized flags passed to the CLI | `argparse` prints usage and calls `sys.exit(2)` | No | Process terminates with exit code 2 |
| `ENABLE_LLM_DOC=False` | Configuration flag disables LLM | `llm_client` is set to `None`; `process_all_files` skips document generation | Yes (feature disabled by design) | LLM-related errors are fully avoided |

---

## 3. Design Notes

- **Thin entry-point philosophy**: `main.py` is intentionally kept as a thin orchestrator. Error handling complexity is encapsulated in the subsystems it calls (`pipeline.py`, `LLMClient`), keeping the top-level flow readable and uncluttered.
- **Guard by configuration, not exception**: The `ENABLE_LLM_DOC` flag acts as a pre-condition guard. Rather than catching errors that arise from absent LLM configuration, the design prevents the error from occurring at all by not constructing `LLMClient` when LLM functionality is disabled.
- **No explicit fallback or recovery at this layer**: `main.py` does not implement any fallback logic (e.g., retrying `process_all_files`, partial output recovery, or error reporting to the user beyond what propagates naturally). Any unhandled exception results in a Python traceback and process termination.

# Summary

`main.py` is the CLI entry point that parses arguments, resolves directories, and launches the async analysis pipeline. Public functions: `parse_args() -> argparse.Namespace` (fields: `project_dir: str|None`, `output_dir: str|None`); `resolve_dirs(args: argparse.Namespace) -> tuple[str, str]` (applies three-way priority: both CLI args → use both; only `--project-dir` → `REPO_ROOT/output`; neither → `.env` defaults); `main() -> None` (initializes logging, conditionally constructs `LLMClient`, runs `process_all_files(project_dir, output_dir, llm_client)`).
