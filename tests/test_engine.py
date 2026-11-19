"""Pruebas del motor: transiciones, reglas, puntuación y casos límite."""

from __future__ import annotations

import random

import pytest

from game.engine import (
    MAX_PLAYERS,
    Game,
    GameError,
    Phase,
    Settings,
    compute_points,
)


def two_players(game: Game):
    a = game.add_player("Ana")
    b = game.add_player("Beto")
    return a, b


def to_question(game: Game, clock) -> None:
    """Avanza la cuenta atrás hasta la fase de pregunta."""
    assert game.phase == Phase.COUNTDOWN
    clock.advance(game.settings.countdown_seconds)
    assert game.tick()
    assert game.phase == Phase.QUESTION


def correct_choice(game: Game) -> int:
    return game.current.correct


def wrong_choice(game: Game) -> int:
    return (game.current.correct + 1) % 4


# ---------------------------------------------------------------- puntuación
def test_compute_points_ranges():
    assert compute_points(20, 20, 1, False) == 1000
    assert compute_points(0, 20, 1, False) == 500
    assert compute_points(10, 20, 1, False) == 750
    assert compute_points(10, 20, 2, False) == 850
    assert compute_points(10, 20, 9, False) == 750 + 300  # tope de racha
    assert compute_points(10, 20, 1, True) == 1500


# ---------------------------------------------------------------- jugadores
def test_join_validation(game: Game):
    game.add_player("  Ana   María ")
    assert any(p.name == "Ana María" for p in game.players.values())
    for bad, code in [("", "name_empty"), ("   ", "name_empty"), ("x" * 17, "name_too_long"), (None, "name_invalid")]:
        with pytest.raises(GameError) as err:
            game.add_player(bad)
        assert err.value.code == code
    with pytest.raises(GameError) as err:
        game.add_player("ANA MARÍA")
    assert err.value.code == "name_taken"
    with pytest.raises(GameError) as err:
        game.add_player("ana maria")  # sin acento también cuenta como duplicado
    assert err.value.code == "name_taken"


def test_name_strips_control_and_keeps_special_chars(game: Game):
    p = game.add_player("<b>Zoë</b>\u0007")
    assert p.name == "<b>Zoë</b>"  # el escape se hace en el cliente; aquí solo se limpia
    p2 = game.add_player("José 🎉")
    assert p2.name == "José 🎉"


def test_room_full(game: Game):
    for i in range(MAX_PLAYERS):
        game.add_player(f"J{i}")
    with pytest.raises(GameError) as err:
        game.add_player("Otro")
    assert err.value.code == "room_full"


def test_unique_avatars(game: Game):
    players = [game.add_player(f"J{i}", "🦊") for i in range(5)]
    assert len({p.avatar for p in players}) == 5
    with pytest.raises(GameError):
        game.add_player("Raro", "<script>")


# ---------------------------------------------------------------- inicio
def test_start_requires_two_connected(game: Game):
    a = game.add_player("Ana")
    with pytest.raises(GameError) as err:
        game.start()
    assert err.value.code == "not_enough_players"
    b = game.add_player("Beto")
    game.set_connected(b.id, False)
    with pytest.raises(GameError):
        game.start()
    game.set_connected(b.id, True)
    game.start()
    assert game.phase == Phase.COUNTDOWN
    assert len(game.rounds) == 5
    assert game.rounds[-1].double and not any(r.double for r in game.rounds[:-1])
    assert a.id in game.players


def test_configure_only_in_lobby_and_clamps(game: Game):
    game.configure({"num_questions": 100, "time_limit": 3, "categories": ["geografia", "nope"]})
    assert game.settings.num_questions == 30
    assert game.settings.time_limit == 10
    assert game.settings.categories == ("geografia",)
    with pytest.raises(GameError):
        game.configure({"num_questions": "diez"})
    with pytest.raises(GameError):
        game.configure({"categories": ["nope"]})
    two_players(game)
    game.start()
    with pytest.raises(GameError) as err:
        game.configure({"num_questions": 5})
    assert err.value.code == "wrong_phase"


