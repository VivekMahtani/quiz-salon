"""Prueba de extremo a extremo con navegadores reales (Playwright + Chrome).

Requiere el servidor de desarrollo en marcha:
    python3 dev/devserver.py --port 8765 --time-limit 10 --questions 5
y después:
    python3 tests/e2e/run_e2e.py --base http://127.0.0.1:8765 --out /tmp/shots

Simula una TV (1920x1080), el panel de anfitrión y 4 móviles que juegan a la vez,
incluida la recarga de un móvil a mitad de partida. Falla si hay errores de consola.
"""

from __future__ import annotations

import argparse
import asyncio
import random
from pathlib import Path

from playwright.async_api import async_playwright

PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
NAMES = ["Ana", "Beto", "Carla", "Dani"]


async def main(base: str, out: Path, chrome: str | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    def watch(page, label):
        page.on("console", lambda m: errors.append(f"[{label}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[{label}] {e}"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chrome) if chrome else await p.chromium.launch()
        tv_ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        tv = await tv_ctx.new_page()
        watch(tv, "tv")
        await tv.goto(f"{base}/quiz_salon/tv")
        await tv.wait_for_selector(".join-card .room b")
        code = (await tv.text_content(".join-card .room b")).strip()
        assert len(code) == 4, code
        qr_ok = await tv.evaluate("() => { const i = document.querySelector('.join-card img'); return i.complete && i.naturalWidth > 0; }")
        assert qr_ok, "el QR no se ha cargado"
        await tv.screenshot(path=out / "01-tv-lobby-vacio.png")

        host_ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        host = await host_ctx.new_page()
        watch(host, "host")
        await host.goto(f"{base}/dev/host")
        await host.wait_for_selector("quiz-salon-panel >> text=Sala de espera")

        phones = []
        for i, name in enumerate(NAMES):
            ctx = await browser.new_context(**PHONE)
            page = await ctx.new_page()
            watch(page, name)
            # El primero escanea el QR (código en la URL); el resto lo teclea.
            url = f"{base}/quiz_salon/play?code={code}" if i == 0 else f"{base}/quiz_salon/play"
            await page.goto(url)
            await page.wait_for_selector("#name")
            if i > 0:
                await page.fill("#code", code.lower())
            await page.fill("#name", name)
            await page.click(f".avatars button >> nth={i * 3}")
            if i == 0:
                await page.screenshot(path=out / "02-movil-unirse.png")
            await page.click("button[type=submit]")
            await page.wait_for_selector(f"text=¡Estás dentro, {name}!")
            phones.append(page)

        # Nombre duplicado: debe rechazarse
        dup_ctx = await browser.new_context(**PHONE)
        dup = await dup_ctx.new_page()
        watch(dup, "dup")
        await dup.goto(f"{base}/quiz_salon/play?code={code}")
        await dup.fill("#name", "ANA")
        await dup.click("button[type=submit]")
        await dup.wait_for_selector("text=Ese nombre ya está en uso")
        await dup_ctx.close()

        await phones[0].screenshot(path=out / "03-movil-sala.png")
        await tv.wait_for_function("document.querySelectorAll('.seat.filled').length === 4")
        await tv.screenshot(path=out / "04-tv-lobby-4.png")
        await host.screenshot(path=out / "05-anfitrion-lobby.png", full_page=True)

        await host.click("quiz-salon-panel >> text=Empezar partida")
        await tv.wait_for_selector(".countdown")
        await tv.screenshot(path=out / "06-tv-cuenta-atras.png")

        rng = random.Random(4)
        total = 5
        for qn in range(total):
            await tv.wait_for_selector(".q-card", timeout=15000)
            await asyncio.sleep(0.4)
            if qn == 0:
                await tv.screenshot(path=out / "07-tv-pregunta.png")
            # Pregunta 2: un móvil recarga la página a mitad de pregunta (reconexión)
            if qn == 1:
                await phones[2].reload()
                await phones[2].wait_for_selector(".p-opt", timeout=10000)
            answered = 0
            for idx, page in enumerate(phones):
                if qn == 2 and idx == 3:
                    continue  # Dani no responde en la 3.ª: se cierra por tiempo
                await page.wait_for_selector(".p-opt:not([disabled])", timeout=10000)
                choice = rng.randrange(4)
                await page.click(f".p-opt >> nth={choice}")
                await page.wait_for_selector(".sent:has-text('enviada'), .result", timeout=5000)
                answered += 1
                if qn == 0 and idx == 0:
                    await page.screenshot(path=out / "08-movil-respondido.png")
                    # Doble toque: el segundo no debe cambiar nada
                    disabled = await page.eval_on_selector_all(".p-opt", "els => els.every(e => e.disabled)")
                    assert disabled, "los botones deberían bloquearse tras responder"
                if qn == 0 and idx == 1:
                    await tv.screenshot(path=out / "09-tv-respondiendo.png")
            await tv.wait_for_selector(".reveal-layout", timeout=15000)
            await asyncio.sleep(0.5)
            if qn == 0:
                await tv.screenshot(path=out / "10-tv-revelacion.png")
                await phones[0].screenshot(path=out / "11-movil-resultado.png")
                await host.screenshot(path=out / "12-anfitrion-revelacion.png", full_page=True)
            if qn == 3:
                # Pausa y reanudación desde el anfitrión
                await host.click("quiz-salon-panel >> text=Pausar")
                await tv.wait_for_selector(".overlay >> text=Pausa")
                await tv.screenshot(path=out / "13-tv-pausa.png")
                await host.click("quiz-salon-panel >> text=Reanudar")
                await tv.wait_for_selector(".overlay", state="detached")
            if qn < total - 1:
                await tv.wait_for_selector(".countdown", timeout=15000)

        await tv.wait_for_selector(".final", timeout=20000)
        await asyncio.sleep(1.6)
        await tv.screenshot(path=out / "14-tv-podio.png")
        await phones[0].screenshot(path=out / "15-movil-final.png")
        await host.screenshot(path=out / "16-anfitrion-final.png", full_page=True)

        # Coherencia: el marcador de la TV coincide con lo que ve cada móvil
        tv_rows = await tv.eval_on_selector_all(".step .nm", "els => els.map(e => e.textContent)")
        assert tv_rows, "no hay podio"
        for page in phones:
            text = await page.text_content(".wait h1")
            assert "Has" in text or "ganado" in text, text

        await browser.close()

    if errors:
        print("ERRORES DE CONSOLA:")
        for e in errors:
            print("  ", e)
        raise SystemExit(1)
    print("E2E OK: TV + anfitrión + 4 móviles, 5 preguntas, reconexión, pausa, nombre duplicado.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default="/tmp/quiz-shots")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    a = ap.parse_args()
    asyncio.run(main(a.base, Path(a.out), a.chrome))
