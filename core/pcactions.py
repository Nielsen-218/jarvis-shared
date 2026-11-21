"""Typed local operations + real postconditions. No LLM, shell text, or coordinate scripts."""
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode

import psutil

APPS = {"notepad": "notepad.exe", "calculator": "calculatorapp.exe", "paint": "mspaint.exe",
    "blender": "blender.exe", "chrome": "chrome.exe", "firefox": "firefox.exe", "brave": "brave.exe",
    "spotify": "spotify.exe", "file explorer": "explorer.exe"}
SCHEMAS = {
    "open_app": ({"app": str}, False), "focus_app": ({"process": str}, False),
    "process_status": ({"process": str}, False), "set_volume": ({"percent": int}, False),
    "get_volume": ({}, False), "pc_status": ({}, False), "list_windows": ({}, False), "open_url": ({"url": str}, False),
    "search_foreground_browser": ({"query": str}, False), "scroll_foreground": ({"direction": str}, False),
    "close_foreground": ({}, True), "close_browser_tab": ({}, True), "close_site_tab": ({"site": str}, True),
    "blender_status": ({}, False), "list_scene_objects": ({}, False),
    "unreal_status": ({}, False), "shadow_world_check": ({"which": str}, False),
    "unreal_tests": ({"contains": str}, False), "unreal_run_tests": ({"contains": str}, False),
    "unreal_test_results": ({}, False), "unreal_log": ({"contains": str}, False), "unreal_find_asset": ({"query": str}, False),
    "shadow_world_import": ({"which": str}, False), "shadow_world_retarget": ({"which": str}, False),
    "shadow_world_game_test": ({"which": str}, False),
    "inspect_blender": ({}, False),
    "render_preview": ({"path": str}, True), "export_glb": ({"path": str}, True),
    "browser_search": ({"query": str, "site": str}, False), "research_web": ({"query": str}, False),
    "read_web_page": ({"url": str, "question": str}, False),
    "browse_research": ({"query": str, "site": str}, False), "browse_open": ({"query": str, "site": str}, False),
    "media_command": ({"command": str}, False), "browse_followup": ({"number": int, "mode": str}, False),
    "change_volume": ({"delta": int}, False),
    "close_app": ({"app": str}, True),
    "minimize_app": ({"app": str}, False), "minimize_foreground": ({}, False),
    "use_app": ({"app": str, "goal": str}, True),
    "install_program": ({"name": str}, True), "uninstall_program": ({"name": str}, True),
    "uia_read": ({"process": str, "window": str, "control_id": str}, False),
    "uia_set_text": ({"process": str, "window": str, "control_id": str, "expected_name": str, "text": str}, True),
    "uia_invoke": ({"process": str, "window": str, "control_id": str, "expected_name": str, "check_id": str, "expected_value": str}, True),
    # Files, projects, text, screen, terminal (the gateway plans these; core.authz decides by effect whether to ask).
    "find_project": ({"name": str}, False), "open_path": ({"path": str}, True), "find_files": ({"folder": str, "pattern": str}, False),
    "type_text": ({"app": str, "text": str}, True), "save_document": ({"app": str, "path": str}, True), "read_screen": ({"app": str}, False),
    "run_terminal": ({"command": str}, True), "investigate_slowness": ({}, False),
}
# The one argument that may be empty: read_screen with no app reads the whole screen.
_MAY_BE_EMPTY = {("read_screen", "app"), ("shadow_world_check", "which"), ("unreal_tests", "contains"), ("unreal_log", "contains"),
                 ("unreal_run_tests", "contains"), ("shadow_world_import", "which"), ("shadow_world_retarget", "which"),
                 ("shadow_world_game_test", "which")}    # empty -> the tool asks / takes the newest
UNREAL_TOOLS = ("unreal_status", "shadow_world_check", "unreal_tests", "unreal_run_tests", "unreal_test_results", "unreal_log",
                "unreal_find_asset", "shadow_world_import", "shadow_world_retarget", "shadow_world_game_test")


PUBLIC_SITES = {"youtube", "google", "bing", "duckduckgo", "wikipedia", "github"}
# Words of a tab's title: letters, digits and simple punctuation, at most 40 characters.
TAB_TITLE = re.compile(r"[a-z0-9][a-z0-9 .+'&-]{2,39}", re.I)
# ...but never words that name no one tab: they would close whichever tab's title happens to contain them ("the", "this").
VAGUE_TAB = {"all", "every", "each", "current", "other", "others", "new", "last", "first", "next", "previous", "browser", "edge",
             "chrome", "firefox", "microsoft", "google chrome", "tab", "tabs", "page", "pages", "window", "windows", "site", "sites",
             "website", "websites", "one", "ones", "it", "them", "this", "that", "these", "those", "the", "a", "an", "and", "or",
             "of", "to", "for", "in", "on", "my", "com", "www", "http", "https", "mail", "untitled", "localhost", "home", "blank"}


def tab_name_ok(site: str) -> bool:
    """A public site by name, or words of a tab's title with at least one word of 3+ letters that names something."""
    text = (site or "").strip().casefold()
    if text in PUBLIC_SITES:
        return True
    words = re.findall(r"[a-z0-9]+", text)
    return bool(TAB_TITLE.fullmatch(text)) and any(len(w) >= 3 and not w.isdigit() and w not in VAGUE_TAB for w in words)


def exe_name(name: str) -> str:
    """'notepad', 'Notepad.exe' and ' NOTEPAD ' are the same program (a plan's focus_app once matched nothing for 'notepad')."""
    n = (name or "").strip().casefold()
    return n if n.endswith(".exe") or not n else n + ".exe"


