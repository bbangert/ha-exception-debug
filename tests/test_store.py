"""Tests for the bounded, TTL-aware exception store."""

from __future__ import annotations

import logging
import traceback

import pytest

from custom_components.exception_debug.store import (
    ExceptionStore,
    FrameIndexError,
    LiveFramesExpired,
)

from .helpers import FRAME_COUNT, MARKER, RAISING_FRAME, make_exc_info

NOW = 1_000_000.0


def _store(max_entries: int = 3, ttl: float = 100.0, max_repr: int = 2000):
    return ExceptionStore(max_entries=max_entries, ttl=ttl, max_repr=max_repr)


def _add(store: ExceptionStore, now: float = NOW, live: bool = True):
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
        formatted="".join(traceback.format_exception(exc_type, exc_value, tb)),
        root_cause={"filename": "helpers.py", "lineno": 1, "function": "_inner"},
    )


def test_add_returns_live_entry() -> None:
    """A freshly added entry keeps its frames and reports itself live."""
    store = _store()
    entry = _add(store)

    assert entry.is_live
    assert entry.count == 1
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
    """An entry stored without formatted text still renders something."""
    store = _store()
    entry = _add(store)
    entry.formatted = ""

    assert "ValueError" in store.full_traceback(entry)


def test_summary_shape() -> None:
    """The listing summary carries the fields callers rely on."""
    store = _store()
    entry = _add(store)

    summary = entry.summary()

    assert summary["id"] == entry.id
    assert summary["logger"] == "tests"
    assert summary["level"] == logging.ERROR
    assert summary["exc_type"] == "ValueError"
    assert summary["count"] == 1
    assert summary["live"] is True
    assert summary["root_cause"]["function"] == "_inner"
