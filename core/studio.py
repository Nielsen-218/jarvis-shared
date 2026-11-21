"""The studio manager for Living Shadow World (owner 2026-11-16): his spoken note about the game becomes a precise WORK
ORDER, he confirms it on his phone, a coding worker (Claude Code / Codex / Claude on the expiring API credit) builds it on
its own branch, and JARVIS runs the non-thinking work itself (the Unreal build, the automation suite, the lane's headless
scenarios, the log digests) and reports back.

Honest framing (agreed with the owner): JARVIS's local model cannot write the game's C++. JARVIS manages: it writes the
order, picks the lane and the worker, runs the builds and tests, and reports. Workers code.

Rules taken from the game's own charter (Documentation/TEAM.md on `integration`):
  * every order gets its own branch `studio/wo-<n>-<slug>` and worktree under C:\\Users\\you\\LSW_wt\\, cut from the latest
    GREEN integration tip; nothing is ever committed to the game's `main` or `integration` (the owner or the LSW
    coordinator merges);
  * Unreal builds use -WaitMutex (one global UBT mutex) and go through the game's own runner `Scripts/lsw.ps1`, which
    keeps every real log under Documentation/BuildEvidence/;
  * nothing heavy starts while he plays (core.acquire.running_game) - the job waits;
  * the owner's own checkout (C:\\Users\\you\\LivingShadowWorld) is only READ (git show / git grep / worktree add).

Flow: tools/studio.studio_note queues a mission "Studio note: <his words>" (core/missions: the queue on the laptop, the
phone sheet, approvals bound to the exact step). The PC's mission worker (core/missionworker) drafts the order HERE on the
PC (the repo is here) and parks it for his yes with the whole order as the preview; his yes starts the job
(scripts/studio_job.py, a detached process that survives a JARVIS restart); the mission waits until the job ends and
finishes with the report, which reaches his phone like every mission.

Money: worker "credit" runs Claude Code with the ANTHROPIC_API_KEY (the promotional credit the owner declared in
JARVIS_TEACHER_CREDIT_*; he approved spending it on game work on 2026-11-16), capped per order (--max-budget-usd) and in
total (JARVIS_STUDIO_CREDIT_CAP_USD, default $80 of the declared credit). Every dollar is written to the usage ledger
(component "studio") so the teacher's credit arithmetic stays true. "claude" runs use his Claude Code login, "codex" his
Codex login - no API money.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

PREFIX = "Studio note:"
STATES = ("draft", "approved", "preparing", "working", "building", "testing", "green", "red", "failed", "cancelled")
FINAL = ("green", "red", "failed", "cancelled")
NO_WINDOW = 0x08000000


# ------------------------------------------------------------------------------------------------ places
def repo() -> Path:
    return Path(os.getenv("JARVIS_LSW_REPO", r"C:\Users\you\LivingShadowWorld"))


def worktree_root() -> Path:
    return Path(os.getenv("JARVIS_LSW_WORKTREES", r"C:\Users\you\LSW_wt"))


def base_ref() -> str:
    """The latest GREEN integration (integ#10 650cfee on 2026-11-05). `main` is 192 commits behind it."""
    return os.getenv("JARVIS_STUDIO_BASE", "integration")


def home() -> Path:
    if os.getenv("JARVIS_STUDIO_DIR"):
        return Path(os.environ["JARVIS_STUDIO_DIR"])
    from core import storage
    return storage.DATA / "studio"


def _order_path(oid: str) -> Path:
    return home() / "orders" / f"{oid}.json"


# ------------------------------------------------------------------------------------------------ the team's lanes
# key, role, words that point at the lane, scenarios (name, map, extra switch) - the scenario maps are the ones the
# coordinator's integration runs used (LSW_Integration/Documentation/BuildEvidence, integ#2-#10).
LANES: Dict[str, Dict] = {
    "boss": {"role": "Boss & Enemy-AI Expert (lsw-boss)", "dirs": ["Source/LivingShadowWorld/Boss/", "Source/LivingShadowWorld/EnemyAI/"],
             "words": r"boss|warden|ashbound|phase|arena|enemy ai|enemies|enemy|tactic|ember|breaker|ashfall|desperation|lunge|poise",
             "scenarios": [("BossFight", "/Game/LSW/Boss/BossArena", ""), ("BossRetreat", "/Game/LSW/Boss/BossArena", ""),
                           ("BossSoulHandoff", "/Game/LSW/Boss/BossArena", ""),
                           ("EnemyTactics", "/Game/LSW/Tests/AnatomyCombatArena", "")]},
    "soul": {"role": "Soul & Shadow Systems Expert (lsw-soul)", "dirs": ["Source/LivingShadowWorld/Shadows/", "Source/LivingShadowWorld/SoulUI/"],
             "words": r"soul|shadow|extract|extraction|ally|allies|summon|recruit|guard me|attack my target",
             "scenarios": [("SoulExtraction", "/Game/Variant_Combat/Lvl_Combat", "")]},
    "physanim": {"role": "Physics & Animation Expert (lsw-physanim)", "dirs": ["Source/LivingShadowWorld/PhysAnim/"],
                 "words": r"ragdoll|knockdown|knocked down|get up|gets up|limp|hop|crawl|physics|animation|animate|hit react|stagger|fall",
                 "scenarios": [("PhysAnim", "/Game/Variant_Combat/Lvl_Combat", "")]},
    "persist": {"role": "Persistence & World-State Expert (lsw-persist)", "dirs": ["Source/LivingShadowWorld/World/", "Source/LivingShadowWorld/Save/"],
                "words": r"save|saving|load|loading|continue|checkpoint|journal|reload|persist",
                "scenarios": [("SaveReload", "/Game/LSW/Tests/AnatomyCombatArena", "")]},
    "ux": {"role": "Presentation & UX Expert (lsw-ux)", "dirs": ["Source/LivingShadowWorld/UI/", "Source/LivingShadowWorld/Playtest/"],
           "words": r"menu|title|opening|subtitle|hud|screen|button|pause|ui|font|controls screen|overlay|text|focus",
           "scenarios": [("UXTitleMenu", "/Game/LSW/UI/Maps/LSW_Title", "-RenderOffscreen"),
                         ("UXOpening", "/Game/LSW/UI/Maps/LSW_Opening", "-RenderOffscreen"),
                         ("UXInjuryHud", "/Game/LSW/Tests/AnatomyCombatArena", "-RenderOffscreen")]},
    "core": {"role": "Core Combat Engineer (lsw-builder)",
             "dirs": ["Source/LivingShadowWorld/Variant_Combat/", "Source/LivingShadowWorld/Anatomy/", "Source/LivingShadowWorld/Combat/",
                      "Source/LivingShadowWorld/Gameplay/"],
             "words": r"combat|attack|swing|sword|weapon|hit|dodge|block|parry|jump|player|camera|controller|anatomy|limb|arm|leg|injur|damage|health|combo",
             "scenarios": [("AnatomyCombat", "/Game/LSW/Tests/AnatomyCombatArena", ""), ("Dummy", "/Game/LSW/Tests/AnatomyArena", ""),
                           ("CombatSpawn", "/Game/Variant_Combat/Lvl_Combat", "")]},
}
ORDER = ("boss", "soul", "physanim", "persist", "ux", "core")       # the more specific lane wins a tie


def lane_scores(note: str) -> Dict[str, int]:
    text = " " + note.casefold() + " "
    return {key: len(re.findall(r"\b(?:" + LANES[key]["words"] + r")", text)) for key in ORDER}


def pick_lane(note: str) -> str:
    """The lane whose words the note uses most; the core combat lane when nothing points elsewhere."""
    best, score = "core", 0
    for key, n in lane_scores(note).items():
        if n > score:
            best, score = key, n
    return best


# ------------------------------------------------------------------------------------------------ reading the game (read-only)
def git(*args, cwd: Optional[Path] = None, timeout: float = 120) -> str:
    out = subprocess.run(["git", "-C", str(cwd or repo()), *args], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=timeout, creationflags=NO_WINDOW if os.name == "nt" else 0)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {out.stderr.strip()[:300]}")
    return out.stdout


_STOP = set("the a an and or but to of in on at for with is are was were be been it its this that these those too very "
            "really feel feels felt like just kind sort bit more less much way when then than there their they them he she "
            "his her my me i you your we our can could should would make makes made get gets got want needs need please "
            "game jarvis work order note studio thing things some any".split())
# A few spoken words name code words the game uses (for the file search only, never for the order's wording).
_SYNONYMS = {"slow": ["Speed", "Cooldown", "Telegraph", "Recovery", "Rate"], "fast": ["Speed", "Cooldown", "Telegraph", "Rate"],
             "slower": ["Speed", "Cooldown"], "faster": ["Speed", "Cooldown"], "hard": ["Damage", "Health", "Poise"],
             "easy": ["Damage", "Health", "Poise"], "strong": ["Damage", "Force"], "weak": ["Damage", "Force"],
             "phase": ["Phase"], "jump": ["Jump"], "camera": ["Camera"], "dies": ["Death", "Defeat"], "die": ["Death", "Defeat"]}


def keywords(note: str) -> List[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z']+", note) if w.casefold() not in _STOP and len(w) > 2]
    out: List[str] = []
    for w in words:
        for k in [w.strip("'")] + _SYNONYMS.get(w.casefold(), []):
            if k.casefold() not in [o.casefold() for o in out]:
                out.append(k)
    return out[:12]


def find_files(note: str, lane: str, ref: Optional[str] = None, limit: int = 6) -> List[Dict]:
    """The lane's source files that mention the note's words most, at the base commit (git grep on a ref: no checkout)."""
    ref = ref or base_ref()
    words = keywords(note)
    if not words:
        return []
    hits: Dict[str, int] = {}
    for w in words:
        try:
            out = git("grep", "-i", "-c", "-F", w, ref, "--", *LANES[lane]["dirs"])
        except RuntimeError:
            continue                                                    # git grep exits 1 when nothing matches
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[-1].isdigit():
                path = ":".join(parts[1:-1])
                if "/Tests/" in path or path.endswith("Tests.cpp"):
                    continue
                hits[path] = hits.get(path, 0) + 1 + min(int(parts[-1]), 20) / 20
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])[:limit]
    return [{"path": p, "score": round(s, 2)} for p, s in ranked]


