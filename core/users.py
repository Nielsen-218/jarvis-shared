"""The people Jarvis serves. Each person has private data in data/users/<id>/;
shared household data lives in data/household/.

Who's being served is per thread: the brain sets it for the request it's
answering (from voice ID or from whose phone sent the message), and every
storage read/write in that request then goes to that person's folder.
"""
import copy
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from core import storage

FILE = "users.json"  # global (not per person)
DEFAULT_TITLES = {"en": "sir", "ht": "mesye", "fr": "monsieur"}
_local = threading.local()


# users.json is shared by Syncthing and read-modify-written by BOTH machines (docs/mission/audit-cross-device.md). Syncthing
# is whole-file last-writer-wins: when the PC and the laptop write close together one write is kept and the other is left as
# users.sync-conflict-<date>-<time>-<id>.json, so a pairing or push subscription written on the losing side used to vanish.
#
# Design (no network between the machines at write time): a deterministic MERGE, applied on every write and on read.
#   * People are unioned by id; a person's devices are unioned by token_hash (the stable identity of a pairing).
#   * A removal leaves a tombstone under the top-level key "tombstones" ({"devices": {token_hash: iso-time}}); a device
#     whose hash has a tombstone is dropped from every merge, so a removed or revoked token never comes back.
#   * A device's push subscription is the one with the later "push_at" (a unix-less ISO stamp set by set_push); a tie keeps
#     the file being merged INTO.
#   * Every conflict copy that carries its own "sync_since" stamp (written by this scheme, so it is judged by content, never
#     by file times or clocks) is merged into users.json, then moved (not deleted) to data/users-conflicts-merged/. A copy
#     without the stamp predates this scheme (it can hold devices removed before tombstones existed), so it is only archived.
#   * Writes are atomic (storage.save_global: temp file in the same folder + replace). An unreadable or half-synced users.json
#     is never replaced by an empty registry: the last good copy (memory, then machine-state/users, then a valid conflict
#     copy) is used and nothing is written until the file reads again.
# Compatibility: only NEW top-level keys ("tombstones", "sync_since") and a new per-device key ("push_at") are added. The
# deployed reader looks at data["users"] only and its writer saves the dict it loaded, so it keeps the new keys; both
# directions are tested. A deployed (old) writer is not tombstone aware: its stale write is repaired by the merge, but an old
# machine's unpair is only seen as an absence (which the merge never treats as removal) until both machines run this code.
_CONFLICT_GLOB = "users.sync-conflict-*"
_CONFLICT_EVERY = 60.0
_ARCHIVE_DIR = "users-conflicts-merged"
_TOMB_KEEP = 500
_conflicts_seen: set = set()
_conflicts_checked = -1e9
_unreadable_said = False
_lock = threading.RLock()
_last_good: dict = {}               # data folder -> the last registry that parsed (in memory)


def _say(msg: str) -> None:
    if sys.stderr is not None:      # pythonw with no log: nowhere to say it (and never fall back to stdout: manage.py pair is parsed)
        print(msg, file=sys.stderr)


def _state_file() -> Path:
    base = Path(os.getenv("JARVIS_USERS_STATE_DIR", str(storage.DATA.parent / "machine-state" / "users")))
    return base / "users.lastgood.json"


def _parse(path: Path):
    """The registry in `path`, or None when it is missing, unreadable, half written or not a registry."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("users"), dict) and data["users"]:
        return data
    return None


def _copies() -> list:
    try:
        return sorted(storage.DATA.glob(_CONFLICT_GLOB))
    except OSError:
        return []


def _stamp() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _push_stamp(prev) -> str:
    """Later than the stamp it replaces even when this machine's clock is behind the machine that wrote that one (a
    Lamport-style bump), so a change made after seeing the other side's always wins."""
    now = _stamp()
    try:
        bumped = (datetime.fromisoformat(prev) + timedelta(microseconds=1)).isoformat(timespec="microseconds")
    except (TypeError, ValueError):
        return now
    return max(now, bumped)


def _tombs(reg: dict) -> dict:
    t = reg.get("tombstones")
    return t["devices"] if isinstance(t, dict) and isinstance(t.get("devices"), dict) else {}


