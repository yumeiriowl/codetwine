# Design Document: codetwine/config/logger.py

# Overview & Purpose

This module centralizes logging configuration for the entire `codetwine` application. It exists as a separate file so that any entry point (e.g., `main.py`, `rlm_qa_agent.py`) can establish a consistent, project-wide logging setup with a single call, avoiding duplicated or inconsistent logging configuration scattered across scripts.

Its responsibilities are:
- Defining where log files are stored (a `logs/` directory at the repository root, computed relative to this file's location).
- Defining a uniform log message format (timestamp, level, logger name, message).
- Configuring the root logger with two output destinations: a console handler (WARNING and above only) and a rotating file handler (all levels down to the configured level, writing to `logs/codetwine.log` with size-based rotation).
- Suppressing noisy blank-line-only log messages from cluttering the log file.
- Silencing verbose third-party library loggers (`httpx`, `httpcore`, `LiteLLM`) by restricting them to WARNING level.

### Main Public Interfaces

| Name | Arguments | Return Value | Responsibility |
|---|---|---|---|
| `setup_logging` | `level: int = logging.INFO` | `None` | Configures the root logger with a console handler (WARNING+) and a rotating file handler (using the given level), applies a shared formatter, and restricts external library loggers to WARNING; intended to be called once at application startup. |
| `_SkipBlankFormatter` (class, `logging.Formatter` subclass) | Constructed with format string (e.g., `_LOG_FORMAT`) | Formatter instance | Overrides `format()` to return an empty string for blank/whitespace-only log messages, preventing them from being written to output. |

### Design Decisions

- **Single configuration entry point**: `setup_logging()` is designed to be invoked exactly once at the start of an entry point's `main()` function (as seen in `main.py`'s usage), rather than configuring logging in multiple places.
- **Separation of console and file verbosity**: The console handler is deliberately limited to `WARNING` and above, while the file handler records at the level passed to `setup_logging` (default `INFO`), so detailed logs are preserved in the file without cluttering the console.
- **Rotating file handler**: Uses `RotatingFileHandler` with fixed `maxBytes` (1,048,576 bytes) and `backupCount` (5) constants to bound log file growth and retain a limited history of rotated logs.
- **Custom formatter for noise reduction**: `_SkipBlankFormatter` is a targeted subclass of `logging.Formatter` that filters out blank or whitespace-only messages, keeping log output clean without altering the overall logging format contract.
- **Third-party log-level suppression**: External libraries known to be verbose (`httpx`, `httpcore`, `LiteLLM`) are explicitly capped at `WARNING` to prevent them from flooding the logs configured by this module.

# Definition Design Specifications

## `_SkipBlankFormatter` (class)

A `logging.Formatter` subclass that suppresses log lines whose message body is empty or whitespace-only.

- **Responsibility / design intent**: Prevents blank or whitespace-only log messages (e.g., from stray print-like calls or blank-line separators) from cluttering the log file or console output.
- **`format(self, record: logging.LogRecord) -> str`**
  - **Argument**: `record` — the `LogRecord` instance to be formatted.
  - **Return value**: `str` — an empty string if the record's message, after stripping whitespace, is empty or equals `"\n"`; otherwise the normally formatted log line produced by the parent formatter.
  - **Design decision**: Checks `record.getMessage().strip()` against a fixed set of blank patterns rather than filtering at the handler/logger level, keeping the blank-suppression logic colocated with formatting so it applies uniformly to both console and file output through a single shared formatter instance.
  - **Edge case**: A message containing only whitespace characters (spaces, tabs) that strip down to `""`, or a message equal to a single newline, is treated as blank; messages with leading/trailing whitespace around actual content are still formatted normally.

## `setup_logging` (function)

```
setup_logging(level: int = logging.INFO) -> None
```

- **Argument**: `level` — the log level (as defined by the `logging` module, e.g. `logging.INFO`, `logging.DEBUG`) applied to the root logger. Defaults to `logging.INFO`.
- **Return value**: `None`. The function's effect is entirely side-effectful: it mutates global logging configuration (root logger handlers/level and specific external loggers' levels).
- **Responsibility / design intent**: Centralizes application-wide logging configuration so that entry points (`main.py`, `rlm_qa_agent.py`) can enable consistent console and file logging with a single call at startup, rather than each module configuring logging independently.
- **Important design decisions**:
  - Uses a split-handler strategy: console output is restricted to `WARNING` and above to keep terminal output minimal, while the file handler receives all messages at the configured `level`, ensuring detailed logs are preserved without cluttering the console.
  - Uses `RotatingFileHandler` with fixed `maxBytes` (1 MiB) and `backupCount` (5) to bound log file growth and retain a limited history without external log rotation tooling.
  - Log directory is computed relative to this file's location (two levels up, `logs/` at the repository root) so log output location is independent of the current working directory when the entry point is invoked.
  - Both handlers share the same `_SkipBlankFormatter` instance, ensuring blank-message suppression and format consistency across console and file outputs.
  - Explicitly lowers noisy third-party loggers (`httpx`, `httpcore`, `LiteLLM`) to `WARNING`, preventing verbose dependency logs from drowning out application logs regardless of the root logger's configured level.
- **Edge cases / constraints**:
  - Intended to be called once per process (typically at the start of `main()`); calling it multiple times will add duplicate handlers to the root logger since no check is made for existing handlers.
  - Requires filesystem write access to create the `logs/` directory and log file; `os.makedirs(..., exist_ok=True)` tolerates the directory already existing but does not handle permission errors.
  - The `level` parameter only affects the root logger and file handler threshold; the console handler's `WARNING` threshold is fixed and not affected by the `level` argument.

