# Plan de pruebas — Quiz del Salón 1.0.0

## Entorno de las pruebas ejecutadas

| Elemento | Valor |
|---|---|
| Python | 3.13 |
| Home Assistant | **No disponible** en el entorno de desarrollo (ver «Pendiente de verificar») |
| Servidor | `dev/devserver.py`: mismo núcleo `game/` y mismo frontend que la integración, con WebSocket estándar |
| Navegadores | Google Chrome (headless) vía Playwright: TV 1920×1080, móviles 390×844 y 360×740, panel 1280×900 |
| Red | Local (127.0.0.1) |

## Pruebas automatizadas (`python3 -m pytest`) — 64 superadas

| Archivo | Cubre |
|---|---|
| `tests/test_engine.py` (24) | Puntuación, nombres (vacío, largo, duplicado con mayúsculas/acentos, caracteres especiales), sala llena, avatares únicos, inicio con < 2 jugadores, configuración y límites, reparto por categorías, barajado de opciones, ronda completa, reglas de respuesta (fuera de fase, doble, pregunta antigua, índice inválido), cierre por tiempo, desconectados que no bloquean, pausa que congela el tiempo, avance manual y automático, fin, nueva partida, expulsión, empates, que la respuesta correcta no se filtra antes de la revelación, bloqueo largo del reloj |
| `tests/test_hub.py` (12) | Roles y autorización de anfitrión, rotación de token, unión, código incorrecto, reconexión con token de sesión, segunda pestaña, sesión caducada, expulsión, JSON inválido, mensajes grandes, rate limit, fuerza bruta del código por IP, conexión rota, eventos, temporizador real sin clientes |
| `tests/test_support.py` (27) | QR decodificado con un lector independiente (OpenCV), detección de red local/IP pública, normalización y validación de URLs, banco de preguntas incluido, rechazo de entradas inválidas, paquetes propios |
| `tests/test_ha_layer.py` (1) | Capa de Home Assistant con dobles de prueba: registro único de rutas, panel solo admin, página TV/jugador con CSP, 403 desde IP pública, QR, token solo para administradores, WebSocket de juego, opciones en caliente, evento al bus, descarga limpia, flujo de configuración y validación de opciones |

Comprobaciones estáticas: `mypy` sin errores en `game/`; `node --check` en los 4 módulos JS; `py_compile` en todos los `.py`; JSON de manifiesto y traducciones válidos.

## Pruebas de extremo a extremo con navegadores reales

| ID | Escenario | Resultado esperado | Resultado real | Estado |
|---|---|---|---|---|
| T01 | Crear partida | TV muestra QR cargado, código de 4 letras y huecos | Correcto | ✅ |
| T02 | Unir 4 móviles (uno por URL del QR, tres tecleando el código en minúsculas) | Todos entran, la TV muestra 4 jugadores | Correcto | ✅ |
| T03 | Nombre duplicado («ANA» con «Ana» dentro) | Error «Ese nombre ya está en uso» | Correcto | ✅ |
| T04 | Responder y repetir toque | Botones bloqueados tras la primera respuesta | Correcto | ✅ |
| T05 | Todos responden | Revelación inmediata | Correcto | ✅ |
| T06 | Un jugador no responde | La pregunta se cierra al agotarse el tiempo | Correcto | ✅ |
| T07 | Recargar un móvil a mitad de pregunta | Recupera sesión, puntos y puede responder | Correcto | ✅ |
| T08 | Pausar y reanudar desde el panel | TV muestra «Pausa» y continúa | Correcto | ✅ |
| T09 | 5 preguntas completas con avance automático | Podio en TV, puesto en cada móvil | Correcto | ✅ |
| T10 | 10 móviles a la vez + un 11.º | Los 10 responden simultáneamente y el reparto suma 10; el 11.º ve «La sala está llena» | Correcto | ✅ |
| T11 | Errores de consola en TV, móviles y panel | Ninguno | Ninguno | ✅ |
| T12 | TV 16:9 y móvil estrecho | Sin desbordes ni scroll en TV | Revisado en capturas | ✅ |

Scripts: `tests/e2e/run_e2e.py` (T01–T09, T11) y `tests/e2e/run_full_room.py` (T10).

## Pendiente de verificar en un Home Assistant real

Estas comprobaciones no se han podido ejecutar porque Home Assistant no está instalado en el entorno de desarrollo:

| ID | Escenario | Cómo comprobarlo |
|---|---|---|
| H01 | La integración carga sin errores en HA ≥ 2025.1 | Instalar, reiniciar, añadir la integración y revisar **Ajustes → Sistema → Registros** |
| H02 | El panel aparece solo para administradores | Entrar con un usuario no administrador: no debe verse |
| H03 | El panel obtiene el token y muestra «Conectado» | Abrir el panel en navegador y en la app Companion |
| H04 | `/quiz_salon/tv` y `/quiz_salon/play` cargan sin sesión de HA | Abrir en la TV y en un móvil sin iniciar sesión |
| H05 | WebSocket del juego a través de HA (aiohttp) | Jugar una partida completa con 2+ móviles |
| H06 | «Solo red local» con proxy inverso | Si usas proxy, configurar `use_x_forwarded_for` y `trusted_proxies` en `http:` |
| H07 | Recargar la integración y cambiar opciones | **⋮ → Recargar**; cambiar la URL base y comprobar el QR |
| H08 | Reinicio de HA durante una partida | La partida se pierde; la TV y los móviles se reconectan a una sala nueva |
| H09 | Navegador real de la TV | Abrir la URL en tu televisor concreto y comprobar legibilidad y rendimiento |
| H10 | Validación de GitHub Actions (hassfest y HACS) | Publicar en GitHub y revisar la pestaña Actions |
