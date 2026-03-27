# Design Document: codetwine/config/logger.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Configures the application-wide logging system by attaching a console handler and a rotating file handler to the root logger, providing a single entry point for all log output setup.

## 2. When to Use This Module

- **At application startup in an entry point** (e.g., `main.py`): Call `setup_logging()` once before any other application logic runs. This ensures that all subsequent `logging.getLogger(...)` calls throughout the codebase write to both the console and the rotating log file at `logs/codetwine.log`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int` (default: `logging.INFO`) | `None` | Attaches a `WARNING`-level console handler and an `INFO`-level rotating file handler to the root logger, creates the `logs/` directory if absent, and suppresses verbose output from `httpx`, `httpcore`, and `LiteLLM` to `WARNING`. |

## 4. Design Decisions

- **Single call contract**: `setup_logging()` is designed to be called exactly once at the start of `main()`. Calling it multiple times would add duplicate handlers to the root logger.
- **Split log levels by handler**: The console handler is intentionally restricted to `WARNING` and above, while the file handler inherits the root logger's level (default `INFO`). This keeps console output quiet during normal operation while preserving detailed records in the log file.
- **Blank-line suppression via custom formatter**: A private `_SkipBlankFormatter` is applied to both handlers. Any log record whose message is whitespace-only is silently dropped by returning an empty string from `format()`, preventing noise in both the console and the log file.
- **Rotating file handler with fixed limits**: The log file is capped at 1 MiB (`_MAX_BYTES = 1_048_576`) with up to 5 backup files (`_BACKUP_COUNT = 5`), bounding total disk usage to approximately 6 MiB without manual intervention.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value | Purpose |
|------|------|-------|---------|
| `_LOG_DIR` | `str` | Computed path | Absolute path to the `logs/` directory, resolved two levels above `__file__` (i.e., at the repository root). |
| `_LOG_FORMAT` | `str` | Format string | Shared log format string used by all handlers; includes timestamp, level, logger name, and message. |
| `_MAX_BYTES` | `int` | `1,048,576` (1 MiB) | Maximum size of a single rotating log file before rollover. |
| `_BACKUP_COUNT` | `int` | `5` | Number of rotated backup log files retained alongside the active log. |

All four constants are module-private (underscore-prefixed) and are not part of the public API.

---

## Class: `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** Extends the standard `logging.Formatter` to suppress log entries whose message body is entirely whitespace or empty, preventing blank lines from being written to the log file or console.

**When to use:** Instantiated internally by `setup_logging`; callers do not interact with it directly.

**Design decisions:**
- The suppression contract is implemented by returning an empty string rather than by raising an exception or filtering at the handler level, because `logging.Handler.emit` checks for an empty formatted string to decide whether to skip output.
- Inherits all formatting behavior from the parent class for non-blank messages, so no duplication of format logic is needed.

**Constraints & edge cases:**
- A message consisting solely of `"\n"` is also suppressed; multi-line messages where only *some* lines are blank are **not** suppressed.
- The class is module-private and not intended for use outside this module.

### Special Method: `format`

| Item | Detail |
|------|--------|
| Signature | `format(self, record: logging.LogRecord) -> str` |
| Returns | Formatted log string for non-blank messages; empty string `""` for whitespace-only messages. |
| Argument | `record` — a standard `logging.LogRecord` object representing a single log event. |

---

## Function: `setup_logging`

**Signature:** `setup_logging(level: int = logging.INFO) -> None`

**Responsibility:** Performs one-time configuration of the root logger, attaching a console handler and a rotating file handler, and suppresses verbose output from selected third-party libraries.

**When to use:** Called once at application startup, at the top of a `main()` entry point, before any other logging activity occurs. The dependent `main.py` calls it as the first statement in `main()`.

**Design decisions:**

