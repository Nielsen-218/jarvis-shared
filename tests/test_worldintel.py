"""World Intelligence: feeds normalise and never invent data, context and actions are verified, routes are PC-only/owner-only,
the Hub wires the page lazily and unloads it. No network, no real globe."""
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import worldintel as wi

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

QUAKES = [
    {"id": "us1", "properties": {"title": "M 6.1 - 10 km S of Jacmel, Haiti", "mag": 6.1, "time": None, "url": "https://earthquake.usgs.gov/e/us1", "status": "reviewed"},
     "geometry": {"coordinates": [-72.5, 18.2, 10]}},
    {"id": "us2", "properties": {"title": "M 2.6 - Somewhere", "mag": 2.6, "time": None, "url": "https://earthquake.usgs.gov/e/us2"}, "geometry": {"coordinates": [10, 20, 5]}},
    {"id": "old", "properties": {"title": "M 5 - too old", "mag": 5.0, "time": 1000, "url": "https://x"}, "geometry": {"coordinates": [0, 0, 1]}},
    {"id": "bad", "properties": {"title": "no geometry", "mag": 4, "time": None}, "geometry": {"coordinates": []}},
]


@pytest.fixture(autouse=True)
def clean():
    wi._cache.clear(); wi._ctx.clear(); wi._queue.clear(); wi._acks.clear(); wi.set_live(True)
    yield


def _quakes(monkeypatch):
    now = time.time() * 1000
    feats = []
    for f in QUAKES:
        p = dict(f["properties"])
        if p["time"] is None:
            p["time"] = now - 3600_000
        feats.append({**f, "properties": p})
    monkeypatch.setattr(wi, "_get_json", lambda url, timeout=10: {"features": feats})


def test_earthquakes_normalised_and_filtered(monkeypatch):
    _quakes(monkeypatch)
    r = wi.earthquakes(24, 2.5)
    assert r["ok"] and r["source"].startswith("USGS") and r["fetched"]
    assert [i["id"] for i in r["items"]] == ["us1", "us2"]          # strongest first; too-old and geometry-less dropped
    assert r["items"][0]["lat"] == 18.2 and r["items"][0]["lon"] == -72.5 and r["items"][0]["time"] < time.time()
    assert wi.feeds_status()["feeds"]["earthquakes"]["ok"]


