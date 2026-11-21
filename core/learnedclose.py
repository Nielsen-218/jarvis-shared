"""The run-time safety wrapper for a LEARNED close_foreground method (host code: the learner never writes or sees any of this).

A learned method for "close this window" / "close the window in front" has passed practice, the sealed exam AND the production
check in the lab (core/skillcontracts.production_check_desktop). On the owner's real desktop it still runs only inside this
wrapper, which owns every decision that could hurt:

  1. TARGET. The window is the true foreground top-level window at the moment of the request, resolved here, never by the method.
  2. REFUSALS (nothing is touched; the built-in method decides instead): Jarvis's own windows, the desktop / taskbar / Start and
     other shell surfaces, the Command Center, a lock screen or permission prompt (the input desktop is not the normal one), a
     full-screen window, anything at all while a game is running (acquire.running_game), a game's window, a system process, and
     the case where the built-in method would have meant a window Jarvis itself just opened.
  3. SCOPE. The method gets a ScopedUI: it sees ONLY the target window, may press only a button named Close, cannot type or
     select. So it cannot reach any other window, and it cannot answer a save prompt (Save / Don't Save / Yes ...).
  4. VERIFIER. Afterwards the existing close verifier (pcactions._wait_window_gone, saveprompt.buttons) must show the window gone,
     or the app's own save prompt on screen. A save prompt is not a failure: Jarvis says the app is asking about unsaved changes
     and stops (saveprompt remembers the question). Anything else is "not done".
  5. NOT DONE / ERROR. The miss is recorded (core/misses) and the built-in close runs ONCE, only while the same window is still in
     front. The learned method is never run a second time. A success is counted as a use (core/independence.log_use, source owner
     on a real request, lab in the lab); a failure is not.
"""
import os
import re
import time

from core import pcactions

RUN_SECONDS = 5.0                      # the whole method; the step's own ceiling in core.pcmethods is longer (20 s) but a close is short
CLOSE_NAMES = ("close", "close window")
CLOSE_ROLES = ("Button",)              # the title-bar Close only: never a File > Close menu item (that closes a document or a tab)
_JARVIS_TITLE = re.compile(r"\bjarvis\b|command center|jarvis companion", re.I)
_OWN_PROCESSES = {"python.exe", "pythonw.exe", "jarvis.exe"}
_SYSTEM_PROCESSES = {"csrss.exe", "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe", "system", "smss.exe", "wininit.exe", "dwm.exe",
                     "lockapp.exe", "logonui.exe", "consent.exe", "credentialuibroker.exe", "securityhealthsystray.exe", "fontdrvhost.exe"}
_SHELL_PROCESSES = {"searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "textinputhost.exe",
                    "searchapp.exe", "widgets.exe", "widgetservice.exe"}
_SHELL_CLASSES = pcactions._SHELL_CLASSES | {"windows.ui.core.corewindow", "foregroundstaging", "xamlexplorerhostislandwindow",
                                             "toplevelwindowforoverflowxamlisland"}
LAB_CLASS = "JarvisLabTarget"          # core.labtarget.CLASS: in the lab the practice target (python.exe) is the window under test


class Refused(RuntimeError):
    """The wrapper declined BEFORE touching anything: not the method's failure, and the built-in method decides."""


class NotDone(RuntimeError):
    """The method ran and the window is still in front with no save prompt: the built-in close may run once."""


class NoFallback(RuntimeError):
    """The method ran and the outcome is unknown or the front window changed: nothing else is tried (it could hit another window)."""


class Stale(NoFallback):
    """What the wrapper resolved is no longer true at the moment of the press (another window is in front, a game started, the lock
    screen appeared ...). Nothing was pressed. Not the method's failure, and nothing else is tried: the owner's request was about a
    window that is no longer the one in front."""


class ScopeError(RuntimeError):
    """The method asked for something a close method may not do (reach another window, type, select, press anything but Close)."""


def in_lab() -> bool:
    return os.getenv("JARVIS_SKILL_LAB") == "1"