- **Two-handler split:** The console handler is restricted to `WARNING` and above to keep terminal output quiet during normal operation, while the file handler inherits the root logger's configured level (default `INFO`) to capture full diagnostic detail.
- **Shared formatter instance:** A single `_SkipBlankFormatter` instance is reused by both handlers, ensuring consistent blank-line suppression and format across outputs.
- **Rotating file handler:** Uses `RotatingFileHandler` with size-based rollover (governed by `_MAX_BYTES` and `_BACKUP_COUNT`) rather than time-based rotation, capping total disk usage at approximately 6 MiB.
- **Third-party library suppression:** `httpx`, `httpcore`, and `LiteLLM` loggers are explicitly clamped to `WARNING` regardless of the root logger's level, preventing their `INFO`/`DEBUG` output from polluting the log file.
- **Log directory creation:** The `logs/` directory is created on demand during the call, so no manual setup step is required.

**Constraints & edge cases:**

- Calling `setup_logging` more than once will add duplicate handlers to the root logger, causing repeated log entries. It is the caller's responsibility to invoke it exactly once.
- The `level` parameter applies to the root logger and the file handler; the console handler's level is hard-coded to `WARNING` and is unaffected by `level`.
- The log file is always named `codetwine.log` and always written to `_LOG_DIR`; these are not configurable via arguments.
- The log file is written with UTF-8 encoding; environments where the log directory path is non-writable will raise an `OSError` at handler creation time.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `level` | `int` | `logging.INFO` | Minimum severity level captured by the root logger and written to the file. Standard `logging` level integers are accepted. |

**Return type:** `None` — configuration is applied as a side effect on the global root logger.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module imports are present in this file. All imports (`os`, `logging`, `logging.handlers.RotatingFileHandler`) are standard library components and are excluded from this description.

---

## Dependents (modules that import this file)

- `main.py` → `codetwine/config/logger_py/logger.py` : imports and calls `setup_logging()` once at the start of the `main()` entry point function to initialize both console and rotating file log output before any other processing begins.

---

## Dependency Direction

- The relationship between `main.py` and `codetwine/config/logger_py/logger.py` is **unidirectional**: `main.py` depends on this logger module, and this logger module has no dependency on `main.py` or any other project-internal module.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `level` parameter | Caller (e.g., `main.py` via `setup_logging()`) | `int` (Python `logging` level constant, defaults to `logging.INFO`) |
| `__file__` | Python runtime | File system path string used to resolve `_LOG_DIR` at module load time |
| Log records | Any module that calls `logging.getLogger(...).info/debug/warning/...` after setup | `logging.LogRecord` instances |

The module also reads no external configuration files; all constants (`_MAX_BYTES`, `_BACKUP_COUNT`, `_LOG_FORMAT`) are defined inline at module level and resolved when the module is imported.

---

## 2. Transformation Overview

```
setup_logging(level) called
        │
        ▼
[Stage 1: Root Logger Configuration]
  Obtain root logger → set its level to `level`

        │
        ▼
[Stage 2: Formatter Construction]
  _SkipBlankFormatter(_LOG_FORMAT) created
  → shared by both handlers
  → blank/whitespace-only log messages are suppressed (return "")
  → non-blank messages are formatted as:
    "YYYY-MM-DD HH:MM:SS,mmm [LEVEL] logger_name: message"

        │
        ├─────────────────────────────────┐
        ▼                                 ▼
[Stage 3a: Console Handler]       [Stage 3b: File Handler]
  StreamHandler                     _LOG_DIR created if absent
  level = WARNING                   RotatingFileHandler
  formatter attached                  → codetwine/logs/codetwine.log
                                      → maxBytes=1,048,576
                                      → backupCount=5
                                      → encoding=utf-8
                                    formatter attached
        │                                 │
        └─────────────────────────────────┘
                        │
                        ▼
[Stage 4: External Library Suppression]
  httpx, httpcore, LiteLLM loggers
  → level set to WARNING
  (prevents verbose third-party output from entering handlers)
```

When a log record is emitted anywhere in the application after `setup_logging()`:
- `_SkipBlankFormatter.format()` is invoked per handler; whitespace-only message bodies are filtered to an empty string (effectively suppressed at the handler level).
- Non-blank records at `WARNING` or above reach both the console and the file.
- Non-blank records below `WARNING` (e.g., `INFO`, `DEBUG`) reach only the file handler.

---

## 3. Outputs