_SCEN_ROW = re.compile(r'@\("(\w+)",\s*"([^"]*)",\s*"([nr])"\)')


def integration_scenarios(ref: Optional[str] = None) -> List[Dict]:
    """Every scenario the coordinator's integration gate runs, read from its own script on the base
    (Scripts/integration_verify.ps1: name, map or '' for the default map, n = -nullrhi / r = -RenderOffscreen)."""
    try:
        text = git("show", f"{ref or base_ref()}:Scripts/integration_verify.ps1")
    except RuntimeError:
        return []
    return [{"name": n, "map": m, "render": r == "r"} for n, m, r in _SCEN_ROW.findall(text)]


def test_plan(lane: str, ref: Optional[str] = None) -> List[Dict]:
    """The lane's scenarios first, then every other integration scenario: an order is green only when nothing the
    coordinator checks went red. Falls back to the lane's own list when the base has no integration script."""
    every = integration_scenarios(ref)
    if not every:
        return [{"name": s["name"], "map": s["map"], "render": "-RenderOffscreen" in s.get("extra", ""), "lane": True}
                for s in scenarios_on(lane, ref)]
    mine = {n for n, _m, _e in LANES[lane]["scenarios"]}
    first = [dict(s, lane=True) for s in every if s["name"] in mine]
    return first + [dict(s, lane=False) for s in every if s["name"] not in mine]