# ------------------------------------------------------------------------------------------------ the real system probes (injectable)

class Env:
    """Every question the wrapper asks the machine. Tests replace the methods; production uses these."""

    def require_foreground(self):
        pcactions._require_foreground("close the window in front")        # never from a mission (background mode)

    def foreground(self) -> dict | None:
        """The true foreground TOP-LEVEL window: {"hwnd","pid","process","cls","title"} or None."""
        if os.name != "nt":
            return None
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetAncestor.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        h = u.GetForegroundWindow()
        if in_lab():                          # a hidden desktop has no foreground window: the top of the Z order is "in front" there
            h = self._lab_front() or h
        if not h:
            return None
        root = u.GetAncestor(h, 2) or h       # GA_ROOT: an owned dialog or child is never mistaken for the app window
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(root, ctypes.byref(pid))
        try:
            import psutil
            process = psutil.Process(pid.value).name()
        except Exception:  # noqa: BLE001
            process = ""
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(root, buf, 512)
        return {"hwnd": int(root), "pid": int(pid.value), "process": process, "cls": pcactions._window_class(int(root)), "title": buf.value}

    @staticmethod
    def _lab_front():
        import win32gui
        found = []
        win32gui.EnumWindows(lambda h, _: found.append(h) or True, None)      # top-to-bottom Z order
        return next((h for h in found if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == LAB_CLASS), 0)

    def front_hwnd(self) -> int:
        t = self.foreground()
        return int(t["hwnd"]) if t else 0

    def input_desktop_ok(self) -> bool:
        """False on the lock screen, a UAC prompt or any secure desktop: the input desktop is then not the normal "Default"."""
        if os.name != "nt":
            return False
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.OpenInputDesktop.restype = wintypes.HANDLE
        desk = u.OpenInputDesktop(0, False, 0x0001)
        if not desk:
            return False
        try:
            buf = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            ok = u.GetUserObjectInformationW(desk, 2, buf, 512, ctypes.byref(needed))
            return bool(ok) and buf.value.casefold() == "default"
        finally:
            u.CloseDesktop(desk)

    def game(self) -> str:
        from core import acquire
        return acquire.running_game()

    def fullscreen(self, hwnd) -> bool:
        """A window with no caption that covers its whole monitor (a borderless full-screen game or video)."""
        try:
            import win32api
            import win32con
            import win32gui
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))["Monitor"]
            captionless = not (win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_CAPTION == win32con.WS_CAPTION)
            return captionless and left <= mon[0] and top <= mon[1] and right >= mon[2] and bottom >= mon[3]
        except Exception:  # noqa: BLE001 - unreadable: treated as full screen (the safe answer)
            return True

    def is_game_window(self, hwnd) -> bool:
        try:
            from tools import apps
            return bool(apps.game_of_window(hwnd))
        except Exception:  # noqa: BLE001
            return False

    def own_pids(self) -> set:
        pids = {os.getpid()}
        try:
            import psutil
            me = psutil.Process()
            pids.update(c.pid for c in me.children(recursive=True))
            pids.update(p.pid for p in me.parents()[:3])
        except Exception:  # noqa: BLE001
            pass
        return pids

    def jarvis_opened_recently(self) -> bool:
        try:
            return pcactions._latest_opened_shown() is not None or bool(
                pcactions._LAST_OPEN and time.monotonic() - pcactions._LAST_OPEN.get("at", 0) < pcactions.OPENED_SECONDS)
        except Exception:  # noqa: BLE001
            return False

    def owner_pointed(self) -> bool:
        try:
            from core import pcbridge
            return bool(pcactions._POINTS_AT_FRONT.search(pcbridge.original_message() or ""))
        except Exception:  # noqa: BLE001
            return False

    def gone(self, hwnd) -> bool:
        return pcactions._wait_window_gone(hwnd, pcactions.CLOSE_WAIT_S)

    def shown(self, hwnd) -> bool:
        return pcactions._window_shown(hwnd)

    def save_prompt(self, hwnd) -> dict | None:
        """The existing close verifier's answer: the app's own save prompt is showing -> the question for the owner, else None."""
        return pcactions._save_prompt("", hwnd=hwnd)

    def ui(self):
        from core import methodlang
        try:
            from jarvis_pc.uiact import uia
            uia.ensure_mta()
        except Exception:  # noqa: BLE001
            pass
        return methodlang.UIA()


