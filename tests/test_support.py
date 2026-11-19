"""Pruebas de QR, red y banco de preguntas."""

from __future__ import annotations

import json

import pytest

from conftest import BANK_PATH
from game import netutil
from game.qr import QRCode, QRError, make_svg
from game.questions import load_all, load_bank_file, parse_bank


# ---------------------------------------------------------------- QR
def _decode(qr: QRCode) -> str:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    scale, border = 8, 4
    n = qr.size + border * 2
    img = np.full((n * scale, n * scale), 255, np.uint8)
    for y in range(qr.size):
        for x in range(qr.size):
            if qr.modules[y][x]:
                img[(y + border) * scale : (y + border + 1) * scale, (x + border) * scale : (x + border + 1) * scale] = 0
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return text


@pytest.mark.parametrize(
    "text",
    [
        "http://192.168.1.50:8123/quiz_salon/play?code=ABCD",
        "http://homeassistant.local:8123/quiz_salon/play?code=WXYZ",
        "https://[fd00::1]:8123/quiz_salon/play?code=QRST",
        "x" * 150,  # fuerza versión >= 7 (bloques de versión)
        "Ñandú 🦊",
    ],
)
def test_qr_roundtrip_with_independent_decoder(text):
    qr = QRCode(text)
    assert _decode(qr) == text


def test_qr_too_long():
    with pytest.raises(QRError):
        QRCode("x" * 400)


def test_qr_svg():
    svg = make_svg("http://a/quiz_salon/play?code=ABCD")
    assert svg.startswith("<svg") and "path" in svg and "<script" not in svg


# ---------------------------------------------------------------- red
@pytest.mark.parametrize(
    "ip,expected",
    [
        ("192.168.1.10", True),
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("fe80::1", True),
        ("fd12:3456::1", True),
        ("::ffff:192.168.1.5", True),
        ("8.8.8.8", False),
        ("2001:4860::8888", False),
        ("::ffff:8.8.8.8", False),
        ("100.64.0.1", False),  # CGNAT: no se considera red doméstica
        ("", False),
        (None, False),
        ("nonsense", False),
    ],
)
def test_is_private_address(ip, expected):
    assert netutil.is_private_address(ip) is expected


def test_normalize_base_url():
    assert netutil.normalize_base_url("http://192.168.1.5:8123/") == "http://192.168.1.5:8123"
    assert netutil.normalize_base_url("https://ha.local/lovelace/0") == "https://ha.local"
    assert netutil.normalize_base_url("http://[fd00::1]:8123") == "http://[fd00::1]:8123"
    for bad in ("ftp://x", "javascript:alert(1)", "http://user:pw@x", "", None, "http://x:99999"):
        assert netutil.normalize_base_url(bad) is None


def test_loopback_detection():
    assert netutil.is_loopback_base("http://localhost:8123")
    assert netutil.is_loopback_base("http://127.0.0.1:8123")
    assert not netutil.is_loopback_base("http://192.168.1.2:8123")


def test_validate_join_url():
    ok = "http://192.168.1.5:8123/quiz_salon/play?code=ABCD"
    assert netutil.validate_join_url(ok) == ok
    assert netutil.build_join_url("http://h:1", "ABCD") == "http://h:1/quiz_salon/play?code=ABCD"
    for bad in (
        "http://192.168.1.5:8123/other?code=ABCD",
        "http://192.168.1.5:8123/quiz_salon/play?code=abcd",
        "http://192.168.1.5:8123/quiz_salon/play?code=ABCD&x=1",
        "http://192.168.1.5:8123/quiz_salon/play?code=ABCD#frag",
        "javascript:alert(1)",
        "http://x/quiz_salon/play?code=" + "A" * 300,
        None,
    ):
        assert netutil.validate_join_url(bad) is None


# ---------------------------------------------------------------- banco
def test_builtin_bank_is_valid():
    bank = load_bank_file(BANK_PATH)
    assert bank.warnings == []
    assert len(bank.questions) >= 150
    counts = bank.category_counts()
    assert len(counts) == 8 and min(counts.values()) >= 20
    for q in bank.questions:
        assert len(set(o.casefold() for o in q.options)) == 4
        assert q.text.endswith("?") or q.text.endswith("?»")


def test_parse_bank_rejects_bad_entries():
    data = {
        "categories": [{"id": "c", "name": "C"}, {"id": "BAD ID", "name": "x"}],
        "questions": [
            {"id": "ok-1", "category": "c", "q": "¿Bien?", "options": ["a", "b", "c", "d"], "answer": 2},
            {"id": "ok-1", "category": "c", "q": "¿Dup?", "options": ["a", "b", "c", "d"], "answer": 0},
            {"id": "x2", "category": "zz", "q": "¿Cat?", "options": ["a", "b", "c", "d"], "answer": 0},
            {"id": "x3", "category": "c", "q": "", "options": ["a", "b", "c", "d"], "answer": 0},
            {"id": "x4", "category": "c", "q": "¿Tres?", "options": ["a", "b", "c"], "answer": 0},
            {"id": "x5", "category": "c", "q": "¿Rep?", "options": ["a", "A", "c", "d"], "answer": 0},
            {"id": "x6", "category": "c", "q": "¿Idx?", "options": ["a", "b", "c", "d"], "answer": 4},
            {"id": "x7", "category": "c", "q": "¿Bool?", "options": ["a", "b", "c", "d"], "answer": True},
            {"id": "x8", "category": "c", "q": "x" * 300, "options": ["a", "b", "c", "d"], "answer": 0},
            "no-dict",
        ],
    }
    bank = parse_bank(data, "test")
    assert [q.id for q in bank.questions] == ["ok-1"]
    assert bank.questions[0].answer == 2
    assert len(bank.warnings) == 10


def test_custom_packs_are_merged(tmp_path):
    pack = {
        "categories": [{"id": "familia", "name": "Mi familia", "icon": "🏠"}],
        "questions": [
            {"id": "fam-1", "category": "familia", "q": "¿Cómo se llama el gato?", "options": ["Misi", "Tom", "Leo", "Bigotes"], "answer": 0},
            {"id": "fam-2", "category": "geografia", "q": "¿Capital de Islandia?", "options": ["Reikiavik", "Oslo", "Bergen", "Nuuk"], "answer": 0},
            {"id": "geo-001", "category": "geografia", "q": "¿Duplicada?", "options": ["a", "b", "c", "d"], "answer": 0},
        ],
    }
    (tmp_path / "familia.json").write_text(json.dumps(pack), encoding="utf-8")
    (tmp_path / "roto.json").write_text("{no es json", encoding="utf-8")
    bank = load_all(BANK_PATH, tmp_path)
    ids = {q.id for q in bank.questions}
    assert {"fam-1", "fam-2"} <= ids
    assert "familia" in bank.categories
    assert any("duplicada" in w.lower() for w in bank.warnings)
    assert any("roto.json" in w for w in bank.warnings)
