# Changelog

## 1.0.0 — 2026-11-18

Primera versión.

- Integración personalizada `quiz_salon` con flujo de configuración (una instancia) y opciones: «Solo red local» y URL base.
- Pantalla de TV (`/quiz_salon/tv`): sala con QR, cuenta atrás, pregunta con temporizador, revelación con reparto y marcador, podio, pausa y aviso de reconexión. Sonidos sintetizados opcionales.
- Mando del jugador (`/quiz_salon/play`): unión por QR o código, avatar, respuesta bloqueada, resultado, puesto y reconexión automática.
- Panel de anfitrión en la barra lateral (solo administradores): configuración, inicio, pausa, avance, fin, nueva partida y expulsión.
- Motor autoritativo en servidor con reloj inyectable, puntuación por rapidez, rachas, pregunta final doble y desempates.
- 163 preguntas originales en español en 8 categorías; carga de paquetes propios desde `/config/quiz_salon/`.
- Generador de QR propio (sin dependencias ni servicios externos).
- Evento `quiz_salon_event` para automatizaciones.
- Seguridad: token de anfitrión vía WebSocket autenticado de HA, validación de mensajes, límites de tamaño y frecuencia, bloqueo de fuerza bruta del código de sala, CSP en las páginas.
