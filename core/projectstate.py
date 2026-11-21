"""What state each of his projects is in NOW, so his background is told as what it is: the past.

Why (2026-11-21): his memory says "The owner is working on a project called 'Shadow World'" (saved Nov 9) and "My big project is Living
Shadow World" (Nov 16), and the brain told them as present fact: "since you're working on Shadow World", "Shadow World's in-game test just
finished: your main character walks". He put all game work ON HOLD on 2026-11-19 (his decision, to Claude: it was never said to Jarvis, so
it is seeded here, like core.commitments seeds his goals). Output filters chased the wordings; this fixes the facts the model reads:

  - every background line that names a project (core.recall.render, tools.memory._core) carries its state: "[ON HOLD since 2026-11-19,
    his own decision; nothing is being worked on]";
  - "How's my game coming along?" / "What's the status of Shadow World?" is answered here in code from that state, never guessed;
  - his word changes it, by the project's name: "Shadow World is back on" / "resume work on Living Shadow World" / "put Shadow World
    on hold" (owner only).

Storage: data/users/<id>/projectstate-<machine>.json {"v": 1, "projects": {id: record}}; one writer per machine, readers merge both
machines' files and the newer "updated" wins (as core.commitments). A record there overrides the seed.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import date

from core import storage

STATES = ("active", "on_hold", "dropped", "done")
MACHINES = ("pc", "server")
_lock = threading.RLock()

# The owner's projects whose state he decided outside Jarvis. "names" are matched in his facts and lines; "refs" only in his
# own question about the state ("how's my game coming along?").
SEED = {"owner": {"lsw": {"id": "lsw", "name": "Living Shadow World", "names": ["living shadow world", "shadow world",
                                                                                    "shadow vanguard", "lsw"],
                          "refs": ["my game", "our game"], "state": "on_hold", "since": "2026-11-19",
                          "by": "owner", "spoken": "the game", "updated": 0.0}}}


def _machine() -> str:
    from core import thread
    return thread.machine()


def _uid() -> str:
    from core import users
    return users.current()


_CACHE: dict = {}
CACHE_S = 2.0                   # the background renders many lines in one go (tools.memory._core trims in a loop): files read once


def projects(uid: str | None = None) -> dict:
    """Every project of this person: the seed, then both machines' saved records (the newer wins)."""
    uid = uid or _uid()
    key = (uid, str(storage.DATA))
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return {k: dict(v) for k, v in hit[1].items()}
    out = _read(uid)
    _CACHE[key] = (time.monotonic(), out)
    return {k: dict(v) for k, v in out.items()}


def _read(uid: str) -> dict:
    out = {k: dict(v) for k, v in SEED.get(uid, {}).items()}
    for machine in MACHINES:
        try:
            saved = storage.load(f"projectstate-{machine}.json", {}) if uid == _uid() else {}
        except Exception:  # noqa: BLE001 - a broken file never costs the background
            saved = {}
        for pid, rec in ((saved or {}).get("projects") or {}).items():
            if isinstance(rec, dict) and rec.get("state") in STATES and float(rec.get("updated") or 0) >= float(
                    (out.get(pid) or {}).get("updated") or 0):
                out[pid] = {**(out.get(pid) or {}), **rec}
    return out


def _named(text: str, rec: dict, refs: bool = False) -> bool:
    low = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()) + " "
    words = list(rec.get("names") or []) + (list(rec.get("refs") or []) if refs else [])
    return any(f" {w} " in low for w in words)


def _since(rec: dict) -> str:
    try:
        day = date.fromisoformat(str(rec.get("since"))[:10])
        return f"{day.strftime('%B')} {day.day}"
    except (TypeError, ValueError):
        return ""


def note(text: str, uid: str | None = None) -> str:
    """The state tag for a background line that names a project not being worked on ('' for every other line)."""
    for rec in projects(uid).values():
        if rec.get("state") in ("on_hold", "dropped", "done") and _named(text, rec):
            word = {"on_hold": "ON HOLD", "dropped": "DROPPED", "done": "FINISHED"}[rec["state"]]
            when = f" since {str(rec.get('since'))[:10]}" if rec.get("since") else ""
            whose = ", his own decision" if rec.get("by") == "owner" else ""
            return f" [{rec.get('name')}: {word}{when}{whose}; nothing on it is being worked on or tested now]"
    return ""


def tagged(line: str, uid: str | None = None) -> str:
    try:
        return line + note(line, uid)
    except Exception:  # noqa: BLE001 - the line as it was
        return line


