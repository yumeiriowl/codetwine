# Design Document: codetwine/config/logger.py

# Overview & Purpose

## 1. Module Summary
Configures application-wide logging by attaching a console handler and a rotating file handler to the root logger.

## 2. When to Use This Module
- When initializing an entry point (e.g., `main.py`, `rlm_qa_agent.py`), call `setup_logging()` once at the start of `main()` to establish consistent logging behavior across the entire application before any other logging calls occur.
- When you need warnings and errors surfaced on the console while retaining detailed logs (including INFO-level) in a persistent file, rely on `setup_logging()` to set this up automatically.
- When you want log output written to a rotating file under a fixed `logs/` directory at the repository root, without manually configuring `RotatingFileHandler`, use `setup_logging()`.
- When you need noisy third-party libraries (`httpx`, `httpcore`, `LiteLLM`) suppressed to WARNING level to avoid cluttering logs, `setup_logging()` handles this automatically.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|------|-------------------|-------------|-----------------|
| `setup_logging` | `level: int = logging.INFO` | `None` | Configures the root logger with a console handler (WARNING+) and a rotating file handler (at the given level), applies a shared formatter, and restricts specific third-party loggers to WARNING level. |

## 4. Design Decisions
- Console and file handlers use different log levels (WARNING for console, the configured `level` for the file) so that the console stays concise while the file retains full detail.
- A custom formatter (`_SkipBlankFormatter`, private) suppresses log entries whose message is blank or whitespace-only, preventing empty log lines from cluttering output.
- The log directory path is computed relative to the module's location to always resolve to a `logs/` directory at the repository root, regardless of the current working directory.

# Definition Design Specifications

## Module-Level Constants

### `_LOG_DIR`

| Aspect | Detail |
|---|---|
| Type | `str` |
| Value | Absolute, normalized path to a `logs` directory located two levels above this file's directory (i.e., the repository root's `logs/` folder). |
| Responsibility | Provides a single, consistent location for all log files, independent of the current working directory from which the program is run. |
| Design decisions | Computed via `os.path.normpath` and `os.path.dirname(__file__)` traversal rather than a hardcoded path, so it resolves correctly regardless of the caller's CWD. |
| Constraints | Assumes this file remains at a fixed depth (two levels below repo root); moving the file would change the resolved log location. |

### `_LOG_FORMAT`

| Aspect | Detail |
|---|---|
| Type | `str` |
| Value | `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` |
| Responsibility | Defines a uniform textual layout (timestamp, level, logger name, message) shared by console and file handlers. |

### `_MAX_BYTES`

| Aspect | Detail |
|---|---|
| Type | `int` |
| Value | `1_048_576` (1 MiB) |
| Responsibility | Threshold size that triggers log file rotation. |

### `_BACKUP_COUNT`

| Aspect | Detail |
|---|---|
| Type | `int` |
| Value | `5` |
| Responsibility | Number of rotated backup log files retained before the oldest is discarded. |

---

## `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** Prevents log records whose message body is empty or whitespace-only (including a single `"\n"`) from producing noisy blank-line entries in the log output.

**When to use:** Instantiated internally by `setup_logging` as the shared formatter for both the console and file handlers; not intended for direct external instantiation.

**Design decisions:**
- Subclasses `logging.Formatter` and overrides only `format`, delegating to the parent implementation for all non-blank messages.
- Blank detection is based on `record.getMessage().strip()` being `""` or `"\n"`, not on raw whitespace variants beyond those two cases.

**Constraints & edge cases:**
- Returning an empty string from `format` still emits a record through the handler (e.g., an empty line may still be written), depending on handler behavior — the class only suppresses the formatted text content, not the handler emit call itself.
- Only guards against exactly `""` and `"\n"` after stripping; other whitespace-only strings (e.g., `"   "`, `"\t"`) are also caught since `.strip()` reduces them to `""`.

### `_SkipBlankFormatter.format`

| Aspect | Detail |
|---|---|
| Signature | `format(self, record: logging.LogRecord) -> str` |
| Responsibility | Returns an empty string for blank/whitespace-only log messages; otherwise defers to `logging.Formatter.format`. |
| When to use | Invoked automatically by the logging framework whenever a handler using this formatter emits a record. |
| Design decisions | Checks the message content before formatting, avoiding unnecessary formatting work for suppressed records. |
| Constraints & edge cases | Relies on `record.getMessage()`, which merges `record.msg` with `record.args`; if message construction raises, this method inherits that failure mode from the base class. |

---

## `setup_logging`

**Signature:** `setup_logging(level: int = logging.INFO) -> None`

- `level`: an `int` representing a standard `logging` module level constant (e.g., `logging.INFO`, `logging.DEBUG`), controlling the root logger's minimum severity threshold.
- Returns `None`; this function performs configuration as a side effect on the global `logging` module state.

