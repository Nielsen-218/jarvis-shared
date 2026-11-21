"""One bounded practice cycle for a weakness that has a DESKTOP lab world (core/labworlds): close_foreground, close_app,
use_app, type_text, save_document, open_path.

The local learner writes a method (core/methodlang: UI Automation only), the lab runs it in fresh worlds on the hidden
desktop, the world's judge reads the real end state, and failures go back as feedback. A method that passes the practice
worlds sits a sealed exam on worlds it never saw.

What this cycle does NOT do: put a method into use unless its kind has a PRODUCTION CHECK (core/skillcontracts.PRODUCTION_KINDS:
close_foreground only). A lab world's target is a purpose-built window, so for every other kind a passed exam is recorded as
"practised" (evidence and the method kept in the cycle folder), never as "learned". For close_foreground a method that also
passes the production check is retained (manifest with provenance) and auto-approved for its exact source hash
(core/learned.auto_approve). No paid model is ever called here.
"""
import inspect
import json
import os
import time

PRACTICE_SEEDS = (601, 602, 603)
FRESH_PER_ROUND = 2
ROUNDS = 4
EXAM_SEEDS = tuple(range(91001, 91013))     # 12 sealed worlds per tool (the browser exam's count; core/procedures needs 12): never shown, their details never fed back
REST_DAYS = 7                               # a practised tool is not practised again for a week (nothing new to gain nightly)


class LabFault(RuntimeError):
    """Every trial of a lab job failed because of the LAB (the world did not come up, the job crashed, no result), so nothing
    about the learner's method was ever judged: the cycle is an error with cause "lab", never a failed lesson."""


def _all_lab_faults(rows) -> bool:
    return bool(rows) and all(r.get("lab_fault") for r in rows)


def _lab_fault_text(what, rows, run) -> str:
    first = next((r["lab_error"] for r in rows if r.get("lab_error")), "")
    last = " ".join(first.strip().splitlines()[-1:])[:400] if first else ""
    return f"{what}: all {len(rows)} trials failed in the lab itself (job {run.get('status')}); first: {last or '(no detail)'}"


def _rows(worlds, run):
    from core import acquire, labworlds
    rows = []
    for w, r in zip(worlds, acquire.all_results(worlds, run)):      # a lost result is a failed row, never a shorter exam
        j = r.get("judge") or {"passed": False, "diff": [(r.get("lab_error") or "lab error")[-300:]], "checks": {}}
        res = r.get("result") or {}
        # Only a job that FINISHED can show the lab was at fault. A job the wall clock killed (timeout) or that died (crashed)
        # may have been hung or crashed by the learner's own method, so it stays the learner's failed lesson.
        fault = bool(r.get("lab_error")) and not r.get("judge") and run.get("status") == "done"
        rows.append({"seed": w.get("seed"), "world": labworlds.world_text(w), "params": w["params"], "passed": bool(j["passed"]),
                     "lab_fault": fault, "lab_error": r.get("lab_error") or "",
                     "checks": j.get("checks", {}), "diff": j.get("diff", []), "trace": res.get("trace", [])[:25],
                     "error": res.get("error", "")})
    return rows


def _feedback(rows) -> str:
    out = []
    for r in rows[:5]:
        if r["passed"]:
            out.append(f"PASSED  params={r['params']}  windows: {r['world'][:200]}")
        else:
            out.append(f"FAILED  params={r['params']}  windows: {r['world'][:300]}\n   judge: {'; '.join(r['diff'])[:500]}\n"
                       f"   error: {r['error'][:250] or '(none)'}\n   trace: " + " | ".join(r["trace"][-8:])[:700])
    return "\n".join(out)


def _prompt(tool, example, tree, feedback, previous) -> str:
    from core import labworlds, methodlang
    parts = [f"Write a method for the PC step `{tool}`. It receives params like {example['params']!r}.",
             "Goal: " + labworlds.goal(tool, example["params"]),
             methodlang.API_DOC,
             "What the desktop looks like through UI Automation in one practice world (windows: " + labworlds.world_text(example) + "):",
             tree[:3500] or "(no observation)"]
    if previous:
        parts += ["Your previous method:", "```python\n" + previous + "```", "How it did in practice:", feedback]
    elif feedback:
        parts += ["Your last reply was refused:", feedback]
    parts.append("Reply with only the method, in one ```python block.")
    return "\n\n".join(parts)


