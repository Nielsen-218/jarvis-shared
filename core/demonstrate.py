"""Learn a PC task by watching the owner do it once (UFO's learning from demonstration, 2026-11-09).

    "Watch me minimize Quad."  -> Jarvis records the owner's own clicks and keys (nothing else, nothing sent anywhere)
    ... the owner does it ...
    "Done."                    -> the recording becomes a typed plan: "Got it, 1 step in Quad. Should I try it now?"
    "Yes."                     -> the plan runs through the normal PC path; verified, it is saved as a workflow for the phrase
    "Minimize Quad."           -> later: the saved workflow runs (the gateway checks workflows first)

What is recorded, per action: which window it was in, and the NAME of the control clicked (UI Automation at the point of the
click), a named key or shortcut, or a run of typed text. Never: pixel positions as the step (a control with no name is
reported, not guessed), a password field's keys (the run is dropped and said), Jarvis's own windows, the taskbar. Steps
compile only into typed tools that already exist (use_app click / press / type, minimize_app, close_app), so a replay goes
through the same permission gate and the same never-click-a-risky-control rules as everything else.

UFO records with Windows Steps Recorder (gone from Windows 11 24H2); here pynput's low-level hooks feed a worker thread that
asks UI Automation what is under the pointer.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

MAX_SECONDS = 180               # a recording stops listening on its own after this
FINISH_SECONDS = 600            # ...and can still be finished ('done') or cancelled this long after it began; then it is dropped
MAX_STEPS = 12                  # a plan holds at most twelve typed steps (pcactions.validate)

_START = re.compile(r"^(?:ok(?:ay)?[,\s]+)?(?:(?:jarvis|sir)[,\s]+)?(?:watch me|let me show you|i(?:'ll| will) show you|watch how i)"
                    r"(?:\s+how\s+(?:to|i|you))?(?:\s+(?P<task>.*?))?\s*[.!]?\s*$", re.I)
_DONE = re.compile(r"^(?:ok(?:ay)?[,\s]+)?(?:i'?m\s+)?(?:done|all done|that'?s it|finished|i'?m finished|stop watching)\b[\s.!,]*(?:jarvis|sir)?[\s.!]*$", re.I)
_CANCEL = re.compile(r"^(?:cancel|never ?mind|forget (?:it|that)|stop recording|don'?t (?:save|keep) (?:it|that))\b", re.I)
_TITLEBAR = {"minimize": "minimize_app", "close": "close_app"}
_MODIFIERS = {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr", "shift", "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"}
_NAMED_KEYS = {"enter", "tab", "esc", "escape", "up", "down", "left", "right", "space", "home", "end", "page_up", "page_down",
               "delete", "backspace"} | {f"f{i}" for i in range(1, 13)}
_OWN_TITLES = re.compile(r"^jarvis\b", re.I)
_CLICKABLE = ("Button", "MenuItem", "TabItem", "Hyperlink", "SplitButton", "ListItem", "RadioButton", "CheckBox", "TreeItem",
              "ComboBox", "Edit", "Document")

_state: Dict[str, object] = {"recorder": None, "task": ""}
_lock = threading.Lock()


# ----------------------------------------------------------------------------------------------- compiling a recording
def _app_of(window: dict) -> str:
    """The app name a typed step takes for this window ('' for the shell, Jarvis itself, or nothing)."""
    title = str(window.get("title") or "")
    if not title or _OWN_TITLES.match(title):
        return ""
    from core import targets
    rows = targets.open_apps([{"process": window.get("process", ""), "title": title}])
    return rows[0]["app"] if rows else ""


def _first_word(name: str) -> str:
    return (name or "").strip().split(" ")[0].casefold()


def _key_name(event: dict) -> str:
    key = event["key"]
    key = {"page_up": "pageup", "page_down": "pagedown", "esc": "escape"}.get(key, key)
    mods = [m for m in ("ctrl", "alt", "shift") if m in event.get("mods", ())]
    return "+".join(mods + [key])


def compile_steps(events: List[dict]) -> Tuple[List[dict], List[str]]:
    """Recorded events -> (typed plan steps, problems said to the owner). A problem means the plan is not complete."""
    steps: List[dict] = []
    problems: List[str] = []
    text, text_app = "", ""

    def flush():
        nonlocal text, text_app
        if text.strip() and text_app:
            steps.append({"tool": "use_app", "arguments": {"app": text_app, "goal": 'type "' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'}})
        text, text_app = "", ""

    for e in events:
        app = _app_of(e.get("window") or {})
        if not app:
            continue
        if e["kind"] == "click":
            flush()
            name = (e.get("name") or "").strip()
            if e.get("titlebar") and _first_word(name) in _TITLEBAR:
                steps.append({"tool": _TITLEBAR[_first_word(name)], "arguments": {"app": app}})
            elif e.get("titlebar"):
                problems.append(f"the window's {name or 'title bar'} button, which I can't repeat yet")
            elif name:
                steps.append({"tool": "use_app", "arguments": {"app": app, "goal": f"click on {name}"}})
            else:
                problems.append(f"a click in {app} on something with no name I can find again")
        elif e["kind"] == "key":
            if e.get("password"):
                text, text_app = "", ""
                problems.append("typing in a password field, which I never record")
                continue
            key, mods = e["key"], set(e.get("mods", ()))
            if len(key) == 1 and not (mods & {"ctrl", "alt"}):
                if text_app and text_app != app:
                    flush()
                text, text_app = text + key, app
            elif key == "backspace" and text and not mods:
                text = text[:-1]
            elif key in _NAMED_KEYS or (len(key) == 1 and mods & {"ctrl", "alt"}):
                flush()
                steps.append({"tool": "use_app", "arguments": {"app": app, "goal": f"press {_key_name(e)}"}})
    flush()
    # The same click twice in a row is one double-click the owner made; keep it once.
    deduped: List[dict] = []
    for s in steps:
        if not deduped or deduped[-1] != s or s["arguments"].get("goal", "").startswith("type "):
            deduped.append(s)
    if len(deduped) > MAX_STEPS:
        from core.words import plural
        problems.append(f"{plural(len(deduped), 'step')}, and I can keep at most {MAX_STEPS} in one go")
    return deduped[:MAX_STEPS], problems


# ----------------------------------------------------------------------------------------------- recording
class Recorder:
    """The owner's clicks and keys, while it runs. The hook callbacks only queue raw events (low-level hooks must return
    fast or Windows drops them); a worker asks UI Automation what was under the pointer."""

    def __init__(self, *, start_listeners: bool = True, clock: Callable[[], float] = time.monotonic):
        self.events: List[dict] = []
        self._raw: "queue.Queue" = queue.Queue()
        self._mods: set = set()
        self._clock = clock
        self._started = clock()
        self._stop = threading.Event()
        self._listeners = []
        self._worker = threading.Thread(target=self._work, daemon=True, name="demonstration")
        if start_listeners:
            from pynput import keyboard, mouse
            self._listeners = [mouse.Listener(on_click=self._on_click), keyboard.Listener(on_press=self._on_press, on_release=self._on_release)]
            for listener in self._listeners:
                listener.start()
        self._worker.start()

    # hook threads: as little as possible
    def _on_click(self, x, y, button, pressed):
        if pressed and getattr(button, "name", "") == "left" and not self._stop.is_set():
            self._raw.put(("click", int(x), int(y), _foreground()))

    def _on_press(self, key):
        name = _pynput_name(key)
        if name in _MODIFIERS:
            self._mods.add(name.split("_")[0])
        elif name and not self._stop.is_set():
            self._raw.put(("key", name, frozenset(self._mods), _foreground()))

    def _on_release(self, key):
        name = _pynput_name(key)
        if name in _MODIFIERS:
            self._mods.discard(name.split("_")[0])

    def _work(self):
        while not self._stop.is_set() or not self._raw.empty():
            try:
                raw = self._raw.get(timeout=0.2)
            except queue.Empty:
                if self._clock() - self._started > MAX_SECONDS:
                    self.stop()
                continue
            try:
                event = describe(raw)
            except Exception:  # noqa: BLE001  (one unreadable event never ends the recording)
                event = None
            if event:
                self.events.append(event)

    def stop(self) -> List[dict]:
        self._stop.set()
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout=5)
        return list(self.events)

    @property
    def listening(self) -> bool:
        return not self._stop.is_set()


def _pynput_name(key) -> str:
    char = getattr(key, "char", None)
    if char:
        return char if char.isprintable() else ""
    name = getattr(key, "name", "") or ""
    return name.lower()


def _foreground() -> int:
    try:
        import win32gui
        return int(win32gui.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return 0


def _window(hwnd: int) -> dict:
    import psutil
    import win32gui
    import win32process
    top = win32gui.GetAncestor(hwnd, 2) or hwnd          # GA_ROOT
    _, pid = win32process.GetWindowThreadProcessId(top)
    return {"hwnd": top, "title": win32gui.GetWindowText(top), "process": psutil.Process(pid).name() if pid else ""}


def describe(raw: tuple) -> Optional[dict]:
    """A raw hook event -> {kind, window, ...} with the control's name read by UI Automation."""
    if raw[0] == "key":
        _, key, mods, hwnd = raw
        return {"kind": "key", "key": key, "mods": sorted(mods), "window": _window(hwnd) if hwnd else {}, "password": _focus_is_password()}
    _, x, y, _hwnd = raw
    import win32gui
    from pywinauto import Desktop
    hwnd = win32gui.WindowFromPoint((x, y))
    window = _window(hwnd) if hwnd else {}
    ctl = Desktop(backend="uia").from_point(x, y)
    name, kind, found = "", "", False
    for _ in range(6):                     # the text inside a button is not the button: climb to the named, clickable thing
        info = ctl.element_info
        kind = info.control_type or ""
        name = re.sub(r"\s+", " ", (info.name or "").strip())[:80]
        if name and kind in _CLICKABLE:
            found = True
            break
        parent = ctl.parent()
        if parent is None:
            break
        ctl = parent
    if not found:
        name, kind = "", ""
    if kind in ("Edit", "Document"):
        return None                        # clicking into a text box only puts the caret there; the typing that follows is the step
    # Minimize / Close of the window itself: those names, in the strip along the window's top edge (Win32 title bars sit
    # under a TitleBar element, Store and Chromium apps draw their own).
    titlebar = False
    if _first_word(name) in ("minimize", "maximize", "restore", "close") and window.get("hwnd"):      # "Minimize Calculator"
        try:
            titlebar = 0 <= y - win32gui.GetWindowRect(window["hwnd"])[1] < 60
        except Exception:  # noqa: BLE001
            titlebar = False
    return {"kind": "click", "name": name, "type": kind, "titlebar": titlebar, "window": window}


