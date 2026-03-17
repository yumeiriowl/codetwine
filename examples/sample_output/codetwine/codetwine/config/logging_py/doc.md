# Design Document: codetwine/config/logging.py

## Overview & Purpose

# Overview & Purpose

## Role & Responsibility

This module centralizes all logging configuration for the `codetwine` package. It exists as a dedicated file so that any entry point (such as `main.py`) can call a single function to establish consistent logging behavior across the entire application, rather than duplicating handler and formatter setup in multiple places.

The module is responsible for:

- Defining where log files are written (`logs/` under the repository root)
- Establishing a uniform log format for all handlers
- Configuring a console handler (WARNING and above) and a rotating file handler (all messages at the configured level)
- Suppressing noisy output from external libraries (`httpx`, `httpcore`, `LiteLLM`) by capping them at WARNING level

---

## Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int = logging.INFO` | `None` | Configures the root logger with a console handler and a rotating file handler; call once at application startup |

> **Note:** `_SkipBlankFormatter` is a private class (prefixed with `_`) and is not part of the public API. It is an internal implementation detail used exclusively within `setup_logging`.

---

## Design Decisions

- **Single call-site initialization:** `setup_logging()` operates on the root logger, meaning one call propagates the configuration to all loggers in the application. The dependent `main.py` calls it as the very first statement in `main()`, consistent with this intent.
- **Asymmetric handler levels:** The console handler is fixed at `WARNING` regardless of the `level` argument, while the file handler inherits the root logger's level. This keeps the console quiet during normal operation while preserving detailed records in the log file.
- **Blank-line suppression via custom `Formatter`:** `_SkipBlankFormatter` overrides `format()` to return an empty string for whitespace-only messages, preventing blank entries from cluttering the log file. This is applied to both handlers through a single shared formatter instance.
- **Rotating file handler:** `RotatingFileHandler` with a 1 MiB cap (`_MAX_BYTES = 1_048_576`) and five backups (`_BACKUP_COUNT = 5`) bounds disk usage without losing recent history.
- **Module-level constants:** `_LOG_DIR`, `_LOG_FORMAT`, `_MAX_BYTES`, and `_BACKUP_COUNT` are defined as private module-level constants, keeping configuration values in one place and making them easy to adjust without touching the function logic.

## Definition Design Specifications

# Definition Design Specifications

---

## `_SkipBlankFormatter` (class)

**Inherits:** `logging.Formatter`

A custom formatter that suppresses log records whose message body is entirely whitespace. This exists to prevent noisy blank lines from polluting the log file when upstream code emits empty or whitespace-only log calls.

### `_SkipBlankFormatter.format`

| Item | Detail |
|---|---|
| **Argument** | `record: logging.LogRecord` — the log record to be formatted |
| **Returns** | `str` — formatted log string, or `""` if the message is whitespace-only |

**Design decision:** Returns an empty string rather than raising an exception or dropping the record at the handler level. This keeps suppression transparent to all handlers that share this formatter.

**Edge case / constraint:** The check matches both the empty string `""` and the single newline `"\n"` after stripping. Any message containing at least one non-whitespace character passes through unaffected.

---

## `setup_logging` (function)

| Item | Detail |
|---|---|
| **Argument** | `level: int` — root logger level; defaults to `logging.INFO` |
| **Returns** | `None` |

Configures the application-wide logging pipeline by attaching a console handler and a rotating file handler to the root logger. This function is the single authoritative place to initialize logging, intended to be called exactly once at the start of each entry point (e.g., `main()`).

**Design decisions:**

- **Console handler threshold is `WARNING`:** Only actionable alerts surface to the terminal; verbose `INFO`/`DEBUG` output is confined to the log file, keeping the user-facing console clean.
- **`RotatingFileHandler` with `maxBytes=1_048_576` and `backupCount=5`:** Bounds log disk usage to approximately 6 MB total while retaining recent history, making the setup viable in long-running or resource-constrained environments.
- **Both handlers share a single `_SkipBlankFormatter` instance:** Blank-line suppression is applied uniformly without duplicating formatter configuration.
- **External library loggers (`httpx`, `httpcore`, `LiteLLM`) are capped at `WARNING`:** Prevents third-party verbose output from flooding the log file regardless of the `level` argument passed by the caller.
- **Log directory is created with `exist_ok=True`:** Allows the function to be called in a fresh checkout without requiring manual directory setup.

**Edge cases / constraints:**

- Calling this function multiple times on the same process will add duplicate handlers to the root logger; callers are responsible for invoking it only once.
- The `level` argument controls the root logger and therefore the file handler's effective floor, but the console handler is unconditionally fixed at `WARNING` and is not influenced by `level`.
- `_LOG_DIR` is resolved relative to this file's location at import time; the resulting path depends on the installed directory structure remaining intact.

## Dependency Description

## Dependency Description

### Dependencies (what this file uses)

This file has no project-internal file dependencies. All imports (`os`, `logging`, `logging.handlers.RotatingFileHandler`) are standard library components. No other files within the project are imported or used.

### Dependents (what uses this file)

- **`main.py`**: Calls `setup_logging()` at the start of the `main()` entry point function to initialize both console and rotating file logging before any other application logic runs. The dependency is unidirectional — `main.py` depends on this file, but this file has no knowledge of or reference to `main.py`.

## Data Flow

