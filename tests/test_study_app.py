"""The Study page and its API routes (fake brain, no model, tmp data)."""
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import study_engine as engine
from core import users
from tools import study

APP = Path(__file__).resolve().parent.parent / "app"
NOTES = """# Chapter 1: Hardware

RAM is a kind of volatile working memory. A CPU is the chip that executes instructions. A motherboard is the board that connects every part. A GPU is a processor for graphics.

# Chapter 2: Switching

A switch is a device that forwards frames using the MAC address table. A hub is a device that repeats every signal to all ports. A VLAN is a logical broadcast domain on a switch. A trunk is a link that carries several VLANs.
"""


@pytest.fixture
def client(monkeypatch):
    from core import appserver

    def down(prompt):
        raise RuntimeError("no model in tests")
    monkeypatch.setattr(study, "MODEL", down)
    monkeypatch.setattr(engine, "clock", lambda: datetime(2026, 3, 2, 10, 0, 0))
    for state in (study._sessions, study._explained, study._follow):
        state.clear()
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    server = appserver.AppServer(lambda uid: None)
    yield TestClient(server.app)
    for state in (study._sessions, study._explained, study._follow):
        state.clear()


def pair(client, uid="owner"):
    from tools import voice

    voice.pair_codes["222222"] = uid
    r = client.post("/api/pair", json={"code": "222222", "device": "Phone"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_page_uses_the_design_system_and_is_linked_and_cached():
    page = (APP / "study.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/design-system.css">' in page
    assert not re.search(r"#[0-9a-fA-F]{6}\b", re.sub(r'<meta name="theme-color"[^>]*>', "", page).split("<script>")[0]
                         .replace("#04160D", "").replace("#1D0508", "")), "colours come from the design tokens"
    assert "ds-empty" in page and "ds-spinner" in page and "ds-skeleton" in page          # empty and loading states
    assert "/study.html" in (APP / "index.html").read_text(encoding="utf-8")
    sw = (APP / "sw.js").read_text(encoding="utf-8")
    assert '"/study.html"' in sw and "jarvis-v32-design" not in sw


def test_the_page_is_served_and_the_api_needs_a_paired_device(client):
    assert client.get("/study.html").status_code == 200
    assert client.get("/api/study").status_code == 401
    assert client.post("/api/study/quiz", json={}).status_code == 401


def test_overview_starts_with_the_builtin_course_then_shows_uploaded_notes(client):
    h = pair(client)
    data = client.get("/api/study", headers=h).json()
    assert [c["id"] for c in data["courses"]] == ["it-fundamentals"] and data["due"] == 0
    assert data["courses"][0]["chapter_names"][0] == "Hardware" and data["courses"][0]["progress"]["mastery"] == 0
    r = client.post("/api/study/material", headers=h, data={"course": "Net 101", "title": "Week 1"},
                    files={"file": ("week1.md", NOTES.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"]["chapters"] == ["Chapter 1: Hardware", "Chapter 2: Switching"] and body["data"]["courses"][0]["title"] == "Net 101"
    pasted = client.post("/api/study/material", headers=h, data={"course": "Net 101", "text": "A router is a device that joins networks together by address."})
    assert pasted.status_code == 200 and len(pasted.json()["data"]["courses"][0]["materials"]) == 2
    assert client.post("/api/study/material", headers=h, data={"course": "Net 101"}).status_code == 400
    bad = client.post("/api/study/material", headers=h, data={"course": "Net 101"}, files={"file": ("deck.pptx", b"PK\x03\x04", "application/zip")})
    assert bad.status_code == 400 and "PDF or text" in bad.json()["detail"]
    gone = client.delete(f"/api/study/material/{body['added']['id']}", headers=h)
    assert gone.status_code == 200 and len(gone.json()["data"]["courses"][0]["materials"]) == 1
    assert client.delete("/api/study/material/nope", headers=h).status_code == 404


def test_a_quiz_over_the_api_one_question_at_a_time_with_the_cited_source(client):
    h = pair(client)
    client.post("/api/study/material", headers=h, data={"course": "Net 101", "title": "Week 1", "text": NOTES})
    start = client.post("/api/study/quiz", headers=h, json={"course": "net-101", "chapter": "Chapter 2: Switching", "count": 2, "kind": "mcq"}).json()
    q = start["question"]
    assert (q["n"], q["of"], len(q["options"])) == (1, 2, 4) and "answer" not in q and "answer_index" not in q
    sid = start["session"]
    live = study._sessions[("owner", "page:" + sid)]
    right = live["q"]["answer_index"]
    assert client.post("/api/study/answer", headers=h, json={"answer": "ABCD"[right]}).status_code == 400      # no session id: no quiz there
    out = client.post("/api/study/answer", headers=h, json={"answer": "ABCD"[right], "id": q["id"], "session": sid}).json()
    again = client.post("/api/study/answer", headers=h, json={"answer": "ABCD"[right], "id": q["id"], "session": sid})
    assert again.status_code == 400 and "already answered" in again.json()["detail"]                           # the double tap
    assert out["result"]["correct"] and out["result"]["cite"] == "Week 1, Chapter 2: Switching" and out["question"]["n"] == 2
    assert out["result"]["quote"] and out["result"]["picked"] == right
    wrong = (live["q"]["answer_index"] + 1) % 4
    end = client.post("/api/study/answer", headers=h, json={"answer": "ABCD"[wrong], "session": sid}).json()
    assert not end["result"]["correct"] and end["summary"]["right"] == 1 and end["summary"]["of"] == 2 and len(end["summary"]["missed"]) == 1
    assert client.post("/api/study/answer", headers=h, json={"answer": "A", "session": sid}).status_code == 400
    assert [e["correct"] for e in engine.events() if e["type"] == "answer"] == [True, False]                   # graded once each
    progress = client.get("/api/study", headers=h).json()["courses"][0]["progress"]
    assert progress["answers"] == 2 and progress["right"] == 1 and len(progress["weak"]) == 1
    assert client.post("/api/study/quiz", headers=h, json={"course": "astronomy"}).status_code == 400
    assert client.post("/api/study/quiz", headers=h, json={"course": "net-101", "count": "lots"}).status_code == 400
    assert client.post("/api/study/cards", headers=h, json={"course": "net-101", "count": "many"}).status_code == 400
    explained = client.post("/api/study/explain", headers=h, json={"topic": "", "session": sid}).json()
    assert explained["source"] == "passage" and explained["cite"].startswith("Week 1")     # the question just answered


def test_cards_are_made_reviewed_and_rescheduled_over_the_api(client):
    h = pair(client)
    made = client.post("/api/study/cards", headers=h, json={"course": "it-fundamentals", "chapter": "Security", "count": 3}).json()
    assert len(made["made"]) == 3 and len(made["due"]) == 3
    due = client.get("/api/study/cards?course=it-fundamentals", headers=h).json()["due"]
    card = client.post("/api/study/cards/review", headers=h, json={"id": due[0]["id"], "rating": "good"}).json()["card"]
    assert card["due"] == "2026-03-03" and card["reps"] == 1
    assert len(client.get("/api/study/cards", headers=h).json()["due"]) == 2
    assert client.post("/api/study/cards/review", headers=h, json={"id": due[1]["id"], "rating": "perfect"}).status_code == 400
    assert client.post("/api/study/cards/review", headers=h, json={"id": "nope", "rating": "good"}).status_code == 400
    assert client.get("/api/study", headers=h).json()["due"] == 2


def test_the_plan_saves_the_exam_date_on_his_course_and_explain_reads_the_material(client):
    h = pair(client)
    client.post("/api/study/material", headers=h, data={"course": "Net 101", "title": "Week 1", "text": NOTES})
    plan = client.post("/api/study/plan", headers=h, json={"course": "net-101", "exam_date": "2026-03-09"}).json()
    assert plan["days_left"] == 7 and plan["days"][-1]["tasks"][0].startswith("Practice exam")
    assert client.get("/api/study", headers=h).json()["courses"][0]["exam_date"] == "2026-03-09"
    assert client.post("/api/study/plan", headers=h, json={"course": "net-101", "exam_date": "someday"}).status_code == 400
    x = client.post("/api/study/explain", headers=h, json={"topic": "DNS", "level": "elementary"}).json()
    assert x["topic"] == "DNS" and x["level"] == "elementary" and "DNS" in x["text"] and x["cite"] == "Concept outline, Networking, DNS"
    assert x["source"] == "builtin" and x["board"]["source"] == "hand" and [r["id"] for r in x["ladder"]][-1] == "one"
    assert client.post("/api/study/explain", headers=h, json={"topic": "zebra husbandry"}).status_code == 400


def test_each_person_has_their_own_courses_and_sessions(client):
    h = pair(client)
    client.post("/api/study/material", headers=h, data={"course": "Net 101", "text": NOTES})
    sid = client.post("/api/study/quiz", headers=h, json={"course": "net-101", "count": 1, "kind": "mcq"}).json()["session"]
    other = pair(client, users.add("Marie", {"en": "madame"}, "wife"))
    theirs = client.get("/api/study", headers=other).json()
    assert [c["id"] for c in theirs["courses"]] == ["it-fundamentals"]
    assert client.post("/api/study/answer", headers=other, json={"answer": "A", "session": sid}).status_code == 400


def test_a_quiz_on_the_page_is_invisible_to_voice_and_stops_by_its_own_id(client):
    h = pair(client)
    sid = client.post("/api/study/quiz", headers=h, json={"course": "it-fundamentals", "count": 2, "kind": "mcq"}).json()["session"]
    with users.acting_as("owner"):
        assert not study.awaiting_answer() and study.on_the_table() is None
    assert client.post("/api/study/stop", headers=h, json={"session": "other"}).json() == {"stopped": False}
    assert client.post("/api/study/stop", headers=h, json={"session": sid}).json()["stopped"] is True


def test_pasted_notes_have_a_size_limit(client):
    h = pair(client)
    r = client.post("/api/study/material", headers=h, data={"course": "Net 101", "text": "x" * (2 * 1024 * 1024 + 1)})
    assert r.status_code == 400                    # the form parser's own 1 MB field limit, or ours: never stored
    assert [c["id"] for c in client.get("/api/study", headers=h).json()["courses"]] == ["it-fundamentals"]
