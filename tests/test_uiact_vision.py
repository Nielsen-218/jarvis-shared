"""The screen-vision fallback: when UI Automation sees nothing, words read from the window's pixels become the names the loop may
target, and a name becomes a real click only inside that window."""
from types import SimpleNamespace

import pytest

from jarvis_pc.guardrails import Guardrails
from jarvis_pc.uiact import agent, lang
from jarvis_pc.uiact.executor import Executor


class Screen:
    """A custom-drawn window: UIA finds no controls, OCR reads its words; clicking 'Add' opens a menu with 'Mesh'."""

    def __init__(self):
        self.clicks = []
        self.menu_open = False
        self.rect_ = (100, 100, 900, 700)

    def window(self, title):
        return "win"

    def rect(self, win):
        return self.rect_

    def screen_labels(self, win):
        base = [{"text": "File", "center": [120, 110], "confidence": 0.9}, {"text": "Add", "center": [300, 140], "confidence": 0.95},
                {"text": "Object Mode", "center": [200, 140], "confidence": 0.8}]
        return base + ([{"text": "Mesh", "center": [310, 180], "confidence": 0.9}] if self.menu_open else [])

    def click_at(self, win, x, y):
        self.clicks.append((x, y))
        self.menu_open = self.menu_open or (x, y) == (300, 140)

    def focus(self, win):
        pass


@pytest.fixture
def guard(tmp_path):
    return Guardrails(allowlist_roots=[str(tmp_path)], audit_path=str(tmp_path / "a.jsonl"), dry_run=False)


def test_tap_is_in_the_language_and_still_a_name_not_a_coordinate():
    assert lang.parse('tap "Add"').verb == "tap"
    with pytest.raises(lang.UIParseError):
        lang.parse("tap 300 140")


def test_tap_clicks_the_word_read_from_the_screen_inside_the_bound_window(guard):
    screen = Screen()
    ex = Executor(guard, "Blender", owned=True, backend=screen, input_tick=lambda: 1)
    r = ex.run('tap "add"')
    assert r.ok and r.stdout == "Add" and screen.clicks == [(300, 140)]
    r = ex.run('tap "Render"')
    assert not r.ok and "cannot see 'Render'" in r.stderr and screen.clicks == [(300, 140)]


def test_a_point_outside_the_window_is_never_clicked():
    from jarvis_pc.uiact import uia
    from jarvis_pc.uiact.executor import UIError

    class Win:
        pass
    uia_rect = uia.rect
    try:
        uia.rect = lambda win: (100, 100, 900, 700)
        with pytest.raises(UIError):
            uia.click_at(Win(), 2000, 50)
    finally:
        uia.rect = uia_rect


def test_the_loop_targets_screen_words_taps_them_and_proves_from_the_screen(guard):
    screen = Screen()
    ex = Executor(guard, "Blender", owned=True, backend=screen, input_tick=lambda: 1)
    views = []

    def observe(title):
        words = [x["text"] for x in screen.screen_labels("win")]
        view = {"click": words, "fields": [], "shown": words, "_vision": True}
        views.append(view)
        return view
    choices = iter([{"action": "click", "target": "Add", "text": ""}, {"action": "click", "target": "Mesh", "text": ""},
                    {"action": "done", "target": "Mesh", "text": ""}])
    out = agent.run("open the Add menu and pick Mesh", "Blender", ex, observe_fn=observe, plan_first=False, ask=lambda *a: next(choices))
    assert out.ok and out.answer == "Mesh"
    assert screen.clicks == [(300, 140), (310, 180)], "names became points only through what was read from the screen"
    assert [s.action for s in out.steps] == ["click", "click", "done"]


def test_the_model_is_told_what_appeared_after_its_click_and_may_only_name_screen_words(guard):
    screen = Screen()
    ex = Executor(guard, "Blender", owned=True, backend=screen, input_tick=lambda: 1)
    bodies, schemas = [], []

    def observe(title):
        words = [x["text"] for x in screen.screen_labels("win")]
        return {"click": words, "fields": [], "shown": words, "_vision": True}

    def ask(system, body, schema, cancelled):
        bodies.append(body)
        schemas.append(schema)
        return {"action": "click", "target": "Add", "text": ""} if len(bodies) == 1 else {"action": "done", "target": "Mesh", "text": ""}
    out = agent.run("add a mesh", "Blender", ex, observe_fn=observe, ask=ask)
    assert out.ok
    assert "APPEARED AFTER YOUR LAST ACTION" not in bodies[0] and 'APPEARED AFTER YOUR LAST ACTION: ["Mesh"]' in bodies[1]
    assert schemas[1]["properties"]["target"]["enum"] == ["Add", "File", "Mesh", "Object Mode", ""]


def test_observe_falls_back_to_screen_words_when_uia_sees_nothing(monkeypatch):
    from jarvis_pc.uiact import uia

    class Empty:
        def descendants(self, control_type=None):
            return []
    monkeypatch.setattr(uia, "window", lambda title: Empty())
    monkeypatch.setattr(uia, "screen_labels", lambda win: [{"text": "Add", "center": [1, 1], "confidence": 0.9}, {"text": "File", "center": [2, 2], "confidence": 0.9},
                                                           {"text": "×", "center": [3, 3], "confidence": 0.9}, {"text": "凸", "center": [4, 4], "confidence": 0.9}])
    view = agent.observe("Blender")
    assert view == {"click": ["Add", "File"], "fields": [], "shown": ["Add", "File"], "_vision": True}
    system, _body = agent._prompt("x", view, [])
    assert "READ FROM THE SCREEN" in system
