"""Utilidades comunes de prueba.

El núcleo ``game`` no depende de Home Assistant, así que se importa directamente
añadiendo la carpeta de la integración al ``sys.path``.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "quiz_salon"
sys.path.insert(0, str(INTEGRATION))

from game.engine import Game, Settings  # noqa: E402
from game.questions import load_all  # noqa: E402

BANK_PATH = INTEGRATION / "data" / "questions_es.json"


class FakeClock:
    """Reloj controlable para probar temporizadores sin esperar."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeConn:
    """Conexión simulada que guarda los mensajes enviados."""

    def __init__(self, remote: str = "192.168.1.10") -> None:
        self.remote = remote
        self.sent: list[dict] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise ConnectionError("cerrada")
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True

    def of_type(self, mtype: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == mtype]

    def last_state(self) -> dict:
        states = self.of_type("state")
        assert states, "no se recibió ningún estado"
        return states[-1]

    def last_error(self) -> dict | None:
        errs = self.of_type("error")
        return errs[-1] if errs else None


@pytest.fixture
def bank():
    return load_all(BANK_PATH, None)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def game(bank, clock):
    return Game(bank, clock, rng=random.Random(42), settings=Settings(num_questions=5, time_limit=20))
