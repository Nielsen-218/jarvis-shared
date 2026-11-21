"""Gaming boost: before a game, the PC's own background work steps behind it; after the game, everything is put back.

Measured on the owner's PC 2026-11-20 (Core Ultra 7 265KF, 32 GB, RTX 5070 Ti, 1080p at 240 Hz): idle launchers cost
little (Epic 145 MB and 3.5% of one core, OneDrive 59 MB). What competes with a game here is his own heavy work running
at the same time: two full test runs took about 180% of a core, and Unreal builds, shader compiles and coding agents
take more. So, run by the Gaming protocol's first step ("free_up"):

  1. Known heavy work (test runs, C++ and Unreal builds, shader compiles, Blender, coding agents) is set to "below
     normal" priority while the game runs. Nothing is stopped or paused: the game simply wins the processor, and the
     work keeps going. Its old priority comes back afterwards, but only if it is still the one set here.
  2. Idle launchers with no window open (Epic Games Launcher, Riot Client, Grammarly) are closed, and OneDrive is asked
     to stop syncing (its own /shutdown). Each comes back after the game with the exact command Windows starts it with
     at sign-in (its Run entry), opened through a shortcut by Explorer, so it runs with his normal rights even though
     Jarvis itself runs as administrator.
  3. Never touched: the game, Steam (Apex runs through it), Discord, browsers, any app with a window open, security
     software, and device helpers (Razer, Logitech, Realtek). A launcher is left alone when it cannot be started again,
     when it is running anything besides its own helpers (a game or an editor it started), or when it is busy reading
     or writing (a download or an install).

What was changed is written to a file in the PC's own state folder (not the synced data folder) BEFORE each change, so
a crash or a Jarvis restart in the middle still puts everything back once the game ends. Put back: 30 s after the game
has closed, after RESTORE_IF_NO_GAME_S if no game ever started, or at once on "stop the gaming protocol". After a
reboot nothing is restarted (Windows already started them at sign-in).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import psutil

BELOW_NORMAL = getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 10)
IDLE = getattr(psutil, "IDLE_PRIORITY_CLASS", 19)
RESTORE_IF_NO_GAME_S = 600        # the protocol asked for a game that never started: give everything back after 10 min
NO_GAME_TICKS = 6                 # the game must be gone for 6 checks in a row (30 s): a crash and relaunch is not the end
SWEEP_EVERY_S = 30                # while a game runs, heavy work started since is put behind it too
BUSY_BYTES_PER_S = 1_000_000      # a launcher reading/writing more than this (a download, an install) is left alone
BUSY_WAIT_S = 120                 # a stop said while the background is still being cleared waits for that to finish
STATE_NAME = "gameboost.json"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Heavy work by process name: what it is, in his words.
HEAVY = {"cl.exe": "C++ compiling", "link.exe": "C++ linking", "msbuild.exe": "a Visual Studio build",
         "unrealbuildtool.exe": "an Unreal build", "shadercompileworker.exe": "Unreal shader compiling",
         "unrealeditor.exe": "the Unreal editor", "unrealeditor-cmd.exe": "an Unreal command-line job",
         "blender.exe": "Blender", "claude.exe": "Claude coding agents", "codex.exe": "Codex coding agents",
         "pytest.exe": "a test run"}
PYTHON = {"python.exe", "pythonw.exe"}
PYTHON_JOBS = (("scripts.gate", "a full test run"), ("gate.py", "a full test run"), ("pytest", "a test run"))

# Launchers that idle in the tray. `run` is the name of the value under HKCU\...\CurrentVersion\Run that Windows starts
# it with at sign-in; `how` is "pause" for OneDrive (its own clean shutdown) and "close" for the rest; `helpers` are the
# only child processes it may have for it to count as idle (anything else, like a game it started, keeps it open).
LAUNCHERS = (
    {"name": "OneDrive", "exe": "onedrive.exe", "run": "OneDrive", "how": "pause",
     "helpers": {"onedrive.exe", "filecoauth.exe", "onedrive.sync.service.exe", "microsoft.sharepoint.exe"}},
    {"name": "Epic Games Launcher", "exe": "epicgameslauncher.exe", "run": "EpicGamesLauncher", "how": "close",
     "helpers": {"epicwebhelper.exe", "epiconlineservicesuserhelper.exe", "eosoverlayrenderer-win64-shipping.exe",
                 "unrealcefsubprocess.exe", "crashpad_handler.exe"}},
    {"name": "Riot Client", "exe": "riotclientservices.exe", "run": "RiotClient", "how": "close",
     "helpers": {"riot client.exe", "riotclientux.exe", "riotclientuxrender.exe", "riotclientcrashhandler.exe"}},
    {"name": "Grammarly", "exe": "grammarly.desktop.exe", "run": "Grammarly", "how": "close",
     "helpers": {"grammarly.desktop.exe", "msedgewebview2.exe"}},
)

_lock = threading.RLock()         # the state file: held only for a read-modify-write, never across a slow close
_busy = threading.Lock()          # one free_up at a time
_last_sweep = 0.0
_no_game = 0                      # consecutive checks without the game, once one was seen
_not_heavy: set = set()           # (pid, created) of python processes already read and found not to be heavy work


class _System:
    """Everything that touches the real PC, in one place (tests/conftest.py replaces it)."""

    def processes(self) -> list[dict]:
        out = []
        for p in psutil.process_iter(["pid", "name", "create_time"]):
            info = p.info
            out.append({"pid": info["pid"], "name": (info.get("name") or "").lower(), "created": info.get("create_time") or 0.0})
        return out

    def _same(self, pid: int, created: float):
        """The process, only if it is still the one recorded (a reused process id is someone else)."""
        try:
            p = psutil.Process(pid)
            return p if abs(p.create_time() - created) < 1.0 else None
        except (psutil.Error, OSError):
            return None

    def cmdline(self, pid: int) -> str:
        try:
            return " ".join(psutil.Process(pid).cmdline())
        except (psutil.Error, OSError):
            return ""

    def priority(self, pid: int, created: float):
        p = self._same(pid, created)
        try:
            return int(p.nice()) if p else None
        except (psutil.Error, OSError):
            return None

    def set_priority(self, pid: int, created: float, value) -> bool:
        p = self._same(pid, created)
        if p is None:
            return False
        try:
            p.nice(int(value))
            return True
        except (psutil.Error, OSError, ValueError):
            return False

    def exe(self, pid: int) -> str:
        try:
            return psutil.Process(pid).exe()
        except (psutil.Error, OSError):
            return ""

    def descendants(self, pid: int) -> set[str]:
        """Names of every process this one started (children of children too)."""
        try:
            return {(c.name() or "").lower() for c in psutil.Process(pid).children(recursive=True)}
        except (psutil.Error, OSError):
            return {"?"}                # can't tell: a name no launcher allows, so it is left alone

    def busy(self, pids: list[int]) -> bool:
        """Reading or writing a lot right now (a download or an install), measured over one second."""
        def total():
            n = 0
            for pid in pids:
                try:
                    p = psutil.Process(pid)
                    for q in [p] + p.children(recursive=True):
                        io = q.io_counters()
                        n += io.read_bytes + io.write_bytes
                except (psutil.Error, OSError, AttributeError):
                    pass
            return n
        first = total()
        time.sleep(1.0)
        return total() - first > BUSY_BYTES_PER_S

    def has_window(self, pids: list[int]) -> bool:
        """Does any of these processes (or a child of one) show a visible, titled top-level window?"""
        try:
            import win32gui
            import win32process
        except ImportError:
            return True                 # can't tell: treat it as in use, so it is left alone
        family = set(pids)
        for pid in pids:
            try:
                family |= {c.pid for c in psutil.Process(pid).children(recursive=True)}
            except (psutil.Error, OSError):
                pass
        found = []

        def each(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip():
                if win32process.GetWindowThreadProcessId(hwnd)[1] in family:
                    found.append(hwnd)
            return True
        try:
            win32gui.EnumWindows(each, None)
        except Exception:  # noqa: BLE001
            return True
        return bool(found)

    def end(self, pid: int, created: float) -> bool:
        """Close a windowless launcher and its helpers: first the polite way, then end them (no document is open)."""
        p = self._same(pid, created)
        if p is None:
            return True
        try:
            family = [p] + p.children(recursive=True)
        except (psutil.Error, OSError):
            family = [p]
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        except (subprocess.SubprocessError, OSError):
            pass
        _, alive = psutil.wait_procs(family, timeout=4)
        for q in alive:
            try:
                q.terminate()
            except (psutil.Error, OSError):
                pass
        _, alive = psutil.wait_procs(alive, timeout=4)
        return not alive

    def pause_onedrive(self, exe: str, pids: list[int]) -> bool:
        """OneDrive's own clean stop (sync resumes when it starts again)."""
        try:
            subprocess.run([exe, "/shutdown"], capture_output=True, timeout=15, creationflags=_NO_WINDOW)
        except (subprocess.SubprocessError, OSError):
            return False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not any(psutil.pid_exists(p) for p in pids):
                return True
            time.sleep(0.5)
        return False

    def run_command(self, value: str) -> str:
        """The command Windows starts this app with at sign-in, from HKCU\\...\\CurrentVersion\\Run ('' if none),
        with %VARIABLES% expanded (a REG_EXPAND_SZ value is read unexpanded)."""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                return os.path.expandvars(str(winreg.QueryValueEx(key, value)[0] or ""))
        except OSError:
            return ""

    def is_file(self, path: str) -> bool:
        return bool(path) and os.path.isfile(path)

    def boot_time(self) -> float:
        return psutil.boot_time()

    def running(self, exe_name: str) -> bool:
        return any(p["name"] == exe_name for p in self.processes())

    def launch(self, command: str, label: str) -> bool:
        """Start a command with his normal rights: a shortcut holding the program and its arguments, opened by Explorer
        (Explorer runs as the signed-in user, so what it opens does too, even though Jarvis runs as administrator)."""
        target, args = split_command(command)
        if not self.is_file(target):
            return False
        try:
            import pythoncom
            import win32com.client
            try:
                pythoncom.CoInitialize()
            except Exception:  # noqa: BLE001 - already initialized on this thread, in another mode: COM is usable
                pass
            folder = _state_path().parent / "gameboost-shortcuts"
            folder.mkdir(parents=True, exist_ok=True)
            link = folder / ("".join(c for c in label if c.isalnum()) + ".lnk")
            shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(link))
            shortcut.TargetPath = target
            shortcut.Arguments = args
            shortcut.WorkingDirectory = str(Path(target).parent)
            shortcut.Save()
            subprocess.Popen(["explorer.exe", str(link)], creationflags=_NO_WINDOW)
            return True
        except Exception:  # noqa: BLE001
            return False


