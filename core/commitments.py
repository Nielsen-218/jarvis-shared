"""Partner stage 2 (docs/plans/partner-roadmap.md): his goals, what Jarvis said it would do, and a morning report built in code.

Goals: per person, "goal / why / next step / state" with state proposed | active | done | dropped. A PROPOSED goal (the seed from
his past logs, or any later suggestion) becomes active only on his explicit word ("the insurance one is a goal", "add it",
"all of them"); an unanswered proposal never activates (2.4).

Commitments: what Jarvis took on for him, recorded only where a real action started (a mission queued, a studio note, a
reminder set) and linked to that record; each one resolves from its record's state (2.2). The promise filter
(core.intelligence.promises_action) already removes a spoken promise with no call behind it, so plain chat never makes one.
One with no link and no result after its window is reported as NOT done, never silently dropped.

Storage (one writer per machine file; data/ syncs PC <-> laptop over Syncthing with a delay):
  data/users/<id>/goals-<machine>.json        {"v": 1, "goals": {id: goal}}       this machine's writes
  data/users/<id>/commitments-<machine>.json  {"v": 1, "items": {id: commitment}}  this machine's writes
  data/users/<id>/partner-<machine>.json      {"report_day", "referent"}          this machine's report / "add it" target
Readers merge both machines' files per id; the copy with the newer "updated" wins (as tools/pca_health does). A change to a
record first made on the other machine is written as a full copy into THIS machine's file.
Mission-linked commitments are resolved only on the machine that queued the mission (the laptop holds the queue in
machine-state/, which does not sync); the PC reads the laptop's resolved copy.

The morning report (2.3) is plain code: active goals with next steps, what finished or failed since the last report, what is
still owed, and what waits on him. Once a day for the owner, never 22:00-07:00, never during a game, spoken on the PC through
core.attention (scheduler._partner_report). The phone gets no morning push: the laptop has no briefing push to reuse.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from core import storage

GOAL_STATES = ("proposed", "active", "done", "dropped")
STATES = ("open", "in_progress", "done", "failed", "reported")
FINAL = ("done", "failed", "reported")
MACHINES = ("pc", "server")
QUIET = (22, 7)                         # never a report from 22:00 to 07:00
REFERENT_S = 15 * 60                    # "add it" / "all of them" answer what was said this long ago at most
WINDOWS = {"mission": 2 * 86400, "studio": 3 * 86400, "reminder": 30 * 60, "protocol": 86400, "": 86400}
MAX_GOALS = 60
MAX_ITEMS = 200
MAX_TEXT = 200
SAY_MAX = 3                             # items read aloud before "and N more"
MORNING_UNTIL = 12                      # "Your morning report" only before noon; later the same report is worded neutrally
RETRY_S = 5 * 60                        # a report the rate limit would hold is withdrawn and tried again this much later
PROPOSAL_MORNINGS = 3                   # a proposal unanswered after this many reports is no longer raised by itself

_lock = threading.RLock()


# -- files -----------------------------------------------------------------------------------------------------------
def _machine() -> str:
    from core import thread
    return thread.machine()


def _file(kind: str, machine: str) -> str:
    return f"{kind}-{machine}.json"


_NUMBERS = ("updated", "created", "due", "resolved", "reported", "confirmed", "dropped", "finished")


def _sane(rid, rec) -> dict | None:
    """One synced record made safe to read: its id is its key, its times are numbers; anything else is skipped (a bad
    row from the other machine, or hand-edited, must not break every reader)."""
    if not isinstance(rec, dict) or not isinstance(rid, str) or not rid:
        return None
    out = dict(rec, id=rid)
    for field in _NUMBERS:
        if field in out and out[field] is not None:
            try:
                out[field] = float(out[field])
            except (TypeError, ValueError):
                return None
    if not isinstance(out.get("state"), str):
        return None
    if out.get("link") is not None and not isinstance(out.get("link"), dict):
        return None
    if not isinstance(out.get("aliases", []), list):
        out["aliases"] = []
    return out


def _load(kind: str, machine: str, key: str) -> dict:
    data = storage.load(_file(kind, machine), {})
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return {}
    out = {}
    for rid, rec in rows.items():
        good = _sane(rid, rec)
        if good is not None:
            out[rid] = good
    return out


def _merged(kind: str, key: str) -> dict:
    out: dict = {}
    for m in MACHINES:
        for rid, rec in _load(kind, m, key).items():
            if rid not in out or float(rec.get("updated") or 0) > float(out[rid].get("updated") or 0):
                out[rid] = rec
    return out


def _put(kind: str, key: str, rec: dict, cap: int) -> dict:
    """Write one record (a full copy) into THIS machine's file, stamped newer than any copy anywhere."""
    with _lock:
        mine = _load(kind, _machine(), key)
        newest = max([float(r.get("updated") or 0) for r in _merged(kind, key).values() if r.get("id") == rec["id"]] or [0.0])
        rec = {**rec, "updated": max(time.time(), newest + 0.001)}
        mine[rec["id"]] = rec
        if len(mine) > cap:            # oldest finished records go first; nothing open is ever trimmed away
            done = sorted((r for r in mine.values() if r.get("state") in FINAL + ("dropped",)), key=lambda r: r.get("updated") or 0)
            for r in done[:len(mine) - cap]:
                mine.pop(r["id"], None)
        storage.save(_file(kind, _machine()), {"v": 1, key: mine})
        return rec


def _clip(text) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


