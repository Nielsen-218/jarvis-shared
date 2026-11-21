"""Bounded local behavior preferences, NOT brain memory or user psychology.

Parent contract:
  result = record_event('play', now=time.time(), action='play', path=None)
  # result = {snapshot, mood, traits, unlocked, new_unlocks, secret_actions}
  # Feed mood into existing Mood; merge unlocked home-compatible collectibles
  # by id into home.inventory (respect its 12-item limit). No automatic home IO.
  # onEvent is an alias for record_event, for event bridge adapters.
  decision = director_choice(result['snapshot'], unix_seconds, seed=123,
      day='2026-10-24', minute=22*60, home=load_home(),
      available_surfaces=[surface.id for surface in live_surfaces])
  # decision = {action, reason, mood, surface_id, home, record}
  # Existing arbiter/presence leases decide whether to execute. If completed,
  # acknowledge decision['record'] with record_event(**record, now=...).
  # A suppressed/cancelled suggestion is NEVER an observed interaction.

EVENTS lists accepted observed facts. Optional action is an animation category,
not a skill name or text. surface_id must match existing desktop_habitat IDs;
no window titles, paths, transcripts, tool results, user traits or identity.
Good interactions: hold/drop/play/skill_success/surface (surface means a
confirmed successful landing/owner-selected spot, NOT a polling observation).
correction suppresses its action/surface for 24 hours, then retains a lower
weight; skill_fail lowers confidence without treating failure as user dislike.
unlock conditions depend exclusively on recorded counts; no idle-time unlocks.
The earned animation key is secret_shuffle (parent-owned renderer clip).
Ordinary shuffle remains a common animation; its corrections are independent.

now is finite Unix SECONDS, never performance.now() milliseconds. For local
daily behavior parent passes ISO day and minute-of-day explicitly; pure
director otherwise derives UTC day/minute from now, never reads wall clock.
record_event defaults now to time.time(), day to UTC. Daily acknowledgements
are idempotent for retained 14 days. Backdated events and backwards clocks are
rejected rather than inflating counts; counters saturate at MAX_COUNT.

load_life returns fresh schema-1 defaults for missing/corrupt/oversized files,
without overwriting them. Atomic replace + fsync, thread-safe read/modify/write
within one process; parent must serialize writers across processes. Default
data root reuses companion.data_directory() / JARVIS_COMPANION_DATA_DIR. No
background loops, tools, registries, network, LLM, telemetry or home writes.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile
import threading
import time

from core.companion_home import validate_home

EVENTS = ("hold", "drop", "play", "skill_success", "skill_fail", "correction", "surface",
          "greeting", "routine", "checkpoint")
ACTIONS = ("idle", "wave", "look_around", "curious", "ponder", "sit", "read", "stretch",
           "salute", "shuffle", "secret_shuffle", "double_take", "play", "fly", "wander", "hologram")
GOOD = frozenset({"hold", "drop", "play", "skill_success", "surface"})
MAX_COUNT, MAX_HISTORY, MAX_SURFACES, MAX_DAYS = 1_000_000, 64, 16, 14
MAX_BYTES = 131072
CORRECTION_COOLDOWN = 86400
_LOCK = threading.RLock()
_SURFACE = re.compile(r"(?:taskbar:-?\d{1,7}:-?\d{1,7}:(?:left|right|top|bottom)|"
                      r"search:taskbar:-?\d{1,7}:-?\d{1,7}:(?:left|right|top|bottom)|"
                      r"window:\d{1,20}|folder:[a-f0-9]{16})")


class LifeError(ValueError):
    """Unsupported or malformed local life data/event."""


def default_life():
    return {"schema": 1, "counts": dict.fromkeys(EVENTS, 0), "last_at": 0,
            "history": [], "last_good": None, "last_correction": None,
            "surfaces": {}, "actions": {}, "daily": []}


def _number(n, lo=0, hi=253402300799):
    return type(n) in (int, float) and lo <= n <= hi and math.isfinite(n)


def _count(n):
    return type(n) is int and 0 <= n <= MAX_COUNT


def _keys(v, fields):
    return isinstance(v, dict) and set(v) == set(fields)


def _day(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return False
    try:
        return date.fromisoformat(v).isoformat() == v
    except ValueError:
        return False


def _clock(v):
    return isinstance(v, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", v) is not None


def _surface(v):
    return isinstance(v, str) and len(v) <= 96 and _SURFACE.fullmatch(v) is not None


def _event(e):
    return (_keys(e, ("kind", "at", "action", "surface_id")) and e["kind"] in EVENTS and
            _number(e["at"]) and (e["action"] is None or e["action"] in ACTIONS) and
            (e["surface_id"] is None or _surface(e["surface_id"])))


def validate_life(value):
    """Strict bounded schema; no extension fields that could hide transcripts."""
    if not _keys(value, default_life()) or type(value["schema"]) is not int or value["schema"] != 1:
        raise LifeError("unsupported life schema")
    if not _keys(value["counts"], EVENTS) or not all(_count(v) for v in value["counts"].values()) or not _number(value["last_at"]):
        raise LifeError("invalid counts/timestamp")
    history = value["history"]
    if (not isinstance(history, list) or len(history) > MAX_HISTORY or not all(_event(e) for e in history) or
            any(e["at"] > value["last_at"] for e in history) or
            any(a["at"] > b["at"] for a, b in zip(history, history[1:]))):
        raise LifeError("invalid history")
    for field, allowed in (("last_good", GOOD), ("last_correction", {"correction"})):
        e = value[field]
        if e is not None and (not _event(e) or e["kind"] not in allowed or e["at"] > value["last_at"]):
            raise LifeError("invalid last interaction")
    for field, limit in (("surfaces", MAX_SURFACES), ("actions", len(ACTIONS))):
        mapping = value[field]
        if not isinstance(mapping, dict) or len(mapping) > limit:
            raise LifeError("too many preferences")
        for key, pref in mapping.items():
            if (not (_surface(key) if field == "surfaces" else key in ACTIONS) or
                    not _keys(pref, ("good", "corrections", "last_at", "last_correction")) or
                    not _count(pref["good"]) or not _count(pref["corrections"]) or
                    not _number(pref["last_at"]) or pref["last_at"] > value["last_at"] or
                    (pref["last_correction"] is not None and
                     (not _number(pref["last_correction"]) or pref["last_correction"] > pref["last_at"]))):
                raise LifeError("invalid preference")
    daily = value["daily"]
    if not isinstance(daily, list) or len(daily) > MAX_DAYS:
        raise LifeError("invalid daily history")
    dates = []
    for d in daily:
        if (not _keys(d, ("day", "greeting", "checkpoint", "routines")) or not _day(d["day"]) or
                type(d["greeting"]) is not bool or type(d["checkpoint"]) is not bool or
                not isinstance(d["routines"], list) or len(d["routines"]) > 12 or
                not all(_clock(v) for v in d["routines"]) or len(set(d["routines"])) != len(d["routines"])):
            raise LifeError("invalid daily acknowledgement")
        dates.append(d["day"])
    if dates != sorted(set(dates)):
        raise LifeError("duplicate/unsorted days")
    return copy.deepcopy(value)


def _path(path):
    if path is not None:
        return Path(path)
    from core.companion import data_directory
    return data_directory() / "companion-life.json"


def load_life(path=None):
    with _LOCK:
        try:
            target = _path(path)
            if target.stat().st_size > MAX_BYTES:
                return default_life()
            return validate_life(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return default_life()


def _save(state, path):
    state = validate_life(state)
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            json.dump(state, f, allow_nan=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        from core.fileio import replace_atomic
        replace_atomic(temporary,target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return state


def traits(snapshot):
    """Animation selection scores derived from observed counts, not diagnoses."""
    c = snapshot["counts"]
    good = sum(c[k] for k in GOOD)
    setbacks = c["skill_fail"] + c["correction"]
    confidence = .25 + .65 * good / (good + setbacks + 8) - .2 * setbacks / (good + setbacks + 8)
    curiosity = .2 + .4 * min(50, c["play"] + c["drop"]) / 50 + .3 * min(8, len(snapshot["surfaces"])) / 8 - .2 * min(10, c["correction"]) / 10
    return {"confidence": round(max(0, min(1, confidence)), 4),
            "curiosity": round(max(0, min(1, curiosity)), 4)}


def _unlocks(snapshot):
    c = snapshot["counts"]
    items, actions = [], []
    if c["hold"] >= 5:
        items.append({"id": "life-gentle-hands", "kind": "star", "color": "#63c9c0"})
    if c["skill_success"] >= 5:
        items.append({"id": "life-helpful", "kind": "badge", "color": "#d7ae61"})
    if c["play"] >= 10 and c["skill_success"] >= 3:
        items.append({"id": "life-secret-shuffle", "kind": "crystal", "color": "#cc88ff"})
        actions.append("secret_shuffle")  # distinct parent-owned earned clip
    return items, actions


def _result(state, previous=()):
    unlocked, actions = _unlocks(state)
    good = state["last_good"]
    correction = state["last_correction"]
    recent = next((e for e in reversed(state["history"]) if e["kind"] in GOOD | {"correction", "skill_fail"}), None)
    if recent and recent["kind"] in ("correction", "skill_fail"):
        mood = "neutral"
    elif good and (recent or not correction or good["at"] > correction["at"]):
        mood = {"play": "playful", "skill_success": "proud", "surface": "curious"}.get(good["kind"], "happy")
    else:
        mood = "neutral"
    return {"snapshot": state, "mood": mood, "traits": traits(state), "unlocked": unlocked,
            "new_unlocks": [i for i in unlocked if i["id"] not in previous], "secret_actions": actions}


def record_event(kind, *, now=None, action=None, surface_id=None, day=None, routine_time=None, path=None):
    """Record one observed event. Reject arbitrary metadata via explicit kwargs.

    greeting/checkpoint require no extra data; routine requires routine_time.
    A correction without action/surface is a global 24h quiet-play preference.
    Failures/corrections are never rewarded with unlock counts.
    """
    now = time.time() if now is None else now
    if not _number(now) or kind not in EVENTS or (action is not None and action not in ACTIONS) or (surface_id is not None and not _surface(surface_id)):
        raise LifeError("invalid event/time/action/surface")
    if day is None:
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    if not _day(day) or (kind == "routine" and not _clock(routine_time)) or (kind != "routine" and routine_time is not None):
        raise LifeError("invalid day/routine acknowledgement")
    with _LOCK:
        state = load_life(path)
        if now < state["last_at"]:
            raise LifeError("backdated event: use current Unix seconds")
        previous = {i["id"] for i in _unlocks(state)[0]}
        if kind in ("greeting", "routine", "checkpoint"):
            daily = next((d for d in state["daily"] if d["day"] == day), None)
            if daily is None:
                if state["daily"] and day < state["daily"][-1]["day"]:
                    raise LifeError("backdated daily acknowledgement")
                daily = {"day": day, "greeting": False, "checkpoint": False, "routines": []}
                state["daily"].append(daily)
                state["daily"] = state["daily"][-MAX_DAYS:]
            if kind == "routine":
                if routine_time in daily["routines"]:
                    return _result(state, previous)
                if len(daily["routines"]) >= 12:
                    raise LifeError("daily routine acknowledgement limit")
                daily["routines"].append(routine_time)
            else:
                if daily[kind]:
                    return _result(state, previous)
                daily[kind] = True
        state["counts"][kind] = min(MAX_COUNT, state["counts"][kind] + 1)
        state["last_at"] = now
        event = {"kind": kind, "at": now, "action": action, "surface_id": surface_id}
        state["history"] = (state["history"] + [event])[-MAX_HISTORY:]
        if kind in GOOD:
            state["last_good"] = dict(event)
        if kind == "correction":
            state["last_correction"] = dict(event)
        for field, key in (("surfaces", surface_id), ("actions", action)):
            if key is None or kind not in GOOD | {"correction"}:
                continue
            mapping = state[field]
            if key not in mapping:
                if field == "surfaces" and len(mapping) >= MAX_SURFACES:
                    oldest = min(mapping, key=lambda k: (mapping[k]["last_at"], k))
                    del mapping[oldest]
                mapping[key] = {"good": 0, "corrections": 0, "last_at": now, "last_correction": None}
            pref = mapping[key]
            pref["last_at"] = now
            count_key = "corrections" if kind == "correction" else "good"
            pref[count_key] = min(MAX_COUNT, pref[count_key] + 1)
            if kind == "correction":
                pref["last_correction"] = now
        return _result(_save(state, path), previous)


onEvent = record_event


def _suppressed(pref, now):
    return pref and pref["last_correction"] is not None and now - pref["last_correction"] < CORRECTION_COOLDOWN


def director_choice(snapshot, now, *, seed=0, day=None, minute=None, home=None,
                    available_surfaces=(), quiet=False, busy=False, reduced_motion=False):
    """Pure deterministic suggestion; never writes, counts events or reads clocks.

    action is an existing animation clip; home optionally contains a compatible
    {room,activity} suggestion from the EXISTING home routine definitions.
    record is None for ordinary animations. Ack other records only on completion.
    A favorite is returned only if present in available_surfaces RIGHT NOW.
    Quiet/busy wins over all daily prompts/routines and playful animations.
    """
    s = validate_life(snapshot)
    if not _number(now) or now < s["last_at"] or not isinstance(seed, (int, str)) or len(str(seed)) > 128:
        raise LifeError("director requires current Unix seconds and bounded seed")
    dt = datetime.fromtimestamp(now, timezone.utc)
    day = dt.date().isoformat() if day is None else day
    minute = dt.hour * 60 + dt.minute if minute is None else minute
    if not _day(day) or type(minute) is not int or not 0 <= minute < 1440:
        raise LifeError("invalid director day/minute")
    if not isinstance(available_surfaces, (list, tuple)) or len(available_surfaces) > 256 or not all(_surface(k) for k in available_surfaces):
        raise LifeError("invalid available surfaces")
    if any(type(v) is not bool for v in (quiet, busy, reduced_motion)):
        raise LifeError("director flags must be boolean")
    home = validate_home(home) if home is not None else None
    choice = {"action": "idle", "reason": "quiet" if quiet or busy else "ambient", "mood": "neutral",
              "surface_id": None, "home": None, "record": None}
    if quiet or busy:
        return choice
    correction = s["last_correction"]
    global_correction = (correction and correction["action"] is None and correction["surface_id"] is None and
                         now - correction["at"] < CORRECTION_COOLDOWN)
    if global_correction:
        choice["reason"] = "recent_correction"
        return choice
    favorites = [(k, p) for k, p in s["surfaces"].items() if k in available_surfaces and
                 p["good"] > p["corrections"] * 3 and not _suppressed(p, now)]
    if favorites:
        choice["surface_id"] = max(favorites, key=lambda kv: (kv[1]["good"] - kv[1]["corrections"] * 3, kv[1]["last_at"], kv[0]))[0]
    daily = next((d for d in s["daily"] if d["day"] == day), {"greeting": False, "checkpoint": False, "routines": []})
    if not daily["greeting"]:
        choice.update(action="idle" if reduced_motion or _suppressed(s["actions"].get("wave"), now) else "wave",
                      reason="daily_greeting", mood="happy", record={"kind": "greeting", "day": day})
        return choice
    if home and home["routines"]["enabled"]:
        entries = sorted(home["routines"]["entries"], key=lambda e: e["time"])
        due = [e for e in entries if int(e["time"][:2]) * 60 + int(e["time"][3:]) <= minute]
        # A pre-first-boundary carry-over is already handled by home.js; no
        # false acknowledgement of yesterday's activity in today's ledger.
        if due and due[-1]["time"] not in daily["routines"]:
            e = due[-1]
            choice.update(reason="daily_routine", home={"room": e["room"], "activity": e["activity"]},
                          record={"kind": "routine", "day": day, "routine_time": e["time"]})
            return choice
    # Require observed interactions TODAY, not lifetime achievements. Derive
    # local midnight from caller's minute-of-day without reading a clock or
    # storing a timezone/user profile. The bounded history is conservative:
    # evicted observations cannot manufacture a checkpoint.
    midnight = now - minute * 60 - now % 60
    todays_good = sum(e["kind"] in GOOD and e["at"] >= midnight for e in s["history"])
    if not daily["checkpoint"] and minute >= 18 * 60 and todays_good >= 3:
        choice.update(reason="daily_checkpoint", record={"kind": "checkpoint", "day": day})
        return choice
    if reduced_motion:
        choice["reason"] = "reduced_motion"
        return choice
    score = traits(s)
    weights = {"idle": 5, "look_around": 1 + 4 * score["curiosity"], "ponder": 2,
               "salute": 3 * score["confidence"], "curious": 3 * score["curiosity"], "sit": 2, "shuffle": 1}
    if "secret_shuffle" in _unlocks(s)[1]:
        weights["secret_shuffle"] = 1.5
    for action in list(weights):
        pref = s["actions"].get(action)
        if _suppressed(pref, now):
            del weights[action]
        elif pref:
            weights[action] *= (1 + min(10, pref["good"]) / 10) / (1 + pref["corrections"] * 2)
    if not weights:
        choice["reason"] = "corrected_actions"
        return choice
    digest = hashlib.sha256(json.dumps([seed, int(now // 300), s["counts"]], sort_keys=True).encode()).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    choice["action"] = rng.choices(list(weights), weights=list(weights.values()), k=1)[0]
    choice["mood"] = "neutral" if choice["action"] in ("idle", "sit", "ponder") else (
        "proud" if choice["action"] == "salute" else "playful" if choice["action"] in ("shuffle", "secret_shuffle") else "curious")
    choice["reason"] = "earned_secret" if choice["action"] == "secret_shuffle" else "learned_preferences"
    return choice
