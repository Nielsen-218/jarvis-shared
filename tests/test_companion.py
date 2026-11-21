"""The PC companion (animated Jarvis on screen) and the Command Center dashboard."""
from pathlib import Path

from core import hud, users
from tools import interface

ROOT = Path(__file__).resolve().parent.parent


def test_companion_tool_drives_the_screen(monkeypatch):
    sent = []
    monkeypatch.setattr(hud, "_send", sent.append)
    monkeypatch.setattr(hud, "running", lambda: True)
    assert "workout is up" in interface.companion("workout")
    assert sent[-1] == {"type": "mode", "value": "workout"}
    assert "isn't running" in (monkeypatch.setattr(hud, "running", lambda: False) or interface.companion("play"))


def test_dashboard_opens_in_the_companion_window_when_it_runs(monkeypatch):
    sent = []
    monkeypatch.setattr(hud, "_send", sent.append)
    monkeypatch.setattr(hud, "running", lambda: True)
    assert interface.open_jarvis_page("dashboard") == "Opened the Command Center on the PC."
    assert sent[-1] == {"type": "dashboard"}


from tests.test_app import client, pair  # noqa: E402,F401


def test_say_returns_jarvis_voice_and_rejects_long_text(client, monkeypatch):
    from core import appserver
    h = pair(client)
    monkeypatch.setattr(appserver.AppServer, "_speak", lambda self, text: "/api/audio/x.mp3")
    assert client.post("/api/say", json={"text": "Ten seconds, sir."}, headers=h).json() == {"audio": "/api/audio/x.mp3"}
    assert client.post("/api/say", json={"text": "x" * 401}, headers=h).status_code == 400
    assert client.post("/api/say", json={"text": "hi"}).status_code == 401


def test_system_stats_are_owner_only(client, monkeypatch):
    from core import appserver
    monkeypatch.setattr(appserver, "system_stats", lambda: {"cpu_pct": 12, "mem_pct": 40})
    assert client.get("/api/system", headers=pair(client)).json()["cpu_pct"] == 12
    wife = users.add("Marie", {"en": "madame"}, "wife")
    assert client.get("/api/system", headers=pair(client, wife)).status_code == 403


def test_pages_exist_and_the_companion_talks_through_its_bridge():
    hudpage = (ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    for needle in ('"/api/fitness"', '"/api/fitness/log"', '"/api/chat"', "API().say(", "case \"mode\"", "function trivia", "function rps", "function reflex"):
        assert needle in hudpage, needle
    dash = (ROOT / "app" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('"/api/dashboard"', '"/api/system"', '"/api/fitness"', '"/api/kitchen"', '"/api/awareness"'):
        assert needle in dash, needle


import pytest  # noqa: E402


@pytest.mark.parametrize("said, kind", [
    ("Open the dashboard on the PC", "dashboard"), ("show me the command center", "dashboard"),
    ("start my workout on the screen", "workout"), ("coach me through today's session on the screen", "workout"),
    ("let's play a game", "play"),
    ("open spotify", None), ("close the dashboard", None), ("what is a dashboard camera", None),
    ("I played a game yesterday", None),
])
def test_own_screens_never_reach_the_app_launcher(monkeypatch, said, kind):
    sent = []
    monkeypatch.setattr(hud, "_send", sent.append)
    monkeypatch.setattr(hud, "running", lambda: True)
    reply = hud.own_screen(said)
    if kind is None:
        assert reply is None and not sent
    else:
        assert reply and sent[-1] == ({"type": "dashboard"} if kind == "dashboard" else {"type": "mode", "value": kind})


def test_phone_relayed_dashboard_request_completes_on_the_pc(monkeypatch):
    from core import pcbridge, pchybrid
    monkeypatch.setattr(hud, "_send", lambda m: None)
    monkeypatch.setattr(hud, "running", lambda: True)
    monkeypatch.setattr(pcbridge, "require_owner", lambda uid=None: "owner")
    out = pchybrid.run("open the dashboard on the PC")
    assert out["state"] == "completed" and "Command Center" in out["detail"]
