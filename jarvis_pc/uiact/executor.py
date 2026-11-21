"""Runs UI actions against ONE bound window, through the permission gate.

Safety rules (all enforced here, none left to the model):
  * the executor is bound to a single window by title; no action can name or reach any other window;
  * every step is a gate decision (`authorize_action`): the kill switch is checked, dry-run blocks it, the audit log records it;
  * a window that is not a school practice window is T2 (always asks), so an unattended session can never act on a real app;
  * UI Automation patterns are used (invoke, value, toggle, select): the mouse cursor is never moved, so the person is not fought;
  * keyboard actions (hotkey, key, type) go to the foreground window, so they first check the person has not touched the
    keyboard or mouse since the last step and abort ("user took over") if they have;
  * passwords: a control that UI Automation marks as a password field can never be read or typed into.
"""
from __future__ import annotations

import ctypes
import re
import time
from types import SimpleNamespace
from typing import Callable, List, Optional

from ..guardrails.gate import Guardrails
from ..guardrails.tiers import Tier
from . import lang

# Windows whose controls change system settings: their UI actions keep the restore point (core of the gate's pre_change).
SYSTEM_WINDOWS = re.compile(r"\b(?:settings|control panel|registry editor|regedit|device manager|services|group policy|"
                            r"local security policy|task scheduler|computer management|disk management|system properties|"
                            r"windows security|firewall|user accounts|credential manager|environment variables)\b", re.I)
CLICKABLE = ("Button", "MenuItem", "TabItem", "Hyperlink", "SplitButton", "ListItem", "RadioButton", "CheckBox")
FIELDS = ("Edit", "ComboBox", "Document")
PRACTICE_PREFIX = "Practice "          # the school's practice windows are titled "Practice <word>"


def last_input_tick() -> int:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))  # type: ignore[attr-defined]
    return int(info.dwTime)


class UIError(RuntimeError):
    pass


