# Design Document: codetwine/config/logger.py

# Overview & Purpose

## 1. Module Summary

Configures the application-wide logging system by setting up a rotating file handler and a console handler with separate log levels, so that all modules can obtain pre-configured loggers via the standard `logging` module.

## 2. When to Use This Module

- **At application startup in an entry point** (e.g., `main.py`): call `setup_logging()` once before any other application logic runs. This ensures that all subsequent calls to `logging.getLogger(...)` throughout the codebase emit records to both the console and the rotating log file under `logs/codetwine.log`.
- **When changing the application-wide log verbosity**: pass an explicit `level` argument to `setup_logging(level=logging.DEBUG)` to lower the threshold for what gets written to the log file, while the console threshold remains fixed at `WARNING`.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int` (default: `logging.INFO`) | `None` | Attaches a `WARNING`-level console handler and an `INFO`-level rotating file handler to the root logger, applies the shared formatter to both, and suppresses verbose output from `httpx`, `httpcore`, and `LiteLLM` to `WARNING`. |

## 4. Design Decisions

- **Single call contract**: `setup_logging` operates on the root logger and adds handlers unconditionally, so it is intended to be called exactly once per process. Calling it multiple times would attach duplicate handlers.
- **Asymmetric log levels**: the console handler is fixed at `WARNING` regardless of the `level` argument, while the file handler inherits the root logger's level. This deliberately keeps console output quiet during normal operation while preserving full detail in the log file.
- **Blank-line suppression via a custom formatter**: rather than filtering at the handler or logger level, a custom `Formatter` subclass returns an empty string for whitespace-only messages, preventing blank entries from cluttering the log file without discarding the record from the logging pipeline entirely.
- **Log directory placement**: the `logs/` directory is resolved relative to this file's location (`../../logs`), anchoring it at the repository root regardless of the working directory at runtime.

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value | Purpose |
|---|---|---|---|
| `_LOG_DIR` | `str` | Resolved path to `<repo_root>/logs/` | Absolute path to the directory where log files are written. Computed relative to this file's location by navigating two levels up from the `config/` package. |
| `_LOG_FORMAT` | `str` | `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` | Shared format string applied to both console and file handlers. |
| `_MAX_BYTES` | `int` | `1,048,576` (1 MiB) | Maximum size of a single rotating log file before rollover. |
| `_BACKUP_COUNT` | `int` | `5` | Number of backup log files retained after rollover. |

All names are prefixed with `_`, marking them as internal to this module and not part of the public API.

---

## Class: `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** Extends the standard `logging.Formatter` to suppress log records whose message content is entirely whitespace or blank, preventing empty lines from cluttering log output.

**When to use:** Instantiated internally by `setup_logging`; not intended for direct use outside this module.

**Design decisions:**
- Inherits from `logging.Formatter` and overrides only `format`, leaving all other formatting behavior unchanged.
- The suppression signal is an empty string return value rather than raising an exception or filtering at the handler level. Callers (handlers) that check the return value of `format` will receive `""` and should skip emission; this relies on standard handler behavior.

**Constraints & edge cases:**
- A message matching `""` or `"\n"` after stripping is suppressed. Messages containing non-whitespace characters alongside newlines are not suppressed.
- The `_` prefix marks this class as private; it should not be subclassed or instantiated outside this module.

---

### `_SkipBlankFormatter.format`

**Signature:** `format(self, record: logging.LogRecord) -> str`

**Responsibility:** Intercepts format requests to return an empty string for whitespace-only messages; delegates all other records to the parent formatter.

**Arguments:**

| Parameter | Type | Description |
|---|---|---|
| `record` | `logging.LogRecord` | The log record to be formatted. |

**Returns:** `str` — The formatted log string for normal records, or `""` for blank-line records.

**Constraints & edge cases:** Exactly two stripped values trigger suppression: `""` and `"\n"`. Any other content, including a single space followed by text, passes through normally.

---

## Function: `setup_logging`

**Signature:** `setup_logging(level: int = logging.INFO) -> None`

**Responsibility:** Performs one-time configuration of the root logger, attaching a console handler and a rotating file handler with consistent formatting, and restricts verbose output from known external libraries.

**When to use:** Called once at application startup, specifically at the beginning of `main()` in entry-point modules (confirmed usage in `main.py`).

**Arguments:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `level` | `int` | `logging.INFO` | The log level applied to the root logger, controlling which records are processed at all. |

**Returns:** `None`

**Design decisions:**

