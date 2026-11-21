"""Reading a window (Mark III, 2026-11-09): one walk sorted into clickable / fields / shown; a second look for Chromium apps
that answer the first query empty; a suspended Store app is brought forward; a Store app listed twice resolves to its frame;
screen reading never reads another window's pixels."""
from types import SimpleNamespace

import pytest

from jarvis_pc.uiact import agent, uia


def _c(t, name, aid=""):
    return SimpleNamespace(element_info=SimpleNamespace(control_type=t, name=name, automation_id=aid))


def test_one_walk_sorts_controls_like_the_per_type_walks_did():
    tree = [_c("Text", "Hello"), _c("ListItem", "Nclex"), _c("Button", "New chat"), _c("Pane", "ignored"),
            _c("Edit", "", "prompt-textarea"), _c("Button", "Close"), _c("Button", "New chat"), _c("Text", "Hello")]
    out = agent.read_tree(tree)
    assert out == {"click": ["New chat", "Nclex"], "fields": ["prompt-textarea"], "shown": ["Hello"]}


def test_the_limit_is_per_bucket():
    tree = [_c("Button", f"b{i}") for i in range(5)] + [_c("Text", "t")]
    assert agent.read_tree(tree, limit=3) == {"click": ["b0", "b1", "b2"], "fields": [], "shown": ["t"]}


class _Win:
    def __init__(self, answers):
        self.answers = list(answers)

    def descendants(self):
        return self.answers.pop(0) if self.answers else []


def _patch(monkeypatch, win, front=True, focused=None):
    monkeypatch.setattr(uia, "window", lambda title: win)
    monkeypatch.setattr(uia, "_hwnd", lambda w: 1)
    monkeypatch.setattr(uia, "_in_front", lambda h: front)
    monkeypatch.setattr(uia, "focus", lambda w: (focused if focused is not None else []).append(w))
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    monkeypatch.setattr(uia, "screen_labels", lambda w: pytest.fail("no screen reading when the tree answers"))


def test_a_chromium_window_that_answers_empty_first_is_asked_again(monkeypatch):
    _patch(monkeypatch, _Win([[], [_c("Button", "New chat")]]))
    assert agent.observe("Claude")["click"] == ["New chat"]


def test_a_suspended_window_behind_others_is_brought_forward_and_read(monkeypatch):
    focused = []
    _patch(monkeypatch, _Win([[], [], [_c("ListItem", "Bluetooth & devices")]]), front=False, focused=focused)
    assert agent.observe("Settings")["click"] == ["Bluetooth & devices"] and len(focused) == 1


def test_a_store_app_listed_twice_resolves_to_its_frame(monkeypatch):
    from tools import pcops
    looked = []
    monkeypatch.setattr(uia, "_desktop", lambda: SimpleNamespace(
        window=lambda **kw: looked.append(kw) or SimpleNamespace(wrapper_object=lambda: None)))
    monkeypatch.setattr(pcops, "windows", lambda: [
        {"title": "Settings", "hwnd": 5, "process": "SystemSettings.exe"},
        {"title": "Settings", "hwnd": 9, "process": "ApplicationFrameHost.exe"}])
    uia.window("Settings")
    assert looked == [{"handle": 9}]


def test_screen_reading_brings_the_window_forward_first(monkeypatch):
    from core import pcvision
    order = []
    monkeypatch.setattr(uia, "rect", lambda w: (0, 0, 500, 400))
    monkeypatch.setattr(uia, "_hwnd", lambda w: 1)
    monkeypatch.setattr(uia, "_in_front", lambda h: False)
    monkeypatch.setattr(uia, "focus", lambda w: order.append("focus"))
    monkeypatch.setattr(pcvision, "clear_cache", lambda: None)
    monkeypatch.setattr(pcvision, "read_screen", lambda **kw: order.append("read") or {"ocr": []})
    uia.screen_labels(object())
    assert order == ["focus", "read"]

    def cannot(w):
        raise uia.UIError("could not bring the window to the front")
    monkeypatch.setattr(uia, "focus", cannot)
    order.clear()
    with pytest.raises(uia.UIError):
        uia.screen_labels(object())
    assert order == []                                    # never reads pixels that may belong to another window
