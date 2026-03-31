# Design Document: codetwine/config/logger.py

## Overview & Purpose

## 1. Module Summary

Configures the application-wide logging system by registering both a console handler and a rotating file handler on the root logger, with a custom formatter that suppresses blank-line messages.

## 2. When to Use This Module

- **Application startup**: Call `setup_logging()` once at the beginning of `main()` in any entry point (e.g., `main.py`, `rlm_qa_agent.py`) to activate both console and file logging before any other application logic runs. This ensures all subsequent log calls throughout the process are captured.

## 3. Public Interface Table

| Name | Arguments (type) | Return type | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int` (default: `logging.INFO`) | `None` | Attaches a console handler (WARNING and above) and a rotating file handler (all levels at `level`) to the root logger, and suppresses verbose output from `httpx`, `httpcore`, and `LiteLLM` to WARNING. |

## 4. Design Decisions

- **Single call site, root logger**: `setup_logging` targets the root logger rather than a named logger so that all modules in the application inherit the configuration without each needing to register their own handlers.
- **Console/file level split**: The console handler is fixed at `WARNING` while the file handler inherits the configurable `level` (default `INFO`), keeping terminal output quiet during normal operation while preserving detailed records in the log file.
- **Rotating file location**: The log directory (`logs/`) is resolved relative to this file's position in the package tree (`../..` from `config/logger_py/`) so that the log directory is always placed at the repository root regardless of the working directory.
- **Blank-line suppression via custom formatter**: Rather than filtering at the handler level, a custom `Formatter` subclass (`_SkipBlankFormatter`) returns an empty string for whitespace-only messages, preventing blank lines from polluting the log file while keeping the filtering logic encapsulated and reusable by both handlers.

## Definition Design Specifications

---

## Module-Level Constants

| Name | Type | Value | Purpose |
|---|---|---|---|
| `_LOG_DIR` | `str` | Resolved absolute path to `<repo_root>/logs/` | Canonical directory where log files are written; computed relative to this file's location, normalized to remove `..` segments |
| `_LOG_FORMAT` | `str` | `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"` | Shared format string applied to all handlers |
| `_MAX_BYTES` | `int` | `1_048_576` (1 MiB) | Maximum size of a single log file before rotation |
| `_BACKUP_COUNT` | `int` | `5` | Number of rotated backup files retained alongside the active log |

All names are module-private (leading underscore); they are not part of the public API and are not intended to be imported by callers.

---

## Class: `_SkipBlankFormatter`

**Signature:** `class _SkipBlankFormatter(logging.Formatter)`

**Responsibility:** Extends the standard `logging.Formatter` to suppress log entries whose message body is entirely whitespace or empty, preventing blank lines from polluting the log file.

**When to use:** Instantiated internally by `setup_logging`; callers never construct this class directly.

**Design decisions:**
- Inherits from `logging.Formatter` rather than replacing it, so all standard formatting logic is preserved for non-blank messages.
- The suppression signal is an empty string return value from `format()`, which the logging framework treats as a no-op entry rather than raising an error.

**Constraints & edge cases:**
- Messages that strip to either `""` or `"\n"` are suppressed; any other non-empty content passes through unchanged.
- Because suppression is implemented at the formatter level rather than the filter level, it applies uniformly to every handler that uses this formatter instance.

---

### Special Method: `_SkipBlankFormatter.format`

**Signature:** `def format(self, record: logging.LogRecord) -> str`

| Parameter | Type | Description |
|---|---|---|
| `record` | `logging.LogRecord` | The log record to be formatted |

**Returns:** `str` — the fully formatted log line for non-blank messages, or `""` to suppress output for blank-only messages.

**Responsibility:** Acts as the single override point where blank-message detection and standard formatting are combined.

**Constraints & edge cases:** Delegates to `super().format(record)` for all non-blank messages, so no standard formatting capability is lost.

---

## Function: `setup_logging`

**Signature:** `def setup_logging(level: int = logging.INFO) -> None`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `level` | `int` | `logging.INFO` | Numeric log level applied to the root logger and the file handler |

**Returns:** `None`

**Responsibility:** Establishes the application-wide logging configuration by attaching a console handler and a rotating file handler to the root logger, and by capping the verbosity of known noisy third-party libraries.

