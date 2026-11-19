"""Motor del juego: estado autoritativo, reglas, temporizadores y puntuación.

El motor no realiza E/S ni usa asyncio. El tiempo se obtiene de un reloj inyectable
(segundos monótonos) y las transiciones por tiempo se aplican al llamar a ``tick()``.
"""

from __future__ import annotations

import random
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .names import AVATARS, InvalidName, name_key, normalize_name, validate_avatar
from .questions import NUM_OPTIONS, Category, Question, QuestionBank

MIN_PLAYERS = 2
MAX_PLAYERS = 10
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # sin I ni O
CODE_LEN = 4

BASE_POINTS = 500
SPEED_POINTS = 500
STREAK_STEP = 100
STREAK_MAX_STEPS = 3

LIMITS = {
    "num_questions": (5, 30),
    "time_limit": (10, 60),
    "reveal_seconds": (4, 20),
}


class Phase(str, Enum):
    """Fases de la partida."""

    LOBBY = "lobby"
    COUNTDOWN = "countdown"
    QUESTION = "question"
    REVEAL = "reveal"
    FINISHED = "finished"


class GameError(Exception):
    """Acción rechazada. ``message`` está en español y se puede mostrar al usuario."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Settings:
    """Configuración de partida elegida por el anfitrión."""

    num_questions: int = 12
    time_limit: int = 20
    categories: tuple[str, ...] = ()  # vacío = todas
    auto_advance: bool = True
    reveal_seconds: int = 8
    countdown_seconds: int = 3

    def as_dict(self) -> dict[str, Any]:
        return {
            "num_questions": self.num_questions,
            "time_limit": self.time_limit,
            "categories": list(self.categories),
            "auto_advance": self.auto_advance,
            "reveal_seconds": self.reveal_seconds,
        }


@dataclass
class Answer:
    """Respuesta registrada por el servidor."""

    choice: int
    remaining: float  # segundos restantes al recibirla
    elapsed: float  # segundos desde el inicio de la pregunta
    correct: bool = False
    points: int = 0


@dataclass
class Player:
    """Jugador dentro de la partida."""

    id: str
    name: str
    avatar: str
    joined_order: int
    connected: bool = True
    score: int = 0
    streak: int = 0
    best_streak: int = 0
    correct_count: int = 0
    correct_time: float = 0.0
    answers: dict[int, Answer] = field(default_factory=dict)

    def reset_scores(self) -> None:
        self.score = 0
        self.streak = 0
        self.best_streak = 0
        self.correct_count = 0
        self.correct_time = 0.0
        self.answers.clear()


@dataclass
class RoundQuestion:
    """Pregunta preparada para una ronda, con opciones ya barajadas."""

    question: Question
    options: tuple[str, ...]
    correct: int
    double: bool = False


def compute_points(remaining: float, total: float, streak_after: int, double: bool) -> int:
    """Puntos por un acierto.

    ``streak_after`` es la racha contando este acierto (1 = primer acierto seguido).
    """
    frac = 0.0 if total <= 0 else max(0.0, min(1.0, remaining / total))
    points = BASE_POINTS + round(SPEED_POINTS * frac)
    points += STREAK_STEP * min(max(streak_after - 1, 0), STREAK_MAX_STEPS)
    if double:
        points *= 2
    return points


def _clamp_int(value: Any, key: str) -> int:
    lo, hi = LIMITS[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise GameError("invalid_settings", f"Valor no válido para {key}.")
    return max(lo, min(hi, value))


class Game:
    """Partida única gestionada por el servidor."""

    def __init__(
        self,
        bank: QuestionBank,
        clock: Callable[[], float],
        rng: random.Random | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.bank = bank
        self._clock = clock
        self._rng = rng or random.Random(secrets.randbits(64))
        self.settings = settings or Settings()
        self.players: dict[str, Player] = {}
        self._join_counter = 0
        self._recent: deque[str] = deque(maxlen=max(50, len(bank.questions) // 2))
        self.code = ""
        self.phase = Phase.LOBBY
        self.paused = False
        self.rounds: list[RoundQuestion] = []
        self.index = -1
        self.deadline: float | None = None
        self.phase_started = 0.0
        self.phase_duration: float = 0.0
        self._paused_remaining: float | None = None
        self.warnings: list[str] = []
        self.version = 0  # se incrementa en cada cambio
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._new_code()

    # ------------------------------------------------------------------ utilidades
    def now(self) -> float:
        return self._clock()

    def _changed(self) -> None:
        self.version += 1

    def _emit(self, event: str, /, **data: Any) -> None:
        self._events.append((event, data))

    def pop_events(self) -> list[tuple[str, dict[str, Any]]]:
        events, self._events = self._events, []
        return events

    def _new_code(self) -> None:
        self.code = "".join(self._rng.choice(CODE_ALPHABET) for _ in range(CODE_LEN))

    @property
    def current(self) -> RoundQuestion | None:
        if 0 <= self.index < len(self.rounds):
            return self.rounds[self.index]
        return None

    def remaining(self) -> float | None:
        """Segundos restantes de la fase con temporizador (None si no hay)."""
        if self.paused:
            return self._paused_remaining
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - self.now())

    def connected_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.connected]

    def categories_info(self) -> list[dict[str, Any]]:
        counts = self.bank.category_counts()
        return [
            {"id": c.id, "name": c.name, "icon": c.icon, "count": counts.get(c.id, 0)}
            for c in self.bank.categories.values()
            if counts.get(c.id, 0) > 0
        ]

    def category(self, cid: str) -> Category:
        return self.bank.categories.get(cid, Category(cid, cid))

    # ------------------------------------------------------------------ jugadores
    def add_player(self, raw_name: Any, raw_avatar: Any = None) -> Player:
        try:
            name = normalize_name(raw_name)
            avatar = validate_avatar(raw_avatar)
        except InvalidName as err:
            raise GameError(err.code, err.message) from err
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("room_full", f"La sala está llena (máximo {MAX_PLAYERS}).")
        key = name_key(name)
        if any(name_key(p.name) == key for p in self.players.values()):
            raise GameError("name_taken", "Ese nombre ya está en uso. Prueba con otro.")
        used = {p.avatar for p in self.players.values()}
        if avatar is None or avatar in used:
            free = [a for a in AVATARS if a not in used] or list(AVATARS)
            avatar = self._rng.choice(free)
        self._join_counter += 1
        pid = f"p{self._join_counter}"
        player = Player(pid, name, avatar, self._join_counter)
        self.players[pid] = player
        self._emit("player_joined", player_id=pid, name=name)
        self._changed()
        return player

    def remove_player(self, pid: str) -> None:
        if pid not in self.players:
            raise GameError("unknown_player", "Ese jugador ya no está en la partida.")
        del self.players[pid]
        self._changed()
        self._maybe_close_question()

    def set_connected(self, pid: str, connected: bool) -> None:
        player = self.players.get(pid)
        if player is None or player.connected == connected:
            return
        player.connected = connected
        self._changed()
        if not connected:
            self._maybe_close_question()

    # ------------------------------------------------------------------ anfitrión
    def configure(self, data: dict[str, Any]) -> None:
        if self.phase != Phase.LOBBY:
            raise GameError("wrong_phase", "Solo se puede configurar antes de empezar.")
        s = self.settings
        new = Settings(**{**s.__dict__})
        if "num_questions" in data:
            new.num_questions = _clamp_int(data["num_questions"], "num_questions")
        if "time_limit" in data:
            new.time_limit = _clamp_int(data["time_limit"], "time_limit")
        if "reveal_seconds" in data:
            new.reveal_seconds = _clamp_int(data["reveal_seconds"], "reveal_seconds")
        if "auto_advance" in data:
            if not isinstance(data["auto_advance"], bool):
                raise GameError("invalid_settings", "Valor no válido para auto_advance.")
            new.auto_advance = data["auto_advance"]
        if "categories" in data:
            cats = data["categories"]
            if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
                raise GameError("invalid_settings", "Lista de categorías no válida.")
            valid = [c for c in dict.fromkeys(cats) if c in self.bank.categories]
            if cats and not valid:
                raise GameError("invalid_settings", "Ninguna categoría seleccionada es válida.")
            new.categories = tuple(valid)
        self.settings = new
        self._changed()

    def _select_questions(self) -> list[RoundQuestion]:
        wanted = set(self.settings.categories) or set(self.bank.categories)
        pool = [q for q in self.bank.questions if q.category in wanted]
        fresh = [q for q in pool if q.id not in self._recent]
        stale = [q for q in pool if q.id in self._recent]
        self._rng.shuffle(fresh)
        self._rng.shuffle(stale)
        # Reparto equilibrado: ronda por categorías, priorizando preguntas no vistas.
        buckets: dict[str, list[Question]] = {}
        for q in fresh + stale:
            buckets.setdefault(q.category, []).append(q)
        order = list(buckets)
        self._rng.shuffle(order)
        selected: list[Question] = []
        n = self.settings.num_questions
        while len(selected) < n and any(buckets.values()):
            for cid in order:
                if buckets[cid] and len(selected) < n:
                    selected.append(buckets[cid].pop(0))
        # Evita dos preguntas seguidas de la misma categoría cuando sea posible.
        self._rng.shuffle(selected)
        for i in range(1, len(selected)):
            if selected[i].category == selected[i - 1].category:
                for j in range(i + 1, len(selected)):
                    if selected[j].category != selected[i - 1].category:
                        selected[i], selected[j] = selected[j], selected[i]
                        break
        rounds = []
        for i, q in enumerate(selected):
            perm = list(range(NUM_OPTIONS))
            self._rng.shuffle(perm)
            rounds.append(
                RoundQuestion(
                    question=q,
                    options=tuple(q.options[k] for k in perm),
                    correct=perm.index(q.answer),
                    double=(i == len(selected) - 1 and len(selected) > 1),
                )
            )
        return rounds

    def start(self) -> None:
        if self.phase != Phase.LOBBY:
            raise GameError("wrong_phase", "La partida ya ha empezado.")
        if len(self.connected_players()) < MIN_PLAYERS:
            raise GameError("not_enough_players", f"Se necesitan al menos {MIN_PLAYERS} jugadores.")
        rounds = self._select_questions()
        if not rounds:
            raise GameError("no_questions", "No hay preguntas en las categorías elegidas.")
        self.warnings = []
        if len(rounds) < self.settings.num_questions:
            self.warnings.append(
                f"Solo hay {len(rounds)} preguntas disponibles en las categorías elegidas."
            )
        for p in self.players.values():
            p.reset_scores()
        self.rounds = rounds
        self._recent.extend(r.question.id for r in rounds)
        self.index = -1
        self._emit("game_started", total=len(rounds))
        self._begin_countdown()

    def pause(self) -> None:
        if self.phase not in (Phase.COUNTDOWN, Phase.QUESTION, Phase.REVEAL):
            raise GameError("wrong_phase", "No hay nada que pausar ahora.")
        if self.paused:
            return
        self._paused_remaining = None if self.deadline is None else max(0.0, self.deadline - self.now())
        self.deadline = None
        self.paused = True
        self._changed()

    def resume(self) -> None:
        if not self.paused:
            return
        self.paused = False
        if self._paused_remaining is not None:
            self.deadline = self.now() + self._paused_remaining
        self._paused_remaining = None
        self._changed()

    def next(self) -> None:
        """Avance manual del anfitrión."""
        if self.paused:
            self.resume()
        if self.phase == Phase.COUNTDOWN:
            self._begin_question()
        elif self.phase == Phase.QUESTION:
            self._close_question()
        elif self.phase == Phase.REVEAL:
            self._advance_after_reveal()
        else:
            raise GameError("wrong_phase", "No se puede avanzar en esta fase.")

    def end(self) -> None:
        if self.phase == Phase.FINISHED:
            return
        if self.phase == Phase.LOBBY:
            raise GameError("wrong_phase", "La partida no ha empezado.")
        self._finish()

    def new_game(self, keep_players: bool = True) -> None:
        self.phase = Phase.LOBBY
        self.paused = False
        self._paused_remaining = None
        self.deadline = None
        self.rounds = []
        self.index = -1
        self.warnings = []
        if keep_players:
            # Los desconectados se descartan para no ocupar plazas.
            self.players = {pid: p for pid, p in self.players.items() if p.connected}
            for p in self.players.values():
                p.reset_scores()
        else:
            self.players = {}
        self._new_code()
        self._emit("new_game")
        self._changed()

    # ------------------------------------------------------------------ jugador
    def answer(self, pid: str, q_index: Any, choice: Any) -> Answer:
        player = self.players.get(pid)
        if player is None:
            raise GameError("unknown_player", "No estás en la partida.")
        if self.phase != Phase.QUESTION:
            raise GameError("wrong_phase", "Ahora no se puede responder.")
        if self.paused:
            raise GameError("paused", "La partida está en pausa.")
        if not isinstance(q_index, int) or isinstance(q_index, bool) or q_index != self.index:
            raise GameError("stale_question", "Esa pregunta ya no está activa.")
        if not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice < NUM_OPTIONS:
            raise GameError("invalid_choice", "Opción no válida.")
        if self.index in player.answers:
            raise GameError("already_answered", "Ya has respondido a esta pregunta.")
        now = self.now()
        remaining = max(0.0, (self.deadline or now) - now)
        if remaining <= 0:
            raise GameError("time_up", "Se acabó el tiempo.")
        ans = Answer(choice=choice, remaining=remaining, elapsed=now - self.phase_started)
        player.answers[self.index] = ans
        self._changed()
        self._maybe_close_question()
        return ans

    # ------------------------------------------------------------------ transiciones
    def _set_timer(self, seconds: float | None) -> None:
        now = self.now()
        self.phase_started = now
        self.phase_duration = seconds or 0.0
        self.deadline = None if seconds is None else now + seconds

    def _begin_countdown(self) -> None:
        self.index += 1
        self.phase = Phase.COUNTDOWN
        self._set_timer(self.settings.countdown_seconds)
        self._changed()

    def _begin_question(self) -> None:
        self.phase = Phase.QUESTION
        self._set_timer(self.settings.time_limit)
        self._emit("question_started", number=self.index + 1)
        self._changed()

    def _maybe_close_question(self) -> None:
        if self.phase != Phase.QUESTION or self.paused:
            return
        connected = self.connected_players()
        if connected and all(self.index in p.answers for p in connected):
            self._close_question()

    def _close_question(self) -> None:
        rq = self.current
        assert rq is not None
        total = float(self.settings.time_limit)
        for p in self.players.values():
            ans = p.answers.get(self.index)
            if ans is not None and ans.choice == rq.correct:
                p.streak += 1
                p.best_streak = max(p.best_streak, p.streak)
                ans.correct = True
                ans.points = compute_points(ans.remaining, total, p.streak, rq.double)
                p.score += ans.points
                p.correct_count += 1
                p.correct_time += ans.elapsed
            else:
                p.streak = 0
        self.phase = Phase.REVEAL
        self._set_timer(self.settings.reveal_seconds if self.settings.auto_advance else None)
        self._emit("question_revealed", number=self.index + 1)
        self._changed()

    def _advance_after_reveal(self) -> None:
        if self.index + 1 >= len(self.rounds):
            self._finish()
        else:
            self._begin_countdown()

    def _finish(self) -> None:
        self.phase = Phase.FINISHED
        self.paused = False
        self._paused_remaining = None
        self.deadline = None
        ranking = self.ranking()
        winners = [p.name for rank, p in ranking if rank == 1]
        self._emit("game_finished", winners=winners)
        self._changed()

    def tick(self) -> bool:
        """Aplica transiciones vencidas. Devuelve True si cambió el estado."""
        changed = False
        # Bucle por si han vencido varias fases (p. ej., tras un bloqueo largo).
        for _ in range(4):
            if self.paused or self.deadline is None or self.now() < self.deadline:
                break
            changed = True
            if self.phase == Phase.COUNTDOWN:
                self._begin_question()
            elif self.phase == Phase.QUESTION:
                self._close_question()
            elif self.phase == Phase.REVEAL:
                self._advance_after_reveal()
            else:
                self.deadline = None
        return changed

    def next_deadline(self) -> float | None:
        return None if self.paused else self.deadline

    # ------------------------------------------------------------------ clasificación
    def ranking(self) -> list[tuple[int, Player]]:
        """Clasificación con empates: puntos, aciertos, menor tiempo total en aciertos."""

        def key(p: Player) -> tuple[int, int, float]:
            return (-p.score, -p.correct_count, round(p.correct_time, 3))

        ordered = sorted(self.players.values(), key=lambda p: (key(p), p.joined_order))
        result: list[tuple[int, Player]] = []
        prev_key: tuple[int, int, float] | None = None
        rank = 0
        for pos, p in enumerate(ordered, start=1):
            k = key(p)
            if k != prev_key:
                rank = pos
                prev_key = k
            result.append((rank, p))
        return result

    # ------------------------------------------------------------------ vistas
    def _player_public(self, p: Player, rank: int) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": p.id,
            "name": p.name,
            "avatar": p.avatar,
            "score": p.score,
            "rank": rank,
            "connected": p.connected,
            "streak": p.streak,
            "correct": p.correct_count,
        }
        if self.phase in (Phase.QUESTION, Phase.REVEAL):
            data["answered"] = self.index in p.answers
        return data

    def _question_view(self, reveal: bool) -> dict[str, Any] | None:
        rq = self.current
        if rq is None or self.phase in (Phase.LOBBY, Phase.FINISHED):
            return None
        cat = self.category(rq.question.category)
        view: dict[str, Any] = {
            "index": self.index,
            "number": self.index + 1,
            "total": len(self.rounds),
            "category": {"id": cat.id, "name": cat.name, "icon": cat.icon},
            "double": rq.double,
            "difficulty": rq.question.difficulty,
        }
        if self.phase in (Phase.QUESTION, Phase.REVEAL):
            view["text"] = rq.question.text
            view["options"] = list(rq.options)
        if reveal and self.phase == Phase.REVEAL:
            counts = [0] * NUM_OPTIONS
            results = []
            for p in self.players.values():
                ans = p.answers.get(self.index)
                if ans is not None:
                    counts[ans.choice] += 1
                results.append(
                    {
                        "id": p.id,
                        "choice": None if ans is None else ans.choice,
                        "correct": bool(ans and ans.correct),
                        "points": 0 if ans is None else ans.points,
                    }
                )
            view["correct"] = rq.correct
            view["counts"] = counts
            view["results"] = results
        return view

    def view(self, role: str, player_id: str | None = None) -> dict[str, Any]:
        """Estado visible para un rol: 'tv', 'host' o 'player'."""
        ranking = self.ranking()
        rank_of = {p.id: r for r, p in ranking}
        remaining = self.remaining()
        state: dict[str, Any] = {
            "type": "state",
            "version": self.version,
            "phase": self.phase.value,
            "paused": self.paused,
            "code": self.code,
            "settings": self.settings.as_dict(),
            "min_players": MIN_PLAYERS,
            "max_players": MAX_PLAYERS,
            "players": [self._player_public(p, rank_of[p.id]) for _, p in ranking],
            "question": self._question_view(reveal=True),
            "remaining_ms": None if remaining is None else int(remaining * 1000),
            "duration_ms": int(self.phase_duration * 1000),
            "warnings": list(self.warnings),
        }
        if self.phase in (Phase.QUESTION, Phase.REVEAL):
            state["answered_count"] = sum(1 for p in self.players.values() if self.index in p.answers)
        if role == "host":
            state["categories_available"] = self.categories_info()
            state["question_bank_size"] = len(self.bank.questions)
        if role == "player" and player_id is not None:
            p = self.players.get(player_id)
            if p is not None:
                you: dict[str, Any] = {
                    "id": p.id,
                    "name": p.name,
                    "avatar": p.avatar,
                    "score": p.score,
                    "rank": rank_of[p.id],
                    "streak": p.streak,
                    "correct": p.correct_count,
                }
                ans = p.answers.get(self.index) if self.phase in (Phase.QUESTION, Phase.REVEAL) else None
                you["answer"] = None if ans is None else ans.choice
                if self.phase == Phase.REVEAL and ans is not None:
                    you["points"] = ans.points
                    you["was_correct"] = ans.correct
                state["you"] = you
        return state

    def summary(self) -> dict[str, Any]:
        """Resumen compacto para atributos/eventos de Home Assistant."""
        leader = self.ranking()[0][1] if self.players else None
        return {
            "phase": self.phase.value,
            "code": self.code,
            "players": len(self.players),
            "question": self.index + 1 if self.current else 0,
            "total_questions": len(self.rounds),
            "leader": leader.name if leader else None,
        }
