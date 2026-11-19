"""Utilidades de red: URL de unión, validación de URL para QR y comprobación de red local."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlencode, urlsplit

PLAY_PATH = "/quiz_salon/play"
TV_PATH = "/quiz_salon/tv"
MAX_URL_LEN = 200
_CODE_RE = re.compile(r"^[A-Z]{4}$")
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def is_private_address(raw: str | None) -> bool:
    """True si la IP es de red local (privada, loopback, enlace local o ULA)."""
    if not raw:
        return False
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return ip.is_private and not ip.is_unspecified
    return ip in ipaddress.ip_network("fc00::/7")


def normalize_base_url(raw: object) -> str | None:
    """Devuelve ``scheme://host[:port]`` o None si no es una URL http(s) válida."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > MAX_URL_LEN:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def is_loopback_base(base: str | None) -> bool:
    """True si la URL base apunta al propio equipo (no sirve para otros dispositivos)."""
    if not base:
        return False
    host = urlsplit(base).hostname or ""
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_join_url(base: str, code: str) -> str:
    return f"{base}{PLAY_PATH}?{urlencode({'code': code})}"


def validate_join_url(raw: object) -> str | None:
    """Valida que una URL sea exactamente una URL de unión del juego (para el QR)."""
    if not isinstance(raw, str) or len(raw) > MAX_URL_LEN:
        return None
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - valida el puerto
    except ValueError:
        return None
    base = normalize_base_url(f"{parts.scheme}://{parts.netloc}")
    if base is None or parts.path != PLAY_PATH or parts.fragment:
        return None
    if not parts.query.startswith("code="):
        return None
    code = parts.query[len("code=") :]
    if not _CODE_RE.match(code):
        return None
    return build_join_url(base, code)
