import os
import logging
from logging.handlers import RotatingFileHandler

from codetwine.config.settings import REPO_ROOT


# Log directory: logs/ under the repository root
_LOG_DIR = os.path.join(REPO_ROOT, "logs")

# Log format
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# RotatingFileHandler settings: maximum size of one log file (bytes) and number of old files kept
_MAX_BYTE = 1_048_576
_BACKUP_COUNT = 5


class _SkipBlankFilter(logging.Filter):
    """Handler filter that drops a record whose message is only whitespace."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether a log record is written.

        A record whose message is empty or only whitespace is dropped unless it carries
        an exception or stack information. A record whose message raises any exception
        when it is built is passed on to the handler, which reports the error.

        Args:
            record: The log record.

        Returns:
            False for a record that is dropped, True otherwise.
        """
        if record.exc_info or record.stack_info:
            return True
        try:
            return record.getMessage().strip() != ""
        except Exception:
            return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configure both console output and log file output.

    Call this function once at the beginning of main() in the entry point (main.py).

    Args:
        level: Log level. Defaults to logging.INFO.
    """
    # Root logger at the given level, with one formatter and one filter for both handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)
    skip_blank_filter = _SkipBlankFilter()

    # ===== Console handler =====
    # Only WARNING and above are output to the console; details are recorded in the log file.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(skip_blank_filter)
    root_logger.addHandler(console_handler)

    # ===== File handler =====
    os.makedirs(_LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(_LOG_DIR, "codetwine.log"),
        maxBytes=_MAX_BYTE,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(skip_blank_filter)
    root_logger.addHandler(file_handler)

    # ===== Restrict external library log levels to WARNING =====
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)


def log_progress(logger: logging.Logger, message: str) -> None:
    """Print a progress message to the console and record it in the log file at INFO level.

    Args:
        logger: The calling module's logger.
        message: The progress message.
    """
    print(message)
    logger.info(message)