def test_selected_questions_respect_categories(bank, clock):
    g = Game(bank, clock, rng=random.Random(1), settings=Settings(num_questions=30, categories=("ciencia",)))
    two_players(g)
    g.start()
    assert {r.question.category for r in g.rounds} == {"ciencia"}
    assert len(g.rounds) == 20  # solo hay 20 de ciencia
    assert g.warnings  # avisa de que hay menos de las pedidas
    assert len({r.question.id for r in g.rounds}) == len(g.rounds)


def test_options_are_shuffled_and_correct_tracked(bank, clock):
    positions = set()
    for seed in range(20):
        g = Game(bank, clock, rng=random.Random(seed), settings=Settings(num_questions=5))
        two_players(g)
        g.start()
        for r in g.rounds:
            assert r.options[r.correct] == r.question.options[r.question.answer]
            positions.add(r.correct)
    assert positions == {0, 1, 2, 3}


# ---------------------------------------------------------------- ronda
def test_full_round_scoring_and_reveal(game: Game, clock):
    a, b = two_players(game)
    game.start()
    with pytest.raises(GameError) as err:
        game.answer(a.id, 0, 0)  # aún en cuenta atrás
    assert err.value.code == "wrong_phase"
    to_question(game, clock)
    clock.advance(5)
    game.answer(a.id, 0, correct_choice(game))
    # La puntuación no se revela hasta cerrar la pregunta
    assert a.score == 0
    view = game.view("player", a.id)
    assert "correct" not in view["question"]
    assert view["you"]["answer"] == correct_choice(game)
    clock.advance(5)
    game.answer(b.id, 0, wrong_choice(game))
    # Todos respondieron -> revelación inmediata
    assert game.phase == Phase.REVEAL
    assert a.score == compute_points(15, 20, 1, False) == 875
    assert b.score == 0 and b.streak == 0
    tv = game.view("tv")
    q = tv["question"]
    assert q["correct"] == correct_choice(game)
    assert sum(q["counts"]) == 2


