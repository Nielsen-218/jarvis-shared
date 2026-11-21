"""The thin pywinauto/UI Automation layer under the executor (the only module that imports pywinauto).

Controls are found by NAME and type; actions use UI Automation patterns, so the mouse cursor is never moved. Everything raises
executor.UIError with a plain-words reason (for example "no button called Save", "the control is disabled").
"""
from __future__ import annotations

import re
import sys
import threading
import time
from typing import Iterable

from .executor import UIError

_mta_ready = threading.Event()
_mta_lock = threading.Lock()


def ensure_mta(timeout: float = 15.0) -> bool:
    """Load pywinauto and create its UI Automation object on a multithreaded-apartment (MTA) thread, once per process.

    Why: pywinauto keeps ONE UI Automation COM object for the whole process, created by whichever thread imports it first. If
    that thread is single-threaded (STA; `import comtypes` makes a thread STA by default, and pywinauto then reverts to STA),
    every UI call from any other thread is marshalled back to that thread, and blocks forever while it is busy or waiting
    (2026-11-06: a 5 h acceptance hang, Brain.ask's main thread waiting on the turn lock while a worker's save_document sat
    in a UIA FindAll). Created in the MTA, the object answers any thread directly.

    The calling thread keeps the COM setup it always had (comtypes is imported HERE first, as before). Returns True when
    the object lives in the MTA; False when pywinauto had already been loaded elsewhere (too late to choose) or the load
    did not finish in `timeout` seconds."""
    if _mta_ready.is_set():
        return "pywinauto" in sys.modules
    with _mta_lock:
        if _mta_ready.is_set():
            return "pywinauto" in sys.modules
        if "pywinauto" in sys.modules:
            _mta_ready.set()
            return False
        try:
            import comtypes  # noqa: F401  (the caller's own implicit COM setup, unchanged)
        except Exception:  # noqa: BLE001
            pass
        result = {}

        def load():
            try:
                import comtypes
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                from pywinauto import uia_defines
                uia_defines.IUIA()
                result["ok"] = True
            except Exception as e:  # noqa: BLE001
                result["error"] = repr(e)

        t = threading.Thread(target=load, daemon=True, name="uia-mta-init")
        t.start()
        t.join(timeout)
        _mta_ready.set()
        return bool(result.get("ok"))


def _desktop():
    ensure_mta()
    from pywinauto import Desktop
    return Desktop(backend="uia")


def window(title: str):
    """The window with this title, resolved ONCE to a concrete handle-based wrapper (a title that changes later, like Notepad adding
    an asterisk for unsaved text, must not lose it)."""
    try:
        # Fast path: exactly one visible top-level window with this exact title, found by the Win32 window list. The UI
        # Automation title search walks every top-level window and cost 2.6-3.7 s per call (measured 2026-11-09).
        from tools import pcops
        hits = [w for w in pcops.windows() if (w.get("title") or "") == title and w.get("hwnd")]
        if len(hits) > 1:
            # A Store app shows twice: its frame (ApplicationFrameHost) and the app's own inner window (SystemSettings.exe).
            # The frame holds the whole UI; with both listed the title search below found two and spent 20 s failing
            # (Settings, measured 2026-11-09).
            frames = [w for w in hits if (w.get("process") or "").lower() == "applicationframehost.exe"]
            if len(frames) == 1:
                hits = frames
        if len(hits) == 1:
            spec = _desktop().window(handle=hits[0]["hwnd"])
            spec.wrapper_object()
            return spec
    except Exception:  # noqa: BLE001  (no Win32 list or the window just went: the UI Automation search decides)
        pass
    try:
        w = _desktop().window(title=title)
        w.wait("exists", timeout=8)
        hwnd = w.wrapper_object().handle
        return _desktop().window(handle=hwnd)     # a handle-based spec: keeps working when the title changes
    except Exception:  # noqa: BLE001
        pass
    try:                                           # the title grew a marker ("* (Unsaved) - Blender", "*Untitled - Notepad"): still that window, if only one
        w = _desktop().window(title_re=".*" + re.escape(title.strip()) + ".*", visible_only=True)
        w.wait("exists", timeout=2)
        return _desktop().window(handle=w.wrapper_object().handle)
    except Exception:  # noqa: BLE001
        raise UIError(f"no window called '{title}'") from None