# -- goals -----------------------------------------------------------------------------------------------------------
def goals(states: tuple = GOAL_STATES) -> list[dict]:
    return sorted((g for g in _merged("goals", "goals").values() if g.get("state") in states),
                  key=lambda g: (float(g.get("created") or 0), g.get("id", "")))


def add_goal(text: str, why: str = "", state: str = "active", source: str = "owner", gid: str = "",
             aliases: list | None = None, spoken: str = "", next_step: str = "") -> dict:
    if state not in GOAL_STATES:
        raise ValueError(state)
    now = time.time()
    rec = {"id": gid or "g" + uuid.uuid4().hex[:8], "goal": _clip(text), "why": _clip(why), "next_step": _clip(next_step),
           "state": state, "source": source, "created": now, "aliases": [a.casefold() for a in (aliases or [])],
           "spoken": _clip(spoken)}
    return _put("goals", "goals", rec, MAX_GOALS)


def set_goal(goal: dict, **fields) -> dict:
    if "state" in fields and fields["state"] not in GOAL_STATES:
        raise ValueError(fields["state"])
    return _put("goals", "goals", {**goal, **fields}, MAX_GOALS)


def name_of(goal: dict) -> str:
    return goal.get("spoken") or goal.get("goal") or "that goal"


_STOP = {"the", "a", "an", "my", "your", "one", "goal", "goals", "to", "of", "and", "for", "on", "in", "is", "it", "that",
         "this", "with", "real", "yes", "yeah", "get", "be", "i", "me", "about", "learn", "learning"}


def _words(text) -> set:
    words = set(re.findall(r"[a-z0-9]+", str(text or "").casefold().replace("ò", "o").replace("é", "e")))
    out = {w for w in words if w not in _STOP and len(w) > 1}
    return out | {w[:-1] for w in out if w.endswith("s") and len(w) > 3}


def _vocab(g: dict) -> set:
    return _words(" ".join([g.get("goal", ""), g.get("spoken", "")] + list(g.get("aliases", []))))


def find_goal(said: str, states: tuple = ("proposed", "active"), exact: bool = True) -> dict | None:
    """The goal he named. exact (the default for every command): every word he used belongs to ONE goal's words or aliases
    ("the insurance one" -> insurance; "the insurance quote on my calendar" -> none). Otherwise an alias said whole, else
    the most words shared (ties and no overlap: none)."""
    pool = goals(states)
    want = _words(said)
    if exact:
        hits = [g for g in pool if want and want <= _vocab(g)]
        return hits[0] if len(hits) == 1 else None
    text = " " + " ".join(re.findall(r"[a-z0-9]+", str(said or "").casefold())) + " "
    for g in pool:
        if any(f" {a} " in text for a in g.get("aliases", []) if a):
            return g
    if not want:
        return None
    scored = sorted(((len(want & _words(" ".join([g.get("goal", ""), g.get("spoken", "")]))), g) for g in pool),
                    key=lambda p: -p[0])
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


# -- commitments -----------------------------------------------------------------------------------------------------
def items(states: tuple = STATES) -> list[dict]:
    return sorted((c for c in _merged("commitments", "items").values() if c.get("state") in states),
                  key=lambda c: (float(c.get("created") or 0), c.get("id", "")))


def record(what: str, source: str = "", link_kind: str = "", link_id: str = "", due: float | None = None,
           detail: str = "", cid: str = "") -> dict:
    """A commitment Jarvis took on. Only callers that STARTED something real record one (missions.add, set_reminder)."""
    now = time.time()
    link_kind = link_kind if link_kind in WINDOWS else ""
    rec = {"id": cid or "c" + uuid.uuid4().hex[:10], "what": _clip(what), "source": _clip(source),
           "link": {"kind": link_kind, "id": str(link_id or "")} if link_kind else None,
           "state": "open", "outcome": "", "detail": _clip(detail), "created": now,
           "due": float(due if due is not None else now + WINDOWS[link_kind]), "machine": _machine()}
    return _put("commitments", "items", rec, MAX_ITEMS)


def _set(c: dict, **fields) -> dict:
    return _put("commitments", "items", {**c, **fields}, MAX_ITEMS)


def on_mission(m: dict) -> dict | None:
    """missions.add queued one (the laptop: the queue's machine). A studio note is a work order in the making."""
    try:
        from core import studio
        note = studio.is_note(m.get("goal", ""))
    except Exception:  # noqa: BLE001 - studio unreadable: an ordinary mission
        note = False
    goal = m.get("goal", "")
    if note:
        from core import studio
        what = "write up and build your game note: " + studio.note_of(goal)
        return record(what, goal, "studio", m.get("id", ""))
    return record(goal, goal, "mission", m.get("id", ""))


def on_reminder(rid: str, text: str, due: datetime, shared: bool = False) -> dict:
    """set_reminder saved a one-off reminder: Jarvis owes him that reminder at that time."""
    return record(f"remind you: {text}", text, "reminder", f"{'household/' if shared else ''}{rid}",
                  due=due.timestamp() + WINDOWS["reminder"], detail=due.isoformat(timespec="minutes"))


# -- resolving from the linked record --------------------------------------------------------------------------------
_MISSION_STATE = {"queued": ("open", "queued"), "unknown": ("open", "interrupted, to be retried"),
                  "running": ("in_progress", "running now"), "waiting_approval": ("in_progress", "waiting for your OK on the phone"),
                  "blocked": ("in_progress", "blocked"), "done": ("done", ""), "failed": ("failed", "it failed"),
                  "cancelled": ("failed", "you cancelled it")}