# ------------------------------------------------------------------------------------------------ refusals

def refusal(t, env) -> str:
    """'' when this window may be closed by a learned method; otherwise the reason it may not (the built-in method decides)."""
    if not t or not t.get("hwnd"):
        return "no window is in front"
    lab_target = in_lab() and t.get("cls") == LAB_CLASS.casefold()
    process, cls, title = (t.get("process") or "").casefold(), (t.get("cls") or "").casefold(), t.get("title") or ""
    if not env.input_desktop_ok():
        return "the lock screen or a permission prompt is showing"
    game = env.game()
    if game:
        return f"a game is running ({game})"
    if not process:
        return "Windows did not say which program owns the window in front"
    if cls in _SHELL_CLASSES or process in _SHELL_PROCESSES:
        return "the desktop or the taskbar is in front"
    if process in _SYSTEM_PROCESSES:
        return f"{process} is a system program"
    if not lab_target and (process in _OWN_PROCESSES or int(t.get("pid") or 0) in env.own_pids()):
        return "that is one of my own windows"
    if not lab_target and _JARVIS_TITLE.search(title):
        return "that is my own window or the Command Center"
    if not title.strip():
        return "the window in front has no title"
    if env.fullscreen(t["hwnd"]):
        return "a full-screen window is in front"
    if env.is_game_window(t["hwnd"]):
        return "the window in front is a game"
    if env.jarvis_opened_recently() and not env.owner_pointed():
        return "I just opened something, so the built-in method decides which window 'it' means"
    return ""


# ------------------------------------------------------------------------------------------------ the scoped ui a method gets

class ScopedUI:
    """The `ui` object of a learned close method: the UI Automation primitives, narrowed to ONE window.
    windows() lists only the target; find/children reach only below it; invoke presses only a button named Close; select and
    set_value are refused. A method therefore cannot touch another window, and cannot press Save / Don't Save / Yes on a prompt."""

    def __init__(self, ui, target_hwnd, hwnd_of=None, guard=None):
        self._ui, self._hwnd, self._allowed = ui, int(target_hwnd), set()
        self._hwnd_of = hwnd_of or getattr(ui, "hwnd_of", None)
        self._guard = guard                  # called immediately before a press: raises if the target / the refusal conditions changed
        self.pressed = []
        self.stale = ""

    @staticmethod
    def _id(x):
        return int(x["id"] if isinstance(x, dict) else x)

    def _need(self, x):
        eid = self._id(x)
        if eid not in self._allowed:
            raise ScopeError("that element is not part of the window being closed")
        return eid

    def windows(self):
        rows = []
        for r in self._ui.windows():
            try:
                mine = self._hwnd_of is not None and int(self._hwnd_of(r["id"])) == self._hwnd
            except Exception:  # noqa: BLE001
                mine = False
            if mine:
                self._allowed.add(int(r["id"]))
                rows.append(r)
        return rows[:1]

    def _take(self, rows):
        for r in rows:
            self._allowed.add(int(r["id"]))
        return rows

    def find(self, within_id=None, role="", name="", contains="", within=None):
        return self._take(self._ui.find(self._need(within_id if within_id is not None else within), role=role, name=name, contains=contains))

    def children(self, eid=None, id=None):
        return self._take(self._ui.children(self._need(eid if eid is not None else id)))

    def parent(self, eid=None, id=None):
        p = self._ui.parent(self._need(eid if eid is not None else id))
        return p if p and int(p["id"]) in self._allowed else None

    def info(self, eid=None, id=None):
        return self._ui.info(self._need(eid if eid is not None else id))

    def value(self, eid=None, id=None):
        return self._ui.value(self._need(eid if eid is not None else id))

    def set_value(self, eid=None, text="", id=None):
        raise ScopeError("a close method does not type")

    def select(self, eid=None, id=None):
        raise ScopeError("a close method does not select")

    def _is_title_bar_close(self, e) -> bool:
        """The button sits directly under a TitleBar element that belongs to the target window itself (a Win32 title bar). An in-page
        Close, a Close inside an owned dialog or pane, and anything under another element's title bar are not it."""
        bar = self._ui.parent(e)
        if not bar or (bar.get("role") or "") != "TitleBar":
            return False
        owner = self._ui.parent(int(bar["id"]))
        try:
            return bool(owner) and self._hwnd_of is not None and int(self._hwnd_of(owner["id"])) == self._hwnd
        except Exception:  # noqa: BLE001
            return False

    def invoke(self, eid=None, id=None):
        e = self._need(eid if eid is not None else id)
        info = self._ui.info(e)
        name = (info.get("name") or "").strip().casefold()
        if (info.get("role") or "") not in CLOSE_ROLES or name not in CLOSE_NAMES:
            raise ScopeError(f"a close method may only press Close, not {info.get('name')!r}")
        if not self._is_title_bar_close(e):
            raise ScopeError("a close method may only press the window's own title-bar Close")
        if self._guard is not None:
            try:
                self._guard()                # the last look: still in front, no game, normal desktop - checked at the moment of the press
            except Exception as error:  # noqa: BLE001 - anything unclear is a stop
                self.stale = " ".join(str(error).split())[:160] or type(error).__name__
                raise ScopeError("the situation changed before the press: " + self.stale) from error
        self.pressed.append(info.get("name") or "")
        return self._ui.invoke(e)

    def wait(self, seconds=0.5):
        return self._ui.wait(seconds)


