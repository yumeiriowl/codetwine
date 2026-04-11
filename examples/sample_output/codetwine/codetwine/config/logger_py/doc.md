# Design Document: codetwine/config/logger.py

# Overview & Purpose

## 1. Module Summary

Configures the application-wide logging system by attaching a console handler and a rotating file handler to the root logger with appropriate log levels and formatting.

## 2. When to Use This Module

- **Application startup**: Call `setup_logging()` once at the beginning of an entry point's `main()` function (e.g., `main.py`, `rlm_qa_agent.py`) to initialize all logging behavior before any other module emits log messages. No return value is needed; the effect is global via the root logger.
- **Adjusting verbosity**: Pass a specific `level` argument to `setup_logging(level=logging.DEBUG)` when a non-default log level is required at startup.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int` (default: `logging.INFO`) | `None` | Attaches a console handler (WARNING and above) and a rotating file handler (all messages at `level`) to the root logger, and suppresses noisy external library loggers to WARNING |

## 4. Design Decisions

- **Single call contract**: `setup_logging` is designed to be called exactly once per process at the entry point. Calling it multiple times would add duplicate handlers to the root logger.
- **Split console/file log levels**: The console handler is fixed at `WARNING` to keep terminal output quiet during normal operation, while the file handler inherits the `level` argument (defaulting to `INFO`) to capture more detailed diagnostic information in the log file.
- **Blank-line suppression via custom formatter**: The private `_SkipBlankFormatter` is applied to both handlers uniformly, so whitespace-only log messages are silently dropped from both console and file output rather than cluttering logs with empty lines.
- **Log file location is repository-relative**: The log directory is resolved relative to this file's location (`../../logs/`), making the output path predictable regardless of the working directory from which the application is launched.

# Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value | Purpose |
|---|---|---|---|
| `_LOG_DIR` | `str` | Resolved at import time | Absolute path to the `logs/` directory located two levels above this file (i.e., the repository root). |
| `_LOG_FORMAT` | `str` | Fixed format string | Shared log format string applied to both console and file handlers. Includes timestamp, level, logger name, and message. |
| `_MAX_BYTES` | `int` | `1,048,576` (1 MiB) | Maximum size of a single rotating log file before rollover is triggered. |
| `_BACKUP_COUNT` | `int` | `5` | Number of rotated backup log files retained alongside the active log file. |

All constants are module-private (prefixed with `_`) and are not intended to be referenced by callers directly.

---

## Class: `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** A custom log formatter that suppresses output for log records whose message content is entirely whitespace or an isolated newline, preventing blank entries from polluting the log file.

**When to use:** Instantiated internally by `setup_logging`; callers never instantiate this class directly.

**Design decisions:**
- Inherits from `logging.Formatter` and overrides only `format`, keeping all other formatting behavior unchanged.
- The suppression signal is an empty string return value. This relies on the handler choosing not to emit empty strings, which is the standard behavior of Python's `StreamHandler` and `RotatingFileHandler`.
- The class is module-private; it is not part of the public API.

**Constraints & edge cases:**
- A message consisting solely of `"\n"` or `""` after `.strip()` is treated as blank and suppressed.
- Messages containing at least one non-whitespace character are formatted normally.

---

### Special Method: `_SkipBlankFormatter.format`

| Item | Detail |
|---|---|
| **Signature** | `format(self, record: logging.LogRecord) -> str` |
| **Argument** | `record`: the standard log record object produced by the logging framework |
| **Return type** | `str` — the formatted log line, or an empty string if the message is blank |

**Responsibility:** Intercepts the standard formatting pipeline to gate blank-line messages, delegating all other records to the parent `Formatter.format` implementation.

**Constraints:** The method does not mutate the passed `record`; it only inspects the message content.

---

## Function: `setup_logging`

**Signature:** `setup_logging(level: int = logging.INFO) -> None`

**Responsibility:** Performs one-time configuration of the Python root logger by attaching a console handler and a rotating file handler, and by suppressing verbose output from known third-party libraries.

**When to use:** Called once at application startup inside `main()` of each entry point (e.g., `main.py`) before any other logging activity occurs.

**Parameters:**

| Name | Type | Default | Meaning |
|---|---|---|---|
| `level` | `int` | `logging.INFO` | The minimum severity level recorded by the root logger and written to the log file. |

**Return type:** `None`

