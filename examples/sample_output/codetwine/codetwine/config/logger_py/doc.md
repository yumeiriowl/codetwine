# Design Document: codetwine/config/logger.py

## Overview & Purpose

# Overview & Purpose

## 1. Module Summary

Configures the application-wide logging system by attaching a rotating file handler and a console handler to the root logger, applying a custom formatter that suppresses blank-line messages.

## 2. When to Use This Module

- **At application startup in an entry point** (e.g., `main()` in `main.py`): call `setup_logging()` once before any other application logic runs. This ensures all subsequent log output across the entire application is routed to both the console (WARNING and above) and the rotating log file (`logs/codetwine.log`, all levels at or above the configured level).
- **When adjusting the application log verbosity**: pass a specific `level` integer (e.g., `logging.DEBUG`) to `setup_logging(level=...)` to change the minimum log level captured by the file handler.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int` (default: `logging.INFO`) | `None` | Attaches a console handler (WARNING+) and a rotating file handler to the root logger, applies the blank-line-suppressing formatter to both, and restricts `httpx`, `httpcore`, and `LiteLLM` loggers to WARNING level. |

## 4. Design Decisions

- **Single call at entry point**: `setup_logging()` is designed to be called exactly once at the start of `main()`. Configuring the root logger means all child loggers throughout the application inherit the handlers without each module needing its own handler setup.
- **Asymmetric handler levels**: The console handler is fixed at WARNING regardless of the `level` argument, while the file handler inherits the root logger's level. This keeps console output quiet in normal operation while preserving detailed logs in the file.
- **Blank-line suppression via custom formatter**: Rather than filtering records at the handler level, the `_SkipBlankFormatter` returns an empty string for whitespace-only messages. This prevents blank lines from polluting the log file without discarding the log record entirely.
- **Log directory relative to module location**: `_LOG_DIR` is resolved relative to `__file__`, placing `logs/` at the repository root regardless of the working directory when the process is started.

## Definition Design Specifications

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value | Purpose |
|---|---|---|---|
| `_LOG_DIR` | `str` | Computed path | Absolute path to the `logs/` directory, anchored two levels above this file's location (i.e., the repository root). |
| `_LOG_FORMAT` | `str` | Format string | Shared log record format applied to both console and file handlers. |
| `_MAX_BYTES` | `int` | `1,048,576` (1 MiB) | Maximum size of a single log file before rotation is triggered. |
| `_BACKUP_COUNT` | `int` | `5` | Number of rotated log file archives retained alongside the active log file. |

All four names are module-private (leading underscore) and are not intended to be imported or overridden by callers.

---

## Class: `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** Extends the standard `logging.Formatter` to suppress log entries whose message body consists entirely of whitespace or blank lines, preventing meaningless blank records from appearing in log output.

**When to use:** Never instantiated directly by callers; it is created internally by `setup_logging` and attached to both handlers.

**Design decisions:**
- Inherits `logging.Formatter` rather than replacing it, so all standard formatting logic is preserved for non-blank messages.
- The suppression signal is an empty string return value from `format()`, exploiting the convention that an empty string causes the handler to emit nothing meaningful.

**Constraints & edge cases:**
- A message that strips to either `""` or `"\n"` is treated as blank; any other non-empty content, including a single non-whitespace character, passes through normally.
- Module-private (leading underscore); not part of the public API.

### Special Method: `format`

| Item | Detail |
|---|---|
| **Signature** | `format(self, record: logging.LogRecord) -> str` |
| **Responsibility** | Intercepts the standard format pipeline to return an empty string for whitespace-only messages, delegating to the parent implementation for all other records. |
| **Return** | `str` — the fully formatted log line, or `""` to suppress the record. |

---

## Function: `setup_logging`

**Signature:** `setup_logging(level: int = logging.INFO) -> None`

- `level`: An integer log level constant from the `logging` module. Defaults to `logging.INFO`. Controls the minimum severity recorded by the root logger and the file handler.

**Responsibility:** Performs one-time configuration of the application-wide logging infrastructure, attaching both a console handler and a rotating file handler to the root logger.

**When to use:** Called once at application startup, specifically at the top of `main()` in entry-point modules (confirmed usage in `main.py`).

**Design decisions:**

