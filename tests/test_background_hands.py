"""Mark VI part 3 (2026-11-10): background hands. Text and keys reach a window WHERE IT IS, behind the owner's windows.

Fake controls only (no app opens, nothing is typed for real): uia.set_text_bg uses the Value pattern when there is one,
else WM_SETTEXT / EM_REPLACESEL on the native handle, and raises when the read-back does not show the text; key_bg posts
one key to the window's own focused child and refuses chords; the executor and pcverbs.type_text route to those in
background mode and never call uia.focus, the clipboard paste or pyautogui; save_document refuses.
"""
import os
from types import SimpleNamespace

import pytest

from jarvis_pc.guardrails.stepfence import BackgroundUnavailable, background_only
from jarvis_pc.uiact import uia


class _Field:
    """A text control whose text only changes when something really writes it."""

    def __init__(self, text="", handle=0, name="Text editor"):
        self.text = text
        self.element_info = SimpleNamespace(element=object(), handle=handle, name=name, automation_id="")

    def is_enabled(self):
        return True

    def is_visible(self):
        return True

    def get_value(self):
        return self.text

    def top_level_parent(self):
        return SimpleNamespace(handle=9000)


class _Value:
    def __init__(self, field, works=True):
        self.field, self.works, self.calls = field, works, []

    def SetValue(self, v):
        self.calls.append(v)
        if self.works:
            self.field.text = v


@pytest.fixture
def messages(monkeypatch):
    """win32gui message calls, recorded; WM_SETTEXT / EM_REPLACESEL really change the fake field when `live`."""
    import win32con
    import win32gui
    state = SimpleNamespace(sent=[], posted=[], field=None, live=True)

    def send(h, msg, w, l):
        state.sent.append((h, msg, w, l))
        if state.live and state.field is not None:
            if msg == win32con.WM_SETTEXT:
                state.field.text = l
            elif msg == uia.EM_REPLACESEL:
                state.field.text += l
        return 1
    monkeypatch.setattr(win32gui, "SendMessage", send)
    monkeypatch.setattr(win32gui, "PostMessage", lambda h, msg, w, l: state.posted.append((h, msg, w, l)))
    monkeypatch.setattr(win32gui, "SetForegroundWindow", lambda *a: pytest.fail("never SetForegroundWindow"))
    monkeypatch.setattr(uia, "focus", lambda w: pytest.fail("never uia.focus"))
    return state


def test_the_value_pattern_is_used_when_the_field_has_one(messages, monkeypatch):
    field = _Field("old ")
    value = _Value(field)
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: value)
    assert uia.set_text_bg(field, "hello") == "value"
    assert field.text == "hello" and messages.sent == [] and messages.posted == []
    assert uia.set_text_bg(field, " world", append=True) == "value"
    assert field.text == "hello world" and value.calls[-1] == "hello world"


def test_without_a_value_pattern_wm_settext_goes_to_the_native_handle(messages, monkeypatch):
    import win32con
    field = _Field("", handle=55)
    messages.field = field
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: None)
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 55)
    assert uia.set_text_bg(field, "W3-WIN CONFIRMED") == "wm_settext"
    assert messages.sent == [(55, win32con.WM_SETTEXT, 0, "W3-WIN CONFIRMED")]
    assert uia.set_text_bg(field, " more", append=True) == "em_replacesel"      # after the caret went to the end
    assert messages.sent[-1] == (55, uia.EM_REPLACESEL, 1, " more") and field.text == "W3-WIN CONFIRMED more"
    assert messages.sent[-2][1] == uia.EM_SETSEL


def test_a_value_pattern_that_refuses_outright_falls_through_to_the_window_message(messages, monkeypatch):
    class Refuses(_Value):
        def SetValue(self, v):
            raise RuntimeError("ElementNotEnabled")
    field = _Field("", handle=55)
    messages.field = field
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: Refuses(field))
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 55)
    assert uia.set_text_bg(field, "hello") == "wm_settext"