def _focus_is_password() -> bool:
    try:
        from pywinauto.uia_defines import IUIA
        return bool(IUIA().iuia.GetFocusedElement().CurrentIsPassword)
    except Exception:  # noqa: BLE001
        return True                        # cannot tell: treat the keys as secret and drop them


# ----------------------------------------------------------------------------------------------- the conversation
def _age() -> float | None:
    at = _state.get("at")
    return None if at is None else time.monotonic() - float(at)


def recording() -> bool:
    """Watching him now: a recording is held and still listening. One that stopped by itself (MAX_SECONDS: he walked away, or
    the mic slept before his 'done') is not, so his PC commands act again (core.gateway.exact)."""
    rec = _state.get("recorder")
    if not rec or not getattr(rec, "listening", True):
        return False
    age = _age()
    return age is None or age <= MAX_SECONDS + 5


def pending() -> bool:
    """A demonstration still waits for 'done' or 'cancel': watched now, or stopped by itself less than FINISH_SECONDS after it
    began. An older one is dropped here (nothing saved), so it never holds the conversation open."""
    rec = _state.get("recorder")
    if not rec:
        return False
    if recording():
        return True
    age = _age()
    if age is not None and age > FINISH_SECONDS:
        cancel()
        return False
    if getattr(rec, "listening", False):
        # past MAX_SECONDS while input kept coming (the worker checks the clock only in a lull): stop capturing his keys now;
        # what was recorded stays for 'done'
        try:
            rec.stop()
        except Exception as error:  # noqa: BLE001
            print(f"[demonstrate] could not stop the recorder: {type(error).__name__}: {error}")
    return True


