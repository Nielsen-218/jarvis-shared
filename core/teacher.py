"""When practice stalls, Jarvis asks a stronger teacher for GUIDANCE — never for the answer (owner 2026-11-08).

  * Stall: JARVIS_TEACHER_AFTER (default 3) finished practice cycles on one weakness ended not_learned since the last lesson.
  * Local first: the first lessons come from a different local model (JARVIS_TEACHER_LOCAL, default qwen3:14b, a reasoning
    model of another family than the learner). Only when local lessons are used up (JARVIS_TEACHER_LOCAL_MAX, default 2)
    and practice is still stuck does Jarvis ask the cloud teacher (JARVIS_TEACHER_MODEL, default claude-opus-5-5).
  * Before any cloud call: the owner's declared promotional credit (JARVIS_TEACHER_CREDIT_USD, ..._CREDIT_AS_OF and
    ..._CREDIT_EXPIRES, read off the Claude console — no API exposes the balance) must be set and unexpired; what the ledger
    shows as spent since AS_OF is subtracted; the monthly budget must not be used up; the key is checked with a free
    token count. Every lesson has a worst-case price, and the per-skill cap (JARVIS_TEACHER_SKILL_USD, default $2) is never
    crossed: a lesson that could cross it is not asked for.
  * Guidance only: the teacher diagnoses the failed assumption and gives hints, explanations and references. Its reply is
    filtered: fenced code and code-like lines are removed (and recorded), references outside a short list of trusted
    documentation sites are dropped. The teacher has no access to the grader, the sealed worlds or the method library;
    only practice-world failures (never the sealed exam's contents) are shown to it.
  * Provenance: any lesson on a weakness makes a method learned for it `teacher_assisted`, never `independent`.
  * Every lesson (prompt hash, raw reply, what was kept, what was removed, cost, outcome) is kept in
    data/learning/lessons/<weakness>/.
"""
import hashlib
import json
import os
import re
import time
from datetime import date
from pathlib import Path

LOCAL_TEACHER = os.getenv("JARVIS_TEACHER_LOCAL", "qwen3.5:9b")  # 2026-11-09: the one brain (the old default was deleted)
CLOUD_TEACHER = os.getenv("JARVIS_TEACHER_MODEL", "claude-opus-5-5")
MAX_TOKENS = 1500           # local teacher's answer budget
CLOUD_MAX_TOKENS = 6000     # the cloud model thinks before it answers: 1500 cut a real lesson off mid-sentence (2026-11-08)
TRUSTED = ("learn.microsoft.com", "docs.microsoft.com", "docs.python.org", "pywinauto.readthedocs.io", "www.chromium.org",
           "chromium.googlesource.com", "developer.chrome.com", "developer.mozilla.org", "www.w3.org", "w3c.github.io",
           "github.com")

SYSTEM = """You are a senior Windows UI-automation engineer TEACHING a junior (JARVIS) who must write a method himself.
JARVIS is stuck: several practice cycles failed. Your job is to find the ONE wrong assumption behind his failures and teach.
Rules you must follow:
- Do NOT write the method, any code, pseudo-code, or code-shaped steps. No code blocks. Explain in plain sentences.
- You may name the ui calls that exist (e.g. "ui.find", "ui.value") inside a sentence, as a pointer, never as code.
- Diagnose from the evidence: what did his method believe about the screen that is not true?
- Give at most 5 short hints, ordered from most to least important. Hints teach how to tell things apart; they do not
  dictate the program.
- References only from official documentation (Microsoft Learn, Chromium, MDN, W3C, Python docs, pywinauto docs).
Reply with JSON only: {"failed_assumption": str, "explanation": str, "hints": [str], "references": [{"title": str, "url": str}]}"""

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_CODE_LINE = re.compile(r"^\s*(def |class |for \S.*:\s*$|while .*:\s*$|if .*:\s*$|elif .*:|else:\s*$|return\b|import |from \w+ import|"
                        r"[A-Za-z_][\w\[\]'\".]*\s*(=|\+=|-=)\s*[^=])")
