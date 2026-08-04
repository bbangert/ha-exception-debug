"""Bounded, TTL-aware store of captured exceptions with live frames.

Each :class:`CapturedException` holds the real traceback object while it is
"live" (frames + locals inspectable). To bound memory, the store:

* caps the number of retained entries (FIFO eviction),
* clears a live entry's frames once its TTL elapses (releasing locals), and
* keeps a text snapshot (:class:`traceback.TracebackException`) so an entry
  stays listable and readable after its frames are gone.

Frames pin everything in scope (``hass``, coordinators, big state dicts), so the
TTL + cap are the safety valve that stops the debugger from leaking memory.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import traceback
from types import TracebackType
from typing import Any

from .const import DEFAULT_MAX_REPR
from .introspect import (
    describe_frame,
    describe_frame_locals,
    eval_in_frame,
    iter_frames,
    safe_repr,
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
    # Text fallback that survives after frames are cleared.
    formatted: str = ""
    root_cause: dict[str, Any] | None = None
    count: int = 1
    live_expires: float = 0.0

    @property
    def is_live(self) -> bool:
        """Whether the traceback frames are still available for introspection."""
        return self._tb is not None

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
            "count": self.count,
            "live": self.is_live,
            "root_cause": self.root_cause,
        }

    def frames(self, max_repr: int = DEFAULT_MAX_REPR) -> list[dict[str, Any]]:
        """Return frame summaries (empty if frames were cleared)."""
        if self._tb is None:
            return []
        return [
            describe_frame(frame, lineno, i, max_repr)
            for i, (frame, lineno) in enumerate(iter_frames(self._tb))
        ]

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

    def _frame_at(self, frame_index: int):
        if self._tb is None:
            raise LiveFramesExpired(self.id)
        frames = iter_frames(self._tb)
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
    """Bounded FIFO/TTL store of captured exceptions."""

    def __init__(self, max_entries: int, ttl: float, max_repr: int) -> None:
        """Initialise an empty store with its retention bounds."""
        self._entries: OrderedDict[str, CapturedException] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl
        self._max_repr = max_repr
        self._counter = 0

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
        formatted: str,
        root_cause: dict[str, Any] | None,
    ) -> CapturedException:
        """Store a new captured exception and enforce bounds."""
        entry = CapturedException(
            id=self._next_id(now),
            timestamp=now,
            logger_name=logger_name,
            level=level,
            message=message,
            exc_type=exc_type,
            exc_value_repr=exc_value_repr,
            _tb=tb,
            formatted=formatted,
            root_cause=root_cause,
            live_expires=now + self._ttl,
        )
        self._entries[entry.id] = entry
        self._enforce_bounds()
        return entry

    def expire(self, now: float) -> None:
        """Clear frames on entries whose live TTL has elapsed."""
        for entry in self._entries.values():
            if entry.is_live and now >= entry.live_expires:
                entry.clear_frames()

    def _enforce_bounds(self) -> None:
        while len(self._entries) > self._max_entries:
            _, evicted = self._entries.popitem(last=False)
            evicted.clear_frames()

    def get(self, entry_id: str) -> CapturedException | None:
        """Return an entry by id, or None."""
        return self._entries.get(entry_id)

    def list(self, limit: int | None = None) -> list[CapturedException]:
        """Return entries newest-first."""
        items = list(reversed(self._entries.values()))
        return items[:limit] if limit else items

    def clear(self) -> None:
        """Drop all entries and release their frames."""
        for entry in self._entries.values():
            entry.clear_frames()
        self._entries.clear()

    def full_traceback(self, entry: CapturedException) -> str:
        """Return the stored formatted traceback text for an entry."""
        return entry.formatted or safe_repr(entry.exc_value_repr, self._max_repr)