**When to use:** Called once at the start of an entry-point function (e.g., `main()` in `main.py`) before any other application code runs.

**Design decisions:**

- **Split verbosity by handler:** The console handler is fixed at `WARNING` regardless of the `level` argument, so the terminal remains quiet during normal operation. The file handler inherits the root logger's level, capturing more detailed output for post-hoc inspection.
- **Shared formatter instance:** A single `_SkipBlankFormatter` object is attached to both handlers, ensuring consistent formatting and blank-suppression behavior across all outputs.
- **Rotating file handler:** Using `RotatingFileHandler` with `_MAX_BYTES` and `_BACKUP_COUNT` bounds disk usage without requiring external log management.
- **Third-party suppression:** `httpx`, `httpcore`, and `LiteLLM` are explicitly clamped to `WARNING` after handler setup, preventing their verbose output from reaching the file handler even when the root level is set lower.

**Constraints & edge cases:**

- The function does not guard against being called multiple times; repeated calls add additional handler instances to the root logger, which would cause duplicate log entries.
- `os.makedirs(_LOG_DIR, exist_ok=True)` is called at configuration time, meaning the process must have write permission to the `logs/` directory under the repository root.
- The `level` parameter controls only the root logger and implicitly the file handler's effective level; the console handler's level is unconditionally `WARNING` and is not configurable through this interface.
- Log file encoding is fixed to UTF-8.

## Dependency Description

## Dependencies (modules this file imports)

No project-internal module imports are present in this file. All imports (`os`, `logging`, `logging.handlers`) are standard library components and are excluded from this description.

## Dependents (modules that import this file)

- `main.py` → `codetwine/config/logger_py/logger.py` : imports and calls `setup_logging()` at the start of the `main()` entry point function to initialize both console and rotating file log output for the application.

## Dependency Direction

- The relationship between `main.py` and this module is **unidirectional**: `main.py` depends on `logger.py`, but `logger.py` has no knowledge of or reference back to `main.py`.

## Data Flow

## 1. Inputs

| Input | Source | Format |
|---|---|---|
| `level` | Caller argument to `setup_logging()` | `int` (Python `logging` level constant, defaults to `logging.INFO`) |
| `_LOG_DIR` | Derived at module load time from `__file__` | File system path string, resolved via `os.path` |
| `_LOG_FORMAT` | Module-level constant | String (Python `logging` format specifier) |
| `_MAX_BYTES` | Module-level constant | `int` (1,048,576 bytes) |
| `_BACKUP_COUNT` | Module-level constant | `int` (5) |

The only runtime caller identified in the dependents is `main.py`, which invokes `setup_logging()` with no arguments, accepting the default `logging.INFO` level.

---

## 2. Transformation Overview

```
Module load
    │
    ├─ __file__ path
    │       │
    │       └─► _LOG_DIR (resolved absolute path: <repo_root>/logs/)
    │
setup_logging(level) called
    │
    ├─ Stage 1: Root logger acquisition & level assignment
    │       logging.getLogger() → root_logger
    │       root_logger.setLevel(level)
    │
    ├─ Stage 2: Formatter construction
    │       _LOG_FORMAT string → _SkipBlankFormatter instance
    │       (filters blank/whitespace-only messages at format time)
    │
    ├─ Stage 3: Console handler assembly
    │       StreamHandler created → level set to WARNING
    │       → formatter attached → handler added to root_logger
    │
    ├─ Stage 4: File handler assembly
    │       _LOG_DIR created if absent
    │       RotatingFileHandler created at _LOG_DIR/codetwine.log
    │           maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding=utf-8
    │       → formatter attached → handler added to root_logger
    │
    └─ Stage 5: Third-party logger suppression
            httpx, httpcore, LiteLLM loggers → level forced to WARNING
```

Log record flow at runtime (after setup):

```
Log record emitted by any logger
    │
    └─► _SkipBlankFormatter.format()
            │
            ├─ getMessage().strip() == "" or "\n"  →  returns ""  (record suppressed)
            └─ otherwise  →  formatted string via super().format()
                    │
                    ├─► StreamHandler  (only if record.levelno >= WARNING)
                    └─► RotatingFileHandler  (all records at root_logger level and above)
```

---

