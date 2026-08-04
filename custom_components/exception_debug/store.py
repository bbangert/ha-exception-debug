"""Bounded, TTL-aware store of captured exceptions with live frames.

Each :class:`CapturedException` holds the real traceback object while it is
"live" (frames + locals inspectable). To bound memory, the store:

* caps the number of retained entries (FIFO eviction),
* clears a live entry's frames once its TTL elapses (releasing locals), and
* keeps a text snapshot (:class:`traceback.TracebackException`) so an entry
  stays listable and readable after its frames are gone.

Frames pin everything in scope (``hass``, coordinators, big state dicts), so the
TTL + cap are the safety valve that stops the debugger from leaking memory.

**Thread safety.** Entries are written by ``ExceptionCaptureHandler.emit`` in
whatever thread emitted the log record, and read from the event loop by the
REST/WebSocket/LLM surfaces. ``logging.Handler.handle`` already serialises
concurrent ``emit`` calls behind the handler's own lock, so writer-vs-writer is
safe — but reader-vs-writer is not. Without the lock below, iterating
``_entries`` on the loop while a logging thread inserts raises
``RuntimeError: OrderedDict mutated during iteration``.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import traceback
from types import FrameType, TracebackType
from typing import Any

from homeassistant.util.json import JsonValueType

from .const import DEFAULT_MAX_REPR, MAX_TRACEBACK_FACTOR
from .introspect import (
    describe_frame,
    describe_frame_locals,
    eval_in_frame,
    iter_frames,
)


@dataclass
class CapturedException:
    """A single captured exception and (while live) its traceback frames."""

    id: str
    timestamp: float
    logger_name: str
    level: int
    message: str
    exc_type: str
    exc_value_repr: str
    # Live objects. Set to None once cleared by TTL/eviction.
    _tb: TracebackType | None = field(default=None, repr=False)
    # Structural snapshot taken at capture time with ``lookup_lines=False``, so
    # building it costs no disk I/O on the emitting thread. It holds filenames
    # and line numbers rather than frames, so it outlives ``clear_frames()``.
    _te: traceback.TracebackException | None = field(default=None, repr=False)
    _formatted: str | None = field(default=None, repr=False)
    root_cause: dict[str, Any] | None = None
    live_expires: float = 0.0

    @property
    def is_live(self) -> bool:
        """Whether the traceback frames are still available for introspection."""
        return self._tb is not None

    @property
    def formatted(self) -> str:
        """Return the formatted traceback text, materialised on first read.

        Deliberately lazy: ``TracebackException.format()`` calls
        ``linecache.getline()`` per frame, which hits the disk the first time a
        source file is seen. Doing that inside ``emit()`` would put blocking
        I/O on whatever thread logged — frequently the event loop.
        """
        if self._formatted is None:
            self._formatted = "".join(self._te.format()) if self._te is not None else ""
        return self._formatted

    def clear_frames(self) -> None:
        """Release frame locals but keep the text snapshot and metadata."""
        if self._tb is not None:
            traceback.clear_frames(self._tb)
            self._tb = None

    def summary(self) -> dict[str, Any]:
        """JSON-serializable listing summary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "logger": self.logger_name,
            "level": self.level,
            "message": self.message,
            "exc_type": self.exc_type,
            "exc_value": self.exc_value_repr,
            "live": self.is_live,
            "root_cause": self.root_cause,
        }

    def frames(self) -> list[dict[str, JsonValueType]]:
        """Return frame summaries (empty if frames were cleared)."""
        # Bind once: another thread may clear frames between the check and use.
        tb = self._tb
        if tb is None:
            return []
        described: list[dict[str, JsonValueType]] = [
            describe_frame(frame, lineno, i)
            for i, (frame, lineno) in enumerate(iter_frames(tb))
        ]
        return described

    def frame_locals(
        self, frame_index: int, max_repr: int = DEFAULT_MAX_REPR
    ) -> dict[str, str]:
        """Return {name: repr} of locals for a given frame index."""
        frame = self._frame_at(frame_index)
        return describe_frame_locals(frame, max_repr)

    def eval(
        self, frame_index: int, source: str, max_repr: int = DEFAULT_MAX_REPR
    ) -> dict[str, Any]:
        """Evaluate ``source`` in the chosen frame's context."""
        frame = self._frame_at(frame_index)
        return eval_in_frame(frame, source, max_repr)

    def _frame_at(self, frame_index: int) -> FrameType:
        """Return the frame at ``frame_index``, or raise."""
        # Bind once so a concurrent clear_frames() cannot null it mid-use.
        tb = self._tb
        if tb is None:
            raise LiveFramesExpired(self.id)
        frames = iter_frames(tb)
        if frame_index < 0 or frame_index >= len(frames):
            raise FrameIndexError(frame_index, len(frames))
        return frames[frame_index][0]


