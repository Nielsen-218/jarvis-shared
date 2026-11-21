"""AUTONOMOUS SKILL ACQUISITION: from the owner's real misses to a verified, retained, reviewable PC method.

    discover -> investigate -> design -> practice -> verify -> improve -> retain -> integrate -> retest

Who does what (this is the honest split, also written into every cycle's evidence):
  * The LEARNER is a local model (JARVIS_LEARNER_MODEL, default qwen2.5-coder:14b). It writes the hypotheses, the practice
    exercises, the search queries, the diagnosis and the method code. Nobody writes the lesson or the expected solution for it.
  * The HOST (code reviewed by people) owns everything that decides success: the isolated lab (core/skilllab), the success
    contract and the held-out worlds (core/skillcontracts), the method language and its primitives (core/methodlang), the budgets,
    and promotion. A method is promoted only when the host's judge passes every one of 12+ held-out worlds the learner never saw.
  * Promotion is not activation: a learned method runs on the owner's desktop only after the owner approves that exact code
    (approve(), bound to its hash). Until then it is "awaiting review".

Budgets (no endless loops): MAX_ROUNDS design rounds, MAX_MODEL_CALLS local model calls, MAX_EXAMS held-out exams, a wall-clock
limit per cycle, lab memory/CPU caps (core/skilllab), and ZERO cloud calls: the learner is called on the local Ollama server only.
Practice runs only on the Windows PC, only while the owner is away, and stops the moment they touch the keyboard or mouse.
"""
import hashlib
import inspect
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from core import storage

LEARNER = os.getenv("JARVIS_LEARNER_MODEL", "qwen3.5:9b")  # 2026-11-09: the one brain; gpt-oss:20b and qwen2.5-coder were deleted
OLLAMA = os.getenv("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")


def _ollama() -> str:
    """Ollama's NATIVE base. Everywhere else JARVIS_OLLAMA_URL is the OpenAI-compatible base ("http://host:11434/v1", see
    core/intelligence and core/warmth), and "<base>/v1/api/chat" does not exist: Ollama answers 404 Not Found, which is how
    two nightly cycles died on their first learner call (2026-11-11/12). The suffix is stripped here like core/warmth does."""
    return OLLAMA.rstrip("/").removesuffix("/v1")
MAX_ROUNDS = 8
MAX_MODEL_CALLS = 80    # 40 would run out once rejected methods get one rewrite each (2026-11-08)
CANDIDATE_TEMPERATURES = (0.3, 0.6, 0.9)    # several different attempts per round; the best one survives
MAX_EXAMS = 2
CYCLE_SECONDS = 2 * 60 * 60
PRACTICE_PER_ROUND = 5
HELD_OUT_SEEDS = tuple(range(90001, 90013))         # 12 sealed worlds: never shown to the learner, results never fed back in detail
RETEST_SEEDS = tuple(range(70001, 70004))           # fresh again: used only after restart, through real JARVIS
IDLE_SECONDS = 15 * 60


def _root() -> Path:
    return Path(os.getenv("JARVIS_LEARNED_DIR", str(storage.DATA / "learned-methods")))


def _evidence_root() -> Path:
    return Path(os.getenv("JARVIS_ACQUIRE_DIR", str(storage.DATA / "learning" / "acquire")))


def _misses_path() -> Path:
    return Path(os.getenv("JARVIS_MISSES_FILE", str(storage.DATA / "learning" / "live-misses.jsonl")))


def _sha(text) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


# ============================================================================================ 1 DISCOVER

_UNDERSTANDING = re.compile(r"not understood|didn't understand|unclear", re.I)


def _signature(reason: str, args: dict) -> str:
    text = reason.casefold()
    for v in (args or {}).values():
        if isinstance(v, str) and v.strip():
            text = text.replace(v.casefold(), "<arg>")
    return re.sub(r"\d+(?:\.\d+)?", "#", re.sub(r"\s+", " ", text)).strip()[:160]


def known_skills() -> dict:
    """{tool: where} for every tool Jarvis already holds a verified learned method or skill for, in EITHER store
    (data/learned-methods "pcmethod.*" and data/learned-skills "skill.*"). A skill demoted by a failed retest is not known."""
    out = {}
    try:
        gone = demoted()
        for m in learned_methods():
            if (gone.get(m["id"]) or {}).get("source_sha") != m["source_sha"]:      # demoted by a retest: learn it again
                out[m["for_tool"]] = "method " + m["id"]
    except Exception:  # noqa: BLE001 - a broken store never breaks the ranking
        pass
    try:
        from core import skillspec
        lib, gone = skillspec.library(), demoted()
        for sid in lib.ids() if lib.root.exists() else []:
            if not sid.startswith("skill."):
                continue
            got = lib.get(sid)
            if got and (gone.get(sid) or {}).get("source_sha") != got[0]["source_sha"]:
                out[sid[len("skill."):]] = "skill " + sid
    except Exception:  # noqa: BLE001
        pass
    return out


def _studied() -> dict:
    try:
        from core import misslessons
        return misslessons.lessons()
    except Exception:  # noqa: BLE001
        return {}


def discover(misses=None, studied=None) -> list:
    """The owner's real misses (failed turns AND what the nightly miss lessons could not work out), deduplicated into
    weaknesses and ranked by how often they happen. Pure data: safe on the laptop or the PC."""
    from core import authz, misslessons, skillcontracts
    studied = _studied() if studied is None else studied
    if misses is None:
        f = _misses_path()
        misses = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []
        try:                                           # a miss whose sentence worked later is recovered (core.misses): not a weakness
            from core import misses as _misses
            recovered = {r.get("key"): float(r.get("at") or 0) for r in _misses._read(_misses.RECOVERIES)}
            for m in misses:
                k = m.get("key") or _misses.key(m.get("said", ""), "")
                if m.get("route") != "api" and recovered.get(k, 0) >= float(m.get("at") or 0):   # API-taught: still to learn
                    m["recovered_at"] = recovered[k]
        except Exception:  # noqa: BLE001
            pass
    groups = {}
    for m in misses:
        said, reason = str(m.get("said", "")), str(m.get("reason", ""))
        if said.startswith("[Automatic") or m.get("recovered_at"):
            continue                                   # not his request, or the same sentence worked later
        intents = (m.get("decision") or {}).get("intents") or []
        steps = [s for i in intents for s in (i.get("steps") or [])]
        if _UNDERSTANDING.search(reason) or not reason.startswith("step not verified"):
            kind, tool, args = "understanding", "", {}
        else:
            kind = "execution"
            tool, args = (steps[0]["tool"], steps[0].get("arguments") or {}) if len(steps) == 1 else ("", {})
        sig = _signature(reason.split("Stopped without")[0], args)
        key = _sha(f"{kind}|{tool}|{sig}")
        g = groups.setdefault(key, {"id": key, "kind": kind, "tool": tool, "signature": sig, "examples": [], "count": 0, "last_at": 0})
        g["count"] += 1
        g["last_at"] = max(g["last_at"], float(m.get("at", 0)))
        if len(g["examples"]) < 4:
            g["examples"].append({"said": said, "args": args, "reason": reason[:240], "at": m.get("at")})
    learned = known_skills()
    try:
        from core import learned as _learned
        removed = _learned.removed_tools()
    except Exception:  # noqa: BLE001
        removed = {}
    out = []
    for g in groups.values():
        risk = authz.EFFECTS.get(g["tool"], "unknown") if g["tool"] else "n/a"
        has_lab = g["tool"] in skillcontracts.KINDS
        rows = [studied.get(misslessons._key(e["said"])) or {} for e in g["examples"]]
        stuck = [r for r in rows if r.get("outcome") in ("unclear", "unsupported") and int(r.get("tries") or 0) >= 2]
        g["restudied"] = sum(int(r.get("tries") or 0) for r in rows)
        if g["kind"] == "understanding" and stuck:
            status, note = "needs_lesson", ("re-studied more than once and still not understood: one lesson is asked of the local "
                                            "teacher first (core/misslessons.teach)")
        elif g["kind"] == "understanding":
            status, note = "elsewhere", "an understanding gap: re-studied nightly by core/misslessons, not by practice"
        elif "save it before i close" in g["signature"] or "has unsaved changes" in g["signature"]:
            status, note = "resolved", "Jarvis now asks about saving on purpose (core/saveprompt)"
        elif g["tool"] in removed:
            status, note = "removed_by_owner", "the owner removed the learned skill for this: not learned again unless he asks"
        elif g["tool"] in learned:
            status, note = "learned", f"already learned ({learned[g['tool']]}): re-tested, not re-learned"
        elif risk not in ("observe", "reversible"):
            status, note = "needs_owner", f"{risk} actions are never practised unattended"
        elif not has_lab:
            status, note = "UNVERIFIED", (f"no isolated lab world or independent verifier exists for {g['tool'] or 'this request'} yet: "
                                          "needs a verifier or an owner demonstration before Jarvis can practise it")
        else:
            status, note = "ready", "low-risk, reproducible in the isolated lab, independently judged"
        recent = 1 if time.time() - g["last_at"] < 7 * 86400 else 0
        g.update(status=status, note=note, risk=risk, lab=has_lab,
                 priority=(10 if status == "ready" else 0) + 2 * g["count"] + recent)
        out.append(g)
    # Most frequent first within what can be worked on now (ready), then everything else by how often it happens.
    out.sort(key=lambda g: (-g["priority"], -g["count"], -g["last_at"]))
    return out