def learn(cand, learner=None, lab=None, now=time.time) -> dict:
    """-> the cycle record (also written to the evidence folder). status: practised | not_learned | suspended | error."""
    from core import acquire, labworlds, methodlang, skillcontracts, skilllab
    tool = cand["tool"]
    learner = learner or acquire.Learner()
    started = now()
    folder = acquire._evidence_root() / cand["id"] / time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while folder.exists():
        n += 1
        folder = folder.with_name(time.strftime("%Y%m%d-%H%M%S") + f"-{n}")
    folder.mkdir(parents=True)
    record = {"weakness": cand, "learner_model": learner.model, "stages": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "cloud_calls": 0, "status": "running", "world": "desktop"}

    def log(stage, what, data):
        record["stages"].append({"stage": stage, "what": what, "autonomous": True, "assist": "", "at": round(now() - started, 1), "data": data})
        (folder / "cycle.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")

    if lab is None:
        def lab(trials, label):
            out = skilllab.run_job(trials, seconds=600, guard=os.getenv("JARVIS_LAB_GUARD", "on") != "off", label=label)
            if out["status"] == "suspended":
                raise skilllab.Suspended("the owner started using the PC")
            return out
    try:
        log("discover", "weakness chosen", {"id": cand["id"], "tool": tool, "count": cand["count"], "status": cand["status"]})
        example = labworlds.generate(600, tool)
        seen = lab([{"world": example, "action": {"type": "observe"}}], "observe")
        first = (seen["results"] or [{}])[0]
        if first.get("lab_error") or not seen["results"]:
            raise LabFault(_lab_fault_text("observe", [{"lab_error": first.get("lab_error") or "no result", "lab_fault": True}], seen))
        tree = (first.get("result") or {}).get("tree", "")
        log("investigate", "looked at one practice world", {"windows": labworlds.world_text(example), "tree_lines": tree.count("\n") + 1 if tree else 0})
        judge_sha = acquire._sha(inspect.getsource(labworlds))
        source, feedback, best = "", "", None
        for rnd in range(1, ROUNDS + 1):
            try:
                draft = acquire._code(learner(_prompt(tool, example, tree, feedback, source)))
                methodlang.check(draft)
            except acquire.OutOfBudget as error:
                log("design", "stopped: " + str(error), {"round": rnd})
                break
            except ValueError as error:                     # a refused method is feedback, not a crash
                source, feedback = "", str(error)[:600]
                log("design", f"round {rnd}: method refused", {"why": feedback})
                continue
            source = draft
            worlds = [labworlds.generate(s, tool) for s in PRACTICE_SEEDS] + [labworlds.generate(1000 + 10 * rnd + i, tool) for i in range(FRESH_PER_ROUND)]
            run = lab([{"world": w, "action": {"type": "method", "source": source}} for w in worlds], f"practice-{rnd}")
            rows = _rows(worlds, run)
            if _all_lab_faults(rows):                      # nothing was judged: stop, the learner is not to blame and gets no feedback
                log("practice", f"round {rnd}: the lab failed on every trial", {"lab": run.get("status"), "first": rows[0]["lab_error"][-600:]})
                raise LabFault(_lab_fault_text(f"practice round {rnd}", rows, run))
            ok = sum(r["passed"] for r in rows)
            log("practice", f"round {rnd}: {ok}/{len(rows)} practice worlds", {"source": source, "rows": [{k: r[k] for k in ("seed", "passed", "diff", "error")} for r in rows]})
            if ok < len(rows) or run.get("status") != "done":       # a job the lab did not finish proves nothing
                feedback = _feedback(rows)
                continue
            sealed = [labworlds.generate(s, tool) for s in EXAM_SEEDS]
            exam_run = lab([{"world": w, "action": {"type": "method", "source": source}} for w in sealed], f"exam-{rnd}")
            exam = _rows(sealed, exam_run)
            if _all_lab_faults(exam):
                log("verify", "the lab failed on every exam trial", {"lab": exam_run.get("status"), "first": exam[0]["lab_error"][-600:]})
                raise LabFault(_lab_fault_text(f"exam round {rnd}", exam, exam_run))
            passed = sum(r["passed"] for r in exam)
            log("verify", f"sealed exam: {passed}/{len(exam)}", {"lab": exam_run.get("status"), "rows": [{k: r[k] for k in ("seed", "passed")} for r in exam]})
            if acquire.exam_passed(exam_run.get("status"), exam, sealed):
                best = {"round": rnd, "passed": passed, "total": len(exam), "exam_sha": acquire._sha(source)}
                if tool in skillcontracts.PRODUCTION_KINDS:      # the further bar before it may touch a real desktop
                    check = production_check(tool, source, lab, rnd)
                    log("verify", f"production check: {check['passed']}/{check['total']}",
                        {"lab": check["lab"], "rows": [{k: r[k] for k in ("seed", "variant", "passed", "diff")} for r in check["rows"]]})
                    if check["ok"]:
                        best["production"] = check
                        break
                    best = None
                    feedback = ("It passed practice and the sealed exam but failed the production check on the real-desktop rules. "
                                "It may touch only the one window in front, only by pressing its Close button, and when the app shows an "
                                "unsaved-changes prompt it must STOP there (ui.fail) and never press Save, Don't Save or Cancel. Failures: "
                                + " | ".join(sorted({d for r in check["rows"] for d in r["diff"]}))[:500])
                    continue
                break
            feedback = f"It passed practice but failed {max(1, len(exam) - passed)} of {len(exam)} unseen worlds. Make it more general."
        if best and best.get("production"):
            if acquire._sha(source) != best["exam_sha"]:            # the method on disk is not the one that sat the exam
                raise RuntimeError("the method changed after its exam; it is not retained")
            (folder / "method.py").write_text(source, encoding="utf-8")
            man = retain(cand, source, best, record, learner, judge_sha)
            from core import learned
            auto = learned.auto_approve("method", man["id"], tool, man["version"], man["evidence"])
            record["status"] = "learned"
            record["method"] = {"id": man["id"], "version": man["version"], "sha": man["source_sha"], "approved_by": "auto" if auto else ""}
            record["practice"] = {"tool": tool, "passed": best["passed"], "total": best["total"], "round": best["round"], "judge": "core.labworlds.judge",
                                  "judge_sha": judge_sha, "source_sha": man["source_sha"], "in_use": bool(auto),
                                  "production": {k: best["production"][k] for k in ("passed", "total", "judge")}}
            log("retain", "passed practice, the sealed exam and the production check; " + (
                "approved automatically for this exact source (core/authz still decides every action)" if auto else "NOT approved automatically"),
                {"id": man["id"], "version": man["version"], "source_sha": man["source_sha"], "approved_by": "auto" if auto else "",
                 "library": str(acquire._root()), "exam": (auto or {}).get("exam")})
        elif best:
            (folder / "method.py").write_text(source, encoding="utf-8")
            record["status"] = "practised"
            record["practice"] = {"tool": tool, "passed": best["passed"], "total": best["total"], "round": best["round"],
                                  "judge": "core.labworlds.judge", "judge_sha": judge_sha, "source_sha": acquire._sha(source),
                                  "in_use": False, "why_not_in_use": "lab world only: no production check for this tool yet"}
            log("retain", "passed the sealed exam in the lab; kept as evidence, NOT put into use", record["practice"])
        else:
            record["status"] = "not_learned"
    except skilllab.Suspended as error:
        record["status"] = "suspended"
        log("practice", "suspended", {"why": str(error)})
    except Exception as error:  # noqa: BLE001 - a crashed cycle is a RECORDED failure with its reason, never a lost night
        record["status"] = "error"
        record["cause"] = "lab" if isinstance(error, LabFault) else acquire.error_cause(error)
        record["error_type"] = type(error).__name__
        record["error"] = f"{type(error).__name__}: {' '.join(str(error).split())[:300]}"
        try:
            log("error", "the cycle stopped on an error", {"cause": record["cause"], "error": record["error"]})
        except Exception:  # noqa: BLE001
            pass
    return acquire._finish(record, folder, learner)


