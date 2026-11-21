"""Workout, Focus and Play are views inside the Command Center; the old panel never opens on its own.
No real window, model, network or wall clock."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
NOW = 1_800_000_000


def run_page(scenario, tmp_path):
    sc = tmp_path / "scenario.json"
    sc.write_text(json.dumps(scenario), encoding="utf-8")
    done = subprocess.run([NODE, str(ROOT / "tests" / "cc_harness.js"), str(APP / "command-center.js"), str(sc)],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_three_views_exist_and_are_wired():
    page = (APP / "command-center.html").read_text(encoding="utf-8")
    js = (APP / "command-center.js").read_text(encoding="utf-8")
    for key in ("workout", "focus", "play"):
        assert f'data-go="{key}"' in page
    assert 'id="focusView"' in page and 'id="focusBody"' in page
    assert '"fitness.html?tab=today"' in js and '"play.html"' in js and "/api/focus" in js
    assert (APP / "play.html").is_file() and (APP / "fitness.html").is_file()
    assert ".classic(" not in js and "side panel" not in js                    # nothing opens the old panel any more


def test_trainer_page_honours_the_tab_and_the_start_request():
    fit = (APP / "fitness.html").read_text(encoding="utf-8")
    assert 'get("tab")' in fit and '"start-workout"' in fit and "e.origin !== location.origin" in fit


def test_play_page_keeps_all_the_games_and_has_no_emoji():
    play = (APP / "play.html").read_text(encoding="utf-8")
    for game in ("trivia", "rps", "reflex", "ttt", "memory", "catchGame", "dance", "hoops", "soccer", "bowling", "treasure",
                 "hideseek", "dodge", "rhythm", "training"):
        assert f"function {game}(" in play, game
    assert not re.search("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]", play)
    assert "pywebview" not in play and "jarvisPlay" in play               # it talks to the window that hosts it, not to the bridge


RUN_PLAY = r"""
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const code = html.slice(html.indexOf("<script>") + 8, html.lastIndexOf("</script>"));
const nodes = {}, sent = [];
const mk = id => nodes[id] || (nodes[id] = { id, innerHTML: "", textContent: "", style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  setAttribute() {}, querySelector: () => mk("q"), querySelectorAll: () => [], appendChild() {}, addEventListener() {}, getBoundingClientRect: () => ({ width: 300, height: 200, left: 0, top: 0 }) });
const parent = { postMessage: m => sent.push(m) };
const ctx = { document: { querySelector: s => mk(s), querySelectorAll: () => [], addEventListener() {}, createElement: () => mk("n") },
  window: { addEventListener() {} }, parent, addEventListener() {},
  location: { hash: "", pathname: "/play.html" }, history: { replaceState() {} }, localStorage: { getItem: () => null, setItem() {} }, console, Math, JSON, Date, Promise, Array, Object, String, Number, Set, Map,
  setTimeout: () => 0, setInterval: () => 0, clearTimeout() {}, clearInterval() {}, requestAnimationFrame() {}, performance: { now: () => 0 },
  fetch: async () => ({ ok: false }), Audio: function () { this.play = async () => {}; }, AudioContext: function () {} };
