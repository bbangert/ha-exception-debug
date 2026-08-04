"""Root-logger handler that captures live exceptions into the store.

Attached directly to ``logging.root`` so it runs as a sibling of Home
Assistant's ``QueueHandler`` and therefore sees records *before* the queue's
``prepare()`` strips ``exc_info``. When a record carries no ``exc_info`` (e.g.
HA's ``catch_log_exception`` logs pre-formatted text), we fall back to
``sys.exc_info()`` because our ``emit`` runs synchronously in the logging
thread, still inside the originating ``except`` block.
"""

from __future__ import annotations

import logging
import sys
import time
import traceback
from types import TracebackType
from typing import Any

from .introspect import safe_repr
from .store import ExceptionStore


class ExceptionCaptureHandler(logging.Handler):
    """A logging handler that retains live exception objects."""

    def __init__(self, store: ExceptionStore, level: int) -> None:
        super().__init__(level)
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        """Capture a record's exception, retaining live frames."""
        try:
            exc_info = record.exc_info
            if not exc_info or exc_info[1] is None:
                # Recover the live exception still on the stack, if any.
                fallback = sys.exc_info()
                if fallback[1] is not None:
                    exc_info = fallback
            if not exc_info or exc_info[1] is None:
                return  # Nothing exception-like to capture.

            exc_type, exc_value, tb = exc_info
            now = time.time()
            self._store.expire(now)

            self._store.add(
                now=now,
                logger_name=record.name,
                level=record.levelno,
                message=self._safe_message(record),
                exc_type=getattr(exc_type, "__name__", str(exc_type)),
                exc_value_repr=safe_repr(exc_value, self._store.max_repr),
                tb=tb,
                formatted="".join(
                    traceback.format_exception(exc_type, exc_value, tb)
                ),
                root_cause=self._root_cause(tb),
            )
        except Exception:  # noqa: BLE001 - a handler must never raise
            # Last resort: hand off to logging's own error machinery.
            self.handleError(record)

    @staticmethod
    def _safe_message(record: logging.LogRecord) -> str:
        try:
            return record.getMessage()
        except Exception as err:  # noqa: BLE001
            return f"<unformattable log message: {err!r}>"

    @staticmethod
    def _root_cause(tb: TracebackType | None) -> dict[str, Any] | None:
        frames = list(traceback.walk_tb(tb))
        if not frames:
            return None
        frame, lineno = frames[-1]
        code = frame.f_code
        return {
            "filename": code.co_filename,
            "lineno": lineno,
            "function": code.co_name,
        }
