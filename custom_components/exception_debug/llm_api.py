"""Expose the exception store as a Home Assistant LLM API.

Registering an :class:`llm.API` makes these tools selectable inside the core
``mcp_server`` integration's options, so an AI agent reaches them over MCP at
``/api/mcp`` with Home Assistant's own OAuth / long-lived-token auth. No extra
HTTP server or MCP plumbing is required here.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType, JsonValueType
import voluptuous as vol

from .const import LLM_API_ID, LLM_API_NAME
from .store import ExceptionStore, FrameIndexError, LiveFramesExpired

API_PROMPT = (
    "Use these tools to inspect recent Home Assistant exceptions. "
    "Each captured exception has an id and, while still 'live', a list of "
    "traceback frames (index 0 = outermost call). You can read a frame's local "
    "variables and, if evaluation is enabled, run Python in a frame's context "
    "for post-mortem debugging."
)


class ExceptionDebugAPI(llm.API):
    """LLM API surfacing captured exceptions and frame introspection."""

    def __init__(
        self, hass: HomeAssistant, store: ExceptionStore, enable_eval: bool
    ) -> None:
        """Initialise the API over ``store``."""
        super().__init__(hass=hass, id=LLM_API_ID, name=LLM_API_NAME)
        self._store = store
        self._enable_eval = enable_eval

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the set of tools for this API."""
        tools: list[llm.Tool] = [
            ListExceptionsTool(self._store),
            GetTracebackTool(self._store),
            GetFramesTool(self._store),
            GetFrameLocalsTool(self._store),
        ]
        if self._enable_eval:
            tools.append(EvalInFrameTool(self._store))
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=tools,
        )


class _StoreTool(llm.Tool):
    """Base tool holding a reference to the store."""

    def __init__(self, store: ExceptionStore) -> None:
        """Initialise the tool with the store it reads from."""
        self._store = store


class ListExceptionsTool(_StoreTool):
    """List recently captured exceptions, newest first."""

    name = "list_exceptions"
    description = "List recently captured Home Assistant exceptions (newest first)."
    parameters = vol.Schema(
        {vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=200))}
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Return summaries of recently captured exceptions."""
        limit = tool_input.tool_args.get("limit", 20)
        entries = self._store.list(limit=limit)
        return {"exceptions": [e.summary() for e in entries]}


class GetTracebackTool(_StoreTool):
    """Return the full formatted traceback text for an exception id."""

    name = "get_traceback"
    description = "Return the full formatted traceback text for an exception id."
    parameters = vol.Schema({vol.Required("id"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Return the formatted traceback text for an exception."""
        entry = self._store.get(tool_input.tool_args["id"])
        if entry is None:
            return _not_found(tool_input.tool_args["id"])
        return {
            "id": entry.id,
            "live": entry.is_live,
            "traceback": self._store.full_traceback(entry),
        }


class GetFramesTool(_StoreTool):
    """List the traceback frames of a captured exception."""

    name = "get_frames"
    description = (
        "List the traceback frames of a captured exception. Returns each frame's "
        "index, file, line, function, and local variable names. Empty if the "
        "exception's live frames have expired."
    )
    parameters = vol.Schema({vol.Required("id"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Return the frame summaries for an exception."""
        entry = self._store.get(tool_input.tool_args["id"])
        if entry is None:
            return _not_found(tool_input.tool_args["id"])
        # list is invariant, so the precise list[dict[str, JsonValueType]]
        # from frames() has to be widened explicitly for JsonObjectType.
        frames: list[JsonValueType] = list(entry.frames())
        return {"id": entry.id, "live": entry.is_live, "frames": frames}


class GetFrameLocalsTool(_StoreTool):
    """Return the local variables of a specific frame."""

    name = "get_frame_locals"
    description = (
        "Return {name: repr} of local variables for a given frame index of a "
        "captured exception."
    )
    parameters = vol.Schema({vol.Required("id"): str, vol.Required("frame"): int})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Return the local variables of one frame."""
        entry = self._store.get(tool_input.tool_args["id"])
        if entry is None:
            return _not_found(tool_input.tool_args["id"])
        try:
            return {
                "id": entry.id,
                "frame": tool_input.tool_args["frame"],
                "locals": entry.frame_locals(tool_input.tool_args["frame"]),
            }
        except (LiveFramesExpired, FrameIndexError) as err:
            return {"error": str(err)}


class EvalInFrameTool(_StoreTool):
    """Evaluate Python in the context of a captured frame (RCE — guarded)."""

    name = "eval_in_frame"
    description = (
        "Evaluate a Python expression or statements in the context of a captured "
        "frame, for post-mortem debugging. Has access to that frame's locals and "
        "globals. Returns the repr of the result."
    )
    parameters = vol.Schema(
        {
            vol.Required("id"): str,
            vol.Required("frame"): int,
            vol.Required("source"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Evaluate the given source in the chosen frame."""
        entry = self._store.get(tool_input.tool_args["id"])
        if entry is None:
            return _not_found(tool_input.tool_args["id"])
        try:
            return {
                "id": entry.id,
                "frame": tool_input.tool_args["frame"],
                **entry.eval(
                    tool_input.tool_args["frame"], tool_input.tool_args["source"]
                ),
            }
        except (LiveFramesExpired, FrameIndexError) as err:
            return {"error": str(err)}


def _not_found(entry_id: str) -> dict[str, Any]:
    return {"error": f"No captured exception with id '{entry_id}'."}
