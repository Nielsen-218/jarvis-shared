"""Voice front for Mark VI missions (core/missions.py): queue one, hear how they are going, cancel one, pause them all, and
answer a step that is waiting for him. The queue lives on the LAPTOP (owner's decision 2026-11-10): on the PC these talk to
the laptop's /api/missions over the same authenticated link notifications use (tools/vault._call); on the laptop they use
the store directly. Part 1 only queues and reports: the worker that carries missions out is part 3+."""
from __future__ import annotations

from core import missions
from core.words import plural
from tools.registry import tool


def _remote() -> bool:
    try:
        from tools import vault
        return bool(vault._remote())
    except Exception:  # noqa: BLE001
        return False


def _call(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    """A GET carries no body (urllib turns a GET with data into a POST)."""
    from tools import vault
    return vault._call(method, path, (body or {}) if method != "GET" else None, query)


NOT_OWNER = "Missions belong to the owner, so only he can queue, check, cancel or pause them."


def _owner() -> bool:
    """2026-11-10 review: on the PC these calls go to the laptop with the PC's own (owner) token whoever is talking, so a
    household member at the PC must never queue, read, cancel or pause the owner's missions."""
    from core import authz
    return authz.is_owner()


def _me() -> str:
    from core import users
    return users.current()


def _pick(which: str, items):
    """The mission he means: by number ('the second one'), by words of its goal, or the only one."""
    which = (which or "").strip().casefold()
    if not items:
        return None
    if not which or which in ("it", "that", "that one", "this", "the mission", "the last one", "the latest"):
        return items[0]
    nums = {"first": 0, "1": 0, "one": 0, "second": 1, "2": 1, "two": 1, "third": 2, "3": 2, "three": 2}
    for w, i in nums.items():
        if w in which.split() and i < len(items):
            return items[i]
    words = [w for w in which.split() if len(w) > 2]
    scored = sorted(items, key=lambda m: -sum(w in m["goal"].casefold() for w in words))
    return scored[0] if words and any(w in scored[0]["goal"].casefold() for w in words) else None


@tool("Queue a MISSION: something the owner hands off to be done in the background while he is away ('go do X while I'm "
      "away', 'when I'm gone, clean up my downloads', 'take care of this while I game'). Only queues it and says so: risky "
      "steps will still stop and ask him on his phone, nothing runs during a game, and nothing takes over his screen.",
      {"goal": {"type": "string", "description": "the job, in his words"}}, required=["goal"], direct=True)
def start_mission(goal: str) -> str:
    if not _owner():
        return NOT_OWNER
    try:
        m = _call("POST", "/api/missions", {"goal": goal, "for_uid": _me()})["mission"] if _remote() else missions.add(goal)
    except ValueError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the laptop that keeps the mission queue, so nothing was queued ({type(e).__name__})."
    return (f"Mission queued, sir: \"{m['goal']}\". I'll work on it in the background without touching your screen, stop and "
            "ask on your phone before anything risky, wait while you game, and report back when it's done.")


@tool("How are my missions going? Lists the queued, running, waiting and recently finished missions in plain words. "
      "Use for 'what are my missions', 'how's the mission going', 'is that job done'.", {}, direct=True)
def mission_status() -> str:
    if not _owner():
        return NOT_OWNER
    try:
        data = _call("GET", "/api/missions", query={"for_uid": _me()}) if _remote() else {"missions": missions.listing(limit=8), "paused": missions.paused(), "hold": missions.hold_reason()}
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the laptop that keeps the mission queue ({type(e).__name__})."
    items = data.get("missions") or []
    if not items:
        return "You have no missions, sir."
    held = f" and missions are on hold because {data['hold']}" if data.get("hold") else (" and missions are paused" if data.get("paused") else "")
    head = f"You have {plural(len(items), 'mission')}" + held + ": "
    return head + "; ".join(missions.spoken(m) for m in items[:6]) + "."


@tool("Cancel one of the owner's missions ('cancel that mission', 'stop the driver update mission'). Only he can cancel.",
      {"which": {"type": "string", "description": "words of its goal, 'the second one', or empty for the latest"}}, direct=True)
def cancel_mission(which: str = "") -> str:
    if not _owner():
        return NOT_OWNER
    try:
        items = (_call("GET", "/api/missions", query={"active": "1", "for_uid": _me()})["missions"] if _remote() else missions.listing(active_only=True))
        m = _pick(which, items)
        if not m:
            return "I couldn't tell which mission you mean, sir." if items else "There's no active mission to cancel."
        if _remote():
            _call("POST", f"/api/missions/{m['id']}/cancel", {"for_uid": _me()})
        else:
            missions.cancel(m["id"])
    except ValueError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the laptop that keeps the mission queue ({type(e).__name__})."
    return f"Cancelled the mission \"{m['goal'][:80]}\"."


@tool("Pause or resume ALL missions ('pause my missions', 'hold all background jobs', 'resume the missions').",
      {"paused": {"type": "boolean"}}, required=["paused"], direct=True)
def pause_missions(paused: bool) -> str:
    if not _owner():
        return NOT_OWNER
    try:
        (_call("POST", "/api/missions/pause", {"paused": bool(paused), "for_uid": _me()}) if _remote() else missions.set_paused(bool(paused)))
    except Exception as e:  # noqa: BLE001
        return f"I couldn't reach the laptop that keeps the mission queue ({type(e).__name__})."
    return "Missions paused, sir. Nothing new will start until you resume them." if paused else "Missions resumed, sir."


def owner_presence(busy: bool, reason: str = "") -> str:
    """He says he is busy at the PC ("I'm at the PC", "I'm gonna game") or done ("I'm done gaming", "I'm off the PC").
    Busy holds his background missions so nothing interferes; done lets them carry on (core/missions.set_hold).
    Called only from core/quick on his exact statements - deliberately NOT a model tool: a loosely related sentence
    must never hold every mission for hours (review 2026-11-10)."""
    if not _owner():
        return NOT_OWNER
    reason = reason or "you're at the PC"
    try:
        if _remote():
            got = _call("POST", "/api/missions/hold", {"busy": bool(busy), "reason": reason, "for_uid": _me()}) or {}
            still_paused = bool(got.get("paused"))
        elif busy:
            missions.set_hold(reason)
            still_paused = False
        else:
            missions.clear_hold()
            still_paused = missions.paused_switch()
    except Exception as e:  # noqa: BLE001
        import urllib.error
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            return "The laptop's JARVIS doesn't know that yet - it needs an update - so nothing was held, sir."
        return f"I couldn't reach the laptop that keeps the mission queue ({type(e).__name__})."
    hours = missions.HOLD_HOURS
    limit = f"{plural(round(hours), 'hour')} at most" if hours >= 1 else "for a little while"
    if busy:
        return f"Understood, sir. Missions will wait until you tell me you're done - {limit}."
    if still_paused:
        return "Noted, sir, but your missions are still paused. Say 'resume my missions' when you want them to carry on."
    return "Very good, sir. Missions will carry on in the background."