- **Dual-handler architecture:** A console handler is intentionally set to `WARNING` and above, while the file handler inherits the root logger's level (defaulting to `INFO`). This means informational detail is captured in the log file without cluttering console output during normal operation.
- **Shared formatter instance:** A single `_SkipBlankFormatter` instance is applied to both handlers, ensuring consistent blank-line suppression and formatting across outputs.
- **Selective external library silencing:** `httpx`, `httpcore`, and `LiteLLM` loggers are explicitly capped at `WARNING` to prevent third-party verbose output from polluting logs regardless of the root level.
- **Log directory creation:** The `logs/` directory is created on demand with `exist_ok=True`, so the function is safe to call even when the directory does not yet exist.
- **RotatingFileHandler settings:** File rollover is governed by `_MAX_BYTES` (1 MiB) and `_BACKUP_COUNT` (5), bounding total disk usage to approximately 6 MiB for the log file family.

**Constraints & edge cases:**
- Intended to be called exactly once. Calling it multiple times will attach duplicate handlers to the root logger, causing repeated log output.
- The `level` parameter governs the root logger only; the console handler's threshold is hardcoded to `WARNING` and is not influenced by `level`.
- Log files are written as UTF-8 encoded text.
- External library logger levels are set unconditionally, overriding any prior configuration for those loggers.

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies are present. `codetwine/config/logger.py` relies exclusively on standard library modules (`os`, `logging`, `logging.handlers`) and no project-internal modules are imported.

---

## Dependents (modules that import this file)

- `main.py` → `codetwine/config/logger.py` : imports and calls `setup_logging()` once at the start of the `main()` entry point function to initialize both console and file logging before any other application logic executes.

---

## Dependency Direction

- The relationship between `main.py` and `codetwine/config/logger.py` is **unidirectional**: `main.py` depends on `codetwine/config/logger.py`, while `codetwine/config/logger.py` has no knowledge of or dependency on `main.py`.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `level` | Caller argument (`setup_logging(level=...)`) | `int` (a `logging` module constant, e.g., `logging.INFO`) |
| `__file__` | Python runtime | String file path, used to derive `_LOG_DIR` at module load time |
| `_LOG_DIR` | Computed at module load from `__file__` | Absolute filesystem path string pointing to `logs/` under the repository root |
| `_LOG_FORMAT` | Module-level constant | String — `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` |
| `_MAX_BYTES` | Module-level constant | `int` — `1_048_576` |
| `_BACKUP_COUNT` | Module-level constant | `int` — `5` |

The sole public entry point is `setup_logging()`, called once from `main.py`'s `main()` function with no arguments (accepting the default `level=logging.INFO`).

---

## 2. Transformation Overview

```
[Call: setup_logging(level)]
        │
        ▼
[1. Root logger acquisition]
   logging.getLogger() → root_logger
   root_logger.setLevel(level)
        │
        ▼
[2. Formatter construction]
   _SkipBlankFormatter(_LOG_FORMAT) → formatter
   (shared by both handlers)
        │
        ├─────────────────────────────────────────────────┐
        ▼                                                 ▼
[3a. Console handler]                          [3b. File handler]
  StreamHandler()                              os.makedirs(_LOG_DIR)
  setLevel(WARNING)                            RotatingFileHandler(
  setFormatter(formatter)                        path=_LOG_DIR/codetwine.log,
  addHandler → root_logger                       maxBytes=_MAX_BYTES,
                                                 backupCount=_BACKUP_COUNT,
                                                 encoding="utf-8")
                                               setFormatter(formatter)
                                               addHandler → root_logger
        │
        ▼
[4. Third-party logger suppression]
   httpx / httpcore / LiteLLM → setLevel(WARNING)
```

**`_SkipBlankFormatter` sub-flow** (triggered per log record at emit time):

```
[logging.LogRecord]
        │
        ▼
  record.getMessage().strip() in ("", "\n")?
        ├── Yes → return ""   (record is silently dropped)
        └── No  → super().format(record) → formatted string
```

The formatter acts as a filter gate on each record before it is written to either destination.

---

## 3. Outputs

| Output | Type | Destination | Notes |
|---|---|---|---|
| Console log stream | Text lines | `stderr` (default `StreamHandler`) | Only `WARNING` and above |
| Log file | Rotating UTF-8 text file | `<repo_root>/logs/codetwine.log` | All records at or above `level` (default `INFO`); rotates at 1 MiB, keeps 5 backups |
| Mutated root logger | Side effect on `logging.Logger` | Global Python logging state | Two handlers attached, level set |
| Suppressed third-party loggers | Side effect on named loggers | `httpx`, `httpcore`, `LiteLLM` | Each forced to `WARNING` regardless of root level |