_STUDIO_STATE = {"draft": ("in_progress", "waiting for your OK on the work order"), "approved": ("in_progress", "approved, starting"),
                 "preparing": ("in_progress", "being set up"), "working": ("in_progress", "being built"),
                 "building": ("in_progress", "building"), "testing": ("in_progress", "in testing"), "green": ("done", "GREEN"),
                 "red": ("failed", "NOT GREEN"), "failed": ("failed", "the work order failed"),
                 "cancelled": ("failed", "the work order was cancelled")}
WAITING = ("waiting for your OK on the phone", "waiting for your OK on the work order")


def _mission(mid: str) -> dict | None:
    from core import missions
    return missions.get(mid)


def _reminder_present(link_id: str) -> bool | None:
    from tools import proactive
    file = proactive.HOUSEHOLD_REMINDERS if link_id.startswith("household/") else proactive.REMINDERS
    rid = link_id.removeprefix("household/")
    rows = storage.load(file, [])
    return any(isinstance(r, dict) and r.get("id") == rid for r in rows) if isinstance(rows, list) else None


def _lookup(c: dict, now: float) -> tuple[str, str] | None:
    """(state, detail) from the linked record; None when the record could not be read (or is not this machine's to read)."""
    link = c.get("link") or {}
    kind, lid = link.get("kind", ""), link.get("id", "")
    if c.get("machine") != _machine():
        return None              # the machine that took it resolves it: the mission queue is in its machine-state/, and a
                                 # reminder it saved may not have synced to the other machine yet (review 2026-11-21)
    if kind in ("mission", "studio"):
        if kind == "studio":
            from core import studio
            order = studio.for_mission(lid)
            if order is not None:
                return _STUDIO_STATE.get(order.get("state"), ("in_progress", order.get("state", "")))
        m = _mission(lid)
        if m is None:
            return None
        state, detail = _MISSION_STATE.get(m.get("state"), ("open", m.get("state", "")))
        if kind == "studio" and state == "done":
            return ("failed", "no work order was written for it")    # a finished mission is not a built note
        return state, detail
    if kind == "reminder":
        present = _reminder_present(lid)
        if present is None:
            return None
        if not present:
            return ("done", "reminded you") if now >= c["due"] - WINDOWS["reminder"] else ("failed", "the reminder was cancelled")
        return ("open", "set") if now < c["due"] else ("failed", "the reminder never went off")
    if kind == "protocol":
        from core import protocols
        run = protocols.last_run(lid, ran=True)
        try:
            started = datetime.fromisoformat(str(run.get("started"))).timestamp() if run else 0.0
        except ValueError:
            started = 0.0
        if not run or started < float(c.get("created") or 0) - 1:
            return None
        bad = any(s.get("status") == protocols.FAILED for s in run.get("steps", []))
        return ("failed", "a step failed") if bad else ("done", "")
    return None


def resolve(now: float | None = None) -> list[dict]:
    """Bring every unfinished commitment up to date from its record; past its window with no result -> failed (said as NOT
    done, never dropped). -> the ones that changed."""
    now = time.time() if now is None else now
    changed = []
    with _lock:
        for c in items(("open", "in_progress")):
            try:
                got = _lookup(c, now)
            except Exception as error:  # noqa: BLE001 - unreadable this pass: left as it was, judged by its window below
                print(f"[commitments] {c.get('id')}: {type(error).__name__}: {error}")
                got = None
            link = c.get("link") or {}
            mine = c.get("machine") == _machine()
            if got is None:
                if now <= float(c.get("due") or 0) or not mine:
                    continue                        # still in its window, or the other machine resolves it
                why = "I lost track of it" if link.get("kind") else "I haven't done it"
                got = ("failed", why)
            state, detail = got
            if state != c.get("state") or detail != c.get("detail"):
                fields = {"state": state, "detail": detail}
                if state in ("done", "failed"):
                    fields.update(outcome=state, resolved=now)
                changed.append(_set(c, **fields))
    return changed


# -- what he hears ---------------------------------------------------------------------------------------------------
def _and(parts: list[str], sep: str = ", ") -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return sep.join(parts[:-1]) + sep.rstrip() + " and " + parts[-1] if sep != ", " else ", ".join(parts[:-1]) + " and " + parts[-1]


def _some(parts: list[str], noun: str = "", sep: str = ", ") -> str:
    """At most SAY_MAX read aloud, then 'and N more'."""
    if len(parts) <= SAY_MAX + 1:
        return _and(parts, sep)
    more = len(parts) - SAY_MAX
    return sep.join(parts[:SAY_MAX]) + sep.rstrip() + f" and {more} more{(' ' + noun) if noun else ''}"


def _status(c: dict) -> str:
    what = c.get("what", "")
    state = c.get("state")
    detail = c.get("detail", "")
    if state == "open":
        overdue = time.time() > float(c.get("due") or 0)
        return f"{what}: not started yet" + (", and it's overdue" if overdue else "")
    if state == "in_progress":
        return f"{what}: {detail or 'under way'}"
    outcome = c.get("outcome") or state
    if outcome == "done":
        return f"{what}: done"
    return f"{what}: not done, {detail or 'it failed'}"


def owed_text() -> str:
    resolve()
    live = items(("open", "in_progress"))
    recent = [c for c in items(("done", "failed", "reported")) if time.time() - float(c.get("resolved") or 0) < 2 * 86400]
    if not live and not recent:
        return "Nothing at the moment. I haven't taken anything on for you that's still open."
    out = []
    if live:
        out.append(f"I owe you {_plural(len(live), 'thing')}: " + _some([_status(c) for c in reversed(live)]) + ".")
    else:
        out.append("Nothing open right now.")
    if recent:
        out.append("Recently: " + _some([_status(c) for c in reversed(recent)]) + ".")
    return " ".join(out)