def scenarios_on(lane: str, ref: Optional[str] = None) -> List[Dict]:
    """Only the lane scenarios that really exist at the base (a scenario named but not registered would fail as 'not run')."""
    ref = ref or base_ref()
    found = []
    for name, map_, extra in LANES[lane]["scenarios"]:
        try:
            git("grep", "-q", "-F", f'"{name}"', ref, "--", "Source/LivingShadowWorld/")
            found.append({"name": name, "map": map_, "extra": extra})
        except RuntimeError:
            continue
    return found


# ------------------------------------------------------------------------------------------------ choosing the worker
_TUNING = re.compile(r"\b(too (slow|fast|quick|long|short|hard|easy|strong|weak|much|little|loud|quiet|big|small|often|rare)|"
                     r"slower|faster|quicker|longer|shorter|tweak|tune|adjust|nudge|bump|typo|rename|wording|colou?r)\b", re.I)


def difficulty(note: str) -> str:
    return "medium" if _TUNING.search(note) else "hard"


def credit_state() -> Dict:
    """What the studio may still spend of the declared promotional credit: the credit (core.teacher.credit) minus a floor
    so the studio stops after JARVIS_STUDIO_CREDIT_CAP_USD of game work."""
    from core import teacher, usage
    c = teacher.credit()
    cap = float(os.getenv("JARVIS_STUDIO_CREDIT_CAP_USD", "80"))
    spent = studio_spent()
    left = round(min(cap - spent, float(c.get("remaining") or 0.0)), 4)
    ok = bool(c.get("declared") and c.get("expires")) and date.today().isoformat() <= c.get("expires", "") and left > 1.0
    why = "" if ok else (c.get("why") or f"the studio cap is reached (${spent:.2f} of ${cap:.2f} spent)")
    if ok and not os.getenv("ANTHROPIC_API_KEY"):
        ok, why = False, "no ANTHROPIC_API_KEY"
    if ok and not api_key_ok():
        ok, why = False, "Anthropic rejects the API key in .env (401): the owner must put a valid key there"
    del usage
    return {"ok": ok, "why": why, "left": max(left, 0.0), "spent": spent, "cap": cap, "expires": c.get("expires", "")}


