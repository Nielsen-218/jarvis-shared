"""PC-control skills for JARVIS: troubleshoot, find/back up files, install from a GitHub link, uninstall, clean up, undo.

This is a thin layer over the `jarvis_pc` package (docs/JARVIS-PC-CONTROL-SKILLS.md). Everything goes through its
permission gate (T0 auto, T1 reversible, T2 always ask, T3 never), its audit log and its change journal.

Reads and backups just answer. Anything that installs, removes or undoes is two-phase: the first call returns the exact
plan as NEEDS_CONFIRMATION, and it only runs on a later call with confirmed=true IF the person's next real message was a
clear yes (core.approvals checks that, the AI cannot). The approval covers only the prompts the shown plan declared.

Skills are learned, not API-dependent: pc_learned_skill runs stored playbooks with no model at all. A teacher model may
be registered with set_teacher() to master a NEW skill once (daily-budget capped, output untrusted and verified before it
is kept); with no teacher registered nothing here ever calls a model.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

from tools.registry import tool

_skills = None
_teacher: Optional[Callable[[str, str], str]] = None
_TEACHER_BUDGET = 3  # teacher calls per day


def root() -> str:
    return os.environ.get("JARVIS_PC_ROOT") or (r"C:\Jarvis" if os.name == "nt" else os.path.expanduser("~/.jarvis_pc"))


def set_teacher(fn: Optional[Callable[[str, str], str]], daily_budget: int = 3) -> None:
    """The brain registers a (task, context) -> text function here to let JARVIS master a skill. Optional."""
    global _teacher, _TEACHER_BUDGET
    _teacher, _TEACHER_BUDGET = fn, daily_budget


def has_install(name: str) -> bool:
    """True if JARVIS itself installed an app by this name (a folder under its apps dir). Read-only: creates nothing, so
    the existing uninstall_program can ask without side effects."""
    import re
    name = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._+-]{0,100}", name):
        return False
    apps = os.path.join(root(), "apps")
    try:
        return any(f != "_downloads" and os.path.isdir(os.path.join(apps, f)) and (f.lower() == name or f.lower().startswith(name + "-"))
                   for f in os.listdir(apps))
    except OSError:
        return False


def skills():
    global _skills
    if _skills is None:
        from jarvis_pc.brain import PCSkills
        from jarvis_pc.windows import make_pre_change
        _skills = PCSkills(root(), pre_change=make_pre_change())
    return _skills


def _two_phase(prepared, confirmed: bool, direct_verb: str = "", direct_word: str = "") -> str:
    """Ask first; run only when the person's own next message approved exactly this plan, or when the OWNER's own command
    asked for exactly this (owner 2026-11-07: "when I say do something there are no rules": his command is the approval)."""
    from core import approvals, authz, users

    if prepared.blocked:
        return prepared.blocked
    uid = users.current()
    owner = authz.is_owner(uid)
    said_so = bool(direct_verb) and (prepared.allows_direct or owner) and approvals.direct(uid, direct_verb, direct_word)
    if said_so or (confirmed and approvals.consume(uid, prepared.key)):
        return prepared.run()
    return approvals.request(uid, prepared.key, prepared.summary)


def _repo_word(source: str) -> str:
    """The repo's own name in a GitHub link (owner said 'install <repo>' or pasted the link)."""
    parts = [p for p in re.split(r"[/?#]+", (source or "").strip()) if p]
    return parts[-1].removesuffix(".git") if parts else ""


# ---------------------------------------------------------------- read-only
@tool("Diagnose a PC problem (slow, crashing, no internet, disk full, command not found). Read-only checks of event logs, "
      "crash dumps, disk, memory, startup, network, PATH and drivers; says what changed recently first and what could not be "
      "checked. Never changes anything. For a quick CPU/memory/battery number use get_system_status; for hidden or unwanted "
      "software use security_checkup.",
      {"symptom": {"type": "string", "description": "what is wrong, in plain words"},
       "everything": {"type": "boolean", "description": "run every check, not just the ones that fit the symptom"}},
      required=["symptom"], slow=True)
def pc_diagnose(symptom: str, everything: bool = False) -> str:
    return skills().diagnose(symptom, bool(everything))


@tool("Find files by name in a folder (for example *.pdf). Read-only.",
      {"folder": {"type": "string"}, "pattern": {"type": "string", "description": "glob such as *.pdf"}})