# Dependency Description

**Dependencies (what this file uses)**

This file relies solely on standard library modules (`os`, `logging`, `logging.handlers`) to implement its functionality, such as constructing the log directory path, formatting log records, and managing rotating file output. No project-internal file dependencies are present in this module.

**Dependents (what uses this file)**

- `main.py` depends on this file through the `setup_logging` function. It calls `setup_logging()` at the start of its `main()` entry point to initialize both console and file logging (including handler setup, formatting, and external library log level restrictions) before proceeding with the rest of the application logic (parsing arguments and resolving directories).

The dependency direction is unidirectional: `main.py` depends on `codetwine/config/logger.py` for logging initialization, while this file has no dependency on `main.py` or any other project-internal file.

# Data Flow

## Input

| Source | Data | Format |
|---|---|---|
| Caller (`main.py`) | `level` parameter to `setup_logging()` | `int` (logging level constant, default `logging.INFO`) |
| Runtime | `LogRecord` objects emitted by `logging` calls throughout the app | `logging.LogRecord` (contains message, level, logger name, timestamp, etc.) |
| Filesystem | Repository root path (derived from `__file__` location) | Path string |

## Transformation Flow

```
setup_logging(level)
   │
   ├─► Get root logger, set level
   │
   ├─► Build shared _SkipBlankFormatter(_LOG_FORMAT)
   │
   ├─► Create StreamHandler (console)
   │      - level = WARNING
   │      - formatter = shared formatter
   │      → attach to root logger
   │
   ├─► Ensure _LOG_DIR exists (logs/ under repo root)
   ├─► Create RotatingFileHandler (file)
   │      - path = logs/codetwine.log
   │      - maxBytes / backupCount rotation
   │      - formatter = shared formatter
   │      → attach to root logger
   │
   └─► Suppress noisy external loggers (httpx, httpcore, LiteLLM) → WARNING

At runtime, each LogRecord flows into both handlers:
   LogRecord → formatter.format(record)
                 │
                 ├─ if message.strip() is "" or "\n" → return "" (suppressed)
                 └─ else → standard Formatter output ("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
```

## Output

| Destination | Content | Condition |
|---|---|---|
| Console (stderr via `StreamHandler`) | Formatted log lines | Only records at `WARNING` or above; blank-only messages produce empty string |
| `logs/codetwine.log` | Formatted log lines | All records at configured `level` or above; rotates at 1 MB, keeps 5 backups |

No return value; `setup_logging` mutates global logging configuration (root logger and named loggers) as a side effect.

## Key Data Structures

| Structure | Fields / Purpose |
|---|---|
| `_LOG_DIR` | Computed absolute path to `logs/` directory at repo root; used as file handler destination |
| `_LOG_FORMAT` | Format string defining `asctime`, `levelname`, `name`, `message` fields for both handlers |
| `_MAX_BYTES` / `_BACKUP_COUNT` | Rotation policy constants for `RotatingFileHandler` |
| `_SkipBlankFormatter` | Custom `logging.Formatter` subclass; overrides `format()` to drop whitespace-only messages, otherwise delegates to parent formatting |
| `root_logger` | Shared `logging.Logger` instance configured once; all handlers (console, file) attached here so every module's logger propagates to both outputs |

# Error Handling

This module does not implement explicit exception handling (no try/except blocks). It follows an implicit **fail-fast** strategy: any failure occurring during logging setup (e.g., directory creation, file handler initialization) propagates directly to the caller rather than being caught or suppressed. Since `setup_logging()` is invoked once at the start of `main()`, any such failure surfaces immediately at application startup rather than being deferred or silently ignored.

| Error Type | Handling | Impact |
|---|---|---|
| Log directory creation failure (`os.makedirs`, e.g., permission denied) | Not caught; exception propagates to the caller | Program startup fails (`main()` aborts before proceeding) |
| Log file open/creation failure (`RotatingFileHandler` init, e.g., invalid path, disk full, permission denied) | Not caught; exception propagates to the caller | Program startup fails |
| Blank/whitespace-only log messages | Explicitly handled by `_SkipBlankFormatter.format`, which returns an empty string instead of raising or logging | No error; message is silently skipped in output (not a failure case, but a deliberate suppression of formatting output) |
| Invalid log level value passed to `setup_logging` | Not validated; relies on `logging.Logger.setLevel`'s own behavior | Any resulting error (if any) is not handled here and would propagate |

### Design Considerations
- The module assumes it is called once at process startup, so failures here are expected to halt initialization early rather than degrade gracefully, ensuring logging infrastructure is either fully functional or the program does not continue.
- The only intentional "error suppression" behavior in this file is limited to formatting: blank or newline-only messages are intentionally converted to empty output to avoid cluttering log files, distinguishing this from actual error suppression of failures.
- No retry, fallback, or default-handler logic is present; the module relies entirely on the standard `logging` and `os` module behaviors for surfacing issues.

# Summary

`codetwine/config/logger.py` centralizes logging setup. Public interface: `setup_logging(level=logging.INFO) -> None`, called once at startup, configures root logger with console handler (WARNING+) and rotating file handler (`logs/codetwine.log`, 1MB, 5 backups) at given level, sharing a `_SkipBlankFormatter` (suppresses blank/whitespace messages) via `_LOG_FORMAT`; also silences `httpx`/`httpcore`/`LiteLLM` to WARNING. Uses stdlib only, no internal deps. Errors (I/O, permissions) propagate unhandled (fail-fast). Used by `main.py` and other entry points.
