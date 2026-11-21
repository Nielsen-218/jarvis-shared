"""A close that fails must not become a minute of planner calls and three contradictory sentences (live 2026-11-13).

Observed through /api/chat: "Open Notepad." (ok) -> "Bring Notepad up for me." (focused) -> "Shut Notepad down for me." took 62 s
and said "I couldn't close Notepad. It may be waiting on a save prompt... I could not finish that in notepad: I could not see the
window. Closed notepad." The ledger showed close_app failing at once with UnboundLocalError (tools.apps._close_visible_windows),
a re-plan into the open-ended use_app click loop (44 s), and a second close that worked. Everything here is a fake: no window,
process or model is touched."""
import sys
import types

import pytest

from core import authz, pcagent, pchybrid, pcmethods
from tools import apps


def step(tool, **args):
    return {"tool": tool, "arguments": args}


# ------------------------------------------------------------------------------------------------ the root cause
def test_a_window_list_that_fails_does_not_turn_into_an_unbound_local_error(monkeypatch):
    """pywinauto raising while it lists the desktop (a window vanished) made `except StepAbandoned` raise UnboundLocalError
    because the name was imported later inside the try; the whole close then failed before anything was asked to close."""
    class Desktop:
        def __init__(self, backend=None):
            pass

        def windows(self):
            raise RuntimeError("element not available")
    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    assert apps._close_visible_windows("notepad") == 0


def test_a_window_that_goes_away_while_listing_is_skipped_not_fatal(monkeypatch):
    closed = []

    class Gone:
        def window_text(self):
            raise RuntimeError("gone")

    class Notepad:
        def window_text(self):
            return "Untitled - Notepad"

        def process_id(self):
            return 1

        def close(self):
            closed.append("notepad")

    class Desktop:
        def __init__(self, backend=None):
            pass

        def windows(self):
            return [Gone(), Notepad()]
    monkeypatch.setitem(sys.modules, "pywinauto", types.SimpleNamespace(Desktop=Desktop))
    monkeypatch.setattr(apps, "_uia_process", lambda window: "Notepad.exe")
    assert apps._close_visible_windows("notepad") == 1 and closed == ["notepad"]


# ------------------------------------------------------------------------------------------------ the whole turn
def wire(monkeypatch):
    monkeypatch.setattr(pchybrid.pcbridge, "require_owner", lambda: "owner")
    monkeypatch.setattr(pchybrid.pcledger, "workflow", lambda t: None)
    monkeypatch.setattr(pchybrid.pcledger, "prepared", lambda t: None)
    monkeypatch.setattr(pchybrid.pcledger, "consume", lambda kind, data: True)
    monkeypatch.setattr(pchybrid.pcledger, "create", lambda *a, **k: "rid")
    monkeypatch.setattr(pchybrid.pcledger, "latency", lambda *a, **k: None)
    monkeypatch.setattr(pchybrid.pcledger, "save_workflow", lambda *a, **k: None)
    monkeypatch.setattr("core.pcstate.describe", lambda snap=None: "PC STATE: test")
    monkeypatch.setattr(authz, "decide", lambda *a, **k: authz.Verdict(ask=False))
    monkeypatch.setattr(pchybrid.time, "sleep", lambda s: None)
    monkeypatch.setattr(apps, "resolve_app", lambda name: "notepad")