def _demoted_file() -> Path:
    return storage.DATA / "learning" / "demoted-skills.json"


def demoted() -> dict:
    """{skill or method id: {"version", "source_sha", "why", "at"}} for learned skills that failed a later retest."""
    try:
        return json.loads(_demoted_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_queue(rows) -> Path:
    path = storage.DATA / "learning" / "acquire-queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.time(), "weaknesses": rows}, indent=1), encoding="utf-8")
    return path


# ============================================================================================ the learner (local model only)

class OutOfBudget(RuntimeError):
    pass


class LearnerUnavailable(RuntimeError):
    """The local model server could not be reached or did not know the model: an outage, not a failed lesson."""


def error_cause(error) -> str:
    """Why a cycle stopped on an exception, by what it REALLY was: only the local model server not answering is an outage
    (Learner wraps its own OSErrors in LearnerUnavailable); any other exception, an OSError from the lab or the disk included,
    is a crash, recorded with its type, never relabelled as the model being away."""
    return "model_unavailable" if isinstance(error, LearnerUnavailable) else "crash"


class Learner:
    def __init__(self, model=LEARNER, max_calls=MAX_MODEL_CALLS, ask=None, guard=None,
                 system="You are JARVIS teaching yourself a new PC-control method. Be concrete and precise.", think=False):
        self.model, self.max_calls, self.calls, self.seconds, self._ask = model, max_calls, 0, 0.0, ask
        self.think = think      # the local TEACHER reasons before it answers (more budget); the learner's own drafts do not
        self.system = system
        self.transcript = []
        # writing a method takes minutes of GPU: the owner coming back must stop the MODEL too, not only the lab
        self.guard = (os.getenv("JARVIS_LAB_GUARD", "on") != "off" and os.name == "nt") if guard is None else guard

    def __call__(self, prompt, temperature=0.2, fmt=None, num_predict=1600) -> str:
        if self.calls >= self.max_calls:
            raise OutOfBudget(f"learner budget of {self.max_calls} model calls used")
        self.calls += 1
        started = time.monotonic()
        if self._ask:
            text = self._ask(prompt, temperature, fmt)
        else:
            from core import warmth
            # The brain's own num_ctx/keep-alive when the learner IS the brain (another size reloads it), thinking off.
            body = {"model": self.model, "stream": False, **warmth.ollama_fields(self.model, "2m"),
                    "messages": [{"role": "system", "content": self.system},
                                 {"role": "user", "content": prompt}]}
            body["options"].update(temperature=temperature, num_predict=num_predict)
            if fmt:
                body["format"] = fmt
            if self.think and "think" in body:               # a model that can think: let it, and leave room for the answer
                body["think"] = True
                body["options"]["num_predict"] = num_predict * 4
            if "gpt-oss" in self.model:                     # a reasoning model: it thinks first, so it needs room for the answer
                body["think"] = "low"
                body["options"]["num_predict"] = num_predict * 4
            try:
                text = self._stream(body)
            except OSError as error:                         # URLError, HTTPError (404: unknown model/path), reset, timeout
                self.seconds += time.monotonic() - started
                raise LearnerUnavailable(f"the local model did not answer ({type(error).__name__}: {str(error)[:120]})") from error
        self.seconds += time.monotonic() - started
        self.transcript.append({"call": self.calls, "prompt_sha": _sha(prompt), "chars_in": len(prompt), "reply": text[:6000],
                                "seconds": round(time.monotonic() - started, 1)})
        return text


    def _stream(self, body) -> str:
        """Stream the reply so it can be abandoned mid-generation: closing the connection makes Ollama stop at once."""
        from core import skilllab
        body = {**body, "stream": True}
        req = urllib.request.Request(_ollama() + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        before, parts, deadline = skilllab._last_input() if self.guard else 0, [], time.monotonic() + 900
        with urllib.request.urlopen(req, timeout=120) as resp:
            for line in resp:
                if self.guard and skilllab._last_input() != before:
                    raise skilllab.Suspended("the owner started using the PC while the learner was writing")
                if time.monotonic() > deadline:
                    raise OutOfBudget("one learner reply took longer than 15 minutes")
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(f"local model error: {chunk['error']}")
                parts.append((chunk.get("message") or {}).get("content", ""))
                if chunk.get("done"):
                    break
        return "".join(parts)


def learner_ready(model=LEARNER) -> str:
    """'' when the local learner model is served, otherwise why not (so a stopped Ollama is waited out, not counted as failures)."""
    try:
        with urllib.request.urlopen(_ollama() + "/api/tags", timeout=10) as resp:
            names = {m.get("name", "") for m in json.loads(resp.read()).get("models", [])}
    except (OSError, ValueError) as error:
        return f"the local model server is not reachable ({type(error).__name__})"
    return "" if model in names or f"{model}:latest" in names else f"the learner model {model} is not installed"


def _json(text):
    m = re.search(r"\{.*\}|\[.*\]", text or "", re.S)
    if not m:
        raise ValueError("no JSON in the reply")
    return json.loads(m.group(0))


def _code(text):
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text or "", re.S)
    src = max(blocks, key=len) if blocks else (text or "")
    i = src.find("def run(")
    return src[i:].strip() + "\n" if i >= 0 else src.strip() + "\n"


# ============================================================================================ 2 INVESTIGATE

def _native_source(tool) -> str:
    """The code that failed: read from the dispatcher, the same way a developer would look it up."""
    from core import pcactions
    disp = inspect.getsource(pcactions.perform)
    m = re.search(r'if name == "%s":\s*\n\s*return (_\w+)\(' % re.escape(tool), disp)
    fn = getattr(pcactions, m.group(1), None) if m else None
    return inspect.getsource(fn) if fn else ""


def _same_failure(error, params, reason, args) -> bool:
    """Did the current code fail in the lab the SAME way it failed for the owner (same message, names aside)?"""
    want = _signature(reason.replace("step not verified: ", "").split(" Stopped without")[0], args)
    got = _signature(str(error).split(": ", 1)[-1], params)
    return bool(want) and bool(got) and want[:60] == got[:60]


def _world_text(world) -> str:
    rows = []
    for wi, win in enumerate(world["windows"]):
        tabs = ", ".join(f"[{'FRONT ' if ti == win.get('active') else ''}{t['title']!r} on {t['host']}]" for ti, t in enumerate(win["tabs"]))
        rows.append(f"window {wi + 1}: {tabs}")
    return "; ".join(rows)


_WORLD_SCHEMA = {"type": "object", "properties": {"exercises": {"type": "array", "items": {"type": "object", "properties": {
    "why": {"type": "string"}, "params": {"type": "object", "properties": {"site": {"type": "string"}}, "required": ["site"]},
    "windows": {"type": "array", "items": {"type": "object", "properties": {
        "tabs": {"type": "array", "items": {"type": "object", "properties": {"host": {"type": "string"}, "title": {"type": "string"}},
                                            "required": ["host", "title"]}},
        "active": {"type": "integer"}}, "required": ["tabs", "active"]}}},
    "required": ["why", "params", "windows"]}}}, "required": ["exercises"]}


def investigate(cand, learner, lab, log) -> dict:
    """What is missing? Existing skills, the failing code, hypotheses tested in the lab, what the UI really exposes, research."""
    from core import pcmethods, skillcontracts
    tool = cand["tool"]
    ex = cand["examples"][0]
    step = {"tool": tool, "arguments": ex["args"]}
    existing = {"methods": pcmethods.candidates(step), "learned": [m["id"] for m in learned_methods() if m.get("for_tool") == tool]}
    native = _native_source(tool)
    log("investigate", "existing skills", existing)
    # Hypotheses: the learner proposes situations that would explain the miss. The lab tests them against the CURRENT code.
    prompt = (f"The owner said {ex['said']!r}. JARVIS ran the PC step {tool}({json.dumps(ex['args'])}) and it failed with:\n  {ex['reason']}\n\n"
              f"This is the code that failed:\n```python\n{native[:3500]}\n```\n\n"
              "Propose 4 concrete browser situations (Microsoft Edge windows and their tabs) that would make this code fail with that "
              "same message, so the failure can be reproduced safely. Use real-looking tab titles and host names "
              f"(the site must be one of {sorted(skillcontracts.SITE_HOSTS)}). 'active' is the index of the tab in front in each window. "
              "Reply as JSON: {\"exercises\": [{\"why\": ..., \"params\": {\"site\": ...}, \"windows\": [{\"tabs\": [{\"host\": ..., \"title\": ...}], \"active\": 0}]}]}")
    exercises, rejected = [], []
    try:
        for e in _json(learner(prompt, fmt=_WORLD_SCHEMA)).get("exercises", [])[:4]:
            try:
                exercises.append({**skillcontracts.validate_world(e, tool), "why": str(e.get("why", ""))[:200]})
            except ValueError as error:
                rejected.append(str(error))
    except (ValueError, OutOfBudget) as error:
        rejected.append(f"no usable exercises: {error}")
    log("investigate", "learner hypotheses", {"accepted": len(exercises), "rejected": rejected,
                                             "worlds": [_world_text(e) + "  // " + e["why"] for e in exercises]})
    worlds = exercises + [skillcontracts.generate(s, tool) for s in (501, 502)]
    run = lab([{"world": w, "action": {"type": "native"}} for w in worlds] + [{"world": worlds[0], "action": {"type": "observe"}}], "reproduce")
    repro = []
    for w, r in zip(worlds, run["results"]):
        err = (r.get("result") or {}).get("error", "") or r.get("lab_error", "")[:200]
        repro.append({"world": _world_text(w), "by": "learner" if w.get("seed") == "learner" else "host", "native_error": err,
                      "reproduced": _same_failure(err, w["params"], ex["reason"], ex["args"]),
                      "passed": bool((r.get("judge") or {}).get("passed"))})
    tree = (run["results"][-1].get("result") or {}).get("tree", "") if run["results"] else ""
    log("investigate", "lab reproduction of the miss", {"status": run["status"], "trials": repro, "tree_lines": tree.count("\n") + 1})
    # Research: the learner writes its own queries; the existing web-research chain fetches public pages (bounded, read-only).
    notes = []
    try:
        qs = _json(learner("A Windows assistant must close a background (not active) Microsoft Edge tab by its site name using only "
                           "UI Automation (no keyboard or mouse). Write 2 short web search queries that would find documentation "
                           "about how Edge/Chromium tabs appear in UI Automation and how to close one. JSON: {\"queries\": [..]}",
                           fmt={"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}}, "required": ["queries"]})).get("queries", [])[:2]
    except (ValueError, OutOfBudget):
        qs = []
    for q in qs:
        try:
            from core import websearch
            pack = websearch.research_question(str(q)[:160], max_pages=2, objective=str(q)[:160])
            for s in pack.get("sources", [])[:2]:
                notes.append({"query": q, "title": s.get("title", ""), "url": s.get("url", ""), "excerpt": (s.get("excerpt") or "")[:700]})
        except Exception as error:  # noqa: BLE001 - research is optional evidence; offline is fine
            notes.append({"query": q, "error": type(error).__name__})
    log("investigate", "research", {"queries": qs, "sources": [(n.get("title"), n.get("url")) for n in notes if n.get("url")],
                                    "failed": [n for n in notes if n.get("error")]})
    try:
        diagnosis = learner("From this evidence, say in 2-3 sentences what capability the failing code is missing. "
                            f"Failing code:\n{native[:2500]}\n\nLab reproductions:\n{json.dumps(repro)[:2500]}\n\n"
                            f"What UI Automation shows in such a browser:\n{tree[:3500]}", temperature=0.1, num_predict=300).strip()
    except OutOfBudget:
        diagnosis = ""
    log("investigate", "learner diagnosis", diagnosis)
    return {"existing": existing, "native": native, "exercises": exercises, "reproduction": repro, "tree": tree, "research": notes,
            "diagnosis": diagnosis, "reproduced": any(r["reproduced"] for r in repro)}


# ============================================================================================ 3-6 DESIGN, PRACTICE, VERIFY, IMPROVE

def _design_prompt(cand, inv, feedback, previous, teaching=""):
    from core import methodlang, skillcontracts
    tool = cand["tool"]
    research = "\n".join(f"- {n['title']} ({n['url']}): {n['excerpt'][:500]}" for n in inv["research"] if n.get("url"))
    parts = [
        f"Write a NEW method for the PC step `{tool}`. Goal, for params {{'site': <one of {sorted(skillcontracts.SITE_HOSTS)}>}}:",
        "  " + skillcontracts.goal(tool, {"site": "github"}).replace("GitHub", "<that site>").replace("github.com", "<its domain>"),
        "The old method (below) fails in the owner's real use:", f"```python\n{inv['native'][:2500]}\n```",
        "What you found when you investigated: " + (inv["diagnosis"] or "(no diagnosis)"),
        "UI Automation tree of a real Edge window in such a situation (control type and name):", inv["tree"][:4500],
    ]
    if research:
        parts += ["Public documentation you found:", research[:2500]]
    parts += [methodlang.API_DOC]
    if teaching:
        parts += [teaching]
    if previous:
        parts += ["Your previous attempt:", f"```python\n{previous}\n```", "What happened when it was practised:", feedback[:5000],
                  "Fix the cause of every failure above. Keep what worked."]
    parts += ["Reply with ONLY the complete method in one ```python block."]
    return "\n".join(parts)


def all_results(worlds, run) -> list:
    """One lab result per world. A job that died part-way returns FEWER results: every missing world is a failed lab row,
    never silently dropped (review 2026-11-12: zip() let 3 results for 12 sealed worlds count as "3/3 passed")."""
    results = list(run.get("results") or [])[:len(worlds)]
    return results + [{"lab_error": "no result: the lab job ended before this world ran"}] * (len(worlds) - len(results))


def exam_passed(run_status, rows, worlds) -> bool:
    """A sealed exam counts only when the lab job FINISHED and every one of its worlds was judged and passed."""
    return run_status == "done" and len(worlds) > 0 and len(rows) == len(worlds) and all(r["passed"] for r in rows)


def _practice(cand, source, worlds, lab, label):
    run = lab([{"world": w, "action": {"type": "method", "source": source}} for w in worlds], label)
    rows = []
    for w, r in zip(worlds, all_results(worlds, run)):
        j = r.get("judge") or {"passed": False, "diff": [r.get("lab_error", "lab error")[-300:]], "checks": {}}
        res = r.get("result") or {}
        rows.append({"seed": w.get("seed"), "site": w["params"]["site"], "world": _world_text(w), "passed": bool(j["passed"]),
                     "checks": j.get("checks", {}), "diff": j.get("diff", []), "trace": res.get("trace", [])[:40],
                     "message": res.get("message", ""), "error": res.get("error", ""), "calls": res.get("calls", 0)})
    return run["status"], rows


def _warm_start(cand, folder):
    """The best method an earlier attempt on this weakness kept (by its own recorded practice score). None when there is none."""
    best = None
    for f in sorted((_evidence_root() / cand["id"]).glob("*/cycle.json")):
        if f.parent == folder:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for st in rec.get("stages", []):
            d = st.get("data") if isinstance(st.get("data"), dict) else {}
            if st.get("stage") != "practice" or not d.get("kept_sha") or not d.get("rows"):
                continue
            score = sum(1 for r in d["rows"] if r.get("passed"))
            for m in f.parent.glob("method-r*.py"):
                src = m.read_text(encoding="utf-8")
                if _sha(src) == d["kept_sha"] and score > 0 and (not best or score >= best["n"]):
                    best = {"source": src, "score": f"{score}/{len(d['rows'])}", "n": score, "from": str(m)}
    return best


RESUME_HOURS = 48


def _shape(source) -> str:
    """The code without comments or formatting, so a reworded copy is not practised again as if it were new."""
    import ast
    try:
        return ast.dump(ast.parse(source))
    except SyntaxError:
        return source


def _checks(source) -> bool:
    from core import methodlang
    try:
        methodlang.check(source)
        return True
    except methodlang.BadMethod:
        return False


def _save_resume(folder, cand, inv, source, feedback, untested) -> str:
    """When the owner comes back mid-cycle, keep the work done so far so the next idle window does not redo it."""
    if not inv:
        return "nothing (stopped during the investigation)"
    (folder / "resume.json").write_text(json.dumps({"weakness": cand["id"], "native_sha": _sha(inv.get("native", "")), "at": time.time(),
                                                    "inv": inv, "source": source, "feedback": feedback, "untested": untested},
                                                   indent=1, default=str), encoding="utf-8")
    return f"the investigation, the best method so far and {len(untested)} untested methods (resume.json)"


def _resume(cand, folder):
    """The newest stopped attempt's saved work on this weakness, if it is recent and the failing code it studied is unchanged.
    Used once: the file is renamed so two later attempts never both resume from it."""
    for f in sorted((_evidence_root() / cand["id"]).glob("*/resume.json"), reverse=True):
        if f.parent == folder:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f.rename(f.with_name("resume-used.json"))
        if data.get("weakness") != cand["id"] or time.time() - data.get("at", 0) > RESUME_HOURS * 3600:
            continue
        if data.get("native_sha") != _sha(_native_source(cand["tool"])):
            continue                                 # the code the investigation studied has changed: investigate again
        data["from"] = f.parent.name
        return data
    return None


def _practice_many(sources, worlds, lab, label):
    """Every candidate on the same worlds, in one lab job. -> [(status, rows)] in the order of sources."""
    trials = [{"world": w, "action": {"type": "method", "source": s}} for s in sources for w in worlds]
    run = lab(trials, label)
    out = []
    for i, s in enumerate(sources):
        part = {"status": run["status"], "results": run["results"][i * len(worlds):(i + 1) * len(worlds)]}
        out.append(_practice(None, s, worlds, lambda t, l, p=part: p, label))
    return out


def _feedback(rows):
    out = []
    for r in rows:
        if r["passed"]:
            out.append(f"PASSED  site={r['site']}  {r['world'][:300]}")
        else:
            trace = "\n      ".join(r["trace"][:25])
            out.append(f"FAILED  site={r['site']}  {r['world'][:500]}\n   judge: {'; '.join(r['diff'])[:600]}\n"
                       f"   what your method called and saw:\n      {trace}\n   your error: {r['error'][:300] or '(none)'}")
    return "\n".join(out)


def learn(cand, learner=None, lab=None, now=time.time, escalate=True) -> dict:
    """One full cycle for one weakness. -> the cycle record (also written to the evidence folder)."""
    from core import methodlang, procedures, skillcontracts, skilllab
    if (skillcontracts.KINDS.get(cand.get("tool")) or {}).get("world") == "desktop":
        from core import labpractice           # a purpose-built window world: practised and examined, never put into use
        return labpractice.learn(cand, learner=learner, lab=lab, now=now)
    learner = learner or Learner()
    started = now()
    folder = _evidence_root() / cand["id"] / time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while folder.exists():                              # two attempts in one second must never share (or overwrite) evidence
        n += 1
        folder = folder.with_name(time.strftime("%Y%m%d-%H%M%S") + f"-{n}")
    folder.mkdir(parents=True)
    record = {"weakness": cand, "learner_model": learner.model, "stages": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "cloud_calls": 0, "status": "running"}

    def log(stage, what, data, autonomous=True, assist=""):
        record["stages"].append({"stage": stage, "what": what, "autonomous": autonomous, "assist": assist, "at": round(now() - started, 1),
                                 "data": data})
        (folder / "cycle.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")

    if lab is None:
        def lab(trials, label):
            out = skilllab.run_job(trials, seconds=900, guard=os.getenv("JARVIS_LAB_GUARD", "on") != "off", label=label)
            if out["status"] == "suspended":
                raise skilllab.Suspended("the owner started using the PC")
            return out
    log("discover", "weakness chosen", {"id": cand["id"], "tool": cand["tool"], "count": cand["count"], "status": cand["status"]})
    from core import teacher
    judge_sha = _sha(inspect.getsource(skillcontracts))       # the grader must be byte-identical when a method is promoted
    inv, source, feedback, untested = None, "", "", []   # untested = methods written but not yet practised (kept if stopped)
    try:
        if escalate:
            teacher.escalate(cand, log)                   # stuck for several cycles: ask for guidance (never for the method)
        teaching = teacher.guidance(cand["id"])
        record["provenance"] = teacher.provenance(cand["id"])
        resumed = _resume(cand, folder)
        if resumed:                 # a stopped attempt's investigation and unpractised methods are reused, not redone
            inv, source, feedback = resumed["inv"], resumed["source"], resumed["feedback"]
            untested = [s for s in resumed["untested"] if _checks(s)]
            if source:
                (folder / "method-r0-best-so-far.py").write_text(source, encoding="utf-8")
            log("investigate", f"reused the investigation of a stopped attempt ({resumed['from']})",
                {"from": resumed["from"], "untested_methods": len(untested), "has_best": bool(source)})
        else:
            inv = investigate(cand, learner, lab, log)
        if not inv["reproduced"]:
            record["status"] = "not_reproduced"
            log("investigate", "stopped", "the miss could not be reproduced in the lab, so practice would prove nothing")
            return _finish(record, folder, learner)
        practice_worlds = list(inv["exercises"])
        exams, best = 0, None                               # source = the best method so far (kept, re-tested every round)
        warm = None if source else _warm_start(cand, folder)
        if warm:                    # a paused or unfinished attempt's best method is kept, not thrown away; it must still pass everything
            source = warm["source"]
            log("design", f"warm start: the best method of an earlier unfinished attempt ({warm['score']} practice worlds passed there)",
                {"from": warm["from"], "sha": _sha(source)})
        fresh = []
        for rnd in range(1, MAX_ROUNDS + 1):
            if now() - started > CYCLE_SECONDS:
                log("improve", "stopped", "cycle time budget used")
                break
            fresh = []
            if untested:
                fresh, untested = untested, []
                for k, m in enumerate(fresh):           # kept in THIS attempt's evidence too, so a later warm start can find them
                    (folder / f"method-r{rnd}-resumed-{k}.py").write_text(m, encoding="utf-8")
                log("design", f"round {rnd}: {len(fresh)} methods written before the stop go straight to practice",
                    {"shas": [_sha(s) for s in fresh]})
            else:
                try:
                    analysis = ""
                    if source and feedback:              # self-debugging: first explain the failure from the observations, then rewrite
                        analysis = learner("Your method for this PC step failed in practice.\n```python\n" + source +
                                           "\n```\nWhat happened:\n" + feedback[:5000] + "\n\nCompare what your method expected with what "
                                           "the ui calls actually returned. In 3-5 sentences: what exactly went wrong, and what must change?" +
                                           ("\n\n" + teaching if teaching else ""),
                                           temperature=0.2, num_predict=400).strip()
                        log("improve", f"round {rnd}: learner's own failure analysis", analysis)
                    notes = feedback + ("\n\nYour own analysis of the failure:\n" + analysis if analysis else "")
                    for k, temperature in enumerate(CANDIDATE_TEMPERATURES):
                        new = _code(learner(_design_prompt(cand, inv, notes, source, teaching), temperature=temperature))
                        (folder / f"method-r{rnd}-{k}.py").write_text(new, encoding="utf-8")
                        try:
                            methodlang.check(new)
                        except methodlang.BadMethod as error:
                            log("design", f"round {rnd}: candidate {k} rejected by the host checker", str(error))
                            notes += f"\n\nOne of your methods was REJECTED before running (not allowed in the method language): {error}"
                            # 2026-11-08: 19 of 24 methods in one cycle were rejected (helper functions, try) and never practised.
                            # He gets ONE rewrite with the checker's exact words; the rules themselves do not change.
                            new = _code(learner("The method below was REJECTED by the checker before it could run:\n  " + str(error) +
                                                "\n```python\n" + new + "\n```\n" + methodlang.API_DOC +
                                                "\nRewrite it so it keeps the same logic but obeys every rule: put everything inline in run() "
                                                "(no inner def), no try/except, no while. Reply with ONLY the complete method in one ```python block.",
                                                temperature=0.2))
                            (folder / f"method-r{rnd}-{k}-rewrite.py").write_text(new, encoding="utf-8")
                            try:
                                methodlang.check(new)
                            except methodlang.BadMethod as again:
                                log("design", f"round {rnd}: candidate {k} rewrite rejected too", str(again))
                                continue
                            log("design", f"round {rnd}: candidate {k} rewritten within the rules", {"sha": _sha(new)})
                        if _shape(new) in {_shape(m) for m in [source] + fresh if m}:     # same code, only comments differ
                            log("design", f"round {rnd}: candidate {k} repeated an existing method", {"sha": _sha(new)})
                            continue
                        fresh.append(new)
                        log("design", f"round {rnd}: candidate {k} written", {"sha": _sha(new), "lines": new.count("\n"), "file": f"method-r{rnd}-{k}.py"})
                except OutOfBudget as error:
                    log("improve", "stopped", str(error))
                    if not fresh:
                        break
            if not fresh:
                feedback += "\n\nNOTE: your last replies repeated an existing method or were rejected. Write a different one."
                continue
            # the best method so far competes again on this round's NEW worlds; the best of the round becomes the base
            entrants = ([source] if source else []) + fresh
            worlds = practice_worlds + [skillcontracts.generate(1000 + 10 * rnd + i, cand["tool"]) for i in range(PRACTICE_PER_ROUND)]
            results = _practice_many(entrants, worlds, lab, f"practice-r{rnd}")
            scores = [sum(r["passed"] for r in rows) for _, rows in results]
            win = max(range(len(entrants)), key=lambda i: (scores[i], i))           # ties go to the newer method
            source, (status, rows), passed = entrants[win], results[win], scores[win]
            log("practice", f"round {rnd}: best {passed}/{len(rows)} practice worlds passed (scores {scores}; kept "
                f"{'the previous best' if source not in fresh else 'a new method'})",
                {"lab": status, "scores": scores, "kept_sha": _sha(source),
                 "rows": [{k: r[k] for k in ("seed", "site", "passed", "diff", "error")} for r in rows]})
            fresh = []                                    # practised now: nothing of this round is left untested
            if passed < len(rows):
                feedback = _feedback(rows)
                log("improve", f"round {rnd}: failures of the best method fed back to the learner", {"failed": len(rows) - passed})
                continue
            if exams >= MAX_EXAMS:
                break
            exams += 1
            held = [skillcontracts.generate(s, cand["tool"]) for s in HELD_OUT_SEEDS]
            status, exam = _practice(cand, source, held, lab, f"exam-{exams}")
            ok = sum(r["passed"] for r in exam)
            log("verify", f"held-out exam {exams}: {ok}/{len(exam)} unseen worlds passed (host judge, DevTools ground truth)",
                {"lab": status, "rows": [{k: r[k] for k in ("seed", "site", "passed", "diff")} for r in exam]})
            if exam_passed(status, exam, held):
                best = {"source": source, "exam": exam, "round": rnd}
                break
            # only the COUNT goes back: the sealed worlds' contents never reach the learner
            if status != "done":                          # the lab's failure, not the method's: say so, and count nothing as passed
                log("verify", f"held-out exam {exams} did not count: the lab job ended as '{status}'", {"lab": status})
                ok = min(ok, len(exam) - 1)
            feedback = (_feedback(rows) + f"\nThen, on {len(exam)} other worlds you have not seen, only {ok} passed. Make the method "
                        "more general: think about which tabs belong to the site, other windows, and windows with only that site.")
        if not best:
            record["status"] = "not_learned"
            log("verify", "not promoted", "no method passed every held-out world within the budget; the evidence above is kept")
            if teaching:
                teacher.mark_outcome(cand["id"], "not_enough_yet")
            return _finish(record, folder, learner)
        if _sha(inspect.getsource(skillcontracts)) != judge_sha:
            record["status"] = "judge_changed"
            log("verify", "not promoted", "the grader's code changed during this cycle, so its verdicts are not comparable")
            return _finish(record, folder, learner)
        man = retain(cand, best, inv, record, learner)
        if teaching:
            teacher.mark_outcome(cand["id"], "led_to_learned")
        from core import learned               # owner decision 2026-11-12: a skill that passed its exam is approved and in use
        auto = learned.auto_approve("method", man["id"], cand["tool"], man["version"], man["evidence"])
        log("retain", "saved as a verified method; " + ("approved automatically on its exam (core/authz still decides every action)"
                                                        if auto else "NOT approved automatically: awaiting the owner"),
            {"id": man["id"], "version": man["version"], "source_sha": man["source_sha"], "library": str(_root()),
             "approved_by": "auto" if auto else "", "exam": (auto or {}).get("exam")})
        record["status"] = "learned"
        record["method"] = {"id": man["id"], "version": man["version"], "sha": man["source_sha"], "approved_by": "auto" if auto else ""}
        return _finish(record, folder, learner)
    except skilllab.Suspended as error:
        record["status"] = "suspended"
        kept = _save_resume(folder, cand, inv, source, feedback, untested + [f for f in locals().get("fresh", []) if f not in untested])
        log("practice", "suspended", {"why": str(error), "kept_for_next_time": kept})
        return _finish(record, folder, learner)
    except Exception as error:  # noqa: BLE001 - a crashed cycle is a RECORDED failure with its reason, never a lost night
        record["status"] = "error"
        record["cause"] = error_cause(error)
        record["error_type"] = type(error).__name__
        record["error"] = f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
        try:
            log("error", "the cycle stopped on an error", {"cause": record["cause"], "error": record["error"]})
        except Exception:  # noqa: BLE001
            pass
        return _finish(record, folder, learner)


def _finish(record, folder, learner):
    record["learner"] = {"calls": learner.calls, "seconds": round(learner.seconds, 1)}
    record["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (folder / "cycle.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    (folder / "learner-transcript.json").write_text(json.dumps(learner.transcript, indent=1), encoding="utf-8")
    record["folder"] = str(folder)
    return record


# ============================================================================================ 7 RETAIN

def retain(cand, best, inv, record, learner):
    from core import procedures, skillcontracts
    lib = procedures.Library(_root())
    judge_sha = _sha(inspect.getsource(skillcontracts))
    failures = sorted({d.split(":")[0] for s in record["stages"] if s["stage"] == "practice"
                       for r in (s["data"].get("rows") or []) for d in r.get("diff", [])})
    evidence = {"judge": judge_sha, "passed": sum(r["passed"] for r in best["exam"]), "total": len(best["exam"]),
                "tasks": [{"seed": r["seed"], "site": r["site"], "checks": r["checks"]} for r in best["exam"]]}
    return lib.register(
        f"pcmethod.{cand['tool']}", f"Learned method for {cand['tool']}", best["source"], triggers=[[cand["tool"]]],
        params={"site": {"type": "string", "enum": sorted(skillcontracts.SITE_HOSTS)}}, recovery={}, authored_by=f"local:{learner.model}",
        evidence=evidence, entry="run", scope=skillcontracts.goal(cand["tool"], {"site": "github"}),
        extra={"for_tool": cand["tool"], "language": "core.methodlang", "status": "verified",
               "learned_from": [e["said"] for e in cand["examples"]], "weakness": cand["id"], "design_round": best["round"],
               "dependencies": ["Windows UI Automation (pywinauto uia)", "Microsoft Edge tab strip"],
               "failure_conditions_seen_in_practice": failures, "research": [(n["title"], n["url"]) for n in inv["research"] if n.get("url")],
               "diagnosis": inv["diagnosis"], "verified_on": time.strftime("%Y-%m-%dT%H:%M:%S"),
               **(record.get("provenance") or {"provenance": "independent", "teacher_lessons": []})})


def learned_methods() -> list:
    from core import procedures
    lib = procedures.Library(_root())
    out = []
    for pid in lib.ids():
        try:
            got = lib.get(pid)
        except procedures.NotVerified:
            continue
        if got and got[0].get("for_tool"):
            out.append(got[0])
    return out


# ============================================================================================ review gate + 8 INTEGRATE

def _approvals_file() -> Path:
    return _root() / "approvals.json"


def approve(pid, version=None, by="owner", exam=None) -> dict:
    """Binds the approval to the exact code hash; any change needs a new approval. by="owner" (scripts/acquire.py approve) or
    by="auto" with the exam evidence it was granted on (core/learned.auto_approve, owner decision 2026-11-12)."""
    from core import procedures
    man, src = procedures.Library(_root()).get(pid, version)
    rows = json.loads(_approvals_file().read_text(encoding="utf-8")) if _approvals_file().exists() else {}
    rows[pid] = {"version": man["version"], "source_sha": man["source_sha"], "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 **({"exam": exam} if exam else {})}
    _approvals_file().write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows[pid]


def revoke(pid) -> None:
    rows = json.loads(_approvals_file().read_text(encoding="utf-8")) if _approvals_file().exists() else {}
    rows.pop(pid, None)
    _approvals_file().write_text(json.dumps(rows, indent=1), encoding="utf-8")


def approved(man) -> bool:
    rows = json.loads(_approvals_file().read_text(encoding="utf-8")) if _approvals_file().exists() else {}
    row = rows.get(man["id"]) or {}
    return row.get("source_sha") == man["source_sha"] and row.get("version") == man["version"]


def production_ok(man) -> bool:
    """A desktop kind with a production check is usable only when its manifest carries a PASSED check for this exact source."""
    from core import skillcontracts
    if man.get("for_tool") not in skillcontracts.PRODUCTION_KINDS:
        return True
    pc = man.get("production_check") or {}
    return int(pc.get("total") or 0) > 0 and pc.get("passed") == pc.get("total") and pc.get("source_sha") == man.get("source_sha")


def candidates_for(tool) -> list:
    """Extra methods for core/pcmethods: only verified, owner-approved learned methods for exactly this step."""
    if os.getenv("JARVIS_LEARNED_METHODS", "on").lower() == "off":
        return []
    try:
        return ["learned:" + m["id"] for m in learned_methods() if m.get("for_tool") == tool and approved(m) and production_ok(m)]
    except Exception:  # noqa: BLE001 - a broken store never breaks the built-in methods
        return []


def _tab_names():
    from pywinauto import Desktop
    names, windows = [], 0
    for win in Desktop(backend="uia").windows():
        title = win.window_text() or ""
        if "Microsoft" not in title or "Edge" not in title.replace("​", ""):
            continue
        windows += 1
        for t in win.descendants(control_type="TabItem"):
            if any("close" in (c.window_text() or "").casefold() for c in t.children()):
                names.append(t.window_text())
    return names, windows


def run_learned(method, step) -> dict:
    """Run an approved learned method on THIS desktop, then check the result independently of what the method says."""
    from core import methodlang, procedures, skillcontracts
    pid = method.split(":", 1)[1]
    man, src = procedures.Library(_root()).get(pid)
    if not approved(man):
        raise RuntimeError("That learned method is not approved.")
    procedures.Library.check_params(man, step["arguments"])
    if step["tool"] in skillcontracts.PRODUCTION_KINDS:      # a desktop kind runs only inside the host's safety wrapper
        if not production_ok(man):
            raise RuntimeError("That learned method has no passed production check.")
        from core import learnedclose
        return learnedclose.run_learned(pid, man, src, step)
    before, wb = _tab_names()
    result = methodlang.run(src, methodlang.UIA(), step["arguments"])
    time.sleep(.6)
    after, wa = _tab_names()
    check = skillcontracts.production_check(step["tool"], step["arguments"], before, after, wb, wa, result)
    from core import independence
    procedures.Library(_root()).log(id=pid, version=man["version"], tool=step["tool"], args=step["arguments"], passed=check["passed"],
                                    checks=check["checks"], error=result.get("error", ""), source=independence.asker(),
                                    teacher=False, outside_calls=0)
    independence.log_use(pid, "method", check["passed"], tool=step["tool"])    # ran from disk: no teacher, no outside call
    if not check["passed"]:
        if not result.get("ok"):
            raise RuntimeError("My learned method could not do it either; nothing else was changed.")
        return {"verified": False, "detail": "My learned method ran, but I could not confirm the tab closed."}
    site = skillcontracts._SHOWN[step["arguments"]["site"]]
    n = len(check["closed"])
    return {"verified": True, "detail": f"Closed the {site} tab{'s' if n > 1 else ''}.", "learned_method": f"{pid} v{man['version']}"}


# ============================================================================================ 9 RETEST (inside the lab)

def lab_jarvis_turn(text) -> dict:
    """In a fresh lab process: the owner's sentence through JARVIS's real PC path (understanding -> plan -> methods -> verification)."""
    if os.getenv("JARVIS_SKILL_LAB") != "1":
        raise RuntimeError("lab only")
    from core import pcfast
    reply = pcfast.handle(text, [])
    uses = procedures_uses()
    return {"ok": True, "message": str(reply)[:400], "learned_uses": uses[-1:] if uses else [], "error": ""}


_ROUTING = ("JARVIS_GATEWAY", "JARVIS_ROLE", "JARVIS_LOCAL_MODEL", "JARVIS_LOCAL_URL", "JARVIS_LOCAL_FAST_PROVIDER", "JARVIS_LOCAL_FAST_MODEL",
            "JARVIS_LOCAL_ADVANCED_MODEL", "JARVIS_OLLAMA_URL", "JARVIS_LOCAL_ONLY", "JARVIS_INTELLIGENCE")


def retest(said, env_file=None, seeds=RETEST_SEEDS) -> dict:
    """Stage 9: a FRESH process (the lab child: nothing from the learning run in memory) loads the method from disk and handles the
    owner's own sentence through JARVIS's real PC path, on worlds no stage has used: a replay of the real miss (the site's tab open
    but not in front) plus fresh variations. The host judge reads DevTools ground truth as before."""
    from core import skillcontracts, skilllab
    env = {"JARVIS_LEARNED_DIR": str(_root())}
    if env_file and Path(env_file).exists():           # only non-secret routing settings: never tokens or keys
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in _ROUTING:
                env[k.strip()] = v.strip().strip('"')
    site = "github"
    replay = {"kind": "close_site_tab", "seed": "replay", "params": {"site": site},
              "windows": [{"tabs": [{"host": "github.com", "title": "alex/jarvis · GitHub", "body": ""},
                                    {"host": "www.youtube.com", "title": "Lo-fi beats to code to - YouTube", "body": ""}], "active": 1}]}
    worlds = [replay] + [w for w in (skillcontracts.generate(s) for s in range(seeds[0], seeds[0] + 400)) if w["params"]["site"] == site][:len(seeds)]
    out = skilllab.run_job([{"world": w, "action": {"type": "jarvis", "text": said}} for w in worlds], seconds=900,
                           guard=os.getenv("JARVIS_LAB_GUARD", "on") != "off", env=env, label="retest")
    rows = [{"seed": w["seed"], "world": _world_text(w), "reply": (r.get("result") or {}).get("message", ""),
             "learned_used": (r.get("result") or {}).get("learned_uses", []), "passed": bool((r.get("judge") or {}).get("passed")),
             "diff": (r.get("judge") or {}).get("diff", []), "lab_error": r.get("lab_error", "")[-600:]}
            for w, r in zip(worlds, out["results"])]
    return {"status": out["status"], "rows": rows, "dir": out["dir"], "env": sorted(env)}


def procedures_uses():
    from core import procedures
    return procedures.Library(_root()).uses()


# ============================================================================================ scheduling (laptop + PC)

def idle_ready() -> str:
    """'' when the PC may practise now; otherwise why not."""
    from core import missionworker
    if missionworker.active():
        return "a mission is running"          # Mark VI C10: practice yields to the owner's errands
    from core import skilllab
    why = skilllab.available()
    if why:
        return why
    from core.scheduler import idle_seconds
    if idle_seconds() < IDLE_SECONDS:
        return "the owner is using the PC"
    game = running_game()
    if game:
        return f"a game is running ({game})"
    if gpu_busy():
        return "the graphics card is busy with something else"
    return ""


GAME_FOLDERS = ("/steamapps/common/", "/epic games/", "/riot games/", "/xboxgames/", "/ea games/",
                "/ubisoft game launcher/games/", "/gog galaxy/games/", "/battle.net/")
# The stores' own background programs live inside those folders too. The Epic launcher, sitting in the tray, keeps five
# EOSOverlayRenderer processes under "Epic Online Services": JARVIS took that for a game all morning on 2026-11-09 (GPU at 6 %)
# and kept unloading his big model "so the game gets the graphics memory".
NOT_GAME_FOLDERS = ("/epic games/launcher/", "/epic games/epic online services/", "/steam/bin/", "/steamapps/common/steamworks shared/",
                    "/battle.net/battle.net", "/riot games/riot client/",
                    "/epic games/ue_")   # the Unreal ENGINE (editor, build tool, its dotnet): live 2026-11-09 a game build's dotnet.exe
                                         # counted as a game, and so would the owner working in the editor
NOT_GAME_NAMES = ("crash", "report", "helper", "launcher", "updater", "setup", "overlay", "webhelper", "service")
GPU_BUSY_PERCENT = 40


def running_game() -> str:
    """A game process, found by itself (the watcher runs outside JARVIS, whose game-mode flag lives in JARVIS's own process).
    Known games by name, plus anything started from a game library folder (Steam, Epic, Riot, Xbox, EA, Ubisoft, GOG, Battle.net)."""
    try:
        import psutil
        from tools import gaming
    except Exception:  # noqa: BLE001
        return ""
    for p in psutil.process_iter(["name", "exe"]):
        name = (p.info.get("name") or "").lower()
        if name in gaming.GAMES:
            return gaming.GAMES[name]
        exe = (p.info.get("exe") or "").lower().replace("\\", "/")
        if any(f in exe for f in GAME_FOLDERS) and not any(f in exe for f in NOT_GAME_FOLDERS) \
                and not any(x in name for x in NOT_GAME_NAMES):
            return p.info.get("name") or "a game"
    return ""


def gpu_busy(samples=3) -> bool:
    """Something is working the graphics card hard (a game we do not know, a render, a video encode): do not add to it."""
    import subprocess
    try:
        for i in range(samples):
            out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode != 0 or max(int(x) for x in out.stdout.split() or ["0"]) < GPU_BUSY_PERCENT:
                return False
            time.sleep(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False                                     # no NVIDIA tools: the game check and input checks still apply
    return True


def paused() -> dict:
    """Weaknesses the OLD click-script learner must not practise any more: {weakness id: why}. Owner 2026-11-08: "Stop
    repeatedly generating near-identical methods" -> Close GitHub moved to the compositional learner (core/compose)."""
    f = Path(os.getenv("JARVIS_PAUSED_FILE", str(storage.DATA / "learning" / "ui-script-paused.json")))
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


STALE_RUNNING_SECONDS = CYCLE_SECONDS + 3600


def close_stale(now=None) -> int:
    """A cycle record still at "running" long after any cycle could be alive belongs to a process that died: it is closed as
    an error with that reason, so no record is ever left at "running" (and the teacher's stall count can see it)."""
    now, closed = time.time() if now is None else now, 0
    for f in _evidence_root().glob("*/*/cycle.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if rec.get("status") != "running" or now - f.stat().st_mtime < STALE_RUNNING_SECONDS:
                continue
            last = (rec.get("stages") or [{}])[-1].get("what", "")
            # Dying at the first learner call (nothing after "existing skills") is the model-server outage of 2026-11-11/12.
            rec.update(status="error", cause="model_unavailable" if last in ("", "weakness chosen", "existing skills") else "crash",
                       error=f"the process stopped after {last or 'starting'!r} and never finished this cycle",
                       finished=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(f.stat().st_mtime)))
            f.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
            closed += 1
        except (OSError, ValueError):
            continue
    return closed


MAX_LAB_FAULT_RETRIES = 3          # consecutive lab-fault cycles after which a weakness waits its usual day like any other


def _may_retry_lab_fault(cycles) -> bool:
    """The newest cycle was a lab fault AND fewer than MAX_LAB_FAULT_RETRIES lab faults came in a row: try again at once.
    A lab that stays broken therefore stops being re-entered by every idle slot (and by any caller, not only the watcher)."""
    streak = 0
    for f in reversed(cycles):
        if not _lab_fault(f):
            break
        streak += 1
    return 0 < streak < MAX_LAB_FAULT_RETRIES


def _lab_fault(cycle_file) -> bool:
    """The last cycle ended because the lab itself was broken: nothing was learned or failed, so the next slot may try again."""
    try:
        rec = json.loads(Path(cycle_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return rec.get("status") == "error" and rec.get("cause") == "lab"


def practice_once(ready_check=None, learn_one=None, now=None) -> dict | None:
    """The PC's idle-time entry point: the highest-priority ready weakness, one bounded cycle. None when there is nothing to do.
    When nothing is ready to learn, the slot is spent on re-testing one learned skill (retest_learned)."""
    if idle_ready():
        return None
    close_stale(now)
    why = (ready_check or learner_ready)()
    if why:                                              # an outage is waited out: no cycle, no failure counted, no crash
        print(f"[acquire] no practice now: {why}")
        return None
    clock = time.time() if now is None else now
    skip = paused()
    for cand in (c for c in discover() if c["status"] == "ready" and c["id"] not in skip):
        recent = sorted((_evidence_root() / cand["id"]).glob("*/cycle.json"))
        if recent and clock - recent[-1].stat().st_mtime < 20 * 3600 and not _may_retry_lab_fault(recent):
            continue                                     # at most one attempt per weakness per day: move on to the next one
        from core import labpractice
        if labpractice.resting(cand, clock):
            continue                                     # passed its lab exam this week: the slot goes to the next weakness
        return (learn_one or learn)(cand)
    try:                                                 # nothing to learn tonight: the slot re-tests one learned skill
        checked = retest_learned(now=clock) if learn_one is None else None
        if checked:
            print(f"[acquire] retest of {checked['id']}: " + ("demoted, " if checked["demoted"] else "") + f"{checked['status']} {checked['detail']}")
    except Exception as error:  # noqa: BLE001
        print(f"[acquire] retest skipped: {type(error).__name__}")
    return None


def summary(record) -> str:
    st = record.get("status")
    if st == "learned":
        from core import learned
        how = "approved on its exam and in use" if (record.get("method") or record.get("skill") or {}).get("approved_by") == "auto" else "awaiting your review"
        return f"Learned how to {learned.what(record['weakness']['tool'])} ({how})."
    if st == "practised":
        from core import learned
        from core.words import plural
        p = record.get("practice") or {}
        return (f"Practised how to {learned.what(record['weakness']['tool'])}: my method passed all "
                f"{plural(int(p.get('total') or 0), 'lab exam task')} (lab only, not in use yet).")
    if st == "error":
        return f"Practised {record['weakness']['tool']}: stopped on an error ({record.get('error', '')[:160]})."
    return f"Practised {record['weakness']['tool']}: {st}."


# ============================================================================================ retest later, demote on failure

RETEST_EVERY = 7 * 86400


def _retests_file() -> Path:
    return storage.DATA / "learning" / "skill-retests.json"


def _active() -> list:
    """Every learned thing that may run for the owner now: [{"id", "kind", "tool", "version", "source_sha", "said"}]."""
    from core import skillspec
    out = []
    for m in learned_methods():
        if approved(m):
            out.append({"id": m["id"], "kind": "method", "tool": m["for_tool"], "version": m["version"], "source_sha": m["source_sha"],
                        "said": (m.get("learned_from") or [""])[0]})
    lib = skillspec.library()
    for sid in lib.ids() if lib.root.exists() else []:
        got = skillspec.approved_skill(sid) if sid.startswith("skill.") else None
        if got:
            out.append({"id": sid, "kind": "skill", "tool": sid[len("skill."):], "version": got[0]["version"],
                        "source_sha": got[0]["source_sha"], "said": ""})
    return out


def _lab_retest(item) -> dict:
    """The real retest: fresh lab worlds on the HIDDEN lab desktop, judged by the host. -> {"status", "passed", "detail"}."""
    from core import skillcontracts, skilllab, skillspec
    if item["kind"] == "method" and item["tool"] in skillcontracts.PRODUCTION_KINDS:
        from core import labpractice
        out = labpractice.retest_desktop(item)
        rows = out["rows"]
    elif item["kind"] == "method":
        out = retest(item["said"] or f"close {item['tool']}")
        rows = out["rows"]
    else:
        worlds = [skillcontracts.generate(s, item["tool"]) for s in RETEST_SEEDS]
        out = skilllab.run_job([{"world": w, "action": {"type": "skill", "skill": item["id"], "version": item["version"],
                                                       "store": str(skillspec._root())}} for w in worlds],
                               seconds=900, guard=os.getenv("JARVIS_LAB_GUARD", "on") != "off", label="retest")
        rows = [{"passed": bool((r.get("judge") or {}).get("passed")), "lab_error": r.get("lab_error", "")} for r in out["results"]]
        rows += [{"passed": False, "lab_error": "no result"}] * (len(worlds) - len(rows))
    lab_broke = out["status"] != "done" or any(r.get("lab_error") for r in rows)
    ok = sum(1 for r in rows if r["passed"])
    return {"status": "lab_unavailable" if lab_broke else "done", "passed": bool(rows) and ok == len(rows), "detail": f"{ok}/{len(rows)} fresh worlds"}


def demote(item, why) -> dict:
    """A learned skill that fails its retest is taken out of service (its approval is withdrawn), not kept: the step falls
    back to the native method, and discover() offers the weakness for learning again. The code and evidence stay on disk."""
    from core import skillspec
    if item["kind"] == "method":
        revoke(item["id"])
    else:
        skillspec.revoke(item["id"])
    rows = demoted()
    rows[item["id"]] = {"version": item["version"], "source_sha": item["source_sha"], "why": why, "at": time.time()}
    _demoted_file().parent.mkdir(parents=True, exist_ok=True)
    _demoted_file().write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows[item["id"]]


def retest_learned(run=None, now=None, every=RETEST_EVERY) -> dict | None:
    """Re-test ONE active learned skill that has not been checked for `every` seconds. Passed: it stays. Failed: demoted.
    A lab that could not run is not a verdict. -> the retest record, or None when nothing is due."""
    now = time.time() if now is None else now
    try:
        seen = json.loads(_retests_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    due = [i for i in _active() if now - float((seen.get(i["id"]) or {}).get("at") or 0) >= every
           or (seen.get(i["id"]) or {}).get("source_sha") != i["source_sha"]]
    if not due:
        return None
    item = min(due, key=lambda i: float((seen.get(i["id"]) or {}).get("at") or 0))
    try:
        out = (run or _lab_retest)(item)
    except Exception as error:  # noqa: BLE001 - a broken lab is not the skill's failure
        out = {"status": "lab_unavailable", "passed": False, "detail": f"{type(error).__name__}"}
    rec = {"id": item["id"], "at": now, "source_sha": item["source_sha"], "version": item["version"], "status": out.get("status"),
           "passed": bool(out.get("passed")), "detail": str(out.get("detail", ""))[:200], "demoted": False}
    if out.get("status") != "done":
        return rec                                           # not recorded: it is tried again next time
    if not rec["passed"]:
        demote(item, f"failed its retest ({rec['detail']})")
        rec["demoted"] = True
    seen[item["id"]] = rec
    _retests_file().parent.mkdir(parents=True, exist_ok=True)
    _retests_file().write_text(json.dumps(seen, indent=1), encoding="utf-8")
    return rec