_KEY_CHECK: Dict[str, tuple] = {}


def api_key_ok() -> bool:
    """A free token count proves the key works (2026-11-16: the .env key was revoked and the first credit worker died
    with 'Invalid API key'). Cached per key for an hour; a network problem is not a rejection."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    stamp = hashlib.sha256(key.encode()).hexdigest()[:12]
    hit = _KEY_CHECK.get(stamp)
    if hit and time.time() - hit[1] < 3600:
        return hit[0]
    try:
        import anthropic
        anthropic.Anthropic(max_retries=0, timeout=20).messages.count_tokens(
            model="claude-haiku-5-5", messages=[{"role": "user", "content": "ok"}])
        good = True
    except Exception as e:  # noqa: BLE001
        good = type(e).__name__ not in ("AuthenticationError", "PermissionDeniedError")
    _KEY_CHECK[stamp] = (good, time.time())
    return good


def studio_spent() -> float:
    """The studio's own append-only spend files (data/studio/spend/*.jsonl), written by the job process. The usage ledger
    gets the same dollars, but it is one JSON file rewritten by live JARVIS too, so a concurrent write could drop a row;
    the cap is never computed from it (review 2026-11-16)."""
    total = 0.0
    for p in (home() / "spend").glob("*.jsonl"):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:                                 # one torn line never hides the rest of the file
            try:
                total += float(json.loads(line).get("usd") or 0.0)
            except (ValueError, TypeError, AttributeError):
                continue
    return round(total, 4)


_ASK_CODEX = re.compile(r"\b(use|with|give it to|send it to|by) codex\b", re.I)
_ASK_CREDIT = re.compile(r"\b(use|with|on|from) the (api |teacher )?credit\b|\bapi credit\b", re.I)
_ASK_CLAUDE = re.compile(r"\b(use|with|give it to|send it to|by) claude( code)?\b", re.I)


def pick_worker(note: str) -> Dict:
    """His words win ('use codex', 'give it to claude code', 'use the credit'). Otherwise: medium work -> Codex, hard
    work -> Claude on the expiring credit this week (it is lost on its expiry date), else Claude Code on his login.
    Only an explicit ask switches worker: 'the credits roll is too fast' is a note, not a request for the paid worker."""
    if _ASK_CODEX.search(note):
        return {"worker": "codex", "why": "you asked for Codex"}
    if _ASK_CREDIT.search(note):
        return {"worker": "credit", "why": "you asked for the API credit"}
    if _ASK_CLAUDE.search(note):
        return {"worker": "claude", "why": "you asked for Claude Code"}
    if difficulty(note) == "medium":
        return {"worker": "codex", "why": "a tuning change (medium work goes to Codex)"}
    c = credit_state()
    if c["ok"]:
        return {"worker": "credit", "why": f"hard work; the expiring API credit pays (${c['left']:.0f} left until {c['expires']})"}
    return {"worker": "claude", "why": f"hard work (the credit can't pay: {c['why']})"}


def order_budget(worker: str) -> float:
    return float(os.getenv("JARVIS_STUDIO_ORDER_USD", "15")) if worker == "credit" else 0.0


# ------------------------------------------------------------------------------------------------ the order
def _slug(text: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.casefold()) if w not in _STOP][:5]
    return "-".join(words)[:40] or "note"


def _next_id() -> str:
    folder = home() / "orders"
    folder.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in folder.glob("WO-*.json") if (m := re.match(r"WO-(\d+)", p.stem))]
    return f"WO-{(max(nums) + 1) if nums else 1}"


def note_of(goal: str) -> str:
    return goal[len(PREFIX):].strip() if is_note(goal) else goal.strip()


def is_note(goal: str) -> bool:
    return str(goal or "").strip().casefold().startswith(PREFIX.casefold())


def draft(note: str, mission: str = "", ref: Optional[str] = None) -> Dict:
    """A precise work order from his words: lane, base commit, files, acceptance criteria, tests, worker. Saved as a draft."""
    ref = ref or base_ref()
    note = note.strip()
    lane = pick_lane(note)
    base_sha = git("rev-parse", "--short", ref).strip()
    files = find_files(note, lane, ref)
    past: List[Dict] = []
    try:            # learning is context: any problem here leaves the order exactly as the keywords made it
        from core import studiolearn
        studiolearn.backfill()                              # a green order whose lesson a crash left unwritten
        studiolearn.build_map(ref)                          # cached per base commit
        lane, files, past = studiolearn.apply_lessons(note, lane, files, ref, lane_scores(note))
    except Exception as e:  # noqa: BLE001
        print(f"[studio] lessons: {type(e).__name__}: {e}", flush=True)
    scen = test_plan(lane, ref)
    w = pick_worker(note)
    oid = _next_id()
    branch = f"studio/{oid.lower()}-{_slug(note)}"
    systems = sorted({Path(f["path"]).stem.replace("LSW", "") for f in files[:4]})
    order = {
        "id": oid, "mission": mission, "note": note, "created": time.time(), "state": "draft",
        "lane": lane, "role": LANES[lane]["role"], "system": ", ".join(systems) or lane,
        "base": ref, "base_sha": base_sha, "branch": branch, "worktree": str(worktree_root() / f"studio-{oid.lower()}"),
        "files": files, "lessons_used": [ls["id"] for ls in past], "difficulty": difficulty(note),
        "lessons_scores": {ls["id"]: ls["score"] for ls in past},
        "worker": w["worker"], "worker_why": w["why"],
        "budget_usd": order_budget(w["worker"]),
        "acceptance": [
            f"The change answers the director's note: \"{note}\". The worker states the measurable before/after it changed "
            "(the exact values or behaviour) in its report.",
            "Tunables stay data-driven (UPROPERTY / DataAsset), new demo values are marked 'demo default, not canon'.",
            "A new or updated automation test (or scenario check) proves the changed behaviour.",
            "Locked canon untouched: exactly three extraction attempts, the opening line verbatim, controller-first, injury "
            "changes real capability.",
            "The editor and Game targets build, the full LivingShadowWorld automation suite passes, and every integration "
            f"scenario passes ({len(scen)}; the lane's own: {', '.join(s['name'] for s in scen if s.get('lane')) or 'none'}).",
            f"Lands only on branch {branch}; nothing is merged into main or integration.",
        ],
        "tests": {"build": ["LivingShadowWorldEditor", "LivingShadowWorld"], "automation": "LivingShadowWorld",
                  "scenarios": scen},
        "history": [{"t": time.time(), "state": "draft"}],
    }
    order["key"] = key_of(order)
    save(order)
    return order


def key_of(order: Dict) -> str:
    """What his yes binds to: the parts of the order he reads on the phone."""
    fields = {k: order.get(k) for k in ("id", "note", "lane", "base_sha", "branch", "worker", "budget_usd", "acceptance")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def save(order: Dict) -> None:
    path = _order_path(order["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(order, indent=1), encoding="utf-8")
    tmp.replace(path)


def load(oid: str) -> Optional[Dict]:
    try:
        return json.loads(_order_path(oid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def orders() -> List[Dict]:
    out = []
    for p in (home() / "orders").glob("WO-*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda o: o.get("created", 0), reverse=True)


def for_mission(mid: str) -> Optional[Dict]:
    return next((o for o in orders() if mid and o.get("mission") == mid), None)


def set_state(order: Dict, state: str, **extra) -> Dict:
    order.update(extra, state=state)
    order.setdefault("history", []).append({"t": time.time(), "state": state, **{k: v for k, v in extra.items() if k in ("why",)}})
    save(order)
    return order


_WORKER_NAMES = {"credit": "Claude on the API credit", "claude": "Claude Code", "codex": "Codex"}


def preview(order: Dict) -> str:
    """The whole order as he reads it on the phone (the mission sheet shows up to 1000 characters)."""
    files = ", ".join(Path(f["path"]).name for f in order["files"][:4]) or "the worker finds them"
    sc = order["tests"]["scenarios"]
    lane_sc = [s["name"] for s in sc if s.get("lane")]
    scen = (f"all {len(sc)} integration scenarios" + (f" (the lane's: {', '.join(lane_sc)})" if lane_sc else "")) if sc else "no scenarios"
    money = f", at most ${order['budget_usd']:.0f}" if order.get("budget_usd") else ""
    # criterion 1 repeats his note (already at the top): the sheet shows its second sentence instead
    first = order["acceptance"][0].split(". ", 1)[-1]
    crit = " ".join(f"({i + 1}) {c}" for i, c in enumerate([first] + order["acceptance"][1:3]))
    note = order["note"] if len(order["note"]) <= 220 else order["note"][:217] + "..."
    head = (f"Work order {order['id']}: \"{note}\". Lane: {order['role']}. Files: {files}. "
            f"Worker: {_WORKER_NAMES.get(order['worker'], order['worker'])}{money} ({order['worker_why']}). "
            f"Branch {order['branch']} from {order['base']} {order['base_sha']}. Done when: ")
    tail = f" JARVIS then builds the editor and Game targets and runs the full test suite and {scen}. Approve to start."
    room = 1000 - len(head) - len(tail)
    if len(crit) > room:                                          # the ending (what runs, 'Approve') always fits
        crit = crit[:max(room - 3, 0)].rstrip() + "..."
    return (head + crit + tail)[:1000]


# ------------------------------------------------------------------------------------------------ starting / watching the job
def job_log(order: Dict) -> Path:
    return home() / "jobs" / f"{order['id']}.log"


def start(order: Dict, key: str) -> str:
    """His yes, bound to the exact order he saw: start the detached job. -> a sentence for the mission's evidence."""
    current = load(order["id"]) or order
    if key != current.get("key") or key_of(current) != key:
        raise ValueError("the work order changed after you looked at it")
    if current["state"] != "draft":
        return f"{current['id']} was already started ({current['state']})"
    set_state(current, "approved")
    spawn(current)
    return f"{current['id']} approved and started with {_WORKER_NAMES.get(current['worker'], current['worker'])}"


