"""Tests for the REST and WebSocket surfaces."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

from custom_components.exception_debug.const import DOMAIN

from .helpers import MARKER, RAISING_FRAME, log_exception

BASE = "/api/exception_debug"


@pytest.fixture
async def captured(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> dict[str, Any]:
    """Capture a single exception and return it with its store."""
    log_exception()
    store = setup_integration.runtime_data.store
    return {"store": store, "id": store.list()[0].id}


@pytest.fixture
async def unconfigured(hass: HomeAssistant) -> None:
    """Load the component with no config entry, so no store exists."""
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def test_list_requires_auth(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """An unauthenticated caller cannot read captured exceptions."""
    client = await hass_client_no_auth()

    assert (await client.get(f"{BASE}/exceptions")).status == 401


@pytest.mark.parametrize(
    "path",
    [
        "/exceptions",
        "/exceptions/whatever",
        "/exceptions/whatever/frames/0/locals",
    ],
)
async def test_rest_requires_admin(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
    hass_read_only_access_token: str,
    path: str,
) -> None:
    """A non-admin user is refused on every REST endpoint.

    Frame locals routinely contain tokens and passwords, so the REST surface
    must not be a weaker boundary than the admin-gated WebSocket commands.
    """
    client = await hass_client(hass_read_only_access_token)

    assert (await client.get(f"{BASE}{path}")).status == 401


async def test_list(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """The list endpoint returns summaries, newest first."""
    client = await hass_client()

    response = await client.get(f"{BASE}/exceptions")

    assert response.status == 200
    body = await response.json()
    assert [e["id"] for e in body["exceptions"]] == [captured["id"]]
    assert body["exceptions"][0]["exc_type"] == "ValueError"
    assert body["exceptions"][0]["live"] is True


async def test_list_with_unparsable_limit(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """A non-numeric limit falls back to the default instead of erroring."""
    client = await hass_client()

    response = await client.get(f"{BASE}/exceptions?limit=lots")

    assert response.status == 200
    assert len((await response.json())["exceptions"]) == 1


async def test_detail(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """The detail endpoint includes the traceback text and the frame list."""
    client = await hass_client()

    response = await client.get(f"{BASE}/exceptions/{captured['id']}")

    assert response.status == 200
    body = await response.json()
    assert "ValueError: boom" in body["traceback"]
    assert body["frames"][RAISING_FRAME]["function"] == "_inner"


async def test_detail_unknown_id(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """An unknown id is a 404."""
    client = await hass_client()

    assert (await client.get(f"{BASE}/exceptions/nope")).status == 404


async def test_frame_locals(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """Frame locals come back as name -> repr."""
    client = await hass_client()

    response = await client.get(
        f"{BASE}/exceptions/{captured['id']}/frames/{RAISING_FRAME}/locals"
    )

    assert response.status == 200
    assert (await response.json())["locals"]["inner_local"] == repr(MARKER.upper())


async def test_frame_locals_unknown_id(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """An unknown id is a 404 here too."""
    client = await hass_client()

    assert (await client.get(f"{BASE}/exceptions/nope/frames/0/locals")).status == 404


async def test_frame_locals_bad_index(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """A non-numeric frame index is a 400."""
    client = await hass_client()

    response = await client.get(
        f"{BASE}/exceptions/{captured['id']}/frames/first/locals"
    )

    assert response.status == 400


async def test_frame_locals_out_of_range(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """An out-of-range frame index is a 409, not a crash."""
    client = await hass_client()

    response = await client.get(f"{BASE}/exceptions/{captured['id']}/frames/99/locals")

    assert response.status == 409


async def test_frame_locals_after_expiry(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_client: ClientSessionGenerator,
) -> None:
    """Once frames are released the endpoint says so rather than 500ing."""
    captured["store"].list()[0].clear_frames()
    client = await hass_client()

    response = await client.get(
        f"{BASE}/exceptions/{captured['id']}/frames/{RAISING_FRAME}/locals"
    )

    assert response.status == 409
    assert "expired" in (await response.json())["message"]


@pytest.mark.parametrize(
    "path",
    [
        "/exceptions",
        "/exceptions/whatever",
        "/exceptions/whatever/frames/0/locals",
    ],
)
async def test_endpoints_when_not_configured(
    hass: HomeAssistant,
    unconfigured: None,
    hass_client: ClientSessionGenerator,
    path: str,
) -> None:
    """With no config entry every endpoint reports 503 rather than raising."""
    client = await hass_client()

    assert (await client.get(f"{BASE}{path}")).status == 503


async def test_ws_list(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The WebSocket list command mirrors the REST listing."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "exception_debug/list"})
    result = await client.receive_json()

    assert result["success"]
    assert [e["id"] for e in result["result"]["exceptions"]] == [captured["id"]]


async def test_ws_requires_admin(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """A non-admin user cannot read captured exceptions."""
    client = await hass_ws_client(hass, hass_read_only_access_token)

    await client.send_json_auto_id({"type": "exception_debug/list"})
    result = await client.receive_json()

    assert not result["success"]
    assert result["error"]["code"] == "unauthorized"


async def test_ws_frames(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The frames command returns the frame summaries."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": "exception_debug/frames", "exc_id": captured["id"]}
    )
    result = await client.receive_json()

    assert result["success"]
    assert result["result"]["frames"][RAISING_FRAME]["function"] == "_inner"


async def test_ws_frame_locals(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """The frame_locals command returns name -> repr."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "exception_debug/frame_locals",
            "exc_id": captured["id"],
            "frame": RAISING_FRAME,
        }
    )
    result = await client.receive_json()

    assert result["success"]
    assert result["result"]["locals"]["inner_local"] == repr(MARKER.upper())


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ({"type": "exception_debug/frames", "exc_id": "nope"}, "not_found"),
        (
            {
                "type": "exception_debug/frame_locals",
                "exc_id": "nope",
                "frame": 0,
            },
            "not_found",
        ),
    ],
)
async def test_ws_unknown_id(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
    message: dict[str, Any],
    code: str,
) -> None:
    """An unknown exception id is an error, not a crash."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(message)
    result = await client.receive_json()

    assert not result["success"]
    assert result["error"]["code"] == code


async def test_ws_frame_locals_after_expiry(
    hass: HomeAssistant,
    captured: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Released frames are reported as unavailable."""
    captured["store"].list()[0].clear_frames()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "exception_debug/frame_locals",
            "exc_id": captured["id"],
            "frame": 0,
        }
    )
    result = await client.receive_json()

    assert not result["success"]
    assert result["error"]["code"] == "unavailable"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "exception_debug/list"},
        {"type": "exception_debug/frames", "exc_id": "x"},
        {"type": "exception_debug/frame_locals", "exc_id": "x", "frame": 0},
    ],
)
async def test_ws_when_not_configured(
    hass: HomeAssistant,
    unconfigured: None,
    hass_ws_client: WebSocketGenerator,
    message: dict[str, Any],
) -> None:
    """With no config entry every command reports not_loaded."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(message)
    result = await client.receive_json()

    assert not result["success"]
    assert result["error"]["code"] == "not_loaded"
