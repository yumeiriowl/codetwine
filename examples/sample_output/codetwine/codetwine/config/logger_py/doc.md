# Design Document: codetwine/config/logger.py

# Design Specification

**Overview**

Configure logging for the application with both console and file output, supporting rotating log files and filtered output levels.

- Call `setup_logging()` at application startup to initialize the root logger with console output for warnings and errors, and file output for all messages at the specified level.
- Call `log_progress()` to print a progress message to the console and simultaneously record it in the log file at INFO level, used by `doc_creator.py` and `pipeline.py` during dependency analysis and document generation.

This file is used by `main.py` to initialize logging at the entry point and by `doc_creator.py` and `pipeline.py` to report progress during file processing and analysis operations. No project-internal files are imported or relied upon.

The logger restricts external library output (httpx, httpcore, LiteLLM) to WARNING level to reduce noise, and uses a custom formatter that skips blank-line messages to keep log files clean. The file handler uses rotation with a 1 MB size limit and keeps 5 backup files.

**Definitions**

## `_SkipBlankFormatter`

A custom logging formatter that filters out messages containing only whitespace or newlines, preventing empty lines from cluttering log files and console output. It extends the standard formatter to check message content before rendering.

## `_SkipBlankFormatter.format`

Formats a log record by returning an empty string for whitespace-only messages, otherwise delegating to the parent formatter to produce the standard log output.

## `setup_logging`

Initializes the root logger with both console and file handlers, setting the console to WARNING level and file to the specified level, creating the logs directory if needed, and suppressing verbose output from external libraries (httpx, httpcore, LiteLLM). Call this once at the start of `main.py` before any other logging occurs.

## `log_progress`

Outputs a progress message to both the console via print and the log file via INFO-level logging, used by the pipeline and document generator to report status during long-running operations.

## `_LOG_DIR`

The directory path where rotating log files are stored, constructed as a `logs/` subdirectory under the repository root.

## `_LOG_FORMAT`

The logging format string specifying that each log line includes the timestamp, log level, logger name, and message.

## `_MAX_BYTE`

The maximum size in bytes (1,048,576) of each individual log file before rotation occurs.

## `_BACKUP_COUNT`

The number of old log files (5) retained after rotation before the oldest is deleted.

# Summary

# Logger Configuration Summary

**Single Responsibility:** Initialize and manage application-wide logging with console and rotating file output, supporting filtered output levels and progress reporting during long-running operations.

**Public Definitions:**
- `setup_logging()` – Initialize root logger with console (WARNING) and file handlers, suppress external library noise
- `log_progress()` – Output status messages to console and log file simultaneously

**Key Characteristics:**
Configures dual-channel logging: console displays warnings and errors only, while rotating log files capture all messages at a specified level. Implements a custom formatter that skips blank-line messages to reduce clutter. Log files rotate at 1 MB with five backups retained. Suppresses verbose output from httpx, httpcore, and LiteLLM. Used at application startup and during file processing and dependency analysis operations.
