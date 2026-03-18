# Design Document: codetwine/config/logging.py

## Overview & Purpose

# Overview & Purpose

## Role and Responsibility

This module serves as the **centralized logging configuration** for the entire `codetwine` package. It exists as a dedicated file to consolidate all logging setup logic into a single location, ensuring consistent formatting, handler configuration, and log-level policies across every component that imports a logger. By isolating this configuration, entry-point modules (such as `main.py`) need only call a single function once to activate both console and rotating file output, rather than duplicating handler setup in multiple places.

The module establishes two output channels simultaneously:
- A **console handler** that emits only `WARNING`-level messages and above.
- A **rotating file handler** that records messages at the configured level (defaulting to `INFO`) into `logs/codetwine.log` under the repository root, rotating the file when it reaches 1 MiB and retaining up to five backup files.

It also enforces quieter log levels for known verbose external libraries (`httpx`, `httpcore`, `LiteLLM`), keeping application logs uncluttered.

---

## Public Interface

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int = logging.INFO` | `None` | Configures the root logger with a console handler (WARNING+) and a rotating file handler, then restricts external library loggers to WARNING level. |

> **Note:** `_SkipBlankFormatter` is a private helper class (prefixed with `_`) and is not part of the public interface. It is used internally by `setup_logging` to attach a formatter that silently drops log records whose message is entirely whitespace or blank lines.

---

## Design Decisions

- **Single-call initialization pattern:** `setup_logging()` targets the root logger, so one call at the start of `main()` propagates the configuration to all named loggers throughout the package automatically.
- **Custom formatter with blank-line suppression:** `_SkipBlankFormatter` overrides `format()` to return an empty string for whitespace-only messages, preventing spurious blank entries in log output without requiring callers to filter their messages.
- **Rotating file handler:** `RotatingFileHandler` with `maxBytes=1_048_576` (1 MiB) and `backupCount=5` bounds total disk usage to approximately 6 MiB while preserving recent history.
- **Separation of console and file verbosity:** The console handler is fixed at `WARNING`, while the file handler inherits the level passed to `setup_logging` (default `INFO`), allowing detailed records in the file without polluting terminal output during normal operation.

## Definition Design Specifications

# Definition Design Specifications

---

## `_SkipBlankFormatter` (class)

**Inherits:** `logging.Formatter`

**Responsibility:** A custom formatter that suppresses log entries whose message content is entirely whitespace or blank. This prevents noise in log files caused by empty or newline-only messages emitted during normal program execution.

### `_SkipBlankFormatter.format`

| Item | Detail |
|---|---|
| **Arguments** | `record: logging.LogRecord` — the log record to be formatted |
| **Returns** | `str` — the formatted log string, or an empty string if the message is whitespace-only |

**Design decision:** Returns an empty string rather than raising an exception or dropping the record at the handler level, keeping suppression logic self-contained within the formatter and applicable uniformly to all handlers that use it.

**Constraint:** A message is considered suppressible only if `getMessage().strip()` equals `""` or `"\n"`. Messages containing any non-whitespace character are passed through normally.

---

## `setup_logging` (function)

| Item | Detail |
|---|---|
| **Arguments** | `level: int` — the log level applied to the root logger; defaults to `logging.INFO` |
| **Returns** | `None` |

**Responsibility:** Initializes the application-wide logging configuration with a console handler and a rotating file handler, intended to be called exactly once at application startup (e.g., at the top of `main()`).

**Design decisions:**

- **Console handler threshold is `WARNING`**: Only high-severity messages surface to the terminal, keeping interactive output clean while full detail is preserved in the log file.
- **`RotatingFileHandler`**: Bounded log growth via a 1 MiB per-file limit and a maximum of 5 backup files, preventing unbounded disk usage in long-running or repeated executions.
- **Shared `_SkipBlankFormatter` instance**: Both handlers use the same formatter instance, ensuring consistent blank-line suppression behavior across all outputs.
- **External library log capping**: `httpx`, `httpcore`, and `LiteLLM` loggers are explicitly set to `WARNING`, preventing verbose debug output from third-party libraries from polluting the log file even when `level` is set below `WARNING`.
- **Log directory**: Resolved relative to this module's own file path, making the location stable regardless of the working directory at call time.

**Precondition:** Must be called before any module-level loggers emit messages to ensure handlers are registered. Calling it more than once will add duplicate handlers to the root logger, which is not guarded against internally.

**Edge case:** If the `logs/` directory cannot be created (e.g., due to permission errors), `os.makedirs` will raise an `OSError` and setup will fail before any log file is written.

## Dependency Description

# Dependency Description

## Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports (`os`, `logging`, `logging.handlers.RotatingFileHandler`) are standard library components.

## Dependents (what uses this file)

- **main.py** uses `setup_logging` to initialize the logging system at the start of the application entry point. It calls `setup_logging()` as the first action inside `main()`, ensuring that both console and rotating file output are configured before any subsequent processing (argument parsing, directory resolution, etc.) begins.

## Direction of Dependency

The dependency is **unidirectional**: `main.py` depends on this file to obtain logging configuration functionality, while this file has no knowledge of or reference back to `main.py`.

## Data Flow

# Data Flow

## Overview

```
Caller (main.py)
     │
     ▼
