"""Jarvis Link server side (tools/pca_link + the /api/link routes): the queue, the long poll, the sleep hint, the reports,
who may take commands, the APK download. Sandboxed storage (tests/conftest.py), a pinned clock, no phone, no network."""
import ast
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import users
from tools import pca_link

T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def pinned(monkeypatch):
    box = {"now": T0, "machine": "server"}
    monkeypatch.setattr(pca_link, "clock", lambda: box["now"])
    monkeypatch.setattr(pca_link, "machine", lambda: box["machine"])
    return box


@pytest.fixture
def client(monkeypatch):
    from core import appserver

    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    server = appserver.AppServer(lambda uid: None)
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(server.app)


def pair(client, uid="owner", device="jarvis-link"):
    from tools import voice

    voice.pair_codes["222222"] = uid
    r = client.post("/api/pair", json={"code": "222222", "device": device})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---- the Python API ---------------------------------------------------------------------------------------------------
def test_send_poll_once_then_result(pinned):
    cid = pca_link.send("next")
    out = pca_link.take()
    assert [c["id"] for c in out["commands"]] == [cid] and out["commands"][0]["name"] == "next" and out["sleep"] == 0
    assert pca_link.take()["commands"] == []                               # delivered once
    assert pca_link.result(cid) is None
    pca_link.report_result({"id": cid, "ok": True, "detail": "skipped", "state": {"playing": True, "title": "Song"}})
    assert pca_link.wait_result(cid, timeout=0) == {"ok": True, "detail": "skipped", "state": pca_link.clean_state(
        {"playing": True, "title": "Song"})}
    assert pca_link.state()["title"] == "Song" and pca_link.connected()


def test_commands_expire_after_60_s(pinned):
    pca_link.send("pause")
    pinned["now"] += 61
    assert pca_link.take()["commands"] == []


def test_results_kept_five_minutes(pinned):
    cid = pca_link.send("play")
    pca_link.take()
    pca_link.report_result({"id": cid, "ok": False, "detail": "no session"})
    pinned["now"] += 299
    assert pca_link.result(cid)["detail"] == "no session"
    pca_link.take()                                                         # a later write prunes
    pinned["now"] += 2
    pca_link.report_state({"playing": False})
    assert pca_link.result(cid) is None


def test_result_for_unknown_id_is_refused(pinned):
    with pytest.raises(pca_link.LinkError):
        pca_link.report_result({"id": "nope", "ok": True})
    with pytest.raises(pca_link.LinkError):
        pca_link.report_result(["not", "a", "dict"])


def test_sleep_hint_follows_active(pinned):
    assert pca_link.take()["sleep"] == pca_link.IDLE_SLEEP == 45
    pca_link.active(600)
    assert pca_link.take()["sleep"] == 0
    pinned["now"] += 601
    assert pca_link.take()["sleep"] == 45
    pca_link.active(600)
    pca_link.active(0)
    assert pca_link.take()["sleep"] == 45
    with pytest.raises(pca_link.LinkError):
        pca_link.active(float("nan"))


def test_pc_queued_command_is_delivered_by_the_server(pinned):
    pinned["machine"] = "pc"
    cid = pca_link.send("toggle")
    assert Path(pca_link.storage.path("fitness/link-queue-pc.json")).exists()
    pinned["machine"] = "server"
    assert [c["id"] for c in pca_link.take()["commands"]] == [cid]
    assert not Path(pca_link.storage.path("fitness/link-queue-server.json")).exists() or \
        pca_link.storage.load("fitness/link-queue-server.json", {}).get("commands") == []
    pca_link.report_result({"id": cid, "ok": True, "detail": "paused"})
    pinned["machine"] = "pc"
    assert pca_link.result(cid)["detail"] == "paused"                   # the PC reads the server's synced file


@pytest.mark.parametrize("name,args", [
    ("dance", {}), ("next", {"x": 1}), ("seek", {}), ("seek", {"position_ms": float("nan")}),
    ("seek", {"position_ms": float("inf")}), ("seek", {"position_ms": -1}), ("seek", {"position_ms": "10"}),
    ("seek", {"position_ms": True}), ("play_id", {"video_id": "short"}), ("play_id", {"video_id": "a" * 11 + "x"}),
    ("play_id", {"video_id": "abc def ghi"}), ("play_id", {"video_id": "dQw4w9WgXcQ", "title": 5}),
    ("play_search", {"query": "   "}), ("play_search", {"query": None}),
])
def test_bad_arguments_are_refused_with_a_sentence(name, args):
    with pytest.raises(pca_link.LinkError) as e:
        pca_link.send(name, **args)
    assert str(e.value).endswith(".")


def test_good_arguments_are_cleaned():
    pca_link.send("seek", position_ms=61500.7)
    pca_link.send("play_id", video_id="dQw4w9WgXcQ", title="T" * 900, artist="Rick\nAstley")
    pca_link.send("play_search", query="  sleepy hallow  ")
    got = {c["name"]: c["args"] for c in pca_link.take()["commands"]}
    assert got["seek"] == {"position_ms": 61500}
    assert got["play_id"]["video_id"] == "dQw4w9WgXcQ" and len(got["play_id"]["title"]) == 200
    assert got["play_id"]["artist"] == "Rick Astley"
    assert got["play_search"] == {"query": "sleepy hallow"}


def test_state_bounds_never_raise():
    st = pca_link.clean_state({"playing": "yes", "title": "x" * 100_000, "artist": 7, "duration_ms": float("inf"),
                               "position_ms": float("nan"), "ts": 1e30, "liked": "true", "media_id": ["a"]})
    assert st["playing"] is False and len(st["title"]) == pca_link.MAX_TEXT and st["artist"] is None
    assert st["duration_ms"] is None and st["position_ms"] is None and st["ts"] is None and st["liked"] is None
    assert pca_link.clean_state("nope") is None


