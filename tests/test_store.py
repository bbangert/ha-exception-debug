"""Tests for the bounded, TTL-aware exception store."""

from __future__ import annotations

import logging
import threading
import traceback

import pytest

from custom_components.exception_debug.const import MIN_MAX_REPR
from custom_components.exception_debug.store import (
    CapturedException,
    ExceptionStore,
    FrameIndexError,
    LiveFramesExpired,
)

from .helpers import FRAME_COUNT, MARKER, RAISING_FRAME, make_exc_info

NOW = 1_000_000.0


def _store(
    max_entries: int = 3, ttl: float = 100.0, max_repr: int = 2000
) -> ExceptionStore:
    return ExceptionStore(max_entries=max_entries, ttl=ttl, max_repr=max_repr)


def _add(
    store: ExceptionStore, now: float = NOW, live: bool = True
) -> CapturedException:
    """Add one real captured exception to ``store``."""
    exc_type, exc_value, tb = make_exc_info()
    return store.add(
        now=now,
        logger_name="tests",
        level=logging.ERROR,
        message="job failed",
        exc_type=exc_type.__name__,
        exc_value_repr=repr(exc_value),
        tb=tb if live else None,
        te=traceback.TracebackException(exc_type, exc_value, tb, lookup_lines=False),
        root_cause={"filename": "helpers.py", "lineno": 1, "function": "_inner"},
    )


def test_add_returns_live_entry() -> None:
    """A freshly added entry keeps its frames and reports itself live."""
    store = _store()
    entry = _add(store)

    assert entry.is_live
    assert entry.exc_type == "ValueError"
    assert entry.live_expires == NOW + 100.0
    assert len(entry.frames()) == FRAME_COUNT


def test_ids_are_unique() -> None:
    """Two captures in the same second still get distinct ids."""
    store = _store()

    first = _add(store)
    second = _add(store)

    assert first.id != second.id


def test_list_is_newest_first_and_limited() -> None:
    """Listing returns newest first and honours the limit."""
    store = _store(max_entries=10)
    first = _add(store)
    second = _add(store)
    third = _add(store)

    assert [e.id for e in store.list()] == [third.id, second.id, first.id]
    assert [e.id for e in store.list(limit=2)] == [third.id, second.id]


def test_fifo_eviction_clears_evicted_frames() -> None:
    """Exceeding max_entries drops the oldest and releases its frames."""
    store = _store(max_entries=2)
    oldest = _add(store)
    _add(store)
    _add(store)

    assert store.get(oldest.id) is None
    assert not oldest.is_live
    assert oldest.frames() == []


def test_expire_clears_frames_but_keeps_text() -> None:
    """Past the TTL an entry loses frames but stays listable and readable."""
    store = _store(ttl=100.0)
    entry = _add(store, now=NOW)

    store.expire(NOW + 99.0)
    assert entry.is_live

    store.expire(NOW + 100.0)
    assert not entry.is_live
    assert store.get(entry.id) is entry
    assert "ValueError" in store.full_traceback(entry)
    assert entry.summary()["live"] is False


def test_clear_releases_everything() -> None:
    """clear() empties the store and releases frames."""
    store = _store()
    entry = _add(store)

    store.clear()

    assert store.list() == []
    assert not entry.is_live


def test_frame_locals_of_live_entry() -> None:
    """Locals of the raising frame are readable while the entry is live."""
    store = _store()
    entry = _add(store)

    locals_ = entry.frame_locals(RAISING_FRAME)

    assert locals_["inner_local"] == repr(MARKER.upper())


def test_frame_locals_raises_once_expired() -> None:
    """Reading locals after expiry reports the frames are gone."""
    store = _store()
    entry = _add(store)
    entry.clear_frames()

    with pytest.raises(LiveFramesExpired, match=entry.id):
        entry.frame_locals(0)


def test_frame_index_out_of_range() -> None:
    """An out-of-range frame index is reported rather than an IndexError."""
    store = _store()
    entry = _add(store)

    with pytest.raises(FrameIndexError, match="out of range"):
        entry.frame_locals(99)


def test_clear_frames_is_idempotent() -> None:
    """Clearing twice is safe (eviction and TTL can both fire)."""
    store = _store()
    entry = _add(store)

    entry.clear_frames()
    entry.clear_frames()

    assert not entry.is_live


