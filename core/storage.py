"""Small JSON stores in data/. Writes are atomic so a crash mid-save can't
corrupt a file.

load/save/path are scoped to the person Jarvis is currently serving
(data/users/<id>/...). Names starting with "household/" go to the shared
data/household/ folder; load_global/save_global are for app-wide files
(settings, usage, the user list).
"""
import json
import os
import threading
from pathlib import Path
from core.fileio import replace_atomic

from core.config import ROOT

DATA = ROOT / "data"


def live_root(root=ROOT) -> Path:
    """The checkout whose data/ and .env are the live ones. A git worktree (<live>/.worktrees/<name>) has its own empty data/,
    so tools that must read live Jarvis's data (the practice watcher) go up to the checkout that owns the worktree.
    JARVIS_LIVE_ROOT overrides."""
    env = os.getenv("JARVIS_LIVE_ROOT")
    if env:
        return Path(env)
    root = Path(root)
    return root.parent.parent if root.parent.name == ".worktrees" else root
_lock = threading.Lock()


def _resolve(name: str) -> Path:
    if name.startswith("household/"):
        return DATA / name
    from core import users

    return DATA / "users" / users.current() / name


def user_dir(uid: str | None = None) -> Path:
    from core import users

    return DATA / "users" / (uid or users.current())


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        path.replace(path.with_suffix(".corrupt.json"))  # keep it for inspection, start fresh
        return default


def _write(path: Path, value) -> None:
    """The same bytes on every machine (LF line endings, never the platform's) and no write at all when nothing
    changed. data/ is shared between the PC and the laptop over Syncthing: both rewrote an unchanged
    household/autolearn/queue.json at the same moment (CRLF here, LF there), so every restart and every 6-hourly
    study run left a conflict copy on each machine (live, 2026-10-30)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
    with _lock:
        try:
            if path.read_bytes() == data:
                return
        except OSError:
            pass
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        replace_atomic(tmp, path)


def load(name: str, default):
    return _read(_resolve(name), default)


def save(name: str, value) -> None:
    _write(_resolve(name), value)


def path(name: str) -> Path:
    p = _resolve(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_global(name: str, default):
    return _read(DATA / name, default)


def save_global(name: str, value) -> None:
    _write(DATA / name, value)


def migrate_single_user() -> None:
    """One-time move of data from before Jarvis knew several people into the
    owner's folder (data/users/owner/)."""
    owner = DATA / "users" / "owner"
    if owner.exists():
        return
    owner.mkdir(parents=True)
    personal = ["memory.json", "notes.json", "reminders.json", "tutor.json", "price_watch.json",
                "voiceprint.npy", "scheduler.json", "fashion", "lessons"]
    for name in personal:
        src = DATA / name
        if src.exists():
            src.replace(owner / name)
    settings = _read(DATA / "settings.json", {})
    if "telegram_owner" in settings or "voice_lock" in settings:
        registry = _read(DATA / "users.json", {"users": {}})
        user = registry["users"].setdefault("owner", {"name": "", "role": "owner",
                                                      "titles": {"en": "sir", "ht": "mesye", "fr": "monsieur"}})
        if settings.get("telegram_owner"):
            user["telegram"] = settings.pop("telegram_owner")
        _write(DATA / "users.json", registry)
        _write(DATA / "settings.json", settings)