def test_two_people_are_separate(pinned):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    with users.acting_as(wife):
        pca_link.send("next")
        pca_link.report_state({"playing": True, "title": "Hers"})
    assert pca_link.take()["commands"] == [] and pca_link.state() is None
    with users.acting_as(wife):
        assert len(pca_link.take()["commands"]) == 1 and pca_link.state()["title"] == "Hers"


def test_module_does_not_touch_the_music_picker():
    tree = ast.parse(Path(pca_link.__file__).read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not ({"pca_music", "pca", "trainer", "fitness"} & names) and not any("pca_music" in (m or "") for m in mods)


# ---- the routes -------------------------------------------------------------------------------------------------------
def test_route_round_trip(client):
    h = pair(client)
    with users.acting_as("owner"):
        cid = pca_link.send("pause")
    r = client.get("/api/link/cmd?wait=0", headers=h).json()
    assert [c["id"] for c in r["commands"]] == [cid] and r["sleep"] == 0
    assert client.post("/api/link/result", headers=h, json={"id": cid, "ok": True, "detail": "paused"}).json() == {"ok": True}
    with users.acting_as("owner"):
        assert pca_link.result(cid)["ok"] is True
    s = client.get("/api/link/status", headers=h).json()
    assert s["paired"] and s["connected"] and s["last_result"]["detail"] == "paused"


def test_long_poll_returns_early_or_at_the_timeout(client, pinned, monkeypatch):
    h = pair(client)
    with users.acting_as("owner"):
        pca_link.active(600)
    start = time.monotonic()
    r = client.get("/api/link/cmd?wait=1", headers=h).json()
    assert r["commands"] == [] and 0.9 <= time.monotonic() - start < 3       # waited for the whole timeout

    real_take, calls = pca_link.take, {"n": 0}

    def take_then_arrive():
        calls["n"] += 1
        if calls["n"] == 2:                                   # a command arrives during the wait
            pca_link.send("next")
        return real_take()
    monkeypatch.setattr(pca_link, "take", take_then_arrive)
    start = time.monotonic()
    r = client.get("/api/link/cmd?wait=20", headers=h).json()
    assert [c["name"] for c in r["commands"]] == ["next"] and time.monotonic() - start < 5


def test_idle_poll_answers_at_once_with_a_long_sleep(client):
    h = pair(client)
    start = time.monotonic()
    r = client.get("/api/link/cmd?wait=25", headers=h).json()
    assert r == {"commands": [], "sleep": 45, "now": T0, "health": r["health"]} and time.monotonic() - start < 2
    assert isinstance(r["health"], bool)                                   # tools/pca_health: asks for a Garmin sync
    assert client.get("/api/link/cmd?wait=nan", headers=h).status_code == 200


def test_other_devices_cannot_take_commands_but_can_read_status(client):
    phone = pair(client, device="iPhone")
    assert client.get("/api/link/cmd", headers=phone).status_code == 403
    assert client.post("/api/link/state", headers=phone, json={"playing": True}).status_code == 403
    assert client.post("/api/link/result", headers=phone, json={"id": "x"}).status_code == 403
    s = client.get("/api/link/status", headers=phone).json()
    assert s["paired"] is False and s["connected"] is False and s["state"] is None
    assert client.get("/api/link/cmd").status_code == 401
    assert client.get("/api/link/status").status_code == 401


def test_people_never_cross_through_the_routes(client):
    wife = users.add("Marie", {"en": "madame"}, "wife")
    his, hers = pair(client), pair(client, wife)
    with users.acting_as("owner"):
        cid = pca_link.send("next")
    assert client.get("/api/link/cmd", headers=hers).json()["commands"] == []
    assert client.post("/api/link/result", headers=hers, json={"id": cid, "ok": True}).status_code == 400
    assert [c["id"] for c in client.get("/api/link/cmd", headers=his).json()["commands"]] == [cid]


def test_state_route_bounds(client):
    h = pair(client)
    ok = client.post("/api/link/state", headers=h, content=b'{"playing": true, "title": "A", "position_ms": NaN, '
                                                            b'"duration_ms": Infinity}')
    assert ok.status_code == 200
    st = client.get("/api/link/status", headers=h).json()["state"]
    assert st["title"] == "A" and st["position_ms"] is None and st["duration_ms"] is None
    assert client.post("/api/link/state", headers=h, content=b"x" * 40_000).status_code == 413
    assert client.post("/api/link/state", headers=h, content=b"{not json").status_code == 400
    bad = client.post("/api/link/state", headers=h, json=[1, 2])
    assert bad.status_code == 400 and bad.json()["detail"].endswith(".")
    huge = client.post("/api/link/state", headers=h, json={"title": "y" * 20_000})
    assert huge.status_code == 200


def test_apk_route(client, tmp_path, monkeypatch):
    h = pair(client, device="iPhone")
    monkeypatch.setattr(pca_link, "APK", tmp_path / "none" / "JarvisLink.apk")
    assert client.get("/api/link/apk").status_code == 401
    r = client.get("/api/link/apk", headers=h)
    assert r.status_code == 404 and r.json()["detail"].endswith(".")
    apk = tmp_path / "JarvisLink.apk"
    apk.write_bytes(b"PK\x03\x04fake")
    monkeypatch.setattr(pca_link, "APK", apk)
    r = client.get("/api/link/apk", headers=h)
    assert r.status_code == 200 and r.content == b"PK\x03\x04fake"
    assert r.headers["content-type"] == "application/vnd.android.package-archive"
    assert client.get("/api/link/apk?t=" + h["Authorization"].split()[1]).status_code == 200
