"""Comandos del WebSocket autenticado de Home Assistant (solo administradores)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import VERSION
from .game.netutil import PLAY_PATH, TV_PATH
from .game.web import QR_URL, WS_URL


def _payload(hass: HomeAssistant, token: str) -> dict[str, Any]:
    from . import get_runtime

    runtime = get_runtime(hass)
    return {
        "token": token,
        "ws_path": WS_URL,
        "tv_path": TV_PATH,
        "play_path": PLAY_PATH,
        "qr_path": QR_URL,
        "join_base": runtime.hub.join_base if runtime else None,
        "local_only": runtime.local_only if runtime else True,
        "version": VERSION,
    }


@websocket_api.websocket_command({vol.Required("type"): "quiz_salon/host_token"})
@websocket_api.require_admin
@callback
def ws_host_token(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Entrega el token de anfitrión al panel de HA."""
    from . import get_runtime

    runtime = get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Quiz del Salón no está configurado")
        return
    connection.send_result(msg["id"], _payload(hass, runtime.hub.host_token))


@websocket_api.websocket_command({vol.Required("type"): "quiz_salon/rotate_token"})
@websocket_api.require_admin
@callback
def ws_rotate_token(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Genera un token nuevo (las conexiones de anfitrión existentes siguen abiertas)."""
    from . import get_runtime

    runtime = get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Quiz del Salón no está configurado")
        return
    connection.send_result(msg["id"], _payload(hass, runtime.hub.rotate_host_token()))


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_host_token)
    websocket_api.async_register_command(hass, ws_rotate_token)