def merge(base: dict, other: dict) -> dict:
    """`other` merged INTO `base` (a new dict; neither argument is changed). Union of people, devices (by token_hash) and
    tombstones; tombstoned devices are dropped. The same inputs always give the same output."""
    out = copy.deepcopy(base)
    tomb = dict(_tombs(out))
    for h, ts in _tombs(other).items():
        tomb[h] = max(str(ts), str(tomb.get(h, "")))
    for key, val in other.items():
        if key not in ("users", "tombstones", "sync_since"):
            out.setdefault(key, copy.deepcopy(val))
    stamps = [s for s in (base.get("sync_since"), other.get("sync_since")) if isinstance(s, str)]
    if stamps:
        out["sync_since"] = min(stamps)
    people = out.setdefault("users", {})
    for uid, theirs in other.get("users", {}).items():
        if not isinstance(theirs, dict):
            continue
        mine = people.get(uid)
        if mine is None:
            people[uid] = copy.deepcopy(theirs)
            continue
        for k, v in theirs.items():
            if k == "devices":
                continue
            if k not in mine or (not mine[k] and v) or (k == "titles" and mine[k] == DEFAULT_TITLES and v != DEFAULT_TITLES):
                mine[k] = copy.deepcopy(v)          # a blank or default field (a fresh machine's owner) takes the real one
        have = {d.get("token_hash"): d for d in mine.get("devices", []) if isinstance(d, dict)}
        for d in theirs.get("devices", []):
            if not isinstance(d, dict) or not d.get("token_hash"):
                continue
            ours = have.get(d["token_hash"])
            if ours is None:
                mine.setdefault("devices", []).append(copy.deepcopy(d))
            elif ours.get("push") != d.get("push") and str(d.get("push_at", "")) > str(ours.get("push_at", "")):
                # push_at is a logical stamp (see _push_stamp): a missing one (an old writer) counts as oldest
                ours["push"], ours["push_at"] = copy.deepcopy(d.get("push")), d.get("push_at")
    if tomb:
        out["tombstones"] = {"devices": dict(sorted(tomb.items(), key=lambda kv: kv[1])[-_TOMB_KEEP:])}
        tomb = out["tombstones"]["devices"]
    for person in people.values():
        if isinstance(person, dict) and isinstance(person.get("devices"), list):
            live = [d for d in person["devices"] if not (isinstance(d, dict) and d.get("token_hash") in tomb)]
            person["devices"] = sorted(live, key=lambda d: (str(d.get("added", "")), str(d.get("id", ""))))
    return out


def _archive(path: Path) -> None:
    folder = storage.DATA / _ARCHIVE_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / path.name
        n = 2
        while target.exists():
            target, n = folder / f"{path.stem}.{n}{path.suffix}", n + 1
        path.replace(target)
    except OSError:
        pass                         # the other machine already moved it, or it is locked: tried again on a later pass


def _new_registry() -> dict:
    return {"users": {"owner": {"name": "", "titles": dict(DEFAULT_TITLES), "role": "owner"}}}


def _load(merge_copies: bool) -> tuple[dict, bool]:
    """(registry, trustworthy). trustworthy is False when users.json could not be read and a fallback is in use: the
    caller may read from it but must not write it back over the real file."""
    global _unreadable_said
    path = storage.DATA / FILE
    key = str(storage.DATA)
    reg = _parse(path)
    trusted = reg is not None
    if reg is None:
        if not path.exists() and not _copies():
            return _new_registry(), True                     # first run on this machine
        for cand in [_last_good.get(key), _parse(_state_file())] + [_parse(c) for c in _copies()]:
            if cand:
                reg = cand
                break
        if not _unreadable_said:
            _unreadable_said = True
            _say(f"[users] {FILE} is unreadable (half-synced?): using the last good copy and not writing until it reads again.")
        if reg is None:
            return _new_registry(), False
        return copy.deepcopy(reg), False
    _unreadable_said = False
    if merge_copies:
        reg = _merge_copies(reg, path)
    _last_good[key] = copy.deepcopy(reg)
    return reg, trusted


def _merge_copies(reg: dict, path: Path) -> dict:
    copies = _copies()
    if not copies:
        return reg
    since = reg.get("sync_since")
    changed = False
    if not isinstance(since, str):
        since, changed = _stamp(), True                      # the scheme starts now: every copy that exists is older
        reg["sync_since"] = since
    for c in copies:
        other = _parse(c)
        if other is None and not c.exists():
            continue
        if other is None:
            if c.name not in _conflicts_seen:
                _conflicts_seen.add(c.name)
                _say(f"[users] conflict copy {c.name} is unreadable: left in place.")
            continue
        # No clocks: a copy written by this scheme carries its own "sync_since" and is merged whatever its file time (a
        # removal in it is guarded by tombstones); one without it predates the scheme and is only archived.
        fresh = isinstance(other.get("sync_since"), str)
        if fresh:
            merged = merge(reg, other)
            changed = changed or merged != reg
            reg = merged
        _say(f"[users] conflict copy {c.name} {'merged into' if fresh else 'is older than'} {FILE} and archived.")
        _archive(c)
    if changed:
        _write(reg)
    return reg