def _plural(n: int, word: str) -> str:
    from core.words import plural
    return plural(n, word)


def _goal_line(g: dict) -> str:
    step = g.get("next_step")
    return f"{name_of(g)}, next step {step}" if step else f"{name_of(g)}, no next step yet"


def goals_text() -> str:
    active, proposed = goals(("active",)), goals(("proposed",))
    if not active and not proposed:
        return "You haven't given me any goals yet. Say 'my goal is' and the goal, and I'll keep track of it."
    out = []
    if active:
        out.append(f"You've got {_plural(len(active), 'goal')}: " + _some([_goal_line(g) for g in active], sep="; ") + ".")
        _set_referent([g["id"] for g in active], "goals")
    if proposed:
        out.append(_proposal_sentence(proposed, also=bool(active)))
    return " ".join(out)


def _proposal_sentence(proposed: list[dict], also: bool = False, refer: bool = True) -> str:
    """The proposals as one sentence; `refer` makes 'all of them' / 'the X one' answer it (only once it is really said)."""
    names = [name_of(g) for g in proposed]
    head = names[:4] if len(names) <= 4 else names[:SAY_MAX]
    more = names[len(head):]
    listing = _and(head) if not more else ", ".join(head) + f", and {len(more)} more"
    if refer:
        _refer_proposals(proposed, len(head))
    lead = "I'm also guessing at" if also else "I think these are your goals:"
    tail = (" Tell me which are real, say 'the " + _alias(proposed[0]) + " one is a goal', or 'all of them'."
            if len(proposed) > 1 else " Is that a real goal? Say 'add it' if it is.")
    if more:
        tail += " Say 'the other goals' to hear the rest."
    return f"{lead} {listing}.{tail}"


def _refer_proposals(proposed: list[dict], shown: int) -> None:
    _set_referent([g["id"] for g in proposed], "proposed", shown=[g["id"] for g in proposed[:shown]])


def _shown_count(proposed: list[dict]) -> int:
    return len(proposed) if len(proposed) <= 4 else SAY_MAX


def _alias(g: dict) -> str:
    aliases = g.get("aliases") or []
    return aliases[0] if aliases else (name_of(g).split() or ["first"])[-1]


# -- the morning report (2.3) ----------------------------------------------------------------------------------------
def _partner() -> dict:
    s = storage.load(_file("partner", _machine()), {})
    return s if isinstance(s, dict) else {}


def _save_partner(s: dict) -> None:
    storage.save(_file("partner", _machine()), s)


def quiet_hours(now: datetime) -> bool:
    return now.hour >= QUIET[0] or now.hour < QUIET[1]


def waiting_on_him() -> list[str]:
    out = []
    waits = [c for c in items(("in_progress",)) if c.get("detail") in WAITING]
    if waits:
        out.append(f"{_plural(len(waits), 'approval')} on your phone")
    try:
        from core import protocols
        pending = [p for p in protocols.protocols() if p.get("learned") and not protocols.approved(p)]
        if pending:
            out.append(f"the {pending[0]['name']} protocol I suggested" if len(pending) == 1
                       else f"{_plural(len(pending), 'protocol')} I suggested")
    except Exception as error:  # noqa: BLE001 - unreadable: not mentioned rather than guessed
        print(f"[commitments] protocols: {type(error).__name__}: {error}")
    return out


def _told() -> dict:
    told = _partner().get("proposals_told")
    return told if isinstance(told, dict) else {}


def build_report(now: datetime | None = None) -> tuple[str, dict]:
    """The deterministic report and what saying it settles ({"reported": [ids], "proposed": [goals], "shown": n}).
    Nothing is marked here: only a report that was really said marks anything (mark_reported)."""
    now = now or datetime.now()
    resolve(now.timestamp())
    parts = []
    owned, owned_day = _self_review(now)
    if owned:                    # his misses yesterday, owned first (core.selfreview); marked only when this report is said
        parts.append(owned)
    active, proposed = goals(("active",)), goals(("proposed",))
    if active:
        parts.append("On your goals: " + _some([_goal_line(g) for g in active], sep="; ") + ".")
    settled = items(("done", "failed"))
    finished = [c for c in settled if (c.get("outcome") or c.get("state")) == "done"]
    failed = [c for c in settled if c not in finished]
    if finished:
        parts.append("Done since last time: " + _some([c.get("what", "") for c in finished]) + ".")
    if failed:
        parts.append("Not done: " + _some([f"{c.get('what', '')}, {c.get('detail') or 'it failed'}" for c in failed]) + ".")
    overdue = [c for c in items(("open", "in_progress")) if now.timestamp() > float(c.get("due") or 0)]
    if overdue:
        parts.append("Still owed: " + _some([_status(c) for c in overdue]) + ".")
    waits = waiting_on_him()
    if waits:
        parts.append("Waiting on you: " + _and(waits) + ".")
    told = _told()
    fresh = [g for g in proposed if int(told.get(g["id"], 0) or 0) < PROPOSAL_MORNINGS]
    if fresh:                    # raised on PROPOSAL_MORNINGS reports at most; "what are my goals" still lists them
        parts.append(_proposal_sentence(fresh, also=bool(active), refer=False))
    if not parts:
        return "", {}
    lead = "Your morning report." if now.hour < MORNING_UNTIL else "Here's where things stand."
    return f"{lead} " + " ".join(parts), {"reported": [c["id"] for c in finished + failed], "proposed": fresh,
                                         "shown": _shown_count(fresh), "selfreview": owned_day}