**Responsibility:** Centralizes application-wide logging configuration by attaching a console handler and a rotating file handler to the root logger, and by tuning noisy third-party library log levels.

**When to use:** Called once at the start of an entry point's `main()` function (as shown in `main.py`), before any other logging calls are expected to take effect.

**Design decisions:**
- Applies two handlers with different verbosity: console output is restricted to `WARNING` and above, while the file handler inherits the root logger's level (default `INFO`), so detailed logs go only to the file.
- Uses `RotatingFileHandler` with `_MAX_BYTES` and `_BACKUP_COUNT` to bound log file growth automatically rather than requiring manual log rotation/cleanup.
- Both handlers share a single `_SkipBlankFormatter` instance, ensuring consistent formatting and blank-line suppression across outputs.
- Explicitly lowers verbosity of `httpx`, `httpcore`, and `LiteLLM` loggers to `WARNING`, preventing third-party library chatter from polluting logs at the configured application level.
- Creates the log directory via `os.makedirs(..., exist_ok=True)` so the function is safe to call even when the `logs/` directory does not yet exist.

**Constraints & edge cases:**
- Not idempotent with respect to handlers: calling this function multiple times will add duplicate handlers to the root logger, resulting in duplicated log output, since there is no check for existing handlers before appending new ones.
- Requires filesystem write access to the computed `_LOG_DIR` location; failure to create the directory or open the log file will raise an exception (e.g., `OSError`/`PermissionError`), which is not caught here.
- The `level` parameter only affects the root logger and, transitively, the file handler's effective threshold; the console handler's `WARNING` floor is fixed regardless of `level`.

# Dependency Description

### Dependencies (modules this file imports)
This file has no dependencies on other project-internal modules. All imports used within `codetwine/config/logger.py` (`os`, `logging`, `logging.handlers.RotatingFileHandler`) are standard library modules and are therefore excluded from this description.

### Dependents (modules that import this file)
- `main.py` → `codetwine/config/logger.py` : Calls `setup_logging()` at the start of `main()` to initialize console and file logging (including log level, formatter, and external library log level restrictions) before proceeding with dependency analysis and design document generation.

### Dependency Direction
The relationship between `main.py` and `codetwine/config/logger.py` is **unidirectional**: `main.py` depends on `codetwine/config/logger.py` by invoking `setup_logging()`, while `codetwine/config/logger.py` has no dependency back on `main.py` or any other project-internal module.

# Data Flow

### 1. Inputs
- **`level` (int, optional)**: Log level passed to `setup_logging()`, defaulting to `logging.INFO`. The sole external caller (`main.py`) invokes it with no arguments, relying on the default.
- **Module-level path construction**: `os.path.dirname(__file__)` is used at import time to derive the repository root, from which the fixed relative path `logs/` is computed (`_LOG_DIR`).
- **Runtime log records**: Once configured, arbitrary `logging.LogRecord` instances generated elsewhere in the codebase (via `logging.getLogger(...)` calls) flow into the configured handlers. Each record's message text (`record.getMessage()`) is inspected during formatting.
- **Constants defined in-module**: `_LOG_FORMAT` (format string), `_MAX_BYTES` (1,048,576), `_BACKUP_COUNT` (5) act as fixed configuration inputs to the handlers.

### 2. Transformation Overview
1. **Root logger acquisition**: `logging.getLogger()` retrieves the global root logger instance; its level is set to the provided `level`.
2. **Formatter construction**: A single `_SkipBlankFormatter` instance is built from `_LOG_FORMAT`, shared by both handlers.
3. **Console handler setup**: A `StreamHandler` is created, restricted to `WARNING` and above, given the shared formatter, and attached to the root logger — this branch handles the "console" output path.
4. **File handler setup**: The log directory is ensured to exist (`os.makedirs`), then a `RotatingFileHandler` is created pointing to `codetwine.log` inside `_LOG_DIR`, configured with rotation size/backup limits and UTF-8 encoding, given the same shared formatter, and attached to the root logger — this branch handles the "file" output path.
5. **Fan-out at emission time**: After setup, any log record emitted anywhere in the application propagates to the root logger and is dispatched to **both** handlers independently (console and file), each applying its own level filter and the shared formatter.
6. **Formatting/filtering stage (`_SkipBlankFormatter.format`)**: For each record reaching a handler, the message is stripped and checked; if it is empty or just a newline, an empty string is returned (suppressing output); otherwise the standard `Formatter.format` produces the final formatted line.
7. **External logger level restriction**: Independently of the root logger pipeline, three named loggers (`httpx`, `httpcore`, `LiteLLM`) have their levels explicitly set to `WARNING`, narrowing what records they generate/propagate regardless of the root level.

