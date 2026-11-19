"""Normalización y validación de nombres y avatares de jugador."""

from __future__ import annotations

import unicodedata

NAME_MAX_LEN = 16

AVATARS: tuple[str, ...] = (
    "🦊", "🐼", "🐙", "🦄", "🐸", "🦁", "🐧", "🐨",
    "🐯", "🐵", "🦉", "🐳", "🦖", "🐝", "🦩", "🐢",
)


class InvalidName(ValueError):
    """Nombre no válido. El mensaje es apto para mostrar al jugador."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_name(raw: object) -> str:
    """Devuelve el nombre limpio o lanza InvalidName.

    - Normaliza Unicode (NFC).
    - Elimina caracteres de control y de formato invisibles.
    - Colapsa espacios.
    - Longitud 1..NAME_MAX_LEN (en puntos de código).
    """
    if not isinstance(raw, str):
        raise InvalidName("name_invalid", "El nombre no es válido.")
    if len(raw) > 200:
        raise InvalidName("name_too_long", f"Máximo {NAME_MAX_LEN} caracteres.")
    text = unicodedata.normalize("NFC", raw)
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch.isspace():
            cleaned.append(" ")
        elif cat.startswith("C") and ch != "‍":
            # Control, formato, no asignados… (se conserva ZWJ para emojis compuestos)
            continue
        else:
            cleaned.append(ch)
    name = " ".join("".join(cleaned).split())
    if not name:
        raise InvalidName("name_empty", "Escribe un nombre.")
    if len(name) > NAME_MAX_LEN:
        raise InvalidName("name_too_long", f"Máximo {NAME_MAX_LEN} caracteres.")
    return name


def name_key(name: str) -> str:
    """Clave para comparar nombres sin distinguir mayúsculas ni acentos."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def validate_avatar(raw: object) -> str | None:
    """Devuelve el avatar si es uno de los permitidos; None si no se indicó."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and raw in AVATARS:
        return raw
    raise InvalidName("avatar_invalid", "Avatar no válido.")