**Handler summary:**

| Handler | Class | Output destination | Minimum level |
|---|---|---|---|
| Console handler | `logging.StreamHandler` | `stderr` (default) | `WARNING` |
| File handler | `RotatingFileHandler` | `logs/codetwine.log` | Inherits root level (default: `INFO`) |

**Design decisions:**
- The console handler is intentionally restricted to `WARNING` and above so that informational and debug output is written only to the file, reducing noise during normal terminal use.
- `_LOG_DIR` is created on demand with `exist_ok=True`, so the function is safe to call in environments where the directory does not yet exist.
- Both handlers share the same `_SkipBlankFormatter` instance, ensuring consistent blank-line suppression across all outputs.
- Third-party loggers (`httpx`, `httpcore`, `LiteLLM`) are explicitly capped at `WARNING` to prevent their verbose output from flooding the log file regardless of the root level set by the caller.

**Constraints & edge cases:**
- The function does not guard against being called multiple times. Repeated calls will attach additional handlers to the root logger, causing duplicate log output.
- The `level` parameter affects the root logger and the file handler's effective floor, but the console handler's floor remains fixed at `WARNING` regardless of the value passed.
- The log file is UTF-8 encoded; log messages containing non-UTF-8 bytes may raise encoding errors at the handler level.
- The rotating file handler retains at most `_BACKUP_COUNT` (5) backup files in addition to the active file, capping total disk usage at approximately 6 MiB.

# Dependency Description

## Dependencies (modules this file imports)

No project-internal module dependencies exist. `codetwine/config/logger.py` relies exclusively on standard library modules (`os`, `logging`, `logging.handlers`) and no other modules within the project's own codebase.

## Dependents (modules that import this file)

- `main.py` → `codetwine/config/logger.py` : imports and calls `setup_logging()` at the start of the `main()` entry point function to initialize both console and file-based logging for the application.

## Dependency Direction

All relationships are **unidirectional**:

- The relationship between `main.py` and `codetwine/config/logger.py` is unidirectional: `main.py` depends on `codetwine/config/logger.py`. `codetwine/config/logger.py` has no knowledge of or reference to `main.py`.

# Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `level` | Caller argument (`setup_logging(level)`) | `int` (a `logging` module constant, e.g., `logging.INFO`) |
| `__file__` | Python runtime | String file path used to derive `_LOG_DIR` at module load time |
| `_LOG_DIR` | Computed at module load from `__file__` | Absolute filesystem path string (`logs/` under the repository root) |
| `_LOG_FORMAT` | Module-level constant | String defining the log record format |
| `_MAX_BYTES`, `_BACKUP_COUNT` | Module-level constants | `int` values controlling log file rotation |

`setup_logging()` is called with no arguments from `main.py`, so `level` defaults to `logging.INFO`.

---

## 2. Transformation Overview

```
[Module load]
  __file__
    → os.path.dirname / normpath / join
    → _LOG_DIR (absolute path string)

[setup_logging() call]

Stage 1 — Root logger acquisition and level assignment
  level (int)
    → logging.getLogger()  →  root_logger
    → root_logger.setLevel(level)

Stage 2 — Formatter construction
  _LOG_FORMAT (string)
    → _SkipBlankFormatter(_LOG_FORMAT)
    → formatter instance shared by both handlers

Stage 3 — Console handler construction
  formatter
    → logging.StreamHandler()
    → setLevel(logging.WARNING)          ← threshold higher than root
    → setFormatter(formatter)
    → root_logger.addHandler(console_handler)

Stage 4 — File handler construction
  _LOG_DIR, _MAX_BYTES, _BACKUP_COUNT, formatter
    → os.makedirs(_LOG_DIR)              ← ensures directory exists
    → RotatingFileHandler("codetwine.log", ...)
    → setFormatter(formatter)
    → root_logger.addHandler(file_handler)

Stage 5 — External library suppression
  hard-coded logger names ("httpx", "httpcore", "LiteLLM")
    → each logger's level set to logging.WARNING

[Per log-record path through _SkipBlankFormatter]
  logging.LogRecord
    → record.getMessage().strip()
    → if result is "" or "\n"  →  return ""  (record suppressed)
    → otherwise                →  super().format(record)  →  formatted string
```

---

## 3. Outputs

