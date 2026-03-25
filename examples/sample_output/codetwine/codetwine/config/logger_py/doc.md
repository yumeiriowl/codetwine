# Design Document: codetwine/config/logger.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Configures the application-wide logging system by attaching a console handler and a rotating file handler to the root logger with appropriate log levels and formatting.

## 2. When to Use This Module

- **At application startup in an entry point** (e.g., `main()` in `main.py`): call `setup_logging()` once before any other application logic runs to ensure all subsequent log output is captured to both the console and the rotating log file.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int` (default: `logging.INFO`) | `None` | Attaches a console handler (WARNING and above) and a rotating file handler (at the configured level) to the root logger, creating the log directory if necessary. |

## 4. Design Decisions

- **Single call convention**: `setup_logging()` is designed to be called exactly once at the start of an entry point. Because it unconditionally appends handlers to the root logger, calling it multiple times would result in duplicate log output.
- **Split log levels between handlers**: The console handler is fixed at `WARNING` while the file handler inherits the `level` argument (default `INFO`). This keeps console noise low while preserving detailed records in the log file.
- **Suppressed blank-line messages**: A custom formatter (`_SkipBlankFormatter`) silences log records whose message is whitespace-only or a bare newline, preventing cosmetic blank entries in the log file without altering the log level or filtering logic.
- **Forced WARNING floor for noisy libraries**: `httpx`, `httpcore`, and `LiteLLM` loggers are explicitly capped at `WARNING` regardless of the root level, isolating them from the application's own verbosity setting.
- **Log directory location**: The log directory is resolved relative to the package root (`codetwine/logs/`), making the path independent of the current working directory when the application is invoked.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value | Purpose |
|---|---|---|---|
| `_LOG_DIR` | `str` | Resolved at import time | Absolute path to the `logs/` directory located two levels above `__file__`, at the repository root. |
| `_LOG_FORMAT` | `str` | Fixed format string | Shared log format string applied to all handlers; includes timestamp, level, logger name, and message. |
| `_MAX_BYTES` | `int` | 1,048,576 (1 MiB) | Maximum size of a single rotating log file before rollover. |
| `_BACKUP_COUNT` | `int` | 5 | Number of rotated backup log files retained before the oldest is discarded. |

All constants are module-private (underscore-prefixed) and are not intended for external access.

---

## Class: `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** A custom log formatter that suppresses output for log records whose message body is entirely whitespace or empty, preventing blank lines from polluting the log file.

**When to use:** Instantiated internally by `setup_logging`; callers never interact with this class directly.

**Design decisions:**
- Inherits from `logging.Formatter` and overrides only `format`, delegating all non-blank records to the parent implementation unchanged.
- Returns an empty string rather than raising an exception or filtering at the handler level, keeping the suppression logic self-contained within the formatter.

**Constraints & edge cases:**
- A message containing only `"\n"` is treated as blank and suppressed.
- Messages with any non-whitespace character pass through normally.
- Because blank records return `""`, any handler that writes the return value verbatim will write nothing; handlers that unconditionally append newlines may still produce a bare newline.

---

### `_SkipBlankFormatter.format`

**Signature:** `def format(self, record: logging.LogRecord) -> str`

| Parameter | Type | Description |
|---|---|---|
| `record` | `logging.LogRecord` | The log record produced by a logging call. |

**Returns:** `str` — The fully formatted log line, or an empty string if the record's message is whitespace-only.

**Responsibility:** Acts as the single decision point that either discards a blank record or delegates formatting to the standard `Formatter`.

---

## Function: `setup_logging`

**Signature:** `def setup_logging(level: int = logging.INFO) -> None`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `level` | `int` | `logging.INFO` | Numeric log level applied to the root logger and the file handler. |

**Returns:** `None`

**Responsibility:** Performs one-time global logging configuration by attaching a console handler and a rotating file handler to the root logger, and suppressing verbose output from specific third-party libraries.

**When to use:** Called once at the start of an application entry point (e.g., `main()` in `main.py`) before any logging calls are made.

**Design decisions:**