def find(win, name: str, types: Iterable[str]):
    """The control whose name equals `name` (any case) and whose type is one of `types`; falls back to 'contains'.
    Targeted lookups (one per type) instead of walking every control, which is several seconds slower per step."""
    types = tuple(types)
    for pattern in (r"(?i)^" + re.escape(name.strip()) + r"$", r"(?i).*" + re.escape(name.strip()) + r".*"):
        for t in types:
            try:
                spec = win.child_window(title_re=pattern, control_type=t)
                if spec.exists(timeout=0.3, retry_interval=0.1):
                    return spec.wrapper_object()
            except Exception:  # noqa: BLE001
                continue
    raise UIError(f"no {'/'.join(t.lower() for t in types[:2])} called '{name}' in this window")


def is_password(ctl) -> bool:
    try:
        return bool(ctl.element_info.element.CurrentIsPassword)
    except Exception:  # noqa: BLE001
        return False


def press(ctl) -> None:
    from ..guardrails.stepfence import fence
    fence("press a control")      # a PC step past its time limit changes nothing
    if not ctl.is_enabled():
        raise UIError("the control is disabled right now (it may become available after a wait)")
    kind = ctl.element_info.control_type
    try:
        if kind in ("ListItem", "TabItem", "RadioButton"):
            ctl.select()
        elif kind == "CheckBox":
            ctl.toggle()
        else:
            ctl.invoke()
    except Exception as e:  # noqa: BLE001
        # Chromium/Electron apps (ChatGPT, Discord...) expose some buttons without the Invoke pattern (live 2026-11-09:
        # ChatGPT's project buttons -> NoPatternInterfaceError). Their accessibility "default action" clicks them without
        # moving the mouse. Still no real-cursor click here.
        try:
            from pywinauto.uia_defines import get_elem_interface
            get_elem_interface(ctl.element_info.element, "LegacyIAccessible").DoDefaultAction()
        except Exception:  # noqa: BLE001
            raise UIError(f"could not press it: {type(e).__name__}") from None


def set_text(ctl, text: str) -> None:
    from ..guardrails.stepfence import fence
    fence("set a field")      # a PC step past its time limit changes nothing
    if not ctl.is_enabled():
        raise UIError("the field is disabled")
    try:
        ctl.set_edit_text(text)
    except Exception as e:  # noqa: BLE001
        raise UIError(f"could not set the text: {type(e).__name__}") from None


# ------------------------------------------------------------------------------------------- background hands (Mark VI)
# A mission never takes the foreground (2026-11-10). These put text and keys into a window WHERE IT IS, behind the owner's
# windows: UI Automation's Value pattern first, then window messages to the control's own native handle. None of them
# focuses, moves the mouse or uses SendInput (so GetLastInputInfo, the owner's "is someone at the PC" signal, is untouched).
# Each one proves its effect by reading the control back, or refuses with BackgroundUnavailable.

EM_REPLACESEL = 0x00C2      # replaces the CURRENT SELECTION (inserts at the caret only when nothing is selected); undoable
EM_SETSEL = 0x00B1          # moves the caret / selection: set to (end, end) first, so nothing of his is replaced
WM_GETTEXTLENGTH = 0x000E
_NATIVE_TEXT_CLASSES = ("RichEditD2DPT", "Edit")         # Windows 11 Notepad's editor; the classic edit box
_UNCERTAIN = ("I sent the text to that window but couldn't confirm it by reading the window back, so please check it "
              "before I try again")


