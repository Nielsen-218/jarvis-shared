"""FAILURE ANALYSIS -> PRACTICE: the owner's real requests Jarvis could not fully do are re-studied while the owner is away.

Each night (core.scheduler, after the PC school) every new miss in data/learning/live-misses.jsonl is replayed through the
understanding layer (core.gateway.decide) as a DECISION ONLY: nothing is executed, opened or typed unattended. The replayed
plan is then checked against the typed tool schemas (core.pcactions.validate) and the installed apps.

Outcomes, written to data/learning/miss-lessons.jsonl (one row per distinct sentence, newest state wins):
  understood   the replay covers everything and every step is a valid typed step: EXPERIMENTAL, never MASTERED. The plan
               runs only when the owner asks again, through the normal gates, and its real verification decides.
  unclear      the replay still asks a question or leaves a part out: needs the owner to teach it ("learn this").
  unsupported  no tool can do it yet: a capability gap to build.
A model reading a sentence differently is never proof it can DO it, so nothing here promotes a skill or changes behaviour.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Dict, List, Optional

LESSONS = "learning/miss-lessons.jsonl"
RETRY_AFTER_S = 20 * 3600.0   # a sentence still not understood is replayed on a LATER night: that is another try
MAX_TRIES = 6                 # after this many replays it is left to the teacher and the owner, not replayed forever
GATE_WAIT_S = 5.0   # the school runs while he is idle: a card that is not free soon means he is back (or gaming)


def _key(said: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (said or "").casefold()).strip()


def lessons() -> Dict[str, dict]:
    """The newest lesson per distinct sentence."""
    from core import storage
    out: Dict[str, dict] = {}
    try:
        for line in (storage.DATA / LESSONS).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[_key(row.get("said", ""))] = row
    except (OSError, ValueError):
        pass
    return out


def _write(row: dict) -> None:
    from core import storage
    path = storage.DATA / LESSONS
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def judge(decision) -> tuple:
    """(outcome, why) for one replayed decision, without running anything."""
    if decision is None:
        return "unsupported", "no understanding came back"
    kinds = [i.kind for i in decision.intents]
    if "clarify" in kinds:
        q = next((i.question for i in decision.intents if i.kind == "clarify"), "")
        return "unclear", ("still needs: " + q)[:160]
    if decision.covers is False:
        return "unclear", "still leaves part of the request out"
    steps = decision.steps()
    if steps:
        from core import pcactions
        try:
            pcactions.validate({"steps": steps})
        except Exception as exc:  # noqa: BLE001
            return "unsupported", f"the plan is not a valid typed step: {exc}"[:160]
    if not steps and all(k == "chat" for k in kinds):
        return "unsupported", "no tool or skill covers it yet"
    return "understood", "a complete, valid plan (not yet proven by doing it)"


def due(miss: dict, before: Optional[dict], now: float) -> bool:
    """Is this miss to be replayed tonight? A miss newer than its last study: yes. A sentence studied before and still not
    understood (unclear / unsupported, not yet taught): yes again once RETRY_AFTER_S has passed, up to MAX_TRIES, so a miss that
    keeps failing builds the tries the teacher gate needs. One that now passes (understood) or was taught is done."""
    if not before:
        return True
    studied = float(before.get("studied_at") or 0)
    if studied < float(miss.get("at") or 0):
        return True
    if before.get("outcome") in ("unclear", "unsupported") and not before.get("taught_as"):
        return now - studied >= RETRY_AFTER_S and int(before.get("tries") or 0) < MAX_TRIES
    return False


def review(limit: int = 6, seconds: float = 300.0, *, misses: Optional[List[dict]] = None,
           decide: Optional[Callable] = None, cancelled: Callable[[], bool] = lambda: False,
           now: Optional[float] = None) -> List[dict]:
    """Replay the misses that are due (new, or still failing from an earlier night), bounded by count and time. A replay that
    fails again is another try; one that now passes is closed as learned-by-fix. Returns the new lesson rows."""
    from core import gateway, inferencegate, intelligence
    misses = gateway.recent_misses(days=14, limit=50) if misses is None else misses
    decide = decide or gateway.decide
    known = lessons()
    clock = time.time() if now is None else now
    started, done = time.monotonic(), []
    for miss in reversed(misses):                       # newest first
        said = str(miss.get("said") or "").strip()
        key = _key(said)
        if not key or any(_key(d["said"]) == key for d in done):
            continue
        before = known.get(key)
        if miss.get("recovered_at") or not due(miss, before, clock):
            continue                                    # worked since (recovered), already studied, or closed
        if len(done) >= limit or time.monotonic() - started > seconds or cancelled():
            break
        # Background model work (core/inferencegate): only in a gap between his turns, never during a game. A replay his
        # turn cut into is not a lesson: nothing is written, and the miss is studied another time.
        with inferencegate.background("misslessons", timeout=GATE_WAIT_S) as slot:
            if not slot:
                break
            try:
                with intelligence.background_inference():   # the owner's own turn always goes first
                    decision = decide(said, cancelled=lambda: cancelled() or slot.cancelled())
                outcome, why = judge(decision)
                plan = decision.brief() if decision is not None else None
            except Exception as exc:  # noqa: BLE001
                outcome, why, plan = "unclear", f"replay failed ({type(exc).__name__})", None
            if slot.cancelled():
                break
        row = {"said": said, "studied_at": clock if now is not None else time.time(), "missed_at": miss.get("at"), "missed_because": miss.get("reason", ""),
               "outcome": outcome, "why": why, "status": "EXPERIMENTAL" if outcome == "understood" else "FAILED",
               "tries": int((before or {}).get("tries") or 0) + 1, "plan": plan}
        if outcome == "understood" and before and before.get("outcome") in ("unclear", "unsupported"):
            row["closed_as"] = "learned-by-fix"          # it failed on an earlier night and the code now reads it: closed
        _write(row)
        done.append(row)
    return done


def summary(rows: Optional[List[dict]] = None) -> str:
    """One short line for the school status: what the misses taught, honestly."""
    rows = list(lessons().values()) if rows is None else rows
    if not rows:
        return ""
    by = {o: [r["said"] for r in rows if r.get("outcome") == o] for o in ("understood", "unclear", "unsupported")}
    parts = []
    if by["understood"]:
        parts.append(f"I now understand {len(by['understood'])} (not yet proven by doing them)")
    if by["unclear"]:
        parts.append(f"{len(by['unclear'])} still need you to show me, like '{by['unclear'][-1]}'")
    if by["unsupported"]:
        parts.append(f"{len(by['unsupported'])} need a new ability, like '{by['unsupported'][-1]}'")
    return "Re-studied real misses: " + "; ".join(parts) + "."


# ------------------------------------------------------------------------------------------------ taught by a teacher
# A sentence re-studied twice and still not understood gets ONE lesson: the local teacher first; the paid teacher only when
# the local lesson did not work, once, behind core/teacher's existing credit and budget gate. The teacher never does the
# task and never plans it: it only says the same request in plain command words. That restatement is VERIFIED by replaying
# it through the normal understanding layer (decide-only, nothing runs) and is stored only when the replay is a complete,
# valid plan made of low-risk steps. From then on the owner's sentence is read as its stored restatement: locally, with no
# teacher. What the plan may DO is still decided by core/authz at the time he asks.

TAUGHT_EFFECTS = ("observe", "reversible")
_taught_cache = {"stamp": None, "rows": {}}


def lesson_id(said: str) -> str:
    import hashlib
    return "miss-" + hashlib.sha256(_key(said).encode("utf-8")).hexdigest()[:16]


def _lesson_prompt(row: dict) -> str:
    from core import authz
    return ("JARVIS (a voice assistant that controls a Windows PC) could not understand a request from his owner, twice.\n"
            f"The request: {row.get('said', '')!r}\nWhy it failed: {row.get('why') or row.get('missed_because') or 'not understood'}\n"
            f"The PC actions he has: {', '.join(sorted(t for t in authz.EFFECTS if isinstance(t, str))[:80])}\n\n"
            "Teach him to understand it. In \"failed_assumption\" say what he misread. In \"explanation\" say what the owner "
            "most likely wants. \"hints\" must hold exactly ONE item: the same request said as one short, plain, literal "
            "command sentence that names the app or thing explicitly (no code, no tool names, no extra steps the owner did "
            "not ask for). If the request cannot be done with those actions, leave \"hints\" empty.")


def _low_risk(decision) -> bool:
    from core import authz
    steps = decision.steps()
    return bool(steps) and all(authz.EFFECTS.get(s.get("tool"), "unknown") in TAUGHT_EFFECTS for s in steps)


# Words whose meaning is the moment, not the sentence ("close it", "do that again", "the thing"). A sentence holding one is
# never bound to a fixed wording: what it points at changes every time, so it must go through the normal path (which can
# ask). Over-matching ("that" as a conjunction) only means no shortcut is stored, which is the safe direction.
_MOMENT = re.compile(r"\b(?:it|its|that|this|these|those|them|they|he|she|him|her|one|ones|here|there|again|same|other|another|"
                     r"thing|things|stuff|something|anything|everything|previous|last|next|before|now|current)\b")


def moment_bound(said: str) -> bool:
    return bool(_MOMENT.search(_key(said)))


def _invents(decision, said: str) -> bool:
    """True when the plan names a target the owner never said (the teacher guessed 'Spotify' for 'the music player')."""
    heard = set(_key(said).split())
    for step in decision.steps():
        for value in (step.get("arguments") or {}).values():
            if isinstance(value, bool) or value is None:
                continue
            if not isinstance(value, (str, int, float)):
                return True                                  # a structure cannot be checked against his words: not stored
            if not set(_key(str(value)).split()) <= heard:
                return True
    return False


def teach(limit: int = 2, *, decide: Optional[Callable] = None, ask_local=None, ask_cloud=None, client=None,
          cancelled: Callable[[], bool] = lambda: False) -> List[dict]:
    """Ask for lessons on the sentences that stayed not understood, most re-studied first. Returns the rows written."""
    from core import gateway, teacher
    decide = decide or gateway.decide
    stuck = [r for r in lessons().values() if r.get("outcome") in ("unclear", "unsupported") and int(r.get("tries") or 0) >= 2
             and not r.get("taught_as")]
    stuck.sort(key=lambda r: -int(r.get("tries") or 0))
    done = []
    for row in stuck:
        if len(done) >= limit or cancelled():
            break
        wid = lesson_id(row["said"])
        ok, tier = teacher.can_help(wid)
        if not ok:
            continue                                      # local lessons used and the paid teacher is closed or already asked
        rec = teacher.consult({"id": wid}, ask_local=ask_local, ask_cloud=ask_cloud, client=client, tier=tier,
                              requested_by="miss lesson", prompt=_lesson_prompt(row))
        restated = ((rec.get("lesson") or {}).get("hints") or [""])[0].strip() if rec.get("status") == "given" else ""
        outcome, why, plan = row.get("outcome"), f"the {tier} teacher's lesson was {rec.get('status')}", None
        if restated and _key(restated) != _key(row["said"]):
            try:
                decision = decide(restated, cancelled=cancelled)
                outcome, why = judge(decision)
                if outcome == "understood" and not _low_risk(decision):
                    outcome, why = "unclear", "the taught wording plans a step that is not low-risk: needs the owner to show me"
                elif outcome == "understood" and moment_bound(row["said"]):
                    outcome, why = "unclear", "what this sentence points at changes with the moment: it is asked, never fixed to one wording"
                elif outcome == "understood" and _invents(decision, row["said"]):
                    outcome, why = "unclear", "the taught wording names something the owner did not say: not stored"
                plan = decision.brief() if decision is not None else None
            except Exception as exc:  # noqa: BLE001
                outcome, why = "unclear", f"replay of the lesson failed ({type(exc).__name__})"
        learned = outcome == "understood" and bool(restated)
        teacher.mark_outcome(wid, "led_to_learned" if learned else "not_enough_yet")
        new = {**row, "studied_at": time.time(), "outcome": outcome, "why": why, "plan": plan if learned else row.get("plan"),
               "status": "EXPERIMENTAL" if learned else "FAILED", "teacher": tier, "teacher_model": rec.get("model"),
               "lesson": rec.get("id"), "lesson_cost_usd": rec.get("cost_usd", 0.0)}
        if learned:
            new["taught_as"] = restated
        _write(new)
        done.append(new)
    return done


def taught_as(said: str) -> Optional[str]:
    """The stored, verified restatement of exactly this sentence, or None. Local file read only: no model, no teacher."""
    import os
    if os.getenv("JARVIS_TAUGHT_PHRASES", "on").lower() == "off":
        return None
    from core import storage
    key = _key(re.sub(r"^\s*\[(?:Sent from|Spoken into)[^\]]*\]\s*", "", said or ""))
    if not key:
        return None
    try:
        stamp = (storage.DATA / LESSONS).stat().st_mtime_ns, str(storage.DATA)
    except OSError:
        return None
    if _taught_cache["stamp"] != stamp:
        _taught_cache.update(stamp=stamp, rows={k: r["taught_as"] for k, r in lessons().items()
                                                if r.get("taught_as") and r.get("outcome") == "understood" and not moment_bound(k)})
    return _taught_cache["rows"].get(key)