# Data Flow

## Overview

```
Caller (main.py)          setup_logging()              Logging Infrastructure
─────────────────         ───────────────              ──────────────────────
main() invokes      ───►  Configures root logger  ───► Console (stderr): WARNING+
setup_logging()           with two handlers       ───► File: logs/codetwine.log (all levels)
```

---

## Input Data

| Source | Data | Format |
|--------|------|--------|
| Caller | `level` parameter | `int` (logging level constant, default: `logging.INFO`) |
| `logging.LogRecord` | Log message text | `str` passed via `.getMessage()` |
| Environment / filesystem | Log output directory | Path resolved relative to this file's location |

---

## Transformation Flow

```
LogRecord.getMessage()
        │
        ▼
_SkipBlankFormatter.format()
        │
        ├─ .strip() == "" or "\n"  ──► returns ""  (suppressed / not written)
        │
        └─ otherwise               ──► super().format()
                                           │
                                           ▼
                              "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
                                           │
                              ┌────────────┴────────────┐
                              ▼                         ▼
                     ConsoleHandler               RotatingFileHandler
                     (WARNING and above)          (root logger level, default INFO)
```

---

## Output

| Destination | Handler Type | Level Filter | Format |
|-------------|-------------|--------------|--------|
| Console (stderr) | `StreamHandler` | `WARNING` and above | `_LOG_FORMAT` via `_SkipBlankFormatter` |
| `logs/codetwine.log` | `RotatingFileHandler` | Root logger level (default `INFO`) | `_LOG_FORMAT` via `_SkipBlankFormatter` |

**File rotation settings:**

| Parameter | Value |
|-----------|-------|
| `maxBytes` | 1,048,576 (1 MiB) |
| `backupCount` | 5 |
| `encoding` | UTF-8 |

**Log directory path** (resolved at module load time):
```
<repo_root>/logs/codetwine.log
# Computed as: dirname(__file__) / .. / .. / logs/
```

---

## Key Data Structures

| Name | Type | Purpose |
|------|------|---------|
| `_SkipBlankFormatter` | `logging.Formatter` subclass | Intercepts `LogRecord` formatting; returns `""` for whitespace-only messages to suppress blank log lines |
| `_LOG_DIR` | `str` (path) | Absolute path to the log output directory, computed once at module load |
| `_LOG_FORMAT` | `str` | Format template applied uniformly to both handlers |
| `_MAX_BYTES` / `_BACKUP_COUNT` | `int` | Control rotating file size (1 MiB) and backup file count (5) |

---

## External Library Log Level Side Effect

`setup_logging()` also applies a hard `WARNING` floor to three external loggers, preventing their `DEBUG`/`INFO` output from reaching either handler regardless of the root logger level:

```
httpx   ──┐
httpcore ─┼──► setLevel(WARNING)
LiteLLM ──┘
```

## Error Handling

# Error Handling

## Overall Strategy

This module adopts a **graceful degradation** approach to error handling. The logging infrastructure is designed to remain non-disruptive to the application: configuration decisions (such as suppressing blank-line messages silently) are made at the formatter level rather than raising exceptions. The module does not explicitly catch or re-raise errors, delegating any unhandled failures (e.g., filesystem errors during log directory creation) to the Python standard library and the calling code.

## Error Patterns and Handling Policies

| Error Type | Handling | Impact |
|---|---|---|
| Log directory creation failure | Delegated to `os.makedirs` (no explicit catch); any `OSError` propagates to the caller | `setup_logging()` raises, preventing the application from starting if the log directory cannot be created |
| `RotatingFileHandler` initialization failure | No explicit catch; propagates to the caller | Same as above — the application fails at startup |
| Blank or whitespace-only log messages | Silently suppressed by `_SkipBlankFormatter.format()` returning an empty string | The message is dropped without any exception or notification |
| External library log noise | Mitigated by explicitly setting `WARNING` level on known noisy loggers (`httpx`, `httpcore`, `LiteLLM`) | Excessive debug output from third-party libraries is filtered; no error is raised |

## Design Considerations

- **No defensive wrapping around `setup_logging()`**: Because this function is called once at application startup (as seen in `main.py`), any failure is intentionally allowed to propagate. A logging setup failure is treated as a fatal condition, consistent with a fail-fast posture at the initialization boundary.
- **Silent suppression of blank messages**: The decision to return an empty string rather than raise or warn is a deliberate policy to keep log files clean without interrupting the logging pipeline.
- **No duplicate-handler guard**: The module does not check whether handlers have already been registered on the root logger before adding new ones. Calling `setup_logging()` more than once would result in duplicate handlers, but this is not defended against, reflecting the assumption that it is called exactly once.

## Summary

## codetwine/config/logging.py

Centralizes logging configuration for the application. Exposes one public function, `setup_logging(level=logging.INFO)`, which attaches a console handler (WARNING+) and a rotating file handler (INFO+, 1 MiB cap, 5 backups) to the root logger. Both handlers share a `_SkipBlankFormatter` instance that silently suppresses whitespace-only messages. External library loggers (`httpx`, `httpcore`, `LiteLLM`) are capped at WARNING. Key constants: `_LOG_DIR`, `_LOG_FORMAT`, `_MAX_BYTES`, `_BACKUP_COUNT`. Intended to be called once at application startup; no internal project dependencies.