Blank or whitespace-only log messages are silently suppressed at formatter level and produce no output to either destination.

---

## 4. Key Data Structures

### `_SkipBlankFormatter` (subclass of `logging.Formatter`)

No additional fields beyond the standard `logging.Formatter`. Its behavior is defined entirely by the overridden `format` method.

| Attribute / Parameter | Type | Purpose |
|---|---|---|
| `_LOG_FORMAT` (passed to `__init__`) | `str` | Controls timestamp, level, logger name, and message layout for all formatted output |
| `record` (method input) | `logging.LogRecord` | Carries the log message and metadata evaluated during formatting |
| Return value of `format` | `str` | Formatted log line, or `""` to suppress blank messages |

### Module-Level Configuration Constants

| Name | Type | Purpose |
|---|---|---|
| `_LOG_DIR` | `str` | Absolute path to the `logs/` directory derived from this file's location |
| `_LOG_FORMAT` | `str` | Shared format string for all handlers |
| `_MAX_BYTES` | `int` | Maximum size of `codetwine.log` before rotation (1 MiB) |
| `_BACKUP_COUNT` | `int` | Number of rotated backup files to retain |

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** approach combined with **silent suppression**. The primary strategy is to filter out undesirable log entries (blank or whitespace-only messages) before they reach the output targets, rather than raising exceptions or halting execution. No explicit error recovery or retry logic is implemented; the module trusts that the underlying Python `logging` infrastructure and the filesystem are available when `setup_logging()` is called.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Blank / whitespace-only log message | A `LogRecord` whose `getMessage()` result is empty or contains only whitespace or a bare newline | `_SkipBlankFormatter.format()` returns an empty string, suppressing the record silently | Yes – the record is skipped; other records continue normally | Only the offending log entry is dropped; no disruption to the logging pipeline |
| Log directory unavailable | `_LOG_DIR` does not exist at the time `setup_logging()` is called | `os.makedirs(_LOG_DIR, exist_ok=True)` creates the directory automatically | Yes – directory is created on demand | None under normal conditions; if directory creation itself fails (e.g., permission denied), an unhandled OS-level exception propagates to the caller |
| Unhandled OS exception during file handler setup | Filesystem permission error or other OS failure when creating `RotatingFileHandler` or its target directory | No explicit handling; the exception propagates uncaught to `setup_logging()`'s caller (`main()`) | No – process terminates with an unhandled exception | Entire application startup fails |
| Excessive verbosity from external libraries | `httpx`, `httpcore`, or `LiteLLM` emit log records below `WARNING` level | Those loggers are explicitly clamped to `WARNING`, discarding lower-severity records | Yes – lower-severity records are permanently filtered | Diagnostic detail from those libraries is suppressed for the lifetime of the process |

---

## 3. Design Notes

- **Suppression over exception**: `_SkipBlankFormatter` deliberately returns an empty string rather than raising an error or logging a warning about the invalid record. This keeps the log file clean without interrupting the application.
- **No defensive guards around `setup_logging()`**: The function contains no `try/except` blocks. Failures in handler creation (e.g., filesystem errors) are intentionally left to propagate, implying the design assumes a correctly provisioned environment and treats setup failures as unrecoverable startup conditions.
- **External library noise reduction is policy, not error handling**: Clamping third-party loggers to `WARNING` is a deliberate output-quality decision rather than a response to an error condition, but it has the side effect of preventing unexpected high-volume debug output from contaminating the log file.
- **`exist_ok=True` as the sole resilience mechanism**: The only proactive fault-tolerance measure in the file is the idempotent directory creation, which prevents a failure on repeated calls to `setup_logging()` or when the directory already exists.

# Summary

**codetwine/config/logger.py** configures application-wide logging once at startup. Public interface: `setup_logging(level: int = logging.INFO) -> None` attaches a `WARNING`-level `StreamHandler` and an `INFO`-level `RotatingFileHandler` to the root logger. Private class `_SkipBlankFormatter(logging.Formatter)` suppresses whitespace-only records. Key constants: `_LOG_DIR` (str), `_LOG_FORMAT` (str), `_MAX_BYTES` (int, 1 MiB), `_BACKUP_COUNT` (int, 5). Silences `httpx`, `httpcore`, and `LiteLLM` loggers to `WARNING`.
