"""Helpers that produce real exceptions, tracebacks and log records.

The integration only ever works with genuine traceback objects, so tests build
theirs by actually raising — never by faking a ``TracebackType``.
"""

from __future__ import annotations

import logging
import sys
from types import TracebackType

LOGGER_NAME = "tests.exception_debug"
MARKER = "marker-value"

# Every helper below raises via <helper> -> _outer -> _inner, so the captured
# traceback has three frames and the innermost one is where it blew up.
FRAME_COUNT = 3
RAISING_FRAME = 2
OUTER_FRAME = 1


def _inner(payload: str) -> None:
    """Raise, with a distinctive local in scope for introspection tests."""
    inner_local = payload.upper()
    raise ValueError(f"boom: {inner_local}")


def _outer(payload: str = MARKER) -> None:
    """Call the raising frame, adding a second frame to the traceback."""
    outer_local = payload
    _inner(outer_local)


def make_traceback() -> TracebackType:
    """Return a real two-frame traceback."""
    try:
        _outer()
    except ValueError:
        return sys.exc_info()[2]
    raise AssertionError("_outer did not raise")


def make_exc_info() -> tuple[type[BaseException], BaseException, TracebackType]:
    """Return a real ``sys.exc_info()`` triple."""
    try:
        _outer()
    except ValueError:
        return sys.exc_info()
    raise AssertionError("_outer did not raise")


def log_exception(logger_name: str = LOGGER_NAME, level: int = logging.ERROR) -> None:
    """Log with live ``exc_info``, the way most Home Assistant code does."""
    try:
        _outer()
    except ValueError:
        logging.getLogger(logger_name).log(level, "job failed", exc_info=True)


def log_bad_format(logger_name: str = LOGGER_NAME) -> None:
    """Log with arguments that cannot be interpolated into the message."""
    try:
        _outer()
    except ValueError:
        logging.getLogger(logger_name).error("%d items", "many", exc_info=True)


def log_preformatted(logger_name: str = LOGGER_NAME) -> None:
    """Log the text only, the way HA's ``catch_log_exception`` does.

    The record carries no ``exc_info``; the handler must recover the live
    exception from ``sys.exc_info()`` instead.
    """
    try:
        _outer()
    except ValueError as err:
        logging.getLogger(logger_name).error("Error doing job: %s", err)
