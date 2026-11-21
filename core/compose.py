"""COMPOSITIONAL skill learning: JARVIS plans with developer-built primitives instead of writing click scripts from scratch.

    observe -> plan (skill spec) -> compose (procedure) -> practice -> classify failures -> repair / ask a teacher / switch
    -> sealed exam (the same 12 worlds, same judge) -> unseen variations -> retain -> fresh-process persistence test

Who does what:
  * DEVELOPERS built the primitives (core/capabilities: the browser tab API, UI Automation, util), the lab, the judge, the
    sealed worlds, the checker, the budget and this loop. None of it encodes the answer to a skill.
  * JARVIS (the local learner model) writes the skill spec (plan, preconditions, expected results, recovery) and the
    procedure that composes the primitives. Nobody writes the task-specific solution for him.
  * A TEACHER is asked only when the dominant failure class is one guidance can fix (planning, perception), the strategy has
    stopped improving, and the lesson passes core/teacher's credit gate and per-skill cap. Lessons for this learner are kept
    under "<weakness>-compose", so the old UI-script lessons do not silently make it "teacher-assisted".
  * The learning budget (core/learnbudget) bounds every strategy; a stalled strategy is preserved and abandoned.

Evidence: data/learning/compose/<weakness>/<time>/ (cycle.json, plan-*.json, proc-*.py, every practice row with its class).
"""
import ast
import json
import os
import time
from pathlib import Path

from core import storage

STRATEGIES = ("compose", "compose-minimal")
CANDIDATES = (0.3, 0.8)
FIXED_PRACTICE = tuple(range(1001, 1007))           # the same 6 worlds every round, so scores are comparable across rounds
FRESH_PER_ROUND = 3
UNSEEN_SEEDS = tuple(range(95001, 95025))           # 24 variations no stage has used (not the sealed 12, not practice)
PERSIST_SEEDS = tuple(range(70001, 70007))
MAX_EXAMS = 2


def _root() -> Path:
    return Path(os.getenv("JARVIS_COMPOSE_DIR", str(storage.DATA / "learning" / "compose")))


def _sha(t):
    from core import acquire
    return acquire._sha(t)


def lesson_key(wid) -> str:
    return f"{wid}-compose"