_CALLS = re.compile(r"\b(ui|params)\.\w+\(")


def _sha(text) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def stall_limit() -> int:
    return max(1, int(os.getenv("JARVIS_TEACHER_AFTER", "3")))


def local_max() -> int:
    return max(0, int(os.getenv("JARVIS_TEACHER_LOCAL_MAX", "2")))


def cloud_max() -> int:
    """How many PAID lessons one skill may get by itself. One: the lesson is stored and the stored lesson is what he works
    from afterwards. The owner's own command ("ask the cloud teacher now") is not counted against this."""
    return max(0, int(os.getenv("JARVIS_TEACHER_CLOUD_MAX", "1")))


def cloud_given(wid) -> int:
    """Paid lessons this skill has used up: every consultation that was SENT to the paid model, whatever came back (a
    lesson, an empty reply, or an error after sending: it may have been billed). A refusal at the gate sent nothing."""
    return sum(1 for l in lessons(wid) if l.get("tier") == "cloud" and l.get("status") != "refused"
               and (l.get("sent") or l.get("status") in ("given", "empty") or float(l.get("cost_usd", 0) or 0) > 0))


def skill_cap() -> float:
    return float(os.getenv("JARVIS_TEACHER_SKILL_USD", "2.00"))


def _evidence_root() -> Path:
    from core import acquire
    return acquire._evidence_root()


def lessons_dir(wid) -> Path:
    return Path(os.getenv("JARVIS_LESSONS_DIR", str(_evidence_root().parent / "lessons"))) / wid


