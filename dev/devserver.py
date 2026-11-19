"""Servidor de desarrollo sin dependencias (solo biblioteca estándar).

Sirve el mismo frontend y el mismo núcleo ``game`` que la integración de Home Assistant,
con un WebSocket mínimo (RFC 6455), para probar con navegadores reales sin instalar HA.

    python3 dev/devserver.py --port 8765

- TV:        http://IP:8765/quiz_salon/tv
- Jugadores: http://IP:8765/quiz_salon/play
- Anfitrión: http://IP:8765/dev/host   (simula el panel de HA; SOLO para desarrollo)

No usar en producción: /dev/host_token entrega el token de anfitrión sin autenticación.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import struct
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "quiz_salon"
sys.path.insert(0, str(INTEGRATION))

from game.engine import Settings  # noqa: E402
from game.hub import MAX_MESSAGE_BYTES, Hub  # noqa: E402
from game.questions import load_all  # noqa: E402
from game.web import FRONTEND_DIR, QR_URL, SECURITY_HEADERS, STATIC_URL, WS_URL, load_pages, qr_svg_for  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
VERSION = json.loads((INTEGRATION / "manifest.json").read_text())["version"]
DEV_HOST_HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="data:,"><title>Anfitrión (dev)</title>
<script type="module">
import "/quiz_salon/static/panel.js";
const panel = document.createElement("quiz-salon-panel");
panel.hass = { callWS: async (msg) => (await fetch("/dev/host_token")).json() };
panel.narrow = innerWidth < 870;
document.body.append(panel);
</script></head><body style="margin:0"></body></html>"""

log = logging.getLogger("devserver")


class WSConn:
    """Conexión WebSocket mínima sobre asyncio streams."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, remote: str) -> None:
        self.reader = reader
        self.writer = writer
        self.remote = remote
        self._closed = False
        self._lock = asyncio.Lock()

    async def _frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack("!H", n)
        else:
            header.append(127)
            header += struct.pack("!Q", n)
        async with self._lock:
            self.writer.write(bytes(header) + payload)
            await self.writer.drain()

    async def send_text(self, text: str) -> None:
        if self._closed:
            raise ConnectionError("cerrada")
        await self._frame(0x1, text.encode("utf-8"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._frame(0x8, struct.pack("!H", 1000))
        except Exception:
            pass
        self.writer.close()

    async def messages(self):
        buf = b""
        while True:
            head = await self.reader.readexactly(2)
            fin, opcode = head[0] & 0x80, head[0] & 0x0F
            masked, n = head[1] & 0x80, head[1] & 0x7F
            if n == 126:
                n = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if n > MAX_MESSAGE_BYTES * 4:
                return
            mask = await self.reader.readexactly(4) if masked else b"\0\0\0\0"
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(await self.reader.readexactly(n)))
            if opcode == 0x8:
                return
            if opcode == 0x9:
                await self._frame(0xA, data)
                continue
            if opcode in (0x1, 0x0):
                buf += data
                if fin:
                    yield buf.decode("utf-8", errors="replace")
                    buf = b""


class DevServer:
    def __init__(self, hub: Hub, pages: dict[str, str]) -> None:
        self.hub = hub
        self.pages = pages

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        remote = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            request_line = (await reader.readline()).decode("latin-1").strip()
            if not request_line:
                writer.close()
                return
            method, target, _ = request_line.split(" ", 2)
            headers = {}
            while True:
                line = (await reader.readline()).decode("latin-1")
                if line in ("\r\n", "\n", ""):
                    break
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
            url = urlsplit(target)
            path = unquote(url.path)
            if path == WS_URL and headers.get("upgrade", "").lower() == "websocket":
                await self.websocket(reader, writer, headers, remote)
                return
            await self.http(writer, method, path, parse_qs(url.query))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:
            log.exception("Error atendiendo petición")
        finally:
            if not writer.is_closing():
                writer.close()

    async def respond(self, writer, status: int, body: bytes | str, ctype: str = "text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        reason = {200: "OK", 302: "Found", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        hdrs = {"Content-Type": ctype, "Content-Length": str(len(body)), "Connection": "close", **(extra or {})}
        head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
        writer.write(head.encode("latin-1") + body)
        await writer.drain()

    async def http(self, writer, method: str, path: str, query) -> None:
        if method != "GET":
            await self.respond(writer, 400, "Solo GET")
        elif path in ("/quiz_salon/tv", "/quiz_salon/play"):
            key = path.rsplit("/", 1)[-1]
            await self.respond(writer, 200, self.pages[key], "text/html; charset=utf-8", SECURITY_HEADERS)
        elif path == "/":
            await self.respond(writer, 302, "", extra={"Location": "/quiz_salon/tv"})
        elif path == "/dev/host":
            await self.respond(writer, 200, DEV_HOST_HTML, "text/html; charset=utf-8")
        elif path == "/dev/host_token":
            data = {"token": self.hub.host_token, "join_base": self.hub.join_base, "local_only": True, "version": VERSION}
            await self.respond(writer, 200, json.dumps(data), "application/json")
        elif path == QR_URL:
            status, body = qr_svg_for((query.get("url") or [None])[0])
            await self.respond(writer, status, body, "image/svg+xml" if status == 200 else "text/plain; charset=utf-8")
        elif path.startswith(STATIC_URL + "/"):
            rel = path[len(STATIC_URL) + 1 :]
            file = (FRONTEND_DIR / rel).resolve()
            if FRONTEND_DIR.resolve() not in file.parents or not file.is_file():
                await self.respond(writer, 404, "No encontrado")
                return
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if file.suffix == ".js":
                ctype = "text/javascript"
            await self.respond(writer, 200, file.read_bytes(), ctype, {"Cache-Control": "no-cache"})
        else:
            await self.respond(writer, 404, "No encontrado")

    async def websocket(self, reader, writer, headers, remote) -> None:
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        conn = WSConn(reader, writer, remote)
        session = self.hub.connect(conn)
        try:
            async for text in conn.messages():
                await self.hub.receive(session, text)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            await self.hub.disconnect(session)
            await conn.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--base-url", default=None, help="URL base para el QR (p. ej. http://192.168.1.20:8765)")
    parser.add_argument("--time-limit", type=int, default=20)
    parser.add_argument("--questions", type=int, default=12)
    parser.add_argument("--custom-dir", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bank = load_all(INTEGRATION / "data" / "questions_es.json", Path(args.custom_dir) if args.custom_dir else None)
    settings = Settings(num_questions=args.questions, time_limit=args.time_limit)
    hub = Hub(bank, settings=settings, join_base=args.base_url, on_event=lambda n, d: log.info("evento %s %s", n, d))
    await hub.start()
    server = DevServer(hub, load_pages(VERSION))
    srv = await asyncio.start_server(server.handle, args.host, args.port)
    log.info("Quiz del Salón (dev) en http://%s:%d  — %d preguntas", args.host, args.port, len(bank.questions))
    log.info("TV: /quiz_salon/tv · Jugadores: /quiz_salon/play · Anfitrión: /dev/host")
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
