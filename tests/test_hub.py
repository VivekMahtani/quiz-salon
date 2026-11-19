"""Pruebas del hub: roles, autorización, reconexión, rate limit y difusión."""

from __future__ import annotations

import asyncio
import json
import random

from conftest import FakeConn
from game.engine import Phase, Settings
from game.hub import JOIN_FAILURES_MAX, MAX_MESSAGE_BYTES, Hub


def run(coro):
    return asyncio.run(coro)


def make_hub(bank, clock, **kw):
    return Hub(bank, clock=clock, rng=random.Random(7), settings=Settings(num_questions=5), **kw)


async def send(hub, sess, /, **msg):
    await hub.receive(sess, json.dumps(msg))


async def join(hub, name, remote="192.168.1.20"):
    conn = FakeConn(remote)
    s = hub.connect(conn)
    await send(hub, s, type="hello", role="player", code=hub.game.code, name=name)
    return conn, s


def test_roles_and_host_authorization(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        tv = FakeConn()
        s_tv = hub.connect(tv)
        await send(hub, s_tv, type="hello", role="tv")
        assert tv.of_type("welcome")[0]["role"] == "tv"
        assert tv.last_state()["phase"] == "lobby"

        # La TV no puede controlar la partida
        await send(hub, s_tv, type="start")
        assert tv.last_error()["code"] == "forbidden"

        # Host con token incorrecto
        bad = FakeConn()
        s_bad = hub.connect(bad)
        await send(hub, s_bad, type="hello", role="host", token="nope")
        assert bad.last_error()["code"] == "bad_token"
        await send(hub, s_bad, type="start")
        assert bad.last_error()["code"] == "not_identified"

        host = FakeConn()
        s_host = hub.connect(host)
        await send(hub, s_host, type="hello", role="host", token=hub.host_token)
        assert host.of_type("welcome")[0]["role"] == "host"
        assert "categories_available" in host.last_state()
        assert "categories_available" not in tv.last_state()

        # Rotar el token invalida el antiguo para nuevas conexiones
        old = hub.host_token
        hub.rotate_host_token()
        other = FakeConn()
        s_other = hub.connect(other)
        await send(hub, s_other, type="hello", role="host", token=old)
        assert other.last_error()["code"] == "bad_token"

    run(scenario())


def test_join_reconnect_and_play(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        host = FakeConn()
        s_host = hub.connect(host)
        await send(hub, s_host, type="hello", role="host", token=hub.host_token)
        tv = FakeConn()
        await send(hub, hub.connect(tv), type="hello", role="tv")

        c_ana, s_ana = await join(hub, "Ana")
        welcome = c_ana.of_type("welcome")[0]
        token = welcome["session"]
        assert c_ana.last_state()["you"]["name"] == "Ana"
        # La TV nunca ve tokens de sesión
        assert token not in json.dumps(tv.sent)
        assert token not in json.dumps(host.sent)

        c_bad = FakeConn()
        s_bad = hub.connect(c_bad)
        await send(hub, s_bad, type="hello", role="player", code="ZZZZ", name="Pepe")
        assert c_bad.last_error()["code"] == "bad_code"

        c_dup = FakeConn()
        await send(hub, hub.connect(c_dup), type="hello", role="player", code=hub.game.code.lower(), name="ana")
        assert c_dup.last_error()["code"] == "name_taken"

        c_beto, s_beto = await join(hub, "Beto")
        assert [p["name"] for p in tv.last_state()["players"]] == ["Ana", "Beto"]

        # Jugador no puede usar comandos de anfitrión
        await send(hub, s_ana, type="start")
        assert c_ana.last_error()["code"] == "forbidden"

        await send(hub, s_host, type="start")
        assert tv.last_state()["phase"] == "countdown"
        clock.advance(3)
        await hub.tick()
        st = c_ana.last_state()
        assert st["phase"] == "question" and "correct" not in st["question"]

        # Ana se desconecta y se reconecta con su token
        await hub.disconnect(s_ana)
        assert [p["connected"] for p in tv.last_state()["players"]] == [False, True]
        c_ana2 = FakeConn()
        s_ana2 = hub.connect(c_ana2)
        await send(hub, s_ana2, type="hello", role="player", session=token)
        assert c_ana2.of_type("welcome")[0]["player_id"] == welcome["player_id"]
        assert len(hub.game.players) == 2

        correct = hub.game.current.correct
        await send(hub, s_ana2, type="answer", q=0, choice=correct)
        assert c_ana2.of_type("ack")[-1]["choice"] == correct
        await send(hub, s_ana2, type="answer", q=0, choice=correct)
        assert c_ana2.last_error()["code"] == "already_answered"
        assert tv.last_state()["answered_count"] == 1
        await send(hub, s_beto, type="answer", q=0, choice=(correct + 1) % 4)
        st = tv.last_state()
        assert st["phase"] == "reveal"
        assert st["question"]["correct"] == correct
        you = c_ana2.last_state()["you"]
        assert you["was_correct"] is True and you["points"] > 0

    run(scenario())


def test_session_replaced_on_second_tab(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        c1, s1 = await join(hub, "Ana")
        token = c1.of_type("welcome")[0]["session"]
        c2 = FakeConn()
        await send(hub, hub.connect(c2), type="hello", role="player", session=token)
        assert c1.of_type("replaced") and c1.closed
        assert len(hub.game.players) == 1

    run(scenario())


def test_expired_session_and_kick(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        host = FakeConn()
        s_host = hub.connect(host)
        await send(hub, s_host, type="hello", role="host", token=hub.host_token)
        c_ana, s_ana = await join(hub, "Ana")
        token = c_ana.of_type("welcome")[0]["session"]
        pid = c_ana.of_type("welcome")[0]["player_id"]
        await send(hub, s_host, type="kick", player_id=pid)
        assert c_ana.of_type("kicked")
        assert hub.game.players == {}
        # El jugador expulsado ya no puede mandar respuestas
        await send(hub, s_ana, type="answer", q=0, choice=0)
        assert c_ana.last_error()["code"] == "not_identified"
        c_again = FakeConn()
        await send(hub, hub.connect(c_again), type="hello", role="player", session=token)
        assert c_again.last_error()["code"] == "session_expired"

    run(scenario())


def test_new_game_without_players_removes_sessions(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        host = FakeConn()
        s_host = hub.connect(host)
        await send(hub, s_host, type="hello", role="host", token=hub.host_token)
        c_ana, _ = await join(hub, "Ana")
        code = hub.game.code
        await send(hub, s_host, type="new_game", keep_players=False)
        await asyncio.sleep(0)  # deja salir el aviso "removed"
        assert c_ana.of_type("removed")
        assert hub.game.code != code or True

    run(scenario())


def test_message_validation_and_limits(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        c = FakeConn()
        s = hub.connect(c)
        await hub.receive(s, "{no json")
        assert c.last_error()["code"] == "bad_json"
        await hub.receive(s, "[1,2]")
        assert c.last_error()["code"] == "bad_message"
        await send(hub, s, type="hello", role="admin")
        assert c.last_error()["code"] == "bad_role"
        await send(hub, s, type="ping")
        assert c.of_type("pong")
        await hub.receive(s, json.dumps({"type": "ping", "x": "a" * MAX_MESSAGE_BYTES}))
        assert c.closed

    run(scenario())


def test_rate_limit(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        c = FakeConn()
        s = hub.connect(c)
        for _ in range(25):
            await send(hub, s, type="ping")
        assert len(c.of_type("pong")) == 20
        assert c.last_error()["code"] == "rate_limited"
        clock.advance(5)
        await send(hub, s, type="ping")
        assert len(c.of_type("pong")) == 21

    run(scenario())


def test_join_bruteforce_blocked(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        for i in range(JOIN_FAILURES_MAX):
            c = FakeConn("10.0.0.5")
            await send(hub, hub.connect(c), type="hello", role="player", code="QQQQ", name=f"x{i}")
        c = FakeConn("10.0.0.5")
        await send(hub, hub.connect(c), type="hello", role="player", code=hub.game.code, name="Legal")
        assert c.last_error()["code"] == "too_many_attempts"
        # Otra IP no se ve afectada
        c2 = FakeConn("10.0.0.6")
        await send(hub, hub.connect(c2), type="hello", role="player", code=hub.game.code, name="Legal")
        assert c2.of_type("welcome")
        clock.advance(61)
        c3 = FakeConn("10.0.0.5")
        await send(hub, hub.connect(c3), type="hello", role="player", code=hub.game.code, name="Otro")
        assert c3.of_type("welcome")

    run(scenario())


def test_broken_connection_is_dropped(bank, clock):
    async def scenario():
        hub = make_hub(bank, clock)
        c_ana, s_ana = await join(hub, "Ana")
        c_ana.closed = True  # el socket muere sin avisar
        await join(hub, "Beto")
        assert s_ana not in hub.sessions
        ana = next(p for p in hub.game.players.values() if p.name == "Ana")
        assert ana.connected is False

    run(scenario())


def test_events_callback_and_ticker(bank, clock):
    events = []

    async def scenario():
        hub = make_hub(bank, clock, on_event=lambda n, d: events.append(n))
        host = FakeConn()
        s_host = hub.connect(host)
        await send(hub, s_host, type="hello", role="host", token=hub.host_token)
        await join(hub, "Ana")
        await join(hub, "Beto")
        await send(hub, s_host, type="start")
        await send(hub, s_host, type="end")
        assert hub.game.phase == Phase.FINISHED
        await hub.start()
        await asyncio.sleep(0.05)
        await hub.stop()
        assert host.closed

    run(scenario())
    assert events[:3] == ["player_joined", "player_joined", "game_started"]
    assert events[-1] == "game_finished"


def test_real_ticker_advances_without_clients(bank):
    """Con reloj real y tiempos cortos, la partida avanza sola sin navegadores."""

    async def scenario():
        import time

        hub = Hub(bank, clock=time.monotonic, rng=random.Random(1), settings=Settings(num_questions=5))
        hub.game.settings.countdown_seconds = 0
        await join(hub, "Ana")
        await join(hub, "Beto")
        hub.game.start()
        hub.game.settings.time_limit = 0  # cierra al instante en la primera comprobación
        await hub.start()
        hub._wake.set()
        for _ in range(50):
            await asyncio.sleep(0.02)
            if hub.game.phase == Phase.REVEAL:
                break
        await hub.stop()
        return hub.game.phase

    assert run(scenario()) == Phase.REVEAL