| Decision | Rationale |
|---|---|
| Console handler threshold fixed at `WARNING` | Keeps terminal output quiet during normal operation; detailed records go only to the file. |
| File handler uses `RotatingFileHandler` | Prevents unbounded log file growth; controlled by `_MAX_BYTES` and `_BACKUP_COUNT`. |
| Single shared `_SkipBlankFormatter` instance | Both handlers use the same formatter object, ensuring consistent blank-line suppression and format string. |
| External library loggers explicitly capped at `WARNING` | Prevents verbose output from `httpx`, `httpcore`, and `LiteLLM` from flooding the log file regardless of the root level. |
| `os.makedirs(..., exist_ok=True)` before file handler creation | Ensures the `logs/` directory exists without raising an error if it was already created. |

**Constraints & edge cases:**
- Not idempotent: calling `setup_logging` multiple times on the same process will attach duplicate handlers to the root logger, resulting in repeated log entries.
- The file handler always writes to the fixed filename `codetwine.log` inside `_LOG_DIR`; the filename is not configurable by callers.
- The `level` parameter governs the root logger and the file handler implicitly (file handler inherits the root level); the console handler is independently fixed at `WARNING` and ignores `level`.
- Suppression of `httpx`, `httpcore`, and `LiteLLM` is unconditional regardless of the `level` argument passed by the caller.

## Dependency Description

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies exist. This file (`codetwine/config/logger_py/logger.py`) imports exclusively from the Python standard library (`os`, `logging`, `logging.handlers`) and no project-internal modules.

## Dependents (modules that import this file)

- `main.py` → `codetwine/config/logger_py/logger.py` : imports and calls `setup_logging()` once at the start of the `main()` entry point function to initialize both console and rotating file log output for the application.

## Dependency Direction

- The relationship between `main.py` and `codetwine/config/logger_py/logger.py` is **unidirectional**: `main.py` depends on this module to consume the `setup_logging` function, while this module has no knowledge of or reference back to `main.py`.

## Data Flow

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `level` | Caller argument (e.g., `main()` invokes `setup_logging()` with no arguments) | `int` (Python `logging` level constant; defaults to `logging.INFO`) |
| `__file__` | Python runtime | File system path used to compute `_LOG_DIR` at module load time |
| `_LOG_DIR` | Derived from `__file__` at module import | Absolute directory path string (`<repo_root>/logs/`) |
| `_LOG_FORMAT` | Module-level constant | Format string: `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` |
| `_MAX_BYTES` / `_BACKUP_COUNT` | Module-level constants | `int` values controlling log rotation (`1,048,576` bytes, `5` backups) |

---

## 2. Transformation Overview

```
[Module import]
    │
    ▼
Compute _LOG_DIR
  os.path.dirname(__file__) → navigate two levels up → append "logs/"
    │
    ▼
[setup_logging(level) called by main()]
    │
    ▼
Stage 1 — Root Logger Configuration
  Acquire root logger → set minimum level to `level` (default INFO)
    │
    ▼
Stage 2 — Formatter Construction
  Instantiate _SkipBlankFormatter with _LOG_FORMAT
  (Applied to both handlers; blank/whitespace-only messages are suppressed
   by returning "" from format())
    │
    ├──────────────────────────────────────┐
    ▼                                      ▼
Stage 3a — Console Handler             Stage 3b — File Handler
  StreamHandler                          Ensure _LOG_DIR exists (makedirs)
  Level: WARNING and above               RotatingFileHandler →
  Formatter: _SkipBlankFormatter           codetwine.log
  Attached to root logger                  maxBytes=1,048,576
                                           backupCount=5
                                           encoding=utf-8
                                         Level: inherits root (INFO)
                                         Formatter: _SkipBlankFormatter
                                         Attached to root logger
    │                                      │
    └──────────────┬───────────────────────┘
                   ▼
Stage 4 — External Library Log Suppression
  httpx, httpcore, LiteLLM loggers → forced to WARNING level
  (Prevents their DEBUG/INFO records from reaching handlers)
```

Log records subsequently emitted anywhere in the application travel through the root logger, are filtered by each handler's level threshold, formatted by `_SkipBlankFormatter` (blank messages become `""`), and dispatched to the console or the rotating file.

---

## 3. Outputs

| Output | Kind | Format / Destination |
|---|---|---|
| Console output | Side effect | Log records at `WARNING` or above written to `stderr` via `StreamHandler`, formatted as `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` |
| Log file | Side effect / file write | Records at the configured `level` (default `INFO`) and above written to `<repo_root>/logs/codetwine.log`; rotated when the file exceeds 1 MB, retaining up to 5 backup files; UTF-8 encoded |
| Suppressed blank messages | Side effect | `_SkipBlankFormatter.format()` returns `""` for whitespace-only messages, causing handlers to emit nothing for those records |
| `setup_logging` return value | Return value | `None` |

