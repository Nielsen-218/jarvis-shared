"""What the gateway's one understanding call gets to see: the PC right now, the apps that exist, the skills that fit, the
recent conversation and anything still pending. Read-only, cheap and bounded, so the prompt stays about 1,500 tokens.

Skill discovery is over EVERY stored playbook (parameterized ones included, which the old router never offered): the
request's word stems are matched against each playbook's title, keywords, subject and parameter names, and only the best
few are shown to the model, with their parameter names, so it can fill values from the sentence or ask for them.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_STOP = set("""a an the to of in on at for and or but so if then than that this these those it its is are was were be been am i me my mine
you your we us our they them he she his her do does did done doing have has had having can could would should will shall may might must not no
yes ok okay please pls want need like going gonna got let make just really very some any more less all with without about into from by as how what
why when where which who whom hey jarvis thanks thank tell check show give get find out up down now right here there pc computer machine this
thing stuff""".split())
_SKILL_STOP = {"what", "which", "how", "many", "much", "does", "this", "have", "the", "is", "my", "pc", "computer", "installed", "set", "current",
               "number", "size", "answer", "ask", "casually", "informal", "dumb", "question", "sorry", "running", "again", "am"}
MAX_SKILLS = 8
MAX_FACTS = 24          # the no-value fact skills always offered after the matches (a line each)
MAX_TURNS = 6


_TOLD = re.compile(r"\b(?:what|whats|which|how|is|are|am|do|does|did|who|whos|where|when|why|tell|remind|show|check|read|look|find out|"
                   r"any idea|wonder)\b|\?", re.I)


def _asks_to_be_told(text: str) -> bool:
    return bool(_TOLD.search(text or ""))


def stem(word: str) -> str:
    for suffix, keep in (("ings", 4), ("ing", 3), ("ers", 4), ("er", 4), ("ies", 3), ("es", 4), ("s", 3), ("ed", 3)):
        if word.endswith(suffix) and len(word) - len(suffix) >= keep:
            return word[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def stems(text: str, stop=_STOP) -> set:
    return {stem(w) for w in re.findall(r"[a-z0-9][a-z0-9+._-]{1,}", (text or "").casefold().replace("'", "")) if w not in stop and len(w) >= 2}


@dataclass
class SkillCard:
    id: str
    title: str
    params: List[str]
    tier: str
    kind: str
    procedure: bool
    score: float
    book: object = None

    def line(self) -> str:
        extra = []
        if self.params:
            extra.append("needs: " + ", ".join(self.params))
        if self.procedure:
            extra.append("a procedure: does the job")
        elif self.kind == "ui":
            extra.append("works inside an app window")
        if self.tier not in ("", "T0"):
            extra.append(f"tier {self.tier}")
        return f"- {self.id}: {self.title}" + (f" ({'; '.join(extra)})" if extra else "")


@dataclass
class Context:
    said: str
    pc: dict
    pc_text: str
    apps_mentioned: List[str]
    apps_running: List[str]
    skills: List[SkillCard]
    recent: List[Tuple[str, str]]
    pending: Optional[str] = None
    everyday: List[str] = field(default_factory=list)
    folders_mentioned: List[str] = field(default_factory=list)
    built_ms: float = 0.0
    catalogue_size: int = 0
    skill_count: int = 0

    def skill_ids(self) -> List[str]:
        return [s.id for s in self.skills]

    def render(self) -> str:
        """The context as the planner reads it. Ordered for the server's prompt cache (Ollama reuses the longest identical
        prefix of the last prompt): what stays the same from one turn to the next comes first (the owner's folders, after the
        tool list gateway._ask puts in front), what changes with the request or the desktop comes after, and the clock, which
        changes every minute, comes last. Until 2026-11-13 the clock was the first line, so every minute the whole context
        after the system text was read again."""
        lines = [owner_folders(), self.pc_text.strip() or "PC STATE: unknown."]
        if self.apps_running:
            lines.append("running apps: " + ", ".join(self.apps_running[:12]))
        lines.append("apps the request may mean (installed): " + (", ".join(self.apps_mentioned) if self.apps_mentioned else "none named"))
        if self.folders_mentioned:
            lines.append("existing folders the request may mean: " + ", ".join(self.folders_mentioned))
        if not self.apps_mentioned and self.everyday:
            lines.append("Windows apps that may be opened for a goal (calculating, drawing, notes...): " + ", ".join(self.everyday))
        if self.skills:
            lines.append("STORED SKILLS that may fit (id: what it does):\n" + "\n".join(s.line() for s in self.skills))
        else:
            lines.append("STORED SKILLS that may fit: none")
        if self.recent:
            lines.append("RECENT CONVERSATION:\n" + "\n".join(f"{who}: {what[:160]}" for who, what in self.recent[-MAX_TURNS:]))
        if self.pending:
            lines.append("PENDING (waiting for the owner's answer): " + self.pending[:200])
        lines.append("now: " + time.strftime("%A %Y-%m-%d %H:%M"))
        return "\n".join(lines)


def _books() -> list:
    try:
        from tools import pcskills
        return [b for b in pcskills.skills().playbooks.all() if not getattr(b, "stale", False)]
    except Exception:  # noqa: BLE001
        return []


def skill_candidates(text: str, books: Optional[list] = None, k: int = MAX_SKILLS) -> List[SkillCard]:
    """The stored skills most likely meant by `text`, best first, from ALL playbooks (parameterized ones included). Learned
    procedures (source == "guide") are always listed, because they are the owner's own jobs, however the sentence is worded."""
    query = stems(text)
    cards: List[SkillCard] = []
    for b in (books if books is not None else _books()):
        title, subject = getattr(b, "title", "") or "", getattr(b, "subject", "") or ""
        if (getattr(b, "kind", "shell") or "shell") != "shell" or subject.startswith("conv_"):
            continue                    # UI skills need a bound window and the school's executor (not wired yet); conv_ are casual duplicates
        params = list(getattr(b, "params", []) or [])
        own = (stems(title, _STOP | _SKILL_STOP) | {stem(w) for w in getattr(b, "keywords", []) or [] if w not in _SKILL_STOP}
               | stems(subject.replace("_", " "), _STOP | _SKILL_STOP) | {stem(p) for p in params})
        own.discard("")
        hits = query & own
        procedure = getattr(b, "source", "") == "guide"
        score = (len(hits) + 0.5 * len(hits) / len(own)) if own and hits else 0.0
        if procedure:
            score += 0.25                               # always visible, and ahead of a tie
        if score <= 0:
            continue
        cards.append(SkillCard(id=subject or getattr(b, "id", title), title=title, params=params, tier=getattr(b, "tier", "T0") or "T0",
                               kind=getattr(b, "kind", "shell") or "shell", procedure=procedure, score=score, book=b))
    cards.sort(key=lambda c: (-c.score, -getattr(c.book, "runs_ok", 0), c.title))
    chosen = cards[:k]
    # The read-only, no-value fact skills are cheap to list (a line each) and are asked for in words no keyword predicts
    # ("what's powering the visuals", "remind me what I named this pc"): they are offered after the matches whenever the
    # sentence asks for something to be told. For a plain action sentence ("open paint and set the volume to 20") the
    # list only tempted the model to pick a fact skill (eval 2026-11-06). Parameterized and procedure skills are offered
    # only when they fit.
    have = {c.id for c in chosen}
    for b in (books if books is not None else _books()) if _asks_to_be_told(text) else []:
        if len(chosen) >= MAX_SKILLS + MAX_FACTS:
            break
        subject = getattr(b, "subject", "") or ""
        sid = subject or getattr(b, "id", "")
        if sid in have or getattr(b, "params", None) or getattr(b, "kind", "shell") != "shell" or (getattr(b, "tier", "T0") or "T0") != "T0" \
                or subject.startswith("conv_"):
            continue
        chosen.append(SkillCard(id=sid, title=getattr(b, "title", ""), params=[], tier="T0", kind="shell", procedure=False, score=0.0, book=b))
        have.add(sid)
    return chosen