def spawn(order: Dict) -> int:
    """Start scripts/studio_job detached (it outlives a JARVIS restart). Its pid and start time go to their own file
    (jobs/<id>.pid.json), never into the order JSON the job itself keeps writing (no lost update)."""
    from core.config import ROOT
    import sys
    log = job_log(order)
    log.parent.mkdir(parents=True, exist_ok=True)
    base = 0x00000008 | 0x00000200 | NO_WINDOW if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP | no window
    p = None
    with open(log, "ab") as fh:
        for flags in ((base | 0x01000000, base) if os.name == "nt" else (0,)):   # + BREAKAWAY_FROM_JOB when allowed
            try:
                p = subprocess.Popen([sys.executable, "-m", "scripts.studio_job", order["id"]], cwd=str(ROOT), stdout=fh,
                                     stderr=fh, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
                break
            except OSError:
                continue
    if p is None:
        raise OSError("the studio job could not be started")
    note_pid(order["id"], p.pid)
    return p.pid


def _pid_file(oid: str) -> Path:
    return home() / "jobs" / f"{oid}.pid.json"


def note_pid(oid: str, pid: int) -> None:
    created = 0.0
    try:
        import psutil
        created = psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001
        pass
    path = _pid_file(oid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"pid": pid, "created": created}), encoding="utf-8")
    tmp.replace(path)                                      # atomic: a reader never sees half a pid file