def production_check(tool, source, lab, rnd=0, seeds=None, label=None) -> dict:
    """Run `source` through the production-check worlds (host wrapper + ground-truth judge).
    -> {"ok","passed","total","lab","rows","judge","source_sha","seeds"}. ok only when the lab job FINISHED and every world was
    judged and passed. A lab that failed on every world raises LabFault (nothing about the method was judged)."""
    from core import acquire, skillcontracts
    worlds = skillcontracts.production_check_worlds(tool, seeds)
    run = lab([{"world": w, "action": {"type": "production", "source": source}} for w in worlds], label or f"production-{rnd}")
    rows = _rows(worlds, run)
    if _all_lab_faults(rows):
        raise LabFault(_lab_fault_text("production check", rows, run))
    for r, w in zip(rows, worlds):
        r["variant"] = w.get("check")
    return {"ok": acquire.exam_passed(run.get("status"), rows, worlds), "passed": sum(r["passed"] for r in rows), "total": len(rows),
            "lab": run.get("status"), "rows": rows, "judge": acquire._sha(inspect.getsource(skillcontracts)),
            "source_sha": acquire._sha(source), "seeds": [w["seed"] for w in worlds]}


def retain(cand, source, best, record, learner, judge_sha):
    """Write the verified method into the learned-method library with its provenance: who wrote it (the local model), how many cloud
    calls it took, the exam and production-check scores and the source hash. -> the manifest."""
    from core import acquire, labworlds, procedures
    tool, prod = cand["tool"], best["production"]
    evidence = {"judge": judge_sha, "passed": best["passed"], "total": best["total"],
                "tasks": [{"seed": s, "world": "desktop"} for s in EXAM_SEEDS],
                "production": {"passed": prod["passed"], "total": prod["total"], "judge": prod["judge"], "seeds": prod["seeds"]}}
    return procedures.Library(acquire._root()).register(
        f"pcmethod.{tool}", f"Learned method for {tool}", source, triggers=[[tool]], params={}, recovery={}, authored_by=f"local:{learner.model}",
        evidence=evidence, entry="run", scope=labworlds.goal(tool, {}),
        extra={"for_tool": tool, "language": "core.methodlang", "status": "verified", "learned_from": [e["said"] for e in cand["examples"]],
               "weakness": cand["id"], "design_round": best["round"], "cloud_calls": record.get("cloud_calls", 0),
               "exam": {"passed": best["passed"], "total": best["total"]}, "provenance": "independent", "teacher_lessons": [],
               "production_check": {"passed": prod["passed"], "total": prod["total"], "judge": prod["judge"], "source_sha": acquire._sha(source)},
               "dependencies": ["Windows UI Automation (pywinauto uia)"], "verified_on": time.strftime("%Y-%m-%dT%H:%M:%S")})


