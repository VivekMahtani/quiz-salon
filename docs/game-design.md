# Quiz del Salón — Diseño del juego

## 1. Resumen

| Campo | Valor |
|---|---|
| Nombre | **Quiz del Salón** (dominio HA: `quiz_salon`) |
| Género | Quiz de cultura general multijugador, respuesta simultánea |
| Promesa | «Todos contra todos desde el sofá: la tele pregunta, el móvil responde, gana el más rápido que acierta.» |
| Jugadores | 2 a 10 (individual, sin equipos) |
| Duración | 8–15 min (12 preguntas por defecto) |
| Idioma | Español |
| Tono | Familiar, apto para todas las edades |

El nombre evita deliberadamente marcas comerciales registradas de juegos de preguntas. Todas las preguntas son originales.

## 2. Dispositivos

- **TV (pantalla pública):** navegador de la TV, Chromecast/Fire TV con navegador, o un portátil conectado por HDMI. Abre `http://<ip-ha>:8123/quiz_salon/tv`. Solo lectura: no hay controles.
- **Móviles de jugadores:** navegador, escaneando el QR. Sin cuenta, sin app.
- **Anfitrión:** panel «Quiz del Salón» en la barra lateral de Home Assistant (navegador o app Companion). Requiere sesión de HA con usuario administrador.
- **Audio:** efectos sintetizados en la TV (opcional, botón «Activar sonido»).
- **Internet:** no necesario.

## 3. Preparación

El anfitrión, en el panel de HA, elige:

- Número de preguntas: 5–30 (por defecto 12).
- Tiempo por pregunta: 10–60 s (por defecto 20 s).
- Categorías (todas por defecto).
- Avance automático tras la revelación (activado por defecto, 8 s) o manual.

Al crear la partida se genera un **código de sala** de 4 letras (sin caracteres ambiguos). La TV muestra el QR con la URL `…/quiz_salon/play?code=ABCD`.

Cada jugador escribe un nombre (1–16 caracteres, único sin distinguir mayúsculas) y elige un avatar de una lista fija.

## 4. Bucle principal

1. **Cuenta atrás (3 s):** la TV anuncia número de pregunta y categoría.
2. **Pregunta:** TV y móviles muestran la pregunta y 4 opciones (A, B, C, D). Empieza el temporizador del servidor.
3. Cada jugador pulsa una opción. La respuesta queda **bloqueada** (no se puede cambiar). La TV muestra quién ha respondido, nunca qué ha respondido.
4. La pregunta termina cuando **todos los jugadores conectados han respondido** o **se agota el tiempo**.
5. **Revelación:** la TV marca la respuesta correcta (con icono ✓ y texto, no solo color), el reparto de respuestas por opción y los puntos ganados. Cada móvil muestra «¡Correcto! +N» o «Era la C».
6. Marcador actualizado. Tras 8 s (o al pulsar «Siguiente» el anfitrión) se pasa a la siguiente pregunta.
7. Tras la última pregunta: **podio** y clasificación final.

## 5. Reglas

### Acciones válidas
| Rol | Acción | Cuándo |
|---|---|---|
| Jugador | Unirse | Lobby o partida en curso (entra con 0 puntos), si hay hueco (máx. 10) |
| Jugador | Responder | Solo en fase `question`, una vez por pregunta |
| Jugador | Salir | Siempre |
| Anfitrión | Configurar | Solo en `lobby` |
| Anfitrión | Empezar | `lobby` con ≥ 2 jugadores |
| Anfitrión | Pausar / reanudar | `countdown`, `question`, `reveal` |
| Anfitrión | Siguiente | `reveal` (adelanta el avance) o `question` (cierra la pregunta ya) |
| Anfitrión | Expulsar jugador | Siempre |
| Anfitrión | Terminar partida | Siempre (pasa a `finished`) |
| Anfitrión | Nueva partida | Siempre (vuelve a `lobby`, nuevo código, conserva o no a los jugadores) |

### Acciones inválidas (rechazadas por el servidor con error)
Responder fuera de fase, responder dos veces, responder a una pregunta que no es la actual, índice de opción fuera de rango, comandos de anfitrión sin token de anfitrión, nombres vacíos/duplicados/largos, sala llena, código incorrecto.

### Simultaneidad
El servidor procesa los mensajes en orden de llegada dentro de un único bucle de eventos; la marca de tiempo de la respuesta es la del servidor al recibirla. No hay condiciones de carrera: cada mutación es atómica.

### Temporizador
Lo lleva el servidor. Los clientes reciben `deadline` y la hora del servidor para compensar el desfase de reloj. La pausa congela el tiempo restante y lo restaura al reanudar.