| Output | Type | Destination | Notes |
|---|---|---|---|
| Console output | Text lines | `stderr` (default `StreamHandler` target) | Only records at `WARNING` level and above are emitted |
| Log file | Rotating UTF-8 text file | `<repo_root>/logs/codetwine.log` | All records at `level` (default `INFO`) and above; rotates at 1 MiB, keeps 5 backups |
| Suppressed blank records | Empty string `""` | Neither console nor file | `_SkipBlankFormatter` returns `""` for whitespace-only messages |
| `setup_logging` return value | `None` | Caller | No value is returned |

Side effects produced by `setup_logging()`:
- The `logs/` directory is created on disk if it does not already exist.
- Two handlers are attached to the root logger (mutations of the global logging state).
- The log levels of `httpx`, `httpcore`, and `LiteLLM` loggers are set to `WARNING` (mutations of the global logging state).

---

## 4. Key Data Structures

### `_SkipBlankFormatter` (subclass of `logging.Formatter`)

| Field / Key | Type | Purpose |
|---|---|---|
| `_fmt` (inherited) | `str` | Holds `_LOG_FORMAT`; controls the text layout of each emitted log line |

No additional instance fields are introduced beyond those inherited from `logging.Formatter`.

### `RotatingFileHandler` configuration (passed as constructor arguments)

| Field / Key | Type | Purpose |
|---|---|---|
| filename | `str` | Absolute path to `codetwine.log` inside `_LOG_DIR` |
| `maxBytes` | `int` | `1_048_576` (1 MiB) — triggers rotation when the file reaches this size |
| `backupCount` | `int` | `5` — number of rotated backup files retained |
| `encoding` | `str` | `"utf-8"` — character encoding for the log file |

# Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** approach with selective **silent suppression**. The primary strategy is to allow the application to proceed without interruption when log messages are determined to be non-informative (blank or whitespace-only). No explicit exception handling is implemented within this file; errors arising from setup operations (such as file system access failures) are left to propagate naturally to the caller (`main()` in `main.py`), following an implicit **fail-fast** posture for infrastructure-level failures.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Blank/whitespace-only log message | A log record whose message content is empty, whitespace, or a bare newline | The `_SkipBlankFormatter` returns an empty string, suppressing the message from being written | Yes – the message is silently skipped; the logger continues operating normally | No output is written for that record; all subsequent records are unaffected |
| Log directory creation failure | `_LOG_DIR` cannot be created (e.g., permission denied) at the `os.makedirs` call | No handling; exception propagates to the caller (`main()`) | No – process terminates | Application startup fails entirely |
| Log file open/write failure | The `RotatingFileHandler` cannot open or write to `codetwine.log` (e.g., disk full, permission denied) | No handling; exception propagates to the caller | No – process terminates | Application startup fails entirely |
| External library over-verbose logging | `httpx`, `httpcore`, or `LiteLLM` emit log records below `WARNING` level | Log level for those loggers is explicitly set to `WARNING`, suppressing lower-severity records | Yes – lower-level records are filtered; no data is lost from the application perspective | Reduced noise in both console output and the log file |

---

## 3. Design Notes

- **Separation of suppression from exception handling:** The blank-message suppression is implemented purely at the formatting layer (`_SkipBlankFormatter`) rather than through exception handling. This means the suppression is a deliberate output-filtering policy, not an error recovery mechanism.
- **Implicit fail-fast for infrastructure errors:** By omitting try-except blocks around directory creation and file handler initialization, the design treats logging setup as a hard prerequisite. Any failure at this stage is considered unrecoverable, and the responsibility for observing and handling such failures is delegated entirely to the caller.
- **External library noise reduction as a policy concern:** Restricting third-party logger levels to `WARNING` is treated as a logging hygiene policy rather than error handling. It prevents external libraries from obscuring application-level log output without suppressing genuinely actionable warnings.

# Summary

**codetwine/config/logger.py**: Configures application-wide logging by attaching a console handler (WARNING+) and rotating file handler (INFO+ by default) to the root logger.

**Public:** `setup_logging(level: int = logging.INFO) -> None`

**Key structures:** `_SkipBlankFormatter(logging.Formatter)` suppresses whitespace-only records; `RotatingFileHandler` writes to `logs/codetwine.log` (1 MiB max, 5 backups, UTF-8); third-party loggers (`httpx`, `httpcore`, `LiteLLM`) capped at WARNING.
