"""Newly learned skills always approve (owner decision, 2026-11-12).

A learned method or skill that PASSED its sealed exam is approved automatically and goes into use; the owner is told once,
in one short line, and can list what was learned or remove one by voice.

What this does NOT change:
  * verification before storing (procedures.Library refuses anything less than a full exam);
  * the weekly retest with demotion (core/acquire.retest_learned) and the use log (core/independence);
  * permission: core/authz still decides on EVERY action when the skill runs, and the owner-named irreversible rule stays.
    Auto-approval trusts a verified skill; it never skips the permission check.
The approval row records who approved ("auto") and the exam evidence it was granted on, bound to the exact code hash.
"""
import json
import os
import re
import time

from core import storage
from core.words import plural

PHRASES = {"close_site_tab": "close a website's tab that is not in front", "close_foreground": "close the window in front",
           "close_app": "close an app by its name", "use_app": "press a button inside an app",
           "type_text": "type text into an app", "save_document": "save a document from an app",
           "open_path": "open a file or folder"}


def _file():
    return storage.DATA / "learning" / "auto-approved.json"


def _load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(rows) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(rows, indent=1), encoding="utf-8")


def enabled() -> bool:
    return os.getenv("JARVIS_AUTO_APPROVE", "on").lower() != "off"


def what(tool) -> str:
    """The skill in the owner's words: 'close the window in front'."""
    return PHRASES.get(tool) or str(tool).replace("_", " ").replace(".", " ").strip()


def removed_tools() -> dict:
    """{tool: when} for learned skills the OWNER removed: they are not learned again behind his back."""
    return {r["tool"]: r["removed"]["at"] for r in _load().values() if (r.get("removed") or {}).get("by") == "owner"}


def auto_approve(kind, sid, tool, version, evidence, notify=None, now=None) -> dict | None:
    """Approve a just-learned method ("method") or skill ("skill") on the strength of its exam. -> the record, or None
    when it must not be approved (switch off, the exam was not passed in full, or the owner removed this skill before)."""
    passed, total = int((evidence or {}).get("passed") or 0), int((evidence or {}).get("total") or 0)
    if not enabled() or total <= 0 or passed != total or tool in removed_tools():
        return None
    # the WHOLE exam, not a shortened one: a lab job that died after 3 of 12 worlds is not "3/3" (review 2026-11-12)
    from core import acquire as _acquire
    if total != len(_acquire.HELD_OUT_SEEDS):
        return None
    from core import skillcontracts as _sc
    if tool in _sc.PRODUCTION_KINDS:       # a desktop kind is approved only with its passed production check in the evidence
        pc = (evidence or {}).get("production") or {}
        if int(pc.get("total") or 0) <= 0 or pc.get("passed") != pc.get("total"):
            return None
    if kind != "method":
        from core import compose as _compose
        if evidence.get("unseen") != f"{len(_compose.UNSEEN_SEEDS)}/{len(_compose.UNSEEN_SEEDS)}":
            return None
    exam = {"passed": passed, "total": total, "judge": evidence.get("judge", "")}
    if evidence.get("production"):
        exam["production"] = evidence["production"]
    if evidence.get("unseen"):
        exam["unseen"] = evidence["unseen"]
    if kind == "method":
        from core import acquire
        row = acquire.approve(sid, version, by="auto", exam=exam)
    else:
        from core import skillspec
        row = skillspec.approve(sid, version, by="auto", exam=exam)
    rec = {"id": sid, "kind": kind, "tool": tool, "version": row["version"], "source_sha": row["source_sha"], "by": "auto",
           "exam": exam, "at": time.time() if now is None else now, "told": False}
    rows = _load()
    rows[sid] = rec
    _save(rows)
    tell(sid, notify)
    return rec


def line(rec) -> str:
    return f"I learned how to {what(rec['tool'])}; say undo that skill to remove it."


def tell(sid, notify=None) -> str:
    """Tell the owner ONCE about one learned skill. -> the line that was sent, '' when it was already told."""
    rows = _load()
    rec = rows.get(sid)
    if not rec or rec.get("told"):
        return ""
    text = line(rec)
    if notify is None:
        from core import notify as _n
        notify = _n.notify
    try:
        notify("Jarvis learned a skill", text, None, "jarvis")
    except Exception:  # noqa: BLE001 - not told: it is tried again by tell_pending()
        return ""
    rec["told"] = True
    _save(rows)
    return text


def tell_pending(notify=None) -> int:
    return sum(1 for sid, r in _load().items() if not r.get("told") and not r.get("removed") and tell(sid, notify))


def _state(rec, active, gone) -> str:
    if rec.get("removed"):
        return "removed"
    if (gone.get(rec["id"]) or {}).get("source_sha") == rec.get("source_sha"):
        return "failed its retest"
    return "in use" if rec["id"] in active else "not in use"


def recent(days=30, now=None) -> list:
    """What was learned lately, newest first: every auto-approved record plus anything active that predates this log."""
    from core import acquire
    now = time.time() if now is None else now
    try:
        active = {i["id"]: i for i in acquire._active()}
    except Exception:  # noqa: BLE001 - a broken store never breaks the list
        active = {}
    gone = acquire.demoted()
    rows = [{**r, "state": _state(r, active, gone)} for r in _load().values() if now - float(r.get("at") or 0) <= days * 86400]
    seen = {r["id"] for r in _load().values()}
    rows += [{"id": i["id"], "kind": i["kind"], "tool": i["tool"], "version": i["version"], "source_sha": i["source_sha"],
              "by": "owner", "at": 0, "state": "in use"} for i in active.values() if i["id"] not in seen]
    return sorted(rows, key=lambda r: -float(r.get("at") or 0))