### 3. Outputs
- **Side effect — logger configuration**: The root logger gains two handlers (console `StreamHandler`, file `RotatingFileHandler`), each with formatter and level set. No return value (`None`).
- **Side effect — directory/file creation**: `_LOG_DIR` (`<repo_root>/logs`) is created if missing; a rotating file `codetwine.log` is created/appended to within it, capped at `_MAX_BYTES` per file with up to `_BACKUP_COUNT` backups.
- **Side effect — console stream output**: Formatted log lines (level `WARNING` and above) are written to the console stream.
- **Side effect — named logger level mutation**: `httpx`, `httpcore`, and `LiteLLM` loggers are set to `WARNING`, suppressing their more verbose output globally for the process.

### 4. Key Data Structures

**`logging.LogRecord`** (consumed, not created by this module)
| Field / Key | Type | Purpose |
|---|---|---|
| `getMessage()` result | str | The rendered log message text, checked for blank/whitespace-only content in `_SkipBlankFormatter.format` |
| `levelno` / level | int | Compared against handler levels (`WARNING` for console) to decide whether the record is emitted |

**Module-level constants**
| Field / Key | Type | Purpose |
|---|---|---|
| `_LOG_DIR` | str | Absolute path to the `logs` directory at the repository root; destination for the rotating log file |
| `_LOG_FORMAT` | str | Format string (`"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`) applied to all formatted records |
| `_MAX_BYTES` | int | Maximum size (1,048,576 bytes) of a single log file before rotation |
| `_BACKUP_COUNT` | int | Number of rotated backup log files to retain (5) |

**`_SkipBlankFormatter` (class extending `logging.Formatter`)**
| Field / Key | Type | Purpose |
|---|---|---|
| (inherited format string) | str | Same as `_LOG_FORMAT`, used to render non-blank messages |
| `format()` return value | str | Either an empty string (blank/whitespace message) or the standard formatted log line |

**Handlers attached to root logger**
| Field / Key | Type | Purpose |
|---|---|---|
| `console_handler` | `logging.StreamHandler` | Outputs formatted records at level ≥ `WARNING` to stderr/stdout |
| `file_handler` | `logging.handlers.RotatingFileHandler` | Outputs all formatted records (at root logger's configured `level`) to `codetwine.log`, rotating by size |

# Error Handling

## 1. Overall Strategy

This module does not implement explicit try-except error handling. It relies on the underlying `logging` and `os` standard library modules to raise exceptions naturally when unrecoverable conditions occur (e.g., filesystem access failures). The design assumes a fail-fast approach: `setup_logging()` is expected to be called once at application startup (as seen in `main.py`), and any failure during logging setup will propagate to the caller rather than being silently suppressed. The only intentional error-suppression logic is functional (not exception-based): the `_SkipBlankFormatter` class silently discards blank or whitespace-only log messages by returning an empty string instead of raising or logging an error.

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Directory creation failure | `os.makedirs(_LOG_DIR, exist_ok=True)` fails (e.g., permission denied, invalid path) | No handling; exception propagates | No | `setup_logging()` raises, halting initialization before file handler is attached |
| Log file open/rotation failure | `RotatingFileHandler` cannot open or write to `codetwine.log` (e.g., permission issues, disk full) | No handling; exception propagates | No | `setup_logging()` fails, application startup is interrupted |
| Blank/whitespace-only log message | `record.getMessage().strip()` evaluates to `""` or `"\n"` | `_SkipBlankFormatter.format()` returns an empty string instead of formatting | Yes (not a true error, functional skip) | Message is effectively suppressed from output; no exception raised |
| Invalid log level argument | Non-standard value passed to `setup_logging(level=...)` | No validation performed; passed directly to `root_logger.setLevel(level)` | No | Underlying `logging` module behavior determines outcome; no explicit handling in this file |

## 3. Design Notes

- The module favors simplicity over defensive coding: it assumes the logging directory and file system are accessible in the expected runtime environment, deferring any failure handling to the standard library's default exception behavior.
- The only custom logic addressing an "error-like" condition (blank log lines) is handled via formatting suppression rather than exceptions, keeping the log output clean without altering control flow.
- Since `setup_logging()` is intended to be invoked once at the start of `main()`, any failure here is treated as a startup-blocking condition, consistent with a fail-fast philosophy for configuration steps.
- External library log level suppression (`httpx`, `httpcore`, `LiteLLM`) is a configuration action, not an error-handling mechanism, and has no associated error handling.

# Summary

Configures app-wide logging by attaching console (WARNING+) and rotating file (1MiB, 5 backups) handlers to the root logger, with blank-line suppression.

Public API: `setup_logging(level: int = logging.INFO) -> None`.

Uses `_SkipBlankFormatter(logging.Formatter)` to suppress blank/whitespace `logging.LogRecord` messages. Constants: `_LOG_DIR` (str), `_LOG_FORMAT` (str), `_MAX_BYTES`/`_BACKUP_COUNT` (int). No internal dependencies; consumed by `main.py`.
