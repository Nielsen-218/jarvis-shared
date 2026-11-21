"""Computer control: Jarvis operates the PC (clicks, types, scrolls) to finish a task.

The public entry routes through core.pchybrid: local actions/workflows first,
then local AI planning, then an explicitly budgeted cloud plan. The legacy paid
screenshot-to-AI execution loop is retired. Low-level Screen helpers remain for
compatibility but are not an automatic fallback. Safety:
- Sensitive supported steps require exact-plan owner approval. Unsupported outward-facing
  actions stop instead of being improvised.
- It never types passwords, card numbers or other secrets.
- Stop requests are checked before/between steps; already-started OS operations cannot
  generally be undone. Legacy raw-input helpers retain pyautogui's fail-safe.
"""
import base64
import ctypes
import io
import math
import re
import threading
import time

from tools.registry import tool

TARGET = (1280, 720)  # screenshots sent to Claude are scaled to fit this
_abort = threading.Event()
_task_lock = threading.Lock()  # remote and local voice must never drive the same mouse at once

_KEYS = {"return": "enter", "enter": "enter", "backspace": "backspace", "escape": "esc", "esc": "esc",
         "tab": "tab", "space": "space", "delete": "delete", "home": "home", "end": "end",
         "page_up": "pageup", "page_down": "pagedown", "prior": "pageup", "next": "pagedown",
         "up": "up", "down": "down", "left": "left", "right": "right", "super": "win", "win": "win",
         "cmd": "win", "meta": "win", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
         "insert": "insert", "caps_lock": "capslock", "print": "printscreen"}


def abort() -> None:
    """Called when the user interrupts Jarvis."""
    _abort.set()


def _keys(combo: str) -> list[str]:
    out = []
    for k in combo.replace(" ", "").split("+"):
        low = k.lower()
        out.append(_KEYS.get(low, low if len(low) > 1 else k.lower()))
    return out


class Screen:
    def __init__(self):
        from jarvis_pc.guardrails.stepfence import require_foreground
        require_foreground("drive the real mouse and keyboard")    # the pyautogui loop: never in a mission (2026-11-10)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, so clicks land exactly
        except Exception:
            pass
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        self.gui = pyautogui
        w, h = pyautogui.size()
        self.scale = min(1.0, TARGET[0] / w, TARGET[1] / h)

    def shot(self) -> dict:
        from PIL import ImageGrab

        img = ImageGrab.grab()
        if self.scale < 1:
            img = img.resize((round(img.width * self.scale), round(img.height * self.scale)))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(buf.getvalue()).decode()}}

    def zoom(self, region) -> dict:
        from PIL import ImageGrab

        x0, y0, x1, y1 = (v / self.scale for v in region)
        img = ImageGrab.grab(bbox=(int(x0), int(y0), int(math.ceil(x1)), int(math.ceil(y1))))
        img.thumbnail((1280, 1280))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(buf.getvalue()).decode()}}

    def xy(self, coord) -> tuple[int, int]:
        return round(coord[0] / self.scale), round(coord[1] / self.scale)

    def do(self, name: str, inp: dict):
        """Run one toolset member. Returns text or an image block (or a list, for a batch)."""
        if "actions" in inp:  # several actions in one call: [{"action": "type", "text": ...}, ...]
            return self._batch(inp["actions"])
        g = self.gui
        mods = _keys(inp["text"]) if name.endswith(("click", "scroll", "drag")) and inp.get("text") else []
        for m in mods:
            g.keyDown(m)
        try:
            if name == "screenshot":
                return self.shot()
            if name == "zoom":
                return self.zoom(inp["region"])
            if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
                x, y = self.xy(inp["coordinate"]) if inp.get("coordinate") else g.position()
                button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
                clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
                g.click(x, y, clicks=clicks, interval=0.08, button=button)
            elif name == "left_click_drag":
                g.moveTo(*self.xy(inp["start_coordinate"]))
                g.dragTo(*self.xy(inp["coordinate"]), duration=0.4, button="left")
            elif name == "mouse_move":
                g.moveTo(*self.xy(inp["coordinate"]), duration=0.15)
            elif name == "left_mouse_down":
                g.mouseDown()
            elif name == "left_mouse_up":
                g.mouseUp()
            elif name == "cursor_position":
                x, y = g.position()
                return f"Cursor at {round(x * self.scale)}, {round(y * self.scale)}."
            elif name == "scroll":
                if inp.get("coordinate"):
                    g.moveTo(*self.xy(inp["coordinate"]))
                amount = int(inp.get("scroll_amount", 3)) * 120
                direction = inp["scroll_direction"]
                if direction in ("up", "down"):
                    g.scroll(amount if direction == "up" else -amount)
                else:
                    g.hscroll(amount if direction == "right" else -amount)
            elif name == "type":
                _type_text(inp["text"])
            elif name == "key":
                for _ in range(int(inp.get("repeat", 1))):
                    g.hotkey(*_keys(inp["text"]))
            elif name == "hold_key":
                keys = _keys(inp["text"])
                for k in keys:
                    g.keyDown(k)
                time.sleep(min(float(inp.get("duration", 1)), 10))
                for k in reversed(keys):
                    g.keyUp(k)
            elif name == "wait":
                time.sleep(min(float(inp.get("duration", 1)), 10))
            else:
                raise ValueError(f"unsupported action {name}")
        finally:
            for m in reversed(mods):
                g.keyUp(m)
        time.sleep(0.15)
        return "OK"