- **Split verbosity by handler:** The console handler is fixed at `WARNING` regardless of `level`, while the file handler inherits the root logger's level, ensuring that detailed diagnostic output goes only to the file.
- **Shared formatter instance:** A single `_SkipBlankFormatter` instance is reused across both handlers to keep blank-suppression behaviour consistent.
- **Rotating file handler:** `RotatingFileHandler` bounds disk usage to approximately `_MAX_BYTES × (_BACKUP_COUNT + 1)` bytes total.
- **Third-party library suppression:** `httpx`, `httpcore`, and `LiteLLM` loggers are explicitly capped at `WARNING` to prevent their verbose output from filling the log file at lower root levels.
- **Directory creation:** `_LOG_DIR` is created on demand with `exist_ok=True`, so no manual setup is required before calling this function.

**Constraints & edge cases:**

- Calling `setup_logging` multiple times will add duplicate handlers to the root logger; the function provides no idempotency guard.
- `level` affects only the root logger and file handler floor; the console handler remains at `WARNING` unconditionally.
- The log file is always named `codetwine.log` and always written to `_LOG_DIR`; neither path is configurable through the public API.
- File encoding is fixed to UTF-8.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

This file has **no project-internal module dependencies**. It imports only from the Python standard library (`os`, `logging`, `logging.handlers`) and no other modules within the codetwine project are imported.

## Dependents (modules that import this file)

- `main.py` → `codetwine/config/logger_py/logger.py` : imports and calls `setup_logging()` once at the start of the `main()` entry point function to initialize both console and rotating file log output for the application.

## Dependency Direction

| Relationship | Direction | Description |
|---|---|---|
| `main.py` → `logger.py` | Unidirectional | `main.py` depends on `logger.py` to obtain logging configuration. `logger.py` has no knowledge of `main.py` and does not import from it. |

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `level` | Caller argument (e.g., `main()` in `main.py`) | `int` (Python `logging` level constant, default `logging.INFO`) |
| `__file__` | Python runtime | File system path used to derive `_LOG_DIR` at module load time |
| `_LOG_DIR` | Computed at module load from `__file__` | Absolute directory path (`<repo_root>/logs/`) |
| `_LOG_FORMAT` | Module-level constant | String (`"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`) |
| `_MAX_BYTES` | Module-level constant | `int` (`1_048_576`) |
| `_BACKUP_COUNT` | Module-level constant | `int` (`5`) |
| `record.getMessage()` | Python logging infrastructure | Raw log message string supplied at call time by any module writing a log entry |

---

## 2. Transformation Overview

```
Caller invokes setup_logging(level)
        │
        ▼
Stage 1 — Root logger configuration
  Root logger retrieved; its level set to the supplied `level` value.
        │
        ▼
Stage 2 — Formatter construction
  A single _SkipBlankFormatter instance is built from _LOG_FORMAT.
  This formatter is shared by both handlers created in later stages.
        │
        ├──────────────────────────────────┐
        ▼                                  ▼
Stage 3a — Console handler             Stage 3b — File handler
  StreamHandler created.                Log directory created on disk.
  Level pinned to WARNING.              RotatingFileHandler created,
  Formatter attached.                   targeting <_LOG_DIR>/codetwine.log,
  Handler registered on root logger.    with _MAX_BYTES / _BACKUP_COUNT limits.
                                        Formatter attached.
                                        Handler registered on root logger.
        │                                  │
        └──────────────┬───────────────────┘
                       ▼
Stage 4 — External library suppression
  Named loggers ("httpx", "httpcore", "LiteLLM") each have their
  levels forced to WARNING, preventing their lower-severity records
  from propagating further.
                       │
                       ▼
Stage 5 — Per-record filtering (_SkipBlankFormatter.format)
  At log-write time, each LogRecord's message is stripped; if the
  result is "" or "\n", format() returns "" so the handler emits
  no output. Otherwise the record is formatted normally.
```

---

## 3. Outputs

| Output | Kind | Destination | Format |
|---|---|---|---|
| Root logger configuration | Side effect | Python `logging` global state | Two handlers attached; root level set |
| Console output | Side effect | `stderr` (StreamHandler default) | `_LOG_FORMAT` string; WARNING and above only |
| `logs/codetwine.log` | File write | `<repo_root>/logs/codetwine.log` | UTF-8 text, one line per record, rotated at 1 MB, up to 5 backups |
| Suppressed library logs | Side effect | Python `logging` global state | Named loggers ("httpx", "httpcore", "LiteLLM") level raised to WARNING |
| Blank-line records | Side effect (suppression) | Neither console nor file | Empty string returned by formatter; handler emits nothing |