## 3. Outputs

| Output | Type | Description |
|---|---|---|
| Root logger configuration | Side effect | Root logger level set; two handlers attached (console + file) |
| Console output | Side effect (stderr) | Log messages at `WARNING` and above written to the console stream |
| Log file | Side effect (file write) | All messages at the configured `level` and above written to `<repo_root>/logs/codetwine.log` in UTF-8; rotated when size exceeds 1 MB, retaining up to 5 backup files |
| Third-party logger levels | Side effect | `httpx`, `httpcore`, and `LiteLLM` loggers restricted to `WARNING` |

`setup_logging()` has no return value (`None`).

---

## 4. Key Data Structures

### `_SkipBlankFormatter` (subclass of `logging.Formatter`)

| Field / Key | Type | Purpose |
|---|---|---|
| Inherited format string | `str` | Holds `_LOG_FORMAT`; controls the text layout of each log record |
| `record.getMessage()` result | `str` | The resolved log message text; checked for whitespace-only content to decide suppression |

### `RotatingFileHandler` configuration (passed at construction)

| Field / Key | Type | Purpose |
|---|---|---|
| filename | `str` | Absolute path to `codetwine.log` inside `_LOG_DIR` |
| `maxBytes` | `int` | Maximum file size (1,048,576 bytes) before rotation triggers |
| `backupCount` | `int` | Number of rotated backup files to retain (5) |
| `encoding` | `str` | Character encoding for the log file (`"utf-8"`) |

## Error Handling

## 1. Overall Strategy

This file adopts a **logging-and-continue** approach for the specific domain it handles (blank-line suppression), combined with **silent omission** for unwanted output. There are no explicit try-except blocks in this file; error handling is instead expressed structurally through formatter-level filtering and configuration defaults. The module relies on Python's standard `logging` infrastructure to propagate or suppress errors naturally, without terminating the process.

---

## 2. Error Pattern Table

| Error Type | Trigger Condition | Handling | Recoverable? | Impact |
|---|---|---|---|---|
| Blank or whitespace-only log message | A log record whose message consists solely of whitespace or a newline character is submitted for formatting | `_SkipBlankFormatter.format()` returns an empty string, causing the message to be silently omitted from output | Yes | The blank message is dropped; all other logging continues normally |
| Excessive log output from external libraries | `httpx`, `httpcore`, or `LiteLLM` emit log records below `WARNING` level | Their loggers are explicitly set to `WARNING`, suppressing `DEBUG` and `INFO` records | Yes | Sub-`WARNING` messages from those libraries are never written; no functional impact on the application |
| Log directory does not exist | `_LOG_DIR` path is absent on the filesystem when `setup_logging()` is called | `os.makedirs(_LOG_DIR, exist_ok=True)` creates the directory automatically | Yes | No impact; the directory is created and file logging proceeds normally |

---

## 3. Design Notes

- **Formatter as a filter gate:** The blank-message suppression is implemented inside the formatter rather than as a separate `logging.Filter` object. Returning an empty string from `format()` is a deliberate signal to the handler to emit nothing, keeping the filtering logic co-located with formatting logic.
- **No exception propagation surface:** Because there are no try-except blocks, any unexpected failure (e.g., a filesystem permission error when creating the log directory or opening the rotating file) will propagate as an unhandled exception to the caller (`main()` in `main.py`). The module makes no attempt to catch or mask infrastructure-level failures; the assumption is that logging setup must succeed for the application to operate correctly.
- **Separation of console and file severity:** Console output is restricted to `WARNING` and above, while the file handler inherits the root logger's level (defaulting to `INFO`). This design choice is not guarded by error handling but represents an intentional policy that limits user-visible noise while preserving detail in the log file.

## Summary

**codetwine/config/logger.py** configures application-wide logging via `setup_logging(level: int = logging.INFO) -> None`, which attaches a `StreamHandler` (WARNING+) and `RotatingFileHandler` (INFO+, UTF-8, 1 MiB/5 backups) to the root logger, suppresses `httpx`/`httpcore`/`LiteLLM` below WARNING, and uses a shared `_SkipBlankFormatter(logging.Formatter)` instance on both handlers to silently drop whitespace-only log records. Log files write to `<repo_root>/logs/codetwine.log`.