def retest_desktop(item) -> dict:
    """The weekly retest of a learned desktop method: fresh production-check worlds on the hidden lab desktop."""
    from core import acquire, procedures, skillcontracts, skilllab
    man, src = procedures.Library(acquire._root()).get(item["id"])
    worlds = skillcontracts.production_check_worlds(item["tool"], skillcontracts.RETEST_CHECK_SEEDS)
    out = skilllab.run_job([{"world": w, "action": {"type": "production", "source": src}} for w in worlds], seconds=600,
                           guard=os.getenv("JARVIS_LAB_GUARD", "on") != "off", label="retest")
    rows = [{"passed": bool((r.get("judge") or {}).get("passed")), "lab_error": r.get("lab_error", "")[-600:]} for r in out["results"]]
    rows += [{"passed": False, "lab_error": "no result"}] * (len(worlds) - len(rows))
    return {"status": out["status"], "rows": rows, "dir": out.get("dir", "")}


def resting(cand, clock) -> bool:
    """True while this weakness was practised to a pass recently: its nightly slot goes to the next one."""
    from core import acquire
    cycles = sorted((acquire._evidence_root() / cand["id"]).glob("*/cycle.json"))
    if not cycles:
        return False
    try:
        last = json.loads(cycles[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return last.get("status") == "practised" and clock - cycles[-1].stat().st_mtime < REST_DAYS * 86400


def self_check(kinds=None, lab=None, seed=1) -> list:
    """Prove each world on the real lab desktop: the lab's reference method must PASS and doing nothing must FAIL.
    -> [{"kind", "reference_passed", "noop_failed", "detail"}]. Used by scripts/lab_worlds.py; no model is involved."""
    from core import labworlds, skilllab
    kinds = list(kinds or labworlds.KINDS)
    if lab is None:
        def lab(trials, label):
            return skilllab.run_job(trials, seconds=300, guard=True, label=label)
    trials = []
    for k in kinds:
        w = labworlds.generate(seed, k)
        trials += [{"world": w, "action": {"type": "method", "source": labworlds.REFERENCE[k]}}, {"world": w, "action": {"type": "noop"}}]
    run = lab(trials, "worlds-check")
    out = []
    for i, k in enumerate(kinds):
        pair = (run["results"] + [{}] * len(trials))[2 * i:2 * i + 2]
        ref, noop = (p.get("judge") or {} for p in pair)
        out.append({"kind": k, "reference_passed": bool(ref.get("passed")), "noop_failed": noop.get("passed") is False,
                    "detail": "; ".join(ref.get("diff") or []) or (pair[0].get("lab_error") or "")[-400:], "lab": run["status"]})
    return out