# -- his words ----------------------------------------------------------------------------------------------------------
# A question about the state that names the project ("How's Shadow World coming along?", "status of Living Shadow World").
_STATUS_Q = re.compile(r"\b(?:how(?:'s| is| are)\b[^.?!]{0,40}\b(?:coming(?: along)?|going|doing|looking|progress\w*)|"
                       r"(?:what(?:'s| is)|give me) the (?:status|state|progress|latest)\b|any (?:progress|news|updates?)\b|"
                       r"where (?:are we|am i|is it|do we stand) (?:on|with)\b|status (?:on|of)\b|update on\b)", re.I)
# "my game" alone is often the game he plays or downloads (review 2026-11-21: "How is my game doing on Steam?", "Update on my game
# download?"): only these whole questions mean the project.
_REF_Q = re.compile(r"^\W*(?:(?:hey |ok |okay )?jarvis\W+)?(?:so\W+)?(?:how(?:'s| is) (?:my|our) game (?:coming along|progressing)|"
                    r"(?:what(?:'s| is) the )?(?:status|progress) (?:on|of) (?:my|our) game|any progress on (?:my|our) game)\W*$", re.I)


def _change(text: str, rec: dict) -> str | None:
    """'active' / 'on_hold' when his line changes THIS project by its name ("Shadow World is back on", "put Living Shadow World on
    hold", "resume work on Shadow World"); never by "my game" (review 2026-11-21: "My game is back on after the update.", "Put my
    game on hold for a sec, the doorbell." changed the state), never "back on hold" taken for back on, never a question."""
    if text.rstrip().endswith("?"):
        return None
    low = re.sub(r"[^a-z0-9' ]+", " ", text.casefold())
    for name in sorted(rec.get("names") or [], key=len, reverse=True):
        n = re.escape(name)
        if re.search(rf"\b(?:put|place) (?:the )?{n}(?: project)? on hold\b|\b{n}(?: project)? (?:is|goes|stays) on hold\b|"
                     rf"\b(?:shelve|pause work on|stop working on) (?:the )?{n}\b", low):
            return "on_hold"
        if re.search(rf"\b{n}(?: project)? (?:is|are) back on\b(?! hold)|\b(?:resume|restart) work(?:ing)? on (?:the )?{n}\b|"
                     rf"\btake (?:the )?{n} off hold\b|\bstart working on (?:the )?{n} again\b|\bpick (?:the )?{n} back up\b", low):
            return "active"
    return None


def set_state(pid: str, state: str, uid: str | None = None) -> dict:
    if state not in STATES:
        raise ValueError(state)
    with _lock:
        rec = dict(projects(uid).get(pid) or {"id": pid, "name": pid})
        rec.update(state=state, since=date.today().isoformat(), by="owner", updated=time.time())
        file = f"projectstate-{_machine()}.json"
        data = storage.load(file, {}) or {}
        data.setdefault("projects", {})[pid] = rec
        data["v"] = 1
        storage.save(file, data)
        _CACHE.clear()
        return rec


def _playing() -> bool:
    try:
        from core import acquire
        return bool(acquire.running_game())
    except Exception:  # noqa: BLE001 - unknown: "my game" may be the one he plays, so it is not taken
        return True


_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*")


def handle(said: str, title: str = "sir") -> str | None:
    """His question about a project's state, or his word that changes it: the answer in code. None for every other line."""
    text = " ".join(_CHANNEL.sub("", said or "").replace("’", "'").split())   # the phone path keeps its channel note
    if not text or len(text) > 200:
        return None
    from core import users
    if users.current() != "owner" and users.get(users.current()).get("role") != "owner":
        return None                                  # his projects are his: the household never changes or hears them
    for pid, rec in projects().items():
        name, spoken = rec.get("name") or pid, rec.get("spoken") or rec.get("name") or pid
        by_name = _named(text, rec)
        if by_name:
            change = _change(text, rec)
            if change == "active" and rec.get("state") != "active":
                set_state(pid, "active")
                return f"Got it: {name} is back on. I'll treat it as current from now on."
            if change == "on_hold" and rec.get("state") != "on_hold":
                set_state(pid, "on_hold")
                return f"Got it: {name} is on hold until you say it's back on."
        asked = (by_name and _STATUS_Q.search(text)) or (not by_name and _REF_Q.match(text) and not _playing())
        if asked and rec.get("state") in ("on_hold", "dropped", "done"):
            when = _since(rec)
            if rec["state"] == "on_hold":
                return (f"It's on hold, {title}: you paused {spoken} on {when}, so nothing's been built or tested since. "
                        f"Say \"{name} is back on\" when you want to pick it up." if when else
                        f"It's on hold by your call, so nothing's been built or tested since.")
            return f"{name} is {'finished' if rec['state'] == 'done' else 'dropped'}, by your call{(' on ' + when) if when else ''}."
    return None