def editable(win, only: bool = False):
    """The window's text area (the same choice core.pcverbs makes): a Document, else an Edit, enabled and visible.
    only (background typing, 2026-11-10 review): the window's ONLY text area; with several (a hidden tab's editor, a second
    field) it refuses instead of guessing which one is in front."""
    from core.pcverbs import _editable, _text_areas
    if only:
        areas = _text_areas(win)
        if len(areas) > 1:
            from ..guardrails.stepfence import BackgroundUnavailable
            raise BackgroundUnavailable("type into that window", "it has more than one text area and I can't tell which "
                                        "one is in front from behind your windows, so it needs you at the PC")
        if areas:
            return areas[0]
    return _editable(win)


def _landed(ctl, text: str) -> bool:
    from core.pcverbs import _text_of
    want = " ".join((text or "").split())
    return bool(want) and want in " ".join(_text_of(ctl).split())


def _text_children(top: int) -> list:
    import win32gui
    found = []

    def visit(h, _x):
        try:
            if win32gui.GetClassName(h) in _NATIVE_TEXT_CLASSES:
                found.append(int(h))
        except Exception:  # noqa: BLE001
            pass
        return True
    try:
        win32gui.EnumChildWindows(top, visit, None)
    except Exception:  # noqa: BLE001
        pass
    return found


def _native_text_hwnd(ctl, strict: bool = False) -> int:
    """The native window handle that holds the control's text: the control's own handle when it is a text window, else the
    first RichEditD2DPT / Edit child of its top-level window. 0 when there is none.
    strict (background writes and keys, 2026-11-10 review): never 'the first' text child - Windows 11 Notepad keeps a hidden
    tab's editor (his restored README) in the same window - only the ONLY one; 0 when there are several."""
    import win32gui
    own = 0
    try:
        own = int(ctl.element_info.handle or 0)
    except Exception:  # noqa: BLE001
        pass
    if own and win32gui.GetClassName(own) in _NATIVE_TEXT_CLASSES:
        return own
    try:
        top = int(ctl.top_level_parent().handle)
    except Exception:  # noqa: BLE001
        return 0
    if strict:
        found = _text_children(top)
        return found[0] if len(found) == 1 else 0
    for cls in _NATIVE_TEXT_CLASSES:
        found = []

        def visit(h, _x, cls=cls, found=found):
            if not found and win32gui.GetClassName(h) == cls:
                found.append(h)
            return True
        try:
            win32gui.EnumChildWindows(top, visit, None)
        except Exception:  # noqa: BLE001
            pass
        if found:
            return int(found[0])
    return 0


def _value_pattern(ctl):
    """The control's UIA Value pattern, or None (Win11 Notepad's document has no writable one)."""
    try:
        from pywinauto.uia_defines import get_elem_interface
        return get_elem_interface(ctl.element_info.element, "Value")
    except Exception:  # noqa: BLE001
        return None


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _appended(before: str, after: str, text: str) -> bool:
    """Appending kept everything that was there (nothing of his replaced or spliced into) and added the text after it."""
    b, a, want = _norm(before), _norm(after), _norm(text)
    return bool(want) and a.startswith(b) and want in a[len(b):]


def _caret_to_end(h: int) -> None:
    """Collapse any selection he left and put the caret at the end, so EM_REPLACESEL / WM_CHAR replace nothing."""
    import win32gui
    n = int(win32gui.SendMessage(h, WM_GETTEXTLENGTH, 0, 0) or 0)
    win32gui.SendMessage(h, EM_SETSEL, n, n)