ctx.window.parent = parent;
vm.createContext(ctx);
vm.runInContext(code + "\n;globalThis.__ids = GAMES.map(g => g[0]); globalThis.__start = startGame;", ctx);
const ids = ctx.__ids, errors = [];
for (const id of ids) { try { ctx.__start(id); } catch (e) { errors.push(id + ": " + e.message); } }
console.log(JSON.stringify({ ids, errors, sent: sent.length }));
"""


@needs_node
def test_play_page_starts_every_game_without_an_error(tmp_path):
    """The page's own script in a stand-in DOM: all 16 entries are listed and each one starts."""
    script = tmp_path / "run_play.js"
    script.write_text(RUN_PLAY, encoding="utf-8")
    done = subprocess.run([NODE, str(script), str(APP / "play.html")], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert len(out["ids"]) == 16 and out["errors"] == [] and out["sent"] > 0     # the games told the window (and so the companion)


@needs_node
@pytest.mark.parametrize("mode,title,src", [("workout", "Workout", "fitness.html?tab=today"), ("play", "Play", "play.html")])
def test_voice_and_tray_modes_open_the_right_page_inside_the_window(tmp_path, mode, title, src):
    out = run_page({"now": NOW, "read": ["#main", "#pageTitle", "#frame"], "steps": [{"event": {"type": "mode", "value": mode}}]}, tmp_path)
    assert out["#main"]["data"]["view"] == "page" and out["#pageTitle"]["text"] == title and out["#frame"]["data"]["src"] == src


@needs_node
def test_workout_is_started_in_the_embedded_page_and_a_game_is_passed_on(tmp_path):
    out = run_page({"now": NOW, "steps": [{"event": {"type": "mode", "value": "workout"}}, {"fire": ["#frame", "load"]}]}, tmp_path)
    assert out["posted"] == [{"jarvisCC": "start-workout"}]                      # sent once the Trainer page has loaded
    out = run_page({"now": NOW, "steps": [{"event": {"type": "mode", "value": "play", "game": "trivia"}}, {"fire": ["#frame", "load"]}]}, tmp_path)
    assert out["posted"] == [{"play": {"game": "trivia"}}]
    out = run_page({"now": NOW, "steps": [{"event": {"type": "mode", "value": "play"}}, {"fire": ["#frame", "load"]},
                                          {"event": {"type": "mode", "value": "play", "game": "rps"}}]}, tmp_path)
    assert out["posted"][-1] == {"play": {"game": "rps"}}                        # already showing: no waiting for a load


@needs_node
def test_focus_view_shows_presets_then_the_running_session(tmp_path):
    out = run_page({"now": NOW, "read": ["#main", "#focusBody"], "steps": [{"event": {"type": "mode", "value": "focus"}}]}, tmp_path)
    assert out["#main"]["data"]["view"] == "focus" and "Deep work" in out["#focusBody"]["html"] and "data-preset" in out["#focusBody"]["html"]
    run = {"phase": "focus", "ends": NOW + 1500, "focus_min": 25, "break_min": 5, "rounds": 4, "done": 0}
    out = run_page({"now": NOW, "api": {"/api/focus": run}, "read": ["#fLeft", "#fRound", "#fSkip"],
                    "steps": [{"event": {"type": "mode", "value": "focus"}}, {"event": {"type": "focus", "value": run}}]}, tmp_path)
    assert out["#fLeft"]["text"] == "25:00" and out["#fRound"]["text"] == "Block 1 of 4" and out["#fSkip"]["text"] == "Break now"


@needs_node
def test_window_takes_play_requests_only_from_its_own_origin(tmp_path):
    out = run_page({"now": NOW, "desk": True, "read": ["#main"], "steps": [
        {"message": {"origin": "http://127.0.0.1:1", "source": "frame", "data": {"jarvisPlay": {"go": "learn"}}}}]}, tmp_path)
    assert out["#main"]["data"]["view"] == "page"                              # "Teach Jarvis" opens the Learn page in the window
    foreign = run_page({"now": NOW, "desk": True, "read": ["#main"], "steps": [
        {"message": {"origin": "http://evil.example", "data": {"jarvisPlay": {"go": "learn"}}}}]}, tmp_path)
    assert foreign["#main"]["data"].get("view") in (None, "home")             # another origin is ignored


def test_the_old_panel_never_opens_on_its_own(monkeypatch):
    from hud import app
    src = (ROOT / "hud" / "app.py").read_text(encoding="utf-8")
    assert "def classic" not in src and "CLASSIC_MODES" not in src and "api.classic" not in src
    assert src.count("open_panel(") == 2                                       # its definition and the panel-mode fallback
    opened = []
    monkeypatch.setattr(app, "open_panel", lambda api: opened.append(1))

    class Gone:
        def show(self):
            raise RuntimeError("window gone")
    api = app.Api("", "")
    api._cc, api._window = Gone(), Gone()
    api.summon_panel()                                                         # even with the window failing
    assert opened == []
    api._cc = None                                                             # panel mode (JARVIS_DESKTOP_UI=panel) keeps the old behaviour
    api.summon_panel()
    assert opened == [1]


def test_companion_play_menu_opens_the_game_in_the_command_center():
    from hud import app
    shown = []

    class Win:
        def show(self): shown.append("show")
        def restore(self): pass
        def evaluate_js(self, js): shown.append(js)
    api = app.Api("", "")
    api._cc = Win()
    assert api.play_game("trivia") and '"value": "play"' in shown[-1] and '"game": "trivia"' in shown[-1]
    assert api._panel is None                                                  # no second window


def test_workout_focus_play_requests_from_tray_and_the_tool_reach_the_window(monkeypatch):
    from core import hud
    from tools import interface
    sent = []
    monkeypatch.setattr(hud, "running", lambda: True)
    monkeypatch.setattr(hud, "_send", sent.append)
    for action in ("workout", "play", "focus"):
        assert "isn't running" not in interface.companion(action)
    assert [m["value"] for m in sent if m.get("type") == "mode"] == ["workout", "play", "focus"]
    tray = (ROOT / "core" / "tray.py").read_text(encoding="utf-8")
    assert 'hud.mode("workout")' in tray and 'hud.mode("play")' in tray


def test_the_panel_stays_in_the_repo_for_the_panel_setting():
    from hud import app
    assert (ROOT / "hud" / "index.html").is_file()
    assert app.desktop_ui("http://127.0.0.1:1", {"JARVIS_DESKTOP_UI": "panel"}) == "panel"
    assert app.desktop_ui("http://127.0.0.1:1", {}) == "command-center"


@needs_node
def test_window_ignores_a_message_that_is_not_from_its_own_frame(tmp_path):
    out = run_page({"now": NOW, "desk": True, "read": ["#main"], "steps": [
        {"message": {"origin": "http://127.0.0.1:1", "data": {"jarvisPlay": {"go": "learn"}}}}]}, tmp_path)     # right origin, no frame source
    assert out["#main"]["data"].get("view") in (None, "home")


@needs_node
def test_guided_stretch_runs_six_moves_of_twenty_seconds(tmp_path):
    """The old panel's 2-minute stretch, now in the Focus view: from the stretch mode (tray, voice) and from a break."""
    steps = [{"event": {"type": "mode", "value": "stretch"}}]
    out = run_page({"now": NOW, "read": ["#main", "#focusBody"], "steps": steps}, tmp_path)
    assert out["#main"]["data"]["view"] == "focus" and "Stretch 1 of 6" in out["#focusBody"]["html"] and "Neck rolls" in out["#focusBody"]["html"]
    assert ">20<" in out["#focusBody"]["html"]
    after = run_page({"now": NOW, "read": ["#focusBody"], "steps": steps + [{"advance": 21}, {"tick": 1000}]}, tmp_path)
    assert "Stretch 2 of 6" in after["#focusBody"]["html"] and "Shoulder rolls" in after["#focusBody"]["html"]
    end = run_page({"now": NOW, "read": ["#focusBody"], "steps": steps + [{"advance": 21}, {"tick": 1000}] * 6}, tmp_path)
    assert "Stretch" not in end["#focusBody"]["html"] and "data-preset" in end["#focusBody"]["html"]       # six moves, then back to the presets


@needs_node
def test_break_offers_the_stretch_and_focus_does_not(tmp_path):
    brk = {"phase": "break", "ends": NOW + 300, "focus_min": 25, "break_min": 5, "rounds": 4, "done": 1}
    out = run_page({"now": NOW, "api": {"/api/focus": brk}, "read": ["#fStretch"], "steps": [{"event": {"type": "mode", "value": "focus"}}, {"event": {"type": "focus", "value": brk}}]}, tmp_path)
    assert out["#fStretch"]["text"] == "2-minute stretch" and not out["#fStretch"]["data"].get("hidden")
    foc = dict(brk, phase="focus")
    out = run_page({"now": NOW, "api": {"/api/focus": foc}, "read": ["#fStretch"], "steps": [{"event": {"type": "mode", "value": "focus"}}, {"event": {"type": "focus", "value": foc}}]}, tmp_path)
    assert out["#fStretch"]["text"] == "2-minute stretch"


def test_tray_has_a_stretch_item():
    assert 'hud.mode("stretch")' in (ROOT / "core" / "tray.py").read_text(encoding="utf-8")