def test_a_write_that_cannot_be_read_back_stops_and_is_never_written_again(messages, monkeypatch):
    """Review finding 14: a rung that SENT a write but cannot read it back must not fall through to the next writing
    rung (text typed twice), and must not claim 'nothing was done' (BackgroundUnavailable)."""
    from jarvis_pc.guardrails.stepfence import UncertainOutcome
    field = _Field("", handle=55)
    messages.field, messages.live = field, False                       # the messages are swallowed: nothing shows
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: None)
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 55)
    with pytest.raises(UncertainOutcome):
        uia.set_text_bg(field, "hi", append=True)
    assert [m[1] for m in messages.sent].count(uia.EM_REPLACESEL) == 1 and messages.posted == []   # no WM_CHAR after it
    value = _Value(field, works=False)                                  # SetValue 'worked' but the read-back is empty
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: value)
    sent = len(messages.sent)
    with pytest.raises(UncertainOutcome):
        uia.set_text_bg(field, "hi")
    assert len(value.calls) == 1 and len(messages.sent) == sent and messages.posted == []
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: None)
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 0)
    with pytest.raises(BackgroundUnavailable):
        uia.set_text_bg(field, "hi")                                     # no Value, no native handle: nothing sent, refuse


def test_an_append_never_replaces_his_selection_and_checks_his_text_is_kept(messages, monkeypatch):
    """Review finding 12: EM_REPLACESEL replaces the selection he left; the caret goes to the end first, and an append
    that changed the text before it is never reported as done."""
    from jarvis_pc.guardrails.stepfence import UncertainOutcome
    field = _Field("his notes. a highlighted paragraph", handle=55)
    sel = {"start": 11, "end": 34}                                     # "a highlighted paragraph" is selected

    def send(h, msg, w, l):
        messages.sent.append((h, msg, w, l))
        if msg == uia.WM_GETTEXTLENGTH:
            return len(field.text)
        if msg == uia.EM_SETSEL:
            sel.update(start=w, end=l)
        if msg == uia.EM_REPLACESEL:
            field.text = field.text[:sel["start"]] + l + field.text[sel["end"]:]
        return 1
    import win32gui
    monkeypatch.setattr(win32gui, "SendMessage", send)
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: None)
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 55)
    assert uia.set_text_bg(field, " X", append=True) == "em_replacesel"
    assert field.text == "his notes. a highlighted paragraph X"         # nothing of his replaced
    # A Value pattern that rewrites the field and loses his text: never a success.
    field2 = _Field("keep this")

    class Lossy(_Value):
        def SetValue(self, v):
            self.field.text = "X"
    monkeypatch.setattr(uia, "_value_pattern", lambda ctl: Lossy(field2))
    with pytest.raises(UncertainOutcome):
        uia.set_text_bg(field2, "X", append=True)


def test_strict_native_handle_never_picks_one_of_several_editors(monkeypatch):
    """Review finding 13: Windows 11 Notepad keeps a hidden tab's editor in the same window: with two text children the
    background write gets no handle at all (it refuses) instead of 'the first one'."""
    import win32gui
    monkeypatch.setattr(win32gui, "GetClassName", lambda h: "RichEditD2DPT" if h in (101, 102) else "Frame")
    monkeypatch.setattr(win32gui, "EnumChildWindows", lambda top, visit, x: [visit(h, x) for h in (101, 102)])
    field = _Field("", handle=0)
    assert uia._native_text_hwnd(field, strict=True) == 0
    assert uia._native_text_hwnd(field) == 101                           # the old, non-strict pick (foreground callers)
    monkeypatch.setattr(win32gui, "EnumChildWindows", lambda top, visit, x: [visit(h, x) for h in (102,)])
    assert uia._native_text_hwnd(field, strict=True) == 102


def test_key_bg_posts_one_key_to_the_windows_own_focused_child_and_refuses_chords(messages, monkeypatch):
    import win32con
    win = SimpleNamespace(wrapper_object=lambda: SimpleNamespace(handle=9000))
    monkeypatch.setattr(uia, "_focused_child", lambda hwnd: 777 if hwnd == 9000 else 0)
    monkeypatch.setattr(uia, "_root", lambda hwnd: 9000 if hwnd in (777, 9000) else 4242)
    uia.key_bg(win, "enter")
    assert [(m[0], m[1], m[2]) for m in messages.posted] == [(777, win32con.WM_KEYDOWN, 0x0D), (777, win32con.WM_KEYUP, 0x0D)]
    assert messages.posted[1][3] & 0xC0000000
    with pytest.raises(BackgroundUnavailable, match="ctrl"):
        uia.key_bg(win, "ctrl+s")
    assert len(messages.posted) == 2, "nothing posted for a chord"
    monkeypatch.setattr(uia, "_focused_child", lambda hwnd: 0)
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 0)
    with pytest.raises(BackgroundUnavailable):
        uia.key_bg(win, "down")


