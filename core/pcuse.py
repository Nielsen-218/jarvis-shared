"""use_app: do a task INSIDE an application Jarvis was never taught, with the general UI loop (jarvis_pc.uiact.agent).

Policy (core/authz.py, EFFECTS['use_app'] = reversible): the owner's command naming the app is the approval, so this runs
without a question when the app is named (the typed tool's own 'sensitive' flag is satisfied by that policy in pchybrid.run);
it asks only for an unnamed app or a non-owner. Whether the fast lane's 'operate' should ask first is the owner's call
(docs/mission/audit-pc-control.md, defect 2). The app is launched by Jarvis itself, exactly one visible window must belong to it, and every action goes through the same parser,
permission gate and executor as the school's UI skills (control names only, never coordinates, never a password field, and a
consequential control such as delete/send/pay is never pressed). A failure raises with the real reason; nothing is claimed that the
window did not show.
"""
from __future__ import annotations

import re
from typing import Callable, Optional


_ONE_CLICK = re.compile(r"^\s*(?:please\s+)?(?:click|press|tap|select|choose|pick|open)(?:\s+on)?\s+(?:the\s+)?(?P<name>.+?)"
                        r"(?:\s+(?:button|tab|chat|option|link|item|project|menu))?\s*[.!]?\s*$", re.I)


_GUESSED_ICON = re.compile(r"\bicon(?: \d+)?$", re.I)


def is_switch(title: str, name: str) -> bool:
    """Is the clickable control called `name` an on/off switch (a CheckBox, or anything with a toggle state)? Live
    2026-11-09: "In Settings, click on Bluetooth" pressed the Bluetooth SWITCH on the Settings home page (a 'Button' named
    exactly 'Bluetooth') and turned Bluetooth on, instead of opening the 'Bluetooth & devices' page."""
    from jarvis_pc.uiact import agent, uia
    win = uia.window(title)
    for c in win.descendants():
        info = c.element_info
        if info.control_type not in agent.INTERACTIVE or re.sub(r"\s+", " ", (info.name or "").strip())[:80] != name:
            continue
        if info.control_type == "CheckBox":
            return True
        try:
            c.get_toggle_state()
            return True
        except Exception:  # noqa: BLE001  (no toggle pattern: an ordinary button / item)
            continue
    return False


_ONE_KEY = re.compile(r"^\s*press\s+(?:the\s+)?(?P<spec>(?:(?:ctrl|alt|shift)\+)*[a-z0-9]+)(?:\s+key)?\s*[.!]?\s*$", re.I)
_ONE_TYPE = re.compile(r'^\s*type\s+"(?P<text>(?:[^"\\]|\\.)*)"\s*[.!]?\s*$', re.I)


def one_key(goal: str, executor, *, display: str = "") -> Optional[dict]:
    """'press enter', 'press ctrl+s', 'type "hello"': one key, shortcut or text into the app's focused field, through the
    executor's own grammar and permission gate (it refuses alt+f4, ctrl+alt+delete and the like). A demonstrated workflow
    (core/demonstrate) replays a key or typed text this way. A plain 'press Seven' is a button, not a key: None."""
    from jarvis_pc.uiact import lang
    m = _ONE_TYPE.match(goal or "")
    if m:
        text = m.group("text").replace('\\"', '"').replace("\\\\", "\\")
        r = executor.run(agent_line("type", text))
        if not r or not r.ok:
            return None
        return {"verified": True, "detail": f"Typed it in {display}." if display else "Typed it.", "app": display, "steps": 1}
    m = _ONE_KEY.match(goal or "")
    if not m:
        return None
    spec = m.group("spec").lower()
    mods, key = lang.split_hotkey(spec)
    if not mods and key not in lang.KEYS:
        return None
    r = executor.run(("hotkey " if mods else "key ") + spec)
    if not r or not r.ok:
        return None
    return {"verified": True, "detail": f"Pressed {spec} in {display}." if display else f"Pressed {spec}.", "app": display, "steps": 1}


def agent_line(action: str, text: str) -> str:
    from jarvis_pc.uiact import agent
    return agent._line(action, "", text)


def one_click(goal: str, title: str, executor, *, observe_fn=None, display: str = "", switch_fn=None) -> Optional[dict]:
    """'click on Autonomous Blender Learning': ONE named control that is on screen right now is clicked directly, no model
    loop (UFO2's lesson: deterministic where possible). Live 2026-11-08 the model loop clicked it, saw the same control list
    and stopped after 73 s. Only an exact or unambiguous name counts; a risky or window-closing control, an unknown name, or a
    failed click returns None and the normal loop (with its own rules and questions) takes over. Verified = the click was
    delivered to that control by UI Automation; nothing more is claimed."""
    m = _ONE_CLICK.match(goal or "")
    if not m:
        return None
    from jarvis_pc.uiact import agent
    view = (observe_fn or agent.observe)(title)
    real = agent._match(m.group("name"), view.get("click", []))
    if not real or agent.RISKY.search(real) or agent._closes_window(real, title):
        return None
    if view.get("_vision") and _GUESSED_ICON.search(real):
        # An icon's name is the vision model's guess from its picture. Live 2026-11-09 in Blender: "click on the Rotate icon"
        # clicked a header icon it had named "Rotate" while the real Rotate tool was named "Move"; 3 of 9 toolbar icons were
        # named right, Florence-2 got 0 of 9. A direct click would claim "Clicked Rotate icon"; the loop checks after acting.
        return None
    if not view.get("_vision"):
        # "Click X" asks to open/select X. An on/off switch is never flipped from here: the one other clickable that carries
        # the same word ("Bluetooth & devices") is what was meant; with none or several, say so instead of guessing.
        switch = switch_fn or is_switch
        try:
            flip = switch(title, real)
            if flip:
                word = re.compile(r"(?<![A-Za-z0-9])" + re.escape(real) + r"(?![A-Za-z0-9])", re.I)
                others = [c for c in view.get("click", []) if c != real and word.search(c) and not agent.RISKY.search(c)]
                starts = [c for c in others if c.casefold().startswith(real.casefold())]
                if len(starts) == 1:            # Settings also lists device cards ending "... State Bluetooth" (live 2026-11-09)
                    others = starts
                if len(others) == 1 and not switch(title, others[0]):
                    real, flip = others[0], False
        except Exception:  # noqa: BLE001  (could not check: the normal loop, with its own checks, decides)
            return None
        if flip:
            raise RuntimeError(f"{real} is an on/off switch, so I left it as it is. Say 'turn {real} on' or 'off' if you want it changed.")
    line = agent._line("click", real, "", vision=bool(view.get("_vision")))
    r = executor.run(line) if line else None
    if not r or not r.ok:
        return None
    return {"verified": True, "detail": f"Clicked {real}" + (f" in {display}." if display else "."), "app": display, "steps": 1}


