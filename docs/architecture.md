# Quiz del Salón — Arquitectura

## Visión general

```
Home Assistant (proceso único, asyncio)
└── custom_components/quiz_salon
    ├── __init__.py          Ciclo de vida: setup/unload, registro de rutas, panel y WS
    ├── config_flow.py       Alta desde la UI (una sola instancia) + opciones
    ├── http_views.py        Vistas aiohttp públicas: WebSocket del juego, QR, páginas
    ├── ha_websocket.py      Comandos WS autenticados de HA (token de anfitrión)
    ├── game/                Núcleo puro Python, sin dependencias de HA (testeable)
    │   ├── engine.py        Máquina de estados, reglas, puntuación (reloj inyectable)
    │   ├── hub.py           Sesiones, roles, validación de mensajes, rate-limit, broadcast
    │   ├── questions.py     Carga y validación de bancos de preguntas
    │   ├── qr.py            Codificador QR propio → SVG (sin dependencias)
    │   ├── netutil.py       Resolución de URL base y comprobación de red local
    │   └── names.py         Normalización de nombres y avatares
    ├── data/questions_es.json
    └── frontend/            HTML/CSS/JS sin paso de compilación
        ├── tv.html          Pantalla pública
        ├── play.html        Mando del jugador
        ├── panel.js         Panel de anfitrión (custom element dentro de HA)
        ├── common.js        Cliente WS con reconexión, utilidades seguras
        └── app.css
```

## Decisiones

| Decisión | Motivo |
|---|---|
| Integración custom (no add-on, no Node) | Funciona en todas las instalaciones de HA (OS, Container, Core) e instala por HACS. Cero dependencias extra (`requirements: []`). |
| Núcleo `game/` sin imports de HA | Pruebas unitarias rápidas y deterministas; el adaptador HA es una capa fina. |
| WebSocket propio (`HomeAssistantView`, `requires_auth = False`) para TV y jugadores | Los móviles y la TV no tienen sesión de HA. El WS nativo de HA exige autenticación. |
| Control de anfitrión vía token obtenido con un comando WS **autenticado y de administrador** de HA | Una URL secreta no sustituye a la autorización. El panel de HA pide el token con la sesión del usuario y lo usa en el WS del juego. El token se puede rotar y nunca se envía a TV ni jugadores. |
| Panel en la barra lateral (`panel_custom.async_register_panel`, `require_admin=True`) | Acceso natural desde HA y la app Companion. |
| Rutas estáticas con `hass.http.async_register_static_paths([StaticPathConfig(...)])` | API vigente (la síncrona `register_static_path` se retiró en 2025.7). |
| QR generado en el servidor con un codificador propio | Sin CDN ni servicios externos; validado en tests con un decodificador independiente (OpenCV). |
| Estado en memoria | Una partida dura minutos; tras reiniciar HA empieza una nueva. Sin datos personales persistidos. |
| Comprobación «solo red local» (activa por defecto) | Si HA está expuesto a Internet, las rutas públicas del juego responden 403 a IPs no privadas. |

## APIs de Home Assistant usadas (verificadas en documentación)

- `homeassistant.components.http.StaticPathConfig` + `hass.http.async_register_static_paths` (desde 2024.7).
- `homeassistant.components.http.HomeAssistantView` + `hass.http.register_view`.
- `homeassistant.components.panel_custom.async_register_panel(...)` y `homeassistant.components.frontend.async_remove_panel`.
- `homeassistant.components.websocket_api`: `websocket_command`, `require_admin`, `async_response`, `async_register_command`.
- `manifest.json`: `single_config_entry` (desde 2024.3), `config_flow`, `integration_type: service`, `iot_class: local_push`, `version`.
- `homeassistant.helpers.network.get_url` (URL interna como último recurso).
- `homeassistant.util.network.is_local`.
- Versión mínima declarada: **2025.1.0**.

## Flujo de datos

```
Panel HA (admin) ──callWS quiz_salon/host_token──▶ HA WS (auth) ──▶ token
Panel HA ──WS /api/quiz_salon/ws {hello, role:host, token}──▶ Hub
TV       ──WS /api/quiz_salon/ws {hello, role:tv}──────────▶ Hub
Móvil    ──WS /api/quiz_salon/ws {hello, role:player, code, name | session}─▶ Hub

Hub ──comando validado──▶ Engine (muta estado) ──▶ Hub construye vista por rol ──▶ broadcast
Ticker asyncio (≤ 250 ms) ──engine.tick(now)──▶ transiciones por tiempo ──▶ broadcast
```

### Protocolo (JSON, ≤ 4 KB por mensaje)

Cliente → servidor:

| type | rol | payload |
|---|---|---|
| `hello` | todos | `role`, `token` (host), `code`+`name`+`avatar` o `session` (player) |
| `answer` | player | `q` (índice de pregunta), `choice` (0–3) |
| `leave` | player | — |
| `configure` | host | `num_questions`, `time_limit`, `categories[]`, `auto_advance` |
| `start` / `pause` / `resume` / `next` / `end` | host | — |
| `new_game` | host | `keep_players` |
| `kick` | host | `player_id` |
| `ping` | todos | — |

Servidor → cliente: `welcome` (rol, id, sesión del jugador), `state` (vista filtrada por rol), `error` (`code`, `message` en español), `ack`, `kicked`, `pong`.

La vista `state` incluye `server_time` y, en fase con tiempo, `deadline` (ms epoch servidor) y `remaining_ms`.

## Seguridad

- Validación estricta de cada mensaje (tipos, rangos, longitudes); mensajes > 4 KB cierran la conexión.
- Rate limit por conexión (cubeta de 20 msg / 5 s) e intentos de `hello` limitados.
- Tokens con `secrets.token_urlsafe(32)`; comparación con `hmac.compare_digest`.
- La respuesta correcta solo aparece en vistas en fase `reveal`/`finished`.
- El frontend usa `textContent`/escape para todo texto de usuario y de preguntas (sin `innerHTML` con datos).
- No se exponen tokens de HA a la TV ni a jugadores.
- Limpieza en `async_unload_entry`: cancelación del ticker, cierre de sockets, eliminación del panel.

## Limitaciones conocidas

- Las rutas HTTP registradas en `hass.http` no se pueden desregistrar en caliente: tras eliminar la integración responden 404 lógico hasta reiniciar HA (comportamiento estándar de HA).
- La TV necesita un navegador moderno (ES2017, WebSocket). Navegadores muy antiguos de Smart TV pueden no funcionar; Chromecast con Google TV, Fire TV (Silk) o un portátil por HDMI sí.
