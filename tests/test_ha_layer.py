"""Prueba de humo de la capa de Home Assistant con dobles de prueba.

Home Assistant no está instalado en el entorno de CI de este repositorio, así que se
sustituyen sus módulos por dobles mínimos que imitan las firmas documentadas. Esto
detecta errores de importación, nombres y cableado (registro de rutas, panel, comandos
WS, opciones), pero NO sustituye a probar la integración en un Home Assistant real.
Si Home Assistant está instalado, esta prueba se omite.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

try:  # pragma: no cover
    import homeassistant  # noqa: F401

    HAS_HA = True
except ImportError:
    HAS_HA = False

pytestmark = pytest.mark.skipif(HAS_HA, reason="Home Assistant real instalado: usar pytest-homeassistant-custom-component")


def _module(name: str, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class Recorder:
    def __init__(self):
        self.calls = []


REC = Recorder()


def install_stubs():
    # ---- voluptuous
    class Schema:
        def __init__(self, schema):
            self.schema = schema

    class _Key(str):
        def __new__(cls, key, default=None, description=None):
            obj = str.__new__(cls, key)
            obj.default = default
            obj.description = description
            return obj

    _module("voluptuous", Schema=Schema, Required=_Key, Optional=_Key)

    # ---- aiohttp
    class WSMsgType:
        TEXT, BINARY, CLOSE, ERROR = 1, 2, 8, 258

    class StreamResponse:
        pass

    class Response(StreamResponse):
        def __init__(self, status=200, text="", content_type=None, charset=None, headers=None):
            self.status, self.text, self.content_type, self.headers = status, text, content_type, headers or {}

    class WebSocketResponse(StreamResponse):
        def __init__(self, heartbeat=None, max_msg_size=None):
            self.closed = False
            self.sent = []
            self.incoming = []

        async def prepare(self, request):
            self.incoming = list(request.ws_messages)

        def __aiter__(self):
            async def gen():
                for m in self.incoming:
                    yield m
            return gen()

        async def send_str(self, data):
            self.sent.append(json.loads(data))

        async def close(self):
            self.closed = True

    web = _module("aiohttp.web", Response=Response, StreamResponse=StreamResponse, WebSocketResponse=WebSocketResponse, Request=object)
    _module("aiohttp", WSMsgType=WSMsgType, web=web)

    # ---- homeassistant
    _module("homeassistant")
    _module("homeassistant.components")

    @dataclass
    class StaticPathConfig:
        url_path: str
        path: str
        cache_headers: bool = True

    class HomeAssistantView:
        url = None
        extra_urls: list = []
        name = None
        requires_auth = True

    _module("homeassistant.components.http", StaticPathConfig=StaticPathConfig, HomeAssistantView=HomeAssistantView)

    async def async_register_panel(hass, **kw):
        REC.calls.append(("register_panel", kw))

    _module("homeassistant.components.panel_custom", async_register_panel=async_register_panel)
    _module("homeassistant.components.frontend", async_remove_panel=lambda hass, path: REC.calls.append(("remove_panel", path)))

    def websocket_command(schema):
        def deco(fn):
            fn.ws_schema = schema
            return fn
        return deco

    def require_admin(fn):
        def wrapper(hass, connection, msg):
            if not connection.user.is_admin:
                connection.send_error(msg["id"], "unauthorized", "Unauthorized")
                return
            return fn(hass, connection, msg)
        wrapper.ws_schema = getattr(fn, "ws_schema", None)
        return wrapper

    _module(
        "homeassistant.components.websocket_api",
        websocket_command=websocket_command,
        require_admin=require_admin,
        async_register_command=lambda hass, fn: REC.calls.append(("ws_command", fn)),
        ActiveConnection=object,
    )
    import homeassistant.components as comps  # type: ignore

    for sub in ("http", "panel_custom", "frontend", "websocket_api"):
        setattr(comps, sub, sys.modules[f"homeassistant.components.{sub}"])

    class ConfigEntry:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, options):
            self.options = options
            self.runtime_data = None
            self._unload = []

        def async_on_unload(self, fn):
            self._unload.append(fn)

        def add_update_listener(self, fn):
            self.listener = fn
            return lambda: None

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kw):
            cls.domain = domain

        def async_create_entry(self, **kw):
            return {"type": "create_entry", **kw}

        def async_show_form(self, **kw):
            return {"type": "form", **kw}

    class OptionsFlow(ConfigFlow):
        config_entry = None

    _module("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigFlow=ConfigFlow, ConfigFlowResult=dict, OptionsFlow=OptionsFlow)
    _module("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    _module("homeassistant.helpers")
    _module("homeassistant.helpers.config_validation", config_entry_only_config_schema=lambda d: None)
    _module("homeassistant.helpers.typing", ConfigType=dict)


class FakeHttp:
    def __init__(self):
        self.views = []
        self.static = []

    async def async_register_static_paths(self, configs):
        self.static.extend(configs)

    def register_view(self, view):
        self.views.append(view)


class FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, name, data):
        self.events.append((name, data))


class FakeConfig:
    def path(self, *parts):
        return str(Path("/nonexistent-config", *parts))


class FakeHass:
    def __init__(self):
        self.data = {}
        self.http = FakeHttp()
        self.bus = FakeBus()
        self.config = FakeConfig()

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class FakeRequest:
    def __init__(self, remote="192.168.1.30", path="/quiz_salon/tv", query=None, ws_messages=()):
        self.remote = remote
        self.path = path
        self.query = query or {}
        self.ws_messages = ws_messages


class Msg:
    def __init__(self, data, type_=1):
        self.type = type_
        self.data = data


class FakeUser:
    def __init__(self, admin):
        self.is_admin = admin


class FakeWSConn:
    def __init__(self, admin=True):
        self.user = FakeUser(admin)
        self.results = []
        self.errors = []

    def send_result(self, mid, result):
        self.results.append((mid, result))

    def send_error(self, mid, code, message):
        self.errors.append((mid, code))


@pytest.fixture(scope="module")
def integration():
    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] in ("homeassistant", "aiohttp", "voluptuous", "custom_components")}
    install_stubs()
    sys.path.insert(0, str(ROOT))
    pkg = importlib.import_module("custom_components.quiz_salon")
    yield pkg
    sys.path.remove(str(ROOT))
    for k in list(sys.modules):
        if k.split(".")[0] in ("homeassistant", "aiohttp", "voluptuous", "custom_components"):
            del sys.modules[k]
    sys.modules.update(saved)


def test_setup_views_panel_and_ws(integration):
    from custom_components.quiz_salon import ha_websocket
    from custom_components.quiz_salon.config_flow import QuizSalonConfigFlow, QuizSalonOptionsFlow
    from custom_components.quiz_salon.const import CONF_BASE_URL, CONF_LOCAL_ONLY

    async def scenario():
        hass = FakeHass()
        assert await integration.async_setup(hass, {})
        assert await integration.async_setup(hass, {})  # segunda vez: no duplica rutas
        assert len(hass.http.views) == 3
        assert hass.http.static[0].url_path == "/quiz_salon/static"
        assert all(v.requires_auth is False for v in hass.http.views)

        entry = sys.modules["homeassistant.config_entries"].ConfigEntry({CONF_LOCAL_ONLY: True, CONF_BASE_URL: ""})
        assert await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        assert runtime.hub.game.bank.questions
        panel = [c for c in REC.calls if c[0] == "register_panel"][-1][1]
        assert panel["require_admin"] is True and panel["webcomponent_name"] == "quiz-salon-panel"
        assert panel["module_url"].startswith("/quiz_salon/static/panel.js")

        ws_view, qr_view, page_view = hass.http.views
        # Página de TV desde la red local
        resp = await page_view.get(FakeRequest())
        assert resp.status == 200 and "tv.js" in resp.text and "__VERSION__" not in resp.text
        assert "Content-Security-Policy" in resp.headers
        resp = await page_view.get(FakeRequest(path="/quiz_salon/play"))
        assert "play.js" in resp.text
        # IP pública rechazada con «solo red local»
        resp = await page_view.get(FakeRequest(remote="8.8.8.8"))
        assert resp.status == 403
        # QR
        resp = await qr_view.get(FakeRequest(query={"url": "http://192.168.1.5:8123/quiz_salon/play?code=ABCD"}))
        assert resp.status == 200 and resp.content_type == "image/svg+xml"
        resp = await qr_view.get(FakeRequest(query={"url": "https://evil.example/phish"}))
        assert resp.status == 400

        # Comandos WS de HA: solo administradores obtienen el token
        admin = FakeWSConn(admin=True)
        ha_websocket.ws_host_token(hass, admin, {"id": 1, "type": "quiz_salon/host_token"})
        token = admin.results[0][1]["token"]
        assert token == runtime.hub.host_token
        user = FakeWSConn(admin=False)
        ha_websocket.ws_host_token(hass, user, {"id": 2, "type": "quiz_salon/host_token"})
        assert user.errors and not user.results

        # WebSocket del juego: un anfitrión con el token y una TV
        host_msgs = [Msg(json.dumps({"type": "hello", "role": "host", "token": token})), Msg(json.dumps({"type": "ping"}))]
        ws = await ws_view.get(FakeRequest(ws_messages=host_msgs))
        types_ = [m["type"] for m in ws.sent]
        assert types_[:2] == ["welcome", "state"] and "pong" in types_
        assert ws.closed
        ws = await ws_view.get(FakeRequest(remote="8.8.8.8"))
        assert getattr(ws, "status", None) == 403

        # Cambio de opciones sin reiniciar la partida
        entry.options = {CONF_LOCAL_ONLY: False, CONF_BASE_URL: "http://192.168.1.50:8123/"}
        await entry.listener(hass, entry)
        assert runtime.local_only is False and runtime.hub.join_base == "http://192.168.1.50:8123"
        resp = await page_view.get(FakeRequest(remote="8.8.8.8"))
        assert resp.status == 200

        # Evento al bus de HA
        runtime.hub.game.add_player("Ana")
        await runtime.hub.broadcast()
        assert hass.bus.events and hass.bus.events[-1][0] == "quiz_salon_event"
        assert hass.bus.events[-1][1]["type"] == "player_joined"

        # Descarga limpia
        assert await integration.async_unload_entry(hass, entry)
        assert ("remove_panel", "quiz-salon") in REC.calls
        resp = await page_view.get(FakeRequest())
        assert resp.status == 503

        # Flujo de configuración y opciones
        flow = QuizSalonConfigFlow()
        assert (await flow.async_step_user())["type"] == "form"
        created = await flow.async_step_user({})
        assert created["type"] == "create_entry" and created["options"][CONF_LOCAL_ONLY] is True
        opt = QuizSalonOptionsFlow()
        opt.config_entry = entry
        bad = await opt.async_step_init({CONF_LOCAL_ONLY: True, CONF_BASE_URL: "ftp://x"})
        assert bad["errors"] == {CONF_BASE_URL: "invalid_url"}
        loop = await opt.async_step_init({CONF_LOCAL_ONLY: True, CONF_BASE_URL: "http://localhost:8123"})
        assert loop["errors"] == {CONF_BASE_URL: "loopback_url"}
        ok = await opt.async_step_init({CONF_LOCAL_ONLY: True, CONF_BASE_URL: "http://192.168.1.9:8123/x"})
        assert ok["data"][CONF_BASE_URL] == "http://192.168.1.9:8123"

    asyncio.run(scenario())
