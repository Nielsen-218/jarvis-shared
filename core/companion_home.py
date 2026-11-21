"""Local miniature-home state; no webview, brain, scheduler or network dependency.

Integration: load_home(path=None) returns a fresh validated schema-1 snapshot.
save_home(state, path=None) validates then atomically replaces the JSON file;
update_home(patch, path=None) recursively merges dictionaries, replaces lists,
validates and saves, returning the saved snapshot. Serialize read/modify/write
through this module (thread-safe in one process). Corrupt/missing files load as
defaults without overwriting the original; malformed writes raise HomeError.
Default storage is data/companion-home.json; JARVIS_COMPANION_DATA_DIR overrides
the directory (evaluated on every call). Explicit path takes precedence.
Furniture positions are metric x/y/z in the .65m-wide room; floor y=0.

Send the whole snapshot to home.js:setState after each successful update. The
parent owns API/menu wiring, room enter/exit, visibility and presence leases.
Routine entries are daily local HH:MM boundaries; renderer selects the latest
boundary cyclically while enabled. No timer or background process is started.
Scrapbook text is local plain text; never inject it as HTML or expose publicly.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

HOME_PATH = Path(__file__).resolve().parent.parent / "data" / "companion-home.json"
MAX_HOME_BYTES = 262144  # fits all bounded text fields even with four-byte Unicode
ROOMS = ("bedroom", "kitchen", "workshop", "fitness", "reading")
ACTIONS = ("idle", "sleep", "wake", "cook", "tinker", "exercise", "read", "pet", "scrapbook")
ACTIVITY_ROOMS = {"sleep": "bedroom", "wake": "bedroom", "cook": "kitchen",
                  "tinker": "workshop", "exercise": "fitness", "read": "reading"}
FURNITURE = {
    "bedroom": {"bed": [-.45, 0, -.1], "nightstand": [.45, 0, -.55], "wardrobe": [.9, 0, -.6]},
    "kitchen": {"counter": [-.5, 0, -.65], "fridge": [.8, 0, -.65], "table": [.15, 0, .4]},
    "workshop": {"bench": [0, 0, -.6], "stool": [.55, 0, .3], "shelf": [-.95, 0, -.6]},
    "fitness": {"mat": [-.3, 0, .2], "weights": [.75, 0, -.5]},
    "reading": {"chair": [-.4, 0, .1], "bookcase": [.8, 0, -.65], "table": [.4, 0, .35]},
}
FURNITURE = {room: {name: [round(v * .24, 6) for v in pos] for name, pos in items.items()}
             for room, items in FURNITURE.items()}
_LOCK = threading.RLock()


class HomeError(ValueError):
    """Invalid home data (no file was changed)."""


def default_home():
    return {"schema": 1, "room": "bedroom", "activity": "idle", "accent": "#63c9c0",
            "lamp": True, "furniture": {
                room: {name: {"position": list(pos), "rotation": 0}
                       for name, pos in items.items()} for room, items in FURNITURE.items()},
            "inventory": [], "pet": {"enabled": True, "name": "Pip", "color": "#e9b76c"},
            "scrapbook": [], "routines": {"enabled": False, "entries": []}}


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise HomeError(f"{label}: expected fields {sorted(keys)}")


def _text(value, limit, label):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 for c in value):
        raise HomeError(f"{label}: expected 1..{limit} plain text characters")


def _number(value, lo, hi):
    return type(value) in (int, float) and lo <= value <= hi and math.isfinite(value)


def _color(value):
    return isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None


def validate_home(value):
    """Reject unknown fields, nonfinite geometry and unbounded collections."""
    _object(value, default_home(), "home")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise HomeError("unsupported home schema")
    if value["room"] not in ROOMS or value["activity"] not in ACTIONS:
        raise HomeError("unknown room/activity")
    if value["activity"] in ACTIVITY_ROOMS and ACTIVITY_ROOMS[value["activity"]] != value["room"]:
        raise HomeError("activity belongs to a different room")
    if not _color(value["accent"]) or type(value["lamp"]) is not bool:
        raise HomeError("invalid accent/lamp")
    _object(value["furniture"], ROOMS, "furniture")
    for room, items in FURNITURE.items():
        _object(value["furniture"][room], items, room)
        for name in items:
            item = value["furniture"][room][name]
            _object(item, ("position", "rotation"), name)
            p = item["position"]
            if (not isinstance(p, list) or len(p) != 3 or
                    not all(_number(v, -.24, .24) for v in p) or p[1] != 0 or abs(p[2]) > .168 or
                    not _number(item["rotation"], -math.pi, math.pi)):
                raise HomeError("furniture requires bounded floor x/z, y=0 and rotation radians")
    inv = value["inventory"]
    if not isinstance(inv, list) or len(inv) > 12:
        raise HomeError("inventory limit is 12")
    ids = []
    for item in inv:
        _object(item, ("id", "kind", "color"), "collectible")
        _text(item["id"], 40, "collectible id")
        if item["kind"] not in ("star", "crystal", "badge") or not _color(item["color"]):
            raise HomeError("invalid collectible")
        ids.append(item["id"])
    if len(set(ids)) != len(ids):
        raise HomeError("duplicate collectible id")
    pet = value["pet"]
    _object(pet, ("enabled", "name", "color"), "pet")
    _text(pet["name"], 32, "pet name")
    if type(pet["enabled"]) is not bool or not _color(pet["color"]):
        raise HomeError("invalid pet")
    book = value["scrapbook"]
    if not isinstance(book, list) or len(book) > 24:
        raise HomeError("scrapbook limit is 24")
    for page in book:
        _object(page, ("title", "text", "color"), "scrapbook page")
        _text(page["title"], 80, "page title")
        _text(page["text"], 500, "page text")
        if not _color(page["color"]):
            raise HomeError("invalid page color")
    routines = value["routines"]
    _object(routines, ("enabled", "entries"), "routines")
    if type(routines["enabled"]) is not bool or not isinstance(routines["entries"], list) or len(routines["entries"]) > 12:
        raise HomeError("invalid routines (limit 12)")
    times = []
    for entry in routines["entries"]:
        _object(entry, ("time", "room", "activity"), "routine")
        if (not isinstance(entry["time"], str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", entry["time"]) or
                entry["room"] not in ROOMS or entry["activity"] not in ACTIONS or
                ACTIVITY_ROOMS.get(entry["activity"], entry["room"]) != entry["room"]):
            raise HomeError("invalid routine time/room/activity")
        times.append(entry["time"])
    if len(set(times)) != len(times):
        raise HomeError("duplicate routine time")
    return copy.deepcopy(value)


def _path(path):
    if path is not None:
        return Path(path)
    directory = os.environ.get("JARVIS_COMPANION_DATA_DIR")
    return Path(directory) / "companion-home.json" if directory else HOME_PATH


def load_home(path=None):
    with _LOCK:
        try:
            target = _path(path)
            if target.stat().st_size > MAX_HOME_BYTES:
                return default_home()
            return validate_home(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return default_home()


def save_home(state, path=None):
    snapshot = validate_home(state)
    target = _path(path)
    with _LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                             prefix=target.name + ".", suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                json.dump(snapshot, f, ensure_ascii=False, allow_nan=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            from core.fileio import replace_atomic
            replace_atomic(temporary,target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return snapshot


def update_home(patch, path=None):
    """Dictionary patch; lists replaced. Room/activity must be compatible."""
    if not isinstance(patch, dict):
        raise HomeError("patch must be an object")

    def merge(base, changes):
        for key, item in changes.items():
            if key not in base:
                raise HomeError(f"unknown field: {key}")
            if isinstance(base[key], dict) and isinstance(item, dict):
                merge(base[key], item)
            else:
                base[key] = copy.deepcopy(item)

    with _LOCK:
        state = load_home(path)
        merge(state, patch)
        return save_home(state, path)
