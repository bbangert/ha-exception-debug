"""Tests for the LLM API that core mcp_server exposes to agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.exception_debug.const import (
    CONF_ENABLE_EVAL,
    DEFAULT_OPTIONS,
    LLM_API_ID,
)

from .helpers import LOGGER_NAME, MARKER, RAISING_FRAME, log_exception

type ToolCaller = Callable[..., Awaitable[dict[str, Any]]]

EVAL_ENABLED = {**DEFAULT_OPTIONS, CONF_ENABLE_EVAL: True}

BASE_TOOLS = {
    "list_exceptions",
    "get_traceback",
    "get_frames",
    "get_frame_locals",
}


@pytest.fixture
def llm_context() -> llm.LLMContext:
    """Build a minimal LLM context, as an MCP client would produce."""
    return llm.LLMContext(
        platform="test",
        context=Context(),
        language="en",
        assistant=None,
        device_id=None,
    )


@pytest.fixture
async def api(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    llm_context: llm.LLMContext,
) -> llm.APIInstance:
    """Resolve the registered API the way mcp_server resolves it."""
    return await llm.async_get_api(hass, LLM_API_ID, llm_context)


@pytest.fixture
async def captured_id(setup_integration: MockConfigEntry) -> str:
    """Capture one exception and return its id."""
    log_exception(LOGGER_NAME)
    return setup_integration.runtime_data.store.list()[0].id


@pytest.fixture
def call_tool(hass: HomeAssistant, api: llm.APIInstance) -> ToolCaller:
    """Invoke a tool the way an agent would, arguments schema-checked.

    ``APIInstance.async_call_tool`` only adds conversation tracing before
    dispatching, and importing that component pulls in speech dependencies the
    test harness does not ship — so resolve and call the tool directly, but
    still push the arguments through the tool's own voluptuous schema.
    """

    async def _call(name: str, **args: Any) -> dict[str, Any]:
        tool = next(tool for tool in api.tools if tool.name == name)
        return await tool.async_call(
            hass,
            llm.ToolInput(tool_name=name, tool_args=tool.parameters(args), id=None),
            api.llm_context,
        )

    return _call


async def test_tools_without_eval(api: llm.APIInstance) -> None:
    """Evaluation is off by default, so the eval tool is not offered."""
    assert {tool.name for tool in api.tools} == BASE_TOOLS
    assert "captured" in api.api_prompt


@pytest.mark.parametrize("options", [EVAL_ENABLED])
async def test_eval_tool_only_when_enabled(api: llm.APIInstance) -> None:
    """Turning the option on adds the in-frame eval tool."""
    assert {tool.name for tool in api.tools} == BASE_TOOLS | {"eval_in_frame"}


async def test_list_exceptions(call_tool: ToolCaller, captured_id: str) -> None:
    """Listing returns summaries of captured exceptions."""
    result = await call_tool("list_exceptions", limit=5)

    assert [e["id"] for e in result["exceptions"]] == [captured_id]
    assert result["exceptions"][0]["exc_type"] == "ValueError"


async def test_get_traceback(call_tool: ToolCaller, captured_id: str) -> None:
    """The traceback tool returns the formatted text."""
    result = await call_tool("get_traceback", id=captured_id)

    assert result["live"] is True
    assert "ValueError: boom" in result["traceback"]


async def test_get_frames(call_tool: ToolCaller, captured_id: str) -> None:
    """The frames tool lists each frame with its local names."""
    result = await call_tool("get_frames", id=captured_id)

    frame = result["frames"][RAISING_FRAME]
    assert frame["function"] == "_inner"
    assert "inner_local" in frame["local_names"]


async def test_get_frame_locals(call_tool: ToolCaller, captured_id: str) -> None:
    """The locals tool returns name -> repr for one frame."""
    result = await call_tool("get_frame_locals", id=captured_id, frame=RAISING_FRAME)

    assert result["locals"]["inner_local"] == repr(MARKER.upper())


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_traceback", {"id": "nope"}),
        ("get_frames", {"id": "nope"}),
        ("get_frame_locals", {"id": "nope", "frame": 0}),
    ],
)
async def test_unknown_id_is_reported(
    call_tool: ToolCaller, captured_id: str, tool: str, args: dict[str, Any]
) -> None:
    """An unknown id comes back as an error field, not an exception."""
    result = await call_tool(tool, **args)

    assert "No captured exception" in result["error"]


async def test_get_frame_locals_out_of_range(
    call_tool: ToolCaller, captured_id: str
) -> None:
    """A bad frame index is reported to the agent."""
    result = await call_tool("get_frame_locals", id=captured_id, frame=99)

    assert "out of range" in result["error"]


async def test_get_frame_locals_after_expiry(
    call_tool: ToolCaller, setup_integration: MockConfigEntry, captured_id: str
) -> None:
    """Once frames are gone the agent is told, and the text still works."""
    setup_integration.runtime_data.store.get(captured_id).clear_frames()

    result = await call_tool("get_frame_locals", id=captured_id, frame=0)
    traceback_result = await call_tool("get_traceback", id=captured_id)

    assert "expired" in result["error"]
    assert traceback_result["live"] is False
    assert "ValueError: boom" in traceback_result["traceback"]


@pytest.mark.parametrize("options", [EVAL_ENABLED])
async def test_eval_in_frame(call_tool: ToolCaller, captured_id: str) -> None:
    """The headline feature: run Python against a captured frame."""
    result = await call_tool(
        "eval_in_frame",
        id=captured_id,
        frame=RAISING_FRAME,
        source="inner_local.lower()",
    )

    assert result["ok"] is True
    assert result["result"] == repr(MARKER)


@pytest.mark.parametrize("options", [EVAL_ENABLED])
async def test_eval_in_frame_unknown_id(
    call_tool: ToolCaller, captured_id: str
) -> None:
    """Evaluating against an unknown id is reported."""
    result = await call_tool("eval_in_frame", id="nope", frame=0, source="1")

    assert "No captured exception" in result["error"]


@pytest.mark.parametrize("options", [EVAL_ENABLED])
async def test_eval_in_frame_after_expiry(
    call_tool: ToolCaller, setup_integration: MockConfigEntry, captured_id: str
) -> None:
    """Evaluation needs live frames and says so when they are gone."""
    setup_integration.runtime_data.store.get(captured_id).clear_frames()

    result = await call_tool("eval_in_frame", id=captured_id, frame=0, source="1")

    assert "expired" in result["error"]