def _said(raw: str) -> str:
    text = re.sub(r"^\s*\[[^\]]*\]\s*", "", str(raw or "")).strip()
    return re.sub(r"^(?:hey\s+)?jarvis[,\s]+", "", text, flags=re.I).strip()


def ends_it(raw: str) -> bool:
    """'Done.' / 'Cancel.' / 'Never mind.' while a demonstration waits: its own answer, a turn even said over Jarvis's voice."""
    text = _said(raw)
    return pending() and bool(_DONE.match(text) or _CANCEL.match(text))


def start(task: str, *, recorder_factory=None) -> str:
    with _lock:
        old = _state.get("recorder")
        if old:
            old.stop()
        _state["recorder"] = (recorder_factory or Recorder)()
        _state["task"] = task
        _state["at"] = time.monotonic()
    return "Okay, I'm watching. Say 'done' when you've finished."


def finish(*, store=None) -> str:
    with _lock:
        rec, task = _state.get("recorder"), str(_state.get("task") or "")
        _state["recorder"], _state["task"] = None, ""
        _state.pop("at", None)
    if not rec:
        return "I wasn't watching anything."
    events = rec.stop()
    steps, problems = compile_steps(events)
    if not steps:
        return "I didn't see anything I can repeat" + (f": {problems[0]}." if problems else ".")
    if problems:
        return f"I can't learn that one yet: {problems[0]}. Nothing was saved."
    plan = {"steps": steps}
    from core import pcactions
    try:
        pcactions.validate(plan)
    except (ValueError, TypeError) as error:
        return f"I couldn't turn that into steps I can run ({error}). Nothing was saved."
    (store or _store)(task, plan)
    apps = []
    for s in steps:
        app = s["arguments"].get("app", "")
        if app and app not in apps:
            apps.append(app)
    from core import targets
    where = " and ".join(targets.display(a) for a in apps[:2])
    n = len(steps)
    from core.words import plural
    return f"Got it, {plural(n, 'step')} in {where}. Should I try it now?"


