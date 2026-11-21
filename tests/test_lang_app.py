"""The Languages page and its API routes (no model, pinned clock, tmp data, no voice)."""
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import lang_engine as engine
from tools import langlesson

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def client(monkeypatch):
    from audio import tts
    from core import appserver

    def down(prompt):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(langlesson, "MODEL", down)
    monkeypatch.setattr(engine, "clock", lambda: datetime(2026, 3, 2, 10, 0, 0))
    monkeypatch.setattr(tts, "_kokoro_cached", lambda voice: False)
    for box in (langlesson._sessions, langlesson._last_lang):
        box.clear()
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    server = appserver.AppServer(lambda uid: None)
    yield TestClient(server.app)
    for box in (langlesson._sessions, langlesson._last_lang):
        box.clear()


def pair(client, uid="owner"):
    from tools import voice

    voice.pair_codes["222222"] = uid
    r = client.post("/api/pair", json={"code": "222222", "device": "Phone"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_page_uses_the_design_system_and_is_linked_and_cached():
    page = (APP / "languages.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/design-system.css">' in page
    assert not re.search(r"#[0-9a-fA-F]{6}\b", re.sub(r'<meta name="theme-color"[^>]*>', "", page).split("<script>")[0]), "colours come from the design tokens"
    assert "ds-empty" in page and "ds-spinner" in page and "ds-skeleton" in page          # empty and loading states
    for wanted in ("Due reviews", "Progress per unit", "Words to practise", "Continue lesson"):
        assert wanted in page
    assert "/languages.html" in (APP / "index.html").read_text(encoding="utf-8")
    sw = (APP / "sw.js").read_text(encoding="utf-8")
    assert '"/languages.html"' in sw and '"/study.html"' in sw and "jarvis-v33-study" not in sw


def test_the_page_is_served_and_the_api_needs_a_paired_device(client):
    assert client.get("/languages.html").status_code == 200
    assert client.get("/api/languages").status_code == 401
    assert client.post("/api/languages/lesson/start", json={"language": "es"}).status_code == 401


def test_overview_lists_the_five_languages_with_their_voices(client):
    data = client.get("/api/languages", headers=pair(client)).json()
    assert [l["lang"] for l in data["languages"]] == ["fr", "es", "zh", "ja", "ht"]
    assert all(l["level"] == "Not started" and len(l["units"]) == 8 for l in data["languages"])
    assert data["lesson"] is None and set(data["voices"]) == {"fr", "es", "zh", "ja", "ht"}
    assert data["voices"]["fr"]["local"] is True and data["voices"]["ja"]["local"] is False


def test_a_page_lesson_from_start_to_progress(client):
    auth = pair(client)
    out = client.post("/api/languages/lesson/start", headers=auth, json={"language": "ja", "minutes": 5}).json()
    sid = out["session"]
    assert out["lang"] == "ja" and out["phase"] == "new" and not out["done"]
    target = [p for p in out["parts"] if isinstance(p, dict)][0]
    assert target["text"] == "こんにちは" and target["roman"] == "konnichiwa" and target["slow"] is True
    assert client.get(f"/api/languages?session={sid}", headers=auth).json()["lesson"]["lang"] == "ja"       # a reload finds it again
    wrong = client.post("/api/languages/lesson/answer", headers=auth, json={"session": sid, "answer": "sayonara"}).json()
    assert wrong["correct"] is False and wrong["retry"] is True
    right = client.post("/api/languages/lesson/answer", headers=auth, json={"session": sid, "answer": "konnichiwa"}).json()
    assert right["correct"] is True and right["step"] == 2
    slow = client.post("/api/languages/lesson/repeat", headers=auth, json={"session": sid, "slower": True}).json()
    assert all(p["slow"] for p in slow["parts"] if isinstance(p, dict))
    assert "Skipped." in client.post("/api/languages/lesson/skip", headers=auth, json={"session": sid}).json()["text"]
    end = client.post("/api/languages/lesson/stop", headers=auth, json={"session": sid}).json()
    assert end["done"] and "That's the lesson" in end["text"]
    ja = next(l for l in client.get("/api/languages", headers=auth).json()["languages"] if l["lang"] == "ja")
    assert ja["learned"] == 1 and ja["lessons"] == 1 and ja["level"] == "Beginner, unit 1 of 8"
    assert client.post("/api/languages/lesson/answer", headers=auth, json={"session": sid, "answer": "x"}).status_code == 400
    assert client.post("/api/languages/lesson/dance", headers=auth, json={"session": sid}).status_code == 404


def test_a_page_lesson_never_captures_voice_and_bad_input_is_a_400(client):
    from core import users
    auth = pair(client)
    client.post("/api/languages/lesson/start", headers=auth, json={"language": "Spanish"})
    with users.acting_as("owner"):
        assert langlesson.on_the_table() is None
    assert client.post("/api/languages/lesson/start", headers=auth, json={"language": "Klingon"}).status_code == 400
    assert client.post("/api/languages/lesson/start", headers=auth, json={"language": "es", "minutes": "soon"}).status_code == 400


def test_phrase_lookup_from_the_page(client):
    auth = pair(client)
    out = client.post("/api/languages/phrase", headers=auth, json={"phrase": "thank you", "language": "ht"}).json()
    assert out["found"] and "Mèsi" in out["text"]
    out = client.post("/api/languages/phrase", headers=auth, json={"phrase": "gracias", "language": "es", "to": "english"}).json()
    assert "thank you" in out["text"]
