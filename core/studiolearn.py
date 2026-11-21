"""What the studio keeps from every task (owner 2026-11-16: "durable knowledge, not one-and-done").

  * LESSONS (data/studio/lessons/<WO>.json): only for an order that went GREEN (build + full suite + every scenario) and
    changed real (non-test) source: his note, the work order, the lane and files, the final diff, why it worked (the end
    of the worker's report), and what proved it. A red/failed order is kept as a FAILURE (data/studio/failures/), never
    retrieved. A green order whose diff could not be read is PENDING (retried by `backfill`), never misfiled as a failure.
  * RETRIEVAL: `apply_lessons` (core/studio.draft) picks at most 2 close lessons once, stores them on the order, and the
    worker's prompt shows exactly those (no second lookup that could pick others).
  * CODEBASE MAP (data/studio/codebase-map.json): each source folder's owning lane, classes, test areas and scenarios at the
    base commit; every green order appends the files it touched. Atomic writes.
  * DATASET (data/studio/dataset/tasks.jsonl): one row per verified lesson whose diff is complete, with the EXACT prompt the
    worker received, de-duplicated by order id, secrets and his user path redacted. Logged only, never trained on: see
    docs/STUDIO-MANAGER.md "Dataset and terms".
  * METRICS: his rejections (recorded by the laptop the moment he says no: core/missions -> note_decision), notes he said
    again after a rejection (heuristic: within 48 h, most words shared), worker second passes, early vs late rates, and
    with-lesson vs without-lesson second-pass rates. Below 10 orders per half it says it is too few to tell; it never
    calls a difference caused by the lessons.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from core import studio

MAX_DIFF = 60_000
LESSON_MIN_SCORE = 0.3
MAX_LESSON_FILES = 3
PROMPT_BUDGET = 1800
HALF_ENOUGH = 10


def _dir(name: str) -> Path:
    p = studio.home() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _words(text: str) -> set:
    return {w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z']+", str(text or "")) if len(w) > 2
            and w.casefold() not in studio._STOP}


# ------------------------------------------------------------------------------------------------ redaction
_SECRETS = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_\-]{12,}", r"sk-ant-[A-Za-z0-9_\-]{12,}", r"gh[pousr]_[A-Za-z0-9]{20,}", r"AIza[0-9A-Za-z_\-]{20,}",
    r"(?im)^\s*[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASS(?:WORD)?|PWD)\s*=\s*\S+",
    r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}")]


def redact(text) -> str:
    """Keys/tokens and KEY=value lines out, his user folder replaced by ~ (lessons and dataset rows sync to the laptop)."""
    s = str(text or "")
    for pat in _SECRETS:
        s = pat.sub("[redacted]", s)
    home = os.path.expanduser("~")
    for form in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        if len(form) > 3:
            s = re.sub(re.escape(form), "~", s, flags=re.I)
    return s


def _red(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [_red(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _red(v) for k, v in obj.items()}
    return obj


# ------------------------------------------------------------------------------------------------ recording
def _diff(order: Dict) -> tuple:
    """-> (files, diff, error). Paths are read NUL-separated (spaces and non-ASCII names stay whole)."""
    wt = Path(order["worktree"])
    head = order.get("commit") or "HEAD"
    try:
        raw = studio.git("-c", "core.quotepath=off", "diff", "-z", "--name-only", f"{order['base_sha']}..{head}", cwd=wt)
        files = [f for f in raw.split("\0") if f]
        diff = studio.git("diff", f"{order['base_sha']}..{head}", "--", "Source", "Config", "*.uproject", cwd=wt)
        return files, diff, ""
    except Exception as e:  # noqa: BLE001
        return [], "", f"{type(e).__name__}: {e}"


def _source_files(files: List[str]) -> List[str]:
    return [f for f in files if f.startswith("Source/") and "/Tests/" not in f and not f.endswith("Tests.cpp")]


def _last_paragraph(text: str, limit: int = 1200) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    return (paras[-1] if paras else "")[-limit:]


def record(order: Dict) -> Optional[Path]:
    """Once per order, when it ends. GREEN with real source changes -> lesson (+ dataset row, map). Red/failed -> failure.
    Green whose diff can't be read -> pending (backfill retries). Idempotent: a recorded order is not recorded again."""
    oid = order["id"]
    if (_dir("lessons") / f"{oid}.json").exists():
        return _dir("lessons") / f"{oid}.json"
    failed_before = _dir("failures") / f"{oid}.json"
    if failed_before.exists() and order.get("state") != "green":
        return failed_before                               # a retried order that turns green is still recorded below
    if failed_before.exists():
        _keep_attempt(oid, failed_before)
    base = {"id": oid, "t": time.time(), "note": order["note"], "lane": order["lane"], "role": order.get("role"),
            "system": order.get("system"), "base": order.get("base"), "base_sha": order.get("base_sha"),
            "branch": order.get("branch"), "commit": order.get("commit"), "worker": order.get("worker"),
            "acceptance": order.get("acceptance"), "fix_rounds": order.get("fix_rounds", 0),
            "cost_usd": order.get("spent_usd", 0.0), "lessons_used": order.get("lessons_used", [])}
    if order.get("state") == "green":
        files, diff, err = _diff(order)
        if err:
            _atomic(_dir("pending") / f"{oid}.json", dict(base, error=err))
            return None
        src = _source_files(files)
        scen = (order.get("results") or {}).get("scenarios")
        proven = bool((order.get("results") or {}).get("automation")) and (scen or not order.get("tests", {}).get("scenarios"))
        if src and diff.strip() and proven:
            truncated = len(diff) > MAX_DIFF
            lesson = _red(dict(base, verified=True, files_touched=files, diff=diff[:MAX_DIFF], diff_truncated=truncated,
                               why=_last_paragraph(order.get("worker_text")), report=(order.get("worker_text") or "")[:4000],
                               proved_by=order.get("results") or {}))
            if not truncated:
                _dataset_row(order, lesson)
            try:
                update_map_with(order, files)
            except Exception:  # noqa: BLE001 - the map is context
                pass
            path = _dir("lessons") / f"{oid}.json"
            _atomic(path, lesson)                           # last: a crash before this leaves the order to backfill
            _clear_pending(oid)
            return path
        why = ("green but no non-test source changed: nothing to learn from" if not src else
               "green but not proven by the suite and scenarios")
    else:
        files, diff, _ = _diff(order)
        why = order.get("why") or order.get("report") or order.get("state")
    failure = _red(dict(base, verified=False, state=order.get("state"), why=str(why)[:2000], files_touched=files,
                        diff=diff[:MAX_DIFF], results=order.get("results") or {},
                        worker_text=(order.get("worker_text") or "")[:4000]))
    path = _dir("failures") / f"{oid}.json"
    _atomic(path, failure)
    _clear_pending(oid)
    return path


