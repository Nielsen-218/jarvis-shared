"""Mark III start: "click X in <app>" (live 2026-11-08: "Click on Autonomous Blender Learning in ChatGPT" only opened ChatGPT,
the wrong plan was saved as a shortcut, and the model loop then gave up after 73 s)."""
from types import SimpleNamespace

import pytest

from core import pchybrid, pcuse


@pytest.fixture(autouse=True)
def _no_switches(monkeypatch):
    monkeypatch.setattr(pcuse, "is_switch", lambda title, name: False)


VIEW = {"click": ["New chat", "Autonomous Blender Learning", "Delete chat", "Close", "Nclex"], "fields": [], "shown": []}


class Recorder:
    def __init__(self, ok=True):
        self.lines, self.ok = [], ok

    def run(self, line):
        self.lines.append(line)
        return SimpleNamespace(ok=self.ok)


def test_one_named_control_on_screen_is_clicked_directly():
    ex = Recorder()
    out = pcuse.one_click("click on Autonomous Blender Learning", "ChatGPT", ex, observe_fn=lambda t: VIEW, display="ChatGPT")
    assert out["verified"] and out["detail"] == "Clicked Autonomous Blender Learning in ChatGPT."
    assert ex.lines == ['click "Autonomous Blender Learning"']
    assert pcuse.one_click("Click the Nclex project.", "ChatGPT", Recorder(), observe_fn=lambda t: VIEW)["verified"]


def test_anything_unclear_or_risky_goes_to_the_normal_loop():
    for goal in ("click on Delete chat", "click Close", "click on Something Not There", "draw a circle", "summarize this chat"):
        ex = Recorder()
        assert pcuse.one_click(goal, "ChatGPT", ex, observe_fn=lambda t: VIEW) is None, goal
        assert ex.lines == [] or goal == "click on Something Not There"
    assert pcuse.one_click("click on Nclex", "ChatGPT", Recorder(ok=False), observe_fn=lambda t: VIEW) is None


def test_a_plan_that_only_opens_the_app_is_not_saved_for_a_sentence_asking_for_work_inside_it():
    opened = {"steps": [{"tool": "open_app", "arguments": {"app": "chatgpt"}}]}
    assert not pchybrid.learnable("Click on autonomous blender learning in ChatGPT.", opened)
    assert pchybrid.learnable("Open ChatGPT.", opened)
    assert pchybrid.learnable("minimize claude", {"steps": [{"tool": "minimize_app", "arguments": {"app": "claude"}}]})


def test_click_words_mean_work_inside_the_app():
    from core.gateway import _DOING
    for said in ("Click on autonomous blender learning in ChatGPT", "select the second tab in settings", "press equals in calculator"):
        assert _DOING.search(said), said
    assert not _DOING.search("Open ChatGPT") and not _DOING.search("bring Claude forward")


def test_click_in_app_sentences_go_straight_to_the_in_app_tool(monkeypatch):
    from core import gateway
    from tools import apps
    monkeypatch.setattr(apps, "resolve_app", lambda name: {"chatgpt": "chatgpt", "calculator": "calculator"}.get(name.casefold()))
    use = lambda app, goal: {"steps": [{"tool": "use_app", "arguments": {"app": app, "goal": goal}}]}  # noqa: E731
    assert gateway.click_in_app("Click on autonomous blender learning in ChatGPT.") == use("chatgpt", "click on autonomous blender learning")
    assert gateway.click_in_app("In ChatGPT, click on Autonomous Blender Learning.") == use("chatgpt", "click on Autonomous Blender Learning")
    assert gateway.click_in_app("Jarvis, press equals in calculator") == use("calculator", "click on equals")
    for said in ("click the button", "What is in ChatGPT?", "Select the second tab in my head", "open ChatGPT"):
        assert gateway.click_in_app(said) is None, said


SETTINGS = {"click": ["Back", "Home", "Bluetooth", "More options", "Add device", "Bluetooth & devices", "Network & internet",
                      "Xbox Wireless Controller, Category Gaming input, Battery 95%, State Bluetooth is"],
            "fields": [], "shown": []}


def test_click_never_flips_a_switch_it_opens_the_page_that_carries_the_name():
    # Live 2026-11-09: "In Settings, click on Bluetooth" turned Bluetooth ON (the home page's switch is named 'Bluetooth').
    ex = Recorder()
    out = pcuse.one_click("click on Bluetooth", "Settings", ex, observe_fn=lambda t: SETTINGS, display="Settings",
                          switch_fn=lambda title, name: name == "Bluetooth")
    assert ex.lines == ['click "Bluetooth & devices"'] and out["detail"] == "Clicked Bluetooth & devices in Settings."


def test_a_switch_with_no_single_page_is_left_alone_and_said():
    import pytest
    view = {"click": ["Dark mode", "Dark mode schedule", "Dark mode colours"], "fields": [], "shown": []}
    ex = Recorder()
    with pytest.raises(RuntimeError, match="on/off switch"):
        pcuse.one_click("click Dark mode", "Settings", ex, observe_fn=lambda t: view, switch_fn=lambda t, n: n == "Dark mode")
    assert ex.lines == []


def test_when_the_switch_check_cannot_look_the_normal_loop_decides():
    def broken(title, name):
        raise RuntimeError("no window")
    ex = Recorder()
    assert pcuse.one_click("click on Bluetooth", "Settings", ex, observe_fn=lambda t: SETTINGS, switch_fn=broken) is None
    assert ex.lines == []


def test_a_guessed_icon_name_is_never_clicked_directly():
    # Live 2026-11-09: Blender's "Rotate icon" was a header icon; the real Rotate tool had been named "Move".
    view = {"click": ["File", "Edit", "Rotate icon", "Move icon"], "fields": [], "shown": [], "_vision": True}
    ex = Recorder()
    assert pcuse.one_click("click on the Rotate icon", "Blender", ex, observe_fn=lambda t: view) is None
    assert ex.lines == []
    out = pcuse.one_click("click on File", "Blender", ex, observe_fn=lambda t: view)    # words read off the screen still work
    assert ex.lines == ['tap "File"'] and out["verified"]