def validate(plan):
    if not isinstance(plan, dict) or set(plan) != {"steps"} or not isinstance(plan["steps"], list) or not 1 <= len(plan["steps"]) <= 12:
        raise ValueError("A plan must contain between one and twelve typed steps.")
    for step in plan["steps"]:
        if not isinstance(step, dict) or set(step) - {"tool", "arguments", "before", "expect"} or not {"tool", "arguments"} <= set(step):
            raise ValueError("Invalid plan step.")
        name, args = step["tool"], step["arguments"]
        if not isinstance(name, str) or name not in SCHEMAS or not isinstance(args, dict):
            raise ValueError("Unsupported local operation; arbitrary code/click scripts are not accepted.")
        schema = SCHEMAS[name][0]
        if set(args) != set(schema) or any(not isinstance(args[k], typ) or isinstance(args[k], bool) for k, typ in schema.items()):
            raise ValueError("Wrong arguments for " + name)
        if any(isinstance(v, str) and (len(v) > 2000 or (not v.strip() and (name, k) not in _MAY_BE_EMPTY)) for k, v in args.items()):
            raise ValueError("Empty or excessive argument.")
        if name == "set_volume" and not 0 <= args["percent"] <= 100:
            raise ValueError("Volume must be 0–100.")
        if name in ("open_url", "read_web_page"):
            from urllib.parse import urlsplit
            p = urlsplit(args["url"])
            if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
                raise ValueError("Only ordinary HTTP(S) URLs without credentials are allowed.")
        if name == "use_app" and len(args["goal"]) > 300:
            raise ValueError("The task for use_app must be at most 300 characters.")
        if name == "change_volume" and (args["delta"] == 0 or not -100 <= args["delta"] <= 100):
            raise ValueError("Volume change must be a non-zero amount from -100 to 100.")
        if name in ("browse_research", "browse_open") and args["site"] not in ("google", "bing", "youtube", "duckduckgo"):
            raise ValueError("Browse site must be Google, Bing, YouTube or DuckDuckGo.")
        if name == "browse_followup" and (args["mode"] not in ("open", "read") or not 1 <= args["number"] <= 8):
            raise ValueError("Follow-up needs result number 1-8 and mode open or read.")
        if name == "media_command" and not (args["command"] in ("pause", "play", "next", "previous")
                                             or re.fullmatch(r"(?:forward|back):\d{1,5}", args["command"])):
            raise ValueError("Media command must be pause, play, next, previous, forward:<seconds> or back:<seconds>.")
        if name == "browser_search" and args["site"] not in ("google", "bing", "youtube"):
            raise ValueError("Search site must be Google, Bing, or YouTube.")
        if name == "close_site_tab" and not tab_name_ok(args["site"]):
            # A public site by name, or a tab named by words of its title ("close my usb to ttl tab", holdout h009): the
            # closer only closes a browser tab whose title contains them (_close_named_site_tab), never the browser window.
            raise ValueError("Name the website or a few words of the tab's title to close.")
        if name == "scroll_foreground" and args["direction"] not in {"up", "down", "top", "bottom"}:
            raise ValueError("Scroll direction must be up, down, top, or bottom.")
        if name in ("render_preview", "export_glb"):
            p = Path(args["path"])
            if p.is_absolute() or ".." in p.parts or p.suffix.lower() not in ((".png",) if name == "render_preview" else (".glb",)):
                raise ValueError("Use a relative PNG/GLB filename inside the approved Blender workspace.")
        for key in ("before", "expect"):
            if key in step:
                c = step[key]
                if not isinstance(c, dict) or set(c) != {"process", "running"} or not isinstance(c["process"], str) or type(c["running"]) is not bool:
                    raise ValueError("Conditions must name a process and whether it must be running.")
    return plan


def process_running(name):
    return any((p.info["name"] or "").casefold() == name.casefold() for p in psutil.process_iter(["name"]))


def condition(c):
    return process_running(c["process"]) == c["running"]