OS = _System()


def split_command(command: str) -> tuple[str, str]:
    """'"C:\\x\\a.exe" -silent' -> ('C:\\x\\a.exe', '-silent'); 'C:\\Riot Games\\b.exe --x' -> ('C:\\Riot Games\\b.exe', '--x')."""
    text = (command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            return text.strip('"'), ""
        return text[1:end], text[end + 1:].strip()
    cut = text.lower().find(".exe")
    if cut < 0:
        return text, ""
    return text[:cut + 4], text[cut + 4:].strip()


def _state_path() -> Path:
    from core import pcledger
    return pcledger.state_dir() / STATE_NAME


def _load() -> dict | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and (state.get("lowered") or state.get("closed")) else None


def _save(state: dict) -> None:
    if not (state.get("lowered") or state.get("closed")):
        _clear()
        return
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _clear() -> None:
    try:
        _state_path().unlink()
    except FileNotFoundError:
        pass


def _update(change) -> dict:
    """Read the state, apply `change(state)`, write it back: one short step under the lock."""
    with _lock:
        state = _load() or {"since": time.time(), "boot": OS.boot_time(), "saw_game": False, "lowered": [], "closed": []}
        change(state)
        _save(state)
        return state


def _stale(state: dict) -> bool:
    """Written before the PC last started: its processes are gone, and Windows started the launchers at sign-in."""
    if "boot" not in state:
        return False
    return abs(OS.boot_time() - float(state["boot"])) > 60


def active() -> bool:
    with _lock:
        return _load() is not None


def _heavy(proc: dict) -> str:
    """What this process is, if it is heavy work; '' otherwise."""
    name = proc["name"]
    if name in HEAVY:
        return HEAVY[name]
    if name in PYTHON:
        key = (proc["pid"], proc["created"])
        if key in _not_heavy:
            return ""
        line = OS.cmdline(proc["pid"])
        for needle, what in PYTHON_JOBS:
            if needle in line:
                return what
        if line:                        # an unreadable command line is asked again next time, never cached
            _not_heavy.add(key)
    return ""


def _lower(procs: list[dict], known: set) -> list[dict]:
    """Put heavy work behind the game; returns a record per process changed (only ones at normal priority or above)."""
    me = os.getpid()
    done = []
    for proc in procs:
        key = (proc["pid"], proc["created"])
        if proc["pid"] == me or key in known:
            continue
        what = _heavy(proc)
        if not what:
            continue
        was = OS.priority(proc["pid"], proc["created"])
        if was is None or was in (BELOW_NORMAL, IDLE):
            continue                    # already behind the game (or gone): left as it is
        if OS.set_priority(proc["pid"], proc["created"], BELOW_NORMAL):
            done.append({"pid": proc["pid"], "created": proc["created"], "was": was, "what": what})
    return done


def _join(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _why_not(app: dict, mine: list[dict]) -> str:
    """Why this launcher must stay open ('' when it may be closed). Checked before anything is changed."""
    pids = [p["pid"] for p in mine]
    if OS.has_window(pids):
        return "it has a window open"
    allowed = app["helpers"] | {app["exe"]}
    for p in mine:
        if not OS.descendants(p["pid"]) <= allowed:
            return "it is running a game or a tool it started"
    if app["how"] == "close" and OS.busy(pids):
        return "it is busy downloading or installing"
    return ""


def free_up() -> tuple[bool, str]:
    """The protocol step. Returns (changed anything, what happened in his words)."""
    global _last_sweep
    if not _busy.acquire(blocking=False):
        return False, "the background is already being cleared"
    try:
        procs = OS.processes()
        with _lock:
            state = _load() or {}
            if state and _stale(state):
                _clear()                          # left from before a restart: nothing in it is real any more
                state = {}
            known = {(r["pid"], r["created"]) for r in state.get("lowered", [])}
            fresh = _lower(procs, known)

            def lowered(s):
                s["since"] = time.time()          # a new run restarts the "game never started" clock
                s["lowered"] = s.get("lowered", []) + fresh
            _update(lowered)
        closed_now, left = [], []
        for app in LAUNCHERS:
            mine = [p for p in procs if p["name"] == app["exe"]]
            if not mine or any(c["name"] == app["name"] for c in (_load() or {}).get("closed", [])):
                continue
            row, gone = None, False
            try:
                why = _why_not(app, mine)
                command = OS.run_command(app["run"]) or OS.exe(mine[0]["pid"])
                if not why and not OS.is_file(split_command(command)[0]):
                    why = "I couldn't tell how to start it again"
                if why:
                    left.append(f"{app['name']} ({why})")
                    continue
                row = {"name": app["name"], "exe": app["exe"], "command": command, "how": app["how"]}
                _update(lambda s, r=row: s.setdefault("closed", []).append(r))     # written down BEFORE it is closed
                if app["how"] == "pause":
                    gone = OS.pause_onedrive(split_command(command)[0], [p["pid"] for p in mine])
                else:
                    gone = all([OS.end(p["pid"], p["created"]) for p in mine])    # every instance is tried
            except Exception:  # noqa: BLE001 - one launcher's trouble never stops the rest, nor loses what was done
                gone = False
            if row is None:
                left.append(f"{app['name']} (I couldn't check it)")
            elif gone or not OS.running(app["exe"]):
                closed_now.append(row)            # closed (even if the close reported trouble): it is recorded, so it comes back
            else:
                _update(lambda s, n=row["name"]: s.update(closed=[c for c in s.get("closed", []) if c["name"] != n]))
                left.append(f"{app['name']} (it didn't close)")
        _last_sweep = time.monotonic()
    finally:
        _busy.release()
    parts = []
    if fresh:
        whats = [r["what"] for r in fresh]
        if "a full test run" in whats:      # the gate and the pytest it starts are one job to him
            whats = [w for w in whats if w != "a test run"]
        parts.append("put " + _join(whats) + " behind the game (still running, at lower priority)")
    shut = [c["name"] for c in closed_now if c["how"] == "close"]
    paused = [c["name"] for c in closed_now if c["how"] == "pause"]
    if shut:
        parts.append("closed " + _join(shut))
    if paused:
        parts.append("paused " + _join(paused) + " syncing")
    if not parts:
        text = "nothing heavy was running in the background"
    else:
        text = "; ".join(parts) + "; all of it comes back when the game closes"
    if left:
        text += "; left alone: " + _join(left)
    return bool(fresh or closed_now), text


def restore() -> str:
    """Put everything back: priorities (only where they are still the one set here) and the closed launchers. Each
    launcher is crossed off the file before it is started, so nothing is ever started twice."""
    if not _busy.acquire(timeout=BUSY_WAIT_S):    # never in the middle of free_up closing launchers
        return "I'm still clearing the background; say it again in a minute."
    try:
        return _restore()
    finally:
        _busy.release()


def _restore() -> str:
    global _no_game
    with _lock:
        state = _load()
        if state is None:
            return ""
        _no_game = 0
        if _stale(state):
            _clear()                    # the PC restarted since: those processes are gone, Windows started the apps
            _not_heavy.clear()
            return "the PC restarted since, so Windows already started everything again."
        back = 0
        for row in state.get("lowered", []):
            try:
                if OS.priority(row["pid"], row["created"]) == BELOW_NORMAL and OS.set_priority(row["pid"], row["created"], row["was"]):
                    back += 1
            except Exception:  # noqa: BLE001
                pass
        state["lowered"] = []
        _save(state)
        reopened, already, failed = [], [], []
        for row in list(state.get("closed", [])):
            state["closed"] = [c for c in state["closed"] if c is not row]
            _save(state)
            try:
                if OS.running(row["exe"]):
                    already.append(row["name"])       # he opened it himself
                elif OS.launch(row["command"], row["name"]):
                    reopened.append(row["name"])
                else:
                    failed.append(row["name"])
            except Exception:  # noqa: BLE001
                failed.append(row["name"])
        _clear()
        _not_heavy.clear()
    parts = []
    if back:
        parts.append(f"background work is back to its usual priority ({back} process{'es' if back != 1 else ''})")
    if reopened:
        parts.append("started " + _join(reopened) + " again")
    if already:
        parts.append(_join(already) + (" was" if len(already) == 1 else " were") + " already open again")
    if failed:
        parts.append("couldn't start " + _join(failed) + " again; open it yourself or it starts at your next sign-in")
    return ("; ".join(parts) or "nothing needed putting back") + "."


def tick(game: str | None) -> str:
    """Called by the game watcher every few seconds. Keeps heavy work behind a running game, and puts everything back
    once the game has been gone for NO_GAME_TICKS checks (or never started). Returns what restore() said, when it ran."""
    global _last_sweep, _no_game
    with _lock:
        state = _load()
        if state is None:
            _no_game = 0
            return ""
        if game:
            _no_game = 0
            if not state.get("saw_game"):
                state["saw_game"] = True
                _save(state)
            if time.monotonic() - _last_sweep >= SWEEP_EVERY_S and not _busy.locked():
                _last_sweep = time.monotonic()
                fresh = _lower(OS.processes(), {(r["pid"], r["created"]) for r in state.get("lowered", [])})
                if fresh:
                    state["lowered"] = state.get("lowered", []) + fresh
                    _save(state)
            return ""
        if _busy.locked():
            return ""                   # free_up is still closing launchers: never put back halfway through
        if state.get("saw_game"):
            _no_game += 1
            if _no_game < NO_GAME_TICKS:
                return ""
        elif time.time() - float(state.get("since") or 0) < RESTORE_IF_NO_GAME_S:
            return ""
    return restore()                    # outside the state lock: restore takes the free_up lock first, then this one
