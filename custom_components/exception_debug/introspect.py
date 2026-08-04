"""Frame introspection and in-frame evaluation helpers.

This module holds the "debugtoolbar" heart of the integration: given a captured
traceback, walk its frames, snapshot local variables, and (optionally) evaluate
an expression or statement in the context of a chosen frame.

All repr() output is length-capped and failure-tolerant so a hostile or
expensive __repr__ on a Home Assistant object can never take down the caller.
"""

from __future__ import annotations

import traceback
from types import FrameType, TracebackType
from typing import Any

from homeassistant.util.json import JsonValueType


def safe_repr(value: Any, max_repr: int) -> str:
    """Return a length-capped repr() that never raises."""
    try:
        text = repr(value)
    except Exception as err:  # noqa: BLE001 - reprs can raise anything
        return f"<unreprable {type(value).__name__}: {err!r}>"
    if len(text) > max_repr:
        return text[:max_repr] + f"... [truncated {len(text) - max_repr} chars]"
    return text


def iter_frames(tb: TracebackType | None) -> list[tuple[FrameType, int]]:
    """Return (frame, lineno) pairs from oldest to newest call."""
    return list(traceback.walk_tb(tb))


def describe_frame(
    frame: FrameType, lineno: int, index: int
) -> dict[str, JsonValueType]:
    """Return a JSON-serializable summary of a single frame.

    Local *names* only — values are never included here, so nothing needs
    length-capping. Use :func:`describe_frame_locals` for values.
    """
    code = frame.f_code
    # Annotated so the element type is JsonValueType: list is invariant, so a
    # bare list[str] would not satisfy the JSON-shaped return type.
    local_names: list[JsonValueType] = [name for name in sorted(frame.f_locals)]
    return {
        "index": index,
        "filename": code.co_filename,
        "lineno": lineno,
        "function": code.co_name,
        "local_names": local_names,
    }


def describe_frame_locals(frame: FrameType, max_repr: int) -> dict[str, str]:
    """Return {name: safe_repr(value)} for a frame's locals."""
    # Copy first: f_locals may be a write-through proxy (PEP 667, py3.13).
    items = dict(frame.f_locals)
    return {name: safe_repr(value, max_repr) for name, value in items.items()}


def eval_in_frame(frame: FrameType, source: str, max_repr: int) -> dict[str, Any]:
    """Evaluate ``source`` in the context of ``frame``.

    Tries eval() first (expression) then falls back to exec() (statements).
    Returns a dict describing the result or the error. This is arbitrary code
    execution by design and must only be reachable behind authentication.
    """
    globs = frame.f_globals
    locs = dict(frame.f_locals)
    try:
        compiled = compile(source, "<exception_debug>", "eval")
        value = eval(compiled, globs, locs)  # noqa: S307 - the whole point of this tool
        return {"ok": True, "mode": "eval", "result": safe_repr(value, max_repr)}
    except SyntaxError:
        # Not an expression - run as one or more statements.
        try:
            compiled = compile(source, "<exception_debug>", "exec")
            exec(compiled, globs, locs)  # noqa: S102 - the whole point of this tool
        except Exception as err:  # noqa: BLE001 - user code raises anything
            return {
                "ok": False,
                "mode": "exec",
                "error": safe_repr(err, max_repr),
                "error_type": type(err).__name__,
            }
        # Surface any new/changed locals the snippet produced.
        return {
            "ok": True,
            "mode": "exec",
            "result": None,
            "locals": {name: safe_repr(val, max_repr) for name, val in locs.items()},
        }
    except Exception as err:  # noqa: BLE001 - user code raises anything
        return {
            "ok": False,
            "mode": "eval",
            "error": safe_repr(err, max_repr),
            "error_type": type(err).__name__,
        }
