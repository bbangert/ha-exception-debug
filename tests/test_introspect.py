"""Tests for frame introspection and in-frame evaluation."""

from __future__ import annotations

from types import FrameType

import pytest

from custom_components.exception_debug.introspect import (
    describe_frame,
    describe_frame_locals,
    eval_in_frame,
    iter_frames,
    safe_repr,
)

from .helpers import MARKER, RAISING_FRAME, make_traceback

MAX_REPR = 2000


class Exploding:
    """An object whose repr() raises, as some HA objects manage to."""

    def __repr__(self) -> str:
        """Raise instead of returning a repr."""
        raise RuntimeError("no repr for you")


@pytest.fixture
def raising_frame() -> FrameType:
    """Return the innermost frame of a real traceback."""
    frame, _lineno = iter_frames(make_traceback())[RAISING_FRAME]
    return frame


def test_safe_repr_passes_through_short_values() -> None:
    """Values under the cap are returned verbatim."""
    assert safe_repr("hello", MAX_REPR) == "'hello'"


def test_safe_repr_truncates_and_says_so() -> None:
    """Long values are cut and annotated with how much was dropped."""
    result = safe_repr("x" * 500, 100)

    assert result.startswith("'" + "x" * 99)
    assert len(result) < 200
    assert "truncated" in result


def test_safe_repr_survives_exploding_repr() -> None:
    """A repr() that raises is reported, not propagated."""
    result = safe_repr(Exploding(), MAX_REPR)

    assert "unreprable Exploding" in result
    assert "no repr for you" in result


def test_iter_frames_is_outermost_first() -> None:
    """Frames come back in call order, innermost last."""
    frames = iter_frames(make_traceback())

    assert [f.f_code.co_name for f, _ in frames][-2:] == ["_outer", "_inner"]


def test_iter_frames_of_none() -> None:
    """A missing traceback yields no frames."""
    assert iter_frames(None) == []


def test_describe_frame_fields(raising_frame: FrameType) -> None:
    """A frame summary carries location and local names, not values."""
    described = describe_frame(raising_frame, 42, RAISING_FRAME)

    assert described["index"] == RAISING_FRAME
    assert described["function"] == "_inner"
    assert described["lineno"] == 42
    assert described["filename"].endswith("helpers.py")
    assert described["local_names"] == ["inner_local", "payload"]


def test_describe_frame_locals(raising_frame: FrameType) -> None:
    """Locals come back as name -> capped repr."""
    locals_ = describe_frame_locals(raising_frame, MAX_REPR)

    assert locals_ == {
        "payload": repr(MARKER),
        "inner_local": repr(MARKER.upper()),
    }


def test_eval_expression(raising_frame: FrameType) -> None:
    """An expression is evaluated against the frame's locals."""
    result = eval_in_frame(raising_frame, "inner_local.lower()", MAX_REPR)

    assert result == {"ok": True, "mode": "eval", "result": repr(MARKER)}


def test_eval_reaches_frame_globals(raising_frame: FrameType) -> None:
    """The frame's module globals are in scope too."""
    result = eval_in_frame(raising_frame, "MARKER", MAX_REPR)

    assert result["ok"] is True
    assert result["result"] == repr(MARKER)


def test_eval_error_is_reported(raising_frame: FrameType) -> None:
    """A failing expression reports the error instead of raising."""
    result = eval_in_frame(raising_frame, "1 / 0", MAX_REPR)

    assert result["ok"] is False
    assert result["mode"] == "eval"
    assert result["error_type"] == "ZeroDivisionError"


def test_exec_statements_return_new_locals(raising_frame: FrameType) -> None:
    """Statements run in exec mode and surface what they defined."""
    result = eval_in_frame(raising_frame, "doubled = inner_local * 2", MAX_REPR)

    assert result["ok"] is True
    assert result["mode"] == "exec"
    assert result["result"] is None
    assert result["locals"]["doubled"] == repr(MARKER.upper() * 2)


def test_exec_does_not_mutate_the_real_frame(raising_frame: FrameType) -> None:
    """Evaluation works on a copy, so the captured frame stays intact."""
    eval_in_frame(raising_frame, "inner_local = 'clobbered'", MAX_REPR)

    assert raising_frame.f_locals["inner_local"] == MARKER.upper()


def test_exec_error_is_reported(raising_frame: FrameType) -> None:
    """A statement that raises is reported as an exec failure."""
    result = eval_in_frame(raising_frame, "raise RuntimeError('nope')", MAX_REPR)

    assert result["ok"] is False
    assert result["mode"] == "exec"
    assert result["error_type"] == "RuntimeError"


def test_syntax_error_is_reported(raising_frame: FrameType) -> None:
    """Input that is neither expression nor statement fails cleanly."""
    result = eval_in_frame(raising_frame, "def (", MAX_REPR)

    assert result["ok"] is False
    assert result["mode"] == "exec"
    assert result["error_type"] == "SyntaxError"


def test_eval_result_is_capped(raising_frame: FrameType) -> None:
    """A huge result is truncated by max_repr like everything else."""
    result = eval_in_frame(raising_frame, "'y' * 10000", 100)

    assert "truncated" in result["result"]
