"""Launch and close any installed app by name, using the Start Menu as the catalog.

Only real installed apps (Start Menu shortcuts) and a few Windows built-ins can be
launched, so a misheard command can't run arbitrary programs.
"""
import difflib
import os
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path

import psutil

from tools.registry import tool

BUILTINS = {
    "notepad": "notepad.exe",
    "calculator": "calculator:",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",           # what Windows 11 and the owner call File Explorer ("open files and go to downloads")
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "paint": "mspaint.exe",
    "terminal": "wt.exe",
    "command prompt": "cmd.exe",
    "camera": "microsoft.windows.camera:",
    "browser": "https://www.google.com",  # opens the default browser
    "spotify": "spotify:",
}

# Well-known websites: an app is found by their name only when it really contains it ("github desktop"), never by a
# close spelling.
WEBSITES = {"youtube", "google", "bing", "duckduckgo", "wikipedia", "github", "git hub", "reddit", "gmail", "facebook",
            "instagram", "twitter", "netflix", "amazon", "stack overflow", "stackoverflow", "linkedin", "twitch", "tiktok"}

# Processes that must never be closed.
PROTECTED = {"explorer", "csrss", "winlogon", "lsass", "services", "svchost", "system",
             "smss", "wininit", "dwm", "python", "pythonw"}

# A process started with a hidden window ("powershell -WindowStyle Hidden ...") is a background job, usually a scheduled
# task's, never the window he means by "close PowerShell" (2026-11-09: that request also closed the hidden console that
# kept Syncthing running, and the phone's journal stopped reaching the PC for two days).
_HIDDEN_START = re.compile(r"(?:^|\s)[-/](?:w|wi|win|wind|windo|window|windows|windowst|windowsty|windowstyl|windowstyle)"
                           r"(?:\s+|:)[\"']?(?:hidden|h|1)\b", re.I)


def _background_pids(exe: str) -> tuple[set[int], set[int]]:
    """(background, other) pids of the running EXE: background ones were started with a hidden window."""
    background, other = set(), set()
    for p in psutil.process_iter(["name", "pid"]):
        if (p.info.get("name") or "").lower() != exe.lower():
            continue
        try:
            hidden = bool(_HIDDEN_START.search(" ".join(p.cmdline())))
        except (psutil.Error, OSError):   # not ours to read: treated as his, the old behaviour
            hidden = False
        (background if hidden else other).add(p.info["pid"])
    return background, other


_SKIP_GAMES = ("redistributable", "steamworks", "dedicated server", "sdk", "proton", "soundtrack")