def run_close(monkeypatch, first_close_works=False, window_goes_late=False, dialog=False):
    """The observed sequence: close fails at once, the planner proposes the click loop and a second close."""
    wire(monkeypatch)
    ran = []
    windows = {"open": True}

    def execute(s, **kwargs):
        ran.append(s["tool"])
        if s["tool"] == "use_app":
            return {"verified": False, "detail": "I could not finish that in notepad: I could not see the window."}
        if s["tool"] == "close_app":
            if len(ran) == 1 and not first_close_works:
                if window_goes_late:
                    windows["open"] = False          # the window was asked and went a moment after the step gave up
                return {"verified": False, "detail": "I couldn't close Notepad. It may be waiting on a save prompt; have a look and tell me what to do with it."}
            windows["open"] = False
            return {"verified": True, "detail": "Closed notepad.", "app": "notepad"}
        return {"verified": True, "detail": "ok"}
    monkeypatch.setattr(pcmethods, "execute", execute)
    monkeypatch.setattr(apps, "visible_windows_for_app", lambda name: [{"title": "Untitled - Notepad"}] if windows["open"] else [])
    monkeypatch.setattr(pchybrid, "_dialog_open", lambda s: dialog)
    from core import gateway
    monkeypatch.setattr(gateway, "replan", lambda *a, **k: {"steps": [step("use_app", app="notepad", goal="handle save prompt"), step("close_app", app="notepad")]})
    out = pchybrid.run("Shut Notepad down for me.", plan={"steps": [step("close_app", app="notepad")]}, route="gateway")
    return out, ran


def test_a_late_window_is_a_single_closed_never_the_failed_attempts_too(monkeypatch):
    out, ran = run_close(monkeypatch, window_goes_late=True)
    assert out["state"] == "completed"
    assert out["detail"] == "Closed notepad."
    assert ran == ["close_app"], "no re-plan, no click loop: the PC already showed the window gone"


def test_a_failed_close_is_never_handed_to_the_click_loop_without_a_dialog(monkeypatch):
    out, ran = run_close(monkeypatch)
    assert "use_app" not in ran, ran
    assert out["state"] == "stopped"
    assert out["detail"].count("Notepad") == 1 and "Closed notepad" not in out["detail"]
    assert "I could not see the window" not in out["detail"]


def test_with_a_blocking_dialog_the_click_loop_is_allowed_and_the_reply_is_one_sentence(monkeypatch):
    out, ran = run_close(monkeypatch, dialog=True)
    assert ran == ["close_app", "use_app", "close_app"], ran
    assert out["state"] == "completed"
    assert out["detail"] == "Closed notepad."            # the earlier failed sentences are history, not the reply


def test_the_compose_rule_says_only_the_final_state(monkeypatch):
    seen = [{"step": 1, "verified": False, "detail": "I couldn't close Notepad. It may be waiting on a save prompt; have a look and tell me what to do with it."},
            {"step": 2, "verified": False, "detail": "I could not finish that in notepad: I could not see the window."},
            {"step": 3, "verified": True, "detail": "Closed notepad."}]
    assert pchybrid.compose_detail(seen, "completed") == "Closed notepad."
    assert pchybrid.compose_detail(seen, "stopped") == "Closed notepad. I could not finish that in notepad: I could not see the window."
    # open / focus / minimize compose the same way
    for ok, bad in (("Opened notepad.", "Notepad did not open."), ("Focused a visible notepad.exe window.", "Windows refused to focus the requested window."),
                    ("Minimized notepad.", "Could not minimize it.")):
        rows = [{"step": 1, "verified": False, "detail": bad}, {"step": 1, "verified": True, "detail": ok}]
        assert pchybrid.compose_detail(rows, "completed") == ok
    assert pchybrid.compose_detail([{"verified": True, "detail": "Closed notepad."}, {"verified": True, "detail": "Closed notepad."}], "completed") == "Closed notepad."


def test_a_late_open_is_one_sentence_too(monkeypatch):
    wire(monkeypatch)
    shown = {"open": False}

    def execute(s, **kwargs):
        shown["open"] = True                      # the window appears just after the step gave up
        return {"verified": False, "detail": "notepad.exe did not show a window in time."}
    monkeypatch.setattr(pcmethods, "execute", execute)
    monkeypatch.setattr(apps, "visible_own_windows", lambda name: [{"title": "Untitled - Notepad"}] if shown["open"] else [])
    from core import gateway
    monkeypatch.setattr(gateway, "replan", lambda *a, **k: pytest.fail("a late window needs no re-plan"))
    out = pchybrid.run("Open Notepad.", plan={"steps": [step("open_app", app="notepad")]}, route="gateway")
    assert out["state"] == "completed" and out["detail"] == "Opened notepad."