def _write(reg: dict) -> None:
    storage.save_global(FILE, reg)
    _last_good[str(storage.DATA)] = copy.deepcopy(reg)
    try:
        state = _state_file()
        state.parent.mkdir(parents=True, exist_ok=True)
        tmp = state.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg), encoding="utf-8")
        os.replace(tmp, state)
    except OSError:
        pass


def _registry() -> dict:
    global _conflicts_checked
    with _lock:
        now = time.monotonic()
        check = now - _conflicts_checked >= _CONFLICT_EVERY
        if check:
            _conflicts_checked = now
        reg, trusted = _load(merge_copies=check)
        if trusted and not _parse(storage.DATA / FILE):
            _write(reg)                                      # first run only: never over an unreadable file
        return reg


def _commit(mutate) -> None:
    """Re-read (merging any conflict copy), apply `mutate(registry)`, and write atomically. No write when nothing
    changed, and none at all while users.json is unreadable."""
    with _lock:
        reg, trusted = _load(merge_copies=True)
        before = copy.deepcopy(reg)
        mutate(reg)
        if reg == before:
            return
        if not trusted:
            raise RuntimeError(f"{FILE} is unreadable right now; nothing was written. Try again in a moment.")
        reg.setdefault("sync_since", _stamp())
        _write(reg)


def _set_devices(reg: dict, uid: str, devices: list) -> None:
    """Replace a person's device list; a device that was there and is not in the new list gets a tombstone, and a push
    subscription that changed gets a push_at stamp."""
    person = reg["users"][uid]
    old = {d.get("token_hash"): d for d in person.get("devices", []) if isinstance(d, dict)}
    new_hashes = {d.get("token_hash") for d in devices if isinstance(d, dict)}
    gone = [h for h in old if h and h not in new_hashes]
    if gone:
        t = dict(_tombs(reg))
        t.update({h: _stamp() for h in gone})
        reg["tombstones"] = {"devices": t}
    stamped = []
    for d in devices:
        d = dict(d)
        prev = old.get(d.get("token_hash"))
        if prev is not None and prev.get("push") != d.get("push"):
            d["push_at"] = _push_stamp(prev.get("push_at"))
        elif prev is not None and "push_at" in prev and "push_at" not in d:
            d["push_at"] = prev["push_at"]
        stamped.append(d)
    person["devices"] = stamped


def mutate_devices(uid: str, fn) -> None:
    """fn(devices_list) -> the new list (or None to keep it), run on the freshest merged registry under the lock."""
    def go(reg):
        if uid not in reg["users"]:
            raise KeyError(uid)
        cur = copy.deepcopy(reg["users"][uid].get("devices", []))
        new = fn(cur)
        _set_devices(reg, uid, cur if new is None else new)
    _commit(go)


def all_users() -> dict:
    return _registry()["users"]


def get(uid: str) -> dict:
    return all_users().get(uid) or all_users()["owner"]


def current() -> str:
    return getattr(_local, "uid", "owner")


def set_current(uid: str) -> None:
    _local.uid = uid if uid in all_users() else "owner"


class acting_as:
    """with acting_as('wife'): ... -> storage goes to her folder inside the block."""

    def __init__(self, uid: str):
        self.uid = uid

    def __enter__(self):
        self.prev = current()
        set_current(self.uid)

    def __exit__(self, *exc):
        set_current(self.prev)


def title(uid: str | None = None, lang: str = "en") -> str:
    titles = get(uid or current()).get("titles", DEFAULT_TITLES)
    return titles.get(lang) or titles.get("en", "sir")


def add(name: str, titles: dict, role: str = "member") -> str:
    out = []

    def go(reg):
        uid = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "member"
        base, n = uid, 2
        while uid in reg["users"] and reg["users"][uid].get("name", "").lower() != name.strip().lower():
            uid, n = f"{base}-{n}", n + 1
        person = reg["users"].get(uid, {})
        person.update({"name": name.strip(), "titles": {**DEFAULT_TITLES, **titles}, "role": role})
        reg["users"][uid] = person            # an existing person keeps their devices
        out.append(uid)
    _commit(go)
    return out[0]


def find(name_or_id: str) -> str | None:
    key = name_or_id.strip().lower()
    for uid, u in all_users().items():
        if key in (uid, u.get("name", "").lower(), u.get("role", "")):
            return uid
    return None


def update(uid: str, **fields) -> None:
    def go(reg):
        person = reg["users"][uid]
        for k, v in fields.items():
            if k == "devices":
                _set_devices(reg, uid, v)       # removals become tombstones, so they stay removed on both machines
            else:
                person[k] = v
    _commit(go)


def by_telegram(chat_id: int) -> str | None:
    for uid, u in all_users().items():
        if u.get("telegram") == chat_id:
            return uid
    return None