def _recent(history: Optional[list]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    try:
        from core import asides, intelligence
        for turn in (history or [])[-(MAX_TURNS * 2):]:
            role = turn.get("role")
            value = turn.get("content", "")
            if isinstance(value, str):
                text = intelligence._CHANNEL_NOTE.sub("", value)
            else:
                texts = []
                for item in value:
                    b = intelligence._block(item)
                    if b.get("type") == "text" and b["text"].lstrip().startswith(asides.TAG):
                        out.extend(("jarvis", line) for line in asides.lines(b["text"]))   # a reminder he read out
                    elif b.get("type") == "text" and not b["text"].lstrip().startswith(intelligence._INJECTED):
                        # his words without the app server's channel note, as a plain-string turn (a typed turn with
                        # <background> or a frame beside it is stored as blocks: review 2026-11-11)
                        texts.append(intelligence._CHANNEL_NOTE.sub("", b["text"]))
                    elif b.get("type") == "tool_use":
                        texts.append(f"[used {b.get('name')}]")
                text = " ".join(texts)
            if text.strip():
                out.append(("owner" if role == "user" else "jarvis", text.strip()))
    except Exception:  # noqa: BLE001
        pass
    return out[-MAX_TURNS:]


def _pending() -> Optional[str]:
    try:
        from core import pcledger, users
        from tools import pcskills
        g = pcskills.pending_guide(users.current())
        if g:
            return "a guide plan: " + str(g.get("summary", ""))[:160]
        p = next(iter(pcledger.pending()), None)
        if p:
            return f"a {p['kind']} approval for: " + str(p["data"].get("task", ""))[:160]
    except Exception:  # noqa: BLE001
        pass
    return None


def owner_folders() -> str:
    """The owner's real folders, so a path in a plan is never a guess ("C:\\Users\\Owner\\Downloads")."""
    import os
    home = os.path.expanduser("~")
    named = [f"{n}={os.path.join(home, n)}" for n in ("Documents", "Downloads", "Desktop", "Pictures") if os.path.isdir(os.path.join(home, n))]
    return f"owner's folders (use these exact paths; never invent a user name): home={home}, " + ", ".join(named)


def folders_mentioned(text: str, limit: int = 4, per_place: int = 400) -> List[str]:
    """Existing folders one level under the profile, Documents, Desktop and Downloads whose name the request says, ignoring
    spaces and case ('my Jarvis acceptance folder' -> C:\\Users\\me\\Documents\\JarvisAcceptance). Names of 4+ letters only;
    a bounded listing, never a walk."""
    import os
    low = re.sub(r"[^0-9a-z]+", "", (text or "").casefold())
    if not low:
        return []
    home = os.path.expanduser("~")
    found: List[str] = []
    for place in [home] + [os.path.join(home, d) for d in ("Documents", "Desktop", "Downloads")]:
        try:
            with os.scandir(place) as it:
                for n, e in enumerate(it):
                    if n >= per_place:
                        break
                    if e.name.startswith((".", "$")) or not e.is_dir(follow_symlinks=False):
                        continue
                    key = re.sub(r"[^0-9a-z]+", "", e.name.casefold())
                    if len(key) >= 6 and key in low and e.path not in found and e.name.casefold() not in ("documents", "desktop", "downloads"):
                        found.append(e.path)
        except OSError:
            continue
    return found[:limit]


def build(text: str, history: Optional[list] = None, books: Optional[list] = None) -> Context:
    started = time.monotonic()
    try:
        from core import pcstate
        snap = pcstate.snapshot()
        pc_text = pcstate.describe(snap)
    except Exception:  # noqa: BLE001
        snap, pc_text = {"foreground": None, "windows": [], "running": []}, "PC STATE: could not be read."
    try:
        from core import pcsemantic
        mentioned = pcsemantic.mentioned_apps(text)
        catalogue = pcsemantic._catalogue()
        everyday = pcsemantic._everyday_apps()
    except Exception:  # noqa: BLE001
        mentioned, catalogue, everyday = [], [], []
    running: List[str] = []
    try:
        procs = {w["process"].casefold() for w in snap.get("windows", [])}
        for name in catalogue:
            first = re.split(r"[\s\-_.]+", name)[0]
            if len(first) >= 4 and any(first in p for p in procs):
                running.append(name)
    except Exception:  # noqa: BLE001
        pass
    skills = skill_candidates(text, books)
    ctx = Context(said=text, pc=snap, pc_text=pc_text, apps_mentioned=mentioned, apps_running=sorted(set(running + snap.get("running", [])))[:12],
                  skills=skills, recent=_recent(history), pending=_pending(), everyday=everyday, folders_mentioned=folders_mentioned(text),
                  catalogue_size=len(catalogue),
                  skill_count=len(books) if books is not None else len(_books()))
    ctx.built_ms = round((time.monotonic() - started) * 1000, 1)
    return ctx