def lessons(wid) -> list:
    out = []
    for f in sorted(lessons_dir(wid).glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def usable(wid) -> list:
    """Lessons that actually reached the learner (a refused or empty consultation teaches nothing)."""
    return [l for l in lessons(wid) if l.get("status") == "given"]


def spent_on(wid) -> float:
    return round(sum(float(l.get("cost_usd", 0) or 0) for l in lessons(wid)), 6)


def cycles(wid) -> list:
    """Finished practice cycles on this weakness, oldest first: [{"folder", "status", "best", "total", "started_at"}].
    Cycles the owner interrupted or an operator stopped are not failures and are left out."""
    rows = []
    for f in sorted((_evidence_root() / wid).glob("*/cycle.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        status = rec.get("status")
        if status == "error" and rec.get("cause") not in ("model_unavailable", "lab"):
            status = "not_learned"                       # a crashed cycle is a failed cycle; a model outage or a broken lab is nobody's failure
        if status not in ("not_learned", "learned"):
            continue
        scores = [(sum(1 for r in s["data"]["rows"] if r.get("passed")), len(s["data"]["rows"])) for s in rec.get("stages", [])
                  if s.get("stage") == "practice" and isinstance(s.get("data"), dict) and s["data"].get("rows")]
        best = max(scores, default=(0, 0))
        def _t(key, default):
            try:
                return time.mktime(time.strptime(rec.get(key, ""), "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                return default
        started = _t("started", f.stat().st_mtime)
        rows.append({"folder": str(f.parent), "status": status, "best": best[0], "total": best[1], "started_at": started,
                     "finished_at": _t("finished", started)})
    return rows


def stalled(wid) -> tuple:
    """-> (True, why) when the last stall_limit() finished cycles since the newest lesson all ended without learning."""
    since = max((l.get("at", 0) for l in usable(wid)), default=0)
    # A lesson is asked for at the START of a cycle, so the cycle that used it began before the lesson was stored:
    # a cycle counts as "since the lesson" when it FINISHED after it.
    after = [c for c in cycles(wid) if c["finished_at"] > since]
    failed = [c for c in after if c["status"] == "not_learned"]
    if any(c["status"] == "learned" for c in after):
        return False, "already learned"
    if len(failed) < stall_limit():
        return False, f"{len(failed)} of {stall_limit()} unlearned cycles since the last lesson"
    scores = ", ".join(f"{c['best']}/{c['total']}" for c in failed[-stall_limit():])
    return True, f"{len(failed)} cycles in a row ended without learning (best practice scores {scores})"


# ------------------------------------------------------------------------------------------------ the cloud's gate

def credit() -> dict:
    """The owner's declared promotional credit, minus the ledger's spending since he read it. No API reports the balance."""
    from core import usage
    raw, as_of, expires = (os.getenv("JARVIS_TEACHER_CREDIT_USD", "").strip(), os.getenv("JARVIS_TEACHER_CREDIT_AS_OF", "").strip(),
                           os.getenv("JARVIS_TEACHER_CREDIT_EXPIRES", "").strip())
    out = {"declared": raw, "as_of": as_of, "expires": expires, "remaining": 0.0, "ok": False, "why": ""}
    if not (raw and as_of and expires):
        out["why"] = ("the promotional credit is not declared: set JARVIS_TEACHER_CREDIT_USD, JARVIS_TEACHER_CREDIT_AS_OF and "
                      "JARVIS_TEACHER_CREDIT_EXPIRES (YYYY-MM-DD) from the Claude console's billing page")
        return out
    try:
        amount, start, end = float(raw), date.fromisoformat(as_of), date.fromisoformat(expires)
    except ValueError:
        out["why"] = "the declared credit is not a number or a date is not YYYY-MM-DD"
        return out
    if date.today() > end:
        out["why"] = f"the promotional credit expired on {expires}"
        return out
    spent = usage.spent_since(start.isoformat())
    out.update(spent_since=spent, remaining=round(amount - spent, 4))
    if out["remaining"] <= 0:
        out["why"] = f"the declared credit (${amount:.2f} on {as_of}) is used up: ${spent:.2f} spent since"
        return out
    if usage.over_budget():
        out["why"] = f"Jarvis's monthly budget (${usage.budget():.2f}) is used up"
        return out
    out["ok"] = True
    return out


def _price(model):
    from core import usage
    return usage.PRICES.get(model, usage.PRICES["claude-opus-5-5"])


def _client():
    import anthropic
    return anthropic.Anthropic(max_retries=0, timeout=120)


def cloud_gate(wid, prompt, client=None) -> dict:
    """-> {"ok", "why", "estimate", "credit"}: a worst-case price for this lesson and every check it must pass first."""
    c = credit()
    if not c["ok"]:
        return {"ok": False, "why": c["why"], "estimate": 0.0, "credit": c}
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"ok": False, "why": "no ANTHROPIC_API_KEY", "estimate": 0.0, "credit": c}
    client = client or _client()
    try:          # free: proves the key works and gives the exact input size
        tokens = client.messages.count_tokens(model=CLOUD_TEACHER, system=SYSTEM,
                                              messages=[{"role": "user", "content": prompt}]).input_tokens
    except Exception as error:  # noqa: BLE001 - any failure here means: do not spend
        return {"ok": False, "why": f"the API key check failed ({type(error).__name__})", "estimate": 0.0, "credit": c}
    p_in, p_out = _price(CLOUD_TEACHER)[:2]
    estimate = round((tokens * p_in + CLOUD_MAX_TOKENS * p_out) / 1e6, 4)
    left = round(skill_cap() - spent_on(wid), 4)
    if estimate > left:
        return {"ok": False, "why": f"the per-skill teacher budget is exhausted (${spent_on(wid):.2f} of ${skill_cap():.2f} spent; "
                                    f"one more lesson could cost ${estimate:.2f})", "estimate": estimate, "credit": c}
    if estimate > c["remaining"]:
        return {"ok": False, "why": f"one lesson could cost ${estimate:.2f}, more than the ${c['remaining']:.2f} credit left",
                "estimate": estimate, "credit": c}
    return {"ok": True, "why": "", "estimate": estimate, "credit": c, "input_tokens": tokens}


# ------------------------------------------------------------------------------------------------ guidance only

def _strip_code(text) -> tuple:
    removed = []
    text = str(text or "")
    for m in _FENCE.finditer(text):
        removed.append(m.group(0)[:400])
    text = _FENCE.sub(" ", text)
    kept = []
    for line in text.splitlines():
        if _CODE_LINE.match(line) or len(_CALLS.findall(line)) >= 2 or line.count("(") >= 3 and line.rstrip().endswith((")", ":")):
            removed.append(line.strip()[:300])
        else:
            kept.append(line)
    return " ".join(" ".join(kept).split()), removed


def clean(reply: str) -> tuple:
    """-> (lesson, removed): the teacher's diagnosis, hints and trusted references, with anything code-shaped taken out."""
    removed = []
    try:
        m = re.search(r"\{.*\}", reply or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    if not data:                 # a reply cut off mid-way: keep every field that was completed, nothing half-written
        data = {}
        for key in ("failed_assumption", "explanation"):
            f = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % key, reply or "", re.S)
            if f:
                data[key] = json.loads('"' + f.group(1) + '"')
        if data:
            removed.append("(the reply was cut off: only its completed fields were kept)")
    if not isinstance(data, dict):
        data = {}
    lesson = {}
    for key in ("failed_assumption", "explanation"):
        text, gone = _strip_code(data.get(key, ""))
        lesson[key], removed = text[:4000], removed + gone
    hints = []
    for h in (data.get("hints") or [])[:5]:
        text, gone = _strip_code(h)
        removed += gone
        if text and not gone:          # a hint that carried code is dropped whole: half a program is still a program
            hints.append(text[:400])
        elif gone:
            removed.append("(hint dropped: it carried code)")
    lesson["hints"] = hints
    refs = []
    for r in (data.get("references") or [])[:6]:
        url = str((r or {}).get("url", "")) if isinstance(r, dict) else ""
        host = re.sub(r"^https://([^/]+).*$", r"\1", url).casefold() if url.startswith("https://") else ""
        if host in TRUSTED:
            refs.append({"title": str(r.get("title", ""))[:120], "url": url[:300]})
        elif url:
            removed.append(f"(reference dropped, not a trusted documentation site: {url[:120]})")
    lesson["references"] = refs
    return lesson, removed


def brief(cand, folders) -> str:
    """What the teacher sees: the goal, the method language, the best method so far and its PRACTICE failures with the
    learner's own analyses. Never the sealed exam's worlds."""
    from core import acquire, methodlang, skillcontracts
    parts = [f"JARVIS is learning the PC step `{cand['tool']}` for the request {cand['examples'][0]['said']!r}.",
             "Goal: " + skillcontracts.goal(cand["tool"], {"site": "github"}).replace("GitHub", "<the site>"),
             "The language his method must be written in:", methodlang.API_DOC]
    best, analyses, failures = "", [], []
    for folder in folders[-3:]:
        try:
            rec = json.loads((Path(folder) / "cycle.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        top = None
        for s in rec.get("stages", []):
            d = s.get("data")
            if s.get("stage") == "improve" and "failure analysis" in s.get("what", "") and isinstance(d, str):
                analyses.append(d[:700])
            if s.get("stage") == "practice" and isinstance(d, dict) and d.get("rows"):
                n = sum(1 for r in d["rows"] if r.get("passed"))
                if not top or n >= top[0]:
                    top = (n, d)
        if top:
            n, d = top
            sealed = set(acquire.HELD_OUT_SEEDS)
            for r in d["rows"]:
                if not r.get("passed") and r.get("seed") not in sealed:
                    failures.append(f"site={r['site']}: {'; '.join(r.get('diff', []))[:300]} {r.get('error', '')[:150]}")
            for m in Path(folder).glob("method-r*.py"):
                src = m.read_text(encoding="utf-8")
                if acquire._sha(src) == d.get("kept_sha"):
                    best = src
    if best:
        parts += ["His best method so far:", best[:4000]]
    if failures:
        parts += ["How it failed in practice (the independent judge compares real browser state before and after):"]
        parts += [" - " + f for f in list(dict.fromkeys(failures))[-14:]]
    if analyses:
        parts += ["His own explanations of the failures (they did not fix it):"] + [" - " + a for a in analyses[-4:]]
    parts += ["Teach him: what is the wrong assumption, and how can a method tell the right tabs apart reliably?"]
    return "\n".join(parts)


def _ask_local(prompt) -> tuple:
    from core import acquire
    # The local teacher is the same brain as the learner, so it gets what the learner's drafts do not: time to THINK
    # before answering and a larger answer budget. If thinking leaves no answer, it is asked once more without it.
    think = os.getenv("JARVIS_TEACHER_LOCAL_THINK", "on").lower() != "off"
    learner = acquire.Learner(model=LOCAL_TEACHER, max_calls=2, system=SYSTEM, think=think)
    reply = learner(prompt, temperature=0.2, fmt="json", num_predict=MAX_TOKENS)
    if think and not reply.strip():
        learner.think = False
        reply = learner(prompt, temperature=0.2, fmt="json", num_predict=MAX_TOKENS)
    return reply, 0.0, {"seconds": round(learner.seconds, 1), "thinking": think, "calls": learner.calls}


def _ask_cloud(prompt, client=None) -> tuple:
    from core import usage
    client = client or _client()
    resp = client.messages.create(model=CLOUD_TEACHER, max_tokens=CLOUD_MAX_TOKENS, system=SYSTEM,
                                  messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in resp.content)
    cost = usage.record(CLOUD_TEACHER, resp.usage, component="teacher")
    return text, cost, {"input_tokens": getattr(resp.usage, "input_tokens", 0), "output_tokens": getattr(resp.usage, "output_tokens", 0)}


def next_tier(wid) -> str:
    given = usable(wid)
    return "local" if sum(1 for l in given if l.get("tier") == "local") < local_max() else "cloud"


def can_help(wid) -> tuple:
    """-> (True, tier) while another lesson is possible; (False, why) once local lessons are used and the cloud is closed."""
    if next_tier(wid) == "local":
        return True, "local"
    if cloud_given(wid) >= cloud_max():
        return False, "the paid teacher already gave its one lesson for this skill; he works from the stored lesson now"
    c = credit()
    if not c["ok"]:
        return False, c["why"]
    if spent_on(wid) >= skill_cap() - 0.01:
        return False, f"the per-skill teacher budget is exhausted (${spent_on(wid):.2f} of ${skill_cap():.2f})"
    return True, "cloud"


def consult(cand, ask_local=None, ask_cloud=None, client=None, tier=None, requested_by="stall", prompt=None) -> dict:
    """Ask for one lesson (local first, unless `tier` is given: the owner can order a cloud lesson now — it still passes the
    same credit and budget gate). `prompt` replaces the default brief (core/compose builds a class-focused one; it must
    still contain practice evidence only). -> the lesson record (status given | refused | empty | failed), also kept on disk."""
    wid = cand["id"]
    tier = tier if tier in ("local", "cloud") else next_tier(wid)
    folders = [c["folder"] for c in cycles(wid)]
    prompt = prompt or brief(cand, folders)
    rec = {"id": time.strftime("%Y%m%d-%H%M%S"), "weakness": wid, "tier": tier, "at": time.time(), "prompt_sha": _sha(prompt),
           "prompt_chars": len(prompt), "evidence": folders[-3:], "cost_usd": 0.0, "status": "failed", "outcome": "pending",
           "requested_by": requested_by}
    try:
        if tier == "local":
            rec["model"] = LOCAL_TEACHER
            reply, cost, meta = (ask_local or _ask_local)(prompt)
        else:
            rec["model"] = CLOUD_TEACHER
            if requested_by != "owner command" and cloud_given(wid) >= cloud_max():
                rec.update(status="refused", why="the paid teacher already gave its one lesson for this skill; "
                                                 "he works from the stored lesson now")
                return _keep(rec)
            gate = cloud_gate(wid, prompt, client=client)
            rec["gate"] = {k: gate[k] for k in ("ok", "why", "estimate")} | {"credit_remaining": gate["credit"].get("remaining")}
            if not gate["ok"]:
                rec.update(status="refused", why=gate["why"])
                return _keep(rec)
            rec["sent"] = True                           # from here on the one paid lesson is used, whatever comes back
            reply, cost, meta = (ask_cloud or (lambda p: _ask_cloud(p, client)))(prompt)
        rec.update(cost_usd=round(cost, 6), meta=meta, reply=reply[:8000])
        lesson, removed = clean(reply)
        rec.update(lesson=lesson, removed=removed)
        rec["status"] = "given" if lesson.get("failed_assumption") or lesson.get("hints") else "empty"
    except Exception as error:  # noqa: BLE001 - a failed consultation is evidence, never a crash of the cycle
        rec.update(status="failed", why=f"{type(error).__name__}: {error}"[:300])
        if type(error).__name__ == "Suspended":      # the owner came back: the cycle stops as usual (the record is kept)
            _keep(rec)
            raise
    return _keep(rec)


def _keep(rec) -> dict:
    d = lessons_dir(rec["weakness"])
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{rec['id']}-{rec['tier']}.json"
    n = 1
    while f.exists():
        n += 1
        f = d / f"{rec['id']}-{n}-{rec['tier']}.json"
    rec["file"] = str(f)
    f.write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return rec


def mark_outcome(wid, outcome) -> None:
    """After a cycle that used lessons: 'led_to_learned' or 'not_enough_yet' (kept beside each lesson, never deleted)."""
    for f in lessons_dir(wid).glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("status") == "given" and rec.get("outcome") in ("pending", "not_enough_yet"):
            rec["outcome"] = outcome
            rec.setdefault("outcomes", []).append({"at": time.time(), "outcome": outcome})
            f.write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def guidance(wid) -> str:
    """Every lesson given on this weakness, as the learner reads it."""
    out = []
    for l in usable(wid):
        les = l.get("lesson", {})
        out.append(f"A TEACHER'S GUIDANCE ({l['tier']} teacher {l.get('model')}; hints, not code — you still write the method):\n"
                   f"  Your wrong assumption: {les.get('failed_assumption', '')}\n  Why: {les.get('explanation', '')}\n" +
                   "".join(f"  Hint: {h}\n" for h in les.get("hints", [])) +
                   "".join(f"  Reference: {r['title']} {r['url']}\n" for r in les.get("references", [])))
    return "\n".join(out)


def provenance(wid) -> dict:
    """How a method for this weakness came to be. Any lesson at all makes it teacher-assisted."""
    given = usable(wid)
    if not given:
        return {"provenance": "independent", "teacher_lessons": []}
    return {"provenance": "teacher_assisted",
            "teacher_lessons": [{"id": l["id"], "tier": l["tier"], "model": l.get("model"), "cost_usd": l.get("cost_usd", 0)}
                                for l in given]}


def escalate(cand, log=None) -> dict | None:
    """Called at the start of a cycle: when practice has stalled, get one lesson (local first, then the gated cloud)."""
    st, why = stalled(cand["id"])
    if not st:
        return None
    rec = consult(cand)
    if log:
        log("teach", f"stuck ({why}): asked the {rec['tier']} teacher {rec.get('model')} for guidance -> {rec['status']}",
            {k: rec.get(k) for k in ("id", "tier", "model", "status", "why", "cost_usd", "lesson", "removed", "file")},
            autonomous=False, assist=f"teacher:{rec.get('model')}")
    return rec