### Puntuación
- Fallo o sin respuesta: 0 puntos y la racha vuelve a 0.
- Acierto: `500 + 500 × (tiempo_restante / tiempo_total)` redondeado → entre 500 y 1000.
- Bonus de racha: `+100 × min(racha − 1, 3)` (2.º acierto seguido +100, 3.º +200, 4.º y siguientes +300).
- **Pregunta final doble:** la última pregunta multiplica ×2 (base + velocidad + racha).

### Victoria y empates
Gana quien tenga más puntos. Desempate: más aciertos; si persiste, menor tiempo total de respuesta en aciertos. Si todo coincide, comparten puesto.

### Abandono y reconexión
- Cada jugador recibe un **token de sesión** guardado en su navegador. Al recargar o perder la conexión, el móvil vuelve a entrar automáticamente con su nombre y puntos.
- Un jugador desconectado no bloquea la pregunta: «todos han respondido» solo cuenta jugadores conectados.
- Si se reconecta durante una pregunta a la que no ha respondido, puede responder.
- Si el anfitrión se desconecta, la partida sigue (avance automático). Al reabrir el panel recupera el control.
- Si la TV se desconecta, se reconecta sola y recibe el estado actual.

### Casos límite
- Todos los jugadores desconectados durante una pregunta: el temporizador sigue y la pregunta se cierra al agotarse.
- Menos de 2 jugadores conectados en mitad de partida: la partida continúa (el anfitrión decide si termina).
- Sin preguntas suficientes en las categorías elegidas: se usa el máximo disponible y se avisa.
- Reinicio de Home Assistant: la partida en curso se pierde (estado en memoria). Las preguntas personalizadas y la configuración persisten.

## 6. Información por vista

| Información | TV | Anfitrión | Jugador |
|---|---|---|---|
| Pregunta y opciones | Sí (en `question`) | Sí | Sí |
| Respuesta correcta | Solo en `reveal` | Solo en `reveal` | Solo en `reveal` |
| Quién ha respondido | Sí (nombres) | Sí | No |
| Qué ha respondido cada uno | Reparto agregado en `reveal` | En `reveal` | Solo la suya |
| Puntuación | Sí | Sí | La suya y su puesto |
| Controles | No | Sí | Solo responder |
| Tokens | Nunca | Nunca de jugadores | Solo el suyo |

## 7. Estados

```
lobby ──start──▶ countdown ──3 s──▶ question ──todos/tiempo/next──▶ reveal
  ▲                  ▲                                               │
  │                  └──────────────── auto 8 s / next ──────────────┤
  │                                                                  ▼ (última)
  └──────────── new_game ◀──────────────────────────────────────  finished
```

`paused` es una marca ortogonal aplicable a `countdown`, `question` y `reveal`. `end` lleva a `finished` desde cualquier estado.

## 8. Contenido

- Banco incluido: preguntas originales en español, 8 categorías, con dificultad (1–3).
- Formato JSON (`data/questions_es.json`), separado del motor.
- Paquetes propios: cualquier `*.json` con el mismo formato en `/config/quiz_salon/` se carga al iniciar.
- Validación al cargar: campos obligatorios, 4 opciones distintas, índice correcto válido, longitudes máximas, ids únicos. Las preguntas inválidas se descartan con aviso en el registro.
- Se evitan datos que cambian con el tiempo (récords vigentes, cargos actuales, poblaciones exactas).

## 9. Dirección visual

- Fondo oscuro con degradado profundo; tarjetas con alto contraste (WCAG AA como mínimo).
- Tipografía del sistema, tamaños en `clamp()`/`vh` para TV; pregunta ≥ 4.5 vh.
- Opciones con letra grande + color + icono: nunca solo color.
- Animaciones suaves; `prefers-reduced-motion` las desactiva.
- Estados visibles: conectando, desconectado, pausado, sala llena, error.

## 10. Criterios de aceptación

1. Diez móviles pueden unirse a la misma sala; el undécimo recibe «Sala llena».
2. Una respuesta solo se acepta una vez; la segunda devuelve error y no altera la puntuación.
3. La TV refleja una respuesta nueva en < 1 s en red local.
4. Un jugador que recarga recupera nombre y puntos sin duplicarse.
5. La pregunta se cierra al agotarse el tiempo aunque ningún navegador esté abierto.
6. La pregunta se cierra en cuanto responden todos los jugadores conectados.
7. La respuesta correcta no viaja a ningún cliente antes de la revelación.
8. Solo un administrador de HA puede controlar la partida.
9. Con «solo red local» activo, una IP pública recibe 403 en las rutas del juego.
