"""The Partner page (app/partner.html, GET /api/partner, POST /api/partner/goal over core/partner_page + core/commitments).

Rules tested: a paired device is required; each person sees and changes only their own goals; every action (add, activate,
drop, done, undo, next_step) goes through the commitments writers into THIS machine's file only; a proposed goal becomes
active only on the explicit tap; bad input is a 4xx, never a 500 and never a write; reading the page marks nothing
reported; the last report said is shown from whichever machine said it. Sandboxed storage (tests/conftest.py)."""
import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core import commitments as C
from core import storage, users

MORNING = datetime(2026, 11, 22, 8, 30)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MISSIONS_DIR", str(tmp_path / "missions"))
    monkeypatch.setattr(C, "_machine", lambda: "server")          # the laptop: the phone's server
    monkeypatch.setattr(C, "_retry_after", {})
    from core import habits
    monkeypatch.setattr(habits, "_turns_since", lambda t: False)
    users.set_current("owner")
    yield
    users.set_current("owner")


@pytest.fixture
def client(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(appserver.AppServer(lambda uid: None).app)


def pair(client, uid="owner", device="claude-verify-phone"):
    from tools import voice

    voice.pair_codes["424242"] = uid
    r = client.post("/api/pair", json={"code": "424242", "device": device})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def act(client, h, **body):
    return client.post("/api/partner/goal", json=body, headers=h)


def goal_file(uid, machine):
    path = storage.user_dir(uid) / f"goals-{machine}.json"
    return json.loads(path.read_text(encoding="utf-8"))["goals"] if path.exists() else {}


# -- auth ----------------------------------------------------------------------------------------------------------------
def test_both_routes_need_a_paired_device(client):
    assert client.get("/api/partner").status_code == 401
    assert client.get("/api/partner", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert act(client, {}, action="add", text="learn to swim").status_code == 401
    assert client.post("/api/partner/goal", json={"action": "add", "text": "x y z"},
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    with users.acting_as("owner"):
        assert C.goals() == []


def test_the_page_is_served_and_linked_from_the_app_and_the_hub(client):
    from pathlib import Path
    app = Path(__file__).resolve().parent.parent / "app"
    assert client.get("/partner.html").status_code == 200
    assert "/partner.html" in (app / "index.html").read_text(encoding="utf-8")
    assert '"partner.html"' in (app / "command-center.js").read_text(encoding="utf-8")
    assert 'data-go="partner"' in (app / "command-center.html").read_text(encoding="utf-8")
    page = (app / "partner.html").read_text(encoding="utf-8")
    assert "/api/partner" in page and "Yes, that's a goal" in page and "Not a goal" in page
    assert "http://" not in page and "https://" not in page          # no external CDN


# -- each action ---------------------------------------------------------------------------------------------------------
def test_add_then_next_step_done_and_undo(client):
    h = pair(client)
    r = act(client, h, action="add", text="  Get the insurance   website live ", next_step="call the designer")
    assert r.status_code == 200 and r.json()["message"] == "Got it. New goal: Get the insurance website live."
    (g,) = r.json()["data"]["goals"]["active"]
    assert g["goal"] == "Get the insurance website live" and g["next_step"] == "call the designer" and g["source"] == "app"

    r = act(client, h, action="next_step", id=g["id"], next_step="send the logo")
    assert r.status_code == 200 and r.json()["data"]["goals"]["active"][0]["next_step"] == "send the logo"
    r = act(client, h, action="next_step", id=g["id"], next_step="")
    assert r.status_code == 200 and r.json()["message"].startswith("Cleared") and r.json()["data"]["goals"]["active"][0]["next_step"] == ""

    r = act(client, h, action="done", id=g["id"])
    data = r.json()["data"]["goals"]
    assert r.status_code == 200 and data["active"] == [] and data["closed"][0]["state"] == "done"
    r = act(client, h, action="undo", id=g["id"])
    assert r.status_code == 200 and [x["id"] for x in r.json()["data"]["goals"]["active"]] == [g["id"]]


def test_a_proposal_becomes_active_only_on_his_tap_and_not_a_goal_can_be_undone(client):
    h = pair(client)
    with users.acting_as("owner"):
        p = C.add_goal("Learn Haitian Creole", state="proposed", source="seed", gid="seed-creole", aliases=["creole"])
        q = C.add_goal("Finish the IT degree", state="proposed", source="seed", gid="seed-degree", aliases=["degree"])
    data = client.get("/api/partner", headers=h).json()
    assert {g["id"] for g in data["goals"]["proposed"]} == {p["id"], q["id"]} and data["goals"]["active"] == []
    assert data["goals"]["active"] == []                               # reading the page approves nothing

    r = act(client, h, action="activate", id=p["id"])
    assert r.status_code == 200 and [g["id"] for g in r.json()["data"]["goals"]["active"]] == [p["id"]]
    assert act(client, h, action="activate", id=p["id"]).status_code == 409        # already active

    r = act(client, h, action="drop", id=q["id"])
    assert r.status_code == 200 and "isn't a goal" in r.json()["message"]
    r = act(client, h, action="undo", id=q["id"])
    assert [g["id"] for g in r.json()["data"]["goals"]["proposed"]] == [q["id"]]     # back as a suggestion, not active


def test_adding_a_goal_that_is_already_suggested_activates_that_one(client):
    h = pair(client)
    with users.acting_as("owner"):
        C.add_goal("Learn Haitian Creole", state="proposed", source="seed", gid="seed-creole", aliases=["creole"])
    r = act(client, h, action="add", text="Creole")
    assert r.status_code == 200 and "real goal now" in r.json()["message"]
    assert [g["id"] for g in r.json()["data"]["goals"]["active"]] == ["seed-creole"]
    r = act(client, h, action="add", text="Creole")
    assert "already one of your goals" in r.json()["message"] and len(r.json()["data"]["goals"]["active"]) == 1


def test_writes_go_only_into_this_machines_file_and_the_other_machines_record_is_copied(client):
    h = pair(client)
    with users.acting_as("owner"):
        C._machine = lambda: "pc"                                           # made on the PC ...
        pc = C.add_goal("Run a 5k", state="active")
    C._machine = lambda: "server"                                           # ... changed from the phone (laptop)
    before = goal_file("owner", "pc")
    r = act(client, h, action="next_step", id=pc["id"], next_step="buy shoes")
    assert r.status_code == 200
    assert goal_file("owner", "pc") == before                               # the PC's file untouched
    assert goal_file("owner", "server")[pc["id"]]["next_step"] == "buy shoes"
    assert client.get("/api/partner", headers=h).json()["goals"]["active"][0]["next_step"] == "buy shoes"


# -- per person ---------------------------------------------------------------------------------------------------------
def test_each_person_sees_and_changes_only_their_own_goals(client):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    owner_h, wife_h = pair(client, "owner"), pair(client, wife, "claude-verify-wife")
    gid = act(client, owner_h, action="add", text="Grow the insurance business").json()["data"]["goals"]["active"][0]["id"]
    act(client, wife_h, action="add", text="Glutes plan three days a week")

    mine = client.get("/api/partner", headers=owner_h).json()["goals"]["active"]
    hers = client.get("/api/partner", headers=wife_h).json()["goals"]["active"]
    assert [g["goal"] for g in mine] == ["Grow the insurance business"]
    assert [g["goal"] for g in hers] == ["Glutes plan three days a week"]
    for action in ("drop", "done", "next_step", "activate", "undo"):
        r = act(client, wife_h, action=action, id=gid, next_step="hijack")
        assert r.status_code == 404, action                                # his goal does not exist for her
    with users.acting_as("owner"):
        (g,) = C.goals()
        assert g["state"] == "active" and g["next_step"] == ""
    assert not (storage.user_dir(wife) / "goals-server.json").read_text(encoding="utf-8").count(gid)


# -- bad input ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("body,status", [
    ({"action": "explode"}, 400),
    ({}, 400),
    ({"action": "add"}, 400),
    ({"action": "add", "text": " "}, 400),
    ({"action": "add", "text": 42}, 400),
    ({"action": "add", "text": ["a", "b"]}, 400),
    ({"action": "add", "text": "x" * 201}, 400),
    ({"action": "add", "text": "learn to swim", "next_step": {"a": 1}}, 400),
    ({"action": "drop"}, 400),
    ({"action": "drop", "id": 7}, 400),
    ({"action": "drop", "id": "nope"}, 404),
    ({"action": "next_step", "id": "nope", "next_step": "x"}, 404),
])
def test_bad_input_is_refused_and_writes_nothing(client, body, status):
    h = pair(client)
    r = client.post("/api/partner/goal", json=body, headers=h)
    assert r.status_code == status, r.text
    assert isinstance(r.json().get("detail"), str)
    with users.acting_as("owner"):
        assert C.goals() == []


@pytest.mark.parametrize("raw", ["[1, 2]", "\"add\"", "null", "not json"])
def test_a_body_that_is_not_an_object_is_a_4xx(client, raw):
    h = {**pair(client), "Content-Type": "application/json"}
    r = client.post("/api/partner/goal", content=raw, headers=h)
    assert 400 <= r.status_code < 500


def test_wrong_state_transitions_are_409(client):
    h = pair(client)
    gid = act(client, h, action="add", text="Read twelve books").json()["data"]["goals"]["active"][0]["id"]
    assert act(client, h, action="undo", id=gid).status_code == 409                 # nothing to undo
    assert act(client, h, action="activate", id=gid).status_code == 409              # not a proposal
    act(client, h, action="drop", id=gid)
    assert act(client, h, action="done", id=gid).status_code == 409
    assert act(client, h, action="drop", id=gid).status_code == 409
    assert act(client, h, action="next_step", id=gid, next_step="x y").status_code == 409


# -- commitments and the report -----------------------------------------------------------------------------------------
def test_owed_shows_open_ones_with_age_and_recent_finished_ones(client):
    h = pair(client)
    with users.acting_as("owner"):
        old = C.record("tidy my downloads folder", cid="c-open")
        C._set(old, created=time.time() - 3 * 3600)
        done = C.record("book the dentist", cid="c-done")
        C._set(done, state="done", outcome="done", resolved=time.time() - 60)
        gone = C.record("back up the photos", cid="c-old")
        C._set(gone, state="reported", outcome="failed", resolved=time.time() - 30 * 86400)
    data = client.get("/api/partner", headers=h).json()
    (o,) = data["owed"]["open"]
    assert o["id"] == "c-open" and o["state"] == "open" and data["now"] - o["created"] > 3 * 3600 - 5
    assert o["overdue"] is False
    assert [c["id"] for c in data["owed"]["recent"]] == ["c-done"]


def test_reading_the_page_marks_nothing_reported_and_the_said_report_is_shown(client, monkeypatch):
    h = pair(client)
    with users.acting_as("owner"):
        C.add_goal("Run a 5k", state="active", next_step="buy shoes")
        c = C.record("book the dentist", cid="c1")
        C._set(c, state="done", outcome="done", resolved=time.time())
    data = client.get("/api/partner", headers=h).json()
    assert "Done since last time: book the dentist" in data["report"]["now"]
    assert data["report"]["said"] is None
    with users.acting_as("owner"):
        assert C.items(("done",))[0]["id"] == "c1"                         # still not 'reported'
        assert C._partner().get("report_day") is None and "proposals_told" not in C._partner()
        C._machine = lambda: "pc"                                           # the PC says it ...
        said = C.morning_report(MORNING)
        C._machine = lambda: "server"
    data = client.get("/api/partner", headers=h).json()                     # ... the phone (laptop) shows it
    assert data["report"]["said"]["text"] == said and data["report"]["said"]["machine"] == "pc"
    assert "book the dentist" not in data["report"]["now"]                  # reported once, not again


def test_last_report_ignores_bad_rows():
    storage.save("partner-pc.json", {"last_report": {"text": "", "at": 5}})
    storage.save("partner-server.json", {"last_report": {"text": "hi", "at": "soon"}})
    assert C.last_report() is None
    storage.save("partner-server.json", {"last_report": {"text": "Your morning report.", "at": 10}})
    assert C.last_report() == {"text": "Your morning report.", "at": 10.0, "machine": "server"}


def test_a_shorter_or_looser_typed_goal_is_a_new_goal_not_a_match(client):
    h = pair(client)
    with users.acting_as("owner"):
        C.add_goal("Save money for a house", state="active")
        C.add_goal("Learn Spanish for the Haiti trip", state="proposed", source="seed", gid="seed-spanish")
    r = act(client, h, action="add", text="Save money")
    assert r.status_code == 200 and r.json()["message"] == "Got it. New goal: Save money."
    r = act(client, h, action="add", text="Haiti")
    goals = r.json()["data"]["goals"]
    assert [g["id"] for g in goals["proposed"]] == ["seed-spanish"]               # the keyword did not approve it
    assert sorted(g["goal"] for g in goals["active"]) == ["Haiti", "Save money", "Save money for a house"]
    r = act(client, h, action="add", text="save money for a HOUSE")
    assert "already one of your goals" in r.json()["message"]
