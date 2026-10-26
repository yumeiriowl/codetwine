"""How the settings are read from the environment, and what the log handlers write.

Each setting is read in a child process that imports the settings module with the
variable set, so the value is the one the import reads.
"""

import io
import logging
import os
import subprocess
import sys

import pytest

from codetwine.config import logger as logger_module

_DEFAULT_EXCLUDE_LIST = ["__pycache__", ".git", ".github", ".venv", "node_modules"]


def _read_setting(name: str, value: str) -> subprocess.CompletedProcess:
    """Import the settings module in a child process with one variable set.

    Args:
        name: Name of the environment variable and of the setting.
        value: What to put in the environment variable.

    Returns:
        The finished process; stdout holds the repr of the setting when the import passed.
    """
    env = dict(os.environ, **{name: value})
    return subprocess.run(
        [sys.executable, "-c",
         "import sys, codetwine.config.settings as s; print(repr(getattr(s, sys.argv[1])))",
         name],
        env=env, capture_output=True, text=True, check=False,
    )


def _last_error_line(proc: subprocess.CompletedProcess) -> str:
    """Return the last line of a failed process's stderr: the exception and its message."""
    return proc.stderr.strip().splitlines()[-1]


# == Numbers ================================================================

@pytest.mark.parametrize("name, value", [
    ("MAX_WORKERS", "four"),
    ("PARSE_CACHE_MAX_FILES", "1k"),
    ("SUMMARY_MAX_CHARS", ""),
])
def test_a_number_setting_that_is_no_number_names_the_setting(name, value):
    proc = _read_setting(name, value)

    assert proc.returncode != 0
    assert name in _last_error_line(proc)


def test_retry_wait_takes_seconds_with_a_fraction():
    proc = _read_setting("RETRY_WAIT", "1.5")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "1.5"


# == True / False ===========================================================

@pytest.mark.parametrize("value, expect", [
    ("True", True), (" true ", True), ("YES", True), ("on", True), ("1", True),
    ("False", False), ("no", False), ("Off", False), ("0", False),
])
def test_a_true_false_setting_reads_its_words_in_any_case(value, expect):
    proc = _read_setting("ENABLE_CODE_SUMMARY", value)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == repr(expect)


@pytest.mark.parametrize("value", ["Ture", "enabled", ""])
def test_a_true_false_setting_refuses_another_word(value):
    proc = _read_setting("ENABLE_CODE_SUMMARY", value)

    assert proc.returncode != 0
    assert "ENABLE_CODE_SUMMARY" in _last_error_line(proc)


# == EXCLUDE_PATTERNS =======================================================

def test_exclude_patterns_are_added_to_the_default_list():
    proc = _read_setting("EXCLUDE_PATTERNS", "*.lock, secrets ,.git")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == repr(_DEFAULT_EXCLUDE_LIST + ["*.lock", "secrets"])


@pytest.mark.parametrize("value", [" ", ",", " , ,"])
def test_exclude_patterns_of_blanks_and_commas_keep_the_default_list(value):
    proc = _read_setting("EXCLUDE_PATTERNS", value)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == repr(_DEFAULT_EXCLUDE_LIST)


# == Log handlers ===========================================================

@pytest.fixture
def log_output(tmp_path, monkeypatch):
    """Set up the log handlers with the log file under tmp_path and the console handler
    writing to a string buffer, and remove them after.

    Yields:
        A (log file path, console buffer, handlers setup_logging added) tuple.
    """
    monkeypatch.setattr(logger_module, "_LOG_DIR", str(tmp_path))
    root_logger = logging.getLogger()
    handler_list = list(root_logger.handlers)
    level = root_logger.level
    logger_module.setup_logging()
    console_stream = io.StringIO()
    add_handler_list = [handler for handler in root_logger.handlers if handler not in handler_list]
    console_handler_list = [
        handler for handler in add_handler_list if type(handler) is logging.StreamHandler
    ]
    assert len(console_handler_list) == 1
    console_handler_list[0].setStream(console_stream)
    yield tmp_path / "codetwine.log", console_stream, add_handler_list
    for handler in root_logger.handlers:
        if handler not in handler_list:
            handler.close()
    root_logger.handlers[:] = handler_list
    root_logger.setLevel(level)


def _message_list(log_text: str) -> list[str]:
    """Return the message part of each line a log handler wrote."""
    return [line.split(": ", 1)[1] for line in log_text.splitlines()]


def test_a_blank_message_writes_no_line(log_output):
    log_file, console_stream, _ = log_output
    test_logger = logging.getLogger("codetwine.test_blank")
    test_logger.warning("first")
    test_logger.warning("")
    test_logger.warning("   ")
    test_logger.warning("\n\n")
    test_logger.warning("%s", " ")
    test_logger.warning("last")

    assert _message_list(log_file.read_text(encoding="utf-8")) == ["first", "last"]
    assert _message_list(console_stream.getvalue()) == ["first", "last"]


class _BadStr:
    """Object whose str() raises AttributeError."""

    def __str__(self) -> str:
        raise AttributeError("no text")


@pytest.mark.parametrize("message, arg_tuple", [
    ("%d", ("x",)),
    ("%c", (10**10,)),
    (_BadStr(), ()),
    ("value %s", (_BadStr(),)),
])
def test_a_message_that_cannot_be_built_is_reported_and_logging_goes_on(
        log_output, capsys, message, arg_tuple):
    log_file, console_stream, handler_list = log_output
    test_logger = logging.getLogger("codetwine.test_bad_message")

    for record_message, record_arg_tuple in ((message, arg_tuple), ("after", ())):
        record = test_logger.makeRecord(
            test_logger.name, logging.WARNING, __file__, 1, record_message, record_arg_tuple, None)
        for handler in handler_list:
            handler.handle(record)

    assert capsys.readouterr().err.count("--- Logging error ---") == len(handler_list)
    assert _message_list(log_file.read_text(encoding="utf-8")) == ["after"]
    assert _message_list(console_stream.getvalue()) == ["after"]


def test_a_blank_message_with_an_exception_keeps_the_traceback(log_output):
    log_file, _, _ = log_output
    test_logger = logging.getLogger("codetwine.test_blank")
    try:
        raise ZeroDivisionError("zero")
    except ZeroDivisionError:
        test_logger.exception("")

    log_text = log_file.read_text(encoding="utf-8")
    assert "Traceback" in log_text
    assert "ZeroDivisionError: zero" in log_text