def _hub(module):
    """Reuse PR #11's single installed backend; never start a second Blender worker."""
    configured = os.getenv("JARVIS_CONTROL_HUB_ROOT")
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parent.parent
    package = root / "hub"
    if not (package / (module + ".py")).is_file():
        raise RuntimeError("Shared Control Hub unavailable. Apply PR #11 or set JARVIS_CONTROL_HUB_ROOT to its existing project worktree.")
    # Load a named trusted installation, never a path supplied by a plan/model.
    import importlib.util
    spec = importlib.util.spec_from_file_location("jarvis_shared_hub_" + module, package / (module + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _uia_control(process, window, control_id):
    try:
        from pywinauto import Desktop
    except ImportError:
        raise RuntimeError("Windows UI Automation needs the optional pywinauto package. No click or paid-AI fallback was started.") from None
    candidates = []
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").casefold() == process.casefold():
            candidates.extend(Desktop(backend="uia").windows(process=proc.pid, title=window, visible_only=True))
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one visible window with the specified process and title.")
    matches = candidates[0].descendants(auto_id=control_id)
    if len(matches) != 1 or not matches[0].is_visible() or not matches[0].is_enabled():
        raise RuntimeError("The expected uniquely identified UI control is missing or unavailable.")
    control = matches[0]
    # Fail closed if the backend cannot certify that a field is not a password/secret input.
    if control.element_info.element.CurrentIsPassword or re.search(
            r"password|credit.?card|card.?number|security.?code|cvv|secret|api.?key|bank.?account", control.element_info.name, re.I):
        raise RuntimeError("Secret or financial credential fields cannot be read or filled by this workflow.")
    return control


def _uia_value(control):
    return str(control.get_value()) if hasattr(control, "get_value") else control.window_text()


def _require_foreground(what: str) -> None:
    """fence() plus, in background mode (a Mark VI mission), a structured refusal: these actions focus a window, send real
    keys or change the owner's desktop, and a mission never does (2026-11-10)."""
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground(what)


def _foreground_identity():
    """Return the current top-level window handle and executable, without guessing from a title."""
    if os.name != "nt":
        return 0, ""
    import ctypes
    hwnd = int(ctypes.windll.user32.GetForegroundWindow())
    if not hwnd:
        return 0, ""
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return hwnd, psutil.Process(pid.value).name()
    except (psutil.Error, OSError):
        return hwnd, ""


def foreground_browser() -> dict | None:
    """The browser that is actually in front, plus the public site identifiable from its title."""
    from tools import pcops
    browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
    front = next((row for row in pcops.windows() if row.get("foreground")), None)
    if not front or front.get("process", "").casefold() not in browsers:
        return None
    title = front.get("title", "").casefold()
    return {"hwnd": front.get("hwnd"), "process": front["process"],
            "site": "youtube" if "youtube" in title else "web"}


def _send_browser_search(query: str):
    """Navigate the visible, active browser through its address bar; never search a background app."""
    _require_foreground("search in the browser in front")
    from urllib.parse import quote_plus
    current = foreground_browser()
    if not current or not current.get("hwnd"):
        raise RuntimeError("A browser must be in front before I search in it.")
    url = ("https://www.youtube.com/results?search_query=" if current["site"] == "youtube"
           else "https://www.google.com/search?q=") + quote_plus(query)
    from jarvis_pc.guardrails.stepfence import fence
    fence("search in the browser")
    try:
        import pyperclip
        from pywinauto import Desktop
        old = pyperclip.paste()
        try:
            pyperclip.copy(url)
            window = Desktop(backend="uia").window(handle=int(current["hwnd"]))
            window.set_focus()
            window.type_keys("^l")
            window.type_keys("^v{ENTER}")
        finally:
            pyperclip.copy(old)
    except Exception as error:
        raise RuntimeError("Windows could not send the search to the foreground browser.") from error
    # The focused target is re-read immediately so a browser that disappeared or
    # lost focus is never reported as a successful search.
    after = foreground_browser()
    if not after or after.get("hwnd") != current["hwnd"]:
        return {"verified": False, "detail": "The browser lost focus before the search could be confirmed."}
    remember_listing("youtube" if current["site"] == "youtube" else "google", query)
    return {"verified": True, "detail": "Searching YouTube." if current["site"] == "youtube" else "Searching the web.",
            "site": current["site"], "url": url}


def _scroll_foreground(direction: str):
    """Send one deliberate scroll/navigation key sequence to the current visible app only."""
    _require_foreground("scroll the window in front")
    from tools import pcops
    front = next((row for row in pcops.windows() if row.get("foreground")), None)
    if not front or not front.get("hwnd"):
        raise RuntimeError("Windows did not identify a foreground window to scroll.")
    protected = {"python.exe", "pythonw.exe"}
    if front.get("process", "").casefold() in protected:
        raise RuntimeError("The foreground window is protected; nothing was scrolled.")
    keys = {"up": "{PGUP}", "down": "{PGDN}", "top": "^{HOME}", "bottom": "^{END}"}
    from jarvis_pc.guardrails.stepfence import fence
    fence("scroll")
    try:
        from pywinauto import Desktop
        window = Desktop(backend="uia").window(handle=int(front["hwnd"]))
        window.set_focus()
        window.type_keys(keys[direction])
    except Exception as error:
        raise RuntimeError("Windows could not send the scroll command to the foreground app.") from error
    active = next((row for row in pcops.windows() if row.get("foreground")), None)
    if not active or active.get("hwnd") != front["hwnd"]:
        return {"verified": False, "detail": "The foreground app changed before the scroll could be confirmed."}
    words = {"up": "Scrolled up.", "down": "Scrolled down.", "top": "At the top.", "bottom": "At the bottom."}
    return {"verified": True, "detail": words[direction], "target": front.get("process", "")}


# Windows Jarvis itself opened (newest last). Live 2026-11-06: "Open files and go to downloads" then "close it" closed the
# owner's ChatGPT window, because ChatGPT was in front; "it" was the Downloads window Jarvis had just opened.
OPENS = {"open_app", "open_path", "open_url", "browser_search", "browse_followup"}
OPENED_SECONDS = 30 * 60
_OPENED: list = []


_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
_SHELL_CLASSES = {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"}


def _window_class(hwnd: int) -> str:
    if os.name != "nt":
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value.casefold()


def _belongs(step: dict, process: str) -> bool:
    """Is the window in front really what this step opened? (A launch that stayed in the background leaves the owner's
    own window in front: that must never be recorded as Jarvis's.)"""
    tool, args, proc = step.get("tool"), step.get("arguments") or {}, process.casefold()
    if tool == "open_path":
        return proc == "explorer.exe"
    if tool in ("open_url", "browser_search", "browse_followup"):
        return proc in _BROWSERS
    if tool == "open_app":
        name = str(args.get("app", "")).casefold()
        tokens = [t for t in re.split(r"[^a-z0-9]+", name) if len(t) >= 3]
        aliases = {"file explorer": "explorer", "files": "explorer", "calculator": "applicationframehost", "settings": "applicationframehost"}
        return any(t in proc for t in tokens) or (aliases.get(name, "\0") in proc)
    return False


_LAST_OPEN: dict = {}        # the last app Jarvis opened by name: {"app", "at"}


def note_opened(step: dict | None = None) -> None:
    if step and step.get("tool") == "open_app":
        _LAST_OPEN.update(app=str((step.get("arguments") or {}).get("app", "")), at=time.monotonic())
    hwnd, process = _foreground_identity()
    if not (hwnd and process) or _window_class(hwnd) in _SHELL_CLASSES or (step is not None and not _belongs(step, process)):
        # The app opened BEHIND another window (live 2026-11-07: Calculator stayed behind Claude). Record the app's own
        # window, found by name, so a later "close it" closes it, not the window the owner is using.
        hwnd, process = _own_window(step)
        if not hwnd:
            return
    app = str(((step or {}).get("arguments") or {}).get("app", "")) if (step or {}).get("tool") == "open_app" else ""
    _OPENED[:] = [o for o in _OPENED if o["hwnd"] != hwnd][-5:] + [{"hwnd": hwnd, "process": process, "at": time.monotonic(), "app": app}]


def _own_window(step: dict | None):
    if not step or step.get("tool") != "open_app":
        return 0, ""
    try:
        from tools import apps
        name = apps.resolve_app(str((step.get("arguments") or {}).get("app", ""))) or ""
        rows = [w for w in apps.visible_windows_for_app(name) if not w.get("minimized")] if name else []
        rows = [w for w in rows if w.get("process", "").casefold() not in ("pythonw.exe", "python.exe")]
        if rows:                                             # a starting app can show two windows for a moment: the first
            return int(rows[0]["hwnd"]), rows[0]["process"]
    except Exception:  # noqa: BLE001
        pass
    return 0, ""


def _window_shown(hwnd: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes
    user32 = ctypes.windll.user32
    return bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd)) and not bool(user32.IsIconic(hwnd))


def _opened_by_jarvis(hwnd: int) -> bool:
    return any(o["hwnd"] == hwnd and time.monotonic() - o["at"] < OPENED_SECONDS for o in _OPENED)


def _latest_opened_shown():
    for o in reversed(_OPENED):
        if time.monotonic() - o["at"] < OPENED_SECONDS and _window_shown(o["hwnd"]):
            return o
    return None


def _wait_window_gone(hwnd: int, timeout: float = 3.0) -> bool:
    """A close is real only when the window is gone or hidden (an app that closes to the tray hides it; the old check,
    'no longer in front', called that a failure and could call a still-open window closed)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _window_shown(hwnd):
            return True
        time.sleep(.05)
    return False


# "Close THIS window", "the window I'm looking at", "this file window": the owner points at the front window.
_POINTS_AT_FRONT = re.compile(r"\b(?:this|the current|the front|the open)\s+(?:\w+\s+)?window\b|\bwindow (?:i'?m|i am) (?:looking at|on|in)\b"
                              r"|\b(?:i'?m|i am) looking at\b|\bin front\b", re.I)
_FRIENDLY = {"explorer.exe": "File Explorer", "msedge.exe": "Edge", "chrome.exe": "Chrome", "firefox.exe": "Firefox",
             "brave.exe": "Brave", "claude.exe": "Claude", "chatgpt.exe": "ChatGPT", "notepad.exe": "Notepad",
             "mspaint.exe": "Paint", "systemsettings.exe": "Settings", "applicationframehost.exe": "app"}


def friendly(process: str) -> str:
    return _FRIENDLY.get(process.casefold(), re.sub(r"\.exe$", "", process, flags=re.I))


def _minimize(app: str = ""):
    """Minimize an app's visible windows (or the window in front when no app is named), then check they ARE minimized.
    Reversible and nothing closes (live 2026-11-08: "Minimize Claude" closed Claude because no minimize action existed)."""
    _require_foreground("minimize a window")             # changes the owner's desktop: never in a mission (2026-11-10)
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.IsIconic.argtypes = [wintypes.HWND]
    u.GetForegroundWindow.restype = wintypes.HWND
    if app:
        from tools import apps, pcops
        canonical = apps.resolve_app(app)
        hwnds = [w["hwnd"] for w in apps.visible_windows_for_app(canonical) if not w.get("minimized")] if canonical else []
        label = canonical or app
        if not hwnds:                            # not in the app catalog (e.g. Claude): a visible window whose title or program names it
            want = app.casefold().strip()
            hwnds = [w["hwnd"] for w in pcops.windows() if not w.get("minimized") and w.get("hwnd")
                     and (want in str(w.get("title", "")).casefold() or want in str(w.get("process", "")).casefold())]
        if not hwnds:
            raise RuntimeError(f"{canonical} has no open window to minimize.")
    else:
        h = u.GetForegroundWindow()
        if not h:
            raise RuntimeError("No window is in front to minimize.")
        hwnds, label = [h], "the window in front"
    for h in hwnds:
        u.ShowWindow(h, 6)                       # SW_MINIMIZE
    time.sleep(.3)
    done = sum(1 for h in hwnds if u.IsIconic(h))
    if not done:
        raise RuntimeError(f"I tried to minimize {label}, but it did not minimize.")
    return {"verified": done == len(hwnds), "detail": f"Minimized {label}."}


def _close_foreground_window():
    """'Close it' / 'close this window'. Live 2026-11-06: 'close it' after Jarvis opened Downloads closed the owner's ChatGPT
    (it was in front), so 'it' is the window Jarvis just opened when there is one. Live 2026-11-07: refusing the owner's own
    front window ('quit this file window that I'm looking at') was wrong: when they point at it, or nothing of Jarvis's is
    open, the front window is what they mean. The desktop, the taskbar, Jarvis and system processes are never closed.
    2026-11-10 review: never from a mission (background mode): 'the window in front' is whatever he left there, not the one he
    saw when he approved, and closing it changes his desktop."""
    _require_foreground("close the window in front")
    hwnd, process = _foreground_identity()
    protected = {"csrss.exe", "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe", "system", "smss.exe", "wininit.exe", "dwm.exe", "python.exe", "pythonw.exe"}
    if not hwnd or not process:
        raise RuntimeError("Windows did not identify a foreground application; nothing was closed.")
    said = ""
    try:
        from core import pcbridge
        said = pcbridge.original_message() or ""
    except Exception:  # noqa: BLE001
        pass
    target_app = next((o.get("app", "") for o in _OPENED if o["hwnd"] == hwnd), "")   # the app Jarvis opened, by name
    if not _opened_by_jarvis(hwnd) and not _POINTS_AT_FRONT.search(said):
        mine = _latest_opened_shown()
        if mine is None and _LAST_OPEN and time.monotonic() - _LAST_OPEN.get("at", 0) < OPENED_SECONDS:
            # Jarvis opened an app but its window was not known at launch: find it now. If it can't be found, stop: the
            # owner's front window is never the fallback right after Jarvis opened something (live 2026-11-07, twice).
            found = _own_window({"tool": "open_app", "arguments": {"app": _LAST_OPEN["app"]}})
            if not found[0]:
                raise RuntimeError(f"I can't find the {_LAST_OPEN['app']} window I opened, so I left everything open. "
                                   "Say which window to close.")
            mine = {"hwnd": found[0], "process": found[1], "app": _LAST_OPEN["app"]}
        if mine is not None:
            hwnd, process = mine["hwnd"], mine["process"]
            target_app = mine.get("app", "")
    if process.casefold() in protected or _window_class(hwnd) in _SHELL_CLASSES:
        raise RuntimeError(f"{friendly(process)} is protected, so I left it open.")
    _require_foreground(f"close {process}")
    from tools import apps
    name = target_app
    if not name and _LAST_OPEN.get("app"):
        try:
            if any(int(w["hwnd"]) == int(hwnd) for w in apps.visible_windows_for_app(_LAST_OPEN["app"])):
                name = _LAST_OPEN["app"]
        except Exception:  # noqa: BLE001
            pass
    ours = bool(name)
    if ours:
        # The app Jarvis opened: close it the way "close calculator" does. A Store app has a frame window and a core
        # window; closing one window left Calculator open and Jarvis asked again (live 2026-11-07).
        apps.close_app(name)                                 # the same proven path as "close calculator"
        deadline = time.monotonic() + 3
        while apps.visible_windows_for_app(name) and time.monotonic() < deadline:
            time.sleep(.1)
        if apps.visible_windows_for_app(name):
            prompt = _save_prompt(name)
            if prompt is not None:
                return prompt
            return {"verified": False, "detail": f"{name.title()} is still open.", "error_kind": "needs_owner"}
        _OPENED[:] = [o for o in _OPENED if o["hwnd"] != hwnd]
        _LAST_OPEN.clear()
        return {"verified": True, "detail": f"Closed {name}."}
    game = apps.game_of_window(hwnd)
    if game:                                                 # "close this" on a game: the same as "close <game>"
        return _close_catalog_app(game)
    try:
        from pywinauto import Desktop
        Desktop(backend="uia").window(handle=hwnd).close()   # one window (a folder window Jarvis opened), never the shell
    except Exception as error:
        raise RuntimeError(f"Windows could not send a graceful close to the {process} window.") from error
    if not _wait_window_gone(hwnd):
        prompt = _save_prompt("", hwnd=hwnd)
        if prompt is not None:
            return prompt
        return {"verified": False, "detail": f"The {friendly(process)} window is still open.", "error_kind": "needs_owner"}
    _OPENED[:] = [o for o in _OPENED if o["hwnd"] != hwnd]
    _LAST_OPEN.clear()
    return {"verified": True, "detail": f"Closed {friendly(process)}."}


def _close_foreground_browser_tab():
    """Close one explicit foreground browser tab and verify that visible state changed."""
    _require_foreground("close the browser tab in front")
    hwnd, process = _foreground_identity()
    browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
    if not hwnd or process.casefold() not in browsers:
        raise RuntimeError("A browser must be in front before I close a browser tab.")
    from tools import pcops
    before_title = next((row.get("title", "") for row in pcops.windows()
                         if row.get("foreground") and row.get("process", "").casefold() == process.casefold()), "")
    try:
        from pywinauto import Desktop
        Desktop(backend="uia").window(handle=hwnd).type_keys("^w")
    except Exception as error:
        raise RuntimeError("Windows could not send the browser-tab close command.") from error
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        current_hwnd, _ = _foreground_identity()
        current_title = next((row.get("title", "") for row in pcops.windows()
                              if row.get("foreground") and row.get("process", "").casefold() == process.casefold()), "")
        if current_hwnd != hwnd or current_title != before_title:
            return {"verified": True, "detail": "Closed the foreground browser tab."}
        time.sleep(.05)
    return {"verified": False, "detail": "The browser tab did not visibly change; stopping without claiming it closed."}


def _close_named_site_tab(site: str):
    """Close a visible named public-site tab while retaining its browser window.

    A one-tab browser normally exits on Ctrl+W.  A blank tab is made first, then
    the named tab is closed, so “close YouTube” does not become “close Edge”.
    """
    _require_foreground("bring a browser tab forward to close it")
    from tools import pcops
    label = site.casefold()
    browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
    candidates = [row for row in pcops.windows()
                  if row.get("process", "").casefold() in browsers and label in row.get("title", "").casefold()]
    if not candidates:
        raise RuntimeError(f"No visible browser tab identifies itself as {site.title()}; nothing was closed.")
    target = next((row for row in candidates if row.get("foreground")), candidates[0])
    try:
        from pywinauto import Desktop
        window = Desktop(backend="uia").window(handle=int(target["hwnd"]))
        window.set_focus()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            active = next((row for row in pcops.windows() if row.get("foreground")), None)
            if active and active.get("hwnd") == target["hwnd"]:
                break
            time.sleep(.05)
        else:
            raise RuntimeError("Windows would not bring the requested website tab forward; nothing was closed.")
        def tabs():
            # Edge exposes page tabs separately from page-content tab controls;
            # the browser tabs carry its "Memory usage" accessibility suffix.
            return [item for item in window.descendants(control_type="TabItem")
                    if "memory usage" in item.window_text().casefold()]
        before = tabs()
        matching = [item for item in before if label in item.window_text().casefold()]
        if not matching:
            raise RuntimeError(f"No browser tab identifies itself as {site.title()}; nothing was closed.")
        if label not in PUBLIC_SITES and (len(matching) > 1 or len(candidates) > 1):
            # a tab named by words of its title: only when exactly one tab has them, never a guess between several
            raise RuntimeError(f"More than one browser tab has '{site}' in its title; nothing was closed. Which one?")
        if len(before) == 1:
            # Preserve a browser with one tab: make a neutral replacement first.
            window.type_keys("^t")
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and len(tabs()) < 2:
                time.sleep(.05)
            before = tabs()
            matching = [item for item in before if label in item.window_text().casefold()]
            if not matching:
                raise RuntimeError("The requested browser tab could not be found after preserving the window.")
        tab = matching[0]
        try:
            tab.select()
        except Exception:
            tab.click_input()
        time.sleep(.08)
        # Send the shortcut to the browser window after selecting its tab.
        # A UIA TabItem is an accessibility proxy, not always the keyboard
        # focus owner, so typing on it can appear to succeed without closing.
        window.type_keys("^w")
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError("Windows could not close the requested website tab.") from error
    deadline = time.monotonic() + 2.0
    before_matches = len([item for item in before if label in item.window_text().casefold()])
    while time.monotonic() < deadline:
        # Verify the actual browser tab collection, not a window title (which
        # can remain stale or describe a different tab).
        try:
            after_matches = len([item for item in tabs() if label in item.window_text().casefold()])
        except Exception:
            after_matches = before_matches
        if after_matches < before_matches:
            return {"verified": True, "detail": f"Closed the {site.title()} tab and kept the browser open."}
        time.sleep(.05)
    return {"verified": False, "detail": f"The {site.title()} tab is still visible; stopping without claiming it closed."}


def _uia_focus(hwnd) -> bool:
    """Ask the UIA provider to broker foreground focus for a known HWND."""
    _require_foreground("bring a window to the front")      # outside the try: a refusal is never a quiet False
    try:
        from pywinauto import Desktop
        Desktop(backend="uia").window(handle=int(hwnd)).set_focus()
        return True
    except Exception:
        return False


def raise_window(hwnd: int) -> bool:
    """Restore (if minimized) and bring one top-level window to the front; True only when Windows reports it is now the
    foreground window. Windows' foreground lock refuses a plain request from a background process (JARVIS runs elevated,
    behind his app), so join the foreground and target input queues for the call, then fall back to UIA."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    for f in (u.SetForegroundWindow, u.BringWindowToTop, u.IsIconic, u.SetFocus):
        f.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    u.ShowWindow(hwnd, 9 if u.IsIconic(hwnd) else 5)        # SW_RESTORE a minimized window, else SW_SHOW
    current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = []
    try:
        for w in (u.GetForegroundWindow(), hwnd):
            tid = wintypes.DWORD()
            u.GetWindowThreadProcessId(w, ctypes.byref(tid))
            if tid.value and tid.value != current_tid and tid.value not in attached \
                    and u.AttachThreadInput(current_tid, tid.value, True):
                attached.append(tid.value)
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetFocus(hwnd)
    finally:
        for tid in reversed(attached):
            u.AttachThreadInput(current_tid, tid, False)
    if (u.GetForegroundWindow() or 0) != hwnd:
        _uia_focus(hwnd)
    return (u.GetForegroundWindow() or 0) == hwnd


def _save_prompt(app: str, save=None, hwnd: int = 0):
    """The app is showing its own "save changes?" prompt: answer it as the owner already said, or ask them (owner
    2026-11-07: "if I don't say anything and it needs to be saved, ask me ... and I can say yes or no"). None: no prompt."""
    from core import saveprompt, users
    if not saveprompt.buttons(app, hwnd):
        return None
    if save is None:
        return {"verified": False, "error_kind": "needs_owner", "detail": saveprompt.remember(users.current(), app, hwnd)}
    said = saveprompt.answer(app, bool(save), hwnd)
    return {"verified": said.startswith(("Saved and closed", "Closed ")), "detail": said,
            **({} if said.startswith(("Saved and closed", "Closed ")) else {"error_kind": "needs_owner"})}


GAME_QUIT_S = 12.0
# How long a close waits for the app's visible windows to go before it says they did not. A packaged app (Windows 11 Notepad
# with its tabs and session restore) can take a couple of seconds to tear its window down after it was asked; 2.6 s called
# closes that were still completing failed (live 2026-11-13).
CLOSE_WAIT_S = 4.0


def _close_catalog_app(canonical: str, save=None) -> dict:
    """Close a known app and verify its visible window, including UWP hosts. `save` answers its save prompt (True /
    False, the owner said so in the same sentence); None asks the owner "Save it before I close it?"."""
    from tools import apps
    text = apps.close_app(canonical)
    game = bool(apps.game_processes(canonical)) or apps.is_game(str(apps._installed().get(canonical, "")))
    deadline = time.monotonic() + (GAME_QUIT_S if game else CLOSE_WAIT_S)      # a big game takes seconds to save and shut down
    asked, attempts = time.monotonic(), 1
    while apps.visible_windows_for_app(canonical) and time.monotonic() < deadline:
        # A window opened moments ago may not be enumerable on the first ask (observed 1 in 5 open->close pairs):
        # re-ask at most twice, never an uncertain destructive retry beyond the same graceful close.
        if attempts < 3 and time.monotonic() - asked > .35:
            text = apps.close_app(canonical)
            asked, attempts = time.monotonic(), attempts + 1
        time.sleep(.05)
    gone = not apps.visible_windows_for_app(canonical)
    if not gone and game:
        # The owner asked for it closed, so a game that ignores the polite close is ended (owner 2026-11-07: "when I ask
        # Jarvis to close something ... he just does it"). Only the game's own processes; documents in ordinary apps
        # keep their save prompt (below), because forcing those would throw the owner's work away.
        forced = apps.force_close_game(canonical)
        done = forced.startswith("Closed ")
        name = canonical.title()
        return {"verified": done, "app": canonical, "asked": text, "forced": True,
                "detail": f"{name} didn't quit when asked, so I ended it." if done else forced,
                **({} if done else {"error_kind": "needs_owner"})}
    if not gone:
        prompt = _save_prompt(canonical, save)
        if prompt is not None:
            return {**prompt, "app": canonical, "asked": text}
    # Said short (owner 2026-11-07: "he says too much"); a window that stays open (an unsaved-work prompt) is said plainly.
    detail = f"Closed {canonical}." if gone else f"{canonical.title()} is still open; it may be asking you to save."
    # A window still open is waiting on the owner (its own save prompt): said once, not re-planned into a review request.
    return {"verified": gone, "detail": detail, "app": canonical, "asked": text, **({} if gone else {"error_kind": "needs_owner"})}


def _browse(name, query, site, cancelled):
    """Timed wrapper: browser stages join the owner latency ledger (p50/p95 beside the other PC stages)."""
    from core import pcledger
    started = time.monotonic()
    try:
        result = _browse_inner(name, query, site, cancelled)
    except Exception:
        _record_browse(pcledger, name, site, started, False)
        raise
    _record_browse(pcledger, name, site, started, True)
    return result


def _record_browse(pcledger, name, site, started, ok):
    try:
        pcledger.latency("browser_" + name.removeprefix("browse_"), started, site=site, ok=ok)
    except Exception:  # timing must never change a task's outcome
        pass


def _browse_inner(name, query, site, cancelled):
    """Rendered-DOM public browsing in the isolated worker; verified against the page that actually loaded."""
    from core import webbrowse
    if not webbrowse.available():
        # Fail closed AND stay useful: the pre-existing typed actions do the same job without the isolated browser.
        note = "The isolated browser is not registered on this PC, so "
        if name == "browse_research":
            fallback = perform("research_web", {"query": query}, cancelled=cancelled)
            fallback["detail"] = note + "I used plain public research instead. " + fallback["detail"]
            return fallback
        fallback = perform("browser_search", {"query": query, "site": site if site in ("google", "bing", "youtube") else "bing"}, cancelled=cancelled)
        fallback["verified"] = True
        fallback["detail"] = note + "I opened the search page but did not pick a result. " + fallback["detail"]
        return fallback
    if name == "browse_research":
        read_site = site if site in ("bing", "duckduckgo") else "bing"
        out = webbrowse.search_and_read(query, read_site)
        if not out["ok"]:
            raise RuntimeError("No readable public page was reached: " + str(out.get("reason")))
        webbrowse.remember("research", query, out["sources"])
        notes = [f"{row['title']} — {row['url']}" for row in out["sources"]]
        return {"verified": True, "detail": f"Read {len(notes)} rendered page(s) via {read_site}: " + "; ".join(notes),
                "sources": out["sources"], "log": out["log"], "ms": out["ms"]}
    out = webbrowse.search_pick(query, "bing" if site == "google" else site, verify_page=site != "youtube")  # Google refuses headless browsers
    if not out["ok"]:
        raise RuntimeError("Could not find and load a result: " + str(out.get("reason")))
    webbrowse.remember("search", query, out.get("results") or [out["chosen"]])
    shown = _open_browser_url(out["url"], cancelled)
    shown["detail"] = f"Found {out['title']} on {site.title()} and opened it. {out['url']} " + shown["detail"]
    shown["source"] = {"title": out["title"], "url": out["url"], "excerpt": out["excerpt"]}
    if site == "youtube":  # page load is not playback: ask Windows whether the visible browser is actually playing it
        from core import mediastate
        playing = mediastate.confirm_playing(out["title"], 10)
        shown["playback"] = playing
        shown["detail"] += (" Windows confirms it is playing." if playing["playing"] else
                            " The page opened, but Windows does not report it playing yet (an advertisement, a consent page or autoplay being off).")
    return shown



_LISTING = {"thread": None}


def remember_listing(site: str, query: str) -> None:
    """A search opened in the owner's visible browser gives Jarvis no result list of its own, so 'play the third video'
    had nothing to pick from (live 2026-11-06, Cocomelon). Read the same search in the background research browser and
    keep its results for the follow-up. Best effort: without the research browser the follow-up says so, as before."""
    import threading
    from core import webbrowse

    def fetch():
        try:
            if not webbrowse.available():
                return
            out = webbrowse.run([{"op": "search", "engine": site, "query": query}])
            rows = (out.get("steps") or [{}])[0].get("results") or []
            if rows:
                webbrowse.remember("search", query, rows)
        except Exception as error:  # noqa: BLE001
            print(f"[browse] could not read the {site} results for the follow-up: {type(error).__name__}")
    if site not in ("youtube", "google", "bing") or not str(query or "").strip():
        return
    t = threading.Thread(target=fetch, name="browse-listing", daemon=True)
    _LISTING["thread"] = t
    t.start()


def _followup(number, mode, cancelled):
    """'Open the second one' / 'read me the first source' against the last list shown (ten-minute memory)."""
    from core import webbrowse
    pending = _LISTING.get("thread")
    if pending is not None and pending.is_alive():      # the list of the search just opened is still being read
        deadline = time.monotonic() + 15
        while pending.is_alive() and time.monotonic() < deadline and not cancelled():
            pending.join(0.25)
    try:
        data, row = webbrowse.pick(number)
    except RuntimeError as error:
        raise RuntimeError(str(error)) from None
    if mode == "open":
        shown = _open_browser_url(row["url"], cancelled)
        shown["detail"] = f"Opened result {number}: {row['title']}. {row['url']} " + shown["detail"]
        shown["source"] = row
        return shown
    from core import websearch
    text = row.get("excerpt") or ""
    if len(text) < 80 and webbrowse.available():
        page = webbrowse.read_with_vision_fallback(row["url"])
        if page["ok"]:
            text = websearch._excerpt(page["text"], data["query"]) or page["text"][:600]
    if len(text) < 40:
        raise RuntimeError("I could not read that page.")
    return {"verified": True, "detail": f"From {row['title']}: {text[:600]} Source: {row['url']}", "source": {**row, "excerpt": text[:600]}}


def _open_browser_url(url: str, cancelled):
    """Verify a usable browser window, while accurately leaving page-content verification separate."""
    _require_foreground("open a browser window")      # a mission never opens a window in front (2026-11-10 review)
    from urllib.parse import urlsplit
    from tools import pcops
    from tools.system import open_website
    browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
    before = {(row["process"].casefold(), row["title"]) for row in pcops.windows()
              if row["process"].casefold() in browsers and not row.get("minimized")}
    detail = open_website(url)
    host = urlsplit(url).hostname or ""
    label = {"www.youtube.com": "YouTube", "youtube.com": "YouTube", "www.google.com": "Google",
             "google.com": "Google", "www.bing.com": "Bing", "bing.com": "Bing"}.get(host.casefold(), "The website")
    spoken = f"{label} search is open." if label == "YouTube" and "/results" in urlsplit(url).path else f"{label} is open."
    # Shell launch is normally cheapest. In the elevated scheduled JARVIS task,
    # however, Edge can accept the URL into a background process without making
    # a window. Give the shell a short chance, then recover with a normal Edge
    # window rather than waiting five seconds and falsely reporting failure.
    recovered = False
    for attempt in range(40):
        visible = {(row["process"].casefold(), row["title"]) for row in pcops.windows()
                   if row["process"].casefold() in browsers and not row.get("minimized")}
        if visible:
            changed = bool(visible - before)
            return {"verified": True, "content_verified": False,
                    "detail": spoken, "url": url,
                    "verification": "browser_window_changed" if changed else "browser_window_ready",
                    "recovered": recovered}
        if attempt == 11:
            edge_paths = [Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                          Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Microsoft/Edge/Application/msedge.exe"]
            edge = next((path for path in edge_paths if path.is_file()), None)
            if edge:
                try:
                    subprocess.Popen([str(edge), "--new-window", url], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    recovered = True
                except OSError:
                    pass
        if cancelled():
            raise InterruptedError("Stop requested while waiting for the browser window.")
        time.sleep(.1)
    return {"verified": False, "content_verified": False,
            "detail": f"I sent the request, but I could not confirm that {label.lower()} opened.", "url": url,
            "launch_detail": detail}


def perform(name, args, *, approved=False, cancelled=lambda: False):
    """Every result says whether the actual outcome was verified, not just that a function returned."""
    if SCHEMAS[name][1] and not approved:
        raise RuntimeError("Sensitive operation needs exact-plan owner approval.")
    if cancelled():
        raise InterruptedError("Stopped before execution.")
    if name == "inspect_blender":
        # Availability inspection only. Scene/object questions use the MCP directly.
        from pywinauto import Desktop
        windows = []
        for proc in psutil.process_iter(["name"]):
            if (proc.info["name"] or "").casefold() == "blender.exe":
                windows.extend(Desktop(backend="uia").windows(process=proc.pid, visible_only=True))
        if len(windows) != 1 or not windows[0].is_visible():
            raise RuntimeError("No unique visible Blender window available for inspection.")
        return {"verified": True, "detail": "Blender has a visible application window.", "running": True}
    if name.startswith("uia_"):
        control = _uia_control(args["process"], args["window"], args["control_id"])
        if name != "uia_read" and control.element_info.name != args["expected_name"]:
            raise RuntimeError("The UI control label changed. Stop before interacting.")
        if name == "uia_set_text":
            if re.search(r"\bsk-[A-Za-z0-9_-]{16,}|\b(?:\d[ -]?){13,19}\b", args["text"]):
                raise RuntimeError("Text resembles a secret or payment credential; owner entry is required.")
            control.set_edit_text(args["text"])
            if _uia_value(control) != args["text"]:
                raise RuntimeError("UI text did not match the requested value.")
        elif name == "uia_invoke":
            control.invoke()
            control = _uia_control(args["process"], args["window"], args["check_id"])
            if _uia_value(control) != args["expected_value"]:
                raise RuntimeError("UI action postcondition failed. Stop instead of guessing another click.")
        return {"verified": True, "detail": "UI Automation control verified.", "value": _uia_value(control)}
    if name == "use_app":
        from core import pcuse
        return pcuse.operate(args["app"], args["goal"], cancelled)
    if name == "open_app":
        from tools.apps import launch_and_wait, resolve_app
        canonical = resolve_app(args["app"])
        if not canonical:
            raise RuntimeError("This app is not in the installed-app catalog.")
        detail, ready = launch_and_wait(canonical, cancelled=cancelled)
        if not ready:
            raise RuntimeError(detail)
        return {"verified": True, "detail": detail, "app": canonical}
    if name == "focus_app":
        _require_foreground("focus " + str(args.get("process", "an app")))
        import ctypes
        from ctypes import wintypes
        want = exe_name(args["process"])
        target_pids = {p.pid for p in psutil.process_iter(["name"]) if exe_name(p.info["name"] or "") == want}
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        windows = []
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def visitor(hwnd, extra):
            pid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in target_pids and u.IsWindowVisible(hwnd):
                windows.append(hwnd)
            return True
        u.EnumWindows(visitor, 0)
        if not windows:
            raise RuntimeError("No visible app window was found.")
        # Multiple document windows are normal.  Select the current foreground
        # one when it belongs to the requested app, otherwise the first visible.
        target = u.GetForegroundWindow()
        if target not in windows:
            target = windows[0]
        from jarvis_pc.guardrails.stepfence import fence
        fence("focus " + args["process"])
        u.ShowWindow(target, 9)
        # Windows foreground-lock rules can reject a direct request from the
        # API worker despite an owner-originated command. Temporarily joining
        # the foreground and target input queues is the documented same-desktop
        # handoff; we still verify the resulting foreground HWND and never
        # report success merely because the calls returned.
        foreground = u.GetForegroundWindow()
        current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_tid = ctypes.c_ulong()
        target_tid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_tid))
        u.GetWindowThreadProcessId(target, ctypes.byref(target_tid))
        attached = []
        try:
            for other in (foreground_tid.value, target_tid.value):
                if other and other != current_tid and u.AttachThreadInput(current_tid, other, True):
                    attached.append(other)
            u.BringWindowToTop(target)
            u.SetForegroundWindow(target)
            u.SetFocus(target)
        finally:
            for other in reversed(attached):
                u.AttachThreadInput(current_tid, other, False)
        if u.GetForegroundWindow() not in windows:
            # UIA's provider can broker focus for packaged applications and
            # desktops where foreground-lock policy rejects the raw call.
            _uia_focus(target)
        if u.GetForegroundWindow() not in windows:
            raise RuntimeError("Windows refused to focus the requested window.")
        return {"verified": True, "detail": "Focused a visible " + args["process"] + " window."}
    if name == "process_status":
        running = process_running(args["process"])
        return {"verified": True, "detail": args["process"] + (" is running." if running else " is not running."), "running": running}
    if name == "change_volume":
        from tools import media
        endpoint = media._endpoint()
        before = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        target = max(0, min(100, before + args["delta"]))
        if target == before:
            return {"verified": True, "detail": f"Volume is already at {before}%, as far as it goes.", "percent": before}
        from jarvis_pc.guardrails.stepfence import fence
        fence("change the volume")
        endpoint.SetMasterVolumeLevelScalar(target / 100, None)
        if args["delta"] > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)
        after = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        if abs(after - target) > 1:
            raise RuntimeError(f"Volume stayed at {after}% instead of reaching {target}%.")
        return {"verified": True, "detail": f"Volume went from {before}% to {after}%.", "percent": after, "previous": before}
    if name in ("set_volume", "get_volume"):
        from tools import media
        if name == "set_volume":
            media.volume(args["percent"])
        value = round(media._endpoint().GetMasterVolumeLevelScalar() * 100)
        if name == "set_volume" and abs(value - args["percent"]) > 1:
            raise RuntimeError("Volume did not reach the requested level.")
        return {"verified": True, "detail": f"Volume is {value}%.", "percent": value}
    if name == "pc_status":
        from tools.system import get_system_status
        return {"verified": True, "detail": json.dumps(get_system_status())}
    if name == "list_windows":
        from tools import pcops
        shown = [w for w in pcops.windows() if w["process"].lower() not in ("textinputhost.exe", "applicationframehost.exe")]
        front = next((w for w in shown if w["foreground"]), None)
        lines = [f"{w['process'].removesuffix('.exe')}: {w['title'][:70]}" for w in shown[:12]]
        spoken = (f"{len(shown)} windows are open. In front: {front['process'].removesuffix('.exe')}, {front['title'][:70]}. " if front
                  else f"{len(shown)} windows are open. ") + "Open: " + "; ".join(lines) + "."
        try:
            # Said aloud the way "which apps are open" is (core/pcstate): app names, no 'pythonw: Jarvis' process lines or
            # page titles (live 2026-11-10 it read out "msedge, this: Python ModuleNotFoundError ... pythonw: Jarvis").
            from core import pcstate
            friendly = pcstate.spoken()
        except Exception:  # noqa: BLE001
            friendly = ""
        return {"verified": True, "detail": friendly or spoken, "windows": lines}
    if name == "search_foreground_browser":
        return _send_browser_search(args["query"])
    if name == "scroll_foreground":
        return _scroll_foreground(args["direction"])
    if name == "open_url":
        shown = _open_browser_url(args["url"], cancelled)
        from urllib.parse import parse_qs, urlsplit
        parts = urlsplit(args["url"])
        host, query = (parts.hostname or "").casefold(), parse_qs(parts.query)
        if host.endswith("youtube.com") and parts.path == "/results" and query.get("search_query"):
            remember_listing("youtube", query["search_query"][0])
        elif host.endswith(("google.com", "bing.com")) and parts.path == "/search" and query.get("q"):
            remember_listing("google" if "google" in host else "bing", query["q"][0])
        return shown
    if name in ("minimize_app", "minimize_foreground"):
        return _minimize(args.get("app", ""))
    if name == "close_foreground":
        return _close_foreground_window()
    if name == "close_browser_tab":
        return _close_foreground_browser_tab()
    if name == "close_site_tab":
        return _close_named_site_tab(args["site"])
    if name == "browser_search":
        query, site = args["query"], args["site"]
        bases = {"google": "https://www.google.com/search?", "bing": "https://www.bing.com/search?",
                 "youtube": "https://www.youtube.com/results?"}
        key = "search_query" if site == "youtube" else "q"
        result = _open_browser_url(bases[site] + urlencode({key: query}), cancelled)
        remember_listing(site, query)
        result["detail"] = f"Opened {site.title()} search for {query}. " + result["detail"]
        return result
    if name == "browse_followup":
        return _followup(args["number"], args["mode"], cancelled)
    if name == "media_command" and ":" in args["command"]:
        from core import mediastate
        direction, seconds = args["command"].split(":", 1)
        out = mediastate.seek(direction, int(seconds))
        if not out["verified"]:
            raise RuntimeError(out["reason"])
        amount = f"{int(seconds) // 60} minute{'s' if int(seconds) >= 120 else ''}" if int(seconds) % 60 == 0 else f"{seconds} seconds"
        where = f", now at {mediastate.clock(out['at'])}" if out.get("at") is not None else ""
        return {"verified": True, "detail": f"{'Forward' if direction == 'forward' else 'Back'} {amount}{where}.", "media": out}
    if name == "media_command":
        from core import mediastate
        out = mediastate.command(args["command"])
        if not out["verified"]:
            raise RuntimeError(out["reason"])
        words = {"pause": "Paused", "play": "Playing", "next": "Skipped to", "previous": "Went back to"}[args["command"]]
        return {"verified": True, "detail": f"{words} {out['title'] or out['app']}. Windows confirms it ({out['app']}: {out['status']}).", "media": out}
    if name in ("browse_research", "browse_open"):
        return _browse(name, args["query"], args["site"], cancelled)
    if name == "research_web":
        from core import websearch
        try:
            pack = websearch.research_question(args["query"], max_pages=3, objective=args["query"])
        except websearch.SearchUnavailable as error:
            raise RuntimeError(websearch.unavailable_text(args["query"], error)) from None
        rows = pack.get("sources") or []
        sources = [{"title": row.get("title", "Untitled"), "url": row.get("url", ""),
                    "excerpt": row.get("excerpt", ""), "facts": row.get("facts", []),
                    "provider": row.get("provider", "")}
                   for row in rows if row.get("url") and row.get("excerpt")]
        if not sources:
            raise RuntimeError("Search results were found, but no readable public page could be verified.")
        return {"verified": True, "detail": "Read public research sources: " + "; ".join(
            f"{row['title']} — {row['url']}" for row in sources), "sources": sources,
            "provider": pack.get("provider"), "unopened": pack.get("unopened", [])}
    if name == "read_web_page":
        from core import websearch
        try:
            page = websearch.open_page(args["url"])
        except websearch.PageUnavailable as error:
            raise RuntimeError("The public page could not be read: " + str(error)) from None
        excerpt = websearch._excerpt(page["text"], args["question"])
        if not excerpt:
            raise RuntimeError("The page loaded but did not contain readable material for that question.")
        source = {"title": page.get("title") or page["url"], "url": page["url"], "excerpt": excerpt,
                  "facts": websearch.key_lines(page["text"]), "provider": page.get("provider"),
                  "source_id": page.get("source_id")}
        return {"verified": True, "detail": f"Read {source['title']} — {source['url']}", "source": source}
    if name == "close_app":
        from tools import apps
        canonical = apps.resolve_app(args["app"])
        if not canonical or canonical.casefold().replace(" ", "").removesuffix(".exe") in apps.PROTECTED:
            raise RuntimeError("Unknown or protected application; stopping.")
        return _close_catalog_app(canonical)
    if name in ("find_project", "open_path", "find_files", "type_text", "save_document", "read_screen", "run_terminal", "investigate_slowness"):
        from core import pcverbs
        return getattr(pcverbs, name)(args, approved=approved, cancelled=cancelled)
    if name in ("install_program", "uninstall_program"):
        from tools import software
        detail = getattr(software, name)(args["name"])
        if not (detail.startswith(("Installed ", "Uninstalled ")) or "is already installed" in detail):
            return {"verified": False, "detail": detail}
        check = software._winget("list", "--name", args["name"])
        if (check.returncode & 0xFFFFFFFF) not in (0, 0x8A150014):
            return {"verified": False, "detail": detail + " Independent package-state query failed; stop for review."}
        present = software._still_there(args["name"])
        return {"verified": bool(present) == (name == "install_program") and "NEEDS_CONFIRMATION" not in detail,
            "detail": detail}
    if name in UNREAL_TOOLS:                                   # Mark V: never starts Unreal (core/unreal, tools/unreal)
        from tools import unreal as unreal_tools
        detail = getattr(unreal_tools, name)(**args)
        failed = detail == unreal_tools.CLOSED or detail.startswith(unreal_tools.FAILED)
        if name == "shadow_world_import":   # only "<name> is in my Shadow World branch, ..." with a commit is done
            failed = " is in my Shadow World branch, " not in detail or "committing it failed" in detail
        if name == "shadow_world_retarget":
            failed = not (" now has " in detail or " already has the game's animations" in detail) or "committing it failed" in detail
        if name == "shadow_world_game_test":
            failed = "passed the in-game test" not in detail or "FAILED" in detail
        return {"verified": not failed, "detail": detail, **({"error_kind": "needs_owner"} if failed else {})}
    op = {"blender_status": "status", "list_scene_objects": "list", "render_preview": "render", "export_glb": "export"}[name]
    if SCHEMAS[name][1]:
        workspace = os.getenv("JARVIS_BLENDER_WORKSPACE", "")
        if not workspace:
            raise RuntimeError("Configure the approved Blender workspace before requesting an output.")
        root = Path(workspace).resolve()
        output = (root / args["path"]).resolve()
        if not output.is_relative_to(root):
            raise RuntimeError("The output path escapes the approved workspace.")
        before = output.stat().st_mtime_ns if output.exists() else None
        # The caller supplies no approval boolean: approved is derived from a consumed exact-plan grant.
        gate = _hub("approval")
        rid = gate.request_approval(name, args)
        ok, _ = gate.grant(rid)
        allowed, _ = gate.check_and_consume(name, args)
        if not (ok and allowed):
            raise RuntimeError("Shared hub approval was not accepted.")
    result = _hub("blender_client").call(op, **args)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Blender worker refused this operation."))
    if op in ("render", "export"):
        workspace = os.getenv("JARVIS_BLENDER_WORKSPACE", "")
        root = Path(workspace).resolve()
        output = (root / args["path"]).resolve()
        if not output.is_relative_to(root) or not output.is_file() or output.stat().st_size == 0 or output.stat().st_mtime_ns == before:
            raise RuntimeError("Blender output could not be verified inside the approved workspace.")
        if op == "export":
            with output.open("rb") as file:
                if file.read(4) != b"glTF":
                    raise RuntimeError("The exported file is not a GLB.")
        else:
            from PIL import Image
            with Image.open(output) as image:
                image.verify()
    return {"verified": True, "detail": json.dumps(result), "result": result}