def test_full_traceback_falls_back_to_value_repr() -> None:
    """The fallback renders the exception repr, not a repr *of* that repr.

    exc_value_repr is already safe_repr() output, so passing it through
    safe_repr() again would wrap it in a second layer of quotes and escaping
    that callers would see in REST and LLM responses.
    """
    store = _store()
    entry = _add(store)
    entry._te = None

    result = store.full_traceback(entry)

    assert result == entry.exc_value_repr
    assert result.startswith("ValueError(")
    assert not result.startswith(('"', "'"))


def test_summary_shape() -> None:
    """The listing summary carries the fields callers rely on."""
    store = _store()
    entry = _add(store)

    summary = entry.summary()

    assert summary["id"] == entry.id
    assert summary["logger"] == "tests"
    assert summary["level"] == logging.ERROR
    assert summary["exc_type"] == "ValueError"
    assert summary["live"] is True
    assert summary["root_cause"]["function"] == "_inner"


def _write_until(
    store: ExceptionStore, stop: threading.Event, errors: list[BaseException]
) -> None:
    """Add entries continuously until told to stop.

    Continuous rather than a fixed count: a writer that finishes early never
    overlaps the reader, which is exactly why a naive version of this test
    passes even with the lock removed.
    """
    exc_type, _exc_value, tb = make_exc_info()
    try:
        while not stop.is_set():
            store.add(
                now=NOW,
                logger_name="tests",
                level=logging.ERROR,
                message="job failed",
                exc_type=exc_type.__name__,
                exc_value_repr="",
                tb=tb,
                te=None,
                root_cause=None,
            )
    except BaseException as err:  # noqa: BLE001 - re-asserted on the main thread
        errors.append(err)


def _read_rounds(
    store: ExceptionStore, stop: threading.Event, errors: list[BaseException]
) -> None:
    """Expire and list repeatedly, then release the writer.

    ``expire()`` is the sharp end: it walks ``_entries`` with a Python-level
    ``for`` loop, so the interpreter can switch threads mid-iteration. A bare
    ``list()`` proves nothing — it is a single C-level call that never yields
    the GIL part-way through.
    """
    try:
        for _ in range(2000):
            store.expire(NOW + 1.0)
            store.list(limit=5)
    except BaseException as err:  # noqa: BLE001 - re-asserted on the main thread
        errors.append(err)
    finally:
        stop.set()


def test_concurrent_reads_and_writes_do_not_raise() -> None:
    """Reading on the loop while a logging thread captures must be safe.

    This is the real deployment shape: emit() runs in whatever thread logged,
    while the expiry timer and the REST/WebSocket/LLM surfaces read from the
    event loop. Without ExceptionStore's lock this fails with
    ``RuntimeError: OrderedDict mutated during iteration``.
    """
    store = _store(max_entries=200)
    errors: list[BaseException] = []
    stop = threading.Event()

    writer = threading.Thread(target=_write_until, args=(store, stop, errors))
    reader = threading.Thread(target=_read_rounds, args=(store, stop, errors))
    writer.start()
    reader.start()
    reader.join()
    writer.join()

    assert errors == []


def test_negative_frame_index_is_rejected() -> None:
    """A negative index is refused rather than wrapping to the last frame."""
    store = _store()
    entry = _add(store)

    with pytest.raises(FrameIndexError, match="out of range"):
        entry.frame_locals(-1)


def test_formatted_is_not_materialised_at_capture() -> None:
    """Capture must not pay the cost of formatting the traceback.

    format() reads source files off disk via linecache, and emit() can run on
    the event loop thread, so the text is built on first read instead.
    """
    store = _store()
    entry = _add(store)

    assert entry._formatted is None

    assert "ValueError: boom" in entry.formatted
    assert entry._formatted is not None


def test_formatted_survives_frame_release() -> None:
    """The text snapshot outlives the frames it was captured from."""
    store = _store()
    entry = _add(store)
    entry.clear_frames()

    assert not entry.is_live
    assert "ValueError: boom" in entry.formatted
    assert "_inner" in entry.formatted


def test_full_traceback_is_capped() -> None:
    """A huge traceback is truncated rather than returned whole."""
    store = _store(max_repr=MIN_MAX_REPR)
    entry = _add(store)
    entry._formatted = "x" * 100_000

    result = store.full_traceback(entry)

    assert len(result) < 100_000
    assert "truncated" in result