def _shape(src) -> str:
    """Names, strings and numbers erased: two candidates with the same shape are the same idea."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            n.id = "_"
        elif isinstance(n, ast.Constant):
            n.value = 0
        elif isinstance(n, ast.arg):
            n.arg = "_"
    return ast.dump(tree, annotate_fields=False)


def _code(text) -> str:
    """The procedure from a reply: the longest ```python block, from its first top-level def (run, verify, recover in any order)."""
    import re
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text or "", re.S)
    src = max(blocks, key=len) if blocks else (text or "")
    m = re.search(r"^def ", src, re.M)
    return (src[m.start():] if m else src).strip() + "\n"


def history_shapes(wid) -> set:
    return {_shape(f.read_text(encoding="utf-8", errors="replace")) for f in (_root() / wid).glob("*/proc-*.py")}


def contract(cand) -> dict:
    from core import skillcontracts
    tool = cand["tool"]
    return {"name": tool, "goal": skillcontracts.goal(tool, {"site": "github"}).replace("GitHub", "<the site>").replace("github.com", "<its domain>"),
            "inputs": {"site": f"short site name, one of {sorted(skillcontracts.SITE_HOSTS)}"},
            "verification": {"independent": "host judge: DevTools ground truth of every tab and window before/after (never shown to the procedure)",
                             "sealed_exam": len(__import__("core.acquire", fromlist=["x"]).HELD_OUT_SEEDS),
                             "unseen_variations": len(UNSEEN_SEEDS), "persistence": "fresh lab process loads the stored skill"}}


_SPEC_SCHEMA = {"type": "object", "properties": {
    "dependencies": {"type": "object", "properties": {"capabilities": {"type": "array", "items": {"type": "string"}}}, "required": ["capabilities"]},
    "preconditions": {"type": "array", "items": {"type": "string"}}, "steps": {"type": "array", "items": {"type": "string"}},
    "expected": {"type": "array", "items": {"type": "string"}}, "recovery": {"type": "array", "items": {"type": "string"}},
    "needs": {"type": "array", "items": {"type": "string"}}}, "required": ["dependencies", "steps", "expected"]}

OBSERVE = '''def run(browser, util, params):
    for w in browser.windows():
        util.log("WINDOW " + str(w))
    for t in browser.tabs():
        util.log("TAB " + str(t))
    return "listed"
'''


def _plan_prompt(con, caps_doc, observed, best, feedback, teaching, strategy):
    from core import methodlang
    parts = [f"You are teaching yourself a reusable skill by COMPOSING existing capabilities (built and tested by developers).",
             f"Skill: {con['name']}. Goal for params {{'site': ...}}: {con['goal']}",
             f"Inputs: {json.dumps(con['inputs'])}",
             "Capabilities you may depend on (exact calls):", caps_doc,
             "What the browser capability returned in a practice world (read-only listing, so you know the data's shape):", observed[:3000]]
    if best:
        parts += ["Your best plan so far and how it did:", json.dumps(best["spec"].get("steps", []))[:1500], feedback[:3500]]
    if teaching:
        parts += [teaching]
    if strategy == "compose-minimal":
        parts += ["Strategy now: the SMALLEST plan that can work. Fewer steps, fewer calls; change only the decisions that failed."]
    parts += ["Write the skill description as JSON: {\"dependencies\": {\"capabilities\": [...]}, \"preconditions\": [...], "
              "\"steps\": [plain-language plan], \"expected\": [what must be true afterwards], \"recovery\": [what to do if a step fails], "
              "\"needs\": [capabilities you would need that do not exist; empty if none]}. Choose the capabilities that make the plan "
              "exact and short; prefer a dedicated API over UI clicking.", "", methodlang.PROCEDURE_DOC]
    return "\n".join(parts)


def _code_prompt(con, spec, caps_doc, best_src, feedback, teaching):
    from core import methodlang
    caps = spec["dependencies"]["capabilities"]
    sig = ", ".join(caps + ["params"])
    parts = [f"Skill: {con['name']}. Goal: {con['goal']}", "Your plan:", json.dumps({k: spec[k] for k in ("steps", "expected", "recovery")}, indent=1),
             "Capabilities (exact calls):", caps_doc, methodlang.PROCEDURE_DOC,
             f"Write the procedure for this plan: def run({sig}), def verify({sig}) that re-reads the state and returns True only "
             f"when every expected result holds, and def recover({sig}, error) if your plan has a recovery step."]
    if best_src:
        parts += ["Your best procedure so far:", f"```python\n{best_src}\n```", "How it did:", feedback[:3500], "Fix what failed; keep what worked."]
    if teaching:
        parts += [teaching]
    parts += ["Reply with ONLY the procedure in one ```python block."]
    return "\n".join(parts)


def _verified(rows, run) -> int:
    """How many worlds of a VERIFICATION job (sealed exam, unseen, persistence) count as passed. A job the lab did not finish
    counts for nothing, even when the rows it did return passed (review 2026-11-12)."""
    if run.get("status") != "done":
        return 0
    return sum(1 for r in rows if r["passed"] and r["class"]["class"] != "verification")


def _rows(worlds, run):
    from core import acquire, failures
    out = []
    results = list(run["results"])
    # a lab job that died part-way returns FEWER results: every missing world is a failed lab row, never silently dropped
    # (found in review 2026-11-08: zip() would have let 5 results for 9 worlds count as "5/5 passed")
    results += [{"lab_error": "no result: the lab job ended before this world ran"}] * (len(worlds) - len(results))
    for w, r in zip(worlds, results):
        j = r.get("judge") or {"passed": False, "diff": ["lab error"]}
        res = r.get("result") or {}
        row = {"seed": w.get("seed"), "site": w["params"]["site"], "world": acquire._world_text(w), "passed": bool(j.get("passed")),
               "diff": j.get("diff", []), "error": res.get("error", ""), "error_type": res.get("error_type", ""),
               "failed_in": res.get("failed_in", ""), "self_check": res.get("self_check"), "recovered": res.get("recovered", False),
               "trace": res.get("trace", [])[:30], "calls": res.get("calls", 0), "lab_error": r.get("lab_error", "")[-400:],
               "world_obj": w}
        row["class"] = failures.classify(row)
        out.append(row)
    return out


