"""Authenticated REST + WebSocket surface for the exception store.

These mirror the LLM tools for clients that speak plain HTTP (curl, an AI
agent with a long-lived access token) or the HA WebSocket API (a frontend
panel). Every view requires auth; the eval endpoint additionally requires an
admin user and the ``enable_eval`` option.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .const import DATA_STORE, DOMAIN
from .store import ExceptionStore, FrameIndexError, LiveFramesExpired

BASE_URL = "/api/exception_debug"


def _store(hass: HomeAssistant) -> ExceptionStore:
    return hass.data[DOMAIN][DATA_STORE]


class ExceptionListView(HomeAssistantView):
    """GET list of captured exceptions."""

    url = BASE_URL + "/exceptions"
    name = "api:exception_debug:list"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            limit = 20
        entries = _store(hass).list(limit=limit)
        return self.json({"exceptions": [e.summary() for e in entries]})


class ExceptionDetailView(HomeAssistantView):
    """GET a single exception with traceback text and frame list."""

    url = BASE_URL + "/exceptions/{entry_id}"
    name = "api:exception_debug:detail"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store = _store(hass)
        entry = store.get(entry_id)
        if entry is None:
            return self.json_message("Unknown exception id", status_code=404)
        return self.json(
            {
                **entry.summary(),
                "traceback": store.full_traceback(entry),
                "frames": entry.frames(),
            }
        )


class FrameLocalsView(HomeAssistantView):
    """GET locals for one frame of an exception."""

    url = BASE_URL + "/exceptions/{entry_id}/frames/{frame_index}/locals"
    name = "api:exception_debug:frame_locals"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, frame_index: str
    ) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = _store(hass).get(entry_id)
        if entry is None:
            return self.json_message("Unknown exception id", status_code=404)
        try:
            data = entry.frame_locals(int(frame_index))
        except ValueError:
            return self.json_message("Bad frame index", status_code=400)
        except (LiveFramesExpired, FrameIndexError) as err:
            return self.json_message(str(err), status_code=409)
        return self.json({"id": entry_id, "frame": int(frame_index), "locals": data})


@callback
def async_register_http(hass: HomeAssistant) -> None:
    """Register REST views and websocket commands."""
    hass.http.register_view(ExceptionListView())
    hass.http.register_view(ExceptionDetailView())
    hass.http.register_view(FrameLocalsView())

    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_frames)
    websocket_api.async_register_command(hass, ws_frame_locals)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "exception_debug/list",
        vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=200)),
    }
)
@callback
def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List captured exceptions."""
    entries = _store(hass).list(limit=msg["limit"])
    connection.send_result(msg["id"], {"exceptions": [e.summary() for e in entries]})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "exception_debug/frames",
        vol.Required("exc_id"): str,
    }
)
@callback
def ws_frames(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List frames for a captured exception."""
    entry = _store(hass).get(msg["exc_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown exception id")
        return
    connection.send_result(msg["id"], {"id": entry.id, "frames": entry.frames()})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "exception_debug/frame_locals",
        vol.Required("exc_id"): str,
        vol.Required("frame"): int,
    }
)
@callback
def ws_frame_locals(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return locals for a frame of a captured exception."""
    entry = _store(hass).get(msg["exc_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown exception id")
        return
    try:
        data = entry.frame_locals(msg["frame"])
    except (LiveFramesExpired, FrameIndexError) as err:
        connection.send_error(msg["id"], "unavailable", str(err))
        return
    connection.send_result(
        msg["id"], {"id": entry.id, "frame": msg["frame"], "locals": data}
    )
