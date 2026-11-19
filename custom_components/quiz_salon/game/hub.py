"""Hub de conexiones: roles, validación de mensajes, rate limit y difusión de estado.

Independiente del servidor web: el adaptador (aiohttp en Home Assistant o el servidor
de desarrollo) solo tiene que implementar ``Connection``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .engine import Game, GameError, Phase, Settings
from .questions import QuestionBank

_LOGGER = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 4096
BUCKET_CAPACITY = 20.0
BUCKET_REFILL_PER_S = 4.0
MAX_REJECTED_BEFORE_CLOSE = 60
JOIN_FAILURES_WINDOW_S = 60.0
JOIN_FAILURES_MAX = 12
SEND_TIMEOUT_S = 5.0

HOST_COMMANDS = {"configure", "start", "pause", "resume", "next", "end", "new_game", "kick"}
PLAYER_COMMANDS = {"answer", "leave"}
ROLES = {"tv", "host", "player"}


class Connection(Protocol):
    """Transporte mínimo que debe implementar el adaptador web."""

    remote: str | None

    async def send_text(self, text: str) -> None: ...

    async def close(self) -> None: ...


@dataclass(eq=False)
class Session:
    """Una conexión WebSocket concreta."""

    conn: Connection
    role: str | None = None
    player_id: str | None = None
    tokens: float = BUCKET_CAPACITY
    last_refill: float = field(default_factory=time.monotonic)
    rejected: int = 0
    closed: bool = False


def _error(code: str, message: str, ref: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if ref:
        data["ref"] = ref
    return data


class Hub:
    """Coordina la partida única y todas las conexiones."""

    def __init__(
        self,
        bank: QuestionBank,
        *,
        clock: Callable[[], float] = time.monotonic,
        settings: Settings | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        join_base: str | None = None,
        rng: Any = None,
    ) -> None:
        self._clock = clock
        self.game = Game(bank, clock, rng=rng, settings=settings)
        self.sessions: set[Session] = set()
        self._player_tokens: dict[str, str] = {}  # token -> player_id
        self._host_token = secrets.token_urlsafe(32)
        self._join_failures: dict[str, deque[float]] = {}
        self._on_event = on_event
        self.join_base = join_base
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._sent_version = -1

    # ------------------------------------------------------------------ ciclo de vida
    @property
    def host_token(self) -> str:
        return self._host_token

    def rotate_host_token(self) -> str:
        self._host_token = secrets.token_urlsafe(32)
        return self._host_token

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="quiz_salon_ticker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for session in list(self.sessions):
            await self._close_session(session)
        self.sessions.clear()

    async def _run(self) -> None:
        while True:
            deadline = self.game.next_deadline()
            timeout = 1.0 if deadline is None else max(0.0, min(1.0, deadline - self._clock()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.tick()
            except Exception:  # pragma: no cover - protección del bucle
                _LOGGER.exception("Error en el temporizador del juego")

    async def tick(self) -> None:
        if self.game.tick():
            await self.broadcast()

    # ------------------------------------------------------------------ conexiones
    def connect(self, conn: Connection) -> Session:
        session = Session(conn=conn, last_refill=self._clock())
        self.sessions.add(session)
        return session

    async def disconnect(self, session: Session) -> None:
        self.sessions.discard(session)
        session.closed = True
        pid = session.player_id
        if pid and not any(s.player_id == pid for s in self.sessions):
            self.game.set_connected(pid, False)
            await self.broadcast()

    async def _close_session(self, session: Session) -> None:
        session.closed = True
        try:
            await session.conn.close()
        except Exception:  # pragma: no cover - el socket ya puede estar cerrado
            pass

    async def _send(self, session: Session, data: dict[str, Any]) -> None:
        if session.closed:
            return
        try:
            await asyncio.wait_for(
                session.conn.send_text(json.dumps(data, ensure_ascii=False)), SEND_TIMEOUT_S
            )
        except Exception:  # conexión rota o lenta: se descarta
            _LOGGER.debug("No se pudo enviar a %s; se cierra", session.conn.remote)
            self.sessions.discard(session)
            await self._close_session(session)
            if session.player_id and not any(s.player_id == session.player_id for s in self.sessions):
                self.game.set_connected(session.player_id, False)

    # ------------------------------------------------------------------ difusión
    def view_for(self, session: Session) -> dict[str, Any] | None:
        if session.role is None:
            return None
        view = self.game.view(session.role, session.player_id)
        if session.role in ("tv", "host"):
            view["join_base"] = self.join_base
        return view

    async def broadcast(self, force: bool = False) -> None:
        """Envía el estado a todas las sesiones identificadas (serializado)."""
        async with self._lock:
            self._prune_removed_players()
            for name, data in self.game.pop_events():
                if self._on_event is not None:
                    try:
                        self._on_event(name, data)
                    except Exception:  # pragma: no cover
                        _LOGGER.exception("Error en callback de evento %s", name)
            if not force and self.game.version == self._sent_version:
                return
            self._sent_version = self.game.version
            cache: dict[str, dict[str, Any]] = {}
            sends = []
            for session in list(self.sessions):
                if session.role is None:
                    continue
                if session.role == "player":
                    view = self.view_for(session)
                else:
                    if session.role not in cache:
                        cache[session.role] = self.view_for(session) or {}
                    view = cache[session.role]
                if view is not None:
                    sends.append(self._send(session, view))
            if sends:
                await asyncio.gather(*sends)
        self._wake.set()

    def _prune_removed_players(self) -> None:
        valid = set(self.game.players)
        self._player_tokens = {t: pid for t, pid in self._player_tokens.items() if pid in valid}
        for session in list(self.sessions):
            if session.role == "player" and session.player_id not in valid:
                session.role = None
                session.player_id = None
                asyncio.get_running_loop().create_task(
                    self._send(session, {"type": "removed", "message": "Has salido de la partida."})
                )

    # ------------------------------------------------------------------ mensajes
    def _allow(self, session: Session) -> bool:
        now = self._clock()
        session.tokens = min(
            BUCKET_CAPACITY, session.tokens + (now - session.last_refill) * BUCKET_REFILL_PER_S
        )
        session.last_refill = now
        if session.tokens < 1.0:
            return False
        session.tokens -= 1.0
        return True

    def _join_blocked(self, remote: str | None) -> bool:
        key = remote or "?"
        q = self._join_failures.get(key)
        now = self._clock()
        if q is None:
            return False
        while q and now - q[0] > JOIN_FAILURES_WINDOW_S:
            q.popleft()
        return len(q) >= JOIN_FAILURES_MAX

    def _join_failed(self, remote: str | None) -> None:
        key = remote or "?"
        self._join_failures.setdefault(key, deque()).append(self._clock())
        if len(self._join_failures) > 1000:  # evita crecimiento ilimitado
            self._join_failures.pop(next(iter(self._join_failures)))

    async def receive(self, session: Session, raw: str | bytes) -> None:
        """Procesa un mensaje entrante de una sesión."""
        if session.closed:
            return
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            await self._send(session, _error("too_large", "Mensaje demasiado grande."))
            self.sessions.discard(session)
            await self._close_session(session)
            return
        if not self._allow(session):
            session.rejected += 1
            if session.rejected >= MAX_REJECTED_BEFORE_CLOSE:
                self.sessions.discard(session)
                await self._close_session(session)
            elif session.rejected % 10 == 1:
                await self._send(session, _error("rate_limited", "Demasiados mensajes. Espera un momento."))
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            await self._send(session, _error("bad_json", "Mensaje no válido."))
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            await self._send(session, _error("bad_message", "Mensaje no válido."))
            return
        mtype = msg["type"]
        try:
            await self._dispatch(session, mtype, msg)
        except GameError as err:
            await self._send(session, _error(err.code, err.message, mtype))
            return
        await self.broadcast()

    async def _dispatch(self, session: Session, mtype: str, msg: dict[str, Any]) -> None:
        if mtype == "ping":
            await self._send(session, {"type": "pong"})
            return
        if mtype == "hello":
            await self._hello(session, msg)
            return
        if session.role is None:
            raise GameError("not_identified", "Conéctate primero.")
        if mtype in HOST_COMMANDS:
            if session.role != "host":
                raise GameError("forbidden", "Solo el anfitrión puede hacer eso.")
            await self._host_command(session, mtype, msg)
            return
        if mtype in PLAYER_COMMANDS:
            if session.role != "player" or session.player_id is None:
                raise GameError("forbidden", "Solo los jugadores pueden hacer eso.")
            await self._player_command(session, mtype, msg)
            return
        raise GameError("unknown_command", "Comando desconocido.")

    async def _hello(self, session: Session, msg: dict[str, Any]) -> None:
        role = msg.get("role")
        if role not in ROLES:
            raise GameError("bad_role", "Rol no válido.")
        if session.role is not None:
            raise GameError("already_identified", "Esta conexión ya está identificada.")
        if role == "tv":
            session.role = "tv"
            await self._send(session, {"type": "welcome", "role": "tv"})
        elif role == "host":
            token = msg.get("token")
            if not isinstance(token, str) or not hmac.compare_digest(
                token.encode(), self._host_token.encode()
            ):
                self._join_failed(session.conn.remote)
                raise GameError("bad_token", "Acceso de anfitrión no autorizado.")
            session.role = "host"
            await self._send(session, {"type": "welcome", "role": "host"})
        else:
            await self._hello_player(session, msg)
        await self._send(session, self.view_for(session) or {})

    async def _hello_player(self, session: Session, msg: dict[str, Any]) -> None:
        if self._join_blocked(session.conn.remote):
            raise GameError("too_many_attempts", "Demasiados intentos. Espera un minuto.")
        token = msg.get("session")
        if isinstance(token, str) and token:
            pid = self._player_tokens.get(token)
            if pid is None or pid not in self.game.players:
                raise GameError("session_expired", "Tu sesión ha caducado. Vuelve a unirte.")
            for other in list(self.sessions):
                if other is not session and other.player_id == pid:
                    # Otra pestaña/dispositivo con la misma sesión: se sustituye.
                    other.player_id = None
                    other.role = None
                    await self._send(other, {"type": "replaced", "message": "Te has conectado desde otra pantalla."})
                    self.sessions.discard(other)
                    await self._close_session(other)
            session.role = "player"
            session.player_id = pid
            self.game.set_connected(pid, True)
            await self._send(session, {"type": "welcome", "role": "player", "player_id": pid, "session": token})
            return
        code = msg.get("code")
        if not isinstance(code, str) or code.strip().upper() != self.game.code:
            self._join_failed(session.conn.remote)
            raise GameError("bad_code", "Código de sala incorrecto.")
        player = self.game.add_player(msg.get("name"), msg.get("avatar"))
        token = secrets.token_urlsafe(24)
        self._player_tokens[token] = player.id
        session.role = "player"
        session.player_id = player.id
        await self._send(
            session, {"type": "welcome", "role": "player", "player_id": player.id, "session": token}
        )

    async def _host_command(self, session: Session, mtype: str, msg: dict[str, Any]) -> None:
        game = self.game
        if mtype == "configure":
            data = msg.get("settings")
            if not isinstance(data, dict):
                raise GameError("invalid_settings", "Configuración no válida.")
            game.configure(data)
        elif mtype == "start":
            game.start()
        elif mtype == "pause":
            game.pause()
        elif mtype == "resume":
            game.resume()
        elif mtype == "next":
            game.next()
        elif mtype == "end":
            game.end()
        elif mtype == "new_game":
            keep = msg.get("keep_players", True)
            game.new_game(keep_players=bool(keep))
        elif mtype == "kick":
            pid = msg.get("player_id")
            if not isinstance(pid, str):
                raise GameError("unknown_player", "Jugador no válido.")
            game.remove_player(pid)
            for s in list(self.sessions):
                if s.player_id == pid:
                    s.role = None
                    s.player_id = None
                    await self._send(s, {"type": "kicked", "message": "El anfitrión te ha sacado de la partida."})
        await self._send(session, {"type": "ack", "ref": mtype})

    async def _player_command(self, session: Session, mtype: str, msg: dict[str, Any]) -> None:
        pid = session.player_id
        assert pid is not None
        if mtype == "answer":
            ans = self.game.answer(pid, msg.get("q"), msg.get("choice"))
            await self._send(session, {"type": "ack", "ref": "answer", "choice": ans.choice})
        elif mtype == "leave":
            self.game.remove_player(pid)
            session.role = None
            session.player_id = None
            await self._send(session, {"type": "left"})

    # ------------------------------------------------------------------ utilidades
    def status(self) -> dict[str, Any]:
        data = self.game.summary()
        data["connections"] = len(self.sessions)
        data["running"] = self.game.phase not in (Phase.LOBBY, Phase.FINISHED)
        return data