def _feedback(rows) -> str:
    out = []
    for r in rows:
        if r["passed"] and r["class"]["class"] != "verification":
            out.append(f"PASSED site={r['site']} {r['world'][:200]}")
            continue
        c = r["class"]
        out.append(f"{'PASSED but' if r['passed'] else 'FAILED'} [{c['class']}{'+' + '+'.join(c['also']) if c['also'] else ''}] "
                   f"site={r['site']} {r['world'][:400]}\n   judge: {'; '.join(r['diff'])[:400]}\n   error: {r['error'][:250] or '(none)'}"
                   f"   your verify(): {r['self_check']}\n   calls and results:\n      " + "\n      ".join(r["trace"][:18]))
    return "\n".join(out)


def _brief(con, best, rows, summ) -> str:
    """Class-focused teacher brief: practice evidence only (sealed seeds are never in `rows`)."""
    from core import capabilities
    bad = [r for r in rows if not r["passed"]][:8]
    return "\n".join([
        f"JARVIS is learning the skill {con['name']} by composing developer-built capabilities. Goal: {con['goal']}",
        "Capabilities he can use:", capabilities.doc(["browser", "ui", "util"]),
        f"His failures are mostly of class '{summ['dominant']}' ({json.dumps(summ['counts'])}).",
        "His plan:", json.dumps(best["spec"].get("steps", [])), "His procedure:", best["source"][:3500],
        "Practice failures (independent judge):", _feedback(bad)[:5000],
        "Teach him the wrong assumption in his PLAN or in how he READS the capability data. No code, no pseudo-code."])


