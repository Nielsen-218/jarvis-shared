"""Fast, no-desktop regression checks for deterministic PC command routing."""

import sys
import types

import pytest

from core import pcactions, pchybrid


@pytest.mark.parametrize(("said", "tool", "arguments"), [
    ("close calculator", "close_app", {"app": "calculator"}),
    ("turn off calculator", "close_app", {"app": "calculator"}),
    ("I don't want to see calculator", "close_app", {"app": "calculator"}),
    ("close it", "close_foreground", {}),
    ("close this tab", "close_browser_tab", {}),
    ("close YouTube", "close_site_tab", {"site": "youtube"}),
    ("close you to", "close_site_tab", {"site": "youtube"}),
    ("open YouTube", "open_url", {"url": "https://www.youtube.com/"}),
    ("take me to YouTube", "open_url", {"url": "https://www.youtube.com/"}),
    ("fire up YouTube", "open_url", {"url": "https://www.youtube.com/"}),
    ("opem You Tube", "open_url", {"url": "https://www.youtube.com/"}),
    ("search YouTube for Blender tutorials", "open_url", {"url": "https://www.youtube.com/results?search_query=Blender+tutorials"}),
    ("open YouTube and look for Blender tutorials", "open_url", {"url": "https://www.youtube.com/results?search_query=Blender+tutorials"}),
    ("Open YouTube, search for Cocomelon", "open_url", {"url": "https://www.youtube.com/results?search_query=Cocomelon"}),
    ("Open YouTube, look for Cocomelon", "open_url", {"url": "https://www.youtube.com/results?search_query=Cocomelon"}),
    ("Look up Cocomelon on YouTube. I have it open.", "open_url", {"url": "https://www.youtube.com/results?search_query=Cocomelon"}),
    # Owner decision 2026-11-04: a find/search ... on YouTube request opens a YouTube search; it is not answered with a spoken list.
    ("Find me two Blender rigging tutorials on YouTube.", "open_url", {"url": "https://www.youtube.com/results?search_query=two+Blender+rigging+tutorials"}),
    ("find me a Blender rigging tutorial on YouTube", "open_url", {"url": "https://www.youtube.com/results?search_query=a+Blender+rigging+tutorial"}),
    ("show me Cocomelon on YouTube", "open_url", {"url": "https://www.youtube.com/results?search_query=Cocomelon"}),
    ("open notepad", "open_app", {"app": "notepad"}),
    ("run notepad", "open_app", {"app": "notepad"}),
    ("pull up notepad", "open_app", {"app": "notepad"}),
    ("boot up notepad", "open_app", {"app": "notepad"}),
])
def test_common_pc_commands_have_a_safe_typed_plan(said, tool, arguments):
    plan = pchybrid.simple_plan(said)
    assert plan == {"steps": [{"tool": tool, "arguments": arguments}]}
    assert pcactions.validate(plan) == plan


def test_only_explicit_deterministic_close_plans_skip_the_second_review():
    assert pchybrid.direct_owner_close(pchybrid.simple_plan("close calculator"), "local_automation")
    assert pchybrid.direct_owner_close(pchybrid.simple_plan("close this tab"), "local_automation")
    assert pchybrid.direct_owner_close(pchybrid.simple_plan("close YouTube"), "local_automation")
    assert not pchybrid.direct_owner_close(pchybrid.simple_plan("open YouTube"), "local_automation")
    assert not pchybrid.direct_owner_close(pchybrid.simple_plan("close calculator"), "local_ai")


@pytest.mark.parametrize(("said", "direction"), [
    ("scroll down", "down"), ("scrol down", "down"), ("move down", "down"), ("page up", "up"), ("show me more", "down"),
    ("keep going", "down"), ("a little higher", "up"), ("go all the way down", "down"),
    ("go all the way up", "up"), ("go to the top", "top"), ("jump to the start", "top"),
    ("take me to the end", "bottom"), ("go all the way to the bottom", "bottom"),
])
def test_scroll_phrases_control_only_the_foreground_app(said, direction):
    plan = pchybrid.simple_plan(said)
    assert plan == {"steps": [{"tool": "scroll_foreground", "arguments": {"direction": direction}}]}
    assert pcactions.validate(plan) == plan


def test_short_lookup_uses_the_browser_already_in_front(monkeypatch):
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: {"site": "youtube", "hwnd": 1})
    for said in ("look up Cocomelon", "look for Cocomelon", "find Cocomelon", "fynd Cocomelon", "search up Cocomelon", "google Cocomelon", "pull up Cocomelon", "show me Cocomelon"):
        assert pchybrid.simple_plan(said) == {
            "steps": [{"tool": "search_foreground_browser", "arguments": {"query": "Cocomelon"}}]}, said


def test_short_lookup_without_a_foreground_browser_remains_normal_research(monkeypatch):
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: None)
    assert pchybrid.simple_plan("look up Cocomelon") is None


def test_safe_local_language_fallback_can_only_navigate_the_foreground(monkeypatch):
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: {"site": "web", "hwnd": 1})
    monkeypatch.setattr(pchybrid.pcplanner, "local_plan", lambda task, cancelled: {
        "steps": [{"tool": "scroll_foreground", "arguments": {"direction": "bottom"}}]})
    plan = pchybrid._semantic_foreground_plan("could you take this article right to the end for me", lambda: False)
    assert plan == {"steps": [{"tool": "scroll_foreground", "arguments": {"direction": "bottom"}}]}
    assert pchybrid.direct_owner_foreground(plan, "local_foreground_ai")


def test_safe_local_language_fallback_rejects_everything_except_navigation(monkeypatch):
    monkeypatch.setattr(pcactions, "foreground_browser", lambda: {"site": "web", "hwnd": 1})
    monkeypatch.setattr(pchybrid.pcplanner, "local_plan", lambda task, cancelled: {
        "steps": [{"tool": "close_foreground", "arguments": {}}]})
    assert pchybrid._semantic_foreground_plan("could you take this article right to the end for me", lambda: False) is None


def test_named_site_close_verifies_the_real_browser_tab_not_a_window_title(monkeypatch):
    """A stale Edge title must never turn an unchanged YouTube tab into success."""
    state = {"tabs": ["A YouTube video - Memory usage - 100 MB", "A Google search - Memory usage - 90 MB"],
             "selected": None, "shortcuts": []}

    class Tab:
        element_info = types.SimpleNamespace(control_type="TabItem")

        def __init__(self, title):
            self.title = title

        def window_text(self):
            return self.title

        def select(self):
            state["selected"] = self.title

        def click_input(self):
            self.select()

    class Window:
        def set_focus(self):
            return None

        def descendants(self, control_type=None):
            return [Tab(title) for title in state["tabs"]]

        def type_keys(self, keys):
            state["shortcuts"].append(keys)
            if keys == "^w" and state["selected"]:
                state["tabs"].remove(state["selected"])

    window = Window()
    class Desktop:
        def __init__(self, backend):
            assert backend == "uia"

        def window(self, handle):
            assert handle == 11
            return window

    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    monkeypatch.setattr("tools.pcops.windows", lambda: [{"hwnd": 11, "process": "msedge.exe",
                                                           "title": "Stale YouTube title", "foreground": True}])

    result = pcactions._close_named_site_tab("youtube")

    assert result["verified"] is True
    assert state["tabs"] == ["A Google search - Memory usage - 90 MB"]
    assert state["shortcuts"] == ["^w"]
