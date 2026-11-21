"""Crash watchdog (2026-11-12: a native crash, heap corruption 0xc0000374, left Jarvis down for 83 minutes).

Python cannot catch a native crash, so a tiny separate process (scripts/watchdog.py, this module, standard library only)
looks every 20 s and restarts Jarvis through start-jarvis.bat when, and only when, he is really gone and was not stopped
on purpose. Jarvis launches it himself at startup (ensure_running), detached, so it outlives him.

State lives in machine-state/watchdog/ (this PC only: data/ is synced to the laptop and must not carry a PC crash):
  stopped-on-purpose  marker written by stop-jarvis.bat and by quit_jarvis (tray Quit); cleared by start-jarvis.bat and
                      by main.py at startup
  watchdog.lock       the watchdog's pid (one watchdog only)
  crashes.json        each crash restart: time, exception code, faulting module, whether the owner was told
The one-line-per-event log is data/watchdog.log.

Everything with side effects is injected into Watchdog so the decisions are tested with fakes."""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK_EVERY = 20             # seconds between checks
HTTP_TIMEOUT = 4             # the server must answer within this
UNRESPONSIVE_AFTER = 6       # consecutive failed HTTP checks (about 2 minutes) before a log line; never a restart by itself
STARTUP_GRACE = 120          # after a restart, give him this long to come up before judging him again
LOOP_WINDOW = 600            # 3 restarts inside this many seconds is a crash loop
LOOP_RESTARTS = 3
BACKOFFS = (600, 1800)       # first loop waits 10 minutes, the next 30
STABLE_AFTER = 600           # alive this long resets the back-off ladder
QUIET_EXIT = 300             # stopped on purpose this long: the watchdog ends itself
CRASH_KEEP = 20

FRIENDLY = {   # faulting file -> what the owner would call it
    "inputwatch.py": "the controller watcher", "gaming.py": "the game watcher", "eyes.py": "the camera",
    "browserbridge.py": "the browser bridge", "appserver.py": "the app server", "scheduler.py": "the scheduler",
    "hud.py": "the on-screen display", "tray.py": "the tray icon", "listener.py": "the microphone listener",
    "transcriber.py": "speech recognition", "voiceid.py": "voice recognition", "speaker.py": "my voice output",
    "bargein.py": "the interruption listener", "keyboard": "the keyboard hook",
}
_NAMED = {"0xc0000005": "access violation", "0xc0000374": "heap corruption", "0xc00000fd": "stack overflow",
          "0xc0000409": "stack buffer overrun"}


def _dir() -> Path:
    return Path(os.getenv("JARVIS_WATCHDOG_DIR", str(ROOT / "machine-state" / "watchdog")))


def marker_file() -> Path:
    return _dir() / "stopped-on-purpose"


def lock_file() -> Path:
    return _dir() / "watchdog.lock"


def crashes_file() -> Path:
    return _dir() / "crashes.json"


def log_file() -> Path:
    return Path(os.getenv("JARVIS_WATCHDOG_LOG", str(ROOT / "data" / "watchdog.log")))


def enabled() -> bool:
    return os.getenv("JARVIS_WATCHDOG", "on").strip().lower() not in ("off", "0", "false", "no")


# ---- the stopped-on-purpose marker ------------------------------------------------------------------------------------
def mark_stopped() -> None:
    """Called by quit_jarvis (tray Quit, quit flag). stop-jarvis.bat writes the same file itself."""
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        marker_file().write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def clear_stopped() -> None:
    try:
        marker_file().unlink(missing_ok=True)
    except OSError:
        pass


def stopped_on_purpose() -> bool:
    return marker_file().exists()


# ---- processes ----------------------------------------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5                      # denied: it exists
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def jarvis_alive() -> bool:
    """His single-instance mutex exists (it vanishes the moment the process dies, whatever the pid file says). Access
    denied means the elevated Jarvis holds it: he exists."""
    if sys.platform != "win32":
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    handle = kernel32.OpenMutexW(0x00100000, False, "Local\\JarvisAssistantMutex")
    if handle:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return True
    return ctypes.get_last_error() == 5