class LiveFramesExpired(RuntimeError):
    """Raised when frames for an entry have already been cleared."""

    def __init__(self, entry_id: str) -> None:
        """Initialise with the id whose frames were released."""
        super().__init__(
            f"Live frames for exception '{entry_id}' have expired; "
            "only the text traceback remains."
        )


class FrameIndexError(IndexError):
    """Raised for an out-of-range frame index."""

    def __init__(self, requested: int, count: int) -> None:
        """Initialise with the requested index and the frame count."""
        super().__init__(f"Frame index {requested} out of range (0..{count - 1}).")


class ExceptionStore:
    """Bounded FIFO/TTL store of captured exceptions.

    All access is guarded by a re-entrant lock because writes arrive from
    arbitrary logging threads while reads happen on the event loop.
    """

    def __init__(self, max_entries: int, ttl: float, max_repr: int) -> None:
        """Initialise an empty store with its retention bounds."""
        self._entries: OrderedDict[str, CapturedException] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl
        self._max_repr = max_repr
        self._counter = 0
        # Re-entrant: add() calls _enforce_bounds() while already holding it.
        self._lock = threading.RLock()

    @property
    def max_repr(self) -> int:
        """Configured repr length cap."""
        return self._max_repr

    def _next_id(self, now: float) -> str:
        self._counter += 1
        return f"exc-{int(now)}-{self._counter}"

    def add(
        self,
        *,
        now: float,
        logger_name: str,
        level: int,
        message: str,
        exc_type: str,
        exc_value_repr: str,
        tb: TracebackType | None,
        te: traceback.TracebackException | None,
        root_cause: dict[str, Any] | None,
    ) -> CapturedException:
        """Store a new captured exception and enforce bounds."""
        with self._lock:
            entry = CapturedException(
                id=self._next_id(now),
                timestamp=now,
                logger_name=logger_name,
                level=level,
                message=message,
                exc_type=exc_type,
                exc_value_repr=exc_value_repr,
                _tb=tb,
                _te=te,
                root_cause=root_cause,
                live_expires=now + self._ttl,
            )
            self._entries[entry.id] = entry
            self._enforce_bounds()
            return entry

    def expire(self, now: float) -> None:
        """Clear frames on entries whose live TTL has elapsed."""
        with self._lock:
            for entry in self._entries.values():
                if entry.is_live and now >= entry.live_expires:
                    entry.clear_frames()

    def _enforce_bounds(self) -> None:
        """Evict oldest entries past the cap. Caller must hold the lock."""
        while len(self._entries) > self._max_entries:
            _, evicted = self._entries.popitem(last=False)
            evicted.clear_frames()

    def get(self, entry_id: str) -> CapturedException | None:
        """Return an entry by id, or None."""
        with self._lock:
            return self._entries.get(entry_id)

    def list(self, limit: int | None = None) -> list[CapturedException]:
        """Return entries newest-first."""
        with self._lock:
            items = list(reversed(self._entries.values()))
        return items[:limit] if limit else items

    def clear(self) -> None:
        """Drop all entries and release their frames."""
        with self._lock:
            for entry in self._entries.values():
                entry.clear_frames()
            self._entries.clear()

    def full_traceback(self, entry: CapturedException) -> str:
        """Return the stored traceback text, length-capped.

        Capped because nothing else bounds it: a traceback through many
        distinct frames can be large, and entries are retained until evicted.
        (Runaway *recursion* is already collapsed by ``traceback`` itself via
        its "[Previous line repeated N more times]" cutoff.)
        """
        # exc_value_repr is already safe_repr() output from capture time, so it
        # is used as-is: running repr() over it again would add a second layer
        # of quotes and escaping to what callers see.
        text = entry.formatted or entry.exc_value_repr
        limit = self._max_repr * MAX_TRACEBACK_FACTOR
        if len(text) > limit:
            return text[:limit] + f"... [truncated {len(text) - limit} chars]"
        return text