def _steam_games() -> dict[str, str]:
    """Installed Steam games -> steam://rungameid/<id> (live 2026-11-05: 'open Apex Legends' found nothing; Steam games have
    no Start Menu shortcut unless the owner ticked one)."""
    import re
    roots = [Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam"]
    games: dict[str, str] = {}
    try:
        vdf = (roots[0] / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        roots += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    for root in dict.fromkeys(roots):
        for acf in (root / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name, appid = re.search(r'"name"\s+"([^"]+)"', text), re.search(r'"appid"\s+"(\d+)"', text)
            if name and appid and not any(w in name[1].lower() for w in _SKIP_GAMES):
                games.setdefault(name[1].lower(), f"steam://rungameid/{appid[1]}")
    return games


def _epic_games() -> dict[str, str]:
    """Installed Epic Games Store games -> the launcher's own launch link."""
    import json
    games: dict[str, str] = {}
    folder = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    for item in folder.glob("*.item"):
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        name, app = str(data.get("DisplayName") or ""), str(data.get("AppName") or "")
        if name and app and data.get("bIsApplication", True) and not any(w in name.lower() for w in _SKIP_GAMES):
            games.setdefault(name.lower(), f"com.epicgames.launcher://apps/{app}?action=launch&silent=true")
    return games


def _unreal_editors() -> dict[str, Path]:
    """The Epic launcher installs Unreal Engine with no Start Menu shortcut (C:\\Program Files\\Epic Games\\UE_5.8): "Open Unreal
    Engine" was 'not installed', and the reply claimed it was opening (holdout h054). The newest version is "unreal engine"."""
    root = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Epic Games"
    found = []
    for folder in root.glob("UE_*"):
        exe = folder / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
        version = tuple(int(p) for p in re.findall(r"\d+", folder.name)[:3])
        if exe.is_file() and version:
            found.append((version, exe))
    return {"unreal engine": max(found)[1]} if found else {}


def game_folders() -> dict[str, Path]:
    """Installed Steam / Epic game name -> its install folder. The process of a game is often named nothing like its store
    name ('crimson desert enhanced' runs as CrimsonDesert.exe, live 2026-11-07), so it is found by WHERE it runs from."""
    import json
    import re
    folders: dict[str, Path] = {}
    roots = [Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam"]
    try:
        vdf = (roots[0] / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        roots += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    for root in dict.fromkeys(roots):
        for acf in (root / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name, folder = re.search(r'"name"\s+"([^"]+)"', text), re.search(r'"installdir"\s+"([^"]+)"', text)
            if name and folder:
                folders.setdefault(name[1].lower(), root / "steamapps" / "common" / folder[1])
    epic = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    for item in epic.glob("*.item"):
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if data.get("DisplayName") and data.get("InstallLocation"):
            folders.setdefault(str(data["DisplayName"]).lower(), Path(data["InstallLocation"]))
    return folders


def game_processes(app: str) -> list:
    """The running processes of an installed game (psutil.Process), found by its install folder; [] for anything else."""
    folder = game_folders().get((app or "").lower())
    if not folder:
        return []
    root = os.path.normcase(str(folder)).rstrip("\\/") + os.sep
    found = []
    for p in psutil.process_iter(["exe"]):
        exe = p.info.get("exe")
        if exe and os.path.normcase(exe).startswith(root):
            found.append(p)
    return found


def game_of_window(hwnd) -> str:
    """The installed game whose process owns this window ('' when it is not a game's)."""
    try:
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(int(hwnd))
        exe = os.path.normcase(psutil.Process(pid).exe())
    except Exception:  # noqa: BLE001
        return ""
    for name, folder in game_folders().items():
        if exe.startswith(os.path.normcase(str(folder)).rstrip("\\/") + os.sep):
            return name
    return ""


def is_game(target) -> bool:
    return isinstance(target, str) and target.startswith(("steam://rungameid/", "com.epicgames.launcher://apps/"))


def _store_apps() -> dict[str, str]:
    """Microsoft Store (packaged) apps, which have no Start Menu .lnk: ChatGPT, Claude, WhatsApp... Live 2026-11-08: "close
    ChatGPT" failed five times because JARVIS did not know ChatGPT existed. Windows' own Start list (Get-StartApps) names
    them; a packaged app's AppID contains '!' and launches as shell:AppsFolder\\<AppID>."""
    import json
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = json.loads(out or "[]")
    except Exception:  # noqa: BLE001  (no list: the shortcut catalogue still stands)
        return {}
    return store_apps_from(rows)


def store_apps_from(rows) -> dict[str, str]:
    """name -> the shell:AppsFolder launch target, for the packaged apps in Get-StartApps rows (pure: tests feed it rows)."""
    if isinstance(rows, dict):
        rows = [rows]
    found: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, appid = str(row.get("Name") or "").strip().lower(), str(row.get("AppID") or "")
        # Windows' own inbox apps (Microsoft.*: Weather, Clock, Photos, Tips...) are left out: their names are everyday words,
        # and "what's the weather" must stay a question, not open an app. The built-ins JARVIS needs are in BUILTINS already.
        inbox = appid.casefold().startswith(("microsoft.", "microsoftwindows.", "windows.", "microsoftcorporationii."))
        own = "jarvis" in re.sub(r"[^a-z]", "", name)          # his own packaged shell must never become a close target
        if (name and "!" in appid and not inbox and not own
                and not any(w in name for w in ("uninstall", "readme", "help", "documentation"))):
            found.setdefault(name, "shell:AppsFolder\\" + appid)
    return found


@lru_cache(maxsize=1)
def _installed() -> dict[str, Path]:
    dirs = [
        Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs",
    ]
    apps: dict[str, Path] = {}
    for d in dirs:
        for lnk in d.rglob("*.lnk"):
            name = lnk.stem.lower()
            if not any(w in name for w in ("uninstall", "readme", "help", "documentation")):
                apps.setdefault(name, lnk)
    for games in (_steam_games, _epic_games, _store_apps, _unreal_editors):   # a Start Menu shortcut, when there is one, stays the first choice
        try:
            for name, link in games().items():
                apps.setdefault(name, link)
        except Exception:  # noqa: BLE001  (a broken library file never hides the other apps)
            pass
    return apps


def _find(query: str) -> tuple[str, str | Path] | None:
    q = query.lower().strip()
    if q in BUILTINS:
        return q, BUILTINS[q]
    apps = _installed()
    if q in apps:
        return q, apps[q]
    # "Edge browser", "the Steam app": the kind of thing said after its name. "Open Edge Browser." opened the default
    # browser's Google page (the close match was "browser"), live 2026-11-12. The name alone decides when it is known.
    stem = re.sub(r"\s+(?:browser|web browser|app|application|program)$", "", q)
    if stem != q and stem:
        found = _find(stem)
        if found and found[0] != "browser":
            return found
    # A short name that begins a Windows built-in ("calc" -> calculator) means the built-in, not a Start Menu app that merely
    # contains the word ("libreoffice calc"); live 2026-11-05: "bring up calc" launched LibreOffice Calc.
    if len(q) >= 4:
        builtin = sorted((n for n in BUILTINS if n.startswith(q) and n != "browser"), key=len)
        if builtin:
            return builtin[0], BUILTINS[builtin[0]]
    # "chrome" -> "google chrome", "word" -> "word"
    contains = sorted((n for n in apps if q in n), key=len)
    if contains:
        return contains[0], apps[contains[0]]
    if q in WEBSITES or q.removeprefix("close ").removeprefix("open ") in WEBSITES:
        return None       # a website is never a look-alike app: "github" was Git GUI, "close github" was Logitune (holdout h041)
    close = difflib.get_close_matches(q, list(apps) + list(BUILTINS), n=1, cutoff=0.6)
    if close:
        name = close[0]
        return name, apps.get(name) or BUILTINS[name]
    return None


def resolve_app(query: str) -> str | None:
    """Return the installed/builtin canonical name without launching anything.

    This is intentionally the single catalog used by both direct tool calls and
    the deterministic PC fast path.  A model never turns a made-up executable
    name into a process launch.
    """
    match = _find(query) if isinstance(query, str) else None
    return match[0] if match else None


def _visible_window_pids() -> set[int]:
    """Best-effort visible top-level windows; UWP hosts are included by PID."""
    if os.name != "nt":
        return set()
    import ctypes
    pids = set()
    user32 = ctypes.windll.user32
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                pids.add(pid.value)
        return True
    user32.EnumWindows(visit, 0)
    return pids


def _matching_process_pids(name: str) -> set[int]:
    tokens = {part for part in name.casefold().replace("-", " ").split() if len(part) > 2}
    matches = set()
    for proc in psutil.process_iter(["name", "exe"]):
        label = " ".join(str(proc.info.get(k) or "") for k in ("name", "exe")).casefold()
        if tokens and any(token in label for token in tokens):
            matches.add(proc.pid)
    return matches


def _ready(name: str, before: set[int]) -> bool:
    """A launched app is ready only when it owns a visible window.

    Process creation alone is deliberately not success: it is common for a
    launcher, a suspended UWP host, or a second background process to exist.
    For UWP applications the visible ApplicationFrameHost window is accepted
    only after a newly observed matching process appears.
    """
    pids = _matching_process_pids(name)
    if pids & _visible_window_pids():
        return True
    if not pids - before:
        return False
    # A UWP/packaged app's window is owned by ApplicationFrameHost, not by its own process.  A newly started
    # matching process plus ANY visible window elsewhere is not evidence (measured: Notepad reported ready ~300 ms
    # before its window existed), so require a visible frame window whose title names the app.
    tokens = {part for part in name.casefold().replace("-", " ").split() if len(part) > 2}
    from tools import pcops
    return any(w.get("process", "").casefold() == "applicationframehost.exe" and not w.get("minimized")
               and any(token in w.get("title", "").casefold() for token in tokens) for w in pcops.windows())


def _own_process_stems(name: str, target) -> set[str]:
    """The process names (no .exe) that ARE this app, never merely a process whose path shares a word with it
    ('microsoft', 'windows', 'studio', 'code' all appear in other apps' paths): the launch target's own exe, the whole
    name run together ('discord'), or one word of the name that is the process name ('google chrome' -> chrome)."""
    stems = {"".join(re.findall(r"[a-z0-9]+", name.casefold()))}
    stems |= {part for part in re.findall(r"[a-z0-9]+", name.casefold()) if len(part) > 2}
    if isinstance(target, (str, Path)) and str(target).lower().endswith(".exe"):
        stems.add(str(target).replace("\\", "/").rsplit("/", 1)[-1][:-4].casefold())
    return {s for s in stems if s}


def _frame_title_is(name: str, title: str) -> bool:
    """A packaged (UWP) app's window belongs to ApplicationFrameHost.exe and is known only by its title: 'Calculator',
    'Settings', 'Photos - img.jpg'. The title must BE the app's name or start/end with it as its own part."""
    n, t = name.casefold().strip(), str(title or "").casefold().strip()
    return bool(n) and (t == n or t.startswith(n + " - ") or t.endswith(" - " + n) or t.endswith(" – " + n))


def _raise_app_window(name: str, target, cancelled=lambda: False) -> bool:
    """Bring the app's own visible window (minimized or behind others) to the front. Its own process's window is focused
    through the verified focus_app action; a packaged app's frame window (Calculator, Settings), which focus_app cannot
    tell apart from other packaged apps, by its handle. True only when Windows reports it in front."""
    from tools import pcops
    from core import pcactions
    rows = pcops.windows()
    stems = _own_process_stems(name, target)
    mine = [w for w in rows if re.sub(r"\.exe$", "", str(w.get("process") or "").casefold()) in stems]
    if mine:
        window = sorted(mine, key=lambda w: (not w.get("foreground"), bool(w.get("minimized"))))[0]
        pcactions.perform("focus_app", {"process": window["process"]}, cancelled=cancelled)
        return True
    frames = [w for w in rows if str(w.get("process") or "").casefold() == "applicationframehost.exe"
              and w.get("hwnd") and _frame_title_is(name, w.get("title"))]
    if frames:
        from jarvis_pc.guardrails.stepfence import fence
        fence("focus " + name)
        return pcactions.raise_window(frames[0]["hwnd"])
    return False


def _bring_forward_if_open(name: str, target, cancelled=lambda: False) -> str:
    """The app already owns a visible window of its own (judged by the window's process NAME against the app's own exe,
    or a packaged app's frame titled with its name, never a loose path match): bring it forward and return a truthful
    sentence, so a second copy is not started (and not reported as opened). "" when it is not open, or focusing was
    refused (then it launches as before)."""
    try:
        if not _raise_app_window(name, target, cancelled):
            return ""
    except InterruptedError:
        raise
    except Exception:  # noqa: BLE001  (focus refused or windows unreadable: launch as before)
        return ""
    return f"{name} was already open; I brought it to the front."


def _front_after_launch(name: str, target, cancelled=lambda: False) -> None:
    """A window JARVIS starts (elevated, from the background) can open BEHIND his current app: bring it forward. The
    launch already verified the window exists, so a refused focus changes nothing about the reply."""
    try:
        _raise_app_window(name, target, cancelled)
    except InterruptedError:
        raise
    except Exception:  # noqa: BLE001
        pass


def launch_and_wait(app: str, *, timeout: float = 8.0, cancelled=lambda: False) -> tuple[str, bool]:
    """Launch an allowlisted app and wait for a visible desktop window.

    Returns a truthful detail string and readiness bit.  The caller may report
    a launch request that is still starting, but must never call it ready.
    """
    match = _find(app)
    if match is None:
        sample = ", ".join(sorted(_installed())[:40])
        return f"No installed app matches '{app}'. Some installed apps: {sample}", False
    name, target = match
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground(f"open {name}")       # a mission never launches a window (or a game) in front: "needs you at the PC"
    if isinstance(target, str) and target.lower().endswith(".exe") and not os.path.isabs(target):
        import shutil
        target = shutil.which(target) or target       # explorer.exe (used to launch with normal rights from admin mode) cannot resolve a bare name like mspaint.exe
    before = _matching_process_pids(name)
    shown = _bring_forward_if_open(name, target, cancelled)
    if shown:
        return shown, True
    from tools.software import is_admin, launch_as_user
    if is_admin():
        launch_as_user(str(target))
    elif isinstance(target, str) and target.endswith(".exe"):
        subprocess.Popen([target])
    else:
        os.startfile(str(target))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError("Stopped while waiting for the application window.")
        if _ready(name, before):
            _front_after_launch(name, target, cancelled)
            return f"Opened {name}.", True
        time.sleep(.1)
    if is_game(target):
        return (f"Asked {'Steam' if target.startswith('steam:') else 'the Epic Games launcher'} to start {name}. Games take a "
                "minute or more to show their window, so it is not verified open yet."), False
    return f"Launch was requested for {name}, but no visible application window was verified yet.", False


@tool(
    "Launch any installed application (browsers, games, Office, Discord, Steam, etc.). "
    "Use open_website instead for a specific web page.",
    {"app": {"type": "string", "description": "App name as the user said it, e.g. 'chrome', 'brave', 'steam', 'word'"}},
)
def open_app(app: str) -> str:
    detail, _ready_now = launch_and_wait(app)
    return detail


@tool(
    "List installed applications whose name contains the given text (empty text lists all).",
    {"contains": {"type": "string"}},
)
def list_apps(contains: str) -> str:
    names = sorted(n for n in list(_installed()) + list(BUILTINS) if contains.lower() in n)
    return ", ".join(names) or "None found."


_SHELL_TITLES = frozenset({"", "program manager"})
_SHELL_CLASSES = frozenset({"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"})


def is_shell_window(title: str, process: str, cls: str = "") -> bool:
    """The desktop itself, never an app's window: explorer.exe's "Program Manager" (the desktop), a window with no title, and the
    shell's tray / desktop classes (when the class is known). Closing "explorer" must not hide the desktop."""
    if str(process or "").casefold().removesuffix(".exe") != "explorer":
        return False
    return str(title or "").strip().casefold() in _SHELL_TITLES or str(cls or "").casefold() in _SHELL_CLASSES


def windows_of(app: str, windows: list[dict], pids=()) -> list[dict]:
    """The windows among `windows` (pcops.windows() rows) that belong to the catalog app `app`.

    The rule is the one this function always had: the app's name (spaces dropped) is in the window's title or process name, or the
    window belongs to one of the app's game processes (`pids`). The only differences: the desktop shell is never an app window
    (is_shell_window), and a row that went away while it was being listed (no title / process to read) is skipped."""
    needle = app.casefold().replace(" ", "")
    out = []
    for window in windows:
        try:
            title, process = window["title"], window["process"]
            hit = needle in (title + process).casefold().replace(" ", "") or window.get("pid") in pids
        except (KeyError, TypeError, AttributeError):
            continue
        if hit and not is_shell_window(title, process, str(window.get("class", ""))):
            out.append(window)
    return out


def _flat(text) -> str:
    """Letters and digits only, lower case: "Microsoft​ Edge" -> "microsoftedge" (invisible marks and spacing never matter)."""
    return re.sub(r"[^a-z0-9+]", "", str(text or "").casefold())


_STEM_PREFIXES = ("microsoft", "windows", "ms", "win")
_STEM_SUFFIXES = ("app", "x64", "x86", "64", "32")


def _stem_names(process: str) -> set[str]:
    """The names a process file could be the app under: "CalculatorApp.exe" -> calculatorapp, calculator; "mspaint.exe" -> paint."""
    stem = _flat(str(process or "").casefold().removesuffix(".exe"))
    names = {stem}
    for prefix in _STEM_PREFIXES:
        if stem.startswith(prefix) and len(stem) > len(prefix):
            names.add(stem[len(prefix):])
    for name in list(names):
        for suffix in _STEM_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                names.add(name[:-len(suffix)])
    return names


def _own_names(app: str) -> set[str]:
    """What the app's own process may be called: its catalogue name, and the program a Windows built-in starts ("explorer.exe")."""
    names = {_flat(app)}
    target = BUILTINS.get(app.casefold())
    if isinstance(target, str) and target.casefold().endswith(".exe"):
        names.add(_flat(target.casefold().removesuffix(".exe")))
    return names - {""}


def own_windows(app: str, windows: list[dict], pids=()) -> list[dict]:
    """The rows of `windows` that are the app's OWN: its process is the app (or the built-in's program, or one of its game
    processes), or the last part of the title is its name ("Untitled - Notepad"). A window that only holds the name somewhere else
    ("notepad tutorial - Google Chrome", "Crossword - Google Chrome") is not the app, and the desktop shell never is. Used only where
    a wrong "it is open" would be said, never to decide what gets closed (that stays windows_of, main's rule)."""
    names = _own_names(app)
    needle = _flat(app)
    out = []
    for window in windows:
        try:
            title, process = str(window["title"]), str(window["process"])
        except (KeyError, TypeError, AttributeError):
            continue
        if is_shell_window(title, process, str(window.get("class", ""))):
            continue
        tail = _flat(title.rsplit(" - ", 1)[-1])
        if window.get("pid") in pids or _stem_names(process) & names or (needle and needle == tail):
            out.append(window)
    return out


def _process_stems() -> set[str]:
    """Every running process's stem names (stem_names of each), for "is it running with no window"; empty if unreadable."""
    stems: set[str] = set()
    try:
        for proc in psutil.process_iter(["name"]):
            stems |= _stem_names(proc.info.get("name") or "")
    except Exception:  # noqa: BLE001
        return set()
    return stems


def app_running(app: str) -> bool:
    """Is a process of the app running (a tray-only app has no window)? The always-running desktop shell is no evidence."""
    names = _own_names(app) - {"explorer"}
    return bool(names and names & _process_stems())


def visible_own_windows(app: str) -> list[dict]:
    """The visible windows that are the app's own (own_windows): the stricter question "is it really open", not "what would a close touch"."""
    from tools import pcops
    pids = {p.pid for p in game_processes(app)}
    return own_windows(app, pcops.windows(), pids)


def visible_windows_for_app(app: str) -> list[dict]:
    """Return visible top-level windows attributable to a catalog app.

    UWP apps commonly put their visible UI in ApplicationFrameHost.exe, so a
    process-name-only test would miss Calculator, Settings, and similar apps.
    This is deliberately a read-only observation: closing code may use it for
    an honest postcondition without treating a lingering background process as
    a visible app window.
    """
    from tools import pcops
    if _is_file_explorer(app):
        return _folder_windows(pcops.windows())
    return windows_of(app, pcops.windows(), {p.pid for p in game_processes(app)})


_FILE_EXPLORER = {"file explorer", "files", "windows explorer"}


def _is_file_explorer(app: str) -> bool:
    return " ".join(str(app or "").casefold().split()) in _FILE_EXPLORER


def _folder_windows(windows: list[dict]) -> list[dict]:
    """File Explorer's folder windows (class CabinetWClass), never the desktop or taskbar of the same explorer.exe."""
    try:
        import win32gui
    except ImportError:
        return []
    out = []
    for w in windows:
        try:
            if str(w.get("process", "")).casefold() == "explorer.exe" and win32gui.GetClassName(int(w["hwnd"])) == "CabinetWClass":
                out.append(w)
        except Exception:  # noqa: BLE001  (a window that went away while listing)
            continue
    return out


def _close_folder_windows(app: str) -> str:
    """'Close all files' / 'close File Explorer': each folder window is asked to close (what its X does). explorer.exe itself
    is the desktop and taskbar and is never ended; matching processes by the word 'files' closed FileSyncHelper (holdout h034)."""
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground(f"close {app}")
    try:
        import win32con
        import win32gui
    except ImportError:
        return "I can't read File Explorer's windows on this machine, so I left them alone."
    shown = visible_windows_for_app(app)
    for window in shown:
        win32gui.PostMessage(int(window["hwnd"]), win32con.WM_CLOSE, 0, 0)
    return f"Asked {len(shown)} File Explorer window(s) to close." if shown else "No File Explorer windows are open."


def _uia_process(window) -> str:
    try:
        return psutil.Process(window.process_id()).name()
    except Exception:  # noqa: BLE001  (gone, or not ours to read: no process name)
        return ""


def _close_visible_windows(app: str) -> int:
    """Ask matching UIA windows to close without answering any save prompt.

    UI Automation's Close pattern addresses the actual top-level UWP window;
    taskkill /IM CalculatorApp.exe does not.  A missing optional UIA dependency
    simply leaves the conservative process fallback below in place.
    """
    # Imported BEFORE the try: an `except StepAbandoned` clause names it, and when it was imported inside the try, any error
    # raised earlier (a window that vanished while UIA listed the desktop) made that clause raise UnboundLocalError instead,
    # which escaped this function and failed the whole close before anything was asked to close (live 2026-11-13, Notepad).
    from jarvis_pc.guardrails.stepfence import StepAbandoned, fence
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        needle = app.casefold().replace(" ", "")
        matches = []
        for window in desktop.windows():
            try:
                title = window.window_text()
            except Exception:  # noqa: BLE001  (a window that went away while listing: not a match)
                continue
            if needle in title.casefold().replace(" ", ""):
                if title.strip().casefold() == "program manager" and is_shell_window(title, _uia_process(window)):
                    continue                # the desktop is no app's window
                matches.append(window)
        for window in matches:
            fence(f"close {app}")           # the window list can take long; past the step's limit nothing is closed
            window.close()
        return len(matches)
    except StepAbandoned:
        raise
    except Exception:
        return 0


def _post_close(app: str) -> None:
    try:
        import win32con
        import win32gui
        for window in visible_windows_for_app(app):
            win32gui.PostMessage(int(window["hwnd"]), win32con.WM_CLOSE, 0, 0)
    except Exception:  # noqa: BLE001  (best effort; the other close requests still went out)
        pass


def force_close_game(app: str) -> str:
    """End a game that did not quit when asked (the owner said yes to losing unsaved progress). Only the game's own
    processes, found by its install folder; never a launcher or anything else."""
    procs = game_processes(app)
    if not procs:
        return f"{app.title()} is not running."
    denied = 0
    for p in procs:
        r = subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True, text=True)
        denied += r.returncode != 0 and "denied" in (r.stdout + r.stderr).lower()
    import time
    end = time.monotonic() + 8
    while game_processes(app) and time.monotonic() < end:
        time.sleep(0.25)
    if not game_processes(app):
        return f"Closed {app.title()}."
    if denied:
        return (f"Windows won't let me close {app.title()}: it is running as administrator and I am not. "
                "Close it from the game, or run me as administrator.")
    return f"{app.title()} is still running; I couldn't end it."


@tool(
    "Close a running application gracefully, as if clicking its X (it may prompt to save). "
    "If the user asked you to close it, just do it; otherwise ask first.",
    {"app": {"type": "string", "description": "Program name, e.g. 'chrome', 'notepad', 'spotify'"}},
    strict=True,
)
def close_app(app: str) -> str:
    q = app.lower().replace(" ", "").removesuffix(".exe")
    if q in PROTECTED:
        return f"{app} is a protected system process."
    if _is_file_explorer(app):
        return _close_folder_windows(app)
    from jarvis_pc.guardrails.stepfence import require_foreground
    require_foreground(f"close {app}")       # closing a window changes his desktop: never from a mission (2026-11-10 review)
    closed_windows = _close_visible_windows(app)
    _post_close(app)              # what clicking the window's X sends: Paint ignored the rest and never asked to save (live 2026-11-07)
    names = {p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]}
    targets = {n for n in names if q in n.lower().removesuffix(".exe")
               and n.lower().removesuffix(".exe") not in PROTECTED}
    targets |= {p.name() for p in game_processes(app)}          # a game's own exe, whatever it is called
    by_pid, left = {}, False
    for exe in list(targets):
        background, other = _background_pids(exe)
        if background:                  # leave the hidden background jobs alone: close only his own ones, one by one
            targets.discard(exe)
            left = True
            if other:
                by_pid[exe] = sorted(other)
    if not targets and not by_pid and not closed_windows:
        if left:      # one IS running (he may see it in Task Manager): never "nothing is running"
            return (f"Only a background {app} is running (started with a hidden window, usually by a scheduled task), "
                    "so I left it alone. End it in Task Manager if you mean that one.")
        return f"Nothing called {app} is running."
    for exe in targets:
        subprocess.run(["taskkill", "/IM", exe], capture_output=True)  # no /F: graceful
    for exe, pids in by_pid.items():
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)  # no /F: graceful
    targets |= set(by_pid)
    requested = []
    if closed_windows:
        requested.append(f"{closed_windows} visible window(s)")
    if targets:
        requested.append(", ".join(sorted(targets)))
    return "Asked " + " and ".join(requested) + " to close."