def http_ok() -> bool:
    """The app server answers (any HTTP answer, even 401/404, means the server thread is alive)."""
    import urllib.error
    import urllib.request
    url = f"http://127.0.0.1:{int(os.getenv('JARVIS_APP_PORT', '8765'))}/"
    try:
        urllib.request.urlopen(url, timeout=HTTP_TIMEOUT).close()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def start_jarvis() -> None:
    bat = ROOT / "start-jarvis.bat"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(ROOT), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- the crash.log block -------------------------------------------------------------------------------------------------
def parse_crash(text: str) -> dict | None:
    """The newest fatal block of a faulthandler dump -> {code, module, cause}. Nothing when the text has none."""
    starts = [m.start() for m in re.finditer(r"^(?:Windows fatal exception|Fatal Python error):", text, re.M)]
    if not starts:
        return None
    block = text[starts[-1]:]
    head = block.splitlines()[0]
    m = re.search(r"code (0x[0-9a-fA-F]+)", head)
    if m:
        code = m.group(1).lower()
    elif "access violation" in head:
        code = "0xc0000005"
    elif "stack overflow" in head:
        code = "0xc00000fd"
    else:
        code = head.split(":", 1)[1].strip()[:60] or "unknown"
    module = ""
    cur = re.search(r"^Current thread[^\n]*\n((?:[ \t]+[^\n]*\n?)+)", block, re.M)
    if cur:
        frame = re.search(r'File "([^"]+)", line \d+', cur.group(1))
        if frame:
            module = frame.group(1)
    short = module.replace("\\", "/")
    if "/jarvis/" in short.lower():
        short = short.split("/jarvis/", 1)[-1] if "/jarvis/" in short else short.lower().split("/jarvis/", 1)[-1]
    elif "site-packages/" in short:
        short = short.split("site-packages/", 1)[-1]
    base = short.rsplit("/", 1)[-1]
    cause = FRIENDLY.get(base) or next((v for k, v in FRIENDLY.items() if "/" + k + "/" in "/" + short), "")
    if not cause:
        cause = f"{short}" if short else "a native library (no Python frame was running)"
    return {"code": code, "name": _NAMED.get(code, ""), "module": short, "cause": cause}