def test_answer_rules(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    for args, code in [
        ((a.id, 1, 0), "stale_question"),
        ((a.id, "0", 0), "stale_question"),
        ((a.id, 0, 4), "invalid_choice"),
        ((a.id, 0, -1), "invalid_choice"),
        ((a.id, 0, True), "invalid_choice"),
        (("p999", 0, 0), "unknown_player"),
    ]:
        with pytest.raises(GameError) as err:
            game.answer(*args)
        assert err.value.code == code
    game.answer(a.id, 0, 0)
    with pytest.raises(GameError) as err:
        game.answer(a.id, 0, 1)
    assert err.value.code == "already_answered"
    assert a.answers[0].choice == 0


def test_timeout_closes_question(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    game.answer(a.id, 0, correct_choice(game))
    clock.advance(19.9)
    assert not game.tick()
    assert game.phase == Phase.QUESTION
    clock.advance(0.2)
    assert game.tick()
    assert game.phase == Phase.REVEAL
    assert b.streak == 0
    # Responder tras el cierre no está permitido
    with pytest.raises(GameError):
        game.answer(b.id, 0, 0)


def test_disconnected_player_does_not_block(game: Game, clock):
    a, b = two_players(game)
    c = game.add_player("Carla")
    game.start()
    to_question(game, clock)
    game.answer(a.id, 0, 0)
    game.answer(b.id, 0, 0)
    assert game.phase == Phase.QUESTION
    game.set_connected(c.id, False)
    assert game.phase == Phase.REVEAL


def test_all_disconnected_waits_for_timer(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    game.set_connected(a.id, False)
    game.set_connected(b.id, False)
    assert game.phase == Phase.QUESTION
    clock.advance(21)
    game.tick()
    assert game.phase == Phase.REVEAL


def test_reconnected_player_can_answer(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    game.set_connected(b.id, False)
    game.answer(a.id, 0, 0)
    assert game.phase == Phase.REVEAL  # b desconectado no bloquea


def test_auto_advance_and_finish_with_double(bank, clock):
    g = Game(bank, clock, rng=random.Random(3), settings=Settings(num_questions=5, time_limit=10))
    a, b = two_players(g)
    g.start()
    for i in range(5):
        to_question(g, clock)
        g.answer(a.id, i, g.current.correct)
        g.answer(b.id, i, (g.current.correct + 1) % 4)
        assert g.phase == Phase.REVEAL
        clock.advance(g.settings.reveal_seconds)
        g.tick()
    assert g.phase == Phase.FINISHED
    # 5 aciertos instantáneos: 1000, 1100, 1200, 1300, (1300)*2
    assert a.score == 1000 + 1100 + 1200 + 1300 + 2600
    assert a.best_streak == 5
    ranking = g.ranking()
    assert ranking[0] == (1, a) and ranking[1] == (2, b)
    events = [name for name, _ in g.pop_events()]
    assert events[0] == "player_joined" and events[-1] == "game_finished"


def test_manual_advance(bank, clock):
    g = Game(bank, clock, rng=random.Random(5), settings=Settings(num_questions=5, auto_advance=False))
    a, b = two_players(g)
    g.start()
    g.next()  # salta la cuenta atrás
    assert g.phase == Phase.QUESTION
    g.next()  # cierra la pregunta
    assert g.phase == Phase.REVEAL
    clock.advance(1000)
    assert not g.tick()  # sin avance automático
    assert g.phase == Phase.REVEAL
    g.next()
    assert g.phase == Phase.COUNTDOWN and g.index == 1


def test_pause_freezes_timer(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    clock.advance(5)
    game.pause()
    assert game.remaining() == pytest.approx(15)
    with pytest.raises(GameError) as err:
        game.answer(a.id, 0, 0)
    assert err.value.code == "paused"
    clock.advance(100)
    assert not game.tick()
    assert game.phase == Phase.QUESTION
    game.resume()
    assert game.remaining() == pytest.approx(15)
    clock.advance(10)
    game.answer(a.id, 0, correct_choice(game))
    game.answer(b.id, 0, correct_choice(game))
    assert a.score == compute_points(5, 20, 1, False)


def test_pause_wrong_phase(game: Game):
    with pytest.raises(GameError):
        game.pause()


def test_end_and_new_game(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    game.answer(a.id, 0, correct_choice(game))
    game.answer(b.id, 0, correct_choice(game))
    old_code = game.code
    game.set_connected(b.id, False)
    game.end()
    assert game.phase == Phase.FINISHED
    game.new_game(keep_players=True)
    assert game.phase == Phase.LOBBY
    assert list(game.players) == [a.id]  # el desconectado se descarta
    assert a.score == 0 and a.answers == {}
    assert game.code != old_code or len(game.code) == 4
    game.new_game(keep_players=False)
    assert game.players == {}


def test_kick_during_question_closes_if_rest_answered(game: Game, clock):
    a, b = two_players(game)
    game.start()
    to_question(game, clock)
    game.answer(a.id, 0, 0)
    game.remove_player(b.id)
    assert game.phase == Phase.REVEAL


def test_ranking_ties(game: Game, clock):
    a, b = two_players(game)
    c = game.add_player("Carla")
    a.score = b.score = 1000
    a.correct_count = b.correct_count = 1
    a.correct_time = b.correct_time = 3.0
    c.score = 1000
    c.correct_count = 1
    c.correct_time = 2.0  # más rápida
    ranking = game.ranking()
    assert ranking[0] == (1, c)
    assert {r for r, _ in ranking[1:]} == {2}


def test_correct_answer_never_leaks_before_reveal(game: Game, clock):
    a, b = two_players(game)
    game.start()
    for role, pid in (("tv", None), ("host", None), ("player", a.id)):
        assert game.view(role, pid)["question"].get("text") is None  # cuenta atrás: sin texto
    to_question(game, clock)
    for role, pid in (("tv", None), ("host", None), ("player", a.id)):
        q = game.view(role, pid)["question"]
        assert "correct" not in q and "counts" not in q and "results" not in q
        assert q["text"] and len(q["options"]) == 4


def test_tick_handles_long_stall(game: Game, clock):
    two_players(game)
    game.start()
    clock.advance(10_000)
    game.tick()
    # Avanza varias fases pero nunca entra en un bucle infinito
    assert game.phase in (Phase.COUNTDOWN, Phase.QUESTION, Phase.REVEAL, Phase.FINISHED)