def run_method(source, target_hwnd, ui, seconds=RUN_SECONDS, guard=None) -> dict:
    """The learned source against `ui` narrowed to the target window. -> methodlang.run's result plus "pressed" (and "stale": why the
    guard stopped the press, '' normally)."""
    from core import methodlang
    scoped = ScopedUI(ui, target_hwnd, guard=guard)
    out = methodlang.run(source, scoped, {}, seconds=seconds)
    out["pressed"] = list(scoped.pressed)
    out["stale"] = scoped.stale
    return out


def _guard(env, hwnd):
    """The press-time re-check: the same refusal conditions as at the start, and the same window still in front."""
    def check():
        env.require_foreground()
        target = env.foreground()
        if not target or int(target.get("hwnd") or 0) != hwnd or env.front_hwnd() != hwnd:
            raise Stale("another window is in front now")
        why = refusal(target, env)
        if why:
            raise Stale(why)
    return check


# ------------------------------------------------------------------------------------------------ the run

def run_learned(pid, man, src, step, env=None) -> dict:
    """Run an approved learned close_foreground method on THIS desktop under every rule above.
    -> the step outcome {"verified", "detail", ...}; raises Refused / NotDone / NoFallback (core.pcmethods turns those into
    'built-in method next' / 'built-in method next, once' / 'stop')."""
    env = env or Env()
    env.require_foreground()
    target = env.foreground()
    why = refusal(target, env)
    if why:
        raise Refused(why)
    hwnd = int(target["hwnd"])
    started = time.monotonic()
    try:
        return _run_and_verify(pid, man, src, step, env, target, hwnd, started)
    except (Refused, NotDone, NoFallback):
        raise
    except Exception as error:  # noqa: BLE001 - anything unexpected after the method may have pressed Close: never guess
        # The press may already have happened and the window may be gone, so the built-in close (which acts on whatever is in
        # front NOW) could hit another window. After an unexpected error nothing else is tried.
        reason = f"{type(error).__name__} after the close"
        _miss(f"learned close_foreground: {reason}")
        raise NoFallback(reason) from error


