"""Vistas HTTP públicas (sin login de HA) para la TV y los móviles.

Solo exponen el WebSocket del juego, el QR y las dos páginas. El control de la
partida exige el token de anfitrión, que solo se obtiene con un comando WS de HA
autenticado y de administrador (ver ``ha_websocket.py``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import WSMsgType, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .game.hub import MAX_MESSAGE_BYTES
from .game.netutil import is_private_address
from .game.web import QR_URL, SECURITY_HEADERS, WS_URL, qr_svg_for

if TYPE_CHECKING:
    from . import QuizRuntime

_LOGGER = logging.getLogger(__name__)


def _runtime(hass: HomeAssistant) -> QuizRuntime | None:
    from . import get_runtime  # import diferido para evitar ciclos

    return get_runtime(hass)


def _check_access(hass: HomeAssistant, request: web.Request) -> web.Response | QuizRuntime:
    runtime = _runtime(hass)
    if runtime is None:
        return web.Response(status=503, text="Quiz del Salón no está configurado.")
    if runtime.local_only and not is_private_address(request.remote):
        _LOGGER.warning("Acceso rechazado desde %s (solo red local)", request.remote)
        return web.Response(status=403, text="Solo disponible en la red local.")
    return runtime


class _HubConnection:
    """Adaptador aiohttp -> ``Connection`` del hub."""

    def __init__(self, ws: web.WebSocketResponse, remote: str | None) -> None:
        self._ws = ws
        self.remote = remote

    async def send_text(self, text: str) -> None:
        if self._ws.closed:
            raise ConnectionError("WebSocket cerrado")
        await self._ws.send_str(text)

    async def close(self) -> None:
        if not self._ws.closed:
            await self._ws.close()


class QuizWebSocketView(HomeAssistantView):
    """WebSocket del juego para TV, jugadores y anfitrión."""

    url = WS_URL
    name = "api:quiz_salon:ws"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.StreamResponse:
        checked = _check_access(self.hass, request)
        if isinstance(checked, web.Response):
            return checked
        hub = checked.hub
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_MESSAGE_BYTES * 2)
        await ws.prepare(request)
        session = hub.connect(_HubConnection(ws, request.remote))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await hub.receive(session, msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                else:  # binario u otros: no admitidos
                    await hub.receive(session, "{}")
        finally:
            await hub.disconnect(session)
            if not ws.closed:
                await ws.close()
        return ws


class QuizQrView(HomeAssistantView):
    """QR (SVG) de la URL de unión, generado localmente."""

    url = QR_URL
    name = "api:quiz_salon:qr"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        checked = _check_access(self.hass, request)
        if isinstance(checked, web.Response):
            return checked
        status, body = qr_svg_for(request.query.get("url"))
        if status != 200:
            return web.Response(status=status, text=body)
        return web.Response(
            text=body,
            content_type="image/svg+xml",
            headers={"Cache-Control": "max-age=300", "X-Content-Type-Options": "nosniff"},
        )


class QuizPageView(HomeAssistantView):
    """Páginas de la TV (/quiz_salon/tv) y del jugador (/quiz_salon/play)."""

    url = "/quiz_salon/tv"
    extra_urls = ["/quiz_salon/play"]
    name = "quiz_salon:page"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, pages: dict[str, str]) -> None:
        self.hass = hass
        self._pages = pages

    async def get(self, request: web.Request) -> web.Response:
        checked = _check_access(self.hass, request)
        if isinstance(checked, web.Response):
            return checked
        key = "play" if request.path.rstrip("/").endswith("/play") else "tv"
        return web.Response(
            text=self._pages[key], content_type="text/html", charset="utf-8", headers=SECURITY_HEADERS
        )