def _parse_actions(raw) -> list[dict]:
    """The model sometimes sends the batch as a JSON string, occasionally slightly malformed
    ('{"action": "screenshot": null}'); accept what can be understood."""
    import json

    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        fixed = re.sub(r'"action":\s*"(\w+)":\s*null', r'"action": "\1"', str(raw))
        return json.loads(fixed)


def _batch(self, raw) -> list:
    out = []
    for a in _parse_actions(raw):
        a = dict(a)
        name = a.pop("action", None) or a.pop("name", "")
        r = self.do(name, a)
        if isinstance(r, dict):
            out.append(r)
    return out or "OK"


Screen._batch = _batch


def _type_text(text: str) -> None:
    """Paste through the clipboard so any language/accents come through exactly."""
    import pyautogui
    import pyperclip
    from jarvis_pc.guardrails.stepfence import fence, require_foreground

    # A step past its time limit types nothing (core.pcmethods._bounded); a mission step never pastes into the window in
    # front, nor touches the owner's clipboard (2026-11-10, Mark VI).
    require_foreground("paste text into the window in front")
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    pyperclip.copy(text)
    fence("paste text")
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if previous is not None:
        pyperclip.copy(previous)


@tool(
    "Complete a PC task using free local automation or saved workflows first. For unfamiliar tasks, "
    "plan with local AI when available; cloud planning requires a separate exact-task owner budget. "
    "No automatic paid screenshot/click loop. Review or budget requests must be shown to the owner.",
    {"task": {"type": "string"}}, slow=True,
)
def control_computer(task: str, *, cancel_event=None) -> list:
    from core import pchybrid
    from core import approvals, users

    if "the user confirmed" in task.lower() and not approvals.consume(users.current(), "computer"):
        return [{"type": "text", "text": approvals.request(users.current(), "computer", "Review the exact sensitive PC operation")}]

    pchybrid.clear_result()
    if not _task_lock.acquire(blocking=False):
        return [{"type": "text", "text": "The PC is already being controlled. Check its status before starting another task."}]
    try:
        _abort.clear()
        cancelled = lambda: _abort.is_set() or (cancel_event is not None and cancel_event.is_set())
        outcome = pchybrid.run(task, cancelled)
        text = outcome["detail"]
        if outcome["state"] in ("needs_review", "needs_budget"):
            text += " Open PC control to review this request; no further action will run automatically."
        # Verification evidence and cost remain in the ledger/result object.
        # They are not natural spoken confirmation for a simple request such as
        # “open YouTube”; report them only when a review screen asks for detail.
        return [{"type": "text", "text": text}]
    finally:
        _task_lock.release()