def test_key_bg_never_posts_into_another_top_level_window_of_the_same_thread(messages, monkeypatch):
    """Review finding 11: Edge runs all its windows on one UI thread; that thread's focus can be in HIS other window (B),
    where Enter would submit whatever he has open. A key aimed at window A is never posted there."""
    win = SimpleNamespace(wrapper_object=lambda: SimpleNamespace(handle=9000))          # the mission's window A
    monkeypatch.setattr(uia, "_focused_child", lambda hwnd: 888)                        # the thread's focus: inside B
    monkeypatch.setattr(uia, "_root", lambda hwnd: 9000 if hwnd == 9000 else 5555)      # 888 belongs to B (5555)
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 0)
    with pytest.raises(BackgroundUnavailable):
        uia.key_bg(win, "enter")
    assert messages.posted == []
    monkeypatch.setattr(uia, "_native_text_hwnd", lambda ctl, strict=False: 889 if strict else 0)
    monkeypatch.setattr(uia, "_root", lambda hwnd: 9000 if hwnd in (9000, 889) else 5555)
    uia.key_bg(win, "enter")                                                            # A's own only text child
    assert {m[0] for m in messages.posted} == {889}


def test_the_executor_types_only_into_the_windows_only_text_area(monkeypatch):
    """Review finding 13 (executor path): uia.editable(only=True) refuses a window with several text areas."""
    class Win:
        def descendants(self, control_type=None):
            return [_Field("readme"), _Field("")] if control_type == "Document" else []
    with pytest.raises(BackgroundUnavailable, match="more than one"):
        uia.editable(Win(), only=True)


class _BgUI:
    """A fake uia for the executor: background verbs record, foreground ones fail the test."""

    def __init__(self):
        self.calls = []
        self.field = _Field()

    def window(self, title):
        return SimpleNamespace(title=title)

    def editable(self, win, only=False):
        self.calls.append(("editable", only))
        return self.field

    def is_password(self, ctl):
        return False

    def set_text_bg(self, ctl, text, append=False):
        self.calls.append(("set_text_bg", text, append))

    def key_bg(self, win, keys):
        self.calls.append(("key_bg", keys))
        if "+" in keys:
            raise BackgroundUnavailable(f"press {keys}")

    def focus(self, win):
        pytest.fail("the executor focused the window in background")

    def send_keys(self, win, keys, literal=False):
        pytest.fail("the executor sent real keys in background")


def _executor(tmp_path, ui):
    from jarvis_pc.guardrails import Guardrails
    from jarvis_pc.uiact.executor import Executor
    guard = Guardrails(allowlist_roots=[str(tmp_path)], audit_path=str(tmp_path / "a.jsonl"), approver=None,
                       dry_run=False)
    return Executor(guard, "Practice abc", input_tick=lambda: 1, sleep=lambda s: None, backend=ui)


def test_the_executor_types_and_presses_keys_in_the_background(tmp_path):
    ui = _BgUI()
    ex = _executor(tmp_path, ui)
    with background_only():
        assert ex.run('type "hello there"').ok
        assert ex.run("key enter").ok
        r = ex.run("hotkey ctrl+s")
    assert not r.ok and r.error_kind == "background_unavailable"
    assert ui.calls == [("editable", True), ("set_text_bg", "hello there", True), ("key_bg", "enter"), ("key_bg", "ctrl+s")]


def test_the_executor_outside_background_is_unchanged(tmp_path):
    class FgUI(_BgUI):
        def focus(self, win):
            self.calls.append(("focus",))

        def send_keys(self, win, keys, literal=False):
            self.calls.append(("keys", keys))
    ui = FgUI()
    ex = _executor(tmp_path, ui)
    assert ex.run('type "hi"').ok and ex.run("key enter").ok
    assert ui.calls == [("focus",), ("keys", "hi"), ("focus",), ("keys", "{ENTER}")]