def _self_review(now: datetime) -> tuple[str, str | None]:
    try:
        from core import selfreview
        return selfreview.for_report(now)
    except Exception as error:  # noqa: BLE001 - a review never breaks the report
        print(f"[commitments] self-review: {type(error).__name__}: {error}")
        return "", None


def mark_reported(now: datetime, settles: dict, text: str = "") -> None:
    """The report was said: its finished/failed items are 'reported', its proposals counted and answerable, and its words
    kept (the Partner page shows the last one said; nothing else reads them)."""
    if text:
        with _lock:
            s = _partner()
            s["last_report"] = {"text": str(text)[:4000], "at": now.timestamp()}
            _save_partner(s)
    if settles.get("selfreview"):
        try:
            from core import selfreview
            selfreview.mark_said(settles["selfreview"], now.timestamp())
        except Exception as error:  # noqa: BLE001
            print(f"[commitments] self-review: {type(error).__name__}: {error}")
    by_id = {c["id"]: c for c in items(("done", "failed"))}
    for cid in settles.get("reported", []):
        if cid in by_id:
            _set(by_id[cid], state="reported", reported=now.timestamp())
    fresh = settles.get("proposed") or []
    if fresh:
        with _lock:
            s = _partner()
            told = s.get("proposals_told") if isinstance(s.get("proposals_told"), dict) else {}
            for g in fresh:
                told[g["id"]] = int(told.get(g["id"], 0) or 0) + 1
            s["proposals_told"] = told
            _save_partner(s)
        _refer_proposals(fresh, settles.get("shown") or len(fresh))


def morning_report(now: datetime | None = None) -> str:
    """Build the report and treat it as said (tests, a direct 'report' call). '' when there is nothing to say."""
    now = now or datetime.now()
    text, settles = build_report(now)
    if text:
        mark_reported(now, settles, text)
    return text