def _keep_attempt(oid: str, path: Path) -> None:
    """An earlier failed attempt of an order that is now green: kept as attempts/<WO>-<n>.json (still a failure, never a
    lesson), so the lesson can be written."""
    n = len(list(_dir("attempts").glob(f"{oid}-*.json"))) + 1
    try:
        path.replace(_dir("attempts") / f"{oid}-{n}.json")
    except OSError:
        pass


def _clear_pending(oid: str) -> None:
    try:
        (_dir("pending") / f"{oid}.json").unlink()
    except OSError:
        pass


def backfill() -> int:
    """Finished orders with no lesson/failure yet (a crash between 'green' and recording, a job that died twice, a diff
    that couldn't be read): record them now. -> how many were recorded."""
    n = 0
    for o in studio.orders():
        if o.get("state") not in studio.FINAL or o.get("state") == "cancelled":
            continue
        if any((_dir(k) / f"{o['id']}.json").exists() for k in ("lessons", "failures")):
            continue
        if record(o) is not None:
            n += 1
    return n


def _valid(d) -> bool:
    return (isinstance(d, dict) and d.get("verified") is True and d.get("lane") in studio.LANES
            and isinstance(d.get("files_touched"), list) and isinstance(d.get("note"), str) and isinstance(d.get("id"), str))