class Executor:
    def __init__(self, guard: Guardrails, title: str, *, owned: bool = False, input_tick: Callable[[], int] = last_input_tick,
                 sleep: Callable[[float], None] = time.sleep, backend=None):
        self.guard, self.title = guard, title
        self.owned = owned                 # the school itself launched this window (and checked no other copy was open)
        self.input_tick, self.sleep = input_tick, sleep
        self._backend = backend            # tests inject a fake; production loads pywinauto lazily
        self._tick: Optional[int] = None
        self._win = None                   # resolved once: finding a top-level window through UI Automation is slow

    # ------------------------------------------------------------------ plumbing
    def _ui(self):
        if self._backend is None:
            from . import uia
            self._backend = uia
        return self._backend

    @property
    def is_practice(self) -> bool:
        return self.owned or self.title.startswith(PRACTICE_PREFIX)

    def _gate(self, step: lang.Step):
        tier = Tier.T1 if self.is_practice else Tier.T2
        why = ["UI Automation action on one bound window; the mouse cursor is not moved"] if self.is_practice else \
              ["acts on a real application window (not a school practice window)"]
        intent = "ui" if SYSTEM_WINDOWS.search(self.title or "") else "ui-app"
        return self.guard.authorize_action(f"ui {step.text} in window '{self.title}'", tier=tier, reasons=why, intent=intent)

    def _user_took_over(self, keyboard: bool) -> bool:
        now = self.input_tick()
        if self._tick is None:
            self._tick = now
            return False
        taken = now != self._tick
        self._tick = now
        return taken and keyboard

    def _record_own_input(self) -> None:
        self._tick = self.input_tick()   # our own SendInput also counts as "input": remember it so it is not mistaken for the person

    # ------------------------------------------------------------------ one step
    def run(self, line: str) -> SimpleNamespace:
        """The shape playbooks.run expects from a command runner (ran/ok/stdout/stderr/exit_code/error_kind/decision)."""
        try:
            step = lang.parse(line)
        except lang.UIParseError as e:
            return self._result(False, False, "", f"rejected: {e}", None, kind="syntax")
        self.guard.killswitch.check()
        decision = self._gate(step)
        if not decision.allowed:
            return self._result(False, False, "", "blocked by the permission gate", decision, kind="blocked")
        keyboard = step.verb in ("hotkey", "key", "type", "tap")   # tap moves the real mouse: same "did the person take over" check
        if self._user_took_over(keyboard):
            return self._result(True, False, "", "the person used the keyboard or mouse; stopping so I do not fight them", decision, kind="user_took_over")
        from ..guardrails.stepfence import BackgroundUnavailable, UncertainOutcome
        try:
            out = self._do(step)
        except UncertainOutcome as e:
            return self._result(True, False, "", str(e), decision, kind=e.kind)   # sent, not confirmed: never retried
        except BackgroundUnavailable as e:
            # A mission step that would need the foreground: its own kind, so the caller stops instead of trying the
            # same thing another way (2026-11-10, Mark VI).
            return self._result(True, False, "", str(e), decision, kind=e.kind)
        except UIError as e:
            return self._result(True, False, "", str(e), decision, kind="ui")
        except Exception as e:  # noqa: BLE001
            return self._result(True, False, "", f"{type(e).__name__}: {str(e)[:160]}", decision, kind="ui")
        if keyboard:
            self._record_own_input()
        return self._result(True, True, out, "", decision)

    @staticmethod
    def _result(ran, ok, out, err, decision, kind=None):
        d = decision or SimpleNamespace(reasons=[err] if err else [], id="ui", tier=None)
        return SimpleNamespace(ran=ran, ok=ok, stdout=out, stderr=err, exit_code=0 if ok else 1, error_kind=kind, decision=d, truncated=False)

    def __call__(self, guard, command, **kw):          # plugs in wherever a shell runner `run_fn(guard, command, ...)` is expected
        return self.run(command)

    # ------------------------------------------------------------------ the actions
    def _do(self, step: lang.Step) -> str:
        ui = self._ui()
        if self._win is None:
            self._win = ui.window(self.title)
        win = self._win
        v, a = step.verb, step.args
        if v == "wait":
            self.sleep(float(a[0]))
            return ""
        if v == "click":
            ctl = ui.find(win, a[0], CLICKABLE)
            ui.press(ctl)
            return ""
        if v == "set":
            ctl = ui.find(win, a[0], FIELDS)
            if ui.is_password(ctl):
                raise UIError("that is a password field; I never type into those")
            ui.set_text(ctl, a[1])
            return ""
        if v in ("check", "uncheck"):
            ctl = ui.find(win, a[0], ("CheckBox", "RadioButton"))
            ui.set_checked(ctl, v == "check")
            return ""
        if v == "select":
            ctl = ui.find(win, a[0], ("ListItem", "TabItem", "MenuItem", "ComboBox"))
            ui.select(ctl)
            return ""
        if v == "read":
            return ui.read(win, a[0])
        if v == "tap":
            labels = ui.screen_labels(win)
            want = a[0].strip().casefold()
            hits = [x for x in labels if x["text"].strip().casefold() == want] or \
                   sorted([x for x in labels if want in x["text"].casefold()], key=lambda x: -x.get("confidence", 0))
            if not hits:
                raise UIError(f"I cannot see '{a[0]}' on this window right now")
            x, y = hits[0]["center"]
            ui.click_at(win, x, y)
            return hits[0]["text"]
        if v in ("hotkey", "key", "type"):
            from ..guardrails.stepfence import is_background
            if is_background():
                # A mission never brings the window forward (2026-11-10, Mark VI): keys are posted to the window's own
                # focused child (a chord refuses), text goes into its text area through UIA / window messages, read back.
                if v == "type":
                    ctl = ui.editable(win, only=True)       # never a hidden tab's editor (2026-11-10 review)
                    if ui.is_password(ctl):
                        raise UIError("that is a password field; I never type into those")
                    ui.set_text_bg(ctl, a[0], append=True)
                else:
                    ui.key_bg(win, a[0])
                return ""
        if v == "hotkey":
            ui.focus(win)
            ui.send_keys(win, lang.to_pywinauto_keys(a[0]))
            return ""
        if v == "key":
            ui.focus(win)
            ui.send_keys(win, lang.to_pywinauto_keys(a[0]))
            return ""
        if v == "type":
            ui.focus(win)
            ui.send_keys(win, re.sub(r"([{}^%+~()])", r"{\1}", a[0]), literal=True)
            return ""
        if v == "move":
            ui.move(win, int(a[0]), int(a[1]))
            return ""
        if v == "resize":
            ui.resize(win, int(a[0]), int(a[1]))
            return ""
        if v == "close":
            ui.close(win)
            return ""
        raise UIError(f"unsupported action {v}")