def test_failed_feed_is_labelled_not_invented(monkeypatch):
    monkeypatch.setattr(wi, "_get_json", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    r = wi.earthquakes(24, 2.5)
    assert r["ok"] is False and r["items"] == [] and "down" in r["error"]


def test_stale_cache_is_marked_stale(monkeypatch):
    _quakes(monkeypatch)
    wi.earthquakes(24, 2.5)
    for k in list(wi._cache):
        wi._cache[k] = (time.time() - 99999, wi._cache[k][1])
    monkeypatch.setattr(wi, "_get_json", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    r = wi.earthquakes(24, 2.5)
    assert r["stale"] is True and r["items"]


def test_live_switch_off_loads_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(wi, "_get_json", lambda *a, **k: called.append(1) or {})
    wi.set_live(False)
    assert wi.earthquakes()["ok"] is False and wi.alerts()["ok"] is False and wi.search_place("Haiti")["ok"] is False
    assert not called


def test_only_allow_listed_https_hosts():
    for bad in ("http://earthquake.usgs.gov/x", "https://evil.example/x", "https://earthquake.usgs.gov.evil.com/x", "file:///c:/x"):
        with pytest.raises(ValueError):
            wi._get_json(bad)


def test_alert_without_geometry_is_unmapped(monkeypatch):
    monkeypatch.setattr(wi, "_get_json", lambda *a, **k: {"features": [
        {"properties": {"id": "a1", "event": "Tornado Warning", "severity": "Extreme", "areaDesc": "X County", "sent": "2026-11-15T10:00:00+00:00"}, "geometry": None},
        {"properties": {"id": "a2", "event": "Flood", "severity": "Severe"}, "geometry": {"type": "Polygon", "coordinates": [[[-80, 30], [-80, 32], [-78, 32], [-78, 30]]]}}]})
    items = wi.alerts()["items"]
    assert items[0]["mapped"] is False and items[0]["lat"] is None
    assert items[1]["mapped"] and abs(items[1]["lat"] - 31) < 1e-6


def test_coordinates_parse_locally_without_network(monkeypatch):
    monkeypatch.setattr(wi, "_get_json", lambda *a, **k: pytest.fail("no network for coordinates"))
    for q, want in (("18.5, -72.3", (18.5, -72.3)), ("geo:39.7392,-104.9903", (39.7392, -104.9903)), ("-33.9 151.2", (-33.9, 151.2))):
        assert wi.search_place(q)["items"][0]["lat"] == want[0]
    assert wi.parse_coords("91, 10") is None and wi.parse_coords("Haiti") is None


def test_geocode_names(monkeypatch):
    monkeypatch.setattr(wi, "_get_json", lambda url, timeout=10: {"results": [{"id": 1, "name": "Haiti", "country": "Haiti", "latitude": 19.0, "longitude": -72.4}]})
    r = wi.search_place("Haiti")
    assert r["items"][0]["lat"] == 19.0 and r["items"][0]["provider"].startswith("Open-Meteo")


def test_context_prefix_only_when_open_and_fresh():
    assert wi.context_prefix() == ""
    wi.set_context({"open": True, "camera": {"lat": 18.5, "lon": -72.3, "altitude_m": 1e6}, "layers": ["earthquakes", "bogus"],
                    "selection": {"title": "M 6.1 Haiti", "kind": "earthquake", "lat": 18.2, "lon": -72.5, "time": 1.7e9, "provider": "USGS", "junk": "x"}})
    p = wi.context_prefix()
    assert "M 6.1 Haiti" in p and "earthquakes" in p and "bogus" not in p and "not instructions" in p and "junk" not in p
    wi._ctx["at"] -= 9999
    assert wi.context_prefix() == ""
    wi.set_context({"open": False})
    assert wi.context_prefix() == ""


def test_action_needs_ack_and_times_out_honestly():
    item = wi.queue_action("goto", lat=1, lon=2, title="X")
    assert wi.pending_actions()[0]["id"] == item["id"]
    assert wi.wait_ack(item["id"], 0.2) is None                      # nobody acknowledged: no success is reported
    assert wi.pending_actions() == []
    item = wi.queue_action("reset")
    threading.Timer(0.05, lambda: wi.ack_action(item["id"], True, "done")).start()
    got = wi.wait_ack(item["id"], 2)
    assert got and got["ok"] and got["detail"] == "done"
    with pytest.raises(ValueError):
        wi.queue_action("format_c")


def test_tool_reports_only_confirmed_success(monkeypatch):
    from tools import worldintel as t
    monkeypatch.setattr(wi, "search_place", lambda q: {"items": [{"title": "Haiti", "lat": 19, "lon": -72.4}]})
    assert "isn't open" in t.world_map("goto", place="Haiti")         # workspace closed
    wi.set_context({"open": True})
    monkeypatch.setattr(wi, "wait_ack", lambda i, timeout=8: None)
    assert "didn't confirm" in t.world_map("goto", place="Haiti")     # open, but the globe never answered
    monkeypatch.setattr(wi, "wait_ack", lambda i, timeout=8: {"ok": True, "detail": "The globe is now centred on Haiti."})
    assert t.world_map("goto", place="Haiti") == "The globe is now centred on Haiti."
    monkeypatch.setattr(wi, "wait_ack", lambda i, timeout=8: {"ok": False, "detail": "no terrain"})
    assert t.world_map("reset") == "no terrain"                       # the globe's own reason, said once
    monkeypatch.setattr(wi, "wait_ack", lambda i, timeout=8: {"ok": False, "detail": ""})
    assert "didn't say why" in t.world_map("reset")
    assert "Which layers" in t.world_map("layers", show=["nonsense"])


def test_watchlist_roundtrip(monkeypatch):
    store = {}
    monkeypatch.setattr(wi.storage, "load", lambda n, d=None: store.get(n, d))
    monkeypatch.setattr(wi.storage, "save", lambda n, v: store.__setitem__(n, v))
    wi.watch_add({"title": "Haiti quake", "lat": 18.2, "lon": -72.5, "kind": "earthquake"})
    wi.watch_add({"title": "Haiti quake", "lat": 18.2, "lon": -72.5, "kind": "earthquake"})   # duplicate ignored
    items = wi.watchlist()
    assert len(items) == 1
    with pytest.raises(ValueError):
        wi.watch_add({"title": "no coords"})
    assert wi.watch_remove(items[0]["id"]) == []


# ---- routes -----------------------------------------------------------------------------------------------------------
class _Brain:
    def __init__(self, uid):
        self.uid = uid

    def ask(self, content, on_sentence):
        on_sentence("ok")


@pytest.fixture
def server(monkeypatch):
    from core import appserver
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    return TestClient(appserver.AppServer(lambda uid: _Brain(uid)).app)


def _pair(client, uid="owner", code="616161"):
    from tools import voice
    voice.pair_codes[code] = uid
    return {"Authorization": f"Bearer {client.post('/api/pair', json={'code': code, 'device': 'Desk'}).json()['token']}"}


def test_routes_need_auth_and_work_for_owner(server, monkeypatch):
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    assert server.get("/api/world/layers").status_code in (401, 403)
    h = _pair(server)
    layers = server.get("/api/world/layers", headers=h).json()
    assert {"aircraft", "earthquakes", "weather"} <= {x["id"] for x in layers["layers"]}
    _quakes(monkeypatch)
    assert server.get("/api/world/feed/earthquakes?hours=24", headers=h).json()["items"]
    assert server.get("/api/world/feed/nope", headers=h).status_code == 404
    assert server.post("/api/world/context", json={"open": True, "layers": ["weather"]}, headers=h).json()["ok"]
    assert wi.workspace_open()


def test_routes_are_pc_only(server, monkeypatch):
    h = _pair(server, code="626262")
    monkeypatch.setenv("JARVIS_ROLE", "server")
    assert server.get("/api/world/layers", headers=h).status_code == 404


def test_chat_text_stays_his_words_with_the_globe_open(server, monkeypatch):
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    h = _pair(server, code="636363")
    seen = {}
    from core import appserver

    def fake(self, uid, content, label, speak, verify=False):
        seen["c"] = content
        return {"reply": "ok"}
    monkeypatch.setattr(appserver.AppServer, "_answer", fake)
    wi.set_context({"open": True, "selection": {"title": "M 6.1 Haiti", "kind": "earthquake", "lat": 18, "lon": -72}})
    server.post("/api/chat", json={"text": "what's happening here?", "speak": False}, headers=h)
    assert seen["c"][-1]["text"].endswith("] what's happening here?")      # the map reaches the brain as <map-context>, not here


def test_typed_command_from_the_hub_reaches_the_fixed_phrases(monkeypatch):
    """Live 2026-11-15: with the globe open, 'take me to Haiti' typed in the Hub went to the model, which invented a flight."""
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    from core import quick
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    monkeypatch.setattr(wi, "search_place", lambda q: {"items": [{"title": "Haiti", "lat": 19, "lon": -72}]})
    monkeypatch.setattr(wi, "_world_action", lambda a, timeout=10, **k: {"ok": True, "detail": "The globe is now centred on Haiti."})
    said = "[Sent from their Jarvis phone app. Keep replies conversational; no markdown.] take me to Haiti"
    assert quick.handle(said) == "The globe is now centred on Haiti."



# ---- the Hub wiring ---------------------------------------------------------------------------------------------------
def test_hub_has_lazy_world_entry_under_systems_and_unloads():
    html = (APP / "command-center.html").read_text(encoding="utf-8")
    js = (APP / "command-center.js").read_text(encoding="utf-8")
    assert html.index("Systems</div>") < html.index('data-go="world"') < html.index('data-go="pc"')
    assert 'data-go="world"' not in html.split("</nav>")[0]                       # not added to the crowded top tabs
    assert 'world: ["World Intelligence", "world.html"]' in js
    assert "world.html" not in html                                              # not loaded at Hub start: the iframe is empty
    leave = js[js.index("function leaveWorld"):js.index("function showHome")]
    assert "about:blank" in leave and "/api/world/service" in leave and "on: false" in leave
    assert "leaveWorld();" in js[js.index("function showHome"):js.index("function showHome") + 120]
    assert 'leaveWorld(); $("#main").dataset.view = "focus"' in js


def test_world_page_is_honest():
    page = (APP / "world.html").read_text(encoding="utf-8")
    for needle in ("Simulation, not real events", "AI-generated", "separate website", "Ask JARVIS about this region", "not confirmation",
                   "when it happened", "Live data", "pagehide", "ac.abort()", "safeUrl"):
        assert needle in page, needle


def test_mode_action_is_verified(monkeypatch):
    from tools import worldintel as t
    wi.set_context({"open": True})
    seen = {}
    monkeypatch.setattr(wi, "queue_action", lambda action, **a: seen.update(action=action, **a) or {"id": "x"})
    monkeypatch.setattr(wi, "wait_ack", lambda i, timeout=8: {"ok": True, "detail": "Switched to the flat 2D map."})
    assert t.world_map("mode", mode="2d") == "Switched to the flat 2D map." and seen == {"action": "mode", "mode": "2d"}
    t.world_map("mode", mode="3d")
    assert seen["mode"] == "3d"


def test_spoken_turn_gets_map_context_block():
    src = (ROOT / "brain" / "llm.py").read_text(encoding="utf-8")
    block = src[src.index("<daily-awareness>"):src.index("<map-context>") + 200]
    assert "worldintel.context_prefix()" in block and 'JARVIS_ROLE") == "server"' in block
    assert "[World Intelligence is open" in block               # a typed /api/chat turn already carries it: never twice


def test_bridge_patch_has_mode_camera_pick():
    patch = (ROOT / "integration" / "gods-eye-view" / "jarvis-bridge.patch").read_text(encoding="utf-8")
    for m in ("gev:camera", "gev:pick", "gev:mode", "morphTo2D", "removeEventListener('message', onMode)"):
        assert m in patch, m


def test_2d_is_refused_with_moving_object_layers():
    page = (APP / "world.html").read_text(encoding="utf-8")
    assert 'const NOT_IN_2D = ["aircraft", "maritime", "satellites"]' in page
    assert "can't show" in page and 'S.mode === "2d" && NOT_IN_2D.includes(id)' in page


def test_globe_loads_without_position_hash_and_replaces_intro():
    page = (APP / "world.html").read_text(encoding="utf-8")
    assert "frame.src = `${GEV_ORIGIN}/?embed=1`;" in page and "?embed=1#" not in page   # a hash kept it from ever being ready
    assert "setTimeout(endIntro, 900)" in page and "if (S.intro) endIntro();" in page
    assert "@media (max-width: 760px)" in page                                            # a 0-width canvas never finishes a flight


def test_globe_service_prefers_the_production_build(monkeypatch, tmp_path):
    (tmp_path / "node_modules").mkdir(); (tmp_path / "package.json").write_text("{}"); (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("x")
    monkeypatch.setenv("JARVIS_GEV_DIR", str(tmp_path))
    seen = {}
    monkeypatch.setattr(wi, "gev_up", lambda: bool(seen))
    monkeypatch.setattr(wi.subprocess, "Popen", lambda args, **k: seen.update(args=args, env=k["env"]) or type("P", (), {"poll": lambda s: None, "pid": 1})())
    monkeypatch.setattr(wi, "_proc", None)
    assert wi.gev_start()["ok"]
    assert seen["args"][1:3] == ["vite", "preview"] and "8765" in seen["env"]["GEV_EMBED_FRAME_ANCESTORS"]
    monkeypatch.setattr(wi, "_proc", None)


# ---- voice: fixed phrases ------------------------------------------------------------------------------------------
@pytest.fixture
def voice_env(monkeypatch):
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    calls = []
    monkeypatch.setattr(wi, "open_world", lambda p: calls.append(("open", p)) or {"ok": True})
    monkeypatch.setattr(wi, "_world_action", lambda a, timeout=10, **k: calls.append((a, k)) or {"ok": True, "detail": "done"})
    monkeypatch.setattr(wi, "search_place", lambda q: {"items": [{"title": q, "lat": 1, "lon": 2}]})
    monkeypatch.setattr(wi, "briefing", lambda: "BRIEF")
    return calls


@pytest.mark.parametrize("said,preset", [
    ("Hey Jarvis, open intelligence", "standard"), ("open intel", "standard"), ("open the world intelligence page", "standard"),
    ("bird's eye view", "birdseye"), ("Hey Jarvis, God's eye view", "birdseye"), ("spy mode", "spy"), ("Jarvis, engage spy mode", "spy"),
    ("smart mode", "situation"), ("what's happening in the world", "situation"), ("I want to know what's happening in the world", "situation"),
    ("thermal vision", "thermal"), ("night vision", "night")])
def test_open_phrases(voice_env, monkeypatch, said, preset):
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice(said) and voice_env == [("open", preset)]


def test_situation_room_speaks_the_briefing(voice_env, monkeypatch):
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice("world briefing").endswith("BRIEF")


@pytest.mark.parametrize("said", ["open YouTube", "what time is it", "zoom in", "take me to Haiti", "show earthquakes", "normal mode", "go to the world"])
def test_not_ours_when_closed(voice_env, monkeypatch, said):
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice(said) is None and voice_env == []


@pytest.mark.parametrize("said,call", [
    ("take me to Haiti", ("goto", {"lat": 1, "lon": 2, "title": "Haiti", "altitude_km": None})), ("zoom in", ("zoom", {"direction": "in"})),
    ("show earthquakes", ("layers", {"show": ["earthquakes"], "only": False})), ("hide planes", ("layers", {"hide": ["aircraft"]})),
    ("only show weather", ("layers", {"show": ["weather"], "only": True})), ("switch to 2D", ("mode", {"mode": "2d"})),
    ("close intelligence", ("close", {}))])
def test_open_workspace_commands(voice_env, monkeypatch, said, call):
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    assert wi.voice(said) and voice_env == [call]


@pytest.mark.parametrize("said,call", [
    ("Yes, sir. Reset.", ("reset", {})), ("Alright, zoom in.", ("zoom", {"direction": "in"})), ("Reset, please.", ("reset", {}))])
def test_filler_words_around_a_command_are_ignored(voice_env, monkeypatch, said, call):
    """His real log (2026-11-15): 'Yes, sir. Reset.' was answered 'Of course, sir.' and nothing reset."""
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    assert wi.voice(said) and voice_env == [call]


@pytest.mark.parametrize("said,preset", [
    ("They drive us. Open World Intelligence. That's it.", "standard"),
    ("No. Hey Jarvis. Come on. What's that? Open World Intelligence.", "standard"),
    ("Okay cool. Spy mode.", "spy")])
def test_a_command_sentence_inside_longer_speech_is_done(voice_env, monkeypatch, said, preset):
    """His real log (2026-11-15): these got 'Open World Intelligence, sir.' from the model and nothing opened."""
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice(said) and voice_env == [("open", preset)]


@pytest.mark.parametrize("said", ["I don't want to open world intelligence. Not now.", "We went to Haiti. It was nice.",
                                  "Zoom in on that. The picture.", "Spy mode is cool. Don't open it."])
def test_chatter_and_refusals_are_not_commands(voice_env, monkeypatch, said):
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice(said) is None and voice_env == []


def test_globe_service_turns_on_quebec_video(monkeypatch, tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "package.json").write_text("{}")
    seen = {}
    monkeypatch.setenv("JARVIS_GEV_DIR", str(tmp_path))
    monkeypatch.delenv("CCTV_QUEBEC_VIDEO_ENABLED", raising=False)
    monkeypatch.delenv("JARVIS_GEV_QUEBEC_VIDEO", raising=False)
    up = iter([False, True])
    monkeypatch.setattr(wi, "gev_up", lambda: next(up))
    monkeypatch.setattr(wi.subprocess, "Popen", lambda *a, **k: seen.update(k["env"]) or object())
    assert wi.gev_start()["ok"] and seen["CCTV_QUEBEC_VIDEO_ENABLED"] == "1"


@pytest.mark.parametrize("said,cam", [
    ("Hey Jarvis, show me Times Square live", "times-square"), ("Times Square cam", "times-square"), ("play the Shibuya crossing live stream", "shibuya"),
    ("live feed from Abbey Road", "abbey-road"), ("show me the Eiffel Tower live", "eiffel"), ("Niagara Falls webcam", "niagara"),
    ("Okay. Show me Times Square live.", "times-square"), ("show live cams", "")])
def test_livecam_voice(voice_env, monkeypatch, said, cam):
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice(said) and voice_env == [("open", "standard"), ("livecam", {"id": cam})]


@pytest.mark.parametrize("said,cam", [
    ("show me the space station live", "iss"), ("volcano cam", "kilauea"), ("show me the bears live", "brooks-falls"),
    ("safari live cam", "safari"), ("Jamaica live", "kingston"), ("live feed from Sydney Harbour", "sydney"), ("New Orleans webcam", "new-orleans")])
def test_more_livecam_voice(voice_env, monkeypatch, said, cam):
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    assert wi.voice(said) and voice_env == [("livecam", {"id": cam})]


def test_every_cam_is_grouped_and_unique():
    ids = [c["id"] for c in wi.LIVECAMS]
    assert len(ids) == len(set(ids)) >= 20 and {c["group"] for c in wi.LIVECAMS} == {"City", "Nature", "Space"}


def test_space_station_cam_flies_to_where_it_is_now(monkeypatch):
    wi._lc_cache.clear()
    monkeypatch.setattr(wi, "_live_search", lambda q, ch: [])
    monkeypatch.setattr(wi, "_embeddable", lambda v: {"title": "ISS", "channel": "NASA"})
    pos = iter([{"lat": 10.0, "lon": 20.0}, {"lat": 11.0, "lon": 25.0}])
    monkeypatch.setattr(wi, "iss_position", lambda: next(pos))
    a, b = wi.livecam("iss"), wi.livecam("iss")
    assert a["moving"] and (a["lat"], a["lon"]) == (10.0, 20.0) and (b["lat"], b["lon"]) == (11.0, 25.0)   # cached stream, fresh position


@pytest.mark.parametrize("said", ["I went to Times Square", "Times Square is busy", "take me to Paris"])
def test_place_talk_is_not_a_livecam(voice_env, monkeypatch, said):
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    assert wi.voice(said) is None and voice_env == []


def test_livecam_returns_only_a_checked_stream(monkeypatch):
    wi._lc_cache.clear()
    monkeypatch.setattr(wi, "_live_search", lambda q, ch: ["AAAAAAAAAAA", "BBBBBBBBBBB"])
    monkeypatch.setattr(wi, "_embeddable", lambda v: {"title": "T", "channel": "EarthCam"} if v == "BBBBBBBBBBB" else None)
    r = wi.livecam("times-square")
    assert r["ok"] and r["video"] == "BBBBBBBBBBB" and r["embed"].startswith("https://www.youtube-nocookie.com/embed/BBBBBBBBBBB")
    wi._lc_cache.clear()
    monkeypatch.setattr(wi, "_embeddable", lambda v: None)
    r = wi.livecam("times-square")
    assert not r["ok"] and r["watch"].startswith("https://www.youtube.com/results?") and "video" not in r
    assert wi.livecam("nope")["ok"] is False
    assert wi._yt_get("http://evil.example/") == (0, "")                  # fixed host only


def test_page_has_the_livecam_player():
    page = (ROOT / "app" / "world.html").read_text(encoding="utf-8")
    assert '"livecam"' in page and "/api/world/livecam/" in page and 'hostname === "www.youtube-nocookie.com"' in page


def test_country_title_is_not_repeated():
    assert wi.place_title("Haiti, Haiti") == "Haiti" and wi.place_title("Paris, Ile-de-France, France") == "Paris, Ile-de-France, France"


@pytest.mark.parametrize("said", ["Open it in its own window.", "open world intelligence in its own window",
                                  "Open World Intelligence on its own window", "open the full God's Eye View", "open god's eye view in a new window"])
def test_own_window_opens_the_full_local_app(voice_env, monkeypatch, said):
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    monkeypatch.setattr(wi, "open_full_app", lambda: {"ok": True, "detail": "God's Eye View is open in its own window."})
    assert wi.voice(said) == "God's Eye View is open in its own window." and voice_env == []


def test_full_app_is_the_local_copy_and_survives_leaving_the_page(monkeypatch):
    from tools import system
    opened = []
    monkeypatch.setattr(wi, "gev_start", lambda: {"ok": True})
    monkeypatch.setattr(system, "open_website", lambda u: opened.append(u) or f"Opened {u}.")
    monkeypatch.setattr(wi, "_full_app_open", False)
    assert wi.open_full_app()["ok"] and opened == [f"http://127.0.0.1:{wi.GEV_PORT}/"]
    assert wi.gev_stop()["stopped"] is False                      # his full-app window keeps its service
    page = (ROOT / "app" / "world.html").read_text(encoding="utf-8")
    assert "github.com/bilawalsidhu" not in page and "/api/world/full-app" in page


def test_open_when_already_open_brings_the_hub_forward(monkeypatch):
    from core import hud
    fronts = []
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    monkeypatch.setattr(hud, "front", lambda: fronts.append(1) or True)
    monkeypatch.setattr(wi, "_world_action", lambda a, timeout=10, **k: {"ok": True, "detail": "x"})
    assert wi.open_world("standard")["ok"] and fronts == [1]


def test_show_me_the_money_is_not_a_place(voice_env, monkeypatch):
    monkeypatch.setattr(wi, "workspace_open", lambda: True)
    assert wi.voice("show me the money") is None


def test_open_is_only_claimed_when_the_hub_confirms(monkeypatch):
    monkeypatch.delenv("JARVIS_ROLE", raising=False)
    from core import hud
    monkeypatch.setattr(wi, "workspace_open", lambda: False)
    monkeypatch.setattr(hud, "world", lambda p: False)
    assert "isn't running" in wi.voice("spy mode")
    monkeypatch.setattr(hud, "world", lambda p: True)
    monkeypatch.setattr(wi.time, "monotonic", iter([0, 0, 99]).__next__)
    monkeypatch.setattr(wi.time, "sleep", lambda s: None)
    assert "didn't confirm" in wi.voice("spy mode")


def test_briefing_uses_only_feed_data(monkeypatch):
    monkeypatch.setattr(wi, "earthquakes", lambda h, m: {"ok": True, "count": 3, "items": [{"title": "M 6.0 - 20 km N of X", "magnitude": 6.0, "time": time.time() - 7200}]})
    monkeypatch.setattr(wi, "alerts", lambda: {"ok": False})
    b = wi.briefing()
    assert "3 earthquakes" in b and "magnitude 6.0" in b and "20 km N of X" in b and "2 hours ago" in b and "alert" not in b
    monkeypatch.setattr(wi, "earthquakes", lambda h, m: {"ok": False})
    assert "won't guess" in wi.briefing()


def test_voice_router_runs_before_the_model_and_hub_opens_presets():
    q = (ROOT / "core" / "quick.py").read_text(encoding="utf-8")
    assert q.index("worldintel.voice(raw)") < q.index("_mission_command(raw.strip())")
    js = (APP / "command-center.js").read_text(encoding="utf-8")
    assert 'if (v === "world")' in js and "jarvisWorldPreset" in js
    page = (APP / "world.html").read_text(encoding="utf-8")
    assert "pushContext(true); paintDossier();" in page and "preset: S.preset" in page and 'id="bootLog"' in page