def lessons() -> List[Dict]:
    out = []
    for p in sorted(_dir("lessons").glob("WO-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _valid(d):
            out.append(d)
    return out


def failures() -> List[Dict]:
    out = []
    for p in sorted(_dir("failures").glob("WO-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


# ------------------------------------------------------------------------------------------------ retrieval
def similar(note: str, lane: Optional[str] = None, k: int = 2, min_score: float = LESSON_MIN_SCORE,
            exclude: tuple = ()) -> List[Dict]:
    """Past VERIFIED lessons close to the note: at least 2 shared content words AND a Jaccard score >= min_score
    (+0.1 for the same lane). Never a failure, never the order itself."""
    want = _words(note)
    if len(want) < 2:
        return []
    scored = []
    for ls in lessons():
        if ls["id"] in exclude:
            continue
        have = _words(ls["note"] + " " + str(ls.get("system") or ""))
        shared = want & have
        if len(shared) < 2:
            continue
        score = len(shared) / len(want | have) + (0.1 if lane and ls["lane"] == lane else 0.0)
        if score >= min_score:
            scored.append((round(score, 3), ls))
    scored.sort(key=lambda x: -x[0])
    return [dict(ls, score=s) for s, ls in scored[:k]]


def apply_lessons(note: str, lane: str, files: List[Dict], ref: str, lane_scores: Dict[str, int]) -> tuple:
    """-> (lane, files, lessons used). A close lesson picks the lane only when his words name none; its files (that still
    exist at the base, in the lane, not tests; at most 3, best lesson first) go ahead of the keyword hits, which keep the
    rest of the list."""
    past = similar(note, k=2)
    if past and max(lane_scores.values() or [0]) == 0:
        lane = past[0]["lane"]
    past = [ls for ls in past if ls["lane"] == lane] or []
    if not past:
        return lane, files, []
    try:
        existing = set(studio.git("ls-tree", "-r", "--name-only", ref, "--", *studio.LANES[lane]["dirs"]).splitlines())
    except Exception:  # noqa: BLE001
        existing = set()
    picked: List[Dict] = []
    for ls in past:                                         # best lesson first
        for path in ls["files_touched"]:
            if len(picked) >= MAX_LESSON_FILES:
                break
            if (path in existing and path.startswith(tuple(studio.LANES[lane]["dirs"])) and "/Tests/" not in path
                    and path not in {p["path"] for p in picked}):
                picked.append({"path": path, "score": round(10 + ls["score"], 2), "from_lesson": ls["id"]})
    rest = [f for f in files if f["path"] not in {p["path"] for p in picked}]
    used = [ls for ls in past if any(p["from_lesson"] == ls["id"] for p in picked)] or past[:1]
    return lane, (picked + rest)[:8], used


def prompt_section(order: Dict) -> str:
    """Exactly the lessons stored on the order + the lane's map, within a fixed budget."""
    by_id = {ls["id"]: ls for ls in lessons()}
    parts = []
    for lid in order.get("lessons_used") or []:
        ls = by_id.get(lid)
        if not ls or lid == order.get("id"):
            continue
        why = re.sub(r"\s+", " ", ls.get("why", ""))[:500]
        parts.append(f"- {ls['id']} (\"{ls['note'][:120]}\", lane {ls['lane']}): touched {', '.join(ls['files_touched'][:5])}; "
                     f"proved by {_proof_line(ls)}. What worked: {why}")
    out = ""
    if parts:
        out += ("\nPAST VERIFIED LESSONS (similar director notes that went green; reuse what applies, verify it still holds):\n"
                + "\n".join(parts))[:PROMPT_BUDGET] + "\n"
    lane_map = map_for_lane(order["lane"], max_chars=PROMPT_BUDGET)
    if lane_map:
        out += "\nCODEBASE MAP for this lane (from the base commit):\n" + lane_map + "\n"
    return out


def _proof_line(ls: Dict) -> str:
    p = ls.get("proved_by") or {}
    auto = re.search(r"automation: (\d+/\d+)", p.get("automation", "") or "")
    scen = p.get("scenarios") or []
    return ", ".join(x for x in [f"suite {auto.group(1)}" if auto else "",
                                 f"{sum(1 for s in scen if ': PASS' in s)} of {len(scen)} scenarios" if scen else ""] if x) or "build"


# ------------------------------------------------------------------------------------------------ the codebase map
def map_path() -> Path:
    studio.home().mkdir(parents=True, exist_ok=True)
    return studio.home() / "codebase-map.json"


def load_map() -> Dict:
    try:
        d = json.loads(map_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _lane_of(folder: str) -> str:
    for key, lane in studio.LANES.items():
        if any(folder == d or folder.startswith(d) for d in lane["dirs"]):
            return key
    return "core" if folder.rstrip("/").endswith("/Tests") else "shared"


def build_map(ref: Optional[str] = None) -> Dict:
    """Read the game's source tree at the base commit (git only, no checkout). Rebuilt when the base commit changes; a
    failed rebuild is not retried for an hour; the 'touched' history survives every rebuild."""
    ref = ref or studio.base_ref()
    sha = studio.git("rev-parse", "--short", ref).strip()
    old = load_map()
    if old.get("base_sha") == sha:
        return old
    if old.get("failed_sha") == sha and time.time() - float(old.get("failed_at") or 0) < 3600:
        return old
    try:
        files = [f for f in studio.git("ls-tree", "-r", "--name-only", ref, "--", "Source/LivingShadowWorld/").splitlines() if f]
    except Exception:  # noqa: BLE001 - a slow or broken repo must not stall every draft
        _atomic(map_path(), dict(old, failed_sha=sha, failed_at=time.time()))
        return old
    systems: Dict[str, Dict] = {}
    for f in files:
        parts = f.split("/")
        if len(parts) < 4 or not f.endswith((".h", ".cpp")):
            continue
        folder = "/".join(parts[:3]) + "/"
        s = systems.setdefault(folder, {"lane": _lane_of(folder), "files": 0, "classes": [], "tests": [], "scenarios": []})
        s["files"] += 1
    # (git grep's POSIX pattern, the Python pattern that reads the name out of the match)
    grep = {"classes": (r"(class|struct)[[:space:]]+LIVINGSHADOWWORLD_API[[:space:]]+[AUFE]LSW[A-Za-z0-9_]+",
                        r"LIVINGSHADOWWORLD_API\s+([AUFE]LSW\w+)"),
            "tests": (r'"LivingShadowWorld\.[A-Za-z0-9_]+\.', r'"LivingShadowWorld\.(\w+)\.'),
            "scenarios": (r'RegisterScenario\([[:space:]]*(TEXT\()?"[A-Za-z0-9_]+"', r'RegisterScenario\(\s*(?:TEXT\()?"(\w+)"')}
    for key, (git_pattern, pattern) in grep.items():
        try:
            listing = studio.git("grep", "-E", "-o", git_pattern, ref, "--", "Source/LivingShadowWorld/")
        except Exception:  # noqa: BLE001 - no match (exit 1) or a timeout: that part of the map stays empty
            continue
        for line in listing.splitlines():
            m = re.match(r"[^:]+:(Source/LivingShadowWorld/[^/]+/)[^:]*:(.*)$", line)
            if not m or m.group(1) not in systems:
                continue
            got = re.search(pattern, m.group(2))
            if got and got.group(1) not in systems[m.group(1)][key]:
                systems[m.group(1)][key].append(got.group(1))
    data = {"base": ref, "base_sha": sha, "built": time.time(), "systems": systems, "touched": old.get("touched", [])}
    _atomic(map_path(), data)
    return data


def map_for_lane(lane: str, max_chars: int = 2500) -> str:
    m = load_map()
    lines = []
    for folder, s in sorted((m.get("systems") or {}).items()):
        if s.get("lane") != lane:
            continue
        lines.append(f"- {folder} ({s['files']} files): classes {', '.join(s['classes'][:14]) or '-'}; "
                     f"test areas {', '.join(sorted(set(s['tests']))[:6]) or '-'}; scenarios {', '.join(s['scenarios']) or '-'}")
    for t in [t for t in m.get("touched", []) if t.get("lane") == lane][-3:]:
        lines.append(f"- recently changed by {t['id']} (green): {', '.join(t['files'][:5])}")
    return "\n".join(lines)[:max_chars]


def update_map_with(order: Dict, files: List[str]) -> None:
    m = load_map()
    if not m.get("systems"):
        m = build_map(order.get("base"))
    m.setdefault("touched", [])
    if any(t.get("id") == order["id"] for t in m["touched"]):
        return
    m["touched"].append({"id": order["id"], "lane": order["lane"], "files": files[:20], "commit": order.get("commit"),
                         "t": time.time()})
    m["touched"] = m["touched"][-200:]
    _atomic(map_path(), m)


# ------------------------------------------------------------------------------------------------ the dataset (not trained on)
def dataset_path() -> Path:
    return _dir("dataset") / "tasks.jsonl"


def _dataset_row(order: Dict, lesson: Dict) -> None:
    path = dataset_path()
    if path.exists() and f'"id": "{order["id"]}"' in path.read_text(encoding="utf-8"):
        return                                              # one row per order
    from core import studiojob
    row = {"schema": "jarvis-studio-task/1", "id": order["id"], "t": time.time(),
           "input": {"note": order["note"], "lane": order["lane"], "base_sha": order["base_sha"],
                     "work_order": {k: order.get(k) for k in ("system", "files", "acceptance")},
                     "prompt": order.get("prompt_sent") or "", "prompt_recorded": bool(order.get("prompt_sent"))},
           "output": {"diff": lesson["diff"], "files": lesson["files_touched"]},
           "verified": {"build": (order.get("results") or {}).get("build"), "automation": (order.get("results") or {}).get("automation"),
                        "scenarios": (order.get("results") or {}).get("scenarios"), "fix_rounds": order.get("fix_rounds", 0)},
           "provenance": {"worker": order["worker"], "model": {"credit": studiojob.CREDIT_MODEL, "claude": "Claude Code (owner login)",
                                                                "codex": "Codex (owner login)"}.get(order["worker"], order["worker"]),
                          "training_use": "not cleared: provider terms restrict training competing models on outputs"}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_red(row)) + chr(10))


# ------------------------------------------------------------------------------------------------ measuring the learning
def decisions_dir() -> Path:
    return _dir("decisions")


def note_decision(mission_id: str, approved: bool) -> None:
    """Called by the LAPTOP when he answers a studio order's approval (core/missions.answer_approval): the reliable record
    of a rejection (the PC never sees a 'no')."""
    if mission_id:
        _atomic(decisions_dir() / f"{mission_id}.json", {"mission": mission_id, "approved": bool(approved), "t": time.time()})


def _decisions() -> Dict[str, Dict]:
    out = {}
    for p in decisions_dir().glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("mission"):
            out[d["mission"]] = d
    return out


def metrics() -> Dict:
    backfill_error = ""
    try:
        backfill()
    except Exception as e:  # noqa: BLE001
        backfill_error = type(e).__name__
    dec = _decisions()
    orders = sorted(studio.orders(), key=lambda o: o.get("created", 0))
    for o in orders:
        d = dec.get(o.get("mission") or "")
        o["_rejected"] = bool(d and not d.get("approved"))
    rejected = [o for o in orders if o["_rejected"]]
    resaid = 0
    for r in rejected:                  # heuristic: a later note within 48 h sharing >= 60% of the rejected note's words
        rw = _words(r["note"])
        later = [o for o in orders if 0 < o.get("created", 0) - r.get("created", 0) <= 48 * 3600 and not o["_rejected"]]
        if rw and any(len(_words(o["note"]) & rw) >= max(2, 0.6 * len(rw)) for o in later):
            resaid += 1
    finished = [o for o in orders if o["state"] in ("green", "red")]

    def rate(items):
        return round(sum(1 for i in items if int(i.get("fix_rounds") or 0) > 0) / len(items), 2) if items else None
    half = len(finished) // 2
    decided = [o for o in orders if o["_rejected"] or o["state"] != "draft"]
    dh = len(decided) // 2
    with_l = [o for o in finished if o.get("lessons_used")]
    without = [o for o in finished if not o.get("lessons_used")]
    return {"orders": len(orders), "decided": len(decided), "rejected": len(rejected), "resaid_after_reject": resaid,
            "finished": len(finished), "green": sum(1 for o in finished if o["state"] == "green"),
            "needed_fix_round": sum(1 for o in finished if int(o.get("fix_rounds") or 0) > 0),
            "lessons": len(lessons()), "failures": len(failures()),
            "orders_using_lessons": sum(1 for o in orders if o.get("lessons_used")),
            "fix_round_rate": {"first_half": rate(finished[:half]), "second_half": rate(finished[half:]),
                               "with_lessons": rate(with_l), "without_lessons": rate(without)},
            "reject_rate": {"first_half": (round(sum(o["_rejected"] for o in decided[:dh]) / dh, 2) if dh else None),
                            "second_half": (round(sum(o["_rejected"] for o in decided[dh:]) / (len(decided) - dh), 2)
                                            if len(decided) - dh else None)},
            "enough_to_tell": half >= HALF_ENOUGH and dh >= HALF_ENOUGH, "backfill_error": backfill_error}


def spoken_metrics(m: Optional[Dict] = None) -> str:
    m = m or metrics()
    from core.words import plural
    if not m["orders"]:
        return "The studio has no work orders yet, so there's nothing to measure."
    s = (f"{plural(m['orders'], 'work order')} so far: {m['rejected']} rejected by you"
         + (f" (about {m['resaid_after_reject']} of those you said again differently; that count is a guess from the words)"
            if m["rejected"] else "")
         + f", {m['finished']} finished, {m['green']} green, {m['needed_fix_round']} needed a second pass from the worker. "
         f"I keep {plural(m['lessons'], 'verified lesson')} and {plural(m['failures'], 'recorded failure')}; "
         f"{m['orders_using_lessons']} orders were written with a past lesson.")
    if not m["enough_to_tell"]:
        return s + f" That's too few to say whether my work orders are getting better: I need {HALF_ENOUGH} orders in each half."
    fr, rr = m["fix_round_rate"], m["reject_rate"]
    s += (f" Second passes went from {fr['first_half']:.0%} of the early orders to {fr['second_half']:.0%} of the later ones, "
          f"and rejections from {rr['first_half']:.0%} to {rr['second_half']:.0%}.")
    if fr["with_lessons"] is not None and fr["without_lessons"] is not None:
        s += (f" Orders written with a lesson needed a second pass {fr['with_lessons']:.0%} of the time, without one "
              f"{fr['without_lessons']:.0%}; the work differs, so that's a hint, not proof the lessons caused it.")
    return s