def _store(task: str, plan: dict) -> str:
    """The plan waits for the owner's yes like any reviewed plan; route 'demonstration' saves it as a workflow once a
    try-run is verified (core/pchybrid)."""
    from core import pcledger
    return pcledger.create("plan", {"task": task, "plan": plan, "route": "demonstration", "cloud_usd": 0})


def cancel() -> str:
    with _lock:
        rec = _state.get("recorder")
        _state["recorder"], _state["task"] = None, ""
        _state.pop("at", None)
    if rec:
        rec.stop()
        return "Okay, I stopped watching. Nothing was saved."
    return ""


def handle(raw: str) -> Optional[str]:
    """Start, finish or cancel a demonstration; while it is watched, his narration ("I'm going to click on save.") gets a short
    'Okay.' (he is showing, not asking: no model turn talks over the next step); None for every other turn (the owner may ask
    other things meanwhile)."""
    text = (raw or "").strip()
    if not text or len(text) > 200:
        return None
    if pending():
        if _DONE.match(text):
            return finish()
        if _CANCEL.match(text):
            return cancel()
        if recording():
            try:
                from core import gateway
                if gateway.narration(text):
                    return "Okay."
            except Exception:  # noqa: BLE001 - unsure: the turn goes on as usual
                pass
            return None
    m = _START.match(text)
    if not m:
        return None
    from core import pcbridge
    if not pcbridge.local_pc():
        return "I can only watch you on the PC."
    task = re.sub(r"\s+", " ", (m.group("task") or "")).strip(" .!?")
    if not task:
        return "Tell me what you're about to show me, like 'watch me minimize Quad'."
    return start(task)
