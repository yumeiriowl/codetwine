import os
import logging
from logging.handlers import RotatingFileHandler


# Log directory: logs/ under the repository root
_LOG_DIR = os.path.join(
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")),
    "logs",
)

# Log format
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# RotatingFileHandler settings
_MAX_BYTES = 1_048_576
_BACKUP_COUNT = 5


class _SkipBlankFormatter(logging.Formatter):
    """Formatter that skips log messages consisting only of blank lines or whitespace."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record, returning an empty string to skip blank-line messages.

        Args:
            record: The log record to format.

        Returns:
            str: The formatted log string, or an empty string for blank-line messages.
        """
        # Suppress output to the log file when the message is whitespace-only
        if record.getMessage().strip() in ("", "\n"):
            return ""
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure both console output and log file output.

    Call this function once at the beginning of main() in entry points
    (main.py, rlm_qa_agent.py).

    Args:
        level: Log level. Defaults to logging.INFO.
    """
    # Get the root logger and set the log level
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Create a formatter used by both console and file handlers
    formatter = _SkipBlankFormatter(_LOG_FORMAT)

    # ===== Console handler =====
    # Only WARNING and above are output to the console; details are recorded in the log file.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # ===== File handler =====
    os.makedirs(_LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(_LOG_DIR, "codetwine.log"),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # ===== Restrict external library log levels to WARNING =====
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