def set_text_bg(ctl, text: str, append: bool = False) -> str:
    """Put text into a field without focusing its window. Rungs, in order: (a) the UIA Value pattern (SetValue; when
    appending, the current text plus `text`), (b) WM_SETTEXT on the control's native handle, or EM_REPLACESEL after moving
    the caret to the end when appending, (c) WM_CHAR per character (appending, or into an empty field). Returns the rung that
    worked.

    2026-11-10 review: (1) an append must keep everything that was there - the read-back checks the old text is still the
    start and the new text follows it (EM_REPLACESEL replaces his selection, so the caret goes to the end first); (2) a rung
    that SENT a write but cannot read it back stops everything with UncertainOutcome: the next rung would write again (text
    twice) and 'nothing was done' would be false; (3) only the field's own native handle, or the window's ONLY text child.
    BackgroundUnavailable only when no write was sent at all."""
    from ..guardrails.stepfence import BackgroundUnavailable, UncertainOutcome, fence
    from core.pcverbs import _text_of
    fence("set a field")                         # a PC step past its time limit changes nothing
    if not ctl.is_enabled():
        raise UIError("the field is disabled")
    before = _text_of(ctl)

    def shows() -> bool:
        return _appended(before, _text_of(ctl), text) if append else _landed(ctl, text)

    def unchanged() -> bool:
        return _text_of(ctl) == before

    value = _value_pattern(ctl)
    if value is not None and not getattr(value, "CurrentIsReadOnly", False):
        try:
            value.SetValue((before + text) if append else text)
        except Exception:  # noqa: BLE001 - refused outright: only when the field provably did not change is the next rung safe
            if not unchanged():
                raise UncertainOutcome(_UNCERTAIN) from None
        else:
            if shows():
                return "value"
            raise UncertainOutcome(_UNCERTAIN)
    import win32con
    import win32gui
    h = _native_text_hwnd(ctl, strict=True)
    if h:
        fence("set a field")
        try:
            if append:
                _caret_to_end(h)
                win32gui.SendMessage(h, EM_REPLACESEL, 1, text)
            else:
                win32gui.SendMessage(h, win32con.WM_SETTEXT, 0, text)
        except Exception:  # noqa: BLE001
            if not unchanged():
                raise UncertainOutcome(_UNCERTAIN) from None
        else:
            if shows():
                return "em_replacesel" if append else "wm_settext"
            raise UncertainOutcome(_UNCERTAIN)
        if append or not before:
            fence("set a field")
            try:
                if append:
                    _caret_to_end(h)
            except Exception:  # noqa: BLE001
                raise BackgroundUnavailable("type into the window behind your windows",
                                            "I couldn't put the cursor at the end of the text, so it needs you at the PC") from None
            for ch in text:
                win32gui.PostMessage(h, win32con.WM_CHAR, ord(ch), 0)
            deadline = time.monotonic() + 1.5                # posted: the window works through its queue
            while time.monotonic() < deadline:
                if shows():
                    return "wm_char"
                time.sleep(0.1)
            raise UncertainOutcome(_UNCERTAIN)               # posted characters may still land: never 'nothing was done'
    raise BackgroundUnavailable("type into the window behind your windows",
                                "I couldn't find exactly one text area to write into from behind your windows, so it "
                                "needs you at the PC")


_VK = {"enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
       "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22}


def _vk(key: str) -> int:
    key = key.strip().lower()
    if key in _VK:
        return _VK[key]
    if re.fullmatch(r"f([1-9]|1[0-2])", key):
        return 0x6F + int(key[1:])                       # VK_F1 = 0x70
    if re.fullmatch(r"[a-z0-9]", key):
        return ord(key.upper())
    raise UIError(f"unknown key {key!r}")


def _focused_child(hwnd: int) -> int:
    """The child window that has the keyboard focus inside the target window's OWN thread (GetGUIThreadInfo), never the
    global focus, which is the owner's front window. 0 when that thread reports none."""
    import ctypes
    from ctypes import wintypes
    import win32process

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]
    tid, _pid = win32process.GetWindowThreadProcessId(hwnd)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


