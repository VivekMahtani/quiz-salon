"""Lógica HTTP compartida por la integración de HA y el servidor de desarrollo."""

from __future__ import annotations

from pathlib import Path

from .netutil import validate_join_url
from .qr import QRError, make_svg

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
STATIC_URL = "/quiz_salon/static"
WS_URL = "/api/quiz_salon/ws"
QR_URL = "/api/quiz_salon/qr.svg"
PAGES = {"tv": "tv.html", "play": "play.html"}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self' ws: wss:; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


def load_pages(version: str) -> dict[str, str]:
    """Lee las páginas HTML e inserta la versión para invalidar caché (bloqueante)."""
    pages = {}
    for key, filename in PAGES.items():
        html = (FRONTEND_DIR / filename).read_text(encoding="utf-8")
        pages[key] = html.replace("__VERSION__", version)
    return pages


def qr_svg_for(url: str | None) -> tuple[int, str]:
    """Devuelve (estado HTTP, cuerpo) para la petición de QR."""
    valid = validate_join_url(url)
    if valid is None:
        return 400, "URL no válida"
    try:
        return 200, make_svg(valid)
    except QRError:
        return 400, "URL demasiado larga"