`setup_logging` has no return value (`None`).

---

## 4. Key Data Structures

### `_SkipBlankFormatter` (extends `logging.Formatter`)

No additional fields beyond the parent class; behaviour is expressed through the overridden `format` method.

| Aspect | Type | Purpose |
|---|---|---|
| Inherited `_fmt` | `str` | Holds `_LOG_FORMAT` passed at construction |
| Input `record` | `logging.LogRecord` | Carries the raw message and metadata for one log event |
| Return value of `format` | `str` | Formatted log line, or `""` to signal the handler to emit nothing |

### Module-level configuration constants

| Name | Type | Purpose |
|---|---|---|
| `_LOG_DIR` | `str` | Absolute path to the `logs/` directory derived from the module's own location |
| `_LOG_FORMAT` | `str` | `strftime`/`%`-style format string applied to every log record |
| `_MAX_BYTES` | `int` | Maximum size of `codetwine.log` before rotation (`1_048_576` bytes) |
| `_BACKUP_COUNT` | `int` | Number of rotated backup files to retain (`5`) |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** approach combined with **silent suppression** for specific undesirable log content. The module does not define explicit exception handling (no try-except blocks); instead, it relies on the Python standard library's built-in error propagation for infrastructure-level failures (e.g., inability to create the log directory or open the log file). For application-level concerns, the primary policy is to silently discard log records that consist solely of whitespace or blank lines, allowing the logging pipeline to continue uninterrupted for all other records.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Blank / whitespace-only log message | A log record whose message content is empty or consists entirely of whitespace or newline characters | `_SkipBlankFormatter.format()` returns an empty string, suppressing the record from being written | Yes – the logging pipeline continues normally for subsequent records | The offending record is silently dropped; no output is written to console or file |
| Log directory creation failure | `os.makedirs(_LOG_DIR, ...)` raises an `OSError` (e.g., insufficient permissions) | Not caught; exception propagates to the caller (`setup_logging` → `main()`) | No – process terminates with an unhandled exception | Application startup fails entirely |
| Log file open / write failure | `RotatingFileHandler` cannot create or write to `codetwine.log` (e.g., disk full, permission denied) | Not caught; exception propagates to the caller | No – process terminates with an unhandled exception | Application startup fails entirely |

---

## 3. Design Notes

- **Intentional absence of exception handling for I/O failures:** The module treats log infrastructure as a hard dependency. If the log directory or file cannot be established, the failure is allowed to propagate immediately to the entry point (`main()`), reflecting a fail-fast stance for setup-phase errors.
- **Suppression scoped to formatting only:** Blank-message suppression is implemented at the `Formatter` level rather than at the handler or logger level. This means the suppression applies uniformly to both the console handler and the file handler without requiring conditional logic in either.
- **External library noise reduction as a policy concern:** Setting third-party loggers (`httpx`, `httpcore`, `LiteLLM`) to `WARNING` is treated as a configuration-time policy, not a runtime error condition. No error handling is applied to these assignments.
- **`setup_logging` is designed for single invocation:** It is called once at `main()` startup. No guard against duplicate handler registration is present, which means repeated calls would accumulate handlers—but this is not treated as an error condition within the module itself.

## Summary

**codetwine/config/logger.py** — Configures application-wide logging once at startup. Public API: `setup_logging(level: int = logging.INFO) -> None`, which attaches a `StreamHandler` (WARNING+) and `RotatingFileHandler` (at `level`) to the root logger. Internal class `_SkipBlankFormatter(logging.Formatter)` suppresses whitespace-only records. Key constants: `_LOG_DIR` (str), `_LOG_FORMAT` (str), `_MAX_BYTES` (int, 1 MiB), `_BACKUP_COUNT` (int, 5). Suppresses `httpx`, `httpcore`, and `LiteLLM` loggers to WARNING.