def pc_find_files(folder: str, pattern: str) -> str:
    return skills().find_files(folder, pattern)


@tool("Find duplicate files (same content) in a folder and say how much space they waste. Read-only: deletes nothing.",
      {"folder": {"type": "string"}}, slow=True)
def pc_find_duplicates(folder: str) -> str:
    return skills().find_duplicates(folder)


@tool("Back up a file or folder: copies it, checks every file's SHA-256, never overwrites, skips credential folders. "
      "The backup can be undone with pc_undo.",
      {"source": {"type": "string"}}, slow=True)
def pc_backup(source: str) -> str:
    return skills().backup(source)


@tool("Prove a backup is intact: restores it to a scratch folder and checks every file against the hashes recorded when "
      "it was made.", {"backup_path": {"type": "string"}}, slow=True)
def pc_restore_test(backup_path: str) -> str:
    return skills().restore_test(backup_path)


# ---------------------------------------------------------------- two-phase (install / remove / undo)
@tool("Install a program from a GitHub LINK (https://github.com/owner/repo). Checks the repo's trust, downloads only a Windows "
      "release asset, verifies its checksum, scans it for viruses, installs it and proves it landed; rolls back on failure. "
      "Returns NEEDS_CONFIRMATION with the exact plan: read it to the user, and only if they answer yes call again with "
      "confirmed=true. Only for GitHub links: for a plain program name like 'Discord' or 'VLC' use install_program.",
      {"source": {"type": "string", "description": "https://github.com/owner/repo"},
       "confirmed": {"type": "boolean"}}, required=["source"], slow=True)
def pc_install(source: str, confirmed: bool = False) -> str:
    if "github.com" not in (source or "").lower():
        return ("pc_install is for GitHub links (https://github.com/owner/repo). For an ordinary program name, "
                "use install_program.")
    from core import users
    prepared = skills().prepare_install(source)
    out = _two_phase(prepared, bool(confirmed), "install", _repo_word(source))
    uid = users.current()
    if out.startswith("NEEDS_CONFIRMATION"):            # the owner's next yes runs exactly this plan (core.pcfast -> run_pending)
        _await_yes(prepared, uid)
    else:
        _PENDING.pop(uid, None)
    return out


GITHUB_INSTALL = re.compile(r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:please\s+)?(?:install|set\s*up|setup|get\s+me|download\s+and\s+install|grab)\s+"
                            r"(?:this|that|the\s+(?:tool|program|app)\s+(?:from|at))?\s*(https?://github\.com/[\w.-]+/[\w.-]+?)(?:\.git)?/?\s*(?:please)?[.!]?$", re.I)


def github_install_request(text: str) -> str:
    """The GitHub link in an 'install https://github.com/owner/repo' sentence, or '' (core.pcfast's deterministic route)."""
    m = GITHUB_INSTALL.match((text or "").strip())
    return m.group(1) if m else ""


_REPO_LINK = re.compile(r"https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+[^\s]*", re.I)
_LOOSE_ACT = re.compile(r"\b(?:install|set\s*(?:it|this|that)?\s*up|setup|get|grab|download|do|follow|run|build|make|try|"
                        r"put|add|deploy|configure|work|working|going|running)\b", re.I)
_NOT_INSTALL = re.compile(r"\b(?:read|what(?:'?s| is| does)|explain|summari[sz]e|tell me about|look up|review|is it safe|"
                          r"star|fork|open|show|uninstall|remove|delete)\b", re.I)


def github_repo_request(text: str) -> str:
    """A GitHub repository link with ANY wording that asks for it to be installed or set up ("can you get this going <link>",
    "<link> set this up for me"): the repository's address, or ''. Owner 2026-11-07: he won't remember exact wording."""
    exact = github_install_request(text)
    if exact:
        return exact
    m = _REPO_LINK.search(text or "")
    if not m or not _LOOSE_ACT.search((text or "").replace(m.group(0), " ")) or _NOT_INSTALL.search((text or "").replace(m.group(0), " ")):
        return ""
    from core import screenguide
    return screenguide.github_repo(m.group(0).rstrip(".,!?)"))


_PENDING: dict = {}   # uid -> the plan the person was just shown and asked about (a guide or a learned procedure), so their yes runs exactly it


def _await_yes(prepared, uid: str) -> None:
    import time
    _PENDING[uid] = {"key": prepared.key, "run": prepared.run, "summary": prepared.summary, "t": time.time()}