def last_report() -> dict | None:
    """The last report really said, from either machine's partner file (the PC speaks it; the phone reads it on the laptop
    once data/ has synced): {"text", "at", "machine"} or None. Read-only."""
    best = None
    for m in MACHINES:
        s = storage.load(_file("partner", m), {})
        rep = s.get("last_report") if isinstance(s, dict) else None
        if not isinstance(rep, dict) or not isinstance(rep.get("text"), str) or not rep["text"].strip():
            continue
        try:
            at = float(rep.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if best is None or at > best["at"]:
            best = {"text": rep["text"], "at": at, "machine": m}
    return best


def report_now(now: datetime | None = None) -> str:
    """Where things stand, as the report would say it right now. Marks nothing reported and answers nothing: no
    proposal count, no "add it" referent, no report day (only resolve() brings commitments up to date, as every reader does)."""
    text, _settles = build_report(now or datetime.now())
    return text


def report_due(now: datetime, sit: dict) -> bool:
    if quiet_hours(now) or sit.get("gaming") or not sit.get("present"):
        return False
    if time.time() < _retry_after.get(_who(), 0.0):
        return False
    return _partner().get("report_day") != now.date().isoformat()


_retry_after: dict = {}          # uid -> not before (a report the rate limit held is tried again later, never queued)


def _who() -> str:
    from core import users
    return users.current()


def deliver_report(now: datetime | None = None, sit: dict | None = None, deliver=None) -> str:
    """Once a day for the person being served (the scheduler acts as the owner): -> what was said, or ''.
    The day and the items are marked only when it was SAID. core.attention never keeps it: a held report would live only
    in memory (lost on a restart) and could be flushed in a later summary at any hour, so it is withdrawn and tried again
    in RETRY_S (review 2026-11-21)."""
    from core import attention
    now = now or datetime.now()
    sit = attention.situation() if sit is None else sit
    if not report_due(now, sit):
        return ""
    text, settles = build_report(now)
    if not text:
        with _lock:
            s = _partner()
            s["report_day"] = now.date().isoformat()       # nothing to say today: not rebuilt every 20 s
            _save_partner(s)
        return ""
    key = "partner-report:" + now.date().isoformat()
    if deliver is not None:
        deliver(text)
        verdict = "said"
    else:
        verdict = attention.deliver(text, attention.NORMAL, key=key, sit=sit)
    if verdict != "said":
        attention.withdraw(key)
        _retry_after[_who()] = time.time() + RETRY_S
        return ""
    with _lock:
        s = _partner()
        s["report_day"] = now.date().isoformat()
        _save_partner(s)
    mark_reported(now, settles, text)
    return text


# -- "add it" / "all of them": what was just said -------------------------------------------------------------------
def _set_referent(ids: list[str], kind: str, shown: list[str] | None = None) -> None:
    with _lock:
        s = _partner()
        s["referent"] = {"ids": ids, "kind": kind, "shown": ids if shown is None else shown, "at": time.time()}
        _save_partner(s)


def _clear_referent() -> None:
    with _lock:
        s = _partner()
        if s.pop("referent", None) is not None:
            _save_partner(s)


def _referent(kind: str = "") -> dict | None:
    """What 'it' / 'all of them' / 'the X one' means: set within REFERENT_S, with no turn by anyone since (fails toward
    'not ours': the line then goes to the model)."""
    ref = _partner().get("referent")
    if not isinstance(ref, dict):
        return None
    try:
        at = float(ref.get("at") or 0)
    except (TypeError, ValueError):
        return None
    if not at or time.time() - at > REFERENT_S or (kind and ref.get("kind") != kind):
        return None
    from core import habits
    if habits._turns_since(at):
        return None
    return ref


# -- voice / text commands (2.1): deterministic, no model ------------------------------------------------------------
_LEAD = r"^\W*(?:(?:hey\s+)?jarvis[\s,]+)?(?:(?:ok(?:ay)?|so|and|well|um+|uh+|alright|all right)[\s,]+)*(?:please\s+)?"
_END = r"(?:[\s,]+(?:please|jarvis|sir|again|right now|now))*\W*$"
_YES = r"(?:(?:yes|yeah|yep|yup|correct|right)[\s,!.]+)*"
_LIST = re.compile(_LEAD + r"(?:(?:what|which)(?: are| r|'re)? my (?:current |main |big |life |long[- ]term )?goals|my goals|"
                   r"(?:list|tell me|remind me of|read me|go over|run through|show me) (?:all )?(?:of )?my (?:current )?goals|"
                   r"what goals (?:do i have|have i (?:got|set|given you)|am i working (?:on|towards?))|"
                   r"what (?:goals )?(?:are you|have you got me) tracking(?: for me)?|do you know my goals)" + _END, re.I)
_OTHERS = re.compile(_LEAD + r"(?:what are )?(?:the )?(?:other|rest of the|remaining) (?:goals|ones)|what are the others|"
                     r"(?:and )?the (?:other|rest)(?: (?:two|three|four|ones))?" + _END, re.I)
# A new goal needs a real separator: "add a goal: X", "new goal: X", "my new goal is X", "set a goal to <verb> X",
# "add X to my goals". Not "set a goal weight of 180", "add a goal to my calendar", "save a goal for later" (review 2026-11-21).
_ADD = re.compile(_LEAD + r"(?:(?:add|set|make|create|log|save|record)(?: me)? (?:a |another |one more )?(?:new )?goal(?: for me)?"
                  r"(?:\s*[:\-]\s*|,\s*|\s+to (?!(?:my|the|a|an|your|this|that|it|its|our|his|her|their)\b))|"
                  r"(?:a )?new goal(?:\s*[:\-]\s*|,\s*| is\s+(?:to )?)|(?:i(?:'ve| have) )?got a new goal\s*[:\-]\s*|"
                  r"(?:i have|i've got) a new goal(?:\s*[:\-]\s*|,?\s+(?:it's|it is)\s+(?:to )?)|"
                  r"my (?:new |next |big |main )?goal is(?: now)?\s*[:\-]?\s*(?:to )?|"
                  r"add (?P<pre>.{3,120}?) (?:to|as) (?:one of )?my goals?\W*$)"
                  r"(?P<text>.*)$", re.I)
# Done: about the goal itself, never "I hit my workout goal" for the day (review 2026-11-21). Active goals only.
_DONE = re.compile(_LEAD + r"(?:mark (?:the |my )?(?P<a>.{2,80}?) goal (?:as )?(?:done|finished|complete|completed|achieved)|"
                   r"mark (?:the |my )?goal\s*[:\-]?\s*(?P<b>.{2,80}?) (?:as )?(?:done|finished|complete|completed|achieved)|"
                   r"(?:the |my )?(?P<c>.{2,80}?) goal is (?:done|complete|completed|achieved)(?: for good)?)" + _END, re.I)
# Drop: the word "goal" in the sentence, or "the X one" right after a goal listing with no turn since.
_DROP = re.compile(_LEAD + r"(?:(?:drop|remove|delete|scrap) (?:the |my )?(?P<a>.{2,80}?) goal(?: from my goals)?|"
                   r"(?:drop|remove|delete|scrap) (?:the |my )?goal\s*[:\-]?\s*(?P<b>.{2,80}?)|"
                   r"(?:drop|remove|scrap) (?:the )?(?P<one>.{2,40}?) one|"
                   r"(?:the |my )?(?P<c>.{2,80}?)(?: one)? (?:is not|isn't|is no longer) (?:a |my )?(?:real )?goal(?: (?:any ?more|of mine))?|"
                   r"(?P<that>(?:that's|that is|it's|it is) not (?:a |my )?(?:real )?goal|not that one|drop (?:it|that)(?: one)?))" + _END, re.I)
_BACK = re.compile(_LEAD + r"(?:(?:bring back|restore|undrop|un-drop|reopen) (?:the |my )?(?P<a>.{2,80}?) goal|"
                   r"(?P<that>undo that|bring (?:it|that) back|put (?:it|that) back))" + _END, re.I)
# Activate by name: "the X one is a goal", "X is a goal", "make X a goal"; or "the X one (is real)" / "yes to the X one"
# right after a proposal with no turn since. Never a bare "keep the game" or "add the website" (review 2026-11-21).
_YES_ONE = re.compile(_LEAD + _YES + r"(?:(?:the )?(?P<what>.{2,60}?)(?: one)? (?:is|'s) (?:a |my )?(?:real )?goal|"
                      r"(?:make|keep|add|activate|approve|confirm) (?:the )?(?P<make>.{2,60}?)(?: one)? (?:as a goal|a goal|goal)|"
                      r"(?:yes to |keep |add |make )?the (?P<one>.{2,40}?) one(?: is real| too| as well)?|"
                      r"(?:the )?(?P<real>.{2,40}?) one is real)" + _END, re.I)
_YES_IT = re.compile(_LEAD + _YES + r"(?:(?:that's|that is|it's|it is) (?:a |my )?(?:real )?goal|"
                     r"add (?:it|that)(?: as a goal| to my goals)?|make (?:it|that) a goal|(?:yes|yeah|yep),? (?:it is|that's (?:right|real))|keep (?:it|that))" + _END, re.I)
_ALL = re.compile(_LEAD + r"(?:(?:yes|yeah|yep)[\s,!.]*)*(?:all of them|they(?:'re| are) all (?:real|goals|right)|all (?:of them )?(?:are )?(?:real|goals)|"
                  r"(?:add|keep|make) (?:them )?all(?: of them)?(?: goals)?|every one of them)" + _END, re.I)
_STEP = re.compile(_LEAD + r"(?:the )?next step (?:for|on) (?:my |the )?(?P<what>.{2,60}?)(?: goal)? (?:is|will be)\s*[:,-]?\s*(?:to )?(?P<step>.{3,160}?)\W*$|"
                   + _LEAD[1:] + r"(?:the )?next step (?:is|will be)\s*[:,-]?\s*(?:to )?(?P<step2>.{3,160}?)\W*$", re.I)
_OWED = re.compile(_LEAD + r"(?:what (?:are|r) you (?:working on|doing|busy with)(?: right now| at the moment)? for me|"
                   r"what do you (?:still )?owe me|what (?:have|did) you promise(?:d)?(?: me| to do)?(?: for me)?|"
                   r"what(?:'s| is) on your (?:plate|list|to-?do list)(?: for me)?|what have you (?:still )?got to do for me|"
                   r"(?:any|what are your|list your|your) (?:open )?(?:promises|commitments)(?: to me)?|"
                   r"what (?:are|r) you (?:supposed|meant) to be doing for me|what did you say you(?:'d| would) do(?: for me)?)" + _END, re.I)
_NOT_A_GOAL = re.compile(r"\b(?:function|code|test|script|game(?:'s| is)?|level|match|round|mission|quest|bug|project|"
                         r"weight|pounds?|lbs?|kilos?|kg|reminder|calendar|highlights?|later|alarm|timer|"
                         r"the goal of|whose goal|our goal|their goal|his goal|her goal|the goal here|the goal is)\b", re.I)
_NOT_A_GOAL_START = re.compile(r"^(?:for|of|weight|reminder|to (?:my|the|a|an)\b|on|at|in|highlights?)\b", re.I)


def _clean(text) -> str:
    raw = re.sub(r"^\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", str(text or "").strip(), flags=re.I)
    return raw.replace("’", "'").replace("‘", "'").strip()


def _strip(text: str) -> str:
    return text.strip().strip(" .!?,:;-\"'")


def _trainer_line(raw: str) -> bool:
    """'my goal is to build muscle', 'my goal is fat loss, four days a week', 'my goal is glutes': the trainer's own
    training goal (core/coachintent, brain/llm.py _trainer_request), which runs after these commands. Left to it."""
    try:
        from core import coachintent
        return coachintent.call_for(raw) is not None
    except Exception:  # noqa: BLE001 - unreadable: treated as the trainer's (the safe side: no goal is written)
        return True


def handle(text: str) -> str | None:
    """A goals / commitments sentence -> the reply; None for anything else (the model answers it)."""
    raw = _clean(text)
    if not raw or len(raw) > 200:
        return None
    if _OWED.match(raw):
        return owed_text()
    from core import selfreview
    if selfreview.asks(raw):                 # "what did you get wrong yesterday": his misses, from the stored review
        return selfreview.handle(raw)
    if _LIST.match(raw):
        return goals_text()
    m = _STEP.match(raw)
    if m:
        return _next_step(m)
    m = _ADD.match(raw)
    if m:
        return None if _trainer_line(raw) else _add(m)
    m = _DONE.match(raw)
    if m:
        g = find_goal(m.group("a") or m.group("b") or m.group("c"), ("active",))
        if g is None:
            return None
        set_goal(g, state="done", finished=time.time(), was=g.get("state"))
        _set_referent([g["id"]], "changed")
        return f"Well done. I've marked {name_of(g)} as done. If that's wrong, say 'bring back the {_alias(g)} goal'."
    m = _BACK.match(raw)
    if m:
        return _back(m)
    m = _DROP.match(raw)
    if m:
        return _drop(m)
    if _ALL.match(raw):
        ref = _referent("proposed")
        if ref is None:
            return None
        shown = set(ref.get("shown") or [])
        got = [g for g in goals(("proposed",)) if g["id"] in shown]       # only the ones he heard (review 2026-11-21)
        if not got:
            return None
        for g in got:
            set_goal(g, state="active", confirmed=time.time())
        rest = [g for g in goals(("proposed",)) if g["id"] in ref.get("ids", []) and g["id"] not in shown]
        _clear_referent()
        if rest:
            _set_referent([g["id"] for g in rest], "proposed", shown=[])
            return (f"Right, those {len(got)} are goals now. There are {len(rest)} more I haven't read you; "
                    "say 'the other goals' to hear them.")
        return f"Right, all {len(got)} are goals now. Tell me a next step for any of them whenever you like."
    if _YES_IT.match(raw):
        ref = _referent()
        if ref is None:
            return None
        pending = [g for g in goals(("proposed",)) if g["id"] in (ref.get("shown") or ref.get("ids", []))]
        if not pending:
            return None
        if len(pending) > 1:
            return f"Which one? Say, for example, 'the {_alias(pending[0])} one is a goal', or 'all of them'."
        set_goal(pending[0], state="active", confirmed=time.time())
        _clear_referent()
        return f"Done, {name_of(pending[0])} is a goal now."
    m = _YES_ONE.match(raw)
    if m:
        return _yes_one(m)
    if _OTHERS.match(raw):
        ref = _referent("proposed")
        if ref is None:
            return None
        rest = [g for g in goals(("proposed",)) if g["id"] in ref.get("ids", []) and g["id"] not in ref.get("shown", [])]
        if not rest:
            return None
        _set_referent([g["id"] for g in rest], "proposed", shown=[g["id"] for g in rest])
        return f"The others: {_and([name_of(g) for g in rest])}. Tell me which are real."
    return None


def _add(m) -> str | None:
    body = _strip(m.group("pre") or m.group("text") or "")
    if m.group("pre") and body.lower() in ("it", "that", "this", "them"):
        ref = _referent()
        pending = [g for g in goals(("proposed",)) if ref and g["id"] in (ref.get("shown") or ref.get("ids", []))]
        if len(pending) == 1:
            set_goal(pending[0], state="active", confirmed=time.time())
            _clear_referent()
            return f"Done, {name_of(pending[0])} is a goal now."
        return None
    if len(body) < 3 or _NOT_A_GOAL.search(body) or _NOT_A_GOAL_START.match(body):
        return None
    goal, _, why = body.partition(" because ")
    goal, why = _strip(goal), _strip(why)
    if len(goal) < 3 or goal.lower() in ("it", "that", "this", "to", "a"):
        return None
    same = find_goal(goal, ("proposed", "active"))
    if same is not None:
        if same.get("state") == "proposed":
            set_goal(same, state="active", confirmed=time.time(), **({"why": why} if why else {}))
            return f"Good, {name_of(same)} is a real goal now."
        return f"That's already one of your goals: {name_of(same)}."
    g = add_goal(goal, why=why)
    _set_referent([g["id"]], "goals")
    return f"Got it. New goal: {goal}. When you know the next step, say 'the next step is' and tell me."


def _next_step(m) -> str | None:
    step = _strip(m.group("step") or m.group("step2") or "")
    if len(step) < 3:
        return None
    if m.group("what"):
        g = find_goal(m.group("what"), ("active", "proposed"))
    else:
        ref = _referent("goals")
        active = [g for g in goals(("active", "proposed")) if ref and g["id"] in ref.get("ids", [])]
        g = active[0] if len(active) == 1 else None
    if g is None:
        return None
    set_goal(g, next_step=_clip(step))
    return f"Noted. Next step for {name_of(g)}: {step}."


def _drop(m) -> str | None:
    named = m.group("a") or m.group("b")
    if named:
        g = find_goal(named, ("proposed", "active"))
    elif m.group("one") or m.group("c"):
        if m.group("one") and _referent("proposed") is None and _referent("goals") is None:
            return None                         # "drop the X one" only answers a goal listing, with no turn since
        g = find_goal(m.group("one") or m.group("c"), ("proposed", "active"))
    else:
        ref = _referent()
        pending = [g for g in goals(("proposed", "active")) if ref and g["id"] in (ref.get("shown") or ref.get("ids", []))]
        g = pending[0] if len(pending) == 1 else None
    if g is None:
        return None
    set_goal(g, state="dropped", dropped=time.time(), was=g.get("state"))
    _set_referent([g["id"]], "changed")
    return f"Fine, I've dropped {name_of(g)}. Say 'bring back the {_alias(g)} goal' if you change your mind."


def _back(m) -> str | None:
    """Undo a drop or a 'done': the goal goes back to the state it had."""
    if m.group("that"):
        ref = _referent("changed")
        pool = [g for g in goals(("dropped", "done")) if ref and g["id"] in ref.get("ids", [])]
        g = pool[0] if len(pool) == 1 else None
    else:
        g = find_goal(m.group("a"), ("dropped", "done"))
    if g is None:
        return None
    back = g.get("was") if g.get("was") in ("proposed", "active") else "active"
    set_goal(g, state=back, restored=time.time())
    _clear_referent()
    return f"Done, {name_of(g)} is back" + (" as one of your goals." if back == "active" else " on the list I'm guessing at.")


def _yes_one(m) -> str | None:
    named = m.group("what") or m.group("make") or m.group("one") or m.group("real")
    if (m.group("one") or m.group("real")) and _referent("proposed") is None:
        return None                     # "the game one" / "the X one is real" only answer a proposal, with no turn since
    g = find_goal(named, ("proposed", "active"))
    if g is None:
        return None
    if g.get("state") == "active":
        return f"{name_of(g)[:1].upper()}{name_of(g)[1:]} is already one of your goals."
    set_goal(g, state="active", confirmed=time.time())
    tail = " Any others?" if goals(("proposed",)) else ""
    return f"Good, {name_of(g)} is a goal now.{tail}"


# -- the seed (one-off; scripts/seed_partner_goals.py) --------------------------------------------------------------
SEED: list[dict] = []   # example goals to propose to a new owner; empty by default (tests supply their own)


def seed_owner(dry_run: bool = False) -> list[str]:
    """Add the owner's goals from his past logs as PROPOSED (never active). Idempotent: a seed id that exists in any state
    (even dropped by him) is left alone. -> the ids added (or that would be)."""
    from core import users
    owner = next((u for u, row in users.all_users().items() if row.get("role") == "owner"), None)
    if owner is None:
        raise RuntimeError("no owner in the user registry")
    added = []
    with users.acting_as(owner):
        have = set(_merged("goals", "goals"))
        for s in SEED:
            if s["gid"] in have:
                continue
            added.append(s["gid"])
            if not dry_run:
                add_goal(s["text"], why=s.get("why", ""), state="proposed", source="seed", gid=s["gid"],
                         aliases=s["aliases"], spoken=s["spoken"])
    return added