def operate(app: str, goal: str, cancelled: Callable[[], bool] = lambda: False, *, launch=None, windows=None, make_executor=None,
            run_agent=None, resolve=None, settle_s: float = 3.0, observe=None) -> dict:
    from tools import apps
    resolve = resolve or apps.resolve_app
    launch = launch or (lambda name: apps.launch_and_wait(name, cancelled=cancelled))
    windows = windows or apps.visible_windows_for_app
    canonical = resolve(app)
    if not canonical:
        raise RuntimeError("That app is not in the installed-app catalog.")
    def settled():
        # A Store app shows TWO frames for a moment right after it starts (measured 2026-11-07: 2 at 0.2 s, 1 from 0.7 s).
        import time as _time
        found = windows(canonical)
        end = _time.monotonic() + settle_s
        while len(found) > 1 and _time.monotonic() < end and not cancelled():
            _time.sleep(0.25)
            found = windows(canonical)
        return found

    found = settled()
    if not found:
        # Launch only when it is not already open: a plan that first brings the app to the front, then uses it, opened a
        # SECOND Calculator window here and the loop refused both (live 2026-11-07, "take over the calculator ...").
        detail, ready = launch(canonical)
        if not ready:
            raise RuntimeError(detail)
        found = settled()
    if len(found) != 1:
        raise RuntimeError(f"I need exactly one visible {canonical} window to work in, and I found {len(found)}.")
    title = found[0]["title"]
    if make_executor is None:
        def make_executor(window_title):                      # noqa: E306
            from jarvis_pc.uiact.executor import Executor
            from tools import pcskills
            return Executor(pcskills.skills()._guard(), window_title, owned=True)
    if run_agent is None:
        def run_agent(task, window_title, executor):          # noqa: E306
            from core import pcnotes, screenjudge
            from jarvis_pc.uiact import agent
            # The window before acting, for the second look when the text check cannot say. Only when a look may happen at
            # all (not switched off, no game, no practice): otherwise no screenshot and no change of focus.
            looking = screenjudge.may_look()
            before = screenjudge.grab_window(window_title) if looking else None

            def second_look(g):
                # Checked again here: a game started during the run must not get a window pushed over it.
                if not looking or cancelled() or not screenjudge.may_look():
                    return None
                v = screenjudge.judge(g, before, screenjudge.grab_window(window_title))
                return (v.state, v.why) if v else None
            try:
                notes = pcnotes.render(pcnotes.notes_for(canonical, task))
            except Exception:  # noqa: BLE001  (a damaged notes file never stops the task)
                notes = ""
            return agent.run(task, window_title, executor, ask=agent.model_ask("advanced"), cancelled=cancelled,
                             verify=agent.model_verify("advanced"), second_look=second_look, notes=notes)
    executor = make_executor(title)
    from core import targets
    shown = targets.display(canonical)
    direct = one_key(goal, executor, display=shown) or one_click(goal, title, executor, observe_fn=observe, display=shown)
    if direct is not None:
        return direct
    outcome = run_agent(goal, title, executor)
    if getattr(outcome, "needs_approval", ""):
        raise RuntimeError(f"I stopped before I would {outcome.needs_approval}; that needs your approval.")
    if not outcome.ok:
        raise RuntimeError(f"I could not finish that in {canonical}: {outcome.why}.")
    shown_name = targets.display(canonical)
    if getattr(outcome, "unsure", ""):
        # Nothing could confirm it (Mark IV): say so and stop. A stop is not a pending plan, so no automatic yes can answer
        # it, nothing is saved as a workflow, and no note is kept.
        return {"verified": False, "error_kind": "needs_owner", "app": canonical, "steps": len(outcome.steps),
                "detail": f"I did that in {shown_name}, but I can't tell from the window whether it worked ({outcome.unsure}). "
                          "Can you check?"}
    try:
        from core import pcnotes
        if not cancelled():                                     # a step already reported as timed out or stopped teaches nothing
            pcnotes.remember(canonical, goal, outcome.steps)   # confirmed: keep how, for next time
    except Exception:  # noqa: BLE001  (a note is a bonus; the task is done either way)
        pass
    return {"verified": True, "detail": f"In {canonical}: {outcome.answer}", "app": canonical, "steps": len(outcome.steps)}