def pending_guide(uid: str):
    """The plan this person was asked about in the last ten minutes, for core.pcfast to run on their yes."""
    import time
    g = _PENDING.get(uid)
    return g if g and time.time() - g["t"] < 600 else None


def withdraw_pending(uid: str, before=None) -> bool:
    """The plan waiting now, if it is not `before` (pending_guide() taken before the turn), was shown by a turn whose
    reply was never said (he cut in): his next yes must not run it."""
    g = _PENDING.get(uid)
    if g is not None and g is not before:
        _PENDING.pop(uid, None)
        return True
    return False


def run_pending(uid: str) -> str:
    """Their own yes arrived (core.approvals saw it): run exactly the plan they were shown, once."""
    from core import approvals
    entry = _PENDING.pop(uid, None)
    if not entry:
        return "Nothing is waiting for a yes."
    if not approvals.consume(uid, entry["key"]):
        return "That plan is no longer waiting for approval; ask for it again if you still want it."
    return entry["run"]()


@tool("Carry out step-by-step instructions that came from somewhere else: a ChatGPT or Claude answer, a README, a tutorial or a "
      "forum post the user pasted. JARVIS reads the commands in it (untrusted text: prompt injection and dangerous lines block "
      "it), looks at what is already on the PC, shows the exact plan with each step's permission tier, runs it only after the "
      "user's yes, proves it with a functional check (not an exit code) and keeps a learned skill when it passes. Returns "
      "NEEDS_CONFIRMATION with the plan: read it to the user, and only if they answer yes call again with confirmed=true. For a "
      "plain program name use install_program; for a GitHub repository link use pc_install.",
      {"goal": {"type": "string", "description": "what the instructions achieve, in a few words"},
       "instructions": {"type": "string", "description": "the pasted instructions, commands included"},
       "confirmed": {"type": "boolean"}}, required=["goal", "instructions"], slow=True)
def pc_follow_guide(goal: str, instructions: str, confirmed: bool = False) -> str:
    from core import users
    prepared = skills().prepare_guide(goal, instructions)
    uid = users.current()
    out = _two_phase(prepared, bool(confirmed), "guide", goal)
    if out.startswith("NEEDS_CONFIRMATION"):
        _await_yes(prepared, uid)
    else:
        _PENDING.pop(uid, None)
    return out


@tool("Uninstall a program that JARVIS itself installed from a GitHub link (it lives in JARVIS's own apps folder). Nothing is "
      "deleted: it moves to quarantine and can be put back with pc_undo. If the user directly said to uninstall it, it just "
      "happens; otherwise NEEDS_CONFIRMATION. For any other program (installed the normal way, from winget or the Store) use "
      "uninstall_program.",
      {"target": {"type": "string"}, "confirmed": {"type": "boolean"}}, required=["target"], slow=True)
def pc_uninstall(target: str, confirmed: bool = False) -> str:
    prepared = skills().prepare_uninstall(target, winget=False)
    if prepared.blocked:
        return prepared.blocked + " If it was installed the normal way, use uninstall_program."
    return _two_phase(prepared, bool(confirmed), "uninstall", target)


@tool("Clean up leftovers JARVIS made (installers for apps already extracted, unfinished downloads, empty folders). Moves "
      "them to quarantine; nothing is deleted. NEEDS_CONFIRMATION first.",
      {"confirmed": {"type": "boolean"}}, required=[], slow=True)
def pc_cleanup(confirmed: bool = False) -> str:
    return _two_phase(skills().prepare_cleanup(), bool(confirmed), "clean")


@tool("PERMANENTLY delete what is in JARVIS's quarantine. This cannot be undone. When the owner's own message told you to empty "
      "or purge the quarantine it runs at once (his command is the approval); otherwise it returns the plan: read it back and only "
      "call again with confirmed=true after a clear yes.",
      {"confirmed": {"type": "boolean"}}, required=[], slow=True)
def pc_purge(confirmed: bool = False) -> str:
    return _two_phase(skills().prepare_purge(), bool(confirmed), "purge", "quarantine")


@tool("Undo the last install, uninstall, cleanup or backup JARVIS did (the whole run, including the downloaded installer). "
      "NEEDS_CONFIRMATION first because it removes files JARVIS created.",
      {"confirmed": {"type": "boolean"}}, required=[], slow=True)