setup_logging(level=logging.INFO)
     │
     ├─── [Input] level: int (logging level constant)
     │
     ├─── Constructs _SkipBlankFormatter(_LOG_FORMAT)
     │         │
     │         └─── Shared by both handlers below
     │
     ├─── Console Handler ──────────────────────────────► stderr/stdout
     │         filter: WARNING and above
     │
     └─── File Handler ─────────────────────────────────► logs/codetwine.log
               filter: root logger level (INFO by default)
               rotation: 1 MB max, 5 backups
```

## Input

| Item | Type | Source | Description |
|---|---|---|---|
| `level` | `int` | Caller (`main.py`) | Python logging level constant; defaults to `logging.INFO` |
| `record.getMessage()` | `str` | Python logging machinery | Raw log message passed through each handler |

## Transformation Flow

```
LogRecord
    │
    ▼
_SkipBlankFormatter.format(record)
    │
    ├── record.getMessage().strip() in ("", "\n")
    │       │
    │       ├── True  ──► return ""  (message suppressed; handler emits nothing)
    │       │
    │       └── False ──► super().format(record)
    │                         │
    │                         ▼
    │               "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    │                         │
    │                         ▼
    │               Formatted string (e.g., "2024-01-01 00:00:00,000 [INFO] mymodule: msg")
    │
    └──► Delivered to Console Handler (≥ WARNING) and/or File Handler (≥ root level)
```

## Output

| Destination | Format | Condition |
|---|---|---|
| Console (`StreamHandler`) | Formatted string via `_LOG_FORMAT` | Level ≥ `WARNING` and message is not blank |
| `logs/codetwine.log` (`RotatingFileHandler`) | Formatted string via `_LOG_FORMAT`, UTF-8 | Level ≥ root logger level and message is not blank |
| *(suppressed)* | Empty string `""` | Message is whitespace-only |

### Log File Rotation

| Parameter | Value |
|---|---|
| `maxBytes` | 1,048,576 (1 MB) |
| `backupCount` | 5 |
| `encoding` | `utf-8` |

## Key Data Structures

### `_SkipBlankFormatter`

Extends `logging.Formatter`. Its sole transformation is a blank-message gate: if `record.getMessage().strip()` equals `""` or `"\n"`, `format()` returns `""` causing the handler to emit nothing; otherwise delegates to the standard formatter with `_LOG_FORMAT`.

### Configuration Constants

| Constant | Value | Purpose |
|---|---|---|
| `_LOG_DIR` | `<repo_root>/logs/` | Directory where the rotating log file is written |
| `_LOG_FORMAT` | `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` | Shared format string for both handlers |
| `_MAX_BYTES` | `1_048_576` | Triggers log file rotation at 1 MB |
| `_BACKUP_COUNT` | `5` | Number of rotated backup files retained |

### External Logger Suppression

After handler setup, the following loggers are explicitly set to `WARNING` to suppress verbose third-party output regardless of the root logger level:

| Logger name | Set level |
|---|---|
| `httpx` | `WARNING` |
| `httpcore` | `WARNING` |
| `LiteLLM` | `WARNING` |

## Error Handling

# Error Handling

## Overall Strategy

This file adopts a **graceful degradation** approach to error handling. Rather than failing fast and halting execution, the logging infrastructure is designed to silently suppress or skip problematic input (specifically, blank or whitespace-only log messages) without interrupting the caller. No exceptions are raised from within the logging configuration itself; failures in log setup would propagate naturally as unhandled Python runtime errors to the caller (`main()` in `main.py`).

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Blank or whitespace-only log message | `_SkipBlankFormatter.format()` returns an empty string, suppressing the message | Message is silently dropped; no exception is raised; normal execution continues |
| Log directory does not exist | `os.makedirs(_LOG_DIR, exist_ok=True)` creates the directory on demand | Directory is created transparently; no error is surfaced to the caller |
| Filesystem or permission errors during handler setup | No explicit handling; Python runtime exception propagates to the caller | `setup_logging()` call in `main()` would fail with an unhandled exception, halting the application |

## Design Considerations

- **Silent suppression of blank messages** is an intentional policy choice implemented via a custom `Formatter` subclass rather than at the handler or logger level. This keeps the suppression logic localized and reusable across both the console and file handlers, both of which share the same formatter instance.
- **No defensive guards around handler registration** means that calling `setup_logging()` multiple times would attach duplicate handlers to the root logger. The design assumes a single call at application startup, as documented in the function's docstring.
- **External library log levels** are clamped to `WARNING` as a policy-level mitigation against noise from third-party dependencies (`httpx`, `httpcore`, `LiteLLM`), but this is enforced only after handlers are attached—no error handling covers the case where those logger names do not exist (which is harmless, as `logging.getLogger()` always returns a logger object).

## Summary

## codetwine/config/logging.py

Centralizes logging configuration for the entire package. Exposes one public function, `setup_logging(level=logging.INFO)`, which attaches a console handler (WARNING+) and a rotating file handler (INFO+, 1 MiB limit, 5 backups) to the root logger. Both handlers share a `_SkipBlankFormatter` instance that silently suppresses whitespace-only messages. Log files are written to `logs/codetwine.log` relative to this module. External library loggers (`httpx`, `httpcore`, `LiteLLM`) are capped at WARNING to reduce noise. Called once at application startup by `main.py`.