def _root(hwnd: int) -> int:
    """The top-level window a handle belongs to (GetAncestor GA_ROOT); 0 when unknown."""
    try:
        import win32con
        import win32gui
        return int(win32gui.GetAncestor(int(hwnd), getattr(win32con, "GA_ROOT", 2)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def key_bg(win, keys: str) -> None:
    """One key ('enter', 'down', 'f5', 'a') posted as WM_KEYDOWN/WM_KEYUP to the window's focused child, without focusing it.
    Modifier chords (ctrl+s) are refused: the app reads the real modifier state (GetKeyState), so a posted chord is
    unreliable; use the menu item or button through UI Automation instead."""
    from ..guardrails.stepfence import BackgroundUnavailable, fence
    from . import lang
    mods, key = lang.split_hotkey(keys) if "+" in keys else ([], keys)
    if mods:
        raise BackgroundUnavailable(f"press {keys}", "a key chord can't be sent to a window behind yours")
    vk = _vk(key)
    top = _hwnd(win)
    target = _focused_child(top)
    if target and _root(target) != int(top):
        # 2026-11-10 review: the thread's focus is in ANOTHER top-level window of the same thread (Edge runs all its
        # windows on one UI thread: his own window, where Enter would submit whatever he has open). Never there.
        target = 0
    if not target:
        target = _native_text_hwnd(_HandleOnly(top), strict=True)
    if target and _root(target) != int(top):
        target = 0
    if not target:
        raise BackgroundUnavailable(f"press {keys}", "I can't tell which part of that window would get the key")
    import win32api
    import win32con
    import win32gui
    scan = win32api.MapVirtualKey(vk, 0)
    fence("press " + keys)                              # a PC step past its time limit changes nothing
    win32gui.PostMessage(target, win32con.WM_KEYDOWN, vk, 1 | (scan << 16))
    win32gui.PostMessage(target, win32con.WM_KEYUP, vk, 1 | (scan << 16) | 0xC0000000)


class _HandleOnly:
    """A top-level window handle shaped like a control, for _native_text_hwnd."""

    def __init__(self, hwnd: int):
        self.handle = hwnd
        self.element_info = self

    def top_level_parent(self):
        return self


def set_checked(ctl, on: bool) -> None:
    if not ctl.is_enabled():
        raise UIError("the control is disabled")
    try:
        if bool(ctl.get_toggle_state()) != on:
            ctl.toggle()
    except Exception as e:  # noqa: BLE001
        raise UIError(f"could not change it: {type(e).__name__}") from None


def select(ctl) -> None:
    from ..guardrails.stepfence import fence
    fence("select an item")      # a PC step past its time limit changes nothing
    try:
        ctl.select()
    except Exception as e:  # noqa: BLE001
        raise UIError(f"could not select it: {type(e).__name__}") from None


def read(win, needle: str) -> str:
    for t in ("Text", "Edit", "Button", "ListItem", "CheckBox", "Document"):
        try:
            spec = win.child_window(title_re=r"(?i).*" + re.escape(needle.strip()) + r".*", control_type=t)
            if spec.exists(timeout=0.3, retry_interval=0.1):
                c = spec.wrapper_object()
                return (c.window_text() or c.element_info.name or "") if t == "Edit" else (c.element_info.name or "")
        except Exception:  # noqa: BLE001
            continue
    # deep trees (WinUI apps such as the new Notepad) are slow for targeted lookups: scan the text-like controls directly
    for t in ("Text", "Edit", "Document", "Button", "ListItem", "CheckBox"):
        try:
            for c in win.descendants(control_type=t):
                name = c.element_info.name or ""
                if needle.strip().lower() in name.lower():
                    return (c.window_text() or name) if t == "Edit" else name
        except Exception:  # noqa: BLE001
            continue
    raise UIError(f"nothing in this window contains '{needle}'")


def _in_front(hwnd: int) -> bool:
    import win32gui
    return int(win32gui.GetForegroundWindow()) == hwnd


def focus(win) -> None:
    """Bring the window to the front and PROVE it is there. Keys go to whatever is in front, so if the window cannot be brought to the
    front this raises instead of letting a later key press land in some other program."""
    from ..guardrails.stepfence import require_foreground
    # A past-limit step changes nothing; a mission (background) step never takes the owner's front window (2026-11-10).
    require_foreground("bring a window to the front")
    hwnd = _hwnd(win)
    for _ in range(4):
        try:
            win.set_focus()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
        if _in_front(hwnd):
            return
    raise UIError("could not bring the window to the front, so I did not send any keys")


def send_keys(win, keys: str, literal: bool = False) -> None:
    from ..guardrails.stepfence import require_foreground
    require_foreground("send keys to the window in front")       # real keys go to the foreground: never in a mission (2026-11-10)
    if not _in_front(_hwnd(win)):                      # re-check right before sending: focus can be stolen between steps
        focus(win)
    try:
        win.type_keys(keys, with_spaces=True, pause=0.02)
    except Exception as e:  # noqa: BLE001
        raise UIError(f"could not send keys: {type(e).__name__}") from None
    time.sleep(0.15)


def _hwnd(win) -> int:
    return int(win.wrapper_object().handle)


def move(win, x: int, y: int) -> None:
    import win32gui
    h = _hwnd(win)
    l, t, r, b = win32gui.GetWindowRect(h)
    win32gui.MoveWindow(h, x, y, r - l, b - t, True)


def resize(win, w: int, h_: int) -> None:
    import win32gui
    h = _hwnd(win)
    l, t, _r, _b = win32gui.GetWindowRect(h)
    win32gui.MoveWindow(h, l, t, w, h_, True)


def close(win) -> None:
    from ..guardrails.stepfence import fence
    fence("close a window")      # a PC step past its time limit changes nothing
    import win32con
    import win32gui
    win32gui.PostMessage(_hwnd(win), win32con.WM_CLOSE, 0, 0)


def rect(win):
    """(left, top, right, bottom) of the bound window on screen."""
    import win32gui
    return win32gui.GetWindowRect(_hwnd(win))


def screen_labels(win):
    """What a person would read on this window when UI Automation shows nothing (a custom-drawn app such as Blender, a game, a canvas):
    local OCR of the window's own pixels only. [{text, center:[x, y], confidence}] in screen coordinates. Never another window."""
    from core import pcvision
    from ..guardrails.stepfence import is_background
    l, t, r, b = rect(win)
    if r - l < 20 or b - t < 20:
        return []
    if is_background():
        return _screen_labels_bg(win, (l, t, r, b))
    if not _in_front(_hwnd(win)):
        # OCR reads the SCREEN inside the window's rectangle: with another app on top it read that app instead (Claude's text
        # came back as "Settings" on 2026-11-09). In front first, or raise; never another window's words.
        from ..guardrails.stepfence import require_foreground
        require_foreground("bring the window to the front to read it")   # a mission never does (2026-11-10)
        focus(win)
        l, t, r, b = rect(win)
    region = (l, t, r - l, b - t)
    key = _pixels_key(region)
    if key is not None and key == _last_read["key"]:
        # The very same pixels were read a moment ago (observe, then the tap that acts on it): reuse that reading. The tap
        # re-read the whole Blender window, ~10 s, right after observe had (live 2026-11-09). Any change on screen is a new key.
        return [dict(x) for x in _last_read["labels"]]
    pcvision.clear_cache()
    icons = _start_icons(region)                   # the icon detector runs while OCR reads (both release the GIL)
    data = pcvision.read_screen(region=region, want_uia=False)
    words = [x for x in data.get("ocr", []) if l <= x["center"][0] <= r and t <= x["center"][1] <= b]
    labels = words + _icon_labels(icons, words)
    _last_read.update(key=key, labels=[dict(x) for x in labels])
    return labels


_last_read = {"key": None, "labels": []}


def _screen_labels_bg(win, box):
    """screen_labels for a mission (2026-11-10, Mark VI): the window paints itself behind the owner's windows (PrintWindow,
    core.pcvision.grab_window_bg), so it is never brought forward and never mixed up with what is on top. OCR runs on that
    picture and its coordinates are moved by the window's (left, top) into screen coordinates, like the foreground read.
    Raises BackgroundUnavailable when the window cannot be captured (minimized, black)."""
    import hashlib
    from core import pcvision
    l, t, r, b = box
    img = pcvision.grab_window_bg(_hwnd(win))
    region = (l, t, r - l, b - t)
    key = (tuple(region), hashlib.md5(img.tobytes()).hexdigest())
    if key == _last_read["key"]:
        return [dict(x) for x in _last_read["labels"]]
    icons = _start_icons(region, img=img)
    lines, _note = pcvision.ocr_image(img, origin=(l, t))
    words = [x for x in lines if l <= x["center"][0] <= r and t <= x["center"][1] <= b]
    labels = words + _icon_labels(icons, words)
    _last_read.update(key=key, labels=[dict(x) for x in labels])
    return labels


def _pixels_key(region):
    """A fingerprint of exactly what is on screen in this region now (None when it cannot grab)."""
    try:
        import hashlib
        from core import pcvision
        img, _ = pcvision._grab(region)
        return (tuple(region), hashlib.md5(img.tobytes()).hexdigest())
    except Exception:  # noqa: BLE001
        return None


def _start_icons(region, img=None):
    """Icon-only buttons (gear, play, '+') OCR cannot read: OmniParser's detector (core/uiicons), started in the background.
    None when the model is missing or JARVIS_UI_ICONS=off. `img`: a picture already taken of this region (a background
    capture, 2026-11-10); without it the screen region is grabbed."""
    try:
        from core import pcvision, uiicons
        if not uiicons.enabled():
            return None
        import concurrent.futures
        img, origin = (img, (region[0], region[1])) if img is not None else pcvision._grab(region)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        job = pool.submit(uiicons.detect, img)
        pool.shutdown(wait=False)
        return img, origin, job
    except Exception:  # noqa: BLE001
        return None


def _icon_labels(started, words):
    """The detected icons, named by one local vision call for the new ones. [] on any failure: the OCR words stand alone."""
    if not started:
        return []
    try:
        from core import uiicons
        img, origin, job = started
        boxes = job.result(timeout=30)
        return uiicons.labels(img, words, origin=origin, detect_fn=lambda _img: boxes)
    except Exception:  # noqa: BLE001
        return []


def click_at(win, x: int, y: int) -> None:
    """A real mouse click, only inside the bound window's rectangle, after bringing it to the front. This is the one place the
    cursor moves: the fallback for windows whose controls UI Automation cannot see."""
    from ..guardrails.stepfence import require_foreground
    require_foreground("click with the real mouse")      # past its limit, or a mission step: no click (2026-11-10)
    l, t, r, b = rect(win)
    if not (l <= x <= r and t <= y <= b):
        raise UIError("that point is outside the window, so I did not click")
    focus(win)
    try:
        from pywinauto import mouse
        mouse.move(coords=(int(x), int(y)))        # hover first: custom-drawn UIs (Blender) activate the region under the cursor
        time.sleep(0.25)                            # before they accept a click, and swallow a click that arrives cold
        mouse.click(coords=(int(x), int(y)))
    except Exception as e:  # noqa: BLE001
        raise UIError(f"could not click there: {type(e).__name__}") from None
    time.sleep(0.3)


def visible(title: str, limit: int = 40) -> str:
    """What a person would see in the window: the names of its controls by type, with digits masked (so a number shown on screen is
    not handed to a model as an answer to copy; it learns WHERE to look, not what the value was). Returns '' if it cannot look."""
    try:
        win = window(title)
        seen, lines = set(), []
        tabs = {re.sub(r"\.\s*(Modified|Unmodified)\.?$", "", (c.element_info.name or "")).strip() for c in win.descendants(control_type="TabItem")}
        for t in ("Button", "CheckBox", "RadioButton", "Edit", "ListItem", "Text", "TabItem", "MenuItem", "ComboBox", "Document"):
            names = []
            for c in win.descendants(control_type=t):
                n = re.sub(r"\d", "#", (c.element_info.name or "").strip().replace("\n", " "))[:50]
                if t == "TabItem" or (t == "Text" and (c.element_info.name or "").strip() in tabs):
                    continue                      # open document tabs are not controls to act on, and a restored session can have dozens
                if n and (t, n) not in seen and n not in ("System", "Minimize", "Maximize", "Close"):
                    seen.add((t, n))
                    names.append(n)
            if names:
                lines.append(f"{t}s: " + ", ".join(f'"{n}"' for n in names[:12]))
        return "\n".join(lines)[: limit * 60]
    except Exception:  # noqa: BLE001
        return ""
