"""In-app clicks: no Windows restore point for an action inside one ordinary app window (it cannot undo a click and cost ~10 s
per click, measured 2026-11-09), system windows keep it; Chromium buttons without Invoke are pressed by their default action."""
from types import SimpleNamespace

import pytest

from jarvis_pc.guardrails.gate import Guardrails
from jarvis_pc.guardrails.tiers import Tier
from jarvis_pc.uiact import uia
from jarvis_pc.uiact.executor import SYSTEM_WINDOWS, Executor


def _gate(tmp_path, made):
    return Guardrails(allowlist_roots=[str(tmp_path)], audit_path=str(tmp_path / "audit.jsonl"), approver=lambda req: True,
                      pre_change=lambda req: made.append(req.command), dry_run=False)


def test_an_app_window_action_needs_no_restore_point_but_a_system_window_does(tmp_path):
    made = []
    g = _gate(tmp_path, made)
    d = g.authorize_action("ui click 'Nclex' in window 'ChatGPT'", tier=Tier.T2, intent="ui-app")
    assert d.allowed and made == [] and "no restore point" in " ".join(d.reasons)
    d = g.authorize_action("ui click 'Bluetooth' in window 'Settings'", tier=Tier.T2, intent="ui")
    assert d.allowed and len(made) == 1


@pytest.mark.parametrize("title, system", [("ChatGPT", False), ("Untitled - Paint", False), ("Friends - Discord", False),
                                           ("Settings", True), ("Control Panel", True), ("Device Manager", True),
                                           ("Registry Editor", True), ("Windows Security", True)])
def test_which_windows_count_as_system(title, system):
    assert bool(SYSTEM_WINDOWS.search(title)) is system


def test_the_executor_asks_for_the_right_kind(tmp_path):
    seen = []
    guard = SimpleNamespace(authorize_action=lambda desc, **kw: seen.append(kw["intent"]) or SimpleNamespace(allowed=True))
    step = SimpleNamespace(text='click "Nclex"')
    Executor(guard, "ChatGPT", owned=True)._gate(step)
    Executor(guard, "Settings", owned=True)._gate(step)
    assert seen == ["ui-app", "ui"]


class _Ctl:
    def __init__(self, invoke_ok):
        self.invoke_ok = invoke_ok
        self.element_info = SimpleNamespace(control_type="Button", element=object())

    def is_enabled(self):
        return True

    def invoke(self):
        if not self.invoke_ok:
            raise RuntimeError("NoPatternInterfaceError")


def test_a_button_without_invoke_is_pressed_by_its_default_action(monkeypatch):
    import pywinauto.uia_defines as defines
    from jarvis_pc.guardrails import stepfence
    monkeypatch.setattr(stepfence, "fence", lambda what: None)
    done = []
    monkeypatch.setattr(defines, "get_elem_interface",
                        lambda element, name: SimpleNamespace(DoDefaultAction=lambda: done.append(name)))
    uia.press(_Ctl(invoke_ok=False))
    assert done == ["LegacyIAccessible"]
    uia.press(_Ctl(invoke_ok=True))
    assert done == ["LegacyIAccessible"]                          # Invoke worked: no fallback

    def broken(element, name):
        raise RuntimeError("no legacy pattern either")
    monkeypatch.setattr(defines, "get_elem_interface", broken)
    with pytest.raises(uia.UIError):
        uia.press(_Ctl(invoke_ok=False))


def test_window_lookup_uses_the_one_exact_title_by_handle(monkeypatch):
    from tools import pcops
    looked = []

    class Spec:
        def __init__(self, **kw):
            looked.append(kw)

        def wrapper_object(self):
            return self

    monkeypatch.setattr(uia, "_desktop", lambda: SimpleNamespace(window=lambda **kw: Spec(**kw)))
    monkeypatch.setattr(pcops, "windows", lambda: [{"title": "ChatGPT", "hwnd": 42}, {"title": "Claude", "hwnd": 7}])
    uia.window("ChatGPT")
    assert looked == [{"handle": 42}]                              # straight to the handle, no title search
    looked.clear()
    monkeypatch.setattr(pcops, "windows", lambda: [{"title": "ChatGPT", "hwnd": 42}, {"title": "ChatGPT", "hwnd": 43}])
    try:
        uia.window("ChatGPT")
    except Exception:  # noqa: BLE001  (the fake has no wait(): only which path was taken matters)
        pass
    assert looked and looked[0] == {"title": "ChatGPT"}            # two matches: the careful search decides