def _run_and_verify(pid, man, src, step, env, target, hwnd, started) -> dict:
    name = pcactions.friendly(target["process"])
    try:
        result = run_method(src, hwnd, env.ui(), guard=_guard(env, hwnd))
    except Exception as error:  # noqa: BLE001 - UIA itself failed: the same as a method that errored
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"[:300], "error_type": type(error).__name__, "pressed": [], "calls": 0}
    if result.get("stale"):
        raise Stale(result["stale"])                                    # nothing was pressed; no blame, no fallback
    version = man.get("version")
    base = {"learned_method": f"{pid} v{version}"}
    if env.gone(hwnd):                                                  # the existing verifier: the window is gone
        _log(pid, man, step, True, result, "closed")
        _count_use(pid, True)
        return {"verified": True, "detail": f"Closed {name}.", **base, "seconds": round(time.monotonic() - started, 2)}
    prompt = env.save_prompt(hwnd)                                      # ... or the app is asking about unsaved changes
    if prompt is not None:
        _log(pid, man, step, False, result, "save prompt showing")
        return {**prompt, **base, "prompt_confirmed": True}             # not a failure of the method, and not a finished close
    _log(pid, man, step, False, result, "window still open")
    reason = "the window is still open" + (f" ({result.get('error')})" if result.get("error") else "")
    _miss(f"learned close_foreground: {reason}")
    if not result.get("pressed") and env.shown(hwnd) and env.front_hwnd() == hwnd:
        raise NotDone(reason)             # nothing was pressed: the built-in close is safe on this very window
    raise NoFallback(reason)              # a Close press went out (it may still be tearing down): never close anything else


def _log(pid, man, step, passed, result, outcome):
    """The learned-method use log (data/learned-methods/uses.jsonl): every run, whatever it ended in."""
    try:
        from core import acquire, independence, procedures
        procedures.Library(acquire._root()).log(id=pid, version=man.get("version"), tool=step["tool"], args=step.get("arguments") or {},
                                                passed=bool(passed), checks={"outcome": outcome, "pressed": result.get("pressed", [])},
                                                error=result.get("error", ""), source=independence.asker(), teacher=False, outside_calls=0)
    except Exception:  # noqa: BLE001 - a log problem never breaks the owner's step
        pass


def _count_use(pid, passed):
    """core/independence's "used on a real request" counter: ONE row per SUCCESSFUL run; a failed run is not a use."""
    if passed:
        from core import independence
        independence.log_use(pid, "method", True, tool="close_foreground")      # source: owner on a real request, lab in the lab


def _miss(reason):
    try:
        from core import misses
        misses.fail(reason, route="learned_close")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------ lab entry points (hidden desktop only)

def run_in_lab(source, params) -> dict:
    """The production check's action: the target and refusals as in production, then the method narrowed to the target window.
    The judge (core/skillcontracts.production_check_desktop) reads what really happened; there is no verifier to trust here."""
    if not in_lab():
        raise RuntimeError("lab only")
    env = Env()
    target = env.foreground()
    why = refusal(target, env)
    if why:
        return {"ok": False, "message": "", "error": "Refused: " + why, "error_type": "Refused", "calls": 0, "trace": []}
    return run_method(source, target["hwnd"], env.ui())


def runtime_in_lab(params) -> dict:
    """The whole run-time path on a lab-spawned window: pcmethods.execute -> ranking -> wrapper -> verifier -> ledger -> log_use."""
    if not in_lab():
        raise RuntimeError("lab only")
    from core import pcmethods
    out = pcmethods.execute({"tool": "close_foreground", "arguments": {}}, approved=True)
    stopped = bool(out.get("verified")) or bool(out.get("prompt_confirmed"))      # a save prompt left open is the right ending
    return {"ok": stopped, "message": str(out.get("detail", ""))[:300], "error": "" if stopped else str(out.get("detail", ""))[:300],
            "method": out.get("method", ""), "attempts": out.get("attempts", []), "trace": []}