| Output | Type | Destination | Notes |
|---|---|---|---|
| Formatted log lines | Text (`utf-8`) | `codetwine/logs/codetwine.log` | Rotated when file reaches 1 MB; up to 5 backup files retained |
| Formatted log lines | Text (stdout/stderr) | Console (stderr via `StreamHandler`) | Only `WARNING` and above |
| Configured root logger | Side effect | Python `logging` global state | Affects all loggers in the process |
| Suppressed third-party loggers | Side effect | Python `logging` global state | `httpx`, `httpcore`, `LiteLLM` capped at `WARNING` |

The function returns `None`; all outputs are side effects on the global logging subsystem and the file system.

---

## 4. Key Data Structures

### `_SkipBlankFormatter` (subclass of `logging.Formatter`)

This module does not define custom dataclasses or dicts. The one notable data structure is the formatter's interaction with `logging.LogRecord`:

| Field / Key | Type | Purpose |
|---|---|---|
| `record.getMessage()` | `str` | The rendered log message string; tested with `.strip()` to detect blank-only content |
| Return value of `format()` | `str` | Empty string `""` causes the handler to emit nothing; a non-empty string is the fully formatted log line written to the handler's output |

### Module-level Constants (scalar configuration values)

| Name | Type | Purpose |
|---|---|---|
| `_LOG_DIR` | `str` | Absolute path to the `logs/` directory, resolved relative to this file's location |
| `_LOG_FORMAT` | `str` | `strftime`-compatible format string applied to every log record |
| `_MAX_BYTES` | `int` | Maximum size (bytes) of `codetwine.log` before rotation (`1,048,576` = 1 MB) |
| `_BACKUP_COUNT` | `int` | Number of rotated backup log files to retain (`5`) |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** strategy combined with **silent suppression** for anticipated non-critical conditions. The primary error handling concern is filtering out blank or whitespace-only log messages before they reach the output destinations. No exceptions are caught or re-raised within this file; errors that arise from underlying infrastructure (e.g., filesystem access for the log directory or file handler initialization) are left to propagate naturally to the caller (`main()` in `main.py`).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Blank/whitespace log message | A log record whose message content is empty, whitespace-only, or a bare newline | `_SkipBlankFormatter.format()` returns an empty string, suppressing the record from being written | Yes (message is silently skipped) | No output is emitted for that record; all other logging continues normally |
| Log directory creation failure | `os.makedirs(_LOG_DIR)` fails (e.g., permission denied on the filesystem) | No handling; exception propagates to the caller (`main()`) | No | Process terminates at startup |
| Log file creation/rotation failure | `RotatingFileHandler` cannot open or rotate the log file | No handling; exception propagates to the caller (`main()`) | No | Process terminates at startup |
| Duplicate handler registration | `setup_logging()` is called more than once on the root logger | No guard is present; handlers accumulate on the root logger | Yes (process continues, but log output is duplicated) | Duplicate log entries appear in console and file output |

---

## 3. Design Notes

- **Blank-message suppression is the only active error policy.** The `_SkipBlankFormatter` class exists exclusively to prevent cosmetically meaningless entries from appearing in the log file. This is treated as a formatting concern rather than an error, and no exception is involved.
- **Infrastructure errors are intentionally unhandled.** Failures in directory creation or file handler setup are considered fatal preconditions for the application. Allowing them to propagate ensures that the caller receives an unambiguous signal that logging infrastructure is unavailable, consistent with a fail-fast posture at the process initialization boundary.
- **External library noise is controlled via level filtering, not error handling.** Setting `httpx`, `httpcore`, and `LiteLLM` loggers to `WARNING` is a signal-reduction measure, not an error response, and carries no error handling implications.
- **No idempotency guard exists for `setup_logging()`.** The design assumes `setup_logging()` is called exactly once per process lifetime, as reflected in the dependent `main()` entry point. The absence of a guard means repeated calls are an implicit usage contract violation rather than a handled error.

## Summary

**codetwine/config/logger.py** configures application-wide logging via a single call to `setup_logging(level: int = logging.INFO) -> None`, which attaches a WARNING-level console handler and an INFO-level rotating file handler (1 MiB, 5 backups) to the root logger. The private `_SkipBlankFormatter(logging.Formatter)` class suppresses whitespace-only log records by returning `""` from `format(record: logging.LogRecord) -> str`. Third-party loggers (`httpx`, `httpcore`, `LiteLLM`) are clamped to WARNING.
