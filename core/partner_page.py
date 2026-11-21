"""The Partner page (app/partner.html, /api/partner): his goals, what Jarvis owes him, and the morning report.

A thin layer over core/commitments: every write goes through its writers (add_goal / set_goal), which write only THIS
machine's goals file; readers merge both machines' files. The caller runs these inside users.acting_as(uid), so a person
only ever sees and changes their own goals. A tap is his explicit word: "Yes, that's a goal" is the approval a proposed
goal needs (commitments 2.4), and nothing here activates a goal without that tap.
"""
from __future__ import annotations

import time
from datetime import datetime

from core import commitments as C

RECENT_S = 7 * 86400            # finished commitments shown this long after they resolved
RECENT_MAX = 20
CLOSED_MAX = 30
ACTIONS = ("add", "activate", "drop", "done", "undo", "next_step")


class PageError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _goal_row(g: dict) -> dict:
    return {"id": g["id"], "goal": g.get("goal", ""), "name": C.name_of(g), "why": g.get("why", ""),
            "next_step": g.get("next_step", ""), "state": g.get("state"), "source": g.get("source", ""),
            "created": g.get("created"), "updated": g.get("updated"),
            "closed": g.get("finished") if g.get("state") == "done" else g.get("dropped"),
            "was": g.get("was") if g.get("was") in ("proposed", "active") else "active"}


def _item_row(c: dict, now: float) -> dict:
    link = c.get("link") or {}
    state = c.get("state")
    outcome = c.get("outcome") or (state if state in ("done", "failed") else "")
    return {"id": c["id"], "what": c.get("what", ""), "state": state, "outcome": outcome, "detail": c.get("detail", ""),
            "kind": link.get("kind", ""), "created": c.get("created"), "due": c.get("due"), "resolved": c.get("resolved"),
            "overdue": state in ("open", "in_progress") and now > float(c.get("due") or 0),
            "waiting_on_you": c.get("detail") in C.WAITING, "machine": c.get("machine", "")}


def page_data(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    ts = now.timestamp()
    report = C.report_now(now)                     # resolves commitments first (as every reader does); marks nothing
    goals = [_goal_row(g) for g in C.goals()]
    closed = sorted((g for g in goals if g["state"] in ("done", "dropped")), key=lambda g: -float(g["closed"] or g["updated"] or 0))
    live = [_item_row(c, ts) for c in C.items(("open", "in_progress"))]
    recent = [_item_row(c, ts) for c in C.items(("done", "failed", "reported"))
              if ts - float(c.get("resolved") or c.get("updated") or 0) < RECENT_S]
    recent.sort(key=lambda r: -float(r["resolved"] or 0))
    said = C.last_report()
    return {"now": ts,
            "goals": {"active": [g for g in goals if g["state"] == "active"],
                      "proposed": [g for g in goals if g["state"] == "proposed"],
                      "closed": closed[:CLOSED_MAX]},
            "owed": {"open": list(reversed(live)), "recent": recent[:RECENT_MAX]},
            "report": {"said": said, "said_today": bool(said) and datetime.fromtimestamp(said["at"]).date() == now.date(),
                       "now": report},
            "waiting": C.waiting_on_him(),
            "self_review": _self_review(now)}


def _self_review(now: datetime) -> dict | None:
    """'What I got wrong yesterday' (core.selfreview): read-only here, nothing is marked said."""
    try:
        from core import selfreview
        return selfreview.page_row(now)
    except Exception as error:  # noqa: BLE001 - the card is left out rather than breaking the page
        print(f"[partner] self-review: {type(error).__name__}: {error}")
        return None


def _text(body: dict, key: str, required: bool) -> str:
    value = body.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise PageError(400, f"'{key}' must be text.")
    value = " ".join(value.split())
    if len(value) > C.MAX_TEXT:
        raise PageError(400, f"Keep it under {C.MAX_TEXT} characters.")
    if required and len(value) < 2:
        raise PageError(400, "Type the goal first.")
    return value


def _find(gid) -> dict:
    if not isinstance(gid, str) or not gid:
        raise PageError(400, "Which goal? (no id)")
    for g in C.goals():
        if g["id"] == gid:
            return g
    raise PageError(404, "That goal isn't on your list any more.")


def _same_goal(text: str) -> dict | None:
    """A typed goal that IS an open goal: the same words as its text, or one of its names said whole ("Creole"). Never a
    looser match: "Save money" is not "Save money for a house", and a keyword never activates a broader proposal (review
    2026-11-21: the spoken rule, every word inside one goal's words, is too loose for a typed box)."""
    want, low = C._words(text), " ".join(text.casefold().split())
    if not want:
        return None
    hits = [g for g in C.goals(("proposed", "active"))
            if want == C._words(g.get("goal", "")) or low in [a for a in g.get("aliases", []) if a]
            or low == " ".join(str(g.get("spoken", "")).casefold().split())]
    return hits[0] if len(hits) == 1 else None


def page_action(body: dict) -> str:
    """One tap or one typed line -> what Jarvis says back. Raises PageError (status, message) for anything it won't do."""
    if not isinstance(body, dict):
        raise PageError(400, "Send a JSON object.")
    action = body.get("action")
    if action not in ACTIONS:
        raise PageError(400, "Unknown action.")
    now = time.time()
    if action == "add":
        text = _text(body, "text", True)
        why = _text(body, "why", False)
        step = _text(body, "next_step", False)
        same = _same_goal(text)
        if same is not None:
            if same.get("state") == "proposed":
                C.set_goal(same, state="active", confirmed=now, **({"why": why} if why else {}),
                           **({"next_step": step} if step else {}))
                return f"Good, {C.name_of(same)} is a real goal now."
            return f"That's already one of your goals: {C.name_of(same)}."
        if len(C.goals(("proposed", "active"))) >= C.MAX_GOALS:
            raise PageError(409, "That's a lot of goals already. Finish or drop one first.")
        C.add_goal(text, why=why, state="active", source="app", next_step=step)
        return f"Got it. New goal: {text}."
    g = _find(body.get("id"))
    state = g.get("state")
    if action == "activate":
        if state != "proposed":
            raise PageError(409, "That one isn't waiting for your answer.")
        C.set_goal(g, state="active", confirmed=now)
        return f"Good, {C.name_of(g)} is a goal now."
    if action == "drop":
        if state not in ("proposed", "active"):
            raise PageError(409, "That goal is already closed.")
        C.set_goal(g, state="dropped", dropped=now, was=state)
        return (f"Fine, {C.name_of(g)} isn't a goal." if state == "proposed" else f"Fine, I've dropped {C.name_of(g)}.")
    if action == "done":
        if state != "active":
            raise PageError(409, "Only an active goal can be marked done.")
        C.set_goal(g, state="done", finished=now, was=state)
        return f"Well done. I've marked {C.name_of(g)} as done."
    if action == "undo":
        if state not in ("dropped", "done"):
            raise PageError(409, "There's nothing to undo on that goal.")
        back = g.get("was") if g.get("was") in ("proposed", "active") else "active"
        C.set_goal(g, state=back, restored=now)
        return f"Done, {C.name_of(g)} is back" + (" as one of your goals." if back == "active" else " on the list I'm guessing at.")
    # next_step
    if state not in ("proposed", "active"):
        raise PageError(409, "That goal is closed; bring it back first.")
    step = _text(body, "next_step", False)
    C.set_goal(g, next_step=C._clip(step))
    return f"Noted. Next step for {C.name_of(g)}: {step}." if step else f"Cleared the next step for {C.name_of(g)}."