def learn(cand, learner=None, lab=None, now=time.time, strategy=None, teach=True, start=None) -> dict:
    """start = {"source", "caps", "spec", "ref"}: begin from one of HIS earlier procedures (e.g. strategy "repair": a procedure
    that passed every world but whose own verify() did not) instead of composing new candidates in round 1."""
    from core import acquire, capabilities, failures, learnbudget, methodlang, skillcontracts, skilllab, skillspec, teacher
    learner = learner or acquire.Learner()
    wid, con = cand["id"], contract(cand)
    state = learnbudget.load(f"{wid}-compose")
    strategy = strategy or next((s for s in STRATEGIES if learnbudget.strategy(state, s)["status"] == "active"), None)
    if not strategy:
        return {"status": "budget_exhausted", "why": learnbudget.next_step(state, STRATEGIES)[1]}
    started = now()
    folder = _root() / wid / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    rec = {"weakness": wid, "skill": con["name"], "strategy": strategy, "learner_model": learner.model, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "labels": {"primitives": "developer-built (core/capabilities, core/browserbridge)", "procedure": "written by JARVIS"},
           "stages": [], "status": "running"}

    def log(stage, what, data=None, autonomous=True, assist=""):
        rec["stages"].append({"stage": stage, "what": what, "at": round(now() - started, 1), "autonomous": autonomous, "assist": assist, "data": data})
        (folder / "cycle.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
        learnbudget.save(state)

    if lab is None:
        def lab(trials, label):
            out = skilllab.run_job(trials, seconds=1200, guard=os.getenv("JARVIS_LAB_GUARD", "on") != "off", label=label)
            if out["status"] == "suspended":
                raise skilllab.Suspended("the owner started using the PC")
            return out

    judge_sha = _sha(__import__("inspect").getsource(skillcontracts))
    caps_offered = capabilities.usable()
    caps_doc = capabilities.doc(caps_offered)
    best, feedback, exams = None, "", 0
    seen = history_shapes(wid)

    def exercises(why, n):
        """JARVIS writes his own practice situations (edge cases of HIS plan); the host only checks they are valid worlds."""
        kept, refused = [], []
        try:
            reply = learner(f"Skill: {con['name']}. Goal: {con['goal']}\n{why}\nWrite {n} practice situations (browser windows and their "
                            f"tabs) that would break a careless plan. Hosts are real-looking host names; the site must be one of "
                            f"{sorted(skillcontracts.SITE_HOSTS)}; 'active' is the index of the tab in front. World rules (the lab "
                            "refuses others): every tab on the site's own hosts must have a title that names the site (e.g. "
                            "'Issues · GitHub', 'Music - YouTube'); every window needs at least one tab. Reply as JSON: "
                            "{\"exercises\": [{\"why\": ..., \"params\": {\"site\": ...}, \"windows\": [{\"tabs\": [{\"host\": ..., \"title\": ...}], \"active\": 0}]}]}",
                            temperature=0.5, fmt=acquire._WORLD_SCHEMA, num_predict=1400)
            for e in acquire._json(reply).get("exercises", [])[:n]:
                try:
                    kept.append({**skillcontracts.validate_world(e, cand["tool"]), "why": str(e.get("why", ""))[:200]})
                except ValueError as error:
                    refused.append(str(error)[:200])
        except (ValueError, acquire.OutOfBudget) as error:
            refused.append(f"no usable exercises: {error}"[:200])
        from core.words import plural
        log("exercises", f"JARVIS wrote {plural(len(kept), 'practice situation')} of his own ({len(refused)} not valid)",
            {"why": why, "worlds": [acquire._world_text(w) + "  // " + w["why"] for w in kept], "refused": refused})
        return kept

    def _repair_verify(cand_, rows):
        """Ask for verify() only, keep run()/recover() byte-for-byte in meaning, practise once on the same worlds."""
        try:
            new = _code(learner(
                f"Skill: {con['name']}. Your run() did the task on every practice world (the independent judge confirmed it), but "
                f"your verify() said it had NOT on {sum(r['class']['class'] == 'verification' for r in rows)} of them, so your own "
                "check cannot be trusted. Rewrite ONLY verify() so it checks the expected results by re-reading the state through "
                "your capabilities. params arrives with only the inputs " + json.dumps(list(con["inputs"])) + "; run(), verify() and "
                "recover() share that same params dict, so anything verify() reads from it must have been put there by your run(). "
                "Keep run() and recover() exactly as they are.\n```python\n" + cand_["source"] +
                "\n```\n" + methodlang.PROCEDURE_DOC + "\nReply with the COMPLETE procedure in one ```python block.", temperature=0.2))
            methodlang.check_procedure(new, cand_["caps"])
        except (methodlang.BadMethod, acquire.OutOfBudget, ValueError) as error:
            log("repair", "verify() repair unusable", f"{type(error).__name__}: {error}"[:300])
            return None

        def fn(src, name):
            t = ast.parse(src)
            return next((ast.dump(n) for n in t.body if isinstance(n, ast.FunctionDef) and n.name == name), "")
        if fn(new, "run") != fn(cand_["source"], "run") or fn(new, "recover") != fn(cand_["source"], "recover"):
            log("repair", "verify() repair changed run() or recover(): refused")
            return None
        ref = cand_["ref"].replace(".py", "-verify.py")
        (folder / ref).write_text(new, encoding="utf-8")
        ws = [r["world_obj"] for r in rows]
        rr = _rows(ws, lab([{"world": w, "action": {"type": "procedure", "source": new, "caps": cand_["caps"]}} for w in ws], "repair-verify"))
        good = sum(1 for r in rr if r["passed"] and r["class"]["class"] != "verification")
        log("repair", f"JARVIS rewrote verify() only: {good}/{len(rr)} worlds done AND confirmed by his own check", {"ref": ref})
        if good < len(rr):
            return None
        return {**cand_, "source": new, "ref": ref, "rows": rr, "score": good, "total": len(rr)}

    def maybe_teach():
        """Guidance only where it can help (planning/perception), only once the strategy stopped improving, within budget."""
        if not (teach and best and best.get("rows")):
            return
        rows = best["rows"]
        summ = failures.summary(rows)
        s = learnbudget.strategy(state, strategy)
        if summ["dominant"] == "missing_tool":
            _tool_request(wid, [r["class"]["why"] for r in rows if r["class"]["class"] == "missing_tool"], folder)
            return
        if not summ["teachable"] or s["since_better"] < 1 or s["lessons"] >= learnbudget.LIMITS["teacher_lessons"]:
            return
        ok, tier = teacher.can_help(lesson_key(wid))
        if not ok:
            log("teach", f"a lesson would help ({summ['dominant']}) but none is possible: {tier}")
            return
        les = teacher.consult({**cand, "id": lesson_key(wid)}, tier=tier, requested_by=f"compose:{summ['dominant']}",
                              prompt=_brief(con, best, rows, summ))
        s["lessons"] += 1
        s["since_better"] = 0                         # the lesson gets its own chance before the strategy counts as stalled
        log("teach", f"{summ['dominant']} failures persisted: asked the {les['tier']} teacher -> {les['status']}",
            {k: les.get(k) for k in ("id", "tier", "model", "status", "cost_usd", "lesson", "removed", "file")},
            autonomous=False, assist=f"teacher:{les.get('model')}")
    try:
        # ---- observe: what the primitives return (perception material; not a solution)
        obs_worlds = [skillcontracts.generate(s) for s in FIXED_PRACTICE[:2]]
        run = lab([{"world": w, "action": {"type": "procedure", "source": OBSERVE, "caps": ["browser", "util"]}} for w in obs_worlds], "observe")
        observed = "\n".join(l for r in run["results"] for l in (r.get("result") or {}).get("trace", []) if l.startswith(("WINDOW", "TAB")))
        log("observe", "developer read-only listing of two practice worlds through the browser capability", {"lines": observed.count("\n") + 1})
        own = exercises("Before planning, think about what could make this skill go wrong.", 4)
        for rnd in range(1, learnbudget.LIMITS["rounds"] + 1):
            v, why = learnbudget.verdict(state, strategy)
            if v != "continue":
                log("budget", f"strategy {strategy} stalled: {why}; its best work is kept", {"best": best and best["ref"]})
                break
            t0 = now()
            teaching = teacher.guidance(lesson_key(wid)) if teach else ""
            fresh = []
            if start and rnd == 1:
                fresh = [{**start, "ref": "start-" + start.get("ref", "earlier.py")}]
                (folder / fresh[0]["ref"]).write_text(start["source"], encoding="utf-8")
                log("compose", f"round 1: starting from his own earlier procedure {start.get('ref')}", {"sha": _sha(start["source"])})
            for k, temp in enumerate(CANDIDATES if not fresh else ()):
                try:
                    raw = acquire._json(learner(_plan_prompt(con, caps_doc, observed, best, feedback, teaching, strategy), temperature=temp,
                                                fmt=_SPEC_SCHEMA, num_predict=900))
                    (folder / f"plan-r{rnd}-{k}.json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
                    caps = [c for c in ((raw.get("dependencies") or {}).get("capabilities") or []) if c in caps_offered] or ["browser", "util"]
                    if "util" not in caps:
                        caps.append("util")
                    draft = {"dependencies": {"capabilities": caps}, **{f: raw.get(f, []) for f in ("steps", "expected", "recovery")}}
                    src = _code(learner(_code_prompt(con, draft, caps_doc, best and best["source"], feedback, teaching), temperature=temp))
                    try:
                        methodlang.check_procedure(src, caps)
                    except methodlang.BadMethod as error:
                        log("compose", f"round {rnd}: candidate {k} refused by the checker; one rewrite", str(error))
                        src = _code(learner(f"The procedure below was REFUSED by the checker:\n  {error}\n```python\n{src}\n```\n"
                                                    + methodlang.PROCEDURE_DOC + "\nRewrite it within the rules, same plan. ONLY the procedure in one ```python block.",
                                                    temperature=0.2))
                        methodlang.check_procedure(src, caps)
                    spec = skillspec.normalise(raw, con, src, granted=caps)
                    spec["dependencies"]["capabilities"] = caps
                    (folder / f"proc-r{rnd}-{k}.py").write_text(src, encoding="utf-8")
                    if spec["needs"]:
                        _tool_request(wid, spec["needs"], folder)
                        log("compose", f"round {rnd}: candidate {k} says it needs a missing tool", spec["needs"])
                    if _shape(src) in seen:
                        learnbudget.strategy(state, strategy)["duplicates"] += 1
                        log("compose", f"round {rnd}: candidate {k} is the same idea as an earlier one: not practised", {"sha": _sha(src)})
                        continue
                    seen.add(_shape(src))
                    fresh.append({"spec": spec, "source": src, "ref": f"proc-r{rnd}-{k}.py", "caps": caps})
                    log("compose", f"round {rnd}: candidate {k} composed", {"caps": caps, "steps": spec["steps"], "lines": src.count("\n")})
                except (ValueError, methodlang.BadMethod, skillspec.BadSpec) as error:
                    log("compose", f"round {rnd}: candidate {k} unusable", f"{type(error).__name__}: {error}"[:400])
                except acquire.OutOfBudget as error:
                    log("compose", "learner budget used", str(error))
                    break
            entrants = ([best] if best else []) + fresh
            if not fresh:
                feedback += "\nNOTE: your last candidates repeated earlier ideas or were unusable. Try a genuinely different plan."
                s = learnbudget.strategy(state, strategy)
                learnbudget.record_round(state, strategy, max(s["best"], 0), s["best_of"] or 1, (now() - t0) / 60, best and best["ref"])
                maybe_teach()
                continue
            worlds = ([skillcontracts.generate(s) for s in FIXED_PRACTICE] + own +
                      [skillcontracts.generate(1100 + 10 * rnd + i) for i in range(FRESH_PER_ROUND)])
            trials = [{"world": w, "action": {"type": "procedure", "source": e["source"], "caps": e["caps"]}} for e in entrants for w in worlds]
            run = lab(trials, f"practice-r{rnd}")
            scored = []
            for i, e in enumerate(entrants):
                rows = _rows(worlds, {"results": run["results"][i * len(worlds):(i + 1) * len(worlds)]})
                scored.append((sum(r["passed"] for r in rows), i, e, rows))
            n, _, win, rows = max(scored, key=lambda x: (x[0], x[1]))
            best = {**win, "score": n, "total": len(rows), "rows": rows}
            if n == len(rows) and any(r["class"]["class"] == "verification" for r in rows):
                # run() did the task everywhere but his own verify() said it had not (live 2026-11-08: a verify() that read
                # params it was never given returned False on all 10 worlds and the skill was still retained). A skill's
                # expected-results check must agree with the independent judge: one LOCAL repair of verify() only.
                fixed = _repair_verify(best, rows)
                if fixed:
                    best, rows, n = fixed, fixed["rows"], fixed["score"]
                else:
                    n = sum(1 for r in rows if r["passed"] and r["class"]["class"] != "verification")
            summ = failures.summary(rows)
            log("practice", f"round {rnd}: best {n}/{len(rows)} ({best['ref']}); failure classes {summ['counts']}",
                {"scores": [s[0] for s in scored], "kept": best["ref"], "classes": summ,
                 "rows": [{k: r[k] for k in ("seed", "site", "passed", "diff", "error", "class")} for r in rows]})
            # progress = practice passed + sealed passed: a 9/9 plan that then gets 11/12 is better than a 9/9 one never examined
            # (live 2026-11-08: counting practice alone called 9/9 -> 9/9 + 11/12 "no improvement" and stalled the strategy)
            n_sealed = len(acquire.HELD_OUT_SEEDS)
            if n < len(rows):
                learnbudget.record_round(state, strategy, n, len(rows) + n_sealed, (now() - t0) / 60, best["ref"])
                feedback = _feedback(rows)
                maybe_teach()
                continue
            # ---- every practice world passed: the sealed exam (unchanged seeds, unchanged judge)
            if exams >= MAX_EXAMS:
                learnbudget.record_round(state, strategy, n, len(rows) + n_sealed, (now() - t0) / 60, best["ref"])
                break
            exams += 1
            sealed = [skillcontracts.generate(s, cand["tool"]) for s in acquire.HELD_OUT_SEEDS]
            ex_run = lab([{"world": w, "action": {"type": "procedure", "source": best["source"], "caps": best["caps"]}} for w in sealed], f"exam-{exams}")
            ex = _rows(sealed, ex_run)
            ok = _verified(ex, ex_run)                       # his own check must agree too
            log("verify", f"sealed exam {exams}: {ok}/{len(ex)}", {"rows": [{k: r[k] for k in ("seed", "site", "passed", "diff", "error")} for r in ex]})
            learnbudget.record_round(state, strategy, n + ok, len(rows) + len(ex), (now() - t0) / 60, best["ref"])
            if ok < len(ex):
                # only the COUNT goes back; he is asked to imagine the cases his practice did not cover and write them himself
                feedback = _feedback(rows) + f"\nThen on {len(ex)} worlds you have not seen, only {ok} passed. Make the plan more general."
                own += exercises(f"Your plan passed all your practice worlds but failed {len(ex) - ok} of {len(ex)} unseen ones. "
                                 "Which situations did your practice NOT cover?", 3)
                continue
            unseen = [skillcontracts.generate(s, cand["tool"]) for s in UNSEEN_SEEDS]
            un_run = lab([{"world": w, "action": {"type": "procedure", "source": best["source"], "caps": best["caps"]}} for w in unseen], "unseen")
            un = _rows(unseen, un_run)
            uok = _verified(un, un_run)
            log("verify", f"unseen variations: {uok}/{len(un)}", {"rows": [{k: r[k] for k in ("seed", "site", "passed", "diff", "error")} for r in un]})
            if uok < len(un):
                feedback = _feedback(rows) + f"\nOn {len(un)} more unseen worlds only {uok} passed. Make the plan more general."
                continue
            if _sha(__import__("inspect").getsource(skillcontracts)) != judge_sha:
                rec["status"] = "judge_changed"
                log("verify", "not retained: the grader changed during the cycle")
                return _finish(rec, folder, learner, state)
            prov = teacher.provenance(lesson_key(wid))
            evidence = {"judge": judge_sha, "passed": ok, "total": len(ex),
                        "tasks": [{"seed": r["seed"], "site": r["site"]} for r in ex], "unseen": f"{uok}/{len(un)}"}
            man = skillspec.retain(best["spec"], best["source"], evidence, prov, learner.model,
                                   extra={"weakness": wid, "strategy": strategy, "evidence_folder": str(folder)})
            log("retain", f"retained {man['id']} v{man['version']} (procedure by {man['procedure_by']}; primitives by developers); approval follows the persistence check",
                {"id": man["id"], "version": man["version"], "sha": man["source_sha"]})
            persist = [skillcontracts.generate(s, cand["tool"]) for s in PERSIST_SEEDS]
            pr_run = lab([{"world": w, "action": {"type": "skill", "skill": man["id"], "version": man["version"], "store": str(skillspec._root())}} for w in persist], "persist")
            pr = _rows(persist, pr_run)
            pok = _verified(pr, pr_run)
            log("persist", f"fresh process loaded the stored skill from disk: {pok}/{len(pr)}",
                {"rows": [{k: r[k] for k in ("seed", "site", "passed", "diff", "error")} for r in pr]})
            rec["status"] = "learned" if pok == len(pr) else "retained_but_persistence_failed"
            if rec["status"] == "learned" and prov.get("teacher_lessons"):
                teacher.mark_outcome(lesson_key(wid), "led_to_learned")
            rec["skill"] = {"id": man["id"], "version": man["version"], "sha": man["source_sha"], "procedure_by": man["procedure_by"]}
            if rec["status"] == "learned":
                from core import learned       # owner decision 2026-11-12: passed its exam AND loads from disk -> approved, in use
                auto = learned.auto_approve("skill", man["id"], cand["tool"], man["version"], evidence)
                rec["skill"]["approved_by"] = "auto" if auto else ""
                log("approve", "approved automatically on its exam" if auto else "not approved automatically: awaiting the owner",
                    {"exam": (auto or {}).get("exam")})
            return _finish(rec, folder, learner, state)
        rec["status"] = "not_learned"
        if teach and teacher.usable(lesson_key(wid)):
            teacher.mark_outcome(lesson_key(wid), "not_enough_yet")
        nxt =learnbudget.next_step(state, STRATEGIES, other_tasks=[c["id"] for c in acquire.discover() if c["status"] == "ready" and c["id"] != wid])
        log("budget", f"not learned with strategy {strategy}; next: {nxt[0]} {nxt[1]}", {"best": best and {k: best[k] for k in ("ref", "score", "total")}})
        return _finish(rec, folder, learner, state)
    except skilllab.Suspended as error:
        rec["status"] = "suspended"
        log("practice", "suspended: the owner came back", str(error))
        return _finish(rec, folder, learner, state)


def _tool_request(wid, needs, folder):
    f = storage.DATA / "learning" / "tool-requests.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "weakness": wid, "needs": needs, "evidence": str(folder)}) + "\n")


def _finish(rec, folder, learner, state):
    from core import learnbudget
    rec["learner"] = {"calls": learner.calls, "seconds": round(learner.seconds, 1)}
    rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (folder / "cycle.json").write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
    (folder / "learner-transcript.json").write_text(json.dumps(learner.transcript, indent=1), encoding="utf-8")
    learnbudget.save(state)
    rec["folder"] = str(folder)
    return rec