def pc_undo(confirmed: bool = False) -> str:
    return _two_phase(skills().prepare_undo(), bool(confirmed), "undo")


# ---------------------------------------------------------------- learned skills (offline)
@tool("Run a skill JARVIS has already learned for this task (stored, verified playbooks; no AI or API call). If none exists "
      "and a teacher model is configured, it may learn the skill once, but only keeps it if it verifies. Commands still go "
      "through the permission gate. Some skills need values, for example {\"dir\": \"C:\\Users\\me\\Downloads\"}: "
      "if it says the skill needs something, call again with values.",
      {"task": {"type": "string"},
       "values": {"type": "string", "description": "optional JSON object of the values a learned skill asks for"}},
      required=["task"], slow=True)
def pc_learned_skill(task: str, values: str = "") -> str:
    import json
    params = {}
    if (values or "").strip():
        try:
            data = json.loads(values)
            params = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except ValueError:
            return "values must be a JSON object such as {\"dir\": \"C:\\Users\\me\"}"
    return skills().solve(task, _teacher, _TEACHER_BUDGET, params)


def _spoken(title: str, output: str):
    """One short sentence from a playbook's raw output.  A bare byte count becomes GB; IPv4 listings keep the addresses."""
    import re
    output = (output or "").replace("\x00", "")           # wsl.exe prints UTF-16: a NUL between every letter reads as "U b u n t u"
    text = " ".join(output.split())
    if not text:
        return None
    lowered = title.lower()
    if re.fullmatch(r"\d{8,}", text):                      # a byte count (free space, RAM, disk size)
        gb = int(text) / 2 ** 30
        amount = f"{gb:.0f} GB" if gb >= 100 else f"{gb:.1f} GB"
        what = ("free on your C drive" if "free" in lowered else "of memory" if ("ram" in lowered or "memory" in lowered)
                else "in total" if ("total" in lowered or "big" in lowered) else "")
        return f"{amount} {what}".strip() + "."
    if re.search(r"\bip\b", lowered):
        found = list(dict.fromkeys(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", output or "")))
        if found:
            return "Your IP address" + ("es are " if len(found) > 1 else " is ") + ", ".join(found[:3]) + "."
    head = title[:1].upper() + title[1:]
    names = _listing_names(output or "")
    if names:                                              # a Get-ChildItem table: the names, not 'Mode LastWriteTime Length'
        shown = ", ".join(names[:25]) + (f" and {len(names) - 25} more" if len(names) > 25 else "")
        return f"{head}: {shown}."
    return f"{head}: {text[:240]}" + ("..." if len(text) > 240 else "") + "."


_LISTING_ROW = re.compile(r"^\s*[dla\-][a-z\-]{4,5}\s+\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}\s+\d{1,2}:\d{2}(?:\s*[AP]M)?\s+(?:\d+\s+)?(\S.*?)\s*$", re.I)


def _listing_names(output: str) -> list:
    """Item names from PowerShell's default Get-ChildItem table (Mode, LastWriteTime, Length, Name)."""
    if not re.search(r"^\s*Mode\s+LastWriteTime\s+(?:Length\s+)?Name\s*$", output, re.M):
        return []
    return [m.group(1) for line in output.splitlines() if (m := _LISTING_ROW.match(line))]


def answer_with_playbook(book):
    """Run ONE matched, proven, read-only playbook through the permission gate and return a spoken sentence.

    Returns None only when nothing could be said; a playbook that fails verification returns an honest sentence instead,
    so nothing downstream invents an answer."""
    s = skills()
    if getattr(book, "tier", "T0") != "T0":              # a learned procedure changes the PC: shown first, replayed on the owner's yes
        from core import approvals, users
        prepared = s.prepare_replay(book)
        uid = users.current()
        asked = approvals.request(uid, prepared.key, prepared.summary)
        if asked.startswith("NOT_ASKED"):
            return "I'm still waiting for your answer about something else; tell me that first."
        _await_yes(prepared, uid)
        return f"I learned how to do that before. {prepared.summary}\nWant me to go ahead?"
    outputs = []
    report = s.playbooks.run(book, s._guard(), run_fn=s.run_fn, outputs=outputs)
    from core import independence                         # a stored playbook: local code only, no model and no API call
    independence.log_use(getattr(book, "subject", "") or book.title, "playbook", report.result == "PASS")
    from core import misses                               # the turn's own sentence (core.misses.turn); none outside an owner turn
    if report.result != "PASS":
        misses.fail("step not verified: learned skill did not verify", "skill")
        return "I tried the skill I learned for that, but it did not verify this time, so I won't guess."
    misses.ok("skill")
    return _spoken(book.title, chr(10).join(o.strip() for o in outputs if o.strip()))


# ---------------------------------------------------------------- the school (automatic skill learning)
_school = None


def school():
    """The School, built once. Its learned skills go into the same store pc_learned_skill reads."""
    global _school
    if _school is None:
        from jarvis_pc.school.live import build_school
        _school = build_school(root(), playbooks=skills().playbooks)
    return _school


def capability_summary() -> str:
    """What Jarvis can really do on this PC, read from the stores that prove it (never from a model's memory). Live
    2026-11-05 "What PC control skills do you have?" got "I need to approve a cloud planning budget first"."""
    import collections
    try:
        books = [b for b in skills().playbooks.all() if not getattr(b, "stale", False)]
    except Exception:  # noqa: BLE001
        books = []
    kinds = collections.Counter(getattr(b, "kind", "shell") for b in books)
    proven = [b for b in books if int(getattr(b, "runs_ok", 0) or 0) > 0]
    try:
        mastered = len(school().transcript.mastered_ids())
    except Exception:  # noqa: BLE001
        mastered = 0
    import re
    examples = [re.sub(r"\{[^}]*\}", "a folder you name", b.title)
                for b in sorted(proven or books, key=lambda b: -int(getattr(b, "runs_ok", 0) or 0))][:6]
    parts = ["On this PC I can open, close and switch between your apps, including your Steam games; type into Notepad and save "
             "files; find your projects and files; open folders; set the volume and control media; read what is on the screen; "
             "run terminal commands with a safety check; and look into why the PC is slow."]
    if books:
        parts.append(f"I also have {len(books)} learned skills stored on the PC ({kinds.get('shell', 0)} command skills, "
                     f"{kinds.get('ui', 0)} on-screen ones), {len(proven)} of them already used for real. For example: "
                     + "; ".join(examples) + ".")
    if mastered:
        parts.append(f"My PC school records {mastered} subjects as passed on fresh offline exams. A passed exam is not the "
                     "same as never failing on your PC, so I still check each result before I say it worked.")
    return " ".join(parts)


@tool("What JARVIS can really do on this PC: the built-in PC actions and the learned, verified skills, from the stores that "
      "prove them. Use for 'what PC skills do you have', 'what have you mastered', 'what can you do on my computer'. Read-only.",
      {}, required=[])
def pc_capabilities() -> str:
    return capability_summary()


@tool("How JARVIS's PC-skill school is going: high school (free local models), college (paid API, capped per day), "
      "graduation, how many skills are mastered, what is left, and how far along the 26-area master blueprint is. Read-only.",
      {})
def pc_school_status() -> str:
    text = school().status()
    try:
        from core import gateway
        misses = gateway.recent_misses()
    except Exception:  # noqa: BLE001
        misses = []
    if misses:
        seen = list(dict.fromkeys(m.get("said", "") for m in misses if m.get("said")))[-8:]
        text += ("\nFrom real use in the last week, requests I could not fully do (what to study next): "
                 + "; ".join(f"'{s}'" for s in seen) + ".")
    try:
        from core import misslessons
        studied = misslessons.summary()
        if studied:
            text += "\n" + studied
    except Exception:  # noqa: BLE001
        pass
    return text


@tool("Run a short study session now: JARVIS teaches himself the next few PC skills on throw-away practice folders, proves "
      "each on fresh variants, and keeps only what is proven. Uses the free local models first; the paid API only for what "
      "they could not learn, under a daily cap. Unattended: nothing that needs approval ever runs, and it only touches its "
      "own practice folders.", {"max_subjects": {"type": "integer", "description": "how many subjects to work on (default 3)"}},
      required=[], slow=True)
def pc_school_study(max_subjects: int = 3) -> str:
    if os.environ.get("JARVIS_SCHOOL", "on").lower() == "off":
        return "The school is switched off (JARVIS_SCHOOL=off), so no study session was started."
    n = max(1, min(int(max_subjects or 3), 10))
    rep = school().study(max_subjects=n, seconds=600)
    return rep.render()