def alive(pid: Optional[int], created: float = 0.0) -> bool:
    """Is that exact process still running? A reused pid (another process, other start time) is not; a process we may not
    inspect (elevated) counts as running, so a live job is never started twice."""
    if not pid:
        return False
    try:
        import psutil
    except ImportError:
        return True
    try:
        proc = psutil.Process(int(pid))
        if created and abs(proc.create_time() - created) > 2:
            return False
        return proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
    except Exception:  # noqa: BLE001
        return True


def job_alive(oid: str) -> bool:
    try:
        info = json.loads(_pid_file(oid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return alive(info.get("pid"), float(info.get("created") or 0.0))


# ------------------------------------------------------------------------------------------------ what he hears
def _ago(t: float) -> str:
    m = int((time.time() - t) / 60)
    from core.words import plural
    return "just now" if m < 2 else (plural(m, "minute") + " ago" if m < 120 else plural(m // 60, "hour") + " ago")


_SAY = {"draft": "is waiting for your OK on the phone", "approved": "is approved and starting", "preparing": "is setting up its branch",
        "working": "is with the worker", "building": "is being built", "testing": "is being tested", "green": "is done and green",
        "red": "finished but is NOT green", "failed": "stopped with a problem", "cancelled": "was cancelled"}


def spoken(order: Dict, full: bool = False) -> str:
    s = f"Work order {order['id'].split('-')[1]}, \"{order['note'][:90]}\", {_SAY.get(order['state'], order['state'])}"
    if order["state"] == "working":
        s += f" ({_WORKER_NAMES.get(order['worker'], order['worker'])}, since {_ago(order['history'][-1]['t'])})"
    if full and order.get("report"):
        s += ". " + order["report"]
    return s


def status(query: str = "") -> str:
    items = orders()
    if not items:
        return "There are no studio work orders yet. Tell me a note about the game and I'll write one."
    q = [w for w in re.findall(r"[a-z0-9]+", query.casefold()) if w not in _STOP and len(w) > 2 and w not in ("status", "work")]
    if q:
        num = next((w for w in q if w.isdigit()), None)
        hits = [o for o in items if (num and o["id"] == f"WO-{num}")
                or any(w in (o["note"] + " " + o["lane"] + " " + o.get("system", "")).casefold() for w in q if not w.isdigit())]
        if not hits:
            return f"I have no work order about {query.strip()}. " + spoken(items[0]) + "."
        return spoken(hits[0], full=True) + ("" if hits[0].get("report") else ".")
    active = [o for o in items if o["state"] not in FINAL]
    shown = active or items[:2]
    return " ".join(spoken(o, full=len(shown) == 1) + ("" if (len(shown) == 1 and o.get("report")) else ".") for o in shown[:4])


def record_cost(usd: float, model: str = "claude-opus-5-5", oid: str = "") -> None:
    """A credit run's cost: first the studio's own append-only file (what the $80 cap reads), then JARVIS's usage ledger
    (component 'studio', so the teacher's credit arithmetic sees it too)."""
    if usd <= 0:
        return
    path = home() / "spend" / f"{oid or 'unassigned'}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "usd": round(usd, 6), "model": model}) + chr(10))
    from core import usage

    class _U:                                     # usage.record prices tokens; this passes the measured dollars through
        input_tokens = int(round(usd * 1e6))
        output_tokens = cache_read_input_tokens = cache_creation_input_tokens = 0
    try:
        usage.record(model, _U(), prices=(1.0, 0.0, 0.0, 0.0), component="studio")
    except Exception:  # noqa: BLE001 - the studio's own file already holds it
        pass


# ------------------------------------------------------------------------------------------------ his exact words (core/quick)
_NOTE = re.compile(r"^(?:(?:take|add|make|log|new|here'?s|here is) (?:a |another )?)?(?:studio |game )?note"
                   r"(?: for| about| on| to)? (?:the game|my game|shadow world|living shadow world|the studio)"
                   r"\s*[:,.\-]?\s*(?P<note>.{8,})$"
                   r"|^(?:tell the studio|studio note|work order|make a work order|new work order)\s*(?:that\s+)?[:,.\-]?\s*(?P<note2>.{8,})$",
                   re.I)
_STATUS = re.compile(r"^(?:what'?s|what is|how'?s|how is|give me|tell me)\s+(?:the\s+)?(?:status|progress)\s+(?:of|on)\s+"
                     r"(?:the\s+|my\s+)?(?P<what>.{2,60}?)\s+(?:work|fix|change|work order|order)\s*\??$"
                     r"|^(?P<learn>(?:how(?:'?s| is) the (?:game )?studio (?:learning|improving|getting better)|what has the "
                     r"studio learned|is the studio (?:learning|getting better)))\s*\??$"
                     r"|^(?:studio status|how(?:'?s| is) the (?:game )?studio(?: doing)?|what(?:'?s| is| are) the "
                     r"(?:studio|coders) (?:doing|working on)|any studio news)\s*\??$", re.I)


def handle(raw: str) -> Optional[str]:
    """'note for the game: the boss feels too slow in phase 2' -> a work order note; 'what's the status of the boss work?'
    -> the real orders. Owner only; anything else returns None (the model answers)."""
    text = (raw or "").strip()
    if not text or len(text) > 400:
        return None
    m = _NOTE.match(text)
    s = None if m else _STATUS.match(text)
    if not (m or s):
        return None
    from core import authz
    if not authz.is_owner():
        return None
    from tools import gamestudio
    if m:
        return gamestudio.studio_note((m.group("note") or m.group("note2") or "").strip())
    if s.group("learn"):
        return gamestudio.studio_status("learning")
    return gamestudio.studio_status((s.group("what") or "").strip())