def read_crash(crash_log: Path, started_at: float) -> dict | None:
    """The crash that ended the instance that started at started_at: only when crash.log was written since then."""
    try:
        if crash_log.stat().st_mtime < started_at:
            return None
        with open(crash_log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            return parse_crash(f.read().decode("utf-8", "replace"))
    except OSError:
        return None


# ---- the record the app reads -----------------------------------------------------------------------------------------
def load_crashes() -> list:
    try:
        rows = json.loads(crashes_file().read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def record_crash(ts: float, info: dict | None, restarted: bool) -> dict:
    row = {"ts": int(ts), "at": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
           "code": (info or {}).get("code", "unknown"), "name": (info or {}).get("name", ""),
           "module": (info or {}).get("module", ""),
           "cause": (info or {}).get("cause", "unknown: there was no crash dump, so he may have been killed from outside"),
           "restarted": bool(restarted), "announced": False}
    rows = (load_crashes() + [row])[-CRASH_KEEP:]
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        tmp = crashes_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        os.replace(tmp, crashes_file())
    except OSError:
        pass
    return row


def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def notice(row: dict) -> str:
    return f"I crashed at {clock(row['ts'])} and restarted myself. Cause: {row.get('cause') or 'unknown'}."


def announce_pending(send=None) -> str:
    """On the first start after a watchdog restart: one short line through the notification path. Marked told so he says it
    once. send(title, message) defaults to core.notify.notify."""
    rows = load_crashes()
    pending = [r for r in rows if r.get("restarted") and not r.get("announced")]
    if not pending:
        return ""
    for r in pending:
        r["announced"] = True
    try:
        crashes_file().write_text(json.dumps(rows, indent=1), encoding="utf-8")
    except OSError:
        pass
    line = notice(pending[-1])
    if len(pending) > 1:
        line += f" That is {len(pending)} crashes since you last heard from me."
    if send is None:
        from core import notify
        send = lambda title, message: notify.notify(title, message, category="jarvis")
    send("Jarvis restarted", line)
    return line


# ---- "did you crash" / "why did you restart" ----------------------------------------------------------------------------
_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*", re.I)
_LEAD = r"^(?:(?:please|jarvis|hey jarvis|okay|ok|so|and)[,\s]+)*"
_TAIL = (r"(?:\s+(?:just|again|lately|recently|today|earlier|yesterday|tonight|last night|just now|by yourself|on your own|"
         r"a (?:while|moment|minute) ago|there|before|at all|then|this (?:morning|afternoon|evening)))*$")
_ASK = re.compile(
    _LEAD + r"(?:"
    r"(?:did|have)\s+you\s+(?:just\s+|recently\s+|actually\s+)?(?:crash(?:ed)?|restart(?:ed)?|reboot(?:ed)?|die[d]?|gone\s+down|go\s+down)"
    r"|why\s+(?:did|have)\s+you\s+(?:just\s+|suddenly\s+)?(?:crash(?:ed)?|restart(?:ed)?|reboot(?:ed)?|die[d]?|gone\s+down|go\s+down|shut\s+(?:down|off))"
    r"|why\s+(?:are|were)\s+you\s+(?:just\s+)?(?:restarting|rebooting|crashing)"
    r"|(?:when|how\s+often)\s+(?:did|have)\s+you\s+(?:last\s+)?(?:crash(?:ed)?|restart(?:ed)?)"
    r"|how\s+many\s+times\s+(?:did|have)\s+you\s+(?:crash(?:ed)?|restart(?:ed)?)"
    r"|what\s+(?:was|caused)\s+(?:the|your|that|this)\s+(?:last\s+)?crash"
    r"|what\s+(?:made|caused)\s+you\s+(?:to\s+)?(?:crash|restart)"
    r")" + _TAIL, re.I)


def is_crash_question(text) -> bool:
    said = " ".join(_CHANNEL.sub("", str(text or "")).split()).strip(" .!?,")
    return bool(_ASK.match(said))


def voice(text, now: float | None = None) -> str:
    """The answer, from the record, when the line is a question about HIS crashing; else ''. Decided in code, no model."""
    if not is_crash_question(text):
        return ""
    rows = [r for r in load_crashes() if r.get("restarted")]
    if not rows:
        return "I have no crash on record. I would have told you."
    last = rows[-1]
    now = time.time() if now is None else now
    same_day = datetime.fromtimestamp(last["ts"]).date() == datetime.fromtimestamp(now).date()
    when = clock(last["ts"]) if same_day else datetime.fromtimestamp(last["ts"]).strftime("%A %B %d at ") + clock(last["ts"])
    said = f"Yes. I crashed at {when} and restarted myself. Cause: {last.get('cause') or 'unknown'}."
    if len(rows) > 1:
        said += f" That is {len(rows)} crashes on record."
    return said


# ---- the loop --------------------------------------------------------------------------------------------------------------
class Watchdog:
    """One check per step(). Every outside effect is a constructor argument."""

    def __init__(self, alive=jarvis_alive, answers=http_ok, stopped=stopped_on_purpose, restart=start_jarvis, now=time.time,
                 log=None, crash_for=None, record=record_crash):
        self.alive, self.answers, self.stopped, self.restart, self.now = alive, answers, stopped, restart, now
        self.log = log or (lambda line: None)
        self.crash_for = crash_for or (lambda started_at: None)
        self.record = record
        self.fails = 0
        self.warned = False
        self.restarts: list[float] = []
        self.hold_until = 0.0
        self.trips = 0
        self.grace_until = 0.0
        self.alive_since: float | None = None
        self.last_seen_alive = self.now()
        self.gone_since: float | None = None
        self.said_stopped = False
        self.done = False

    def step(self) -> str:
        t = self.now()
        if self.alive():
            self.gone_since = None
            self.said_stopped = False
            self.alive_since = self.alive_since or t
            self.last_seen_alive = t
            if t - self.alive_since >= STABLE_AFTER:
                self.trips = 0
            if self.answers():
                if self.fails >= UNRESPONSIVE_AFTER:
                    self.log("answering again")
                self.fails, self.warned = 0, False
                return "ok"
            self.fails += 1
            if self.fails >= UNRESPONSIVE_AFTER and not self.warned:
                self.warned = True
                self.log(f"alive but the server has not answered for {self.fails} checks; not restarting (the process still exists)")
                return "unresponsive"
            return "slow"
        # the process is gone
        self.fails, self.warned = 0, False
        self.alive_since = None
        if self.gone_since is None:
            self.gone_since = t
        if self.stopped():
            if not self.said_stopped:
                self.said_stopped = True
                self.log("stopped on purpose; not restarting")
            if t - self.gone_since >= QUIET_EXIT:
                self.log("stopped on purpose for a while; watchdog ends")
                self.done = True
                return "exit"
            return "stopped"
        if t < self.grace_until:
            return "starting"
        if t < self.hold_until:
            return "backoff"
        self.restarts = [r for r in self.restarts if t - r < LOOP_WINDOW]
        if len(self.restarts) >= LOOP_RESTARTS:
            wait = BACKOFFS[min(self.trips, len(BACKOFFS) - 1)]
            self.trips += 1
            self.hold_until = t + wait
            self.restarts = []
            self.log(f"crash loop: restarts={LOOP_RESTARTS} window_min={LOOP_WINDOW // 60} wait_min={wait // 60}")
            return "backoff"
        info = None
        try:
            info = self.crash_for(self.last_seen_alive - STARTUP_GRACE)
        except Exception:
            pass
        self.record(t, info, True)
        what = f"{info['code']} in {info['module'] or 'native code'}" if info else "no crash dump"
        try:
            self.restart()
            self.log(f"gone, not stopped on purpose ({what}); restarted through start-jarvis.bat")
        except Exception as error:
            self.log(f"gone ({what}); restart FAILED: {type(error).__name__}: {error}")
        self.restarts.append(t)
        self.grace_until = t + STARTUP_GRACE
        self.gone_since = t
        return "restarted"


def write_log(line: str) -> None:
    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 500_000:
            path.write_text(path.read_text(encoding="utf-8", errors="replace")[-100_000:], encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


# ---- one watchdog only ---------------------------------------------------------------------------------------------------
def take_lock() -> bool:
    """True when this process now holds the lock; False when a live watchdog already does."""
    _dir().mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_file()), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            try:
                other = int(lock_file().read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                other = 0
            if other and other != os.getpid() and pid_alive(other):
                return False
            try:
                lock_file().unlink(missing_ok=True)
            except OSError:
                return False
    return False


def drop_lock() -> None:
    try:
        if lock_file().read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock_file().unlink(missing_ok=True)
    except OSError:
        pass


def running() -> bool:
    try:
        return pid_alive(int(lock_file().read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return False


def ensure_running() -> bool:
    """Called by main.py at startup: launch the watchdog detached (it outlives Jarvis) unless JARVIS_WATCHDOG=off, this is
    not Windows (the laptop's systemd restarts him), or one already runs. True when one was launched."""
    if not enabled() or sys.platform != "win32" or running():
        return False
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    flags = 0x00000008 | 0x00000200 | 0x08000000      # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    cmd = [str(exe), str(ROOT / "scripts" / "watchdog.py")]
    kw = dict(cwd=str(ROOT), close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)       # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, **kw)                    # a job that forbids breakaway
    return True


def run_forever() -> int:
    if not take_lock():
        write_log("another watchdog is running; this one ends")
        return 0
    write_log(f"watchdog started (pid {os.getpid()})")
    crash_log = ROOT / "crash.log"
    running_file = ROOT / "data" / "jarvis.running"

    def crash_for(since: float):
        try:
            started = running_file.stat().st_mtime
        except OSError:
            started = since
        return read_crash(crash_log, started)

    dog = Watchdog(log=write_log, crash_for=crash_for)
    try:
        while not dog.done:
            try:
                dog.step()
            except Exception as error:                      # a bug here must never end the watch
                write_log(f"check failed: {type(error).__name__}: {error}")
            time.sleep(CHECK_EVERY)
    finally:
        drop_lock()
    return 0
