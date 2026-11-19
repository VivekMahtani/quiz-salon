"""Carga y validación de bancos de preguntas.

Formato JSON:

{
  "version": 1,
  "language": "es",
  "categories": [{"id": "geografia", "name": "Geografía", "icon": "🌍"}],
  "questions": [
    {"id": "geo-001", "category": "geografia", "difficulty": 1,
     "q": "Texto", "options": ["correcta", "b", "c", "d"], "answer": 0}
  ]
}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_QUESTION_LEN = 220
MAX_OPTION_LEN = 80
MAX_CATEGORY_NAME_LEN = 32
NUM_OPTIONS = 4
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


@dataclass(frozen=True)
class Category:
    """Categoría de preguntas."""

    id: str
    name: str
    icon: str = "❓"


@dataclass(frozen=True)
class Question:
    """Pregunta con exactamente cuatro opciones; ``answer`` es el índice correcto."""

    id: str
    category: str
    text: str
    options: tuple[str, str, str, str]
    answer: int
    difficulty: int = 2


@dataclass
class QuestionBank:
    """Conjunto de categorías y preguntas válidas."""

    categories: dict[str, Category]
    questions: list[Question]
    warnings: list[str]

    def by_category(self) -> dict[str, list[Question]]:
        result: dict[str, list[Question]] = {cid: [] for cid in self.categories}
        for q in self.questions:
            result.setdefault(q.category, []).append(q)
        return result

    def category_counts(self) -> dict[str, int]:
        return {cid: len(qs) for cid, qs in self.by_category().items()}

    def merge(self, other: QuestionBank) -> None:
        """Añade otro banco ignorando ids repetidos."""
        for cid, cat in other.categories.items():
            self.categories.setdefault(cid, cat)
        known = {q.id for q in self.questions}
        for q in other.questions:
            if q.id in known:
                self.warnings.append(f"Pregunta duplicada ignorada: {q.id}")
                continue
            known.add(q.id)
            self.questions.append(q)
        self.warnings.extend(other.warnings)


def _clean_text(value: Any, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > max_len:
        return None
    return text


def parse_bank(
    data: Any, source: str = "banco", known_categories: dict[str, Category] | None = None
) -> QuestionBank:
    """Valida un banco ya decodificado. Las entradas inválidas se descartan con aviso."""
    warnings: list[str] = []
    categories: dict[str, Category] = {}
    questions: list[Question] = []
    if not isinstance(data, dict):
        return QuestionBank({}, [], [f"{source}: el archivo no contiene un objeto JSON"])

    for raw in data.get("categories", []) or []:
        if not isinstance(raw, dict):
            warnings.append(f"{source}: categoría inválida")
            continue
        cid = raw.get("id")
        name = _clean_text(raw.get("name"), MAX_CATEGORY_NAME_LEN)
        raw_icon = raw.get("icon")
        icon: str = raw_icon if isinstance(raw_icon, str) else "❓"
        if not isinstance(cid, str) or not _ID_RE.match(cid) or not name:
            warnings.append(f"{source}: categoría inválida {cid!r}")
            continue
        categories[cid] = Category(cid, name, icon[:4] or "❓")

    all_categories = dict(known_categories or {})
    all_categories.update(categories)

    seen: set[str] = set()
    for idx, raw in enumerate(data.get("questions", []) or []):
        label = f"{source}#{idx}"
        if not isinstance(raw, dict):
            warnings.append(f"{label}: no es un objeto")
            continue
        qid = raw.get("id")
        if not isinstance(qid, str) or not _ID_RE.match(qid):
            warnings.append(f"{label}: id inválido")
            continue
        label = f"{source}:{qid}"
        if qid in seen:
            warnings.append(f"{label}: id duplicado")
            continue
        cat = raw.get("category")
        if cat not in all_categories:
            warnings.append(f"{label}: categoría desconocida {cat!r}")
            continue
        text = _clean_text(raw.get("q"), MAX_QUESTION_LEN)
        if not text:
            warnings.append(f"{label}: texto vacío o demasiado largo")
            continue
        opts_raw = raw.get("options")
        if not isinstance(opts_raw, list) or len(opts_raw) != NUM_OPTIONS:
            warnings.append(f"{label}: se necesitan exactamente {NUM_OPTIONS} opciones")
            continue
        opts = [_clean_text(o, MAX_OPTION_LEN) for o in opts_raw]
        if any(o is None for o in opts):
            warnings.append(f"{label}: opción vacía o demasiado larga")
            continue
        if len({o.casefold() for o in opts if o}) != NUM_OPTIONS:
            warnings.append(f"{label}: opciones repetidas")
            continue
        answer = raw.get("answer")
        if not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer < NUM_OPTIONS:
            warnings.append(f"{label}: índice de respuesta inválido")
            continue
        difficulty = raw.get("difficulty", 2)
        if not isinstance(difficulty, int) or isinstance(difficulty, bool) or not 1 <= difficulty <= 3:
            difficulty = 2
        seen.add(qid)
        questions.append(
            Question(qid, cat, text, tuple(opts), answer, difficulty)  # type: ignore[arg-type]
        )
    return QuestionBank(categories, questions, warnings)


def load_bank_file(path: Path, known_categories: dict[str, Category] | None = None) -> QuestionBank:
    """Lee y valida un archivo JSON (bloqueante: ejecutar en executor)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        return QuestionBank({}, [], [f"{path.name}: no se pudo leer ({err})"])
    return parse_bank(data, source=path.name, known_categories=known_categories)


def load_all(builtin: Path, custom_dir: Path | None) -> QuestionBank:
    """Carga el banco incluido y, si existe, los paquetes de ``custom_dir`` (bloqueante)."""
    bank = load_bank_file(builtin)
    if custom_dir is not None and custom_dir.is_dir():
        for path in sorted(custom_dir.glob("*.json")):
            if path.stat().st_size > 2_000_000:
                bank.warnings.append(f"{path.name}: archivo demasiado grande (>2 MB)")
                continue
            bank.merge(load_bank_file(path, known_categories=bank.categories))
    return bank