def spoken_recent(days=30, now=None) -> str:
    rows = recent(days, now)
    if not rows:
        return "I have not learned any new skill lately."
    parts = [f"how to {what(r['tool'])} ({r['state']}" + (", approved automatically after " + plural(r["exam"]["total"], "exam task")
                                                          if r.get("by") == "auto" and r.get("exam") else "") + ")" for r in rows[:6]]
    return (f"I have {plural(len(rows), 'learned skill')}: " + "; ".join(parts) +
            ". Say undo that skill, or remove the skill and its name, to take one out.")


def _words(text) -> set:
    stem = lambda w: re.sub(r"(?:ing|es|e|s)$", "", w) if len(w) > 4 else w.rstrip("s") if len(w) > 3 else w   # closing ~ close
    return {stem(w) for w in re.findall(r"[a-z0-9]+", str(text).casefold())} - {"the", "a", "an", "skill", "that", "to", "how", "learned", "last",
                                                                    "one", "it", "this", "my", "your", "for", "by", "its", "in", "of"}


def remove(name="", now=None) -> str:
    """The owner's "undo that skill" / "remove the skill for closing apps". Withdraws the approval (the code and evidence
    stay on disk), so the step goes back to the built-in way, and the skill is not learned again unasked."""
    from core import acquire, skillspec
    rows = [r for r in recent(days=3650, now=now) if r["state"] == "in use"]
    if not rows:
        return "There is no learned skill in use to remove."
    want = _words(name)
    if want:
        scored = [(len(want & (_words(what(r["tool"])) | _words(r["tool"]) | _words(r["id"]))), r) for r in rows]
        best = max(s for s, _ in scored)
        hits = [r for s, r in scored if s == best and s > 0]
        if not hits:
            return f"I have no learned skill like that. {spoken_recent(3650, now)}"
        if len(hits) > 1:
            return "Which one: " + ", or ".join("how to " + what(r["tool"]) for r in hits[:4]) + "?"
        rec = hits[0]
    else:
        rec = rows[0]                                        # "that skill": the newest one
    if rec["kind"] == "method":
        acquire.revoke(rec["id"])
    else:
        skillspec.revoke(rec["id"])
    log = _load()
    row = log.setdefault(rec["id"], {k: rec[k] for k in ("id", "kind", "tool", "version", "source_sha", "by", "at")})
    row["removed"] = {"at": time.time() if now is None else now, "by": "owner"}
    row["told"] = True
    _save(log)
    return f"Removed. I no longer use my learned way to {what(rec['tool'])}; the built-in way is back, and I will not learn it again unless you ask."


_REMOVE = re.compile(r"^(?:please\s+|jarvis[,\s]+)*(?:undo|remove|forget|delete|drop|unlearn)\s+(?:that|the|this|your)\s+"
                     r"(?:(?:last|new|newest|latest|learned)\s+)*skill\b(?P<rest>.*)$", re.I)
_LIST = re.compile(r"^(?:please\s+|jarvis[,\s]+)*(?:what(?:\s+new)?(?:\s+skills?)?\s+(?:did|have)\s+you\s+learn(?:ed)?\b.{0,30}|"
                   r"which\s+(?:new\s+)?skills\s+(?:did|have)\s+you\s+learn(?:ed)?\b.{0,30}|"
                   r"(?:list|show|tell)\s+(?:me\s+)?(?:your|the)\s+(?:(?:recent|recently|new|newly)\s+)?learned\s+skills\b.{0,20})$", re.I)


_INDEPENDENCE = re.compile(
    r"^(?:(?:please|jarvis|hey jarvis)[,\s]+)*(?:"
    r"(?:just\s+)?how\s+independent\s+(?:are\s+you|have\s+you\s+(?:got|become|gotten))(?:\s+(?:now|these days|lately|really|today|at this point))?|"
    r"(?:(?:give|show|tell)\s+me\s+|what(?:'s|\s+is)\s+)?(?:the|your)\s+independence\s+report|independence\s+report|"
    r"how\s+much\s+(?:do|can|did)\s+you\s+do\s+(?:on\s+your\s+own|by\s+yourself|without\s+(?:the\s+cloud|(?:any\s+|the\s+)?paid\s+(?:models?|help|teachers?|apis?|calls?)|(?:any\s+)?outside\s+help))|"
    r"how\s+many\s+paid\s+(?:calls|requests)\s+(?:did|have)\s+you\s+(?:make|made)(?:\s+(?:lately|this\s+month|so\s+far|today|recently))?"
    r")$", re.I)


_CHANNEL = re.compile(r"^\s*\[Sent from their Jarvis phone app[^\]]*\]\s*", re.I)


def voice(text):
    """-> (tool name, arguments) for the two spoken commands about his own learned skills, else None. Narrow on purpose:
    a plain "undo that" (a PC change) or "what did you learn about X" (a study topic) is not taken."""
    said = " ".join(_CHANNEL.sub("", str(text or "")).split()).strip(" .!?")   # typed on the phone: the app's note comes first
    if _INDEPENDENCE.match(said):
        return "independence_report", {}
    m = _REMOVE.match(said)
    if m:
        rest = re.sub(r"^\s*(?:you\s+(?:just\s+)?learned|for|to|of|about|called|named)\b", "", m.group("rest").strip(" ,"), flags=re.I)
        return "remove_learned_skill", {"name": "" if re.fullmatch(r"\s*(?:you\s+(?:just\s+)?learned)?\s*", rest, re.I) else rest.strip()}
    if _LIST.match(said) and not re.search(r"\blearn(?:ed)?\s+(?:about|from|in|at)\b", said, re.I):
        return "learned_skills", {}
    return None