class _NotepadWin:
    def __init__(self, title, field):
        self.title, self.field = title, field

    def window_text(self):
        return self.title

    def descendants(self, control_type=None):
        return [self.field] if control_type == "Document" else []


def _bg_notepad(monkeypatch, title, field):
    from core import pcverbs
    import tools.computer as computer
    win = _NotepadWin(title, field)
    monkeypatch.setattr(pcverbs, "_one_window", lambda app, cancelled=None: ("notepad", {"title": title, "hwnd": 1}))
    monkeypatch.setattr(uia, "window", lambda t: win)
    monkeypatch.setattr(uia, "focus", lambda w: pytest.fail("type_text focused the window in background"))
    monkeypatch.setattr(pcverbs, "_keys", lambda *k: pytest.fail("type_text pressed keys in background"))
    monkeypatch.setattr(computer, "_type_text", lambda t: pytest.fail("type_text pasted in background"))
    monkeypatch.setattr(pcverbs, "_focused_editor", lambda: pytest.fail("read the GLOBAL focused element"))
    monkeypatch.setattr(pcverbs.time, "sleep", lambda s: None)
    return win


def test_type_text_in_background_writes_into_the_windows_own_editor_and_reads_it_back(monkeypatch):
    from core import pcverbs
    field = _Field("")
    _bg_notepad(monkeypatch, "Untitled - Notepad", field)
    seen = []

    def set_text_bg(ctl, text, append=False):
        seen.append((ctl, text, append))
        ctl.text += text
        return "em_replacesel"
    monkeypatch.setattr(uia, "set_text_bg", set_text_bg)
    with background_only():
        out = pcverbs.type_text({"app": "notepad", "text": "W3-WIN CONFIRMED 42"})
    assert out["verified"] and out["background"] and "without bringing it forward" in out["detail"]
    assert seen == [(field, "W3-WIN CONFIRMED 42", True)]
    pcverbs._OURS.clear()


def test_type_text_in_background_refuses_a_document_the_owner_did_not_name_and_a_failed_read_back(monkeypatch):
    from core import pcverbs
    field = _Field("")
    _bg_notepad(monkeypatch, "README.TXT - Notepad", field)
    monkeypatch.setattr(uia, "set_text_bg", lambda *a, **k: pytest.fail("typed into a foreign document"))
    with background_only(), pytest.raises(BackgroundUnavailable, match="new tab"):
        pcverbs.type_text({"app": "notepad", "text": "hello"})
    _bg_notepad(monkeypatch, "Untitled - Notepad", field)
    monkeypatch.setattr(uia, "set_text_bg", lambda *a, **k: "value")              # claims success, writes nothing
    with background_only(), pytest.raises(RuntimeError, match="don't see it"):
        pcverbs.type_text({"app": "notepad", "text": "hello"})


def test_save_document_refuses_in_background_before_touching_anything(monkeypatch, tmp_path):
    from core import pcverbs
    monkeypatch.setattr(pcverbs, "_one_window", lambda *a, **k: pytest.fail("looked for a window"))
    target = tmp_path / "new" / "hello.txt"
    with background_only(), pytest.raises(BackgroundUnavailable, match="Save dialog"):
        pcverbs.save_document({"app": "notepad", "path": str(target)})
    assert not os.path.exists(target.parent), "not even the folder was made"


def test_type_text_in_background_refuses_when_a_hidden_tab_editor_is_in_the_window(monkeypatch):
    """Review finding 13: 'Untitled' is in front, but his restored README tab's editor is still in the tree. The
    background path never takes 'the first editor': with two text areas it refuses and writes nothing."""
    from core import pcverbs
    readme, new = _Field("his readme"), _Field("")
    win = _bg_notepad(monkeypatch, "Untitled - Notepad", new)
    win.descendants = lambda control_type=None: [readme, new] if control_type == "Document" else []
    monkeypatch.setattr(uia, "set_text_bg", lambda *a, **k: pytest.fail("typed into one of two editors"))
    with background_only(), pytest.raises(BackgroundUnavailable, match="more than one document"):
        pcverbs.type_text({"app": "notepad", "text": "hello"})
    assert readme.text == "his readme"
