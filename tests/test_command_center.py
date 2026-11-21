"""The Command Center is the main desktop window (owner 2026-11-12): the page, the routes it calls, which window the HUD
process opens, and the companion that comes when called and leaves when told. No model, speaker, network or live PC."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PAGE = (APP / "command-center.html").read_text(encoding="utf-8")
JS = (APP / "command-center.js").read_text(encoding="utf-8")
CSS = (APP / "command-center.css").read_text(encoding="utf-8")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")


# ------------------------------------------------------------------ the page
def test_page_links_the_design_system_and_its_own_files():
    assert 'href="design-system.css"' in PAGE and 'href="command-center.css"' in PAGE and 'src="command-center.js"' in PAGE
    assert "var(--text-1)" in CSS and "--cc-gold" in CSS and "--cc-blue" in CSS      # tokens plus the command-center scope


@pytest.mark.parametrize("region", ["topbar", "tabs", "gauges", "sidebar", "hero", "feed", "micbar", "profile", "now",
                                    "mission", "launch", "system", "micBtn", "askText"])
def test_page_has_the_main_regions(region):
    assert f'id="{region}"' in PAGE


def test_no_glowing_globe_and_no_companion_button():
    """2026-11-19, owner: the animated glowing globe and the 3D desktop companion lagged the PC; both are gone."""
    assert "cc-globe" not in PAGE and "cc-globe" not in CSS and "cc-spin" not in CSS and "cc-pulse" not in CSS
    assert 'id="companionBtn"' not in PAGE


def test_page_carries_the_design_text_as_real_html():
    for text in ("PERSONAL AI COMMAND CENTER", "BUILT FOR ALEX", "Your Name", "Today's Mission", "Quick Launch",
                 "System Status", "PC Control", "My World"):
        assert text in PAGE, text
    for tab in ("Home", "Chat", "Code", "Build", "Game", "Fitness", "Learn", "Projects", "Automation"):
        assert re.search(rf'<button data-go="[a-z]+"[^>]*>(?:<i [^>]*></i>)?{tab}</button>', PAGE), tab
    assert PAGE.count("<img") == 1 and 'id="mediaImg"' in PAGE   # text and controls are HTML; the only <img> is the picture card


def test_no_emoji_icons_and_no_native_dialogs():
    for name, text in (("html", PAGE), ("js", JS), ("css", CSS)):
        assert not EMOJI.search(text), name
    assert not re.search(r"\b(?:alert|confirm|prompt)\(", JS)


def test_backdrop_art_exists_and_pages_it_embeds_exist():
    for art in re.findall(r'url\("(assets/command-center/[^"]+)"\)', CSS):
        assert (APP / art).is_file(), art
    pages = set(re.findall(r'"([a-z-]+\.html)"', JS))
    assert {"fitness.html", "study.html", "pc.html", "memory.html", "wardrobe.html", "dashboard.html"} <= pages
    for page in pages:
        assert (APP / page).is_file(), page


def test_every_route_the_page_calls_exists():
    from core import appserver
    app = appserver.AppServer(lambda uid: None).app
    served = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    called = set(re.findall(r'"(/api/[a-z/_-]+)', JS))
    assert {"/api/chat", "/api/system", "/api/dashboard", "/api/companion/listen", "/api/companion/visible"} <= called
    posts = {"/api/chat", "/api/companion/listen", "/api/companion/stop", "/api/companion/mute", "/api/companion/visible",
             "/api/say", "/api/me/tagline", "/api/me/photo", "/api/launcher/pin", "/api/launcher/unpin", "/api/world/ack", "/api/world/context", "/api/world/service"}
    for path in called:
        assert (("POST" if path in posts else "GET"), path) in served, path


def test_page_handles_the_events_the_old_panel_received():
    for kind in ("state", "level", "line", "doing", "notify", "badges", "media", "media_clear", "timers", "focus", "visibility"):
        assert f'case "{kind}"' in JS, kind
    assert "window.jarvis" in JS


# ------------------------------------------------------------------ which window
def test_command_center_is_the_default_and_the_old_panel_is_a_setting():
    from hud import app
    assert app.desktop_ui("http://127.0.0.1:1", {}) == "command-center"
    for value in ("panel", "classic", " PANEL "):
        assert app.desktop_ui("http://127.0.0.1:1", {"JARVIS_DESKTOP_UI": value}) == "panel"
    assert app.desktop_ui("", {}) == "panel"                     # no app server: nothing could serve the page
    assert (ROOT / "hud" / "index.html").is_file()               # kept, reachable only with JARVIS_DESKTOP_UI=panel


def test_window_size_and_position_are_remembered(tmp_path):
    from hud import app
    path = tmp_path / "cc.json"
    assert app.load_cc_geometry(1920, 1080, path) == {"width": 1500, "height": 850, "x": None, "y": None}
    assert app.save_cc_geometry({"width": 1700, "height": 950, "x": 60, "y": 40}, path)
    assert app.load_cc_geometry(1920, 1080, path) == {"width": 1700, "height": 950, "x": 60, "y": 40}
    assert not app.save_cc_geometry({"width": 1700, "height": 950, "x": -32000, "y": -32000}, path)   # minimised
    assert app.load_cc_geometry(1920, 1080, path)["x"] == 60
    path.write_text(json.dumps({"width": 1700, "height": 950, "x": 5000, "y": 40}), encoding="utf-8")
    assert app.load_cc_geometry(1920, 1080, path) == {"width": 1700, "height": 950, "x": 110, "y": 65}   # gone monitor: centred
    path.write_text("not json", encoding="utf-8")
    assert app.load_cc_geometry(1280, 720, path)["width"] <= 1280


def test_panel_target_is_the_main_window_only_in_panel_mode():
    from hud import app
    api = app.Api("", "")
    assert api._cc is None and api._classic_target() is api._window
    api._cc = object()
    assert api._classic_target() is None                        # under the Command Center there is no second panel


# ------------------------------------------------------------------ the companion comes and goes
SHOW = ["come here", "Come here.", "Hey Jarvis, come here", "show yourself", "companion on", "I need you on screen",
        "Jarvis, I need you on the screen please", "turn the companion on", "show the companion", "come back"]
HIDE = ["go away", "Go away, Jarvis.", "That will be all, go.", "companion off", "hide yourself", "dismiss the companion",
        "hide the companion", "you're dismissed", "get off my screen"]
BARE = ["leave", "hide", "dismiss", "dismissed", "Leave.", "Hey Jarvis, hide"]
NOT = ["go away for the weekend", "hide the window", "leave it", "leave it alone", "I want to go away next month",
       "hide my taskbar", "dismiss the alarm", "show me the weather", "come here often?", "leave a note for my wife",
       "should I leave now", "show yourself out of this meeting invite", "tell her to come here", "", "open the companion app store"]


@pytest.mark.parametrize("line", SHOW)
def test_called(line):
    from core import hud
    assert hud.companion_call(line) == "show"
    assert hud.companion_call("[Sent from their Jarvis phone app, typed] " + line) == "show"


@pytest.mark.parametrize("line", HIDE)
def test_dismissed(line):
    from core import hud
    assert hud.companion_call(line) == "hide"
    assert hud.companion_call("[Sent from their Jarvis phone app, typed] " + line) == "hide"


@pytest.mark.parametrize("line", NOT)
def test_ordinary_sentences_are_not_captured(line):
    from core import hud
    assert hud.companion_call(line) is None


def test_one_word_after_a_question_is_his_answer_not_a_dismissal():
    from core import hud
    for word in ("leave", "hide", "dismiss"):
        assert hud.companion_call(word, answering=True) is None
    assert hud.companion_call("go away", answering=True) == "hide"


@pytest.mark.parametrize("line", BARE)
def test_a_bare_word_dismisses_only_when_the_caller_knows_nothing_is_being_answered(line):
    from core import hud
    assert hud.companion_call(line) is None                      # no history to look at (phone relay, acquire): never
    assert hud.companion_call(line, answering=True) is None
    assert hud.companion_call(line, answering=False) == "hide"
    for phrase in HIDE:
        assert hud.companion_call(phrase) == "hide"              # the unambiguous phrases need no history


def test_companion_line_without_history_ignores_bare_words_but_takes_phrases(desk, monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(pcfast, "_session_open", lambda: False)
    for word in BARE:
        assert pcfast.companion_line(word) is None and pcfast.companion_line(word, None) is None
    assert desk.sent == []
    assert "As you wish" in pcfast.companion_line("hide yourself")
    assert "As you wish" in pcfast.companion_line("dismiss the companion", None)
    assert pcfast.companion_line("come here").startswith("Right here")


def test_companion_line_with_history_reads_the_question_and_open_lessons(desk, monkeypatch):
    from core import pcbridge, pcfast
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    monkeypatch.setattr(pcfast, "_session_open", lambda: False)
    quiet = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Good evening, sir."}]
    asked = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Which window should I close, sir?"}]
    assert pcfast.companion_line("leave", asked) is None
    assert desk.sent == []
    monkeypatch.setattr(pcfast, "_session_open", lambda: True)
    assert pcfast.companion_line("leave", quiet) is None         # a quiz / lesson / workout is open: it reads the word first
    assert pcfast.companion_line("go away", quiet).startswith("As you wish")
    monkeypatch.setattr(pcfast, "_session_open", lambda: False)
    assert pcfast.companion_line("leave", quiet).startswith("As you wish")
    assert desk.sent[-2:] == [{"type": "companion_hide"}, {"type": "companion", "visible": False}]


def test_the_phone_relay_never_dismisses_on_a_bare_word(desk):
    from core import hud
    assert hud.companion_command("leave") is None and hud.companion_command("hide") is None and desk.sent == []
    assert "As you wish" in hud.companion_command("go away")
    assert "As you wish" in hud.companion_command("leave", answering=False)


@pytest.fixture
def desk(monkeypatch, tmp_path):
    """core.hud with a stand-in window process: records what would be sent, state file in a temp folder."""
    from core import companion, hud
    monkeypatch.setattr(companion, "default_position_path", lambda: tmp_path / "companion-position.json")
    sent = []
    monkeypatch.setattr(hud, "running", lambda: True)
    monkeypatch.setattr(hud, "_mode", "companion")
    monkeypatch.setattr(hud, "_send", sent.append)
    return SimpleNamespace(hud=hud, sent=sent, tmp=tmp_path)


def test_hidden_until_called_and_remembered(desk):
    hud = desk.hud
    assert hud.companion_visible() is False                      # a fresh install: he waits off screen
    assert hud.companion_command("come here") == "Right here, sir."
    assert desk.sent[0] == {"type": "companion_show"} and desk.sent[1] == {"type": "companion", "visible": True}
    assert hud.companion_visible() is True                       # read back from disk: survives a restart
    assert json.loads((desk.tmp / "companion-visible.json").read_text(encoding="utf-8")) == {"visible": True}
    assert "As you wish" in hud.companion_command("go away")
    assert desk.sent[2] == {"type": "companion_hide"} and hud.companion_visible() is False
    assert hud.companion_command("hide the window") is None and len(desk.sent) == 4


def test_events_pass_validation_and_honest_when_not_running(desk, monkeypatch):
    from core import companion
    assert companion.validate_event({"type": "companion", "visible": 1}) == {"type": "companion", "visible": True}
    assert companion.validate_event({"type": "companion_hide"}) == {"type": "companion_hide"}
    monkeypatch.setattr(desk.hud, "running", lambda: False)
    assert "isn't running" in desk.hud.companion_command("come here")
    assert desk.sent == [] and not (desk.tmp / "companion-visible.json").exists()   # nothing remembered for a companion that isn't there
    assert desk.hud.set_companion_visible(True) is False and desk.hud.companion_visible() is False
    monkeypatch.setattr(desk.hud, "running", lambda: True)
    monkeypatch.setattr(desk.hud, "_mode", "panel")              # running, but no companion window in this mode
    assert desk.hud.set_companion_visible(True) is False and desk.sent == [] and desk.hud.companion_visible() is False


def test_start_hides_him_and_fullscreen_logic_leaves_a_dismissed_companion_alone():
    src = (ROOT / "hud" / "app.py").read_text(encoding="utf-8")
    assert "api._user_hidden = not companion_visible()" in src
    assert src.index("if api._user_hidden:                    # dismissed") < src.index("if fullscreen and not api._fs_hidden:")
    tray = (ROOT / "core" / "tray.py").read_text(encoding="utf-8")
    assert "hud.set_companion_visible(not hud.companion_visible())" in tray


# ------------------------------------------------------------------ the real typed and spoken routes
@pytest.fixture
def route(monkeypatch, desk):
    from brain import llm
    from core import appserver, intelligence, pcbridge, pcfast, quick, users
    from tools import voice

    monkeypatch.setenv("JARVIS_INTELLIGENCE", "local-first")
    monkeypatch.setattr(quick, "handle", lambda text: None)
    monkeypatch.setattr(pcbridge, "local_pc", lambda: True)
    # Only the companion decision of the real PC route runs: nothing else in pcfast.handle may reach this computer.
    monkeypatch.setattr(pcfast, "handle", lambda text, history=None, say=None, earcon=None: pcfast.companion_line(text, history))
    monkeypatch.setattr(intelligence, "relevant_memory", lambda text: "")
    monkeypatch.setattr(intelligence, "audit", lambda *a, **k: None)
    model_calls = []

    def request(role, system, history, tools, cancelled=None, on_text=None, **kw):
        model_calls.append(history[-1])
        return SimpleNamespace(content=[TextBlock(type="text", text="Small talk, sir.")], stop_reason="end_turn")
    monkeypatch.setattr(intelligence, "request", request)
    brain = llm.Brain("claude-opus-5-5", "medium", "You are Jarvis.", uid="owner")
    brain.context = lambda: ""
    monkeypatch.setattr(appserver, "CACHE", appserver.storage.DATA / "cache")
    (appserver.storage.DATA / "cache").mkdir(parents=True, exist_ok=True)
    client = TestClient(appserver.AppServer(lambda uid: brain).app)
    voice.pair_codes["444444"] = "owner"
    token = client.post("/api/pair", json={"code": "444444", "device": "Phone"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    def typed(line):
        model_calls.clear()
        reply = client.post("/api/chat", json={"text": line, "speak": False}, headers=headers).json()["reply"]
        return reply, list(model_calls)

    def spoken(line):
        model_calls.clear()
        said = []
        brain.ask(line, said.append)
        return " ".join(said), list(model_calls)
    return SimpleNamespace(typed=typed, spoken=spoken, client=client, headers=headers, sent=desk.sent, hud=desk.hud)


@pytest.mark.parametrize("how", ["typed", "spoken"])
def test_come_here_and_go_away_are_done_in_code_on_both_paths(route, how):
    reply, model = getattr(route, how)("Come here.")
    assert reply.startswith("Right here") and not model, (how, reply, model)
    assert {"type": "companion_show"} in route.sent and route.hud.companion_visible()
    reply, model = getattr(route, how)("That will be all, go.")
    assert "As you wish" in reply and not model
    assert {"type": "companion_hide"} in route.sent and not route.hud.companion_visible()


@pytest.mark.parametrize("line", ["I might go away for the weekend.", "Hide the window.", "Leave it."])
@pytest.mark.parametrize("how", ["typed", "spoken"])
def test_ordinary_sentences_go_on_to_the_conversation(route, how, line):
    reply, model = getattr(route, how)(line)
    assert not [m for m in route.sent if m["type"].startswith("companion")] and not route.hud.companion_visible()
    assert model and "Small talk" in reply


def test_command_center_button_routes(route):
    c, h = route.client, route.headers
    assert c.get("/api/companion/visible", headers=h).json() == {"visible": False, "running": True}
    assert c.post("/api/companion/visible", json={"on": True}, headers=h).json() == {"ok": True, "visible": True}
    assert {"type": "companion_show"} in route.sent
    assert c.post("/api/companion/visible", json={"on": False}, headers=h).json() == {"ok": True, "visible": False}
    assert c.post("/api/companion/visible", json={"on": True}).status_code in (401, 403)       # no token, no entry


def test_mic_bar_routes_use_the_hooks_main_gives(route):
    from core import appserver
    c, h = route.client, route.headers
    assert c.post("/api/companion/stop", json={}, headers=h).json()["ok"] is False             # no voice: says so
    assert c.post("/api/companion/mute", json={}, headers=h).json()["ok"] is False
    server = next(o for o in __import__("gc").get_objects() if isinstance(o, appserver.AppServer) and o.app is c.app)
    calls, state = [], {"muted": False}
    server.on_stop = lambda: calls.append("stop")
    server.on_mute = lambda: state.__setitem__("muted", not state["muted"]) or state["muted"]
    assert c.post("/api/companion/stop", json={}, headers=h).json() == {"ok": True} and calls == ["stop"]
    assert c.post("/api/companion/mute", json={}, headers=h).json() == {"ok": True, "muted": True}
    assert c.post("/api/companion/mute", json={}, headers=h).json() == {"ok": True, "muted": False}


def test_gpu_numbers_survive_a_decimal_temperature(monkeypatch):
    """Seen live: nvidia-smi printed "50.87" for the temperature and the GPU gauge showed nothing."""
    import subprocess

    from core import appserver
    clock = [100.0]
    monkeypatch.setattr(appserver, "_gpu_clock", lambda: clock[0])
    monkeypatch.setattr(appserver, "_gpu_cache", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="NVIDIA GeForce RTX 5070 Ti, 34, 11636, 16303, 50.87\n"))
    gpu = appserver.system_stats()["gpu"]
    assert gpu == {"name": "RTX 5070 Ti", "util_pct": 34, "vram_used_gb": 11.4, "vram_gb": 16, "temp_c": 51}
    clock[0] += 60                                               # the cache has expired
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert "gpu" not in appserver.system_stats()                 # no card: the gauge says so instead of inventing a number


def test_nvidia_smi_runs_once_per_few_seconds_even_when_polled_fast(monkeypatch):
    import subprocess

    from core import appserver
    clock, spawned = [100.0], []

    def run(*a, **k):
        spawned.append(1)
        return SimpleNamespace(stdout="NVIDIA GeForce RTX 5070 Ti, 10, 1024, 16303, 40\n")
    monkeypatch.setattr(appserver, "_gpu_clock", lambda: clock[0])
    monkeypatch.setattr(appserver, "_gpu_cache", None)
    monkeypatch.setattr(subprocess, "run", run)
    for _ in range(5):
        assert appserver.system_stats()["gpu"]["util_pct"] == 10
        clock[0] += 0.5
    assert len(spawned) == 1
    clock[0] += appserver._GPU_TTL
    appserver.system_stats()
    assert len(spawned) == 2
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(appserver, "_gpu_cache", None)
    appserver.system_stats(); appserver.system_stats()          # a miss is kept too: no spawn storm on a PC without the tool


# ---- a bare word during an open study quiz, language lesson or workout goes to that session, not the companion
@pytest.mark.parametrize("session", ["study", "lesson", "workout"])
@pytest.mark.parametrize("how", ["typed", "spoken"])
def test_a_bare_leave_during_a_session_reaches_the_session_hook_first(route, monkeypatch, session, how):
    from core import coachintent, langintent, studyintent
    from tools import langlesson, study, trainer
    seen = []
    table = {"stem": "Which is a mammal?", "answer": "whale", "options": ["whale", "shark"], "quote": "", "fresh": True}
    monkeypatch.setattr(study, "on_the_table", lambda: table if session == "study" else None)
    monkeypatch.setattr(langlesson, "on_the_table", lambda: {"kind": "meaning"} if session == "lesson" else None)
    monkeypatch.setattr(trainer, "running", lambda: session == "workout")
    monkeypatch.setattr(studyintent, "call_for", lambda said, *a, **k: seen.append(("study", said)))
    monkeypatch.setattr(langintent, "call_for", lambda said, *a, **k: seen.append(("lesson", said)))
    monkeypatch.setattr(coachintent, "call_for", lambda said, *a, **k: seen.append(("workout", said)))
    reply, model = getattr(route, how)("Leave.")
    assert not [m for m in route.sent if m["type"].startswith("companion")] and not route.hud.companion_visible()
    assert (session, "Leave.") in seen, seen
    assert "As you wish" not in reply


def test_a_bare_leave_with_nothing_open_still_dismisses_him_on_the_pc(route, monkeypatch):
    from core import pcfast
    monkeypatch.setattr(pcfast, "_session_open", lambda: False)
    for how in ("typed", "spoken"):
        route.sent.clear()
        reply, model = getattr(route, how)("Leave.")
        assert "As you wish" in reply and not model and {"type": "companion_hide"} in route.sent


# ------------------------------------------------------------------ the page's own code, run against a fake DOM (node)
import shutil
import subprocess

NODE = shutil.which("node")


def run_page(scenario: dict, tmp_path) -> dict:
    sc = tmp_path / "scenario.json"
    sc.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "cc_harness.js"), str(APP / "command-center.js"), str(sc)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
NOW = 1_800_000_000


@needs_node
def test_page_shows_running_timers_and_a_focus_session_with_countdowns(tmp_path):
    out = run_page({"now": NOW, "read": ["#chips"], "steps": [
        {"event": {"type": "timers", "value": [{"id": "a1", "label": "Pasta", "ends": NOW + 125}, {"id": "b2", "label": "Done", "ends": NOW - 60}]}},
        {"event": {"type": "focus", "value": {"phase": "focus", "ends": NOW + 1500, "focus_min": 25}}}]}, tmp_path)
    chips = out["#chips"]
    assert chips["on"] and "Pasta" in chips["html"] and "2:05" in chips["html"] and "Focus" in chips["html"] and "25:00" in chips["html"]
    assert "Done" not in chips["html"]                           # a finished timer drops off


@needs_node
def test_page_clears_the_focus_chip_when_the_session_ends(tmp_path):
    out = run_page({"now": NOW, "read": ["#chips"], "steps": [
        {"event": {"type": "focus", "value": {"phase": "break", "ends": NOW + 60}}},
        {"event": {"type": "focus", "value": {"phase": "off"}}}]}, tmp_path)
    assert not out["#chips"]["on"] and out["#chips"]["html"] == ""


@needs_node
def test_page_shows_a_picture_card_and_clears_it(tmp_path):
    shown = run_page({"read": ["#media", "#mediaCap", "#mediaImg"], "steps": [
        {"event": {"type": "media", "src": "data:image/jpeg;base64,AAAA", "caption": "Your photo", "link": "https://example.com/x"}}]}, tmp_path)
    assert shown["#media"]["on"] and shown["#mediaCap"]["text"] == "Your photo" and shown["#mediaImg"]["src"].startswith("data:image/jpeg")
    cleared = run_page({"read": ["#media"], "steps": [
        {"event": {"type": "media", "src": "data:image/jpeg;base64,AAAA", "caption": "x"}}, {"event": {"type": "media_clear"}}]}, tmp_path)
    assert not cleared["#media"]["on"]
    refused = run_page({"read": ["#media"], "steps": [{"event": {"type": "media", "src": "javascript:alert(1)", "caption": "x"}}]}, tmp_path)
    assert not refused["#media"]["on"]                           # only pictures, never a script URL


@needs_node
def test_polling_pauses_while_hidden_never_overlaps_and_slow_data_is_slow(tmp_path):
    api = {"/api/system": {"cpu_pct": 5, "mem_pct": 5, "disk_pct": 5}, "/api/dashboard": {"today": []}, "/api/me": {"name": "Your Name"},
           "/api/fitness": {}, "/api/missions?active=1": {"missions": []}, "/api/timers": {"timers": []}, "/api/companion/visible": {"visible": False}}
    out = run_page({"now": NOW, "desk": True, "api": api, "steps": [{"event": {"type": "visibility", "value": False}}, {"fetchedReset": True},
                                                      {"advance": 70}, {"tick": 5000}, {"advance": 70}, {"tick": 5000}]}, tmp_path)
    assert out["fetched"] == []                                  # hidden window: not one request
    shown = run_page({"now": NOW, "desk": True, "api": api, "steps": [{"fetchedReset": True}, {"advance": 6}, {"tick": 5000}]}, tmp_path)
    assert "/api/system" in shown["fetched"] and "/api/dashboard" not in shown["fetched"]   # the dashboard waits its 60 s
    later = run_page({"now": NOW, "desk": True, "api": api, "steps": [{"fetchedReset": True}, {"advance": 61}, {"tick": 5000}]}, tmp_path)
    assert {"/api/system", "/api/dashboard", "/api/fitness"} <= set(later["fetched"])
    back = run_page({"now": NOW, "desk": True, "api": api, "steps": [{"event": {"type": "visibility", "value": False}}, {"fetchedReset": True},
                                                       {"event": {"type": "visibility", "value": True}}]}, tmp_path)
    assert "/api/system" in back["fetched"] and "/api/dashboard" in back["fetched"]       # shown again: refreshed at once


# ---- window creation, geometry events and the boot loader: the real functions, with a stand-in window library
class _Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class _FakeWindow:
    def __init__(self, **kw):
        self.kw, self.x, self.y, self.hidden, self.js = kw, None, None, False, []
        self.events = SimpleNamespace(resized=_Event(), moved=_Event(), closing=_Event(), restored=_Event(), minimized=_Event())

    def hide(self):
        self.hidden = True

    def evaluate_js(self, code):
        self.js.append(code)


@pytest.fixture
def cc_window(monkeypatch, tmp_path):
    import sys

    from hud import app
    made = []
    fake = SimpleNamespace(screens=[SimpleNamespace(x=0, y=0, width=1920, height=1080)],
                           create_window=lambda title, **kw: made.append(_FakeWindow(title=title, **kw)) or made[-1])
    monkeypatch.setitem(sys.modules, "webview", fake)
    store = tmp_path / "cc.json"
    monkeypatch.setattr(app, "_data_file", lambda name: store)
    api = app.Api("http://127.0.0.1:47000", "tok-1")
    api._screen = fake.screens[0]
    return SimpleNamespace(app=app, api=api, make=lambda: app._create_command_center_window(api), store=store)


def test_the_window_opens_a_boot_loader_that_waits_for_the_server(cc_window):
    win = cc_window.make()
    page = win.kw["html"]
    assert "url" not in win.kw and win.kw["js_api"] is cc_window.api and win.kw["resizable"]
    assert '"http://127.0.0.1:47000/command-center.html#t=tok-1"' in page      # where it goes once the server answers
    assert "no-cors" in page and "setTimeout(loop, 1000)" in page and "location.replace" in page   # retries until it does
    hostile = cc_window.app.cc_boot_html('http://x/</script><script>alert(1)')
    assert "</script><script>alert" not in hostile


def test_closing_the_window_hides_it_and_tells_the_page_but_quitting_closes(cc_window):
    win = cc_window.make()
    closing = win.events.closing[0]
    assert closing() is False and win.hidden and '"visibility"' in win.js[-1] and '"value": false' in win.js[-1]
    cc_window.api._quitting = True
    assert closing() is True


def test_geometry_is_saved_on_resize_and_move_but_never_the_minimised_position(cc_window):
    win = cc_window.make()
    win.x, win.y = 40, 30                                        # the window knows where it is; nothing was remembered yet
    win.events.resized[0](1600, 900)
    assert json.loads(cc_window.store.read_text(encoding="utf-8")) == {"width": 1600, "height": 900, "x": 40, "y": 30}
    win.events.moved[0](200, 120)
    assert json.loads(cc_window.store.read_text(encoding="utf-8"))["x"] == 200
    win.events.moved[0](-32000, -32000)                          # minimised
    win.events.resized[0](160, 28)                               # and its tiny size
    assert json.loads(cc_window.store.read_text(encoding="utf-8")) == {"width": 1600, "height": 900, "x": 200, "y": 120}


def test_a_saved_position_off_every_monitor_is_centred_on_the_primary(tmp_path):
    from hud import app
    path = tmp_path / "cc.json"
    two = [SimpleNamespace(x=0, y=0, width=1920, height=1080), SimpleNamespace(x=1920, y=0, width=1920, height=1080)]
    path.write_text(json.dumps({"width": 1500, "height": 850, "x": 2500, "y": 100}), encoding="utf-8")
    assert app.load_cc_geometry(path=path, screens=two)["x"] == 2500                 # on the second monitor: kept
    assert app.load_cc_geometry(path=path, screens=two[:1]) == {"width": 1500, "height": 850, "x": 210, "y": 115}   # it was unplugged
    path.write_text(json.dumps({"width": 1500, "height": 850, "x": -32000, "y": -32000}), encoding="utf-8")
    assert app.load_cc_geometry(path=path, screens=two)["x"] == 210                  # an old sentinel on disk is never used


def test_without_the_app_server_the_classic_panel_is_the_main_window_and_the_command_center_otherwise():
    from hud import app
    assert app.desktop_ui("", {}) == "panel" and app.desktop_ui("http://127.0.0.1:1", {}) == "command-center"
