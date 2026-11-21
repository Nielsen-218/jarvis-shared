"""Quick Launch: each app's own icon, taken from the program installed on this PC, and the apps a person pinned.

No brand artwork is bundled or downloaded. The icon of an installed program (found through the same Start Menu catalogue
Jarvis opens apps from, tools/apps.py) is extracted once and cached as a PNG under data/cache/app-icons/. When the app
is not installed or the icon cannot be taken, the caller shows the letter mark instead."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from core import storage

MAX_PINNED = 6
RETRY_AFTER = 600                # a failed extraction waits ten minutes before the next try
_lock = threading.Lock()      # guards the table of per-icon locks and the pinned-apps file
_key_locks: dict = {}

# The built-in tiles: key -> (label, what is said to open it, letter mark, colour class, words that find it in the catalogue,
# known install locations when it has no Start Menu shortcut).
DEFAULTS = {
    "blender": ("Blender", "Open Blender", "B", "bl", ["blender"], []),
    "unreal": ("UE", "Open Unreal Engine", "UE", "ue", ["unreal engine", "unrealeditor"],
               [r"%PROGRAMFILES%\Epic Games\UE_*\Engine\Binaries\Win64\UnrealEditor.exe"]),
    "vscode": ("VS Code", "Open Visual Studio Code", "VS", "vs", ["visual studio code"],
               [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe", r"%PROGRAMFILES%\Microsoft VS Code\Code.exe"]),
    "github": ("GitHub", "Open GitHub", "GH", "gh", ["github desktop"], [r"%LOCALAPPDATA%\GitHubDesktop\GitHubDesktop.exe"]),
    "steam": ("Steam", "Open Steam", "S", "st", ["steam"], []),
}


def installed() -> dict:
    """name -> launch target, the catalogue Jarvis opens apps from (empty when it cannot be read)."""
    try:
        from tools import apps
        return dict(apps._installed())
    except Exception:  # noqa: BLE001
        return {}


def catalogue_names() -> list[str]:
    """Installed programs a person can pin (the Start Menu catalogue; Steam and Epic games included, no Windows built-ins)."""
    return sorted(installed())


def source_for(key: str):
    """The file whose icon stands for this key: a default tile's key, or an exact catalogue name. None when not installed."""
    key = str(key or "").strip().casefold()
    apps = installed()
    if key in DEFAULTS:
        words, paths = DEFAULTS[key][4], DEFAULTS[key][5]
        for word in words:
            hits = sorted((n for n in apps if word in n), key=len)
            if hits:
                return apps[hits[0]]
        for pattern in paths:
            found = sorted(glob.glob(os.path.expandvars(pattern)))
            if found:
                return Path(found[-1])
        return None
    return apps.get(key)


def _extract_windows(src: Path, dest: Path) -> bool:
    """The shell's icon for a shortcut or program, saved as a PNG (Windows only; no network)."""
    if os.name != "nt":
        return False
    script = ("Add-Type -AssemblyName System.Drawing; $i=[System.Drawing.Icon]::ExtractAssociatedIcon($env:ICON_SRC); "
              "$b=$i.ToBitmap(); $b.Save($env:ICON_DST,[System.Drawing.Imaging.ImageFormat]::Png)")
    env = dict(os.environ, ICON_SRC=str(src), ICON_DST=str(dest))
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, capture_output=True,
                       timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return dest.is_file() and dest.stat().st_size > 0


EXTRACT = _extract_windows       # tests replace this


def cache_dir() -> Path:
    return storage.DATA / "cache" / "app-icons"


def icon_path(key: str, now: float | None = None) -> Path | None:
    """The cached PNG for this app, made on first use. None when the app or its icon is not available."""
    key = str(key or "").strip().casefold()
    if not key or len(key) > 120:
        return None
    base = cache_dir() / hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    png, miss = base.with_suffix(".png"), base.with_suffix(".none")
    if png.is_file():
        return png
    src = source_for(key)
    if src is None or not Path(str(src)).exists():     # not installed: nothing to extract and nothing to remember
        return None
    now = time.time() if now is None else now
    try:
        if miss.is_file() and now - miss.stat().st_mtime < RETRY_AFTER:
            return None
    except OSError:
        pass
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:                                      # one extraction per icon, and never under the global lock
        if png.is_file():
            return png
        png.parent.mkdir(parents=True, exist_ok=True)
        tmp = png.with_suffix(".tmp")
        ok = False
        try:
            ok = bool(EXTRACT(Path(str(src)), tmp)) and tmp.is_file()
            if ok:
                tmp.replace(png)
        except Exception:  # noqa: BLE001  (an app that will not give up its icon keeps its letter mark)
            ok = False
        finally:
            tmp.unlink(missing_ok=True)
        if ok:
            miss.unlink(missing_ok=True)
            return png
        try:
            miss.write_text("", encoding="utf-8")
            os.utime(miss, (now, now))
        except OSError:
            pass
        return None


# ------------------------------------------------------------------ pinned apps (per person)
def _file(uid: str) -> Path:
    return storage.user_dir(uid) / "launcher.json"


def pinned(uid: str) -> list[str]:
    try:
        names = json.loads(_file(uid).read_text(encoding="utf-8")).get("pinned", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [n for n in names if isinstance(n, str)][:MAX_PINNED]


def _canonical(name: str) -> str | None:
    low = str(name or "").strip().casefold()
    return low if low in installed() else None


def pin(uid: str, name: str) -> list[str]:
    """Pin an installed app. ValueError says why not (not installed, or the row is full)."""
    canon = _canonical(name)
    if canon is None:
        raise ValueError("That app isn't installed on this computer.")
    with _lock:
        names = pinned(uid)
        if canon in names:
            return names
        if len(names) >= MAX_PINNED:
            raise ValueError("Quick Launch is full. Remove one first.")
        names.append(canon)
        storage._write(_file(uid), {"pinned": names})
        return names


def unpin(uid: str, name: str) -> list[str]:
    low = str(name or "").strip().casefold()
    with _lock:
        names = [n for n in pinned(uid) if n != low]
        storage._write(_file(uid), {"pinned": names})
        return names