---

## 4. Key Data Structures

### `_SkipBlankFormatter` (subclass of `logging.Formatter`)

This is a behavioral wrapper; it holds no additional fields beyond its parent class. Its distinguishing logic is documented below for clarity.

| Attribute / Behavior | Type | Purpose |
|---|---|---|
| Inherited format string | `str` | `_LOG_FORMAT` passed at construction; controls final record rendering |
| `format(record)` gate | `str` → `""` | Returns empty string when `record.getMessage().strip()` is `""` or `"\n"`, suppressing blank-line log output |

### `logging.LogRecord` (consumed by `_SkipBlankFormatter.format`)

| Field | Type | Purpose |
|---|---|---|
| `getMessage()` result | `str` | The rendered log message text; checked for whitespace-only content before formatting |
| `levelname` | `str` | Severity label embedded in the formatted output |
| `name` | `str` | Logger name embedded in the formatted output |
| `asctime` | `str` | Timestamp embedded in the formatted output |

### Module-level configuration constants

| Name | Type | Purpose |
|---|---|---|
| `_LOG_DIR` | `str` | Absolute path to the `logs/` directory computed relative to this file's location |
| `_LOG_FORMAT` | `str` | `logging.Formatter` format string shared by both handlers |
| `_MAX_BYTES` | `int` | Maximum size of `codetwine.log` before rotation (`1,048,576`) |
| `_BACKUP_COUNT` | `int` | Number of rotated backup files to retain (`5`) |

## Error Handling

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** strategy combined with **silent suppression** for specific unwanted log content. The primary design philosophy is to remain unobtrusive: rather than raising exceptions or terminating the process when undesirable conditions occur, the module quietly filters out noise at the formatting layer. No explicit error handling (try-except blocks) is present in this file; the module delegates robustness to the Python standard library's `logging` and `os` modules and relies on their built-in behaviors for any infrastructure-level failures (e.g., directory creation, file opening).

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Blank or whitespace-only log message | A log record whose message consists entirely of whitespace or blank lines is passed to `_SkipBlankFormatter.format()` | The formatter returns an empty string, effectively suppressing the output silently | Yes | The blank message is not written to the log file or console; all other messages are unaffected |
| Log directory absent at setup time | `_LOG_DIR` does not exist when `setup_logging()` is called | `os.makedirs(_LOG_DIR, exist_ok=True)` creates the directory automatically before the file handler is attached | Yes | No impact on logging operation; directory is created transparently |
| Verbose output from external libraries (`httpx`, `httpcore`, `LiteLLM`) | These libraries emit log records below `WARNING` level | Their logger levels are explicitly set to `logging.WARNING`, suppressing `DEBUG` and `INFO` records from those namespaces | Yes | Lower-severity records from those libraries are silently discarded; `WARNING` and above pass through normally |

---

## 3. Design Notes

- **Suppression via formatter, not filtering:** Blank-line suppression is implemented inside `_SkipBlankFormatter.format()` by returning an empty string rather than by attaching a `logging.Filter`. This means the suppression is tied to the formatter itself and applies uniformly wherever that formatter is used (both console and file handlers share the same formatter instance).
- **No defensive coding around I/O failures:** The module contains no explicit handling for OS-level failures such as permission errors on directory creation or log file write failures. Any such failure propagates as an unhandled exception, which is consistent with a fail-fast posture for infrastructure setup errors that occur once at startup.
- **Asymmetric console and file levels:** The console handler is set to `WARNING` while the root logger is set to `INFO` (default). This design intentionally keeps detailed records in the log file without polluting standard output, but the strategy does not involve any error handling logic — it is a visibility policy rather than a fault-tolerance mechanism.

## Summary

**codetwine/config/logger.py** configures application-wide logging by attaching a rotating file handler and console handler to the root logger.

**Public:** `setup_logging(level: int = logging.INFO) -> None` — attaches both handlers, applies shared formatter, and caps `httpx`/`httpcore`/`LiteLLM` at WARNING.

**Key structures:** `_SkipBlankFormatter(logging.Formatter)` suppresses whitespace-only messages; constants `_LOG_DIR: str`, `_LOG_FORMAT: str`, `_MAX_BYTES: int` (1 MiB), `_BACKUP_COUNT: int` (5) configure rotation and formatting.
