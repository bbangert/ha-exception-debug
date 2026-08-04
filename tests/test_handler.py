"""Tests for the root-logger capture handler.

Most cases use a logger wired directly to a fresh handler rather than the real
root logger, so unrelated Home Assistant log traffic cannot land in the store.
One end-to-end case covers the actual root-logger attachment.
"""

from __future__ import annotations

from collections.abc import Generator
import logging
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.exception_debug.handler import ExceptionCaptureHandler
from custom_components.exception_debug.store import ExceptionStore

from .helpers import (
    LOGGER_NAME,
    MARKER,
    RAISING_FRAME,
    log_bad_format,
    log_exception,
    log_preformatted,
)

ISOLATED = "tests.exception_debug.isolated"


@pytest.fixture
def isolated(request: pytest.FixtureRequest) -> Generator[ExceptionStore]:
    """Build a store fed by a handler on a private logger, off the root logger.

    Parametrise indirectly to change the handler's capture level.
    """
    level = getattr(request, "param", logging.ERROR)
    store = ExceptionStore(max_entries=10, ttl=100.0, max_repr=2000)
    handler = ExceptionCaptureHandler(store, level)
    logger = logging.getLogger(ISOLATED)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    yield store
    logger.removeHandler(handler)


def test_captures_live_exc_info(isolated: ExceptionStore) -> None:
    """A record carrying exc_info is stored with inspectable frames."""
    log_exception(ISOLATED)

    entry = isolated.list()[0]

    assert isolated.list() == [entry]
    assert entry.is_live
    assert entry.exc_type == "ValueError"
    assert entry.logger_name == ISOLATED
    assert entry.message == "job failed"
    assert entry.root_cause["function"] == "_inner"
    assert "ValueError: boom" in entry.formatted


def test_captures_via_sys_exc_info_fallback(isolated: ExceptionStore) -> None:
    """A pre-formatted record still yields live frames via sys.exc_info().

    This is the shape HA's ``catch_log_exception`` produces: text only, no
    ``exc_info`` on the record.
    """
    log_preformatted(ISOLATED)

    entry = isolated.list()[0]

    assert entry.is_live
    assert entry.exc_type == "ValueError"
    assert entry.message.startswith("Error doing job:")
    assert entry.frame_locals(RAISING_FRAME)["inner_local"] == repr(MARKER.upper())


@pytest.mark.parametrize("isolated", [logging.CRITICAL], indirect=True)
def test_ignores_records_below_level(isolated: ExceptionStore) -> None:
    """Records under the configured level are not captured."""
    log_exception(ISOLATED, logging.ERROR)

    assert isolated.list() == []


def test_ignores_records_without_an_exception(isolated: ExceptionStore) -> None:
    """A plain error log with nothing on the stack is not captured."""
    logging.getLogger(ISOLATED).error("nothing actually went wrong")

    assert isolated.list() == []


def test_unformattable_message_is_reported(isolated: ExceptionStore) -> None:
    """A message whose args do not interpolate does not lose the capture."""
    log_bad_format(ISOLATED)

    entry = isolated.list()[0]

    assert entry.exc_type == "ValueError"
    assert entry.message.startswith("<unformattable log message:")


def test_emit_never_raises(isolated: ExceptionStore) -> None:
    """A failure inside emit() goes to logging's error machinery, not up."""
    with (
        patch.object(ExceptionStore, "add", side_effect=RuntimeError("store broke")),
        patch.object(ExceptionCaptureHandler, "handleError") as handle_error,
    ):
        log_exception(ISOLATED)

    assert handle_error.called
    assert isolated.list() == []


def test_root_cause_without_traceback() -> None:
    """An exception with no traceback reports no root cause."""
    assert ExceptionCaptureHandler._root_cause(None) is None


async def test_attached_to_root_logger(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """End to end: the configured entry captures from the real root logger."""
    log_exception(LOGGER_NAME)

    entry = setup_integration.runtime_data.store.list()[0]

    assert entry.logger_name == LOGGER_NAME
    assert entry.is_live
    assert entry.frame_locals(RAISING_FRAME)["inner_local"] == repr(MARKER.upper())
