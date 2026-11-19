"""Quiz del Salón: juego de preguntas multijugador local para Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BASE_URL,
    CONF_LOCAL_ONLY,
    CUSTOM_QUESTIONS_DIR,
    DATA_GLOBAL,
    DEFAULT_LOCAL_ONLY,
    DOMAIN,
    EVENT_NAME,
    NAME,
    PANEL_ELEMENT,
    PANEL_ICON,
    PANEL_URL_PATH,
    VERSION,
)
from .game.hub import Hub
from .game.netutil import normalize_base_url
from .game.questions import load_all
from .game.web import FRONTEND_DIR, STATIC_URL, load_pages
from .ha_websocket import async_register_commands
from .http_views import QuizPageView, QuizQrView, QuizWebSocketView

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
BUILTIN_QUESTIONS = Path(__file__).parent / "data" / "questions_es.json"


@dataclass
class QuizRuntime:
    """Estado en ejecución de la integración."""

    hub: Hub
    local_only: bool


type QuizConfigEntry = ConfigEntry[QuizRuntime]


def get_runtime(hass: HomeAssistant) -> QuizRuntime | None:
    """Devuelve el runtime activo (o None si la integración no está cargada)."""
    return hass.data.get(DATA_GLOBAL, {}).get("runtime")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Registra rutas HTTP y comandos WS una sola vez por arranque de HA."""
    glob: dict[str, Any] = hass.data.setdefault(DATA_GLOBAL, {"runtime": None})
    if not glob.get("http_registered"):
        pages = await hass.async_add_executor_job(load_pages, VERSION)
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), False)]
        )
        hass.http.register_view(QuizWebSocketView(hass))
        hass.http.register_view(QuizQrView(hass))
        hass.http.register_view(QuizPageView(hass, pages))
        async_register_commands(hass)
        glob["http_registered"] = True
    return True


def _apply_options(runtime: QuizRuntime, entry: ConfigEntry) -> None:
    runtime.local_only = bool(entry.options.get(CONF_LOCAL_ONLY, DEFAULT_LOCAL_ONLY))
    runtime.hub.join_base = normalize_base_url(entry.options.get(CONF_BASE_URL)) or None


async def async_setup_entry(hass: HomeAssistant, entry: QuizConfigEntry) -> bool:
    """Crea el hub de juego y el panel de anfitrión."""
    custom_dir = Path(hass.config.path(CUSTOM_QUESTIONS_DIR))
    bank = await hass.async_add_executor_job(load_all, BUILTIN_QUESTIONS, custom_dir)
    for warning in bank.warnings:
        _LOGGER.warning("Preguntas: %s", warning)
    _LOGGER.info("Quiz del Salón: %d preguntas cargadas", len(bank.questions))

    @callback
    def _on_event(name: str, data: dict[str, Any]) -> None:
        hass.bus.async_fire(EVENT_NAME, {"type": name, **data, **hub.game.summary()})

    hub = Hub(bank, on_event=_on_event)
    runtime = QuizRuntime(hub=hub, local_only=DEFAULT_LOCAL_ONLY)
    _apply_options(runtime, entry)
    entry.runtime_data = runtime
    hass.data[DATA_GLOBAL]["runtime"] = runtime
    await hub.start()

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=NAME,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/panel.js?v={VERSION}",
        embed_iframe=False,
        require_admin=True,
        config={"version": VERSION},
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: QuizConfigEntry) -> None:
    """Aplica las opciones sin reiniciar la partida en curso."""
    _apply_options(entry.runtime_data, entry)
    await entry.runtime_data.hub.broadcast(force=True)


async def async_unload_entry(hass: HomeAssistant, entry: QuizConfigEntry) -> bool:
    """Detiene el temporizador, cierra conexiones y quita el panel."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    await entry.runtime_data.hub.stop()
    hass.data[DATA_GLOBAL]["runtime"] = None
    return True
