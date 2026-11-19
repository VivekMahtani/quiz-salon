"""E2E: 10 móviles a la vez responden la misma pregunta y el 11.º ve «sala llena».

    python3 dev/devserver.py --port 8765 --time-limit 15 --questions 5
    python3 tests/e2e/run_full_room.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

PHONE = {"viewport": {"width": 360, "height": 740}, "is_mobile": True, "has_touch": True}


async def main(base: str, chrome: str) -> None:
    token = json.load(urllib.request.urlopen(f"{base}/dev/host_token"))["token"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chrome)
        tv = await (await browser.new_context(viewport={"width": 1920, "height": 1080})).new_page()
        await tv.goto(f"{base}/quiz_salon/tv")
        await tv.wait_for_selector(".join-card .room b")
        code = (await tv.text_content(".join-card .room b")).strip()

        async def join(i: int):
            page = await (await browser.new_context(**PHONE)).new_page()
            await page.goto(f"{base}/quiz_salon/play?code={code}")
            await page.fill("#name", f"Jugador {i + 1}")
            await page.click("button[type=submit]")
            return page

        phones = await asyncio.gather(*(join(i) for i in range(10)))
        await asyncio.gather(*(ph.wait_for_selector("text=¡Estás dentro") for ph in phones))
        extra = await join(10)
        await extra.wait_for_selector("text=La sala está llena")

        host = await (await browser.new_context()).new_page()
        await host.goto(f"{base}/dev/host")
        await host.wait_for_selector("quiz-salon-panel >> text=Jugadores (10/10)")
        await host.click("quiz-salon-panel >> text=Empezar partida")

        await asyncio.gather(*(ph.wait_for_selector(".p-opt:not([disabled])", timeout=15000) for ph in phones))
        # Los 10 pulsan a la vez
        await asyncio.gather(*(ph.click(f".p-opt >> nth={i % 4}") for i, ph in enumerate(phones)))
        await tv.wait_for_selector(".reveal-layout", timeout=5000)
        counts = await tv.eval_on_selector_all(".opt .count", "els => els.map(e => +e.textContent)")
        assert sum(counts) == 10, counts
        await tv.screenshot(path="/tmp/shots/17-tv-10-jugadores.png")
        await browser.close()
        print(f"OK: 10 jugadores simultáneos, reparto {counts}, el 11.º rechazado (sala llena). Token anfitrión len={len(token)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    a = ap.parse_args()
    asyncio.run(main(a.base, a.chrome))
